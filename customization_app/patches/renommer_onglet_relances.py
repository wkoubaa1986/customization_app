"""L'onglet « Relances et partenaire » devient « Relances, partenaires et réglages » (demande du 19/11/2026).

Un espace de travail public porte le MÊME texte dans son nom, son libellé et son titre : Frappe cherche l'onglet par
son titre dans la barre latérale et par son nom côté serveur — un titre seul changé donnerait un onglet qui ne s'ouvre
plus. D'où un vrai renommage, ici (patches.txt), donc AVANT l'import des fixtures : sans lui, la fixture renommée
créerait un second onglet à côté de l'ancien. Joué une fois ; sans effet si l'onglet porte déjà le nouveau nom.
"""
import frappe

ANCIEN = "Relances et partenaire"
NOUVEAU = "Relances, partenaires et réglages"


def execute():
    if not frappe.db.exists("Workspace", ANCIEN):
        return
    if frappe.db.exists("Workspace", NOUVEAU):
        # Les deux existent (fixture importée avant ce patch) : l'ancien est le doublon à retirer.
        frappe.delete_doc("Workspace", ANCIEN, force=True, ignore_permissions=True)
    else:
        frappe.rename_doc("Workspace", ANCIEN, NOUVEAU, force=True)
        frappe.db.set_value("Workspace", NOUVEAU, {"label": NOUVEAU, "title": NOUVEAU}, update_modified=False)
    for enfant in frappe.get_all("Workspace", filters={"parent_page": ANCIEN}, pluck="name"):
        frappe.db.set_value("Workspace", enfant, "parent_page", NOUVEAU, update_modified=False)
    # La liste des onglets est en cache PAR UTILISATEUR.
    frappe.clear_cache()
