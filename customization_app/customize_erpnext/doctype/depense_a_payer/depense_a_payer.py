"""Dépense avec facture PAS ENCORE PAYÉE, saisie en caisse.

La charge (et la TVA) est comptabilisée immédiatement CONTRE LE COMPTE DE
DÉCOUVERT — la dette est visible tant que la fiche est « À payer ». Le bouton
« Payer » de la page Caisse journalière génère l'écriture de règlement
(découvert → caisse/banque) au jour du paiement : c'est ce jour-là que la
dépense entre dans le rapport de caisse. Voir `caisse_depenses.solder_depense`.
"""

from frappe.model.document import Document


class DepenseAPayer(Document):
    pass
