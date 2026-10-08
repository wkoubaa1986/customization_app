"""Portail /rdv (08/10/2026) : secteurs voisins acceptés sur les jours proches — activé au déploiement (demande
utilisateur), seulement si personne n'a encore touché la case ; voisins ajoutés à la main d'après la carte des secteurs
(6 touche 5, 2 et 1 ; 7 touche 3 et 4) s'ils ne sont pas déjà réglés ; voisins calculés tout de suite pour le réglage."""

import frappe

AJOUTS_CARTE = "Secteur 6, Secteur 2\nSecteur 6, Secteur 1\nSecteur 7, Secteur 3\nSecteur 7, Secteur 4"


def execute():
    if not frappe.db.exists("DocType", "Config Portail RDV"):
        return
    frappe.reload_doc("customize_erpnext", "doctype", "config_portail_rdv")
    deja = {r[0] for r in frappe.db.sql("""select field from tabSingles where doctype='Config Portail RDV'
                                            and field in ('voisins_actif', 'voisins_ajouts')""")}
    if "voisins_actif" not in deja:
        frappe.db.set_single_value("Config Portail RDV", "voisins_actif", 1, update_modified=False)
    if "voisins_ajouts" not in deja:
        frappe.db.set_single_value("Config Portail RDV", "voisins_ajouts", AJOUTS_CARTE, update_modified=False)
    from customization_app.portail_rdv_planning import rafraichir_voisins_nuit
    rafraichir_voisins_nuit()
    frappe.db.commit()
