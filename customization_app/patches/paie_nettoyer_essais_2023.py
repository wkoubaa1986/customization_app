"""Paie : les essais d'octobre 2023 (structures « CIVP » / « Gerant », leurs 3 affectations, un brouillon de fiche,
le barème « Impot nejib », la période « J » de 2023 à 2029) faisaient double emploi avec les structures « Paie CDI /
CIVP / Gérant » du 24/11/2026 — et la période « J » aurait déclenché le calcul d'impôt HRMS (indien) sur chaque fiche.
Rien n'est supprimé si une fiche VALIDÉE s'appuie sur ces essais."""
import frappe

ANCIENNES = ["CIVP", "Gerant"]


def _supprimer(doctype, name):
    doc = frappe.get_doc(doctype, name)
    doc.flags.ignore_permissions = True
    if doc.docstatus == 1:
        doc.cancel()
    frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)


def execute():
    structures = [s for s in ANCIENNES if frappe.db.exists("Salary Structure", s)]
    if structures and frappe.db.exists("Salary Slip", {"salary_structure": ["in", structures], "docstatus": 1}):
        return
    for nom in frappe.get_all("Salary Slip", filters={"salary_structure": ["in", structures or [""]], "docstatus": 0}, pluck="name"):
        _supprimer("Salary Slip", nom)
    for nom in frappe.get_all("Salary Structure Assignment", filters={"salary_structure": ["in", structures or [""]],
                                                                     "docstatus": ["<", 2]}, pluck="name"):
        _supprimer("Salary Structure Assignment", nom)
    for nom in frappe.get_all("Salary Structure Assignment", filters={"salary_structure": ["in", structures or [""]],
                                                                     "docstatus": 2}, pluck="name"):
        frappe.delete_doc("Salary Structure Assignment", nom, ignore_permissions=True, force=True)
    for s in structures:
        _supprimer("Salary Structure", s)
    if frappe.db.exists("Income Tax Slab", {"name": "Impot nejib", "docstatus": 0}):
        frappe.delete_doc("Income Tax Slab", "Impot nejib", ignore_permissions=True, force=True)
    if frappe.db.exists("Payroll Period", "J") and not frappe.db.exists("Salary Slip", {"docstatus": 1}):
        frappe.delete_doc("Payroll Period", "J", ignore_permissions=True, force=True)
