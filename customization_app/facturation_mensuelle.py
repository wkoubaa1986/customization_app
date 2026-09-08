"""Cron mensuel versionné et rattrapage explicite d'un mois, sans date figée."""
from pathlib import Path

import frappe
from frappe.utils import cint, getdate, get_last_day
from frappe.utils.safe_exec import safe_exec

SCRIPT_NAME = "Facturation Autoaitque Compte Pro"


def script_source():
    return (Path(__file__).parent / "scripts" / "facturation_mensuelle.py").read_text()


def run(month, dry_run=1, send_emails=0):
    """Console/bench uniquement. month=YYYY-MM, prévisualisation par défaut."""
    import re
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("Mois attendu : YYYY-MM")
    date = get_last_day(getdate(month + "-01"))
    old_form = frappe.local.form_dict
    frappe.local.form_dict = frappe._dict(old_form or {})
    frappe.local.form_dict.update({
        "facturation_month_end": str(date),
        "facturation_send_emails": "1" if cint(send_emails) and not cint(dry_run) else "0",
    })
    frappe.db.savepoint("monthly_run")
    try:
        namespace, _ = safe_exec(script_source(), script_filename=SCRIPT_NAME)
        result = {"month": month, "dry_run": bool(cint(dry_run)),
                  "created": namespace["created_invoices"], "errors": namespace["errors"]}
        if cint(dry_run) or result["errors"]:
            frappe.db.rollback(save_point="monthly_run")
            result["rolled_back"] = True
        else:
            frappe.db.commit()
            result["rolled_back"] = False
        return result
    except BaseException:
        frappe.db.rollback(save_point="monthly_run")
        raise
    finally:
        frappe.local.form_dict = old_form
