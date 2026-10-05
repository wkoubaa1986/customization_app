"""Réparations à venir ramenées à 75 min (décision du 05/10/2026 : une réparation dure 1 h 15 partout).

Les tâches Réparation ouvertes qui commencent à partir du 06/10/2026 et ne durent pas 75 min finissent désormais
75 min après leur début. Le début ne bouge pas ; une tâche sans fin en reçoit une. `set_value` sans toucher à
`modified` : seule la fin change, aucun hook de la tâche n'a à se rejouer. Idempotent.
"""

import frappe
from frappe.utils import add_to_date, get_datetime

DEPUIS = "2026-10-06 00:00:00"
MINUTES = 75


def execute():
    taches = frappe.db.sql(
        """SELECT name, starts_on, ends_on FROM `tabTache de travail`
           WHERE custom_type_dintervention = 'Réparation' AND status = 'Open' AND starts_on >= %s
             AND (ends_on IS NULL OR TIMESTAMPDIFF(MINUTE, starts_on, ends_on) <> %s)""",
        (DEPUIS, MINUTES), as_dict=True)
    for t in taches:
        frappe.db.set_value("Tache de travail", t.name, "ends_on",
                            add_to_date(get_datetime(t.starts_on), minutes=MINUTES), update_modified=False)
    if taches:
        print(f"Réparations ramenées à {MINUTES} min : {len(taches)} ({', '.join(t.name for t in taches)})")
    frappe.db.commit()
