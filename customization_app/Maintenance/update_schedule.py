# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from customization_app.Maintenance.relance_maintenance_sms import envoyer_relances_maintenance
import frappe
import json
import pdb
from frappe import _

from customization_app.utils.run_safely import run_safely

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------

# Clients à ignorer totalement (pas de suppression de leurs maintenances)
LIST_TO_IGNORE = (
    "Ayman Belguith",
    "Koubaâ Néjib",
    "Jamel Aloui",
    "Koubaâ Néjib - 1",
)

# Groupes B2B à exclure du process de maintenance
B2B_GROUPS = ("Compte Pro", "Quincaillerie", "Technicien", "Pro Grand Rayon")

# Machine "type" à utiliser quand on devine une famille via consommable
# ⚠️ METS ICI TES VRAIS CODE ARTICLES
DEFAULT_MACHINE_ITEM_BY_FAMILY = {
    "RO_DOM": "AP-M-AJ-5-SM",      # ex : osmoseur domestique standard
    "RO_COM": "AP-SM-5-200GPD",    # ex
    "RO_IND": "OsI-1500GPD",       # ex
    "ADOUCISSEUR": "Ad-30L",       # ex
    "UV": "F-UV-6w",               # ex
    "FONTAINE": "FF-5-Mini-75GPD", # ex
    "BIO": "AP-SM-6-Bi2-75GPD",    # ex
    "PF": "P-F-T-10'-T-SC",        # ex
}


# ----------------------------------------------------------------------
# LOGGING & HELPERS CLIENT
# ----------------------------------------------------------------------


def _logger():
    return frappe.logger("maintenance_scheduler")


def log(msg):
    _logger().info(msg)


def is_b2b_group(customer_group):
    return (customer_group or "").strip() in B2B_GROUPS


def is_customer_interested(customer):
    # Uniquement 'Oui' ou 'OUI' acceptés
    val = (getattr(customer, "custom_intéressé_par_le_service_entretien", "") or "").strip()
    return val in ("Oui", "OUI")


# ----------------------------------------------------------------------
# 1) NETTOYAGE DE BASE
# ----------------------------------------------------------------------


def verify_data_base(list_to_ignore):
    """
    Corrige les flags d'intérêt entretien pour les B2B,
    et supprime certains Maintenance Schedule pour B2B / non intéressés.
    """
    # Forcer custom_intéressé_par_le_service_entretien = 'Non' pour les B2B marqués 'Oui'
    customers = frappe.db.sql(
        """
        SELECT DISTINCT
            C.name
        FROM
            `tabCustomer` C
        WHERE
            C.custom_intéressé_par_le_service_entretien = "Oui"
            AND C.customer_group IN ("Quincaillerie", "Technicien", "Compte Pro", "Pro Grand Rayon")
        """,
        as_dict=True,
    )

    changed_customers = 0
    for icus in customers:
        customer = frappe.get_doc("Customer", icus["name"])
        customer.custom_intéressé_par_le_service_entretien = "Non"
        customer.save()
        changed_customers += 1

    log(f"[CLEANUP] Clients B2B corrigés (intéressé -> Non) : {changed_customers}")

    # Supprimer certains Maintenance Schedule liés aux B2B ou non intéressés + pas de SMS
    scheduled_maintenance = frappe.db.sql(
        """
        SELECT
            sm.name
        FROM
            `tabMaintenance Schedule` sm
        JOIN
            `tabCustomer` c ON c.name = sm.customer
        WHERE
            (
                c.customer_group IN ("Quincaillerie", "Technicien", "Compte Pro", "Pro Grand Rayon")
                AND c.name NOT IN %(ignore)s
            )
            OR (
                c.custom_intéressé_par_le_service_entretien = "Non"
                AND c.custom_envoi_sms = "Non"
                AND sm.docstatus = 1
            )
        """,
        {"ignore": list_to_ignore},
        as_dict=True,
    )

    deleted_ms = 0
    for i_maint in scheduled_maintenance:
        doci = frappe.get_doc("Maintenance Schedule", i_maint["name"])
        log(f"[CLEANUP] Suppression MS {doci.name} pour client {doci.customer}")
        doci.flags.ignore_links = True
        doci.cancel()
        frappe.delete_doc("Maintenance Schedule", i_maint["name"], force=True)
        deleted_ms += 1

    log(f"[CLEANUP] Maintenance Schedule supprimés : {deleted_ms}")


# ----------------------------------------------------------------------
# 2) FAMILLES MACHINES & CONSOMMABLES
# ----------------------------------------------------------------------

# Familles internes : RO_DOM, RO_COM, RO_IND, BIO, FONTAINE, ADOUCISSEUR, UV, POMPE, PF

MACHINE_FAMILY_BY_GROUP = {
    # Osmoseurs domestiques
    "RO domestique avec pompe": "RO_DOM",
    "RO domestique sans pompe": "RO_DOM",
    "RO flux direct": "RO_DOM",

    # Osmoseurs commerciaux
    "Appareils commerciaux": "RO_COM",

    # Osmoseurs industriels / bi-osmose / fontaines
    "Osmoseurs Industriels": "RO_IND",
    "Bi-osmose": "BIO",
    "Fontaines": "FONTAINE",

    # Adoucisseurs
    "Adoucisseurs Domestiques": "ADOUCISSEUR",
    "Adoucisseurs Commerciaux": "ADOUCISSEUR",
    "Vannes adoucisseurs automatiques": "ADOUCISSEUR",
    "Vannes adoucisseurs manuelles": "ADOUCISSEUR",

    # Préfiltration / bouteilles
    "Bouteilles FRP": "PF",
    "Porte-filtres": "PF",

    # UV
    "Filtres UV": "UV",
}

# Tous les groupes considérés comme "machines avec échéancier"
MACHINE_ITEM_GROUPS = set(MACHINE_FAMILY_BY_GROUP.keys())

# Groupes considérés comme consommables
CONSUMABLE_ITEM_GROUPS = {
    # RO domestique & préfiltration
    "Cartouches à charbon",
    "Cartouches anti-calcaire",
    "Cartouches anti-sédiment",
    "Cartouches lavables",
    "Cartouches plissées (anti-bactériennes inf 1 micron)",
    "Filtres T33",
    "RO Consommables & Kits d’entretien",
    "Accessoires divers",

    # Consommables commerciaux
    "Consommables commerciaux",

    # Membranes RO
    "Membranes RO domestiques (≤100 GPD)",
    "Membranes RO commerciales (≤800 GPD)",
    "Membranes RO industrielles (4040/8040)",

    # Médias & filtres
    "Médias filtrants",

    # Consommables adoucisseurs
    "Consommables & Accessoires",

    "Accessoires UV",

    # Produits chimiques liés à la maintenance
    "Antiscalants",
}

# Mapping consommables -> famille de machine
CONSUMABLE_FAMILY_BY_GROUP = {
    # RO domestique
    "RO Consommables & Kits d’entretien": ["RO_DOM"],
    "Cartouches à charbon": ["RO_DOM", "RO_COM", "RO_IND", "PF", "BIO", "FONTAINE"],
    "Cartouches anti-calcaire": ["PF"],
    "Cartouches anti-sédiment": ["RO_DOM", "RO_COM", "RO_IND", "PF", "BIO", "FONTAINE"],
    "Cartouches lavables": ["PF"],
    "Cartouches plissées (anti-bactériennes inf 1 micron)": ["RO_DOM", "RO_COM", "RO_IND", "PF", "BIO"],
    "Filtres T33": ["RO_DOM"],
    "Accessoires divers": ["RO_DOM"],

    # RO commerciaux
    "Consommables commerciaux": ["RO_COM"],
    "Membranes RO commerciales (≤800 GPD)": ["RO_COM"],

    # RO industriels
    "Membranes RO industrielles (4040/8040)": ["RO_IND"],
    "Médias filtrants": ["RO_IND"],

    # Membranes domestiques
    "Membranes RO domestiques (≤100 GPD)": ["RO_DOM"],

    # Adoucisseurs
    "Consommables & Accessoires": ["ADOUCISSEUR"],

    # UV
    "Filtres UV": ["UV"],
    "Accessoires UV": ["UV"],

    # Produits chimiques
    "Antiscalants": ["RO_IND"],
}


def map_item_to_machine_family(item, only_machine=True):
    """
    Retourne une liste de familles internes (RO_DOM, RO_COM, ...) ou une liste vide.
    Si only_machine=True, on ne renvoie que si c'est une machine.
    Sinon, on permet aussi le mapping via les groupes de consommables.
    """
    group = (item.item_group or "").strip()
    families = []

    # Cas machine (mapping direct)
    if group in MACHINE_FAMILY_BY_GROUP:
        families.append(MACHINE_FAMILY_BY_GROUP[group])
        return families

    if only_machine:
        # Pour les machines, si on n'a pas de mapping direct, on essaie des heuristiques
        name = (item.item_name or "").replace(" ", "").lower()

        if "4040" in name or "8040" in name:
            families.append("RO_IND")
            return families
        if "3012" in name or "3013" in name or "600gpd" in name or "800gpd" in name:
            families.append("RO_COM")
            return families
        if "50gpd" in name or "75gpd" in name or "100gpd" in name:
            families.append("RO_DOM")
            return families
        if "uv" in name:
            families.append("UV")
            return families
        if "cartouche" in name and ("polyphosp" in name or "5'" in name or "résine" in name or "lava" in name or "bobinet" in name):
            families.append("PF")
            return families
        if "cartouche" in name and ('10"' in name):
            families.append("RO_DOM")
            return families
        if "cartouche" in name and ('20"' in name):
            families.append("RO_COM")
            return families
        if "cartouche" in name and ("30'" in name or "40'" in name):
            families.append("RO_IND")
            return families

        return families  # vide

    # Cas consommable mappé
    if group in CONSUMABLE_FAMILY_BY_GROUP:
        families.extend(CONSUMABLE_FAMILY_BY_GROUP[group])
        return families

    # Heuristiques de secours aussi pour les consommables
    name = (item.item_name or "").replace(" ", "").lower()

    if "4040" in name or "8040" in name:
        families.append("RO_IND")
        return families
    if "3012" in name or "3013" in name or "600gpd" in name or "800gpd" in name:
        families.append("RO_COM")
        return families
    if "50gpd" in name or "75gpd" in name or "100gpd" in name:
        families.append("RO_DOM")
        return families
    if "uv" in name:
        families.append("UV")
        return families

    return families  # Retourne une liste vide si aucune famille n'est trouvée


def get_default_item_for_family(family):
    """
    Retourne un doc Item pour la famille donnée, en utilisant DEFAULT_MACHINE_ITEM_BY_FAMILY.
    Si rien n'est configuré ou que l'article n'existe pas, retourne None.
    """
    item_code = DEFAULT_MACHINE_ITEM_BY_FAMILY.get(family)
    if not item_code:
        log(f"[GUESS] Aucun item par défaut configuré pour la famille {family}")
        return None

    try:
        return frappe.get_doc("Item", item_code)
    except Exception:
        log(f"[GUESS] Item par défaut '{item_code}' introuvable pour la famille {family}")
        return None


# ----------------------------------------------------------------------
# 3) CRÉATION D'ÉCHÉANCIER POUR LES MACHINES
# ----------------------------------------------------------------------


def create_maintenance_for_machines(i_sal):
    """
    Crée un Maintenance Schedule pour les machines (osmoseurs, adoucisseurs, UV, pompes...) d'une commande.
    i_sal est un dict avec : sales_order, customer, delivery_date, items (string "code1,, code2 ...")
    """
    create_main = False
    customer = frappe.get_doc("Customer", i_sal["customer"])

    # Exclusions
    if is_b2b_group(customer.customer_group):
        log(f"[SKIP] SO {i_sal['sales_order']} client B2B {customer.name}")
        return False
    if not is_customer_interested(customer):
        log(f"[SKIP] SO {i_sal['sales_order']} client non intéressé {customer.name}")
        return False

    maintenance_schedule = frappe.new_doc("Maintenance Schedule")
    maintenance_schedule.customer = i_sal["customer"]
    maintenance_schedule.transaction_date = i_sal["delivery_date"]

    items_str = i_sal.get("items") or ""
    raw_items = [x.strip() for x in items_str.split(",,") if x.strip()]
    unique_item_codes = []
    machines_added = []

    for i_item in raw_items:
        # Résolution du code article
        try:
            item = frappe.get_doc("Item", i_item.replace(" ", ""))
        except Exception:
            i_item2 = i_item.replace(" ", "")
            i_item2 = i_item2.replace("GPD", " GPD")
            item = frappe.get_doc("Item", i_item2)

        group = (item.item_group or "").strip()
        if group not in MACHINE_ITEM_GROUPS:
            continue

        if item.item_code in unique_item_codes:
            # Pas deux fois la même machine dans le même MS
            continue

        unique_item_codes.append(item.item_code)
        machines_added.append(f"{item.item_code} ({group})")
        create_main = True

        ms_item = frappe.new_doc("Maintenance Schedule Item")
        ms_item.parentfield = "items"
        ms_item.parenttype = "Maintenance Schedule"
        ms_item.item_code = item.item_code
        ms_item.item_name = item.item_name
        ms_item.start_date = i_sal["delivery_date"]
        ms_item.end_date = frappe.utils.add_years(ms_item.start_date, 5)
        ms_item.sales_order = i_sal["sales_order"]
        ms_item.periodicity = "Half Yearly"
        ms_item.no_of_visits = 10  # 5 ans / 6 mois = 10 visites
        ms_item.sales_person = "Équipe des Ventes"

        maintenance_schedule.append("items", ms_item)

    if create_main:
        maintenance_schedule.generate_events = False
        maintenance_schedule.insert(ignore_permissions=True)
        maintenance_schedule.submit()
        log(
            f"[CREATE] MS {maintenance_schedule.name} créé pour client {customer.name}, "
            f"SO {i_sal['sales_order']}, machines: {', '.join(machines_added)}"
        )
    else:
        log(f"[NO-MACHINE] SO {i_sal['sales_order']} - aucune machine trouvée pour client {customer.name}")

    return create_main


# ----------------------------------------------------------------------
# 4) UTILITAIRE : VÉRIFIER SI UN MS CONCERNE UNE FAMILLE
# ----------------------------------------------------------------------


def maintenance_has_family(maintenance, family):
    """
    Retourne True si au moins un item 'machine' du MS appartient à la famille donnée.
    family est une string: "RO_DOM", "RO_COM", ...
    """
    for ms_item in getattr(maintenance, "items", []):
        try:
            item = frappe.get_doc("Item", ms_item.item_code)
        except Exception:
            continue

        # On ne regarde que les machines
        if (item.item_group or "").strip() not in MACHINE_ITEM_GROUPS:
            continue

        fams = map_item_to_machine_family(item, only_machine=True)
        if family in fams:
            return True

    return False


# Nombre de mois par périodicité standard ERPNext
PERIODICITY_MONTHS = {
    "Monthly": 1,
    "Quarterly": 3,
    "Half Yearly": 6,
    "Yearly": 12,
}


def has_free_sms_slots(ms):
    """
    Retourne True s'il existe au moins une ligne d'horaire
    où SMS1 et SMS2 sont vides (donc encore utilisable pour envoyer des messages).
    """
    for row in getattr(ms, "schedules", []):
        sms1 = getattr(row, "sms_1", None)
        sms2 = getattr(row, "sms_2", None)
        if not sms1 and not sms2:
            return True
    return False


def build_item_periodicity_map(ms):
    """
    Construit un dict {item_code: nb_mois} à partir de la table Items du Maintenance Schedule.
    La périodicité est définie par article.
    """
    mapping = {}

    for item in getattr(ms, "items", []):
        periodicity = (getattr(item, "periodicity", None) or "Half Yearly").strip()
        months = PERIODICITY_MONTHS.get(periodicity, 6)
        if item.item_code:
            mapping[item.item_code] = months

    return mapping


def extend_schedule_for_sms(ms, extra_visits_per_item=2):
    """
    Étend l'échéancier si tous les slots SMS existants sont déjà utilisés.

    Logique :
    - Si au moins une ligne a SMS1 & SMS2 vides -> on ne fait rien.
    - Sinon :
        * on regarde la périodicité par article (table Items)
        * pour chaque article présent dans le calendrier, on ajoute `extra_visits_per_item`
          visites supplémentaires espacées selon la périodicité de cet article.
    """
    # S'il reste au moins une ligne avec SMS1 et SMS2 vides, pas besoin d'étendre
    if has_free_sms_slots(ms):
        return

    schedules = getattr(ms, "schedules", [])
    if not schedules:
        return

    before = len(schedules)

    # Map {item_code: nb_mois}
    item_periodicity = build_item_periodicity_map(ms)

    # On regroupe les lignes d'horaire par article
    rows_by_item = {}
    for row in schedules:
        code = getattr(row, "item_code", None)
        if not code:
            continue
        rows_by_item.setdefault(code, []).append(row)

    for item_code, rows in rows_by_item.items():
        months = item_periodicity.get(item_code, 6)

        # dernière date planifiée pour cet article
        last_date = None
        for r in rows:
            if r.scheduled_date:
                d = frappe.utils.getdate(r.scheduled_date)
                if not last_date or d > last_date:
                    last_date = d

        if not last_date:
            continue

        for i in range(extra_visits_per_item):
            new_row = ms.append("schedules", {})
            new_row.item_code = item_code
            new_row.scheduled_date = frappe.utils.add_months(last_date, months * (i + 1))
            new_row.completion_status = "En Attente"  # ou "Pending" selon ta traduction
            # sms_1 / sms_2 restent vides → ces lignes serviront pour les prochains SMS

    after = len(getattr(ms, "schedules", []))
    if after > before:
        log(f"[EXTEND] MS {ms.name}: {before} -> {after} lignes (extra_visits_per_item={extra_visits_per_item})")


# ----------------------------------------------------------------------
# 5) HELPERS POUR TROUVER / CLASSER LES ÉCHÉANCIERS PAR FAMILLE
# ----------------------------------------------------------------------


def find_machine_schedules(customer_name, family):
    """
    Retourne tous les Maintenance Schedule (doc) d'un client pour une famille donnée.
    """
    rows = frappe.db.get_all(
        "Maintenance Schedule",
        filters={"customer": customer_name, "docstatus": 1},
        fields=["name"],
    )

    matches = []
    for row in rows:
        ms = frappe.get_doc("Maintenance Schedule", row["name"])
        if maintenance_has_family(ms, family):
            matches.append(ms)

    return matches


def get_next_due_date(ms, ref_date=None):
    """
    Retourne la prochaine date planifiée non complétée (>= ref_date), ou None.
    ref_date: date à partir de laquelle on cherche.
    """
    ref_date = frappe.utils.getdate(ref_date or frappe.utils.nowdate())
    candidates = []

    for row in getattr(ms, "schedules", []):
        row_date = frappe.utils.getdate(row.scheduled_date)
        if getattr(row, "completion_status", None) != "Fully Completed" and row_date >= ref_date:
            candidates.append(row_date)

    return min(candidates) if candidates else None


def shift_schedule_for_delivery(target_ms, delivery_date, sales_order):
    """
    Décale les dates de l'échéancier target_ms autour de la date de livraison,
    et marque la visite la plus proche comme réalisée.
    """
    if not getattr(target_ms, "schedules", None):
        log(f"[UPDATE] Maintenance {target_ms.name} sans 'schedules'")
        return

    delivery_date = frappe.utils.getdate(delivery_date)

    # Trouver la ligne de schedule la plus proche de la nouvelle date
    closest_row = None
    closest_diff = None

    for row in target_ms.schedules:
        row_date = frappe.utils.getdate(row.scheduled_date)
        diff = abs(frappe.utils.date_diff(row_date, delivery_date))
        if closest_row is None or diff < closest_diff:
            closest_row = row
            closest_diff = diff

    if not closest_row:
        log(f"[UPDATE] Aucun schedule trouvé sur {target_ms.name}")
        return

    old_ref_date = frappe.utils.getdate(closest_row.scheduled_date)
    shift_days = frappe.utils.date_diff(delivery_date, old_ref_date)

    # Décaler toutes les dates >= ref et marquer la visite de ref comme réalisée
    for row in target_ms.schedules:
        row_date = frappe.utils.getdate(row.scheduled_date)

        if row_date == old_ref_date:
            row.actual_date = delivery_date
            row.custom_sales_order = sales_order
            row.completion_status = "Fully Completed"

        if row_date >= old_ref_date:
            row.scheduled_date = frappe.utils.add_days(row_date, shift_days)

    # 🔁 Étendre l'échéancier si tous les slots SMS sont déjà utilisés
    extend_schedule_for_sms(target_ms, extra_visits_per_item=4)
    target_ms.save()
    log(f"[UPDATE] Échéancier {target_ms.name} mis à jour pour {target_ms.customer}")


def create_maintenance_from_consumable_guess(customer_name, family, delivery_date, sales_order):
    """
    Crée un Maintenance Schedule 'deviné' pour un client qui a acheté un consommable
    mais n'a pas de machine enregistrée chez nous pour cette famille.

    On utilise une machine 'type' définie dans DEFAULT_MACHINE_ITEM_BY_FAMILY.
    """
    customer = frappe.get_doc("Customer", customer_name)

    if is_b2b_group(customer.customer_group):
        return None
    if not is_customer_interested(customer):
        return None

    item = get_default_item_for_family(family)
    if not item:
        # On ne peut pas deviner sans machine type
        return None

    ms = frappe.new_doc("Maintenance Schedule")
    ms.customer = customer_name
    ms.transaction_date = delivery_date

    ms_item = frappe.new_doc("Maintenance Schedule Item")
    ms_item.parentfield = "items"
    ms_item.parenttype = "Maintenance Schedule"
    ms_item.item_code = item.item_code
    ms_item.item_name = item.item_name
    ms_item.start_date = delivery_date
    ms_item.end_date = frappe.utils.add_years(ms_item.start_date, 5)
    ms_item.sales_order = sales_order
    ms_item.periodicity = "Half Yearly"
    ms_item.no_of_visits = 10
    ms_item.sales_person = "Équipe des Ventes"

    ms.append("items", ms_item)

    ms.generate_events = False
    ms.insert(ignore_permissions=True)
    ms.submit()

    log(f"[GUESS] Création d'un MS deviné pour {customer_name} / {family} à partir de {sales_order}")

    return ms


def get_customer_machine_families(customer_name):
    """
    Retourne l'ensemble des familles de machines (RO_DOM, ADOUCISSEUR, etc.)
    déjà présentes dans les Maintenance Schedule du client.
    """
    rows = frappe.db.get_all(
        "Maintenance Schedule",
        filters={"customer": customer_name, "docstatus": 1},
        fields=["name"],
    )

    families = set()

    for row in rows:
        ms = frappe.get_doc("Maintenance Schedule", row["name"])
        for ms_item in getattr(ms, "items", []):
            try:
                item = frappe.get_doc("Item", ms_item.item_code)
            except Exception:
                continue

            group = (item.item_group or "").strip()
            if group not in MACHINE_ITEM_GROUPS:
                continue

            fams = map_item_to_machine_family(item, only_machine=True)
            for f in fams:
                families.add(f)

    return families


# ----------------------------------------------------------------------
# 6) UPDATE D'UN ÉCHÉANCIER POUR UNE FAMILLE DE MACHINE
# ----------------------------------------------------------------------


def update_single_machine_schedule(customer_name, family, delivery_date, sales_order, max_schedules=None):
    """
    Pour un client + famille de machine (RO_DOM, RO_COM, RO_IND, ADOUCISSEUR, etc.),
    on cherche les échéanciers concernés et on les met à jour.

    max_schedules:
        - None  => on met à jour TOUS les échéanciers de cette famille pour ce client
        - n > 0 => on met à jour les n échéanciers avec la prochaine visite la plus proche
    """
    delivery_date = frappe.utils.getdate(delivery_date)

    all_ms = find_machine_schedules(customer_name, family)

    # Si aucun MS trouvé, on en crée un 'deviné' à partir du consommable
    if not all_ms:
        log(f"[GUESS] Aucun MS existant pour client {customer_name}, famille {family}. Création devinée.")
        guessed_ms = create_maintenance_from_consumable_guess(
            customer_name=customer_name,
            family=family,
            delivery_date=delivery_date,
            sales_order=sales_order,
        )
        if not guessed_ms:
            # On ne peut vraiment rien faire (pas de machine type configurée)
            log(f"[ERROR] Impossible de créer un MS deviné pour {customer_name} / {family}")
            return

        # On ne met à jour que celui-là dans ce cas
        shift_schedule_for_delivery(guessed_ms, delivery_date, sales_order)
        return

    # --- logique standard si on a déjà des MS existants ---

    scored = []
    for ms in all_ms:
        next_due = get_next_due_date(ms, ref_date=delivery_date)
        # fallback : si pas de prochaine visite, on prend la transaction_date
        next_due = next_due or frappe.utils.getdate(ms.transaction_date)
        scored.append((next_due, ms))

    # Trier par prochaine visite due
    scored.sort(key=lambda x: x[0])

    if max_schedules is None:
        selected = [ms for _, ms in scored]
    else:
        selected = [ms for _, ms in scored[:max_schedules]]

    for ms in selected:
        log(
            f"[UPDATE-SCHED] Client {customer_name}, famille {family}, "
            f"MS {ms.name}, next_due={get_next_due_date(ms, delivery_date)}"
        )
        shift_schedule_for_delivery(ms, delivery_date, sales_order)


# ----------------------------------------------------------------------
# 7) UPDATE DES ÉCHÉANCIERS À PARTIR DES CONSOMMABLES
# ----------------------------------------------------------------------


def update_maintenance_schedule(i_sal):
    """
    Quand un client achète des consommables (cartouches, membranes, sel, UV, etc.),
    on met à jour l'échéancier de la ou des machines concernées.

    Logique :
    - On détecte les familles possibles à partir des consommables (target_families)
    - On regarde les familles déjà présentes dans les MS du client (existing_families)
    - Si intersection non vide -> on met à jour uniquement ces familles
    - Sinon -> on choisit UNE seule famille "devinée" pour créer un échéancier hypothétique
    """
    customer = frappe.get_doc("Customer", i_sal["customer"])
    log(f"[CONSO] Traitement consommables SO {i_sal['sales_order']} pour client {customer.name}")

    if is_b2b_group(customer.customer_group):
        log(f"[SKIP-CONSO] Client B2B {customer.name}")
        return
    if not is_customer_interested(customer):
        log(f"[SKIP-CONSO] Client non intéressé {customer.name}")
        return

    items_str = i_sal.get("items") or ""
    raw_items = [x.strip() for x in items_str.split(",,") if x.strip()]

    # familles candidates et familles "uniques" (consommables qui pointent vers une seule famille)
    target_families = set()
    single_families = set()

    for i_item in raw_items:
        try:
            item = frappe.get_doc("Item", i_item.replace(" ", ""))
        except Exception:
            i_item2 = i_item.replace(" ", "")
            i_item2 = i_item2.replace("GPD", " GPD")
            item = frappe.get_doc("Item", i_item2)

        group = (item.item_group or "").strip()
        name = (item.item_name or "").lower()

        is_consumable = group in CONSUMABLE_ITEM_GROUPS or "sel" in name
        if not is_consumable:
            continue

        fams = map_item_to_machine_family(item, only_machine=False)
        if not fams:
            log(f"[WARN-CONSO] Aucune famille trouvée pour consommable {item.item_code}")
            continue

        # Ajout de toutes les familles possibles
        for f in fams:
            target_families.add(f)

        # Si ce consommable ne pointe que vers UNE famille, on la garde à part
        if len(fams) == 1:
            single_families.add(fams[0])

    if not target_families:
        # Aucun lien possible machine <-> consommable
        log(f"[NO-LINK] Aucun lien machine<->consommable pour SO {i_sal['sales_order']}")
        return

    delivery_date = i_sal["delivery_date"]
    sales_order = i_sal["sales_order"]

    # Familles déjà connues dans les MS du client
    existing_families = get_customer_machine_families(customer.name)

    # Intersection : familles à la fois dans les consommables ET dans les MS existants
    intersection = target_families.intersection(existing_families)

    # 🟢 Cas 1 : le client a déjà des machines connues dans ces familles
    if intersection:
        log(
            f"[CONSO-UPDATE] Client {customer.name}, SO {sales_order}, "
            f"familles existantes: {sorted(list(intersection))}"
        )
        for family in sorted(intersection):
            update_single_machine_schedule(
                customer.name,
                family,
                delivery_date,
                sales_order,
                max_schedules=None,  # ou 1/2 si tu veux limiter
            )
        return

    # 🔵 Cas 2 : le client n'a AUCUNE machine connue => on "devine" une seule famille

    # On privilégie les familles issues de consommables qui ne mappent qu'une seule famille
    if single_families:
        guessed_family = next(iter(single_families))
        source = "single_families"
    else:
        # Sinon on prend une famille parmi target_families (par ex. la première)
        guessed_family = next(iter(target_families))
        source = "target_families"

    log(
        f"[CONSO-GUESS] Client {customer.name}, SO {sales_order}, "
        f"famille devinée {guessed_family} (source={source})"
    )

    # Ici on appelle update_single_machine_schedule:
    # - si aucun MS pour cette famille: create_maintenance_from_consumable_guess va créer un MS
    # - sinon: on mettra à jour les échéanciers existants
    update_single_machine_schedule(
        customer.name,
        guessed_family,
        delivery_date,
        sales_order,
        max_schedules=None,
    )


# ----------------------------------------------------------------------
# 8) FONCTION PRINCIPALE (SCHEDULER)
# ----------------------------------------------------------------------


@frappe.whitelist()
def extend_sms_for_all_active_schedules(extra_visits_per_item=2):
    """
    Passe sur tous les Maintenance Schedule soumis (docstatus=1)
    et étend ceux qui n'ont plus de lignes SMS disponibles.

    À appeler via un Scheduled Job (par ex. 1 fois par jour).
    """
    rows = frappe.db.get_all(
        "Maintenance Schedule",
        filters={"docstatus": 1},
        fields=["name"],
    )

    for row in rows:
        ms = frappe.get_doc("Maintenance Schedule", row["name"])
        before = len(getattr(ms, "schedules", []))
        extend_schedule_for_sms(ms, extra_visits_per_item=extra_visits_per_item)
        after = len(getattr(ms, "schedules", []))

        if after > before:
            ms.save()
            log(f"[EXTEND] {ms.name}: horaires étendus de {before} à {after} lignes")


@frappe.whitelist()
def run_maintenance_planning():
    """
    Point d'entrée appelé par le scheduler :
    - nettoie la base pour les B2B / non intéressés
    - parcourt les ventes récentes
      → crée des échéanciers pour les machines
      → ou met à jour/crée des échéanciers à partir des consommables.
    """
    log("========== [CRON] Début run_maintenance_planning ==========")

    summary = {
        "total_sales_orders": 0,
        "b2b_skipped": 0,
        "not_interested_skipped": 0,
        "new_ms_created": 0,
        "updated_from_consumables": 0,
        "already_covered": 0,
    }

    list_to_ignore = LIST_TO_IGNORE

    # Nettoyage
    verify_data_base(list_to_ignore)

    today = frappe.utils.nowdate()
    monthmin_2 = frappe.utils.add_months(today, -4)
    monthplus_2 = frappe.utils.add_months(today, 1)

    # Sales Orders éligibles
    sales_orders_to_generate_maint = frappe.db.sql(
        """
        SELECT DISTINCT
            so.name AS sales_order,
            so.customer,
            so.delivery_date,
            cust.customer_name,
            cust.customer_group,
            cust.custom_intéressé_par_le_service_entretien,
            cust.custom_liste_telephone,
            GROUP_CONCAT(DISTINCT so_item.item_code SEPARATOR ",, ") AS items
        FROM
            `tabSales Order` so
        LEFT JOIN
            `tabCustomer` cust ON so.customer = cust.name
        LEFT JOIN
            `tabSales Order Item` so_item ON so.name = so_item.parent
        LEFT JOIN
            `tabDelivery Note Item` dni ON so.name = dni.against_sales_order
        LEFT JOIN
            `tabDelivery Note` dn ON dni.parent = dn.name
            AND dn.docstatus = 1
            AND dn.status != "Closed"
        WHERE
            so.docstatus = 1
            -- exclure B2B
            AND IFNULL(cust.customer_group, "") NOT IN ("Quincaillerie", "Technicien", "Compte Pro", "Pro Grand Rayon")
            -- ne garder que les clients intéressés
            AND UPPER(IFNULL(cust.custom_intéressé_par_le_service_entretien, "")) = "OUI"
            -- commande livrée / prise en compte
            AND (
                so.delivery_status = "Fully Delivered"
                OR (
                    dn.name IS NOT NULL
                    AND dn.custom_reconciliation_stock IS NOT NULL
                )
            )
            -- fenêtre temporelle
            AND so.delivery_date BETWEEN %(from_date)s AND %(to_date)s
        GROUP BY
            so.name, so.customer, so.delivery_date,
            cust.customer_name, cust.customer_group,
            cust.custom_intéressé_par_le_service_entretien,
            cust.custom_liste_telephone
        ORDER BY
            so.delivery_date
        """,
        {"from_date": monthmin_2, "to_date": monthplus_2},
        as_dict=True,
    )

    # Maintenances existantes (pour liste des SO déjà couverts)
    all_maintenance = frappe.db.sql(
        """
        SELECT
            ms.name AS schedule_name,
            ms.customer,
            CONCAT_WS(", ",
                GROUP_CONCAT(DISTINCT msi.sales_order SEPARATOR ", "),
                GROUP_CONCAT(DISTINCT msd.custom_sales_order SEPARATOR ", ")
            ) AS sales_orders
        FROM
            `tabMaintenance Schedule` ms
        LEFT JOIN
            `tabMaintenance Schedule Item` msi ON ms.name = msi.parent
        LEFT JOIN
            `tabMaintenance Schedule Detail` msd ON ms.name = msd.parent
        WHERE
            ms.docstatus = 1
        GROUP BY
            ms.name, ms.customer
        """,
        as_dict=True,
    )

    # Sales Orders déjà couverts
    new_all_maint = []
    for entry in all_maintenance:
        if not entry.get("sales_orders"):
            continue
        sales_order_list = [
            order.strip()
            for order in entry["sales_orders"].split(",")
            if order.strip()
        ]
        unique_sales_orders = sorted(list(set(sales_order_list)))
        new_all_maint.append(unique_sales_orders)

    liste_of_sales_order = list({item for sublist in new_all_maint for item in sublist})

    summary["total_sales_orders"] = len(sales_orders_to_generate_maint)
    log(f"[INFO] SO à traiter : {summary['total_sales_orders']}")

    # Boucle principale
    for i_sal in sales_orders_to_generate_maint:
        sale = i_sal["sales_order"]
        cust = i_sal["customer"]
        customer = frappe.get_doc("Customer", cust)

        if is_b2b_group(customer.customer_group):
            summary["b2b_skipped"] += 1
            log(f"[SKIP] SO {sale} (client B2B {customer.name})")
            continue
        if not is_customer_interested(customer):
            summary["not_interested_skipped"] += 1
            log(f"[SKIP] SO {sale} (client non intéressé {customer.name})")
            continue

        # Cas 1 : ce Sales Order n'a jamais été utilisé dans un MS
        if sale not in liste_of_sales_order:
            created = create_maintenance_for_machines(i_sal)

            if created:
                summary["new_ms_created"] += 1
                log(f"[RESULT] SO {sale} -> nouveau Maintenance Schedule créé")
            else:
                summary["updated_from_consumables"] += 1
                log(f"[RESULT] SO {sale} -> update / création via consommables")
                update_maintenance_schedule(i_sal)

        # Cas 2 : ce Sales Order est déjà référencé dans au moins un MS
        else:
            summary["already_covered"] += 1
            log(f"[RESULT] SO {sale} déjà couvert par un MS existant")

    summary_line = (
        "[SUMMARY] "
        f"total={summary['total_sales_orders']}, "
        f"b2b={summary['b2b_skipped']}, "
        f"non_interessé={summary['not_interested_skipped']}, "
        f"new_ms={summary['new_ms_created']}, "
        f"from_conso={summary['updated_from_consumables']}, "
        f"already_covered={summary['already_covered']}"
    )
    log(summary_line)
    log("========== [CRON] Fin run_maintenance_planning ==========")

    return {
        "summary": summary,
        "log": summary_line,
    }
def run_cron():
    run_safely("Cron - Mise à jour échéancier maintenance", run_maintenance_planning)