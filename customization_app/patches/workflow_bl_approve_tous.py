"""
Bon de livraison : l'approbation ouverte à tous les employés.

Le workflow « Validation Magasin » réservait Approve (En attente validation
magasin → Approved) au rôle « Responsable magasin » : un technicien qui doit
valider son BL depuis le dialogue de clôture de tâche (règle du 27/08/2026 —
pas de clôture avec un BL en brouillon) restait coincé devant « En attente ».
Décision utilisateur : n'importe quel employé peut approuver. Reject reste au
Responsable magasin — refuser une sortie de stock demeure un geste de contrôle.

Le workflow vit en base (pas en fixture) : ce patch l'aligne partout.
"""

import frappe


def execute():
    if not frappe.db.exists("Workflow", "Validation Magasin"):
        return
    wf = frappe.get_doc("Workflow", "Validation Magasin")
    change = False
    for t in wf.transitions:
        if (t.action == "Approve"
                and t.state == "En attente validation magasin"
                and t.allowed != "All"):
            t.allowed = "All"
            change = True
    if change:
        wf.flags.ignore_permissions = True
        wf.save()
        frappe.db.commit()
        print("[workflow_bl_approve_tous] Approve ouvert à tous.")
