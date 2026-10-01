"""Prime Versee — une tranche de prime remise à un vendeur, rattachée à un trimestre.

Saisie depuis la page « Rapport Prime » (customization_app.rapport_prime), qui la
déduit de la prime calculée. L'écriture de caisse est facultative : les primes
d'avant la page n'ont pas toujours une écriture identifiable.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class PrimeVersee(Document):
    def validate(self):
        if flt(self.montant) <= 0:
            frappe.throw(_("Le montant versé doit être positif."))
        if self.journal_entry:
            verifier_plafond_ecriture(self.journal_entry, self.montant, exclure=self.name)


def deja_rattache(journal_entry: str, exclure: str | None = None) -> float:
    """Somme des versements déjà rattachés à cette écriture (hors `exclure`)."""
    rows = frappe.get_all("Prime Versee", filters={"journal_entry": journal_entry,
                                                   "name": ["!=", exclure or ""]}, pluck="montant")
    return flt(sum(flt(m) for m in rows), 3)


def verifier_plafond_ecriture(journal_entry: str, montant, exclure: str | None = None):
    """Une écriture de caisse peut porter plusieurs versements (prime de deux
    vendeurs sur la même dépense, ou dépense qui contient plus que la prime) :
    la somme rattachée ne doit juste pas dépasser le montant de l'écriture."""
    total = flt(frappe.db.get_value("Journal Entry", journal_entry, "total_debit"))
    deja = deja_rattache(journal_entry, exclure)
    if flt(deja) + flt(montant) > total + 0.005:
        frappe.throw(_("L'écriture {0} fait {1} ; {2} y sont déjà rattachés, il ne reste que {3}.")
                     .format(journal_entry, frappe.format_value(total, "Currency"),
                             frappe.format_value(deja, "Currency"),
                             frappe.format_value(max(total - deja, 0), "Currency")))
