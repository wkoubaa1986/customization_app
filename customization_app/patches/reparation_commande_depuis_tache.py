"""Réparation osmoseurs : un dossier sans commande prend celle de sa tâche de réparation (24/11/2026). OSM-2026-0008
(Walid Fguira) affichait « aucune commande liée » alors que sa tâche Tache-09002, clôturée par Sadok Bouziri, portait
SAL-ORD-2026-04205. Désormais `synchroniser` le fait à chaque changement de tâche ; ce patch rattrape les dossiers
existants, ouverts ou rendus. Une commande déjà choisie n'est jamais remplacée."""
import frappe

from customization_app.reparation_osmoseur import DOCTYPE, rattacher_commande


def execute():
    for nom in frappe.get_all(DOCTYPE, filters={"commande_client": ["in", ["", None]]}, pluck="name"):
        rattacher_commande(nom)
