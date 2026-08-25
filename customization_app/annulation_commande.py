"""Annulation d'une commande client : cascade BL + échéancier, suppression forcée.

LE GESTE MÉTIER
---------------
Une commande annulée traîne souvent derrière elle un BL, un échéancier
d'entretien et des références dans le calendrier d'autres échéanciers. Le
Server Script « cancel sales order payment » (Before Cancel) supprime déjà les
paiements/écritures et ANNULE les BL et les échéanciers ; il ne SUPPRIME pas
les BL, ignore les magasins désactivés et ne touche pas au calendrier. Ce
module complète la cascade — il tourne AVANT le Server Script (les hooks
Python d'un événement passent avant les Server Scripts), donc les boucles du
script trouvent des pièces déjà disparues et ne font rien.

CE QUI EST FAIT À L'ANNULATION (before_cancel)
----------------------------------------------
  - BL liés (brouillons ou validés) : annulés puis SUPPRIMÉS. L'annulation
    fait le reposting standard (SLE/GL inversés : le stock revient dans le
    magasin d'origine) ;
  - magasin désactivé sur le BL : réactivé le temps de l'annulation (la
    validation SLE refuse un magasin désactivé), le stock repris est ensuite
    TRANSFÉRÉ vers le magasin par défaut (Stock Settings) par une entrée de
    stock, puis le magasin est re-désactivé ;
  - échéanciers dont la table Articles référence la commande : annulés puis
    SUPPRIMÉS (liens ignorés : une visite soumise ne doit pas bloquer) ;
  - lignes du CALENDRIER (Maintenance Schedule Detail) d'autres échéanciers
    portant la commande dans custom_sales_order : la référence est retirée et
    la ligne repasse « En Attente » (completion_status = Pending, date réelle
    effacée).

À LA SUPPRESSION (on_trash, commandes ANNULÉES uniquement)
----------------------------------------------------------
Une commande annulée doit pouvoir être supprimée même si d'autres pièces la
référencent encore (Tâche de travail, brouillons de paiement…). on_trash passe
AVANT le contrôle des liens de delete_doc : on retire ici toute référence
encore bloquante (liens statiques et dynamiques, pièces non annulées), avec un
commentaire de traçabilité sur chaque pièce déliée. Les brouillons de commande
gardent le comportement standard (pas de passe-droit).
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt


# --------------------------------------------------------------- annulation

def before_cancel_sales_order(doc, method=None):
    """Cascade complète, dans la transaction de l'annulation : tout écart
    lève et annule l'ensemble (rollback), la commande reste intacte."""
    _annuler_et_supprimer_bls(doc)
    _nettoyer_echeanciers(doc)


def _bls_de(commande: str) -> list:
    return frappe.db.sql(
        """select distinct dn.name, dn.docstatus
           from `tabDelivery Note` dn
           join `tabDelivery Note Item` dni on dni.parent = dn.name
           where dni.against_sales_order = %s""",
        commande, as_dict=True)


def _annuler_et_supprimer_bls(doc):
    for b in _bls_de(doc.name):
        if b.docstatus != 1:
            # brouillon (aucun mouvement de stock) ou déjà annulé (reposting
            # déjà fait) : suppression directe
            frappe.delete_doc("Delivery Note", b.name, force=True, ignore_permissions=True)
            continue

        dn = frappe.get_doc("Delivery Note", b.name)
        magasins = {it.warehouse for it in dn.items if it.warehouse}
        desactives = [w for w in magasins if frappe.db.get_value("Warehouse", w, "disabled")]

        # la validation SLE refuse un magasin désactivé : réactivation temporaire
        for w in desactives:
            frappe.db.set_value("Warehouse", w, "disabled", 0, update_modified=False)

        dn.flags.ignore_permissions = True
        dn.cancel()  # reposting standard : le stock revient dans le magasin d'origine

        # le stock repris dans un magasin désactivé n'a rien à y faire :
        # transfert vers le magasin par défaut AVANT de re-désactiver
        if desactives:
            _transferer_vers_magasin_defaut(dn, desactives)

        # AVANT la suppression : le repost créé par l'annulation référence le
        # BL par son nom et échouerait à jamais (« BL introuvable ») une fois
        # la pièce supprimée — on le remplace par des recalculs par
        # (article, magasin), qui ne dépendent plus du BL.
        _convertir_reposts_du_bl(dn)

        frappe.delete_doc("Delivery Note", dn.name, force=True, ignore_permissions=True)

        for w in desactives:
            frappe.db.set_value("Warehouse", w, "disabled", 1, update_modified=False)


def _convertir_reposts_du_bl(dn):
    """Bascule les Repost Item Valuation « Transaction » du BL (en attente) en
    reposts « Item and Warehouse » à la date du BL, puis marque les originaux
    Skipped. Sans cela, chaque annulation laisserait un repost en échec."""
    en_attente = frappe.get_all(
        "Repost Item Valuation",
        filters={"voucher_type": "Delivery Note", "voucher_no": dn.name,
                 "status": ["in", ["Queued", "In Progress"]]},
        pluck="name")
    if not en_attente:
        return
    for nom in en_attente:
        frappe.db.set_value("Repost Item Valuation", nom, "status", "Skipped",
                            update_modified=False)
    paires = {(it.item_code, it.warehouse) for it in dn.items
              if it.warehouse and frappe.db.get_value("Item", it.item_code, "is_stock_item")}
    for article, entrepot in paires:
        riv = frappe.get_doc({
            "doctype": "Repost Item Valuation",
            "based_on": "Item and Warehouse",
            "item_code": article,
            "warehouse": entrepot,
            "posting_date": dn.posting_date,
            "posting_time": dn.posting_time,
            "company": dn.company,
            "allow_negative_stock": 1,
        })
        riv.flags.ignore_permissions = True
        riv.insert()
        riv.submit()


def _transferer_vers_magasin_defaut(dn, magasins_desactives: list):
    defaut = frappe.db.get_single_value("Stock Settings", "default_warehouse")
    if not defaut:
        frappe.throw(_(
            "Le BL {0} touche un magasin désactivé ({1}) mais aucun magasin par défaut "
            "n'est défini dans les paramètres de stock — annulation abandonnée.")
            .format(dn.name, ", ".join(magasins_desactives)))

    # seuls les articles STOCKÉS bougent : une ligne de main d'œuvre (M-E-OD…)
    # figure sur le BL mais n'a ni SLE ni transfert possible
    lignes = [it for it in dn.items
              if it.warehouse in magasins_desactives and flt(it.stock_qty) > 0
              and it.warehouse != defaut
              and frappe.db.get_value("Item", it.item_code, "is_stock_item")]
    if not lignes:
        return

    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Transfer"
    se.purpose = "Material Transfer"
    se.company = dn.company
    # À LA DATE DU BL ANNULÉ, pas à aujourd'hui : l'annulation rend le stock
    # dans l'historique à la date du BL — un transfert daté du jour laisserait
    # le stock « posé » dans le magasin désactivé sur toute la période, et
    # fausserait la valorisation en aval (avertissements de stock négatif).
    se.set_posting_time = 1
    se.posting_date = dn.posting_date
    se.posting_time = dn.posting_time
    se.remarks = _("Reprise du BL {0} (commande {1} annulée) : stock déplacé du "
                   "magasin désactivé vers {2}.").format(
        dn.name, ", ".join({it.against_sales_order for it in lignes if it.against_sales_order}) or "?",
        defaut)
    for it in lignes:
        se.append("items", {
            "item_code": it.item_code,
            "qty": flt(it.stock_qty),
            "uom": it.stock_uom,
            "stock_uom": it.stock_uom,
            "conversion_factor": 1,
            "s_warehouse": it.warehouse,
            "t_warehouse": defaut,
        })
    se.flags.ignore_permissions = True
    se.insert()
    se.submit()


def _nettoyer_echeanciers(doc):
    # 1. la commande est dans la table Articles → l'échéancier entier est
    #    annulé puis supprimé (force : ses propres visites ne doivent pas bloquer)
    parents = set(frappe.get_all("Maintenance Schedule Item",
                                 filters={"sales_order": doc.name}, pluck="parent"))
    for nom in parents:
        if not frappe.db.exists("Maintenance Schedule", nom):
            continue
        ms = frappe.get_doc("Maintenance Schedule", nom)
        if ms.docstatus == 1:
            ms.flags.ignore_permissions = True
            ms.flags.ignore_links = True
            ms.cancel()
        frappe.delete_doc("Maintenance Schedule", nom, force=True, ignore_permissions=True)

    # 2. la commande est posée sur une ligne du CALENDRIER d'un autre
    #    échéancier (custom_sales_order) → on retire la référence et la ligne
    #    repasse « En Attente ». SQL direct : les parents sont soumis, un
    #    save() de ligne enfant serait refusé.
    lignes = frappe.db.sql(
        """select name, parent from `tabMaintenance Schedule Detail`
           where custom_sales_order = %s""", doc.name, as_dict=True)
    for l in lignes:
        frappe.db.sql(
            """update `tabMaintenance Schedule Detail`
               set custom_sales_order = null, completion_status = 'Pending',
                   actual_date = null
               where name = %s""", l.name)
    for parent in {l.parent for l in lignes if l.parent}:
        if frappe.db.exists("Maintenance Schedule", parent):
            frappe.get_doc("Maintenance Schedule", parent).add_comment(
                "Comment",
                _("Commande {0} annulée : ligne(s) du calendrier remise(s) « En Attente ».")
                .format(doc.name))


# -------------------------------------------------------------- suppression

def on_trash_sales_order(doc, method=None):
    """Une commande ANNULÉE se supprime même encore référencée ailleurs :
    on retire les références bloquantes avant le contrôle des liens."""
    if doc.docstatus != 2:
        return
    _delier_references(doc)


def _delier_references(doc):
    from frappe.model.dynamic_links import get_dynamic_link_map
    from frappe.model.rename_doc import get_link_fields

    ignores = set(frappe.get_hooks("ignore_links_on_delete") or [])
    delies = set()  # (doctype, nom) des pièces déliées, pour la traçabilité

    # liens statiques (champs Link → Sales Order), tables enfants comprises
    for df in get_link_fields("Sales Order"):
        dt, champ = df.get("parent"), df.get("fieldname")
        if not dt or df.get("issingle") or dt in ignores or not frappe.db.table_exists(dt):
            continue
        meta = frappe.get_meta(dt)
        colonnes = ["name", "docstatus"] + (["parent", "parenttype"] if meta.istable else [])
        lignes = frappe.db.get_values(dt, {champ: doc.name}, colonnes, as_dict=True)
        for l in lignes:
            # seules les pièces NON annulées bloquent la suppression
            if l.docstatus == 2:
                continue
            porteur = (l.parenttype, l.parent) if meta.istable else (dt, l.name)
            if porteur[0] in ignores or porteur == ("Sales Order", doc.name):
                continue
            frappe.db.sql(
                "update `tab{0}` set `{1}` = null where name = %s".format(dt, champ),
                l.name)
            delies.add(porteur)

    # liens dynamiques (reference_doctype/reference_name et assimilés)
    for df in get_dynamic_link_map().get("Sales Order", []):
        dt = df.parent
        if dt in ignores or not frappe.db.table_exists(dt):
            continue
        meta = frappe.get_meta(dt)
        if meta.issingle:
            continue
        colonnes = ["name", "docstatus"] + (["parent", "parenttype"] if meta.istable else [])
        lignes = frappe.db.get_values(
            dt, {df.options: "Sales Order", df.fieldname: doc.name}, colonnes, as_dict=True)
        for l in lignes:
            if l.docstatus == 2:
                continue
            porteur = (l.parenttype, l.parent) if meta.istable else (dt, l.name)
            if porteur[0] in ignores:
                continue
            frappe.db.sql(
                "update `tab{0}` set `{1}` = null, `{2}` = null where name = %s".format(
                    dt, df.fieldname, df.options),
                l.name)
            delies.add(porteur)

    for dt, nom in delies:
        if dt and nom and frappe.db.exists(dt, nom):
            try:
                frappe.get_doc(dt, nom).add_comment(
                    "Comment",
                    _("Référence retirée : la commande annulée {0} a été supprimée.")
                    .format(doc.name))
            except Exception:
                pass  # la traçabilité ne doit jamais empêcher la suppression
