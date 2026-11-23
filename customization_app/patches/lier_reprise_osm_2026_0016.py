"""Machine de CARLO BATAGLIA : le dossier OSM-2026-0016 relié à l'entretien pendant lequel elle a été reprise.

Sa demande du 23/11/2026 (« lie cette tâche à cette réparation ») : l'entretien du 21/11/2026 (Tache-09536, Radhouan
Sifi) a été annulé avec la raison « Autre » et le rapport « la machine a été transportée vers le magasin pour entretien
général » ; le dossier de réparation a été ouvert à part le 23/11 au magasin. Le bouton « Machine reprise » de Ma
journée fait désormais ce lien tout seul ; ce patch le pose pour ce cas d'avant : l'entretien s'affiche « machine
reprise à l'atelier » (orange, avec l'état du dossier) au lieu d'une annulation ordinaire.

Rien n'est fait si le dossier ou la tâche manquent, ne sont pas du même client, ou si le lien existe déjà. Pour
d'autres cas du même genre : ajouter la paire à LIENS dans un NOUVEAU patch (celui-ci ne rejoue pas).
"""
import frappe

LIENS = (("OSM-2026-0016", "Tache-09536"),)


def execute():
    if not frappe.db.has_column("Machine Reparation", "tache_origine"):
        return
    from customization_app.reparation_osmoseur import lier_reprise

    for machine, tache in LIENS:
        refus = lier_reprise(machine, tache)
        if refus:
            frappe.log_error(title="Reprise %s / %s non reliée" % (machine, tache), message=refus)
