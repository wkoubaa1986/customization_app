"""Allocations « Récupération » : retirer le drapeau « report » (carry_forward) des allocations qui n'ont rien
reporté (19/10/2026).

Les règles de récupération planifiées jusqu'en 2028 ont créé des allocations 2027/2028 avec carry_forward=1 et
unused_leaves=0. HRMS refuse alors d'augmenter l'allocation 2026 (« Leave cannot be allocated before 01-01-2027, as
leave balance has already been carry-forwarded ») : impossible de poser une récupération en 2026 à Akram, Jamel,
Mohamed Hedi ou Sadok. Le drapeau ne portant aucun jour, on le retire en base, sans toucher au grand livre.
"""

import frappe


def execute():
    from customization_app.conges_recuperations import TYPE_RECUP
    if not frappe.db.exists("Leave Type", TYPE_RECUP):
        return
    noms = frappe.get_all("Leave Allocation", filters={"leave_type": TYPE_RECUP, "docstatus": 1, "carry_forward": 1,
                                                       "unused_leaves": ["<=", 0]}, pluck="name")
    for n in noms:
        frappe.db.set_value("Leave Allocation", n, "carry_forward", 0, update_modified=False)
    if noms:
        frappe.db.commit()
        print(f"[recup_allocations_sans_report] {len(noms)} allocation(s) sans report : {', '.join(noms)}")
