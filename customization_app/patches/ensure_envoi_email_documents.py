"""Tous les employés peuvent envoyer par e-mail devis, factures, commandes, BL et tâches depuis le système (02/11/2026).

Le bouton « E-mail » d'une fiche exige le droit « email » du rôle sur le doctype (et « print » pour joindre le PDF).
Ici, chaque rôle qui LIT un de ces documents reçoit ces deux droits — sauf les rôles externes : Partenaire,
Sous-traitant et Contractor (ils gardent exactement ce qu'ils ont). Devis (Quotation) n'avait pas de Custom DocPerm :
Frappe les crée d'abord à partir des permissions standard (sinon la première ligne personnalisée effacerait les autres).
Idempotent : un droit déjà accordé n'est pas retouché.
"""
import frappe
from frappe.permissions import setup_custom_perms, update_permission_property

DOCTYPES = ("Quotation", "Sales Invoice", "Sales Order", "Delivery Note", "Tache de travail")
ROLES_EXTERNES = {"Partenaire", "Sous-traitant", "Contractor", "Guest", "All"}


def execute():
    accordes = []
    for dt in DOCTYPES:
        if not frappe.db.exists("DocType", dt):
            continue
        if not frappe.db.exists("Custom DocPerm", {"parent": dt}):
            setup_custom_perms(dt)
        for p in frappe.get_all("Custom DocPerm", filters={"parent": dt, "permlevel": 0, "read": 1},
                                fields=["name", "role", "email", "print"]):
            if p.role in ROLES_EXTERNES:
                continue
            for droit in ("email", "print"):
                if not p.get(droit):
                    update_permission_property(dt, p.role, 0, droit, 1, validate=False)
                    accordes.append((dt, p.role, droit))
    if accordes:
        frappe.clear_cache()
        frappe.db.commit()
    return accordes
