"""Jours fériés (liste HRMS « Holiday List » de la société) — demande du 19/10/2026.

Trois usages : (1) bande grise « 🎌 nom du férié » sur toute la journée dans le Calendrier de travail ;
(2) avertissement, sans blocage, à l'enregistrement d'une tâche posée un jour férié ;
(3) portail RDV fermé ces jours-là, sauf « Accepter les jours fériés » dans Config Portail RDV.
Les dimanches (weekly_off) ne sont PAS des fériés ici : le calendrier les montre déjà, le portail a sa règle.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, getdate


def liste_par_defaut() -> str | None:
    """La Holiday List de la société (la première société si plusieurs)."""
    rows = frappe.get_all("Company", fields=["default_holiday_list"], order_by="creation", limit=1)
    return rows[0].default_holiday_list if rows and rows[0].default_holiday_list else None


def feries(debut, fin, employee: str | None = None) -> dict[str, str]:
    """{ 'AAAA-MM-JJ': description } des fériés (hors repos hebdomadaire) entre `debut` et `fin` inclus,
    d'après la liste de l'employé si donné (sinon celle de la société)."""
    liste = (frappe.db.get_value("Employee", employee, "holiday_list") if employee else None) or liste_par_defaut()
    if not liste:
        return {}
    rows = frappe.get_all("Holiday", filters={"parent": liste, "parenttype": "Holiday List",
                                              "holiday_date": ["between", [str(getdate(debut)), str(getdate(fin))]]},
                          fields=["holiday_date", "description", "weekly_off"], order_by="holiday_date")
    # Une fête qui tombe un dimanche garde la ligne « repos hebdomadaire » (une seule ligne par date dans HRMS) :
    # on la reconnaît à son libellé « <fête> (dimanche) » et on l'affiche quand même.
    return {str(r.holiday_date): (r.description or _("Jour férié")).strip() for r in rows
            if not cint(r.weekly_off) or (r.description or "").strip().lower().endswith("(dimanche)")}


def est_ferie(jour, employee: str | None = None) -> str | None:
    """Le nom du férié ce jour-là, sinon None."""
    jour = getdate(jour)
    return feries(jour, jour, employee).get(str(jour))


def message_ferie(doc) -> str | None:
    """Avertissement pour une tâche posée un jour férié — seulement quand la date est nouvelle ou a changé,
    pour ne pas répéter l'avis à chaque enregistrement."""
    if not doc.get("starts_on"):
        return None
    jour = getdate(doc.starts_on)
    avant = doc.get_doc_before_save() if not doc.is_new() else None
    if avant and avant.get("starts_on") and getdate(avant.starts_on) == jour:
        return None
    nom = est_ferie(jour, doc.get("custom_choix_du_staff"))
    if not nom:
        return None
    return _("⚠️ Le {0} est un jour férié ({1}). La tâche est enregistrée quand même.").format(
        frappe.utils.formatdate(jour, "dd/MM/yyyy"), nom)


def avertir_ferie(doc, method=None):
    msg = message_ferie(doc)
    if msg:
        frappe.msgprint(msg, indicator="orange", alert=True)


COULEUR_FERIE = "#9ca3af"


def evenements_calendrier(debut, fin) -> list:
    """Les fériés de la période pour le Calendrier de travail : une pastille « 🎌 nom » toute la journée, ET un fond gris
    (`rendering: background` de FullCalendar) qui grise toute la colonne du jour en semaine et la case en vue mois."""
    out = []
    for d, nom in feries(debut, fin).items():
        out.append(frappe._dict({
            # Fin = début : FullCalendar prend la fin d'un « toute la journée » comme EXCLUSIVE (23:59 débordait sur le lendemain).
            "doctype": "Holiday", "name": "FERIE-%s" % d, "starts_on": "%s 00:00:00" % d, "ends_on": "%s 00:00:00" % d,
            "all_day": 1, "titre": "🎌 %s" % nom, "color": COULEUR_FERIE, "status": "Open", "ferie": 1,
            "custom_type_dintervention": _("Jour férié"), "subject": nom,
        }))
        out.append(frappe._dict({
            "doctype": "Holiday", "name": "FERIE-FOND-%s" % d, "starts_on": "%s 00:00:00" % d,
            "ends_on": "%s 00:00:00" % frappe.utils.add_days(d, 1), "all_day": 0, "titre": "", "color": COULEUR_FERIE,
            "status": "Open", "ferie": 1, "fond": 1, "rendering": "background",
        }))
    return out
