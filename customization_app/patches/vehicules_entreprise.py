"""Véhicules de l'entreprise : une case sur la fiche Véhicule, cochée pour les deux voitures de la société.

Demande du 22/11/2026 : dans « Générer les BL », la liste « Véhicule » proposait les 25 véhicules enregistrés (ceux
des clients et des transporteurs compris) — « mes voitures d'entreprise sont la Cenntro et la Fiat Fiorino, est-ce que
tu peux shortlister les voitures pour la sélection ? ». La case « Véhicule de l'entreprise » les fait passer en tête
de la liste ; les autres restent proposés dessous. Elle se coche ou se décoche ensuite à la main sur la fiche.

Le champ est aussi dans fixtures/custom_field.json ; on le crée ici parce que les patchs passent AVANT les fixtures.
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_field

CHAMP = "custom_vehicule_entreprise"
#: les deux voitures de la société, par leur immatriculation (le nom de la fiche Véhicule)
VEHICULES = ("3554 TU 261",      # Cenntro LS 100
             "232 TU 5957")      # Fiat Fiorino

DEFINITION = {
    "fieldname": CHAMP, "label": "Véhicule de l'entreprise", "fieldtype": "Check", "insert_after": "model", "default": "0",
    "in_list_view": 1, "in_standard_filter": 1, "module": "Customize erpnext",
    "description": "Proposé en tête de la liste « Véhicule » de la fenêtre Générer les BL.",
}


def a_cocher(existants, vehicules=VEHICULES) -> list:
    """Parmi les voitures de la société, celles dont la fiche existe. PURE."""
    connus = set(existants)
    return [v for v in vehicules if v in connus]


def execute():
    if not frappe.db.exists("DocType", "Vehicle"):
        return
    if not frappe.get_meta("Vehicle").has_field(CHAMP):
        create_custom_field("Vehicle", dict(DEFINITION))
        frappe.clear_cache(doctype="Vehicle")
    for nom in a_cocher(frappe.get_all("Vehicle", pluck="name")):
        frappe.db.set_value("Vehicle", nom, CHAMP, 1, update_modified=False)
