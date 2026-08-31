"""Le partenaire termine SON intervention de bout en bout.

Demande 31/08/2026 : quand Economiq Aqua Solutions exécute une intervention, il
doit pouvoir aller au bout seul — valider la commande liée à sa tâche, valider
le bon de livraison de cette commande, et clôturer la tâche.

POURQUOI UN POINT D'ENTRÉE ET NON DES RÔLES. Lui donner « Sales User » et
« Stock User » lui ouvrirait TOUTES les commandes et TOUS les bons de livraison
de la société. Ici il ne gagne qu'un pouvoir : mener au bout la tâche qui lui
est affectée. L'appartenance est vérifiée AVANT toute élévation, et l'élévation
ne porte que sur les documents de cette tâche.

CE QUI N'EST PAS CONTOURNÉ. Les règles de clôture (photos, position GPS, compte
rendu, code superviseur) restent entières : la tâche est passée à « Completed »
par un `save()` normal, donc `verifier_photos_cloture` s'exécute comme pour
n'importe qui. Une clôture partenaire qui sauterait ces contrôles serait une
porte dérobée.

L'ORDRE : commande, puis bon de livraison, puis tâche. La tâche se ferme en
DERNIER — si une étape échoue, elle reste ouverte et l'opération est rejouable,
au lieu de laisser une intervention close derrière un BL manquant.
"""
from __future__ import annotations

from contextlib import contextmanager

import frappe
from frappe import _

DOCTYPE_TACHE = "Tache de travail"


def _employe_de_l_utilisateur():
    return frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")


def _ma_tache(nom):
    """La tâche DOIT être celle de l'utilisateur connecté — sinon rien.

    C'est la seule barrière avant l'élévation de droits : elle passe donc par
    le champ d'affectation, pas par un partage qui pourrait être large.
    """
    doc = frappe.get_doc(DOCTYPE_TACHE, nom)
    employe = _employe_de_l_utilisateur()
    if not employe or doc.custom_choix_du_staff != employe:
        frappe.throw(_("Cette intervention ne vous est pas affectée."),
                     frappe.PermissionError)
    if doc.status == "Completed":
        frappe.throw(_("Cette intervention est déjà terminée."))
    if doc.status == "Cancelled":
        frappe.throw(_("Cette intervention est annulée."))
    return doc


@contextmanager
def _en_systeme():
    """Exécute la chaîne commande/BL sous l'identité système, puis rend la main.

    Pourquoi c'est nécessaire : valider une commande déclenche un Server Script
    qui crée un Payment Entry, lequel lit le plan de comptes. `frappe.has_permission`
    ne consulte NI le drapeau `ignore_permissions` du document NI le drapeau global :
    seul un changement d'identité passe. Constaté le 31/08 — PermissionError sans
    message, levée au fond d'ERPNext.

    Pourquoi c'est sûr : l'appartenance de la tâche à l'utilisateur RÉEL est
    vérifiée AVANT d'entrer ici, et on ne touche qu'aux documents de cette tâche.
    La fermeture de la tâche, elle, se fait hors de ce bloc — sous l'identité
    réelle, pour que la trace dise qui a clôturé.
    """
    reel = frappe.session.user
    frappe.set_user("Administrator")
    try:
        yield
    finally:
        frappe.set_user(reel)


def _valider_commande(nom):
    """Soumet la commande si elle est encore en brouillon. -> (nom, message)"""
    so = frappe.get_doc("Sales Order", nom)
    if so.docstatus == 1:
        return so.name, "déjà validée"
    if so.docstatus == 2:
        frappe.throw(_("La commande {0} est annulée.").format(nom))
    so.flags.ignore_permissions = True
    try:
        so.submit()
    except frappe.MandatoryError as e:
        # Le partenaire ne peut pas deviner un mode de paiement ni un champ
        # métier manquant : on lui dit quoi, et à qui s'adresser, plutôt que de
        # lui renvoyer l'erreur brute d'ERPNext.
        frappe.throw(_("La commande {0} est incomplète et ne peut pas être "
                       "validée : {1}. Demandez au magasin de la compléter.")
                     .format(nom, str(e).split(":")[-1].strip()[:120]))
    frappe.db.commit()
    return so.name, "validée"


def _bon_de_livraison(tache, commande):
    """Crée le BL de la commande s'il n'existe pas, puis le valide.

    On réutilise `generer_bl` — le même chemin que le bouton « Générer BL » du
    magasin, y compris ses règles de dépôt, de livreur et de véhicule. Deux
    fabriques de BL divergeraient au premier changement.
    """
    from customization_app import generer_bl as G

    nom = G._get_dn_for_so(commande)
    cree = False
    if not nom:
        nom = G._create_dn_from_so(
            so_name=commande,
            date=frappe.utils.nowdate(),
            employee=tache.custom_choix_du_staff,
            vehicle=None)
        cree = True
    if not nom:
        frappe.throw(_("Impossible de créer le bon de livraison de {0}.").format(commande))

    dn = frappe.get_doc("Delivery Note", nom)
    if dn.docstatus == 1:
        return dn.name, "déjà validé"
    if dn.docstatus == 2:
        frappe.throw(_("Le bon de livraison {0} est annulé.").format(nom))
    dn.flags.ignore_permissions = True
    try:
        dn.submit()
    except frappe.MandatoryError as e:
        frappe.throw(_("Le bon de livraison {0} est incomplet : {1}. "
                       "Demandez au magasin de le compléter.")
                     .format(dn.name, str(e).split(":")[-1].strip()[:120]))
    frappe.db.commit()
    return dn.name, ("créé et validé" if cree else "validé")


def _verifier_photos_et_position(doc):
    """Les exigences qui NE dépendent pas des documents liés : photos et GPS."""
    from customization_app import cloture_tache as C

    if frappe.utils.cint(doc.get("dispense_photos")) or not C.regle_active():
        return
    if C.gmap_requis(doc) and not (doc.get("google_map") or "").strip():
        frappe.throw(_("📍 Le lien Google Map est obligatoire pour clôturer une "
                       "tâche {0} : utilisez le bouton « 📍 Ma position actuelle », "
                       "ou un code superviseur.")
                     .format(doc.get("custom_type_dintervention")))

    exigence = C.exigence_du_doc(doc)
    if not exigence:
        return
    manques = [cle for cle in ("avant", "apres")
               if C._nb_photos(doc.get(C.CHAMPS[cle])) < exigence[cle]]
    if manques:
        frappe.throw(_("📷 Photos obligatoires avant de clôturer cette tâche "
                       "({0}) :").format(doc.get("custom_type_dintervention"))
                     + "<br>" + "<br>".join("• %s" % s["label"]
                                            for s in exigence["slots"])
                     + "<br><br>" + _("Utilisez « 📷 Photos de clôture », ou un "
                                      "code superviseur."))


@frappe.whitelist()
def peut_cloturer(tache) -> dict:
    """Ce que l'écran a besoin de savoir pour afficher le bouton, sans agir.

    Le point d'entrée est ouvert : sans ce contrôle de lecture, n'importe qui
    pourrait énumérer les tâches et apprendre quelle commande est liée à
    laquelle. La fonction ne modifie rien, mais elle RENSEIGNE.
    """
    doc = frappe.get_doc(DOCTYPE_TACHE, tache)
    frappe.has_permission(DOCTYPE_TACHE, "read", doc=doc, throw=True)
    employe = _employe_de_l_utilisateur()
    mienne = bool(employe) and doc.custom_choix_du_staff == employe
    commande = doc.commande_client
    return {
        "mienne": mienne,
        "statut": doc.status,
        "commande": commande,
        "commande_validee": bool(commande) and frappe.db.get_value(
            "Sales Order", commande, "docstatus") == 1,
        "bon_livraison": (frappe.db.get_value(
            "Delivery Note Item", {"against_sales_order": commande,
                                   "docstatus": ["<", 2]}, "parent")
            if commande else None),
    }


@frappe.whitelist()
def cloturer(tache, rapport_visite=None) -> dict:
    """Valide la commande, valide son bon de livraison, puis ferme la tâche."""
    doc = _ma_tache(tache)
    etapes = []

    # PHOTOS ET POSITION D'ABORD. Valider la commande et le bon de livraison
    # puis buter sur des photos manquantes laisserait le STOCK SORTI derrière une
    # intervention restée ouverte.
    # ⚠️ On ne rejoue PAS toute la validation de clôture ici : elle exige que la
    # commande et le BL soient DÉJÀ validés — précisément ce que cette fonction
    # est là pour faire. Ne pré-contrôler que ce qui ne dépend pas d'eux.
    _verifier_photos_et_position(doc)

    if doc.commande_client:
        with _en_systeme():
            nom, etat = _valider_commande(doc.commande_client)
            etapes.append({"quoi": "Commande", "doc": nom, "etat": etat})
            nom, etat = _bon_de_livraison(doc, doc.commande_client)
            etapes.append({"quoi": "Bon de livraison", "doc": nom, "etat": etat})
    else:
        # Sans commande, le BL de main d'œuvre reste produit par le mécanisme
        # existant (« Générer BL », modèle du type d'intervention) : on n'y
        # touche pas, on ferme seulement la tâche.
        etapes.append({"quoi": "Commande", "doc": "—",
                       "etat": "aucune commande liée : BL de main d'œuvre inchangé"})

    doc.reload()
    if rapport_visite:
        doc.rapport_visite = rapport_visite
    doc.status = "Completed"
    # PAS d'ignore_permissions ici : les contrôles de clôture (photos, GPS,
    # compte rendu, code superviseur) doivent s'appliquer au partenaire comme
    # à tout le monde.
    doc.save()
    frappe.db.commit()
    etapes.append({"quoi": "Intervention", "doc": doc.name, "etat": "terminée"})
    return {"etapes": etapes, "tache": doc.name}
