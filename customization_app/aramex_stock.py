"""Commandes livrées par Aramex : le stock SORT du Magasin et y REVIENT, toujours.

Demande du 12/10/2026 : le colis Aramex se prépare au magasin, quelle que soit la sortie
principale de l'article (page Stock « Zones & sorties », Item Default.default_warehouse) — un
article dont la sortie est le Hall ou le stock d'un employé sort quand même du Magasin s'il part
chez Aramex. Le retour (BL de retour, retour_aramex.constater_retour) y rentre aussi.

Une commande est « Aramex » si elle porte les termes de paiement « Livraison Aramex » ou un
bordereau (même règle que cloture_tache._est_livraison_aramex). Deux points d'application :
- Sales Order.before_validate : les lignes d'une commande Aramex (brouillon ou à la validation)
  passent au Magasin — la réservation de stock vise le bon entrepôt ;
- Delivery Note.before_validate : toute ligne adossée à une commande Aramex (BL ou BL de retour)
  sort/rentre au Magasin, composants d'ensemble compris — couvre aussi les commandes validées
  avant d'être marquées Aramex. Le Server Script « Verif BL du magasin » passe APRÈS les hooks
  Python : le BL demande donc l'approbation magasin, comme tout BL qui sort du Magasin.
"""

from __future__ import annotations

import frappe

ENTREPOT_ARAMEX = "Magasins - A&S"
TERMES_ARAMEX = "Livraison Aramex"


def est_aramex(termes: str | None, bordereau: str | None) -> bool:
    return termes == TERMES_ARAMEX or bool((bordereau or "").strip())


def commandes_aramex(commandes) -> set[str]:
    commandes = [c for c in set(commandes or []) if c]
    if not commandes:
        return set()
    return {r.name for r in frappe.get_all("Sales Order", filters={"name": ["in", commandes]},
                                            fields=["name", "payment_terms_template", "custom_bordereau_aramex"])
            if est_aramex(r.payment_terms_template, r.custom_bordereau_aramex)}


def _entrepot_utilisable() -> bool:
    """Un Magasin désactivé ou renommé ne doit pas bloquer les ventes : la règle se tait."""
    return frappe.db.get_value("Warehouse", ENTREPOT_ARAMEX, "disabled") == 0


def _articles_a_stock(codes) -> set[str]:
    """Articles qui bougent le stock : articles stockés et ensembles (leurs composants suivent la ligne)."""
    codes = [c for c in set(codes) if c]
    if not codes:
        return set()
    stockes = set(frappe.get_all("Item", filters={"name": ["in", codes], "is_stock_item": 1}, pluck="name"))
    ensembles = set(frappe.get_all("Product Bundle", filters={"new_item_code": ["in", codes]}, pluck="new_item_code"))
    return stockes | ensembles


def _forcer(doc, lignes) -> bool:
    """Pose le Magasin sur ces lignes et sur les composants d'ensemble qui en dépendent."""
    a_stock = _articles_a_stock(l.item_code for l in lignes)
    lignes = [l for l in lignes if l.item_code in a_stock]
    for l in lignes:
        l.warehouse = ENTREPOT_ARAMEX
    parents = {l.name for l in lignes}
    for p in doc.get("packed_items") or []:
        if p.parent_detail_docname in parents:
            p.warehouse = ENTREPOT_ARAMEX
    return bool(lignes)


def sales_order_before_validate(doc, method=None):
    if not est_aramex(doc.get("payment_terms_template"), doc.get("custom_bordereau_aramex")):
        return
    if not _entrepot_utilisable():
        return
    _forcer(doc, doc.get("items") or [])


def delivery_note_before_validate(doc, method=None):
    lignes = doc.get("items") or []
    aramex = commandes_aramex(l.against_sales_order for l in lignes)
    if not aramex or not _entrepot_utilisable():
        return
    _forcer(doc, [l for l in lignes if l.against_sales_order in aramex])
