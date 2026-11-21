"""Espace « Calendrier » : le raccourci « Calendrier d'activité » ouvre le nouveau Calendrier d'équipe.

Demande du 22/11/2026 : « change le lien de mon calendrier vers le nouveau calendrier, patch ça pour la prod ».
L'espace « Calendrier » n'est pas dans les fixtures de l'application (il a été composé à la main en prod) : on change
donc l'adresse du raccourci en base, sans réenregistrer l'espace. Seule l'adresse change — le libellé, la place et la
façon de s'ouvrir restent. L'ancien calendrier reste accessible par le lien « Ancien calendrier » du nouveau.
Les raccourcis « Calendrier de travail » des espaces Appels et Sous-traitant ne sont pas touchés.
"""
import frappe

ESPACE = "Calendrier"
ANCIEN = "tache-de-travail/view/calendar"
NOUVEAU = "/app/calendrier-equipe"


def a_basculer(raccourcis: list) -> list:
    """Les raccourcis (nom, adresse) qui mènent à l'ancien calendrier. PURE."""
    return [nom for nom, url in raccourcis if ANCIEN in str(url or "")]


def execute():
    if not frappe.db.exists("Workspace", ESPACE) or not frappe.db.exists("Page", "calendrier-equipe"):
        return
    lignes = frappe.get_all("Workspace Shortcut", filters={"parent": ESPACE, "parenttype": "Workspace"}, fields=["name", "url"])
    for nom in a_basculer([(l.name, l.url) for l in lignes]):
        frappe.db.set_value("Workspace Shortcut", nom, "url", NOUVEAU, update_modified=False)
    frappe.clear_cache()
