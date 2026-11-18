"""L'avoir d'une commande refusé pour un écart de calcul invisible (constaté le 18/11/2026).

À la validation d'une commande réglée en plusieurs fois, le dernier règlement est souvent l'avoir du client,
passé par une écriture comptable rattachée à la commande. ERPNext vérifie alors que l'avance déjà reçue plus
l'avoir ne dépasse pas le total (`JournalEntry.validate_orders`) — mais il additionne sans arrondir. Cas réel :
espèces 50 + chèque 40 + carte 116,133 = 206,133 d'avance ; 206,133 + 78 d'avoir donne, pour la machine,
284,13300000000004, « plus grand » que le total 284,133. La commande était refusée (« Advance paid against
Sales Order … cannot be greater than Grand Total ») alors que les montants tombent juste.

Ici le même contrôle est gardé, avec une tolérance d'un demi-millime : un dépassement réel (un millime ou
plus) reste refusé, à l'identique. Rien d'autre ne change dans l'écriture.

Appliqué au chargement de l'application (`customization_app/__init__.py`) ; idempotent.
"""
from __future__ import annotations

TOLERANCE = 0.0005  # un demi-millime : sous le plus petit montant saisissable (3 décimales)
MARQUE = "_origine_avant_avance_arrondie"


def totaux_toleres(totaux: dict, tolerance: float = TOLERANCE) -> dict:
    """Les montants rattachés à chaque commande, diminués de la tolérance, pour la seule comparaison avec le
    total de la commande. PURE."""
    return {nom: float(montant or 0) - tolerance for nom, montant in (totaux or {}).items()}


def appliquer() -> bool:
    """Enveloppe `JournalEntry.validate_orders`. -> True si la surcharge est en place."""
    from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry

    if getattr(JournalEntry, MARQUE, None):
        return True
    origine = JournalEntry.validate_orders

    def validate_orders(self):
        totaux = self.reference_totals
        self.reference_totals = totaux_toleres(totaux)
        try:
            return origine(self)
        finally:
            self.reference_totals = totaux

    setattr(JournalEntry, MARQUE, origine)
    JournalEntry.validate_orders = validate_orders
    return True
