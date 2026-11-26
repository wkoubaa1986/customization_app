"""Facture SCPS FT26080301 (207,158 TTC, chèque 4001066) : ressaisie en FACTURE D'ACHAT, comme si Sadok l'avait saisie
ainsi — décision de l'utilisateur du 26/11/2026 (« patche la base … comme si Sadok l'a fait, pour que ça sorte dans sa
caisse aussi »), après son essai sur le dev.

Le 26/11/2026 à 11:52, Sadok a saisi cette facture en « Dépense avec facture » (DAP-1892) : le seul type qui
reconnaissait alors un BL déjà reçu. Stock juste, mais tout le HT en « Charges Diverses » et aucune facture d'achat.
Depuis, « Facture d'achat » reconnaît les BL (caisse_facture_bl).

Ce que fait ce patch, EN UNE FOIS (tout ou rien) :
  1. défait la dépense : le BL BL2615138 (FAS-0317) est rouvert (son écriture de solde annulée), la part stock est
     annulée (écriture, réception MAT-PRE-…, commande, fiche FAS-1893), la dépense ACC-JV-2026-01103 est annulée, la
     fiche DAP-1892 supprimée ;
  2. ressaisit la facture par le chemin de l'écran (caisse_depenses.creer, type « Facture d'achat »), AU NOM de Sadok
     et à la DATE de sa saisie : même chèque, même scan, même photo du chèque. Le BL déjà reçu est facturé depuis sa
     réception ; les 30 + 4 réductions du BL2615063 sont reçues (le stock revient à l'identique) ; la facture d'achat
     est préparée EN BROUILLON. Le comptable la vérifie et la valide : le chèque devient alors son paiement.

Prudence : la prod a pu changer depuis la lecture du 26/11/2026 à 17:30. L'état réel est vérifié avant d'agir (pièces
présentes et inchangées, chèque non rapproché en banque, stock encore là, facture pas déjà ressaisie) ; au moindre
doute rien n'est touché. Tout se joue dans un point de sauvegarde, validations intermédiaires neutralisées : un échec
annule tout. Le patch ne fait JAMAIS échouer la migration. Compte rendu dans le journal des erreurs
(« Facture SCPS FT26080301 ») et en commentaire sur les pièces. Aucun appel à l'IA (lignes et BL sont ceux de l'essai).
"""
import base64
import os
from unittest import mock

import frappe
from frappe.utils import flt

DAP = "DAP-1892"
DEPENSE = "ACC-JV-2026-01103"
FICHE_BL = "FAS-0317"
FOURNISSEUR = "SCPS"
NUMERO = "FT26080301"
DATE_FACTURE = "2026-08-31"
TTC, TVA, TIMBRE = 207.158, 32.916, 1.0
CHEQUE, BANQUE = "4001066", "Banque Zitouna"
# Les lignes du BL jamais saisi, telles que l'écran les a lues et que l'utilisateur les a validées à l'essai.
LIGNES = [
    {"item_code": "BRS-BQ-15-12", "qty": 30, "rate": 2.545, "designation": 'REDUCTION COLORETTE 1/2" X 3/8" BLUE',
     "bl_cle": "2615063", "code": "BMAMFM1/2-3"},
    {"item_code": "BRS-SP-15-12", "qty": 4, "rate": 2.653, "designation": "REDUCTION SPECIAL FM 1/2-3/8 BLUE",
     "bl_cle": "2615063", "code": "BRSFM15/12"},
]
CITES = [{"numero": "BL2615063", "date": "2026-08-18", "total_ht": 86.953, "total_ttc": None},
         {"numero": "BL2615138", "date": "2026-08-21", "total_ht": 86.289, "total_ttc": None}]
MARQUE = "Ressaisie en facture d'achat de la facture SCPS FT26080301 (26/11/2026)"
TITRE_JOURNAL = "Facture SCPS FT26080301"
FICHE = "Facture Achat a Saisir"


def _commenter(doctype, name, texte):
    if not name or not frappe.db.exists(doctype, name):
        return
    frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": doctype,
                    "reference_name": name, "content": "%s — %s" % (MARQUE, texte)}).insert(ignore_permissions=True)


def _contenu(doctype, name, extensions):
    """(nom, octets) du premier fichier joint à cette pièce dont l'extension convient, lu sur le disque."""
    for f in frappe.get_all("File", filters={"attached_to_doctype": doctype, "attached_to_name": name},
                            fields=["file_name", "file_url", "is_private"], order_by="creation"):
        if not f.file_url or not (f.file_name or "").lower().endswith(extensions):
            continue
        chemin = frappe.get_site_path("private" if f.is_private else "public", "files", os.path.basename(f.file_url))
        if os.path.exists(chemin):
            with open(chemin, "rb") as fichier:
                return f.file_name, fichier.read()
    return None, None


def _data_url(nom, octets):
    ext = (nom or "").lower().rsplit(".", 1)[-1]
    mime = {"pdf": "application/pdf", "png": "image/png"}.get(ext, "image/jpeg")
    return "data:%s;base64,%s" % (mime, base64.b64encode(octets).decode())


def etat():
    """Ce qu'il faut pour agir, ou le motif qui fait tout laisser en place. → (dict, motif)."""
    if frappe.db.exists("Purchase Invoice", {"supplier": FOURNISSEUR, "bill_no": NUMERO, "docstatus": ["<", 2]}):
        return None, "une facture d'achat %s existe déjà pour %s : déjà ressaisie" % (NUMERO, FOURNISSEUR)
    dap = frappe.db.get_value("Depense A Payer", DAP, ["numero_facture", "montant", "journal_entry", "fiche_achat", "description",
                                                       "fournisseur", "saisi_par"], as_dict=True)
    if not dap:
        return None, "la dépense %s n'existe plus" % DAP
    if dap.numero_facture != NUMERO or abs(flt(dap.montant) - TTC) > 0.0005 or dap.journal_entry != DEPENSE:
        return None, "la dépense %s n'est plus celle du 26/11 (%s, %s, %s)" % (DAP, dap.numero_facture, dap.montant, dap.journal_entry)
    je = frappe.db.get_value("Journal Entry", DEPENSE, ["docstatus", "posting_date", "owner", "clearance_date", "user_remark"], as_dict=True)
    if not je or je.docstatus != 1:
        return None, "l'écriture %s n'est plus validée" % DEPENSE
    if je.clearance_date:
        return None, "le chèque de %s est déjà rapproché en banque (%s) : à corriger à la main" % (DEPENSE, je.clearance_date)
    if ("Chq N° %s" % CHEQUE) not in (je.user_remark or ""):
        return None, "l'écriture %s ne porte plus le chèque %s" % (DEPENSE, CHEQUE)
    if not frappe.db.get_value("User", je.owner, "enabled"):
        return None, "le compte de %s n'est plus actif" % je.owner
    bl = frappe.db.get_value(FICHE, FICHE_BL, ["statut", "depense_a_payer", "purchase_receipt", "supplier"], as_dict=True)
    if not bl or bl.depense_a_payer != DAP or bl.supplier != FOURNISSEUR or not bl.purchase_receipt:
        return None, "le BL %s n'est plus couvert par %s" % (FICHE_BL, DAP)
    part = frappe.db.get_value(FICHE, dap.fiche_achat, ["journal_entry", "purchase_order", "purchase_receipt"], as_dict=True) \
        if dap.fiche_achat else None
    if not part or not part.purchase_receipt or not part.purchase_order:
        return None, "la part stock de %s (fiche %s) est introuvable" % (DAP, dap.fiche_achat)
    if frappe.db.get_value("Purchase Receipt", part.purchase_receipt, "docstatus") != 1:
        return None, "la réception %s n'est plus validée" % part.purchase_receipt
    # Le stock reçu ce matin doit être encore là : la réception est annulée puis refaite à l'identique.
    for l in frappe.get_all("Purchase Receipt Item", filters={"parent": part.purchase_receipt}, fields=["item_code", "warehouse", "stock_qty"]):
        dispo = flt(frappe.db.get_value("Bin", {"item_code": l.item_code, "warehouse": l.warehouse}, "actual_qty"))
        if dispo < flt(l.stock_qty) - 0.0005:
            return None, ("%s : il ne reste que %s en %s sur les %s reçus le 26/11 (déjà sortis) — la réception ne peut pas être "
                          "annulée, à corriger à la main" % (l.item_code, dispo, l.warehouse, l.stock_qty))
    facture = _contenu("Journal Entry", DEPENSE, (".pdf",))
    cheque = _contenu("Journal Entry", DEPENSE, (".jpg", ".jpeg", ".png"))
    if not facture[1] or not cheque[1]:
        return None, "le scan de la facture ou la photo du chèque n'est plus joint à %s" % DEPENSE
    return {"dap": dap, "je": je, "part": part, "fiche_part": dap.fiche_achat, "facture": facture, "cheque": cheque}, ""


def defaire(e):
    """La dépense du 26/11 est défaite, dans l'ordre inverse de sa saisie."""
    from customization_app import caisse_bl as B

    B.rouvrir_bls(DAP)   # FAS-0317 de nouveau « À saisir », son écriture de solde annulée, sa réception « à facturer »
    part = e["part"]
    if part.journal_entry and frappe.db.get_value("Journal Entry", part.journal_entry, "docstatus") == 1:
        frappe.get_doc("Journal Entry", part.journal_entry).cancel()
    # La commande d'abord rouverte : ERPNext refuse d'annuler la réception d'une commande fermée.
    if frappe.db.get_value("Purchase Order", part.purchase_order, "status") == "Closed":
        frappe.get_doc("Purchase Order", part.purchase_order).update_status("Submitted")
    pr = frappe.get_doc("Purchase Receipt", part.purchase_receipt)
    if pr.status == "Closed":
        pr.update_status("Submitted")
        pr.reload()
    pr.cancel()
    frappe.get_doc("Purchase Order", part.purchase_order).cancel()
    frappe.db.set_value("Depense A Payer", DAP, {"fiche_achat": None, "purchase_order": None}, update_modified=False)
    frappe.delete_doc(FICHE, e["fiche_part"], force=True, ignore_permissions=True)
    frappe.get_doc("Journal Entry", DEPENSE).cancel()
    frappe.db.set_value("Depense A Payer", DAP, {"journal_entry": None, "reglement_journal_entry": None}, update_modified=False)
    frappe.delete_doc("Depense A Payer", DAP, force=True, ignore_permissions=True)


def ressaisir(e):
    """La même facture, par le chemin de l'écran, au nom de celui qui l'avait saisie et à la date de sa saisie."""
    from customization_app import caisse_depenses as C

    je, dap = e["je"], e["dap"]
    frappe.set_user(je.owner)
    try:
        # La date : celle de la saisie d'origine (l'écriture de caisse reste dans la journée du 26/11 de sa caisse).
        # La lecture de la photo du chèque par l'IA (une aide à l'écran) n'a pas sa place dans une migration.
        with mock.patch.object(C, "nowdate", lambda: str(je.posting_date)), \
                mock.patch.object(C, "_avertissements_pieces", lambda regs: []):
            return C.creer(
                type_depense="Facture d'achat", montant=TTC, mode="Chèque", description=dap.description,
                fournisseur=dap.fournisseur, tva=TVA, taux_tva=19, numero_facture=NUMERO, date_facture=DATE_FACTURE,
                n_cheque=CHEQUE, banque=BANQUE, supplier=FOURNISSEUR, timbre=TIMBRE,
                photo_facture=_data_url(*e["facture"]), photo_facture_nom=e["facture"][0],
                photo_cheque=_data_url(*e["cheque"]), photo_cheque_nom=e["cheque"][0],
                articles_stock=frappe.as_json(LIGNES), bls_couverts=frappe.as_json([FICHE_BL]),
                bls_cites=frappe.as_json(CITES))
    finally:
        frappe.set_user("Administrator")


def controler(r):
    """Le résultat attendu, sinon tout est annulé."""
    achat = r.get("achat") or {}
    pi = achat.get("purchase_invoice")
    if not pi or abs(flt(frappe.db.get_value("Purchase Invoice", pi, "grand_total")) - TTC) > 0.0005:
        raise frappe.ValidationError("la facture préparée ne fait pas %s TTC" % TTC)
    if [b.get("fiche") for b in achat.get("bls_factures") or []] != [FICHE_BL]:
        raise frappe.ValidationError("le BL %s n'est pas facturé par la facture préparée" % FICHE_BL)
    if len(achat.get("bls_recus") or []) != 1 or not r.get("name"):
        raise frappe.ValidationError("la réception du BL2615063 ou l'écriture du chèque manque")
    return pi


def execute():
    if frappe.db.exists("Comment", {"reference_doctype": "Journal Entry", "reference_name": DEPENSE,
                                    "content": ["like", "%" + MARQUE + "%"]}):
        return
    compte_rendu = []
    try:
        e, motif = etat()
        if not e:
            compte_rendu.append("RIEN N'A ÉTÉ TOUCHÉ : %s." % motif)
            return
        stock_avant = _stock()
        frappe.db.savepoint("facture_scps")
        try:
            # Aucune validation intermédiaire : tout ou rien. Les tâches de fond partiront de l'état final.
            with mock.patch.object(frappe.db, "commit", lambda *a, **k: None):
                defaire(e)
                r = ressaisir(e)
                pi = controler(r)
                if _stock() != stock_avant:
                    raise frappe.ValidationError("le stock a changé : %s → %s" % (stock_avant, _stock()))
                achat = r["achat"]
                recu = achat["bls_recus"][0]
                _commenter("Journal Entry", DEPENSE, "dépense annulée : la facture est une facture d'achat, ressaisie comme telle "
                           "(fiche %s, écriture du chèque %s, facture préparée %s)." % (r["fiche"], r["name"], pi))
                _commenter(FICHE, r["fiche"], "remplace la dépense %s (%s) saisie le 26/11/2026 par %s." % (DAP, DEPENSE, e["je"].owner))
                _commenter("Purchase Invoice", pi, "à vérifier et valider : le chèque %s (écriture %s) deviendra son paiement. "
                           "BL2615138 facturé depuis sa réception ; BL2615063 reçu par %s." % (CHEQUE, r["name"], recu["purchase_receipt"]))
                # La caisse de ce jour-là, déjà comptée : la pièce a changé de nature, pas l'argent.
                for clo in frappe.get_all("Cloture Caisse", pluck="name", filters={
                        "date_cloture": e["je"].posting_date, "docstatus": ["<", 2],
                        "caisse": C_caisse(e["je"].owner)}):
                    _commenter("Cloture Caisse", clo, "la dépense par chèque %s de %s DT (%s) est devenue une facture d'achat "
                               "payée par le même chèque (%s) : aucun effet sur les espèces comptées." % (CHEQUE, TTC, DEPENSE, r["name"]))
                compte_rendu.append("FAIT : dépense %s / %s défaite ; facture ressaisie au nom de %s, datée du %s — fiche %s, écriture "
                                    "du chèque %s, réception %s, facture d'achat %s EN BROUILLON (%s TTC, arrondi %s). Stock inchangé : %s."
                                    % (DAP, DEPENSE, e["je"].owner, e["je"].posting_date, r["fiche"], r["name"], recu["purchase_receipt"],
                                       pi, achat["ttc"], achat["arrondi"], stock_avant))
        except Exception:
            erreur = frappe.get_traceback().strip().splitlines()[-1]
            frappe.set_user("Administrator")
            try:
                frappe.db.rollback(save_point="facture_scps")
                compte_rendu.append("ÉCHEC, tout a été annulé, rien n'a changé — %s" % erreur)
            except Exception:
                compte_rendu.append("ÉCHEC — %s (retour arrière impossible : état à vérifier)" % erreur)
    except Exception:
        compte_rendu.append("ÉCHEC avant toute écriture — %s" % frappe.get_traceback().strip().splitlines()[-1])
    finally:
        frappe.set_user("Administrator")
        try:
            frappe.log_error(title=TITRE_JOURNAL, message="\n".join(compte_rendu) or "rien à dire")
        except Exception:
            pass


def C_caisse(user):
    from customization_app import caisse_depenses as C
    return C.caisse_de_l_utilisateur(user)


def _stock():
    return sorted((i, w, flt(q, 3)) for i, w, q in frappe.db.sql(
        "select item_code, warehouse, actual_qty from tabBin where item_code in ('BRS-BQ-15-12', 'BRS-SP-15-12') and actual_qty <> 0"))
