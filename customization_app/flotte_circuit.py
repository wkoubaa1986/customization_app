"""Sorties de circuit — le véhicule d'un technicien s'écarte-t-il de SES TÂCHES du jour ? (page /app/sortie-circuit,
rôle « Sortie Circuit » : Wassim et Néjib seulement ; demande du 15/10/2026.)

Le circuit = les clients planifiés du jour (positions exactes ou approchées), plus le Magasin et le domicile. Trois
signaux, calculés chaque soir (cron, après l'appariement) et rejouables en lot sur une période :
- « Arrêt inexpliqué » : le véhicule reste ≥ ARRET_MIN min, pendant la journée de travail, à plus de DISTANCE_KM de
  tout client du jour (DISTANCE_APPROX_KM si le client le plus proche n'a qu'une position approchée) et hors Magasin /
  domicile ;
- « Hors horaires » : un trajet de KM_TRAJET_MIN km ou plus commencé bien avant l'heure de début du technicien, fini
  bien après son heure de fin, ou un dimanche ;
- « Détour » : les km réels de la journée dépassent nettement les km du circuit (domicile ou Magasin → clients dans
  l'ordre des heures → Magasin, à vol d'oiseau × coefficient routier) — indicatif, un seul écart par jour.

Un écart est un fait à REGARDER, pas une faute : un fournisseur, une tâche faite sans être planifiée (Sadok, 85 km par
jour les 13-14/10/2026 avec 0 tâche), une course Aramex ou une pause sont des sorties de circuit légitimes. La page
permet de les justifier ; une justification survit au recalcul (clé stable jour|employé|type|début).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import frappe
from frappe.utils import add_days, cint, flt, get_datetime, getdate, now_datetime, nowdate

from customization_app import flotte_gps as fg
from customization_app import tournee_optimisation as to

ECART = "Ecart Circuit GPS"
ROLE = "Sortie Circuit"

ARRET_MIN = 15                 # un arrêt plus court n'est pas une sortie (feu, dépôt rapide)
DISTANCE_KM = 3.0              # loin de tout client à position exacte
DISTANCE_APPROX_KM = 6.0       # … ou à position approchée (3 clients sur 4 à moins de 4,3 km du centre du secteur)
MARGE_AVANT_MIN = 60           # journée de travail élargie, pour ne pas compter le trajet du matin / du soir
MARGE_APRES_MIN = 90
HORS_AVANT_MIN = 120           # trajet commencé plus de 2 h avant l'heure de début = hors horaires
HORS_APRES_MIN = 180           # … ou fini plus de 3 h après l'heure de fin
KM_TRAJET_MIN = 2.0            # un déplacement de moins de 2 km ne compte pas
RATIO_DETOUR = 1.5             # km réels > 1,5 × km du circuit…
KM_DETOUR_MIN = 20.0           # … et au moins 20 km de plus : détour

TYPES = ("Arrêt inexpliqué", "Hors horaires", "Détour")
JUSTIFICATIONS = ("", "Fournisseur", "Client non planifié", "Aramex / courses", "Pause", "Autre", "Anomalie confirmée")


def _garde():
    if ROLE not in frappe.get_roles() and frappe.session.user != "Administrator":
        frappe.throw("Réservé au rôle « %s »" % ROLE, frappe.PermissionError)


def _cle(jour, employe, typ, debut) -> str:
    return "%s|%s|%s|%s" % (jour, employe, typ, str(debut)[:16])


# ── Calcul pur ───────────────────────────────────────────────────────────────

def circuit(taches: list) -> list:
    """Les points du circuit : [(lat, lng, approché ?, nom client, tâche)] — exacts ou approchés, hors tâches annulées."""
    out = []
    for t in taches:
        if t.get("statut") == "Cancelled":
            continue
        if t.get("lat") and t.get("lng"):
            out.append((t["lat"], t["lng"], False, t.get("client") or t["name"], t["name"]))
        elif t.get("approx"):
            out.append((t["approx"][0], t["approx"][1], True, t.get("client") or t["name"], t["name"]))
    return out


def km_circuit(points: list, depart, depot, coef: float = 1.3) -> float:
    """Km du circuit à vol d'oiseau × coef : départ → clients (dans l'ordre donné) → Magasin. 0 sans client."""
    if not points:
        return 0.0
    etapes = [depart] + [(p[0], p[1]) for p in points] + [depot]
    return round(coef * sum(to.haversine_km(etapes[i], etapes[i + 1]) for i in range(len(etapes) - 1)), 1)


def ecarts_pure(jour, taches: list, journal: dict, lieux: list, horaires: tuple, depart, depot, coef: float = 1.3) -> list:
    """Les écarts d'UN employé pour UN jour. taches : _taches() de l'employé (ordre des heures) ; journal : lire_journal()
    du véhicule qu'il conduisait ; lieux : Magasin + domiciles ; horaires : (début, fin) en minutes depuis minuit. PURE."""
    if not journal or journal.get("erreur"):
        return []
    jour = getdate(jour)
    d0 = datetime.combine(jour, datetime.min.time())
    d1 = d0 + timedelta(days=1)
    h_deb, h_fin = horaires
    pts = circuit(sorted(taches, key=lambda t: t["debut"]))
    out = []
    # 1. Arrêts inexpliqués
    for a in fg._fusionner(journal.get("arrets") or []):
        if not (d0 <= a["arr"] < d1) or a["minutes"] < ARRET_MIN:
            continue
        m = a["arr"].hour * 60 + a["arr"].minute
        if m < h_deb - MARGE_AVANT_MIN or m > h_fin + MARGE_APRES_MIN:
            continue                                        # avant / après la journée : le trajet domicile, pas une sortie
        pa = (a["lat"], a["lng"])
        if any(l and fg._m(pa, l) <= fg.RAYON_LIEU_M for l in lieux):
            continue
        proche = min(((to.haversine_km(pa, (p[0], p[1])), p) for p in pts), key=lambda x: x[0], default=(None, None))
        km_client, p = proche
        seuil = DISTANCE_APPROX_KM if (p and p[2]) else DISTANCE_KM
        if km_client is not None and km_client <= seuil:
            continue
        out.append({"type": "Arrêt inexpliqué", "debut": a["arr"], "fin": a["dep"], "minutes": a["minutes"], "lat": a["lat"], "lng": a["lng"],
                    "km_client": round(km_client, 1) if km_client is not None else None,
                    "client_proche": p[3] if p else None, "tache_proche": p[4] if p else None, "km": None,
                    "detail": ("%d min à l'arrêt, à %s km du client le plus proche (%s%s)" % (
                        a["minutes"], ("%.1f" % km_client).replace(".", ","), p[3], ", position approchée" if p[2] else "")
                               if p else "%d min à l'arrêt — aucune tâche planifiée ce jour" % a["minutes"])})
    # 2. Hors horaires
    dimanche = jour.weekday() == 6
    for tr in journal.get("trajets") or []:
        if not (d0 <= tr["debut"] < d1) or flt(tr.get("km")) < KM_TRAJET_MIN:
            continue
        m_deb = tr["debut"].hour * 60 + tr["debut"].minute
        m_fin = tr["fin"].hour * 60 + tr["fin"].minute if tr["fin"] < d1 else 24 * 60
        motif = "un dimanche" if dimanche else ("avant %02d:%02d" % divmod(h_deb - HORS_AVANT_MIN, 60) if m_deb < h_deb - HORS_AVANT_MIN
                                               else "après %02d:%02d" % divmod(min(h_fin + HORS_APRES_MIN, 23 * 60 + 59), 60) if m_fin > h_fin + HORS_APRES_MIN else None)
        if not motif:
            continue
        out.append({"type": "Hors horaires", "debut": tr["debut"], "fin": tr["fin"], "minutes": tr["minutes"], "lat": None, "lng": None,
                    "km_client": None, "client_proche": None, "tache_proche": None, "km": flt(tr["km"]),
                    "detail": "trajet de %s km %s (%s → %s)" % (("%.1f" % flt(tr["km"])).replace(".", ","), motif, str(tr["debut"])[11:16], str(tr["fin"])[11:16])})
    # 3. Détour (un seul par jour, indicatif)
    trajets_jour = [tr for tr in journal.get("trajets") or [] if d0 <= tr["debut"] < d1]
    km_reel = round(sum(flt(tr.get("km")) for tr in trajets_jour), 1)
    km_prevu = km_circuit(pts, depart, depot, coef)
    if pts and km_reel >= max(RATIO_DETOUR * km_prevu, km_prevu + KM_DETOUR_MIN):
        out.append({"type": "Détour", "debut": trajets_jour[0]["debut"] if trajets_jour else d0, "fin": trajets_jour[-1]["fin"] if trajets_jour else d0,
                    "minutes": sum(tr["minutes"] for tr in trajets_jour), "lat": None, "lng": None, "km_client": None, "client_proche": None,
                    "tache_proche": None, "km": round(km_reel - km_prevu, 1),
                    "detail": "%s km réels pour un circuit de %s km (%d client%s) : %s km en plus" % (
                        ("%.0f" % km_reel), ("%.0f" % km_prevu), len(pts), "s" if len(pts) > 1 else "", ("%.0f" % (km_reel - km_prevu)))})
    return out


# ── Calcul écrit (cron / lot) ────────────────────────────────────────────────

def calculer(jour=None, ecrire: bool = True, journaux: dict | None = None) -> dict:
    """Les écarts de tous les techniciens d'un jour, écrits dans Ecart Circuit GPS (les justifications sont gardées).
    `journaux` : le journal déjà lu (lire_journal) pour ne pas solliciter la plateforme deux fois (rattrapage)."""
    jour = getdate(jour or nowdate())
    cfg = fg.config()
    cfg_t = to.config()
    taches = fg._taches(jour)
    cboxes = cfg["cboxes"]
    if journaux is None:
        journaux = fg.lire_journal(jour, cboxes, cfg=cfg) if cboxes else {}
    lieux = fg._lieux_communs(cfg_t)
    emp_cbox = fg.vehicules_du_jour(taches, journaux, fg.defaut_du_jour(cfg, jour), cfg["rayon"], lieux,
                                    nuits={cb: fg.point_de_nuit(jour, j) for cb, j in journaux.items()}, domiciles=cfg_t["departs"], depot=cfg_t["depot"],
                                    groupes=fg.equipages())
    # Un véhicule sans employé reconnu ce jour-là : son conducteur habituel (le réglage), pour ne pas le perdre.
    for emp, cb in fg.defaut_du_jour(cfg, jour).items():
        if cb not in emp_cbox.values() and emp not in emp_cbox:
            emp_cbox[emp] = cb
    noms = {}
    trouves = []
    # Un véhicule = un circuit : les tâches de tout l'équipage qui roule dedans, les écarts au conducteur principal.
    equipage_de = {}
    for e, cb in emp_cbox.items():
        equipage_de.setdefault(cb, []).append(e)
    for cbox, emps in equipage_de.items():
        emp = emps[0]
        j = journaux.get(cbox)
        if not j or j.get("erreur"):
            continue
        tt = [t for t in taches if t["employe"] in emps and t.get("type") not in fg.TYPES_HORS]
        horaires = cfg_t["horaires"].get(emp) or (cfg_t["debut"], cfg_t["fin"])
        depart = cfg_t["departs"].get(emp) or cfg_t["depot"]
        for e in ecarts_pure(jour, tt, j, lieux, horaires, depart, cfg_t["depot"], cfg_t.get("coef") or 1.3):
            if emp not in noms:
                noms[emp] = frappe.db.get_value("Employee", emp, "employee_name") or emp
            e.update(date=jour, employe=emp, employe_nom=noms[emp], cbox=cbox, vehicule=cfg["cbox_vehicule"].get(cbox) or cbox,
                     cle=_cle(jour, emp, e["type"], e["debut"]))
            trouves.append(e)
    bilan = {"jour": str(jour), "ecarts": len(trouves), "par_type": {t: len([e for e in trouves if e["type"] == t]) for t in TYPES}, "ecrit": ecrire}
    if not ecrire:
        bilan["lignes"] = trouves
        return bilan
    existants = {r.cle: r for r in frappe.get_all(ECART, filters={"date": jour}, fields=["name", "cle", "justification"])}
    vus = set()
    for e in trouves:
        vus.add(e["cle"])
        anc = existants.get(e["cle"])
        doc = frappe.get_doc(ECART, anc.name) if anc else frappe.new_doc(ECART)
        doc.update({k: v for k, v in e.items() if k not in ("justification", "note", "justifie_par", "justifie_le")})
        doc.save(ignore_permissions=True)
    for cle, r in existants.items():
        if cle not in vus and not r.justification:                 # disparu au recalcul, jamais justifié : on l'enlève
            frappe.delete_doc(ECART, r.name, ignore_permissions=True, force=True)
    return bilan


def calculer_periode(du, au) -> dict:
    """Lot : du → au inclus (bench execute customization_app.flotte_circuit.calculer_periode --args "['2026-09-14','2026-10-15']")."""
    du, au = getdate(du), getdate(au)
    out = {"jours": 0, "ecarts": 0, "erreurs": []}
    j = du
    while j <= au:
        try:
            b = calculer(j)
            out["jours"] += 1
            out["ecarts"] += b["ecarts"]
            frappe.db.commit()
        except Exception as ex:
            out["erreurs"].append("%s : %s" % (j, str(ex)[:120]))
        j = add_days(j, 1)
    return out


def cron_du_soir():
    """21 h 10, après l'appariement : aujourd'hui et la veille (boîtier qui remonte tard)."""
    cfg = fg.config()
    if not (cfg["actif"] and cfg["utilisateur"] and cfg["mot_de_passe"] and cfg["cboxes"]):
        return
    for jour in (add_days(nowdate(), -1), nowdate()):
        try:
            calculer(jour)
        except Exception:
            frappe.log_error(frappe.get_traceback()[-1500:], "Sortie circuit : calcul %s" % jour)


# ── Page ─────────────────────────────────────────────────────────────────────

def statistiques_pure(lignes: list, journees: list) -> list:
    """Par employé : jours suivis, km, écarts par type, minutes d'arrêts inexpliqués, km de détour, justifiés / à voir. PURE."""
    par = {}
    for j in journees:
        e = par.setdefault(j["employe"] or "?", {"employe": j["employe"], "employe_nom": j.get("employe_nom") or j["employe"], "jours": 0, "km": 0.0,
                                                 "arrets": 0, "minutes_arrets": 0, "hors_horaires": 0, "detours": 0, "km_detour": 0.0,
                                                 "justifies": 0, "a_voir": 0, "anomalies": 0})
        e["jours"] += 1
        e["km"] += flt(j.get("km"))
    for l in lignes:
        e = par.setdefault(l["employe"] or "?", {"employe": l["employe"], "employe_nom": l.get("employe_nom") or l["employe"], "jours": 0, "km": 0.0,
                                                 "arrets": 0, "minutes_arrets": 0, "hors_horaires": 0, "detours": 0, "km_detour": 0.0,
                                                 "justifies": 0, "a_voir": 0, "anomalies": 0})
        if l["type"] == "Arrêt inexpliqué":
            e["arrets"] += 1
            e["minutes_arrets"] += cint(l.get("minutes"))
        elif l["type"] == "Hors horaires":
            e["hors_horaires"] += 1
        else:
            e["detours"] += 1
            e["km_detour"] += flt(l.get("km"))
        if l.get("justification") == "Anomalie confirmée":
            e["anomalies"] += 1
        elif l.get("justification"):
            e["justifies"] += 1
        else:
            e["a_voir"] += 1
    for e in par.values():
        e["km"] = round(e["km"], 1)
        e["km_detour"] = round(e["km_detour"], 1)
        e["km_par_jour"] = round(e["km"] / e["jours"], 1) if e["jours"] else None
    return sorted(par.values(), key=lambda e: (-e["a_voir"], -e["arrets"], e["employe_nom"] or ""))


LIEU_M = 200                   # deux arrêts à moins de 200 m = le même endroit


def lieux_recurrents(lignes: list, minimum: int = 2) -> list:
    """Les endroits où l'on s'arrête sans raison PLUSIEURS fois (domicile non réglé, fournisseur, café…) : à justifier
    d'un coup, ou à ajouter aux domiciles du réglage des tournées. Regroupement par distance (≤ LIEU_M du premier arrêt
    de l'endroit), pas par grille — une grille coupe un même endroit en deux. PURE."""
    lieux = []
    for l in lignes:
        if l.get("type") != "Arrêt inexpliqué" or not l.get("lat"):
            continue
        pt = (flt(l["lat"]), flt(l["lng"]))
        c = next((c for c in lieux if fg._m(pt, (c["lat"], c["lng"])) <= LIEU_M), None)
        if c is None:
            c = {"lat": pt[0], "lng": pt[1], "n": 0, "minutes": 0, "employes": {}, "noms": [], "a_voir": 0, "justifications": {}}
            lieux.append(c)
        c["n"] += 1
        c["minutes"] += cint(l.get("minutes"))
        emp = l.get("employe_nom") or l.get("employe") or "?"
        c["employes"][emp] = c["employes"].get(emp, 0) + 1
        c["noms"].append(l["name"])
        if l.get("justification"):
            c["justifications"][l["justification"]] = c["justifications"].get(l["justification"], 0) + 1
        else:
            c["a_voir"] += 1
    out = [dict(c, employes=sorted(c["employes"].items(), key=lambda x: -x[1])) for c in lieux if c["n"] >= minimum]
    return sorted(out, key=lambda c: -c["minutes"])


@frappe.whitelist()
def justifier_lot(names, justification="", note=""):
    """Tous les arrêts d'un même endroit d'un coup."""
    _garde()
    names = frappe.parse_json(names) if isinstance(names, str) else names
    for n in names:
        justifier(n, justification, note)
    return {"n": len(names)}


@frappe.whitelist()
def sorties(du=None, au=None, employe=None):
    """Les écarts d'une période + statistiques par employé + journées suivies. Sans `du`, la période part du réglage
    « Statistiques depuis le » (Config Flotte GPS), sinon des 30 derniers jours."""
    _garde()
    au = getdate(au or nowdate())
    depuis = fg.stats_depuis()
    du = getdate(du) if du else (depuis if depuis and depuis <= au else add_days(au, -29))
    filtres = {"date": ["between", [du, au]]}
    if employe:
        filtres["employe"] = employe
    lignes = frappe.get_all(ECART, filters=filtres, order_by="date desc, employe_nom asc, debut asc",
                            fields=["name", "cle", "date", "employe", "employe_nom", "vehicule", "type", "debut", "fin", "minutes", "km",
                                    "lat", "lng", "km_client", "client_proche", "tache_proche", "detail", "justification", "note",
                                    "justifie_par", "justifie_le"], limit_page_length=0)
    fj = {"date": ["between", [du, au]], "employe": ["is", "set"]}
    if employe:
        fj["employe"] = employe
    journees = frappe.get_all(fg.JOURNEE, filters=fj, fields=["date", "employe", "employe_nom", "km", "vehicule"], limit_page_length=0)
    employes = sorted({(l["employe"], l["employe_nom"]) for l in lignes} | {(j["employe"], j["employe_nom"]) for j in journees},
                      key=lambda x: x[1] or "")
    return {"du": str(du), "au": str(au), "stats_depuis": str(depuis) if depuis else None, "lignes": lignes,
            "stats": statistiques_pure(lignes, journees), "lieux": lieux_recurrents(lignes),
            "employes": [{"employe": e, "nom": n} for e, n in employes if e], "justifications": list(JUSTIFICATIONS),
            "types": list(TYPES), "seuils": {"arret_min": ARRET_MIN, "distance_km": DISTANCE_KM, "distance_approx_km": DISTANCE_APPROX_KM,
                                              "ratio_detour": RATIO_DETOUR, "km_detour_min": KM_DETOUR_MIN}}


@frappe.whitelist()
def justifier(name, justification="", note=""):
    _garde()
    if justification not in JUSTIFICATIONS:
        frappe.throw("Justification inconnue : %s" % justification)
    doc = frappe.get_doc(ECART, name)
    doc.update({"justification": justification, "note": (note or "").strip(),
                "justifie_par": frappe.session.user if justification else None,
                "justifie_le": now_datetime() if justification else None})
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "justification": doc.justification}


@frappe.whitelist()
def recalculer(du, au):
    """Bouton « Recalculer la période » : au plus 62 jours d'un coup (chaque jour lit la plateforme GPS)."""
    _garde()
    du, au = getdate(du), getdate(au)
    if (au - du).days > 62:
        frappe.throw("Au plus deux mois à la fois")
    return calculer_periode(du, au)
