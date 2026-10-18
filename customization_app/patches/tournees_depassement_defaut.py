"""Remettre les défauts du réglage « Config Optimisation Tournees » écrasés par un enregistrement de côté (18/10/2026).

Le 16/10/2026, le patch `seed_domiciles_flotte_gps` a enregistré le réglage (doc.save, pour les domiciles) alors que
`depassement_minutes` (ajouté le 12/10) et `google_routes_max_jour` n'avaient JAMAIS été saisis : un Single existant ne
reçoit pas les défauts du DocType, ils ont donc été écrits à 0. Conséquence en prod : fin de journée STRICTE (0 min de
dépassement) → le 18/10, la journée de Mohamed Hedi du 19/10 (8 entretiens, un rendez-vous fixe à 16:30) ne tenait pas,
et l'optimiseur figeait les tâches « sans place » en une tournée absurde. Le plafond Google à 0 valait déjà le défaut
par chance (`or PLAFOND_DEFAUT`). On remet les défauts là où la valeur est 0 ; une valeur saisie est gardée.
"""

import frappe

CONFIG = "Config Optimisation Tournees"
DEFAUTS = {"depassement_minutes": 60, "google_routes_max_jour": 150}


def execute():
    if not frappe.db.exists("DocType", CONFIG):
        return
    for champ, defaut in DEFAUTS.items():
        valeur = frappe.db.sql("select value from tabSingles where doctype = %s and field = %s", (CONFIG, champ))
        if valeur and str(valeur[0][0] or "0").strip() in ("0", "", "None"):
            frappe.db.set_single_value(CONFIG, champ, defaut, update_modified=False)
    frappe.db.commit()
