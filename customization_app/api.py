import hashlib
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


def _compute_ar_hash(client, adresse, remarque, sujet=""):
    """SHA-256 court des 4 valeurs source pour détecter un changement."""
    raw = f"{client}|{adresse}|{remarque}|{sujet}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _translate_ar_fields(client, adresse, remarque, changed_fields, sujet=""):
    """
    Appelle OpenAI pour traduire en arabe tunisien uniquement les champs
    listés dans changed_fields ({'client', 'adresse', 'remarque', 'sujet'}).
    Retourne un dict avec seulement les clés des champs traduits.
    """
    if not changed_fields:
        return {}

    try:
        ai = frappe.get_single("AI Settings")
        api_key = ai.openai_api_key
        model   = ai.open_ai_model or "gpt-4o-mini"
    except Exception:
        return {}

    if not api_key:
        return {}

    # Construire la liste des items à traduire
    items_to_translate = []
    if "client" in changed_fields and client:
        items_to_translate.append(f'client: "{client}"')
    if "adresse" in changed_fields and adresse:
        items_to_translate.append(f'adresse: "{adresse}"')
    if "remarque" in changed_fields and remarque:
        items_to_translate.append(f'remarque: "{remarque}"')
    if "sujet" in changed_fields and sujet:
        items_to_translate.append(f'sujet: "{sujet}"')

    if not items_to_translate:
        return {}

    prompt = (
        "أنت مترجم محترف متخصص في الدارجة التونسية المكتوبة بالحروف العربية. "
        "ترجم القيم التالية إلى الدارجة التونسية بالحروف العربية فقط (لا روماني). "
        "أجب فقط بـ JSON صحيح يحتوي على نفس المفاتيح. "
        "ترجم أسماء الأشخاص إلى العربية كما تُنطق (مثال: Amal Hammouda → أمل حمودة). "
        "ترجم العناوين بشكل طبيعي (المدينة، الشارع، إلخ) بالحروف العربية. "
        "ترجم الرسائل والملاحظات للدارجة التونسية. "
        "IMPORTANT: garde tous les chiffres en numerals occidentaux (0-9), ne les convertis JAMAIS en chiffres arabes-indiens (٠١٢٣٤٥٦٧٨٩).\n\n"
        "القيم:\n" + "\n".join(items_to_translate)
    )

    try:
        import urllib.request
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        content = data["choices"][0]["message"]["content"]
        translated = json.loads(content)

        result = {}
        if "client" in translated:
            result["ar_client"]  = translated["client"]
        if "adresse" in translated:
            result["ar_adresse"] = translated["adresse"]
        if "remarque" in translated:
            result["ar_remarque"] = translated["remarque"]
        if "sujet" in translated:
            result["ar_sujet"] = translated["sujet"]
        return result

    except Exception as e:
        frappe.log_error(f"OpenAI translation error: {str(e)}", "translate_ar_fields")
        return {}


@frappe.whitelist()
def sync_interventions(doc_name, is_initial=False):
    lock_key = frappe.cache().make_key(f"sync_lock:{doc_name}")
    acquired = frappe.cache().set(lock_key, 1, nx=True, ex=60)
    if not acquired:
        rows_db = frappe.db.sql(
            "SELECT name, ar_client, ar_adresse, ar_remarque, ar_sujet "
            "FROM `tabIntervention` WHERE parent = %s",
            doc_name, as_dict=True
        )
        return {
            "added": 0, "removed": 0, "doc": doc_name, "locked": True,
            "ar_translations": {r.name: {
                "ar_client":   r.ar_client   or "",
                "ar_adresse":  r.ar_adresse  or "",
                "ar_remarque": r.ar_remarque or "",
                "ar_sujet":    r.ar_sujet    or "",
            } for r in rows_db}
        }
    try:
        return _sync_interventions_inner(doc_name, is_initial)
    finally:
        frappe.cache().delete(lock_key)


def _get_or_translate_task_ar(task_name, client, adresse, sujet):
    """
    Lit ar_* depuis Tache de travail.
    Si absents ou hash différent → traduit via OpenAI et stocke sur la tâche.
    Retourne (dict {ar_client, ar_adresse, ar_sujet}, new_hash).
    """
    new_hash = _compute_ar_hash(client, adresse, "", sujet)
    stored = frappe.db.get_value(
        "Tache de travail", task_name,
        ["ar_hash", "ar_client", "ar_adresse", "ar_sujet"], as_dict=True
    ) or {}

    if stored.get("ar_hash") == new_hash and stored.get("ar_client"):
        return stored, new_hash

    if not (client or adresse or sujet):
        return {}, new_hash

    changed = set()
    if client:  changed.add("client")
    if adresse: changed.add("adresse")
    if sujet:   changed.add("sujet")

    tr = _translate_ar_fields(client, adresse, "", changed, sujet)
    if tr:
        frappe.db.set_value("Tache de travail", task_name, {
            "ar_client":  tr.get("ar_client",  ""),
            "ar_adresse": tr.get("ar_adresse", ""),
            "ar_sujet":   tr.get("ar_sujet",   ""),
            "ar_hash":    new_hash,
        }, update_modified=False)
        return tr, new_hash

    return stored, new_hash


def _sync_interventions_inner(doc_name, is_initial=False):
    doc = frappe.get_doc("Mes Interventions Employe", doc_name)
    employee_daily = doc.get("employé") or doc.get("employee")
    doc_date = str(doc.date) if doc.date else frappe.utils.today()

    tasks = frappe.get_all(
        "Tache de travail",
        filters={
            "custom_choix_du_staff": employee_daily,
            "starts_on": ["between", [doc_date + " 00:00:00", doc_date + " 23:59:59"]],
            "status": ["!=", "Cancelled"]
        },
        fields=[
            "name", "custom_type_dintervention", "starts_on",
            "custom_client", "nom_client", "tel",
            "select_address", "details_adresse", "google_map",
            "rapport_visite", "subject", "commande_client",
        ],
        order_by="starts_on asc"
    )

    active_task_names = {t.name for t in tasks}
    task_map = {t.name: t for t in tasks}

    existing_rows = frappe.db.sql(
        "SELECT name, tache_de_travail, source_task, client, adresse, tel, heure, "
        "ar_client, ar_adresse, ar_sujet, ar_hash "
        "FROM `tabIntervention` WHERE parent = %s ORDER BY idx",
        doc_name, as_dict=True
    )
    source_to_row = {}
    for row in existing_rows:
        src = row.tache_de_travail or row.source_task
        if src:
            source_to_row[src] = row

    added = 0
    removed = 0
    updated = 0

    # ── 1. Supprimer les rows dont la tâche est annulée / supprimée ───────────
    for src in list(source_to_row.keys()):
        if src not in active_task_names:
            frappe.db.delete("Intervention", {"name": source_to_row[src].name})
            del source_to_row[src]
            removed += 1

    # ── 2. Rows existantes : détecter changement via hash ─────────────────────
    for src, row in source_to_row.items():
        task = task_map.get(src)
        if not task:
            continue

        client  = task.custom_client or task.nom_client or row.client or ""
        adresse = task.details_adresse or task.select_address or row.adresse or ""
        tel     = task.tel or ""
        heure   = str(task.starts_on) if task.starts_on else ""
        sujet   = task.subject or ""
        new_hash = _compute_ar_hash(client, adresse, "", sujet)

        # Détecter si les données de base ou les ar_* ont changé
        base_changed = (client != (row.client or "") or adresse != (row.adresse or "")
                        or tel != (row.tel or "") or heure != (row.heure or ""))

        if row.ar_hash == new_hash and row.ar_client and not base_changed:
            continue  # rien n'a changé

        # Distinguer "jamais traduit" (ar_hash vide) de "contenu modifié" (hash différent)
        is_content_changed = bool(row.ar_hash) and row.ar_hash != new_hash
        client_changed = client != (row.client or "")

        # Changement ou première traduction → (re)traduire sur la tâche + MAJ row
        task_ar, new_hash = _get_or_translate_task_ar(src, client, adresse, sujet)

        if client_changed:
            # Le client a changé → reset complet aux valeurs de la tâche
            frappe.db.set_value("Intervention", row.name, {
                "client":           client,
                "adresse":          adresse,
                "tel":              tel,
                "heure":            heure,
                "google_maps":      task.google_map or "",
                "remarque":         task.rapport_visite or "",
                "commande":         task.commande_client or "",
                "vente":            "",
                "photo1":           "",
                "photo2":           "",
                "photo3":           "",
                "nb_appels":        0,
                "nb_appels_detail": "",
                "annule":           0,
                "ar_client":        task_ar.get("ar_client",  ""),
                "ar_adresse":       task_ar.get("ar_adresse", ""),
                "ar_sujet":         task_ar.get("ar_sujet",   ""),
                "ar_hash":          new_hash if task_ar.get("ar_client") else "",
                "nouvelle_tache":   0 if is_initial else 1,
            }, update_modified=False)
        else:
            frappe.db.set_value("Intervention", row.name, {
                "client":         client,
                "adresse":        adresse,
                "tel":            tel,
                "heure":          heure,
                "ar_client":      task_ar.get("ar_client",  ""),
                "ar_adresse":     task_ar.get("ar_adresse", ""),
                "ar_sujet":       task_ar.get("ar_sujet",   ""),
                "ar_hash":        new_hash if task_ar.get("ar_client") else "",
                "nouvelle_tache": 0 if (is_initial or not is_content_changed) else 1,
            }, update_modified=False)
        updated += 1

    # ── 3. Nouvelles tâches → INSERT ──────────────────────────────────────────
    max_idx = frappe.db.sql(
        "SELECT COALESCE(MAX(idx), 0) FROM `tabIntervention` WHERE parent = %s",
        doc_name
    )[0][0] or 0

    now_dt = frappe.utils.now_datetime()
    user   = frappe.session.user or "Administrator"

    for task in tasks:
        if task.name in source_to_row:
            continue

        client   = task.custom_client or task.nom_client or ""
        adresse  = task.details_adresse or task.select_address or ""
        remarque = task.rapport_visite or ""
        sujet    = task.subject or ""

        task_ar, new_hash = _get_or_translate_task_ar(task.name, client, adresse, sujet)

        max_idx += 1
        frappe.db.sql("""
            INSERT INTO `tabIntervention`
                (name, parent, parenttype, parentfield, idx,
                 source_task, tache_de_travail, intervention, heure,
                 client, tel, adresse, google_maps, remarque, commande,
                 nouvelle_tache, ar_client, ar_adresse, ar_remarque, ar_sujet, ar_hash,
                 owner, creation, modified, modified_by, docstatus)
            VALUES
                (%s, %s, 'Mes Interventions Employe', 'tache', %s,
                 %s, %s, %s, %s,
                 %s, %s, %s, %s, %s, %s,
                 %s, %s, %s, %s, %s, %s,
                 %s, %s, %s, %s, 0)
        """, (
            frappe.generate_hash("", 10), doc_name, max_idx,
            task.name, task.name,
            task.custom_type_dintervention or "",
            task.starts_on or "",
            client, task.tel or "",
            adresse, task.google_map or "",
            remarque, task.commande_client or "",
            0 if is_initial else 1,
            task_ar.get("ar_client",  ""),
            task_ar.get("ar_adresse", ""),
            "",
            task_ar.get("ar_sujet",   ""),
            new_hash if task_ar.get("ar_client") else "",
            user, now_dt, now_dt, user,
        ))
        added += 1

    if added or removed or updated:
        frappe.db.commit()

    # Cron du matin → forcer nouvelle_tache=0 sur toutes les lignes
    if is_initial:
        frappe.db.sql(
            "UPDATE `tabIntervention` SET nouvelle_tache=0 WHERE parent=%s AND nouvelle_tache=1",
            doc_name
        )
        frappe.db.commit()

    db_rows = frappe.db.sql(
        "SELECT name, ar_client, ar_adresse, ar_remarque, ar_sujet "
        "FROM `tabIntervention` WHERE parent = %s",
        doc_name, as_dict=True
    )
    return {
        "added": added, "removed": removed, "doc": doc_name,
        "ar_translations": {r.name: {
            "ar_client":   r.ar_client   or "",
            "ar_adresse":  r.ar_adresse  or "",
            "ar_remarque": r.ar_remarque or "",
            "ar_sujet":    r.ar_sujet    or "",
        } for r in db_rows}
    }


@frappe.whitelist()
def test_cron_yesterday():
    """Lance tache_journalier_nizar sur hier pour tester."""
    import customization_app.api as api
    original_today = frappe.utils.today

    yesterday = frappe.utils.add_days(frappe.utils.today(), -1)
    frappe.utils.today = lambda: yesterday
    try:
        tache_journalier_nizar()
        return {"ran_for": yesterday}
    finally:
        frappe.utils.today = original_today


def sync_all_taches_to_interventions():
    """
    Cron (every minute) : pour toutes les Tache de travail actives du jour assignées à HR-EMP-00009,
    propager client, tel, adresse, heure + ar_* vers les rows tabIntervention correspondantes.
    """
    today = frappe.utils.today()
    EMPLOYEE = "HR-EMP-00009"

    tasks = frappe.db.sql("""
        SELECT name, custom_client, nom_client, tel, details_adresse, select_address,
               starts_on, subject, ar_client, ar_adresse, ar_sujet, ar_hash
        FROM `tabTache de travail`
        WHERE custom_choix_du_staff = %s
        AND DATE(starts_on) = %s
        AND status != 'Cancelled'
    """, (EMPLOYEE, today), as_dict=True)

    if not tasks:
        return

    updated = 0
    for task in tasks:
        client  = task.custom_client or task.nom_client or ""
        adresse = task.details_adresse or task.select_address or ""
        tel     = task.tel or ""
        heure   = str(task.starts_on) if task.starts_on else ""

        # Mettre à jour les données de base (client, tel, adresse, heure)
        frappe.db.sql("""
            UPDATE `tabIntervention`
            SET client  = %s,
                adresse = %s,
                tel     = %s,
                heure   = %s
            WHERE (tache_de_travail = %s OR source_task = %s)
        """, (client, adresse, tel, heure, task.name, task.name))

        # Mettre à jour les ar_* si la tâche a des traductions
        if task.ar_client and task.ar_hash:
            frappe.db.sql("""
                UPDATE `tabIntervention`
                SET ar_client  = %s,
                    ar_adresse = %s,
                    ar_sujet   = %s,
                    ar_hash    = %s
                WHERE (tache_de_travail = %s OR source_task = %s)
                AND (ar_hash != %s OR ar_hash IS NULL OR ar_hash = '')
            """, (
                task.ar_client, task.ar_adresse or "", task.ar_sujet or "", task.ar_hash,
                task.name, task.name, task.ar_hash,
            ))

        updated += 1

    if updated:
        frappe.db.commit()


def tache_journalier_nizar():
    """
    Scheduled job (30 7 * * *) — uniquement pour HR-EMP-00009 :
    1. Récupère toutes les Tache de travail actives du jour
    2. Traduit en arabe celles qui ne le sont pas encore (stockage sur la tâche)
    3. Crée le doc Mes Interventions Employe du jour si inexistant
    4. Sync des rows (is_initial=True → nouvelle_tache=0 sur tout)
    5. Crée la tâche vérification stock si besoin
    """
    today     = frappe.utils.today()
    yesterday = frappe.utils.add_days(today, -1)

    EMPLOYEE_DAILY = "HR-EMP-00009"
    EMPLOYEE_STOCK = "HR-EMP-00001"

    # ── 1. Récupérer les tâches du jour pour cet employé ──────────────────────
    tasks_today = frappe.get_all(
        "Tache de travail",
        filters={
            "custom_choix_du_staff": EMPLOYEE_DAILY,
            "starts_on": ["between", [today + " 00:00:00", today + " 23:59:59"]],
            "status": ["!=", "Cancelled"]
        },
        fields=[
            "name", "custom_client", "nom_client",
            "select_address", "details_adresse", "subject",
        ]
    )

    if not tasks_today:
        return

    # ── 2. Traduire toutes les tâches AVANT de créer le doc ───────────────────
    # Logique de traduction centralisée sur Tache de travail :
    # si ar_* manquants ou hash différent → OpenAI + stockage sur la tâche
    for task in tasks_today:
        client  = task.custom_client or task.nom_client or ""
        adresse = task.details_adresse or task.select_address or ""
        sujet   = task.subject or ""
        _get_or_translate_task_ar(task.name, client, adresse, sujet)

    frappe.db.commit()

    # ── 3. Créer le doc du jour si inexistant ─────────────────────────────────
    doc_name = frappe.db.exists("Mes Interventions Employe", {
        "employé": EMPLOYEE_DAILY,
        "date": today
    })

    if not doc_name:
        employee_full_name = frappe.db.get_value("Employee", EMPLOYEE_DAILY, "employee_name") or EMPLOYEE_DAILY
        doc_name = f"{employee_full_name} - {frappe.utils.formatdate(today, 'dd-MM-yyyy')}"
        frappe.get_doc({
            "doctype": "Mes Interventions Employe",
            "name": doc_name,
            "employé": EMPLOYEE_DAILY,
            "date": today
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    # ── 4. Sync des rows — is_initial=True → pas de badge جديد le matin ───────
    sync_interventions(doc_name, is_initial=True)

    # ── 5. Créer tâche vérification stock si interventions hier ───────────────
    taches_hier = frappe.get_all(
        "Tache de travail",
        filters={
            "custom_choix_du_staff": EMPLOYEE_DAILY,
            "starts_on": ["between", [yesterday + " 00:00:00", yesterday + " 23:59:59"]],
            "status": ["!=", "Cancelled"]
        },
        fields=["name"],
        limit_page_length=1
    )

    if taches_hier:
        stock_task_exists = frappe.db.exists("Tache de travail", {
            "custom_choix_du_staff": EMPLOYEE_STOCK,
            "starts_on": today + " 08:45:00",
            "titre": "Vérification Stock nizar"
        })
        if not stock_task_exists:
            frappe.get_doc({
                "doctype":                   "Tache de travail",
                "custom_choix_du_staff":     EMPLOYEE_STOCK,
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
    """Set color + traduire en arabe si client/adresse/sujet ont changé."""
    if doc.status == "Completed":
        doc.color = "#bbf7d0"

    EMPLOYEE = "HR-EMP-00009"
    if doc.custom_choix_du_staff != EMPLOYEE:
        return

    client  = doc.custom_client or doc.nom_client or ""
    adresse = doc.details_adresse or doc.select_address or ""
    sujet   = doc.subject or ""

    if not (client or adresse or sujet):
        return

    new_hash = _compute_ar_hash(client, adresse, "", sujet)

    # Lire le hash stocké en DB (doc.ar_hash est None lors de la création)
    stored_hash = frappe.db.get_value("Tache de travail", doc.name, "ar_hash") or ""

    if stored_hash == new_hash and doc.get("ar_client"):
        return  # rien n'a changé

    changed = set()
    hash_changed = stored_hash != new_hash
    if client  and (hash_changed or not doc.get("ar_client")):  changed.add("client")
    if adresse and (hash_changed or not doc.get("ar_adresse")): changed.add("adresse")
    if sujet   and (hash_changed or not doc.get("ar_sujet")):   changed.add("sujet")

    if not changed:
        return

    try:
        translations = _translate_ar_fields(client, adresse, "", changed, sujet)
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "before_save_tache_de_travail")
        frappe.throw(f"Erreur traduction arabe : {e}")

    if translations:
        # Écrire directement sur doc — sauvegardé avec le reste du document
        doc.ar_client  = translations.get("ar_client",  "")
        doc.ar_adresse = translations.get("ar_adresse", "")
        doc.ar_sujet   = translations.get("ar_sujet",   "")
        doc.ar_hash    = new_hash
        # Mémoriser pour propager après save
        doc._ar_translations_to_propagate = (translations, new_hash)
    else:
        frappe.msgprint(
            "⚠️ Traduction arabe échouée — vérifiez les paramètres OpenAI (AI Settings).",
            alert=True, indicator="orange"
        )


def after_save_tache_de_travail(doc, method=None):
    """Propager toutes les données (ar_* + client/adresse/tel) aux lignes Intervention après save."""
    try:
        client  = doc.custom_client or doc.nom_client or ""
        adresse = doc.details_adresse or doc.select_address or ""
        tel     = doc.tel or ""
        heure   = str(doc.starts_on) if doc.starts_on else ""

        # Toujours mettre à jour client/adresse/tel/heure (changements sans traduction)
        frappe.db.sql("""
            UPDATE `tabIntervention`
            SET client  = %s,
                adresse = %s,
                tel     = %s,
                heure   = %s
            WHERE (tache_de_travail = %s OR source_task = %s)
        """, (client, adresse, tel, heure, doc.name, doc.name))

        # Propager les traductions ar_* si elles ont été recalculées
        data = getattr(doc, "_ar_translations_to_propagate", None)
        if data:
            translations, new_hash = data
            frappe.db.sql("""
                UPDATE `tabIntervention`
                SET ar_client      = %s,
                    ar_adresse     = %s,
                    ar_sujet       = %s,
                    ar_hash        = %s,
                    nouvelle_tache = 1
                WHERE (tache_de_travail = %s OR source_task = %s)
                AND   ar_hash != %s
            """, (
                translations.get("ar_client",  ""),
                translations.get("ar_adresse", ""),
                translations.get("ar_sujet",   ""),
                new_hash,
                doc.name, doc.name,
                new_hash,
            ))

        frappe.db.commit()
    except Exception:
        frappe.log_error(frappe.get_traceback(), "after_save_tache_de_travail")


def _annuler_tache_et_commande(tache_name, so_name):
    """
    Annule la Tache de travail, sa commande (Sales Order) et le bon de livraison
    lié, en ignorant les contraintes de liens.
    """
    # 1. Annuler le bon de livraison lié à la commande
    if so_name and frappe.db.exists("Sales Order", so_name):
        dn_names = frappe.get_all(
            "Delivery Note Item",
            filters={"against_sales_order": so_name, "docstatus": ["!=", 2]},
            fields=["distinct parent"],
            as_list=True,
        )
        for (dn_name,) in dn_names:
            try:
                dn = frappe.get_doc("Delivery Note", dn_name)
                if dn.docstatus == 1:
                    dn.flags.ignore_permissions = True
                    dn.flags.ignore_links = True
                    dn.cancel()
            except Exception as e:
                frappe.log_error(f"Annulation BL {dn_name}: {str(e)}", "annuler_tache")

        # 2. Annuler la commande
        try:
            so = frappe.get_doc("Sales Order", so_name)
            if so.docstatus == 1:
                so.flags.ignore_permissions = True
                so.flags.ignore_links = True
                so.cancel()
        except Exception as e:
            frappe.log_error(f"Annulation SO {so_name}: {str(e)}", "annuler_tache")

    # 3. Annuler la Tache de travail
    if tache_name and frappe.db.exists("Tache de travail", tache_name):
        try:
            frappe.db.set_value("Tache de travail", tache_name, "status", "Cancelled")
        except Exception as e:
            frappe.log_error(f"Annulation tache {tache_name}: {str(e)}", "annuler_tache")


def before_submit_mes_interventions(doc, method=None):
    """
    Triggered automatically on submit of Mes Interventions Employe.
    For each intervention row:
      0. Si annule=1 → annule tache + commande + BL, skip le reste.
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

        # 0. Si la ligne est annulée → annule tache + commande + BL et passe à la suivante
        if row.annule:
            so_to_cancel = row.commande or task.get("commande_client") or ""
            _annuler_tache_et_commande(row.tache_de_travail, so_to_cancel)
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
def delete_doc_nizar_today():
    """Supprime le doc Nizar du jour pour pouvoir le recréer proprement."""
    today = frappe.utils.today()
    rows = frappe.db.sql(
        "SELECT name FROM `tabMes Interventions Employe` WHERE `employé`='HR-EMP-00009' AND date=%s",
        (today,), as_dict=True
    )
    deleted = []
    for r in rows:
        frappe.delete_doc("Mes Interventions Employe", r.name, force=True, ignore_permissions=True)
        deleted.append(r.name)
    frappe.db.commit()
    return {"deleted": deleted}


@frappe.whitelist()
def count_tasks_today():
    """Compte les tâches de Nizar pour aujourd'hui."""
    today = frappe.utils.today()
    count = frappe.db.sql(
        "SELECT COUNT(*) as nb FROM `tabTache de travail` WHERE custom_choix_du_staff='HR-EMP-00009' AND starts_on BETWEEN %(a)s AND %(b)s AND status != 'Cancelled'",
        {"a": today + " 00:00:00", "b": today + " 23:59:59"},
        as_dict=True
    )
    return {"today": today, "count": count[0]["nb"] if count else 0}


@frappe.whitelist()
def check_first_ar():
    """Vérifie les 3 premières lignes traduites en DB."""
    rows = frappe.db.sql(
        "SELECT name, client, ar_client, ar_adresse, ar_hash FROM `tabIntervention` WHERE ar_hash IS NOT NULL LIMIT 3",
        as_dict=True
    )
    return rows


@frappe.whitelist()
def list_docs_nizar():
    """Liste les docs Mes Interventions Employe pour Nizar."""
    rows = frappe.db.sql(
        "SELECT name, date FROM `tabMes Interventions Employe` WHERE `employé`='HR-EMP-00009' ORDER BY date DESC LIMIT 5",
        as_dict=True
    )
    return rows


@frappe.whitelist()
def debug_sync(doc_name):
    """Appelle sync_interventions et retourne un aperçu des ar_translations."""
    result = sync_interventions(doc_name)
    ar = result.get("ar_translations", {})
    # Retourner seulement les 3 premières entrées pour lisibilité
    sample = dict(list(ar.items())[:3])
    return {"total_rows": len(ar), "sample": sample, "added": result.get("added"), "removed": result.get("removed")}


@frappe.whitelist()
def reset_ar_cache():
    """Réinitialise le cache de traduction arabe pour forcer une re-traduction."""
    frappe.db.sql("UPDATE `tabIntervention` SET ar_hash=NULL, ar_client=NULL, ar_adresse=NULL, ar_remarque=NULL, ar_sujet=NULL")
    frappe.db.commit()
    return {"reset": True}


@frappe.whitelist()
def test_translation():
    """Test direct de la traduction OpenAI — retourne le résultat ou l'erreur."""
    try:
        result = _translate_ar_fields(
            client="Amal Hammouda",
            adresse="El Nasr 2, Ariana, Tunisia",
            remarque="test",
            changed_fields={"client", "adresse", "remarque"},
            sujet="صيانة"
        )
        return {"ok": True, "result": result}
    except Exception as e:
        import traceback
        return {"ok": False, "error": str(e), "traceback": traceback.format_exc()}


@frappe.whitelist()
def force_translate_nizar():
    """Force la traduction de toutes les lignes des docs récents de Nizar."""
    docs = frappe.db.sql(
        "SELECT name FROM `tabMes Interventions Employe` WHERE `employé`='HR-EMP-00009' ORDER BY date DESC LIMIT 3",
        as_dict=True
    )
    total = 0
    for d in docs:
        result = force_translate_doc(d.name)
        total += result.get("translated", 0)
    return {"docs_processed": len(docs), "total_translated": total}


@frappe.whitelist()
def force_translate_doc(doc_name):
    """Force la traduction de toutes les lignes d'un doc (bench execute)."""
    doc = frappe.get_doc("Mes Interventions Employe", doc_name)
    translated_count = 0
    errors = []
    for row in doc.tache:
        source = row.tache_de_travail or row.source_task
        if not source:
            continue
        task = frappe.db.get_value("Tache de travail", source, [
            "custom_client", "nom_client", "details_adresse", "select_address",
            "rapport_visite", "subject"
        ], as_dict=True) or {}
        client   = task.get("custom_client") or task.get("nom_client") or row.client or ""
        adresse  = task.get("details_adresse") or task.get("select_address") or row.adresse or ""
        remarque = task.get("rapport_visite") or row.remarque or ""
        sujet    = task.get("subject") or ""
        if not client and not adresse:
            continue
        changed = set()
        if client:   changed.add("client")
        if adresse:  changed.add("adresse")
        if remarque: changed.add("remarque")
        if sujet:    changed.add("sujet")
        try:
            translations = _translate_ar_fields(client, adresse, remarque, changed, sujet)
            if translations and row.name:
                new_hash = _compute_ar_hash(client, adresse, remarque, sujet)
                frappe.db.set_value("Intervention", row.name, {
                    "ar_client":   translations.get("ar_client", ""),
                    "ar_adresse":  translations.get("ar_adresse", ""),
                    "ar_remarque": translations.get("ar_remarque", ""),
                    "ar_sujet":    translations.get("ar_sujet", ""),
                    "ar_hash":     new_hash,
                }, update_modified=False)
                translated_count += 1
                frappe.db.commit()
        except Exception as e:
            errors.append(f"{row.name}: {str(e)}")
    return {"translated": translated_count, "errors": errors}


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
