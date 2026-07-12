"""
Numérotation automatique des factures — `custom_numero_facture`.

Source unique de la logique de numérotation, partagée avec « Facturation Auto ».

Quand une Sales Invoice est créée manuellement sans numéro, on lui attribue un numéro
qui « saute en avant » depuis le dernier numéro utilisé, afin de RÉSERVER un bloc de
trous que la Facturation Auto mensuelle remplira ensuite avec les factures des paiements
Zitouna (commandes livrées, encaissées sur le compte Zitouna).

Le modèle est AUTO-CORRECTEUR :
    reserve = paiements_Zitouna_en_attente + factures_PASSAGER − trous_libres_existants
  - paiements_Zitouna_en_attente : 1 facture par paiement Zitouna éligible non facturé (> 1 DT).
  - factures_PASSAGER : ventes LIVRÉES non facturées du mois → on facture 50 %, réparti en
    factures d'au plus 1000 DT → ceil(0.5 * ventes_livrées_non_facturées / 1000).
Au fil du temps, la Facturation Auto remplit les trous et de nouveaux paiements arrivent,
donc (en_attente − trous_libres) reste petit → les sauts restent raisonnables. La 1re facture
manuelle réserve le bloc ; les suivantes n'incrémentent que de 1 tant que le bloc tient.

Points clés (corrigés par rapport à l'ancien Server Script « Generation N Facture ») :
  - Aucune année codée en dur : ancrage glissant sur 12 mois.
  - Brouillons (docstatus=0) pris en compte : numéros RÉSERVÉS (anti-collision) ET
    valeurs COMPTÉES dans l'estimation.
  - Estimation PASSAGER lisible (50 % / plafond 1000 DT) au lieu du magique 900.
  - Anti-collision : le numéro attribué est garanti libre.
  - Éligibilité des paiements alignée sur « Facturation Auto ».
"""

import math

import frappe
from frappe.utils import getdate, nowdate, get_first_day, add_months, flt

# --- Constantes métier (nommées et commentées) -------------------------------
ZITOUNA_ACCOUNT = "STE430127B - Zitouna - A&S"
# Client exclu de la facturation mensuelle sauf exception explicite (aligné Facturation Auto).
SPECIAL_INCLUDED_CUSTOMER = "Ayman Belguith"

# Fenêtre d'ancrage : on ne considère que les 12 derniers mois (numérotation continue).
ANCHOR_LOOKBACK_MONTHS = 12
# Montant minimal d'un paiement pour générer une facture (aligné Facturation Auto : > 1 DT).
MIN_PAYMENT_AMOUNT = 1.0

# --- Factures PASSAGER (règle métier) ----------------------------------------
# Les ventes LIVRÉES mais NON FACTURÉES du mois sont facturées à 50 % via des
# factures PASSAGER, chacune plafonnée à 1000 DT. On RÉSERVE donc autant de numéros
# que de factures PASSAGER à créer : ceil(0.5 * ventes_livrées_non_facturées / 1000).
PASSAGER_FACTOR = 0.5            # part facturée en PASSAGER (aligné Facturation Auto)
MAX_PASSAGER_INVOICE = 1000.0    # montant maximal d'une facture PASSAGER (DT)


def _anchor(today=None):
    """1er jour du mois, 12 mois avant `today` (ancrage glissant, pas de date en dur)."""
    today = getdate(today or nowdate())
    return get_first_day(add_months(today, -ANCHOR_LOOKBACK_MONTHS))


def used_invoice_numbers(since, include_draft=True):
    """Ensemble des numéros de facture déjà PRIS depuis `since`.

    include_draft=True → inclut les brouillons (docstatus=0) : leurs numéros sont
    réservés (anti-collision).
    """
    docstatus_clause = "docstatus IN (0, 1)" if include_draft else "docstatus = 1"
    rows = frappe.db.sql(
        f"""
        SELECT DISTINCT CAST(custom_numero_facture AS UNSIGNED) AS n
        FROM `tabSales Invoice`
        WHERE {docstatus_clause}
          AND custom_numero_facture REGEXP '^[0-9]+$'
          AND posting_date >= %s
        """,
        (since,),
        as_dict=True,
    )
    return set(int(r["n"]) for r in rows if r.get("n") is not None)


def free_slots_below_max(used):
    """Trous libres (numéros non pris) entre le min et le max de `used`."""
    if not used:
        return set()
    return set(range(min(used) + 1, max(used) + 1)) - used


def eligible_pending_payments(since):
    """Paiements Zitouna éligibles NON encore facturés (aligné « Facturation Auto »).

    Commandes livrées (Fully Delivered ou BL réconcilié), non-Compte-Pro mensuel,
    encaissées sur le compte Zitouna, sans facture liée. Pas de borne supérieure de
    date : tous les paiements en attente jusqu'à aujourd'hui.
    """
    return frappe.db.sql(
        """
        SELECT DISTINCT
            PE.name AS Payment_Entry_Id,
            PE.paid_amount AS Paid_Amount,
            SO.customer AS Customer
        FROM `tabPayment Entry` AS PE
            INNER JOIN `tabPayment Entry Reference` AS PER ON PER.parent = PE.name
            INNER JOIN `tabSales Order` AS SO ON SO.name = PER.reference_name
            INNER JOIN `tabCustomer` AS CUS ON SO.customer = CUS.name
        WHERE
            PE.paid_to = %(zitouna)s
            AND PE.docstatus = 1
            AND PE.posting_date >= %(since)s
            AND PER.reference_doctype = 'Sales Order'
            AND PE.name NOT IN (
                SELECT DISTINCT PER2.parent
                FROM `tabPayment Entry Reference` AS PER2
                WHERE PER2.reference_doctype = 'Sales Invoice'
            )
            AND (
                CUS.custom_generation_facture_mensuelle != 'Oui'
                OR CUS.name = %(special)s
            )
            AND SO.docstatus = 1
            AND SO.status != 'Closed'
            AND (
                SO.delivery_status = 'Fully Delivered'
                OR EXISTS (
                    SELECT 1
                    FROM `tabDelivery Note Item` dni
                        INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
                    WHERE dni.against_sales_order = SO.name
                        AND dn.docstatus = 1
                        AND dn.status != 'Closed'
                        AND dn.custom_reconciliation_stock IS NOT NULL
                )
            )
        """,
        {"zitouna": ZITOUNA_ACCOUNT, "since": since, "special": SPECIAL_INCLUDED_CUSTOMER},
        as_dict=True,
    )


def unbilled_delivered_sales(today):
    """SUM(grand_total) des commandes LIVRÉES et NON FACTURÉES ce mois-ci.

    Base de la facturation PASSAGER (aligné « Facturation Auto » : commandes
    Fully Delivered du mois, sans aucune Sales Invoice liée).

    EXCLUT les commandes ayant déjà un paiement Zitouna en attente : celles-ci
    seront facturées via les factures liées aux paiements (pas en PASSAGER), donc
    on ne doit pas les compter deux fois.
    """
    first_day = get_first_day(getdate(today))
    last_day = frappe.utils.get_last_day(getdate(today))
    rows = frappe.db.sql(
        """
        SELECT SUM(so.grand_total) AS total
        FROM `tabSales Order` so
        WHERE so.docstatus = 1
          AND so.status != 'Closed'
          AND so.delivery_status = 'Fully Delivered'
          AND so.delivery_date BETWEEN %(first_day)s AND %(last_day)s
          AND NOT EXISTS (
              SELECT 1 FROM `tabSales Invoice Item` sii
              WHERE sii.sales_order = so.name AND sii.docstatus = 1
          )
          AND NOT EXISTS (
              SELECT 1
              FROM `tabPayment Entry Reference` per
                  INNER JOIN `tabPayment Entry` pe ON pe.name = per.parent
              WHERE per.reference_doctype = 'Sales Order'
                AND per.reference_name = so.name
                AND pe.paid_to = %(zitouna)s
                AND pe.docstatus = 1
                AND pe.name NOT IN (
                    SELECT DISTINCT per2.parent
                    FROM `tabPayment Entry Reference` per2
                    WHERE per2.reference_doctype = 'Sales Invoice'
                )
          )
        """,
        {"first_day": first_day, "last_day": last_day, "zitouna": ZITOUNA_ACCOUNT},
        as_dict=True,
    )
    return flt(rows[0]["total"]) if rows and rows[0].get("total") else 0.0


def passager_invoice_count(delivered_not_invoiced_total):
    """Nombre de factures PASSAGER à réserver pour la valeur livrée non facturée.

    On facture 50 % de cette valeur, réparti en factures d'au plus 1000 DT.
    """
    target = PASSAGER_FACTOR * flt(delivered_not_invoiced_total)
    if target <= 0:
        return 0, 0.0
    return int(math.ceil(target / MAX_PASSAGER_INVOICE)), round(target, 3)


def compute_breakdown(today=None, include_draft=True):
    """Calcule le prochain numéro ET le détail (pour dry-run / logs / validation).

    Retourne un dict avec toutes les valeurs intermédiaires et `next_numero`.
    """
    today = getdate(today or nowdate())
    since = _anchor(today)

    used = used_invoice_numbers(since, include_draft=include_draft)

    # Aucune facture dans la fenêtre : on démarre au max global connu + 1 (ou 1).
    if not used:
        used_all = used_invoice_numbers("1900-01-01", include_draft=include_draft)
        next_numero = (max(used_all) + 1) if used_all else 1
        return {
            "today": str(today),
            "anchor": str(since),
            "max_used": (max(used_all) if used_all else 0),
            "free_slots": 0,
            "pending_payments_gt1": 0,
            "delivered_not_invoiced": 0.0,
            "passager_target": 0.0,
            "passager_invoices": 0,
            "reserve": 1,
            "next_numero": next_numero,
            "no_history": True,
        }

    max_used = max(used)
    free = free_slots_below_max(used)

    # (1) Factures liées aux paiements Zitouna déjà reçus, non encore facturés.
    pending = eligible_pending_payments(since)
    pending_count = sum(1 for p in pending if flt(p.get("Paid_Amount")) > MIN_PAYMENT_AMOUNT)

    # (2) Factures PASSAGER : 50 % des ventes livrées non facturées du mois,
    #     plafonnées à 1000 DT/facture.
    sales = unbilled_delivered_sales(today)
    passager_count, passager_target = passager_invoice_count(sales)

    # Réserve auto-correctrice : total à couvrir moins les trous déjà libres.
    reserve = max(pending_count + passager_count - len(free), 1)

    candidate = max_used + reserve
    # Anti-collision : garantir un numéro libre (brouillons inclus dans `used`).
    while candidate in used:
        candidate += 1

    return {
        "today": str(today),
        "anchor": str(since),
        "max_used": max_used,
        "free_slots": len(free),
        "pending_payments_gt1": pending_count,
        "delivered_not_invoiced": round(sales, 3),
        "passager_target": passager_target,
        "passager_invoices": passager_count,
        "reserve": reserve,
        "next_numero": candidate,
        "no_history": False,
    }


def compute_next_numero(doc=None, today=None, include_draft=True):
    """Numéro à attribuer à une nouvelle facture. Voir compute_breakdown pour le détail."""
    return compute_breakdown(today=today, include_draft=include_draft)["next_numero"]


def set_numero_facture(doc, method=None):
    """Hook `before_insert` sur Sales Invoice : attribue `custom_numero_facture`.

    N'agit que si le champ est vide (respecte un numéro déjà saisi/importé) et que la
    facture n'est pas une facture d'ouverture.
    """
    if getattr(doc, "custom_numero_facture", None):
        return
    if getattr(doc, "is_opening", None) == "Yes":
        return
    try:
        doc.custom_numero_facture = str(compute_next_numero(doc))
    except Exception:
        # Ne jamais bloquer la création d'une facture à cause de la numérotation auto.
        frappe.log_error(frappe.get_traceback(), "set_numero_facture")
