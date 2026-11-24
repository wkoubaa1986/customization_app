"""Accord préalable des dépenses en espèces au-delà d'un seuil (demande du 24/11/2026).

Au-delà de « Accord préalable au-delà de » (Config Caisse, 200 DT par défaut, 0 = désactivé), une dépense dont la
part ESPÈCES dépasse le seuil ne s'enregistre qu'avec l'accord d'un responsable de collecte (Config Caisse :
titulaire et co-titulaires). Le circuit :
  1. l'employé saisit sa dépense ; `caisse_depenses.creer` la refuse (`AccordDepenseRequis`) faute d'accord ;
  2. la page propose « Demander l'accord » : une fiche « Accord Depense Caisse » En attente, les responsables
     sont prévenus et voient une barre en haut de l'écran (public/js/caisse_rappel.js) ;
  3. un responsable accorde ou refuse (commentaire obligatoire pour refuser) ; le demandeur est prévenu ;
  4. l'employé ressaisit sa dépense : l'accord est consommé (« Utilisé », lié à l'écriture).
Les responsables eux-mêmes n'ont pas besoin d'accord. Un accord non utilisé vaut 7 jours. Les corrections d'une
dépense déjà saisie (`corriger`) ne redemandent rien.
"""
import frappe
from frappe import _
from frappe.utils import add_days, flt, now_datetime

DOCTYPE = "Accord Depense Caisse"
EN_ATTENTE, ACCORDE, REFUSE, UTILISE, ANNULE = "En attente", "Accordé", "Refusé", "Utilisé", "Annulé"
VALIDITE_JOURS = 7
COMMENTAIRE_MIN = 10
EVENEMENT = "caisse_accord"


class AccordDepenseRequis(frappe.ValidationError):
    """Levée par `exiger` : la page reconnaît ce nom d'exception et propose de demander l'accord."""


def _fmt(v) -> str:
    return "{:,.3f}".format(flt(v)).replace(",", " ").replace(".", ",")


def approbateurs() -> list:
    from customization_app import caisse_collecte as CC
    return [u for u in CC.config()["responsables"] if u]


def doit_demander(part_especes, seuil, est_approbateur) -> bool:
    """Fonction pure : un accord est-il nécessaire ?"""
    return bool(seuil) and not est_approbateur and flt(part_especes) > flt(seuil) + 0.0005


def _accord_disponible(user, montant):
    """Le plus ancien accord encore valable du demandeur qui couvre ce montant."""
    for a in frappe.get_all(DOCTYPE, filters={"demandeur": user, "statut": ACCORDE,
                                              "decide_le": [">=", add_days(now_datetime(), -VALIDITE_JOURS)]},
                            fields=["name", "montant"], order_by="decide_le asc"):
        if flt(a.montant) + 0.0005 >= flt(montant):
            return a.name
    return None


def exiger(part_especes, user=None):
    """Dans `creer`, avant toute écriture : le nom de l'accord qui couvre la dépense, None s'il n'en faut pas ; lève
    `AccordDepenseRequis` sinon."""
    from customization_app.caisse_revue import seuil_accord
    user = user or frappe.session.user
    seuil = seuil_accord()
    if not doit_demander(part_especes, seuil, user in approbateurs() or user == "Administrator"):
        return None
    nom = _accord_disponible(user, part_especes)
    if nom:
        return nom
    frappe.throw(_("Dépense de {0} DT en espèces : au-delà de {1} DT, il faut d'abord l'accord d'un responsable "
                   "({2}).").format(_fmt(part_especes), _fmt(seuil),
                                    ", ".join(frappe.utils.get_fullname(u) for u in approbateurs())),
                 exc=AccordDepenseRequis, title=_("Accord préalable nécessaire"))


def utiliser(nom, piece):
    if nom:
        frappe.db.set_value(DOCTYPE, nom, {"statut": UTILISE, "journal_entry": piece, "utilise_le": now_datetime()},
                            update_modified=True)


def _prevenir(user, sujet, nom):
    from customization_app import caisse_collecte as CC
    CC._prevenir(user, sujet, DOCTYPE, nom)
    try:
        frappe.publish_realtime(EVENEMENT, {"accord": nom}, user=user, after_commit=True)
    except Exception:
        pass


@frappe.whitelist()
def demander(montant, objet, fournisseur=None, caisse=None):
    """L'employé demande l'accord. Une demande en attente du même montant n'est pas dupliquée."""
    from customization_app import caisse_cloture as CL
    from customization_app import caisse_depenses as D
    frappe.only_for(CL.ROLES)
    montant = flt(montant, 3)
    objet = " ".join((objet or "").split())
    if montant <= 0:
        frappe.throw(_("Le montant doit être positif."))
    if len(objet) < 3:
        frappe.throw(_("Dites en quelques mots ce que vous allez payer."))
    moi = frappe.session.user
    caisse = D.caisse_payeuse_valide(caisse) or D.caisse_de_l_utilisateur(moi)
    deja = frappe.db.get_value(DOCTYPE, {"demandeur": moi, "statut": EN_ATTENTE, "montant": montant}, "name")
    if deja:
        return {"name": deja, "deja": True, "approbateurs": [frappe.utils.get_fullname(u) for u in approbateurs()]}
    doc = frappe.get_doc({"doctype": DOCTYPE, "statut": EN_ATTENTE, "demandeur": moi,
                          "demandeur_nom": frappe.utils.get_fullname(moi), "caisse": caisse,
                          "demande_le": now_datetime(), "montant": montant, "objet": objet,
                          "fournisseur": (fournisseur or "").strip()}).insert(ignore_permissions=True)
    for u in approbateurs():
        if u != moi:
            _prevenir(u, "💰 %s demande l'accord pour %s DT en espèces : %s" % (
                doc.demandeur_nom, _fmt(montant), objet), doc.name)
    frappe.db.commit()
    return {"name": doc.name, "deja": False, "approbateurs": [frappe.utils.get_fullname(u) for u in approbateurs()]}


@frappe.whitelist()
def a_decider():
    """Pour la carte des responsables : les demandes en attente. None pour les autres (la page cesse d'interroger)."""
    if frappe.session.user not in approbateurs():
        return None
    return frappe.get_all(DOCTYPE, filters={"statut": EN_ATTENTE},
                          fields=["name", "demandeur_nom", "caisse", "montant", "objet", "fournisseur", "demande_le"],
                          order_by="demande_le asc")


@frappe.whitelist()
def decider(name, accorder, commentaire=None):
    """Un responsable accorde ou refuse (commentaire obligatoire pour refuser)."""
    moi = frappe.session.user
    if moi not in approbateurs():
        frappe.throw(_("Seuls les responsables de collecte décident des accords de dépense."), frappe.PermissionError)
    doc = frappe.get_doc(DOCTYPE, name)
    if doc.statut != EN_ATTENTE:
        frappe.throw(_("Cette demande est déjà « {0} » ({1}).").format(
            doc.statut, frappe.utils.get_fullname(doc.decide_par) if doc.decide_par else "?"))
    accorder = frappe.utils.cint(accorder)
    commentaire = " ".join((commentaire or "").split())
    if not accorder and len(commentaire) < COMMENTAIRE_MIN:
        frappe.throw(_("Dites pourquoi vous refusez ({0} caractères minimum).").format(COMMENTAIRE_MIN))
    doc.db_set({"statut": ACCORDE if accorder else REFUSE, "decide_par": moi, "decide_le": now_datetime(),
                "commentaire": commentaire})
    _prevenir(doc.demandeur, "%s %s votre dépense de %s DT (%s)%s" % (
        "✅" if accorder else "❌", "%s accorde" % frappe.utils.get_fullname(moi) if accorder
        else "%s refuse" % frappe.utils.get_fullname(moi), _fmt(doc.montant), doc.objet,
        (" — " + commentaire) if commentaire else "")
        + (" : vous pouvez la saisir." if accorder else ""), doc.name)
    frappe.db.commit()
    return {"name": doc.name, "statut": doc.statut}


@frappe.whitelist()
def mes_accords():
    """Pour la fenêtre de dépense : les demandes récentes de l'utilisateur (en attente, accordées non utilisées)."""
    return frappe.get_all(DOCTYPE, filters={"demandeur": frappe.session.user, "statut": ["in", [EN_ATTENTE, ACCORDE]],
                                            "demande_le": [">=", add_days(now_datetime(), -VALIDITE_JOURS)]},
                          fields=["name", "statut", "montant", "objet", "decide_par", "commentaire"],
                          order_by="demande_le desc")
