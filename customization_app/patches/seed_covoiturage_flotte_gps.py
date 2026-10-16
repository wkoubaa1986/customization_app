"""Équipages par défaut de l'onglet Présence Aquaworld (demande du 16/10/2026 : « Jamel et Sadok partent ensemble,
Mohamed et Akram partent ensemble »). Posé une fois si le réglage est vide ; ensuite Config Flotte GPS fait foi."""

import frappe

DEFAUT = "Sadok Bouziri + Jamel Aloui\nAkram + Mohamed Hedi Chouchane"


def execute():
    frappe.reload_doc("customize_erpnext", "doctype", "config_flotte_gps")      # Single : pas de table à interroger
    if not (frappe.db.get_single_value("Config Flotte GPS", "covoiturage") or "").strip():
        frappe.db.set_single_value("Config Flotte GPS", "covoiturage", DEFAUT)
