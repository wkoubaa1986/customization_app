"""Vérification de stock : la tâche du calendrier n'est verte qu'à l'accord des deux (sa demande du 23/11/2026).

Jusqu'ici la tâche de l'employé passait à « Terminée » dès la fin de son comptage, avant la validation du
responsable et l'accord final. Le code ne la ferme plus qu'à la fin (`stock_entrepots._cloturer`) ; ce patch, une
fois, remet d'accord ce qui existe déjà :
1. les tâches déjà « terminées » d'une vérification qui attend encore une signature (« À valider », « À confirmer »)
   redeviennent ouvertes, avec la couleur de l'employé ;
2. les tâches d'une vérification « Terminée » fermées sans leur couleur (le statut était posé sans elle) prennent
   le vert des tâches terminées.

`modified` des tâches n'est pas touché.
"""
import frappe

TACHE = "Tache de travail"


def a_rouvrir():
    """Les tâches terminées trop tôt : (tâche, vérification)."""
    from customization_app.stock_entrepots import A_CONFIRMER, A_VALIDER

    return frappe.db.sql("""select t.name, v.name from `tabVerification Stock` v
                            join `tabTache de travail` t on t.name in (v.tache_employe, v.tache_responsable)
                            where v.statut in %s and t.status = 'Completed'""", ([A_VALIDER, A_CONFIRMER],))


def a_verdir(vert):
    from customization_app.stock_entrepots import TERMINEE

    return frappe.db.sql_list("""select t.name from `tabVerification Stock` v
                                 join `tabTache de travail` t on t.name in (v.tache_employe, v.tache_responsable)
                                 where v.statut = %s and t.status = 'Completed' and ifnull(t.color, '') != %s""", (TERMINEE, vert))


def execute():
    if not frappe.db.exists("DocType", "Verification Stock"):
        return
    from customization_app.api import STATUS_COLORS, compute_tache_color

    for tache, _verif in a_rouvrir():
        t = frappe.db.get_value(TACHE, tache, ["custom_choix_du_staff", "owner"], as_dict=True)
        couleur = compute_tache_color({"status": "Open", "custom_choix_du_staff": t.custom_choix_du_staff, "owner": t.owner})
        frappe.db.set_value(TACHE, tache, {"status": "Open", "color": couleur}, update_modified=False)
    vert = STATUS_COLORS["Completed"]
    for tache in a_verdir(vert):
        frappe.db.set_value(TACHE, tache, "color", vert, update_modified=False)
