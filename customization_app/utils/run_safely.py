import frappe


def run_safely(job_name: str, fn):
    """Exécute un cron en isolant son échec : traceback en Error Log + e-mail, et la VALEUR de retour est rendue (elle
    était perdue, donc les résumés des crons n'arrivaient nulle part). L'exception n'est pas relancée : les crons isolent
    déjà chaque élément (savepoint) et un commit du planificateur garde le travail fait."""
    try:
        return fn()
    except Exception:
        tb = frappe.get_traceback()
        frappe.log_error(tb, job_name)
        try:
            frappe.sendmail(recipients=["koubaawassim@gmail.com"], subject=f"[ERPNext] Erreur dans le job : {job_name}",
                            message=f"<pre>{frappe.as_unicode(tb)}</pre>")
        except Exception:
            pass
        return None
