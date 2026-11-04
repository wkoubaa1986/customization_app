"""Retire les anciens participants désactivés des réunions futures, sans toucher au passé."""

import frappe


def execute():
    from customization_app import reunion_activite as RA, suivi_activite as SA

    if not frappe.db.exists("DocType", RA.DOCTYPE):
        return []
    modifications = []
    for row in frappe.get_all(RA.DOCTYPE, filters={"statut": "Prévue"},
                              fields=["name", "date", "heure", "duree_min", "statut"]):
        if RA.etat_horaire(row)["statut_affiche"] != "Prévue":
            continue
        doc = frappe.get_doc(RA.DOCTYPE, row.name, for_update=True)
        if RA.etat_horaire(doc)["statut_affiche"] != "Prévue":
            continue
        actifs = SA.employes_actifs(p.employe for p in doc.participants)
        retires = [p.employe for p in doc.participants if p.employe not in actifs]
        if not retires:
            continue
        doc.set("participants", [p for p in doc.participants if p.employe in actifs])
        doc.save(ignore_permissions=True)
        modifications.append({"reunion": doc.name, "employes_retires": retires})
    return modifications
