"""Régularisation d'un chèque impayé sans modifier le paiement d'origine.

Le paiement basculé sur « Chèques sans provision - A&S » ne bouge JAMAIS : c'est lui que le
mouvement bancaire « Cheque repris » identifie, lui qui garde la commande payée, lui que BRS
compte pour expliquer le trou de la remise d'origine. La régularisation est un TRANSFERT INTERNE
depuis ce compte, dont le champ `custom_impaye_origine` désigne la pièce d'origine (ERPNext
efface `party` sur un transfert interne : Relance, rapport de caisse et BRS retrouvent le client
par ce lien).

Trois façons de régulariser (demande utilisateur 22/09/2026) :
  - Espèces : transfert vers « Espèces - A&S » (entrée de caisse) ;
  - Redépôt du même chèque : transfert vers « Chèques - A&S » (portefeuille), même n° et même
    banque que l'impayé → il ressort dans les chèques à remettre, part sur un bordereau, et le
    Server Script « Traitement des encaissement » le passe sur Zitouna à la remise ;
  - Nouveau chèque : idem avec le n° / la banque du chèque de remplacement.
Si le chèque redéposé ou de remplacement revient encore impayé, le transfert est simplement
annulé (par le bordereau « Sans provision » ou par le flux BRS des impayés) : l'argent est de
nouveau sur les impayés, porté par la pièce d'origine — la traçabilité est complète.

`reference_no` : pour les espèces, le nom de la pièce d'origine (convention historique) ; pour un
chèque, « <n°>-<banque> / … » — la forme que BRS lit pour apparier une remise.
"""
import math
import re

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from customization_app.caisse_encaissement_dettes import ROLES

IMPAYES = "Chèques sans provision - A&S"
ESPECES = "Espèces - A&S"
PORTEFEUILLE = "Chèques - A&S"
MODE_ESPECES = "Espèces"
MODE_REDEPOT = "Redépôt du même chèque"
MODE_NOUVEAU = "Nouveau chèque"
COMPTE_PAR_MODE = {MODE_ESPECES: ESPECES, MODE_REDEPOT: PORTEFEUILLE, MODE_NOUVEAU: PORTEFEUILLE}
MOYEN_PAR_MODE = {MODE_ESPECES: "Espèces", MODE_REDEPOT: "Chèque", MODE_NOUVEAU: "Chèque"}


# ------------------------------------------------------------ règles pures


def numero_et_banque(reference_no):
    """« 0001170-BIAT / BR:90028502 / Impayé FT… » -> ("0001170", "BIAT")."""
    tete = (reference_no or "").split("/")[0].strip()
    num, _sep, banque = tete.partition("-")
    return num.strip(), banque.strip()


def reference_transfert(mode, piece, origine_reference, n_cheque=None, banque=None):
    """Le `reference_no` du transfert selon le mode. Pour un chèque, le n° ouvre le libellé
    (c'est ce que BRS et le bordereau lisent) et la pièce d'origine est citée après."""
    if mode == MODE_ESPECES:
        return piece
    if mode == MODE_REDEPOT:
        num, bq = numero_et_banque(origine_reference)
        return "%s / Redépôt de %s" % ("%s-%s" % (num, bq) if bq else num, piece)
    return "%s-%s / Remplace %s" % ((n_cheque or "").strip(), (banque or "").strip(), piece)


def motif_refus_mode(mode, origine_reference, n_cheque=None, banque=None):
    """None si les paramètres du mode sont complets, sinon la phrase à afficher."""
    if mode not in COMPTE_PAR_MODE:
        return "Mode de régularisation inconnu."
    if mode == MODE_REDEPOT and not numero_et_banque(origine_reference)[0]:
        return "La pièce d'origine ne porte pas de numéro de chèque : choisissez « Nouveau chèque »."
    if mode == MODE_NOUVEAU:
        if not re.fullmatch(r"\d{4,20}", (n_cheque or "").strip()):
            return "Le numéro du nouveau chèque est obligatoire (4 à 20 chiffres)."
        if not (banque or "").strip():
            return "La banque du nouveau chèque est obligatoire."
    return None


def _montant(montant, restant):
    try:
        valeur = float(montant)
    except (TypeError, ValueError):
        valeur = 0
    if not math.isfinite(valeur) or round(valeur, 3) <= 0:
        frappe.throw(_("Saisissez un montant positif."))
    valeur = round(valeur, 3)
    if valeur > round(restant, 3):
        frappe.throw(_("Le montant dépasse le solde restant. Actualisez la liste."))
    return valeur


# ------------------------------------------------------------ lecture

# Un transfert régularise une pièce s'il la désigne par `custom_impaye_origine`, ou (forme
# historique des espèces) si son `reference_no` est exactement le nom de la pièce.
_LIEN = "(tr.custom_impaye_origine = pe.name OR (tr.paid_to = %(especes)s AND tr.reference_no = pe.name))"


def _soldes(client, piece=None):
    return frappe.db.sql(
        """SELECT pe.name, pe.company, pe.party AS customer, pe.party_name,
                  pe.posting_date, pe.reference_no, pe.paid_amount,
                  ROUND(SUM(gl.debit - gl.credit) - COALESCE(reg.montant, 0), 3) AS restant
           FROM `tabPayment Entry` pe
           JOIN `tabGL Entry` gl ON gl.voucher_type = 'Payment Entry'
                AND gl.voucher_no = pe.name AND gl.is_cancelled = 0
                AND gl.account = %(impayes)s
           LEFT JOIN (
               SELECT COALESCE(tr.custom_impaye_origine, tr.reference_no) AS origine,
                      SUM(g.credit - g.debit) AS montant
               FROM `tabPayment Entry` tr
               JOIN `tabGL Entry` g ON g.voucher_type = 'Payment Entry'
                    AND g.voucher_no = tr.name AND g.is_cancelled = 0
                    AND g.account = %(impayes)s
               WHERE tr.docstatus = 1 AND tr.payment_type = 'Internal Transfer'
                 AND tr.paid_from = %(impayes)s
               GROUP BY COALESCE(tr.custom_impaye_origine, tr.reference_no)
           ) reg ON reg.origine = pe.name
           WHERE pe.docstatus = 1 AND pe.party_type = 'Customer' AND pe.party = %(client)s
             AND (%(piece)s IS NULL OR pe.name = %(piece)s)
           GROUP BY pe.name
           HAVING restant > 0
           ORDER BY pe.posting_date, pe.name""",
        dict(client=client, piece=piece, impayes=IMPAYES, especes=ESPECES), as_dict=True)


def _restant_verrouille(piece):
    # Lectures courantes : même sous REPEATABLE READ, une requête ayant attendu
    # le verrou doit voir les règlements validés entre-temps, pas son snapshot.
    origine = frappe.db.sql(
        """SELECT debit, credit FROM `tabGL Entry`
           WHERE voucher_type = 'Payment Entry' AND voucher_no = %s
             AND account = %s AND is_cancelled = 0 FOR UPDATE""", (piece, IMPAYES))
    reglements = frappe.db.sql(
        """SELECT gl.debit, gl.credit FROM `tabGL Entry` gl
           JOIN `tabPayment Entry` tr ON gl.voucher_no = tr.name
           WHERE gl.voucher_type = 'Payment Entry' AND gl.account = %(impayes)s
             AND gl.is_cancelled = 0 AND tr.docstatus = 1
             AND tr.payment_type = 'Internal Transfer' AND tr.paid_from = %(impayes)s
             AND (tr.custom_impaye_origine = %(piece)s
                  OR (tr.paid_to = %(especes)s AND tr.reference_no = %(piece)s)) FOR UPDATE""",
        dict(impayes=IMPAYES, especes=ESPECES, piece=piece))
    return round(sum(flt(d) - flt(c) for d, c in origine + reglements), 3)


@frappe.whitelist()
def pieces(client):
    frappe.only_for(ROLES)
    frappe.get_doc("Customer", client).check_permission("read")
    out = []
    for p in _soldes(client):
        num, bq = numero_et_banque(p.reference_no)
        p["numero"], p["banque"] = num, bq
        out.append(p)
    return out


@frappe.whitelist()
def modes():
    return [MODE_ESPECES, MODE_REDEPOT, MODE_NOUVEAU]


# ------------------------------------------------------------ écriture


def _commenter(doctype, name, texte):
    try:
        frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": doctype,
                        "reference_name": name, "content": texte}).insert(ignore_permissions=True)
    except Exception:
        pass


@frappe.whitelist()
def encaisser(client, piece, montant, mode=MODE_ESPECES, n_cheque=None, banque=None, date_cheque=None, photo=None):
    frappe.only_for(ROLES)
    # Sérialiser deux validations de la même pièce avant de relire son solde.
    frappe.db.sql("SELECT name FROM `tabPayment Entry` WHERE name = %s FOR UPDATE", piece)
    origine = frappe.get_doc("Payment Entry", piece)
    origine.check_permission("read")
    if origine.docstatus != 1 or origine.party_type != "Customer" or origine.party != client:
        frappe.throw(_("La pièce ne correspond pas à un impayé validé de ce client."))
    refus = motif_refus_mode(mode, origine.reference_no, n_cheque, banque)
    if refus:
        frappe.throw(_(refus))
    restant = _restant_verrouille(piece)
    if restant <= 0:
        frappe.throw(_("Cette pièce n'a plus de solde à encaisser. Actualisez la liste."))
    valeur = _montant(montant, restant)
    compte = COMPTE_PAR_MODE[mode]
    devise = frappe.get_cached_value("Company", origine.company, "default_currency")
    for nom in (IMPAYES, compte):
        meta = frappe.get_doc("Account", nom)
        if meta.company != origine.company or meta.account_currency != devise or meta.is_group or meta.disabled:
            frappe.throw(_("Le compte {0} doit être actif, dans la société et sa devise.").format(nom))
    reference = reference_transfert(mode, piece, origine.reference_no, n_cheque, banque)
    jour = nowdate()
    libelle = {
        MODE_ESPECES: _("Règlement en espèces du chèque impayé {0} — client {1}"),
        MODE_REDEPOT: _("Redépôt du chèque impayé {0} — client {1}"),
        MODE_NOUVEAU: _("Chèque de remplacement de l'impayé {0} — client {1}"),
    }[mode].format(piece, origine.party_name or client)
    paiement = frappe.get_doc({
        "doctype": "Payment Entry", "payment_type": "Internal Transfer",
        "company": origine.company, "posting_date": jour,
        "paid_from": IMPAYES, "paid_to": compte,
        "paid_amount": valeur, "received_amount": valeur,
        "source_exchange_rate": 1, "target_exchange_rate": 1,
        "mode_of_payment": MOYEN_PAR_MODE[mode], "reference_no": reference,
        "reference_date": date_cheque or jour, "custom_remarks": 1,
        "custom_impaye_origine": piece,
        "remarks": libelle,
    })
    paiement.insert()
    paiement.submit()
    if photo:
        try:
            from frappe.utils.file_manager import save_file

            f = frappe.get_doc("File", {"file_url": photo})
            save_file(f.file_name, f.get_content(), "Payment Entry", paiement.name, is_private=1)
        except Exception:
            pass
    reste = round(restant - valeur, 3)
    texte = "💵 %s : %s → %s (%s)%s" % (
        libelle, frappe.format_value(valeur, {"fieldtype": "Currency"}), paiement.name, reference,
        (" — " + _("reste {0}").format(frappe.format_value(reste, {"fieldtype": "Currency"}))) if reste > 0.0005 else "")
    _commenter("Payment Entry", piece, texte)
    for r in origine.references:
        if r.reference_doctype in ("Sales Order", "Sales Invoice"):
            _commenter(r.reference_doctype, r.reference_name, texte)
    return {"name": paiement.name, "restant": reste, "reference": reference, "compte": compte}
