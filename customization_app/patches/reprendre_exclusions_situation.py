"""Situation mensuelle : les pièces exclues « en dur » dans le Script Report « Test » entrent dans le
registre « Exclusion Situation » (omission totale), pour que la nouvelle page donne exactement les
mêmes chiffres et que ces exclusions deviennent visibles et réversibles. Idempotent.

Le rapport retirait 13 écritures de toutes les charges (Coût de la marchandise + Charges) et 4
d'entre elles de la TVA achat. Une omission n'est créée que dans les rubriques où l'écriture
compte réellement.
"""

import frappe

MOTIF = "Repris de l'ancien rapport « Test » (exclusion écrite dans son code)."
EXCLUES_CHARGES = ("ACC-JV-2024-00037", "ACC-JV-2024-00332", "ACC-JV-2024-00331", "ACC-JV-2024-00329",
                   "ACC-JV-2024-00378", "ACC-JV-2024-00574", "ACC-JV-2025-00470", "ACC-JV-2025-00619",
                   "ACC-JV-2025-00783", "ACC-JV-2025-00851", "ACC-JV-2026-00373", "ACC-JV-2026-00372",
                   "ACC-JV-2026-00460")
EXCLUES_TVA = ("ACC-JV-2024-00037", "ACC-JV-2024-00574", "ACC-JV-2025-00470", "ACC-JV-2025-00619")


def execute():
    if not frappe.db.exists("DocType", "Exclusion Situation"):
        return
    from customization_app.situation_mensuelle import RUBRIQUES, comptes
    for rubriques, ecritures in ((("Coût de la marchandise", "Charges"), EXCLUES_CHARGES), (("TVA Achat",), EXCLUES_TVA)):
        for rubrique in rubriques:
            cpt = comptes(rubrique)
            debit_seul = RUBRIQUES[rubrique][1]
            for je in ecritures:
                if frappe.db.exists("Exclusion Situation", {"rubrique": rubrique, "voucher_type": "Journal Entry", "voucher_no": je}):
                    continue
                r = frappe.db.sql("""select min(posting_date), sum(debit), sum(credit) from `tabGL Entry`
                                     where account in %s and voucher_type = 'Journal Entry' and voucher_no = %s
                                       and is_cancelled = 0 and is_opening = 'No'""", (tuple(cpt), je))[0]
                if r[0] is None:
                    continue
                contribution = (r[1] or 0) if debit_seul else (r[1] or 0) - (r[2] or 0)
                frappe.get_doc({"doctype": "Exclusion Situation", "rubrique": rubrique, "voucher_type": "Journal Entry",
                                "voucher_no": je, "mode": "Totale", "montant_exclu": abs(contribution),
                                "montant_piece": contribution, "date_piece": r[0], "motif": MOTIF}).insert(ignore_permissions=True)
    frappe.db.commit()
