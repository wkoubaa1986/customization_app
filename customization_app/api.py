import hashlib
import json

import frappe
from frappe import _
from frappe.utils import flt


# ---------------------------------------------------------------------------
# Tache de travail — couleur (SOURCE DE VÉRITÉ UNIQUE)
# Tout calcul de couleur passe par compute_tache_color(). Le serveur tranche
# (before_save) ; le JS ne fait que l'aperçu. Ordre de priorité :
#   statut (Completed/Cancelled) > partenaire > staff > défaut
# ---------------------------------------------------------------------------
PARTNER_USER = "economiqaquasolutions23@gmail.com"

STAFF_COLORS = {
    "HR-EMP-00001": "#449CF0",
    "HR-EMP-00002": "#ECAD4B",
    "HR-EMP-00003": "#AA00AA",
    "HR-EMP-00004": "#761ACB",
    "HR-EMP-00005": "#CB2929",
    "HR-EMP-00006": "#ED6396",
    "HR-EMP-00007": "#888c89",
    "HR-EMP-00008": "#37fab2",
    "HR-EMP-00009": "#e6f542",
}
STAFF_COLOR_DEFAULT = "#888c89"
STATUS_COLORS = {"Completed": "#32CD32", "Cancelled": "#DCDCDC"}
PARTNER_COLOR = "#29def2"


def _client_created_by_partner(customer):
    """Vrai si le Customer a été créé par le compte partenaire."""
    if not customer:
        return False
    return frappe.db.get_value("Customer", customer, "owner") == PARTNER_USER


def _address_google_map(address):
    """Lien Google Map de l'adresse (champ custom_lien_google_map), ou None."""
    if not address:
        return None
    return (frappe.db.get_value("Address", address, "custom_lien_google_map") or "").strip() or None


def compute_tache_color(doc):
    """Couleur unique d'une Tache de travail.
    Priorité : statut (Completed/Cancelled) > client créé par le partenaire (cyan)
    > couleur par employé > défaut.
    NB : le marqueur cyan dépend du créateur du CLIENT, pas de celui de la tâche."""
    status = doc.get("status") or ""
    if status in STATUS_COLORS:
        return STATUS_COLORS[status]
    if _client_created_by_partner(doc.get("custom_client")):
        return PARTNER_COLOR
    return STAFF_COLORS.get(doc.get("custom_choix_du_staff"), STAFF_COLOR_DEFAULT)


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
    """Set color (source unique) + traduire en arabe si client/adresse/sujet ont changé."""
    # Couleur : seule autorité. Priorité statut > partenaire > staff > défaut.
    doc.color = compute_tache_color(doc)

    # Lien Google Map : toujours synchronisé depuis l'adresse affectée,
    # indépendamment de l'utilisateur et du chemin de création (bouton RDV,
    # liste d'appel, rattrapage, formulaire...). N'écrase pas si l'adresse n'a pas de lien.
    gmap = _address_google_map(doc.get("select_address"))
    if gmap:
        doc.google_map = gmap

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
def get_pos_invoice_totals(names):
    """Retourne les totaux (grand_total) pour une liste de POS Invoice.
    Utilise ignore_permissions pour éviter les erreurs d'accès sur les utilisateurs
    n'ayant pas le rôle POS Invoice.
    """
    import json
    if isinstance(names, str):
        names = json.loads(names)
    if not names:
        return []
    rows = frappe.db.get_all(
        "POS Invoice",
        filters=[["name", "in", names]],
        fields=["name", "grand_total"],
        ignore_permissions=True,
    )
    return rows


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

@frappe.whitelist()
def get_user_pos_profile():
    """Retourne le POS Profile assigné à l'utilisateur courant, ou Vente Nizar par défaut."""
    DEFAULT_POS_PROFILE = "Vente Nizar"
    user = frappe.session.user
    if not user or user == "Guest":
        return DEFAULT_POS_PROFILE
    # Chercher un profil assigné directement à cet utilisateur
    profile = frappe.db.get_value(
        "POS Profile User",
        {"user": user},
        "parent"
    )
    if not profile:
        # Fallback : vérifier si le profil par défaut existe
        if frappe.db.exists("POS Profile", DEFAULT_POS_PROFILE):
            return DEFAULT_POS_PROFILE
    return profile or DEFAULT_POS_PROFILE


def on_delivery_note_cancel(doc, method=None):
	"""
	When a Delivery Note is cancelled via ERPNext standard cancel button,
	reset the workflow_state to 'Annulé' so the list view shows the correct status.
	"""
	if doc.workflow_state != "Annulé":
		frappe.db.set_value("Delivery Note", doc.name, "workflow_state", "Annulé")


def on_sales_order_cancel(doc, method=None):
	"""
	When a Sales Order is cancelled, ERPNext cascade-cancels linked Delivery Notes
	using a bulk method that bypasses the DN's own on_cancel/after_cancel hooks.
	This hook fires on the SO itself and fixes all linked DN workflow_states.
	"""
	linked_dns = frappe.db.sql("""
		SELECT DISTINCT dn.name
		FROM `tabDelivery Note` dn
		INNER JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
		WHERE dni.against_sales_order = %s
		  AND dn.docstatus = 2
		  AND dn.workflow_state != 'Annulé'
	""", doc.name, as_dict=True)

	for row in linked_dns:
		frappe.db.set_value("Delivery Note", row.name, "workflow_state", "Annulé")


# ---------------------------------------------------------------------------
# CAISSE ESPECES -- Dashboard API
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_caisse_dashboard(d1, d2):
    """
    Calls get_caisse_situation_api Server Script and returns structured caisse data.
    """
    frappe.has_permission("Caisse Especes Config", throw=True)

    # Server Scripts of type "API" must be executed via execute_method().
    # The script uses start_date / end_date as parameter names (visible in its code).
    # The document name has spaces: "get caisse situation api"
    frappe.form_dict["start_date"] = d1
    frappe.form_dict["end_date"]   = d2
    script_doc = frappe.get_doc("Server Script", "get caisse situation api")
    script_doc.execute_method()

    # API Server Scripts set frappe.response["message"] directly
    message = frappe.response.get("message") or []

    if isinstance(message, list):
        ventes_raw   = message[0] if len(message) > 0 else []
        achats_raw   = message[1] if len(message) > 1 else []
        depenses_raw = message[2] if len(message) > 2 else []
        versems_raw  = message[3] if len(message) > 3 else []
    else:
        ventes_raw   = message.get("ventes", [])
        achats_raw   = message.get("achats", [])
        depenses_raw = message.get("depenses", [])
        versems_raw  = message.get("versements", [])

    def flt(v):
        try:
            return float(v or 0)
        except Exception:
            return 0.0

    entrees_detail = []
    for fac in (ventes_raw or []):
        especes = flt(fac.get("especes", 0))
        if especes > 0:
            entrees_detail.append({
                "date":           str(fac.get("invoice_date", "")),
                "invoice_number": fac.get("invoice_number", ""),
                "erp_name":       fac.get("erp_name", ""),
                "client":         fac.get("client", ""),
                "montant":        especes,
            })

    sorties_achat = []
    for ach in (achats_raw or []):
        especes = flt(ach.get("especes", 0))
        if especes > 0:
            sorties_achat.append({
                "date":           str(ach.get("invoice_date", "")),
                "invoice_number": ach.get("invoice_number", ""),
                "supplier":       ach.get("supplier", ""),
                "montant":        especes,
            })

    sorties_dep = []
    for dep in (depenses_raw or []):
        especes = flt(dep.get("especes", 0))
        if especes > 0:
            sorties_dep.append({
                "date":                 str(dep.get("entry_date", "")),
                "journal_entry_number": dep.get("journal_entry_number", ""),
                "description":          dep.get("description", ""),
                "montant":              especes,
            })

    versements = []
    for ver in (versems_raw or []):
        montant = flt(ver.get("montant", 0))
        if montant > 0:
            versements.append({
                "date":                 str(ver.get("entry_date", "")),
                "journal_entry_number": ver.get("journal_entry_number", ""),
                "description":          ver.get("description", ""),
                "montant":              montant,
            })

    return {
        "entrees_detail": entrees_detail,
        "sorties_achat":  sorties_achat,
        "sorties_dep":    sorties_dep,
        "versements":     versements,
    }


@frappe.whitelist()
def get_caisse_config():
    """Returns saved Caisse Especes Config (dates + solde_initial + excluded_versements)."""
    frappe.has_permission("Caisse Especes Config", throw=True)
    try:
        doc = frappe.get_single("Caisse Especes Config")
        return {
            "date_debut": str(doc.date_debut) if doc.date_debut else None,
            "date_fin":   str(doc.date_fin)   if doc.date_fin   else None,
            "solde_initial": doc.solde_initial or 0,
            "excluded_versements": [
                {
                    "versement_ref": row.versement_ref,
                    "description":   row.description,
                    "montant":       row.montant,
                }
                for row in (doc.excluded_versements or [])
            ],
        }
    except Exception:
        return {"date_debut": None, "date_fin": None, "solde_initial": 0, "excluded_versements": []}


@frappe.whitelist()
def save_caisse_config(solde_initial, excluded_versements, date_debut=None, date_fin=None):
    """Saves dates + solde_initial + excluded_versements to Caisse Especes Config (Single)."""
    frappe.has_permission("Caisse Especes Config", ptype="write", throw=True)
    if isinstance(excluded_versements, str):
        excluded_versements = json.loads(excluded_versements)
    doc = frappe.get_single("Caisse Especes Config")
    if date_debut:
        doc.date_debut = date_debut
    if date_fin:
        doc.date_fin = date_fin
    doc.solde_initial = float(solde_initial or 0)
    doc.set("excluded_versements", [])
    for v in (excluded_versements or []):
        doc.append("excluded_versements", {
            "versement_ref": v.get("versement_ref", ""),
            "description":   v.get("description", ""),
            "montant":       float(v.get("montant", 0)),
        })
    doc.save(ignore_permissions=False)
    frappe.db.commit()
    return {"status": "ok"}


@frappe.whitelist()
def get_caisse_solde():
    """
    Number Card -- returns Solde Caisse.
    d1: from saved config (or 1st of current month if not set).
    d2: always last day of current month (dynamic).
    """
    frappe.has_permission("Caisse Especes Config", throw=True)
    import datetime
    import calendar
    try:
        config  = get_caisse_config()
        today   = datetime.date.today()
        last_day = calendar.monthrange(today.year, today.month)[1]
        d1 = config.get("date_debut") or today.replace(day=1).isoformat()
        d2 = today.replace(day=last_day).isoformat()   # always current month end
        data        = get_caisse_dashboard(d1, d2)
        initial     = float(config.get("solde_initial") or 0)
        excluded_refs = {v["versement_ref"] for v in config.get("excluded_versements", [])}
        entrees    = sum(float(r["montant"]) for r in data["entrees_detail"])
        sorties    = sum(float(r["montant"]) for r in data["sorties_achat"]) \
                   + sum(float(r["montant"]) for r in data["sorties_dep"])
        versements = sum(
            float(r["montant"]) for r in data["versements"]
            if (r.get("journal_entry_number") or r.get("ref", "")) not in excluded_refs
        )
        solde = round(initial + entrees - sorties - versements, 3)
        return {"value": solde, "route": "caisse-especes"}
    except Exception:
        return {"value": 0, "route": "caisse-especes"}


# =============================================================================
# RDV Libre — Calendrier workspace button (bypasses sharing for allowed users)
# =============================================================================

_RDV_LIBRE_ALLOWED = {"economiqaquasolutions23@gmail.com", "Administrator"}


@frappe.whitelist()
def search_customer_all(doctype, txt, searchfield, start, page_len, filters):
    """
    Search ALL customers ignoring share/permission restrictions.
    Called by Frappe's search_widget when used as a link query.
    """
    rows = frappe.db.sql(
        """
        SELECT name, customer_name
        FROM `tabCustomer`
        WHERE (name LIKE %(txt)s OR customer_name LIKE %(txt)s OR custom_liste_telephone LIKE %(txt)s)
        ORDER BY name
        LIMIT %(page_len)s OFFSET %(start)s
        """,
        {"txt": f"%{txt}%", "page_len": int(page_len), "start": int(start)},
    )
    return rows


@frappe.whitelist()
def get_customer_info_all(customer):
    """
    Return addresses, secteur and telephone for a customer, ignoring permissions.
    """
    addresses = frappe.db.sql(
        """
        SELECT a.name, a.address_line1, a.address_line2, a.city,
               a.pincode, a.state, a.country, a.custom_lien_google_map
        FROM `tabAddress` a
        JOIN `tabDynamic Link` dl ON dl.parent = a.name
        WHERE dl.link_doctype = 'Customer' AND dl.link_name = %s
        """,
        (customer,),
        as_dict=True,
    )

    cust = frappe.db.get_value(
        "Customer",
        customer,
        ["customer_name", "custom_liste_telephone"],
        as_dict=True,
    ) or {}

    # Secteur from first linked address (via Dynamic Link child table)
    secteur_row = frappe.db.sql(
        """
        SELECT a.custom_secteur
        FROM `tabAddress` a
        JOIN `tabDynamic Link` dl ON dl.parent = a.name
        WHERE dl.link_doctype = 'Customer' AND dl.link_name = %s
        AND a.custom_secteur IS NOT NULL AND a.custom_secteur != ''
        LIMIT 1
        """,
        (customer,),
    )
    secteur = secteur_row[0][0] if secteur_row else ""

    return {
        "addresses": addresses,
        "secteur": secteur,
        "telephone": cust.get("custom_liste_telephone") or "",
        "customer_name": cust.get("customer_name") or customer,
    }


@frappe.whitelist()
def save_investigation_note(tache, note):
    """
    Save an investigation note on a Tache de travail and mark it as investigated.
    """
    doc = frappe.get_doc("Tache de travail", tache)
    doc.rapport_visite = note
    doc.recontacte = "Investigué"
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return True


@frappe.whitelist()
def marquer_tache_rattrapee(tache, raison="planifié"):
    """
    Mark a Tache de travail as handled (planifié, contacté, etc.)
    so it disappears from the Rattrapage report.
    """
    frappe.db.set_value("Tache de travail", tache, "recontacte", raison,
                        update_modified=False)
    frappe.db.commit()
    return True


# =====================================================================
#  RELANCE PAIEMENTS CLIENTS (Dettes / Chèques / Traites sans provision)
#  Source de vérité : écritures de paiement (Payment Entry) uniquement.
#  Lecture seule — aucune écriture comptable n'est modifiée.
# =====================================================================

# Comptes ciblés et leur clé logique + libellé court pour les messages.
RELANCE_ACCOUNTS = {
    "Dettes - A&S": {"key": "dettes", "label": "Dettes"},
    "Chèques sans provision - A&S": {"key": "cheques", "label": "Chèques sans provision"},
    "Traite Bancaire sans provision - A&S": {"key": "traites", "label": "Traites sans provision"},
}


def _relance_account_list():
    return list(RELANCE_ACCOUNTS.keys())


# Marqueur permettant de retrouver uniquement les commentaires créés par cette UI.
RELANCE_MARKER = "[RELANCE-PAIEMENT]"


def _log_relance_comment(customer, type_relance, note=""):
    """
    Enregistre une trace de relance sous forme de commentaire sur la fiche client.
    Le contenu commence par RELANCE_MARKER pour pouvoir l'extraire ensuite.
    Format : [RELANCE-PAIEMENT] | <type> | <note>
    """
    if not customer or not frappe.db.exists("Customer", customer):
        return
    note = (note or "").strip()
    content = f"{RELANCE_MARKER} | {type_relance} | {note}"
    cmt = frappe.get_doc({
        "doctype": "Comment",
        "comment_type": "Comment",
        "reference_doctype": "Customer",
        "reference_name": customer,
        "content": content,
    })
    cmt.insert(ignore_permissions=True)
    return cmt.name


@frappe.whitelist()
def enregistrer_relance_telephone(customer, commentaire=""):
    """
    Enregistre une relance téléphonique : ajoute un commentaire tracé
    sur la fiche client avec la date courante.
    """
    if not customer:
        frappe.throw(_("Client manquant."))
    name = _log_relance_comment(customer, "Téléphone", commentaire)
    frappe.db.commit()
    return {"ok": True, "comment": name}


@frappe.whitelist()
def get_historique_relances(customer):
    """
    Retourne l'historique des relances de ce client (uniquement celles
    créées par cette interface), trié du plus récent au plus ancien.
    """
    if not customer:
        return []
    rows = frappe.db.sql(
        """
        SELECT name, content, creation, owner
        FROM `tabComment`
        WHERE reference_doctype = 'Customer'
          AND reference_name = %(cust)s
          AND comment_type = 'Comment'
          AND content LIKE %(marker)s
        ORDER BY creation DESC
        """,
        {"cust": customer, "marker": "%" + RELANCE_MARKER + "%"},
        as_dict=True,
    )
    out = []
    for r in rows:
        # Format attendu : [RELANCE-PAIEMENT] | <type> | <note>
        parts = (r.content or "").split("|", 2)
        type_relance = parts[1].strip() if len(parts) > 1 else "—"
        note = parts[2].strip() if len(parts) > 2 else ""
        out.append({
            "name": r.name,
            "date": frappe.utils.format_datetime(r.creation, "dd/MM/yyyy HH:mm"),
            "type": type_relance,
            "note": note,
            "user": r.owner,
        })
    return out


@frappe.whitelist()
def envoyer_relance_test(telephone=None, email=None, message=None, channel="both"):
    """
    Envoi de TEST : envoie un message vers un numéro et/ou un email arbitraire,
    sans toucher à un client ni créer de trace. Sert à valider SMS / Email.
    """
    message = (message or "Ceci est un test de relance — AquaWorld & Servicing.").strip()
    channels = ["sms", "email"] if channel == "both" else [channel]
    sent, failed = [], []

    for ch in channels:
        try:
            if ch == "sms":
                phone = (telephone or "").strip()
                if not phone:
                    failed.append({"channel": "sms", "reason": "Téléphone manquant"})
                    continue
                from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import (
                    _send_sms_with_fallback,
                )
                _send_sms_with_fallback([phone], message)
                sent.append({"channel": "sms", "to": phone})
            elif ch == "email":
                mail = (email or "").strip()
                if not mail:
                    failed.append({"channel": "email", "reason": "Email manquant"})
                    continue
                frappe.sendmail(
                    recipients=[mail],
                    subject="[TEST] Relance Paiements - AquaWorld & Servicing",
                    message=message.replace("\n", "<br>"),
                )
                sent.append({"channel": "email", "to": mail})
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Relance Paiements: échec test")
            failed.append({"channel": ch, "reason": str(e)[:120]})

    return {"sent": sent, "failed": failed}



@frappe.whitelist()
def get_relance_clients(search=None, customer_group=None, debt_type=None):
    """
    Agrège, par client, les montants en attente sur les 3 comptes ciblés,
    en se basant UNIQUEMENT sur les écritures de paiement (Payment Entry).

    Le montant net par compte = SUM(debit) - SUM(credit) de la GL Entry,
    en récupérant le client via le Payment Entry (party_type = 'Customer').

    Retourne {"clients": [...], "kpi": {...}}.
    """
    accounts = _relance_account_list()

    rows = frappe.db.sql(
        """
        SELECT
            pe.party                         AS customer,
            cust.customer_name               AS customer_name,
            cust.customer_group              AS customer_group,
            cust.custom_liste_telephone      AS telephone,
            cust.email_id                    AS email,
            gle.account                      AS account,
            SUM(gle.debit - gle.credit)      AS montant
        FROM `tabGL Entry` gle
        INNER JOIN `tabPayment Entry` pe
            ON pe.name = gle.voucher_no
        LEFT JOIN `tabCustomer` cust
            ON cust.name = pe.party
        WHERE gle.voucher_type = 'Payment Entry'
          AND gle.is_cancelled = 0
          AND gle.account IN %(accounts)s
          AND pe.party_type = 'Customer'
          AND pe.party IS NOT NULL
        GROUP BY pe.party, gle.account
        """,
        {"accounts": accounts},
        as_dict=True,
    )

    # Regroupement par client
    clients = {}
    for r in rows:
        cust = r.customer
        if cust not in clients:
            clients[cust] = {
                "customer": cust,
                "customer_name": r.customer_name or cust,
                "customer_group": r.customer_group or "",
                "telephone": r.telephone or "",
                "email": r.email or "",
                "dettes": 0.0,
                "cheques": 0.0,
                "traites": 0.0,
                "total": 0.0,
            }
        key = RELANCE_ACCOUNTS.get(r.account, {}).get("key")
        if key:
            clients[cust][key] += float(r.montant or 0)

    # Total + arrondis + filtrage des soldes nuls/négatifs
    result = []
    for c in clients.values():
        c["total"] = round(c["dettes"] + c["cheques"] + c["traites"], 3)
        c["dettes"] = round(c["dettes"], 3)
        c["cheques"] = round(c["cheques"], 3)
        c["traites"] = round(c["traites"], 3)
        if c["total"] <= 0.009:
            continue  # rien à relancer

        # Filtre type de dette
        if debt_type == "dettes" and c["dettes"] <= 0.009:
            continue
        if debt_type == "cheques" and c["cheques"] <= 0.009:
            continue
        if debt_type == "traites" and c["traites"] <= 0.009:
            continue

        # Filtre groupe client
        if customer_group and c["customer_group"] != customer_group:
            continue

        # Recherche texte (nom ou téléphone)
        if search:
            s = search.strip().lower()
            hay = f"{c['customer_name']} {c['customer']} {c['telephone']}".lower()
            if s not in hay:
                continue

        result.append(c)

    # Tri par total décroissant
    result.sort(key=lambda x: x["total"], reverse=True)

    kpi = {
        "total_a_relancer": round(sum(c["total"] for c in result), 3),
        "nb_clients": len(result),
        "total_dettes": round(sum(c["dettes"] for c in result), 3),
        "total_cheques": round(sum(c["cheques"] for c in result), 3),
        "total_traites": round(sum(c["traites"] for c in result), 3),
    }

    return {"clients": result, "kpi": kpi}


@frappe.whitelist()
def get_relance_detail(customer):
    """
    Détail de toutes les écritures de paiement (Payment Entry) d'un client
    sur les 3 comptes ciblés : date, compte, n° de pièce, débit/crédit,
    statut, remarque, et les pièces liées (Sales Order / Sales Invoice)
    via Payment Entry Reference.
    """
    accounts = _relance_account_list()

    rows = frappe.db.sql(
        """
        SELECT
            gle.posting_date                 AS posting_date,
            gle.account                      AS account,
            gle.voucher_type                 AS voucher_type,
            gle.voucher_no                   AS voucher_no,
            gle.debit                        AS debit,
            gle.credit                       AS credit,
            gle.remarks                      AS remarks,
            pe.docstatus                     AS docstatus,
            pe.reference_no                  AS reference_no,
            pe.reference_date                AS reference_date,
            pe.remarks                       AS pe_remarks
        FROM `tabGL Entry` gle
        INNER JOIN `tabPayment Entry` pe
            ON pe.name = gle.voucher_no
        WHERE gle.voucher_type = 'Payment Entry'
          AND gle.is_cancelled = 0
          AND gle.account IN %(accounts)s
          AND pe.party_type = 'Customer'
          AND pe.party = %(customer)s
        ORDER BY gle.posting_date DESC, gle.voucher_no
        """,
        {"accounts": accounts, "customer": customer},
        as_dict=True,
    )

    # Pièces liées par Payment Entry
    voucher_nos = list({r.voucher_no for r in rows})
    refs_map = {}
    # Mapping voucher -> ensemble des Sales Orders concernées (pour les tâches)
    voucher_orders = {}
    all_orders = set()
    si_names = set()
    if voucher_nos:
        refs = frappe.db.sql(
            """
            SELECT parent, reference_doctype, reference_name, allocated_amount
            FROM `tabPayment Entry Reference`
            WHERE parent IN %(vouchers)s
            """,
            {"vouchers": voucher_nos},
            as_dict=True,
        )
        for ref in refs:
            refs_map.setdefault(ref.parent, []).append({
                "reference_doctype": ref.reference_doctype,
                "reference_name": ref.reference_name,
                "allocated_amount": float(ref.allocated_amount or 0),
            })
            if ref.reference_doctype == "Sales Order":
                voucher_orders.setdefault(ref.parent, set()).add(ref.reference_name)
                all_orders.add(ref.reference_name)
            elif ref.reference_doctype == "Sales Invoice":
                si_names.add((ref.parent, ref.reference_name))

    # Résoudre les Sales Orders à partir des Sales Invoice (via Sales Invoice Item)
    if si_names:
        si_list = list({s[1] for s in si_names})
        si_so = frappe.db.sql(
            """
            SELECT DISTINCT parent AS sales_invoice, sales_order
            FROM `tabSales Invoice Item`
            WHERE parent IN %(si)s AND sales_order IS NOT NULL AND sales_order != ''
            """,
            {"si": si_list},
            as_dict=True,
        )
        si_to_so = {}
        for row in si_so:
            si_to_so.setdefault(row.sales_invoice, set()).add(row.sales_order)
            all_orders.add(row.sales_order)
        for voucher, si_name in si_names:
            for so in si_to_so.get(si_name, []):
                voucher_orders.setdefault(voucher, set()).add(so)

    # Charger les Tâches de travail liées à ces Sales Orders
    orders_tasks = {}
    if all_orders:
        taches = frappe.db.sql(
            """
            SELECT name, commande_client, status, custom_type_dintervention,
                   starts_on, ends_on
            FROM `tabTache de travail`
            WHERE commande_client IN %(orders)s
            """,
            {"orders": list(all_orders)},
            as_dict=True,
        )
        for t in taches:
            orders_tasks.setdefault(t.commande_client, []).append({
                "name": t.name,
                "status": t.status,
                "type": t.custom_type_dintervention or "",
                "starts_on": str(t.starts_on) if t.starts_on else "",
                "ends_on": str(t.ends_on) if t.ends_on else "",
            })

    status_label = {0: "Brouillon", 1: "Validé", 2: "Annulé"}
    detail = []
    running = 0.0
    # Calcul du solde cumulé chronologique (du plus ancien au plus récent)
    for r in reversed(rows):
        running += float(r.debit or 0) - float(r.credit or 0)
        r["_balance"] = round(running, 3)

    for r in rows:
        # Tâches de travail liées à cette ligne (via les Sales Orders de la pièce)
        line_orders = voucher_orders.get(r.voucher_no, set())
        line_tasks = []
        seen_tasks = set()
        for so in line_orders:
            for t in orders_tasks.get(so, []):
                if t["name"] in seen_tasks:
                    continue
                seen_tasks.add(t["name"])
                line_tasks.append({**t, "sales_order": so})

        detail.append({
            "posting_date": str(r.posting_date) if r.posting_date else "",
            "account": r.account,
            "account_label": RELANCE_ACCOUNTS.get(r.account, {}).get("label", r.account),
            "voucher_type": r.voucher_type,
            "voucher_no": r.voucher_no,
            "debit": float(r.debit or 0),
            "credit": float(r.credit or 0),
            "balance": r.get("_balance", 0),
            "docstatus": status_label.get(r.docstatus, "?"),
            "reference_no": r.reference_no or "",
            "remarks": (r.pe_remarks or r.remarks or "").strip(),
            "references": refs_map.get(r.voucher_no, []),
            "tasks": line_tasks,
        })

    return detail


def _build_relance_message(client):
    """Construit le message de relance personnalisé à partir d'une ligne client."""
    parts = []
    if float(client.get("dettes") or 0) > 0.009:
        parts.append(f"Dettes: {_fmt_tnd(client['dettes'])} TND")
    if float(client.get("cheques") or 0) > 0.009:
        parts.append(f"Chèques sans provision: {_fmt_tnd(client['cheques'])} TND")
    if float(client.get("traites") or 0) > 0.009:
        parts.append(f"Traites sans provision: {_fmt_tnd(client['traites'])} TND")
    repartition = ", ".join(parts)
    nom = client.get("customer_name") or client.get("customer") or "Client"
    total = _fmt_tnd(client.get("total") or 0)

    # Détail par paiement (montant + facture/commande liée + total de la pièce)
    detail_txt = ""
    details = _get_payment_details_for_message(client.get("customer"))
    if details:
        lignes = []
        for d in details:
            piece = d["fac_name"] or d["doc_ref"] or "—"
            date_txt = f"{_fmt_date(d['posting_date'])} " if d.get("posting_date") else ""
            if d["doc_total"]:
                lignes.append(
                    f"- {date_txt}{_fmt_tnd(d['montant'])} TND ({piece}, "
                    f"total {_fmt_tnd(d['doc_total'])} TND)"
                )
            else:
                lignes.append(f"- {date_txt}{_fmt_tnd(d['montant'])} TND ({piece})")
        detail_txt = "\nDétail des paiements :\n" + "\n".join(lignes) + "\n"

    return (
        f"Bonjour {nom}, sauf erreur de notre part, votre situation présente "
        f"un montant en attente de règlement de {total} TND, réparti comme suit : "
        f"{repartition}.\n{detail_txt}"
        f"Merci de bien vouloir régulariser votre situation. "
        f"AquaWorld & Servicing."
    )


def _fac_display_name(numero, posting_date):
    """Construit le nom de facture FAC-{mois}-{année}-{numéro sur 5 chiffres}
    à partir de custom_numero_facture et posting_date."""
    try:
        dt = frappe.utils.getdate(posting_date)
        num = str(int(numero)).zfill(5) if numero else ""
        if not num:
            return ""
        return f"FAC-{dt.month:02d}-{dt.year}-{num}"
    except Exception:
        return str(numero or "")


def _get_payment_details_for_message(customer):
    """Pour chaque écriture de paiement ciblée du client, retourne le montant,
    la facture/commande liée et le total de cette pièce."""
    if not customer:
        return []
    accounts = _relance_account_list()
    rows = frappe.db.sql(
        """
        SELECT gle.voucher_no               AS voucher_no,
               gle.posting_date             AS posting_date,
               SUM(gle.debit - gle.credit)  AS montant
        FROM `tabGL Entry` gle
        INNER JOIN `tabPayment Entry` pe ON pe.name = gle.voucher_no
        WHERE gle.voucher_type = 'Payment Entry'
          AND gle.is_cancelled = 0
          AND gle.account IN %(accounts)s
          AND pe.party_type = 'Customer'
          AND pe.party = %(customer)s
        GROUP BY gle.voucher_no
        HAVING montant > 0.009
        ORDER BY gle.posting_date
        """,
        {"accounts": accounts, "customer": customer},
        as_dict=True,
    )
    if not rows:
        return []

    vouchers = [r.voucher_no for r in rows]
    refs = frappe.db.sql(
        """SELECT parent, reference_doctype, reference_name
           FROM `tabPayment Entry Reference` WHERE parent IN %(v)s""",
        {"v": vouchers},
        as_dict=True,
    )
    ref_by_voucher = {}
    si_names, so_names = set(), set()
    for ref in refs:
        ref_by_voucher.setdefault(ref.parent, []).append(ref)
        if ref.reference_doctype == "Sales Invoice":
            si_names.add(ref.reference_name)
        elif ref.reference_doctype == "Sales Order":
            so_names.add(ref.reference_name)

    # Commandes -> factures liées (via Sales Invoice Item)
    so_info, so_to_si = {}, {}
    if so_names:
        for s in frappe.db.sql(
            "SELECT name, grand_total FROM `tabSales Order` WHERE name IN %(n)s",
            {"n": list(so_names)}, as_dict=True,
        ):
            so_info[s.name] = s
        for row in frappe.db.sql(
            """SELECT sales_order, parent AS si FROM `tabSales Invoice Item`
               WHERE sales_order IN %(n)s AND sales_order != ''""",
            {"n": list(so_names)}, as_dict=True,
        ):
            so_to_si.setdefault(row.sales_order, row.si)
            si_names.add(row.si)

    si_info = {}
    if si_names:
        for s in frappe.db.sql(
            """SELECT name, custom_numero_facture, posting_date, grand_total
               FROM `tabSales Invoice` WHERE name IN %(n)s""",
            {"n": list(si_names)}, as_dict=True,
        ):
            si_info[s.name] = s

    details = []
    for r in rows:
        refs_v = ref_by_voucher.get(r.voucher_no, [])
        si_ref = next((x for x in refs_v if x.reference_doctype == "Sales Invoice"), None)
        so_ref = next((x for x in refs_v if x.reference_doctype == "Sales Order"), None)

        fac_name, doc_ref, doc_total = "", "", 0.0
        if si_ref and si_ref.reference_name in si_info:
            s = si_info[si_ref.reference_name]
            fac_name = _fac_display_name(s.custom_numero_facture, s.posting_date)
            doc_total = float(s.grand_total or 0)
        elif so_ref:
            so = so_info.get(so_ref.reference_name)
            doc_total = float(so.grand_total or 0) if so else 0.0
            doc_ref = f"Commande {so_ref.reference_name}"
            si_name = so_to_si.get(so_ref.reference_name)
            if si_name and si_name in si_info:
                s = si_info[si_name]
                fac_name = _fac_display_name(s.custom_numero_facture, s.posting_date)

        details.append({
            "montant": round(float(r.montant or 0), 3),
            "posting_date": str(r.posting_date) if r.posting_date else "",
            "fac_name": fac_name,
            "doc_ref": doc_ref,
            "doc_total": round(doc_total, 3),
        })
    return details


def _fmt_tnd(val):
    try:
        return f"{float(val):,.3f}".replace(",", " ").replace(".", ",")
    except Exception:
        return str(val)


def _fmt_date(val):
    """Formate une date en JJ/MM/AAAA pour les messages."""
    try:
        return frappe.utils.getdate(val).strftime("%d/%m/%Y")
    except Exception:
        return str(val or "")


@frappe.whitelist()
def preparer_messages_relance(customers):
    """
    Prépare les messages de relance personnalisés pour une liste de clients.
    `customers` : JSON list de noms de clients (Customer.name).
    Retourne une liste {customer, customer_name, telephone, email, message, total}.
    """
    if isinstance(customers, str):
        customers = json.loads(customers)

    data = get_relance_clients()
    by_name = {c["customer"]: c for c in data["clients"]}

    out = []
    for cust in customers:
        c = by_name.get(cust)
        if not c:
            continue
        out.append({
            "customer": c["customer"],
            "customer_name": c["customer_name"],
            "telephone": c["telephone"],
            "email": c["email"],
            "total": c["total"],
            "message": _build_relance_message(c),
        })
    return out


@frappe.whitelist()
def envoyer_relance(payload, channel="sms"):
    """
    Envoie les relances.
    `payload` : JSON list de {customer, telephone, email, message}.
    `channel` : 'sms', 'email' ou 'both' (SMS + Email).
    Retourne {sent: [...], failed: [...]}.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)

    # Canaux à utiliser
    channels = ["sms", "email"] if channel == "both" else [channel]

    sent, failed = [], []

    def _send_sms(item):
        phone = (item.get("telephone") or "").strip()
        if not phone:
            failed.append({"customer": item.get("customer"), "reason": "Téléphone manquant (SMS)"})
            return
        from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import (
            _send_sms_with_fallback,
        )
        _send_sms_with_fallback([phone], item["_message"])
        sent.append({"customer": item.get("customer"), "channel": "sms", "to": phone})
        _log_relance_comment(item.get("customer"), "SMS", f"Envoyé au {phone}")

    def _send_email(item):
        email = (item.get("email") or "").strip()
        if not email:
            failed.append({"customer": item.get("customer"), "reason": "Email manquant (Email)"})
            return
        frappe.sendmail(
            recipients=[email],
            subject="Régularisation de votre situation - AquaWorld & Servicing",
            message=item["_message"].replace("\n", "<br>"),
            reference_doctype="Customer",
            reference_name=item.get("customer"),
        )
        sent.append({"customer": item.get("customer"), "channel": "email", "to": email})
        _log_relance_comment(item.get("customer"), "Email", f"Envoyé à {email}")

    for item in payload:
        cust = item.get("customer")
        message = (item.get("message") or "").strip()
        if not message:
            failed.append({"customer": cust, "reason": "Message vide"})
            continue
        item["_message"] = message

        for ch in channels:
            try:
                if ch == "sms":
                    _send_sms(item)
                elif ch == "email":
                    _send_email(item)
                else:
                    failed.append({"customer": cust, "reason": f"Canal inconnu: {ch}"})
            except Exception as e:
                frappe.log_error(frappe.get_traceback(), "Relance Paiements: échec envoi")
                failed.append({"customer": cust, "reason": f"{ch}: {str(e)[:100]}"})

    frappe.db.commit()
    return {"sent": sent, "failed": failed}


# ---------------------------------------------------------------------------
#  Saisie groupée des prix de vente (popup sur la fiche Item)
# ---------------------------------------------------------------------------

#: Marge sous laquelle l'enregistrement est BLOQUÉ (taux de marge sur prix de vente), en %.
MARGE_BLOCK_PCT = 5.0
#: Marge minimale "rouge" (avertissement, non bloquant), en %.
MARGE_MIN_PCT = 15.0
#: Marge préférée, en % — avertissement (non bloquant) sous ce seuil.
MARGE_PREF_PCT = 25.0
#: TVA par défaut si l'article n'a pas d'Item Tax Template.
TVA_DEFAUT_PCT = 19.0


def _get_item_vat_rate(item_code, default=19.0):
    """Taux de TVA de l'article (via son Item Tax Template), sinon `default`."""
    tpl = frappe.db.get_value(
        "Item Tax", {"parenttype": "Item", "parent": item_code}, "item_tax_template"
    )
    if tpl:
        rate = frappe.db.get_value(
            "Item Tax Template Detail", {"parent": tpl}, "tax_rate"
        )
        if rate is not None:
            return flt(rate)
    return flt(default)


def _get_item_valuation_rate(item_code, fallback=0.0):
    """
    Dernière valorisation "réelle" de l'article :
      1) moyenne pondérée par la quantité en stock (Bin, actual_qty > 0)
      2) repli : dernière Stock Ledger Entry non annulée
      3) repli : champ Item.valuation_rate (souvent 0 / non maintenu)
    """
    bins = frappe.get_all(
        "Bin",
        filters={"item_code": item_code, "actual_qty": [">", 0]},
        fields=["actual_qty", "valuation_rate"],
    )
    total_qty = sum(flt(b.actual_qty) for b in bins)
    if total_qty:
        total_val = sum(flt(b.actual_qty) * flt(b.valuation_rate) for b in bins)
        return total_val / total_qty

    last_sle = frappe.get_all(
        "Stock Ledger Entry",
        filters={"item_code": item_code, "is_cancelled": 0},
        fields=["valuation_rate"],
        order_by="posting_date desc, posting_time desc, creation desc",
        limit=1,
    )
    if last_sle and flt(last_sle[0].valuation_rate):
        return flt(last_sle[0].valuation_rate)

    return flt(fallback)


@frappe.whitelist()
def get_item_selling_prices(item_code):
    """
    Retourne, pour un article, toutes les listes de prix de VENTE (selling=1, enabled=1)
    avec le prix actuel s'il existe, plus la dernière valorisation et le dernier prix d'achat.
    Sert à alimenter le popup "Prix de vente" de la fiche Item.
    """
    if not item_code:
        frappe.throw(_("Article manquant."))

    item = frappe.db.get_value(
        "Item", item_code,
        ["valuation_rate", "last_purchase_rate", "stock_uom", "sales_uom"],
        as_dict=True,
    )
    if not item:
        frappe.throw(_("Article introuvable : {0}").format(item_code))

    uom = item.sales_uom or item.stock_uom

    valuation = _get_item_valuation_rate(item_code, fallback=item.valuation_rate)
    # Coût de revient HT pour le calcul de marge : valorisation, sinon dernier prix d'achat
    cost = valuation if valuation > 0 else flt(item.last_purchase_rate)
    tva = _get_item_vat_rate(item_code, default=TVA_DEFAUT_PCT)

    price_lists = frappe.get_all(
        "Price List",
        filters={"selling": 1, "enabled": 1},
        fields=["name", "currency"],
        order_by="name asc",
    )

    rows = []
    for pl in price_lists:
        existing = frappe.db.get_value(
            "Item Price",
            {"item_code": item_code, "price_list": pl.name, "selling": 1},
            ["name", "price_list_rate", "currency"],
            as_dict=True,
        )
        rate = existing.price_list_rate if existing else None

        # Marge (taux sur PV) calculée côté serveur si un prix existe et un coût est connu
        marge = None
        if rate and cost > 0:
            pv_ht = flt(rate) / (1 + tva / 100.0)
            if pv_ht > 0:
                marge = round((pv_ht - cost) / pv_ht * 100.0, 1)

        rows.append({
            "price_list": pl.name,
            "currency": (existing.currency if existing else None) or pl.currency,
            "rate": rate,
            "marge": marge,
            "item_price": existing.name if existing else None,
        })

    return {
        "item_code": item_code,
        "uom": uom,
        "valuation_rate": valuation,
        "last_purchase_rate": item.last_purchase_rate,
        "tva": _get_item_vat_rate(item_code, default=TVA_DEFAUT_PCT),
        "cost": cost,
        "marge_block": MARGE_BLOCK_PCT,
        "marge_min": MARGE_MIN_PCT,
        "marge_pref": MARGE_PREF_PCT,
        "price_lists": rows,
    }


@frappe.whitelist()
def save_item_selling_prices(item_code, prices):
    """
    Upsert des prix de vente d'un article.
    `prices` : JSON list de {price_list, rate}.
    Règle : si un Item Price (article, liste, selling) existe -> mise à jour du taux ;
            sinon -> création. Tous les prix de vente sont obligatoires (vérif serveur).
    Retourne {updated: [...], created: [...]}.
    """
    if isinstance(prices, str):
        prices = json.loads(prices)
    if not item_code:
        frappe.throw(_("Article manquant."))

    item = frappe.db.get_value(
        "Item", item_code, ["stock_uom", "sales_uom"], as_dict=True
    )
    if not item:
        frappe.throw(_("Article introuvable : {0}").format(item_code))
    uom = item.sales_uom or item.stock_uom

    # Listes de prix de vente attendues -> {nom: devise}
    selling_lists = {
        pl.name: pl.currency
        for pl in frappe.get_all(
            "Price List",
            filters={"selling": 1, "enabled": 1},
            fields=["name", "currency"],
        )
    }

    # Valeurs saisies indexées par liste de prix
    provided = {}
    for row in (prices or []):
        pl = (row.get("price_list") or "").strip()
        if pl:
            provided[pl] = row.get("rate")

    # Vérification : tous les prix de vente doivent être renseignés
    missing = [
        pl for pl in selling_lists
        if provided.get(pl) is None or str(provided.get(pl)).strip() == ""
    ]
    if missing:
        frappe.throw(
            _("Tous les prix de vente sont obligatoires. Manquant(s) : {0}").format(
                ", ".join(missing)
            )
        )

    updated, created = [], []
    for pl, currency in selling_lists.items():
        rate = flt(provided[pl])
        existing = frappe.db.get_value(
            "Item Price",
            {"item_code": item_code, "price_list": pl, "selling": 1},
            "name",
        )
        if existing:
            doc = frappe.get_doc("Item Price", existing)
            doc.price_list_rate = rate
            doc.save(ignore_permissions=True)
            updated.append(pl)
        else:
            doc = frappe.get_doc({
                "doctype": "Item Price",
                "item_code": item_code,
                "price_list": pl,
                "selling": 1,
                "currency": currency,
                "uom": uom,
                "price_list_rate": rate,
            })
            doc.insert(ignore_permissions=True)
            created.append(pl)

    frappe.db.commit()
    return {"updated": updated, "created": created}


@frappe.whitelist()
def scan_item_database():
    """
    Scanne tous les articles actifs et retourne ceux qui ont un problème :
      - synchronisés avec le site (custom_sync_avec_woocommerce=1) mais SANS image
      - vendables (is_sales_item=1) avec un prix de liste de VENTE manquant
      - taux de marge (sur prix de vente) < MARGE_MIN_PCT sur une liste de vente
        marge = (PV_HT - coût_HT) / PV_HT * 100
        PV_HT = price_list_rate (TTC) / (1 + TVA_article/100)
        coût_HT = valorisation pondérée du stock (repli : dernier prix d'achat)
    Retourne {count, problems:[{item_code, item_name, image, reasons:[...]}]}.
    Alimente le popup "Vérification base article" de la vue liste Item.
    """
    selling_lists = set(
        frappe.get_all("Price List", filters={"selling": 1, "enabled": 1}, pluck="name")
    )

    items = frappe.get_all(
        "Item",
        filters={"disabled": 0},
        fields=[
            "name", "item_name", "image", "last_purchase_rate",
            "custom_sync_avec_woocommerce", "is_sales_item",
        ],
        order_by="item_name asc",
    )

    # Prix de vente existants {article: {liste: taux_TTC}} (bulk)
    prices_by_item = {}
    if selling_lists:
        for p in frappe.get_all(
            "Item Price",
            filters={"selling": 1, "price_list": ["in", list(selling_lists)]},
            fields=["item_code", "price_list", "price_list_rate"],
        ):
            prices_by_item.setdefault(p.item_code, {})[p.price_list] = flt(p.price_list_rate)

    # Valorisation HT pondérée par le stock {article: coût_HT} (bulk Bin)
    val_acc = {}  # item -> [valeur_totale, qty_totale]
    for b in frappe.get_all(
        "Bin",
        filters={"actual_qty": [">", 0]},
        fields=["item_code", "actual_qty", "valuation_rate"],
    ):
        acc = val_acc.setdefault(b.item_code, [0.0, 0.0])
        acc[0] += flt(b.actual_qty) * flt(b.valuation_rate)
        acc[1] += flt(b.actual_qty)
    cost_by_item = {it: (v[0] / v[1]) for it, v in val_acc.items() if v[1]}

    # Taux de TVA par article via Item Tax Template (bulk)
    tpl_rate = {
        r.parent: flt(r.tax_rate)
        for r in frappe.get_all(
            "Item Tax Template Detail",
            fields=["parent", "tax_rate"],
        )
    }
    vat_by_item = {}
    for r in frappe.get_all(
        "Item Tax",
        filters={"parenttype": "Item"},
        fields=["parent", "item_tax_template"],
    ):
        vat_by_item.setdefault(r.parent, tpl_rate.get(r.item_tax_template, TVA_DEFAUT_PCT))

    problems = []
    for it in items:
        reasons = []

        if it.custom_sync_avec_woocommerce and not it.image:
            reasons.append(_("Synchronisé avec le site mais sans image"))

        if it.is_sales_item and selling_lists:
            item_prices = prices_by_item.get(it.name, {})

            missing = selling_lists - set(item_prices)
            if missing:
                reasons.append(
                    _("Prix de vente manquant : {0}").format(", ".join(sorted(missing)))
                )

            # Marge : coût HT (stock, sinon dernier prix d'achat)
            cost_ht = cost_by_item.get(it.name) or flt(it.last_purchase_rate)
            if cost_ht > 0:
                vat = vat_by_item.get(it.name, TVA_DEFAUT_PCT)
                faibles = []
                for pl, ttc in sorted(item_prices.items()):
                    pv_ht = flt(ttc) / (1 + vat / 100.0)
                    if pv_ht <= 0:
                        continue
                    # Taux de marge sur prix de vente : (PV_HT - coût_HT) / PV_HT * 100
                    marge = (pv_ht - cost_ht) / pv_ht * 100.0
                    if marge < MARGE_MIN_PCT:
                        faibles.append(f"{pl} ({marge:.0f}%)")
                if faibles:
                    reasons.append(
                        _("Marge < {0}% : {1}").format(
                            int(MARGE_MIN_PCT), ", ".join(faibles)
                        )
                    )

        if reasons:
            problems.append({
                "item_code": it.name,
                "item_name": it.item_name,
                "image": it.image,
                "reasons": reasons,
            })

    return {"count": len(problems), "problems": problems}
