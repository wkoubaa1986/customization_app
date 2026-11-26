# -*- coding: utf-8 -*-
"""Relance SMS / e-mail de l'entretien (cron 10:00, lundi–samedi).

Revue du 25/11/2026 (analyse « cartographie entretien », copie de prod du 25/11 12:26) :
- UN SEUL SMS PAR CLIENT ET PAR JOUR (réglage « un_sms_par_client_par_jour », 1 par défaut) : avant, un client à deux
  échéanciers (deux machines, un doublon, un « deviné » de trop) ou dont un échéancier devait à la fois un SMS1 et un
  SMS2 recevait deux SMS le même jour — 60 fois du 01/09 au 25/11/2026 (36 clients) ; l'analyse compte 73 couples
  client × famille relancés deux fois le même mois. Désormais un seul message (celui de la famille prioritaire, avec
  son coût) et TOUTES les visites dues sont marquées ; le plafond quotidien compte un SMS par client ;
- le résumé « exclus_partenaire » comptait les ÉCHÉANCIERS de TOUS les clients exclus (529 à la nuit simulée du 26/11,
  aucun lien avec le partenaire) → compteurs séparés par motif, « exclus_partenaire » = les seuls clients du partenaire ;
- un échec sur un échéancier ou un client (fiche supprimée, article disparu, sauvegarde refusée) arrêtait tout le
  passage → chacun est isolé et journalisé (Error Log « SMS Maintenance — … ») ;
- après un vrai passage, les compteurs du jour vont à l'entonnoir des relances (customization_app.entonnoir_relances).
"""
from __future__ import unicode_literals

from collections import Counter

import frappe

from customization_app import modeles_sms, relances_config as RC
from frappe.utils import nowdate, add_days, getdate, cint
from urllib.parse import quote_plus
import requests
# On réutilise l'extension depuis update_schedule (les familles viennent du réglage, RC)
from customization_app.Maintenance.update_schedule import extend_schedule_for_sms
from customization_app.utils.run_safely import run_safely
# ---------------------------------------------------------------------------
#  LOGGING
# ---------------------------------------------------------------------------

def _logger():
    return RC.journal("maintenance_sms")      # niveau INFO forcé : le logger Frappe est en ERROR en prod (journal vide)

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

def simulation_envois():
    """⛔ GARDE-FOU DEV — même règle que sms_commandes / portail_rdv.

    Le site de développement porte les VRAIS numéros et e-mails des clients
    (base restaurée de la prod) et partage la VRAIE passerelle SMS : une
    relance lancée à la main en dev part pour de bon (incident du 29/08/2026,
    2 SMS réels partis pendant des tests). En developer_mode on SIMULE — sauf
    `sms_groupe_reel_en_dev` posé exprès dans site_config.json.
    """
    return bool(frappe.utils.cint(frappe.conf.get("developer_mode"))) \
        and not frappe.utils.cint(frappe.conf.get("sms_groupe_reel_en_dev"))


# ---------------------------------------------------------------------------
#  Prise de rendez-vous en ligne : à qui peut-on proposer le portail ?
# ---------------------------------------------------------------------------

def _config_portail():
    """La config du portail RDV, ou None s'il est éteint / absent."""
    try:
        cfg = frappe.get_single("Config Portail RDV")
    except Exception:
        return None
    return cfg if cfg and frappe.utils.cint(cfg.actif) else None


def gouvernorats_du_client(customer):
    """Les gouvernorats des adresses du client — `custom_state_s` OU `state`,
    car la moitié des adresses Sousse/Monastir n'ont que `state` (piège déjà
    payé sur le déplacement de RDV, cf. `_gouvernorat_adresse`)."""
    lignes = frappe.db.sql(
        """
        SELECT DISTINCT COALESCE(NULLIF(a.custom_state_s, ''), a.state) AS gouvernorat
        FROM `tabAddress` a
        JOIN `tabDynamic Link` dl
          ON dl.parent = a.name AND dl.parenttype = 'Address'
         AND dl.link_doctype = 'Customer' AND dl.link_name = %s
        """,
        (customer,), as_dict=True)
    return [r["gouvernorat"] for r in lignes if r["gouvernorat"]]


def lien_rdv_pour_client(customer, secteur, config=None):
    """Le lien du portail SI ce client peut réellement réserver en ligne, sinon "".

    Deux cas ouvrent la réservation (demande 30/08/2026) :
      1. il est dans NOS secteurs (l'équipe de Tunis passe déjà chez lui) ;
      2. il est « Hors Secteur » mais son gouvernorat est couvert par un
         PARTENAIRE déclaré dans Config Portail RDV (Sousse, Monastir…).
    Proposer le lien à un client hors zone serait cruel : le portail lui
    répondrait « aucun créneau ». On ne le met donc que si ça marche.
    """
    config = config or _config_portail()
    if not config:
        return ""

    # `secteurs` vient d'un GROUP_CONCAT : un client peut avoir plusieurs
    # adresses, donc plusieurs secteurs — un seul vrai secteur suffit.
    secteurs = [s.strip() for s in (secteur or "").split(",") if s.strip()]
    if any(s and s != "Hors Secteur" for s in secteurs):
        return frappe.utils.get_url("/rdv")

    from customization_app.portail_rdv_planning import contexte_partenaire
    for gouvernorat in gouvernorats_du_client(customer):
        if contexte_partenaire(config, gouvernorat):
            return frappe.utils.get_url("/rdv")
    return ""


def emails_du_client(customer):
    """Les e-mails des contacts du client — jamais `Customer.email_id`, vide dès
    qu'aucune ligne n'est marquée « principale » (cf. _coordonnees_des_contacts)."""
    from customization_app.retenue_source import _coordonnees_des_contacts

    return list((_coordonnees_des_contacts([customer]).get(customer) or {})
                .get("emails") or [])


def corps_email_relance(message, lien_rdv):
    """Le même texte que le SMS, mis en page pour l'e-mail : le lien devient un
    bouton cliquable (dans un SMS il reste en clair, on ne le duplique pas)."""
    texte = message.replace(lien_rdv, "").rstrip() if lien_rdv else message
    corps = "<p>%s</p>" % frappe.utils.escape_html(texte).replace("\n", "<br>")
    if lien_rdv:
        corps += (
            '<p style="margin:18px 0"><a href="{0}" '
            'style="background:#0ea5e9;color:#fff;text-decoration:none;'
            'padding:10px 18px;border-radius:8px;display:inline-block;'
            'font-weight:600">📅 Prendre rendez-vous en ligne</a></p>'
            '<p style="font-size:12px;color:#64748b">Ou copiez ce lien : {0}</p>'
        ).format(lien_rdv)
    return corps


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

HORS_SECTEUR = "Hors Secteur"


def message_relance(T, nom_client, appareil, cout, telephones, lien_boutique, secteur, lien_rdv, voeux="", promo="", zone_partenaire=False):
    """Le texte de la relance, assemblé depuis le réglage « Config Modeles SMS » (modeles_sms). PURE.
    Client de nos secteurs — ou de la ZONE d'un partenaire (Sousse, Monastir, Mahdia… : notre client, même message,
    décision 03/10/2026) : coût de la main-d'œuvre + (portail ouvert → lien RDV, sinon « contactez-nous ») ;
    hors secteur : sans coût, vente de filtres à distance."""
    from customization_app.modeles_sms import rendre
    hors = secteur == HORS_SECTEUR and not zone_partenaire
    valeurs = {"nom_client": nom_client, "voeux": voeux or "", "appareil": appareil, "promo": promo or "",
               "telephones": telephones, "lien_rdv": lien_rdv or "", "lien_boutique": lien_boutique or "",
               "cout": None if hors else cout}
    if lien_rdv:
        suite = T["relance_rdv_en_ligne"]
    else:
        suite = T["relance_hors_secteur"] if hors else T["relance_sans_lien"]
    return rendre(T["relance_entretien"], dict(valeurs, suite=rendre(suite, valeurs)))


def envoyer_relances_maintenance(dry_run: bool = False):
    """
    À appeler via cron / scheduler.

    dry_run = True  -> ne pas envoyer les SMS, ne rien modifier en BDD,
                      renvoyer simplement la liste des messages préparés : UNE entrée par SMS (par client quand le
                      réglage « un_sms_par_client_par_jour » est actif), avec les visites qu'il couvre (« visites »).

    Retourne un dict :
    {
        "dry_run": bool,
        "summary": {...},
        "preview": [...]  # seulement si dry_run=True
        "log": [ "ligne1", "ligne2", ... ]
    }

    Résumé (25/11/2026) — les clés d'avant gardent leur sens : sms1_candidates / sms2_candidates / total_sms_candidates
    = les SMS qu'aurait envoyés l'ancien code (un par échéancier × type) ; envois_candidats = les SMS après
    regroupement par client, sms_evites = la différence, total_sms_prevus = les envois retenus par le plafond.
    Exclusions : clients_exclus (clients distincts ayant un échéancier actif), exclus_par_motif (chaque client sous
    son premier motif, la somme = clients_exclus), exclus_partenaire (les seuls clients du partenaire),
    echeanciers_exclus (l'ancien compte, en échéanciers). marquages_secours = échéanciers qui ont refusé leur
    sauvegarde après l'envoi et dont les visites ont été marquées sans validation (marquer_en_base).
    """
    today = nowdate()
    log("========== [CRON] Début envoyer_relances_maintenance ==========")
    cfg = RC.config()
    fams = cfg.get("familles") or RC.familles()
    un_par_client = cint(cfg.get("un_sms_par_client_par_jour", 1))

    # Structure de summary pour log + retour
    summary = {
        "date": today,
        "un_sms_par_client": un_par_client,
        "total_maintenance_schedules": 0,
        "clients_avec_echeancier": 0,
        "clients_avec_rdv": 0,
        "clients_exclus": 0,
        "exclus_par_motif": {m: 0 for m in MOTIFS_EXCLUSION},
        "exclus_partenaire": 0,
        "echeanciers_exclus": 0,
        "sms1_candidates": 0,
        "sms2_candidates": 0,
        "total_sms_candidates": 0,
        "visites_candidates": 0,
        "clients_candidats": 0,
        "clients_plusieurs_sms": 0,
        "envois_candidats": 0,
        "sms_evites": 0,
        "total_sms_prevus": 0,
        "sms_hors_plafond": 0,
        "repartition_sms_par_secteur": {},
        "secteurs_autorises": [],
        "sms_sent_success": 0,
        "sms_invalid_number": 0,
        "sms_failed": 0,
        "emails_sent": 0,
        "emails_failed": 0,
        "visites_marquees": 0,
        "marquages_secours": 0,
        "maintenances_extended": 0,
        "erreurs": 0,
    }

    # On ne fait rien certains jours (jours fériés, etc.)
    if False and today in EXCLUDED_DATES:   # jours fériés 2024 : obsolète (réglage à venir si besoin)
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
    relance_date = add_days(today, -cfg["delai_sms2_jours"])  # fenêtre pour considérer les SMS récents (réglage)

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

    # 2) Clients à ne pas relancer : rendez-vous récent ou à venir — MÊME règle que la liste d'appels (relances_config).
    clients_avec_rdv = RC.clients_avec_rdv(cfg)
    summary["clients_avec_rdv"] = len(clients_avec_rdv)

    # Clients hors relance : gérés par le partenaire, « À requalifier » / « Perdu », « Non » à l'entretien, reportés.
    # RC.clients_exclus_relance reste la SEULE règle du filtre (commune aux trois crons) ; les motifs ne servent qu'au
    # journal et à l'entonnoir (compteurs séparés, 25/11/2026).
    exclus = RC.clients_exclus_relance(cfg)
    # « Non aux SMS » (26/11/2026) : AUCUN SMS ni e-mail, mais leurs visites sont marquées « Refus SMS » aux mêmes dates
    # (envoyer_et_marquer_sms) — sinon le client n'entrerait plus dans aucune liste d'appel, qui part des visites
    # relancées SMS 1 + SMS 2 (relecture finale du 26/11/2026). Ils ne comptent pas dans le plafond quotidien.
    refus_sms = RC.clients_refus_sms()
    clients = {m["customer"] for m in all_maintenance if m["customer"]}
    summary["clients_avec_echeancier"] = len(clients)
    try:
        motifs = motifs_exclusion(cfg)
    except Exception:
        motifs = {}
        summary["erreurs"] += 1
        frappe.log_error(title="SMS Maintenance — motifs d'exclusion", message=frappe.get_traceback())
    par_motif = compter_exclusions(clients, clients_avec_rdv, exclus, motifs)
    summary["exclus_par_motif"] = par_motif
    summary["exclus_partenaire"] = par_motif["partenaire"]
    summary["clients_exclus"] = sum(par_motif.values())

    # 3) Les SMS dus, échéancier par échéancier : un « paquet » = le SMS qu'envoyait l'ancien code (un échéancier × un
    #    type, toutes ses visites dues). Un échéancier en erreur est journalisé et n'arrête pas le passage.
    list_sms1 = []
    list_sms2 = []
    familles_articles = {}

    def famille_de(code):
        return _famille_article(code, familles_articles, fams)

    for imant in all_maintenance:
        if imant["customer"] in clients_avec_rdv or imant["customer"] in exclus:
            # Rendez-vous planifié ou client hors relance : on ne l'inclut pas
            summary["echeanciers_exclus"] += 1
            continue

        secteur = imant["secteurs"] or "Secteur 1"
        try:
            sms1, sms2 = calculer_sms_pour_maintenance(
                maint_name=imant["schedule_name"],
                today=today,
                relance_date=relance_date,
                secteur=secteur,
            )
            if sms1:
                list_sms1.append(visites_du_paquet(sms1, imant["customer"], famille_de))
            if sms2:
                list_sms2.append(visites_du_paquet(sms2, imant["customer"], famille_de))
        except Exception:
            summary["erreurs"] += 1
            frappe.log_error(title="SMS Maintenance — échéancier %s" % imant["schedule_name"], message=frappe.get_traceback())

    # Tri par secteur (stable), SMS1 d'abord : l'ordre d'envoi d'avant
    list_sms1 = sorted(list_sms1, key=lambda p: p[0]["secteur"])
    list_sms2 = sorted(list_sms2, key=lambda p: p[0]["secteur"])
    paquets = list_sms1 + list_sms2

    summary["sms1_candidates"] = len(list_sms1)
    summary["sms2_candidates"] = len(list_sms2)
    summary["total_sms_candidates"] = len(paquets)

    # 4) Un SMS par client et par jour (réglage) : les paquets d'un même client fusionnent en un envoi.
    envois = regrouper_envois(paquets, [f["code"] for f in fams], un_par_client)
    summary.update(bilan_regroupement(paquets, envois))

    # 5) Secteurs autorisés dans la limite du réglage (un SMS par envoi, donc par client si regroupé)
    secteurs_autorises, repartition_sms, total_sms_prevus = calculer_secteurs_autorises(
        [e for e in envois if e.get("customer") not in refus_sms])
    summary["secteurs_autorises"] = secteurs_autorises
    summary["repartition_sms_par_secteur"] = repartition_sms
    summary["total_sms_prevus"] = total_sms_prevus

    # 6) Envoi / simulation des SMS + update éventuel
    preview, stats = envoyer_et_marquer_sms(
        envois,
        secteurs_autorises,
        today,
        dry_run=dry_run,
        refus=refus_sms,
    )

    # fusion des stats dans le summary
    for cle in ("sms_sent_success", "sms_invalid_number", "sms_failed", "emails_sent", "emails_failed",
                "maintenances_extended", "visites_marquees", "marquages_secours", "sms_hors_plafond", "refus_sms"):
        summary[cle] = stats.get(cle, 0)
    summary["erreurs"] += stats.get("erreurs", 0)

    # Logs finaux
    summary_line = ligne_resume(summary)
    end_line = "========== [CRON] Fin envoyer_relances_maintenance =========="

    log(summary_line)
    log(end_line)

    # 7) Un VRAI passage remet ses compteurs à l'entonnoir des relances (jamais un aperçu).
    if not dry_run:
        noter_entonnoir(summary)

    return {
        "dry_run": bool(dry_run),
        "summary": summary,
        "preview": preview if dry_run else None,
        "log": [summary_line, end_line],
    }


# ---------------------------------------------------------------------------
#  Exclusions par motif, résumé, entonnoir (25/11/2026)
# ---------------------------------------------------------------------------

# Ordre d'attribution : chaque client écarté est compté UNE fois, sous le premier motif qu'il porte. Les motifs du
# réglage d'abord (durables), le rendez-vous ensuite (passager), « autre » = exclu par RC sans motif reconnu ici.
MOTIFS_DU_REGLAGE = ("partenaire", "perdu", "a_requalifier", "non_interesse", "reporte")
MOTIFS_EXCLUSION = MOTIFS_DU_REGLAGE + ("rendez_vous", "autre")


def motifs_exclusion(cfg) -> dict:
    """{motif: {clients}} lus aux MÊMES sources que RC.clients_exclus_relance — pour le JOURNAL seulement : le filtre,
    lui, reste RC.clients_exclus_relance (règle commune). Avant le 25/11/2026 tout tombait dans « exclus_partenaire »
    (529 échéanciers à la nuit simulée du 26/11), sans dire pourquoi un client n'était plus relancé."""
    from customization_app import partenaire_clients

    out = {m: set() for m in MOTIFS_DU_REGLAGE}
    out["partenaire"] = set(partenaire_clients.clients_geres())
    if frappe.db.has_column("Customer", "custom_statut_relance"):
        for nom, statut in frappe.get_all("Customer", filters={"custom_statut_relance": ["in", RC.STATUTS_RELANCE_EXCLUS]},
                                          fields=["name", "custom_statut_relance"], as_list=True):
            out["perdu" if statut == "Perdu" else "a_requalifier"].add(nom)
    if not cfg.get("relancer_non_interesses"):
        out["non_interesse"] = set(frappe.get_all("Customer", filters={"custom_intéressé_par_le_service_entretien": "Non"},
                                                  pluck="name"))
    out["reporte"] = set(RC.clients_reportes())
    return out


def compter_exclusions(clients, clients_rdv, exclus, motifs) -> dict:
    """Les clients (ayant un échéancier actif) que le passage écarte, comptés UNE fois chacun sous leur premier motif
    (ordre MOTIFS_EXCLUSION) : la somme = clients écartés, ce qui rend l'entonnoir lisible. Un client exclu par RC
    porte d'abord ses motifs du réglage ; un client seulement « rendez-vous » est compté là. PURE.
    → {motif: nombre} pour chaque motif de MOTIFS_EXCLUSION."""
    compte = {m: 0 for m in MOTIFS_EXCLUSION}
    motifs = motifs or {}
    for c in clients or ():
        if c in exclus:
            m = next((m for m in MOTIFS_DU_REGLAGE if c in motifs.get(m, ())), None) \
                or ("rendez_vous" if c in clients_rdv else "autre")
        elif c in clients_rdv:
            m = "rendez_vous"
        else:
            continue
        compte[m] += 1
    return compte


def ligne_resume(s) -> str:
    """La ligne [SUMMARY] du journal maintenance_sms, affichée telle quelle par la page « Journaux des relances ». Les
    champs d'avant gardent leur nom et leur place ; les nouveaux (25/11/2026) suivent, à plat (clé=valeur). PURE."""
    g = s.get
    champs = [
        ("date", g("date")), ("total_ms", g("total_maintenance_schedules", 0)), ("clients_avec_rdv", g("clients_avec_rdv", 0)),
        ("sms1_candidats", g("sms1_candidates", 0)), ("sms2_candidats", g("sms2_candidates", 0)),
        ("total_sms_candidats", g("total_sms_candidates", 0)), ("total_sms_prevus", g("total_sms_prevus", 0)),
        ("sms_envoyes", g("sms_sent_success", 0)), ("sms_invalid", g("sms_invalid_number", 0)), ("sms_failed", g("sms_failed", 0)),
        ("emails_envoyes", g("emails_sent", 0)), ("emails_failed", g("emails_failed", 0)),
        ("ms_extendues", g("maintenances_extended", 0)),
        ("un_sms_par_client", g("un_sms_par_client", 0)), ("envois_candidats", g("envois_candidats", 0)),
        ("clients_plusieurs_sms", g("clients_plusieurs_sms", 0)), ("sms_evites", g("sms_evites", 0)),
        ("visites_candidates", g("visites_candidates", 0)), ("visites_marquees", g("visites_marquees", 0)),
        ("hors_plafond", g("sms_hors_plafond", 0)), ("clients_exclus", g("clients_exclus", 0)),
    ]
    par_motif = g("exclus_par_motif") or {}
    champs += [("exclus_" + m, par_motif.get(m, 0)) for m in MOTIFS_EXCLUSION]
    champs.append(("marquages_secours", g("marquages_secours", 0)))
    champs.append(("erreurs", g("erreurs", 0)))
    return "[SUMMARY] " + ", ".join("%s=%s" % kv for kv in champs)


def compteurs_entonnoir(summary) -> dict:
    """Les compteurs du jour remis à l'entonnoir (contrat noter(source, compteurs, jour)) : les nombres entiers du
    résumé, plus un « exclus_<motif> » par motif. PURE."""
    out = {k: v for k, v in (summary or {}).items() if isinstance(v, int) and not isinstance(v, bool)}
    for m, n in ((summary or {}).get("exclus_par_motif") or {}).items():
        out["exclus_" + m] = n
    return out


def noter_entonnoir(summary) -> bool:
    """Après un VRAI passage : les compteurs du jour à l'entonnoir des relances (contrat partagé
    customization_app.entonnoir_relances.noter, 25/11/2026). L'analyse du 25/11 a montré qu'une baisse (la marche du
    03-04/10) ne se voyait qu'au bout de deux mois : chaque passage laisse désormais sa trace en base. Jamais bloquant :
    module pas encore livré = une ligne au journal, module cassé ou toute autre erreur = Error Log (appelé APRÈS le
    commit des marques : une erreur ici ne doit pas faire croire au planificateur que les SMS n'ont pas été marqués).
    Le planificateur valide après le cron (ScheduledJobType.execute) : l'écriture de noter() est donc conservée.
    → le résultat de noter() (False tant que la table « Entonnoir Relances » n'est pas migrée)."""
    try:
        from customization_app.entonnoir_relances import noter
    except ImportError:
        log("entonnoir des relances absent : compteurs SMS non notés")
        return False
    except Exception:
        frappe.log_error(title="SMS Maintenance — entonnoir", message=frappe.get_traceback())
        return False
    try:
        return bool(noter("sms", compteurs_entonnoir(summary), jour=summary.get("date")))
    except Exception:
        frappe.log_error(title="SMS Maintenance — entonnoir", message=frappe.get_traceback())
        return False


# ---------------------------------------------------------------------------
#  Partie 1 : Calcul des SMS (SMS1 & SMS2) pour un échéancier
# ---------------------------------------------------------------------------

def choisir_relances(lignes, today, delai_sms2: int, jours_lassitude: int):
    """Par article, UN SEUL cycle à la fois (02/11/2026). PURE.

    `lignes` : [{item_code, scheduled_date, custom_sms_1, custom_sms_2, custom_appelle, actual_date, completion_status}].
    La visite ÉCHUE LA PLUS RÉCENTE de l'article est la seule qui compte : sans SMS1 → SMS1 (sauf lassitude : un SMS ou
    un appel sur cet article à moins de `jours_lassitude`) ; avec SMS1 sans SMS2 après `delai_sms2` jours → SMS2. Les
    visites échues plus anciennes sont ignorées — avant, une fois la dernière relancée, le cron remontait aux
    précédentes et relançait un client tous les 7 jours (MAT-MSH-2023-00602 : 5 SMS en 17 jours).
    → ({item: scheduled_date} pour SMS1, {item: scheduled_date} pour SMS2)."""
    today = getdate(today)
    limite = add_days(today, -int(jours_lassitude or 0))
    par_item = {}
    for r in lignes or []:
        g = r.get if isinstance(r, dict) else (lambda k, _r=r: getattr(_r, k, None))
        par_item.setdefault(g("item_code"), []).append({k: g(k) for k in ("scheduled_date", "custom_sms_1", "custom_sms_2",
                                                                            "custom_appelle", "actual_date", "completion_status")})
    sms1, sms2 = {}, {}
    for item, rows in par_item.items():
        echues = sorted((r for r in rows if r["scheduled_date"] and getdate(r["scheduled_date"]) < today
                         and not r["actual_date"] and r.get("completion_status") != "Fully Completed"),
                        key=lambda r: getdate(r["scheduled_date"]))
        if not echues:
            continue
        derniere = echues[-1]
        if not derniere["custom_sms_1"]:
            recent = any(d and getdate(d) > limite for r in rows for d in (r["custom_sms_1"], r["custom_sms_2"], r["custom_appelle"]))
            if not recent:
                sms1[item] = getdate(derniere["scheduled_date"])
        elif not derniere["custom_sms_2"] and today >= add_days(getdate(derniere["custom_sms_1"]), int(delai_sms2)):
            sms2[item] = getdate(derniere["scheduled_date"])
    return sms1, sms2


def calculer_sms_pour_maintenance(maint_name, today, relance_date, secteur):
    """
    Retourne deux dict :
        - NewSMS1 : { "item_code*?/*schedule_name*?/*SMS1": [scheduled_date, secteur], ... }
        - NewSMS2 : idem pour SMS2
    (`relance_date` n'est plus utilisé : la lassitude se règle par `jours_entre_deux_appels`.)
    """
    cfg = RC.config()
    maintenance = frappe.get_doc("Maintenance Schedule", maint_name)
    date_par_item_sms1, date_par_item_sms2 = choisir_relances(maintenance.schedules, today, cfg["delai_sms2_jours"],
                                                              cfg["jours_entre_deux_appels"])

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
#  Partie 1 bis : un seul SMS par client et par jour (25/11/2026)
# ---------------------------------------------------------------------------

SEPARATEUR_CLE = "*?/*"
APPAREIL_PLUSIEURS_FAMILLES = "vos équipements de traitement d'eau"


def visites_du_paquet(paquet, customer, famille_de):
    """Les visites d'un paquet de calculer_sms_pour_maintenance ({"article*?/*échéancier*?/*SMS1": [date, secteur]}).
    Un paquet = UN SMS de l'ancien envoi (un échéancier × un type, toutes ses visites dues). PURE (famille_de(article)
    injecté). → [{customer, maintenance, item_code, sms_type, scheduled_date, secteur, famille}]"""
    out = []
    for cle, (date_prevue, secteur) in (paquet or {}).items():
        item_code, maint_name, sms_type = (cle.split(SEPARATEUR_CLE) + ["", "", ""])[:3]
        out.append({"customer": customer, "maintenance": maint_name, "item_code": item_code, "sms_type": sms_type,
                    "scheduled_date": date_prevue, "secteur": secteur, "famille": famille_de(item_code)})
    return out


def _famille_article(item_code, cache, fams):
    """La famille machine d'un article (groupe d'article → réglage), mémorisée. Un article disparu → None (avant :
    frappe.get_doc levait et arrêtait tout le passage)."""
    if item_code not in cache:
        groupe = frappe.db.get_value("Item", item_code, "item_group") if item_code else None
        cache[item_code] = RC.famille_du_groupe_machine(groupe, fams)
    return cache[item_code]


def _rang_famille(famille, ordre):
    """Rang de priorité d'une famille (ordre du réglage) ; inconnue après, sans famille en dernier. PURE."""
    if famille in ordre:
        return ordre.index(famille)
    return len(ordre) + (0 if famille else 1)


def regrouper_envois(paquets, ordre_familles, un_par_client=True):
    """Les SMS du jour. PURE.

    `paquets` : [[visite, …], …] dans l'ordre d'envoi d'avant (SMS1 par secteur, puis SMS2 par secteur).
    `un_par_client` (réglage « un_sms_par_client_par_jour », 25/11/2026) : les paquets d'un même client fusionnent en
    UN envoi — un client à deux échéanciers (deux machines, un doublon, un deviné de trop) ou dont un échéancier devait
    à la fois un SMS1 et un SMS2 recevait deux SMS le même jour (60 fois du 01/09 au 25/11/2026, 36 clients). Sinon
    (réglage à 0) un envoi par paquet, exactement comme avant.
    → [{"customer", "secteur", "visites", "paquets", "principale", "familles", "familles_message", "nb_paquets",
    "regroupe"}] dans l'ordre de première apparition. `principale` = la visite de la famille prioritaire (ordre du
    réglage), puis l'échéance la plus ancienne : elle donne le secteur, le type affiché et la référence de l'e-mail.
    `familles` = codes distincts de TOUTES les visites couvertes, par priorité. `familles_message` = celles du seul paquet
    de la principale : le SMS envoyé est, mot pour mot, celui qu'aurait envoyé l'ancien code pour ce paquet (relecture
    du 25/11/2026 — avant, un client à UN seul SMS dont l'échéancier devait adoucisseur + UV perdait « vos équipements
    de traitement d'eau » : 27 SMS non regroupés changeaient de texte du 26/11 au 31/12). `regroupe` = il y a eu
    fusion (plusieurs paquets), pas seulement le réglage."""
    envois, par_client = [], {}
    for paquet in paquets or []:
        visites = [v for v in (paquet or []) if v]
        if not visites:
            continue
        cle = visites[0].get("customer") if un_par_client else None
        if cle is not None and cle in par_client:
            env = par_client[cle]
            env["visites"].extend(visites)
            env["paquets"].append(visites)
            env["nb_paquets"] += 1
            continue
        env = {"customer": visites[0].get("customer"), "visites": list(visites), "paquets": [visites], "nb_paquets": 1}
        envois.append(env)
        if cle is not None:
            par_client[cle] = env

    def par_priorite(visites):
        distinctes = {v.get("famille") for v in visites if v.get("famille")}
        return sorted(distinctes, key=lambda f: (_rang_famille(f, ordre_familles), f))

    for env in envois:
        env["regroupe"] = env["nb_paquets"] > 1
        env["principale"] = min(env["visites"], key=lambda v: (_rang_famille(v.get("famille"), ordre_familles),
                                                               str(v.get("scheduled_date") or ""), v.get("maintenance") or "",
                                                               v.get("item_code") or "", v.get("sms_type") or ""))
        env["secteur"] = env["principale"].get("secteur")
        env["familles"] = par_priorite(env["visites"])
        paquet_principal = next(p for p in env["paquets"] if any(v is env["principale"] for v in p))
        env["familles_message"] = par_priorite(paquet_principal)
    return envois


def bilan_regroupement(paquets, envois) -> dict:
    """Les chiffres du regroupement pour le résumé et l'entonnoir. PURE.
    clients_plusieurs_sms = clients qui auraient reçu plusieurs SMS ce jour-là avec l'ancien envoi ;
    sms_evites = SMS d'avant − SMS envoyés (0 quand le réglage est à 0)."""
    pleins = [p for p in paquets or [] if p]
    par_client = Counter(p[0].get("customer") for p in pleins)
    return {
        "visites_candidates": sum(len(p) for p in pleins),
        "clients_candidats": len(par_client),
        "clients_plusieurs_sms": sum(1 for n in par_client.values() if n > 1),
        "envois_candidats": len(envois or []),
        "sms_evites": len(pleins) - len(envois or []),
    }


def texte_appareil(familles, fams=None) -> str:
    """Le nom de l'appareil dans le SMS : la règle d'avant, « vos équipements de traitement d'eau » quand le paquet doit
    plusieurs familles, sinon le libellé de sa famille. PURE avec `fams`.
    On lui passe les familles du paquet de la famille prioritaire (`familles_message` de regrouper_envois) : un client
    regroupé reçoit UN message, celui de la famille prioritaire avec son coût (décision du 25/11/2026), et ce message
    est exactement l'un de ceux d'avant. Relecture du 25/11/2026 : la version précédente nommait la seule famille
    prioritaire dès que le réglage était actif, même sans regroupement (Sté LMM, adoucisseur + UV, le 28/11)."""
    codes = [f for f in (familles or []) if f]
    if len(set(codes)) > 1:
        return APPAREIL_PLUSIEURS_FAMILLES
    return RC.libelle_famille(codes[0], fams) if codes else RC.LIBELLE_FAMILLE_DEFAUT


def marquer_visites(lignes, visites, jour, statut) -> int:
    """Pose la date et le statut du SMS sur CHAQUE visite couverte par l'envoi, chacune dans SON champ (SMS1 →
    custom_sms_1 / custom_sms1_status, SMS2 → custom_sms_2 / custom_sms2_status). Une visite = la première ligne de
    même article et même date prévue (règle d'avant). PURE (modifie les lignes reçues). → nombre de lignes marquées."""
    n = 0
    for v in visites or []:
        prevue = getdate(v["scheduled_date"]) if v.get("scheduled_date") else None
        for row in lignes or []:
            if row.item_code != v.get("item_code") or not row.scheduled_date or getdate(row.scheduled_date) != prevue:
                continue
            if v.get("sms_type") == "SMS1":
                row.custom_sms_1 = jour
                row.custom_sms1_status = statut
                n += 1
            elif v.get("sms_type") == "SMS2":
                row.custom_sms_2 = jour
                row.custom_sms2_status = statut
                n += 1
            break
    return n


# ---------------------------------------------------------------------------
#  Partie 2 : calcul des secteurs autorisés
# ---------------------------------------------------------------------------

def _secteur_de(envoi):
    """Le secteur d'un envoi (regrouper_envois) — ou d'un paquet au format d'avant {cle: [date, secteur]}."""
    if isinstance(envoi, dict) and "visites" in envoi:
        return envoi.get("secteur")
    return list(envoi.values())[0][1]


def secteurs_dans_le_plafond(compte_par_secteur, plafond):
    """La règle du plafond, inchangée : « Hors Secteur » toujours autorisé et compté en premier ; les autres secteurs,
    du plus gros au plus petit, pris EN ENTIER seulement s'ils tiennent sous le plafond. PURE.
    → (secteurs_autorises, {secteur: nb} dans l'ordre d'examen, total retenu)"""
    count_sms = dict(compte_par_secteur or {})
    hors_secteur_value = count_sms.pop(HORS_SECTEUR, 0)
    sorted_count_sms = dict(sorted(count_sms.items(), key=lambda item: item[1], reverse=True))
    if hors_secteur_value:
        sorted_count_sms = {HORS_SECTEUR: hors_secteur_value, **sorted_count_sms}

    secteurs_autorises = []
    total_sms = 0
    for secteur, nb in sorted_count_sms.items():
        if secteur == HORS_SECTEUR or total_sms + nb <= plafond:
            secteurs_autorises.append(secteur)
            total_sms += nb
    return secteurs_autorises, sorted_count_sms, total_sms


def calculer_secteurs_autorises(list_sms, plafond=None):
    """
    Calcule :
    - nombre de SMS par secteur — un par ENVOI : par client quand le réglage « un_sms_par_client_par_jour » est actif
      (25/11/2026), le plafond compte donc des clients et non plus des échéanciers × types
    - liste de secteurs autorisés en respectant le plafond du réglage (max_sms_par_jour)

    Retourne:
    - secteurs_autorises: list
    - sorted_count_sms: dict secteur -> nb_sms
    - total_sms: int
    """
    count_sms = {}
    for sms in list_sms or []:
        secteur = _secteur_de(sms)
        count_sms[secteur] = count_sms.get(secteur, 0) + 1

    plafond = RC.config()["max_sms_par_jour"] if plafond is None else cint(plafond)
    secteurs_autorises, sorted_count_sms, total_sms = secteurs_dans_le_plafond(count_sms, plafond)

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

    from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import (
        normaliser_sms,
    )

    phone = f"216{numero}"

    # Lire config depuis SMS Settings
    ss = frappe.get_doc("SMS Settings", "SMS Settings")
    params = {p.parameter: p.value for p in ss.get("parameters") if not p.header}
    params[ss.receiver_parameter] = phone
    # GSM-7 : un seul « — » ou une espace insécable ferait basculer TOUT le SMS
    # en unicode (67 caractères par segment au lieu de 153).
    params[ss.message_parameter] = normaliser_sms(message)
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

STATUT_REFUS_SMS = "Refus SMS"


def envoyer_et_marquer_sms(list_sms, secteurs_autorises, today, dry_run: bool = False, refus=None):
    """
    Parcourt les ENVOIS du jour (regrouper_envois : un par client, ou un par échéancier × type si le réglage
    « un_sms_par_client_par_jour » est à 0), filtre par secteurs autorisés.

    - Si dry_run = False :
        * envoie UN SMS (et un e-mail) par envoi,
        * marque CHAQUE visite couverte : custom_sms_1 ou custom_sms_2 selon la sienne, avec le statut d'envoi,
        * appelle extend_schedule_for_sms sur chaque échéancier touché,
        * commit en base.
      Chaque client et chaque échéancier sont isolés (savepoint) : une erreur part en Error Log
      « SMS Maintenance — … » et n'arrête pas les autres (avant, une seule fiche en erreur arrêtait tout le passage).
    - Si dry_run = True :
        * ne fait AUCUN appel API,
        * ne modifie PAS la base,
        * retourne une entrée de prévisualisation par envoi, avec les visites qu'il couvre.

    Retourne (preview, stats):
      - preview: liste de dicts (messages) si dry_run=True, sinon []
      - stats: dict avec compteurs (SMS comptés par envoi, visites marquées à part)
    """
    today_date = getdate(today)
    fams = RC.config().get("familles") or RC.familles()

    maintenance_cache = {}
    customer_cache = {}

    extended_maintenances = set()
    preview = []  # liste des messages pour DRY RUN

    stats = {
        "sms_sent_success": 0,
        "sms_invalid_number": 0,
        "sms_failed": 0,
        "emails_sent": 0,
        "emails_failed": 0,
        "maintenances_extended": 0,
        "visites_marquees": 0,
        "marquages_secours": 0,
        "sms_hors_plafond": 0,
        "refus_sms": 0,
        "erreurs": 0,
    }
    refus = set(refus or ())

    simulation = simulation_envois()
    config_portail = _config_portail()

    # Gestion promo Ramadhan
    promo_ramdhan = ""
    if "2024-03-10" <= today <= "2024-04-08":
        promo_ramdhan = "\nPromo: Ramadan reduc allant à 33% sur nos filtres rechanges"

    corp = ""
    if "2024-03-10" <= today <= "2024-03-16":
        corp = "\nInchallah Ramdhanek Mabrouk."
    website_url, phones_emp = get_relance_config()
    phones_txt = _format_phones_for_message(phones_emp)
    modeles = modeles_sms.textes()        # textes du réglage « Config Modeles SMS », défauts = textes historiques
    from customization_app import partenaire_clients
    zones_partenaires = partenaire_clients.zones_partenaires()
    for envoi in list_sms:
        secteur = envoi.get("secteur")
        principale = envoi.get("principale") or {}
        if not envoi.get("visites") or not principale.get("maintenance") or not secteur:
            continue

        customer_name = envoi.get("customer")
        if customer_name in refus:
            # « Non aux SMS » : rien ne part (ni SMS ni e-mail), hors plafond ; les visites sont marquées « Refus SMS » aux
            # dates d'un SMS pour garder le circuit de l'appel (relecture finale du 26/11/2026). Pas d'aperçu.
            stats["refus_sms"] += 1
            if not dry_run:
                log("%s — %s : %s visite(s) marquée(s) sans envoi" % (STATUT_REFUS_SMS, customer_name, len(envoi["visites"])))
                marquer_envoi(envoi, STATUT_REFUS_SMS, today_date, maintenance_cache, extended_maintenances, stats)
            continue

        if secteur not in secteurs_autorises:
            stats["sms_hors_plafond"] += 1
            continue

        try:
            # --- Construction description en fonction de la famille de machine ---
            # Famille PRIORITAIRE (ordre du réglage, déterministe) : libellé et coût de la main-d'œuvre — règle commune
            # avec la liste d'appels. Le texte et le coût sont ceux du paquet de la famille prioritaire : regroupé ou
            # non, le SMS est mot pour mot l'un de ceux d'avant (texte_appareil, relecture du 25/11/2026).
            familles = envoi.get("familles") or []
            familles_message = envoi.get("familles_message")
            if familles_message is None:
                familles_message = familles
            primary_family, cout = RC.cout_entretien(familles_message, fams)
            desc = texte_appareil(familles_message, fams)

            if customer_name not in customer_cache:
                customer_cache[customer_name] = frappe.get_doc("Customer", customer_name)
            client = customer_cache[customer_name]

            promo = promo_ramdhan  # copie locale

            # Le portail n'est proposé qu'aux clients qui peuvent VRAIMENT y
            # réserver : nos secteurs, ou un gouvernorat couvert par un partenaire.
            lien_rdv = lien_rdv_pour_client(customer_name, secteur, config_portail)

            zone = partenaire_clients.zone_partenaire_du_client(customer_name, zones_partenaires) if (secteur == HORS_SECTEUR and lien_rdv) else None
            message = message_relance(modeles, nom_client=client.customer_name, appareil=desc, cout=cout, telephones=phones_txt,
                                      lien_boutique=website_url, secteur=secteur, lien_rdv=lien_rdv, voeux=corp, promo=promo,
                                      zone_partenaire=bool(zone))
        except Exception:
            stats["erreurs"] += 1
            frappe.log_error(title="SMS Maintenance — message %s" % customer_name, message=frappe.get_traceback())
            continue

        maint_name = principale["maintenance"]

        # Numéros de téléphone
        try:
            list_tel = traiter_numero_tel(client.custom_liste_telephone)
        except Exception:
            _logger().error(f"Erreur numéros client : {client.customer_name}")
            list_tel = []

        # E-mails des contacts : la relance part AUSSI par e-mail quand le
        # client en a un (demande 30/08/2026) — c'est le seul canal qui reste
        # pour ceux dont le numéro est invalide.
        try:
            list_email = emails_du_client(customer_name)
        except Exception:
            _logger().error(f"Erreur e-mails client : {client.customer_name}")
            list_email = []

        # Si DRY RUN : on n'envoie rien, on marque juste la preview (une entrée par SMS, ses visites dedans)
        if dry_run:
            preview.append({
                "maintenance": maint_name,
                "customer": customer_name,
                "customer_name": client.customer_name,
                "secteur": secteur,
                "familles": list(familles),
                "familles_message": list(familles_message),
                "primary_family": primary_family,
                "cout": float(cout) if cout is not None else None,
                "message": message,
                "phones": list_tel,
                "emails": list_email,
                "lien_rdv": lien_rdv,
                "sms_type": principale.get("sms_type"),
                "scheduled_date": str(principale["scheduled_date"]) if principale.get("scheduled_date") else None,
                "nb_sms_avant": envoi.get("nb_paquets", 1),
                "visites": [{"maintenance": v.get("maintenance"), "item_code": v.get("item_code"), "sms_type": v.get("sms_type"),
                             "scheduled_date": str(v["scheduled_date"]) if v.get("scheduled_date") else None,
                             "famille": v.get("famille")} for v in envoi["visites"]],
            })
            # On ne modifie pas la base, on ne fait pas d'extend_schedule_for_sms
            continue

        # ----------- MODE NORMAL (envoi réel) ----------------
        status_sms = "Invalid Number" if not list_tel else "Failed"

        for tel in list_tel:
            if simulation:
                status_sms = "Success"
                log(f"[SIMULÉ dev] SMS -> {tel}")
                continue
            try:
                resp = envoyer_sms_winsms(tel, message)
                data = resp.json()
                if data.get("message") == "Successfully Send":
                    status_sms = "Success"
            except Exception:
                _logger().error(
                    f"Echec SMS vers {tel} pour client {client.customer_name}"
                )

        if list_email:
            if simulation:
                log(f"[SIMULÉ dev] e-mail -> {', '.join(list_email)}")
            else:
                try:
                    frappe.sendmail(
                        recipients=list_email,
                        subject="Aqua World & Servicing — l'entretien de votre équipement est à échéance",
                        message=corps_email_relance(message, lien_rdv),
                        reference_doctype="Maintenance Schedule",
                        reference_name=maint_name)
                    stats["emails_sent"] += len(list_email)
                except Exception:
                    stats["emails_failed"] += 1
                    _logger().error(
                        f"Echec e-mail pour client {client.customer_name}")

        log("%s — %s : %s visite(s), %s SMS d'avant" % (status_sms, customer_name, len(envoi["visites"]),
                                                      envoi.get("nb_paquets", 1)))
        log(message)

        # Compteurs stats : UN SMS par envoi, quel que soit le nombre de visites couvertes
        if status_sms == "Success":
            stats["sms_sent_success"] += 1
        elif status_sms == "Invalid Number":
            stats["sms_invalid_number"] += 1
        else:
            stats["sms_failed"] += 1

        # Mise à jour des flags custom_sms_1 / custom_sms_2 de TOUTES les visites couvertes, puis 🔁 extension de
        # chaque échéancier touché UNIQUEMENT SI SMS considéré "envoyé"
        marquer_envoi(envoi, status_sms, today_date, maintenance_cache, extended_maintenances, stats)

    if dry_run:
        # Pas de commit, on renvoie juste la preview + stats
        return preview, stats

    frappe.db.commit()
    return [], stats


CHAMPS_SMS = ("custom_sms_1", "custom_sms1_status", "custom_sms_2", "custom_sms2_status")


def marquer_en_base(maint_name, visites, jour, statut) -> int:
    """Repli de marquer_envoi quand l'échéancier refuse sa sauvegarde APRÈS l'envoi (relecture du 25/11/2026) : la date
    et le statut du SMS sont posés directement sur les lignes « Maintenance Schedule Detail », sans valider la fiche.
    Sans ce repli, une fiche qui refuse save() (validation, lien cassé) restait sans marque : choisir_relances retrouvait
    ses visites dues et le client recevait le même SMS chaque jour, avec pour seule trace une ligne d'Error Log par jour.
    Même choix de ligne que marquer_visites (la première de même article et même date prévue, dans l'ordre idx), lue en
    base : le Server Script « Update Schedule based on Real Date » décale les dates au save, la fiche en mémoire n'est
    donc pas fiable ici. → nombre de visites marquées."""
    lignes = frappe.db.sql(
        """SELECT name, item_code, scheduled_date, custom_sms_1, custom_sms1_status, custom_sms_2, custom_sms2_status
           FROM `tabMaintenance Schedule Detail`
           WHERE parent = %s AND parenttype = 'Maintenance Schedule' ORDER BY idx""",
        (maint_name,), as_dict=True)
    avant = [{c: r.get(c) for c in CHAMPS_SMS} for r in lignes]
    n = marquer_visites(lignes, visites, jour, statut)
    for row, valeurs_avant in zip(lignes, avant):
        modifs = {c: row.get(c) for c in CHAMPS_SMS if row.get(c) != valeurs_avant[c]}
        if modifs:
            frappe.db.set_value("Maintenance Schedule Detail", row.name, modifs, update_modified=False)
    return n


def marquer_envoi(envoi, status_sms, jour, maintenance_cache, extended_maintenances, stats):
    """Marque les visites d'un envoi, échéancier par échéancier, chacun dans son savepoint : un échéancier qui refuse la
    sauvegarde est journalisé et les autres restent marqués (le SMS est parti : mieux vaut marquer ce qui peut l'être
    que tout défaire). L'échéancier refusé est alors marqué sans validation (marquer_en_base, relecture du 25/11/2026),
    sinon le même SMS repartait chaque jour ; il n'est pas prolongé. Puis prolonge chaque échéancier bien sauvegardé,
    une fois par passage, si le SMS est considéré parti (Success / Invalid Number — règle d'avant)."""
    par_echeancier = {}
    for v in envoi.get("visites") or []:
        par_echeancier.setdefault(v.get("maintenance"), []).append(v)

    for maint_name, visites in par_echeancier.items():
        if not maint_name:
            continue
        frappe.db.savepoint("sms_marquage")
        try:
            if maint_name not in maintenance_cache:
                maintenance_cache[maint_name] = frappe.get_doc("Maintenance Schedule", maint_name)
            maintenance = maintenance_cache[maint_name]
            n = marquer_visites(maintenance.schedules, visites, jour, status_sms)
            maintenance.save()
            stats["visites_marquees"] += n
        except Exception:
            trace = frappe.get_traceback()
            frappe.db.rollback(save_point="sms_marquage")
            maintenance_cache.pop(maint_name, None)
            stats["erreurs"] += 1
            n = 0
            frappe.db.savepoint("sms_marquage_secours")
            try:
                n = marquer_en_base(maint_name, visites, jour, status_sms)
            except Exception:
                frappe.db.rollback(save_point="sms_marquage_secours")
                trace += "\n--- marquage sans validation, en échec aussi ---\n" + frappe.get_traceback()
            stats["visites_marquees"] += n
            if n:
                stats["marquages_secours"] = stats.get("marquages_secours", 0) + 1
            frappe.log_error(title="SMS Maintenance — marquage %s" % maint_name,
                             message="Sauvegarde refusée après l'envoi du SMS ; %s visite(s) marquée(s) sans validation "
                                     "(sinon le même SMS repartirait demain), échéancier non prolongé.\n\n%s" % (n, trace))
            continue

        if status_sms in ("Success", "Invalid Number") and maint_name not in extended_maintenances:
            extended_maintenances.add(maint_name)
            frappe.db.savepoint("sms_prolongation")
            try:
                if extend_schedule_for_sms(maintenance):            # visites ajoutées (réglage) — 0 si rien à faire
                    maintenance.save()
                    stats["maintenances_extended"] += 1
            except Exception:
                frappe.db.rollback(save_point="sms_prolongation")
                maintenance_cache.pop(maint_name, None)
                stats["erreurs"] += 1
                frappe.log_error(title="SMS Maintenance — prolongation %s" % maint_name, message=frappe.get_traceback())

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