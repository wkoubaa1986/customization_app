"""Listes d'appels d'entretien (« Liste Appelle Entretien ») — cron lundi–samedi 07:00 (run_cron) et bouton de la vue liste
(run_manual). Trois listes : Normal (nos secteurs), Zone partenaire (nos clients de Sousse/Monastir/Mahdia, décision du
03/10/2026), Urgence (clients à échéance d'un secteur lointain où un rendez-vous est déjà planifié), plus la liste
Relance = 2e appel des « Ne répond pas 1er appel ».

Revue du 03/10/2026 (bugs corrigés ici ; réglages dans « Config Relances » = customization_app.relances_config) :
- un client y figurait plusieurs fois (clé = échéancier) → UNE ligne par client, ses échéanciers regroupés ;
- la règle « pas d'appel si une intervention date de moins de 160 j » dépendait de l'ordre des lignes → calculée à part ;
- le 2e appel marquait les listes sources AVANT de créer la nouvelle liste (perte si la création échouait) → l'inverse ;
- la détection d'urgence comparait les dates sans le secteur et comptait les listes annulées ; la répartition comparait
  la chaîne entière des secteurs ; des clients disparaissaient quand l'urgence était détectée mais non créée ;
- plafond de 100 silencieux et tri alphabétique des secteurs → plafond réglable, tri numérique, « (+N en attente) » dans
  le titre ; délai réglable après le SMS 2 ; exclusions de rendez-vous et coût communs avec la relance SMS ;
- run_manual sans rôle qui renvoyait None (« Succès » après un plantage) → rôles, résultat rendu, erreur visible.
"""
from __future__ import annotations

import json
import re

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, nowdate

from customization_app import partenaire_clients, relances_config as RC
from customization_app.utils.run_safely import run_safely

DOCTYPE = "Liste Appelle Entretien"
TYPE_NORMAL, TYPE_RELANCE, TYPE_URGENCE = "Normal", "Relance", "Urgence"
TYPE_ZONE = partenaire_clients.TYPE_LISTE_ZONE
NE_REPOND_PAS = "Ne répond pas 1er appel"
PROMO_SELS = "Promo sac sels: pour l'achat de 5 sacs, le prix du sac est à 28 DT au lieu de 35 DT."


def log(msg):
    RC.journal("creation_liste_appelle").info(msg)


def has_draft_of_type(list_type: str) -> bool:
    return bool(frappe.db.exists(DOCTYPE, {"docstatus": 0, "type_liste": list_type}))


def get_recent_rdv_customers(days_back=None, cfg=None):
    """Clients avec un rendez-vous récent ou à venir : même règle que la relance SMS (relances_config)."""
    return RC.clients_avec_rdv(cfg, jours=days_back)


# =============================================================================
# 0) URGENCE (secteurs lointains du réglage)
# =============================================================================

def _taches_couvertes(listes_urgence) -> set:
    """{(date, secteur)} des rendez-vous déjà portés par une liste Urgence NON annulée."""
    noms = set()
    for l in listes_urgence:
        for n in re.split(r"[\[\]',\s]+", l.get("liste_maintenance") or ""):
            if n:
                noms.add(n)
    if not noms:
        return set()
    return {(str(getdate(d)), s or "") for d, s in frappe.db.sql(
        "select starts_on, secteur from `tabTache de travail` where name in %s and status <> 'Cancelled'", (tuple(noms),)) if d}


def get_urgence_context(today, cfg=None):
    """{gener_urgence, secteur_urgence: [...], related_tache: [...]} : un rendez-vous planifié dans un secteur lointain,
    pas encore couvert par une liste Urgence (même DATE et même SECTEUR)."""
    cfg = cfg or RC.config()
    secteurs = cfg["secteurs_urgence_liste"]
    if not secteurs:
        return {"gener_urgence": False, "secteur_urgence": [], "related_tache": []}
    listes = frappe.get_all(DOCTYPE, filters={"type_liste": TYPE_URGENCE, "docstatus": ["!=", 2]}, fields=["name", "liste_maintenance"])
    couvertes = _taches_couvertes(listes)
    rdv = frappe.db.sql("""select name, starts_on, secteur from `tabTache de travail`
                           where status in ('Open', 'Completed') and starts_on >= %s and ifnull(dans_local, '') <> 'Oui'
                             and custom_client is not null and secteur in %s
                             and custom_type_dintervention in ('Installation', 'Entretien', 'Réparation', 'Visite')""",
                        (str(getdate(today)) + " 00:00:00", tuple(secteurs)), as_dict=True)
    nouveaux = [t for t in rdv if (str(getdate(t.starts_on)), t.secteur or "") not in couvertes]
    return {"gener_urgence": bool(nouveaux), "secteur_urgence": sorted({t.secteur for t in nouveaux if t.secteur}),
            "related_tache": [t.name for t in nouveaux]}


# =============================================================================
# 1) ÉCHÉANCES À APPELER, PAR CLIENT
# =============================================================================

def cle_secteur(secteur: str):
    """Tri NUMÉRIQUE des secteurs (« Secteur 2 » avant « Secteur 10 »), hors secteur / zones à la fin. PURE."""
    m = re.search(r"(\d+)", secteur or "")
    return (0, int(m.group(1)), secteur or "") if m else (1, 0, secteur or "")


def regrouper_par_client(lignes, dernieres_interventions, cfg, today):
    """`lignes` : [{customer, schedule, item_code, scheduled_date, secteurs}] déjà filtrées sur SMS1+SMS2 envoyés, non réalisées,
    non appelées. On retire les articles dont la dernière intervention est trop récente, puis on regroupe par CLIENT :
    {customer: {"echeanciers": {ms: {item: date}}, "secteurs": str, "premiere": date}}. PURE."""
    limite = add_days(getdate(today), -cfg["jours_apres_intervention"])
    out = {}
    for l in lignes:
        derniere = dernieres_interventions.get((l["schedule"], l["item_code"]))
        if derniere and getdate(derniere) >= limite:
            continue
        c = out.setdefault(l["customer"], {"echeanciers": {}, "secteurs": l.get("secteurs") or "", "premiere": None})
        c["echeanciers"].setdefault(l["schedule"], {})
        d = getdate(l["scheduled_date"])
        if l["item_code"] not in c["echeanciers"][l["schedule"]] or d < c["echeanciers"][l["schedule"]][l["item_code"]]:
            c["echeanciers"][l["schedule"]][l["item_code"]] = d
        c["premiere"] = d if c["premiere"] is None or d < c["premiere"] else c["premiere"]
        if not c["secteurs"] and l.get("secteurs"):
            c["secteurs"] = l["secteurs"]
    return out


def echeances_a_appeler(today, cfg=None):
    """Les clients à appeler : SMS1 et SMS2 envoyés (SMS2 depuis `delai_apres_sms2_jours`), visite non réalisée et non
    encore appelée, pas d'intervention récente. Quatre requêtes, quel que soit le nombre d'échéanciers."""
    cfg = cfg or RC.config()
    today = getdate(today)
    lignes = frappe.db.sql("""
        SELECT ms.customer, d.parent AS schedule, d.item_code, d.scheduled_date,
               (SELECT GROUP_CONCAT(DISTINCT a.custom_secteur SEPARATOR ', ') FROM `tabDynamic Link` dl
                  JOIN `tabAddress` a ON a.name = dl.parent
                 WHERE dl.parenttype = 'Address' AND dl.link_doctype = 'Customer' AND dl.link_name = ms.customer) AS secteurs
        FROM `tabMaintenance Schedule Detail` d
        JOIN `tabMaintenance Schedule` ms ON ms.name = d.parent AND ms.docstatus = 1 AND ms.status = 'Submitted'
        WHERE d.scheduled_date < %(today)s AND d.custom_sms_1 IS NOT NULL AND d.custom_sms_2 IS NOT NULL
          AND d.custom_sms_2 <= %(sms2_max)s AND d.actual_date IS NULL AND d.custom_appelle IS NULL""",
        {"today": today, "sms2_max": add_days(today, -cfg["delai_apres_sms2_jours"])}, as_dict=True)
    if not lignes:
        return {}
    schedules = tuple({l.schedule for l in lignes})
    dernieres = {(s, i): d for s, i, d in frappe.db.sql(
        "select parent, item_code, max(actual_date) from `tabMaintenance Schedule Detail` where parent in %s and actual_date is not null group by parent, item_code",
        (schedules,))}
    clients = regrouper_par_client(lignes, dernieres, cfg, today)
    if not clients:
        return {}
    noms = tuple(clients)
    # Nombre d'appels déjà passés (listes validées, hors Relance) et date du dernier appel, PAR CLIENT.
    for c, n in frappe.db.sql("""select a.client, count(distinct l.name) from `tabAppelle Client` a join `tabListe Appelle Entretien` l on l.name = a.parent
                                 where l.docstatus = 1 and ifnull(l.type_liste, 'Normal') <> 'Relance' and a.client in %s group by a.client""", (noms,)):
        clients[c]["nb_appels"] = n
    for c, d in frappe.db.sql("""select ms.customer, max(d.custom_appelle) from `tabMaintenance Schedule Detail` d
                                 join `tabMaintenance Schedule` ms on ms.name = d.parent where ms.customer in %s and d.custom_appelle is not null group by ms.customer""", (noms,)):
        clients[c]["dernier_appel"] = d
    for c in clients.values():
        c.setdefault("nb_appels", 0)
        c.setdefault("dernier_appel", None)
    return clients


def repartir(clients, today, cfg, exclus, zones, zone_du_client, rdv_clients, secteur_urgence, urgence_active):
    """Range chaque client dans normal / zone / urgence (ou l'écarte) : géré par le partenaire, rendez-vous récent,
    appelé il y a moins de `jours_entre_deux_appels`, vrai hors secteur. PURE (zone_du_client injectable)."""
    limite = add_days(getdate(today), -cfg["jours_entre_deux_appels"])
    normal, zone, urgence, ecartes = {}, {}, {}, {"partenaire": 0, "rdv": 0, "recent": 0, "hors_secteur": 0}
    for customer, c in clients.items():
        if customer in exclus:
            ecartes["partenaire"] += 1
            continue
        if customer in rdv_clients:
            ecartes["rdv"] += 1
            continue
        if c["dernier_appel"] and getdate(c["dernier_appel"]) > limite:
            ecartes["recent"] += 1
            continue
        z = zone_du_client(customer) if (c["secteurs"] or "") in ("", "Hors Secteur") and zones else None
        classe = partenaire_clients.classer(c["secteurs"], False, z)
        if classe == "hors_secteur":
            ecartes["hors_secteur"] += 1
            continue
        if classe == "zone_partenaire":
            c = dict(c, secteurs="Zone partenaire - %s" % z["nom"])
            zone[customer] = c
            continue
        secteurs = [s.strip() for s in (c["secteurs"] or "").split(",") if s.strip()]
        if urgence_active and any(s in secteur_urgence for s in secteurs):
            urgence[customer] = c
        else:
            normal[customer] = c
    return normal, zone, urgence, ecartes


# =============================================================================
# 2) CRÉATION D'UNE LISTE
# =============================================================================

def _adresses(customer):
    out = []
    for a in frappe.get_all("Address", filters={"link_doctype": "Customer", "link_name": customer},
                            fields=["address_line1", "city", "state"]):
        out.append(", ".join(x for x in (a.address_line1, a.city, a.state) if x))
    return "\n".join(out)


def message_info(familles, fams=None):
    """Le texte de la carte : une ligne par famille (libellé du réglage) + le coût de la famille prioritaire (règle commune
    avec le SMS). PURE hors prix."""
    lignes = []
    for fam in sorted(familles, key=lambda f: ([x["code"] for x in (fams or RC.familles())] + [f]).index(f)):
        label = RC.libelle_famille(fam, fams)
        if fam == "ADOUCISSEUR":
            lignes.append(f"{label}: vérification, test de la dureté et contrôle général.")
            lignes.append(PROMO_SELS)
        else:
            lignes.append(f"{label}: changement des filtres et contrôle général.")
    if not lignes:
        lignes.append("Entretien de votre installation de traitement d'eau.")
    _fam, cout = RC.cout_entretien(familles, fams)
    if cout is not None:
        lignes.append(f"Cout main d'oeuvre: {cout} DT")
    return "\n".join(lignes)


def _create_liste_appel_doc(today, clients, type_liste, related_tache=None, cfg=None):
    """Une liste à partir de {customer: {echeanciers, secteurs, premiere, nb_appels}} : UNE ligne par client, triée par nombre
    d'appels puis secteur (numérique) puis ancienneté de l'échéance, plafonnée (réglage) avec le reste annoncé dans le titre."""
    cfg = cfg or RC.config()
    if not clients:
        return {"created": False, "type": type_liste, "count_clients": 0, "name": None}
    fams, cache_items = RC.familles(), {}
    ordre = sorted(clients.items(), key=lambda kv: (kv[1].get("nb_appels", 0), cle_secteur(kv[1]["secteurs"]), str(kv[1]["premiere"])))
    plafond = cfg["plafond_liste"] or len(ordre)
    retenus, en_attente = ordre[:plafond], max(0, len(ordre) - plafond)
    doc = frappe.new_doc(DOCTYPE)
    nb_c_n_e = nb_c_n_r = 0
    for customer, c in retenus:
        client = frappe.db.get_value("Customer", customer, ["custom_liste_telephone", "custom_intéressé_par_le_service_entretien", "custom_envoi_sms"], as_dict=True) or frappe._dict()
        articles, familles = {}, set()
        for ms, items in c["echeanciers"].items():
            for code, d in items.items():
                if code not in articles or d < getdate(articles[code]):
                    articles[code] = d.isoformat()
                if code not in cache_items:
                    cache_items[code] = frappe.db.get_value("Item", code, "item_group")
                f = RC.famille_du_groupe_machine(cache_items[code], fams)
                if f:
                    familles.add(f)
        principal = min(c["echeanciers"], key=lambda ms: min(c["echeanciers"][ms].values()))
        doc.append("clients", {
            "client": customer, "échéancier_dentretien": principal, "telephone": client.custom_liste_telephone,
            "intéressé_par_le_service_dentretien": client.custom_intéressé_par_le_service_entretien,
            "intéressé_par_le_service_de_relance": client.custom_envoi_sms, "adresse": _adresses(customer),
            "info": message_info(familles, fams) + (("\nAutres échéanciers : " + ", ".join(sorted(set(c["echeanciers"]) - {principal}))) if len(c["echeanciers"]) > 1 else ""),
            "detail_articles": json.dumps(articles), "secteur": c["secteurs"]})
        nb_c_n_e += client.custom_intéressé_par_le_service_entretien == "Non"
        nb_c_n_r += client.custom_envoi_sms == "Non"
    doc.type_liste, doc.date = type_liste, today
    doc.nb_appels_restant, doc.nb_r_p, doc.nb_r_c, doc.nb_c_n_e, doc.nb_c_n_r = len(retenus), 0, 0, nb_c_n_e, nb_c_n_r
    if type_liste == TYPE_URGENCE and related_tache:
        doc.liste_maintenance = str(related_tache)[:140]
    doc.titre = ("%s!!! %s" if type_liste == TYPE_URGENCE else "%s %s") % (type_liste, today) + (" (+%d en attente)" % en_attente if en_attente else "")
    doc.flags.ignore_permissions = True
    doc.insert()
    log(f"[CREATE {type_liste}] {doc.name} : {len(retenus)} client(s), {en_attente} en attente")
    return {"created": True, "type": type_liste, "count_clients": len(retenus), "en_attente": en_attente, "name": doc.name}


# =============================================================================
# 3) LISTE « 2e APPEL » (Relance)
# =============================================================================

def _toujours_a_appeler(customer, schedule, today) -> bool:
    """L'échéancier existe toujours, soumis, et porte encore une visite échue non réalisée."""
    if frappe.db.get_value("Maintenance Schedule", schedule, "docstatus") != 1:
        return False
    return bool(frappe.db.exists("Maintenance Schedule Detail", {"parent": schedule, "scheduled_date": ["<", getdate(today)], "actual_date": ["is", "not set"]}))


def _generate_second_call_list(today, rdv_clients, cfg=None):
    """Les « Ne répond pas 1er appel » des listes validées depuis `delai_2e_appel_jours` repartent dans une liste Relance
    (une pour nos secteurs, une pour la zone partenaire). Les lignes sont RECRÉÉES (résumé, réponse, tâche vides) ;
    les sources ne sont marquées traitées QU'APRÈS la création réussie."""
    cfg = cfg or RC.config()
    limite = add_days(getdate(today), -cfg["delai_2e_appel_jours"])
    sources = frappe.get_all(DOCTYPE, filters={"docstatus": 1, "type_liste": ["!=", TYPE_RELANCE], "liste_2iéme_relance": 0, "date_fin": ["<=", limite]},
                             fields=["name", "type_liste"])
    sans_fin = frappe.db.count(DOCTYPE, {"docstatus": 1, "type_liste": ["!=", TYPE_RELANCE], "liste_2iéme_relance": 0, "date_fin": ["is", "not set"]})
    if sans_fin:
        log(f"[2e APPEL] {sans_fin} liste(s) validée(s) sans date de fin : jamais éligibles au 2e appel")
    if not sources:
        return {"created": False, "count_clients": 0}
    exclus = partenaire_clients.clients_geres()
    lots = {"secteurs": {}, "zone": {}}
    traitees = []
    for src in sources:
        try:
            lignes = frappe.get_all("Appelle Client", filters={"parent": src.name, "resume_appel": NE_REPOND_PAS}, fields=["*"], order_by="idx")
            for l in lignes:
                if not l.client or l.client in rdv_clients or l.client in exclus or not l.échéancier_dentretien:
                    continue
                if not _toujours_a_appeler(l.client, l.échéancier_dentretien, today):
                    continue
                lot = lots["zone" if (src.type_liste == TYPE_ZONE or (l.secteur or "").startswith("Zone partenaire")) else "secteurs"]
                lot.setdefault(l.client, l)             # un client une fois, sa première ligne
            traitees.append(src.name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"2e appel — liste source {src.name}")
            RC.alerter("[ERPNext] Listes d’appels : liste source %s en erreur au 2e appel" % src.name,
                       "<p>La liste %s n’a pas pu être relue pour le 2e appel ; elle n’est pas marquée traitée et sera retentée demain. "
                       "Détail dans /app/error-log.</p>" % src.name, cfg)
    resultats = []
    for cle, lot in lots.items():
        if not lot:
            continue
        doc = frappe.new_doc(DOCTYPE)
        for l in lot.values():
            doc.append("clients", {k: l.get(k) for k in ("client", "échéancier_dentretien", "telephone", "intéressé_par_le_service_dentretien",
                                                       "intéressé_par_le_service_de_relance", "info", "secteur", "adresse", "detail_articles")})
        doc.type_liste, doc.date = TYPE_RELANCE, today
        doc.nb_appels_restant, doc.nb_r_p, doc.nb_r_c = len(lot), 0, 0
        doc.titre = f"{TYPE_RELANCE} {today}" + (" zone partenaire" if cle == "zone" else "")
        doc.flags.ignore_permissions = True
        doc.insert()
        resultats.append({"name": doc.name, "count_clients": len(lot)})
        log(f"[2e APPEL] {doc.name} : {len(lot)} client(s) ({cle})")
    # Marquage des sources APRÈS création (et seulement celles lues sans erreur), sans re-valider de vieilles listes.
    for name in traitees:
        frappe.db.set_value(DOCTYPE, name, "liste_2iéme_relance", 1, update_modified=False)
    return {"created": bool(resultats), "listes": resultats, "count_clients": sum(r["count_clients"] for r in resultats), "sources": len(traitees)}


# =============================================================================
# 4) LISTES NORMAL / ZONE PARTENAIRE / URGENCE
# =============================================================================

def _generate_normal_urgence_list(today, rdv_clients, create_normal=True, create_urgence=True, ignore_draft_normal=False, create_zone=True, cfg=None):
    cfg = cfg or RC.config()
    results = {"normal": None, "urgence": None, "zone_partenaire": None}
    urg = get_urgence_context(today, cfg)
    clients = echeances_a_appeler(today, cfg)
    zones = partenaire_clients.zones_partenaires()
    normal, zone, urgence, ecartes = repartir(
        clients, today, cfg, RC.clients_exclus_relance(cfg), zones,
        lambda c: partenaire_clients.zone_partenaire_du_client(c, zones), rdv_clients,
        urg["secteur_urgence"], urgence_active=bool(urg["gener_urgence"] and create_urgence))
    results["ecartes"] = ecartes
    log(f"[REPARTITION] normal={len(normal)} zone={len(zone)} urgence={len(urgence)} écartés={ecartes}")
    if create_urgence and urg["gener_urgence"] and urgence:
        results["urgence"] = _create_liste_appel_doc(today, urgence, TYPE_URGENCE, related_tache=urg["related_tache"], cfg=cfg)
    if create_zone and zone and (ignore_draft_normal or not has_draft_of_type(TYPE_ZONE)):
        results["zone_partenaire"] = _create_liste_appel_doc(today, zone, TYPE_ZONE, cfg=cfg)
    elif zone:
        log(f"[ZONE PARTENAIRE] {len(zone)} client(s) en attente (brouillon existant ou génération non demandée)")
    if create_normal and normal and (ignore_draft_normal or not has_draft_of_type(TYPE_NORMAL)):
        results["normal"] = _create_liste_appel_doc(today, normal, TYPE_NORMAL, cfg=cfg)
    elif normal:
        log(f"[NORMAL] {len(normal)} client(s) en attente (brouillon existant ou génération non demandée)")
    if create_normal and (ignore_draft_normal or not has_draft_of_type(TYPE_REQUALIFICATION)):
        results["requalification"] = _generate_requalification_list(today, cfg)
    return results


TYPE_REQUALIFICATION = "Requalification"


def clients_requalification_a_lister() -> list:
    """Clients « À requalifier » jamais entrés dans une liste Requalification (un seul dernier appel)."""
    if not frappe.db.has_column("Customer", "custom_statut_relance"):
        return []
    return frappe.db.sql_list("""select c.name from tabCustomer c where c.custom_statut_relance = 'À requalifier'
                                 and not exists (select 1 from `tabAppelle Client` a join `tabListe Appelle Entretien` l on l.name = a.parent
                                                 where a.client = c.name and l.type_liste = %s and l.docstatus <> 2)""", (TYPE_REQUALIFICATION,))


def _generate_requalification_list(today, cfg=None):
    """La liste du dernier appel humain : les clients requalifiés, avec leurs visites échues non réalisées."""
    cfg = cfg or RC.config()
    noms = clients_requalification_a_lister()
    if not noms:
        return None
    clients = {}
    for r in frappe.db.sql("""select ms.customer, d.parent, d.item_code, min(d.scheduled_date) premiere,
                                     (select group_concat(distinct a.custom_secteur separator ', ') from `tabDynamic Link` dl join `tabAddress` a on a.name = dl.parent
                                       where dl.parenttype = 'Address' and dl.link_doctype = 'Customer' and dl.link_name = ms.customer) secteurs
                              from `tabMaintenance Schedule Detail` d join `tabMaintenance Schedule` ms on ms.name = d.parent and ms.docstatus = 1
                              where ms.customer in %s and d.actual_date is null and d.scheduled_date < %s group by ms.customer, d.parent, d.item_code""",
                           (tuple(noms), getdate(today)), as_dict=True):
        c = clients.setdefault(r.customer, {"echeanciers": {}, "secteurs": r.secteurs or "", "premiere": None, "nb_appels": 0})
        c["echeanciers"].setdefault(r.parent, {})[r.item_code] = getdate(r.premiere)
        c["premiere"] = getdate(r.premiere) if c["premiere"] is None or getdate(r.premiere) < c["premiere"] else c["premiere"]
    for n in noms:
        clients.setdefault(n, {"echeanciers": {}, "secteurs": "", "premiere": getdate(today), "nb_appels": 0})
    # Un client sans visite échue ne peut pas porter de ligne (échéancier requis) : il reste « À requalifier » sans appel.
    clients = {k: v for k, v in clients.items() if v["echeanciers"]}
    if not clients:
        return None
    r = _create_liste_appel_doc(today, clients, TYPE_REQUALIFICATION, cfg=cfg)
    if r.get("created"):
        frappe.db.set_value(DOCTYPE, r["name"], "titre", "%s %s — dernier appel : %d cycles de relance sans réponse" % (TYPE_REQUALIFICATION, today, cint(cfg["cycles_sans_reponse"])), update_modified=False)
    return r


# =============================================================================
# 5) POINTS D'ENTRÉE
# =============================================================================

def _run_cron_internal():
    cfg, today = RC.config(), nowdate()
    rdv = get_recent_rdv_customers(cfg=cfg)
    results = {}
    results["second_call"] = _generate_second_call_list(today, rdv, cfg) if not has_draft_of_type(TYPE_RELANCE) \
        else {"created": False, "skipped": True, "reason": "draft_relance_exists"}
    results["normal_urgence"] = _generate_normal_urgence_list(today, rdv, create_normal=not has_draft_of_type(TYPE_NORMAL),
                                                              create_urgence=True, ignore_draft_normal=False, create_zone=True, cfg=cfg)
    log(f"[CRON SUMMARY] {results}")
    return results


def _run_manual_internal(list_type=None):
    cfg, today = RC.config(), nowdate()
    rdv = get_recent_rdv_customers(cfg=cfg)
    lt = (list_type or "all").lower()
    results = {}
    if lt in ("all", "relance", "2e", "2e_appel", "second", "second_call"):
        results["second_call"] = _generate_second_call_list(today, rdv, cfg)
    if lt in ("all", "normal", "urgence", "zone", "partenaire", "zone_partenaire"):
        results["normal_urgence"] = _generate_normal_urgence_list(
            today, rdv, create_normal=lt in ("all", "normal"), create_urgence=lt in ("all", "urgence"),
            ignore_draft_normal=True, create_zone=lt in ("all", "zone", "partenaire", "zone_partenaire"), cfg=cfg)
    log(f"[MANUAL SUMMARY] ({lt}) {results}")
    return results


def run_cron():
    return run_safely("Cron - Génération listes d'appels", _run_cron_internal)


@frappe.whitelist(methods=["POST"])
def run_manual(list_type=None):
    """Bouton « Générer listes d'appels » : réservé aux responsables ; une erreur remonte à l'écran (plus de « Succès » à tort)."""
    frappe.only_for(("System Manager", "Sales Manager", "Maintenance Manager"))
    results = _run_manual_internal(list_type)
    return {"results": results, "resume": resume_resultats(results)}


def resume_resultats(results) -> str:
    """Phrase pour l'écran. PURE."""
    morceaux = []
    sc = (results or {}).get("second_call") or {}
    if sc.get("created"):
        morceaux.append("2e appel : %d client(s)" % sc.get("count_clients", 0))
    nu = (results or {}).get("normal_urgence") or {}
    for cle, libelle in (("normal", "Normal"), ("zone_partenaire", "Zone partenaire"), ("urgence", "Urgence"), ("requalification", "Requalification")):
        r = nu.get(cle)
        if r and r.get("created"):
            morceaux.append("%s : %d client(s)%s" % (libelle, r["count_clients"], (" (+%d en attente)" % r["en_attente"]) if r.get("en_attente") else ""))
    return " · ".join(morceaux) if morceaux else _("Aucune liste créée (rien à appeler, ou un brouillon existe déjà).")
