"""Garde-fous sur une entrée de stock « Reconditionnement » (Repack).

Cas réel du 14/10/2026 (MAT-STE-2026-00171, créée par la page « Transformation d'articles ») :
l'osmoseur obtenu n'avait jamais été stocké (référence 0), la répartition l'a ignoré et a mis
les 1 218 DT consommés sur deux coudes Acc001-B (2 × 609,097) ; l'osmoseur est ressorti à 0.
Résultat : Acc001-B valorisé 5,416 au Hall contre 0,097 ailleurs, marge minimale faussée.

La répartition de l'outil est corrigée (`transformation_articles.repartir_valeur`) ; ce module
est le filet, à la validation de TOUTE écriture Repack, manuelle ou issue de l'outil :
  1. plusieurs produits finis sans taux manuel : refusé — ERPNext répartirait le coût au prorata
     des quantités, un coude vaudrait autant qu'un osmoseur ;
  2. un produit fini ressortant à 0 alors que de la valeur a été consommée : refusé ;
  3. un article obtenu à plus de FACTEUR_MAX fois sa valorisation actuelle, pour au moins
     SEUIL_ABSOLU DT d'écart : refusé — c'est une erreur de saisie, pas un coût.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

FACTEUR_MAX = 3.0
#: écart (taux − valorisation) × qté minimal pour que la règle 3 joue : quelques millimes sur
#: un raccord bougent beaucoup en proportion sans fausser quoi que ce soit.
SEUIL_ABSOLU = 20.0


def anomalies_repack(lignes: list, valo_actuelle, valeur_consommee=None) -> list:
    """Les anomalies d'une liste de lignes OBTENUES (entrepôt cible). Fonction pure.
    `lignes` : [{item_code, qty, basic_rate, is_finished_item, set_basic_rate_manually}] ;
    `valo_actuelle(item_code) -> float` : valorisation courante (0 = inconnue, pas comparée) ;
    `valeur_consommee` : valeur totale des articles consommés (None = inconnue)."""
    out = []
    finis = [l for l in lignes if l.get("is_finished_item")]
    if len(finis) > 1 and any(not l.get("set_basic_rate_manually") for l in finis):
        out.append(_("{0} produits finis sans taux manuel ({1}) : ERPNext répartirait le coût consommé au "
                     "prorata des quantités, un raccord vaudrait autant qu'un appareil. Fixez le taux de "
                     "chaque ligne obtenue (ou passez par la page Transformation d'articles).")
                   .format(len(finis), ", ".join(l.get("item_code") or "?" for l in finis)))
    if valeur_consommee is not None and flt(valeur_consommee) > 0:
        for l in finis:
            if flt(l.get("basic_rate")) <= 0:
                out.append(_("{0} : produit obtenu valorisé 0 alors que {1} DT ont été consommés : la valeur "
                             "doit lui être affectée.").format(l.get("item_code"), round(flt(valeur_consommee), 3)))
    for l in lignes:
        taux, actuel = flt(l.get("basic_rate")), flt(valo_actuelle(l.get("item_code")))
        if actuel > 0 and taux > FACTEUR_MAX * actuel and (taux - actuel) * flt(l.get("qty") or 1) >= SEUIL_ABSOLU:
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
    items = doc.get("items") or []
    sorties = [{"item_code": i.item_code, "qty": i.qty, "basic_rate": i.basic_rate,
                "is_finished_item": i.get("is_finished_item"),
                "set_basic_rate_manually": i.get("set_basic_rate_manually")}
               for i in items if i.get("t_warehouse") and not i.get("s_warehouse")]
    consomme = sum(flt(i.basic_amount) or flt(i.basic_rate) * flt(i.qty)
                   for i in items if i.get("s_warehouse") and not i.get("t_warehouse"))
    anomalies = anomalies_repack(sorties, _valo, valeur_consommee=consomme)
    if anomalies:
        frappe.throw("<br>".join(anomalies), title=_("Reconditionnement à vérifier"))
