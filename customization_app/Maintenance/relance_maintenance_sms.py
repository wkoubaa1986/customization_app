# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import frappe
from frappe.utils import nowdate, add_days, getdate
from urllib.parse import quote_plus
import requests
# On réutilise la config machine & extension depuis update_schedule
from customization_app.Maintenance.update_schedule import (
    MACHINE_FAMILY_BY_GROUP,
    extend_schedule_for_sms,
)
from customization_app.utils.run_safely import run_safely
# ---------------------------------------------------------------------------
#  LOGGING
# ---------------------------------------------------------------------------

def _logger():
    return frappe.logger("maintenance_sms")

def log(msg):
    _logger().info(msg)


# Jours où on ne lance pas la campagne de SMS
EXCLUDED_DATES = {
    "2024-01-01",
    "2024-04-10",
    "2024-04-11",
    "2024-04-12",
}

# Limite de SMS par jour (hors secteur + secteurs)
MAX_SMS_PAR_JOUR = 110

# Mapping famille machine -> article d'entretien (pour récupérer le prix)
FAMILY_MAINTENANCE_ITEM = {
    "RO_DOM": "M-E-OD",      # entretien osmoseur domestique
    "ADOUCISSEUR": "M-E-Ad", # entretien adoucisseur
    "RO_COM": "M-E-OC",      # entretien osmoseur commercial
    "RO_IND": "M-E-OI",      # entretien osmoseur industriel
    # tu peux ajouter d'autres familles ici si nécessaire
}

# Libellés lisibles pour les familles de machine dans le SMS
FAMILY_SMS_LABEL = {
    "RO_DOM": "votre osmoseur domestique",
    "RO_COM": "votre osmoseur commercial",
    "RO_IND": "votre osmoseur industriel",
    "BIO": "votre système de bi-osmose",
    "FONTAINE": "votre fontaine à eau",
    "ADOUCISSEUR": "votre adoucisseur",
    "UV": "votre stérilisateur UV",
    "POMPE": "votre pompe",
    "PF": "votre porte-filtre",
}


# ---------------------------------------------------------------------------
#  Helpers prix
# ---------------------------------------------------------------------------

def get_price_for_item(item_code, price_list):
    """Retourne le price_list_rate pour un item + price list, ou None."""
    price = frappe.db.get_value(
        "Item Price",
        {
            "item_code": item_code,
            "price_list": price_list,
            "selling": 1,
        },
        "price_list_rate",
    )
    return price


# ---------------------------------------------------------------------------
#  Point d'entrée principal (pour CRON)
# ---------------------------------------------------------------------------

def envoyer_relances_maintenance(dry_run: bool = False):
    """
    À appeler via cron / scheduler.

    dry_run = True  -> ne pas envoyer les SMS, ne rien modifier en BDD,
                      renvoyer simplement la liste des messages préparés.

    Retourne un dict :
    {
        "dry_run": bool,
        "summary": {...},
        "preview": [...]  # seulement si dry_run=True
        "log": [ "ligne1", "ligne2", ... ]
    }
    """
    today = nowdate()
    log("========== [CRON] Début envoyer_relances_maintenance ==========")

    # Structure de summary pour log + retour
    summary = {
        "date": today,
        "total_maintenance_schedules": 0,
        "clients_avec_rdv": 0,
        "sms1_candidates": 0,
        "sms2_candidates": 0,
        "total_sms_candidates": 0,
        "total_sms_prevus": 0,
        "repartition_sms_par_secteur": {},
        "secteurs_autorises": [],
        "sms_sent_success": 0,
        "sms_invalid_number": 0,
        "sms_failed": 0,
        "maintenances_extended": 0,
    }

    # On ne fait rien certains jours (jours fériés, etc.)
    if today in EXCLUDED_DATES:
        log(f"[INFO] Date {today} dans EXCLUDED_DATES, aucune relance envoyée.")
        end_line = "========== [CRON] Fin envoyer_relances_maintenance (jour exclu) =========="
        log(end_line)
        return {
            "dry_run": bool(dry_run),
            "summary": summary,
            "preview": [] if dry_run else None,
            "log": [
                f"[INFO] Date {today} dans EXCLUDED_DATES, aucune relance envoyée.",
                end_line,
            ],
        }

    # Fenêtres temporelles
    rend_date = add_days(today, -15)     # RDV existants sur 15 jours
    relance_date = add_days(today, -7)  # fenêtre pour considérer les SMS récents

    # 1) Récupérer tous les échéanciers de maintenance
    all_maintenance = frappe.db.sql(
        """
        SELECT 
            ms.name AS schedule_name,
            ms.customer,
            ms.status,
            CONCAT_WS(', ', 
                GROUP_CONCAT(DISTINCT msi.sales_order SEPARATOR ', '), 
                GROUP_CONCAT(DISTINCT msd.custom_sales_order SEPARATOR ', ')
            ) AS sales_orders,
            GROUP_CONCAT(DISTINCT addr.custom_secteur SEPARATOR ', ') AS secteurs
        FROM 
            `tabMaintenance Schedule` ms
        LEFT JOIN 
            `tabMaintenance Schedule Item` msi ON ms.name = msi.parent
        LEFT JOIN 
            `tabMaintenance Schedule Detail` msd ON ms.name = msd.parent
        LEFT JOIN 
            `tabDynamic Link` dl 
              ON dl.link_name = ms.customer 
             AND dl.link_doctype = 'Customer'
        LEFT JOIN 
            `tabAddress` addr ON addr.name = dl.parent
        WHERE 
            ms.status = 'Submitted'
        GROUP BY 
            ms.name, ms.customer, ms.status
        """,
        as_dict=True,
    )

    summary["total_maintenance_schedules"] = len(all_maintenance)

    # 2) Récupérer les rendez-vous déjà planifiés (pour éviter de relancer ces clients)
    rendez_vous = frappe.db.sql(
        """
        SELECT DISTINCT
            TT.custom_client,
            TT.custom_employé,
            TT.custom_type_dintervention,
            TT.starts_on,
            TT.ends_on,
            C.custom_envoi_sms,
            C.custom_liste_telephone
        FROM
            `tabTache de travail` TT
        JOIN
            `tabCustomer` C ON C.name = TT.custom_client
        WHERE
            TT.status IN ('Open','Completed')
            AND DATE(TT.starts_on) >= %s
            AND TT.dans_local != 'Oui'
            AND TT.custom_client IS NOT NULL
            AND TT.custom_type_dintervention != 'Livraison'
            AND C.custom_envoi_sms = 'Oui'
        """,
        (rend_date,),
        as_dict=True,
    )

    clients_avec_rdv = list({x["custom_client"] for x in rendez_vous})
    summary["clients_avec_rdv"] = len(clients_avec_rdv)

    # 3) Calculer les SMS à envoyer pour chaque échéancier
    list_sms1 = []
    list_sms2 = []

    for imant in all_maintenance:
        if imant["customer"] in clients_avec_rdv:
            # Le client a déjà un rendez-vous planifié : on ne l'inclut pas
            continue

        secteur = imant["secteurs"] or "Secteur 1"

        sms1, sms2 = calculer_sms_pour_maintenance(
            maint_name=imant["schedule_name"],
            today=today,
            relance_date=relance_date,
            secteur=secteur,
        )
        if sms1:
            list_sms1.append(sms1)
        if sms2:
            list_sms2.append(sms2)

    # Tri par secteur
    list_sms1 = sorted(list_sms1, key=lambda d: list(d.values())[0][1])
    list_sms2 = sorted(list_sms2, key=lambda d: list(d.values())[0][1])
    list_sms = list_sms1 + list_sms2

    summary["sms1_candidates"] = len(list_sms1)
    summary["sms2_candidates"] = len(list_sms2)
    summary["total_sms_candidates"] = len(list_sms)

    # 4) Calcul des secteurs autorisés dans la limite MAX_SMS_PAR_JOUR
    secteurs_autorises, repartition_sms, total_sms_prevus = calculer_secteurs_autorises(list_sms)
    summary["secteurs_autorises"] = secteurs_autorises
    summary["repartition_sms_par_secteur"] = repartition_sms
    summary["total_sms_prevus"] = total_sms_prevus

    # 5) Envoi / simulation des SMS + update éventuel
    preview, stats = envoyer_et_marquer_sms(
        list_sms,
        secteurs_autorises,
        today,
        dry_run=dry_run
    )

    # fusion des stats dans le summary
    summary["sms_sent_success"] = stats.get("sms_sent_success", 0)
    summary["sms_invalid_number"] = stats.get("sms_invalid_number", 0)
    summary["sms_failed"] = stats.get("sms_failed", 0)
    summary["maintenances_extended"] = stats.get("maintenances_extended", 0)

    # Logs finaux
    summary_line = (
        "[SUMMARY] "
        f"date={summary['date']}, "
        f"total_ms={summary['total_maintenance_schedules']}, "
        f"clients_avec_rdv={summary['clients_avec_rdv']}, "
        f"sms1_candidats={summary['sms1_candidates']}, "
        f"sms2_candidats={summary['sms2_candidates']}, "
        f"total_sms_candidats={summary['total_sms_candidates']}, "
        f"total_sms_prevus={summary['total_sms_prevus']}, "
        f"sms_envoyes={summary['sms_sent_success']}, "
        f"sms_invalid={summary['sms_invalid_number']}, "
        f"sms_failed={summary['sms_failed']}, "
        f"ms_extendues={summary['maintenances_extended']}"
    )
    end_line = "========== [CRON] Fin envoyer_relances_maintenance =========="

    log(summary_line)
    log(end_line)

    return {
        "dry_run": bool(dry_run),
        "summary": summary,
        "preview": preview if dry_run else None,
        "log": [summary_line, end_line],
    }


# ---------------------------------------------------------------------------
#  Partie 1 : Calcul des SMS (SMS1 & SMS2) pour un échéancier
# ---------------------------------------------------------------------------

def calculer_sms_pour_maintenance(maint_name, today, relance_date, secteur):
    """
    Retourne deux dict :
        - NewSMS1 : { "item_code*?/*schedule_name*?/*SMS1": [scheduled_date, secteur], ... }
        - NewSMS2 : idem pour SMS2
    """
    date_par_item_sms1 = {}
    date_par_item_sms2 = {}

    today = getdate(today)
    relance_date = getdate(relance_date)

    maintenance = frappe.get_doc("Maintenance Schedule", maint_name)

    # On trie les lignes pour un comportement cohérent
    schedules = sorted(maintenance.schedules, key=lambda x: x.scheduled_date or today)

    for item in schedules:
        # --- SMS1 ---
        if item.scheduled_date and item.scheduled_date < today:
            if item.item_code not in date_par_item_sms1 and not item.custom_sms_1:
                date_par_item_sms1[item.item_code] = item.scheduled_date
            elif (
                item.item_code in date_par_item_sms1
                and item.scheduled_date > date_par_item_sms1[item.item_code]
                and not item.custom_sms_1
            ):
                date_par_item_sms1[item.item_code] = item.scheduled_date

        # Si SMS1 déjà envoyé récemment : on ne relance pas
        if (
            item.item_code in date_par_item_sms1
            and item.custom_sms_1
            and item.custom_sms_1 >= relance_date
        ):
            date_par_item_sms1.pop(item.item_code, None)

        # Si la maintenance a été réalisée : on ne relance pas
        if item.item_code in date_par_item_sms1 and item.actual_date:
            date_par_item_sms1.pop(item.item_code, None)

        # --- SMS2 ---
        if (
            item.scheduled_date
            and item.scheduled_date < today
            and item.custom_sms_1
            and not item.custom_sms_2
            and today >= add_days(item.custom_sms_1, 7)
        ):
            if item.item_code not in date_par_item_sms2:
                date_par_item_sms2[item.item_code] = item.scheduled_date
            elif item.scheduled_date > date_par_item_sms2[item.item_code]:
                date_par_item_sms2[item.item_code] = item.scheduled_date

        # SMS2 déjà envoyé récemment
        if (
            item.item_code in date_par_item_sms2
            and item.custom_sms_2
            and item.custom_sms_2 >= relance_date
        ):
            date_par_item_sms2.pop(item.item_code, None)

        # Maintenance réalisée : pas de SMS2
        if item.item_code in date_par_item_sms2 and item.actual_date:
            date_par_item_sms2.pop(item.item_code, None)

    newsms1 = {}
    for code, date_sched in date_par_item_sms1.items():
        key = f"{code}*?/*{maint_name}*?/*SMS1"
        newsms1[key] = [date_sched, secteur]

    newsms2 = {}
    for code, date_sched in date_par_item_sms2.items():
        key = f"{code}*?/*{maint_name}*?/*SMS2"
        newsms2[key] = [date_sched, secteur]

    return newsms1, newsms2


# ---------------------------------------------------------------------------
#  Partie 2 : calcul des secteurs autorisés
# ---------------------------------------------------------------------------

def calculer_secteurs_autorises(list_sms):
    """
    Calcule :
    - nombre de SMS par secteur
    - liste de secteurs autorisés en respectant MAX_SMS_PAR_JOUR

    Retourne:
    - secteurs_autorises: list
    - sorted_count_sms: dict secteur -> nb_sms
    - total_sms: int
    """
    count_sms = {}

    for sms in list_sms:
        secteur = list(sms.values())[0][1]
        count_sms.setdefault(secteur, 0)
        count_sms[secteur] += 1

    hors_secteur_value = 0
    if "Hors Secteur" in count_sms:
        hors_secteur_value = count_sms.pop("Hors Secteur")

    sorted_count_sms = dict(
        sorted(count_sms.items(), key=lambda item: item[1], reverse=True)
    )

    if hors_secteur_value:
        sorted_count_sms = {"Hors Secteur": hors_secteur_value, **sorted_count_sms}

    secteurs_autorises = []
    total_sms = 0

    for secteur, nb in sorted_count_sms.items():
        if secteur == "Hors Secteur":
            secteurs_autorises.append(secteur)
            total_sms += nb
        else:
            if total_sms + nb <= MAX_SMS_PAR_JOUR:
                secteurs_autorises.append(secteur)
                total_sms += nb

    log(f"Répartition SMS par secteur : {sorted_count_sms}")
    log(f"Total SMS prévus : {total_sms}")
    log(f"Secteurs autorisés : {secteurs_autorises}")

    return secteurs_autorises, sorted_count_sms, total_sms


# ---------------------------------------------------------------------------
#  Partie 3 : gestion des numéros + envoi SMS
# ---------------------------------------------------------------------------

def normaliser_numero(telephone_raw):
    """Nettoie un numéro et renvoie 0, 1 ou 2 numéros (8 chiffres) possibles."""
    tel = (telephone_raw or "").replace(" ", "").replace("-", "")
    if not tel:
        return []

    nums = []

    if tel.startswith("+216"):
        tel = tel[4:]

    if len(tel) == 8:
        nums.append(tel)
    elif len(tel) == 16:
        nums.append(tel[:8])
        nums.append(tel[8:])

    return nums


def is_mobile_tunisien(num):
    """Mobiles tunisiens classiques : 2,4,5,9."""
    return len(num) == 8 and num[0] in ("2", "4", "5", "9")

@frappe.whitelist()
def traiter_numero_tel(champ_tel):
    """
    Prend custom_liste_telephone (multi-ligne),
    renvoie la liste de numéros mobiles valides (8 chiffres).
    """
    tel_to_send = []

    for raw in (champ_tel or "").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        for num in normaliser_numero(raw):
            if is_mobile_tunisien(num):
                tel_to_send.append(num)

    # dédoublonnage
    seen = set()
    unique = []
    for n in tel_to_send:
        if n not in seen:
            seen.add(n)
            unique.append(n)

    return unique

@frappe.whitelist()
def envoyer_sms_winsms(numero, message):
    """
    Envoi via WinSMSPro. `numero` = 8 chiffres (sans +216).
    Lit la config depuis SMS Settings. Fallback urllib si requests échoue (ex: 429).
    Retourne un objet avec .json() -> dict ou lève une exception.
    """
    import urllib.request
    import urllib.error
    from urllib.parse import urlencode

    phone = f"216{numero}"

    # Lire config depuis SMS Settings
    ss = frappe.get_doc("SMS Settings", "SMS Settings")
    params = {p.parameter: p.value for p in ss.get("parameters") if not p.header}
    params[ss.receiver_parameter] = phone
    params[ss.message_parameter] = message
    url = ss.sms_gateway_url + "?" + urlencode(params)

    # Tentative 1 : requests
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return resp
    except Exception as e1:
        _logger().warning(f"[SMS] requests échoué ({e1}), fallback urllib → {phone}")

    # Tentative 2 : urllib (contourne les problèmes de scope et de rate-limit transitoire)
    try:
        req = urllib.request.urlopen(url, timeout=15)
        body = req.read().decode()
        # Simuler un objet compatible .json()
        import json
        class _FakeResp:
            def __init__(self, text): self.text = text
            def json(self): return json.loads(self.text)
        return _FakeResp(body)
    except urllib.error.HTTPError as http_err:
        body = http_err.read().decode() if http_err else ""
        frappe.log_error(f"phone={phone} status={http_err.code} body={body}", "SMS Maintenance: fallback HTTP error")
        raise
    except Exception as e2:
        frappe.log_error(frappe.get_traceback(), "SMS Maintenance: échec fallback urllib")
        raise


# ---------------------------------------------------------------------------
#  Partie 4 : envoi + marquage + extension des MS
# ---------------------------------------------------------------------------

def envoyer_et_marquer_sms(list_sms, secteurs_autorises, today, dry_run: bool = False):
    """
    Parcourt les SMS à envoyer, filtre par secteurs autorisés.

    - Si dry_run = False :
        * envoie les SMS,
        * met à jour custom_sms_1 / custom_sms_2,
        * appelle extend_schedule_for_sms,
        * commit en base.
    - Si dry_run = True :
        * ne fait AUCUN appel API,
        * ne modifie PAS la base,
        * retourne une liste de prévisualisation des messages.

    Retourne (preview, stats):
      - preview: liste de dicts (messages) si dry_run=True, sinon []
      - stats: dict avec compteurs
    """
    today_date = getdate(today)

    maintenance_cache = {}
    customer_cache = {}
    item_cache = {}

    extended_maintenances = set()
    preview = []  # liste des messages pour DRY RUN

    stats = {
        "sms_sent_success": 0,
        "sms_invalid_number": 0,
        "sms_failed": 0,
        "maintenances_extended": 0,
    }

    # Gestion promo Ramadhan
    promo_ramdhan = ""
    if "2024-03-10" <= today <= "2024-04-08":
        promo_ramdhan = "\nPromo: Ramadan reduc allant à 33% sur nos filtres rechanges"

    corp = ""
    if "2024-03-10" <= today <= "2024-03-16":
        corp = "\nInchallah Ramdhanek Mabrouk."
    website_url, phones_emp = get_relance_config()
    phones_txt = _format_phones_for_message(phones_emp)
    for sms_dict in list_sms:
        familles = set()
        secteur = None
        maint_name = None
        scheduled_date_for_sms = None
        sms_type = None

        # Normalement un seul élément dans sms_dict
        for key, (scheduled_date_for_sms, secteur) in sms_dict.items():
            info = key.split("*?/*")
            item_code = info[0]
            maint_name = info[1]
            sms_type = info[2]

            if item_code not in item_cache:
                item_cache[item_code] = frappe.get_doc("Item", item_code)
            item_doc = item_cache[item_code]

            group = item_doc.item_group
            family = MACHINE_FAMILY_BY_GROUP.get(group)
            if family:
                familles.add(family)

        if not maint_name or not secteur:
            continue

        if secteur not in secteurs_autorises:
            continue

        # --- Construction description en fonction de la famille de machine ---
        if len(familles) == 1:
            primary_family = next(iter(familles))
            desc = FAMILY_SMS_LABEL.get(
                primary_family, "votre appareil de traitement d'eau"
            )
        elif len(familles) > 1:
            primary_family = None
            # on essaie de prioriser RO_DOM / ADOUCISSEUR si présent
            for fam in ("RO_DOM", "ADOUCISSEUR", "RO_COM", "RO_IND"):
                if fam in familles:
                    primary_family = fam
                    break
            desc = "vos équipements de traitement d'eau"
        else:
            primary_family = None
            desc = "votre appareil de traitement d'eau"

        # --- Calcul du coût main d'oeuvre en fonction de la famille ---
        cout = get_price_for_item("M-E-OD", "Vente standard")
        if primary_family and primary_family in FAMILY_MAINTENANCE_ITEM:
            maint_item_code = FAMILY_MAINTENANCE_ITEM[primary_family]
            cout = get_price_for_item(maint_item_code, "Vente standard")

        # Récup Maintenance + Client
        if maint_name not in maintenance_cache:
            maintenance_cache[maint_name] = frappe.get_doc(
                "Maintenance Schedule", maint_name
            )
        maintenance = maintenance_cache[maint_name]

        customer_name = maintenance.customer
        if customer_name not in customer_cache:
            customer_cache[customer_name] = frappe.get_doc("Customer", customer_name)
        client = customer_cache[customer_name]

        suff = " est arrivée"

        promo = promo_ramdhan  # copie locale

        if secteur != "Hors Secteur":
            message = (
                f"Bonjour {client.customer_name},{corp}\n"
                f"Rappel: La maintenance de {desc}{suff} à échéance."
                f"{promo}\n"
            )
            if cout is not None:
                message += (
                    f"Cout main-d'oeuvre: {cout} DT. Ce tarif exclut les filtres de remplacement, "
                    f"facturés séparément selon entretien.\n"
                )
            message += f"Pour planifier votre entretien, contactez-nous au {phones_txt}."

        else:
            message = (
                f"Bonjour {client.customer_name},{corp}\n"
                f"Rappel: La maintenance de {desc}{suff} à échéance."
                f"{promo}\n"
                f"Commandez vos filtres directement sur notre site :\n{website_url}\n"
                f"Ou contactez-nous au {phones_txt} pour passer votre commande"
            )

        # Numéros de téléphone
        try:
            list_tel = traiter_numero_tel(client.custom_liste_telephone)
        except Exception:
            _logger().error(f"Erreur numéros client : {client.customer_name}")
            list_tel = []

        # Si DRY RUN : on n'envoie pas de SMS, on marque juste la preview
        if dry_run:
            preview.append({
                "maintenance": maint_name,
                "customer": customer_name,
                "customer_name": client.customer_name,
                "secteur": secteur,
                "familles": list(familles),
                "primary_family": primary_family,
                "cout": float(cout) if cout is not None else None,
                "message": message,
                "phones": list_tel,
                "sms_type": sms_type,
                "scheduled_date": str(scheduled_date_for_sms) if scheduled_date_for_sms else None,
            })
            # On ne modifie pas la base, on ne fait pas d'extend_schedule_for_sms
            continue

        # ----------- MODE NORMAL (envoi réel) ----------------
        status_sms = "Invalid Number" if not list_tel else "Failed"

        for tel in list_tel:
            try:
                resp = envoyer_sms_winsms(tel, message)
                data = resp.json()
                if data.get("message") == "Successfully Send":
                    status_sms = "Success"
            except Exception:
                _logger().error(
                    f"Echec SMS vers {tel} pour client {client.customer_name}"
                )

        log(status_sms)
        log(message)

        # Mise à jour des flags custom_sms_1 / custom_sms_2
        for key, (scheduled_date_for_sms, _) in sms_dict.items():
            info = key.split("*?/*")
            item_code = info[0]
            sms_type = info[2]

            for row in maintenance.schedules:
                if row.item_code == item_code and row.scheduled_date == scheduled_date_for_sms:
                    if sms_type == "SMS1":
                        row.custom_sms_1 = today_date
                        row.custom_sms1_status = status_sms
                    elif sms_type == "SMS2":
                        row.custom_sms_2 = today_date
                        row.custom_sms2_status = status_sms
                    break

        maintenance.save()

        # Compteurs stats
        if status_sms == "Success":
            stats["sms_sent_success"] += 1
        elif status_sms == "Invalid Number":
            stats["sms_invalid_number"] += 1
        else:
            stats["sms_failed"] += 1

        # 🔁 EXTENSION DE L'ÉCHÉANCIER UNIQUEMENT SI SMS considéré "envoyé"
        if status_sms in ("Success", "Invalid Number") and maint_name not in extended_maintenances:
            extend_schedule_for_sms(maintenance, extra_visits_per_item=2)
            maintenance.save()
            extended_maintenances.add(maint_name)
            stats["maintenances_extended"] += 1

    if dry_run:
        # Pas de commit, on renvoie juste la preview + stats
        return preview, stats

    frappe.db.commit()
    return [], stats

def _format_phones_for_message(phones):
    phones = [p.strip() for p in phones if p and p.strip()]
    if not phones:
        return ""
    if len(phones) == 1:
        return phones[0]
    if len(phones) == 2:
        return f"{phones[0]} ou {phones[1]}"
    return f"{', '.join(phones[:-1])} ou {phones[-1]}"

@frappe.whitelist()
def get_relance_config():
    cfg = frappe.get_single("Responsable Relance")

    # lien
    website_url = (cfg.lien_website or "").strip()

    # numéros depuis la table d'employés
    phones = []
    for row in (cfg.responsable_relance_employee or []):
   

        emp = frappe.get_doc("Employee", row.employee)

        # adapte selon tes champs Employee
       
        phones.append(emp.cell_number)


    return website_url, phones
# ---------------------------------------------------------------------------
#  Fonction de prévisualisation (pour tests)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def preview_relances_maintenance():
    """
    Simule les relances de maintenance sans envoyer de SMS et sans modifier la BDD.
    À appeler depuis bench console ou via API.

    Retourne un dict :
    {
        "dry_run": True,
        "summary": {...},
        "preview": [...],
        "log": [...]
    }
    """
    return envoyer_relances_maintenance(dry_run=True)
def run_cron():
    run_safely("Cron - Relance maintenance SMS", envoyer_relances_maintenance)