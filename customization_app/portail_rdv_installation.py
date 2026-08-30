"""Convertir une LIVRAISON en INSTALLATION sur la commande du client.

Demande 30/08/2026 : le client a commandé en ligne avec une livraison Aramex ;
il préfère finalement que nos techniciens viennent installer. La commande doit
alors changer — la ligne « Livraison » cède la place à la main d'œuvre
d'installation — et le mode de règlement passe de « Livraison Aramex » à
« A la livraison » (on encaisse sur place).

DEUX PRINCIPES :

1. LE MONTANT CHANGE : on ne touche jamais à la commande sans avoir MONTRÉ le
   nouveau total et obtenu un « oui » explicite. `apercu()` calcule, `appliquer()`
   exécute — deux appels distincts, jamais un seul.

2. SEULEMENT SUR UN BROUILLON. Une commande déjà validée engage la comptabilité
   (facture, stock, échéancier) : la modifier depuis un chat client serait une
   porte ouverte. 128 des 140 commandes concernées sont des brouillons ; pour
   les autres, on renvoie vers le magasin.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

from customization_app.portail_rdv import LISTE_PRIX

ARTICLE_LIVRAISON = "Liv"
TERMES_INSTALLATION = "A la livraison"
# Installation Osmoseur domestique : le cas courant, et déjà l'article
# d'installation de référence du portail (ARTICLES_MO). Décision 30/08.
INSTALLATION_DEFAUT = "M-I-OD"
MODE_PAIEMENT_DEFAUT = "Espèces"       # le technicien encaisse sur place

# Quelle main d'œuvre pour quelle machine — même découpage que les échéanciers
# de maintenance (`MACHINE_FAMILY_BY_GROUP`), pour ne pas inventer un 2e modèle.
INSTALLATION_PAR_FAMILLE = {
    "RO_DOM": "M-I-OD",
    "RO_COM": "M-I-OC",
    "RO_IND": "M-I-OI",
    "ADOUCISSEUR": "M-I-Ad",
    "PF": "M-I-PF",
}


def _prix(article, liste=None):
    return flt(frappe.db.get_value(
        "Item Price", {"item_code": article, "selling": 1,
                       "price_list": liste or LISTE_PRIX}, "price_list_rate"))


def article_installation(doc):
    """L'article d'installation qui correspond à CE qui a été commandé.

    On regarde la famille de machine des articles de la commande ; à défaut
    d'une correspondance, « Installation Général ».
    """
    from customization_app.Maintenance.update_schedule import MACHINE_FAMILY_BY_GROUP

    for ligne in doc.items:
        groupe = frappe.db.get_value("Item", ligne.item_code, "item_group")
        famille = MACHINE_FAMILY_BY_GROUP.get(groupe)
        code = INSTALLATION_PAR_FAMILLE.get(famille)
        if code and frappe.db.exists("Item", code):
            return code
    return INSTALLATION_DEFAUT


def _commande_du_client(session, commande):
    doc = frappe.get_doc("Sales Order", commande)
    if doc.customer != session["client"]:
        frappe.throw(_("Cette commande n'est pas la vôtre."))
    return doc


def _lignes_livraison(doc):
    return [l for l in doc.items if l.item_code == ARTICLE_LIVRAISON]


def _echanger_lignes(doc, code, prix):
    """Retire la livraison, pose l'installation. Utilisé par l'aperçu (sur une
    COPIE) et par l'application (sur le vrai document) — un seul calcul, donc
    le montant annoncé est exactement celui qui sera enregistré."""
    doc.items = [l for l in doc.items if l.item_code != ARTICLE_LIVRAISON]
    doc.append("items", {
        "item_code": code,
        "qty": 1,
        "rate": prix,
        # getdate() : une date en TEXTE fait échouer la validation d'ERPNext
        # (comparaison str/date dans before_save).
        "delivery_date": frappe.utils.getdate(
            doc.delivery_date or frappe.utils.add_days(frappe.utils.nowdate(), 7)),
    })
    return doc


def apercu(session, commande):
    """Ce que deviendrait la commande — SANS rien modifier."""
    doc = _commande_du_client(session, commande)
    livraisons = _lignes_livraison(doc)
    if not livraisons:
        frappe.throw(_("Cette commande ne contient pas de ligne de livraison."))
    if doc.docstatus != 0:
        frappe.throw(_("Cette commande est déjà validée — appelez-nous, "
                       "nous la modifierons pour vous."))

    code = article_installation(doc)
    prix = _prix(code, doc.selling_price_list)
    retire = sum(flt(l.amount) for l in livraisons)
    # Le total est calculé par ERPNext lui-même sur une COPIE EN MÉMOIRE : une
    # estimation « au prorata de la TVA » se trompait de 7 DT (611 annoncés,
    # 619 appliqués) — annoncer un prix puis en appliquer un autre est le plus
    # sûr moyen de perdre la confiance du client.
    simule = frappe.get_doc(doc.as_dict())
    # Le client du portail est un INVITÉ : sans ce drapeau, `set_missing_values`
    # lit la fiche Client et Frappe refuse (« Guest n'a pas d'accès au document
    # Client », HTTP 403). ERPNext le prévoit — `_get_party_details` reçoit
    # `ignore_permissions=self.flags.ignore_permissions`. L'appartenance de la
    # commande au client de la session est vérifiée juste au-dessus.
    simule.flags.ignore_permissions = True
    _echanger_lignes(simule, code, prix)
    # set_missing_values AVANT le calcul, comme le fait validate() : sans lui la
    # nouvelle ligne n'a pas son modèle de taxe et le total simulé sortait HORS
    # TVA sur cette ligne (169 annoncés contre 178,5 réels).
    simule.run_method("set_missing_values")
    simule.run_method("calculate_taxes_and_totals")
    return {
        "commande": doc.name,
        "article_installation": code,
        "libelle_installation": frappe.db.get_value("Item", code, "item_name") or code,
        "prix_installation": prix,
        "livraison_retiree": retire,
        "ancien_total": flt(doc.grand_total, 3),
        "nouveau_total": flt(simule.grand_total, 3),
        "devise": doc.currency or "TND",
        "termes_actuels": doc.payment_terms_template,
        "termes_apres": TERMES_INSTALLATION,
    }


def appliquer(session, commande):
    """Remplace la livraison par l'installation, et change le mode de règlement.

    Le client a vu le nouveau montant et l'a confirmé : on écrit, on trace, et
    on lui envoie un e-mail avec le nouveau total — un changement de prix qui
    ne laisse aucune trace écrite, c'est une contestation garantie.
    """
    resume = apercu(session, commande)       # revalide TOUT (propriété, brouillon, ligne)
    doc = _commande_du_client(session, commande)

    code = resume["article_installation"]
    _echanger_lignes(doc, code, resume["prix_installation"])
    if frappe.db.exists("Payment Terms Template", TERMES_INSTALLATION):
        # ⚠️ Le mode de paiement est OBLIGATOIRE sur les lignes d'échéancier de
        # ce site. Vider l'échéancier et laisser ERPNext le régénérer produit
        # des lignes SANS mode -> MandatoryError en pleine conversation client.
        # On garde donc le mode déjà choisi, « Espèces » à défaut (le technicien
        # encaisse sur place puisqu'il se déplace).
        mode = next((l.mode_of_payment for l in doc.payment_schedule
                     if l.mode_of_payment), None) or MODE_PAIEMENT_DEFAUT
        doc.payment_terms_template = TERMES_INSTALLATION
        doc.payment_schedule = []
        doc.run_method("set_payment_schedule")
        for ligne in doc.payment_schedule:
            if not ligne.mode_of_payment:
                ligne.mode_of_payment = mode
    doc.flags.ignore_permissions = True
    doc.save()

    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info",
        "reference_doctype": "Sales Order", "reference_name": doc.name,
        "content": _("🔧 Livraison remplacée par l'installation ({0}) depuis le "
                     "portail, à la demande du client. Total : {1} → {2} {3}. "
                     "Règlement : {4}.").format(
            code, resume["ancien_total"], flt(doc.grand_total, 3),
            resume["devise"], doc.payment_terms_template or "—"),
    }).insert(ignore_permissions=True)
    frappe.db.commit()

    _informer(doc, resume)
    return {"commande": doc.name, "nouveau_total": flt(doc.grand_total, 3),
            "devise": doc.currency or "TND",
            "article_installation": code,
            "termes": doc.payment_terms_template}


def _informer(doc, resume):
    """E-mail au client avec le NOUVEAU MONTANT (demande 30/08)."""
    from customization_app.retenue_source import _coordonnees_des_contacts

    emails = list((_coordonnees_des_contacts([doc.customer]).get(doc.customer) or {})
                  .get("emails") or [])
    if doc.contact_email and doc.contact_email not in emails:
        emails.append(doc.contact_email)
    if not emails:
        return
    if frappe.utils.cint(frappe.conf.get("developer_mode")) \
            and not frappe.utils.cint(frappe.conf.get("sms_groupe_reel_en_dev")):
        frappe.logger("portail_rdv").info(
            "[SIMULÉ dev] e-mail installation -> %s" % ", ".join(emails))
        return
    try:
        frappe.sendmail(
            recipients=emails,
            subject=_("Votre commande {0} — installation par nos techniciens").format(doc.name),
            message=_("<p>Bonjour {0},</p>"
                      "<p>À votre demande, la livraison de votre commande <b>{1}</b> "
                      "est remplacée par une <b>installation par nos techniciens</b> "
                      "({2}).</p>"
                      "<p>Nouveau total : <b>{3} {4}</b> (au lieu de {5} {4}).<br>"
                      "Règlement : {6}.</p>"
                      "<p>Il ne vous reste qu'à choisir votre créneau sur "
                      "<a href=\"{7}\">notre page de rendez-vous</a>.</p>"
                      "<p>Aqua World &amp; Servicing</p>").format(
                          doc.customer_name or doc.customer, doc.name,
                          resume["libelle_installation"], flt(doc.grand_total, 3),
                          doc.currency or "TND", resume["ancien_total"],
                          doc.payment_terms_template or "—",
                          frappe.utils.get_url("/rdv")),
            reference_doctype="Sales Order", reference_name=doc.name)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Portail : e-mail installation")
