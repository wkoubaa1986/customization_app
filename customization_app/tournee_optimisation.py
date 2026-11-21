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

import hashlib
import math
import re
import statistics

import frappe
import requests
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, getdate

CONFIG = "Config Optimisation Tournees"
TACHE = "Tache de travail"
TYPES_MOBILES_DEFAUT = ("Entretien", "Réparation", "Installation", "Visite", "Livraison")
TYPES_HORS_JOURNEE = ("Jour de récupération",)
OSRM_DEFAUT = "https://router.project-osrm.org"
# Dépassement de la fin de journée (12/10/2026) : plutôt que de laisser des tâches « gardées telles quelles » faute de
# place, l'employé peut finir jusqu'à N minutes plus tard. Chaque minute en plus coûte COUT_DEPASSEMENT (contre 1 par
# minute de route et 100 000 pour une tâche laissée de côté) : le moteur ne s'en sert que si c'est nécessaire.
DEPASSEMENT_DEFAUT = 60
COUT_DEPASSEMENT = 100
# Dernier recours quand la journée est trop chargée (18/10/2026) : plutôt que figer des tâches à leur heure d'origine
# (sans leurs trajets, d'où des tournées absurdes), on laisse la journée déborder jusqu'à autant — et on le dit en rouge.
DEPASSEMENT_SANS_LIMITE = 6 * 60
DEPOT_DEFAUT = (36.8700, 10.1950)        # Soukra, à défaut du lien du réglage
VITESSE_KMH = 30.0                       # repli à vol d'oiseau
ROLES = ("System Manager", "Responsable magasin", "Appels")   # Appels = le poste de Salma, qui planifie la journée (16/10/2026 : elle ne voyait pas le bouton)
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
    # Champ ajouté le 12/10/2026 : tant qu'il n'est pas enregistré, sa valeur par défaut (get_single_value rendrait 0).
    depassement = cint(v("depassement_minutes")) if enregistre and frappe.db.sql(
        "select 1 from tabSingles where doctype = %s and field = 'depassement_minutes' limit 1", CONFIG) else DEPASSEMENT_DEFAUT
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
    # Passage par le Magasin (22/11/2026) : un employé qui a un point de départ particulier garde la voiture chez lui mais
    # « passe au magasin le matin et après avoir fini la journée » — case par employé, cochée par défaut. `departs` ne
    # change PAS de sens (ce sont aussi les domiciles du suivi GPS) : `via_magasin` dit seulement qui passe par le Magasin,
    # et l'itinéraire (tournee_itineraire.journee) en tient compte. Avant la migration : personne, rien ne change.
    via_magasin = set(frappe.get_all("Config Optimisation Tournees Depart", filters={"parent": CONFIG, "parenttype": CONFIG, "passe_magasin": 1},
                                     pluck="employe")) & set(departs) \
        if departs and frappe.db.has_column("Config Optimisation Tournees Depart", "passe_magasin") else set()

    def minutes_magasin(champ, defaut):
        # champ jamais enregistré : son défaut (get_single_value rendrait 0)
        connu = enregistre and frappe.db.sql("select 1 from tabSingles where doctype = %s and field = %s limit 1", (CONFIG, champ))
        return max(0, cint(v(champ))) if connu else defaut

    return {"depot": (lat, lng) if lat and lng else DEPOT_DEFAUT, "departs": departs, "horaires": horaires, "pointes": pointes,
            "via_magasin": via_magasin, "magasin_matin": minutes_magasin("magasin_matin", 15), "magasin_soir": minutes_magasin("magasin_soir", 10),
            "debut": _minutes(v("heure_debut"), 8 * 60), "fin": _minutes(v("heure_fin"), 17 * 60),
            "premiere": _minutes(v("heure_premiere"), 9 * 60),
            "pause": (_minutes(v("pause_debut"), 0), _minutes(v("pause_fin"), 0)) if v("pause_debut") and v("pause_fin") else None,
            "marge": cint(v("marge_minutes")) if v("marge_minutes") is not None else 10,
            "rangement": cint(v("rangement_minutes")) if v("rangement_minutes") is not None else 5,
            "coef": min(2.0, max(1.0, flt(v("coef_osrm")) or 1.0)),
            "fenetre": cint(v("fenetre_minutes")) if v("fenetre_minutes") is not None else 60,
            "depassement": max(0, depassement),
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


# ── Plus Codes (Open Location Code) ─────────────────────────────────────────
# Un lieu partagé depuis l'appli Google Maps (résidence, magasin) donne un lien court qui se développe en
# « …/maps/place/V59C+242 Résidence La Princesse, … /data=…!1s0x…:0x… » : AUCUNE coordonnée, mais un Plus Code,
# qui EST une position (≈ 14 m à 10 caractères, ≈ 3 m à 11). Décodé ici, sans API ni réseau (08/10/2026 : Faleh
# Wajdi, « Cité Ennasr 2 », était lu d'après le texte à Mateur, 55 km plus loin). Un code COURT (« V59C+242 », les
# 4 premiers caractères omis) se complète autour d'un point de référence : valable à ± 0,5° (≈ 55 km) — au-delà, on
# préfère ne rien dire plutôt qu'une position à 111 km.
OLC_ALPHABET = "23456789CFGHJMPQRVWX"
RE_PLUS_CODE = re.compile(r"(?:^|[/\s,=:])([23456789CFGHJMPQRVWX]{2,8}\+[23456789CFGHJMPQRVWX]{2,3})(?=$|[\s,+/&?])")
PLUS_CODE_PORTEE_KM = 50


def _olc_decoder(code: str):
    """Centre (lat, lng) d'un Plus Code COMPLET (8 caractères avant le « + »). PURE."""
    chiffres = code.replace("+", "")
    if len(chiffres) < 10 or any(c not in OLC_ALPHABET for c in chiffres):
        return None
    lat, lng, res = -90.0, -180.0, 20.0
    for i in range(0, 10, 2):
        lat += OLC_ALPHABET.index(chiffres[i]) * res
        lng += OLC_ALPHABET.index(chiffres[i + 1]) * res
        res /= 20
    res_lat = res_lng = res * 20
    for c in chiffres[10:]:                      # au-delà de 10 : grille de 4 colonnes × 5 lignes
        res_lat, res_lng = res_lat / 5, res_lng / 4
        v = OLC_ALPHABET.index(c)
        lat, lng = lat + (v // 4) * res_lat, lng + (v % 4) * res_lng
    return lat + res_lat / 2, lng + res_lng / 2


def _olc_prefixe(lat: float, lng: float, n: int) -> str:
    lat, lng, res, out = min(max(lat, -90), 89.999999) + 90, ((lng + 180) % 360), 20.0, ""
    for _i in range(n // 2):
        a, b = int(lat // res), int(lng // res)
        out += OLC_ALPHABET[a] + OLC_ALPHABET[b]
        lat, lng, res = lat - a * res, lng - b * res, res / 20
    return out


def decoder_plus_code(code: str, reference=DEPOT_DEFAUT):
    """(lat, lng) d'un Plus Code complet, ou court complété autour de `reference` ; None si invalide ou si le code
    court tombe à plus de PLUS_CODE_PORTEE_KM de la référence (ambiguïté). PURE."""
    code = (code or "").strip().upper()
    if "+" not in code:
        return None
    avant = code.index("+")
    if avant > 8 or avant % 2 or len(code) - avant - 1 < 2:
        return None
    if avant == 8:
        return _olc_decoder(code)
    manque = 8 - avant
    res = 20.0 ** (2 - manque // 2)              # taille de la cellule que le préfixe désigne (1° pour 4 caractères)
    centre = _olc_decoder(_olc_prefixe(reference[0], reference[1], manque) + code)
    if not centre:
        return None
    lat, lng = centre
    if reference[0] + res / 2 < lat:
        lat -= res
    elif reference[0] - res / 2 > lat:
        lat += res
    if reference[1] + res / 2 < lng:
        lng -= res
    elif reference[1] - res / 2 > lng:
        lng += res
    if haversine_km((lat, lng), reference) > PLUS_CODE_PORTEE_KM:
        return None
    return (round(lat, 6), round(lng, 6))


def coordonnees_plus_code(url: str | None, reference=DEPOT_DEFAUT):
    """(lat, lng) d'après le Plus Code d'un lien Google Maps développé (« /place/V59C+242 … »), sinon None. PURE."""
    from urllib.parse import unquote
    m = RE_PLUS_CODE.search(unquote(url or ""))
    return decoder_plus_code(m.group(1), reference) if m else None


LIEN_CACHE = "tournee_lien:"
LIEN_CACHE_OK = 30 * 24 * 3600          # un lien court ne change pas de destination
LIEN_CACHE_VIDE = 6 * 3600              # rien de lisible (ou réseau absent) : on réessaie plus tard, pas à chaque écran


def resoudre_lien(url: str | None, timeout: float = 6.0):
    """Les coordonnées d'un lien Google Maps, lien court compris (maps.app.goo.gl → redirection vers le
    lien développé, qui porte « !3d<lat>!4d<lng> »). None si rien n'est lisible ou sans réseau.

    ⚠️ MÉMORISÉ. Chaque lien court coûte une requête à Google (≈ 0,2 s) : sans mémoire, la liste des
    employés du jour et chaque « Calculer » les résolvaient TOUS à nouveau — 1,5 à 2,5 s d'attente à chaque
    changement de date (05/10/2026). Résultat gardé 30 jours ; « rien de lisible » gardé 6 h."""
    if not url:
        return None
    direct = coordonnees_du_lien(url)
    if direct:
        return direct
    if frappe.flags.get("tournee_sans_reseau"):
        return None
    cle = LIEN_CACHE + hashlib.sha1(url.strip().encode()).hexdigest()
    deja = frappe.cache().get_value(cle, expires=True)          # expires=True : pas de valeur figée dans la requête
    if deja is not None:
        return tuple(deja) if deja else None
    c = _resoudre_par_le_reseau(url, timeout)
    frappe.cache().set_value(cle, list(c) if c else [], expires_in_sec=LIEN_CACHE_OK if c else LIEN_CACHE_VIDE)
    return c


def _resoudre_par_le_reseau(url: str, timeout: float):
    courant = url
    try:
        for _i in range(4):
            r = requests.head(courant, allow_redirects=False, timeout=timeout,
                              headers={"User-Agent": "Mozilla/5.0 (aquaworld-erpnext)"})
            suivant = r.headers.get("location")
            if not suivant:
                break
            # Coordonnées écrites dans le lien d'abord (le point exact du lieu), sinon son Plus Code.
            c = coordonnees_du_lien(suivant) or coordonnees_plus_code(suivant)
            if c:
                return c
            courant = suivant
    except Exception:
        return None
    return None


MARQUE_LIEN_MORT = "lien mort"


def controle_localisation_actif():
    from customization_app.geocodage_adresses import reglages
    return reglages()["actif"]


def geocoder_texte(adresse_texte: str, ville: str | None = None):
    """Compatibilité : seul un résultat jugé fiable est utilisable comme point."""
    if frappe.flags.get("tournee_sans_reseau") or not (adresse_texte or ville):
        return None
    from customization_app.geocodage_adresses import rechercher
    r = rechercher({"address_line1": adresse_texte, "city": ville, "country": "Tunisia"})
    return tuple(r["point"]) if r.get("point") else None


PLAUSIBLE_KM = 30.0                      # un point lu dans le texte à plus de 30 km du centre de son secteur est faux
TEXTE_HORS_SECTEUR = "texte ≈ (hors secteur)"


def plausible(c, secteur: str | None, centres: dict | None = None, max_km: float = PLAUSIBLE_KM) -> bool:
    """Un point géocodé d'après le TEXTE est-il crédible pour son secteur ? Nominatim lit « Cité Ennasr, Ariana » à
    Mateur (51 km) ou « Secteur 2 » du côté de Bizerte : 26 adresses du Secteur 2 à plus de 30 km de son centre le
    16/10/2026 — deux clients fantômes à 65 km rendaient la journée du 17/10 infaisable (Mariem et Nabil laissées de
    côté). Sans secteur connu (Hors Secteur, vide) on ne juge pas. PURE si `centres` est donné."""
    if not c or not secteur or secteur == "Hors Secteur":
        return True
    centres = centres if centres is not None else _centres_secteurs()
    centre = centres.get(secteur)
    return not centre or haversine_km((c[0], c[1]), centre) <= max_km


def _geocoder_adresse(nom: str, texte: bool = True):
    """Point fiable/validé ; les anciens points texte restent approximatifs tant que
    le nouveau contrôle n'est pas activé. Aucun repli réseau sur un centre de ville."""
    if not nom:
        return None
    from customization_app.geocodage_adresses import resoudre_adresse
    if not controle_localisation_actif():
        a = frappe.db.get_value("Address", nom,
            ["custom_latitude", "custom_longitude", "custom_geocode_source", "custom_geocode_statut", "custom_secteur"], as_dict=True)
        if a and not a.custom_geocode_statut and (a.custom_geocode_source or "").startswith("texte"):
            c = (flt(a.custom_latitude), flt(a.custom_longitude))
            if c[0] and c[1] and plausible(c, a.custom_secteur):
                return c
    r = resoudre_adresse(nom, texte=texte, reseau=not frappe.flags.get("tournee_sans_reseau"))
    return tuple(r["point"]) if r.get("point") else None


def reprendre_liens_sans_coordonnees(limite: int = 500, ecrire: bool = True) -> dict:
    """Reprise (08/10/2026) des adresses dont le lien n'avait rien donné (« lien mort », position « texte ≈ (lien mort) »)
    maintenant que le Plus Code du lien se lit : le lien est relu SANS le cache, la position trouvée remplace
    l'approximation. `ecrire=False` : simple mesure. → {vues, reprises, toujours_sans, exemples}."""
    adresses = frappe.db.sql("""select name, custom_lien_google_map lien, custom_latitude lat, custom_longitude lng,
                                       custom_geocode_source src
                                from tabAddress
                                where ifnull(custom_lien_google_map, '') != ''
                                  and (custom_geocode_source like %(mort)s or custom_geocode_source like %(texte)s)
                                limit %(limite)s""",
                             {"mort": MARQUE_LIEN_MORT + "%", "texte": "texte%" + MARQUE_LIEN_MORT + "%", "limite": cint(limite)},
                             as_dict=True)
    reprises, sans, exemples, plus_de_5_km = 0, 0, [], 0
    for a in adresses:
        c = coordonnees_du_lien(a.lien) or coordonnees_plus_code(a.lien) or _resoudre_par_le_reseau(a.lien.strip(), 8.0)
        if not c:
            sans += 1
            continue
        reprises += 1
        if a.lat and haversine_km((flt(a.lat), flt(a.lng)), c) > 5:
            plus_de_5_km += 1
        if len(exemples) < 10:
            exemples.append({"adresse": a.name, "avant": [flt(a.lat), flt(a.lng)] if a.lat else None, "apres": list(c),
                             "ecart_km": round(haversine_km((flt(a.lat), flt(a.lng)), c), 1) if a.lat else None})
        if ecrire:
            _memoriser(a.name, c, "lien adresse (plus code)")
            frappe.cache().set_value(LIEN_CACHE + hashlib.sha1(a.lien.strip().encode()).hexdigest(), list(c),
                                     expires_in_sec=LIEN_CACHE_OK)
    if ecrire:
        frappe.db.commit()
    return {"vues": len(adresses), "reprises": reprises, "toujours_sans": sans, "plus_de_5_km": plus_de_5_km, "exemples": exemples}


def geocoder_adresses(limite: int = 500) -> dict:
    """Traitement borné, sans remplacer les positions connues par une approximation."""
    from customization_app.geocodage_adresses import resoudre_adresse, purger_expirees
    purger_expirees()
    bilan = {"liens": 0, "textes": 0, "morts": 0, "restantes": 0, "a_verifier": 0}
    if not controle_localisation_actif():
        return dict(bilan, desactive=True)
    adresses = frappe.get_list("Address", filters={"disabled": 0, "country": "Tunisia"},
        or_filters={"custom_latitude": ["=", 0], "custom_geocode_source": ["like", "texte%"],
                    "custom_geocode_statut": ["in", ["À vérifier", "Introuvable", "Indisponible"]]},
        pluck="name", order_by="modified desc", limit_page_length=max(1, min(cint(limite) or 500, 1000)))
    for nom in adresses:
        r = resoudre_adresse(nom)
        if r.get("point"):
            bilan["textes" if r.get("choix") else "liens"] += 1
        else:
            bilan["a_verifier"] += 1
        frappe.db.commit()
        if "Plafond" in (r.get("motif") or ""):
            break
    bilan["restantes"] = frappe.db.count("Address", {"disabled": 0, "custom_geocode_statut": "À vérifier"})
    return bilan


def geocodage_quotidien():
    """Actualise uniquement les positions des tâches ouvertes des 14 prochains jours."""
    try:
        from customization_app.geocodage_adresses import purger_expirees
        purger_expirees()
        if controle_localisation_actif():
            prechauffer_positions(jours=14)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "tournées : positions des tâches à venir")


def prechauffer_positions(jours: int = 14) -> int:
    """Résout (et mémorise) la position de chaque tâche ouverte des `jours` prochains jours. -> nombre de tâches."""
    debut = frappe.utils.nowdate()
    taches = frappe.get_all(TACHE, filters={"starts_on": ["between", ["%s 00:00:00" % debut, "%s 23:59:59" % frappe.utils.add_days(debut, jours)]],
                                           "status": "Open"},
                            fields=["name", "google_map", "select_address", "secteur"], limit_page_length=0)
    centres = _centres_secteurs()
    for t in taches:
        coordonnees_tache(t, centres)
        frappe.db.commit()          # les positions mémorisées sur les adresses tiennent même si le cron s'arrête
    return len(taches)


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
    if not controle_localisation_actif():
        frappe.throw("Activez explicitement la recherche des adresses clients dans les réglages avant de lancer ce traitement.")
    frappe.enqueue("customization_app.tournee_optimisation.geocoder_adresses", queue="long", timeout=3600,
                   limite=cint(limite) or 500, enqueue_after_commit=True)
    return etat_geocodage()


def _memoriser(adresse: str, c, source: str):
    if adresse and frappe.db.has_column("Address", "custom_latitude"):
        if frappe.flags.get("tournee_sans_reseau"):
            return
        from customization_app.geocodage_adresses import signature
        a = frappe.get_doc("Address", adresse)
        valeurs = {"custom_latitude": c[0], "custom_longitude": c[1], "custom_geocode_source": source}
        if frappe.db.has_column("Address", "custom_geocode_statut"):
            valeurs.update(custom_geocode_statut="Validée", custom_geocode_signature=signature(a),
                           custom_geocode_expire_le=None, custom_geocode_motif="Position issue d’un lien.", custom_geocode_valide_par=None)
        frappe.db.set_value("Address", adresse, valeurs, update_modified=False)


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


GRILLE_GROSSIERE = 37          # pas de la grille (1/37° ≈ 3 km) des positions réseau/IP rendues par un téléphone sans GPS
GROSSIERE = "grossière"


def position_grossiere(c) -> bool:
    """Vrai pour une position RÉSEAU/IP (pas le GPS) : le téléphone la rend sur une grille de 1/37° en latitude
    (La Goulette 36.810811 × 37 = 1362.000007). Le bouton « 📍 Ma position » de la clôture en a écrit 31 sur 156 liens
    q=lat,lng depuis 09/2026 (15/10/2026) : une vraie position tombe sur la grille moins d'une fois sur 10 000. PURE."""
    if not c or not c[0]:
        return False
    x = flt(c[0]) * GRILLE_GROSSIERE
    return abs(x - round(x)) < 5e-5


def coordonnees_tache(t, centres: dict):
    """(lat, lng, source) — source : « tâche », « adresse », « secteur » ou « grossière » (approximatifs) ou None.

    « grossière » : un lien de tâche (ou mémorisé depuis un lien de tâche) sur la grille 1/37° — position réseau du
    téléphone, à 1-3 km du client ; on la donne comme approchée, et on ne la mémorise jamais sur l'adresse
    (13 des 16 tâches à lien grossier finissaient « aucun passage », 15/10/2026)."""
    from customization_app import geocodage_adresses as A
    from customization_app.geocodage_liens import resoudre_lien as lire_lien, lien_google
    a = frappe.get_doc("Address", t.select_address) if t.get("select_address") else None
    if a and (a.get("custom_geocode_valide_par") or not t.get("google_map")
              or t.get("google_map") == a.get("custom_lien_google_map")):
        c = A.position_memorisee(a)
        if c:
            return c[0], c[1], "Google Maps" if A.source_google(a.get("custom_geocode_source")) else "adresse"
    reseau = not frappe.flags.get("tournee_sans_reseau")
    detail = lire_lien(t.get("google_map"), reseau=reseau)
    c = detail.get("point") if detail else None
    if c and (detail.get("choix") or {}).get("service") == "google":
        if a and reseau and t.get("google_map") == a.get("custom_lien_google_map"):
            A._memoriser(a, detail)
        return c[0], c[1], "Google Maps"
    if not c and not lien_google(t.get("google_map")):
        c = resoudre_lien(t.get("google_map"))
    grossiere = c if c and position_grossiere(c) else None
    if c and not grossiere:
        if t.get("select_address") and frappe.db.has_column("Address", "custom_latitude"):
            lat, src = frappe.db.get_value("Address", t.select_address, ["custom_latitude", "custom_geocode_source"]) or (None, None)
            # Vide, ou seulement approchée d'après le texte (Faleh Wajdi : « Cité Ennasr 2 » lu à Mateur, 55 km, 09/10/2026),
            # ou mémorisée depuis un lien de tâche grossier : le lien de la tâche est plus sûr — l'adresse le garde pour
            # les autres tâches du client.
            if not lat or (src or "").startswith("texte") or (src == "lien tâche" and position_grossiere((lat, 0))):
                _memoriser(t.select_address, c, "lien tâche")
        return c[0], c[1], "tâche"
    if a and controle_localisation_actif():
        r = A.resoudre_pour_tournee(a.name, reseau=reseau, secteur=t.get("secteur"), centres=centres)
        if r.get("point"):
            google = A.source_google(r.get("source")) or (r.get("choix") or {}).get("service") == "google"
            source = ("Google Maps estimé" if google else "texte estimé") if r.get("estimee") else ("Google Maps" if google else "adresse")
            return r["point"][0], r["point"][1], source
        c = None
    else:
        c = _geocoder_adresse(t.get("select_address"))
    if c:
        src = frappe.db.get_value("Address", t.select_address, "custom_geocode_source") or ""
        if src == "lien tâche" and position_grossiere(c):
            return c[0], c[1], GROSSIERE
        statut = frappe.db.get_value("Address", t.select_address, "custom_geocode_statut") or ""
        if A.source_google(src) and statut in ("Fiable", "Validée"):
            return c[0], c[1], "Google Maps"
        return c[0], c[1], ("secteur" if src.startswith("texte") and statut not in ("Fiable", "Validée") else "adresse")
    if grossiere:
        return grossiere[0], grossiere[1], GROSSIERE
    if t.get("secteur") in centres:
        c = centres[t.secteur]
        return c[0], c[1], "secteur"
    return None


def intervention_terrain(t, cfg):
    return t.get("custom_type_dintervention") in cfg["types"] and not (
        t.get("custom_type_dintervention") == "Réparation" and (t.get("dans_local") or "") == "Oui")


def position_estimee(source):
    return source in ("secteur", GROSSIERE, "Google Maps estimé", "texte estimé")


def position_a_verifier(t, pos, cfg):
    """Qualité indicative ; une estimation ne bloque plus le calcul automatique."""
    return intervention_terrain(t, cfg) and (not pos or position_estimee(pos[2]))


def motif_position_a_verifier(t, pos=None):
    """Explique le blocage avec le diagnostic déjà enregistré, sans appel réseau."""
    if pos and pos[2] == GROSSIERE:
        return "Position du téléphone trop approximative : renseignez un point GPS confirmé."
    if not t.get("select_address"):
        return "Aucune fiche Adresse associée : renseignez l’adresse ou un lien GPS sur la tâche."
    motif = frappe.db.get_value("Address", t.select_address, "custom_geocode_motif")
    return motif or "Position absente ou approximative : confirmez le point sur la fiche Adresse."


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


def matrice(points: list, osrm: str, ecarts: list | None = None) -> tuple[list, list, str]:
    """(minutes, km, source) entre tous les points, par OSRM ; repli à vol d'oiseau si injoignable.
    `ecarts` (liste fournie) reçoit, par point, sa distance en mètres à la route la plus proche (0 sans OSRM)."""
    if ecarts is not None:
        ecarts[:] = [0.0] * len(points)
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
        if ecarts is not None and len(d.get("sources") or []) == len(points):
            ecarts[:] = [flt((w or {}).get("distance")) for w in d["sources"]]
        return mn, km, "osrm"
    except Exception:
        frappe.log_error(frappe.get_traceback()[-800:], "tournées : OSRM injoignable, repli vol d’oiseau")
        return matrice_haversine(points)


HORS_ROUTE_M = 200            # un point à plus de 200 m de la route la plus proche n'est pas une adresse


def hors_route(ecarts: list, noeuds: list, seuil: int = HORS_ROUTE_M) -> list:
    """Les arrêts dont le point est loin de toute route (OSRM donne, pour chaque point, la distance à sa projection sur
    le réseau) : un point géocodé dans le lac, en mer ou dans un terrain vague (18/10/2026 : Fondation Kamel LAZA,
    « Jardin du Lac » lu d'après le texte à 314 m de la route, au SUD du lac de Tunis, à 3 km de la vraie rue → OSRM
    contournait le lac, 45 min depuis La Soukra au lieu de 17, et la journée ne tenait plus). PURE."""
    return [n for n in noeuds if n < len(ecarts) and flt(ecarts[n]) > seuil]


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
             debuts: list | None = None, fins: list | None = None, rangement: int = 0,
             depassements: list | None = None, vehicules_equilibres: list | None = None,
             charges_fixes: list | None = None) -> dict:
    """Tournées à fenêtres de temps (OR-Tools). `minutes` couvre tous les nœuds : les dépôts (nœud 0 = Magasin,
    puis les points de départ particuliers) et les arrêts, qui occupent les DERNIERS nœuds. `arrets[i]` :
    {service: min, fenetre: (a, b) | None, vehicule: idx | None}. `depots[v]` = nœud de départ ET de retour du
    véhicule v (0 par défaut). Les temps sont en minutes depuis minuit. `depassements[v]` : minutes que le véhicule v peut
    finir après sa fin de journée, chacune coûtant COUT_DEPASSEMENT (0 = fin stricte).
    → {"routes": [[(noeud, arrivee), …] par véhicule], "non_places": [noeuds], "cout": minutes de route}."""
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    n = len(minutes)
    nb_depots = n - len(arrets)
    depots = [cint(d) for d in (depots or [0] * nb_vehicules)]
    manager = pywrapcp.RoutingIndexManager(n, nb_vehicules, depots, depots)
    routing = pywrapcp.RoutingModel(manager)
    service = [0] * nb_depots + [cint(a.get("service")) for a in arrets]
    # Marge (stationnement, accueil) à l'arrivée sur un arrêt — sauf un arrêt « au Magasin » (tâche sans lieu :
    # vérification de stock, réunion…) : on y est déjà. Sans cette exception, une telle tâche fixée au début de la
    # journée (09:00 pour une journée qui commence à 09:00) n'était « atteignable » qu'à 09:20 et finissait en
    # « tâche qui ne tient pas dans la tournée » (Tache-08926, 06/10/2026).
    marges = [0] * nb_depots + [0 if a.get("sans_marge") else cint(marge) for a in arrets]
    # Rangement (ranger ses affaires dans la voiture) après la fin d'une tâche, avant de repartir (05/10/2026).
    rangements = [0] * nb_depots + [0 if a.get("sans_marge") else cint(rangement) for a in arrets]

    def transit(i, j):
        # service au départ + rangement + route + marge à l'arrivée sur un arrêt
        a, b = manager.IndexToNode(i), manager.IndexToNode(j)
        return minutes[a][b] + service[a] + rangements[a] + marges[b]

    cb = routing.RegisterTransitCallback(transit)
    routing.SetArcCostEvaluatorOfAllVehicles(cb)
    debuts = [cint(x) for x in (debuts or [debut] * nb_vehicules)]       # début de journée par véhicule
    fins = [cint(x) for x in (fins or [fin] * nb_vehicules)]             # fin de journée par véhicule (retour compris)
    deps = [max(0, cint(x)) for x in (depassements or [0] * nb_vehicules)]
    fin_max = max(f + d for f, d in zip(fins, deps))                     # la plus tardive, dépassement compris
    horizon = max(fin_max, max((a["fenetre"][1] + cint(a.get("service")) for a in arrets if a.get("fenetre")), default=fin)) + 1
    routing.AddDimension(cb, horizon, horizon, False, "Temps")
    temps = routing.GetDimensionOrDie("Temps")
    # Charge active : intervention + route + stationnement + rangement, sans
    # attente ni pause. Les autres employés ont un coût d'équilibrage nul.
    groupes = set(range(nb_vehicules) if vehicules_equilibres is None else vehicules_equilibres)
    if equilibre > 0 and len(groupes) > 1:
        fixes = charges_fixes if charges_fixes is not None else [
            sum(max(0, f - d) for d, f in (occupations or {}).get(v, [])) for v in range(nb_vehicules)]
        zero = routing.RegisterTransitCallback(lambda i, j: 0)
        evaluateurs = []
        for v in range(nb_vehicules):
            if v not in groupes:
                evaluateurs.append(zero)
                continue
            def charge(i, j, v=v):
                return transit(i, j) + (cint(fixes[v]) if routing.IsStart(i) else 0)
            evaluateurs.append(routing.RegisterTransitCallback(charge))
        capacite = sum(service) + sum(marges) + sum(rangements) + sum(fixes) + n * max(max(ligne) for ligne in minutes) + 1
        routing.AddDimensionWithVehicleTransits(evaluateurs, 0, int(capacite), True, "Charge")
        routing.GetDimensionOrDie("Charge").SetGlobalSpanCostCoefficient(2 * cint(equilibre))
    # Créneaux où un véhicule est OCCUPÉ hors tournée (tâche qu'on n'a pas pu placer, gardée telle quelle) :
    # des pauses imposées, pour que les autres arrêts ne viennent pas se poser dessus.
    visites = [service[manager.IndexToNode(i)] for i in range(routing.Size())]
    for v in range(nb_vehicules):
        occ = fusionner_intervalles([(cint(d), cint(f)) for d, f in (occupations or {}).get(v, []) if f > d])
        # Sa journée finit au plus tôt à la fin de son dernier créneau occupé.
        # Cette amplitude reste distincte de sa charge de travail active.
        fin_min = max(debuts[v], occ[-1][1] if occ else debuts[v])
        temps.CumulVar(routing.Start(v)).SetRange(debuts[v], debuts[v])
        fin_v = max(fins[v], fin_min)
        temps.CumulVar(routing.End(v)).SetRange(fin_min, fin_v + deps[v] + 1)
        if deps[v]:
            # Au-delà de la fin de journée : permis jusqu'à deps[v] minutes, mais chaque minute est pénalisée.
            temps.SetCumulVarSoftUpperBound(routing.End(v), fin_v + 1, COUT_DEPASSEMENT)
        routing.AddVariableMinimizedByFinalizer(temps.CumulVar(routing.End(v)))
        if occ:
            solver = routing.solver()
            temps.SetBreakIntervalsOfVehicle(
                [solver.FixedDurationIntervalVar(d, d, f - d, False, "occupe_%d_%d" % (v, k)) for k, (d, f) in enumerate(occ)], v, visites)
    for k, a in enumerate(arrets):
        idx = manager.NodeToIndex(nb_depots + k)
        fen = a.get("fenetre") or (max(debut, premiere or debut), fin_max)       # pas de visite libre avant « première »
        impossible = cint(fen[0]) > cint(fen[1])
        if impossible:
            # Aucune intersection entre la fenêtre promise et les heures de visite.
            # L'arrêt reste non placé, sans élargir sa fenêtre ni casser le solveur.
            routing.ActiveVar(idx).SetValue(0)
        else:
            temps.CumulVar(idx).SetRange(cint(fen[0]), cint(fen[1]))
        if pause and not a.get("fixe_pause") and not impossible:
            # Pas d'intervention à cheval sur la pause : elle finit avant, ou commence après.
            temps.CumulVar(idx).RemoveInterval(max(cint(pause[0]) - cint(a.get("service")) + 1, cint(fen[0])), cint(pause[1]) - 1)
        if a.get("vehicule") is not None:
            # Le véhicule imposé, ET −1 (« non desservi ») pour rester compatible avec la disjonction
            # ci-dessous — sans −1, le modèle est insoluble dès qu'une tâche est fixée.
            routing.VehicleVar(idx).SetValues([-1, int(a["vehicule"])])
        elif a.get("vehicules") is not None:
            routing.VehicleVar(idx).SetValues([-1] + [int(v) for v in a["vehicules"]])
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

# Épingles d'une tâche (08/10/2026 : une seule case « Heure et employé fixes » devient deux) : « Heure fixe »
# (custom_tournee_fixe — l'heure promise ne bouge pas, un autre employé peut la faire) et « Employé fixe »
# (custom_employe_fixe — le technicien ne change pas, l'heure peut bouger). Les deux cochées = rien ne bouge.
HEURE_FIXE, EMPLOYE_FIXE = "custom_tournee_fixe", "custom_employe_fixe"


def _champs_epingles() -> list[str]:
    return [c for c in (HEURE_FIXE, EMPLOYE_FIXE) if frappe.db.has_column(TACHE, c)]


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


def employes_techniciens(employes):
    """Rôle effectif du compte lié, sans déduire le métier du nom ou de l'intitulé."""
    noms = tuple(sorted({e for e in employes if e}))
    if not noms:
        return set()
    return {r[0] for r in frappe.db.sql("""select distinct e.name
        from tabEmployee e join tabUser u on u.name=e.user_id and u.enabled=1
        join `tabHas Role` r on r.parent=u.name and r.parenttype='User' and r.role='Technicien'
        where e.status='Active' and e.name in %(employes)s""", {"employes": noms})}


def charge_travail(arrets, trajets, marge, rangement):
    """Minutes de travail prévues, indépendantes des heures de départ/fin et de l'attente."""
    interventions = sum(cint(a.get("service")) for a in arrets)
    visites = sum(a.get("position") != "dépôt" for a in arrets)
    resultat = {"interventions": interventions, "trajets": cint(trajets),
                "stationnement": visites * cint(marge), "rangement": visites * cint(rangement)}
    resultat["total"] = sum(resultat.values())
    return resultat


@frappe.whitelist()
def reglages_calcul():
    """Les valeurs du réglage que la fenêtre d'optimisation propose par défaut (fenêtre ±, dépassement permis)."""
    _superviseur()
    cfg = config()
    return {"fenetre": cfg["fenetre"], "depassement": cfg["depassement"]}


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
    techniciens = employes_techniciens([r.employe for r in rows])
    out = [{"employe": r.employe, "nom": noms.get(r.employe, r.employe), "taches": cint(r.n), "mobiles": cint(r.mobiles),
            "technicien": r.employe in techniciens,
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
                                    *_champs_epingles()],
                            order_by="starts_on asc", limit_page_length=0)
    centres = _centres_secteurs()
    out = {}
    for t in taches:
        if t.custom_type_dintervention in TYPES_HORS_JOURNEE:
            continue
        d, f = get_datetime(t.starts_on), get_datetime(t.ends_on) if t.ends_on else None
        pos = coordonnees_tache(t, centres)
        heure_fixe, employe_fixe = cint(t.get(HEURE_FIXE)), cint(t.get(EMPLOYE_FIXE))
        if heure_fixe and employe_fixe:
            motif = "📌 heure et employé fixes"
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
            # Déplaçable, mais à moitié : ce qui reste imposé.
            "contrainte": "🕘 heure fixe" if heure_fixe and not employe_fixe else "👤 employé fixe" if employe_fixe and not heure_fixe else "",
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
def proposer(date, fenetre=None, employes=None, depassement=None):
    """La proposition pour la journée : par employé, tournée actuelle et tournée optimisée (ordre, heures,
    km, minutes de route), tâches déplacées, approximations et tâches impossibles à placer. RIEN N'EST ÉCRIT.
    `depassement` : minutes permises après la fin de journée pour ce calcul (vide = réglage)."""
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
                                    *_champs_epingles()],
                            order_by="starts_on asc", limit_page_length=0)
    centres = _centres_secteurs()
    base = jour.strftime("%Y-%m-%d")
    points, arrets, avert = [cfg["depot"]], [], []
    controle_position = controle_localisation_actif()
    candidates = [t for t in taches if t.custom_type_dintervention not in TYPES_HORS_JOURNEE
                  and t.custom_choix_du_staff not in cfg["exclus"]]
    positions = {t.name: coordonnees_tache(t, centres) for t in candidates}
    # Sans aucun point (ni lien, ni géocodage, ni secteur), conserver la journée
    # de l'employé : on ne peut pas calculer ses trajets ni lui ajouter des visites.
    sans_position = {t.custom_choix_du_staff for t in candidates
                     if controle_position and intervention_terrain(t, cfg) and not positions[t.name]}
    conservees = [{"tache": t.name, "client": t.nom_client or t.custom_client or "", "employe": t.custom_choix_du_staff}
                 for t in candidates if t.custom_choix_du_staff in sans_position]
    if conservees:
        avert.append(_("{0} tâche(s) conservées aux heures et employés actuels : une position est introuvable dans leur tournée. Les autres tournées sont optimisées.").format(len(conservees)))
    estimees = []
    for t in taches:
        if t.custom_type_dintervention in TYPES_HORS_JOURNEE or t.custom_choix_du_staff in cfg["exclus"] or t.custom_choix_du_staff in sans_position:
            continue
        debut = get_datetime(t.starts_on)
        dmin = debut.hour * 60 + debut.minute
        service = _duree(t)
        heure_fixe, employe_fixe = bool(cint(t.get(HEURE_FIXE))), bool(cint(t.get(EMPLOYE_FIXE)))
        mobile = (t.custom_type_dintervention in cfg["types"] and t.status == "Open" and not (heure_fixe and employe_fixe)
                  and not (t.custom_type_dintervention == "Réparation" and (t.dans_local or "") == "Oui"))
        pos = positions[t.name]
        if pos and position_estimee(pos[2]):
            estimees.append({"tache": t.name, "client": t.nom_client or t.custom_client or "", "source": pos[2]})
        if not pos:
            if mobile:
                avert.append(_("{0} ({1}) : pas de position connue — gardée telle quelle").format(t.name, t.nom_client or t.custom_client or ""))
                mobile = False
            if not (cfg["debut"] - 60 <= dmin <= cfg["fin"] + 60):
                continue        # une tâche sans lieu, hors journée (ex. « Autre » à minuit) : pas une étape
            pos = (cfg["depot"][0], cfg["depot"][1], "dépôt")
        elif pos[2] == "secteur" and mobile:
            # « secteur » couvre deux cas : le centre du secteur (aucune position), ou le point trouvé d'après le TEXTE
            # de l'adresse (OpenStreetMap, rue ou quartier) — le dire tel quel.
            d_ou = (_("par le centre du {0}").format(t.secteur) if centres.get(t.secteur) == (pos[0], pos[1])
                    else _("d’après une ancienne approximation de l’adresse"))
            avert.append(_("{0} ({1}) : position approchée {2}").format(t.name, t.nom_client or t.custom_client or "", d_ou))
        points.append((pos[0], pos[1]))
        arrets.append({"noeud": len(points) - 1, "tache": t.name, "client": t.nom_client or t.custom_client or "", "type": t.custom_type_dintervention,
                       "employe": t.custom_choix_du_staff, "debut": dmin, "debut_exact": dmin + debut.second / 60,
                       "service": service, "mobile": mobile, "statut": t.status,
                       "heure_fixe": heure_fixe, "employe_fixe": employe_fixe,
                       "position": pos[2], "lat": pos[0], "lng": pos[1], "adresse": t.details_adresse or "", "secteur": t.secteur or ""})
    employes = sorted({a["employe"] for a in arrets})
    if not employes:
        return {"date": str(jour), "employes": [], "deplacees": 0, "decalees": 0, "conservees_sans_position": conservees,
                "message": avert[0] if conservees else _("Aucun employé n’a de tâche de terrain ce jour-là.")}
    noms = {e.name: e.employee_name for e in frappe.get_all("Employee", filters={"name": ["in", employes]}, fields=["name", "employee_name"])}
    techniciens = employes_techniciens(employes)
    vehicules_techniciens = [v for v, e in enumerate(employes) if e in techniciens]
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
    ecarts = []
    mn_brut, km, source = matrice(points, cfg["osrm"], ecarts)
    if source == "osrm":
        # Un point loin de toute route (dans le lac, en mer, terrain vague) trahit une position fausse : OSRM le rattache à
        # la route la plus proche, parfois de l'autre côté de l'eau, et le trajet devient un contournement. Approchée
        # (texte, grossière), la position est remplacée par le centre du secteur et la matrice refaite ; précise (lien),
        # on prévient de vérifier le lien.
        par_n = {a["noeud"]: a for a in arrets}
        refaire = False
        for n in hors_route(ecarts, sorted(par_n)):
            a = par_n[n]
            centre = centres.get(a["secteur"]) if position_estimee(a["position"]) else None
            if centre and centre != (a["lat"], a["lng"]):
                points[n] = centre
                a["lat"], a["lng"], a["position"] = centre[0], centre[1], "secteur"
                refaire = True
                avert.append(_("{0} ({1}) : position approchée à {2} m de toute route (dans le lac ou la mer ?) — remplacée par le centre du {3}").format(
                    a["tache"], a["client"], cint(ecarts[n]), a["secteur"]))
            else:
                avert.append(_("{0} ({1}) : position à {2} m de toute route (dans le lac ou la mer ?) — vérifiez le lien Google Maps de la tâche").format(
                    a["tache"], a["client"], cint(ecarts[n])))
        if refaire:
            mn_brut, km, source = matrice(points, cfg["osrm"])
    # OSRM sous-estime les trajets à Tunis (≈ ×1,3 mesuré avec Google le 05/10/2026) : l'ordre est choisi sur des temps
    # corrigés par le rapport Google / OSRM appris à chaque calcul ; les heures, elles, viennent de Google (recalage).
    mn = [[int(round(x * cfg["coef"])) for x in ligne] for ligne in mn_brut] if cfg["coef"] != 1 else mn_brut
    # Journée de CHAQUE employé : réglage global, puis ses horaires du réglage, puis ceux saisis dans la fenêtre.
    # Elle ne coupe jamais son travail déjà planifié (une tâche fixe plus tôt l'avance, une tâche qui finit
    # après l'étend, retour compris) — sinon le moteur laisserait des tâches de côté au lieu de les placer.
    debuts, fins, deps, voulues = [], [], [], []
    dep_calcul = cint(cfg.get("depassement")) if depassement in (None, "") else max(0, cint(depassement))
    for e in employes:
        d0, f0 = cfg["horaires"].get(e, (cfg["debut"], cfg["fin"]))
        hc = horaires_choisis.get(e, (None, None))
        fin_voulue = bool(hc[1])          # une fin saisie dans la fenêtre est VOULUE : ses tâches en trop vont ailleurs
        d0, f0 = hc[0] or d0, hc[1] or f0
        for a in arrets:
            if a["employe"] != e:
                continue
            heure_imposee = not a["mobile"] or a["heure_fixe"]
            if heure_imposee or not fin_voulue:
                f0 = max(f0, a["debut"] + a["service"] + cfg["rangement"] + mn[a["noeud"]][depot_de[e]])
            if heure_imposee:
                d0 = min(d0, a["debut"])
        debuts.append(d0)
        fins.append(f0)
        deps.append(0 if fin_voulue else dep_calcul)       # une fin saisie dans la fenêtre reste stricte
        voulues.append(fin_voulue)
    debut_j, fin_j = min(debuts), max(fins)
    deps_depart = list(deps)
    # Le solveur cherche 5 s par résolution ; une journée de quelques arrêts n'en a pas besoin (une proposition à un
    # seul employé prenait 1 min 30 : jusqu'à 12 résolutions).
    limite = max(2, min(5, len(arrets) // 3))
    fenetre = cint(cfg["fenetre"]) if fenetre is None else cint(fenetre)
    if fenetre < 0:
        frappe.throw(_("Le déplacement maximal ne peut pas être négatif."))

    def _noeud(a):
        if not a["mobile"]:
            return {"service": a["service"], "fenetre": (a["debut"], a["debut"]), "vehicule": employes.index(a["employe"]), "fixe_pause": True,
                    "sans_marge": a["position"] == "dépôt"}
        vehicule = employes.index(a["employe"]) if a["employe_fixe"] or a["employe"] not in techniciens else None
        if a["heure_fixe"]:
            # « Heure fixe » : l'heure promise ; redistribution entre techniciens seulement.
            a["fenetre"] = (a["debut"], a["debut"])
            return {"service": a["service"], "fenetre": a["fenetre"], "vehicule": vehicule,
                    "vehicules": vehicules_techniciens, "fixe_pause": True}
        # La fenêtre demandée est une limite stricte. Arrondir ses bornes vers
        # l'intérieur évite que l'arrondi final à 5 min dépasse la borne supérieure.
        fen = None if not fenetre else (
            int(math.ceil(max(cfg["premiere"], a["debut_exact"] - fenetre) / PAS_MIN) * PAS_MIN),
            int(math.floor(min(fin_j + max(deps), a["debut_exact"] + fenetre) / PAS_MIN) * PAS_MIN))
        a["fenetre"] = fen
        return {"service": a["service"], "fenetre": fen, "vehicule": vehicule, "vehicules": vehicules_techniciens}

    par_noeud = {a["noeud"]: a for a in arrets}
    # L'état de départ de chaque calcul. ⚠️ Les passes FIGENT les tâches qu'elles n'arrivent pas à placer (mobile=False,
    # journée étendue) : sans remise à zéro, le 2e calcul (heures de pointe) héritait des tâches figées par le 1er et ne
    # tentait même plus de les déplacer — JUS NATUR ALL restait à 10:20 au milieu de la réparation de Ghassen Mbarek
    # alors qu'elle se plaçait chez Mohamed Hedi à 10:55 (09/10/2026).
    mobiles_depart, fins_depart = {a["noeud"]: a["mobile"] for a in arrets}, list(fins)
    for a in arrets:
        a["fixe_initial"] = not a["mobile"]          # pour la colonne « Actuel » : les vraies épingles, pas les abandons

    surcharge, courant = {"fait": False}, {"avert": []}

    def _passes(mn_x):
        """Fenêtre demandée stricte, puis conservation des tâches impossibles à placer."""
        nonlocal fin_j
        deps[:] = deps_depart
        for a in arrets:
            a["mobile"] = mobiles_depart[a["noeud"]]
        fins[:] = fins_depart
        fin_j = max(fins)
        noeuds = [_noeud(a) for a in arrets]
        sol = resoudre(mn_x, noeuds, len(employes), debut_j, fin_j, cfg["equilibre"], premiere=cfg["premiere"], depots=depots,
                       marge=cfg["marge"], pause=cfg["pause"], debuts=debuts, fins=fins,
                       rangement=cfg["rangement"], depassements=deps, limite_s=limite, vehicules_equilibres=vehicules_techniciens)
        if sol["non_places"]:
            # DERNIER RECOURS avant de figer quoi que ce soit (18/10/2026, journée de Mohamed Hedi du 19/10 : 8 entretiens,
            # un rendez-vous à heure fixe à 16:30, la pause et les marges ne tenaient pas avant 18:00). Figer les tâches
            # sans place à leur heure d'origine les sort du modèle SANS leurs trajets : la tournée se construisait autour
            # comme si elles n'existaient pas (La Marsa à 09:45 puis Manouba à 10:30 à 40 min de route), avec des retards
            # partout, et chaque tâche figée en chassait une autre (6 figées sur 8 en fenêtre libre). Mieux vaut une
            # tournée complète et cohérente qui finit plus tard — dite en rouge — que ce chaos : on relâche la fin de
            # journée (sauf une fin SAISIE dans la fenêtre, voulue) et on réessaie en conservant la fenêtre stricte des rendez-vous.
            sans_limite = [0 if voulues[v] else max(deps_depart[v], DEPASSEMENT_SANS_LIMITE) for v in range(len(employes))]
            if sans_limite != deps_depart:
                deps[:] = sans_limite
                meilleure = sol
                for a in arrets:
                    a["mobile"] = mobiles_depart[a["noeud"]]
                fins[:] = fins_depart
                fin_j = max(fins)
                noeuds = [_noeud(a) for a in arrets]
                essai = resoudre(mn_x, noeuds, len(employes), debut_j, fin_j, cfg["equilibre"], premiere=cfg["premiere"], depots=depots,
                                 marge=cfg["marge"], pause=cfg["pause"], debuts=debuts, fins=fins,
                                 rangement=cfg["rangement"], depassements=deps, limite_s=limite, vehicules_equilibres=vehicules_techniciens)
                if len(essai["non_places"]) < len(meilleure["non_places"]):
                    meilleure = essai
                if meilleure is not sol:
                    sol = meilleure
                    if not surcharge["fait"]:
                        surcharge["fait"] = True
                        courant["avert"].append(_("Journée trop chargée : avec les contraintes demandées, tout ne tient pas avant la fin de journée{0}. "
                                       "La tournée proposée finit plus tard (retour en rouge) : déplacez une tâche à un autre jour, "
                                       "ou cochez un employé de plus.").format(
                            _(" (+{0} min permis)").format(dep_calcul) if dep_calcul else ""))
                else:
                    deps[:] = deps_depart
        if sol["non_places"]:
            # Ce qui n'a pas trouvé place reste où c'est (employé, heure) et la tournée se recalcule autour,
            # pour que la proposition soit complète et comparable à l'existant.
            for n in sol["non_places"]:
                a = par_noeud[n]
                a["mobile"] = False
                v = employes.index(a["employe"])
                fins[v] = max(fins[v], a["debut"] + a["service"] + cfg["rangement"] + mn_x[n][depot_de[a["employe"]]])
                fin_j = max(fins)
            noeuds = [_noeud(a) for a in arrets]
            sol = resoudre(mn_x, noeuds, len(employes), debut_j, fin_j, cfg["equilibre"], premiere=cfg["premiere"], depots=depots,
                           marge=cfg["marge"], pause=cfg["pause"], debuts=debuts, fins=fins,
                       rangement=cfg["rangement"], depassements=deps, limite_s=limite, vehicules_equilibres=vehicules_techniciens)
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
                fins[v] = max(fins[v], a["debut"] + a["service"] + cfg["rangement"] + mn_x[n][depot_de[a["employe"]]])
            fin_j = max(fins)
            restants = [a for a in arrets if a["noeud"] not in bloques]
            nb_depots = len(points) - len(arrets)
            garder = list(range(nb_depots)) + [a["noeud"] for a in restants]
            mn2 = [[mn_x[i][j] for j in garder] for i in garder]
            noeuds = [_noeud(a) for a in restants]
            charges_fixes = [charge_travail([par_noeud[n] for n in bloques if par_noeud[n]["employe"] == e],
                                            0, cfg["marge"], cfg["rangement"])["total"] for e in employes]
            sol2 = resoudre(mn2, noeuds, len(employes), debut_j, fin_j, cfg["equilibre"], premiere=cfg["premiere"], depots=depots,
                            marge=cfg["marge"], pause=cfg["pause"], occupations=occupations, debuts=debuts, fins=fins,
                            rangement=cfg["rangement"], depassements=deps, limite_s=limite, vehicules_equilibres=vehicules_techniciens, charges_fixes=charges_fixes)
            inverse = {nb_depots + i: a["noeud"] for i, a in enumerate(restants)}    # retour aux nœuds d'origine
            sol = {"routes": [[(inverse[n], t) for n, t in r] for r in sol2["routes"]],
                   "non_places": sorted({inverse[n] for n in sol2["non_places"]} - bloques), "cout": sol2["cout"]}
        sol["non_places"] = sorted(bloques | set(sol["non_places"]))
        deps[:] = deps_depart                              # l'écran affiche le dépassement DEMANDÉ, pas le dernier recours
        return sol

    def _calcul(mn_base, pointes: bool):
        """Un calcul complet : les passes sur la matrice donnée, puis (si demandé) sur la matrice majorée aux heures de
        pointe lues dans la 1re solution. → (solution, matrice des heures, messages des passes)."""
        surcharge["fait"] = False
        courant["avert"] = []
        sol = _passes(mn_base)
        mn_x = mn_base
        if pointes and cfg["pointes"]:
            # Heures de pointe : OSRM ne connaît pas le trafic. On lit l'heure de départ de chaque trajet dans la première
            # résolution, on majore les trajets qui partent en pointe, et on résout à nouveau.
            departs = {depots[v]: debuts[v] for v in range(len(employes))}
            for v, r in enumerate(sol["routes"]):
                for n, t in r:
                    departs[n] = t + par_noeud[n]["service"]
            mn_x = matrice_majoree(mn_base, departs, cfg["pointes"])
            sol = _passes(mn_x)
        return sol, mn_x, list(courant["avert"])

    def _construire(sol, mn_ap):
        """La proposition par employé (tournée actuelle et optimisée) d'après une solution du solveur."""
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
                deb = a["debut"] if not a["mobile"] or a["heure_fixe"] else int(math.ceil(arrivee / PAS_MIN) * PAS_MIN)
                serv_ap, fin_ap = serv_ap + a["service"], max(fin_ap, deb + a["service"])
                apres.append({"tache": a["tache"], "client": a["client"], "type": a["type"], "debut": _hm(deb), "fin": _hm(deb + a["service"]),
                              "starts_on": "%s %s:00" % (base, _hm(deb)), "ends_on": "%s %s:00" % (base, _hm(deb + a["service"])),
                              "employe": e, "de": a["employe"], "de_nom": noms.get(a["employe"], a["employe"]),
                              "deplace": a["employe"] != e, "decale": a["mobile"] and deb != a["debut"], "fixe": not a["mobile"],
                              "ancien_debut": _hm(a["debut"]), "ecart_min": deb - a["debut"],
                              "non_place": n in sol["non_places"], "heure_fixe": a["heure_fixe"], "employe_fixe": a["employe_fixe"],
                              "position": a["position"], "lat": a["lat"], "lng": a["lng"], "adresse": a["adresse"],
                              "noeud": n, "mobile": a["mobile"], "service": a["service"], "debut_min": deb, "original_min": a["debut"],
                              "fenetre": a.get("fenetre") if a["mobile"] else None})
            out.append({"employe": e, "nom": noms.get(e, e), "technicien": e in techniciens,
                        "equilibrage_charge": e in techniciens and len(techniciens) > 1 and cfg["equilibre"] > 0, "depart": "domicile" if dep else "Magasin", "depart_point": points[dep],
                        "depot_noeud": dep, "journee_min": [debuts[v], fins[v]], "depassement_autorise": deps[v],
                        "journee": [_hm(debuts[v]), _hm(fins[v])],
                        "avant": {"minutes": m_av, "km": k_av, "interventions": sum(a["service"] for a in actuels),
                                  "charge": charge_travail(actuels, m_av, cfg["marge"], cfg["rangement"]),
                                  "fin": _hm(max((a["debut"] + a["service"] for a in actuels), default=debuts[v])),
                                  "arrets": [{"tache": a["tache"], "client": a["client"], "type": a["type"],
                                              "debut": _hm(a["debut"]), "fin": _hm(a["debut"] + a["service"]),
                                              "fixe": a["fixe_initial"], "heure_fixe": a["heure_fixe"],
                                              "employe_fixe": a["employe_fixe"], "lat": a["lat"], "lng": a["lng"]}
                                             for a in sorted(actuels, key=lambda a: a["debut"])]},
                        "apres": {"minutes": m_ap, "km": k_ap, "interventions": serv_ap, "fin": _hm(fin_ap or debuts[v]), "arrets": apres}})
            tot_av, tot_ap, km_av, km_ap = tot_av + m_av, tot_ap + m_ap, km_av + k_av, km_ap + k_ap
        non_places = [{"tache": par_noeud[n]["tache"], "client": par_noeud[n]["client"], "employe": noms.get(par_noeud[n]["employe"])} for n in sol["non_places"]]
        return out, non_places, {"avant_min": tot_av, "apres_min": tot_ap, "avant_km": round(km_av, 1), "apres_km": round(km_ap, 1)}

    def _bilan(out, non_places) -> tuple:
        """Priorité aux tâches placées et ponctuelles, puis trajets et charge des techniciens."""
        retards = sum(cint(a.get("retard")) + (30 if a.get("hors_fenetre") else 0) for e in out for a in e["apres"]["arrets"])
        retour = max((_minutes((e.get("horaires") or {}).get("retour"), 0) for e in out), default=0)
        travail = [charge_travail(e["apres"]["arrets"], (e.get("horaires") or {}).get("trajets", e["apres"]["minutes"]),
                                  cfg["marge"], cfg["rangement"])["total"] for e in out if e["employe"] in techniciens]
        score = sum((e.get("horaires") or {}).get("trajets", e["apres"]["minutes"]) for e in out)
        if len(travail) > 1 and cfg["equilibre"] > 0:
            score += 2 * cfg["equilibre"] * max(travail)
        return (len(non_places), retards, score, retour)

    sol, mn_ap, avert_calcul = _calcul(mn, pointes=True)
    # La tournée ACTUELLE, majorée aux heures où elle roule vraiment : comparable à la proposition.
    departs_actuels = {depots[v]: debuts[v] for v in range(len(employes))}
    departs_actuels.update({a["noeud"]: a["debut"] + a["service"] for a in arrets})
    mn_av = matrice_majoree(mn, departs_actuels, cfg["pointes"]) if cfg["pointes"] else mn
    out, non_places, total = _construire(sol, mn_ap)
    # Les heures de la tournée choisie sont recalculées trajet par trajet avec Google (trafic habituel à l'heure de chaque
    # départ), avec stationnement et rangement : départ du Magasin, chaque tâche, retour (05/10/2026).
    from customization_app.tournee_google import recaler_proposition
    nouveaux_appels = 0

    def _recaler(proposition):
        nonlocal nouveaux_appels
        r = recaler_proposition(proposition, jour, cfg, mn_brut, mn, km)
        nouveaux_appels += cint(r.get("nouveaux_appels"))
        return r

    recalage = _recaler(out)
    mesures = recalage.pop("mesures", None) or {}
    if mesures and ((len(techniciens) > 1 and cfg["equilibre"] > 0)
                    or any(a.get("retard") or a.get("hors_fenetre") for e in out for a in e["apres"]["arrets"])):
        # SECOND TOUR avec les temps de Google (18/10/2026) : l'ordre était choisi sur OSRM × coef, puis les heures recalées
        # avec Google SANS re-planifier. Quand Google allonge un trajet, la tâche suivante peut basculer après la pause et
        # tout glisser : le 19/10, Sté L.K.X. passait à 16:50 et Noura Elouaer (16:30, heure fixe) prenait 93 min de retard.
        # On réinjecte les trajets mesurés dans la matrice (déjà avec trafic : pas de majoration de pointe) et on refait le
        # calcul ; on garde le meilleur des deux (sans place, retards, trajets et charge).
        mn_g = [ligne[:] for ligne in mn_ap]
        for (i, j), m in mesures.items():
            if 0 <= i < len(mn_g) and 0 <= j < len(mn_g):
                mn_g[i][j] = cint(m)
        sol2, mn_ap2, avert2 = _calcul(mn_g, pointes=False)
        out2, non_places2, total2 = _construire(sol2, mn_ap2)
        recalage2 = _recaler(out2)
        recalage2.pop("mesures", None)
        if _bilan(out2, non_places2) < _bilan(out, non_places):
            out, non_places, total, recalage, avert_calcul = out2, non_places2, total2, recalage2, avert2
            recalage["second_tour"] = True
            mn_ap = mn_ap2
    retablis, dernier_recalage = retablir_limite_horaire(out, fenetre, _recaler)
    if retablis:
        second_tour = recalage.get("second_tour")
        recalage = dernier_recalage
        recalage.pop("mesures", None)
        if second_tour:
            recalage["second_tour"] = True
        recalage["limite_horaire_appliquee"] = True
        non_places = list({a["tache"]: a for a in non_places + retablis}.values())
        avert_calcul.append(_("Limite stricte de ± {0} minutes : {1} rendez-vous gardé(s) à leur heure et chez leur employé initiaux après le recalcul des trajets.").format(fenetre, len(retablis)))
        # Le repli peut rendre une tâche à un autre employé : actualiser les bilans
        # de distance/temps de la matrice dans l'ordre effectivement affiché.
        for e in out:
            ordre = [e["depot_noeud"]] + [a["noeud"] for a in e["apres"]["arrets"]] + [e["depot_noeud"]]
            e["apres"]["minutes"] = sum(mn_ap[i][j] for i, j in zip(ordre, ordre[1:]))
            e["apres"]["km"] = round(sum(km[i][j] for i, j in zip(ordre, ordre[1:])), 1)
            e["apres"]["interventions"] = sum(a["service"] for a in e["apres"]["arrets"])
        total["apres_min"] = sum(e["apres"]["minutes"] for e in out)
        total["apres_km"] = round(sum(e["apres"]["km"] for e in out), 1)
    recalage["nouveaux_appels"] = nouveaux_appels
    avert.extend(avert_calcul)
    for e in out:
        h = e.get("horaires") or {}
        # Retour après la fin de journée : de combien, et est-ce dans le dépassement permis (affiché en orange, pas en rouge) ?
        h["depassement_min"] = max(0, _minutes(h["retour"], 0) - e["journee_min"][1]) if h.get("retour") else 0
        h["depassement_autorise"] = e["depassement_autorise"]
        e["apres"]["charge"] = charge_travail(e["apres"]["arrets"], h.get("trajets", e["apres"]["minutes"]),
                                                cfg["marge"], cfg["rangement"])
    return {"date": str(jour), "employes": out, "depot": cfg["depot"], "source": source, "recalage": recalage,
            "depassement": dep_calcul,
            "equilibrage": {"techniciens": sorted(techniciens), "actif": len(techniciens) > 1 and cfg["equilibre"] > 0,
                            "critere": "interventions + trajets + stationnement + rangement"},
            "rangement": cfg["rangement"], "coef": cfg["coef"],
            "total": total,
            "deplacees": sum(1 for e in out for a in e["apres"]["arrets"] if a["deplace"]),
            "decalees": sum(1 for e in out for a in e["apres"]["arrets"] if a["decale"] and not a["deplace"]),
            "non_places": non_places, "avertissements": avert, "positions_estimees": estimees, "conservees_sans_position": conservees,
            "journee": [_hm(debut_j), _hm(fin_j)], "premiere": _hm(cfg["premiere"]),
            "fenetre": fenetre, "marge": cfg["marge"], "pause": [_hm(cfg["pause"][0]), _hm(cfg["pause"][1])] if cfg["pause"] else None,
            "pointes": ["%s–%s +%d %%" % (_hm(d), _hm(f), pct) for d, f, pct in cfg["pointes"]],
            "sans_domicile": [noms.get(e, e) for e, p in choix.items() if p == "domicile" and e not in cfg["departs"] and e in noms],
            "prevenir": {"sms": cfg["prevenir"]["sms"], "email": cfg["prevenir"]["email"], "seuil": cfg["prevenir"]["seuil"], "plage": cfg["prevenir"]["plage"]},
            "notifications": notifications_proposees([a for e in out for a in e["apres"]["arrets"]], cfg)}


def reaffecter_titre(titre, anciens_noms, nouveau_nom):
    """La dernière ligne du titre porte le nom de l'employé (gabarit du script client et du portail) :
    elle est remplacée quand elle vaut un des anciens noms. Sinon le titre reste tel quel. Fonction pure."""
    if not titre:
        return titre
    lignes = titre.split("\n")
    anciens = {(a or "").strip() for a in anciens_noms if a}
    if lignes and lignes[-1].strip() in anciens:
        lignes[-1] = nouveau_nom
    return "\n".join(lignes)


def retablir_limite_horaire(employes, fenetre, recalculer):
    """Après le trafic, remet les visites hors limite à leur heure et employé initiaux.

    Chaque passage fige au moins une visite supplémentaire. Recalculer tous les
    trajets après ces déplacements évite de garder les durées d'un ancien ordre.
    Les retards éventuels restent affichés, sans changer l'heure promise.
    """
    if not fenetre:
        return [], None
    par_emp = {e["employe"]: e for e in employes}
    retablis, dernier = {}, None
    while True:
        depassements = [(e, a) for e in employes for a in e["apres"]["arrets"]
                        if abs(_minutes(a["debut"], 0) - a["original_min"]) > fenetre]
        if not depassements:
            return list(retablis.values()), dernier
        for e, a in depassements:
            if a["tache"] in retablis:
                frappe.throw(_("Le recalcul n’a pas respecté la limite horaire. Aucune modification n’a été appliquée."))
            retablis[a["tache"]] = {"tache": a["tache"], "client": a["client"],
                                    "employe": par_emp[a["de"]]["nom"], "motif": "limite horaire stricte"}
            e["apres"]["arrets"].remove(a)
            a.update(employe=a["de"], debut_min=a["original_min"], mobile=False, fixe=True,
                     non_place=True, deplace=False, decale=False, ecart_min=0,
                     fenetre=(a["original_min"], a["original_min"]), motif_non_place="limite horaire stricte")
            par_emp[a["de"]]["apres"]["arrets"].append(a)
        for e in employes:
            e["apres"]["arrets"].sort(key=lambda a: (a["debut_min"], a["tache"]))
        dernier = recalculer(employes)


def verifier_limite_application(doc, ligne, date, fenetre):
    """Vérification de tout le plan avant la première écriture (anciens écrans inclus)."""
    if doc.status != "Open" or str(getdate(doc.starts_on)) != str(getdate(date)):
        return
    # Une heure devenue fixe prime toujours sur la proposition reçue.
    if cint(doc.get(HEURE_FIXE)):
        return
    debut = get_datetime(ligne["starts_on"])
    if getdate(debut) != getdate(date):
        frappe.throw(_("{0} : le rendez-vous doit rester le jour sélectionné.").format(doc.name))
    if fenetre and abs((debut - get_datetime(doc.starts_on)).total_seconds()) > fenetre * 60:
        frappe.throw(_("{0} : déplacement supérieur à ± {1} minutes. Relancez le calcul avec la limite demandée.").format(doc.name, fenetre))


def verifier_redistribution(doc, ligne, techniciens):
    """Une tâche ne peut changer d'employé qu'entre deux techniciens actifs."""
    if doc.status != "Open" or cint(doc.get(EMPLOYE_FIXE)):
        return
    origine = doc.custom_choix_du_staff
    cible = ligne.get("employe") or origine
    if cible != origine and not {origine, cible} <= techniciens:
        frappe.throw(_("{0} : redistribution autorisée uniquement entre employés ayant le rôle Technicien. Relancez le calcul.").format(doc.name))


@frappe.whitelist(methods=["POST"])
def appliquer(date, plan, prevenir=0, fenetre=None):
    """Écrit la proposition : employé, heures de chaque tâche listée. plan : [{tache, employe, starts_on, ends_on, prevenir}].
    Un commentaire sur chaque tâche modifiée ; la tâche garde son statut, ses photos, sa commande.
    `prevenir` : les clients des tâches marquées `prevenir` dans le plan reçoivent le SMS / e-mail du réglage
    (plage horaire centrée sur la nouvelle heure), en tâche de fond, verdict en commentaire sur la tâche."""
    _superviseur()
    plan = frappe.parse_json(plan) if isinstance(plan, str) else (plan or [])
    cfg_application = config()
    limite_horaire = cint(cfg_application.get("fenetre", 60)) if fenetre is None else cint(fenetre)
    if limite_horaire < 0:
        frappe.throw(_("Le déplacement maximal ne peut pas être négatif."))
    # Verrouiller en ordre stable et contrôler toutes les lignes avant d'écrire :
    # une erreur sur la dernière tâche ne doit pas laisser un plan partiel.
    for nom in sorted({p["tache"] for p in plan}):
        frappe.db.sql("select name from `tabTache de travail` where name=%s for update", (nom,))
    documents = {p["tache"]: frappe.get_doc(TACHE, p["tache"]) for p in plan}
    transferts = [p for p in plan if (p.get("employe") or documents[p["tache"]].custom_choix_du_staff) != documents[p["tache"]].custom_choix_du_staff
                  and not cint(documents[p["tache"]].get(EMPLOYE_FIXE))]
    techniciens = employes_techniciens({e for p in transferts for e in (p.get("employe"), documents[p["tache"]].custom_choix_du_staff)})
    for ligne in plan:
        doc = documents[ligne["tache"]]
        verifier_limite_application(doc, ligne, date, limite_horaire)
        verifier_redistribution(doc, ligne, techniciens)
    if controle_localisation_actif():
        cfg_position = config()
        centres_position = _centres_secteurs()
        ancien_sans_reseau = frappe.flags.get("tournee_sans_reseau")
        frappe.flags.tournee_sans_reseau = True
        try:
            for ligne in plan:
                t = frappe.get_doc(TACHE, ligne["tache"])
                if intervention_terrain(t, cfg_position) and not coordonnees_tache(t, centres_position):
                    frappe.throw(_("La position de {0} n’est plus disponible. Relancez le calcul pour actualiser la tournée.").format(t.name))
        finally:
            frappe.flags.tournee_sans_reseau = ancien_sans_reseau
    cfg_pv = config()["prevenir"]
    noms = {}
    modifiees, a_prevenir_ = [], {}
    for p in plan:
        doc = frappe.get_doc(TACHE, p["tache"])
        if doc.status != "Open" or str(getdate(doc.starts_on)) != str(getdate(date)):
            continue
        nouveau_emp, nd, nf = p.get("employe") or doc.custom_choix_du_staff, get_datetime(p["starts_on"]), get_datetime(p["ends_on"])
        # Une épingle posée APRÈS le calcul (fenêtre restée ouverte) l'emporte sur le plan.
        if cint(doc.get(EMPLOYE_FIXE)):
            nouveau_emp = doc.custom_choix_du_staff
        if cint(doc.get(HEURE_FIXE)):
            nd, nf = get_datetime(doc.starts_on), get_datetime(doc.ends_on)
        if nouveau_emp == doc.custom_choix_du_staff and get_datetime(doc.starts_on) == nd and get_datetime(doc.ends_on) == nf:
            continue
        for e in (doc.custom_choix_du_staff, nouveau_emp):
            if e and e not in noms:
                noms[e] = frappe.db.get_value("Employee", e, "employee_name") or e
        avant = "%s %s" % (noms.get(doc.custom_choix_du_staff, doc.custom_choix_du_staff), str(doc.starts_on)[11:16])
        if nouveau_emp != doc.custom_choix_du_staff:
            # Le NOM de l'employé vit à trois endroits sur la tâche : le lien (custom_choix_du_staff), le
            # texte « Employé » (custom_employé — calendrier, Ma tournée, historique client, SMS) et la
            # dernière ligne du titre. Les trois suivent la réaffectation (bug prod 04/10/2026 : seul le
            # lien changeait, le nom affiché restait l'ancien).
            ancien_nom = noms.get(doc.custom_choix_du_staff) or doc.get("custom_employé") or ""
            doc.titre = reaffecter_titre(doc.titre, (ancien_nom, doc.get("custom_employé") or ""), noms[nouveau_emp])
            doc.set("custom_employé", noms[nouveau_emp])
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
