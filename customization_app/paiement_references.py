"""Lignes de référence en double sur une Écriture de Paiement : fusion avant validation.

Vu le 01/10/2026 en prod (ACC-PAY-2026-07487, chèque de 3 000 DT de BEN Sassi) : « Ligne #3 : entrée en
double dans les références Sales Invoice ACC-SINV-2026-01556 » dès qu'on tente de reclasser (Ajustement
Écarts Aramex) ou d'amender le paiement. ERPNext crée lui-même ces doublons : un paiement porte une ligne
par COMMANDE ; quand une facture (recréée par l'encaissement, ou mensuelle) tire ces avances, chaque ligne
est convertie vers la MÊME facture — sans passer par le contrôle `validate_duplicate_entry`, réservé aux
enregistrements complets. En dev, 30 paiements validés sont dans cet état (jusqu'à 11 lignes identiques),
et chacun refuse d'être amendé.

Fusionner ces lignes ne change rien aux écritures : les grands livres sont déjà passés, seule la table du
paiement est compactée (une ligne par pièce, montant alloué additionné). On ne touche QU'UN BROUILLON (nouveau
document, amendement, copie) — jamais un paiement validé, dont les lignes sont référencées par les avances
des factures (`Sales Invoice Advance.reference_row`).
"""
from __future__ import annotations

from frappe.utils import flt

PRECISION = 3


def fusionner_references(references):
    """La liste de lignes sans doublon : même (type, nom, échéance, demande) → une seule ligne, montants
    additionnés, ordre conservé. FONCTION PURE, testée telle quelle. Les lignes sont des objets à attributs
    (rangées Frappe ou SimpleNamespace)."""
    gardees, par_cle = [], {}
    for r in references or []:
        cle = (r.reference_doctype, r.reference_name, getattr(r, "payment_term", None) or None,
               getattr(r, "payment_request", None) or None)
        if cle in par_cle:
            tenue = par_cle[cle]
            tenue.allocated_amount = flt(flt(tenue.allocated_amount) + flt(r.allocated_amount), PRECISION)
            continue
        par_cle[cle] = r
        gardees.append(r)
    return gardees


def before_validate(doc, method=None):
    if doc.docstatus != 0:
        return
    avant = doc.get("references") or []
    apres = fusionner_references(avant)
    if len(apres) == len(avant):
        return
    for i, r in enumerate(apres, 1):
        r.idx = i
    doc.set("references", apres)
