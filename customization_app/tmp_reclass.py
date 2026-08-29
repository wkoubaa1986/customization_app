import frappe


def run():
    from bank_retenue_sync.api import mouvements
    r = mouvements.reclassifier(date_from="2026-08-27", date_to="2026-08-28")
    print("RECLASSIFIE:", {k: r[k] for k in list(r)[:4]} if isinstance(r, dict) else r)
    ligne = frappe.db.sql("""select reference, statut, document_type, document_name, raison
                             from `tabBRS Mouvement Bancaire`
                             where reference='FT262402PF2L'""", as_dict=True)
    if not ligne:
        ligne = frappe.db.sql("""select name from tabDocType where name like 'BRS%Mouvement%'""")
        print("DOCTYPE?", ligne)
    else:
        l = ligne[0]
        print("LIGNE:", l.statut, "|", l.document_type, "|", l.document_name)
        print("RAISON:", (l.raison or "")[:140])
