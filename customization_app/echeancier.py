"""Échéancier (Payment Schedule) : le moyen de paiement n'est obligatoire QUE sur une vente (31/10/2026).

Le champ est partagé par les commandes et factures de vente ET d'achat. Le rendre obligatoire partout (ancien
Property Setter `reqd`) faisait échouer chaque facture d'achat dont l'échéancier est généré par ERPNext depuis
la date d'échéance, sans moyen de paiement (« Calendrier de paiement Ligne #1 : Valeur manquante »). Désormais :
côté écran, `mandatory_depends_on` sur le parent (commande / facture de vente) ; côté serveur, ce contrôle, branché
sur le validate des deux documents de vente — les scripts « Generation payement » en dépendent.
"""
import frappe
from frappe import _

DOCUMENTS_DE_VENTE = ("Sales Order", "Sales Invoice")


def lignes_sans_mode(doctype: str, echeancier) -> list:
    """Numéros (idx) des lignes d'échéancier sans moyen de paiement, pour un document de vente ; [] sinon. Pure."""
    if doctype not in DOCUMENTS_DE_VENTE:
        return []
    return [r.get("idx") if isinstance(r, dict) else r.idx for r in (echeancier or [])
            if not (r.get("mode_of_payment") if isinstance(r, dict) else r.mode_of_payment)]


def exiger_mode_de_paiement(doc, method=None):
    manquantes = lignes_sans_mode(doc.doctype, doc.get("payment_schedule"))
    if manquantes:
        frappe.throw(_("Échéancier : moyen de paiement obligatoire sur une vente (ligne(s) {0}).")
                     .format(", ".join(str(i) for i in manquantes)))
