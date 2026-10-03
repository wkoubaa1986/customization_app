"""Liste d'appels : champ « Réponse du client » sur la ligne (Appelle Client) — une case à écrire quand le résumé est
« Autres reponses » (demande 03/10/2026). Le DocType est CUSTOM (en base, module Support) : on ajoute le champ au DocType
lui-même. Idempotent."""
import frappe


def execute():
    if not frappe.db.exists("DocType", "Appelle Client"):
        return
    if frappe.db.exists("DocField", {"parent": "Appelle Client", "fieldname": "reponse_client"}):
        return
    dt = frappe.get_doc("DocType", "Appelle Client")
    idx = next((f.idx for f in dt.fields if f.fieldname == "resume_appel"), len(dt.fields))
    dt.append("fields", {"fieldname": "reponse_client", "fieldtype": "Small Text", "label": "Réponse du client",
                         "in_list_view": 0, "description": "Ce que le client a répondu (résumé « Autres reponses »)."})
    # Placer juste après « Résumé d'appel »
    champ = dt.fields[-1]
    dt.fields.remove(champ)
    dt.fields.insert(idx, champ)
    for i, f in enumerate(dt.fields, 1):
        f.idx = i
    dt.flags.ignore_permissions = True
    dt.save()
    frappe.clear_cache(doctype="Appelle Client")
    frappe.clear_cache(doctype="Liste Appelle Entretien")
