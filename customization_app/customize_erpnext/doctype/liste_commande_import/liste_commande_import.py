"""Liste Commande Import — préparation d'une demande de cotation fournisseur.

Le controller recalcule les volumes (ligne + total) et capitalise le volume
unitaire saisi vers la fiche Article (Item.custom_volume_m3) pour qu'il soit
réutilisé dans les listes futures.
"""

import json

import frappe
from frappe.model.document import Document
from frappe.utils import flt


def _adds(row):
    """Articles additionnels embarqués sur une ligne pack (champ JSON)."""
    try:
        return json.loads(row.articles_additionnels or "[]") or []
    except Exception:
        return []


class ListeCommandeImport(Document):
    def validate(self):
        # lignes libres : une ligne totalement vide est retirée silencieusement ;
        # une ligne avec du contenu mais sans désignation est auto-nommée.
        kept = []
        for row in self.articles:
            if not row.item_code and not (row.item_name or "").strip():
                has_content = ((row.description or "").strip()
                               or (row.image or "").strip())
                if not has_content:
                    continue  # ligne placeholder vide -> on la retire
                row.item_name = f"Article libre {len(kept) + 1}"
            kept.append(row)
        if len(kept) != len(self.articles):
            self.articles = kept
            for i, row in enumerate(self.articles, 1):
                row.idx = i

        total = 0.0
        for row in self.articles:
            vol = flt(row.qty) * flt(row.volume_unitaire_m3)
            for a in _adds(row):  # volume des additionnels embarqués
                vol += flt(row.qty) * flt(a.get("qty_par_pack")) * flt(a.get("volume_unitaire_m3"))
            row.volume_ligne_m3 = vol
            total += row.volume_ligne_m3
            # capitaliser le volume unitaire vers la fiche Article
            if row.item_code and flt(row.volume_unitaire_m3) > 0:
                current = flt(frappe.db.get_value("Item", row.item_code, "custom_volume_m3"))
                if abs(current - flt(row.volume_unitaire_m3)) > 0.00005:
                    frappe.db.set_value("Item", row.item_code, "custom_volume_m3",
                                        flt(row.volume_unitaire_m3), update_modified=False)
        self.volume_total_m3 = total
        self.nb_articles = len(self.articles)
