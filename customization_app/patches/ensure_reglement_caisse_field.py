"""
Champ `custom_reglement_caisse` sur Payment Entry.

Posé par le règlement des factures d'achat depuis la caisse journalière
(`caisse_depenses.payer_factures`). Le rapport de caisse le relit pour faire
entrer ces paiements dans les dépenses du jour — leur part espèces pèse sur le
solde théorique de la clôture. Sans ce drapeau, il faudrait deviner quels
règlements fournisseurs sont sortis de la caisse, et on réécrirait des années
d'historique.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from customization_app.caisse_depenses import CHAMP_REGLEMENT

#: La déclaration du champ — une seule source, relue par les tests.
#:
#: ⚠️ `no_copy` : DUPLIQUER dans ERPNext un paiement né de la caisse ne doit PAS
#: transmettre le drapeau. Le champ étant en lecture seule, personne ne pourrait
#: le décocher sur la copie, et le rapport compterait comme sortie de caisse un
#: règlement saisi ailleurs — en espèces, il ferait baisser le solde théorique de
#: la clôture sans qu'aucun billet ne quitte le tiroir.
#: L'AMENDEMENT, lui, garde le drapeau : Frappe n'écarte les champs `no_copy` que
#: pour une duplication ordinaire (`frappe.model.copy_doc(doc, from_amend)`,
#: apps/frappe/frappe/public/js/frappe/model/create_new.js). C'est exactement ce
#: qu'il faut — corriger un règlement RÉELLEMENT issu de la caisse doit donner un
#: paiement qui reste, lui aussi, un règlement de caisse.
CHAMP = {
    "fieldname": CHAMP_REGLEMENT,
    "fieldtype": "Check",
    "label": "Règlement saisi en caisse journalière",
    "default": "0",
    "read_only": 1,
    "no_copy": 1,
    "print_hide": 1,
    "insert_after": "custom_exclu_caisse",
}


def execute():
    create_custom_fields({"Payment Entry": [CHAMP]}, ignore_validate=True)
    frappe.db.commit()
