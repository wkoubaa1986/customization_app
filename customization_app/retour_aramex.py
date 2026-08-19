"""
Retour d'un colis Aramex (statut « Returned ») — bouton « 📦 Retour reçu » de
l'écran Livraisons Aramex.

LE GESTE (décisions utilisateur 19/08) :
  1. la PHOTO du colis physiquement revenu est OBLIGATOIRE — le statut Aramex
     annonce un retour, la photo prouve qu'il est arrivé ;
  2. le stock RENTRE par un BL de retour (is_return, adossé au BL d'origine) —
     la voie native : dépôt d'origine, traçabilité article par article ;
  3. le paiement « Dette non payée » sur Livraison Aramex - A&S est SUPPRIMÉ
     (convention maison : annulé puis supprimé, jamais laissé en docstatus 2) —
     Aramex ne versera jamais ce colis, le compte ne doit plus l'attendre ;
  4. la commande est FERMÉE (Closed) et marquée « Retour colis »
     (custom_retour_colis) : pastille en vue liste + bandeau sur la fiche ;
  5. tout se fige sur la fiche Suivi Aramex + un commentaire sur la commande —
     la suppression du paiement ne fait perdre aucune trace.

BLOQUANT : une facture VALIDÉE liée à la commande arrête tout — l'avoir se
traite à la main (décision utilisateur : pas d'avoir automatique).

Retour TOTAL seulement pour l'instant ; le retour partiel (cas Baccari : un
article défectueux, Aramex verse le reste) reste un geste manuel.
"""

import base64

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from customization_app.livraison_aramex import (
    COMPTE_ARAMEX,
    DOCTYPE_SUIVI,
    _sans_accent,
    reference_aramex,
)

ROLES = ("System Manager", "Accounts Manager", "Accounts User",
         "Sales Manager", "Sales User")

# Le colis retourné revient PHYSIQUEMENT au magasin — quel que soit l'entrepôt
# de départ (souvent désactivé depuis : « Hall - A&S » a bloqué le premier
# essai). Le BL de retour entre donc toujours ici.
ENTREPOT_RETOUR = "Magasins - A&S"

# Les formes, dans le statut ou la dernière description, qui disent qu'un colis
# REVIENT — sous-ensemble « retour » des mots d'alerte du suivi.
_MOTS_RETOUR = ("return", "retour", "renvoy", "expediteur", "refus", "refused")


def _est_retour(reference):
    """Le suivi connu de ce bordereau annonce-t-il un retour ?"""
    doc = frappe.db.get_value(DOCTYPE_SUIVI, reference,
                              ["statut", "derniere_description"], as_dict=True)
    if not doc:
        return False
    texte = _sans_accent("%s %s" % (doc.statut or "", doc.derniere_description or ""))
    return any(mot in texte for mot in _MOTS_RETOUR)


def _pieces_du_paiement(pe_name):
    """La commande visée par le paiement Aramex, ses BL validés et ses factures."""
    refs = frappe.get_all("Payment Entry Reference",
                          filters={"parent": pe_name},
                          fields=["reference_doctype", "reference_name"])
    so = next((r.reference_name for r in refs
               if r.reference_doctype == "Sales Order"), None)
    sis = [r.reference_name for r in refs if r.reference_doctype == "Sales Invoice"]
    if not so and sis:
        so = frappe.db.get_value("Sales Invoice Item",
                                 {"parent": sis[0], "sales_order": ("!=", "")},
                                 "sales_order")
    if not so:
        frappe.throw(_("Le paiement {0} ne référence aucune commande.").format(pe_name))
    dns = [r.parent for r in frappe.get_all(
        "Delivery Note Item", filters={"against_sales_order": so},
        fields=["parent"], distinct=True)]
    dns_valides = [d for d in frappe.get_all(
        "Delivery Note", filters={"name": ("in", dns), "docstatus": 1},
        pluck="name")] if dns else []
    sis_so = frappe.db.sql(
        """SELECT DISTINCT si.name FROM `tabSales Invoice` si
           INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
           WHERE sii.sales_order = %s AND si.docstatus = 1""", (so,))
    factures_validees = sorted({r[0] for r in sis_so} | {
        s for s in sis
        if frappe.db.get_value("Sales Invoice", s, "docstatus") == 1})
    return so, dns_valides, factures_validees


def _valider_bl_retour(doc):
    """Soumet le BL de retour comme le contrôle de clôture soumet un BL : état
    de workflow « Approved » puis soumission sous in_import."""
    if doc.get("workflow_state") is not None:
        doc.db_set("workflow_state", "Approved", update_modified=False)
        doc.reload()
    frappe.flags.in_import = True
    try:
        doc.submit()
    finally:
        frappe.flags.in_import = False


@frappe.whitelist()
def constater_retour(payment_entry, photo, photo_nom=None, note=None):
    """Constate le retour physique d'un colis Aramex : photo, BL(s) de retour,
    suppression du paiement dette, commande fermée et marquée."""
    frappe.only_for(ROLES)

    pe = frappe.get_doc("Payment Entry", payment_entry)
    if pe.docstatus != 1:
        frappe.throw(_("Le paiement {0} n'est pas validé.").format(payment_entry))
    if pe.paid_to != COMPTE_ARAMEX:
        frappe.throw(_("Le paiement {0} n'est pas sur le compte {1}.")
                     .format(payment_entry, COMPTE_ARAMEX))

    reference = reference_aramex(pe.reference_no)
    if not reference or not _est_retour(reference):
        frappe.throw(_("Le suivi Aramex de ce colis n'annonce pas un retour — "
                       "rafraîchissez le suivi avant de constater le retour."))
    if frappe.db.get_value(DOCTYPE_SUIVI, reference, "retour_recu_le"):
        frappe.throw(_("Le retour du bordereau {0} est déjà constaté.").format(reference))

    if not (photo or "").strip():
        frappe.throw(_("La photo du colis retourné est obligatoire."))

    so, dns, factures = _pieces_du_paiement(pe.name)
    if factures:
        # Décision utilisateur : pas d'avoir automatique — on s'arrête net.
        frappe.throw(_("Facture validée liée ({0}) : à traiter manuellement "
                       "(avoir) avant de constater le retour.")
                     .format(", ".join(factures)))
    if not dns:
        frappe.throw(_("Aucun bon de livraison validé sur {0} — le stock n'est "
                       "jamais sorti, rien à ré-entrer. Traitez le paiement à la main.")
                     .format(so))
    deja = frappe.get_all("Delivery Note",
                          filters={"is_return": 1, "return_against": ("in", dns),
                                   "docstatus": ("<", 2)}, pluck="name")
    if deja:
        frappe.throw(_("Un BL de retour existe déjà : {0}.").format(", ".join(deja)))

    # ── 1. Le stock rentre : un BL de retour TOTAL par BL d'origine ──────────
    from erpnext.controllers.sales_and_purchase_return import make_return_doc
    bls_retour = []
    for dn in dns:
        rd = make_return_doc("Delivery Note", dn)
        rd.set_warehouse = ENTREPOT_RETOUR
        for item in rd.items:
            item.warehouse = ENTREPOT_RETOUR
        rd.insert(ignore_permissions=True)
        _valider_bl_retour(rd)
        bls_retour.append(rd.name)

    # ── 2. La photo, preuve du retour, attachée au Suivi et au 1er BL retour ─
    from frappe.utils.file_manager import save_file
    contenu = base64.b64decode(photo.split(",", 1)[-1])
    fichier = save_file(photo_nom or "retour-%s.jpg" % reference, contenu,
                        DOCTYPE_SUIVI, reference, is_private=1)
    save_file(photo_nom or "retour-%s.jpg" % reference, contenu,
              "Delivery Note", bls_retour[0], is_private=1)

    # ── 3. Le paiement dette disparaît (convention maison), trace d'abord ────
    trace = ("📦 Retour de colis Aramex constaté le %s par %s — bordereau %s. "
             "BL de retour : %s. Paiement dette supprimé : %s (%s DT, réf « %s »). "
             "Photo attachée au Suivi Aramex.%s") % (
        str(now_datetime())[:16], frappe.session.user, reference,
        ", ".join(bls_retour), pe.name, flt(pe.paid_amount, 3), pe.reference_no or "",
        (" Note : %s" % note.strip()) if (note or "").strip() else "")
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info",
        "reference_doctype": "Sales Order", "reference_name": so,
        "content": trace,
    }).insert(ignore_permissions=True)

    montant = flt(pe.paid_amount, 3)
    pe.flags.ignore_permissions = True
    pe.cancel()
    pe.delete(ignore_permissions=True)

    # ── 4. La commande se ferme et porte la marque « Retour colis » ──────────
    so_doc = frappe.get_doc("Sales Order", so)
    if so_doc.docstatus == 1 and so_doc.status != "Closed":
        so_doc.update_status("Closed")
    frappe.db.set_value("Sales Order", so, "custom_retour_colis", 1,
                        update_modified=False)

    # ── 5. Tout se fige sur la fiche Suivi Aramex ────────────────────────────
    frappe.db.set_value(DOCTYPE_SUIVI, reference, {
        "retour_recu_le": now_datetime(),
        "retour_par": frappe.session.user,
        "retour_bl": ", ".join(bls_retour),
        "retour_pe": pe.name,
        "retour_montant": montant,
        "retour_note": (note or "").strip(),
    }, update_modified=False)

    frappe.db.commit()
    return {"commande": so, "bls_retour": bls_retour, "pe_supprimee": pe.name,
            "montant": montant, "photo": fichier.file_url}
