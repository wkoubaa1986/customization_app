"""Les achats se réceptionnent en MAGASIN, quel que soit l'entrepôt par défaut de l'article.

Décision utilisateur 30/10/2026 : 152 articles avaient « Hall » comme entrepôt par défaut, et une
commande, une réception ou une facture d'achat avec mise à jour de stock les envoyait au Hall
sans que personne ne le voie. Toute ligne d'article stocké d'un document d'achat part donc en
« Magasins - A&S » (en-tête et lignes), à la validation — le flux magasin → autres stocks passe
ensuite par les transferts habituels. Le patch `entrepot_achats_magasin` aligne aussi l'entrepôt
par défaut des articles.
"""
from __future__ import annotations

import frappe

ENTREPOT_ACHATS = "Magasins - A&S"
DOCTYPES = ("Purchase Order", "Purchase Receipt", "Purchase Invoice")


def lignes_a_forcer(lignes, est_stock, entrepot: str = ENTREPOT_ACHATS) -> list:
    """Les lignes dont l'entrepôt doit devenir `entrepot` : articles stockés, entrepôt différent.
    Fonction pure. `lignes` : [{item_code, warehouse}], `est_stock(item_code) -> bool`."""
    return [l for l in lignes or []
            if l.get("item_code") and est_stock(l.get("item_code")) and (l.get("warehouse") or "") != entrepot]


def forcer_entrepot_magasin(doc, method=None) -> None:
    """validate de Purchase Order / Purchase Receipt / Purchase Invoice (avec mise à jour de stock)."""
    if doc.doctype == "Purchase Invoice" and not doc.get("update_stock"):
        return
    if not frappe.db.exists("Warehouse", ENTREPOT_ACHATS):
        return
    stock = {}

    def est_stock(code):
        if code not in stock:
            stock[code] = bool(frappe.db.get_value("Item", code, "is_stock_item"))
        return stock[code]

    for l in lignes_a_forcer([{"item_code": i.item_code, "warehouse": i.get("warehouse"), "_row": i}
                              for i in doc.get("items") or []], est_stock):
        l["_row"].warehouse = ENTREPOT_ACHATS
    if doc.meta.has_field("set_warehouse") and doc.get("set_warehouse") != ENTREPOT_ACHATS \
            and any(est_stock(i.item_code) for i in doc.get("items") or [] if i.item_code):
        doc.set_warehouse = ENTREPOT_ACHATS
