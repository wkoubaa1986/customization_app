"""Échéancier d'une facture créée depuis une commande déjà encaissée (29/10/2026).

À la caisse, l'échéancier d'une commande n'est pas un plan de paiement mais le RELEVÉ des règlements reçus :
une ligne par paiement (mode, n° de chèque), part de facture à 0 %, et le Server Script « Generation payement »
crée un Payment Entry par ligne. Ces paiements sont des AVANCES sur la commande.

Quand on crée la facture depuis cette commande, ERPNext recopie ces lignes telles quelles (part à 0 % : les
montants sont repris, pas recalculés), puis alloue les avances. Il exige alors que l'échéancier de la facture
égale « total − avances » : 381 de lignes contre 0 à payer → « Le montant total du paiement dans l'échéancier
doit être égal au Total Général ». Une commande avec un modèle de conditions (part à 100 %) ne pose pas ce
problème : ERPNext recalcule la part sur le reste à payer, d'où les factures existantes à une ligne de 0.

Correctif : avant le contrôle, ramener l'échéancier recopié au RESTE à payer, au prorata des lignes (tout à 0
quand la commande est entièrement réglée), en gardant mode et n° de chèque pour l'impression.
"""
from __future__ import annotations

from frappe.utils import flt, getdate, nowdate

TOLERANCE = 0.005


def aligner_echeancier(lignes: list[dict], grand_total: float, total_advance: float) -> bool:
    """Ramène `lignes` ({payment_amount, invoice_portion}) au reste à payer `grand_total − total_advance`,
    au prorata, SEULEMENT si l'échéancier a été recopié de la commande (sa somme vaut le total alors que des
    avances existent). PURE. Renvoie True si quelque chose a changé."""
    if not lignes or flt(total_advance) <= TOLERANCE:
        return False
    somme = sum(flt(l.get("payment_amount")) for l in lignes)
    reste = flt(grand_total) - flt(total_advance)
    if abs(somme - reste) <= TOLERANCE or abs(somme - flt(grand_total)) > TOLERANCE:
        return False
    reste = max(reste, 0.0)
    restant = reste
    for i, l in enumerate(lignes):
        part = flt(l.get("payment_amount")) / somme if somme else 0.0
        montant = round(reste * part, 3) if i < len(lignes) - 1 else round(restant, 3)
        restant = round(restant - montant, 3)
        l["payment_amount"] = montant
        l["invoice_portion"] = round(montant / flt(grand_total) * 100, 6) if flt(grand_total) else 0.0
    return True


def avances_allouees(doc) -> float:
    """Les avances que la facture va imputer. `total_advance` n'est calculé QUE pendant validate
    (calculate_taxes_and_totals), après before_validate : on additionne la table des avances nous-mêmes."""
    return flt(doc.get("total_advance")) or flt(sum(flt(a.get("allocated_amount")) for a in (doc.get("advances") or [])))


def aligner_echeancier_avances(doc, method=None):
    """Hook before_validate de Sales Invoice — AVANT le contrôle d'ERPNext (validate_payment_schedule_amount
    est appelé dans validate ; un hook « validate » passerait après l'erreur)."""
    lignes = doc.get("payment_schedule") or []
    avances = avances_allouees(doc) if lignes else 0.0
    if not lignes or not avances:
        return
    data = [{"payment_amount": r.payment_amount, "invoice_portion": r.invoice_portion} for r in lignes]
    if not aligner_echeancier(data, flt(doc.get("rounded_total") or doc.get("grand_total")), avances):
        return
    taux = flt(doc.get("conversion_rate") or 1)
    # Les lignes recopiées portent la DATE du règlement à la caisse, souvent antérieure à la facture : ERPNext
    # refuse une échéance avant la date de facture (« Due Date cannot be before Posting Date »). Une échéance
    # déjà réglée se date du jour de la facture.
    posting = getdate(doc.get("posting_date") or nowdate())
    for r, d in zip(lignes, data):
        r.payment_amount = d["payment_amount"]
        r.invoice_portion = d["invoice_portion"]
        r.base_payment_amount = flt(d["payment_amount"] * taux, r.precision("base_payment_amount"))
        if hasattr(r, "outstanding"):
            r.outstanding = d["payment_amount"]
        if r.get("due_date") and getdate(r.due_date) < posting:
            r.due_date = posting
    if doc.get("due_date") and getdate(doc.due_date) < posting:
        doc.due_date = posting
    # ERPNext refuse deux échéances à la même date : les lignes ramenées au même jour fusionnent (montants
    # additionnés, la première garde mode et n° de pièce). Une commande entièrement réglée finit avec UNE
    # ligne à 0, comme les factures créées depuis une commande à modèle de conditions.
    premiere = {}
    for r in list(doc.get("payment_schedule") or []):
        cle = str(r.get("due_date"))
        if cle in premiere:
            g = premiere[cle]
            g.payment_amount = flt(g.payment_amount) + flt(r.payment_amount)
            g.base_payment_amount = flt(g.base_payment_amount) + flt(r.base_payment_amount)
            g.invoice_portion = flt(g.invoice_portion) + flt(r.invoice_portion)
            if hasattr(g, "outstanding"):
                g.outstanding = g.payment_amount
            doc.remove(r)
        else:
            premiere[cle] = r
    for i, r in enumerate(doc.get("payment_schedule") or [], start=1):
        r.idx = i
