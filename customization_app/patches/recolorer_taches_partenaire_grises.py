"""
Recalcule la couleur stockée des tâches OUVERTES créées par le partenaire.

Le Server Script planifié « ajuster rendez vous pris par partenaire »
recolorait ces tâches avec sa propre copie du barème STAFF_COLORS, copie qui
avait dérivé de celle d'api.py : HR-EMP-00010 (Akram) n'y figurait pas et
retombait sur le gris par défaut #888c89 — le cron re-grisait donc chaque
nuit ce que compute_tache_color avait correctement coloré à l'enregistrement.

Le script délègue désormais à customization_app.api.recolorer_taches_partenaire
(source unique) ; ce patch répare les tâches déjà grisées, y compris celles
créées il y a plus de 7 jours que le cron ne revisite plus.

Périmètre volontairement limité aux tâches non terminées/annulées du
partenaire : comme pour recolorer_taches_akram, les anciennes tâches
Completed/Cancelled divergentes (verts historiques) sont laissées telles
quelles.
"""

import frappe

from customization_app.api import PARTNER_USER, compute_tache_color


def execute():
    taches = frappe.get_all(
        "Tache de travail",
        filters={
            "owner": PARTNER_USER,
            "status": ["not in", ["Completed", "Cancelled"]],
        },
        fields=["name", "color", "status", "custom_choix_du_staff", "custom_client"],
    )
    if not taches:
        print("[recolorer_taches_partenaire_grises] aucune tâche ouverte du partenaire.")
        return

    modifiees = 0
    for t in taches:
        voulu = compute_tache_color(t)
        if (t.color or "") != voulu:
            frappe.db.set_value(
                "Tache de travail", t.name, "color", voulu, update_modified=False
            )
            modifiees += 1

    frappe.db.commit()
    frappe.clear_cache()
    print(
        f"[recolorer_taches_partenaire_grises] {modifiees} tâche(s) recolorée(s) "
        f"sur {len(taches)}."
    )
