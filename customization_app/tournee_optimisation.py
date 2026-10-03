"""Optimisation des tournées d'une journée (calendrier des tâches, demande du 02/10/2026).

Pour un jour donné, les tâches de terrain des employés QUI TRAVAILLENT CE JOUR-LÀ sont redistribuées et
réordonnées entre eux pour que chacun roule moins : départ et retour au Magasin, journée bornée par le réglage,
durée de chaque intervention respectée.

- Déplaçables : les types du réglage (Entretien, Réparation, Installation, Visite, Livraison), sauf une
  réparation dans les locaux d'Aquaworld (`dans_local`) et sauf une tâche épinglée (`custom_tournee_fixe`).
- Fixes : tout le reste garde employé ET heure (rendez-vous promis, vérification de stock au Magasin…) ;
  l'optimisation s'organise autour.
- Distances : OSRM (OpenStreetMap, gratuit, réglage `osrm_url`) ; à vol d'oiseau à 30 km/h si injoignable.
- Coordonnées : lien Google Maps de la tâche ou de l'adresse (les liens courts sont résolus une fois et
  mémorisés sur l'adresse), sinon le centre du secteur (approximation signalée).
- Solveur : OR-Tools (tournées à fenêtres de temps, plusieurs véhicules, charge équilibrée).

Rien n'est écrit avant « Appliquer » : la proposition se lit, se discute, puis s'applique d'un geste,
avec un commentaire sur chaque tâche déplacée.
"""

from __future__ import annotations

import math
import re
import statistics
import time

import frappe
import requests
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, getdate

CONFIG = "Config Optimisation Tournees"
TACHE = "Tache de travail"
TYPES_MOBILES_DEFAUT = ("Entretien", "Réparation", "Installation", "Visite", "Livraison")
TYPES_HORS_JOURNEE = ("Jour de récupération",)
OSRM_DEFAUT = "https://router.project-osrm.org"
DEPOT_DEFAUT = (36.8700, 10.1950)        # Soukra, à défaut du lien du réglage
VITESSE_KMH = 30.0                       # repli à vol d'oiseau
ROLES = ("System Manager", "Responsable magasin")
PAS_MIN = 5                              # les heures proposées tombent sur 5 min
# Temps standard par type (celui de api.DUREE_INTERVENTION) : la durée retenue est ce standard, sauf si la tâche
# planifie nettement plus (décision utilisateur 02/10/2026 : un « 15 min » ne raccourcit pas un entretien).
DUREE_TYPE = {"Entretien": 30, "Installation": 75, "Réparation": 75, "Livraison": 30, "Visite": 120, "Autre": 60}
DUREES_MIN = {"15 min": 15, "30 min": 30, "45 min": 45, "1 heure": 60, "1 heure, 15 min": 75, "1 heure, 30 min": 90,
              "1 heure, 45 min": 105, ">=2 heures": 120}

# Les formes rencontrées : ?q=lat,lng · @lat,lng · !3dlat!4dlng · /search/lat,+lng (liens goo.gl/maps, « + » ou
# « %2B » ou « , » avant la longitude) · /place/lat,lng · ll=/destination=.
_SEP = r"(?:,\s*|,\+|,%2B|%2C\+?|%2C%2B)"
RE_COORDS = (r"[?&]q=(-?\d+\.\d+)" + _SEP + r"(-?\d+\.\d+)",
             r"[?&](?:ll|destination|daddr|center)=(-?\d+\.\d+)" + _SEP + r"(-?\d+\.\d+)",
             r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", r"@(-?\d+\.\d+)" + _SEP + r"(-?\d+\.\d+)",
             r"/(?:search|place|dir)/(-?\d+\.\d{3,})" + _SEP + r"(-?\d+\.\d{3,})",
             r"/(-?\d+\.\d{4,})" + _SEP + r"(-?\d+\.\d{4,})")


# ── Accès et réglage ────────────────────────────────────────────────────────

def _superviseur():
    if not set(ROLES) & set(frappe.get_roles()):
        frappe.throw(_("L’optimisation des tournées est réservée aux superviseurs."), frappe.PermissionError)


def _minutes(h, defaut: int) -> int:
    """Un champ Time (timedelta en base, ou « 08:00 ») → minutes depuis minuit."""
    if h is None or h == "":
        return defaut
    if hasattr(h, "total_seconds"):
        return int(h.total_seconds() // 60)
    p = str(h).split(":")
    return int(p[0]) * 60 + (int(p[1]) if len(p) > 1 else 0)


def config() -> dict:
    """Le réglage tel qu'ENREGISTRÉ (tabSingles) : un réglage jamais sauvegardé rend ses valeurs par défaut —
    `get_single` sur un Single vierge renvoie l'heure courante dans un champ Time, pas son défaut."""
    # Jamais enregistré : `get_single_value` rend 0 / timedelta(0), pas les défauts → on les pose nous-mêmes.
    # (`frappe.db.exists` ne sait pas interroger tabSingles : requête directe.)
    enregistre = frappe.db.exists("DocType", CONFIG) and frappe.db.sql(
        "select 1 from tabSingles where doctype = %s and field = 'heure_fin' limit 1", CONFIG)
    v = (lambda champ: frappe.db.get_single_value(CONFIG, champ)) if enregistre else (lambda champ: None)
    types = [t.strip() for t in (v("types_mobiles") or "").splitlines() if t.strip()] or list(TYPES_MOBILES_DEFAUT)
    lat, lng = flt(v("depot_latitude")), flt(v("depot_longitude"))
    exclus = set(frappe.get_all("Config Optimisation Tournees Exclu", filters={"parent": CONFIG, "parenttype": CONFIG}, pluck="employe")) \
        if frappe.db.exists("DocType", "Config Optimisation Tournees Exclu") else set()
    departs = {r.employe: (flt(r.latitude), flt(r.longitude)) for r in frappe.get_all(
        "Config Optimisation Tournees Depart", filters={"parent": CONFIG, "parenttype": CONFIG}, fields=["employe", "latitude", "longitude"])
        if r.latitude and r.longitude} if frappe.db.exists("DocType", "Config Optimisation Tournees Depart") else {}
    horaires = {r.employe: (_minutes(r.heure_debut, 8 * 60), _minutes(r.heure_fin, 17 * 60)) for r in frappe.get_all(
        "Config Optimisation Tournees Horaire", filters={"parent": CONFIG, "parenttype": CONFIG}, fields=["employe", "heure_debut", "heure_fin"])} \
        if frappe.db.exists("DocType", "Config Optimisation Tournees Horaire") else {}
    pointes = [(_minutes(r.heure_debut, 0), _minutes(r.heure_fin, 0), cint(r.majoration)) for r in frappe.get_all(
        "Config Optimisation Tournees Pointe", filters={"parent": CONFIG, "parenttype": CONFIG}, fields=["heure_debut", "heure_fin", "majoration"])
        if cint(r.majoration) > 0] if frappe.db.exists("DocType", "Config Optimisation Tournees Pointe") else []
    return {"depot": (lat, lng) if lat and lng else DEPOT_DEFAUT, "departs": departs, "horaires": horaires, "pointes": pointes,
            "debut": _minutes(v("heure_debut"), 8 * 60), "fin": _minutes(v("heure_fin"), 17 * 60),
            "premiere": _minutes(v("heure_premiere"), 9 * 60),
            "pause": (_minutes(v("pause_debut"), 0), _minutes(v("pause_fin"), 0)) if v("pause_debut") and v("pause_fin") else None,
            "marge": cint(v("marge_minutes")) if v("marge_minutes") is not None else 10,
            "fenetre": cint(v("fenetre_minutes")) if v("fenetre_minutes") is not None else 60,
            "types": types, "osrm": (v("osrm_url") or OSRM_DEFAUT).rstrip("/"),
            "equilibre": cint(v("equilibre")) if v("equilibre") is not None else 1, "exclus": exclus,
            "prevenir": {"sms": cint(v("prevenir_sms")), "email": cint(v("prevenir_email")),
                         "seuil": cint(v("prevenir_seuil")) if v("prevenir_seuil") is not None else 15,
                         "plage": cint(v("plage_minutes")) or 60, "sujet": v("sujet_email") or SUJET_EMAIL_DEFAUT,
                         "sms_texte": v("modele_sms") or MODELE_SMS_DEFAUT, "email_texte": v("modele_email") or ""}}


# ── Coordonnées ─────────────────────────────────────────────────────────────

def coordonnees_du_lien(url: str | None):
    """(lat, lng) lu dans un lien Google Maps déjà développé, sinon None. PURE.
    Une page de consentement Google (consent.google.com?continue=<lien>) porte le lien dans `continue`."""
    from urllib.parse import unquote
    url = unquote(url or "")
    for motif in RE_COORDS:
        m = re.search(motif, url)
        if m:
            lat, lng = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                return (lat, lng)
    return None


def resoudre_lien(url: str | None, timeout: float = 6.0):
    """Les coordonnées d'un lien Google Maps, lien court compris (maps.app.goo.gl → redirection vers le
    lien développé, qui porte « !3d<lat>!4d<lng> »). None si rien n'est lisible ou sans réseau."""
    if not url:
        return None
    direct = coordonnees_du_lien(url)
    if direct:
        return direct
    if frappe.flags.get("tournee_sans_reseau"):
        return None
    courant = url
    try:
        for _i in range(4):
            r = requests.head(courant, allow_redirects=False, timeout=timeout,
                              headers={"User-Agent": "Mozilla/5.0 (aquaworld-erpnext)"})
            suivant = r.headers.get("location")
            if not suivant:
                break
            c = coordonnees_du_lien(suivant)
            if c:
                return c
            courant = suivant
    except Exception:
        return None
    return None


NOMINATIM = "https://nominatim.openstreetmap.org/search"
MARQUE_LIEN_MORT = "lien mort"


def geocoder_texte(adresse_texte: str, ville: str | None = None):
    """Nominatim (OpenStreetMap, gratuit, 1 requête/s) sur le texte de l'adresse : une position APPROCHÉE
    (le quartier, la rue), mieux que rien quand le lien est mort ou absent. None sans réseau ou sans résultat."""
    if frappe.flags.get("tournee_sans_reseau") or not (adresse_texte or ville):
        return None
    try:
        for q in ([x for x in (adresse_texte, ville, "Tunisia") if x], [x for x in (ville, "Tunisia") if x]):
            r = requests.get(NOMINATIM, params={"q": ", ".join(q), "format": "json", "limit": 1, "countrycodes": "tn"},
                             headers={"User-Agent": "aquaworld-erpnext (koubaawassim@gmail.com)"}, timeout=10)
            d = r.json() if r.ok else []
            if d:
                return (float(d[0]["lat"]), float(d[0]["lon"]))
            time.sleep(1.1)
    except Exception:
        return None
    return None


def _geocoder_adresse(nom: str, texte: bool = True):
    """Coordonnées mémorisées sur l'adresse, sinon résolues depuis son lien (puis son texte) et mémorisées.
    Un lien mort est marqué pour ne pas être réessayé à chaque proposition."""
    if not nom:
        return None
    a = frappe.db.get_value("Address", nom, ["custom_latitude", "custom_longitude", "custom_lien_google_map",
                                             "custom_geocode_source", "address_line1", "city"], as_dict=True)
    if not a:
        return None
    if a.custom_latitude and a.custom_longitude:
        return (flt(a.custom_latitude), flt(a.custom_longitude))
    if a.custom_lien_google_map and not (a.custom_geocode_source or "").startswith(MARQUE_LIEN_MORT):
        c = resoudre_lien(a.custom_lien_google_map)
        if c:
            _memoriser(nom, c, "lien adresse")
            return c
        if not frappe.flags.get("tournee_sans_reseau") and frappe.db.has_column("Address", "custom_geocode_source"):
            frappe.db.set_value("Address", nom, "custom_geocode_source", "%s %s" % (MARQUE_LIEN_MORT, frappe.utils.nowdate()), update_modified=False)
    if texte and not (a.custom_geocode_source or "").startswith("texte"):
        c = geocoder_texte(a.address_line1, a.city)
        if c:
            _memoriser(nom, c, "texte ≈")
            return c
    return None


def geocoder_adresses(limite: int = 500) -> dict:
    """Tâche de fond : géocode les adresses sans position — par leur lien, sinon par leur texte. Idempotent :
    les liens morts et les textes déjà essayés sont marqués. → {liens, textes, morts, restantes}."""
    if not frappe.db.has_column("Address", "custom_latitude"):
        return {}
    bilan = {"liens": 0, "textes": 0, "morts": 0, "restantes": 0}
    adresses = frappe.db.sql("""select name, custom_lien_google_map, custom_geocode_source, address_line1, city
                                from tabAddress
                                where disabled = 0 and ifnull(custom_latitude, 0) = 0
                                  and (custom_lien_google_map like 'http%%' or ifnull(city, '') != '')
                                  and ifnull(custom_geocode_source, '') not like 'texte introuvable%%'
                                order by (custom_lien_google_map like 'http%%') desc, modified desc
                                limit %(n)s""", {"n": cint(limite) or 500}, as_dict=True)
    for a in adresses:
        src = a.custom_geocode_source or ""
        if a.custom_lien_google_map and not src.startswith(MARQUE_LIEN_MORT):
            c = resoudre_lien(a.custom_lien_google_map)
            if c:
                _memoriser(a.name, c, "lien adresse")
                bilan["liens"] += 1
                continue
            frappe.db.set_value("Address", a.name, "custom_geocode_source", "%s %s" % (MARQUE_LIEN_MORT, frappe.utils.nowdate()), update_modified=False)
            bilan["morts"] += 1
            src = MARQUE_LIEN_MORT
        if not src.startswith("texte"):
            c = geocoder_texte(a.address_line1, a.city)
            if c:
                # Le lien mort reste visible dans la source : c'est lui qu'il faut corriger sur l'adresse.
                _memoriser(a.name, c, "texte ≈ (lien mort)" if src.startswith(MARQUE_LIEN_MORT) else "texte ≈")
                bilan["textes"] += 1
            else:
                frappe.db.set_value("Address", a.name, "custom_geocode_source", "texte introuvable %s" % frappe.utils.nowdate(), update_modified=False)
            time.sleep(1.1)       # politesse Nominatim : 1 requête par seconde
        frappe.db.commit()
    bilan["restantes"] = frappe.db.sql("""select count(*) from tabAddress where disabled = 0 and ifnull(custom_latitude, 0) = 0
                                          and custom_lien_google_map like 'http%%' and ifnull(custom_geocode_source, '') not like 'lien mort%%'""")[0][0]
    return bilan


def geocodage_quotidien():
    """Cron : les adresses nouvelles ou modifiées depuis la veille (lien collé, adresse créée)."""
    try:
        geocoder_adresses(limite=300)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "tournées : géocodage quotidien")


@frappe.whitelist()
def etat_geocodage():
    _superviseur()
    total = frappe.db.count("Address", {"disabled": 0})
    return {"total": total,
            "avec_lien": frappe.db.count("Address", {"disabled": 0, "custom_lien_google_map": ["like", "http%"]}),
            "geocodees": frappe.db.count("Address", {"disabled": 0, "custom_latitude": [">", 0]}),
            "liens_morts": frappe.db.count("Address", {"disabled": 0, "custom_geocode_source": ["like", "%" + MARQUE_LIEN_MORT + "%"]}),
            "approchees": frappe.db.count("Address", {"disabled": 0, "custom_geocode_source": ["like", "texte%"], "custom_latitude": [">", 0]})}


@frappe.whitelist(methods=["POST"])
def lancer_geocodage(limite=500):
    """Bouton du réglage : géocodage en tâche de fond (quelques minutes pour des centaines d'adresses)."""
    _superviseur()
    frappe.enqueue("customization_app.tournee_optimisation.geocoder_adresses", queue="long", timeout=3600,
                   limite=cint(limite) or 500, enqueue_after_commit=True)
    return etat_geocodage()


def _memoriser(adresse: str, c, source: str):
    if adresse and frappe.db.has_column("Address", "custom_latitude"):
        frappe.db.set_value("Address", adresse, {"custom_latitude": c[0], "custom_longitude": c[1], "custom_geocode_source": source},
                            update_modified=False)


def _centres_secteurs() -> dict:
    """Centre de chaque secteur = médiane des adresses géocodées des tâches de l'année : une approximation
    honnête pour une tâche sans lien (signalée comme telle)."""
    out = {}
    if not frappe.db.has_column("Address", "custom_latitude"):
        return out
    rows = frappe.db.sql("""select t.secteur, a.custom_latitude lat, a.custom_longitude lng
                            from `tabTache de travail` t join tabAddress a on a.name = t.select_address
                            where t.secteur is not null and t.secteur != '' and a.custom_latitude and a.custom_longitude
                              and t.starts_on >= date_sub(curdate(), interval 365 day)
                            union all
                            select a.custom_secteur, a.custom_latitude, a.custom_longitude from tabAddress a
                            where a.custom_secteur is not null and a.custom_secteur != '' and a.custom_latitude and a.custom_longitude
                              and a.custom_geocode_source not like 'texte%%'""", as_dict=True)
    par = {}
    for r in rows:
        par.setdefault(r.secteur, []).append((flt(r.lat), flt(r.lng)))
    for s, pts in par.items():
        if len(pts) >= 3:
            out[s] = (statistics.median(p[0] for p in pts), statistics.median(p[1] for p in pts))
    return out


def coordonnees_tache(t, centres: dict):
    """(lat, lng, source) — source : « tâche », « adresse », « secteur » (approximatif) ou None."""
    c = resoudre_lien(t.get("google_map"))
    if c:
        if t.get("select_address") and not frappe.db.get_value("Address", t.select_address, "custom_latitude"):
            _memoriser(t.select_address, c, "lien tâche")
        return c[0], c[1], "tâche"
    c = _geocoder_adresse(t.get("select_address"))
    if c:
        src = frappe.db.get_value("Address", t.select_address, "custom_geocode_source") or ""
        return c[0], c[1], ("secteur" if src.startswith("texte") else "adresse")   # texte = approché, dit comme tel
    if t.get("secteur") in centres:
        c = centres[t.secteur]
        return c[0], c[1], "secteur"
    return None


# ── Distances ───────────────────────────────────────────────────────────────

def haversine_km(a, b) -> float:
    r = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def matrice_haversine(points: list) -> tuple[list, list, str]:
    """(minutes, km) à vol d'oiseau × 1,3 (détour routier moyen), 30 km/h. PURE."""
    n = len(points)
    km = [[0.0] * n for _ in range(n)]
    mn = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                km[i][j] = round(haversine_km(points[i], points[j]) * 1.3, 2)
                mn[i][j] = int(round(km[i][j] / VITESSE_KMH * 60))
    return mn, km, "vol d’oiseau"


def matrice(points: list, osrm: str) -> tuple[list, list, str]:
    """(minutes, km, source) entre tous les points, par OSRM ; repli à vol d'oiseau si injoignable."""
    if len(points) < 2:
        return [[0]], [[0.0]], "osrm"
    if frappe.flags.get("tournee_sans_reseau"):
        return matrice_haversine(points)
    try:
        coords = ";".join("%.6f,%.6f" % (p[1], p[0]) for p in points)
        r = requests.get("%s/table/v1/driving/%s" % (osrm, coords), params={"annotations": "duration,distance"}, timeout=20)
        d = r.json()
        if d.get("code") != "Ok":
            raise ValueError(d.get("message") or d.get("code"))
        mn = [[int(round((x or 0) / 60)) for x in ligne] for ligne in d["durations"]]
        km = [[round((x or 0) / 1000, 2) for x in ligne] for ligne in d["distances"]]
        return mn, km, "osrm"
    except Exception:
        frappe.log_error(frappe.get_traceback()[-800:], "tournées : OSRM injoignable, repli vol d’oiseau")
        return matrice_haversine(points)


def facteur_pointe(minute: int, pointes: list) -> float:
    """× appliqué à un trajet qui DÉMARRE à `minute` : 1 hors pointe, 1 + majoration % dans une plage. PURE."""
    for d, f, pct in pointes or []:
        if d <= minute < f:
            return 1 + pct / 100.0
    return 1.0


def matrice_majoree(mn: list, departs: dict, pointes: list) -> list:
    """La matrice des minutes, chaque ligne (origine) majorée selon l'heure à laquelle on en repart. PURE.
    `departs` : {nœud: minute de départ} connue d'une première résolution ; nœud absent = pas de majoration."""
    if not pointes:
        return mn
    return [[int(round(x * facteur_pointe(departs[i], pointes))) if i in departs and x else x for x in ligne]
            for i, ligne in enumerate(mn)]


# ── Solveur ─────────────────────────────────────────────────────────────────

def fusionner_intervalles(intervalles: list) -> list:
    """Union de créneaux (début, fin) en minutes : ceux qui se recouvrent ou se touchent n'en font qu'un. PURE.
    Deux pauses imposées qui se chevauchent (deux réparations d'Akram à 11:01 et 11:20 le 03/10/2026) déroutent
    OR-Tools : il repousse les visites suivantes d'une heure, et dans la journée réelle n'a plus rien placé chez
    l'employé après — l'après-midi entier allait à son collègue, déjà le plus chargé."""
    out = []
    for d, f in sorted((int(d), int(f)) for d, f in intervalles if f > d):
        if out and d <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], f))
        else:
            out.append((d, f))
    return out


def resoudre(minutes: list, arrets: list, nb_vehicules: int, debut: int, fin: int, equilibre: int = 1,
             limite_s: int = 5, premiere: int | None = None, depots: list | None = None,
             marge: int = 0, pause: tuple | None = None, occupations: dict | None = None,
             debuts: list | None = None, fins: list | None = None) -> dict:
    """Tournées à fenêtres de temps (OR-Tools). `minutes` couvre tous les nœuds : les dépôts (nœud 0 = Magasin,
    puis les points de départ particuliers) et les arrêts, qui occupent les DERNIERS nœuds. `arrets[i]` :
    {service: min, fenetre: (a, b) | None, vehicule: idx | None}. `depots[v]` = nœud de départ ET de retour du
    véhicule v (0 par défaut). Les temps sont en minutes depuis minuit.
    → {"routes": [[(noeud, arrivee), …] par véhicule], "non_places": [noeuds], "cout": minutes de route}."""
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    n = len(minutes)
    nb_depots = n - len(arrets)
    depots = [cint(d) for d in (depots or [0] * nb_vehicules)]
    manager = pywrapcp.RoutingIndexManager(n, nb_vehicules, depots, depots)
    routing = pywrapcp.RoutingModel(manager)
    service = [0] * nb_depots + [cint(a.get("service")) for a in arrets]

    def transit(i, j):
        # route + service au départ + marge (stationnement, accueil) à l'arrivée sur un arrêt
        a, b = manager.IndexToNode(i), manager.IndexToNode(j)
        return minutes[a][b] + service[a] + (cint(marge) if b >= nb_depots else 0)

    cb = routing.RegisterTransitCallback(transit)
    routing.SetArcCostEvaluatorOfAllVehicles(cb)
    debuts = [cint(x) for x in (debuts or [debut] * nb_vehicules)]       # début de journée par véhicule
    fins = [cint(x) for x in (fins or [fin] * nb_vehicules)]             # fin de journée par véhicule (retour compris)
    horizon = max(max(fins), max((a["fenetre"][1] + cint(a.get("service")) for a in arrets if a.get("fenetre")), default=fin)) + 1
    routing.AddDimension(cb, horizon, horizon, False, "Temps")
    temps = routing.GetDimensionOrDie("Temps")
    # Équilibrage : c'est la fin de journée LA PLUS TARDIVE, toutes tournées confondues, qui coûte — chaque minute
    # vaut 2 × equilibre minutes de route. Charger un employé n'a d'intérêt que tant que sa journée reste plus
    # courte que celle du plus chargé : les fins de journée se rapprochent d'elles-mêmes.
    # (Historique : une borne souple « part équitable + 1 h » par véhicule ne voyait pas qu'un employé occupé par
    # des tâches sorties du modèle restait libre après, et laissait tout l'après-midi au collègue déjà le plus
    # chargé — Akram 13:20 / Mohamed Hedi 17:25, 03/10/2026. Une somme des amplitudes chargeait tout sur un seul.)
    if equilibre:
        temps.SetGlobalSpanCostCoefficient(2 * cint(equilibre))
    # Créneaux où un véhicule est OCCUPÉ hors tournée (tâche qu'on n'a pas pu placer, gardée telle quelle) :
    # des pauses imposées, pour que les autres arrêts ne viennent pas se poser dessus.
    visites = [service[manager.IndexToNode(i)] for i in range(routing.Size())]
    for v in range(nb_vehicules):
        occ = fusionner_intervalles([(cint(d), cint(f)) for d, f in (occupations or {}).get(v, []) if f > d])
        # Sa journée finit au plus tôt à la fin de son dernier créneau occupé : c'est sur cette base que son
        # amplitude est comparée aux autres (sinon un employé pris jusqu'à 13:20 paraît fini à 10:30).
        fin_min = max(debuts[v], occ[-1][1] if occ else debuts[v])
        temps.CumulVar(routing.Start(v)).SetRange(debuts[v], debuts[v])
        temps.CumulVar(routing.End(v)).SetRange(fin_min, max(fins[v], fin_min) + 1)
        routing.AddVariableMinimizedByFinalizer(temps.CumulVar(routing.End(v)))
        if occ:
            solver = routing.solver()
            temps.SetBreakIntervalsOfVehicle(
                [solver.FixedDurationIntervalVar(d, d, f - d, False, "occupe_%d_%d" % (v, k)) for k, (d, f) in enumerate(occ)], v, visites)
    for k, a in enumerate(arrets):
        idx = manager.NodeToIndex(nb_depots + k)
        fen = a.get("fenetre") or (max(debut, premiere or debut), max(fins))     # pas de visite libre avant « première »
        temps.CumulVar(idx).SetRange(cint(fen[0]), cint(fen[1]))
        if pause and not a.get("fixe_pause"):
            # Pas d'intervention à cheval sur la pause : elle finit avant, ou commence après.
            temps.CumulVar(idx).RemoveInterval(max(cint(pause[0]) - cint(a.get("service")) + 1, cint(fen[0])), cint(pause[1]) - 1)
        if a.get("vehicule") is not None:
            # Le véhicule imposé, ET −1 (« non desservi ») pour rester compatible avec la disjonction
            # ci-dessous — sans −1, le modèle est insoluble dès qu'une tâche est fixée.
            routing.VehicleVar(idx).SetValues([-1, int(a["vehicule"])])
        # On préfère laisser une tâche de côté (et le dire) plutôt que rendre le problème insoluble.
        routing.AddDisjunction([idx], 100000 if a.get("vehicule") is None else 1000000)
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromSeconds(limite_s)
    sol = routing.SolveWithParameters(params)
    if not sol:
        return {"routes": [[] for _ in range(nb_vehicules)], "non_places": list(range(nb_depots, n)), "cout": 0}
    routes, places = [], set()
    for v in range(nb_vehicules):
        r, idx = [], routing.Start(v)
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node >= nb_depots:
                r.append((node, sol.Min(temps.CumulVar(idx))))
                places.add(node)
            idx = sol.Value(routing.NextVar(idx))
        routes.append(r)
    return {"routes": routes, "non_places": [k for k in range(nb_depots, n) if k not in places], "cout": sol.ObjectiveValue()}


# ── Proposition ─────────────────────────────────────────────────────────────

def duree_retenue(type_: str | None, planifie: int | None, temps: str | None) -> int:
    """Standard du type, sauf planifié (créneau du calendrier, à défaut le champ Temps) NETTEMENT plus long. PURE."""
    standard = DUREE_TYPE.get(type_ or "", 60)
    propre = planifie if planifie and 5 <= planifie <= 480 else DUREES_MIN.get(temps or "")
    return propre if propre and propre > standard else standard


def _duree(t) -> int:
    planifie = None
    if t.get("starts_on") and t.get("ends_on"):
        planifie = int((get_datetime(t.ends_on) - get_datetime(t.starts_on)).total_seconds() // 60)
    return duree_retenue(t.get("custom_type_dintervention"), planifie, t.get("temps"))


def chevauchements(arrets: list) -> list:
    """Les paires d'arrêts d'un même employé qui se recouvrent (clés `employe`, `debut`, `service`). PURE."""
    out = []
    par_emp = {}
    for a in arrets:
        par_emp.setdefault(a["employe"], []).append(a)
    for liste in par_emp.values():
        liste = sorted(liste, key=lambda a: a["debut"])
        for i, a in enumerate(liste):
            for b in liste[i + 1:]:
                if b["debut"] < a["debut"] + a["service"]:
                    out.append((a, b))
    return out


def _hm(minutes: int) -> str:
    return "%02d:%02d" % (minutes // 60, minutes % 60)


# ── Prévenir les clients ────────────────────────────────────────────────────

SUJET_EMAIL_DEFAUT = "Aqua World & Servicing — votre rendez-vous est déplacé"
MODELE_SMS_DEFAUT = ("Bonjour {nom_client},\n\n"
                     "Pour mieux organiser notre tournée, votre rendez-vous ({type}) du {date} est déplacé : "
                     "notre technicien {technicien} passera {demi} entre {plage}{au_lieu_de}.\n\n"
                     "Vous pouvez consulter ou modifier votre rendez-vous ici : {lien_rdv}\n\n"
                     "Merci de votre compréhension.\n\n{signature}")


def _h(minutes: int) -> str:
    """« 14h45 », « 9h » — l'écriture d'un SMS, pas celle d'une base."""
    return "%dh%s" % (minutes // 60, "%02d" % (minutes % 60) if minutes % 60 else "")


def plage_annoncee(minute: int, largeur: int = 60) -> dict:
    """La plage dite au client : `largeur` minutes centrées sur la nouvelle heure, bornes au quart d'heure.
    15:15 / 60 → « 14h45 et 15h45 ». PURE."""
    largeur = max(15, int(largeur or 60))
    debut = max(0, int(math.floor((minute - largeur / 2) / 15.0) * 15))
    fin = min(24 * 60 - 1, debut + largeur)
    return {"debut": _hm(debut), "fin": _hm(fin), "plage": "%s et %s" % (_h(debut), _h(fin)),
            "demi": "le matin" if minute < 12 * 60 + 30 else "l'après-midi"}


def a_prevenir(arret: dict, seuil: int) -> bool:
    """Un arrêt vaut un message si l'heure bouge d'au moins `seuil` minutes ou si l'employé change
    (le message nomme le technicien). Fixes, non placés : jamais."""
    if arret.get("fixe") or arret.get("non_place"):
        return False
    return bool(arret.get("deplace")) or (bool(arret.get("decale")) and abs(int(arret.get("ecart_min") or 0)) >= int(seuil or 0))


def _extras_notification(arret: dict, largeur: int) -> dict:
    """Les balises propres au déplacement, pour sms_taches.rendre : plage, demi, ancienne heure, nouvelle heure."""
    deb = _minutes(arret["debut"], 0)
    pl = plage_annoncee(deb, largeur)
    ancienne = _h(_minutes(arret["ancien_debut"], 0))
    return {"plage": pl["plage"], "demi": pl["demi"], "ancienne_heure": ancienne, "heure": _hm(deb),
            # seul le technicien change : pas de « au lieu de 13h30 » quand l'heure est la même
            "au_lieu_de": "" if arret["ancien_debut"] == arret["debut"] else " (au lieu de %s)" % ancienne}


def notifications_proposees(arrets: list, cfg: dict) -> list:
    """Pour la fenêtre, AVANT d'appliquer : qui recevra quoi (numéros, e-mails, texte rendu avec les NOUVELLES
    valeurs) parmi les arrêts qui le méritent. Le technicien/heure de `_destinataires` sont ceux d'avant
    application : on les écrase par ceux de la proposition."""
    from customization_app import sms_taches
    pv = cfg["prevenir"]
    cibles = {a["tache"]: a for a in arrets if a_prevenir(a, pv["seuil"])}
    if not cibles:
        return []
    noms = {e.name: (e.employee_name, e.cell_number) for e in frappe.get_all(
        "Employee", filters={"name": ["in", list({a["employe"] for a in cibles.values()})]}, fields=["name", "employee_name", "cell_number"])}
    out = []
    for ligne in sms_taches._destinataires(list(cibles)):
        a = cibles[ligne["tache"]]
        ligne.update(_extras_notification(a, pv["plage"]))
        ligne["technicien"], ligne["tel_technicien"] = noms.get(a["employe"], (ligne["technicien"], ligne["tel_technicien"]))
        out.append({"tache": a["tache"], "client": ligne["nom_client"], "employe": a["employe"], "numeros": ligne["numeros"], "emails": ligne["emails"],
                    "ancienne_heure": a["ancien_debut"], "heure": a["debut"], "plage": ligne["plage"], "demi": ligne["demi"],
                    "sms": sms_taches.rendre(pv["sms_texte"], ligne), "email": sms_taches.rendre(pv["email_texte"] or pv["sms_texte"], ligne)})
    return out


def _route_actuelle(arrets_emp: list, mn: list, km: list, depot: int = 0) -> tuple[int, float]:
    """Minutes et km de la tournée telle qu'elle est (ordre des heures), dépôt → … → dépôt."""
    ordre = [depot] + [a["noeud"] for a in sorted(arrets_emp, key=lambda a: a["debut"])] + [depot]
    return (sum(mn[ordre[i]][ordre[i + 1]] for i in range(len(ordre) - 1)),
            round(sum(km[ordre[i]][ordre[i + 1]] for i in range(len(ordre) - 1)), 1))


@frappe.whitelist()
def employes_du_jour(date):
    """Les employés qui ont des tâches ce jour-là (hors exclus du réglage), pour choisir qui entre dans
    l'optimisation et d'où chacun part : Magasin, ou son domicile s'il est réglé."""
    _superviseur()
    jour = getdate(date)
    cfg = config()
    rows = frappe.db.sql("""select custom_choix_du_staff employe, count(*) n,
                                   sum(custom_type_dintervention in %(types)s and status = 'Open') mobiles
                            from `tabTache de travail`
                            where starts_on between %(d)s and %(f)s and status != 'Cancelled'
                              and ifnull(custom_choix_du_staff, '') != '' and custom_type_dintervention not in %(hors)s
                            group by custom_choix_du_staff""",
                         {"d": "%s 00:00:00" % jour, "f": "%s 23:59:59" % jour, "types": tuple(cfg["types"]), "hors": TYPES_HORS_JOURNEE}, as_dict=True)
    noms = {e.name: e.employee_name for e in frappe.get_all("Employee", filters={"name": ["in", [r.employe for r in rows] or [""]]}, fields=["name", "employee_name"])}
    detail = _taches_par_employe(jour, cfg)
    out = [{"employe": r.employe, "nom": noms.get(r.employe, r.employe), "taches": cint(r.n), "mobiles": cint(r.mobiles),
            "exclu": r.employe in cfg["exclus"], "domicile": r.employe in cfg["departs"],
            "debut": _hm(cfg["horaires"].get(r.employe, (cfg["debut"], cfg["fin"]))[0]),
            "fin": _hm(cfg["horaires"].get(r.employe, (cfg["debut"], cfg["fin"]))[1]),
            "horaire_propre": r.employe in cfg["horaires"], "liste": detail.get(r.employe, [])} for r in rows]
    out.sort(key=lambda e: (e["exclu"], e["nom"]))
    return out


def _taches_par_employe(jour, cfg) -> dict:
    """Les tâches du jour de chaque employé pour la fenêtre : heure, type, client, adresse, et si elle peut
    bouger (sinon pourquoi : épinglée, pas ouverte, type hors tournée, réparation au local, sans position)."""
    taches = frappe.get_all(TACHE, filters={"starts_on": ["between", ["%s 00:00:00" % jour, "%s 23:59:59" % jour]],
                                           "status": ["!=", "Cancelled"], "custom_choix_du_staff": ["is", "set"]},
                            fields=["name", "custom_choix_du_staff", "custom_type_dintervention", "starts_on", "ends_on",
                                    "status", "custom_client", "nom_client", "select_address", "google_map", "secteur",
                                    "details_adresse", "dans_local",
                                    *(["custom_tournee_fixe"] if frappe.db.has_column(TACHE, "custom_tournee_fixe") else [])],
                            order_by="starts_on asc", limit_page_length=0)
    centres = _centres_secteurs()
    out = {}
    for t in taches:
        if t.custom_type_dintervention in TYPES_HORS_JOURNEE:
            continue
        d, f = get_datetime(t.starts_on), get_datetime(t.ends_on) if t.ends_on else None
        pos = coordonnees_tache(t, centres)
        if cint(t.get("custom_tournee_fixe")):
            motif = "📌 épinglée"
        elif t.status != "Open":
            motif = "statut « %s »" % t.status
        elif t.custom_type_dintervention not in cfg["types"]:
            motif = "type hors tournée"
        elif t.custom_type_dintervention == "Réparation" and (t.dans_local or "") == "Oui":
            motif = "réparation au local"
        elif not pos:
            motif = "sans position"
        else:
            motif = ""
        adresse = re.sub(r"^Secteur\s*:\s*[^,]*,\s*", "", t.details_adresse or "")     # le secteur est affiché à part
        if not adresse and t.select_address:
            adresse = frappe.db.get_value("Address", t.select_address, "address_line1") or t.select_address
        out.setdefault(t.custom_choix_du_staff, []).append({
            "tache": t.name, "debut": d.strftime("%H:%M"), "fin": f.strftime("%H:%M") if f else "",
            "type": t.custom_type_dintervention or "", "client": t.nom_client or t.custom_client or "",
            "adresse": adresse, "secteur": t.secteur or "", "mobile": not motif, "motif": motif,
            "position": pos[2] if pos else ""})
    return out


@frappe.whitelist(methods=["POST"])
def definir_depart(employe, lien):
    """Depuis la fenêtre : le point de départ (domicile) d'un employé, collé en lien Google Maps, enregistré dans
    le réglage (table « Points de départ particuliers »). Lien vide = retour au Magasin."""
    _superviseur()
    cfg = frappe.get_single(CONFIG)
    lignes = [r for r in (cfg.get("departs") or []) if r.employe != employe]
    if (lien or "").strip():
        c = resoudre_lien(lien.strip())
        if not c:
            frappe.throw(_("Ce lien ne donne pas de coordonnées : ouvrez Google Maps sur le lieu, « Partager », copiez le lien."))
        lignes.append({"employe": employe, "lien": lien.strip(), "latitude": c[0], "longitude": c[1]})
    cfg.set("departs", lignes)
    cfg.flags.ignore_permissions = True
    cfg.save()
    return {"employe": employe, "domicile": bool((lien or "").strip())}


@frappe.whitelist(methods=["POST"])
def definir_horaires(employe, debut, fin):
    """Depuis la fenêtre : les horaires habituels d'un employé, mémorisés dans le réglage. Vides = heures globales."""
    _superviseur()
    cfg = frappe.get_single(CONFIG)
    lignes = [r for r in (cfg.get("horaires") or []) if r.employe != employe]
    if debut and fin:
        if _minutes(debut, 0) >= _minutes(fin, 0):
            frappe.throw(_("La fin de journée doit suivre son début."))
        lignes.append({"employe": employe, "heure_debut": str(debut)[:5] + ":00", "heure_fin": str(fin)[:5] + ":00"})
    cfg.set("horaires", lignes)
    cfg.flags.ignore_permissions = True
    cfg.save()
    return {"employe": employe, "propre": bool(debut and fin)}


@frappe.whitelist()
def proposer(date, fenetre=None, employes=None):
    """La proposition pour la journée : par employé, tournée actuelle et tournée optimisée (ordre, heures,
    km, minutes de route), tâches déplacées, approximations et tâches impossibles à placer. RIEN N'EST ÉCRIT."""
    _superviseur()
    jour = getdate(date)
    cfg = config()
    # Choix de l'écran : qui entre dans l'optimisation, et d'où il part (« magasin » / « domicile »).
    brut = frappe.parse_json(employes) if isinstance(employes, str) else (employes or [])
    choix = {c["employe"]: (c.get("depart") or "magasin") for c in brut if c.get("employe")}
    horaires_choisis = {c["employe"]: (_minutes(c.get("debut"), 0) or None, _minutes(c.get("fin"), 0) or None) for c in brut if c.get("employe")}
    if choix:
        du_jour = [x["employe"] for x in employes_du_jour(date)]
        cfg = dict(cfg, exclus=set(cfg["exclus"]) | {e for e in du_jour if e not in choix},
                   departs={e: p for e, p in cfg["departs"].items() if choix.get(e) == "domicile"})
    taches = frappe.get_all(TACHE, filters={"starts_on": ["between", ["%s 00:00:00" % jour, "%s 23:59:59" % jour]],
                                           "status": ["!=", "Cancelled"], "custom_choix_du_staff": ["is", "set"]},
                            fields=["name", "custom_choix_du_staff", "custom_type_dintervention", "starts_on", "ends_on", "temps",
                                    "status", "custom_client", "nom_client", "select_address", "google_map", "secteur",
                                    "details_adresse", "dans_local", "titre",
                                    *(["custom_tournee_fixe"] if frappe.db.has_column(TACHE, "custom_tournee_fixe") else [])],
                            order_by="starts_on asc", limit_page_length=0)
    centres = _centres_secteurs()
    base = jour.strftime("%Y-%m-%d")
    points, arrets, avert = [cfg["depot"]], [], []
    for t in taches:
        if t.custom_type_dintervention in TYPES_HORS_JOURNEE or t.custom_choix_du_staff in cfg["exclus"]:
            continue
        debut = get_datetime(t.starts_on)
        dmin = debut.hour * 60 + debut.minute
        service = _duree(t)
        mobile = (t.custom_type_dintervention in cfg["types"] and t.status == "Open" and not cint(t.get("custom_tournee_fixe"))
                  and not (t.custom_type_dintervention == "Réparation" and (t.dans_local or "") == "Oui"))
        pos = coordonnees_tache(t, centres)
        if not pos:
            if mobile:
                avert.append(_("{0} ({1}) : pas de position connue — gardée telle quelle").format(t.name, t.nom_client or t.custom_client or ""))
                mobile = False
            if not (cfg["debut"] - 60 <= dmin <= cfg["fin"] + 60):
                continue        # une tâche sans lieu, hors journée (ex. « Autre » à minuit) : pas une étape
            pos = (cfg["depot"][0], cfg["depot"][1], "dépôt")
        elif pos[2] == "secteur" and mobile:
            avert.append(_("{0} ({1}) : position approchée par le centre du {2}").format(t.name, t.nom_client or t.custom_client or "", t.secteur))
        points.append((pos[0], pos[1]))
        arrets.append({"noeud": len(points) - 1, "tache": t.name, "client": t.nom_client or t.custom_client or "", "type": t.custom_type_dintervention,
                       "employe": t.custom_choix_du_staff, "debut": dmin, "service": service, "mobile": mobile, "statut": t.status,
                       "position": pos[2], "lat": pos[0], "lng": pos[1], "adresse": t.details_adresse or ""})
    employes = sorted({a["employe"] for a in arrets})
    if not employes:
        return {"date": str(jour), "employes": [], "message": _("Aucun employé n’a de tâche de terrain ce jour-là.")}
    noms = {e.name: e.employee_name for e in frappe.get_all("Employee", filters={"name": ["in", employes]}, fields=["name", "employee_name"])}
    for a, b in chevauchements(arrets):
        avert.append(_("{0} : {1} ({2}) et {3} ({4}) se chevauchent déjà dans le planning actuel").format(
            noms.get(a["employe"], a["employe"]), a["client"], _hm(a["debut"]), b["client"], _hm(b["debut"])))
    # Points de départ particuliers (domicile…) : insérés juste après le Magasin, les arrêts décalés d'autant.
    particuliers = [e for e in employes if e in cfg["departs"]]
    decalage = len(particuliers)
    for a in arrets:
        a["noeud"] += decalage
    points = [points[0]] + [cfg["departs"][e] for e in particuliers] + points[1:]
    depot_de = {e: (1 + particuliers.index(e) if e in cfg["departs"] else 0) for e in employes}
    depots = [depot_de[e] for e in employes]
    mn, km, source = matrice(points, cfg["osrm"])
    # Journée de CHAQUE employé : réglage global, puis ses horaires du réglage, puis ceux saisis dans la fenêtre.
    # Elle ne coupe jamais son travail déjà planifié (une tâche fixe plus tôt l'avance, une tâche qui finit
    # après l'étend, retour compris) — sinon le moteur laisserait des tâches de côté au lieu de les placer.
    debuts, fins = [], []
    for e in employes:
        d0, f0 = cfg["horaires"].get(e, (cfg["debut"], cfg["fin"]))
        hc = horaires_choisis.get(e, (None, None))
        fin_voulue = bool(hc[1])          # une fin saisie dans la fenêtre est VOULUE : ses tâches en trop vont ailleurs
        d0, f0 = hc[0] or d0, hc[1] or f0
        for a in arrets:
            if a["employe"] != e:
                continue
            if not a["mobile"] or not fin_voulue:
                f0 = max(f0, a["debut"] + a["service"] + mn[a["noeud"]][depot_de[e]])
            if not a["mobile"]:
                d0 = min(d0, a["debut"])
        debuts.append(d0)
        fins.append(f0)
    debut_j, fin_j = min(debuts), max(fins)
    fenetre = cint(cfg["fenetre"]) if fenetre is None else cint(fenetre)

    def _noeud(a):
        if not a["mobile"]:
            return {"service": a["service"], "fenetre": (a["debut"], a["debut"]), "vehicule": employes.index(a["employe"]), "fixe_pause": True}
        # Déplaçable : libre dans la journée, ou dans ± fenêtre autour de son heure actuelle (défaut).
        fen = None if not fenetre else (max(cfg["premiere"], a["debut"] - fenetre), min(fin_j, max(a["debut"] + fenetre, cfg["premiere"])))
        return {"service": a["service"], "fenetre": fen, "vehicule": None}

    par_noeud = {a["noeud"]: a for a in arrets}

    def _passes(mn_x):
        """Les trois passes (libre → non placées fixées → non placées en créneaux occupés) sur une matrice donnée."""
        nonlocal fin_j
        noeuds = [_noeud(a) for a in arrets]
        sol = resoudre(mn_x, noeuds, len(employes), debut_j, fin_j, cfg["equilibre"], premiere=cfg["premiere"], depots=depots,
                       marge=cfg["marge"], pause=cfg["pause"], debuts=debuts, fins=fins)
        if sol["non_places"]:
            # Ce qui n'a pas trouvé place reste où c'est (employé, heure) et la tournée se recalcule autour,
            # pour que la proposition soit complète et comparable à l'existant.
            for n in sol["non_places"]:
                a = par_noeud[n]
                a["mobile"] = False
                v = employes.index(a["employe"])
                fins[v] = max(fins[v], a["debut"] + a["service"] + mn_x[n][depot_de[a["employe"]]])
                fin_j = max(fins)
            noeuds = [_noeud(a) for a in arrets]
            sol = resoudre(mn_x, noeuds, len(employes), debut_j, fin_j, cfg["equilibre"], premiere=cfg["premiere"], depots=depots,
                           marge=cfg["marge"], pause=cfg["pause"], debuts=debuts, fins=fins)
        bloques = set()
        for _tour in range(6):
            if not sol["non_places"]:
                break
            # Toujours impossibles (deux rendez-vous à la même heure, journée trop chargée…) : elles restent telles
            # quelles ET occupent leur créneau — les autres arrêts du même employé se calculent autour. Chaque
            # résolution peut en laisser de NOUVELLES de côté (en fixer une en chasse une autre) : on recommence
            # jusqu'à ce que tout ce qui reste de côté ait son créneau réservé — sinon un arrêt se pose dessus
            # (constaté le 03/10/2026 : Jilani 12:00 sur Ons 11:30–12:45, laissée de côté au dernier tour).
            bloques |= set(sol["non_places"])
            occupations = {}
            for n in bloques:
                a = par_noeud[n]
                a["mobile"] = False
                v = employes.index(a["employe"])
                occupations.setdefault(v, []).append((a["debut"], a["debut"] + a["service"]))
                fins[v] = max(fins[v], a["debut"] + a["service"] + mn_x[n][depot_de[a["employe"]]])
            fin_j = max(fins)
            restants = [a for a in arrets if a["noeud"] not in bloques]
            nb_depots = len(points) - len(arrets)
            garder = list(range(nb_depots)) + [a["noeud"] for a in restants]
            mn2 = [[mn_x[i][j] for j in garder] for i in garder]
            noeuds = [_noeud(a) for a in restants]
            sol2 = resoudre(mn2, noeuds, len(employes), debut_j, fin_j, cfg["equilibre"], premiere=cfg["premiere"], depots=depots,
                            marge=cfg["marge"], pause=cfg["pause"], occupations=occupations, debuts=debuts, fins=fins)
            inverse = {nb_depots + i: a["noeud"] for i, a in enumerate(restants)}    # retour aux nœuds d'origine
            sol = {"routes": [[(inverse[n], t) for n, t in r] for r in sol2["routes"]],
                   "non_places": sorted({inverse[n] for n in sol2["non_places"]} - bloques), "cout": sol2["cout"]}
        sol["non_places"] = sorted(bloques | set(sol["non_places"]))
        return sol

    sol = _passes(mn)
    mn_ap = mn
    if cfg["pointes"]:
        # Heures de pointe : OSRM ne connaît pas le trafic. On lit l'heure de départ de chaque trajet dans la première
        # résolution, on majore les trajets qui partent en pointe, et on résout à nouveau.
        departs = {depots[v]: debuts[v] for v in range(len(employes))}
        for v, r in enumerate(sol["routes"]):
            for n, t in r:
                departs[n] = t + par_noeud[n]["service"]
        mn_ap = matrice_majoree(mn, departs, cfg["pointes"])
        sol = _passes(mn_ap)
    # La tournée ACTUELLE, majorée aux heures où elle roule vraiment : comparable à la proposition.
    departs_actuels = {depots[v]: debuts[v] for v in range(len(employes))}
    departs_actuels.update({a["noeud"]: a["debut"] + a["service"] for a in arrets})
    mn_av = matrice_majoree(mn, departs_actuels, cfg["pointes"]) if cfg["pointes"] else mn

    out, tot_av, tot_ap, km_av, km_ap = [], 0, 0, 0.0, 0.0
    for v, e in enumerate(employes):
        actuels = [a for a in arrets if a["employe"] == e]
        dep = depot_de[e]
        m_av, k_av = _route_actuelle(actuels, mn_av, km, dep)
        route = sol["routes"][v]
        ordre = [dep] + [n for n, _t in route] + [dep]
        m_ap = sum(mn_ap[ordre[i]][ordre[i + 1]] for i in range(len(ordre) - 1))
        k_ap = round(sum(km[ordre[i]][ordre[i + 1]] for i in range(len(ordre) - 1)), 1)
        apres, serv_ap, fin_ap = [], 0, 0
        # Les tâches que même fixées le solveur n'a pu servir (deux rendez-vous à la même heure chez le même
        # employé, hors journée…) restent dans la liste de leur employé, telles quelles, pour que la journée
        # proposée soit complète.
        restes = [(a["noeud"], a["debut"]) for a in arrets if a["noeud"] in sol["non_places"] and a["employe"] == e]
        for n, arrivee in sorted(list(route) + restes, key=lambda x: x[1]):
            a = par_noeud[n]
            deb = a["debut"] if not a["mobile"] else int(math.ceil(arrivee / PAS_MIN) * PAS_MIN)
            serv_ap, fin_ap = serv_ap + a["service"], max(fin_ap, deb + a["service"])
            apres.append({"tache": a["tache"], "client": a["client"], "type": a["type"], "debut": _hm(deb), "fin": _hm(deb + a["service"]),
                          "starts_on": "%s %s:00" % (base, _hm(deb)), "ends_on": "%s %s:00" % (base, _hm(deb + a["service"])),
                          "employe": e, "de": a["employe"], "de_nom": noms.get(a["employe"], a["employe"]),
                          "deplace": a["employe"] != e, "decale": a["mobile"] and deb != a["debut"], "fixe": not a["mobile"],
                          "ancien_debut": _hm(a["debut"]), "ecart_min": deb - a["debut"],
                          "non_place": n in sol["non_places"],
                          "position": a["position"], "lat": a["lat"], "lng": a["lng"], "adresse": a["adresse"]})
        out.append({"employe": e, "nom": noms.get(e, e), "depart": "domicile" if dep else "Magasin", "depart_point": points[dep],
                    "journee": [_hm(debuts[v]), _hm(fins[v])],
                    "avant": {"minutes": m_av, "km": k_av, "interventions": sum(a["service"] for a in actuels),
                              "fin": _hm(max((a["debut"] + a["service"] for a in actuels), default=debuts[v])), "arrets": [{"tache": a["tache"], "client": a["client"], "type": a["type"],
                                                                        "debut": _hm(a["debut"]), "fin": _hm(a["debut"] + a["service"]),
                                                                        "fixe": not a["mobile"], "lat": a["lat"], "lng": a["lng"]}
                                                                       for a in sorted(actuels, key=lambda a: a["debut"])]},
                    "apres": {"minutes": m_ap, "km": k_ap, "interventions": serv_ap, "fin": _hm(fin_ap or debuts[v]), "arrets": apres}})
        tot_av, tot_ap, km_av, km_ap = tot_av + m_av, tot_ap + m_ap, km_av + k_av, km_ap + k_ap
    non_places = [{"tache": par_noeud[n]["tache"], "client": par_noeud[n]["client"], "employe": noms.get(par_noeud[n]["employe"])} for n in sol["non_places"]]
    return {"date": str(jour), "employes": out, "depot": cfg["depot"], "source": source,
            "total": {"avant_min": tot_av, "apres_min": tot_ap, "avant_km": round(km_av, 1), "apres_km": round(km_ap, 1)},
            "deplacees": sum(1 for e in out for a in e["apres"]["arrets"] if a["deplace"]),
            "decalees": sum(1 for e in out for a in e["apres"]["arrets"] if a["decale"] and not a["deplace"]),
            "non_places": non_places, "avertissements": avert, "journee": [_hm(debut_j), _hm(fin_j)], "premiere": _hm(cfg["premiere"]),
            "fenetre": fenetre, "marge": cfg["marge"], "pause": [_hm(cfg["pause"][0]), _hm(cfg["pause"][1])] if cfg["pause"] else None,
            "pointes": ["%s–%s +%d %%" % (_hm(d), _hm(f), pct) for d, f, pct in cfg["pointes"]],
            "sans_domicile": [noms.get(e, e) for e, p in choix.items() if p == "domicile" and e not in cfg["departs"] and e in noms],
            "prevenir": {"sms": cfg["prevenir"]["sms"], "email": cfg["prevenir"]["email"], "seuil": cfg["prevenir"]["seuil"], "plage": cfg["prevenir"]["plage"]},
            "notifications": notifications_proposees([a for e in out for a in e["apres"]["arrets"]], cfg)}


@frappe.whitelist(methods=["POST"])
def appliquer(date, plan, prevenir=0):
    """Écrit la proposition : employé, heures de chaque tâche listée. plan : [{tache, employe, starts_on, ends_on, prevenir}].
    Un commentaire sur chaque tâche modifiée ; la tâche garde son statut, ses photos, sa commande.
    `prevenir` : les clients des tâches marquées `prevenir` dans le plan reçoivent le SMS / e-mail du réglage
    (plage horaire centrée sur la nouvelle heure), en tâche de fond, verdict en commentaire sur la tâche."""
    _superviseur()
    plan = frappe.parse_json(plan) if isinstance(plan, str) else (plan or [])
    cfg_pv = config()["prevenir"]
    noms = {}
    modifiees, a_prevenir_ = [], {}
    for p in plan:
        doc = frappe.get_doc(TACHE, p["tache"])
        if doc.status != "Open" or str(getdate(doc.starts_on)) != str(getdate(date)):
            continue
        nouveau_emp, nd, nf = p.get("employe") or doc.custom_choix_du_staff, get_datetime(p["starts_on"]), get_datetime(p["ends_on"])
        if nouveau_emp == doc.custom_choix_du_staff and get_datetime(doc.starts_on) == nd and get_datetime(doc.ends_on) == nf:
            continue
        for e in (doc.custom_choix_du_staff, nouveau_emp):
            if e and e not in noms:
                noms[e] = frappe.db.get_value("Employee", e, "employee_name") or e
        avant = "%s %s" % (noms.get(doc.custom_choix_du_staff, doc.custom_choix_du_staff), str(doc.starts_on)[11:16])
        if nouveau_emp != doc.custom_choix_du_staff and doc.titre:
            # Le titre porte le nom de l'employé en dernière ligne : il suit la réaffectation.
            lignes = doc.titre.split("\n")
            if lignes and lignes[-1].strip() == noms.get(doc.custom_choix_du_staff):
                lignes[-1] = noms[nouveau_emp]
                doc.titre = "\n".join(lignes)
        doc.custom_choix_du_staff, doc.starts_on, doc.ends_on = nouveau_emp, nd, nf
        doc.flags.ignore_permissions = True
        doc.save()
        doc.add_comment("Comment", _("🗺️ Optimisation de la tournée du {0} : {1} → {2} {3}")
                        .format(frappe.utils.formatdate(date), avant, noms[nouveau_emp], str(nd)[11:16]))
        modifiees.append(doc.name)
        if cint(prevenir) and cint(p.get("prevenir")) and (cfg_pv["sms"] or cfg_pv["email"]):
            a_prevenir_[doc.name] = _extras_notification({"debut": str(nd)[11:16], "ancien_debut": avant[-5:]}, cfg_pv["plage"])
    if a_prevenir_:
        frappe.enqueue("customization_app.sms_taches._executer", queue="short", timeout=1800,
                       taches=list(a_prevenir_), modele=cfg_pv["sms_texte"], sujet=cfg_pv["sujet"],
                       sms=cfg_pv["sms"], email=cfg_pv["email"], utilisateur=frappe.session.user, differe=True,
                       extras=a_prevenir_, modele_email=cfg_pv["email_texte"] or None,
                       job_name="tournee_prevenir_%s" % frappe.generate_hash(length=8))
    return {"modifiees": modifiees, "prevenus": len(a_prevenir_)}
