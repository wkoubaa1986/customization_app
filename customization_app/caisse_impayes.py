"""Régularisation d'un chèque impayé sans modifier le paiement d'origine.

Le paiement basculé sur « Chèques sans provision - A&S » ne bouge JAMAIS : c'est lui que le
mouvement bancaire « Cheque repris » identifie, lui qui garde la commande payée, lui que BRS
compte pour expliquer le trou de la remise d'origine. La régularisation est un TRANSFERT INTERNE
depuis ce compte, dont le champ `custom_impaye_origine` désigne la pièce d'origine (ERPNext
efface `party` sur un transfert interne : Relance, rapport de caisse et BRS retrouvent le client
par ce lien).

Six façons de régulariser, COMBINABLES en plusieurs lignes (demande utilisateur 22/09/2026 :
« ou plusieurs paiements fractionnés de ces combinaisons ») :
  - Espèces : transfert vers « Espèces - A&S » (entrée de caisse) ;
  - Redépôt du même chèque : vers « Chèques - A&S » (portefeuille), même n° et même banque ;
  - Nouveau chèque : idem avec le n° / la banque du chèque de remplacement ;
  - Traite bancaire : vers « Traite Bancaire - A&S » (portefeuille des effets), n° + échéance ;
  - Virement, Carte de crédit : directement sur Zitouna (l'argent y est déjà), référence /
    n° de ticket TPE quand on les a — BRS relie le mouvement par la référence ou, à défaut,
    par montant et date.
Un chèque ou une traite de régularisation part ensuite sur un bordereau comme n'importe quelle
pièce ; s'il revient encore impayé, le transfert est simplement annulé (bordereau « Sans
provision » ou flux BRS des impayés) : la créance est de nouveau sur la pièce d'origine.

`reference_no` : pour les espèces, le nom de la pièce d'origine (convention historique) ; pour un
chèque ou une traite, « <n°>-<banque> / … » — la forme que BRS lit pour apparier une remise.
"""
import json
import math
import re

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from customization_app.caisse_encaissement_dettes import ROLES

IMPAYES = "Chèques sans provision - A&S"
ESPECES = "Espèces - A&S"
PORTEFEUILLE = "Chèques - A&S"
TRAITES = "Traite Bancaire - A&S"
BANQUE = "STE430127B - Zitouna - A&S"

MODE_ESPECES = "Espèces"
MODE_REDEPOT = "Redépôt du même chèque"
MODE_NOUVEAU = "Nouveau chèque"
MODE_TRAITE = "Traite bancaire"
MODE_VIREMENT = "Virement"
MODE_CARTE = "Carte de crédit"

#: compte de destination, moyen de paiement ERPNext, contrôle de la pièce saisie.
MODES = {
    MODE_ESPECES: {"compte": ESPECES, "moyen": "Espèces", "piece": None},
    MODE_REDEPOT: {"compte": PORTEFEUILLE, "moyen": "Chèque", "piece": None},
    MODE_NOUVEAU: {"compte": PORTEFEUILLE, "moyen": "Chèque", "piece": "obligatoire",
                   "rx": r"\d{4,20}", "attendu": "4 à 20 chiffres", "banque": True,
                   "libelle": "le numéro du nouveau chèque"},
    MODE_TRAITE: {"compte": TRAITES, "moyen": "Traite bancaire LC", "piece": "obligatoire",
                  "rx": r"\d{4,20}", "attendu": "4 à 20 chiffres", "banque": False, "echeance": True,
                  "libelle": "le numéro de la traite"},
    MODE_VIREMENT: {"compte": BANQUE, "moyen": "Virement", "piece": "facultatif",
                    "rx": r"[A-Za-z0-9/\-]{3,30}", "attendu": "3 à 30 lettres ou chiffres",
                    "libelle": "la référence du virement"},
    MODE_CARTE: {"compte": BANQUE, "moyen": "Carte de crédit", "piece": "facultatif",
                 "rx": r"[A-Za-z0-9/\-]{3,30}", "attendu": "3 à 30 lettres ou chiffres",
                 "libelle": "le numéro du ticket TPE"},
}
TOLERANCE = 0.0005


# ------------------------------------------------------------ règles pures


def numero_et_banque(reference_no):
    """« 0001170-BIAT / BR:90028502 / Impayé FT… » -> ("0001170", "BIAT")."""
    tete = (reference_no or "").split("/")[0].strip()
    num, _sep, banque = tete.partition("-")
    return num.strip(), banque.strip()


def reference_transfert(mode, piece, origine_reference, n_piece=None, banque=None):
    """Le `reference_no` du transfert selon le mode. Pour un chèque ou une traite, le n° ouvre
    le libellé (c'est ce que BRS et le bordereau lisent) ; la pièce d'origine est citée après."""
    n_piece = (n_piece or "").strip()
    banque = (banque or "").strip()
    if mode == MODE_ESPECES:
        return piece
    if mode == MODE_REDEPOT:
        num, bq = numero_et_banque(origine_reference)
        return "%s / Redépôt de %s" % ("%s-%s" % (num, bq) if bq else num, piece)
    if mode in (MODE_NOUVEAU, MODE_TRAITE):
        return "%s / Remplace %s" % ("%s-%s" % (n_piece, banque) if banque else n_piece, piece)
    if mode == MODE_VIREMENT:
        return "%s / Remplace %s" % ("Virement reçu N: %s" % n_piece if n_piece else "Virement", piece)
    return "%s / Remplace %s" % ("Ticket TPE %s" % n_piece if n_piece else "Carte de crédit", piece)


def motif_refus_mode(mode, origine_reference, n_piece=None, banque=None, echeance=None):
    """None si les paramètres du mode sont complets, sinon la phrase à afficher."""
    cfg = MODES.get(mode)
    if not cfg:
        return "Mode de régularisation inconnu."
    if mode == MODE_REDEPOT and not numero_et_banque(origine_reference)[0]:
        return "La pièce d'origine ne porte pas de numéro de chèque : choisissez « Nouveau chèque »."
    n_piece = (n_piece or "").strip()
    if cfg.get("piece") == "obligatoire" and not n_piece:
        return "Saisissez %s (%s)." % (cfg["libelle"], cfg["attendu"])
    if n_piece and cfg.get("rx") and not re.fullmatch(cfg["rx"], n_piece):
        return "%s : %s attendu." % (cfg["libelle"].capitalize(), cfg["attendu"])
    if cfg.get("banque") and not (banque or "").strip():
        return "La banque du nouveau chèque est obligatoire."
    if cfg.get("echeance") and not echeance:
        return "L'échéance de la traite est obligatoire."
    return None


def normaliser_paiements(paiements, restant):
    """Lignes du dialogue -> lignes propres [{mode, montant, n_piece, banque, date_piece, photo}].
    Lève une erreur claire si une ligne est vide, un montant non positif, ou si le total dépasse
    le restant. Pur (hors frappe.throw)."""
    if isinstance(paiements, str):
        paiements = json.loads(paiements or "[]")
    lignes = []
    for p in paiements or []:
        try:
            montant = round(float(p.get("montant") or 0), 3)
        except (TypeError, ValueError):
            montant = 0.0
        if not math.isfinite(montant) or montant <= 0:
            frappe.throw(_("Chaque ligne doit porter un montant positif."))
        lignes.append({"mode": (p.get("mode") or MODE_ESPECES).strip(), "montant": montant,
                       "n_piece": (p.get("n_piece") or p.get("n_cheque") or "").strip(),
                       "banque": (p.get("banque") or "").strip(),
                       "date_piece": p.get("date_piece") or p.get("date_cheque") or None,
                       "photo": p.get("photo") or None})
    if not lignes:
        frappe.throw(_("Aucune ligne de règlement."))
    total = round(sum(l["montant"] for l in lignes), 3)
    if total > round(float(restant), 3) + TOLERANCE:
        frappe.throw(_("Le total ({0}) dépasse le solde restant ({1}). Actualisez la liste.").format(total, round(float(restant), 3)))
    return lignes


def _montant(montant, restant):
    """Conservé pour les appels historiques (une seule ligne)."""
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
        dict(client=client, piece=piece, impayes=IMPAYES), as_dict=True)


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
    """Les modes et leurs exigences, pour le dialogue."""
    return [{"mode": m, "piece": c.get("piece"), "banque": bool(c.get("banque")), "echeance": bool(c.get("echeance")),
             "libelle": c.get("libelle", ""), "attendu": c.get("attendu", ""), "compte": c["compte"]}
            for m, c in MODES.items()]


# ------------------------------------------------------------ écriture


def _commenter(doctype, name, texte):
    try:
        frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": doctype,
                        "reference_name": name, "content": texte}).insert(ignore_permissions=True)
    except Exception:
        pass


def _verifier_comptes(societe, comptes):
    devise = frappe.get_cached_value("Company", societe, "default_currency")
    for nom in comptes:
        meta = frappe.get_doc("Account", nom)
        if meta.company != societe or meta.account_currency != devise or meta.is_group or meta.disabled:
            frappe.throw(_("Le compte {0} doit être actif, dans la société et sa devise.").format(nom))


@frappe.whitelist()
def encaisser(client, piece, montant=None, mode=MODE_ESPECES, n_cheque=None, banque=None, date_cheque=None,
              photo=None, paiements=None):
    """Régularise `piece` par une ou plusieurs lignes {mode, montant, n_piece, banque, date_piece,
    photo}. Les paramètres unitaires (`montant`, `mode`…) restent acceptés : une seule ligne."""
    frappe.only_for(ROLES)
    # Sérialiser deux validations de la même pièce avant de relire son solde.
    frappe.db.sql("SELECT name FROM `tabPayment Entry` WHERE name = %s FOR UPDATE", piece)
    origine = frappe.get_doc("Payment Entry", piece)
    origine.check_permission("read")
    if origine.docstatus != 1 or origine.party_type != "Customer" or origine.party != client:
        frappe.throw(_("La pièce ne correspond pas à un impayé validé de ce client."))
    restant = _restant_verrouille(piece)
    if restant <= 0:
        frappe.throw(_("Cette pièce n'a plus de solde à encaisser. Actualisez la liste."))
    if paiements is None:
        paiements = [{"mode": mode, "montant": montant, "n_piece": n_cheque, "banque": banque,
                      "date_piece": date_cheque, "photo": photo}]
    lignes = normaliser_paiements(paiements, restant)
    for l in lignes:
        refus = motif_refus_mode(l["mode"], origine.reference_no, l["n_piece"], l["banque"], l["date_piece"])
        if refus:
            frappe.throw(_(refus))
    _verifier_comptes(origine.company, {IMPAYES} | {MODES[l["mode"]]["compte"] for l in lignes})

    jour = nowdate()
    client_libelle = origine.party_name or client
    crees, references = [], []
    for l in lignes:
        cfg = MODES[l["mode"]]
        reference = reference_transfert(l["mode"], piece, origine.reference_no, l["n_piece"], l["banque"])
        libelle = {
            MODE_ESPECES: _("Règlement en espèces du chèque impayé {0} — client {1}"),
            MODE_REDEPOT: _("Redépôt du chèque impayé {0} — client {1}"),
            MODE_NOUVEAU: _("Chèque de remplacement de l'impayé {0} — client {1}"),
            MODE_TRAITE: _("Traite en remplacement de l'impayé {0} — client {1}"),
            MODE_VIREMENT: _("Virement en règlement de l'impayé {0} — client {1}"),
            MODE_CARTE: _("Carte bancaire en règlement de l'impayé {0} — client {1}"),
        }[l["mode"]].format(piece, client_libelle)
        if len(lignes) > 1:
            libelle += _(" — règlement fractionné ({0} lignes)").format(len(lignes))
        paiement = frappe.get_doc({
            "doctype": "Payment Entry", "payment_type": "Internal Transfer",
            "company": origine.company, "posting_date": jour,
            "paid_from": IMPAYES, "paid_to": cfg["compte"],
            "paid_amount": l["montant"], "received_amount": l["montant"],
            "source_exchange_rate": 1, "target_exchange_rate": 1,
            "mode_of_payment": cfg["moyen"], "reference_no": reference,
            "reference_date": l["date_piece"] or jour, "custom_remarks": 1,
            "custom_impaye_origine": piece,
            # ERPNext efface `party` sur un transfert, mais garde `party_name` (vérifié) : c'est lui
            # que la ligne de bordereau affiche comme émetteur (fetch_from ref_paiement.party_name).
            "party_name": origine.party_name or client,
            "remarks": libelle,
        })
        paiement.insert()
        paiement.submit()
        if l["photo"]:
            try:
                from frappe.utils.file_manager import save_file

                f = frappe.get_doc("File", {"file_url": l["photo"]})
                save_file(f.file_name, f.get_content(), "Payment Entry", paiement.name, is_private=1)
            except Exception:
                pass
        crees.append(paiement.name)
        references.append(reference)
        _commenter("Payment Entry", piece, "💵 %s : %s → %s (%s)" % (
            libelle, frappe.format_value(l["montant"], {"fieldtype": "Currency"}), paiement.name, reference))
    total = round(sum(l["montant"] for l in lignes), 3)
    reste = round(restant - total, 3)
    resume = _("Impayé {0} régularisé pour {1} ({2}){3}").format(
        piece, frappe.format_value(total, {"fieldtype": "Currency"}),
        ", ".join("%s %s" % (l["mode"], frappe.format_value(l["montant"], {"fieldtype": "Currency"})) for l in lignes),
        (" — " + _("reste {0}").format(frappe.format_value(reste, {"fieldtype": "Currency"}))) if reste > TOLERANCE else "")
    for r in origine.references:
        if r.reference_doctype in ("Sales Order", "Sales Invoice"):
            _commenter(r.reference_doctype, r.reference_name, "💵 " + resume + " → " + ", ".join(crees))
    return {"name": crees[0], "paiements": crees, "references": references, "montant": total, "restant": reste}
