"""
generer_bl.py — API pour la génération des BL depuis le calendrier Tache de travail.

Règles métier :
  - N'afficher que les employés avec au moins une tâche Entretien/Réparation/Installation/Livraison
  - Tâches AVEC commande_client :
      * Soumettre SO si brouillon (jamais changer le posting_date du SO)
      * Créer/récupérer DN réel, posting_date = date sélectionnée
      * custom_livré_par + custom_véhicle → Entretien / Réparation / Installation uniquement
      * Livraison → posting_date seulement, sans livré_par/véhicle
  - Tâches Entretien/Réparation SANS commande_client :
      * Un seul DN virtuel par employé (brouillon, pour impression)
      * Pas de custom_livré_par / custom_véhicle
      * Articles pris aléatoirement depuis le stock de l'employé (ou défaut)
"""

import random
import math
import frappe

DEFAULT_WAREHOUSE = "Stock Nizar Maddouri - A&S"
TYPES_BL = {"Entretien", "Réparation", "Installation", "Livraison"}
TYPES_VIRTUEL = {"Entretien", "Réparation"}


@frappe.whitelist()
def get_taches_par_date(date):
    """
    Retourne les employés ayant au moins une tâche de type TYPES_BL à cette date.
    """
    taches = frappe.db.sql("""
        SELECT
            t.name,
            t.custom_choix_du_staff AS employee,
            t.custom_type_dintervention AS type_intervention,
            t.commande_client,
            t.afficher_commande,
            t.nom_client,
            t.custom_client,
            e.employee_name,
            e.custom_warehouse
        FROM `tabTache de travail` t
        LEFT JOIN `tabEmployee` e ON e.name = t.custom_choix_du_staff
        WHERE
            t.status = 'Open'
            AND DATE(t.starts_on) = %(date)s
            AND t.custom_choix_du_staff IS NOT NULL
            AND t.custom_choix_du_staff != ''
            AND t.custom_type_dintervention IN ('Entretien', 'Réparation', 'Installation', 'Livraison')
        ORDER BY t.custom_choix_du_staff, t.starts_on
    """, {"date": date}, as_dict=True)

    employees = {}
    for t in taches:
        emp = t.employee
        if emp not in employees:
            vehicle = frappe.db.get_value("Vehicle", {"employee": emp}, "name")
            employees[emp] = {
                "employee": emp,
                "employee_name": t.employee_name or emp,
                "default_vehicle": vehicle or "",
                "warehouse": t.custom_warehouse or DEFAULT_WAREHOUSE,
                "taches": [],
            }
        employees[emp]["taches"].append({
            "name": t.name,
            "type": t.type_intervention,
            "commande_client": t.commande_client,
            "afficher_commande": t.afficher_commande,
            "client": t.custom_client,
            "nom_client": t.nom_client,
        })

    return list(employees.values())


@frappe.whitelist()
def generer_bl_employe(date, employee, vehicle):
    """
    Pour un employé donné à une date donnée :
    1. Tâches avec commande_client → DN réel, posting_date = date sélectionnée
       - Entretien / Réparation / Installation → custom_livré_par + custom_véhicle
       - Livraison → posting_date seulement, PAS de livré_par/véhicle
    2. Tâches Entretien/Réparation SANS commande_client → DN virtuel par employé
       - Articles depuis stock employé (priorisé membrane/pack/filtre/T)
       - Pas de livré_par/véhicle sur ces DNs
    """
    taches = frappe.db.sql("""
        SELECT
            t.name,
            t.custom_type_dintervention AS type_intervention,
            t.commande_client,
            t.afficher_commande,
            t.custom_client AS client,
            t.nom_client,
            e.custom_warehouse
        FROM `tabTache de travail` t
        LEFT JOIN `tabEmployee` e ON e.name = t.custom_choix_du_staff
        WHERE
            t.status = 'Open'
            AND DATE(t.starts_on) = %(date)s
            AND t.custom_choix_du_staff = %(employee)s
            AND t.custom_type_dintervention IN ('Entretien', 'Réparation', 'Installation', 'Livraison')
        ORDER BY t.starts_on
    """, {"date": date, "employee": employee}, as_dict=True)

    emp_doc = frappe.get_doc("Employee", employee)
    warehouse = emp_doc.custom_warehouse or DEFAULT_WAREHOUSE
    emp_name = emp_doc.employee_name

    dns_reels = []
    dns_virtuels = []

    # ── 1. Tâches AVEC commande client ──────────────────────────────────────
    for t in taches:
        if not t.commande_client:
            continue

        so_name = t.commande_client
        so = frappe.get_doc("Sales Order", so_name)

        # Soumettre le SO si brouillon — ne jamais modifier son posting_date
        if so.docstatus == 0:
            so.submit()
            frappe.db.commit()

        dn_name = _get_dn_for_so(so_name)
        is_livraison = (t.type_intervention == "Livraison")

        if not dn_name:
            dn_name = _create_dn_from_so(
                so_name=so_name,
                date=date,
                employee=None if is_livraison else employee,
                vehicle=None if is_livraison else vehicle,
            )
        else:
            # Mettre à jour posting_date (jamais du SO) + livré_par/véhicle sauf Livraison
            # Pour Livraison : vider explicitement ces champs s'ils étaient déjà renseignés
            if is_livraison:
                updates = {
                    "posting_date": date,
                    "custom_livré_par": None,
                    "custom_véhicle": None,
                }
            else:
                updates = {
                    "posting_date": date,
                    "custom_livré_par": employee,
                    "custom_véhicle": vehicle,
                }
            frappe.db.set_value("Delivery Note", dn_name, updates)
            frappe.db.commit()

        if dn_name and dn_name not in dns_reels:
            dns_reels.append(dn_name)

    # ── 2. Tâches Entretien/Réparation SANS commande → DN virtuel ───────────
    taches_sans_commande = [
        t for t in taches
        if not t.commande_client and t.type_intervention in TYPES_VIRTUEL
    ]

    if taches_sans_commande:
        client = next(
            (t.client for t in taches_sans_commande if t.client),
            taches_sans_commande[0].nom_client if taches_sans_commande else None,
        )
        if client:
            dn_virtuel = _create_virtual_dn(
                client=client,
                warehouse=warehouse,
                date=date,
                taches=taches_sans_commande,
                employee=employee,
                vehicle=vehicle,
            )
            if dn_virtuel:
                dns_virtuels.append(dn_virtuel)

    return {
        "dns_reels": dns_reels,
        "dns_virtuels": dns_virtuels,
        "employee_name": emp_name,
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_dn_for_so(so_name):
    """Retourne le nom du premier DN non annulé lié à ce SO."""
    row = frappe.db.sql("""
        SELECT DISTINCT dni.parent
        FROM `tabDelivery Note Item` dni
        JOIN `tabDelivery Note` dn ON dn.name = dni.parent
        WHERE dni.against_sales_order = %s
          AND dn.docstatus != 2
        LIMIT 1
    """, so_name)
    return row[0][0] if row else None


def _create_dn_from_so(so_name, date, employee=None, vehicle=None):
    """
    Crée un DN depuis un SO.
    - posting_date = date sélectionnée (le SO garde son propre posting_date)
    - livré_par + véhicle renseignés uniquement si fournis (pas pour Livraison)
    """
    from erpnext.stock.doctype.delivery_note.delivery_note import make_delivery_note
    dn = make_delivery_note(so_name)
    dn.posting_date = date
    if employee:
        dn.custom_livré_par = employee
    if vehicle:
        dn.custom_véhicle = vehicle
    dn.insert(ignore_permissions=True)
    frappe.db.commit()
    return dn.name


def _create_virtual_dn(client, warehouse, date, taches, employee=None, vehicle=None):
    """
    Crée un DN brouillon pour tâches Entretien/Réparation sans commande.
    - Nom client = client de la tâche
    - livré_par + véhicle renseignés si fournis
    - Articles depuis stock employé, priorisé membrane/pack/filtre/T, qty variées
    """
    company = frappe.defaults.get_global_default("company")

    # Résoudre le customer
    if frappe.db.exists("Customer", client):
        customer_name = client
    else:
        rows = frappe.db.get_all("Customer",
            filters={"customer_name": ["like", f"%{client}%"]},
            pluck="name", limit=1)
        customer_name = rows[0] if rows else None

    if not customer_name:
        return None

    dn = frappe.new_doc("Delivery Note")
    dn.customer = customer_name
    dn.company = company
    dn.posting_date = date
    dn.set_warehouse = warehouse
    dn.run_method("set_missing_values")

    # Appliquer le modèle de taxes (Sans Timbre Fiscal par défaut)
    DEFAULT_TAX_TEMPLATE = "Vente Standard Sans Timbre Fiscale - A&S"
    dn.taxes_and_charges = DEFAULT_TAX_TEMPLATE
    dn.run_method("set_taxes")

    if employee:
        dn.custom_livré_par = employee
    if vehicle:
        dn.custom_véhicle = vehicle

    # Articles depuis le stock de l'employé, fallback sur stock défaut
    used_warehouse = warehouse
    items = _get_stock_items_for_virtual(warehouse, ratio=0.80)
    if not items and warehouse != DEFAULT_WAREHOUSE:
        items = _get_stock_items_for_virtual(DEFAULT_WAREHOUSE, ratio=0.50)
        used_warehouse = DEFAULT_WAREHOUSE

    description_base = "Entretien / Réparation — " + ", ".join(
        filter(None, [t.nom_client or t.client for t in taches])
    )

    for idx, item in enumerate(items):
        dn.append("items", {
            "item_code": item["item_code"],
            "item_name": item["item_name"],
            "description": description_base if idx == 0 else item["item_name"],
            "qty": item["qty"],
            "uom": item["uom"],
            "stock_uom": item["uom"],
            "conversion_factor": 1,
            "warehouse": used_warehouse,
            "rate": item["rate"],
            "amount": item["rate"] * item["qty"],
        })

    original_so_required = frappe.db.get_single_value("Selling Settings", "so_required")
    if original_so_required == "Yes":
        frappe.db.set_single_value("Selling Settings", "so_required", "No")

    try:
        dn.flags.ignore_validate = True
        dn.flags.ignore_mandatory = True
        dn.insert(ignore_permissions=True)
        frappe.db.commit()
    finally:
        if original_so_required == "Yes":
            frappe.db.set_single_value("Selling Settings", "so_required", "Yes")

    return dn.name


@frappe.whitelist()
def delete_virtual_dn(name):
    """Supprime un DN virtuel (brouillon). Toujours en docstatus=0."""
    frappe.delete_doc("Delivery Note", name, ignore_permissions=True, force=True)
    frappe.db.commit()
    return {"deleted": name}


def _get_stock_items_for_virtual(warehouse, ratio=0.80):
    """
    Retourne tous les articles en stock dans le warehouse avec qty = ratio * actual_qty.
    - ratio=0.80 : stock employé → 80 % de la quantité disponible de chaque article
    - ratio=0.50 : stock par défaut (fallback) → 50 % de la quantité disponible
    Les articles prioritaires (membrane/pack/filtre/T) apparaissent en premier.
    """
    selling_price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Vente standard"

    rows = frappe.db.sql("""
        SELECT
            b.item_code,
            i.item_name,
            i.stock_uom AS uom,
            b.actual_qty,
            COALESCE(ip.price_list_rate, 0) AS rate,
            CASE
                WHEN LOWER(i.item_name) REGEXP 'membrane|pack|filtre|\\bT\\b'
                  OR LOWER(i.item_code) REGEXP 'membrane|pack|filtre|\\bT\\b'
                THEN 1 ELSE 0
            END AS is_priority
        FROM `tabBin` b
        JOIN `tabItem` i ON i.name = b.item_code
        LEFT JOIN `tabItem Price` ip
            ON ip.item_code = b.item_code
            AND ip.selling = 1
            AND ip.price_list = %(pricelist)s
        WHERE
            b.warehouse = %(warehouse)s
            AND b.actual_qty > 0
            AND i.disabled = 0
            AND i.is_sales_item = 1
        ORDER BY is_priority DESC, b.item_code
    """, {"warehouse": warehouse, "pricelist": selling_price_list}, as_dict=True)

    if not rows:
        return []

    result = []
    for item in rows:
        # qty = ratio * stock disponible, arrondi au sup, minimum 1
        qty = max(1, math.ceil(float(item.actual_qty) * ratio))
        result.append({
            "item_code": item.item_code,
            "item_name": item.item_name,
            "uom": item.uom,
            "rate": item.rate,
            "qty": qty,
        })

    return result
