"""
Suivi des appels de confirmation sur les commandes WEB.

Une commande WooCommerce n'est expédiée qu'après confirmation téléphonique du
client. Deux boutons du formulaire enregistrent un appel resté sans réponse,
chacun une seule fois et horodaté, ce qui permet de savoir combien de
tentatives ont eu lieu avant d'annuler.

Le rang 2 exige le rang 1 : le décompte reste ainsi cohérent.

Discriminant WEB : la présence de `woocommerce_id`. Vérifié en base — les 270
commandes WEB en ont un, aucune commande saisie au Desk n'en a. Plus fiable que
le préfixe du nom, qui n'est qu'une convention de nommage.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime

CHAMPS = {
    1: "custom_appel_1_sans_reponse",
    2: "custom_appel_2_sans_reponse",
}

LIBELLES = {1: "1er appel sans réponse", 2: "2e appel sans réponse"}


def est_commande_web(nom):
    return bool(frappe.db.get_value("Sales Order", nom, "woocommerce_id"))


@frappe.whitelist()
def enregistrer_appel(commande, rang):
    """
    Horodate un appel resté sans réponse. Irréversible depuis l'interface.

    Refuse : une commande hors WEB, un rang déjà renseigné, ou un rang 2 sans
    rang 1 — les trois garde-fous du bouton, revérifiés côté serveur puisque
    l'appel est whitelisté.
    """
    rang = int(rang)
    if rang not in CHAMPS:
        frappe.throw(_("Rang d'appel invalide."))

    frappe.has_permission("Sales Order", "write", doc=commande, throw=True)

    if not est_commande_web(commande):
        frappe.throw(_("Le suivi des appels ne concerne que les commandes WEB."))

    valeurs = frappe.db.get_value(
        "Sales Order", commande, list(CHAMPS.values()), as_dict=True
    )
    if not valeurs:
        frappe.throw(_("Commande introuvable."))

    if valeurs.get(CHAMPS[rang]):
        frappe.throw(_("Le {0} est déjà enregistré.").format(LIBELLES[rang]))

    if rang == 2 and not valeurs.get(CHAMPS[1]):
        frappe.throw(_("Enregistrez d'abord le {0}.").format(LIBELLES[1]))

    horodatage = now_datetime()
    # db_set plutôt qu'un save : ces commandes sont soumises, et l'on ne veut ni
    # déclencher les hooks de mise à jour ni toucher au champ modified.
    frappe.db.set_value(
        "Sales Order", commande, CHAMPS[rang], horodatage, update_modified=False
    )
    frappe.db.commit()

    return {"champ": CHAMPS[rang], "horodatage": horodatage}


def nb_appels(valeurs):
    """Nombre d'appels enregistrés à partir d'un dict de valeurs de champs."""
    return sum(1 for champ in CHAMPS.values() if valeurs.get(champ))
