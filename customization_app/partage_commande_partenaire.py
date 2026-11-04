"""Commandes en litige : partage MANUEL d'une commande avec le partenaire (Economiq) — décision du 04/11/2026.

Remplace la règle automatique essayée le même jour (toute commande d'un client créé par le partenaire entrait au
Bilan Vente et lui était partagée) : c'est désormais un GESTE, commande par commande, pour les cas discutés.

Une commande partagée avec le compte partenaire :
  - lui devient visible, en lecture seule ;
  - entre DIRECTEMENT au Bilan Vente : bilan_vente._shared_orders compte les commandes partagées (section
    « Aqua World & Servicing », ou « Economiq » si sa tâche est affectée à l'employé partenaire).
Retirer le partage la retire du bilan des mois non encore validés (un mois validé reste figé côté Economiq).

⚠️ SEUL UN PARTAGE EN LECTURE SE RETIRE ICI. Un partage en écriture vient de la tâche affectée au partenaire
(Server Script « Autorisation Sales order partenaire ») : c'est la condition de son travail sur la commande.
"""

import frappe
from frappe import _
from frappe.share import add_docshare, get_share_name
from frappe.utils import cint

from customization_app.bilan_vente import PARTNER_USER

ROLES = ("System Manager", "Accounts Manager")   # ceux qui règlent déjà le bilan (bilan_vente.set_regle)
SANS_CONTROLE = {"ignore_share_permission": True}

AUCUN, MANUEL, TACHE = "aucun", "manuel", "tache"


def etat(partage) -> str:
    """AUCUN, MANUEL (lecture seule : posé par le bouton, retirable) ou TACHE (écriture). Fonction pure."""
    if not partage:
        return AUCUN
    return TACHE if cint(partage.get("write")) else MANUEL


def _partage(sales_order):
    nom = get_share_name("Sales Order", sales_order, PARTNER_USER, 0)
    return frappe.db.get_value("DocShare", nom, ["name", "write"], as_dict=True) if nom else None


@frappe.whitelist()
def etat_partage(sales_order):
    frappe.has_permission("Sales Order", "read", doc=sales_order, throw=True)
    return {"etat": etat(_partage(sales_order)), "peut_modifier": bool(set(frappe.get_roles()) & set(ROLES))}


@frappe.whitelist()
def partager(sales_order):
    """Partage la commande avec le partenaire : elle lui est visible et compte au Bilan Vente."""
    frappe.only_for(ROLES)
    commande = frappe.get_doc("Sales Order", sales_order)
    if commande.docstatus != 1:
        frappe.throw(_("Seule une commande validée peut être partagée avec le partenaire."))
    if not _partage(sales_order):
        add_docshare("Sales Order", sales_order, user=PARTNER_USER, read=1, write=0, submit=0, share=0,
                     everyone=0, flags=SANS_CONTROLE, notify=0)
        commande.add_comment("Comment", _("Commande partagée avec le partenaire : elle compte désormais au Bilan Vente."))
    return etat_partage(sales_order)


@frappe.whitelist()
def retirer(sales_order):
    """Retire un partage posé par le bouton : la commande ne compte plus au Bilan Vente."""
    frappe.only_for(ROLES)
    partage = _partage(sales_order)
    if etat(partage) == TACHE:
        frappe.throw(_("Ce partage vient de la tâche affectée au partenaire : il ne se retire pas ici."))
    if partage:
        frappe.delete_doc("DocShare", partage.name, ignore_permissions=True, flags=SANS_CONTROLE)
        frappe.get_doc("Sales Order", sales_order).add_comment(
            "Comment", _("Partage avec le partenaire retiré : la commande ne compte plus au Bilan Vente."))
    return etat_partage(sales_order)


def on_tache_update(doc, method=None):
    """Hook Tache de travail on_update — passe AVANT le Server Script « Autorisation Sales order partenaire ».

    ⚠️ LE SCRIPT NE POSE SON PARTAGE QUE S'IL N'EN EXISTE AUCUN. Quand la tâche d'une commande déjà partagée
    en lecture (par le bouton) est affectée au partenaire, il passerait son tour, et le partenaire ne pourrait
    ni modifier ni valider la commande qu'il exécute. On aligne donc le partage sur celui du script (lecture,
    écriture, validation) ; le script le trouve ensuite et ne fait rien. Les hooks Python passent avant les
    Server Scripts (cf. hooks.py, before_cancel de Sales Order).
    """
    so = doc.get("commande_client")
    if not so:
        return
    from customization_app.partenaire_clients import employe_partenaire

    employe = employe_partenaire()
    if not employe or doc.get("custom_choix_du_staff") != employe:
        return
    if etat(_partage(so)) == MANUEL:
        add_docshare("Sales Order", so, user=PARTNER_USER, read=1, write=1, submit=1, share=0,
                     everyone=0, flags=SANS_CONTROLE, notify=0)
