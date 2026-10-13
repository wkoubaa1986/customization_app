"""Suppression d'une tâche de travail : les simples renvois ne bloquent plus.

LE GESTE MÉTIER
---------------
Une tâche créée par erreur (mauvais client, doublon) se supprime. Frappe refusait
dès qu'un autre document la désignait — le plus souvent la ligne de la liste
d'appels qui a pris le rendez-vous (« Impossible de supprimer… associé à Liste
Appelle Entretien LIST-10-10-2026-00003 », Salma, 13/10/2026). La liste étant
validée, personne ne pouvait retirer ce lien à la main.

CE QUI EST LIBÉRÉ, ET CE QUI BLOQUE TOUJOURS
--------------------------------------------
Libérés (le lien est vidé, rien d'autre ne bouge) : les documents qui ne font
que DÉSIGNER la tâche — ligne d'appel, message GPS au client, tournée et visite
commerciales. Le « Rendez-vous pris » de la ligne d'appel reste : c'est
l'historique de l'appel, pas l'état du rendez-vous.

Les lignes « Intervention » (Mes Interventions Employe) sont, elles, SUPPRIMÉES
par le Server Script « Ignore links » (Before Delete, hors fixtures) — inchangé.

Bloquants, volontairement : ceux qui portent un TRAVAIL rattaché à la tâche —
dossier de réparation, vérification de stock, pointage RH. Supprimer la tâche
les rendrait orphelins sans que personne ne le voie.

`on_trash` tourne AVANT le contrôle des liens de Frappe (comme
`annulation_commande.on_trash_sales_order`) : c'est ce qui permet de libérer
avant que la suppression ne soit refusée.
"""
import frappe
from frappe import _

#: (doctype, champ) des documents qui ne font que désigner la tâche.
RENVOIS = (
    ("Appelle Client", "tache_de_travail"),
    ("Message Client GPS", "tache"),
    ("Tournee Commerciale", "tache_de_travail"),
    ("Visite Commerciale", "tache_relance"),
)

#: Journaux techniques : le lien est vidé sans commentaire (un par SMS serait du bruit).
SANS_TRACE = {"Message Client GPS"}


def on_trash_tache(doc, method=None):
    """Tache de travail on_trash : vide les renvois avant le contrôle des liens."""
    for dt, champ in RENVOIS:
        if not frappe.db.table_exists(dt) or not frappe.db.has_column(dt, champ):
            continue
        meta = frappe.get_meta(dt)
        colonnes = ["name"] + (["parent", "parenttype"] if meta.istable else [])
        for ligne in frappe.get_all(dt, filters={champ: doc.name}, fields=colonnes):
            # Écriture directe : la liste d'appels est VALIDÉE, un save() refuserait.
            frappe.db.set_value(dt, ligne.name, champ, None, update_modified=False)
            if dt in SANS_TRACE:
                continue
            porteur = (ligne.parenttype, ligne.parent) if meta.istable else (dt, ligne.name)
            if porteur[0] and porteur[1]:
                frappe.get_doc({
                    "doctype": "Comment", "comment_type": "Info",
                    "reference_doctype": porteur[0], "reference_name": porteur[1],
                    "content": _("Tâche {0} ({1}) supprimée par {2} : lien retiré.").format(
                        doc.name, _libelle(doc), frappe.session.user),
                }).insert(ignore_permissions=True)


def _libelle(doc) -> str:
    """Le titre de la tâche sur une ligne (il en compte souvent trois : secteur, type, employé)."""
    titre = doc.get("titre") or doc.get("subject") or doc.get("type_intervention") or ""
    lignes = [l.strip() for l in str(titre).splitlines() if l.strip()]
    return " — ".join(lignes)[:140]
