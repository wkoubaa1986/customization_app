"""Collecte de la caisse d'un sous-traitant (décision utilisateur 31/10/2026).

À la vente, ses espèces, chèques et traites sont encaissés sur SON compte (« <Nom> - A&S », cf. script
« Generation payement ») ; ses dettes naissent directement sur Dettes - A&S. Quand le responsable de collecte
valide sa clôture de période (caisse_collecte.finaliser), l'argent change de mains : chaque paiement de son
compte est EFFACÉ (annulé puis supprimé, convention maison) et RECRÉÉ à la date de la réception, par le
collecteur (il entre donc dans SA caisse du jour), sur nos comptes habituels — espèces → Espèces - A&S,
chèque → Chèques - A&S, traite → Traite Bancaire - A&S. Le nouveau paiement garde la commande, le client,
le n° de pièce, la banque et les marques du script de régénération (uid / empreinte de l'échéance).
Le transfert se fait APRÈS le PDF de la clôture : l'instantané montre ce qu'il a remis.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from customization_app.sous_traitant import CHAMP_COMPTE, CHAMP_FLAG

COMPTES_CIBLES = {"Espèces": "Espèces - A&S", "Chèque": "Chèques - A&S", "Traite bancaire LC": "Traite Bancaire - A&S"}


def compte_cible(mode: str):
    """Le compte de caisse de la société qui reçoit un mode remis par le sous-traitant. Fonction pure."""
    return COMPTES_CIBLES.get(mode or "")


def compte_du_sous_traitant(caisse: str):
    if not caisse or not frappe.db.has_column("Employee", CHAMP_FLAG):
        return None
    return frappe.db.get_value("Employee", {"employee_name": caisse, CHAMP_FLAG: 1}, CHAMP_COMPTE)


def encaissements_a_transferer(compte: str, jusqu_au) -> list:
    """Les paiements reçus encore sur son compte, datés au plus tard du jour de la clôture (ceux d'après
    appartiennent à sa période suivante)."""
    return frappe.get_all("Payment Entry",
                          filters={"docstatus": 1, "payment_type": "Receive", "paid_to": compte,
                                   "posting_date": ["<=", getdate(jusqu_au)]},
                          fields=["name", "mode_of_payment", "paid_amount", "party_name", "reference_no"],
                          order_by="posting_date, creation")


def transferer(cloture) -> list:
    """Efface les paiements du compte du sous-traitant et les recrée, à la date de réception, dans la caisse
    du collecteur, sur nos comptes. Renvoie [{ancien, nouveau, mode, montant}]."""
    compte = compte_du_sous_traitant(cloture.caisse)
    if not compte:
        return []
    collecteur = cloture.get("collecte_par") or frappe.session.user
    jour = getdate(cloture.get("collecte_le")) if cloture.get("collecte_le") else getdate(nowdate())
    faits = []
    for ligne in encaissements_a_transferer(compte, cloture.date_cloture):
        cible = compte_cible(ligne.mode_of_payment)
        if not cible:
            frappe.throw(_("Paiement {0} : mode « {1} » inattendu sur le compte {2}, transfert impossible.")
                         .format(ligne.name, ligne.mode_of_payment, compte))
        ancien = frappe.get_doc("Payment Entry", ligne.name)
        nouveau = frappe.copy_doc(ancien)
        ancien.flags.ignore_links = True
        ancien.flags.ignore_permissions = True
        ancien.cancel()
        frappe.delete_doc("Payment Entry", ancien.name, ignore_permissions=True, force=True)

        nouveau.update({"paid_to": cible, "paid_to_account_currency": "TND", "posting_date": jour,
                        "reference_date": ancien.reference_date or jour,
                        "custom_remise_en_banque": None, "custom_exclu_caisse": 0, "remarks": None})
        nouveau.flags.ignore_permissions = True
        nouveau.insert()
        nouveau.submit()
        # Le paiement est celui du COLLECTEUR : c'est dans sa caisse du jour qu'il entre.
        frappe.db.set_value("Payment Entry", nouveau.name, "owner", collecteur, update_modified=False)
        frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": "Payment Entry",
                        "reference_name": nouveau.name,
                        "content": _("Remis par le sous-traitant {0} (clôture {1} du {2}) : remplace {3} "
                                     "encaissé sur {4}.").format(cloture.caisse, cloture.name,
                                                                frappe.format_value(cloture.date_cloture, {"fieldtype": "Date"}),
                                                                ancien.name, compte)}).insert(ignore_permissions=True)
        faits.append({"ancien": ancien.name, "nouveau": nouveau.name, "mode": ancien.mode_of_payment,
                      "montant": ancien.paid_amount, "compte": cible})
    if faits:
        lignes = "".join("<li>%s → <a href=\"/app/payment-entry/%s\">%s</a> : %s %s DT sur %s</li>" % (
            f["ancien"], f["nouveau"], f["nouveau"], f["mode"], frappe.format_value(f["montant"], {"fieldtype": "Float"}),
            f["compte"]) for f in faits)
        frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": "Cloture Caisse",
                        "reference_name": cloture.name,
                        "content": _("💱 Encaissements transférés du compte {0} vers la caisse de {1} ({2}) :<ul>{3}</ul>")
                        .format(compte, collecteur, frappe.format_value(jour, {"fieldtype": "Date"}), lignes)}
                       ).insert(ignore_permissions=True)
    return faits
