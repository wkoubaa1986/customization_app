"""Double validation de la caisse (04/10/2026) : les clôtures déjà soumises deviennent « Validée »,
marquées historiques et validées seules (pas de remise, pas de collecteur) ; elles servent de report
comme avant. Les nouvelles colonnes Currency naissent à 0 (pas NULL) : le fond conservé d'une clôture
historique = ses espèces comptées (sinon son théorique), puisque rien n'était remis. Idempotent."""
import frappe


def execute():
    frappe.reload_doc("customize_erpnext", "doctype", "cloture_caisse")
    frappe.db.sql(
        """UPDATE `tabCloture Caisse`
           SET statut = 'Validée', historique = 1, validation_seule = 1
           WHERE docstatus = 1 AND (statut IS NULL OR statut != 'Validée' OR historique = 0)""")
    frappe.db.sql(
        """UPDATE `tabCloture Caisse`
           SET fond_conserve = COALESCE(especes_comptees, solde_theorique), especes_remises = 0
           WHERE docstatus = 1 AND historique = 1 AND fond_conserve = 0
             AND COALESCE(especes_comptees, solde_theorique) <> 0""")
