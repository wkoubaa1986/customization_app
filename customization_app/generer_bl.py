"""
generer_bl.py — API de génération des bons de livraison depuis le calendrier
« Tache de travail ».

Règles métier
-------------
Tâche AVEC commande client :
  - la commande est soumise si elle est en brouillon (make_delivery_note l'exige)
  - le BL existant est réutilisé, sinon il est créé depuis la commande
  - posting_date = date choisie ; custom_livré_par + custom_véhicle renseignés,
    sauf pour le type « Livraison » où ils sont au contraire vidés

Tâche SANS commande client :
  - un BL « virtuel » (brouillon) est créé PAR TÂCHE
  - son contenu vient du « Modele BL Virtuel » du type d'intervention :
    articles et quantités fixes, aucun repli sur le stock
  - pas de modèle actif pour ce type → aucun BL, la tâche est signalée « ignoré »

Les BL virtuels ne servent qu'à l'impression : ils sont supprimés APRÈS que le PDF
fusionné a été produit et enregistré, jamais avant.
"""

import json

import frappe
from frappe.utils import flt

DEFAULT_WAREHOUSE = "Stock Nizar Maddouri - A&S"
DEFAULT_TAX_TEMPLATE = "Vente Standard Sans Timbre Fiscale - A&S"
PRINT_FORMAT = "Aqua World BL"

# Types de tâche qui donnent lieu à un bon de livraison réel (via commande client).
TYPES_BL = ("Entretien", "Réparation", "Installation", "Livraison")

# Les livraisons Aramex partent par transporteur : leur BL n'est pas à imprimer
# avec la tournée. Elles restent proposées, mais décochées par défaut.
PAYMENT_TERMS_ARAMEX = "Livraison Aramex"


# ── Lecture des tâches ───────────────────────────────────────────────────────


def _types_avec_modele():
    """Types d'intervention disposant d'un modèle de BL virtuel actif."""
    if not frappe.db.exists("DocType", "Modele BL Virtuel"):
        return []
    return frappe.get_all("Modele BL Virtuel", filters={"actif": 1}, pluck="name")


@frappe.whitelist()
def get_taches_par_date(date):
    """
    Retourne les employés ayant au moins une tâche imprimable à cette date, avec
    le détail de chaque tâche et ce qui en sera fait (BL réel, BL virtuel, ignoré).
    """
    types = list(dict.fromkeys(list(TYPES_BL) + _types_avec_modele()))
    modeles = set(_types_avec_modele())

    taches = frappe.db.sql(
        """
        SELECT
            t.name,
            t.custom_choix_du_staff AS employee,
            t.custom_type_dintervention AS type_intervention,
            t.commande_client,
            t.nom_client,
            t.custom_client,
            t.starts_on,
            e.employee_name,
            e.custom_warehouse,
            so.payment_terms_template
        FROM `tabTache de travail` t
        LEFT JOIN `tabEmployee` e ON e.name = t.custom_choix_du_staff
        LEFT JOIN `tabSales Order` so ON so.name = t.commande_client
        WHERE
            t.status = 'Open'
            AND DATE(t.starts_on) = %(date)s
            AND t.custom_choix_du_staff IS NOT NULL
            AND t.custom_choix_du_staff != ''
            AND t.custom_type_dintervention IN %(types)s
        ORDER BY t.custom_choix_du_staff, t.starts_on
    """,
        {"date": date, "types": types},
        as_dict=True,
    )

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

        aramex = t.payment_terms_template == PAYMENT_TERMS_ARAMEX

        if t.commande_client and aramex:
            prevu, motif = "reel", f"Commande {t.commande_client} — livraison Aramex"
        elif t.commande_client:
            prevu, motif = "reel", f"Commande {t.commande_client}"
        elif t.type_intervention in modeles:
            prevu, motif = "virtuel", f"Modèle « {t.type_intervention} »"
        else:
            prevu, motif = "ignore", f"Aucun modèle actif pour « {t.type_intervention} »"

        employees[emp]["taches"].append(
            {
                "name": t.name,
                "type": t.type_intervention,
                "commande_client": t.commande_client,
                "client": t.custom_client or t.nom_client or "",
                "heure": frappe.utils.format_datetime(t.starts_on, "HH:mm") if t.starts_on else "",
                "prevu": prevu,
                "motif": motif,
                "aramex": aramex,
            }
        )

    return list(employees.values())


# ── Orchestration ────────────────────────────────────────────────────────────


@frappe.whitelist()
def generer_et_imprimer(date, selections):
    """
    Génère les bons de livraison des tâches sélectionnées, produit le PDF fusionné
    puis — et seulement alors — supprime les BL virtuels.

    selections : [{"employee": ..., "vehicle": ..., "taches": [noms de tâches]}]

    Retourne {file_url, erreur_pdf, rapport, nb_generes, nb_ignores, nb_erreurs}.
    Aucune exception n'est propagée par tâche : chacune produit une ligne de
    compte-rendu, y compris en cas d'échec.
    """
    if isinstance(selections, str):
        selections = json.loads(selections)

    rapport = []
    dns_a_imprimer = []
    virtuels = []

    for sel in selections or []:
        employee = sel.get("employee")
        vehicle = sel.get("vehicle") or None
        noms_taches = sel.get("taches") or []
        if not employee or not noms_taches:
            continue

        emp = frappe.db.get_value(
            "Employee", employee, ["employee_name", "custom_warehouse"], as_dict=True
        ) or frappe._dict()
        warehouse = emp.custom_warehouse or DEFAULT_WAREHOUSE
        employee_name = emp.employee_name or employee

        for nom_tache in noms_taches:
            entree = _traiter_tache(nom_tache, date, employee, employee_name, vehicle, warehouse)
            rapport.append(entree)

            if entree["statut"] == "genere" and entree["dn"]:
                if entree["dn"] not in dns_a_imprimer:
                    dns_a_imprimer.append(entree["dn"])
                if entree["virtuel"] and entree["dn"] not in virtuels:
                    virtuels.append(entree["dn"])

    file_url = None
    erreur_pdf = None

    if dns_a_imprimer:
        try:
            contenu = _construire_pdf(dns_a_imprimer)
            file_url = _sauver_pdf(contenu, date)
        except Exception:
            erreur_pdf = "Le PDF n'a pas pu être produit. Les bons de livraison sont conservés."
            frappe.log_error(title="Générer BL — échec du rendu PDF", message=frappe.get_traceback())

    # Les BL virtuels ne disparaissent qu'une fois le PDF réellement enregistré.
    if file_url and virtuels:
        _supprimer_virtuels(virtuels, rapport)

    return {
        "file_url": file_url,
        "erreur_pdf": erreur_pdf,
        "rapport": rapport,
        "nb_generes": len([r for r in rapport if r["statut"] == "genere"]),
        "nb_ignores": len([r for r in rapport if r["statut"] == "ignore"]),
        "nb_erreurs": len([r for r in rapport if r["statut"] == "erreur"]),
    }


def _traiter_tache(nom_tache, date, employee, employee_name, vehicle, warehouse):
    """Traite une tâche et retourne sa ligne de compte-rendu."""
    entree = {
        "tache": nom_tache,
        "employee": employee,
        "employee_name": employee_name,
        "type": None,
        "client": None,
        "dn": None,
        "virtuel": False,
        "statut": "ignore",
        "message": "",
    }

    t = frappe.db.get_value(
        "Tache de travail",
        nom_tache,
        ["name", "custom_type_dintervention", "commande_client", "custom_client", "nom_client"],
        as_dict=True,
    )
    if not t:
        entree["message"] = "Tâche introuvable."
        return entree

    entree["type"] = t.custom_type_dintervention
    entree["client"] = t.custom_client or t.nom_client or ""

    try:
        if t.commande_client:
            dn = _traiter_avec_commande(t, date, employee, vehicle)
            entree.update(
                dn=dn,
                statut="genere",
                message=f"Bon de livraison de la commande {t.commande_client}.",
            )
        else:
            dn, motif = _creer_bl_virtuel(t, warehouse, date, employee, vehicle)
            if not dn:
                entree["message"] = motif
                return entree
            entree.update(
                dn=dn,
                virtuel=True,
                statut="genere",
                message=f"Bon de chargement depuis le modèle « {t.custom_type_dintervention} ».",
            )
    except Exception as e:
        entree["statut"] = "erreur"
        entree["message"] = str(e)[:200]
        frappe.log_error(
            title=f"Générer BL — tâche {nom_tache}", message=frappe.get_traceback()
        )

    return entree


# ── BL réel, depuis la commande client ───────────────────────────────────────


def _traiter_avec_commande(t, date, employee, vehicle):
    """
    Soumet la commande si nécessaire puis crée ou met à jour son bon de livraison.
    Pour le type « Livraison », livré_par et véhicule sont explicitement vidés.
    """
    so_name = t.commande_client
    so = frappe.get_doc("Sales Order", so_name)

    # make_delivery_note() refuse une commande en brouillon.
    if so.docstatus == 0:
        so.submit()
        frappe.db.commit()

    is_livraison = t.custom_type_dintervention == "Livraison"
    dn_name = _get_dn_for_so(so_name)

    if not dn_name:
        return _create_dn_from_so(
            so_name=so_name,
            date=date,
            employee=None if is_livraison else employee,
            vehicle=None if is_livraison else vehicle,
        )

    updates = {
        "posting_date": date,
        "custom_livré_par": None if is_livraison else employee,
        "custom_véhicle": None if is_livraison else vehicle,
    }
    frappe.db.set_value("Delivery Note", dn_name, updates)
    frappe.db.commit()
    return dn_name


def _get_dn_for_so(so_name):
    """Retourne le nom du premier BL non annulé lié à cette commande."""
    row = frappe.db.sql(
        """
        SELECT DISTINCT dni.parent
        FROM `tabDelivery Note Item` dni
        JOIN `tabDelivery Note` dn ON dn.name = dni.parent
        WHERE dni.against_sales_order = %s
          AND dn.docstatus != 2
        LIMIT 1
    """,
        so_name,
    )
    return row[0][0] if row else None


def _create_dn_from_so(so_name, date, employee=None, vehicle=None):
    """Crée un BL depuis une commande. La commande garde son propre posting_date."""
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


# ── BL virtuel, depuis le modèle du type d'intervention ──────────────────────


def _creer_bl_virtuel(t, warehouse, date, employee, vehicle):
    """
    Crée un BL brouillon pour UNE tâche sans commande, à partir du modèle de son
    type d'intervention. Retourne (nom_du_bl, "") ou (None, motif du refus).
    """
    from customization_app.customize_erpnext.doctype.modele_bl_virtuel.modele_bl_virtuel import (
        get_modele_actif,
    )

    type_intervention = t.custom_type_dintervention
    modele = get_modele_actif(type_intervention)
    if not modele:
        return None, f"Aucun modèle de BL actif pour le type « {type_intervention or '?'} »."

    client = _resoudre_client(t)
    if not client:
        libelle = t.custom_client or t.nom_client or "non renseigné"
        return None, f"Client introuvable ({libelle})."

    dn = frappe.new_doc("Delivery Note")
    dn.customer = client
    dn.company = frappe.defaults.get_global_default("company")
    dn.posting_date = date
    dn.set_warehouse = warehouse
    dn.run_method("set_missing_values")

    dn.taxes_and_charges = DEFAULT_TAX_TEMPLATE
    dn.run_method("set_taxes")

    if employee:
        dn.custom_livré_par = employee
    if vehicle:
        dn.custom_véhicle = vehicle

    for row in modele.articles:
        dn.append("items", _ligne_article(row, warehouse))

    # Les BL virtuels n'ont pas de commande : neutraliser temporairement l'exigence.
    so_required = frappe.db.get_single_value("Selling Settings", "so_required")
    if so_required == "Yes":
        frappe.db.set_single_value("Selling Settings", "so_required", "No")

    try:
        dn.flags.ignore_validate = True
        dn.flags.ignore_mandatory = True
        dn.insert(ignore_permissions=True)
        frappe.db.commit()
    finally:
        if so_required == "Yes":
            frappe.db.set_single_value("Selling Settings", "so_required", "Yes")

    return dn.name, ""


def _resoudre_client(t):
    """custom_client (lien Customer) en priorité, repli sur nom_client en texte."""
    if t.custom_client and frappe.db.exists("Customer", t.custom_client):
        return t.custom_client

    libelle = t.nom_client
    if not libelle:
        return None
    if frappe.db.exists("Customer", libelle):
        return libelle

    rows = frappe.db.get_all(
        "Customer", filters={"customer_name": ["like", f"%{libelle}%"]}, pluck="name", limit=1
    )
    return rows[0] if rows else None


def _ligne_article(row, warehouse):
    """Construit une ligne de BL à partir d'une ligne de modèle (quantité fixe)."""
    item = frappe.db.get_value(
        "Item", row.item_code, ["item_name", "stock_uom", "description", "brand"], as_dict=True
    ) or frappe._dict()
    qty = flt(row.qty) or 1
    rate = _prix_article(row.item_code)

    # « Delivery Note Item » ne porte aucun fetch_from : marque et description
    # doivent être posées ici, sinon le print format perd la sous-ligne
    # descriptive et la marque affichée à côté de la désignation.
    return {
        "item_code": row.item_code,
        "item_name": row.item_name or item.item_name,
        "description": row.description or item.description or row.item_name or item.item_name,
        "brand": item.brand,
        "qty": qty,
        "uom": row.uom or item.stock_uom,
        "stock_uom": item.stock_uom,
        "conversion_factor": 1,
        "warehouse": warehouse,
        "rate": rate,
        "amount": rate * qty,
    }


def _prix_article(item_code):
    price_list = (
        frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Vente standard"
    )
    return flt(
        frappe.db.get_value(
            "Item Price",
            {"item_code": item_code, "selling": 1, "price_list": price_list},
            "price_list_rate",
        )
    )


# ── Impression ───────────────────────────────────────────────────────────────


def _construire_pdf(noms_dn):
    """Fusionne les BL en un seul PDF et retourne son contenu binaire."""
    from pypdf import PdfWriter

    from frappe.utils.print_format import read_multi_pdf

    writer = PdfWriter()
    for nom in noms_dn:
        writer = frappe.get_print(
            "Delivery Note", nom, PRINT_FORMAT, as_pdf=True, output=writer
        )
    return read_multi_pdf(writer)


def _sauver_pdf(contenu, date):
    """Enregistre le PDF fusionné en fichier privé et retourne son URL."""
    fichier = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": f"BL-{date}-{frappe.generate_hash(length=6)}.pdf",
            "is_private": 1,
            "content": contenu,
        }
    ).insert(ignore_permissions=True)
    frappe.db.commit()
    return fichier.file_url


def _supprimer_virtuels(noms_dn, rapport):
    """Supprime les BL virtuels une fois le PDF produit, et l'indique au rapport."""
    supprimes = set()
    for nom in noms_dn:
        try:
            frappe.delete_doc("Delivery Note", nom, ignore_permissions=True, force=True)
            supprimes.add(nom)
        except Exception:
            frappe.log_error(
                title=f"Générer BL — suppression de {nom}", message=frappe.get_traceback()
            )
    frappe.db.commit()

    for entree in rapport:
        if entree["dn"] in supprimes:
            entree["message"] += " Brouillon supprimé après impression."


# ── Compatibilité (ancien flux, conservé le temps d'une version) ─────────────


@frappe.whitelist()
def generer_bl_employe(date, employee, vehicle):
    """
    Obsolète — remplacé par generer_et_imprimer(). Conservé pour ne rien casser si
    un appel subsiste ailleurs. Traite toutes les tâches ouvertes de l'employé.
    """
    types = list(dict.fromkeys(list(TYPES_BL) + _types_avec_modele()))
    noms = frappe.db.sql_list(
        """
        SELECT name FROM `tabTache de travail`
        WHERE status = 'Open' AND DATE(starts_on) = %(date)s
          AND custom_choix_du_staff = %(employee)s
          AND custom_type_dintervention IN %(types)s
        ORDER BY starts_on
    """,
        {"date": date, "employee": employee, "types": types},
    )

    emp = frappe.db.get_value(
        "Employee", employee, ["employee_name", "custom_warehouse"], as_dict=True
    ) or frappe._dict()
    warehouse = emp.custom_warehouse or DEFAULT_WAREHOUSE

    dns_reels, dns_virtuels = [], []
    for nom in noms:
        entree = _traiter_tache(
            nom, date, employee, emp.employee_name or employee, vehicle, warehouse
        )
        if entree["statut"] != "genere" or not entree["dn"]:
            continue
        cible = dns_virtuels if entree["virtuel"] else dns_reels
        if entree["dn"] not in cible:
            cible.append(entree["dn"])

    return {
        "dns_reels": dns_reels,
        "dns_virtuels": dns_virtuels,
        "employee_name": emp.employee_name or employee,
    }


@frappe.whitelist()
def delete_virtual_dn(name):
    """Obsolète — la suppression est désormais faite par generer_et_imprimer()."""
    frappe.delete_doc("Delivery Note", name, ignore_permissions=True, force=True)
    frappe.db.commit()
    return {"deleted": name}
