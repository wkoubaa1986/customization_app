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
DUREES_MIN = {"15 min": 15, "30 min": 30, "45 min": 45, "1 heure": 60, "1 heure, 15 min": 75, "1 heure, 30 min": 90,
              "1 heure, 45 min": 105, ">=2 heures": 120}

RE_COORDS = (r"[?&]q=(-?\d+\.\d+),(-?\d+\.\d+)", r"[?&](?:ll|destination|daddr|center)=(-?\d+\.\d+),(-?\d+\.\d+)",
             r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", r"@(-?\d+\.\d+),(-?\d+\.\d+)", r"/(-?\d+\.\d{4,}),(-?\d+\.\d{4,})")


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
    enregistre = frappe.db.exists("DocType", CONFIG) and frappe.db.exists("Singles", {"doctype": CONFIG, "field": "heure_fin"})
    v = (lambda champ: frappe.db.get_single_value(CONFIG, champ)) if enregistre else (lambda champ: None)
    types = [t.strip() for t in (v("types_mobiles") or "").splitlines() if t.strip()] or list(TYPES_MOBILES_DEFAUT)
    lat, lng = flt(v("depot_latitude")), flt(v("depot_longitude"))
    exclus = set(frappe.get_all("Config Optimisation Tournees Exclu", filters={"parent": CONFIG, "parenttype": CONFIG}, pluck="employe")) \
        if frappe.db.exists("DocType", "Config Optimisation Tournees Exclu") else set()
    return {"depot": (lat, lng) if lat and lng else DEPOT_DEFAUT,
            "debut": _minutes(v("heure_debut"), 8 * 60), "fin": _minutes(v("heure_fin"), 17 * 60),
            "premiere": _minutes(v("heure_premiere"), 9 * 60),
            "types": types, "osrm": (v("osrm_url") or OSRM_DEFAUT).rstrip("/"),
            "equilibre": cint(v("equilibre")) if v("equilibre") is not None else 1, "exclus": exclus}


# ── Coordonnées ─────────────────────────────────────────────────────────────

def coordonnees_du_lien(url: str | None):
    """(lat, lng) lu dans un lien Google Maps déjà développé, sinon None. PURE."""
    for motif in RE_COORDS:
        m = re.search(motif, url or "")
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


def _geocoder_adresse(nom: str):
    """Coordonnées mémorisées sur l'adresse, sinon résolues depuis son lien et mémorisées."""
    if not nom:
        return None
    a = frappe.db.get_value("Address", nom, ["custom_latitude", "custom_longitude", "custom_lien_google_map"], as_dict=True)
    if not a:
        return None
    if a.custom_latitude and a.custom_longitude:
        return (flt(a.custom_latitude), flt(a.custom_longitude))
    c = resoudre_lien(a.custom_lien_google_map)
    if c:
        _memoriser(nom, c, "lien adresse")
    return c


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
                              and t.starts_on >= date_sub(curdate(), interval 365 day)""", as_dict=True)
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
        return c[0], c[1], "adresse"
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


# ── Solveur ─────────────────────────────────────────────────────────────────

def resoudre(minutes: list, arrets: list, nb_vehicules: int, debut: int, fin: int, equilibre: int = 1,
             limite_s: int = 5, premiere: int | None = None) -> dict:
    """Tournées à fenêtres de temps (OR-Tools). Nœud 0 = dépôt. `arrets[i]` décrit le nœud i+1 :
    {service: min, fenetre: (a, b) | None, vehicule: idx | None}. Les temps sont en minutes depuis minuit.
    → {"routes": [[(noeud, arrivee), …] par véhicule], "non_places": [noeuds], "cout": minutes de route}."""
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    n = len(arrets) + 1
    manager = pywrapcp.RoutingIndexManager(n, nb_vehicules, 0)
    routing = pywrapcp.RoutingModel(manager)
    service = [0] + [cint(a.get("service")) for a in arrets]

    def transit(i, j):
        a, b = manager.IndexToNode(i), manager.IndexToNode(j)
        return minutes[a][b] + service[a]

    cb = routing.RegisterTransitCallback(transit)
    routing.SetArcCostEvaluatorOfAllVehicles(cb)
    horizon = max(fin, max((a["fenetre"][1] + cint(a.get("service")) for a in arrets if a.get("fenetre")), default=fin)) + 1
    routing.AddDimension(cb, horizon, horizon, False, "Temps")
    temps = routing.GetDimensionOrDie("Temps")
    # Équilibrage : au-delà d'une part équitable de la journée (travail total / véhicules, + 1 h), chaque
    # minute de plus coûte. (Une somme des amplitudes pénalise au contraire le second véhicule, et le
    # solveur chargeait tout sur un seul — constaté en test.)
    charge = sum(service) + sum(min((minutes[i][j] for j in range(n) if j != i), default=0) for i in range(1, n))
    equitable = debut + int(charge / max(nb_vehicules, 1)) + 60
    for v in range(nb_vehicules):
        temps.CumulVar(routing.Start(v)).SetRange(debut, debut)
        temps.CumulVar(routing.End(v)).SetRange(debut, horizon)
        if equilibre:
            temps.SetCumulVarSoftUpperBound(routing.End(v), equitable, 2 * cint(equilibre))
        routing.AddVariableMinimizedByFinalizer(temps.CumulVar(routing.End(v)))
    for k, a in enumerate(arrets):
        idx = manager.NodeToIndex(k + 1)
        fen = a.get("fenetre") or (max(debut, premiere or debut), fin)     # pas de visite libre avant « première »
        temps.CumulVar(idx).SetRange(cint(fen[0]), cint(fen[1]))
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
        return {"routes": [[] for _ in range(nb_vehicules)], "non_places": list(range(1, n)), "cout": 0}
    routes, places = [], set()
    for v in range(nb_vehicules):
        r, idx = [], routing.Start(v)
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            if node != 0:
                r.append((node, sol.Min(temps.CumulVar(idx))))
                places.add(node)
            idx = sol.Value(routing.NextVar(idx))
        routes.append(r)
    return {"routes": routes, "non_places": [k for k in range(1, n) if k not in places], "cout": sol.ObjectiveValue()}


# ── Proposition ─────────────────────────────────────────────────────────────

def _duree(t) -> int:
    if t.get("temps") in DUREES_MIN:
        return DUREES_MIN[t.temps]
    if t.get("starts_on") and t.get("ends_on"):
        d = int((get_datetime(t.ends_on) - get_datetime(t.starts_on)).total_seconds() // 60)
        if 5 <= d <= 480:
            return d
    return 60


def _hm(minutes: int) -> str:
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def _route_actuelle(arrets_emp: list, mn: list, km: list) -> tuple[int, float]:
    """Minutes et km de la tournée telle qu'elle est (ordre des heures), dépôt → … → dépôt."""
    ordre = [0] + [a["noeud"] for a in sorted(arrets_emp, key=lambda a: a["debut"])] + [0]
    return (sum(mn[ordre[i]][ordre[i + 1]] for i in range(len(ordre) - 1)),
            round(sum(km[ordre[i]][ordre[i + 1]] for i in range(len(ordre) - 1)), 1))


@frappe.whitelist()
def proposer(date):
    """La proposition pour la journée : par employé, tournée actuelle et tournée optimisée (ordre, heures,
    km, minutes de route), tâches déplacées, approximations et tâches impossibles à placer. RIEN N'EST ÉCRIT."""
    _superviseur()
    jour = getdate(date)
    cfg = config()
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
    mn, km, source = matrice(points, cfg["osrm"])
    debut_j, fin_j = cfg["debut"], cfg["fin"]
    for a in arrets:
        if not a["mobile"]:
            debut_j, fin_j = min(debut_j, a["debut"]), max(fin_j, a["debut"] + a["service"] + mn[a["noeud"]][0])
    noeuds = [{"service": a["service"], "fenetre": None if a["mobile"] else (a["debut"], a["debut"]),
               "vehicule": None if a["mobile"] else employes.index(a["employe"])} for a in arrets]
    sol = resoudre(mn, noeuds, len(employes), debut_j, fin_j, cfg["equilibre"], premiere=cfg["premiere"])
    par_noeud = {a["noeud"]: a for a in arrets}
    if sol["non_places"]:
        # Ce qui n'a pas trouvé place reste où c'est (employé, heure) et la tournée se recalcule autour,
        # pour que la proposition soit complète et comparable à l'existant.
        for n in sol["non_places"]:
            a = par_noeud[n]
            a["mobile"] = False
            fin_j = max(fin_j, a["debut"] + a["service"] + mn[n][0])
        noeuds = [{"service": a["service"], "fenetre": None if a["mobile"] else (a["debut"], a["debut"]),
                   "vehicule": None if a["mobile"] else employes.index(a["employe"])} for a in arrets]
        sol = resoudre(mn, noeuds, len(employes), debut_j, fin_j, cfg["equilibre"], premiere=cfg["premiere"])
    out, tot_av, tot_ap, km_av, km_ap = [], 0, 0, 0.0, 0.0
    for v, e in enumerate(employes):
        actuels = [a for a in arrets if a["employe"] == e]
        m_av, k_av = _route_actuelle(actuels, mn, km)
        route = sol["routes"][v]
        ordre = [0] + [n for n, _t in route] + [0]
        m_ap = sum(mn[ordre[i]][ordre[i + 1]] for i in range(len(ordre) - 1))
        k_ap = round(sum(km[ordre[i]][ordre[i + 1]] for i in range(len(ordre) - 1)), 1)
        apres = []
        # Les tâches que même fixées le solveur n'a pu servir (deux rendez-vous à la même heure chez le même
        # employé, hors journée…) restent dans la liste de leur employé, telles quelles, pour que la journée
        # proposée soit complète.
        restes = [(a["noeud"], a["debut"]) for a in arrets if a["noeud"] in sol["non_places"] and a["employe"] == e]
        for n, arrivee in sorted(list(route) + restes, key=lambda x: x[1]):
            a = par_noeud[n]
            deb = a["debut"] if not a["mobile"] else int(math.ceil(arrivee / PAS_MIN) * PAS_MIN)
            apres.append({"tache": a["tache"], "client": a["client"], "type": a["type"], "debut": _hm(deb), "fin": _hm(deb + a["service"]),
                          "starts_on": "%s %s:00" % (base, _hm(deb)), "ends_on": "%s %s:00" % (base, _hm(deb + a["service"])),
                          "employe": e, "de": a["employe"], "de_nom": noms.get(a["employe"], a["employe"]),
                          "deplace": a["employe"] != e, "decale": a["mobile"] and deb != a["debut"], "fixe": not a["mobile"],
                          "non_place": n in sol["non_places"],
                          "position": a["position"], "lat": a["lat"], "lng": a["lng"], "adresse": a["adresse"]})
        out.append({"employe": e, "nom": noms.get(e, e),
                    "avant": {"minutes": m_av, "km": k_av, "arrets": [{"tache": a["tache"], "client": a["client"], "type": a["type"],
                                                                        "debut": _hm(a["debut"]), "fin": _hm(a["debut"] + a["service"]),
                                                                        "fixe": not a["mobile"], "lat": a["lat"], "lng": a["lng"]}
                                                                       for a in sorted(actuels, key=lambda a: a["debut"])]},
                    "apres": {"minutes": m_ap, "km": k_ap, "arrets": apres}})
        tot_av, tot_ap, km_av, km_ap = tot_av + m_av, tot_ap + m_ap, km_av + k_av, km_ap + k_ap
    non_places = [{"tache": par_noeud[n]["tache"], "client": par_noeud[n]["client"], "employe": noms.get(par_noeud[n]["employe"])} for n in sol["non_places"]]
    return {"date": str(jour), "employes": out, "depot": cfg["depot"], "source": source,
            "total": {"avant_min": tot_av, "apres_min": tot_ap, "avant_km": round(km_av, 1), "apres_km": round(km_ap, 1)},
            "deplacees": sum(1 for e in out for a in e["apres"]["arrets"] if a["deplace"]),
            "decalees": sum(1 for e in out for a in e["apres"]["arrets"] if a["decale"] and not a["deplace"]),
            "non_places": non_places, "avertissements": avert, "journee": [_hm(debut_j), _hm(fin_j)], "premiere": _hm(cfg["premiere"])}


@frappe.whitelist(methods=["POST"])
def appliquer(date, plan):
    """Écrit la proposition : employé, heures de chaque tâche listée. plan : [{tache, employe, starts_on, ends_on}].
    Un commentaire sur chaque tâche modifiée ; la tâche garde son statut, ses photos, sa commande."""
    _superviseur()
    plan = frappe.parse_json(plan) if isinstance(plan, str) else (plan or [])
    noms = {}
    modifiees = []
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
    return {"modifiees": modifiees}
