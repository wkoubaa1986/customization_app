"""La couleur d'un employé change sur sa fiche : ses tâches OUVERTES la prennent tout de suite.

Question du 22/11/2026 : « est-ce que le calendrier d'équipe s'actualise lorsque je change la couleur d'un employé ? »
Pas entièrement : la couleur est ENREGISTRÉE sur chaque tâche quand on la sauve (api.compute_tache_color). Après un
changement de `custom_couleur` sur la fiche Employé, la pastille et l'en-tête de colonne suivaient, mais les tâches
déjà créées gardaient l'ancienne couleur jusqu'à leur prochain enregistrement — dans l'ancien calendrier comme dans
le nouveau. (Seul le panneau des sous-traitants recolorait, par sous_traitant.changer_couleur.)

Hook Employee.on_update : quand la couleur de la fiche a changé, les tâches ouvertes de l'employé sont recolorées,
par la règle habituelle — une tâche créée par le compte partenaire reste cyan, une tâche terminée ou annulée garde la
couleur de son statut (on ne touche qu'aux ouvertes). Écriture directe de la colonne : ni hooks de tâche, ni date de
modification, ni message au client.
"""
import frappe

CHAMP = "custom_couleur"
TACHE = "Tache de travail"


def a_recolorer(taches: list, couleur: str, compte_partenaire: str) -> list:
    """Les tâches ouvertes à passer à `couleur` : pas celles du partenaire (cyan), pas celles qui l'ont déjà. PURE.
    `taches` : [{name, owner, color}]."""
    voulue = (couleur or "").lower()
    return [t["name"] for t in taches
            if t.get("owner") != compte_partenaire and (t.get("color") or "").lower() != voulue]


def apres_fiche(doc, method=None):
    """Employee.on_update."""
    if not frappe.db.has_column("Employee", CHAMP):
        return
    avant = doc.get_doc_before_save()
    if not avant or (avant.get(CHAMP) or "") == (doc.get(CHAMP) or ""):
        return
    from customization_app import api

    frappe.local._couleurs_sous_traitants = {}          # la couleur de la fiche est gardée en mémoire le temps d'une requête
    couleur = api.compute_tache_color({"custom_choix_du_staff": doc.name, "status": "Open", "owner": ""})
    ouvertes = frappe.get_all(TACHE, filters={"custom_choix_du_staff": doc.name, "status": "Open"},
                              fields=["name", "owner", "color"], limit_page_length=0)
    noms = a_recolorer(ouvertes, couleur, api.PARTNER_USER)
    if noms:
        frappe.db.sql("update `tab%s` set color = %%s where name in %%s" % TACHE, (couleur, tuple(noms)))
        frappe.msgprint(frappe._("{0} tâche(s) ouverte(s) de {1} ont pris la nouvelle couleur.").format(len(noms), doc.employee_name),
                        alert=True, indicator="green")
