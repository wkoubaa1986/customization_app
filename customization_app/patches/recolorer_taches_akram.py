"""
Recalcule la couleur stockée des tâches d'Akram (HR-EMP-00010).

La couleur du calendrier n'est pas calculée à l'affichage : elle est écrite
dans la colonne `color` de chaque tâche au moment de l'enregistrement, puis
relue telle quelle par get_custom_tache_events. Ajouter une entrée dans
STAFF_COLORS ne change donc que les tâches créées ou modifiées ensuite —
les tâches existantes gardent leur ancienne couleur.

Périmètre volontairement restreint à HR-EMP-00010. Un contrôle global a montré
472 autres tâches dont la couleur stockée diverge du calcul, essentiellement
d'anciennes tâches terminées en vert pâle #bbf7d0 au lieu du #32CD32 actuel :
les recolorer changerait l'aspect de centaines de tâches sans que ce soit
demandé.

compute_tache_color est appliqué plutôt qu'une couleur en dur, afin que les
priorités soient respectées : une tâche terminée reste verte, une tâche dont
le client vient du partenaire reste cyan.
"""

import frappe

from customization_app.api import compute_tache_color

EMPLOYE = "HR-EMP-00010"


def execute():
    taches = frappe.db.sql(
        """SELECT name, color, status, custom_choix_du_staff, custom_client
           FROM `tabTache de travail` WHERE custom_choix_du_staff = %(emp)s""",
        {"emp": EMPLOYE},
        as_dict=True,
    )
    if not taches:
        print(f"[recolorer_taches_akram] aucune tâche pour {EMPLOYE}.")
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
        f"[recolorer_taches_akram] {modifiees} tâche(s) recolorée(s) "
        f"sur {len(taches)}."
    )
