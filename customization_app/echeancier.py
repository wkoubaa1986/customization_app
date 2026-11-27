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


#: Écart admis entre l'échéancier et le total : celui que l'ancien script admettait (les arrondis au dinar).
TOLERANCE_TOTAL = 0.1
STATUTS_INACTIFS = ("Draft", "Cancelled", "Closed", "On Hold")


def ecart_echeancier(echeancier, total_arrondi, total, sans_arrondi: bool = False) -> float | None:
    """Total des paiements − total de la commande, quand il dépasse la tolérance ; None s'ils concordent. Pure.
    Le total de référence est le total ARRONDI (au dinar), sauf si l'arrondi est désactivé sur la commande."""
    somme = sum(float((r.get("payment_amount") if isinstance(r, dict) else r.payment_amount) or 0) for r in (echeancier or []))
    reference = float(total or 0) if sans_arrondi or not float(total_arrondi or 0) else float(total_arrondi)
    ecart = round(round(somme, 3) - round(reference, 3), 3)
    return ecart if abs(ecart) >= TOLERANCE_TOTAL else None


def verifier_total_echeancier(doc, method=None):
    """LE VERROU « total des paiements = total de la commande » sur une commande DÉJÀ VALIDÉE (27/11/2026).

    À la validation d'un brouillon, ERPNext le contrôle lui-même. Sur une commande validée dont on modifie
    l'échéancier, le contrôle vivait dans le Server Script « generer un echeancier de maintenace », éteint le
    07/10/2026 pour une autre raison (il cherchait d'anciens groupes d'articles) : depuis, seul le formulaire le
    vérifiait, et tout ce qui n'y passe pas (outil, script, traitement) pouvait enregistrer un échéancier de
    203,200 pour une commande de 236,615. Mêmes conditions que le script : commande active, et pas pendant
    l'outil de paiement ni une commande née d'un entretien (ils réécrivent l'échéancier en deux temps)."""
    if doc.doctype != "Sales Order" or doc.get("status") in STATUTS_INACTIFS:
        return
    if frappe.utils.cint(doc.get("custom_maintenace_origine")) or frappe.utils.cint(doc.get("custom_outil_de_paiement")):
        return
    ecart = ecart_echeancier(doc.get("payment_schedule"), doc.get("rounded_total"), doc.get("grand_total"),
                             bool(frappe.utils.cint(doc.get("disable_rounded_total"))))
    if ecart is not None:
        somme = sum(frappe.utils.flt(r.payment_amount) for r in doc.payment_schedule)
        frappe.throw(_("Le total des paiements ({0}) est différent du total de la commande ({1}).")
                     .format(frappe.utils.fmt_money(somme, 3), frappe.utils.fmt_money(somme - ecart, 3)))


def exiger_mode_de_paiement(doc, method=None):
    manquantes = lignes_sans_mode(doc.doctype, doc.get("payment_schedule"))
    if manquantes:
        frappe.throw(_("Échéancier : moyen de paiement obligatoire sur une vente (ligne(s) {0}).")
                     .format(", ".join(str(i) for i in manquantes)))
