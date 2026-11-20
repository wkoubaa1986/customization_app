"""Position du téléphone des employés, relevée À CHAQUE ACTION dans l'app (demande du 20/11/2026).

Pourquoi : le suivi terrain attribue les véhicules suivis par GPS aux techniciens d'après leurs arrêts chez les
clients et le lieu de la nuit — et ça change tous les jours (« l'identification journalière doit se faire
naturellement »). Ça échoue quand deux techniciens roulent dans la même voiture, quand l'un n'a pas de véhicule
suivi (Hedi ibidhii : 0 passage confirmé sur 64), ou quand le client n'a pas de position connue. La position du
téléphone au moment d'une action (clôture, photo, appel, caisse) tranche : on la compare aux véhicules à l'arrêt
au même instant, et elle donne une position au client qu'on ne savait pas placer.

Ce qu'on garde : seulement un relevé assez précis (≤ PRECISION_MAX ; au-delà c'est le réseau qui répond, à 1-3 km,
et un tel point tromperait plus qu'il n'aiderait), avec l'action et le document concernés. Le relevé est demandé par
le navigateur au moment du geste, écran ouvert — l'employé voit la demande d'autorisation de son téléphone, et
l'app le dit une fois (public/js/flotte_position.js). Un refus ne bloque jamais l'action.

Les fonctions du haut sont pures (sans site) : tests/test_flotte_positions.py.
"""
from __future__ import annotations

from datetime import timedelta

import frappe
from frappe.utils import cint, flt, get_datetime, getdate, now_datetime

DOCTYPE = "Position Employe"
PRECISION_MAX = 200          # m — au-delà, le relevé est refusé (position réseau, pas GPS)
PRECISION_CLIENT = 60        # m — en deçà, le relevé peut servir de position au client (si on n'en a pas de meilleure)
ACTIONS = ("clôture", "photo", "appel", "caisse", "autre")
TUNISIE = ((30.0, 38.0), (7.0, 12.0))


def acceptable(lat, lng, precision) -> tuple[bool, str]:
    """Le relevé vaut-il d'être gardé ? PURE. -> (oui, raison du refus)."""
    lat, lng, precision = flt(lat), flt(lng), cint(precision)
    if not (TUNISIE[0][0] <= lat <= TUNISIE[0][1] and TUNISIE[1][0] <= lng <= TUNISIE[1][1]):
        return False, "hors de Tunisie"
    if precision <= 0:
        return False, "précision inconnue"
    if precision > PRECISION_MAX:
        return False, "trop imprécis (%d m, position réseau)" % precision
    return True, ""


def position_telephone(releves: list, tache: str, precision_max: int = PRECISION_CLIENT):
    """Le meilleur relevé pris SUR cette tâche (clôture, photo) et assez précis pour positionner le client :
    (lat, lng) ou None. PURE. `releves` : [{reference_name, lat, lng, precision}]."""
    bons = [r for r in releves or [] if r.get("reference_name") == tache and 0 < cint(r.get("precision")) <= precision_max]
    if not bons:
        return None
    r = min(bons, key=lambda r: cint(r.get("precision")))
    return (flt(r["lat"]), flt(r["lng"]))


@frappe.whitelist(methods=["POST"])
def enregistrer(lat, lng, precision=None, action="autre", doctype=None, name=None):
    """Un relevé de l'utilisateur connecté, au moment d'une action. Jamais bloquant pour l'écran : un refus (trop
    imprécis, pas d'employé) est rendu, pas levé. -> {enregistre, raison}"""
    user = frappe.session.user
    if user in ("Guest", "Administrator"):
        return {"enregistre": False, "raison": "pas un employé"}
    employe = frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, ["name", "employee_name"], as_dict=True)
    if not employe:
        return {"enregistre": False, "raison": "pas un employé"}
    ok, raison = acceptable(lat, lng, precision)
    if not ok:
        return {"enregistre": False, "raison": raison}
    if doctype and not (frappe.db.exists("DocType", doctype) and name and frappe.db.exists(doctype, name)):
        doctype, name = None, None
    doc = frappe.get_doc({"doctype": DOCTYPE, "employe": employe.name, "employe_nom": employe.employee_name, "user": user,
                          "horodatage": now_datetime(), "action": (action or "autre")[:40], "lat": flt(lat, 6), "lng": flt(lng, 6),
                          "precision": cint(precision), "reference_doctype": doctype, "reference_name": name})
    doc.insert(ignore_permissions=True)
    # le suivi en direct relit sa position et celle des véhicules au prochain appel
    try:
        frappe.cache().delete_value("flotte_gps:suivi:%s" % getdate())
    except Exception:
        pass
    return {"enregistre": True, "raison": ""}


def du_jour(jour) -> list:
    """Les relevés d'un jour : [{employe, heure, lat, lng, precision, action, reference_name}]."""
    if not frappe.db.exists("DocType", DOCTYPE):
        return []
    jour = getdate(jour)
    rows = frappe.get_all(DOCTYPE, filters={"horodatage": ["between", ["%s 00:00:00" % jour, "%s 23:59:59" % jour]]},
                          fields=["employe", "horodatage", "lat", "lng", "precision", "action", "reference_doctype", "reference_name"],
                          order_by="horodatage", limit_page_length=0)
    return [{"employe": r.employe, "heure": get_datetime(r.horodatage), "lat": flt(r.lat), "lng": flt(r.lng),
             "precision": cint(r.precision), "action": r.action, "reference_name": r.reference_name
             if r.reference_doctype == "Tache de travail" else None} for r in rows]


def autour(jour, marge_h: int = 12) -> list:
    """Comme `du_jour`, élargi de part et d'autre (une clôture tardive, après minuit)."""
    if not frappe.db.exists("DocType", DOCTYPE):
        return []
    d0 = get_datetime("%s 00:00:00" % getdate(jour)) - timedelta(hours=marge_h)
    d1 = get_datetime("%s 23:59:59" % getdate(jour)) + timedelta(hours=marge_h)
    rows = frappe.get_all(DOCTYPE, filters={"horodatage": ["between", [d0, d1]]},
                          fields=["employe", "horodatage", "lat", "lng", "precision", "action", "reference_doctype", "reference_name"],
                          order_by="horodatage", limit_page_length=0)
    return [{"employe": r.employe, "heure": get_datetime(r.horodatage), "lat": flt(r.lat), "lng": flt(r.lng),
             "precision": cint(r.precision), "action": r.action, "reference_name": r.reference_name
             if r.reference_doctype == "Tache de travail" else None} for r in rows]
