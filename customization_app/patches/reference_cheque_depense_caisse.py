"""Paiements nés d'une dépense caisse réglée par chèque : leur référence est le libellé
« Dépense caisse — … » au lieu du n° du chèque (22/10/2026, ACC-PAY-2026-08004 — corrigé dans
`caisse_depenses._avances_en_paiements`). ERPNext avait aussi réécrit la remarque qui portait
le n° : il n'est plus que dans l'écriture de caisse supprimée, que garde `Deleted Document`.

On ne touche qu'un paiement dont l'écriture d'origine porte UN SEUL chèque : avec plusieurs, on
ne saurait pas lequel revient à quel paiement.
"""
import json

import frappe

from customization_app.caisse_depenses import COMPTE_BANQUE, pieces_bancaires


def execute():
    ecritures = {}
    for d in frappe.db.sql(
            """SELECT data FROM `tabDeleted Document`
               WHERE deleted_doctype = 'Journal Entry'
                 AND JSON_VALUE(data, '$.cheque_no') LIKE 'Dépense caisse%%'""", as_dict=True):
        je = json.loads(d.data)
        ecritures.setdefault(je.get("cheque_no"), []).append(je.get("user_remark") or "")

    paiements = frappe.db.sql(
        """SELECT name, reference_no FROM `tabPayment Entry`
           WHERE payment_type = 'Pay' AND docstatus = 1 AND paid_from = %s
             AND reference_no LIKE 'Dépense caisse%%'""", (COMPTE_BANQUE,), as_dict=True)
    for pe in paiements:
        remarques = ecritures.get(pe.reference_no) or []
        if len(remarques) != 1:
            continue
        cheques = [num for mode, num in pieces_bancaires(remarques[0]) if mode == "Chèque" and num]
        if len(cheques) != 1:
            continue
        frappe.db.set_value("Payment Entry", pe.name,
                            {"reference_no": cheques[0], "remarks": remarques[0], "custom_remarks": 1},
                            update_modified=False)
