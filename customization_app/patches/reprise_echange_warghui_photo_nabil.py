"""Reprise en production de ce qui a été mis au point sur le dev le 19/11/2026 (demande : « que ça se corrige
automatiquement quand j'envoie en prod »). Joué UNE fois, à la migration.

1. L'ÉCHANGE DE WARGHUI RIDHA devient un vrai échange. Le 14/11, 3 Acc020-A lui ont été envoyés (commande
   SAL-ORD-2026-04691, « Livraison » seule) contre 3 Acc020-B livrés par erreur (MAT-DN-2026-06019) ; le stock a été
   corrigé à la main le 18/11 par MAT-STE-2026-00311. Ici : cette correction manuelle est ANNULÉE et remplacée par
   l'échange lui-même — sortie des 3 Acc020-A datée de la validation de la commande (14/11), entrée des 3 Acc020-B
   datée de la correction manuelle (18/11, quand la pochette a été reçue), photo de la pochette jointe.
   Le stock final est le même ; il est seulement juste quatre jours plus tôt, et l'échange est tracé.

2. LA PHOTO DU RETOUR DE NABIL INOUBLI (WEB1-008706) est retirée : elle montrait la pochette de cet échange, pas son
   colis. Le constat du retour, lui, est GARDÉ : son colis (bordereau de retour 55087946126) nous a bien été livré
   le 19/11 à 15:52 selon Aramex — défaire le BL de retour et recréer le paiement pour les refaire le lendemain
   n'apporterait rien, et laisserait le stock faux tant que personne ne reclique.

⚠️ NE PEUT PAS BLOQUER LE DÉPLOIEMENT. Chaque étape vérifie que la production est EXACTEMENT dans l'état attendu et
ne fait rien sinon ; elle tourne dans un point de reprise : la moindre erreur (stock gelé, pièce déjà modifiée…) la
défait entièrement et se lit dans le journal des erreurs (« Reprise Aramex du 19/11 »).
"""
import frappe

COMMANDE, COMMANDE_ORIGINE, BL_ORIGINE = "SAL-ORD-2026-04691", "WEB1-008697", "MAT-DN-2026-06019"
CORRECTION_MANUELLE = "MAT-STE-2026-00311"
ENTREPOT = "Hall - A&S"
ENVOYES = [{"item_code": "Acc020-A", "qty": 3}]
ATTENDUS = [{"item_code": "Acc020-B", "qty": 3}]
BORDEREAU, BORDEREAU_RETOUR = "55087711265", "55087851044"
NABIL_COMMANDE, NABIL_BORDEREAU = "WEB1-008706", "55087538380"
PHOTO = "17950149860297132933301419325066ba7eb3.jpg"
TITRE = "Reprise Aramex du 19/11"


def correction_attendue(lignes) -> bool:
    """La correction manuelle est-elle bien celle qu'on connaît : 3 Acc020-A sortis du Hall, 3 Acc020-B entrés au
    Hall, rien d'autre ? `lignes` : [(article, quantité, entrepôt de sortie, entrepôt d'entrée)]. PURE."""
    return sorted((a, float(q), s or "", t or "") for a, q, s, t in lignes or []) == [
        ("Acc020-A", 3.0, ENTREPOT, ""), ("Acc020-B", 3.0, "", ENTREPOT)]


def _echange_warghui():
    from customization_app import echange_aramex as E

    so = frappe.db.get_value("Sales Order", COMMANDE, ["name", "customer", "customer_name", "docstatus",
                                                       "custom_bordereau_aramex"], as_dict=True)
    if not so or so.docstatus != 1 or not frappe.db.exists("Stock Entry", CORRECTION_MANUELLE):
        return "rien à reprendre ici"
    if frappe.db.exists(E.DOCTYPE, {"commande": COMMANDE, "statut": ["!=", E.ANNULE]}):
        return "échange déjà déclaré"
    manuelle = frappe.get_doc("Stock Entry", CORRECTION_MANUELLE)
    if not correction_attendue([(i.item_code, i.qty, i.s_warehouse, i.t_warehouse) for i in manuelle.items]):
        return "la correction manuelle n'est pas celle attendue : rien n'est touché"
    if manuelle.docstatus not in (1, 2):
        return "la correction manuelle n'est pas validée : rien n'est touché"
    recu_le = "%s %s" % (manuelle.posting_date, str(manuelle.posting_time).split(".")[0].zfill(8))
    recu_par = manuelle.owner
    if manuelle.docstatus == 1:
        manuelle.flags.ignore_permissions = True
        manuelle.cancel()

    envoyes, attendus = E.lignes_propres(ENVOYES), E.lignes_propres(ATTENDUS)
    E._verifier_articles(attendus)
    E._verifier_articles(envoyes, ENTREPOT)
    doc, sortie, _le = E.creer_echange(
        so, envoyes, attendus, ENTREPOT,
        bl_origine=BL_ORIGINE if frappe.db.exists("Delivery Note", BL_ORIGINE) else None,
        commande_origine=COMMANDE_ORIGINE if frappe.db.exists("Sales Order", COMMANDE_ORIGINE) else None,
        note="Reprise de la correction manuelle %s (annulée), le 19/11/2026." % CORRECTION_MANUELLE)
    frappe.db.set_value(E.DOCTYPE, doc.name, {"bordereau": BORDEREAU, "bordereau_retour": BORDEREAU_RETOUR,
                                             "retour_suivi": "Livré chez nous le 18/11/2026 10:34"},
                        update_modified=False)
    doc.reload()
    entree = E.enregistrer_reception(
        doc, ENTREPOT,
        "Photo de la pochette vérifiée le 19/11/2026 : ✅ correspond au bordereau (étiquette lue : bordereau de retour "
        "%s, réf. expéditeur %s)." % (BORDEREAU_RETOUR, BORDEREAU),
        bordereau=BORDEREAU, le=recu_le, par=recu_par)

    # La photo de la pochette existe déjà (jointe à tort au retour de Nabil Inoubli) : elle rejoint l'échange.
    source = frappe.db.get_value("File", {"file_name": PHOTO}, ["file_url", "is_private", "content_hash", "file_size"],
                                 as_dict=True)
    if source:
        frappe.get_doc({"doctype": "File", "file_name": PHOTO, "file_url": source.file_url,
                        "is_private": source.is_private, "content_hash": source.content_hash,
                        "file_size": source.file_size, "attached_to_doctype": E.DOCTYPE,
                        "attached_to_name": doc.name}).insert(ignore_permissions=True)
    return "échange %s : sortie %s, entrée %s, correction manuelle annulée" % (doc.name, sortie, entree)


def _photo_nabil():
    if not frappe.db.get_value("Suivi Aramex", NABIL_BORDEREAU, "retour_recu_le"):
        return "retour non constaté : rien à corriger"
    # La photo ne quitte la commande que si elle est en sécurité sur l'échange (étape 1 réussie).
    echange = frappe.db.get_value("File", {"file_name": PHOTO, "attached_to_doctype": "Echange Aramex"},
                                  "attached_to_name")
    if not echange:
        return "la photo n'est pas sur l'échange : elle reste où elle est"
    a_retirer = frappe.get_all("File", pluck="name", filters={
        "file_name": PHOTO, "attached_to_doctype": ["in", ["Sales Order", "Suivi Aramex", "Delivery Note"]]})
    if not a_retirer:
        return "photo déjà retirée"
    for nom in a_retirer:
        frappe.delete_doc("File", nom, force=True, ignore_permissions=True)     # le fichier reste : l'échange le porte
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info", "reference_doctype": "Sales Order",
        "reference_name": NABIL_COMMANDE,
        "content": ("📷 Photo du retour retirée le 19/11/2026 : elle montrait la pochette de l'échange %s (Warghui "
                    "Ridha), pas ce colis. Le retour constaté le 18/11 est gardé : le colis de Nabil Inoubli "
                    "(bordereau de retour 55087946126) nous a été livré le 19/11/2026 à 15:52 selon Aramex. "
                    "La photo du vrai colis reste à joindre.") % echange,
    }).insert(ignore_permissions=True)
    return "%d pièce(s) jointe(s) retirée(s)" % len(a_retirer)


def execute():
    if not frappe.db.exists("DocType", "Echange Aramex"):
        return
    bilan = []
    for etape in (_echange_warghui, _photo_nabil):
        point = "reprise_%s" % etape.__name__.strip("_")
        frappe.db.savepoint(point)
        try:
            bilan.append("%s : %s" % (etape.__name__.strip("_"), etape()))
        except Exception:
            frappe.db.rollback(save_point=point)
            frappe.log_error(title=TITRE, message="%s a échoué — rien n'a été modifié pour cette étape.\n\n%s"
                             % (etape.__name__, frappe.get_traceback()))
            bilan.append("%s : ÉCHEC (voir le journal des erreurs)" % etape.__name__.strip("_"))
    print("   " + " | ".join(bilan))
