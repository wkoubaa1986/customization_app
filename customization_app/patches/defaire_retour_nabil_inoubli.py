"""Le retour de Nabil Inoubli (WEB1-008706, bordereau 55087538380) redevient « pas encore constaté » en production
(demande du 19/11/2026 : « il y avait un retour mal positionné sur WEB1-008706 qui devait être patché »).

Le 18/11/2026 ce retour a été constaté avec la photo d'un AUTRE colis (la pochette de l'échange de Warghui Ridha), un
jour avant l'arrivée du vrai. Le patch précédent (`reprise_echange_warghui_photo_nabil`) n'avait retiré que la photo
et gardait le constat ; ce n'est pas ce qui était voulu. Ici on DÉFAIT le constat, exactement comme
`retour_aramex.constater_retour` l'avait fait, à l'envers :
  1. la commande est rouverte et perd sa marque « Retour colis » ;
  2. le BL de retour est annulé et supprimé (le stock ressort) ;
  3. le paiement « Dette non payée » sur Livraison Aramex est rétabli à l'identique, depuis la corbeille des
     documents supprimés ;
  4. la fiche de suivi oublie le retour ; la mauvaise photo ne reste jointe nulle part sur ce colis.
La carte du colis réapparaît alors dans Livraisons Aramex avec « 📦 Retour reçu » et son bordereau de retour
(55087946126) : il reste à photographier le VRAI colis, arrivé le 19/11 — la photo sera vérifiée cette fois.

⚠️ NE PEUT PAS BLOQUER LE DÉPLOIEMENT : l'état attendu est vérifié avant de toucher à quoi que ce soit, tout se fait
dans un point de reprise, et une erreur défait tout et s'écrit dans le journal des erreurs (« Reprise Aramex du 19/11 »).
Le même enchaînement a été joué avec succès sur une copie de la production (dev, 19/11/2026).
"""
import json

import frappe

COMMANDE, BORDEREAU = "WEB1-008706", "55087538380"
PHOTO = "17950149860297132933301419325066ba7eb3.jpg"
TITRE = "Reprise Aramex du 19/11"


def _defaire():
    suivi = frappe.db.get_value("Suivi Aramex", BORDEREAU, ["retour_recu_le", "retour_bl", "retour_pe", "commande"],
                                as_dict=True)
    if not suivi or not suivi.retour_recu_le:
        return "retour non constaté : rien à défaire"
    if suivi.commande and suivi.commande != COMMANDE:
        return "le bordereau ne porte pas la commande attendue : rien n'est touché"
    paiement = (suivi.retour_pe or "").strip()
    bls = [b.strip() for b in (suivi.retour_bl or "").split(",") if b.strip()]
    if not paiement or not bls:
        return "constat sans BL de retour ou sans paiement connu : rien n'est touché"
    if frappe.db.exists("Payment Entry", paiement):
        return "le paiement %s existe déjà : rien n'est touché" % paiement
    corbeille = frappe.get_all("Deleted Document", filters={"deleted_name": paiement, "deleted_doctype": "Payment Entry"},
                               fields=["name", "data"], order_by="creation desc", limit=1)
    if not corbeille:
        return "le paiement %s n'est pas dans la corbeille : rien n'est touché" % paiement
    for bl in bls:
        etat = frappe.db.get_value("Delivery Note", bl, ["docstatus", "is_return"], as_dict=True)
        if not etat or not etat.is_return or etat.docstatus not in (1, 2):
            return "le BL de retour %s n'est pas dans l'état attendu : rien n'est touché" % bl
    if frappe.db.sql("""select 1 from `tabSales Invoice Item` sii join `tabSales Invoice` si on si.name = sii.parent
                        where sii.sales_order = %s and si.docstatus = 1 limit 1""", COMMANDE):
        return "la commande est facturée : à traiter à la main"

    drapeau = frappe.flags.in_import
    frappe.flags.in_import = True          # le même drapeau que la validation du BL de retour par l'application
    try:
        # 1. La commande se rouvre (sinon le paiement ne peut pas s'y rattacher).
        so = frappe.get_doc("Sales Order", COMMANDE)
        if so.status == "Closed":
            so.update_status("Draft")
        frappe.db.set_value("Sales Order", COMMANDE, "custom_retour_colis", 0, update_modified=False)

        # 2. Le BL de retour disparaît : le stock ressort, le colis n'était pas encore là.
        for bl in bls:
            doc = frappe.get_doc("Delivery Note", bl)
            doc.flags.ignore_permissions = True
            if doc.docstatus == 1:
                doc.cancel()
            frappe.delete_doc("Delivery Note", bl, force=True, ignore_permissions=True)

        # 3. Le paiement « Dette non payée » revient, à l'identique.
        donnees = json.loads(corbeille[0].data)
        for cle in ("docstatus", "amended_from", "_user_tags", "_comments", "_assign", "_liked_by", "__last_sync_on"):
            donnees.pop(cle, None)
        for table in ("references", "deductions", "taxes"):
            for ligne in donnees.get(table) or []:
                for cle in ("name", "docstatus", "creation", "modified", "owner", "modified_by"):
                    ligne.pop(cle, None)
        pe = frappe.get_doc(donnees)
        pe.docstatus = 0
        pe.flags.ignore_permissions = True
        pe.insert(set_name=paiement)
        pe.submit()
    finally:
        frappe.flags.in_import = drapeau

    # 4. La fiche de suivi oublie le retour ; la mauvaise photo ne reste pas sur ce colis.
    frappe.db.set_value("Suivi Aramex", BORDEREAU, {"retour_recu_le": None, "retour_par": None, "retour_bl": None,
                                                   "retour_pe": None, "retour_montant": 0, "retour_note": None},
                        update_modified=False)
    ailleurs = frappe.db.exists("File", {"file_name": PHOTO, "attached_to_doctype": "Echange Aramex"})
    for f in frappe.get_all("File", filters={"file_name": PHOTO, "attached_to_name": ["in", [COMMANDE, BORDEREAU]]},
                            fields=["name"]):
        if ailleurs:
            frappe.delete_doc("File", f.name, force=True, ignore_permissions=True)   # le fichier reste : l'échange le porte
        else:
            frappe.db.delete("File", {"name": f.name})                                 # pièce jointe retirée, fichier gardé
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info", "reference_doctype": "Sales Order", "reference_name": COMMANDE,
        "content": ("↩️ Constat de retour du 18/11/2026 DÉFAIT le 19/11/2026 : il avait été fait avec la photo d'un autre "
                    "colis (la pochette d'un échange), un jour avant l'arrivée du vrai. BL de retour %s supprimé, "
                    "paiement %s rétabli, commande rouverte. Le colis (bordereau de retour 55087946126, livré chez "
                    "nous le 19/11/2026 à 15:52 selon Aramex) attend de nouveau « 📦 Retour reçu » dans Livraisons "
                    "Aramex : photographier le vrai colis, la photo est vérifiée.") % (", ".join(bls), paiement),
    }).insert(ignore_permissions=True)
    return "constat défait : BL %s supprimé, paiement %s rétabli" % (", ".join(bls), paiement)


def execute():
    point = "defaire_retour_nabil"
    frappe.db.savepoint(point)
    try:
        print("   retour Nabil Inoubli : %s" % _defaire())
    except Exception:
        frappe.db.rollback(save_point=point)
        frappe.log_error(title=TITRE, message="defaire_retour_nabil_inoubli a échoué — rien n'a été modifié.\n\n%s"
                         % frappe.get_traceback())
        print("   retour Nabil Inoubli : ÉCHEC (voir le journal des erreurs)")
