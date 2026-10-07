"""Le Server Script « generer un echeancier de maintenace » (Sales Order, Before Save Submitted) cherche les ANCIENS
noms de groupes d'articles (« Osmoseur Domestique », « Porte filtre », « Adoucisseur »…) : depuis la réorganisation de
l'arbre des groupes (≈ 16/08/2025) il ne trouve plus rien. Les échéanciers sont créés par
Maintenance/update_schedule.py. Éteint à la demande de l'utilisateur le 07/10/2026, jamais supprimé.

⚠️ Il est AUSSI dans fixtures/server_script.json (disabled = 1 depuis le 07/10/2026) : le migrate synchronise les
fixtures APRÈS les patches, la fixture seule l'aurait réactivé si elle était restée à 0."""
import frappe

NOM = "generer un echeancier de maintenace"


def execute():
    if frappe.db.exists("Server Script", NOM):
        frappe.db.set_value("Server Script", NOM, "disabled", 1)
        frappe.clear_cache()
