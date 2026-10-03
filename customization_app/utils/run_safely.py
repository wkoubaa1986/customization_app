import frappe


def run_safely(job_name: str, fn):
    """Exécute un cron en isolant son échec : traceback en Error Log + e-mail à l'adresse de Config Relances, et la VALEUR
    de retour est rendue. L'exception n'est pas relancée : les crons isolent déjà chaque élément (savepoint) et le commit du
    planificateur garde le travail fait."""
    try:
        return fn()
    except Exception:
        tb = frappe.get_traceback()
        frappe.log_error(tb, job_name)
        try:
            from customization_app import relances_config as RC
            RC.alerter(f"[ERPNext] Erreur dans le job : {job_name}", f"<pre>{frappe.as_unicode(tb)}</pre>")
        except Exception:
            pass
        return None
