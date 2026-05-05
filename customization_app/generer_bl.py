"""
generer_bl.py — API pour la génération des BL depuis le calendrier Tache de travail.

Endpoints:
  - get_taches_par_date(date)  → liste des employés + nb tâches
  - generer_bl_employe(date, employee, vehicle)  → génère/récupère les BLs et retourne les noms
"""

import frappe
from frappe import _

DEFAULT_WAREHOUSE = "Stock Nizar Maddouri - A&S"


@frappe.whitelist()
def get_taches_par_date(date):
    """
    Retourne les employés ayant des tâches ouvertes à cette date,
    avec leur véhicule par défaut (lié via Vehicle.employee).
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
        ORDER BY t.custom_choix_du_staff, t.starts_on
    """, {"date": date}, as_dict=True)

    # Grouper par employé
    employees = {}
    for t in taches:
        emp = t.employee
        if emp not in employees:
            # Trouver le véhicule par défaut lié à cet employé
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
    1. Tâches Livraison/avec commande → soumet SO si brouillon, crée ou récupère le DN, remplit custom_livré_par + custom_véhicle
    2. Tâches Entretien/Réparation/autres sans commande → crée un DN virtuel (non soumis) pour impression
    Retourne la liste des noms de DNs à imprimer.
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
        ORDER BY t.starts_on
    """, {"date": date, "employee": employee}, as_dict=True)

    emp_doc = frappe.get_doc("Employee", employee)
    warehouse = emp_doc.custom_warehouse or DEFAULT_WAREHOUSE
    emp_name = emp_doc.employee_name

    dns_reels = []       # DNs réels (sauvegardés)
    dns_virtuels = []    # DNs virtuels (pour impression uniquement)

    # ── 1. Tâches avec commande client ──────────────────────────────────────
    for t in taches:
        if not t.afficher_commande or not t.commande_client:
            continue

        so_name = t.commande_client
        so = frappe.get_doc("Sales Order", so_name)

        # Soumettre si brouillon
        if so.docstatus == 0:
            so.submit()
            frappe.db.commit()

        # Chercher un DN existant lié à ce SO (non annulé)
        dn_name = _get_dn_for_so(so_name)

        if not dn_name:
            # Créer le DN depuis le SO
            dn_name = _create_dn_from_so(so_name, employee, vehicle)
        else:
            # Mettre à jour custom_livré_par et custom_véhicle
            frappe.db.set_value("Delivery Note", dn_name, {
                "custom_livré_par": employee,
                "custom_véhicle": vehicle,
            })
            frappe.db.commit()

        if dn_name and dn_name not in dns_reels:
            dns_reels.append(dn_name)

    # ── 2. Tâches Entretien/Réparation sans commande → DN virtuel ───────────
    entretien_types = {"Entretien", "Réparation", "Installation", "Visite", "Autre"}
    taches_entretien = [
        t for t in taches
        if (not t.afficher_commande or not t.commande_client)
        and (t.type_intervention in entretien_types or not t.type_intervention)
    ]

    if taches_entretien:
        # Prendre le premier client disponible comme client du BL virtuel
        client = None
        for t in taches_entretien:
            if t.client:
                client = t.client
                break
        if not client and taches_entretien:
            # Utiliser nom_client si pas de lien client
            client = taches_entretien[0].nom_client

        if client:
            dn_virtuel = _create_virtual_dn(
                client=client,
                employee=employee,
                vehicle=vehicle,
                warehouse=warehouse,
                taches=taches_entretien,
            )
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


def _create_dn_from_so(so_name, employee, vehicle):
    """Crée un Delivery Note depuis un Sales Order et remplit les champs custom."""
    from erpnext.stock.doctype.delivery_note.delivery_note import make_delivery_note
    dn = make_delivery_note(so_name)
    dn.custom_livré_par = employee
    dn.custom_véhicle = vehicle
    dn.insert(ignore_permissions=True)
    frappe.db.commit()
    return dn.name


def _create_virtual_dn(client, employee, vehicle, warehouse, taches):
    """
    Crée un DN sauvegardé (brouillon) pour les tâches entretien/réparation.
    Pas d'articles — juste l'en-tête avec client, employé, véhicule.
    Ce DN est brouillon (docstatus=0) et peut être imprimé.
    """
    company = frappe.defaults.get_global_default("company")

    # Vérifier si client est un nom de Customer ou juste un texte
    customer_exists = frappe.db.exists("Customer", client)
    if not customer_exists:
        # Chercher par nom partiel
        rows = frappe.db.get_all("Customer", filters={"customer_name": ["like", f"%{client}%"]}, pluck="name", limit=1)
        customer_name = rows[0] if rows else None
    else:
        customer_name = client

    if not customer_name:
        return None

    dn = frappe.new_doc("Delivery Note")
    dn.customer = customer_name
    dn.company = company
    dn.custom_livré_par = employee
    dn.custom_véhicle = vehicle
    dn.set_warehouse = warehouse

    # Ajouter une ligne placeholder pour que le DN soit valide
    dn.append("items", {
        "item_code": _get_placeholder_item(),
        "qty": 1,
        "warehouse": warehouse,
        "rate": 0,
        "description": "Entretien / Réparation - " + ", ".join(
            [t.nom_client or t.client or "" for t in taches if (t.nom_client or t.client)]
        ),
    })

    dn.insert(ignore_permissions=True)
    frappe.db.commit()
    return dn.name


def _get_placeholder_item():
    """Retourne un article service générique pour les BLs entretien."""
    # Chercher un article "Entretien" ou "Service" existant
    candidates = ["Entretien", "Service Entretien", "Prestation"]
    for c in candidates:
        if frappe.db.exists("Item", c):
            return c
    # Sinon retourner le premier article service
    row = frappe.db.get_all("Item",
        filters={"is_stock_item": 0, "is_sales_item": 1, "disabled": 0},
        pluck="name", limit=1)
    return row[0] if row else None
