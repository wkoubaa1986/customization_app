"""Repose les partages des tâches partenaire encore ouvertes.

La validation d'une « Liste Appelle Entretien » retirait au partenaire TOUS les
partages des clients de la liste, y compris ceux que le Server Script
« Autorisation Sales order partenaire » pose quand une tâche lui est affectée
(client + adresses, tâche, commande). Le partenaire ouvrait ensuite sa tâche et
ne pouvait plus créer la commande : « Pas d'autorisation pour Client Raouf
gaddab » (Tache-09310, liste LIST-30-10-2026-00002 validée le 04/11/2026).

Le retrait est débranché (hooks.py). Ce patch remet, pour chaque tâche ouverte
du partenaire, exactement ce que pose le Server Script — mêmes droits — sans
toucher aux partages déjà présents. Idempotent.
"""
import frappe
from frappe.share import add_docshare, get_share_name

from customization_app.api import PARTNER_USER

SANS_CONTROLE = {"ignore_share_permission": True}
FERMEES = ("Completed", "Cancelled")

# Droits identiques à ceux du Server Script « Autorisation Sales order partenaire ».
DROITS_CLIENT = {"read": 1, "write": 0, "submit": 0, "share": 1}
DROITS_TACHE = {"read": 1, "write": 1, "submit": 0, "share": 0}
DROITS_COMMANDE = {"read": 1, "write": 1, "submit": 1, "share": 0}


def a_partager(tache: dict, adresses_du_client) -> list:
    """[(doctype, name, droits)] qu'une tâche partenaire ouverte doit lui ouvrir. PURE.

    `adresses_du_client(client)` -> noms des Address liées au client (injecté).
    """
    out = [("Tache de travail", tache["name"], DROITS_TACHE)]
    client = tache.get("custom_client")
    if client:
        out.append(("Customer", client, DROITS_CLIENT))
        out += [("Address", a, DROITS_CLIENT) for a in adresses_du_client(client)]
    if tache.get("commande_client"):
        out.append(("Sales Order", tache["commande_client"], DROITS_COMMANDE))
    return out


def _adresses(client):
    return frappe.get_all("Dynamic Link", filters={"link_doctype": "Customer", "link_name": client,
                                                   "parenttype": "Address"}, pluck="parent")


def execute():
    employe = frappe.db.get_value("Employee", {"user_id": PARTNER_USER}, "name")
    if not employe:
        return []
    taches = frappe.get_all("Tache de travail",
                            filters={"custom_choix_du_staff": employe, "status": ["not in", FERMEES]},
                            fields=["name", "custom_client", "commande_client"])
    reposes = []
    for tache in taches:
        for doctype, name, droits in a_partager(tache, _adresses):
            if not frappe.db.exists(doctype, name) or get_share_name(doctype, name, PARTNER_USER, 0):
                continue
            add_docshare(doctype, name, user=PARTNER_USER, everyone=0, flags=SANS_CONTROLE, notify=0, **droits)
            reposes.append((doctype, name))
    if reposes:
        frappe.db.commit()
    return reposes
