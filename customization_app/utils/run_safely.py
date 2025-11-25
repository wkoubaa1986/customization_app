import frappe

def run_safely(job_name: str, fn):
    """
    Exécute une fonction dans un try/except et envoie un email en cas d'erreur.
    """
    try:
        fn()
    except Exception:
        tb = frappe.get_traceback()
        frappe.log_error(tb, job_name)

        frappe.sendmail(
            recipients=["koubaawassim@gmail.com"],  # 👉 mets ton email
            subject=f"[ERPNext] Erreur dans le job : {job_name}",
            message=f"<pre>{frappe.as_unicode(tb)}</pre>",
        )
        # Si tu veux que le scheduler voie aussi l'erreur :
        # raise
