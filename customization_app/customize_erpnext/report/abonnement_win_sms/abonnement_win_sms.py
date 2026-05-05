import frappe
from urllib.parse import urlencode


@frappe.whitelist()
def get_winsms_balance():
    """Retourne le solde SMS pour la Number Card du tableau de bord."""
    try:
        _, data, *_ = execute()
        return {"result": data[0].get("available_units", 0) if data else 0}
    except Exception:
        return {"result": 0}


@frappe.whitelist()
def get_winsms_days_remaining():
    """Retourne le nombre de jours avant expiration pour la Number Card."""
    try:
        _, data, *_ = execute()
        exp = data[0].get("expiration_date") if data else None
        if exp:
            delta = frappe.utils.getdate(exp) - frappe.utils.getdate(frappe.utils.today())
            return {"result": delta.days}
        return {"result": 0}
    except Exception:
        return {"result": 0}


@frappe.whitelist()
def get_winsms_status():
    """Retourne 1 si actif, 0 sinon, pour la Number Card."""
    try:
        _, data, *_ = execute()
        status = data[0].get("statut", "") if data else ""
        return {"result": 1 if str(status).lower() == "active" else 0}
    except Exception:
        return {"result": 0}


def execute(filters=None):
    cache_key = "winsmspro_sms_balance"

    # Lire la clé API depuis SMS Settings (pas en dur dans le code)
    ss = frappe.get_doc("SMS Settings", "SMS Settings")
    api_key = next((p.value for p in ss.get("parameters") if p.parameter == "api_key"), None)

    url = (
        "https://www.winsmspro.com/sms/sms/api?"
        + urlencode({"action": "check-balance", "api_key": api_key, "response": "json"})
    ) if api_key else None

    columns = [
        {"label": "Statut",                "fieldname": "statut",          "fieldtype": "Data", "width": 180},
        {"label": "Date d'expiration",     "fieldname": "expiration_date", "fieldtype": "Date", "width": 180},
        {"label": "Nombre de SMS restant", "fieldname": "available_units", "fieldtype": "Int",  "width": 220},
        {"label": "Jours restants",        "fieldname": "days_remaining",  "fieldtype": "Int",  "width": 150},
    ]

    try:
        r = frappe.cache().get_value(cache_key)
        if not r and url:
            import urllib.request
            import json
            resp = urllib.request.urlopen(url, timeout=15)
            r = json.loads(resp.read().decode())
            frappe.cache().set_value(cache_key, r, expires_in_sec=600)

        status = "Active"
        expiration_date = None
        available_units = 0

        if r:
            if r.get("result"):
                status          = r["result"][0].get("status", "Active")
                expiration_date = r["result"][0].get("expirationDate")
                available_units = r["result"][0].get("availableUnits", 0)
            else:
                status          = "Active"
                expiration_date = frappe.utils.getdate(r.get("licence")) if r.get("licence") else None
                available_units = r.get("balance", 0)

        available_units = int(available_units or 0)
        days_remaining = 0
        if expiration_date:
            days_remaining = (frappe.utils.getdate(expiration_date) - frappe.utils.getdate(frappe.utils.today())).days
        indicator = "Red" if available_units <= 100 else ("Orange" if available_units <= 500 else "Green")

        data = [{"statut": status, "expiration_date": expiration_date, "available_units": available_units, "days_remaining": days_remaining}]
        report_summary = [
            {"value": status,          "label": "Statut",                "datatype": "Data"},
            {"value": expiration_date, "label": "Date d'expiration",     "datatype": "Date"},
            {"value": available_units, "indicator": indicator, "label": "Nombre de SMS restant", "datatype": "Int"},
            {"value": days_remaining,  "label": "Jours restants",        "datatype": "Int"},
        ]
        return columns, data, None, None, report_summary

    except Exception as e:
        frappe.log_error(str(e), "Erreur rapport WinSMSPro")
        data = [{"statut": "Erreur API / Limite atteinte", "expiration_date": None, "available_units": 0}]
        report_summary = [{"value": "Erreur API", "indicator": "Red", "label": "Statut", "datatype": "Data"}]
        return columns, data, None, None, report_summary
