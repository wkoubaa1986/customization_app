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


#: Posés le temps d'un traitement qui réécrit l'échéancier en plusieurs enregistrements (frappe.flags : la requête
#: ou la tâche en cours seulement, jamais en base).
DRAPEAU_OUTIL = "outil_encaissement"            # un « Encaissement Paiement » est en cours de validation
DRAPEAU_DEUX_TEMPS = "echeancier_en_deux_temps"  # régularisation d'une livraison partielle (livraison_partielle)


def marquer_outil_encaissement(doc, method=None):
    """Un « Encaissement Paiement » se valide : son Server Script « Traitement des encaissement » va réécrire
    l'échéancier des commandes qu'il encaisse (une ligne « Virement » à la place de la dette Aramex, les chèques
    versés…), en plusieurs enregistrements. Le verrou se tait le temps de CE traitement."""
    frappe.flags[DRAPEAU_OUTIL] = doc.name


def verrou_exempte(entretien: bool, outil_en_cours: bool, deux_temps: bool) -> bool:
    """Le verrou se tait-il pour cet enregistrement ? Pure.

    ⚠️ PLUS SUR LA MARQUE « Outil de paiement » DE LA COMMANDE (27/11/2026). L'ancien script s'en servait, mais
    cette marque, posée par « Traitement des encaissement », n'est JAMAIS remise à zéro en base (le script qui
    devait le faire l'écrit après l'enregistrement) : 103 commandes sur 790 la portaient à vie et échappaient au
    verrou pour toujours (SAL-ORD-2026-04558 : total porté de 9 654,500 à 15 154,500 sans toucher l'échéancier).
    L'exemption tient maintenant au traitement EN COURS, pas à la commande."""
    return bool(entretien or outil_en_cours or deux_temps)


def rien_n_a_bouge(avant, doc) -> bool:
    """Cet enregistrement ne touche ni au total de la commande ni à la somme de son échéancier : un écart qui
    existait déjà n'est pas de son fait. Bloquer ici arrêterait un traitement sans rapport (une retenue à la
    source, un avoir) sur une commande qu'un autre a laissée en écart — sans rien réparer."""
    if not avant:
        return False
    somme = lambda d: round(sum(frappe.utils.flt(r.payment_amount) for r in (d.get("payment_schedule") or [])), 3)  # noqa: E731
    total = lambda d: (round(frappe.utils.flt(d.get("grand_total")), 3), round(frappe.utils.flt(d.get("rounded_total")), 3))  # noqa: E731
    return somme(avant) == somme(doc) and total(avant) == total(doc)


def verifier_total_echeancier(doc, method=None):
    """LE VERROU « total des paiements = total de la commande » sur une commande DÉJÀ VALIDÉE (27/11/2026).

    À la validation d'un brouillon, ERPNext le contrôle lui-même. Sur une commande validée dont on modifie
    l'échéancier, le contrôle vivait dans le Server Script « generer un echeancier de maintenace », éteint le
    07/10/2026 pour une autre raison (il cherchait d'anciens groupes d'articles) : depuis, seul le formulaire le
    vérifiait, et tout ce qui n'y passe pas (outil, script, traitement) pouvait enregistrer un échéancier de
    203,200 pour une commande de 236,615. Même tolérance que le script ; exemptions : `verrou_exempte`."""
    if doc.doctype != "Sales Order" or doc.get("status") in STATUTS_INACTIFS:
        return
    if verrou_exempte(bool(frappe.utils.cint(doc.get("custom_maintenace_origine"))),
                      bool(frappe.flags.get(DRAPEAU_OUTIL)), bool(frappe.flags.get(DRAPEAU_DEUX_TEMPS))):
        return
    ecart = ecart_echeancier(doc.get("payment_schedule"), doc.get("rounded_total"), doc.get("grand_total"),
                             bool(frappe.utils.cint(doc.get("disable_rounded_total"))))
    if ecart is not None and rien_n_a_bouge(doc.get_doc_before_save(), doc):
        return
    if ecart is not None:
        somme = sum(frappe.utils.flt(r.payment_amount) for r in doc.payment_schedule)
        frappe.throw(_("Le total des paiements ({0}) est différent du total de la commande ({1}).")
                     .format(frappe.utils.fmt_money(somme, 3), frappe.utils.fmt_money(somme - ecart, 3)))


def exiger_mode_de_paiement(doc, method=None):
    manquantes = lignes_sans_mode(doc.doctype, doc.get("payment_schedule"))
    if manquantes:
        frappe.throw(_("Échéancier : moyen de paiement obligatoire sur une vente (ligne(s) {0}).")
                     .format(", ".join(str(i) for i in manquantes)))
