"""Reprise de la transformation MAT-STE-2026-00171 du 14/10/2026 (décision utilisateur 30/10/2026).

L'écriture avait mis les 1 218 DT consommés sur deux coudes Acc001-B (609,097 pièce) et sorti
l'osmoseur AP-BF-TDS2-5-1500GPD à 0 (cf. `transformation_articles.repartir_valeur`). Ce patch :
  1. annule l'écriture si elle est encore soumise ;
  2. la refait à la MÊME date et heure, mêmes articles consommés (Hall), avec les bonnes valeurs :
     les coudes et le raccord à leur taux habituel (0,097 et 0,001), l'osmoseur prend tout le
     reste, et il entre en Magasins (c'est de là qu'il a été vendu le 15/10) ;
  3. lance la re-valorisation des opérations postérieures (sinon le planificateur horaire s'en charge).
Idempotent : la reprise porte la marque REPRISE dans sa remarque ; si elle existe déjà, rien n'est fait.
"""
from __future__ import annotations

import frappe
from frappe.utils import flt

ORIGINE = "MAT-STE-2026-00171"
DATE, HEURE = "2026-10-14", "15:32:28"
REPRISE = f"reprise de {ORIGINE}"
#: article obtenu -> (entrepôt d'entrée, taux fixé ; None = prend le reste de la valeur)
OBTENUS = {
    "AP-BF-TDS2-5-1500GPD": ("Magasins - A&S", None),
    "Acc001-B": ("Hall - A&S", 0.097),
    "Acc008-K": ("Hall - A&S", 0.001),
}


def lignes_de_reprise(rows: list, obtenus: dict = None) -> tuple:
    """(consommés, obtenus) pour `transformation_articles.valider`, à partir des lignes de
    l'écriture d'origine. Fonction pure. `rows` : [{item_code, qty, s_warehouse, t_warehouse}]."""
    obtenus = OBTENUS if obtenus is None else obtenus
    conso, obt = [], []
    for r in rows:
        if r.get("s_warehouse") and not r.get("t_warehouse"):
            conso.append({"item_code": r["item_code"], "qty": flt(r["qty"]), "warehouse": r["s_warehouse"]})
        elif r.get("t_warehouse"):
            entrepot, taux = obtenus.get(r["item_code"], (r["t_warehouse"], None))
            obt.append({"item_code": r["item_code"], "qty": flt(r["qty"]), "warehouse": entrepot,
                        "taux_fixe": taux})
    return conso, obt


def execute():
    from customization_app import transformation_articles as TA

    if not frappe.db.exists("Stock Entry", ORIGINE):
        return
    if frappe.db.exists("Stock Entry", {"purpose": TA.PURPOSE, "docstatus": 1, "remarks": ["like", f"%{REPRISE}%"]}):
        return
    frappe.flags.ignore_permissions = True
    origine = frappe.get_doc("Stock Entry", ORIGINE)
    rows = [{"item_code": i.item_code, "qty": i.qty, "s_warehouse": i.s_warehouse, "t_warehouse": i.t_warehouse}
            for i in origine.items]
    if origine.docstatus == 1:
        origine.flags.ignore_permissions = True
        origine.cancel()
    conso, obt = lignes_de_reprise(rows)
    res = TA.valider(conso, obt, "Hall - A&S", posting_date=DATE, posting_time=HEURE,
                     remarque=f"{REPRISE} (patch du 30/10/2026)")
    frappe.db.commit()
    print(f"reprise : {res['name']} — consommé {res['total_consomme']} / obtenu {res['total_obtenu']}")
    try:
        from erpnext.stock.doctype.repost_item_valuation.repost_item_valuation import repost_entries
        repost_entries()
        frappe.db.commit()
    except Exception:
        frappe.log_error("reprise_transformation_00171 : re-valorisation différée au planificateur")
