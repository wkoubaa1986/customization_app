"""Reprise du 06/10/2026 : une vérification « À valider » dont le comptage a été terminé par un responsable
magasin À LA PLACE de l'employé du stock passe chez l'employé (« À confirmer »). Sans cela l'employé ne voyait
aucun bouton (VERIF-2026-00001 : stock de Mohamed Hedi Chouchane compté par Hedi ibidhii)."""
import frappe


def execute():
    if not frappe.db.exists("DocType", "Verification Stock"):
        return
    from customization_app import stock_entrepots as S

    for nom in frappe.get_all(S.VERIF, filters={"statut": S.A_VALIDER}, pluck="name"):
        v = frappe.get_doc(S.VERIF, nom)
        emp = S._employe_du_stock(v.entrepot)
        if not emp or not emp.user_id or not v.valide_employe_par or v.valide_employe_par == emp.user_id:
            continue
        S.passer_a_l_employe(v, v.valide_employe_par, v.valide_employe_le)
