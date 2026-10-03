"""Données (03/10/2026) : 1 625 visites « orphelines » — en attente, mais DÉPASSÉES par une visite réalisée plus récente du
même article sur le même échéancier. L'ancienne règle de décalage (« ligne la plus proche ») avait marqué la voisine et laissé
celle-ci ouverte pour toujours ; elles faussaient le retard et les cycles de relance. Elles sont retirées (lignes de
planification remplacées, pas d'historique perdu : la visite réalisée est sur la ligne voisine), avec une trace en
commentaire sur chaque échéancier. Rejouable sans effet une fois propre."""
import frappe


def execute():
    lignes = frappe.db.sql("""select d.name, d.parent, d.item_code, d.scheduled_date from `tabMaintenance Schedule Detail` d
                              join `tabMaintenance Schedule` ms on ms.name = d.parent and ms.docstatus = 1
                              where d.actual_date is null and exists (select 1 from `tabMaintenance Schedule Detail` d2
                                    where d2.parent = d.parent and d2.item_code = d.item_code and d2.actual_date is not null
                                      and d2.scheduled_date > d.scheduled_date)""", as_dict=True)
    if not lignes:
        print("clore_visites_orphelines : rien à faire")
        return
    par_parent = {}
    for l in lignes:
        par_parent.setdefault(l.parent, []).append(l)
    frappe.db.sql("delete from `tabMaintenance Schedule Detail` where name in %s", (tuple(l.name for l in lignes),))
    for parent, ls in par_parent.items():
        frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": "Maintenance Schedule", "reference_name": parent,
                        "content": "🧹 %d visite(s) en attente retirée(s) le %s : dépassée(s) par une visite réalisée plus récente (%s)."
                                   % (len(ls), frappe.utils.nowdate(), ", ".join("%s %s" % (l.item_code, l.scheduled_date) for l in ls[:6])
                                      + (" …" if len(ls) > 6 else ""))}).insert(ignore_permissions=True)
    print("clore_visites_orphelines : %d visite(s) retirée(s) sur %d échéancier(s)" % (len(lignes), len(par_parent)))
