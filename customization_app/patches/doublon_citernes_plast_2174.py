"""Doublon CITERNES PLAST, facture n° 2174 du 14/11/2026 (87,537 DT) — décision de l'utilisateur du 25/11/2026 :
« c'est la caisse de Jamel qui a payé : garder les factures, enlever le FAS » (le doublon de Sadok).

Le 16/11, la même facture a été saisie deux fois dans la caisse, à 24 secondes d'écart :
  - Jamel : fiche FAS-1619 (restée « À saisir ») + ACC-JV-2026-01064, sortie de SA caisse, avance au fournisseur
    sans facture rattachée ;
  - Sadok : fiche FAS-1620 → facture d'achat ACC-PINV-2026-00129 + ACC-PAY-2026-08472, sortie de SA caisse.
Les deux clôtures du 16/11 (Jamel CLO-1727, Sadok CLO-1721) ont déduit chacune 87,537 DT.

Ce que fait ce patch :
  1. annule le paiement de Sadok ACC-PAY-2026-08472 (le doublon) ;
  2. rapproche la sortie de caisse de Jamel ACC-JV-2026-01064 de la facture ACC-PINV-2026-00129 (Payment
     Reconciliation d'ERPNext) : la facture est payée par la caisse de Jamel ;
  3. la fiche de Jamel FAS-1619 passe « Saisie », liée à la facture (elle ne pourra plus en créer une seconde) ;
     la pièce jointe de la fiche de Sadok lui est rattachée ;
  4. supprime la fiche en double de Sadok FAS-1620 ;
  5. laisse les clôtures VALIDÉES telles quelles (instantanés figés, PDF joint) mais y écrit la correction : celle de
     Sadok du 16/11 a déduit 87,537 DT qu'il n'a pas payés — cet argent est à retrouver chez lui (ou chez le
     collecteur).

Prudence : la prod a pu changer depuis la copie du dev. Chaque étape vérifie l'état réel avant d'agir, saute ce qui
est déjà fait, s'annule seule si elle échoue (point de sauvegarde) et ne fait JAMAIS échouer la migration. Le compte
rendu est écrit dans le journal des erreurs (« Doublon CITERNES PLAST 2174 ») et en commentaire sur les pièces.
"""
import frappe
from frappe.utils import flt

PE_SADOK = "ACC-PAY-2026-08472"
JV_JAMEL = "ACC-JV-2026-01064"
FACTURE = "ACC-PINV-2026-00129"
FICHE_JAMEL = "FAS-1619"
FICHE_SADOK = "FAS-1620"
CLOTURE_SADOK = "CLO-1721"
CLOTURE_JAMEL = "CLO-1727"
FOURNISSEUR = "Citernes Plast"
NUMERO = "2174"
MONTANT = 87.537
COMPTE_FOURNISSEURS = "Créditeurs - A&S"
MARQUE = "Correction du doublon CITERNES PLAST n° 2174 (25/11/2026)"
TITRE_JOURNAL = "Doublon CITERNES PLAST 2174"


def _commenter(doctype, name, texte):
    """Un commentaire Info, une seule fois (la marque évite de le répéter si le patch repasse)."""
    if not frappe.db.exists(doctype, name):
        return
    if frappe.db.exists("Comment", {"reference_doctype": doctype, "reference_name": name,
                                    "content": ["like", "%" + MARQUE + "%"]}):
        return
    frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": doctype,
                    "reference_name": name, "content": "%s — %s" % (MARQUE, texte)}).insert(ignore_permissions=True)


def _etape(compte_rendu, nom, fonction):
    """Une étape dans son point de sauvegarde : si elle échoue, elle s'annule seule et la suite continue."""
    point = "doublon_2174_%d" % len(compte_rendu)
    frappe.db.savepoint(point)
    try:
        compte_rendu.append("%s : %s" % (nom, fonction()))
    except Exception:
        erreur = frappe.get_traceback().strip().splitlines()[-1]
        try:
            frappe.db.rollback(save_point=point)
            compte_rendu.append("%s : ÉCHEC, rien n'a été changé pour cette étape — %s" % (nom, erreur))
        except Exception:
            # Un point de sauvegarde perdu (une validation intermédiaire) ne doit jamais remonter : la migration de la
            # prod échouerait. Chaque étape vérifie l'état avant d'agir, un nouveau passage reprendrait le reste.
            compte_rendu.append("%s : ÉCHEC — %s (retour arrière impossible : état à vérifier)" % (nom, erreur))


def annuler_paiement_sadok():
    pe = frappe.db.get_value("Payment Entry", PE_SADOK, ["docstatus", "paid_amount", "party", "paid_from"], as_dict=True)
    if not pe:
        return "paiement introuvable, rien à faire"
    if pe.docstatus == 2:
        return "déjà annulé"
    if pe.docstatus != 1 or abs(flt(pe.paid_amount) - MONTANT) > 0.0005 or pe.party != FOURNISSEUR:
        return "état inattendu (docstatus %s, %s DT, %s) : laissé tel quel" % (pe.docstatus, pe.paid_amount, pe.party)
    doc = frappe.get_doc("Payment Entry", PE_SADOK)
    doc.flags.ignore_permissions = True
    doc.cancel()
    _commenter("Payment Entry", PE_SADOK, "paiement en double annulé : la facture a été payée par la caisse de Jamel "
               "(%s)." % JV_JAMEL)
    return "annulé"


def _ligne_avance_jamel():
    """La ligne fournisseur de la sortie de caisse de Jamel, si elle n'est rattachée à aucune facture."""
    lignes = frappe.get_all("Journal Entry Account", filters={"parent": JV_JAMEL, "party_type": "Supplier",
                                                               "party": FOURNISSEUR},
                            fields=["name", "debit_in_account_currency", "reference_type", "reference_name"])
    return [l for l in lignes if not l.reference_name and flt(l.debit_in_account_currency) > 0]


def rapprocher_jamel_facture():
    if frappe.db.get_value("Journal Entry", JV_JAMEL, "docstatus") != 1:
        return "sortie de caisse de Jamel absente ou annulée : rien à rapprocher"
    facture = frappe.db.get_value("Purchase Invoice", FACTURE, ["docstatus", "outstanding_amount", "supplier", "company"],
                                  as_dict=True)
    if not facture or facture.docstatus != 1:
        return "facture absente ou non validée : rien à rapprocher"
    if flt(facture.outstanding_amount) < 0.0005:
        return "facture déjà payée (reste dû 0) : laissée telle quelle"
    if not _ligne_avance_jamel():
        return "la sortie de caisse de Jamel est déjà rattachée à une facture : laissée telle quelle"
    pr = frappe.new_doc("Payment Reconciliation")
    pr.company = facture.company
    pr.party_type = "Supplier"
    pr.party = FOURNISSEUR
    pr.receivable_payable_account = COMPTE_FOURNISSEURS
    pr.get_unreconciled_entries()
    factures = [i.as_dict() for i in pr.invoices if i.invoice_number == FACTURE]
    paiements = [p.as_dict() for p in pr.payments if p.reference_name == JV_JAMEL]
    if not factures or not paiements:
        return "rapprochement impossible (facture %s, paiement %s trouvés)" % (bool(factures), bool(paiements))
    pr.allocate_entries({"invoices": factures, "payments": paiements})
    pr.reconcile()
    reste = flt(frappe.db.get_value("Purchase Invoice", FACTURE, "outstanding_amount"))
    if reste > 0.0005:
        raise frappe.ValidationError("la facture reste due de %s DT après rapprochement" % reste)
    _commenter("Purchase Invoice", FACTURE, "payée par la caisse de Jamel (%s, 16/11/2026) ; le paiement en double de "
               "Sadok (%s) est annulé." % (JV_JAMEL, PE_SADOK))
    _commenter("Journal Entry", JV_JAMEL, "rapprochée de la facture %s (CITERNES PLAST n° %s)." % (FACTURE, NUMERO))
    return "facture payée par la sortie de caisse de Jamel (reste dû 0)"


def fiche_jamel_saisie():
    fiche = frappe.db.get_value("Facture Achat a Saisir", FICHE_JAMEL, ["statut", "purchase_invoice", "cle_facture"],
                                as_dict=True)
    if not fiche:
        return "fiche introuvable, rien à faire"
    if fiche.purchase_invoice and fiche.purchase_invoice != FACTURE:
        return "déjà liée à une autre facture (%s) : laissée telle quelle — à vérifier" % fiche.purchase_invoice
    valeurs = {"statut": "Saisie", "purchase_invoice": FACTURE}
    # La clé anti-doublon (24/11/2026) : la fiche de Sadok la porterait encore si elle l'avait ; elle part à l'étape
    # suivante, la clé n'est posée que si personne ne l'a.
    from customization_app.caisse_depenses import cle_facture
    cle = cle_facture(FOURNISSEUR, NUMERO)
    if cle and not fiche.cle_facture and not frappe.db.exists("Facture Achat a Saisir", {"cle_facture": cle}):
        valeurs["cle_facture"] = cle
    frappe.db.set_value("Facture Achat a Saisir", FICHE_JAMEL, valeurs, update_modified=True)
    # La photo de la fiche de Sadok (la même facture) rejoint celle de Jamel avant la suppression.
    for f in frappe.get_all("File", filters={"attached_to_doctype": "Facture Achat a Saisir",
                                             "attached_to_name": FICHE_SADOK}, pluck="name"):
        frappe.db.set_value("File", f, "attached_to_name", FICHE_JAMEL, update_modified=False)
    _commenter("Facture Achat a Saisir", FICHE_JAMEL, "saisie dans la facture %s ; c'est cette caisse (Jamel) qui a "
               "payé ; la fiche en double de Sadok (%s) est supprimée." % (FACTURE, FICHE_SADOK))
    return "« Saisie », liée à %s" % FACTURE


def supprimer_fiche_sadok():
    if not frappe.db.exists("Facture Achat a Saisir", FICHE_SADOK):
        return "déjà supprimée"
    fiche = frappe.db.get_value("Facture Achat a Saisir", FICHE_SADOK, ["purchase_invoice", "payment_entry"], as_dict=True)
    if fiche.purchase_invoice not in (None, "", FACTURE) or fiche.payment_entry not in (None, "", PE_SADOK):
        return "pointe vers d'autres pièces (%s, %s) : laissée telle quelle — à vérifier" % (
            fiche.purchase_invoice, fiche.payment_entry)
    if frappe.db.get_value("Payment Entry", PE_SADOK, "docstatus") == 1:
        return "son paiement n'est pas annulé : laissée telle quelle"
    if frappe.get_all("File", filters={"attached_to_doctype": "Facture Achat a Saisir", "attached_to_name": FICHE_SADOK}):
        return "porte encore une pièce jointe (l'étape 3 n'est pas passée) : laissée telle quelle"
    # Sans « force » : si un document s'y rattache encore en prod, Frappe refuse et l'étape s'annule seule.
    frappe.delete_doc("Facture Achat a Saisir", FICHE_SADOK, ignore_permissions=True)
    return "supprimée"


def noter_clotures():
    _commenter("Cloture Caisse", CLOTURE_SADOK,
               "cette clôture a déduit la dépense CITERNES PLAST de 87,537 DT (%s), un doublon : c'est la caisse de "
               "Jamel qui a payé. Le paiement est annulé ; la clôture validée n'est pas modifiée. Sadok n'ayant pas "
               "payé, 87,537 DT de plus devaient se trouver dans sa caisse ce jour-là : à retrouver chez lui (ou chez "
               "le collecteur, qui a validé la caisse au montant attendu)." % PE_SADOK)
    _commenter("Cloture Caisse", CLOTURE_JAMEL,
               "confirmé : c'est cette caisse qui a payé CITERNES PLAST n° 2174 (87,537 DT, %s) ; le doublon saisi par "
               "Sadok est annulé." % JV_JAMEL)
    return "commentaires posés sur %s et %s" % (CLOTURE_SADOK, CLOTURE_JAMEL)


def autres_factures():
    """Une seconde facture d'achat n° 2174 créée en prod depuis le 24/11 ? Signalée, jamais touchée."""
    autres = frappe.get_all("Purchase Invoice", filters={"supplier": FOURNISSEUR, "bill_no": ["like", "%" + NUMERO + "%"],
                                                          "docstatus": ["<", 2], "name": ["!=", FACTURE]}, pluck="name")
    return ("AUTRES FACTURES n° %s trouvées, à vérifier à la main : %s" % (NUMERO, ", ".join(autres))) if autres \
        else "aucune autre facture n° %s" % NUMERO


def execute():
    try:
        _executer()
    except Exception:
        # Jamais d'échec de « bench migrate » à cause de cette correction de données.
        try:
            frappe.log_error(title=TITRE_JOURNAL, message=frappe.get_traceback())
            frappe.db.commit()
        except Exception:
            pass


def _executer():
    if not frappe.db.exists("Purchase Invoice", FACTURE) and not frappe.db.exists("Payment Entry", PE_SADOK):
        return            # un site sans ces pièces (site neuf, autre société) : rien à faire
    compte_rendu = []
    _etape(compte_rendu, "1. paiement de Sadok %s" % PE_SADOK, annuler_paiement_sadok)
    _etape(compte_rendu, "2. rapprochement %s → %s" % (JV_JAMEL, FACTURE), rapprocher_jamel_facture)
    _etape(compte_rendu, "3. fiche de Jamel %s" % FICHE_JAMEL, fiche_jamel_saisie)
    _etape(compte_rendu, "4. fiche de Sadok %s" % FICHE_SADOK, supprimer_fiche_sadok)
    _etape(compte_rendu, "5. clôtures du 16/11", noter_clotures)
    _etape(compte_rendu, "6. contrôle", autres_factures)
    texte = "\n".join(compte_rendu)
    print(TITRE_JOURNAL + "\n" + texte)
    frappe.log_error(title=TITRE_JOURNAL, message=texte)
    frappe.db.commit()
