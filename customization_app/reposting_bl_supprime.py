"""Bon de livraison annulé puis SUPPRIMÉ : la re-valorisation d'ERPNext ne doit pas rester en échec.

Constaté le 02/11/2026 (e-mails « Error while reposting item valuation »). La finalisation d'une tâche
partenaire (`api.partner_finalize_order`) annule les BL automatiques de la commande, pose un BL
consolidé « Main d'œuvre », puis SUPPRIME les anciens BL. Or l'annulation d'un BL crée un
« Repost Item Valuation » basé sur la transaction : quand il tourne (planificateur, quelques minutes
plus tard), le BL n'existe plus → « Delivery Note … not found », statut Failed, e-mail aux Stock
Manager. Treize échecs de ce genre depuis août 2026, un à deux par clôture partenaire.

CE QUI EST EN JEU. Pour un BL sans article de stock (main d'œuvre), il n'y a rien à recalculer :
l'échec est seulement du bruit. Pour un BL qui portait des articles de stock, l'annulation a remis
les quantités mais le recalcul des écritures POSTÉRIEURES (quantité après mouvement, taux de
valorisation) n'a jamais eu lieu : il faut le relancer PAR ARTICLE ET ENTREPÔT, depuis la date du
BL, ce qui ne dépend pas de l'existence du bon.

DONC : avant de supprimer un bon annulé, on remplace son repost « Transaction » par des reposts
« Item and Warehouse » (un par couple article de stock / entrepôt) et on passe le premier en
Skipped. `reparer_orphelins` fait la même chose après coup pour les échecs déjà présents, à partir
de la copie gardée dans Deleted Document.
"""
from __future__ import annotations

import json

import frappe

STATUTS_EN_ATTENTE = ("Queued", "Failed")


def paires_stock(lignes, est_stock) -> list:
    """[(article, entrepôt)] distincts des lignes d'ARTICLES DE STOCK, dans l'ordre d'apparition. Pure."""
    vues, out = set(), []
    for ligne in lignes or []:
        lire = ligne.get if isinstance(ligne, dict) else (lambda k: getattr(ligne, k, None))
        code, entrepot = lire("item_code"), lire("warehouse")
        if not code or not entrepot or (code, entrepot) in vues or not est_stock(code):
            continue
        vues.add((code, entrepot))
        out.append((code, entrepot))
    return out


def _est_article_de_stock(code) -> bool:
    return bool(frappe.db.get_value("Item", code, "is_stock_item"))


def remplacer_reposts(voucher_type, voucher_no, lignes, posting_date, posting_time, company=None) -> dict:
    """Reposts « Item and Warehouse » pour les articles de stock du bon, puis ses reposts « Transaction »
    en attente passent en Skipped. Renvoie {"crees": [...], "ignores": [...]}."""
    crees = []
    for code, entrepot in paires_stock(lignes, _est_article_de_stock):
        # Deux bons du même jour pour les mêmes articles (cas du 15/08/2026) : un seul repost suffit.
        if frappe.db.exists("Repost Item Valuation", {"based_on": "Item and Warehouse", "item_code": code,
                                                      "warehouse": entrepot, "posting_date": posting_date,
                                                      "docstatus": 1, "status": "Queued"}):
            continue
        repost = frappe.get_doc({
            "doctype": "Repost Item Valuation",
            "based_on": "Item and Warehouse",
            "item_code": code,
            "warehouse": entrepot,
            "posting_date": posting_date,
            "posting_time": posting_time or "00:00:00",
            "company": company,
            "allow_negative_stock": 1,
            "allow_zero_rate": 0,
        })
        repost.flags.ignore_links = True
        repost.flags.ignore_permissions = True
        repost.submit()
        crees.append(repost.name)
    ignores = frappe.get_all("Repost Item Valuation",
                             filters={"voucher_type": voucher_type, "voucher_no": voucher_no,
                                      "based_on": "Transaction", "docstatus": 1,
                                      "status": ["in", STATUTS_EN_ATTENTE]},
                             pluck="name")
    for name in ignores:
        frappe.db.set_value("Repost Item Valuation", name, "status", "Skipped", update_modified=False)
    return {"crees": crees, "ignores": ignores}


def avant_suppression(doc) -> dict:
    """À appeler sur un bon ANNULÉ (docstatus 2), juste avant `frappe.delete_doc`."""
    return remplacer_reposts(doc.doctype, doc.name, doc.get("items"), doc.posting_date, doc.posting_time,
                             doc.get("company"))


def reparer_orphelins(bons_supprimes_depuis="2026-08-01") -> dict:
    """Reposts « Transaction » en attente ou en échec dont le bon n'existe plus.

    Bon supprimé DEPUIS `bons_supprimes_depuis` (début du flux partenaire) : remplacé d'après la copie de
    Deleted Document (articles, date, heure). Supprimé AVANT, ou sans copie exploitable : simplement
    Skipped — relancer une valorisation depuis 2024-2025 (bons supprimés à la main à l'époque, exercices
    clos) est une décision à prendre avec l'utilisateur, pas un effet de bord de ce nettoyage.
    """
    resultats = {}
    for r in frappe.get_all("Repost Item Valuation",
                            filters={"based_on": "Transaction", "docstatus": 1,
                                     "status": ["in", STATUTS_EN_ATTENTE]},
                            fields=["name", "voucher_type", "voucher_no", "company"]):
        if not r.voucher_no or frappe.db.exists(r.voucher_type, r.voucher_no):
            continue
        copie = frappe.db.get_value("Deleted Document",
                                    {"deleted_doctype": r.voucher_type, "deleted_name": r.voucher_no},
                                    ["data", "creation"], order_by="creation desc", as_dict=True)
        lignes, date, heure = [], None, None
        if copie and str(copie.creation)[:10] >= bons_supprimes_depuis:
            d = json.loads(copie.data)
            lignes, date, heure = d.get("items") or [], d.get("posting_date"), d.get("posting_time")
        if lignes and date:
            resultats[r.voucher_no] = remplacer_reposts(r.voucher_type, r.voucher_no, lignes, date, heure,
                                                        r.company)
        else:
            frappe.db.set_value("Repost Item Valuation", r.name, "status", "Skipped", update_modified=False)
            resultats[r.voucher_no] = {"crees": [], "ignores": [r.name]}
    return resultats
