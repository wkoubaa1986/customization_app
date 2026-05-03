import json

import frappe
import json
import frappe
from frappe import _


@frappe.whitelist()
def get_custom_tache_events(start, end, filters=None):
    # Define the field mapping for your custom "Tache de travail" doctype
    field_map = frappe._dict({
        "start": "starts_on",     # Field for the start date of the event
        "end": "ends_on",         # Field for the end date of the event
        "title": "titre",         # Title of the event
        "color": "color"          # Color field for the event
    })

    # List of fields to be retrieved from the "Tache de travail" doctype
    fields = [
        field_map.start, field_map.end, field_map.title, "name", field_map.color,
        "custom_choix_du_staff", "custom_employé", "custom_client", "nom_client","custom_type_dintervention",
        "status", "toute_la_journée", "custom_reservation_app","secteur","tel","details_adresse","info_secteur","google_map",
        "subject","raison_annulation","rapport_visite"
    ]

    # If filters are passed, use them; otherwise, default to an empty list
    filters = json.loads(filters) if filters else []

    # Add conditions to filter events within the date range
    start_date = "ifnull(%s, '0001-01-01 00:00:00')" % field_map.start
    end_date = "ifnull(%s, '2199-12-31 00:00:00')" % field_map.end

    filters += [
        ['Tache de travail', start_date, '<=', end],  # Events should start before or on the end date
        ['Tache de travail', end_date, '>=', start]   # Events should end after or on the start date
    ]

    # Ensure that all necessary fields are unique and avoid any duplicates
    fields = list({field for field in fields if field})
    # print(fields)
    
    # Retrieve events from the "Tache de travail" doctype using the filters and fields
    events = frappe.get_list('Tache de travail', fields=fields, filters=filters)

    # Add the custom logic for checking all-day events
    for event in events:
        # Calculate the duration of the event in hours
        if event.get('starts_on') and event.get('ends_on'):
            start_time = frappe.utils.get_datetime(event['starts_on'])
            end_time = frappe.utils.get_datetime(event['ends_on'])
            duration = (end_time - start_time).total_seconds() / 3600  # Convert duration to hours
            
            # Check if the event duration is greater than 7 hours or if 'toute_la_journée' is checked
            if duration > 7 or event.get('toute_la_journée'):
                event['all_day'] = 1
            else:
                event['all_day'] = 0

    # print(events)
    return events

@frappe.whitelist()
def get_data(data=None):
    return {
		"heatmap": True,
		"heatmap_message": _(
			"Ceci est basé sur les commandes client. Voir la chronologie ci-dessous pour plus de détails"
		),
		"fieldname": "customer",
		"non_standard_fieldnames": {
			"Payment Entry": "party",
			"Quotation": "party_name",
			"Opportunity": "party_name",
			"Bank Account": "party",
			"Subscription": "party",
            "Appelle Client": "client",
            "Tache de travail":"custom_client",
		},
		"dynamic_links": {"party_name": ["Customer", "quotation_to"]},
		"transactions": [
			{"label": _("Pre Sales"), "items": ["Opportunity", "Quotation"]},
			{"label": _("Orders"), "items": ["Sales Order", "Delivery Note", "Sales Invoice"]},
			{"label": _("Payments"), "items": ["Payment Entry", "Bank Account", "Dunning"]},
			{
				"label": _("Support"),
				"items": ["Maintenance Schedule","Issue", "Maintenance Visit", "Installation Note", "Warranty Claim"],
			},
			{"label": _("Projects"), "items": ["Project"]},
			{"label": _("Pricing"), "items": ["Pricing Rule"]},
			{"label": _("Subscriptions"), "items": ["Subscription"]},
            {
                "label": _("Tache de travail"),
                "items": ["Tache de travail"]
            },
            # {
            #     "label": _("Liste Appelle Entretien"),
            #     "items": ["Appelle Client"]
            # },
		],
	}

@frappe.whitelist()
def sync_interventions(doc_name):
    """
    Synchronise les tâches du jour pour un document Mes Interventions Employe.
    Equivalent au Server Script, mais stocke aussi commande_client et subject.
    """
    today = frappe.utils.today()
    yesterday = frappe.utils.add_days(today, -1)

    doc = frappe.get_doc("Mes Interventions Employe", doc_name)
    employee_daily = doc.get("employé") or doc.get("employee")

    tasks_today = frappe.get_all(
        "Tache de travail",
        filters={
            "custom_choix_du_staff": employee_daily,
            "starts_on": ["between", [today + " 00:00:00", today + " 23:59:59"]],
            "status": ["!=", "Cancelled"]
        },
        fields=[
            "name",
            "custom_type_dintervention",
            "starts_on",
            "custom_client",
            "nom_client",
            "tel",
            "select_address",
            "details_adresse",
            "google_map",
            "rapport_visite",
            "subject",
            "commande_client",
        ],
        order_by="starts_on asc"
    )

    existing_tasks = []
    for row in doc.tache:
        if row.tache_de_travail:
            existing_tasks.append(row.tache_de_travail)
        if row.source_task:
            existing_tasks.append(row.source_task)

    # Update mutable fields on existing rows
    task_map = {t.name: t for t in tasks_today}
    for row in doc.tache:
        task = task_map.get(row.tache_de_travail) or task_map.get(row.source_task)
        if task:
            if task.get("commande_client") and not row.commande:
                row.commande = task.commande_client
            if task.get("google_map") and not row.google_maps:
                row.google_maps = task.google_map

    added = 0
    for task in tasks_today:
        if task.name in existing_tasks:
            continue

        adresse = task.details_adresse or task.select_address or ""
        client = task.custom_client or task.nom_client or ""

        doc.append("tache", {
            "source_task":       task.name,
            "tache_de_travail":  task.name,
            "intervention":      task.custom_type_dintervention,
            "heure":             task.starts_on,
            "client":            client,
            "tel":               task.tel,
            "adresse":           adresse,
            "google_maps":       task.google_map,
            "remarque":          task.rapport_visite,
            "commande":          task.commande_client or "",
            "nouvelle_tache":    1,
        })

        existing_tasks.append(task.name)
        added += 1

    if added > 0:
        doc.save(ignore_permissions=True)
        frappe.db.commit()

    # Also create stock verification task if yesterday had interventions
    employee_stock = "HR-EMP-00001"
    taches_hier = frappe.get_all(
        "Tache de travail",
        filters={
            "custom_choix_du_staff": employee_daily,
            "starts_on": ["between", [yesterday + " 00:00:00", yesterday + " 23:59:59"]],
            "status": ["!=", "Cancelled"]
        },
        fields=["name"],
        limit_page_length=1
    )

    if taches_hier:
        stock_task_exists = frappe.db.exists("Tache de travail", {
            "custom_choix_du_staff": employee_stock,
            "starts_on": today + " 08:45:00",
            "titre": "Vérification Stock nizar"
        })
        if not stock_task_exists:
            frappe.get_doc({
                "doctype":                  "Tache de travail",
                "custom_choix_du_staff":    employee_stock,
                "custom_type_dintervention": "Autre",
                "titre":                    "Vérification Stock nizar",
                "starts_on":                today + " 08:45:00",
                "ends_on":                  today + " 09:30:00",
                "subject":                  "Vérification Stock nizar",
            }).insert(ignore_permissions=True)
            frappe.db.commit()

    return {"added": added, "doc": doc.name}


def tache_journalier_nizar():
    """
    Scheduled job (30 7 * * *): creates/updates Mes Interventions Employe for
    HR-EMP-00009 and creates the stock verification task if needed.
    """
    today = frappe.utils.today()
    yesterday = frappe.utils.add_days(today, -1)

    employee_daily = "HR-EMP-00009"
    employee_stock = "HR-EMP-00001"

    doc_name = frappe.db.exists("Mes Interventions Employe", {
        "employé": employee_daily,
        "date": today
    })

    if doc_name:
        doc = frappe.get_doc("Mes Interventions Employe", doc_name)
    else:
        doc = frappe.get_doc({
            "doctype": "Mes Interventions Employe",
            "employé": employee_daily,
            "date": today
        })
        doc.insert(ignore_permissions=True)

    tasks_today = frappe.get_all(
        "Tache de travail",
        filters={
            "custom_choix_du_staff": employee_daily,
            "starts_on": ["between", [today + " 00:00:00", today + " 23:59:59"]],
            "status": ["!=", "Cancelled"]
        },
        fields=[
            "name",
            "custom_type_dintervention",
            "starts_on",
            "custom_client",
            "nom_client",
            "tel",
            "select_address",
            "details_adresse",
            "google_map",
            "rapport_visite",
            "subject",
            "commande_client",
        ],
        order_by="starts_on asc"
    )

    existing_tasks = []
    for row in doc.tache:
        if row.tache_de_travail:
            existing_tasks.append(row.tache_de_travail)
        if row.source_task:
            existing_tasks.append(row.source_task)

    # Update mutable fields on existing rows
    task_map = {t.name: t for t in tasks_today}
    for row in doc.tache:
        task = task_map.get(row.tache_de_travail) or task_map.get(row.source_task)
        if task:
            if task.get("commande_client") and not row.commande:
                row.commande = task.commande_client
            if task.get("google_map") and not row.google_maps:
                row.google_maps = task.google_map

    added = 0
    for task in tasks_today:
        if task.name in existing_tasks:
            continue

        adresse = task.details_adresse or task.select_address or ""
        client = task.custom_client or task.nom_client or ""

        doc.append("tache", {
            "source_task":      task.name,
            "tache_de_travail": task.name,
            "intervention":     task.custom_type_dintervention,
            "heure":            task.starts_on,
            "client":           client,
            "tel":              task.tel,
            "adresse":          adresse,
            "google_maps":      task.google_map,
            "remarque":         task.rapport_visite,
            "commande":         task.commande_client or "",
            "nouvelle_tache":   1,
        })
        existing_tasks.append(task.name)
        added += 1

    if added > 0:
        doc.save(ignore_permissions=True)
        frappe.db.commit()

    # Create stock verification task if there were interventions yesterday
    taches_hier = frappe.get_all(
        "Tache de travail",
        filters={
            "custom_choix_du_staff": employee_daily,
            "starts_on": ["between", [yesterday + " 00:00:00", yesterday + " 23:59:59"]],
            "status": ["!=", "Cancelled"]
        },
        fields=["name"],
        limit_page_length=1
    )

    if taches_hier:
        stock_task_exists = frappe.db.exists("Tache de travail", {
            "custom_choix_du_staff": employee_stock,
            "starts_on": today + " 08:45:00",
            "titre": "Vérification Stock nizar"
        })
        if not stock_task_exists:
            frappe.get_doc({
                "doctype":                   "Tache de travail",
                "custom_choix_du_staff":     employee_stock,
                "custom_type_dintervention": "Autre",
                "titre":                     "Vérification Stock nizar",
                "starts_on":                 today + " 08:45:00",
                "ends_on":                   today + " 09:30:00",
                "subject":                   "Vérification Stock nizar",
            }).insert(ignore_permissions=True)
            frappe.db.commit()


# ─── Helpers for on_submit_mes_interventions ─────────────────────────────────

def _attach_file_to_doc(file_url, doctype, docname):
    """Create a File attachment record linking an existing file to a doctype."""
    if not file_url:
        return
    # Skip if already attached
    if frappe.db.exists("File", {
        "file_url": file_url,
        "attached_to_doctype": doctype,
        "attached_to_name": docname,
    }):
        return
    orig = frappe.db.get_value(
        "File", {"file_url": file_url}, ["file_name", "is_private"], as_dict=True
    )
    file_name = (orig.file_name if orig else None) or file_url.split("/")[-1]
    is_private = orig.is_private if orig else 0
    frappe.get_doc({
        "doctype": "File",
        "file_url": file_url,
        "file_name": file_name,
        "attached_to_doctype": doctype,
        "attached_to_name": docname,
        "is_private": is_private,
    }).insert(ignore_permissions=True)


def _cancel_or_delete_so(so_name):
    """Cancel/delete a Sales Order and any related Delivery Notes, ignoring link constraints."""
    if not frappe.db.exists("Sales Order", so_name):
        return

    # 1. Cancel/delete related Delivery Notes first
    dn_names = frappe.get_all(
        "Delivery Note Item",
        filters={"against_sales_order": so_name, "docstatus": ["!=", 2]},
        fields=["distinct parent"],
        as_list=True,
    )
    for (dn_name,) in dn_names:
        dn = frappe.get_doc("Delivery Note", dn_name)
        if dn.docstatus == 1:
            dn.flags.ignore_permissions = True
            dn.flags.ignore_links = True
            dn.cancel()
        frappe.delete_doc("Delivery Note", dn_name, force=True, ignore_permissions=True, ignore_on_trash=True)

    # 2. Cancel/delete the Sales Order
    so = frappe.get_doc("Sales Order", so_name)
    if so.docstatus == 1:
        so.flags.ignore_permissions = True
        so.flags.ignore_links = True
        so.cancel()
    frappe.delete_doc("Sales Order", so_name, force=True, ignore_permissions=True, ignore_on_trash=True)


def _create_so_from_pos(pos_name, extra_items=None):
    """
    Create a draft Sales Order from a POS Invoice.
    - Items from POS Invoice keep their POS warehouse.
    - extra_items (list of dicts) are appended with their original warehouse.
    - Payment schedule is built exclusively from POS payments.
    """
    pos = frappe.get_doc("POS Invoice", pos_name)

    so = frappe.new_doc("Sales Order")
    so.customer = pos.customer
    so.transaction_date = pos.posting_date
    so.delivery_date = pos.posting_date
    so.order_type = "Sales"
    so.company = pos.company
    so.currency = pos.currency or "TND"
    so.selling_price_list = pos.selling_price_list or "Vente standard"
    so.ignore_pricing_rule = 1
    so.custom_type_de_transaction = "Vente directe sans BL et Facture"
    so.taxes_and_charges = "Vente Standard Sans Timbre Fiscale - A&S"

    # Populate taxes rows from the template
    taxes_template = frappe.get_doc("Sales Taxes and Charges Template", "Vente Standard Sans Timbre Fiscale - A&S")
    for tax in taxes_template.taxes:
        so.append("taxes", {
            "charge_type":        tax.charge_type,
            "account_head":       tax.account_head,
            "description":        tax.description or "",
            "rate":               tax.rate,
            "included_in_print_rate": tax.included_in_print_rate,
        })

    # Items from POS Invoice — warehouse = POS store warehouse
    for item in pos.items:
        so.append("items", {
            "item_code":   item.item_code,
            "item_name":   item.item_name,
            "description": item.description or "",
            "qty":         item.qty,
            "rate":        item.rate,
            "warehouse":   item.warehouse,
            "delivery_date": pos.posting_date,
            "uom":         item.uom,
            "stock_uom":   item.stock_uom,
        })

    # Extra items from an old Sales Order — respect their original warehouse
    if extra_items:
        for item in extra_items:
            so.append("items", {
                "item_code":   item["item_code"],
                "item_name":   item.get("item_name", ""),
                "description": item.get("description", ""),
                "qty":         item["qty"],
                "rate":        item["rate"],
                "warehouse":   item["warehouse"],
                "delivery_date": pos.posting_date,
                "uom":         item.get("uom", "Nos"),
                "stock_uom":   item.get("stock_uom", "Nos"),
            })

    # ── Payment schedule from POS payments ──
    # Collect non-zero POS payments
    pos_payments = [(float(p.amount or 0), p.mode_of_payment)
                    for p in pos.payments if float(p.amount or 0) > 0]
    total_payments = sum(a for a, _ in pos_payments)

    if pos_payments and total_payments > 0:
        for idx, (amt, mop) in enumerate(pos_payments):
            is_last = (idx == len(pos_payments) - 1)
            portion = round(amt / total_payments * 100, 6) if not is_last else None
            # Each row must have a unique due_date — increment by idx days
            due = frappe.utils.add_days(frappe.utils.getdate(pos.posting_date), idx)
            so.append("payment_schedule", {
                "due_date":        due,
                "payment_amount":  amt,
                "mode_of_payment": mop,
                "invoice_portion": portion,
            })
        # Force last row's portion to be whatever makes the sum exactly 100
        if so.payment_schedule:
            total_so_far = sum(r.invoice_portion or 0 for r in so.payment_schedule[:-1])
            so.payment_schedule[-1].invoice_portion = round(100 - total_so_far, 6)

    so.flags.ignore_permissions = True
    so.flags.ignore_mandatory = True
    so.insert(ignore_permissions=True)
    return so


def before_save_tache_de_travail(doc, method=None):
    """Set color to light green when status is Completed."""
    if doc.status == "Completed":
        doc.color = "#bbf7d0"


def before_submit_mes_interventions(doc, method=None):
    """
    Triggered automatically on submit of Mes Interventions Employe.
    For each intervention row:
      1. Attaches photo1/photo2/photo3 to the Tache de travail.
      2. Updates google_map on the Tache de travail if not already set.
      3. Sales Order logic:
         - vente only (no commande)  → create SO from POS, link to task
         - commande only (no vente)  → do nothing
         - both vente + commande     → cancel old SO, create new from POS + old SO
      4. If task has commande + all 3 photos + google maps → set status = Completed.
    """
    results = {"created": 0, "updated": 0, "errors": []}

    for row in doc.tache:
        if not row.tache_de_travail:
            continue

        try:
            task = frappe.get_doc("Tache de travail", row.tache_de_travail)
        except Exception as e:
            results["errors"].append(f"Tâche introuvable {row.tache_de_travail}: {str(e)}")
            continue

        task_changed = False

        # 1. Attach photos to Tache de travail + store URLs in liste_photos fields
        for photo_field in ["photo1", "photo2", "photo3"]:
            url = row.get(photo_field)
            if url:
                try:
                    _attach_file_to_doc(url, "Tache de travail", task.name)
                except Exception as e:
                    results["errors"].append(f"Photo {photo_field} → {task.name}: {str(e)}")

        # photo1 + photo2 → liste_photos_avant (newline-separated)
        avant_urls = [row.get(f) for f in ["photo1", "photo2"] if row.get(f)]
        if avant_urls:
            existing = task.get("liste_photos_avant") or ""
            existing_list = [u.strip() for u in existing.splitlines() if u.strip()]
            for u in avant_urls:
                if u not in existing_list:
                    existing_list.append(u)
            task.liste_photos_avant = "\n".join(existing_list)
            task_changed = True

        # photo3 → liste_photos_apres
        apres_url = row.get("photo3")
        if apres_url:
            existing = task.get("liste_photos_apres") or ""
            existing_list = [u.strip() for u in existing.splitlines() if u.strip()]
            if apres_url not in existing_list:
                existing_list.append(apres_url)
            task.liste_photos_apres = "\n".join(existing_list)
            task_changed = True

        # 2. Update google_map on Tache de travail if not already set
        if row.google_maps and not task.google_map:
            task.google_map = row.google_maps
            task_changed = True

        # 3. Sales Order logic
        has_vente    = bool(row.vente    and str(row.vente).strip())
        has_commande = bool(row.commande and str(row.commande).strip())

        if has_vente and not has_commande:
            try:
                so = _create_so_from_pos(row.vente)
                row.commande = so.name
                task.commande_client = so.name
                task_changed = True
                results["created"] += 1
            except Exception as e:
                results["errors"].append(f"Création commande depuis {row.vente}: {str(e)}")

        elif has_vente and has_commande:
            try:
                old_so = frappe.get_doc("Sales Order", row.commande)
                old_items = [
                    {
                        "item_code":   i.item_code,
                        "item_name":   i.item_name,
                        "description": i.description or "",
                        "qty":         i.qty,
                        "rate":        i.rate,
                        "warehouse":   i.warehouse,
                        "delivery_date": None,
                        "uom":         i.uom,
                        "stock_uom":   i.stock_uom,
                    }
                    for i in old_so.items
                ]
                _cancel_or_delete_so(row.commande)
                so = _create_so_from_pos(row.vente, extra_items=old_items)
                row.commande = so.name
                task.commande_client = so.name
                task_changed = True
                results["updated"] += 1
            except Exception as e:
                results["errors"].append(f"Màj commande {row.commande}: {str(e)}")

        # 4. Set status Completed if all conditions met
        has_all_photos = bool(row.photo1 and row.photo2 and row.photo3)
        has_maps       = bool(row.google_maps and str(row.google_maps).strip())
        has_cmd        = bool(row.commande and str(row.commande).strip())

        if has_all_photos and has_maps and has_cmd:
            task.status = "Completed"
            task_changed = True

        if task_changed:
            task.save(ignore_permissions=True)

    # Doc is still in draft — child row changes will be persisted by the submit transaction
    # No need for db.set_value; just modify the row objects directly.

    if results["errors"]:
        frappe.log_error(
            "\n".join(results["errors"]),
            "before_submit_mes_interventions errors"
        )
    return results


@frappe.whitelist()
def delete_pos_invoice(name):
    """Annule et supprime une POS Invoice (draft ou soumise)."""
    if not frappe.db.exists("POS Invoice", name):
        return {"status": "not_found"}

    doc = frappe.get_doc("POS Invoice", name)

    if doc.docstatus == 1:  # soumise → annuler d'abord
        doc.cancel()

    if doc.docstatus in (0, 2):  # brouillon ou annulée → supprimer
        frappe.delete_doc("POS Invoice", name, force=True, ignore_permissions=True)

    frappe.db.commit()
    return {"status": "deleted"}

@frappe.whitelist()
def get_customer_ristourne_dashboard(customer):
    APP_NAME = "booking_ristourne"
    is_installed = frappe.db.exists(
        "Installed Application",
        {"app_name": APP_NAME}
    )
    if not is_installed:
        return {}
    from booking_ristourne.ristourne import generate_ristourne_report
    from booking_ristourne.sales_order import get_available_for_sales_order
    current_ristourne = generate_ristourne_report(customer)
    ristourne_situation = get_available_for_sales_order(customer=customer)
    return {
        "current_ristourne": current_ristourne,
        "ristourne_situation": ristourne_situation
    }
