"""Tâche de travail : « 1er / 2e appel sans réponse » refusés à l'enregistrement — le vieux réglage est retiré.

Signalé le 23/11/2026 (compte Economic Aqua Solution, Tache-09441) : « recontacte ne peut pas être "1er appel sans
réponse". Il devrait être l'un de "", "contacté", "Investigué", "planifié", "couvert", "vendu" ». Les deux valeurs ont
été ajoutées au type de document le 05/10/2026 (Liste Appels Rattrapage, bouton « 📵 Pas de réponse »), mais un
Property Setter du 29/06/2026, resté dans les fixtures, remplaçait toujours la liste par l'ancienne : la valeur était
posée sans contrôle par la liste d'appels, puis TOUT enregistrement de la tâche (clôture, déplacement, annulation)
était refusé.

La liste vit dans le type de document (tache_de_travail.json) ; le réglage est retiré des fixtures, et supprimé ici
de la base — la synchronisation des fixtures n'efface pas ce qui a disparu du fichier.
"""
import frappe

REGLAGE = "Tache de travail-recontacte-options"


def execute():
    if frappe.db.exists("Property Setter", REGLAGE):
        frappe.delete_doc("Property Setter", REGLAGE, ignore_permissions=True, force=True)
    frappe.clear_cache(doctype="Tache de travail")
