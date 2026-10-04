"""Aligne `custom_employé` sur le nom de l'employé du « Choix du staff » pour TOUTES les tâches (04/10/2026) :
en prod, des tâches du 05/10 montraient un autre employé (réaffectation par l'ancienne optimisation) ou un nom
vide. Les écritures futures sont couvertes par tache_employe.aligner_nom_employe. Idempotent, sans toucher à
la date de modification."""
import frappe


def execute():
    frappe.db.sql(
        """UPDATE `tabTache de travail` t
           JOIN tabEmployee e ON e.name = t.custom_choix_du_staff
           SET t.`custom_employé` = e.employee_name
           WHERE e.employee_name IS NOT NULL AND e.employee_name <> ''
             AND (t.`custom_employé` IS NULL OR t.`custom_employé` <> e.employee_name)""")
