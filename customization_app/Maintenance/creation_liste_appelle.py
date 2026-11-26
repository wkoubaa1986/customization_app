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

Lot du 25/11/2026 (bilan « entretien-cartographie ») :
- la liste Hors secteur LIST-06-11-2026-00004 (100 lignes, 45 appelées, immobile depuis le 11/11) bloquait son type :
  183 clients relancés par SMS jamais appelés. Plafond propre à cette liste (plafond_hors_secteur, 30), alerte e-mail
  quand un brouillon bloquant n'a pas bougé depuis jours_brouillon_alerte (3) jours ouvrés ;
- une liste se valide désormais avec des lignes non appelées (liste_validation) : elles DOIVENT revenir dans la
  prochaine liste de leur type. Normal / Zone / Hors secteur / Urgence : la visite reste sans `custom_appelle`, elle
  est donc toujours candidate, et une ligne non appelée ne compte plus comme un appel (nb_appels). Relance et
  Requalification : reprise explicite (_reprises_relance, clients_requalification_a_lister) ;
- le journal [REPARTITION] rangeait toute exclusion sous « partenaire » (99 clients, 0 du partenaire) : un compteur
  par motif réel ; les compteurs du passage vont à l'entonnoir (entonnoir_relances.noter).

Relecture du 26/11/2026 :
- `plafond_hors_secteur` à 0 (ce que Frappe écrit dans un champ Int neuf au premier enregistrement du réglage)
  reprend `plafond_liste` au lieu de « sans plafond » ;
- une liste Urgence validée avec des lignes non appelées ne couvre plus son rendez-vous (listes_qui_couvrent) : une
  Urgence est recréée pour ce passage du technicien, au lieu d'envoyer les clients laissés dans la liste Normal ;
- « en attente » d'un type bloqué = les clients DERRIÈRE le brouillon (sans ceux qui y sont déjà) : le même chiffre
  que l'alerte du brouillon (129, pas 183, le 25/11).
"""
from __future__ import annotations

import datetime
import html
import json
import re

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, nowdate

from customization_app import partenaire_clients, relances_config as RC
# Lot 5.1 (26/11/2026) : une visite échue « couverte » par un appel plus récent sur le même article ne ramène plus le client
# (marquer toutes les vieilles visites aurait gonflé la lassitude de 64 clients — voir liste_appels).
from customization_app.liste_appels import SQL_VISITE_NON_COUVERTE
from customization_app.utils.run_safely import run_safely

DOCTYPE = "Liste Appelle Entretien"
TYPE_NORMAL, TYPE_RELANCE, TYPE_URGENCE = "Normal", "Relance", "Urgence"
TYPE_ZONE = partenaire_clients.TYPE_LISTE_ZONE
# 15/10/2026 : les clients hors secteur (Gafsa, Sfax, Sousse sans partenaire…) recevaient leurs SMS puis n'étaient jamais
# appelés (217 en attente, pour toujours). Ils ont leur liste, dernière priorité : on l'appelle quand tout le reste est fait.
TYPE_HORS_SECTEUR = "Hors secteur"
TYPE_REQUALIFICATION = "Requalification"
NE_REPOND_PAS = "Ne répond pas 1er appel"
PROMO_SELS = "Promo sac sels: pour l'achat de 5 sacs, le prix du sac est à 28 DT au lieu de 35 DT."

# 25/11/2026 : une liste Hors secteur de 100 lignes ne se termine pas (LIST-06-11-2026-00004 : 45 appelées en 5 jours,
# plus rien depuis le 11/11) et bloque son type. Plafond propre, réglable (plafond_hors_secteur ; 0 = sans plafond).
PLAFOND_HORS_SECTEUR = 30
# Alerte quand un brouillon qui bloque son type n'a pas bougé depuis N jours ouvrés (lundi–samedi ; 0 = pas d'alerte).
JOURS_BROUILLON_ALERTE = 3
TYPES_BLOQUES_PAR_BROUILLON = (TYPE_RELANCE, TYPE_ZONE, TYPE_NORMAL, TYPE_REQUALIFICATION, TYPE_HORS_SECTEUR)
ALERTE_BROUILLON = "⏰ Brouillon immobile"
# Motifs d'exclusion de la relance (RC.clients_exclus_relance), dans l'ordre où un client est compté.
MOTIFS_EXCLUSION = ("partenaire", "perdu", "a_requalifier", "non_interesse", "reporte")


def log(msg):
    RC.journal("creation_liste_appelle").info(msg)


def has_draft_of_type(list_type: str) -> bool:
    return bool(frappe.db.exists(DOCTYPE, {"docstatus": 0, "type_liste": list_type}))


# Un brouillon que personne n'a touché depuis sa création bloque pour toujours les listes suivantes de son type : la liste
# « Zone partenaire » du 05/10/2026 (100 clients, aucun appelé) a laissé 108 clients « en attente » chaque matin pendant dix
# jours. Passé ce délai, un brouillon intact est remplacé par une liste à jour ; un brouillon entamé n'est jamais touché.
JOURS_BROUILLON_INTACT = 7


def brouillon_abandonne(creation, modified, lignes_touchees: int, today, jours=JOURS_BROUILLON_INTACT) -> bool:
    """Jamais modifié depuis sa création, aucune ligne appelée/renseignée, et plus vieux que `jours`. PURE."""
    if lignes_touchees or getdate(modified) != getdate(creation):
        return False
    return getdate(creation) <= add_days(getdate(today), -jours)


def purger_brouillons_abandonnes(today, jours=JOURS_BROUILLON_INTACT) -> list:
    """Supprime les brouillons abandonnés (tous types) pour que le cron en recrée un à jour. → noms supprimés."""
    supprimes = []
    for l in frappe.get_all(DOCTYPE, filters={"docstatus": 0}, fields=["name", "type_liste", "creation", "modified"]):
        touchees = frappe.db.count("Appelle Client", {"parent": l.name, "parenttype": DOCTYPE, "a_été_appelé": 1}) \
            + frappe.db.count("Appelle Client", {"parent": l.name, "parenttype": DOCTYPE, "resume_appel": ["!=", ""]})
        if not brouillon_abandonne(l.creation, l.modified, touchees, today, jours):
            continue
        try:
            frappe.delete_doc(DOCTYPE, l.name, ignore_permissions=True, force=True)
            supprimes.append(l.name)
            log(f"[BROUILLON] {l.name} ({l.type_liste}) du {getdate(l.creation)} jamais touché : supprimé, une liste à jour prend sa place")
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Brouillon abandonné {l.name}")
    return supprimes


def get_recent_rdv_customers(days_back=None, cfg=None):
    """Clients avec un rendez-vous récent ou à venir : même règle que la relance SMS (relances_config)."""
    return RC.clients_avec_rdv(cfg, jours=days_back)


# =============================================================================
# 0) URGENCE (secteurs lointains du réglage)
# =============================================================================

def listes_qui_couvrent(listes_urgence, non_appelees) -> list:
    """Les listes Urgence qui couvrent encore leurs rendez-vous : un brouillon (l'appel est en cours) ou une liste
    validée ENTIÈREMENT appelée. `non_appelees` = {liste: lignes non appelées}. PURE.
    26/11/2026 (relecture) : une liste Urgence validée avec des lignes non appelées (liste_validation) ne couvre plus
    son rendez-vous. Sinon le passage du technicien restait « couvert », aucune nouvelle Urgence n'était créée, et
    les clients laissés partaient dans la prochaine liste Normal, après le passage du technicien. Désormais une
    Urgence est recréée dès le passage de 07:00 suivant, tant que le rendez-vous est à venir. Jusqu'au 25/11 toute
    liste validée était entièrement appelée : rien ne change pour l'existant (aucune liste validée n'a de ligne non
    appelée sur la copie du 25/11/2026)."""
    return [l for l in listes_urgence or []
            if cint(l.get("docstatus")) == 0 or not cint((non_appelees or {}).get(l.get("name")))]


def _non_appelees_par_liste(noms) -> dict:
    """{liste: lignes non appelées} pour les listes données (une requête)."""
    noms = tuple(n for n in noms or () if n)
    if not noms:
        return {}
    return dict(frappe.db.sql("""select parent, count(*) from `tabAppelle Client`
                                 where parenttype = %s and parent in %s and ifnull(a_été_appelé, 0) = 0 group by parent""",
                              (DOCTYPE, noms)))


def _taches_couvertes(listes_urgence) -> set:
    """{(date, secteur)} des rendez-vous déjà portés par une liste Urgence NON annulée qui les couvre encore
    (listes_qui_couvrent : brouillon, ou validée entièrement appelée)."""
    listes_urgence = list(listes_urgence or [])
    validees = [l.get("name") for l in listes_urgence if cint(l.get("docstatus")) == 1]
    noms = set()
    for l in listes_qui_couvrent(listes_urgence, _non_appelees_par_liste(validees)):
        for n in re.split(r"[\[\]',\s]+", l.get("liste_maintenance") or ""):
            if n:
                noms.add(n)
    if not noms:
        return set()
    return {(str(getdate(d)), s or "") for d, s in frappe.db.sql(
        "select starts_on, secteur from `tabTache de travail` where name in %s and status <> 'Cancelled'", (tuple(noms),)) if d}


def get_urgence_context(today, cfg=None):
    """{gener_urgence, secteur_urgence: [...], related_tache: [...]} : un rendez-vous planifié dans un secteur lointain,
    pas encore couvert par une liste Urgence (même DATE et même SECTEUR ; _taches_couvertes)."""
    cfg = cfg or RC.config()
    secteurs = cfg["secteurs_urgence_liste"]
    if not secteurs:
        return {"gener_urgence": False, "secteur_urgence": [], "related_tache": []}
    listes = frappe.get_all(DOCTYPE, filters={"type_liste": TYPE_URGENCE, "docstatus": ["!=", 2]}, fields=["name", "liste_maintenance", "docstatus"])
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


def regrouper_par_client(lignes, dernieres_interventions, cfg, today, stats=None):
    """`lignes` : [{customer, schedule, item_code, scheduled_date, secteurs}] déjà filtrées sur SMS1+SMS2 envoyés, non réalisées,
    non appelées. On retire les articles dont la dernière intervention est trop récente, puis on regroupe par CLIENT :
    {customer: {"echeanciers": {ms: {item: date}}, "secteurs": str, "premiere": date}}. PURE.
    `stats` (dict, facultatif, 25/11/2026) reçoit l'entonnoir : visites, clients, clients écartés en entier par la règle
    des `jours_apres_intervention` (ils n'apparaissaient dans aucun compteur du journal)."""
    limite = add_days(getdate(today), -cfg["jours_apres_intervention"])
    out = {}
    vus = set()
    for l in lignes:
        vus.add(l["customer"])
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
    if stats is not None:
        stats["visites"] = len(lignes)
        stats["clients"] = len(vus)
        stats["intervention_recente"] = len(vus - set(out))
        stats["a_repartir"] = len(out)
    return out


def echeances_a_appeler(today, cfg=None, stats=None):
    """Les clients à appeler : SMS1 et SMS2 envoyés (SMS2 depuis `delai_apres_sms2_jours`), visite non réalisée et non
    encore appelée, pas d'intervention récente. Quatre requêtes, quel que soit le nombre d'échéanciers.
    `stats` : voir regrouper_par_client."""
    cfg = cfg or RC.config()
    today = getdate(today)
    if stats is not None:
        stats.update({"visites": 0, "clients": 0, "intervention_recente": 0, "a_repartir": 0})
    lignes = frappe.db.sql("""
        SELECT ms.customer, d.parent AS schedule, d.item_code, d.scheduled_date,
               (SELECT GROUP_CONCAT(DISTINCT a.custom_secteur SEPARATOR ', ') FROM `tabDynamic Link` dl
                  JOIN `tabAddress` a ON a.name = dl.parent
                 WHERE dl.parenttype = 'Address' AND dl.link_doctype = 'Customer' AND dl.link_name = ms.customer) AS secteurs
        FROM `tabMaintenance Schedule Detail` d
        JOIN `tabMaintenance Schedule` ms ON ms.name = d.parent AND ms.docstatus = 1 AND ms.status = 'Submitted'
        WHERE d.scheduled_date < %(today)s AND d.custom_sms_1 IS NOT NULL AND d.custom_sms_2 IS NOT NULL
          AND d.custom_sms_2 <= %(sms2_max)s AND d.actual_date IS NULL AND d.custom_appelle IS NULL""" + SQL_VISITE_NON_COUVERTE,
        {"today": today, "sms2_max": add_days(today, -cfg["delai_apres_sms2_jours"])}, as_dict=True)
    if not lignes:
        return {}
    schedules = tuple({l.schedule for l in lignes})
    dernieres = {(s, i): d for s, i, d in frappe.db.sql(
        "select parent, item_code, max(actual_date) from `tabMaintenance Schedule Detail` where parent in %s and actual_date is not null group by parent, item_code",
        (schedules,))}
    clients = regrouper_par_client(lignes, dernieres, cfg, today, stats)
    if not clients:
        return {}
    noms = tuple(clients)
    # Nombre d'appels déjà passés (listes validées, hors Relance) et date du dernier appel, PAR CLIENT.
    # 25/11/2026 : seules les lignes APPELÉES comptent. Une liste validée avec des lignes non appelées (liste_validation)
    # aurait sinon fait passer ces clients derrière les autres au tri, donc au-delà du plafond (30 en Hors secteur).
    # Jusqu'ici toute ligne validée était appelée (validation refusée sinon) : même résultat pour l'existant.
    for c, n in frappe.db.sql("""select a.client, count(distinct l.name) from `tabAppelle Client` a join `tabListe Appelle Entretien` l on l.name = a.parent
                                 where l.docstatus = 1 and a.a_été_appelé = 1 and ifnull(l.type_liste, 'Normal') <> 'Relance' and a.client in %s
                                 group by a.client""", (noms,)):
        clients[c]["nb_appels"] = n
    for c, d in frappe.db.sql("""select ms.customer, max(d.custom_appelle) from `tabMaintenance Schedule Detail` d
                                 join `tabMaintenance Schedule` ms on ms.name = d.parent where ms.customer in %s and d.custom_appelle is not null group by ms.customer""", (noms,)):
        clients[c]["dernier_appel"] = d
    # 02/11/2026 : la marque `custom_appelle` manquait sur plus de la moitié des appels (date de visite non retrouvée),
    # et la règle des `jours_entre_deux_appels` ne voyait rien : des clients « Fait lui-même » rappelés trois fois en un
    # mois. Les listes validées font foi aussi : le dernier appel = le plus récent des deux.
    for c, d in frappe.db.sql("""select a.client, max(l.date) from `tabAppelle Client` a join `tabListe Appelle Entretien` l on l.name = a.parent
                                 where l.docstatus = 1 and a.a_été_appelé = 1 and a.client in %s group by a.client""", (noms,)):
        if d and (not clients[c].get("dernier_appel") or getdate(d) > getdate(clients[c]["dernier_appel"])):
            clients[c]["dernier_appel"] = d
    for c in clients.values():
        c.setdefault("nb_appels", 0)
        c.setdefault("dernier_appel", None)
    return clients


def motif_exclusion(client, geres, today, relancer_non=False) -> str:
    """Pourquoi un client exclu de la relance l'est : la règle de RC.clients_exclus_relance, motif par motif, dans
    l'ordre de MOTIFS_EXCLUSION (un client compté une fois). `client` : {name, custom_statut_relance,
    custom_intéressé_par_le_service_entretien, custom_relance_reportee_au}. « autre » = la règle commune a changé sans
    ce compteur. PURE."""
    if client.get("name") in (geres or ()):
        return "partenaire"
    statut = client.get("custom_statut_relance") or ""
    if statut == "Perdu":
        return "perdu"
    if statut == "À requalifier":
        return "a_requalifier"
    if not relancer_non and client.get("custom_intéressé_par_le_service_entretien") == "Non":
        return "non_interesse"
    reporte = client.get("custom_relance_reportee_au")
    if reporte and getdate(reporte) > getdate(today):
        return "reporte"
    return "autre"


def motifs_des_exclus(noms, cfg, today) -> dict:
    """{client: motif} pour les candidats exclus (une requête sur eux seuls). Sert au journal, pas à la décision : la
    décision reste RC.clients_exclus_relance, la règle commune aux SMS et aux listes."""
    noms = sorted(n for n in (noms or ()) if n)
    if not noms:
        return {}
    champs = ["name", "custom_intéressé_par_le_service_entretien"]
    champs += [c for c in ("custom_statut_relance", "custom_relance_reportee_au") if frappe.db.has_column("Customer", c)]
    geres = partenaire_clients.clients_geres()
    relancer_non = bool(cint(cfg.get("relancer_non_interesses")))
    return {r.name: motif_exclusion(r, geres, today, relancer_non)
            for r in frappe.get_all("Customer", filters={"name": ["in", noms]}, fields=champs)}


def repartir(clients, today, cfg, exclus, zones, zone_du_client, rdv_clients, secteur_urgence, urgence_active, motifs=None):
    """Range chaque client dans normal / zone / urgence / hors secteur (ou l'écarte) : exclu de la relance, rendez-vous
    récent, appelé il y a moins de `jours_entre_deux_appels`. Les vrais hors secteur ne sont plus écartés : ils forment
    la liste « Hors secteur », dernière priorité (le compteur `ecartes["hors_secteur"]` reste pour le journal).
    25/11/2026 : un compteur par motif d'exclusion (`motifs` = {client: motif}, cf. motifs_des_exclus) ; avant, tout
    allait dans « partenaire » (99 clients le 25/11, aucun du partenaire). Sans motif connu : « autre ».
    PURE (zone_du_client injectable). → (normal, zone, urgence, ecartes, hors)."""
    limite = add_days(getdate(today), -cfg["jours_entre_deux_appels"])
    normal, zone, urgence, hors = {}, {}, {}, {}
    ecartes = dict.fromkeys(MOTIFS_EXCLUSION, 0)
    ecartes.update({"rdv": 0, "recent": 0, "hors_secteur": 0})
    for customer, c in clients.items():
        if customer in exclus:
            motif = (motifs or {}).get(customer) or "autre"
            ecartes[motif] = ecartes.get(motif, 0) + 1
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
            hors[customer] = dict(c, secteurs="Hors Secteur")
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
    return normal, zone, urgence, ecartes, hors


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


def plafond_du_type(type_liste, cfg) -> int:
    """Nombre maximal de lignes d'une liste (0 = sans plafond) : `plafond_hors_secteur` pour la liste Hors secteur
    (25/11/2026, 30 par défaut : une liste de 100 lignes ne se terminait pas et bloquait son type), `plafond_liste`
    pour les autres. PURE.
    26/11/2026 (relecture) : `plafond_hors_secteur` à 0 ou vide reprend `plafond_liste`, il ne veut PAS dire « sans
    plafond ». Un champ Int ajouté à un Single déjà enregistré vaut 0 au premier enregistrement du réglage (Frappe
    n'applique le défaut d'un champ qu'à un Single neuf) : avec « 0 = sans plafond », la liste Hors secteur aurait
    pris d'un coup les 183 candidats du 25/11, pire que les 100 d'avant."""
    if type_liste == TYPE_HORS_SECTEUR:
        propre = cint(cfg.get("plafond_hors_secteur", PLAFOND_HORS_SECTEUR))
        if propre > 0:
            return propre
    return max(0, cint(cfg.get("plafond_liste")))


def _create_liste_appel_doc(today, clients, type_liste, related_tache=None, cfg=None):
    """Une liste à partir de {customer: {echeanciers, secteurs, premiere, nb_appels}} : UNE ligne par client, triée par nombre
    d'appels puis secteur (numérique) puis ancienneté de l'échéance, plafonnée (réglage) avec le reste annoncé dans le titre."""
    cfg = cfg or RC.config()
    if not clients:
        return {"created": False, "type": type_liste, "count_clients": 0, "name": None}
    fams, cache_items = RC.familles(), {}
    ordre = sorted(clients.items(), key=lambda kv: (kv[1].get("nb_appels", 0), cle_secteur(kv[1]["secteurs"]), str(kv[1]["premiere"])))
    plafond = plafond_du_type(type_liste, cfg) or len(ordre)
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


def _reprises_relance() -> list:
    """Les listes Relance VALIDÉES qui ont des lignes non appelées pas encore reprises (25/11/2026). Une ligne non
    appelée d'une liste Relance ne revient jamais d'elle-même : sa visite est déjà marquée appelée par le 1er appel, et
    ses sources sont déjà marquées traitées. Elle repart donc dans la liste Relance suivante, sans délai (elle n'a pas
    été appelée) ; la liste est marquée `liste_2iéme_relance` une fois reprise, comme une source de 2e appel.
    Sur la copie du 25/11/2026, aucune liste validée n'a de ligne non appelée : rien ne revient d'un coup."""
    return frappe.db.sql_list("""select distinct l.name from `tabListe Appelle Entretien` l
                                 join `tabAppelle Client` a on a.parent = l.name and a.parenttype = %s
                                 where l.docstatus = 1 and l.type_liste = %s and ifnull(l.liste_2iéme_relance, 0) = 0
                                   and ifnull(a.a_été_appelé, 0) = 0 order by l.name""", (DOCTYPE, TYPE_RELANCE))


def _generate_second_call_list(today, rdv_clients, cfg=None):
    """Les « Ne répond pas 1er appel » des listes validées depuis `delai_2e_appel_jours` repartent dans une liste Relance
    (une pour nos secteurs, une pour la zone partenaire). Les lignes sont RECRÉÉES (résumé, réponse, tâche vides) ;
    les sources ne sont marquées traitées QU'APRÈS la création réussie. 25/11/2026 : seules les lignes COCHÉES
    « a été appelé » comptent comme 1er appel (une ligne non cochée revient déjà dans la liste suivante de son type),
    et les lignes non appelées des listes Relance validées sont reprises (_reprises_relance)."""
    cfg = cfg or RC.config()
    limite = add_days(getdate(today), -cfg["delai_2e_appel_jours"])
    hors_2e = [TYPE_RELANCE, TYPE_HORS_SECTEUR]     # Hors secteur : un seul appel, pas de 2e (dernière priorité)
    sources = frappe.get_all(DOCTYPE, filters={"docstatus": 1, "type_liste": ["not in", hors_2e], "liste_2iéme_relance": 0, "date_fin": ["<=", limite]},
                             fields=["name", "type_liste"])
    sans_fin = frappe.db.count(DOCTYPE, {"docstatus": 1, "type_liste": ["not in", hors_2e], "liste_2iéme_relance": 0, "date_fin": ["is", "not set"]})
    if sans_fin:
        log(f"[2e APPEL] {sans_fin} liste(s) validée(s) sans date de fin : jamais éligibles au 2e appel")
    reprises = _reprises_relance()
    if not sources and not reprises:
        return {"created": False, "count_clients": 0}
    exclus = clients_exclus_du_2e_appel(cfg)
    lots = {"secteurs": {}, "zone": {}}
    traitees = []
    a_lire = [(src.name, src.type_liste, {"resume_appel": NE_REPOND_PAS, "a_été_appelé": 1}) for src in sources]
    a_lire += [(nom, TYPE_RELANCE, {"a_été_appelé": 0}) for nom in reprises]
    for nom_src, type_src, filtre in a_lire:
        try:
            lignes = frappe.get_all("Appelle Client", filters=dict(filtre, parent=nom_src, parenttype=DOCTYPE), fields=["*"], order_by="idx")
            for l in lignes:
                if not l.client or l.client in rdv_clients or l.client in exclus or not l.échéancier_dentretien:
                    continue
                if not _toujours_a_appeler(l.client, l.échéancier_dentretien, today):
                    continue
                lot = lots["zone" if (type_src == TYPE_ZONE or (l.secteur or "").startswith("Zone partenaire")) else "secteurs"]
                lot.setdefault(l.client, l)             # un client une fois, sa première ligne
            traitees.append(nom_src)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"2e appel — liste source {nom_src}")
            RC.alerter("[ERPNext] Listes d’appels : liste source %s en erreur au 2e appel" % nom_src,
                       "<p>La liste %s n’a pas pu être relue pour le 2e appel ; elle n’est pas marquée traitée et sera retentée demain. "
                       "Détail dans /app/error-log.</p>" % nom_src, cfg)
    resultats = []
    for cle, lot in lots.items():
        if not lot:
            continue
        doc = frappe.new_doc(DOCTYPE)
        cases = cases_actuelles_des_clients(list(lot))
        for l in lot.values():
            ligne = {k: l.get(k) for k in ("client", "échéancier_dentretien", "telephone", "intéressé_par_le_service_dentretien",
                                           "intéressé_par_le_service_de_relance", "info", "secteur", "adresse", "detail_articles")}
            # Les deux cases suivent la fiche client d'AUJOURD'HUI, pas la ligne du 1er appel : un client passé Non entre les
            # deux appels redevenait « Oui » si l'opératrice enregistrait la ligne sans y toucher (26/11/2026).
            ligne.update({k: v for k, v in cases.get(l.client, {}).items() if v})
            doc.append("clients", ligne)
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
    if reprises:
        log(f"[2e APPEL] {len(reprises)} liste(s) Relance validée(s) avec des lignes non appelées reprises : {', '.join(reprises)}")
    return {"created": bool(resultats), "listes": resultats, "count_clients": sum(r["count_clients"] for r in resultats),
            "sources": len(traitees), "reprises": len([n for n in reprises if n in traitees])}


def clients_exclus_du_2e_appel(cfg) -> set:
    """Les clients que le 2e appel ne reprend pas : ceux du partenaire, et — depuis que les clients « Non » gardent leurs
    échéanciers (26/11/2026) — les « Non » à l'entretien (sauf réglage `relancer_non_interesses`) et les « Perdu ».
    PAS RC.clients_exclus_relance : il écarte aussi « À requalifier », ce qui viderait le 2e appel des listes
    Requalification."""
    exclus = set(partenaire_clients.clients_geres())
    if not cint(cfg.get("relancer_non_interesses")):
        exclus |= set(frappe.get_all("Customer", filters={"custom_intéressé_par_le_service_entretien": "Non"}, pluck="name"))
    if frappe.db.has_column("Customer", "custom_statut_relance"):
        exclus |= set(frappe.get_all("Customer", filters={"custom_statut_relance": "Perdu"}, pluck="name"))
    return exclus


def cases_actuelles_des_clients(noms) -> dict:
    """{client: {intéressé_par_le_service_dentretien, intéressé_par_le_service_de_relance}} lus sur la fiche client."""
    if not noms:
        return {}
    out = {}
    for r in frappe.get_all("Customer", filters={"name": ["in", list(noms)]},
                            fields=["name", "custom_intéressé_par_le_service_entretien", "custom_envoi_sms"]):
        out[r.name] = {"intéressé_par_le_service_dentretien": r.get("custom_intéressé_par_le_service_entretien"),
                       "intéressé_par_le_service_de_relance": r.get("custom_envoi_sms")}
    return out


# =============================================================================
# 4) LISTES NORMAL / ZONE PARTENAIRE / URGENCE
# =============================================================================

def clients_en_brouillon(type_liste) -> set:
    """Les clients déjà portés par un brouillon de ce type (une requête)."""
    return set(frappe.db.sql_list("""select distinct a.client from `tabAppelle Client` a
                                     join `tabListe Appelle Entretien` l on l.name = a.parent and a.parenttype = %s
                                     where l.docstatus = 0 and l.type_liste = %s and a.client is not null""", (DOCTYPE, type_liste)))


def separer_bloques(candidats, deja) -> tuple:
    """(en attente derrière le brouillon, déjà dans le brouillon) : les candidats d'un type bloqué, coupés en deux.
    Le 25/11/2026 : 183 candidats Hors secteur, dont 54 encore dans LIST-06-11-2026-00004 (leurs lignes non appelées
    n'ont pas de `custom_appelle`, ils restent donc candidats) : 129 attendent derrière. PURE."""
    candidats = set(candidats or ())
    dedans = candidats & set(deja or ())
    return len(candidats - dedans), len(dedans)


def _noter_bloque(bloques, bloques_clients, deja_en_brouillon, type_liste, candidats):
    """Un type dont la création est bloquée par un brouillon : `bloques` = clients qui attendent DERRIÈRE (relecture du
    26/11/2026 : on y comptait aussi ceux déjà dans le brouillon, 183 au lieu des 129 de l'alerte), `deja_en_brouillon`
    = ceux qui y sont, `bloques_clients` = tous les candidats (l'alerte refait le calcul par brouillon)."""
    derriere, dedans = separer_bloques(candidats, clients_en_brouillon(type_liste))
    bloques[type_liste], deja_en_brouillon[type_liste], bloques_clients[type_liste] = derriere, dedans, set(candidats)
    return derriere, dedans


def _generate_normal_urgence_list(today, rdv_clients, create_normal=True, create_urgence=True, ignore_draft_normal=False, create_zone=True, cfg=None,
                                  create_hors=True):
    cfg = cfg or RC.config()
    results = {"normal": None, "urgence": None, "zone_partenaire": None, "hors_secteur": None}
    # 25/11/2026 : `bloques` = {type: clients qui attendent derrière le brouillon} quand un brouillon empêche la création,
    # `deja_en_brouillon` = {type: candidats déjà dans ce brouillon} (26/11/2026) ; `bloques_clients` = tous les
    # candidats en ensembles, pour l'alerte du brouillon (retiré du résultat avant le journal par _run_cron_internal).
    bloques, bloques_clients, deja_en_brouillon = {}, {}, {}
    results["bloques"], results["bloques_clients"], results["deja_en_brouillon"] = bloques, bloques_clients, deja_en_brouillon
    urg = get_urgence_context(today, cfg)
    entonnoir = results["entonnoir"] = {}
    clients = echeances_a_appeler(today, cfg, stats=entonnoir)
    zones = partenaire_clients.zones_partenaires()
    exclus = RC.clients_exclus_relance(cfg)
    motifs = motifs_des_exclus(set(clients) & set(exclus), cfg, today)
    normal, zone, urgence, ecartes, hors = repartir(
        clients, today, cfg, exclus, zones,
        lambda c: partenaire_clients.zone_partenaire_du_client(c, zones), rdv_clients,
        urg["secteur_urgence"], urgence_active=bool(urg["gener_urgence"] and create_urgence), motifs=motifs)
    results["ecartes"] = ecartes
    results["repartition"] = {"normal": len(normal), "zone_partenaire": len(zone), "urgence": len(urgence), "hors_secteur": len(hors)}
    log(f"[REPARTITION] normal={len(normal)} zone={len(zone)} urgence={len(urgence)} hors_secteur={len(hors)} écartés={ecartes} "
        f"(candidats : {entonnoir.get('visites', 0)} visite(s), {entonnoir.get('clients', 0)} client(s), "
        f"{entonnoir.get('intervention_recente', 0)} écarté(s) pour une intervention de moins de {cfg['jours_apres_intervention']} j)")
    if create_urgence and urg["gener_urgence"] and urgence:
        results["urgence"] = _create_liste_appel_doc(today, urgence, TYPE_URGENCE, related_tache=urg["related_tache"], cfg=cfg)
    if create_zone and zone and (ignore_draft_normal or not has_draft_of_type(TYPE_ZONE)):
        results["zone_partenaire"] = _create_liste_appel_doc(today, zone, TYPE_ZONE, cfg=cfg)
    elif zone:
        precision = ""
        if create_zone:
            derriere, dedans = _noter_bloque(bloques, bloques_clients, deja_en_brouillon, TYPE_ZONE, zone)
            precision = f" : {derriere} derrière le brouillon, {dedans} déjà dedans"
        log(f"[ZONE PARTENAIRE] {len(zone)} client(s) en attente (brouillon existant ou génération non demandée){precision}")
    rappels = RC.clients_reportes_echus(today, statut="") - set(normal) - set(zone) - set(urgence)
    if rappels:
        normal.update(_clients_depuis_visites_echues(rappels, today))
        log(f"[RAPPEL] {len(rappels)} client(s) « rappeler plus tard » à échéance repris dans la liste Normal")
    results["rappels"] = len(rappels)
    if create_normal and normal and (ignore_draft_normal or not has_draft_of_type(TYPE_NORMAL)):
        results["normal"] = _create_liste_appel_doc(today, normal, TYPE_NORMAL, cfg=cfg)
        if results["normal"].get("created"):
            _solder_rappels(rappels, today)
    elif normal:
        precision = ""
        if not ignore_draft_normal and has_draft_of_type(TYPE_NORMAL):
            derriere, dedans = _noter_bloque(bloques, bloques_clients, deja_en_brouillon, TYPE_NORMAL, normal)
            precision = f" : {derriere} derrière le brouillon, {dedans} déjà dedans"
        log(f"[NORMAL] {len(normal)} client(s) en attente (brouillon existant ou génération non demandée){precision}")
    if create_normal and (ignore_draft_normal or not has_draft_of_type(TYPE_REQUALIFICATION)):
        results["requalification"] = _generate_requalification_list(today, cfg)
    if create_hors and hors and (ignore_draft_normal or not has_draft_of_type(TYPE_HORS_SECTEUR)):
        results["hors_secteur"] = _create_liste_appel_doc(today, hors, TYPE_HORS_SECTEUR, cfg=cfg)
    elif hors:
        precision = ""
        if create_hors:
            derriere, dedans = _noter_bloque(bloques, bloques_clients, deja_en_brouillon, TYPE_HORS_SECTEUR, hors)
            precision = f" : {derriere} derrière le brouillon, {dedans} déjà dedans"
        log(f"[HORS SECTEUR] {len(hors)} client(s) en attente (brouillon existant ou génération non demandée){precision}")
    return results


def clients_requalification_a_lister(today=None) -> list:
    """Clients « À requalifier » jamais entrés dans une liste Requalification (un seul dernier appel), plus ceux dont le
    « Rappeler plus tard » arrive à échéance (15/10/2026 : ils reviennent pour un nouvel appel à la date demandée).
    25/11/2026 : une ligne laissée NON APPELÉE dans une liste validée ne compte plus comme ce dernier appel — le client
    revient dans la prochaine liste Requalification (un brouillon, lui, le retient toujours : l'appel est en cours).
    Sur la copie du 25/11/2026 aucune liste validée n'a de ligne non appelée : rien ne revient d'un coup."""
    if not frappe.db.has_column("Customer", "custom_statut_relance"):
        return []
    jamais = frappe.db.sql_list("""select c.name from tabCustomer c where c.custom_statut_relance = 'À requalifier'
                                   and not exists (select 1 from `tabAppelle Client` a join `tabListe Appelle Entretien` l on l.name = a.parent
                                                   where a.client = c.name and l.type_liste = %s
                                                     and (l.docstatus = 0 or (l.docstatus = 1 and a.a_été_appelé = 1)))""", (TYPE_REQUALIFICATION,))
    noms = set(jamais) | RC.clients_reportes_echus(today or nowdate(), statut="À requalifier")
    # 26/11/2026 : un « Non » à l'entretien n'est plus requalifié par la nuit, mais 64 « Non » portent déjà ce statut.
    if not cint(RC.config().get("relancer_non_interesses")):
        noms -= set(frappe.get_all("Customer", filters={"custom_intéressé_par_le_service_entretien": "Non"}, pluck="name"))
    return sorted(noms)


def _clients_depuis_visites_echues(noms, today) -> dict:
    """{client: {echeanciers, secteurs, premiere, nb_appels}} depuis les visites échues non réalisées — la forme attendue
    par _create_liste_appel_doc. Un client sans visite échue n'y figure pas (une ligne exige un échéancier)."""
    noms = tuple(noms)
    if not noms:
        return {}
    clients = {}
    for r in frappe.db.sql("""select ms.customer, d.parent, d.item_code, min(d.scheduled_date) premiere,
                                     (select group_concat(distinct a.custom_secteur separator ', ') from `tabDynamic Link` dl join `tabAddress` a on a.name = dl.parent
                                       where dl.parenttype = 'Address' and dl.link_doctype = 'Customer' and dl.link_name = ms.customer) secteurs
                              from `tabMaintenance Schedule Detail` d join `tabMaintenance Schedule` ms on ms.name = d.parent and ms.docstatus = 1
                              where ms.customer in %s and d.actual_date is null and d.scheduled_date < %s group by ms.customer, d.parent, d.item_code""",
                           (noms, getdate(today)), as_dict=True):
        c = clients.setdefault(r.customer, {"echeanciers": {}, "secteurs": r.secteurs or "", "premiere": None, "nb_appels": 0})
        c["echeanciers"].setdefault(r.parent, {})[r.item_code] = getdate(r.premiere)
        c["premiere"] = getdate(r.premiere) if c["premiere"] is None or getdate(r.premiere) < c["premiere"] else c["premiere"]
    return clients


def _solder_rappels(noms, today):
    """La date de rappel est consommée dès que le client est dans une liste."""
    noms = tuple(noms)
    if noms and frappe.db.has_column("Customer", "custom_relance_reportee_au"):
        frappe.db.sql("update tabCustomer set custom_relance_reportee_au = null where name in %s and custom_relance_reportee_au <= %s",
                      (noms, getdate(today)))


def _generate_requalification_list(today, cfg=None):
    """La liste du dernier appel humain : les clients requalifiés, avec leurs visites échues non réalisées."""
    cfg = cfg or RC.config()
    noms = clients_requalification_a_lister(today)
    if not noms:
        return None
    clients = _clients_depuis_visites_echues(noms, today)
    for n in noms:
        clients.setdefault(n, {"echeanciers": {}, "secteurs": "", "premiere": getdate(today), "nb_appels": 0})
    # Un client sans visite échue ne peut pas porter de ligne (échéancier requis) : il reste « À requalifier » sans appel.
    clients = {k: v for k, v in clients.items() if v["echeanciers"]}
    if not clients:
        return None
    r = _create_liste_appel_doc(today, clients, TYPE_REQUALIFICATION, cfg=cfg)
    if r.get("created"):
        _solder_rappels(clients, today)
        frappe.db.set_value(DOCTYPE, r["name"], "titre", "%s %s — dernier appel : %d cycles de relance sans réponse" % (TYPE_REQUALIFICATION, today, cint(cfg["cycles_sans_reponse"])), update_modified=False)
    return r


# =============================================================================
# 5) BROUILLONS QUI BLOQUENT LEUR TYPE (alerte du 25/11/2026)
# =============================================================================
# Un brouillon empêche la création de la liste suivante de son type (has_draft_of_type). LIST-06-11-2026-00004 (Hors
# secteur, 45 lignes appelées sur 100) n'a plus bougé du 11/11 au 25/11 sans que personne le voie, et la Zone partenaire a
# eu 25 jours de retard en octobre : un e-mail par brouillon immobile et par jour, au passage de 07:00.

def jours_ouvres_ecoules(depuis, today) -> int:
    """Jours du lundi au samedi STRICTEMENT entre la date de `depuis` et `today` (le jour même ne compte pas : à 07:00 il
    commence). Modifié un vendredi → 2 le mardi (samedi, lundi), 3 le mercredi. PURE."""
    if not depuis:
        return 0
    d, fin = getdate(depuis) + datetime.timedelta(days=1), getdate(today)
    n = 0
    while d < fin:
        n += d.weekday() != 6
        d += datetime.timedelta(days=1)
    return n


def brouillons_immobiles(today, jours, types=TYPES_BLOQUES_PAR_BROUILLON) -> list:
    """Les brouillons des types bloquants dont la dernière modification date d'au moins `jours` jours ouvrés, avec leurs
    lignes appelées / total. Le commentaire d'alerte ne touche pas `modified` : il ne remet pas le compteur à zéro."""
    rows = frappe.db.sql("""select l.name, l.type_liste, l.titre, l.date, l.creation, l.modified, l.modified_by,
                                   count(a.name) as total, coalesce(sum(a.a_été_appelé), 0) as appeles
                            from `tabListe Appelle Entretien` l
                            left join `tabAppelle Client` a on a.parent = l.name and a.parenttype = %s
                            where l.docstatus = 0 and l.type_liste in %s
                            group by l.name, l.type_liste, l.titre, l.date, l.creation, l.modified, l.modified_by
                            order by l.creation""", (DOCTYPE, tuple(types)), as_dict=True)
    out = []
    for r in rows:
        r["jours_ouvres"] = jours_ouvres_ecoules(r.modified, today)
        r["appeles"], r["total"] = cint(r.appeles), cint(r.total)
        if r["jours_ouvres"] >= jours:
            out.append(r)
    return out


def message_alerte_brouillon(b, qui, url, en_attente=None):
    """(sujet, corps HTML) de l'alerte : la liste, lignes appelées / total, dernière modification et par qui, ce qui
    attend derrière. PURE."""
    t = b.get("type_liste") or TYPE_NORMAL
    modif = frappe.utils.get_datetime(b.get("modified")) if b.get("modified") else None
    quand = modif.strftime("%d/%m/%Y à %H:%M") if modif else "?"
    sujet = "[ERPNext] Liste d’appels bloquée : %s (%s) immobile depuis %d jour(s) ouvré(s)" % (b.get("name"), t, cint(b.get("jours_ouvres")))
    attente = ""
    if en_attente:
        attente = " %d client(s) attendent déjà derrière elle." % cint(en_attente)
    corps = ("<p>La liste <b>%s</b> (%s, « %s ») est en brouillon et n’a pas bougé depuis <b>%d jour(s) ouvré(s)</b> : "
             "dernière modification le %s par %s.</p>"
             "<p><b>%d</b> ligne(s) appelée(s) sur <b>%d</b>. Tant qu’elle n’est pas validée, aucune nouvelle liste « %s » "
             "n’est créée.%s</p>"
             "<p>Une liste se valide même avec des lignes non appelées : elles reviennent dans la prochaine liste de son type.</p>"
             "<p><a href=\"%s\">Ouvrir la liste</a></p>") % (
        html.escape(b.get("name") or ""), html.escape(t), html.escape(b.get("titre") or ""), cint(b.get("jours_ouvres")),
        quand, html.escape(qui or b.get("modified_by") or "?"), cint(b.get("appeles")), cint(b.get("total")), html.escape(t),
        attente, html.escape(url or "", quote=True))
    return sujet, corps


def _alerte_deja_envoyee(nom, today) -> bool:
    debut = getdate(today)
    return bool(frappe.db.exists("Comment", {"reference_doctype": DOCTYPE, "reference_name": nom, "comment_type": "Info",
                                              "content": ["like", ALERTE_BROUILLON + "%"],
                                              "creation": ["between", [str(debut) + " 00:00:00", str(debut) + " 23:59:59.999999"]]}))


def _memoriser_alerte(b, today, destinataires):
    """La mémoire de l'alerte du jour est un commentaire Info sur la liste : visible de tous, et il ne change pas
    `modified` (le brouillon reste « immobile »)."""
    frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": DOCTYPE, "reference_name": b.name,
                    "content": "%s depuis %d jour(s) ouvré(s) (%d/%d lignes appelées) : alerte envoyée le %s à %s." % (
                        ALERTE_BROUILLON, b.jours_ouvres, b.appeles, b.total, getdate(today).strftime("%d/%m/%Y"),
                        ", ".join(destinataires) or "?")}).insert(ignore_permissions=True)


def attente_derriere(b_nom, en_attente):
    """Les clients qui attendent DERRIÈRE un brouillon : les candidats de son type qui n'y sont pas déjà (le 25/11 :
    183 candidats Hors secteur, dont 54 déjà dans LIST-06-11-2026-00004 → 129 derrière). `en_attente` : ensemble de
    clients, ou un nombre déjà calculé."""
    if en_attente is None or isinstance(en_attente, int):
        return en_attente
    deja = set(frappe.get_all("Appelle Client", filters={"parent": b_nom, "parenttype": DOCTYPE}, pluck="client"))
    return len(set(en_attente) - deja)


def alerter_brouillons_immobiles(today, cfg=None, en_attente=None) -> dict:
    """Au passage de 07:00 : pour chaque brouillon qui bloque son type et n'a pas bougé depuis `jours_brouillon_alerte`
    jours ouvrés, un e-mail (RC.alerter) — UN par brouillon et par jour — et une ligne de journal. Chaque brouillon est
    isolé ; jamais bloquant pour le passage. `en_attente` : {type: clients candidats (ensemble) ou nombre}."""
    cfg = cfg or RC.config()
    jours = cint(cfg.get("jours_brouillon_alerte", JOURS_BROUILLON_ALERTE))
    bilan = {"immobiles": 0, "alertes": 0, "deja": 0, "plus_vieux_jours": 0, "listes": []}
    if jours <= 0:
        return bilan
    for b in brouillons_immobiles(today, jours):
        bilan["immobiles"] += 1
        bilan["plus_vieux_jours"] = max(bilan["plus_vieux_jours"], b.jours_ouvres)
        bilan["listes"].append(b.name)
        try:
            modif = frappe.utils.get_datetime(b.modified).strftime("%d/%m/%Y %H:%M")
            qui = frappe.utils.get_fullname(b.modified_by) if b.modified_by else ""
            ligne = "[INFO] Brouillon bloquant %s (%s) : %d/%d ligne(s) appelée(s), immobile depuis %d jour(s) ouvré(s) (modifié le %s par %s)" % (
                b.name, b.type_liste, b.appeles, b.total, b.jours_ouvres, modif, qui or b.modified_by)
            if _alerte_deja_envoyee(b.name, today):
                bilan["deja"] += 1
                log(ligne + " : alerte déjà envoyée aujourd’hui")
                continue
            derriere = attente_derriere(b.name, (en_attente or {}).get(b.type_liste))
            sujet, corps = message_alerte_brouillon(b, qui, frappe.utils.get_url_to_form(DOCTYPE, b.name), derriere)
            if derriere:
                ligne += ", %d client(s) derrière" % derriere
            if RC.alerter(sujet, corps, cfg):
                _memoriser_alerte(b, today, RC.emails_alertes(cfg))
                bilan["alertes"] += 1
                log(ligne + " : alerte envoyée")
            else:
                log(ligne + " : alerte NON envoyée (pas d’adresse dans Config Relances, ou envoi impossible)")
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Cron - Génération listes : alerte du brouillon %s" % b.name)
    return bilan


# =============================================================================
# 6) COMPTEURS DU PASSAGE (entonnoir, 25/11/2026)
# =============================================================================

CLES_LISTES = ("normal", "zone_partenaire", "urgence", "requalification", "hors_secteur")
TYPE_DE_CLE = {"normal": TYPE_NORMAL, "zone_partenaire": TYPE_ZONE, "urgence": TYPE_URGENCE,
               "requalification": TYPE_REQUALIFICATION, "hors_secteur": TYPE_HORS_SECTEUR}


def compteurs_du_passage(results) -> dict:
    """Les compteurs du passage de 07:00, à plat (entiers) : candidats, écartés par motif, répartition, clients mis en
    liste et en attente par type, brouillons. Une baisse se voit le jour même au lieu de deux mois après. PURE."""
    r = results or {}
    nu = r.get("normal_urgence") or {}
    ent = nu.get("entonnoir") or {}
    out = {"visites_candidates": cint(ent.get("visites")), "clients_candidats": cint(ent.get("clients")),
           "ecartes_intervention_recente": cint(ent.get("intervention_recente")), "clients_a_repartir": cint(ent.get("a_repartir"))}
    ecartes = nu.get("ecartes") or {}
    for motif in MOTIFS_EXCLUSION + ("autre",):
        out["exclus_" + motif] = cint(ecartes.get(motif))
    out["ecartes_rendez_vous"] = cint(ecartes.get("rdv"))
    out["ecartes_appel_recent"] = cint(ecartes.get("recent"))
    for cle, n in (nu.get("repartition") or {}).items():
        out["vers_" + cle] = cint(n)
    out["rappels_repris"] = cint(nu.get("rappels"))
    bloques, dedans = nu.get("bloques") or {}, nu.get("deja_en_brouillon") or {}
    for cle in CLES_LISTES:
        x = nu.get(cle) or {}
        out["liste_" + cle] = cint(x.get("count_clients")) if x.get("created") else 0
        # attente_ = au-delà du plafond + DERRIÈRE le brouillon bloquant ; en_brouillon_ = candidats déjà dans ce
        # brouillon (26/11/2026 : 183 = 129 + 54 le 25/11 en Hors secteur, le même chiffre que l'alerte).
        out["attente_" + cle] = cint(x.get("en_attente")) + cint(bloques.get(TYPE_DE_CLE[cle]))
        out["en_brouillon_" + cle] = cint(dedans.get(TYPE_DE_CLE[cle]))
    sc = r.get("second_call") or {}
    out["liste_relance"] = cint(sc.get("count_clients")) if sc.get("created") else 0
    out["relance_bloquee"] = 1 if sc.get("skipped") else 0
    out["relance_reprises"] = cint(sc.get("reprises"))
    out["brouillons_purges"] = len(r.get("brouillons_supprimes") or [])
    bb = r.get("brouillons_bloquants") or {}
    out["brouillons_immobiles"] = cint(bb.get("immobiles"))
    out["brouillons_alertes"] = cint(bb.get("alertes"))
    out["brouillon_plus_vieux_jours"] = cint(bb.get("plus_vieux_jours"))
    out["erreur"] = cint(r.get("erreur"))
    return out


def _noter_entonnoir(results, today) -> bool:
    """Contrat partagé : entonnoir_relances.noter(source, compteurs, jour) — ne lève jamais, mais le module peut ne pas
    encore exister ; rien ici ne doit arrêter le passage."""
    try:
        from customization_app.entonnoir_relances import noter
    except Exception:
        log("[INFO] Entonnoir : module entonnoir_relances absent, compteurs du passage non mémorisés")
        return False
    try:
        return bool(noter("listes", compteurs_du_passage(results), jour=str(getdate(today))))
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Cron - Génération listes : entonnoir non mémorisé")
        return False


# =============================================================================
# 7) POINTS D'ENTRÉE
# =============================================================================

def _run_cron_internal():
    cfg, today = RC.config(), nowdate()
    results = {}
    try:
        rdv = get_recent_rdv_customers(cfg=cfg)
        results["brouillons_supprimes"] = purger_brouillons_abandonnes(today)
        results["second_call"] = _generate_second_call_list(today, rdv, cfg) if not has_draft_of_type(TYPE_RELANCE) \
            else {"created": False, "skipped": True, "reason": "draft_relance_exists"}
        results["normal_urgence"] = _generate_normal_urgence_list(today, rdv, create_normal=not has_draft_of_type(TYPE_NORMAL),
                                                                  create_urgence=True, ignore_draft_normal=False, create_zone=True, cfg=cfg)
    except Exception:
        # Le passage a planté (run_safely journalise et alerte) : l'entonnoir le note, au lieu d'une journée « à zéro ».
        results["erreur"] = 1
        (results.get("normal_urgence") or {}).pop("bloques_clients", None)
        _noter_entonnoir(results, today)
        raise
    attente = (results["normal_urgence"] or {}).pop("bloques_clients", None) or {}     # des ensembles : pas dans le journal
    try:
        results["brouillons_bloquants"] = alerter_brouillons_immobiles(today, cfg, attente)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Cron - Génération listes : alertes des brouillons")
    log(f"[CRON SUMMARY] {results}")
    _noter_entonnoir(results, today)
    return results


def _run_manual_internal(list_type=None):
    cfg, today = RC.config(), nowdate()
    rdv = get_recent_rdv_customers(cfg=cfg)
    lt = (list_type or "all").lower()
    results = {}
    if lt in ("all", "relance", "2e", "2e_appel", "second", "second_call"):
        results["second_call"] = _generate_second_call_list(today, rdv, cfg)
    if lt in ("all", "normal", "urgence", "zone", "partenaire", "zone_partenaire", "hors", "hors_secteur"):
        results["normal_urgence"] = _generate_normal_urgence_list(
            today, rdv, create_normal=lt in ("all", "normal"), create_urgence=lt in ("all", "urgence"),
            ignore_draft_normal=True, create_zone=lt in ("all", "zone", "partenaire", "zone_partenaire"), cfg=cfg,
            create_hors=lt in ("all", "hors", "hors_secteur"))
        results["normal_urgence"].pop("bloques_clients", None)       # des ensembles : ni journal ni réponse JSON
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
    for cle, libelle in (("normal", "Normal"), ("zone_partenaire", "Zone partenaire"), ("urgence", "Urgence"), ("requalification", "Requalification"),
                         ("hors_secteur", "Hors secteur")):
        r = nu.get(cle)
        if r and r.get("created"):
            morceaux.append("%s : %d client(s)%s" % (libelle, r["count_clients"], (" (+%d en attente)" % r["en_attente"]) if r.get("en_attente") else ""))
    return " · ".join(morceaux) if morceaux else _("Aucune liste créée (rien à appeler, ou un brouillon existe déjà).")
