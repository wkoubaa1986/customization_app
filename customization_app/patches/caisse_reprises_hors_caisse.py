"""Les règlements REPRIS par la correction d'un bordereau, hors des caisses (27/11/2026).

Quand un bordereau de remise montre qu'un chèque ou une traite a été déposé net de la retenue à la source, le règlement
est repris au bon montant (bank_retenue_sync, piece_nette) : une copie naît le jour de la correction, au nom de celui
qui a cliqué. La caisse journalière la prenait pour un encaissement du jour — la traite MH2C de 3 965 DT, déjà en
banque depuis le 23/11, est apparue dans la caisse de Wassim le 27/11. Les reprises à venir sont marquées à la source ;
ce patch marque celles qui existent déjà : exclues des caisses (visibles, sans peser), sauf si un comptage les a prises."""
import frappe


def execute():
    if not frappe.db.has_column("Payment Entry", "custom_exclu_caisse"):
        return
    cloture = "AND IFNULL(custom_cloture_caisse, '') = ''" if frappe.db.has_column("Payment Entry", "custom_cloture_caisse") else ""
    noms = frappe.db.sql_list(
        f"""SELECT name FROM `tabPayment Entry` WHERE docstatus = 1 AND payment_type = 'Receive'
            AND IFNULL(custom_exclu_caisse, 0) = 0 {cloture}
            AND remarks LIKE %s AND creation >= '2026-11-01'""", ("%Repris le % : montant ramené de %celui de la pièce remise en banque%",))
    for nom in noms:
        frappe.db.set_value("Payment Entry", nom, "custom_exclu_caisse", 1, update_modified=False)
        frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": "Payment Entry",
                        "reference_name": nom,
                        "content": "Exclu de la caisse journalière : règlement repris au montant du bordereau, "
                                   "ce n'est pas un encaissement du jour."}).insert(ignore_permissions=True)
    if noms:
        frappe.log_error(title="Reprises de bordereau exclues des caisses", message=", ".join(noms))
    frappe.db.commit()
