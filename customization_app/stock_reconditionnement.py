"""Garde-fous sur une entrée de stock « Reconditionnement » (Repack).

Cas réel du 14/10/2026 (MAT-STE-2026-00171) : trois sorties cochées « produit fini », et ERPNext
a mis la totalité du coût consommé (1 224,474) sur deux coudes Acc001-B (2 × 609,097) au lieu
de l'osmoseur, qui est ressorti à 0. Résultat : Acc001-B valorisé 5,416 au Hall contre 0,097
ailleurs, et la marge minimale déclenchée sur ce seul stock.

Deux règles, à la validation :
  1. UN SEUL produit fini par reconditionnement (les autres sorties portent leur propre taux) ;
  2. une sortie dont le taux dépasse de plus de FACTEUR_MAX fois sa valorisation actuelle est
     refusée — c'est une erreur de saisie, pas un coût.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

FACTEUR_MAX = 3.0
#: en dessous, on ne compare pas : un taux de quelques millimes bouge beaucoup en proportion.
PLANCHER_COMPARAISON = 1.0


def anomalies_repack(lignes: list, valo_actuelle) -> list:
    """Les anomalies d'une liste de lignes de sortie. Fonction pure.
    `lignes` : [{item_code, qty, basic_rate, is_finished_item}] (sorties = lignes avec t_warehouse) ;
    `valo_actuelle(item_code) -> float` : valorisation courante (0 = inconnue, pas comparée)."""
    out = []
    finis = [l for l in lignes if l.get("is_finished_item")]
    if len(finis) > 1:
        out.append(_("Un reconditionnement n'a qu'UN produit fini : {0} lignes sont cochées « produit fini » ({1}). "
                     "Le coût des articles consommés irait sur la mauvaise ligne.")
                   .format(len(finis), ", ".join(l.get("item_code") or "?" for l in finis)))
    for l in lignes:
        taux, actuel = flt(l.get("basic_rate")), flt(valo_actuelle(l.get("item_code")))
        if actuel >= PLANCHER_COMPARAISON and taux > FACTEUR_MAX * actuel:
            out.append(_("{0} : taux de sortie {1} alors que sa valorisation actuelle est {2} (plus de {3} fois) : "
                         "vérifiez le produit fini et les taux.")
                       .format(l.get("item_code"), round(taux, 3), round(actuel, 3), int(FACTEUR_MAX)))
    return out


def _valo(item_code: str) -> float:
    r = frappe.db.sql("""select sum(stock_value), sum(actual_qty) from `tabBin`
                         where item_code = %s and actual_qty > 0""", (item_code,))
    if r and r[0][1]:
        return flt(r[0][0]) / flt(r[0][1])
    return flt(frappe.db.get_value("Item", item_code, "valuation_rate")) \
        or flt(frappe.db.get_value("Item", item_code, "last_purchase_rate"))


def stock_entry_validate(doc, method=None) -> None:
    if (doc.get("purpose") or "") != "Repack":
        return
    sorties = [{"item_code": i.item_code, "qty": i.qty, "basic_rate": i.basic_rate,
                "is_finished_item": i.get("is_finished_item")}
               for i in doc.get("items") or [] if i.get("t_warehouse") and not i.get("s_warehouse")]
    anomalies = anomalies_repack(sorties, _valo)
    if anomalies:
        frappe.throw("<br>".join(anomalies), title=_("Reconditionnement à vérifier"))
