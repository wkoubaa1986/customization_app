"""
Backend de l'outil « Facturation Auto ».

Wrapper mince autour du Server Script existant « Facturation Auto » (méthode
generation_fac_auto). Le script utilise `getattr` (interdit dans safe_exec), il est
donc exécuté ici en Python complet — exactement comme en console. Réservé à un seul
utilisateur.

Paramètres (comme le script, lus dans frappe.form_dict) :
  dry_run, use_gaps, create_leftover, verbose, last_fac_num, passager_factor
"""

import frappe
from frappe import _
from frappe.utils import flt

ALLOWED_USER = "koubaawassim@gmail.com"
SERVER_SCRIPT_NAME = "Facturation Auto"


def _guard():
    if frappe.session.user != ALLOWED_USER:
        frappe.throw(_("Accès réservé."), frappe.PermissionError)


def _enrich_next_month(result):
    """Ajoute le 1er n° de facture déjà existant pour le mois SUIVANT la période (M+1)."""
    if not (isinstance(result, dict) and result.get("date_end")):
        return result
    try:
        from frappe.utils import getdate, add_months, get_first_day, get_last_day
        ns = get_first_day(add_months(getdate(result["date_end"]), 1))
        ne = get_last_day(ns)
        row = frappe.db.sql(
            """SELECT MIN(CAST(custom_numero_facture AS UNSIGNED))
               FROM `tabSales Invoice`
               WHERE docstatus = 1 AND posting_date >= %s AND posting_date <= %s
                 AND custom_numero_facture REGEXP '^[0-9]+$'""",
            (ns, ne),
        )
        result["first_fac_num_next_month"] = (row[0][0] if row and row[0] else None)
        result["next_month_start"] = str(ns)
        result["next_month_end"] = str(ne)
    except Exception:
        pass
    return result


def _enrich_result(result):
    """Ajoute : ventilation TTC/HT/TVA générée, totaux TTC/HT en base (période) et
    contrôle de cohérence (trous non comblés + numéros en double)."""
    if not isinstance(result, dict):
        return result

    created = result.get("created_invoices") or []

    # --- Ventilation des factures GÉNÉRÉES : TTC / HT / TVA ---
    result["generated_ttc"] = round(sum(flt(c.get("grand_total")) for c in created), 3)
    result["generated_ht"] = round(sum(flt(c.get("net_total")) for c in created), 3)
    result["generated_tva"] = round(sum(flt(c.get("total_taxes")) for c in created), 3)

    ds, de = result.get("date_start"), result.get("date_end")
    gen_names = [c.get("invoice") for c in created if c.get("invoice")]

    # --- Totaux TTC / HT déjà EN BASE pour la période (hors factures générées) ---
    if ds and de:
        try:
            params = [ds, de]
            not_in = ""
            if gen_names:
                not_in = " AND name NOT IN (%s)" % ", ".join(["%s"] * len(gen_names))
                params += gen_names
            row = frappe.db.sql(
                """SELECT SUM(grand_total) AS ttc, SUM(net_total) AS ht
                   FROM `tabSales Invoice`
                   WHERE docstatus = 1 AND is_opening = 'No'
                     AND posting_date BETWEEN %s AND %s""" + not_in,
                params,
                as_dict=True,
            )
            result["db_period_ttc"] = round(flt(row[0].ttc), 3) if row and row[0].ttc else 0.0
            result["db_period_ht"] = round(flt(row[0].ht), 3) if row and row[0].ht else 0.0
        except Exception:
            pass

    # --- Cohérence : trous non comblés + doublons de numéro ---
    result["remaining_gaps"] = list(result.get("final_gaps") or [])

    try:
        gen_nums = [
            int(c.get("custom_numero_facture"))
            for c in created
            if str(c.get("custom_numero_facture") or "").isdigit()
        ]
        # doublons internes aux factures générées
        internal = set(n for n in gen_nums if gen_nums.count(n) > 1)
        # collisions avec des factures déjà en base sur la période (hors générées)
        collisions = set()
        if ds and de and gen_nums:
            params = [ds, de]
            not_in = ""
            if gen_names:
                not_in = " AND name NOT IN (%s)" % ", ".join(["%s"] * len(gen_names))
                params += gen_names
            existing = frappe.db.sql(
                """SELECT DISTINCT CAST(custom_numero_facture AS UNSIGNED) AS n
                   FROM `tabSales Invoice`
                   WHERE docstatus = 1 AND custom_numero_facture REGEXP '^[0-9]+$'
                     AND posting_date BETWEEN %s AND %s""" + not_in,
                params,
                as_dict=True,
            )
            existing_set = set(r["n"] for r in existing if r.get("n") is not None)
            collisions = set(gen_nums) & existing_set
        result["duplicate_numbers"] = sorted(internal | collisions)
    except Exception:
        result["duplicate_numbers"] = []

    return result


def _exec_script(form_params):
    """Exécute le Server Script « Facturation Auto » en Python complet (getattr requis).

    Renseigne frappe.form_dict avec form_params, exécute, renvoie frappe.response['result'].
    Le mode PREVIEW du script lève une exception interne _FacPreviewDone après avoir posé
    son résultat ; on la neutralise ici (identifiée par nom, non importable).
    """
    code = frappe.db.get_value("Server Script", SERVER_SCRIPT_NAME, "script")
    if not code:
        frappe.throw(_("Server Script « {0} » introuvable.").format(SERVER_SCRIPT_NAME))

    def s(v):
        return "" if v is None else str(v)

    for k, v in form_params.items():
        frappe.form_dict[k] = s(v)

    frappe.response.pop("result", None)
    try:
        exec(code, {"frappe": frappe, "__builtins__": __builtins__})  # noqa: S102
    except Exception as e:
        if type(e).__name__ != "_FacPreviewDone":
            raise
    result = frappe.response.get("result")
    frappe.response.pop("result", None)
    return result


@frappe.whitelist()
def preview(use_gaps=1, last_fac_num=None, passager_factor=0.5):
    """État de la base AVANT lancement : dernier n° M-1, 1er n° M+1, nombre de trous.

    N'exécute PAS la génération (le script rollback et retourne dès le calcul de
    numérotation). Read-only, réservé à l'utilisateur autorisé.
    """
    _guard()
    result = _exec_script({
        "preview": 1,
        "dry_run": 1,
        "use_gaps": use_gaps,
        "create_leftover": 0,
        "verbose": 0,
        "last_fac_num": last_fac_num,
        "passager_factor": passager_factor,
    })
    return _enrich_next_month(result)


@frappe.whitelist()
def run(dry_run=1, use_gaps=0, create_leftover=0, verbose=0,
        last_fac_num=None, passager_factor=0.5):
    """Exécute la logique de facturation auto et renvoie son bilan (result)."""
    _guard()
    result = _exec_script({
        "preview": 0,
        "dry_run": dry_run,
        "use_gaps": use_gaps,
        "create_leftover": create_leftover,
        "verbose": verbose,
        "last_fac_num": last_fac_num,
        "passager_factor": passager_factor,
    })
    return _enrich_result(_enrich_next_month(result))
