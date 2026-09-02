"""Éteint le Server Script « Rappelle Rendez vous », repris par `rappel_rdv`.

Sans cela, DEUX rappels partiraient chaque soir à 20 h : l'ancien script et le
nouveau cron. Le client recevrait le message en double, dans deux formulations
différentes.

Il est DÉSACTIVÉ, pas supprimé : son code reste consultable, et le geste est
réversible d'une case à cocher. Même précédent que « Generation N Facture » et
que les deux scripts d'annulation de facture.

⚠️ LE SERVER SCRIPT PORTE SON PROPRE `Scheduled Job Type`. Le désactiver ne
suffit pas toujours : Frappe garde une ligne `rappelle_rendez_vous_cron` qui
continue de le rappeler chaque soir — et qui, script désactivé, échouerait
proprement mais salirait le journal tous les jours. On l'arrête aussi.
"""
import frappe

SCRIPT = "Rappelle Rendez vous"
JOB = "rappelle_rendez_vous_cron"


def execute():
    fait = []
    if frappe.db.exists("Server Script", SCRIPT) \
            and not frappe.db.get_value("Server Script", SCRIPT, "disabled"):
        frappe.db.set_value("Server Script", SCRIPT, "disabled", 1)
        fait.append("script éteint")
    if frappe.db.exists("Scheduled Job Type", JOB) \
            and not frappe.db.get_value("Scheduled Job Type", JOB, "stopped"):
        frappe.db.set_value("Scheduled Job Type", JOB, "stopped", 1)
        fait.append("tâche planifiée arrêtée")
    if fait:
        frappe.clear_cache()
        frappe.db.commit()
    return fait
