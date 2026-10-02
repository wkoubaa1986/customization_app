"""Les tâches de vérification de stock à venir duraient 2 h quel que soit le réglage (la fin était
recalculée par le type « Autre » à la création). Remet la durée du réglage sur les tâches encore ouvertes."""

import frappe
from frappe.utils import add_to_date, get_datetime


def execute():
    from customization_app.stock_entrepots import DUREES_MIN, config_verification

    minutes = DUREES_MIN.get(config_verification()["duree"], 60)
    n = 0
    for t in frappe.get_all("Tache de travail", filters={"titre": ["like", "🧾 Vérification stock%"], "status": "Open"},
                            fields=["name", "starts_on", "ends_on"]):
        if not t.starts_on:
            continue
        fin = add_to_date(get_datetime(t.starts_on), minutes=minutes)
        if get_datetime(t.ends_on) != fin:
            frappe.db.set_value("Tache de travail", t.name, {"ends_on": fin, "temps": config_verification()["duree"]}, update_modified=False)
            n += 1
    frappe.db.commit()
    print(f"[corriger_duree_taches_verification] {n} tâche(s) remise(s) à {minutes} min.")
