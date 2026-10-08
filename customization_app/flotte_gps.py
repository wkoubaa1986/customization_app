"""Flotte GPS — plateforme unidev « ELA Tracking » (https://www.unidevpro.com.tn/track).

Trois usages : (1) chaque soir, les tâches du jour sont rapprochées des arrêts GPS du véhicule de l'employé →
arrivée / départ / durée réels écrits sur la tâche (`custom_gps_*`) ; (2) la page « Suivi terrain » lit la position
live et le journal du jour pour dire où en est chaque employé par rapport à son planning, avec une heure d'arrivée
estimée pour les tâches restantes (de quoi répondre au client) ; (3) des statistiques du réalisé sur une période.

La plateforme n'a pas d'API d'historique : on parle à son serveur deepstream (websocket) par l'aide Node
`flotte_gps_client.bundle.js` (source `flotte_gps_client.src.js`), qui lit une requête JSON sur stdin.
Les heures de la plateforme sont en UTC ; la Tunisie est à UTC+1 toute l'année.
"""

import glob
import json
import os
import shutil
import statistics
import subprocess
from datetime import datetime, timedelta

import frappe
from frappe.utils import add_days, cint, flt, get_datetime, getdate, now_datetime, nowdate

from customization_app import tournee_optimisation as to

CONFIG = "Config Flotte GPS"
TACHE = "Tache de travail"
JOURNEE = "Journee Flotte GPS"
ROLES = ("Employee", "System Manager", "Responsable magasin", "Appels")      # le suivi live : tous les employés
DIRECTION = "Direction Suivi Terrain"                                          # statistiques, journées, réglages, appariement manuel
DECALAGE = timedelta(hours=1)                      # UTC → heure de Tunis
BUNDLE = os.path.join(os.path.dirname(__file__), "flotte_gps_client.bundle.js")
DELAI_NODE = 120                                   # secondes, par appel (login + un RPC par véhicule)
FUSION_MIN = 10                                    # deux arrêts au même endroit à ≤ 10 min = un seul passage
FUSION_M = 80                                      # … « même endroit » = à moins de 80 m l'un de l'autre
RAYON_LIEU_M = 200                                 # « au Magasin », « domicile »
SAUTEE_APRES_MIN = 60                              # créneau dépassé d'une heure sans passage = probablement sautée
TYPES_HORS = set(to.TYPES_HORS_JOURNEE) | {"Congé", "Tournée commerciale"}

CONFIRME, AUCUN, SANS_POS, SANS_VEH, SANS_GPS = ("Passage confirmé", "Aucun passage", "Sans position",
                                                "Pas de véhicule", "Pas de données GPS")
# Les entiers sont NOT NULL en base (0 = pas de passage) ; seuls les Datetime se vident.
CHAMPS_VIDES = {"custom_gps_statut": "", "custom_gps_vehicule": "", "custom_gps_distance": 0, "custom_gps_arrivee": None,
                "custom_gps_depart": None, "custom_gps_duree": 0, "custom_gps_ecart": 0}


# ── Réglage & accès ──────────────────────────────────────────────────────────

def _garde():
    if not set(frappe.get_roles()) & set(ROLES):
        frappe.throw("Accès réservé (rôles %s)" % ", ".join(ROLES), frappe.PermissionError)


def _garde_direction():
    if DIRECTION not in frappe.get_roles():
        frappe.throw("Réservé à la direction (rôle %s)" % DIRECTION, frappe.PermissionError)


def config() -> dict:
    v = lambda champ: frappe.db.get_single_value(CONFIG, champ)  # noqa: E731
    vehicules = frappe.get_all("Config Flotte GPS Vehicule", filters={"parent": CONFIG, "parenttype": CONFIG},
                               fields=["vehicule", "cbox", "employe", "employe_nom", "actif"], order_by="idx")
    vehicules = [frappe._dict(r, cbox=str(r.cbox or "").strip()) for r in vehicules if r.cbox]
    mdp = None
    if v("utilisateur"):
        from frappe.utils.password import get_decrypted_password
        mdp = get_decrypted_password(CONFIG, CONFIG, "mot_de_passe", raise_exception=False)
    return {"url": v("url_ws") or "wss://www.unidevpro.com.tn/api", "utilisateur": v("utilisateur"), "mot_de_passe": mdp,
            "actif": cint(v("actif")), "rayon": cint(v("rayon_metres")) or 150, "arret_min": cint(v("arret_minimum")) or 60,
            "tolerance": cint(v("ecart_tolere")) or 30, "vehicules": vehicules,
            "cboxes": [r.cbox for r in vehicules if cint(r.actif)],
            "employe_cbox": {r.employe: r.cbox for r in vehicules if r.employe and cint(r.actif)},
            "cbox_vehicule": {r.cbox: r.vehicule for r in vehicules},
            "cbox_employe": {r.cbox: (r.employe, r.employe_nom) for r in vehicules}}


def _node() -> str:
    n = shutil.which("node")
    if not n:
        cand = sorted(glob.glob("/home/frappe/.nvm/versions/node/*/bin/node"))
        n = cand[-1] if cand else None
    if not n:
        frappe.throw("Node introuvable : l’aide GPS ne peut pas tourner")
    return n


def appeler_plateforme(commande: str, cfg: dict | None = None, **q):
    """Un appel à l'aide Node (login + commande) ; rend `resultat`. Lève sur erreur de connexion ou de login."""
    cfg = cfg or config()
    if not (cfg["utilisateur"] and cfg["mot_de_passe"]):
        frappe.throw("Renseigner l’utilisateur et le mot de passe de la plateforme GPS (Config Flotte GPS)")
    req = {"url": cfg["url"], "path": "/api", "username": cfg["utilisateur"], "password": cfg["mot_de_passe"],
           "commande": commande, "timeout": 60000}
    req.update(q)
    try:
        p = subprocess.run([_node(), BUNDLE], input=json.dumps(req), capture_output=True, text=True, timeout=DELAI_NODE)
    except subprocess.TimeoutExpired:
        frappe.throw("Plateforme GPS : pas de réponse en %d s" % DELAI_NODE)
    try:
        rep = json.loads(p.stdout or "{}")
    except ValueError:
        rep = {"erreur": (p.stderr or p.stdout or "réponse illisible")[-400:]}
    if rep.get("erreur") or "resultat" not in rep:
        frappe.throw("Plateforme GPS : %s" % (rep.get("erreur") or (p.stderr or "")[-400:] or "réponse vide"))
    return rep["resultat"]


@frappe.whitelist()
def lister_vehicules():
    _garde_direction()
    return appeler_plateforme("vehicules")


# ── Lecture du journal ───────────────────────────────────────────────────────

def _local(s: str | None):
    """« 2026-10-07T16:05:44.000000000Z » / « 2026-10-07 07:25:09+00:00 » (UTC) → datetime naïf heure de Tunis."""
    if not s:
        return None
    return datetime.strptime(str(s)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S") + DECALAGE


def _bornes_utc(jour) -> tuple[str, str]:
    d = datetime.combine(getdate(jour), datetime.min.time()) - DECALAGE
    return d.strftime("%Y-%m-%dT%H:%M:%S.000Z"), (d + timedelta(days=1) - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def lire_journal(jour, cboxes: list, live: bool = False, cfg: dict | None = None, trace: bool = False) -> dict:
    """{cbox: {arrets: [{arr, dep, lat, lng, minutes}], trajets: [{debut, fin, minutes, km, vmax}], km, position, erreur}}.
    Les arrêts sont en heure locale ; un arrêt commencé la veille est gardé (il dit où dormait le véhicule)."""
    cfg = cfg or config()
    if not cboxes:
        return {}
    dt1, dt2 = _bornes_utc(jour)
    res = appeler_plateforme("suivi" if live else "journal", cfg, cbox=list(cboxes), dt1=dt1, dt2=dt2, stoplen=cfg["arret_min"],
                             trace=bool(trace))
    out = {}
    for cbox in cboxes:
        lignes = (res.get("journal") or {}).get(str(cbox))
        v = {"arrets": [], "trajets": [], "km": 0.0, "position": None, "erreur": None}
        if isinstance(lignes, dict):
            v["erreur"] = lignes.get("erreur") or "journal illisible"
        else:
            for r in lignes or []:
                d = _local(r.get("DT"))
                minutes = flt(r.get("AGE")) * 24 * 60
                if cint(r.get("EV")) == 1:
                    v["trajets"].append({"debut": d, "fin": d + timedelta(minutes=minutes), "minutes": round(minutes),
                                         "km": round(flt(r.get("DIST")), 1), "vmax": cint(r.get("SPDMAX"))})
                    v["km"] += flt(r.get("DIST"))
                else:
                    v["arrets"].append({"arr": d, "dep": d + timedelta(minutes=minutes), "lat": flt(r.get("LAT")),
                                        "lng": flt(r.get("LON")), "minutes": round(minutes)})
            v["km"] = round(v["km"], 1)
        # Trace du jour : [[lat, lng, "HH:MM"]] (heure UTC rendue en heure de Tunis)
        v["trace"] = [[t[0], t[1], "%02d:%s" % ((int(t[2][:2]) + 1) % 24, t[2][3:])] for t in ((res.get("traces") or {}).get(str(cbox)) or [])
                      if isinstance(t, list) and len(t) == 3]
        pos = (res.get("positions") or {}).get(str(cbox))
        if pos and not pos.get("erreur") and pos.get("lat"):
            v["position"] = {"lat": flt(pos["lat"]), "lng": flt(pos["lng"]), "vitesse": cint(pos.get("vitesse")),
                             "heure": _local(pos.get("heure")), "moteur": bool(pos.get("moteur")),
                             "mouvement": bool(pos.get("mouvement")), "en_ligne": bool(pos.get("en_ligne"))}
        out[str(cbox)] = v
    return out


# ── Appariement (pur) ────────────────────────────────────────────────────────

def _m(a, b) -> float:
    return to.haversine_km(a, b) * 1000


def grappes(arrets: list, point: tuple, rayon_m: float) -> list:
    """Les arrêts d'un véhicule à ≤ rayon du point, fusionnés quand ils se suivent à ≤ FUSION_MIN et à ≤ FUSION_M
    l'un de l'autre (le technicien a bougé la voiture devant chez le client) → [{arr, dep, minutes, dist}].
    Deux arrêts distincts dans le rayon (le Magasin puis un client voisin, deux clients du même immeuble) restent séparés."""
    out = []
    for i, a in enumerate(arrets):
        d = _m((a["lat"], a["lng"]), point)
        if d > rayon_m:
            continue
        prec = out[-1] if out else None
        if prec and prec["_i"] == i - 1 and (a["arr"] - prec["dep"]).total_seconds() <= FUSION_MIN * 60 \
                and _m((a["lat"], a["lng"]), (prec["_lat"], prec["_lng"])) <= FUSION_M:
            prec.update(dep=a["dep"], minutes=prec["minutes"] + a["minutes"], dist=min(prec["dist"], d), _i=i, _lat=a["lat"], _lng=a["lng"])
        else:
            out.append({"arr": a["arr"], "dep": a["dep"], "minutes": a["minutes"], "dist": d, "_i": i, "_lat": a["lat"], "_lng": a["lng"]})
    return out


def vehicules_du_jour(taches: list, journaux: dict, defaut: dict, rayon_m: float, lieux_communs: list = ()) -> dict:
    """{employé: cbox} — qui conduisait quoi ce jour-là. Ça change d'un jour à l'autre (Akram sur la 261 TU 3554 du 15 au
    25/09/2026 puis sur le Changan, Sadok sur la 5957 TU 232 puis sur le Changan) : le réglage n'est qu'un défaut.
    Indice = une tâche de l'employé avec un arrêt du véhicule à ≤ rayon, à ± 3 h de l'heure annoncée. **Un seul indice
    suffit** (en live, dès le premier client de la matinée) — sauf aux lieux communs (Magasin, domiciles), où tout le
    monde s'arrête : ces tâches-là ne comptent pas. Attribution par nombre d'indices décroissant, un véhicule par employé ;
    à égalité le véhicule habituel ; sans indice, le véhicule habituel s'il est libre."""
    scores = {}
    for t in taches:
        if not (t.get("lat") and t.get("lng")) or t.get("statut") == "Cancelled" or not t.get("employe"):
            continue
        if any(_m((t["lat"], t["lng"]), lieu) <= RAYON_LIEU_M + rayon_m for lieu in lieux_communs):
            continue
        for cb, j in journaux.items():
            if j.get("erreur") or not j["arrets"]:
                continue
            if any(not t.get("debut") or abs((g["arr"] - t["debut"]).total_seconds()) <= 3 * 3600
                   for g in grappes(j["arrets"], (t["lat"], t["lng"]), rayon_m)):
                scores[(t["employe"], cb)] = scores.get((t["employe"], cb), 0) + 1
    out, pris = {}, set()
    for (e, cb), n in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0][1] != defaut.get(kv[0][0]), kv[0])):
        if e in out or cb in pris:
            continue
        out[e] = cb
        pris.add(cb)
    for e, cb in defaut.items():
        if e not in out and cb not in pris and cb in journaux:
            out[e] = cb
            pris.add(cb)
    return out


def apparier_pure(taches: list, journaux: dict, employe_cbox: dict, rayon_m: float) -> dict:
    """{nom de tâche: {statut, cbox, arrivee, depart, duree, distance, ecart, en_cours}}.
    taches : [{name, employe, lat, lng, debut, fin, type, statut, dans_local}] ; journaux : lire_journal().
    Un arrêt ne sert qu'à une tâche : les paires (tâche, grappe) sont prises par score croissant
    (distance en m + 5 × minutes entre l'arrivée et l'heure annoncée), ce qui sépare deux tâches chez le même client."""
    out, paires = {}, []
    for t in taches:
        if t.get("statut") == "Cancelled" or t.get("type") in TYPES_HORS or (t.get("dans_local") or "") == "Oui":
            continue
        cbox = employe_cbox.get(t.get("employe"))
        if not cbox:
            out[t["name"]] = {"statut": SANS_VEH}
            continue
        j = journaux.get(str(cbox))
        if not j or j.get("erreur") or not (j["arrets"] or j["trajets"]):
            out[t["name"]] = {"statut": SANS_GPS, "cbox": cbox}
            continue
        if not (t.get("lat") and t.get("lng")):
            out[t["name"]] = {"statut": SANS_POS, "cbox": cbox}
            continue
        out[t["name"]] = {"statut": AUCUN, "cbox": cbox}
        for g in grappes(j["arrets"], (t["lat"], t["lng"]), rayon_m):
            ecart = (g["arr"] - t["debut"]).total_seconds() / 60 if t.get("debut") else 0
            paires.append((g["dist"] + 5 * abs(ecart), t["name"], cbox, g["_i"], g, round(ecart)))
    pris_t, pris_g = set(), set()
    for score, nom, cbox, i, g, ecart in sorted(paires, key=lambda p: p[0]):
        if nom in pris_t or (cbox, i) in pris_g:
            continue
        pris_t.add(nom)
        pris_g.add((cbox, i))
        out[nom] = {"statut": CONFIRME, "cbox": cbox, "arrivee": g["arr"], "depart": g["dep"], "duree": g["minutes"],
                    "distance": round(g["dist"]), "ecart": ecart, "en_cours": False}
    return out


def _lieux_communs(cfg_t: dict | None = None) -> list:
    """Magasin et domiciles connus (réglage des tournées) : des arrêts qui n'identifient personne."""
    cfg_t = cfg_t or to.config()
    return [cfg_t["depot"], *cfg_t["departs"].values()]


# ── Tâches du jour ───────────────────────────────────────────────────────────

def _taches(jour) -> list:
    rows = frappe.get_all(TACHE, filters={"starts_on": ["between", ["%s 00:00:00" % jour, "%s 23:59:59" % jour]]},
                          fields=["name", "custom_choix_du_staff", "custom_employé", "custom_type_dintervention", "starts_on",
                                  "ends_on", "status", "custom_client", "nom_client", "select_address", "google_map", "secteur",
                                  "details_adresse", "dans_local", "temps", "titre", "custom_gps_statut", "custom_gps_arrivee",
                                  "custom_gps_depart", "custom_gps_duree", "custom_gps_ecart", "commande_client", "tel"],
                          order_by="starts_on asc", limit_page_length=0)
    centres = to._centres_secteurs()
    out = []
    for r in rows:
        pos = to.coordonnees_tache(r, centres) if r.custom_type_dintervention not in TYPES_HORS else None
        if pos and pos[2] == "secteur":
            # Position approchée (centre du secteur, adresse lue depuis le texte) : on ne peut ni confirmer ni
            # infirmer un passage à 150 m près → « sans position », plutôt qu'un faux « aucun passage ».
            pos = (None, None, "approchée")
        out.append({"name": r.name, "employe": r.custom_choix_du_staff, "employe_nom": r["custom_employé"],
                    "type": r.custom_type_dintervention or "", "debut": get_datetime(r.starts_on),
                    "fin": get_datetime(r.ends_on) if r.ends_on else None, "statut": r.status,
                    "client": r.nom_client or r.custom_client or "", "client_id": r.custom_client, "tel": r.tel,
                    "adresse": r.details_adresse or r.select_address or "", "secteur": r.secteur or "",
                    "lat": pos[0] if pos else None, "lng": pos[1] if pos else None, "position_src": pos[2] if pos else "",
                    "dans_local": r.dans_local, "temps": r.temps, "titre": r.titre or "", "commande": r.commande_client,
                    "gps": {"statut": r.custom_gps_statut, "arrivee": r.custom_gps_arrivee, "depart": r.custom_gps_depart,
                            "duree": r.custom_gps_duree, "ecart": r.custom_gps_ecart}})
    return out


def _duree_planifiee(t) -> int:
    if t.get("debut") and t.get("fin") and t["fin"] > t["debut"]:
        return int((t["fin"] - t["debut"]).total_seconds() // 60)
    return to.duree_retenue(t.get("type"), None, t.get("temps"))


# ── Appariement écrit (cron / bouton) ────────────────────────────────────────

def _resume_journee(jour, cbox, j: dict, taches: list, resultat: dict, cfg: dict) -> dict:
    d0 = datetime.combine(getdate(jour), datetime.min.time())
    d1 = d0 + timedelta(days=1)
    trajets = [x for x in j["trajets"] if d0 <= x["debut"] < d1]
    arrets = [x for x in j["arrets"] if x["dep"] > d0 and x["arr"] < d1]
    noms = [t["name"] for t in taches]
    passages = [resultat[n] for n in noms if resultat.get(n, {}).get("statut") == CONFIRME]
    return {"date": getdate(jour), "vehicule": cfg["cbox_vehicule"].get(cbox) or cbox, "cbox": cbox,
            "km": round(sum(x["km"] for x in trajets), 1),
            "minutes_conduite": sum(x["minutes"] for x in trajets),
            "minutes_arrets": sum(int((min(x["dep"], d1) - max(x["arr"], d0)).total_seconds() // 60) for x in arrets),
            "nb_arrets": len([x for x in arrets if d0 <= x["arr"] < d1]),
            "premiere_sortie": trajets[0]["debut"] if trajets else None,
            "dernier_retour": trajets[-1]["fin"] if trajets else None,
            "nb_taches": len(noms), "nb_passages": len(passages), "minutes_chez_clients": sum(p["duree"] for p in passages)}


def apparier(jour=None, ecrire: bool = True) -> dict:
    """Rapproche les tâches du jour des arrêts GPS et l'écrit sur chaque tâche (sans toucher `modified`)."""
    jour = getdate(jour or nowdate())
    cfg = config()
    taches = _taches(jour)
    cboxes = cfg["cboxes"]
    journaux = lire_journal(jour, cboxes, cfg=cfg) if cboxes else {}
    emp_cbox = vehicules_du_jour(taches, journaux, cfg["employe_cbox"], cfg["rayon"], _lieux_communs())
    cbox_emp = {cb: e for e, cb in emp_cbox.items()}
    resultat = apparier_pure(taches, journaux, emp_cbox, cfg["rayon"])
    bilan = {"jour": str(jour), "taches": len(taches), "vehicules": len(cboxes), CONFIRME: 0, AUCUN: 0, SANS_POS: 0,
             SANS_VEH: 0, SANS_GPS: 0, "ecrit": ecrire,
             "conducteurs": {cfg["cbox_vehicule"].get(cb, cb): e for cb, e in cbox_emp.items()}}
    for t in taches:
        r = resultat.get(t["name"])
        if not r:
            continue
        bilan[r["statut"]] = bilan.get(r["statut"], 0) + 1
        if ecrire:
            valeurs = dict(CHAMPS_VIDES, custom_gps_statut=r["statut"],
                           custom_gps_vehicule=cfg["cbox_vehicule"].get(r.get("cbox"), "") if r.get("cbox") else "")
            if r["statut"] == CONFIRME:
                valeurs.update(custom_gps_distance=r["distance"], custom_gps_arrivee=r["arrivee"], custom_gps_depart=r["depart"],
                               custom_gps_duree=r["duree"], custom_gps_ecart=r["ecart"])
            frappe.db.set_value(TACHE, t["name"], valeurs, update_modified=False)
    if ecrire:
        for cbox in cboxes:
            j = journaux.get(cbox)
            if not j or j.get("erreur"):
                continue
            emp = cbox_emp.get(cbox)
            res = _resume_journee(jour, cbox, j, [t for t in taches if emp and t["employe"] == emp], resultat, cfg)
            res["employe"], res["employe_nom"] = emp, (frappe.db.get_value("Employee", emp, "employee_name") if emp else None)
            nom = frappe.db.exists(JOURNEE, {"date": res["date"], "cbox": cbox})
            doc = frappe.get_doc(JOURNEE, nom) if nom else frappe.new_doc(JOURNEE)
            doc.update(res)
            doc.save(ignore_permissions=True)
        msg = "%s : %d tâches, %d passages confirmés, %d sans passage, %d sans position, %d sans véhicule, %d sans données GPS" % (
            jour.strftime("%d/%m/%Y"), bilan["taches"], bilan[CONFIRME], bilan[AUCUN], bilan[SANS_POS], bilan[SANS_VEH], bilan[SANS_GPS])
        frappe.db.set_single_value(CONFIG, {"derniere_synchro": now_datetime(), "dernier_message": msg})
        frappe.db.commit()
        bilan["message"] = msg
    return bilan


@frappe.whitelist(methods=["POST"])
def apparier_jour(jour):
    _garde_direction()
    return apparier(jour)


@frappe.whitelist(methods=["POST"])
def apparier_periode(du, au):
    """Reprise de l'historique, jour par jour (un appel plateforme par jour)."""
    _garde_direction()
    d, fin, out = getdate(du), getdate(au), []
    while d <= fin:
        try:
            out.append(apparier(d))
        except Exception as e:
            out.append({"jour": str(d), "erreur": str(e)[:200]})
        d = add_days(d, 1)
    return out


def cron_du_soir():
    """21 h : la journée est finie, le journal est complet. La veille est reprise aussi (boîtier qui remonte tard)."""
    cfg = config()
    if not (cfg["actif"] and cfg["utilisateur"] and cfg["mot_de_passe"] and cfg["cboxes"]):
        return                                      # pas encore configuré : rien à faire, rien à journaliser
    for jour in (add_days(nowdate(), -1), nowdate()):
        try:
            apparier(jour)
        except Exception:
            frappe.log_error(frappe.get_traceback()[-1500:], "Flotte GPS : appariement %s" % jour)


# ── Suivi terrain (temps réel) ───────────────────────────────────────────────

def _trace_routiere(points: list, osrm: str) -> list:
    """Le tracé routier (OSRM, simplifié) qui enchaîne les points dans l'ordre → [[lat, lng], …] ; repli : traits droits.
    Mis en cache 15 min (la carte se rafraîchit toutes les 60 s)."""
    pts = [(round(p[0], 5), round(p[1], 5)) for p in points if p and p[0] and p[1]]
    if len(pts) < 2:
        return [list(p) for p in pts]
    cle = "flotte_gps:route:" + frappe.generate_hash(json.dumps(pts), 12)
    cache = frappe.cache().get_value(cle)
    if cache:
        return cache
    out = [list(p) for p in pts]
    if not frappe.flags.get("tournee_sans_reseau"):
        try:
            import requests
            coords = ";".join("%.5f,%.5f" % (p[1], p[0]) for p in pts)
            r = requests.get("%s/route/v1/driving/%s" % (osrm, coords), params={"overview": "simplified", "geometries": "geojson"}, timeout=15)
            d = r.json()
            if d.get("code") == "Ok" and d["routes"]:
                out = [[round(c[1], 5), round(c[0], 5)] for c in d["routes"][0]["geometry"]["coordinates"]]
        except Exception:
            pass                                    # traits droits, sans bruit dans le journal d'erreurs
    frappe.cache().set_value(cle, out, expires_in_sec=900)
    return out


def _lieu(point, taches: list, depot, departs: dict, employe, rayon_m) -> str:
    for t in taches:
        if t.get("lat") and _m(point, (t["lat"], t["lng"])) <= rayon_m:
            return "chez %s" % (t["client"] or t["name"])
    if depot and _m(point, depot) <= RAYON_LIEU_M:
        return "au Magasin"
    if employe in departs and _m(point, departs[employe]) <= RAYON_LIEU_M:
        return "au domicile"
    return ""


def _projection(position, maintenant, restantes: list, osrm: str, sur_place: dict | None) -> None:
    """Heure d'arrivée estimée des tâches restantes, dans l'ordre planifié : on enchaîne depuis la position actuelle
    (temps de route OSRM + durée planifiée de chaque tâche). Si l'employé est déjà sur place, le temps restant y est
    la durée planifiée moins le temps déjà passé. Écrit eta / eta_depart / ecart_prevu sur chaque tâche."""
    pts = [position] + [(t["lat"], t["lng"]) for t in restantes]
    mn, _km, _src = to.matrice(pts, osrm)
    heure, i_prec = maintenant, 0
    for k, t in enumerate(restantes, start=1):
        if sur_place and sur_place["name"] == t["name"]:
            arrivee = sur_place["arr"]
            restant = max(5, _duree_planifiee(t) - int((maintenant - arrivee).total_seconds() // 60))
            heure = maintenant + timedelta(minutes=restant)
            t.update(eta=arrivee, eta_depart=heure, route_min=0)
        else:
            route = mn[i_prec][k] + 5                       # 5 min de marge (stationnement, montée)
            arrivee = heure + timedelta(minutes=route)
            heure = arrivee + timedelta(minutes=_duree_planifiee(t))
            t.update(eta=arrivee, eta_depart=heure, route_min=route)
        t["ecart_prevu"] = round((t["eta"] - t["debut"]).total_seconds() / 60) if t.get("debut") else None
        i_prec = k


@frappe.whitelist()
def suivi(jour=None):
    """Page Suivi terrain : où en est chaque employé par rapport à son planning (voir _suivi)."""
    _garde()
    return _suivi(jour)


CACHE_SUIVI_S = 45          # le live est partagé entre la page (60 s), le moteur de messages (2 min) et la page client


def _suivi(jour=None) -> dict:
    """Où en est chaque employé par rapport à son planning : position, arrêt en cours, tâches passées (heures réelles),
    en cours, restantes (arrivée estimée). Aujourd'hui = live (mis en cache CACHE_SUIVI_S) ; un autre jour = relecture."""
    jour = getdate(jour or nowdate())
    cle = "flotte_gps:suivi:%s" % jour
    if jour == getdate(nowdate()):
        cache = frappe.cache().get_value(cle)
        if cache:
            return cache
    out = _suivi_calcul(jour)
    if out["aujourdhui"]:
        frappe.cache().set_value(cle, out, expires_in_sec=CACHE_SUIVI_S)
    return out


def _suivi_calcul(jour) -> dict:
    jour = getdate(jour)
    aujourdhui = jour == getdate(nowdate())
    maintenant = now_datetime().replace(microsecond=0)
    cfg = config()
    cfg_t = to.config()
    taches = _taches(jour)
    employes = {}
    for t in taches:
        if t["employe"]:
            employes.setdefault(t["employe"], {"employe": t["employe"], "nom": t["employe_nom"] or t["employe"], "taches": []})["taches"].append(t)
    for v in cfg["vehicules"]:
        if v.employe and cint(v.actif) and v.employe not in employes:
            employes[v.employe] = {"employe": v.employe, "nom": v.employe_nom or v.employe, "taches": []}
    cboxes = cfg["cboxes"]
    erreur = None
    try:
        journaux = lire_journal(jour, cboxes, live=aujourdhui, cfg=cfg, trace=True) if cboxes else {}
    except Exception as e:
        journaux, erreur = {}, str(e)[:300]
    emp_cbox = vehicules_du_jour(taches, journaux, cfg["employe_cbox"], cfg["rayon"], _lieux_communs(cfg_t))
    resultat = apparier_pure(taches, journaux, emp_cbox, cfg["rayon"])
    messages = {}
    if frappe.db.exists("DocType", "Message Client GPS"):
        for m in frappe.get_all("Message Client GPS", filters={"tache": ["in", [t["name"] for t in taches] or [""]]},
                                fields=["tache", "type", "statut", "heure", "eta", "texte", "telephone"], order_by="heure"):
            messages.setdefault(m.tache, []).append(m)
    sortie = []
    for emp, e in sorted(employes.items(), key=lambda kv: kv[1]["nom"]):
        cbox = emp_cbox.get(emp)
        j = journaux.get(cbox) if cbox else None
        e.update(vehicule=cfg["cbox_vehicule"].get(cbox, "") if cbox else "", cbox=cbox or "",
                 vehicule_detecte=bool(cbox and cbox != cfg["employe_cbox"].get(emp)), position=None, etat="", lieu="",
                 arret_depuis=None, km=j["km"] if j else None, erreur=(j or {}).get("erreur"),
                 premiere_sortie=j["trajets"][0]["debut"] if j and j["trajets"] else None,
                 dernier_mouvement=(j["trajets"][-1]["fin"] if j and j["trajets"] else None))
        sur_place = None
        if j and not j.get("erreur"):
            dernier = j["arrets"][-1] if j["arrets"] and (not j["trajets"] or j["arrets"][-1]["arr"] > j["trajets"][-1]["debut"]) else None
            pos = j.get("position")
            if aujourdhui and pos:
                e["position"] = dict(pos, age_min=int((maintenant - pos["heure"]).total_seconds() // 60) if pos.get("heure") else None,
                                     lien="https://www.google.com/maps?q=%.6f,%.6f" % (pos["lat"], pos["lng"]))
                if pos["vitesse"] > 3 or (pos["mouvement"] and not dernier):
                    e["etat"] = "en mouvement"
                elif dernier:
                    e["etat"] = "à l’arrêt"
                    e["arret_depuis"] = dernier["arr"]
                    e["lieu"] = _lieu((dernier["lat"], dernier["lng"]), e["taches"], cfg_t["depot"], cfg_t["departs"], emp, cfg["rayon"])
                    for t in e["taches"]:
                        if t.get("lat") and _m((dernier["lat"], dernier["lng"]), (t["lat"], t["lng"])) <= cfg["rayon"] \
                                and resultat.get(t["name"], {}).get("statut") != CONFIRME and t["statut"] != "Cancelled":
                            sur_place = {"name": t["name"], "arr": dernier["arr"]}
                            break
                        if resultat.get(t["name"], {}).get("arrivee") == dernier["arr"]:
                            sur_place = {"name": t["name"], "arr": dernier["arr"]}
                            resultat[t["name"]]["en_cours"] = True
                            break
                else:
                    e["etat"] = "à l’arrêt"
            elif not aujourdhui and j["trajets"]:
                e["etat"] = "journée terminée"
        for t in e["taches"]:
            r = resultat.get(t["name"], {})
            t["gps_live"] = r
            if t["statut"] == "Cancelled":
                t["etape"] = "annulée"
            elif sur_place and sur_place["name"] == t["name"]:
                t["etape"] = "sur place"
                t["arrivee_reelle"] = sur_place["arr"]
            elif r.get("statut") == CONFIRME:
                t["etape"] = "passée"
            elif r.get("statut") in (SANS_VEH, SANS_GPS, SANS_POS) or not r:
                t["etape"] = "clôturée" if t["statut"] == "Completed" else ("non suivie" if r.get("statut") == SANS_VEH or not r else "sans position")
            elif t.get("fin") and t["fin"] < maintenant and (t["statut"] == "Completed" or not aujourdhui):
                t["etape"] = "clôturée sans passage" if t["statut"] == "Completed" else "sans passage"
            elif aujourdhui and t.get("fin") and t["fin"] < maintenant:
                t["etape"] = "en retard"          # heure dépassée, toujours ouverte : projetée à sa place dans l'ordre
            else:
                t["etape"] = "à venir"
        # Sautée : heure dépassée sans passage, ET une intervention planifiée APRÈS elle déjà faite (ou en cours), ou le
        # créneau dépassé depuis plus d'une heure. Elle sort du calcul (sinon elle fausse l'arrivée estimée des suivants)
        # et ne reçoit aucun SMS automatique : c'est à Salma de rappeler le client pour la reprogrammer.
        for t in e["taches"]:
            if t["etape"] == "en retard" and (
                    any(x["etape"] in ("passée", "sur place") and x["debut"] > t["debut"] for x in e["taches"])
                    or (t.get("fin") and t["fin"] < maintenant - timedelta(minutes=SAUTEE_APRES_MIN))):
                t["etape"] = "sautée"
        if aujourdhui and e.get("position"):
            restantes = [t for t in e["taches"] if t["etape"] in ("à venir", "sur place", "en retard") and t.get("lat")]
            if restantes:
                try:
                    _projection((e["position"]["lat"], e["position"]["lng"]), maintenant, restantes, cfg_t["osrm"], sur_place)
                    if restantes[0]["etape"] in ("à venir", "en retard") and e["etat"] == "en mouvement":
                        restantes[0]["etape"] = "en route"
                except Exception:
                    frappe.log_error(frappe.get_traceback()[-800:], "Flotte GPS : projection")
        # Pour la carte : la trace réelle du véhicule et l'itinéraire prévu (Magasin ou domicile → clients dans l'ordre).
        e["trace"] = j["trace"] if j and not j.get("erreur") else []
        depart = cfg_t["departs"].get(emp) or cfg_t["depot"]
        etapes = [t for t in e["taches"] if t.get("lat") and t["etape"] != "annulée"]
        e["itineraire"] = _trace_routiere([depart] + [(t["lat"], t["lng"]) for t in etapes], cfg_t["osrm"]) if etapes else []
        e["depart"] = list(depart)
        e["faites"] = len([t for t in e["taches"] if t["etape"] in ("passée", "clôturée")])
        e["total"] = len([t for t in e["taches"] if t["etape"] != "annulée"])
        for t in e["taches"]:
            t.pop("gps", None)
            t["messages"] = messages.get(t["name"], [])
        sortie.append(e)
    return {"jour": str(jour), "aujourdhui": aujourdhui, "maintenant": maintenant, "employes": sortie, "erreur": erreur,
            "depot": list(cfg_t["depot"]),
            "rayon": cfg["rayon"], "tolerance": cfg["tolerance"], "configure": bool(cfg["utilisateur"] and cfg["vehicules"])}


# ── Statistiques du réalisé ──────────────────────────────────────────────────

def _bucket(ecart, tol) -> str:
    if ecart is None:
        return "?"
    if ecart < -tol:
        return "avance"
    if ecart > tol:
        return "retard"
    return "à l’heure"


@frappe.whitelist()
def statistiques(du=None, au=None, employe=None):
    """Sur les tâches déjà appariées (custom_gps_statut) : par employé et par type, durées réelles vs standard,
    ponctualité, passages manquants ; par jour, km et amplitude depuis Journee Flotte GPS."""
    _garde_direction()
    au = getdate(au or nowdate())
    du = getdate(du or add_days(au, -29))
    tol = cint(frappe.db.get_single_value(CONFIG, "ecart_tolere")) or 30
    filtres = {"starts_on": ["between", ["%s 00:00:00" % du, "%s 23:59:59" % au]], "custom_gps_statut": ["is", "set"]}
    if employe:
        filtres["custom_choix_du_staff"] = employe
    rows = frappe.get_all(TACHE, filters=filtres, fields=["name", "custom_choix_du_staff", "custom_employé", "custom_type_dintervention",
                                                          "starts_on", "ends_on", "status", "nom_client", "custom_gps_statut",
                                                          "custom_gps_arrivee", "custom_gps_depart", "custom_gps_duree", "custom_gps_ecart",
                                                          "custom_gps_vehicule"], order_by="starts_on", limit_page_length=0)
    par_emp, par_type, manquants, buckets = {}, {}, [], {"avance": 0, "à l’heure": 0, "retard": 0}
    for r in rows:
        e = par_emp.setdefault(r.custom_choix_du_staff, {"employe": r.custom_choix_du_staff, "nom": r["custom_employé"] or r.custom_choix_du_staff,
                                                        "taches": 0, "confirmes": 0, "aucun": 0, "autres": 0, "durees": [], "ecarts": [],
                                                        "avance": 0, "à l’heure": 0, "retard": 0, "types": {}})
        e["taches"] += 1
        typ = r.custom_type_dintervention or "?"
        ty = par_type.setdefault(typ, {"type": typ, "standard": to.DUREE_TYPE.get(typ), "taches": 0, "confirmes": 0, "durees": [], "ecarts": []})
        ty["taches"] += 1
        et = e["types"].setdefault(typ, {"type": typ, "n": 0, "durees": []})
        if r.custom_gps_statut == CONFIRME:
            e["confirmes"] += 1
            ty["confirmes"] += 1
            et["n"] += 1
            if r.custom_gps_duree is not None:
                e["durees"].append(cint(r.custom_gps_duree))
                ty["durees"].append(cint(r.custom_gps_duree))
                et["durees"].append(cint(r.custom_gps_duree))
            if r.custom_gps_ecart is not None:
                e["ecarts"].append(cint(r.custom_gps_ecart))
                ty["ecarts"].append(cint(r.custom_gps_ecart))
                b = _bucket(cint(r.custom_gps_ecart), tol)
                e[b] += 1
                buckets[b] += 1
        elif r.custom_gps_statut == AUCUN:
            e["aucun"] += 1
            if r.status == "Completed":
                manquants.append({"tache": r.name, "date": str(r.starts_on)[:16], "employe": r["custom_employé"], "client": r.nom_client,
                                  "type": typ, "vehicule": r.custom_gps_vehicule})
        else:
            e["autres"] += 1

    def _stats(l):
        return {"n": len(l), "moyenne": round(statistics.mean(l)) if l else None, "mediane": round(statistics.median(l)) if l else None,
                "max": max(l) if l else None}

    for e in par_emp.values():
        e["duree"] = _stats(e["durees"])
        e["ecart"] = _stats(e["ecarts"])
        e["ecart_abs_moyen"] = round(statistics.mean(abs(x) for x in e["ecarts"])) if e["ecarts"] else None
        e["types"] = [dict(x, duree=_stats(x["durees"])) for x in e["types"].values()]
        for x in e["types"]:
            x.pop("durees")
        e.pop("durees"), e.pop("ecarts")
    for ty in par_type.values():
        ty["duree"] = _stats(ty["durees"])
        ty["ecart"] = _stats(ty["ecarts"])
        ty.pop("durees"), ty.pop("ecarts")
    jf = {"date": ["between", [du, au]]}
    if employe:
        jf["employe"] = employe
    journees = frappe.get_all(JOURNEE, filters=jf, fields=["date", "vehicule", "employe", "employe_nom", "km", "minutes_conduite",
                                                           "minutes_arrets", "nb_arrets", "premiere_sortie", "dernier_retour",
                                                           "nb_taches", "nb_passages", "minutes_chez_clients"],
                              order_by="date desc, vehicule", limit_page_length=0)
    par_vehicule = {}
    for jr in journees:
        v = par_vehicule.setdefault(jr.vehicule, {"vehicule": jr.vehicule, "employe_nom": jr.employe_nom, "jours": 0, "km": 0.0,
                                                   "minutes_conduite": 0, "minutes_chez_clients": 0, "nb_taches": 0, "nb_passages": 0,
                                                   "sorties": [], "retours": []})
        v["jours"] += 1
        v["km"] += flt(jr.km)
        v["minutes_conduite"] += cint(jr.minutes_conduite)
        v["minutes_chez_clients"] += cint(jr.minutes_chez_clients)
        v["nb_taches"] += cint(jr.nb_taches)
        v["nb_passages"] += cint(jr.nb_passages)
        if jr.premiere_sortie:
            v["sorties"].append(get_datetime(jr.premiere_sortie).hour * 60 + get_datetime(jr.premiere_sortie).minute)
        if jr.dernier_retour:
            v["retours"].append(get_datetime(jr.dernier_retour).hour * 60 + get_datetime(jr.dernier_retour).minute)
    for v in par_vehicule.values():
        v["km"] = round(v["km"], 1)
        v["km_jour"] = round(v["km"] / v["jours"], 1) if v["jours"] else 0
        v["sortie_moyenne"] = round(statistics.mean(v["sorties"])) if v["sorties"] else None
        v["retour_moyen"] = round(statistics.mean(v["retours"])) if v["retours"] else None
        v.pop("sorties"), v.pop("retours")
    # Détail tâche par tâche (pour un technicien, ou tout le monde sur une courte période) et vue par jour.
    detail = [{"tache": r.name, "date": str(r.starts_on)[:10], "annonce": str(r.starts_on)[11:16], "fin_annoncee": str(r.ends_on)[11:16] if r.ends_on else "",
               "employe": r["custom_employé"] or r.custom_choix_du_staff, "client": r.nom_client, "type": r.custom_type_dintervention,
               "statut": r.status, "passage": r.custom_gps_statut, "vehicule": r.custom_gps_vehicule,
               "arrivee": str(r.custom_gps_arrivee)[11:16] if r.custom_gps_arrivee else "", "depart": str(r.custom_gps_depart)[11:16] if r.custom_gps_depart else "",
               "duree": cint(r.custom_gps_duree) if r.custom_gps_statut == CONFIRME else None,
               "ecart": cint(r.custom_gps_ecart) if r.custom_gps_statut == CONFIRME else None,
               "planifie": int((get_datetime(r.ends_on) - get_datetime(r.starts_on)).total_seconds() // 60) if r.ends_on else None}
              for r in rows[-600:]]
    par_jour = {}
    for r in rows:
        if employe or len(par_emp) == 1:
            pj = par_jour.setdefault(str(r.starts_on)[:10], {"date": str(r.starts_on)[:10], "taches": 0, "confirmes": 0, "minutes": 0, "ecarts": []})
            pj["taches"] += 1
            if r.custom_gps_statut == CONFIRME:
                pj["confirmes"] += 1
                pj["minutes"] += cint(r.custom_gps_duree)
                pj["ecarts"].append(cint(r.custom_gps_ecart))
    for jr in journees:
        if (employe and jr.employe == employe) or (not employe and len(par_emp) == 1):
            pj = par_jour.setdefault(str(jr.date), {"date": str(jr.date), "taches": 0, "confirmes": 0, "minutes": 0, "ecarts": []})
            pj.update(vehicule=jr.vehicule, km=flt(jr.km), conduite=cint(jr.minutes_conduite), depart=str(jr.premiere_sortie)[11:16] if jr.premiere_sortie else "",
                      retour=str(jr.dernier_retour)[11:16] if jr.dernier_retour else "")
    for pj in par_jour.values():
        pj["ecart_moyen"] = round(statistics.mean(pj["ecarts"])) if pj["ecarts"] else None
        pj.pop("ecarts")
    return {"du": str(du), "au": str(au), "tolerance": tol, "taches": len(rows), "detail": detail,
            "par_jour": sorted(par_jour.values(), key=lambda x: x["date"], reverse=True),
            "confirmes": sum(e["confirmes"] for e in par_emp.values()), "buckets": buckets,
            "employes": sorted(par_emp.values(), key=lambda e: e["nom"]), "types": sorted(par_type.values(), key=lambda t: -t["taches"]),
            "manquants": manquants[-60:], "vehicules": sorted(par_vehicule.values(), key=lambda v: v["vehicule"]),
            "journees": journees[:120], "standard": to.DUREE_TYPE}
