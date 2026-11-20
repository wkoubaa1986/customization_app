"""Réparation des osmoseurs déposés à l'atelier — écran « Réparation osmoseurs ».

Le processus, tel que demandé le 01/10/2026 :

1. RÉCEPTION : photo de la machine à l'arrivée, client, note. La réception se
   CLÔTURE sur une seconde photo (la machine avec son post-it qui porte le numéro
   de dossier), ou sur le code superviseur de Config Cloture Tache — un seul code
   à retenir dans l'app.
2. AFFECTATION AUTOMATIQUE : à la clôture, une tâche « Réparation » est créée pour
   le LENDEMAIN. On parcourt les responsables de réparation DANS L'ORDRE configuré
   (Config Reparation Osmoseur), on retient le premier disponible ce jour-là ; un
   jour n'a qu'UN SEUL opérateur et reçoit au plus N machines (3 par défaut). Jour
   plein ou personne de disponible → jour suivant.
3. VALIDATION : la clôture de la (des) tâche(s) liée(s) fait passer le dossier à
   « Réparée » — c'est-à-dire À RENDRE.
4. RESTITUTION (02/10/2026) : deux chemins. « Le client vient la chercher » → « Prête au
   magasin » (SMS « machine prête » proposé), puis « Rendue au client » à la remise (personne,
   photo facultative). « Planifier une livraison » → tâche Livraison au calendrier, dossier
   « Livraison planifiée » ; la clôture de cette tâche rend la machine toute seule, sa
   suppression ramène le dossier à « Réparée ».

5. SIMPLIFICATION DU 20/11/2026 (« l'état n'est pas clair si c'est restitué ou pas ») — constat sur 14 dossiers :
   le clic « Rendue au client » n'avait jamais été fait (seules les 3 livraisons clôturées avaient fermé leur
   dossier), 6 machines parties restaient « à rendre », et une tâche annulée renvoyait pour toujours à « à affecter ».
   - L'ÉCRAN ne montre plus que trois groupes : À réparer / À rendre / Rendue (les 7 statuts restent en base).
   - « RENDUE » dit que la machine est SORTIE ; le RÈGLEMENT s'affiche à côté et ne bloque pas. Une « Dette non
     payée » n'est PAS un paiement (ses Payment Entry gonflent `advance_paid`) : le reste dû se lit sur
     l'échéancier, comme dans livraison_partielle.
   - Ce qu'il faut pour fermer (`regle_sortie`) : une commande liée (payée ou non) → libre ; sous garantie avec la
     commande d'origine → libre, sans vente ; sous garantie sans cette commande → code superviseur ; hors garantie
     sans aucune vente → motif + code superviseur ; machine reprise SANS réparation → motif seulement.
   - La sortie se confirme là où l'on est déjà : question posée à celui qui vient de clôturer la réparation
     (événement temps réel `osm_a_rendre`), bandeau sur la commande / le BL du client, tâche Livraison clôturée.
   - Une tâche liée au dossier reste SA réparation même si quelqu'un change son type (OSM-2026-0012 : retypée
     « Livraison », clôturée, dossier resté « à affecter ») ; seule `tache_livraison` est la livraison de retour.
   - Rappel du matin à la personne du réglage (« Suivi des restitutions »), relance à une seconde si rien ne bouge.

Toute la décision est ici, côté serveur ; l'écran n'invente rien.
"""

from __future__ import annotations

import base64
import hmac
import json
import re
import unicodedata

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, now_datetime, nowdate

DOCTYPE = "Machine Reparation"
DOCTYPE_TACHE = "Tache de travail"
DOCTYPE_CONFIG = "Config Reparation Osmoseur"
CHAMP_TACHE = "machine_reparation"        # Custom Field sur la Tache de travail
TYPE_TACHE = "Réparation"

S_RECEPTION = "Réception en cours"
S_RECEPTIONNEE = "Réceptionnée"           # clôturée mais SANS tâche (personne à affecter)
S_PLANIFIEE = "Planifiée"
S_REPAREE = "Réparée"                     # = à rendre
S_PRETE = "Prête au magasin"              # le client vient la chercher
S_LIVRAISON = "Livraison planifiée"       # une tâche Livraison la ramène
S_RENDUE = "Rendue au client"
STATUTS = [S_RECEPTION, S_RECEPTIONNEE, S_PLANIFIEE, S_REPAREE, S_PRETE, S_LIVRAISON, S_RENDUE]
EN_RESTITUTION = (S_PRETE, S_LIVRAISON)
REPAREE = (S_REPAREE, S_PRETE)            # réparée, encore chez nous : on peut la rendre

# Ce que l'écran montre (20/11/2026) : trois groupes, pas sept statuts.
G_REPARER, G_RENDRE, G_RENDUE = "À réparer", "À rendre", "Rendue"
GROUPES = {G_REPARER: (S_RECEPTION, S_RECEPTIONNEE, S_PLANIFIEE), G_RENDRE: (S_REPAREE, S_PRETE, S_LIVRAISON),
           G_RENDUE: (S_RENDUE,)}
RAPPEL_JOURS_DEFAUT = 3                  # une machine qui attend depuis 3 jours est rappelée
RELANCE_JOURS_DEFAUT = 3                 # sans réponse 3 jours après le premier rappel : la seconde personne
EVENEMENT_SUITE = "osm_a_rendre"         # temps réel : « la machine est réparée — et maintenant ? »
BOT = "Magasin Bot"                      # le bot Raven qui porte le rappel du matin
PAGE = "/app/reparation-osmoseurs"
MOTIFS_SANS_REPARATION = ("Le client reprend sa machine", "Réparation impossible ou pas rentable", "Devis refusé",
                          "Réception par erreur", "Autre")
MOTIFS_NON_FACTUREE = ("Geste commercial", "Rien à réparer", "Autre")
#: Ce qui RÉPOND à un rappel laisse une trace sur le dossier, commençant par un de ces repères : c'est ce que la file
#: « Machines de l'atelier » de « Mes appels par priorité » compte comme fait aujourd'hui (tableau_appels).
REPERES_REPONSE = ("✅ Rendue", "🔓 Rendue", "↪️ Sortie sans réparation", "📍 Toujours au magasin", "📲 SMS", "📅 Technicien affecté")


def groupe_de(statut) -> str:
    """Le groupe d'écran d'un statut. PURE."""
    return next((g for g, statuts in GROUPES.items() if statut in statuts), G_REPARER)

CHAMPS_PHOTO = {"arrivee": "photo_arrivee", "post_it": "photo_post_it", "remise": "photo_remise"}
TYPE_LIVRAISON = "Livraison"
DUREE_LIVRAISON = 30
SMS_PRET = ("Bonjour {nom}, votre appareil ({ref}) est repare et vous attend au magasin Aquaworld (Soukra).{garantie} "
            "Pour toute question : {tel}.")
SMS_LIVRAISON = ("Bonjour {nom}, votre appareil ({ref}) est repare : nous vous le livrons le {date} ({employe}).{garantie} "
                 "Pour toute question : {tel}.")
GARANTIES = ("Sous garantie", "Hors garantie")
MOIS_HISTORIQUE = 13                     # la dernière année + 1 mois (décision 01/10/2026)
NOTE_GARANTIE = "🆓 SOUS GARANTIE — réparation GRATUITE, ne rien facturer au client"
MARQUE_CONTROLE_INDISPONIBLE = "indisponible"
TYPES_ABSENCE = ("Congé", "Jour de récupération")
CRENEAUX_DEFAUT = ["09:00", "10:15", "11:30"]
DUREE_DEFAUT = 75                        # 1 h 15 par réparation (décision 01/10/2026)
MACHINES_PAR_JOUR_DEFAUT = 3
HORIZON_DEFAUT = 30


# ── Accès ────────────────────────────────────────────────────────────────────

def _lecture():
    frappe.has_permission(DOCTYPE, "read", throw=True)


def _ecriture(nom=None):
    frappe.has_permission(DOCTYPE, "write", doc=nom, throw=True)


# ── Configuration ────────────────────────────────────────────────────────────

def config() -> dict:
    """Les réglages lus du single, avec des défauts sûrs quand rien n'est enregistré."""
    cfg = frappe.get_cached_doc(DOCTYPE_CONFIG) if frappe.db.exists("DocType", DOCTYPE_CONFIG) else None
    responsables = [r.employee for r in (cfg.get("responsables") if cfg else []) or [] if r.employee]
    creneaux = [c.strip() for c in ((cfg and cfg.get("creneaux")) or "").split(",") if c.strip()] or CRENEAUX_DEFAUT
    return {
        "responsables": responsables,
        "machines_par_jour": cint(cfg and cfg.get("machines_par_jour")) or MACHINES_PAR_JOUR_DEFAUT,
        "creneaux": creneaux,
        "horizon_jours": cint(cfg and cfg.get("horizon_jours")) or HORIZON_DEFAUT,
        "duree_minutes": cint(cfg and cfg.get("duree_minutes")) or DUREE_DEFAUT,
        # Suivi des restitutions : qui reçoit le rappel du matin, après combien de jours, et qui est relancé.
        "suivi": (cfg and cfg.get("suivi_restitutions")) or None,
        "rappel_jours": cint(cfg and cfg.get("rappel_jours")) or RAPPEL_JOURS_DEFAUT,
        "relance": (cfg and cfg.get("relance_a")) or None,
        "relance_jours": cint(cfg and cfg.get("relance_jours")) or RELANCE_JOURS_DEFAUT,
    }


# ── Disponibilité ────────────────────────────────────────────────────────────

def _ferie(employee: str, jour) -> bool:
    """Férié ou repos hebdomadaire selon le calendrier de l'employé (HRMS)."""
    try:
        from hrms.hr.utils import get_holidays_for_employee
        return bool(get_holidays_for_employee(employee, jour, jour, raise_exception=False))
    except Exception:
        return getdate(jour).weekday() == 6   # sans HRMS : le dimanche seulement


def _en_conge(employee: str, jour) -> bool:
    if frappe.db.exists("Leave Application", {"employee": employee, "docstatus": 1, "status": "Approved",
                                              "from_date": ["<=", jour], "to_date": [">=", jour]}):
        return True
    return bool(frappe.db.exists(DOCTYPE_TACHE, {
        "custom_choix_du_staff": employee, "status": ["!=", "Cancelled"],
        "custom_type_dintervention": ["in", TYPES_ABSENCE],
        "starts_on": ["<", add_days(jour, 1)], "ends_on": [">=", f"{jour} 00:00:00"]}))


def disponible(employee: str, jour) -> bool:
    jour = getdate(jour)
    if frappe.db.get_value("Employee", employee, "status") != "Active":
        return False
    return not _ferie(employee, jour) and not _en_conge(employee, jour)


def taches_atelier_du_jour(jour) -> list[dict]:
    """Les tâches de réparation ATELIER (liées à un dossier) posées ce jour-là, hors annulées."""
    jour = getdate(jour)
    return frappe.get_all(DOCTYPE_TACHE, fields=["name", "custom_choix_du_staff", "starts_on", CHAMP_TACHE],
                          filters={CHAMP_TACHE: ["is", "set"], "status": ["!=", "Cancelled"],
                                   "starts_on": ["between", [f"{jour} 00:00:00", f"{jour} 23:59:59"]]},
                          order_by="starts_on")


def choisir_creneau(a_partir, cfg=None, est_disponible=disponible, taches_du_jour=taches_atelier_du_jour):
    """Le premier (jour, responsable, rang) qui respecte la règle, à partir de `a_partir` inclus.

    Règle : un seul opérateur par jour — s'il y a déjà des tâches atelier ce jour-là, c'est
    LEUR employé qui reçoit la machine suivante, tant que le plafond n'est pas atteint. Sinon
    le premier responsable disponible dans l'ordre configuré. Jour plein ou personne → lendemain.

    `est_disponible` et `taches_du_jour` sont injectables : la règle se teste sans base.
    Retourne None quand l'horizon est épuisé ou qu'aucun responsable n'est configuré.
    """
    cfg = cfg or config()
    if not cfg["responsables"]:
        return None
    jour = getdate(a_partir)
    for _i in range(cfg["horizon_jours"]):
        du_jour = taches_du_jour(jour)
        if du_jour:
            operateur = du_jour[0]["custom_choix_du_staff"]
            if len(du_jour) < cfg["machines_par_jour"] and operateur in cfg["responsables"]:
                return {"jour": jour, "employee": operateur, "rang": len(du_jour)}
        else:
            for emp in cfg["responsables"]:
                if est_disponible(emp, jour):
                    return {"jour": jour, "employee": emp, "rang": 0}
        jour = add_days(jour, 1)
    return None


# ── Lecture pour l'écran ─────────────────────────────────────────────────────

def _taches_par_machine(noms: list[str]) -> dict[str, list[dict]]:
    if not noms:
        return {}
    rows = frappe.get_all(DOCTYPE_TACHE, filters={CHAMP_TACHE: ["in", noms]},
                          fields=["name", CHAMP_TACHE, "status", "custom_choix_du_staff", "custom_employé",
                                  "starts_on", "custom_type_dintervention", "rapport_visite",
                                  "liste_photos_avant", "liste_photos_apres"],
                          order_by="starts_on")
    noms_emp = {r.custom_choix_du_staff for r in rows if r.custom_choix_du_staff}
    libelles = dict(frappe.get_all("Employee", filters={"name": ["in", list(noms_emp)]},
                                   fields=["name", "employee_name"], as_list=True)) if noms_emp else {}
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r[CHAMP_TACHE], []).append({
            "name": r.name, "status": r.status, "type": r.custom_type_dintervention,
            "employee": r.custom_choix_du_staff,
            "employe": libelles.get(r.custom_choix_du_staff) or r.get("custom_employé") or "",
            "starts_on": r.starts_on, "rapport": r.rapport_visite,
            "photos_avant": photos_de(r.liste_photos_avant), "photos_apres": photos_de(r.liste_photos_apres),
        })
    return out


def photos_de(texte) -> list:
    """Les URL d'un champ liste de photos d'une tâche (une par ligne, « 📁 "url" »). PURE."""
    out = []
    for ligne in (texte or "").splitlines():
        trouve = re.search(r'"([^"]+)"', ligne)
        url = (trouve.group(1) if trouve else ligne.replace("📁", "")).strip()
        if url.startswith(("/files/", "/private/files/", "http://", "https://")):
            out.append(url)
    return out


def photos_du_dossier(m, taches) -> list:
    """Toutes les photos prises pour ce dossier, dans l'ordre de sa vie : arrivée, post-it, réparation (pièces
    enlevées puis appareil réparé), remise. -> [{url, titre}]. PURE."""
    out = []
    if m.get("photo_arrivee"):
        out.append({"url": m["photo_arrivee"], "titre": "À l'arrivée"})
    if m.get("photo_post_it"):
        out.append({"url": m["photo_post_it"], "titre": "Avec son post-it"})
    for t in taches or []:
        livraison = t.get("name") and t.get("name") == m.get("tache_livraison")
        for url in t.get("photos_avant") or []:
            out.append({"url": url, "titre": "Livraison" if livraison else "Réparation — pièces enlevées"})
        for url in t.get("photos_apres") or []:
            out.append({"url": url, "titre": "Livraison" if livraison else "Réparation — appareil réparé"})
    if m.get("photo_remise"):
        out.append({"url": m["photo_remise"], "titre": "À la remise"})
    return out


# ── Règlement : ce que la commande de la réparation a vraiment encaissé ──────

def etat_reglement(total, regle, dette, hors_dette) -> dict:
    """Où en est le règlement d'une commande. PURE. -> {total, reste, etat: "payee" | "du" | "zero"}.
    `dette` : les lignes « Dette non payée » de l'échéancier — le client doit encore cette somme, même si un
    Payment Entry de ce mode fait croire à ERPNext que la commande est payée. Sans ligne de dette, le reste dû est
    ce que ni les encaissements (`regle`) ni les autres lignes de l'échéancier (`hors_dette` : avoir, chèque…)
    ne couvrent."""
    total, dette = flt(total), flt(dette)
    if total <= 0.001:
        return {"total": 0.0, "reste": 0.0, "etat": "zero"}
    reste = dette if dette > 0.001 else max(total - max(flt(regle), flt(hors_dette)), 0.0)
    reste = round(min(reste, total), 3)
    return {"total": round(total, 3), "reste": reste, "etat": "du" if reste > 0.001 else "payee"}


def reglements(commandes) -> dict:
    """{commande: etat_reglement(...) + {"commande"}} pour des commandes validées. Mêmes fragments SQL que
    livraison_partielle : un seul endroit dit ce qu'est un encaissement réel."""
    from customization_app import livraison_partielle as LP

    noms = tuple({c for c in commandes or [] if c})
    if not noms:
        return {}
    rows = frappe.db.sql(
        "SELECT so.name, " + LP.SQL_TOTAL + " AS total, " + LP.SQL_REGLE + " AS regle, " + LP.SQL_DETTE_LIGNE
        + " AS dette, " + LP.SQL_REGLE_ECHEANCIER + " AS hors_dette FROM `tabSales Order` so"
        + " WHERE so.docstatus = 1 AND so.name IN %(noms)s", dict(LP.PARAMS_SQL, noms=noms), as_dict=True)
    return {r.name: dict(etat_reglement(r.total, r.regle, r.dette, r.hors_dette), commande=r.name) for r in rows}


def libelle_reglement(reg, garantie) -> str:
    """La phrase gardée sur le dossier au moment de la sortie. PURE."""
    if reg:
        if reg["etat"] == "du":
            return "reste dû %.3f DT sur %.3f (commande %s)" % (reg["reste"], reg["total"], reg["commande"])
        if reg["etat"] == "zero":
            return "commande à 0 DT (%s)" % reg["commande"]
        return "payée — %.3f DT (commande %s)" % (reg["total"], reg["commande"])
    return "garantie — rien à facturer" if garantie == "Sous garantie" else "non facturée"


def regle_sortie(repare: bool, garantie, commande: bool, commande_garantie: bool) -> dict:
    """Ce qu'il faut pour fermer le dossier. PURE. -> {cas, motif, code} (motif / code : obligatoires ou non).

    - machine NON réparée (reprise par le client, réception par erreur…) : un motif, rien d'autre ;
    - une commande est liée : libre — payée ou pas, la dette reste sur le compte du client et s'affiche ;
    - sous garantie AVEC la commande d'origine : libre, sans vente ;
    - sous garantie SANS cette commande : code superviseur (cocher « sous garantie » ne suffit pas à sortir une
      machine gratuitement) ;
    - hors garantie, aucune vente : motif + code superviseur."""
    if not repare:
        return {"cas": "sans_reparation", "motif": True, "code": False}
    if commande:
        return {"cas": "commande", "motif": False, "code": False}
    if garantie == "Sous garantie":
        return ({"cas": "garantie", "motif": False, "code": False} if commande_garantie
                else {"cas": "garantie_sans_preuve", "motif": False, "code": True})
    return {"cas": "non_facturee", "motif": True, "code": True}


# ── Attente : ce qui traîne, pour l'écran et le rappel du matin ──────────────

def attente(m, aujourdhui) -> dict | None:
    """Depuis combien de jours ce dossier attend quelqu'un, et pour quoi. PURE. None : rien à signaler.
    -> {"motif": "a_rendre" | "sans_technicien" | "livraison", "jours": n}.
    « Toujours au magasin » (`toujours_la_le`) remet le compteur à zéro."""
    def jours(*dates):
        connues = [getdate(d) for d in dates if d]
        return (aujourdhui - max(connues)).days if connues else None

    statut = m.get("statut")
    if statut in REPAREE:
        j = jours(m.get("date_reparee"), m.get("toujours_la_le"))
        return None if j is None else {"motif": "a_rendre", "jours": j}
    if statut == S_RECEPTIONNEE:
        j = jours(m.get("date_cloture_reception") or m.get("date_reception"), m.get("toujours_la_le"))
        return None if j is None else {"motif": "sans_technicien", "jours": j}
    if statut == S_LIVRAISON and m.get("date_livraison_prevue"):
        j = (aujourdhui - getdate(m["date_livraison_prevue"])).days
        return {"motif": "livraison", "jours": j} if j > 0 else None
    return None


def a_verifier(m, aujourdhui, rappel_jours) -> bool:
    """Ce dossier attend depuis trop longtemps (une livraison : dès le lendemain de sa date). PURE."""
    a = attente(m, aujourdhui)
    return bool(a) and (a["motif"] == "livraison" or a["jours"] >= rappel_jours)


def a_rappeler(machines, aujourdhui, rappel_jours, relance_jours) -> dict:
    """Le tri du matin. PURE. -> {"rappel": [...], "relance": [...]} (dossiers enrichis de `attente`, les plus
    anciens d'abord). `relance` : ceux déjà rappelés (`rappel_depuis`) restés sans réponse `relance_jours` jours."""
    rappel = []
    for m in machines or []:
        if a_verifier(m, aujourdhui, rappel_jours):
            rappel.append(dict(m, attente=attente(m, aujourdhui)))
    rappel.sort(key=lambda m: -m["attente"]["jours"])
    relance = [m for m in rappel
               if m.get("rappel_depuis") and (aujourdhui - getdate(m["rappel_depuis"])).days >= relance_jours]
    return {"rappel": rappel, "relance": relance}


CHAMPS_ATTENTE = ["name", "statut", "nom_client", "client", "date_reception", "date_cloture_reception", "date_reparee",
                  "toujours_la_le", "date_livraison_prevue", "rappel_depuis"]


@frappe.whitelist()
def get_data(statut=None, client=None, responsable=None, recherche=None, inclure_rendues=0, vue=None, verifier=0):
    """`vue` : un des trois groupes d'écran (À réparer / À rendre / Rendue). `verifier` : seulement les dossiers
    qui attendent depuis trop longtemps (ceux du rappel du matin)."""
    _lecture()
    filtres = {}
    if statut:
        filtres["statut"] = statut
    elif vue in GROUPES:
        filtres["statut"] = ["in", list(GROUPES[vue])]
    elif not cint(inclure_rendues):
        filtres["statut"] = ["!=", S_RENDUE]
    if client:
        filtres["client"] = client
    if responsable:
        filtres["responsable"] = responsable
    or_filtres = None
    if recherche:
        motif = f"%{recherche}%"
        or_filtres = [["name", "like", motif], ["nom_client", "like", motif], ["tel", "like", motif]]
    machines = frappe.get_all(DOCTYPE, filters=filtres, or_filters=or_filtres, limit_page_length=500,
                              fields=["name", "client", "nom_client", "tel", "statut", "date_reception", "note_reception", "photo_arrivee",
                                      "photo_post_it", "dispense_post_it", "date_cloture_reception",
                                      "controle_ia_ok", "controle_ia_resultat", "garantie", "commande_garantie", "responsable", "nom_responsable", "date_prevue", "date_reparee",
                                      "date_rendue", "commande_client", "modified", "mode_restitution", "sms_pret_le", "tache_livraison",
                                      "date_livraison_prevue", "rendu_a", "photo_remise", "remarque_restitution",
                                      "sans_reparation", "motif_sortie", "reglement_sortie", "rendue_par",
                                      "toujours_la_le", "rappel_depuis"],
                              order_by="creation desc")
    cfg = config()
    taches = _taches_par_machine([m.name for m in machines])
    regs = reglements([m.commande_client for m in machines])
    aujourdhui = getdate(nowdate())
    for m in machines:
        m["taches"] = taches.get(m.name, [])
        ref = getdate(m.date_reparee) if m.date_reparee else getdate(m.date_reception or m.modified)
        m["age_jours"] = (aujourdhui - ref).days
        m["groupe"] = groupe_de(m.statut)
        m["photos"] = photos_du_dossier(m, m["taches"])
        m["reglement"] = regs.get(m.commande_client) if m.commande_client else None
        m["attente"] = attente(m, aujourdhui)
        m["a_verifier"] = a_verifier(m, aujourdhui, cfg["rappel_jours"])
    if cint(verifier):
        machines = [m for m in machines if m["a_verifier"]]
    compteurs = dict(frappe.get_all(DOCTYPE, fields=["statut", "count(name) as n"], group_by="statut", as_list=True))
    ouverts = frappe.get_all(DOCTYPE, filters={"statut": ["in", [S_RECEPTIONNEE, S_REPAREE, S_PRETE, S_LIVRAISON]]},
                             fields=CHAMPS_ATTENTE)
    return {
        "machines": machines,
        "kpis": {s: cint(compteurs.get(s)) for s in STATUTS},
        "groupes": [{"nom": g, "n": sum(cint(compteurs.get(s)) for s in statuts),
                     "detail": {s: cint(compteurs.get(s)) for s in statuts}} for g, statuts in GROUPES.items()],
        "a_verifier": sum(1 for m in ouverts if a_verifier(m, aujourdhui, cfg["rappel_jours"])),
        "rappel_jours": cfg["rappel_jours"],
        "suivi": frappe.utils.get_fullname(cfg["suivi"]) if cfg["suivi"] else None,
        "statuts": STATUTS,
        "responsables": [{"name": e, "employee_name": frappe.db.get_value("Employee", e, "employee_name")}
                         for e in cfg["responsables"]],
        "config_ok": bool(cfg["responsables"]),
        "peut_configurer": "System Manager" in frappe.get_roles(),
    }


# ── Commandes du client (garantie) ───────────────────────────────────────────

def _articles_garantis(codes: set) -> set:
    """Les codes dont le groupe (ou un ancêtre) est coché « Sous garantie » — même règle que la
    mention du BL (jinja_methods.bl_sous_garantie)."""
    if not codes or not frappe.db.has_column("Item Group", "custom_sous_garantie"):
        return set()
    return {r[0] for r in frappe.db.sql("""
        SELECT DISTINCT i.name FROM `tabItem` i
        JOIN `tabItem Group` g ON g.name = i.item_group
        JOIN `tabItem Group` p ON g.lft >= p.lft AND g.rgt <= p.rgt
        WHERE i.name IN %(codes)s AND p.custom_sous_garantie = 1""", {"codes": tuple(codes)})}


@frappe.whitelist()
def commandes_client(client, mois=MOIS_HISTORIQUE):
    """Les commandes validées du client sur les N derniers mois, avec leurs articles (nom, image,
    composants de bundle) et un drapeau « appareil garanti » par article : c'est au vu de cette
    liste que l'accueil décide si la réparation est sous garantie."""
    _lecture()
    frappe.has_permission("Sales Order", "read", throw=True)
    depuis = frappe.utils.add_months(getdate(nowdate()), -cint(mois))
    commandes = frappe.get_all("Sales Order", filters={"customer": client, "docstatus": 1,
                                                        "transaction_date": [">=", depuis]},
                               fields=["name", "transaction_date", "delivery_date", "status", "grand_total",
                                       "per_delivered"], order_by="transaction_date desc", limit_page_length=50)
    if not commandes:
        return {"depuis": str(depuis), "commandes": []}
    noms = [c.name for c in commandes]
    lignes = frappe.get_all("Sales Order Item", filters={"parent": ["in", noms]}, order_by="parent, idx",
                            fields=["parent", "item_code", "item_name", "qty", "image"])
    packed = frappe.get_all("Packed Item", filters={"parent": ["in", noms], "parenttype": "Sales Order"},
                            fields=["parent", "item_code", "item_name", "qty"], order_by="parent, idx")
    codes = {l.item_code for l in lignes + packed if l.item_code}
    images = dict(frappe.get_all("Item", filters={"name": ["in", list(codes)]}, fields=["name", "image"], as_list=True)) if codes else {}
    garantis = _articles_garantis(codes)
    par_commande: dict[str, list] = {}
    for l in lignes:
        par_commande.setdefault(l.parent, []).append({
            "item_code": l.item_code, "item_name": l.item_name, "qty": l.qty,
            "image": l.image or images.get(l.item_code), "garanti": l.item_code in garantis, "composant": 0})
    for l in packed:
        par_commande.setdefault(l.parent, []).append({
            "item_code": l.item_code, "item_name": l.item_name, "qty": l.qty,
            "image": images.get(l.item_code), "garanti": l.item_code in garantis, "composant": 1})
    for c in commandes:
        c["articles"] = par_commande.get(c.name, [])
        c["garanti"] = any(a["garanti"] for a in c["articles"])
    return {"depuis": str(depuis), "commandes": commandes}


# ── Réception ────────────────────────────────────────────────────────────────

@frappe.whitelist()
def _verifier_garantie(garantie, commande_garantie, client):
    if garantie not in GARANTIES:
        frappe.throw(_("Choisissez : sous garantie ou hors garantie."))
    if commande_garantie and frappe.db.get_value("Sales Order", commande_garantie, "customer") != client:
        frappe.throw(_("La commande {0} n'est pas celle de ce client.").format(commande_garantie))


@frappe.whitelist()
def creer_reception(client, note=None, tel=None, garantie=None, commande_garantie=None):
    """Étape 1 : le dossier naît tout de suite — il faut un numéro pour le post-it, et un
    document pour y attacher les photos. La garantie se décide ICI, au vu des commandes."""
    frappe.has_permission(DOCTYPE, "create", throw=True)
    if not frappe.db.exists("Customer", client):
        frappe.throw(_("Client introuvable : {0}").format(client))
    _verifier_garantie(garantie, commande_garantie, client)
    doc = frappe.get_doc({
        "doctype": DOCTYPE, "client": client, "tel": tel, "note_reception": note, "statut": S_RECEPTION,
        "date_reception": now_datetime(), "garantie": garantie, "commande_garantie": commande_garantie or None,
    }).insert()
    return {"name": doc.name, "nom_client": doc.nom_client, "tel": doc.tel}


@frappe.whitelist()
def enregistrer_photo(machine, champ, file_url):
    """Pose l'URL d'une photo déjà téléversée (FileUploader côté client) sur le dossier."""
    _ecriture(machine)
    if champ not in CHAMPS_PHOTO:
        frappe.throw(_("Photo inconnue : {0}").format(champ))
    if not (file_url or "").startswith(("/files/", "/private/files/")):
        frappe.throw(_("Fichier invalide."))
    frappe.db.set_value(DOCTYPE, machine, CHAMPS_PHOTO[champ], file_url)
    return etat_reception(machine)


@frappe.whitelist()
def etat_reception(machine):
    _lecture()
    m = frappe.db.get_value(DOCTYPE, machine, ["name", "client", "nom_client", "tel", "statut", "photo_arrivee",
                                               "photo_post_it", "dispense_post_it", "note_reception"], as_dict=True)
    if not m:
        frappe.throw(_("Dossier introuvable."))
    m["peut_cloturer"] = bool(m.photo_arrivee) and bool(m.photo_post_it or m.dispense_post_it)
    m["post_it"] = texte_post_it(m)
    return m


def texte_post_it(m) -> dict:
    """Ce qui doit être écrit sur le post-it : nom du client, son téléphone, la référence."""
    tel = (m.get("tel") or "").strip()
    if not tel and m.get("client"):
        from customization_app.customize_erpnext.doctype.machine_reparation.machine_reparation import telephone_client
        tel = telephone_client(m.get("client"))
    return {"nom": m.get("nom_client") or "", "tel": tel, "ref": m.get("name")}


def _verifier_code(code: str) -> bool:
    """Le code superviseur de Config Cloture Tache — le même que pour les tâches."""
    from frappe.utils.password import get_decrypted_password
    attendu = get_decrypted_password("Config Cloture Tache", "Config Cloture Tache",
                                     "code_deverrouillage", raise_exception=False)
    if not attendu:
        frappe.throw(_("Aucun code superviseur n'est configuré (Config Cloture Tache)."))
    return hmac.compare_digest(str(code or ""), str(attendu))


@frappe.whitelist()
def enregistrer_details(machine, note=None, tel=None, commande_client=None, garantie=None, commande_garantie=None):
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if garantie:
        _verifier_garantie(garantie, commande_garantie, doc.client)
        doc.update({"garantie": garantie, "commande_garantie": commande_garantie or None})
    doc.update({"note_reception": note, "tel": tel, "commande_client": commande_client or None})
    doc.save()
    return {"ok": True}


@frappe.whitelist()
def cloturer_reception(machine, code=None):
    """Étape 2 : clôture sur la photo post-it OU le code superviseur, puis affectation."""
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut != S_RECEPTION:
        frappe.throw(_("Ce dossier n'est plus en réception ({0}).").format(doc.statut))
    if not doc.photo_arrivee:
        frappe.throw(_("La photo de la machine à l'arrivée est obligatoire."))
    if doc.garantie not in GARANTIES:
        frappe.throw(_("Décidez d'abord si la réparation est sous garantie (fiche du dossier)."))
    avertissement = None
    if code:
        if not _verifier_code(code):
            frappe.log_error("Code superviseur refusé — dossier %s, utilisateur %s"
                             % (machine, frappe.session.user), "reparation_osmoseur")
            frappe.throw(_("Code incorrect."))
        if not doc.photo_post_it:
            doc.dispense_post_it = 1
            doc.add_comment("Info", _("🔓 Réception clôturée sans photo post-it par {0} (code superviseur).")
                            .format(frappe.session.user))
        else:
            doc.add_comment("Info", _("🔓 Contrôle IA des photos passé outre par {0} (code superviseur).")
                            .format(frappe.session.user))
    elif not doc.photo_post_it:
        frappe.throw(_("Photo de la machine avec son post-it manquante (ou code superviseur)."))
    else:
        # Le post-it doit être JUSTE (nom, téléphone, référence) et la machine la MÊME que
        # celle de la photo d'arrivée : c'est l'IA qui relit les deux photos. Un service
        # indisponible ne bloque pas l'atelier — il est signalé et tracé sur le dossier.
        verdict = controler_photos(machine, doc=doc)
        if verdict.get("indisponible"):
            avertissement = verdict["message"]
        elif not verdict["ok"]:
            frappe.throw(_("Clôture refusée par le contrôle des photos :<br>• {0}<br><br>"
                           "Reprenez la photo, ou passez outre avec le code superviseur.")
                         .format("<br>• ".join(verdict["problemes"])), title=_("Photos non conformes"))
    doc.statut = S_RECEPTIONNEE
    doc.date_cloture_reception = now_datetime()
    doc.save()
    affectation = planifier(machine, trace=0)
    return {"name": doc.name, "statut": frappe.db.get_value(DOCTYPE, machine, "statut"),
            "avertissement": avertissement, **affectation}


# ── Contrôle IA des photos ───────────────────────────────────────────────────

def _contenu_image(url: str):
    """(octets, mime) d'une photo du dossier, par le DocType File — seul chemin qui lise un
    fichier PRIVÉ (les photos téléversées le sont) et qui résiste à un déplacement du dossier."""
    nom = frappe.db.get_value("File", {"file_url": url}, "name")
    if not nom:
        return None, None
    contenu = frappe.get_doc("File", nom).get_content()
    if not contenu:
        return None, None
    if contenu[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif contenu[:4] == b"RIFF" and contenu[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = "image/jpeg"
    return contenu, mime


_INSTRUCTIONS_IA = (
    "Tu contrôles la réception d'un appareil de traitement d'eau (osmoseur, adoucisseur, filtre) "
    "dans un atelier de réparation en Tunisie. On te donne DEUX photos : la photo 1 est l'appareil "
    "à son arrivée ; la photo 2 doit montrer LE MÊME appareil avec un post-it manuscrit collé "
    "dessus. Rends STRICTEMENT un objet JSON, sans texte autour : "
    '{"post_it_visible": <true si un post-it (papier collé avec écriture) est visible sur la photo 2>, '
    '"post_it_texte": "<tout le texte lu sur le post-it, tel quel, ou null>", '
    '"reference": "<la référence de dossier lue sur le post-it, de la forme OSM-AAAA-NNNN, ou null>", '
    '"telephone": "<le numéro de téléphone lu sur le post-it (chiffres), ou null>", '
    '"nom": "<le nom de client lu sur le post-it, ou null>", '
    '"meme_appareil": <true si les deux photos montrent le même appareil (même type, même forme, '
    "même couleur, mêmes détails visibles), false si ce sont des appareils différents ou si la photo "
    "2 ne montre pas d'appareil>, "
    '"appareil_commentaire": "<une phrase sur la ressemblance ou la différence>"}. '
    "Ne devine pas : si une information n'est pas lisible, mets null."
)


def _lire_photos_ia(arrivee_url: str, post_it_url: str) -> dict:
    """La lecture brute du modèle sur les deux photos. Le modèle LIT, il ne juge pas la
    concordance avec le dossier : la comparaison se fait ici (un modèle à qui l'on demande
    « est-ce bien OSM-2026-0001 ? » a tendance à dire oui)."""
    images = []
    for url in (arrivee_url, post_it_url):
        contenu, mime = _contenu_image(url)
        if not contenu:
            raise FileNotFoundError(url)
        images.append({"type": "input_image",
                       "image_url": "data:%s;base64,%s" % (mime, base64.b64encode(contenu).decode())})
    client, model = _client_ia()
    res = client.responses.create(
        model=model, instructions=_INSTRUCTIONS_IA,
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": "Photo 1 : appareil à l'arrivée."}, images[0],
            {"type": "input_text", "text": "Photo 2 : le même appareil avec son post-it."}, images[1]]}])
    texte = (res.output_text or "").strip().strip("`")
    if texte.lower().startswith("json"):
        texte = texte.split("\n", 1)[1]
    lu = json.loads(texte)
    return lu if isinstance(lu, dict) else {}


def _client_ia():
    """Même plomberie OpenAI que la caisse et le planning (clé + modèle d'AI Settings)."""
    try:
        from bank_retenue_sync.ai.invoice_extract import _get_client_model_temp
        client, model, _t = _get_client_model_temp()
        return client, model
    except ImportError:
        from customization_app.liste_commande_import import _model, _openai_client
        return _openai_client(), _model()


LECTEUR_IA = _lire_photos_ia     # injectable (tests)


def _normaliser(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", str(texte or ""))
    return "".join(c for c in texte if not unicodedata.combining(c)).upper()


def _chiffres(texte) -> str:
    return re.sub(r"\D", "", str(texte or ""))


def verdict_controle(lu: dict, attendu: dict) -> dict:
    """Confronte la lecture du modèle à ce que le post-it DOIT porter → {ok, problemes}.

    FONCTION PURE ET TOLÉRANTE : le modèle est censé rendre un objet, rien ne l'y oblige.
    - référence : égalité stricte après normalisation (espaces, casse, tirets) — c'est LA donnée
      qui identifie la machine dans l'atelier ;
    - téléphone : un des numéros du dossier doit apparaître dans les chiffres lus (ou l'inverse) ;
    - nom : au moins un mot significatif (≥ 3 lettres) du nom du client, sans accents ni casse ;
    - appareil : le modèle doit voir le même appareil sur les deux photos.
    """
    lu = lu if isinstance(lu, dict) else {}
    problemes = []
    texte_lu = _normaliser(lu.get("post_it_texte"))
    if not lu.get("post_it_visible"):
        problemes.append(_("aucun post-it visible sur la photo 2"))
    ref_att = re.sub(r"[\s-]", "", _normaliser(attendu.get("ref")))
    ref_lue = re.sub(r"[\s-]", "", _normaliser(lu.get("reference")))
    if not ref_lue and ref_att and ref_att in re.sub(r"[\s-]", "", texte_lu):
        ref_lue = ref_att
    if ref_lue != ref_att:
        problemes.append(_("référence lue « {0} » au lieu de {1}").format(lu.get("reference") or "—", attendu.get("ref")))
    numeros = [n for n in (_chiffres(x) for x in re.split(r"[/,;\s]+", str(attendu.get("tel") or ""))) if len(n) >= 6]
    tel_lu = _chiffres(lu.get("telephone")) or _chiffres(texte_lu)
    if numeros and not any(n in tel_lu or (len(tel_lu) >= 6 and tel_lu in n) for n in numeros):
        problemes.append(_("téléphone lu « {0} » ≠ {1}").format(lu.get("telephone") or "—", attendu.get("tel")))
    mots = [m for m in re.findall(r"[A-Z0-9]{3,}", _normaliser(attendu.get("nom"))) if m not in ("STE", "SARL", "SUARL", "LES", "DES")]
    nom_lu = _normaliser(lu.get("nom")) + " " + texte_lu
    if mots and not any(m in nom_lu for m in mots):
        problemes.append(_("nom lu « {0} » ≠ {1}").format(lu.get("nom") or "—", attendu.get("nom")))
    if not lu.get("meme_appareil"):
        problemes.append(_("l'appareil de la photo 2 ne ressemble pas à celui réceptionné ({0})")
                         .format(lu.get("appareil_commentaire") or _("pas de justification")))
    return {"ok": not problemes, "problemes": problemes, "lecture": lu}


@frappe.whitelist()
def controler_photos(machine, doc=None):
    """Relit les deux photos du dossier et compare au post-it attendu. Résultat tracé sur la fiche.

    -> {ok, problemes, message, indisponible}. `indisponible` = le service ou les fichiers n'ont
    pas pu être lus : ce n'est pas un refus, c'est un contrôle qui n'a pas eu lieu."""
    _ecriture(machine)
    doc = doc or frappe.get_doc(DOCTYPE, machine)
    if not (doc.photo_arrivee and doc.photo_post_it):
        frappe.throw(_("Les deux photos sont nécessaires au contrôle."))
    attendu = texte_post_it(doc)
    try:
        lu = LECTEUR_IA(doc.photo_arrivee, doc.photo_post_it)
    except Exception:
        frappe.log_error(title="Réparation osmoseurs : contrôle IA indisponible", message=frappe.get_traceback())
        message = _("Contrôle automatique des photos impossible (service indisponible) : vérifiez le post-it à l'œil.")
        frappe.db.set_value(DOCTYPE, machine, {"controle_ia_ok": 0, "controle_ia_resultat": "⚠️ " + message},
                            update_modified=False)   # le doc en cours de clôture se sauve juste après
        doc.controle_ia_ok, doc.controle_ia_resultat = 0, "⚠️ " + message
        return {"ok": False, "indisponible": True, "problemes": [], "message": message}
    v = verdict_controle(lu, attendu)
    resume = (_("✅ Post-it et appareil conformes") if v["ok"] else "❌ " + " ; ".join(v["problemes"]))
    detail = lu.get("post_it_texte") or ""
    if lu.get("appareil_commentaire"):
        detail += ("\n" if detail else "") + "Appareil : " + lu["appareil_commentaire"]
    frappe.db.set_value(DOCTYPE, machine, {"controle_ia_ok": 1 if v["ok"] else 0,
                                           "controle_ia_resultat": resume + ("\n" + detail if detail else "")},
                        update_modified=False)
    doc.controle_ia_ok = 1 if v["ok"] else 0
    doc.controle_ia_resultat = frappe.db.get_value(DOCTYPE, machine, "controle_ia_resultat")
    v["message"] = resume
    v["indisponible"] = False
    return v


# ── Affectation ──────────────────────────────────────────────────────────────

def _creer_tache(doc, employee: str, jour, rang: int, cfg: dict) -> str:
    creneaux = cfg["creneaux"]
    heure = creneaux[min(rang, len(creneaux) - 1)]
    nom_emp = frappe.db.get_value("Employee", employee, "employee_name") or employee
    garantie = doc.get("garantie") == "Sous garantie"
    titre = "🧰 Réparation atelier: %s%s\nClient: %s\n%s" % (
        doc.name, " 🆓 GARANTIE" if garantie else "", doc.nom_client or doc.client, nom_emp)
    sujet = "Réparation osmoseur %s" % doc.name
    if garantie:
        # En tête du sujet : c'est ce que le technicien lit en ouvrant la tâche, avant de valider.
        sujet = NOTE_GARANTIE + (" (commande %s)" % doc.commande_garantie if doc.get("commande_garantie") else "") + "\n" + sujet
    if doc.note_reception:
        sujet += "\n" + doc.note_reception
    tache = frappe.get_doc({
        "doctype": DOCTYPE_TACHE, "custom_type_dintervention": TYPE_TACHE,
        "custom_choix_du_staff": employee, "custom_employé": nom_emp,
        "custom_client": doc.client, "nom_client": doc.nom_client, "tel": doc.tel,
        "starts_on": "%s %s:00" % (jour, heure), "titre": titre, "subject": sujet,
        "dans_local": "Oui", "status": "Open", CHAMP_TACHE: doc.name,
        "commande_client": doc.get("commande_garantie") if garantie else None,
    })
    tache.flags.ignore_permissions = True
    tache.insert()
    # before_save fixe la fin par type (Réparation = 2 h) : l'atelier, lui, compte 1 h 15 par machine.
    fin = frappe.utils.add_to_date(frappe.utils.get_datetime(tache.starts_on), minutes=cfg["duree_minutes"])
    frappe.db.set_value(DOCTYPE_TACHE, tache.name, "ends_on", fin, update_modified=False)
    return tache.name


@frappe.whitelist()
def planifier(machine, a_partir=None, trace=1):
    """Crée la tâche de réparation du dossier selon la règle du lendemain.

    Idempotent : un dossier qui a déjà une tâche ouverte n'en reçoit pas une seconde.
    Sans responsable configuré ou sans jour libre dans l'horizon, le dossier reste
    « Réceptionnée » et l'écran le signale — rien n'est inventé.
    """
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut in (S_RECEPTION, S_RENDUE):
        frappe.throw(_("Le dossier {0} ne peut pas être planifié ({1}).").format(machine, doc.statut))
    ouverte = frappe.db.get_value(DOCTYPE_TACHE, {CHAMP_TACHE: machine, "status": "Open"}, "name")
    if ouverte:
        return {"tache": ouverte, "deja": True, "message": _("Une tâche est déjà ouverte : {0}").format(ouverte)}
    cfg = config()
    if not cfg["responsables"]:
        return {"tache": None, "message": _("Aucun responsable de réparation configuré "
                                            "(Config Reparation Osmoseur) : dossier à affecter à la main.")}
    debut = getdate(a_partir) if a_partir else add_days(getdate(nowdate()), 1)
    choix = choisir_creneau(debut, cfg)
    if not choix:
        return {"tache": None, "message": _("Aucun responsable disponible dans les {0} prochains jours.")
                                            .format(cfg["horizon_jours"])}
    nom = _creer_tache(doc, choix["employee"], choix["jour"], choix["rang"], cfg)
    synchroniser(machine)
    if cint(trace):      # demandé depuis l'écran (pas la suite automatique d'une réception) : une réponse au rappel
        doc.add_comment("Info", _("📅 Technicien affecté — {0}, le {1} ({2})").format(
            frappe.db.get_value("Employee", choix["employee"], "employee_name") or choix["employee"],
            frappe.format(choix["jour"], {"fieldtype": "Date"}), frappe.utils.get_fullname(frappe.session.user)))
    return {"tache": nom, "employee": choix["employee"], "jour": str(choix["jour"]),
            "employe": frappe.db.get_value("Employee", choix["employee"], "employee_name"),
            "message": _("Tâche {0} créée pour le {1}").format(nom, frappe.format(choix["jour"], {"fieldtype": "Date"}))}


@frappe.whitelist()
def taches_candidates(machine):
    """Les tâches Réparation du calendrier encore ouvertes et sans dossier, celles du client d'abord :
    une réparation déjà planifiée au calendrier se rattache au dossier au lieu d'en recréer une."""
    _lecture()
    client = frappe.db.get_value(DOCTYPE, machine, "client")
    rows = frappe.get_all(DOCTYPE_TACHE,
                          filters={"custom_type_dintervention": TYPE_TACHE, "status": "Open", CHAMP_TACHE: ["in", ["", None]],
                                   "starts_on": [">=", add_days(nowdate(), -30)]},
                          fields=["name", "starts_on", "custom_choix_du_staff", "custom_client", "nom_client", "subject"],
                          order_by="starts_on asc", limit_page_length=200)
    noms = {e.name: e.employee_name for e in frappe.get_all("Employee", filters={"name": ["in", [r.custom_choix_du_staff for r in rows if r.custom_choix_du_staff] or [""]]},
                                                            fields=["name", "employee_name"])}
    out = [{"name": r.name, "date": str(r.starts_on)[:16], "employe": noms.get(r.custom_choix_du_staff, r.custom_choix_du_staff or ""),
            "client": r.nom_client or r.custom_client or "", "meme_client": r.custom_client == client,
            "sujet": (r.subject or "").split("\n")[0][:80]} for r in rows]
    out.sort(key=lambda t: (not t["meme_client"], t["date"]))
    return out


@frappe.whitelist()
def rattacher_tache(machine, tache):
    """Rattache une tâche Réparation EXISTANTE du calendrier au dossier (demande 02/10/2026) : elle devient
    la tâche de réparation du dossier, qui passe « Planifiée » ; sa clôture le validera comme une tâche créée ici."""
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut not in (S_RECEPTIONNEE, S_PLANIFIEE):
        frappe.throw(_("Le dossier {0} ne peut pas recevoir de tâche ({1}).").format(machine, doc.statut))
    t = frappe.get_doc(DOCTYPE_TACHE, tache)
    if t.custom_type_dintervention != TYPE_TACHE:
        frappe.throw(_("{0} n'est pas une tâche de type Réparation ({1}).").format(tache, t.custom_type_dintervention))
    if t.status != "Open":
        frappe.throw(_("{0} n'est plus ouverte ({1}).").format(tache, t.status))
    if t.get(CHAMP_TACHE) and t.get(CHAMP_TACHE) != machine:
        frappe.throw(_("{0} est déjà la tâche du dossier {1}.").format(tache, t.get(CHAMP_TACHE)))
    garantie = doc.garantie == "Sous garantie"
    t.set(CHAMP_TACHE, machine)
    t.dans_local = "Oui"
    if doc.client and not t.custom_client:
        t.custom_client, t.nom_client, t.tel = doc.client, doc.nom_client, doc.tel
    entete = "Réparation osmoseur %s" % machine
    if entete not in (t.subject or ""):
        t.subject = ((NOTE_GARANTIE + "\n") if garantie else "") + entete + ("\n" + t.subject if t.subject else "")
    if garantie and not t.commande_client and doc.get("commande_garantie"):
        t.commande_client = doc.commande_garantie
    t.flags.ignore_permissions = True
    t.save()
    t.add_comment("Comment", _("🔗 Rattachée au dossier de réparation {0} par {1}").format(machine, frappe.utils.get_fullname(frappe.session.user)))
    statut = synchroniser(machine)
    return {"statut": statut, "tache": tache}


@frappe.whitelist()
def detacher_tache(machine, tache):
    """Rattachement par erreur : la tâche reste au calendrier, sans dossier."""
    _ecriture(machine)
    t = frappe.get_doc(DOCTYPE_TACHE, tache)
    if t.get(CHAMP_TACHE) != machine:
        frappe.throw(_("{0} n'est pas rattachée à {1}.").format(tache, machine))
    if t.status == "Completed":
        frappe.throw(_("{0} est clôturée : elle reste la preuve de la réparation.").format(tache))
    t.set(CHAMP_TACHE, None)
    t.flags.ignore_permissions = True
    t.save()
    t.add_comment("Comment", _("🔗 Détachée du dossier de réparation {0}").format(machine))
    return {"statut": synchroniser(machine)}


@frappe.whitelist()
def affecter_tache(machine, employee, date, heure="09:00"):
    """Affectation À LA MAIN d'une tâche supplémentaire (ou de remplacement)."""
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut == S_RECEPTION:
        frappe.throw(_("Clôturez d'abord la réception du dossier {0}.").format(machine))
    if not frappe.db.exists("Employee", {"name": employee, "status": "Active"}):
        frappe.throw(_("Employé inconnu ou inactif."))
    cfg = dict(config(), creneaux=[heure or "09:00"])
    nom = _creer_tache(doc, employee, getdate(date), 0, cfg)
    synchroniser(machine)
    doc.add_comment("Info", _("📅 Technicien affecté — {0}, le {1} ({2})").format(
        frappe.db.get_value("Employee", employee, "employee_name") or employee,
        frappe.format(getdate(date), {"fieldtype": "Date"}), frappe.utils.get_fullname(frappe.session.user)))
    return {"tache": nom}


# ── Cycle de vie ─────────────────────────────────────────────────────────────

def synchroniser(machine: str) -> str:
    """Recalcule le statut du dossier depuis ses tâches. Ne redescend jamais une machine rendue."""
    courant = frappe.db.get_value(DOCTYPE, machine, "statut")
    if not courant or courant in (S_RECEPTION, S_RENDUE):
        return courant
    if courant in EN_RESTITUTION:
        return _synchroniser_livraison(machine, courant)
    # Toute tâche liée au dossier est SA réparation, quel que soit son type : une tâche retypée « Livraison » ou
    # « Autre » puis clôturée laissait le dossier « à affecter » (OSM-2026-0012). Seule la livraison de retour
    # posée par `planifier_livraison` (`tache_livraison`) ne dit rien de l'état de la réparation.
    livraison = frappe.db.get_value(DOCTYPE, machine, "tache_livraison")
    taches = [t for t in frappe.get_all(DOCTYPE_TACHE, filters={CHAMP_TACHE: machine, "status": ["!=", "Cancelled"]},
                                        fields=["name", "status", "custom_choix_du_staff", "starts_on"], order_by="starts_on")
              if t.name != livraison]
    maj = {}
    if not taches:
        nouveau = S_RECEPTIONNEE
        maj.update({"responsable": None, "date_prevue": None, "date_reparee": None})
    elif all(t.status == "Completed" for t in taches):
        nouveau = S_REPAREE
        derniere = taches[-1]
        maj.update({"responsable": derniere.custom_choix_du_staff, "date_prevue": getdate(derniere.starts_on)})
        if courant != S_REPAREE:
            maj["date_reparee"] = now_datetime()
            _proposer_suite(machine)
    else:
        nouveau = S_PLANIFIEE
        prochaine = next(t for t in taches if t.status == "Open")
        maj.update({"responsable": prochaine.custom_choix_du_staff, "date_prevue": getdate(prochaine.starts_on),
                    "date_reparee": None})
    maj["statut"] = nouveau
    if "responsable" in maj:
        # Le nom suit le responsable : posé par set_value, le champ rapatrié gardait l'ancien technicien quand la
        # tâche changeait de main (OSM-2026-0012 affichait Mohamed Hedi pour une réparation faite par Jamel).
        maj["nom_responsable"] = (frappe.db.get_value("Employee", maj["responsable"], "employee_name")
                                  if maj["responsable"] else None)
    frappe.db.set_value(DOCTYPE, machine, maj)
    return nouveau


def _proposer_suite(machine: str):
    """Le dossier vient de passer « à rendre » : celui qui clôture la réparation est encore devant son écran, on lui
    demande tout de suite ce que devient la machine (le client est là / elle attend / à livrer). Un événement temps
    réel, jamais bloquant : manqué, le dossier reste « à rendre » et le rappel du matin le rattrape."""
    try:
        user = frappe.session.user
        if user and user != "Guest" and frappe.has_permission(DOCTYPE, "write", user=user):
            frappe.publish_realtime(EVENEMENT_SUITE, {"machine": machine}, user=user, after_commit=True)
    except Exception:
        pass


def on_tache_change(doc, method=None):
    """Hook on_update / after_delete de la Tache de travail : la clôture valide le dossier."""
    machine = doc.get(CHAMP_TACHE)
    if not machine:
        return
    try:
        synchroniser(machine)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "reparation_osmoseur.on_tache_change")


def _synchroniser_livraison(machine: str, courant: str) -> str:
    """Dossier en restitution par livraison : la tâche Livraison clôturée rend la machine ; disparue ou
    annulée, le dossier revient « Réparée » (à rendre). « Prête au magasin » ne bouge pas tout seul."""
    tache = frappe.db.get_value(DOCTYPE, machine, "tache_livraison")
    if courant != S_LIVRAISON or not tache:
        return courant
    etat = frappe.db.get_value(DOCTYPE_TACHE, tache, ["status", "custom_choix_du_staff"], as_dict=True)
    if etat and etat.status == "Completed":
        nom = frappe.db.get_value("Employee", etat.custom_choix_du_staff, "employee_name") or etat.custom_choix_du_staff or ""
        frappe.db.set_value(DOCTYPE, machine, {"statut": S_RENDUE, "date_rendue": now_datetime(),
                                               "rendu_a": _("livrée par {0}").format(nom)[:140]})
        return S_RENDUE
    if not etat or etat.status == "Cancelled":
        frappe.db.set_value(DOCTYPE, machine, {"statut": S_REPAREE, "tache_livraison": None, "date_livraison_prevue": None,
                                               "mode_restitution": None})
        return S_REPAREE
    return courant


def _sms_simule() -> bool:
    """Le dev porte les vrais numéros : en developer_mode on SIMULE, sauf `sms_reel_en_dev` dans site_config."""
    return bool(cint(frappe.conf.get("developer_mode"))) and not cint(frappe.conf.get("sms_reel_en_dev"))


def _envoyer_sms(doc, texte: str, libelle: str) -> dict:
    """Un SMS au(x) numéro(s) du dossier, tracé en commentaire ; simulé en dev."""
    from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import _send_sms_with_fallback, traiter_numero_tel
    from customization_app.sms_annulation import sans_accents

    numeros = traiter_numero_tel(doc.tel or "")
    if not numeros:
        frappe.throw(_("Aucun numéro mobile tunisien valide sur le dossier ({0}).").format(doc.tel or "—"))
    texte = sans_accents(texte)
    simule = _sms_simule()
    if not simule:
        _send_sms_with_fallback(["216%s" % n for n in numeros], texte)
    doc.db_set("sms_pret_le", now_datetime(), update_modified=False)
    doc.add_comment("Comment", _("📲 SMS « {0} » {1} à {2} : {3}")
                    .format(libelle, _("SIMULÉ (dev)") if simule else _("envoyé"), ", ".join(numeros), texte))
    return {"numeros": numeros, "simule": simule, "texte": texte}


@frappe.whitelist()
def envoyer_sms_pret(machine):
    """SMS « votre appareil est réparé, à retirer au magasin » au(x) numéro(s) du dossier."""
    from customization_app.sms_annulation import telephone_contact

    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut not in (S_REPAREE, S_PRETE):
        frappe.throw(_("Le SMS « machine prête » ne s'envoie que pour une machine réparée ({0}).").format(doc.statut))
    return _envoyer_sms(doc, SMS_PRET.format(nom=doc.nom_client or doc.client, ref=doc.name, tel=telephone_contact(),
                                             garantie=" Reparation hors garantie : a regler au retrait." if doc.garantie == "Hors garantie" else ""),
                        "machine prête")


@frappe.whitelist()
def restituer_magasin(machine, sms=0):
    """Le client viendra la chercher : « Prête au magasin », SMS proposé."""
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut != S_REPAREE:
        frappe.throw(_("Seule une machine réparée se met en attente au magasin ({0}).").format(doc.statut))
    doc.statut, doc.mode_restitution = S_PRETE, "Retrait au magasin"
    doc.save()
    out = {"statut": doc.statut, "sms": None}
    if cint(sms):
        out["sms"] = envoyer_sms_pret(machine)
    return out


@frappe.whitelist()
def adresses_client(client):
    """Les adresses du client, pour choisir où livrer."""
    _lecture()
    noms = frappe.get_all("Dynamic Link", filters={"link_doctype": "Customer", "link_name": client, "parenttype": "Address"},
                          pluck="parent", distinct=True)
    out = []
    for a in frappe.get_all("Address", filters={"name": ["in", noms or [""]], "disabled": 0},
                            fields=["name", "address_title", "address_line1", "address_line2", "city", "custom_secteur",
                                    "custom_lien_google_map", "is_shipping_address", "is_primary_address"]):
        a["libelle"] = ", ".join(x for x in (a.address_line1, a.address_line2, a.city) if x)
        out.append(a)
    out.sort(key=lambda a: (not a.is_shipping_address, not a.is_primary_address, a.name))
    return out


@frappe.whitelist()
def planifier_livraison(machine, employee, date, heure="09:00", adresse=None, note=None, sms=0):
    """Une tâche Livraison au calendrier ramène la machine chez le client ; sa clôture rend le dossier.
    `sms` : prévenir le client (date et livreur)."""
    from customization_app.sms_annulation import telephone_contact

    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut not in (S_REPAREE, S_PRETE):
        frappe.throw(_("Seule une machine réparée se livre ({0}).").format(doc.statut))
    if not frappe.db.exists("Employee", {"name": employee, "status": "Active"}):
        frappe.throw(_("Employé inconnu ou inactif : {0}").format(employee))
    adresses = adresses_client(doc.client)
    a = next((x for x in adresses if x.name == adresse), None) if adresse else (adresses[0] if adresses else None)
    nom_emp = frappe.db.get_value("Employee", employee, "employee_name") or employee
    garantie = doc.garantie == "Sous garantie"
    sujet = "Retour de l'osmoseur réparé %s%s" % (doc.name, " — " + NOTE_GARANTIE if garantie else " — hors garantie : encaisser la réparation à la livraison")
    if note:
        sujet += "\n" + note.strip()[:300]
    tache = frappe.get_doc({
        "doctype": DOCTYPE_TACHE, "custom_type_dintervention": TYPE_LIVRAISON,
        "custom_choix_du_staff": employee, "custom_employé": nom_emp,
        "custom_client": doc.client, "nom_client": doc.nom_client, "tel": doc.tel,
        "starts_on": "%s %s:00" % (getdate(date), str(heure or "09:00")[:5]),
        "titre": "%s\n🚚 Livraison: Client: %s\n%s" % ((a.custom_secteur if a and a.custom_secteur else ""), doc.nom_client or doc.client, nom_emp),
        "subject": sujet, "temps": "30 min", "status": "Open", CHAMP_TACHE: doc.name,
        "select_address": a.name if a else None, "details_adresse": (a.libelle if a else "")[:140],
        "secteur": a.custom_secteur if a else None, "google_map": (a.custom_lien_google_map if a else "") or "",
        "commande_client": doc.get("commande_garantie") if garantie else None,
    })
    tache.flags.ignore_permissions = True
    tache.flags.duree_fixee = True
    tache.insert()
    fin = frappe.utils.add_to_date(frappe.utils.get_datetime(tache.starts_on), minutes=DUREE_LIVRAISON)
    frappe.db.set_value(DOCTYPE_TACHE, tache.name, "ends_on", fin, update_modified=False)
    doc.reload()
    doc.statut, doc.mode_restitution = S_LIVRAISON, "Livraison"
    doc.tache_livraison, doc.date_livraison_prevue = tache.name, getdate(date)
    doc.save()
    out = {"statut": doc.statut, "tache": tache.name, "employe": nom_emp, "date": str(getdate(date)),
           "adresse": a.libelle if a else None, "sms": None}
    if cint(sms):
        out["sms"] = _envoyer_sms(doc, SMS_LIVRAISON.format(
            nom=doc.nom_client or doc.client, ref=doc.name, date=frappe.utils.formatdate(getdate(date), "dd/MM/yyyy"),
            employe=nom_emp, tel=telephone_contact(),
            garantie=" Reparation hors garantie : a regler a la livraison." if doc.garantie == "Hors garantie" else ""), "livraison")
    return out


@frappe.whitelist()
def situation_sortie(machine):
    """Ce que le dialogue « Rendre au client » montre : le dossier, les commandes du client depuis la réception avec
    leur règlement réel (c'est parmi elles qu'on désigne celle de la réparation), et ce qu'il faut pour fermer."""
    _lecture()
    m = frappe.db.get_value(DOCTYPE, machine, ["name", "client", "nom_client", "tel", "statut", "garantie", "commande_garantie",
                                               "commande_client", "date_reception", "photo_remise"], as_dict=True)
    if not m:
        frappe.throw(_("Dossier introuvable."))
    commandes = []
    if frappe.has_permission("Sales Order", "read"):
        commandes = frappe.get_all("Sales Order", filters={"customer": m.client, "docstatus": 1,
                                                            "transaction_date": [">=", getdate(m.date_reception or nowdate())]},
                                   fields=["name", "transaction_date"], order_by="transaction_date desc, creation desc",
                                   limit_page_length=12)
        if m.commande_client and m.commande_client not in [c.name for c in commandes]:
            liee = frappe.db.get_value("Sales Order", m.commande_client, ["name", "transaction_date"], as_dict=True)
            if liee:
                commandes.insert(0, liee)
        regs = reglements([c.name for c in commandes])
        articles = {}
        for l in frappe.get_all("Sales Order Item", filters={"parent": ["in", [c.name for c in commandes] or [""]]},
                                fields=["parent", "item_name", "qty"], order_by="parent, idx"):
            articles.setdefault(l.parent, []).append("%g× %s" % (l.qty, l.item_name))
        for c in commandes:
            c.update(regs.get(c.name) or {"total": 0.0, "reste": 0.0, "etat": "zero"})
            c["articles"] = ", ".join(articles.get(c.name, [])[:4]) + (" …" if len(articles.get(c.name, [])) > 4 else "")
    repare = m.statut in REPAREE
    return {"machine": m, "repare": repare, "commandes": commandes,
            "regle": regle_sortie(repare, m.garantie, bool(m.commande_client), bool(m.commande_garantie)),
            "motifs": list(MOTIFS_NON_FACTUREE if repare else MOTIFS_SANS_REPARATION)}


def _exiger_code(code, machine: str, raison: str):
    if not code:
        frappe.throw(_("Code superviseur requis : {0}.").format(raison))
    if not _verifier_code(code):
        frappe.log_error("Code superviseur refusé — dossier %s, utilisateur %s" % (machine, frappe.session.user),
                         "reparation_osmoseur")
        frappe.throw(_("Code incorrect."))


def _fermer(doc, rendu_a, remarque, reglement: str, motif=None, sans_reparation=0):
    doc.add_comment("Info", (_("↪️ Sortie sans réparation — {0}").format((motif or "").strip()) if sans_reparation
                             else _("✅ Rendue au client — {0}").format(reglement or ""))
                    + " (" + frappe.utils.get_fullname(frappe.session.user) + ")")
    doc.statut = S_RENDUE
    doc.date_rendue = now_datetime()
    doc.rendue_par = frappe.utils.get_fullname(frappe.session.user)[:140]
    doc.reglement_sortie = (reglement or "")[:140]
    doc.motif_sortie = (motif or "").strip()[:300] or None
    doc.sans_reparation = 1 if sans_reparation else 0
    doc.rappel_depuis = None
    if rendu_a:
        doc.rendu_a = rendu_a.strip()[:140]
    if remarque:
        doc.remarque_restitution = remarque.strip()[:500]
    doc.save()


@frappe.whitelist()
def rendre(machine, rendu_a=None, remarque=None, commande=None, sans_commande=0, commande_garantie=None,
           motif=None, code=None):
    """La machine réparée SORT (remise en main propre). Le règlement ne bloque pas : il est constaté et gardé
    sur le dossier (payée / reste dû / garantie). Ce qu'il faut selon le cas : voir `regle_sortie`.
    `commande` : la commande de la réparation, à lier ; `sans_commande` : aucune vente pour cette réparation."""
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut not in REPAREE:
        frappe.throw(_("Seule une machine réparée peut être rendue ({0}).").format(doc.statut))
    if cint(sans_commande):
        doc.commande_client = None
    elif commande:
        so = frappe.db.get_value("Sales Order", commande, ["customer", "docstatus"], as_dict=True)
        if not so or so.docstatus != 1:
            frappe.throw(_("La commande {0} n'est pas validée.").format(commande))
        if so.customer != doc.client:
            frappe.throw(_("La commande {0} n'est pas celle de ce client.").format(commande))
        doc.commande_client = commande
    if commande_garantie:
        _verifier_garantie(doc.garantie, commande_garantie, doc.client)
        doc.commande_garantie = commande_garantie
    regle = regle_sortie(True, doc.garantie, bool(doc.commande_client), bool(doc.commande_garantie))
    if regle["motif"] and not (motif or "").strip():
        frappe.throw(_("Hors garantie et aucune vente : indiquez pourquoi la réparation n'est pas facturée."))
    if regle["code"]:
        _exiger_code(code, machine, _("sous garantie sans commande d'origine") if regle["cas"] == "garantie_sans_preuve"
                     else _("réparation hors garantie rendue sans aucune vente"))
        doc.add_comment("Info", _("🔓 Rendue {0} par {1} (code superviseur){2}").format(
            _("sous garantie sans commande d'origine") if regle["cas"] == "garantie_sans_preuve" else _("sans aucune vente"),
            frappe.utils.get_fullname(frappe.session.user), (" — " + motif.strip()) if (motif or "").strip() else ""))
    reg = reglements([doc.commande_client]).get(doc.commande_client) if doc.commande_client else None
    doc.mode_restitution = doc.mode_restitution or "Retrait au magasin"
    _fermer(doc, rendu_a, remarque, libelle_reglement(reg, doc.garantie), motif=motif if regle["motif"] else None)
    return {"statut": doc.statut, "reglement": doc.reglement_sortie, "cas": regle["cas"]}


@frappe.whitelist()
def sortir_sans_reparation(machine, motif, rendu_a=None, remarque=None):
    """La machine repart SANS avoir été réparée (le client la reprend, devis refusé, réception par erreur…).
    Le dossier se ferme avec son motif au lieu de rester « à affecter » pour toujours ; les tâches encore
    ouvertes sont supprimées (comme à la réouverture d'une réception : elles n'ont jamais eu lieu)."""
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut not in (S_RECEPTIONNEE, S_PLANIFIEE):
        frappe.throw(_("Sortie sans réparation impossible pour un dossier « {0} ».").format(doc.statut))
    if not (motif or "").strip():
        frappe.throw(_("Indiquez pourquoi la machine repart sans réparation."))
    livraison = doc.tache_livraison
    faites = [t for t in frappe.get_all(DOCTYPE_TACHE, filters={CHAMP_TACHE: machine, "status": "Completed"}, pluck="name")
              if t != livraison]
    if faites:
        frappe.throw(_("Une réparation est déjà clôturée sur ce dossier ({0}) : terminez ou annulez les tâches "
                       "restantes, puis rendez la machine normalement.").format(", ".join(faites)))
    for t in frappe.get_all(DOCTYPE_TACHE, filters={CHAMP_TACHE: machine, "status": "Open"}, pluck="name"):
        frappe.delete_doc(DOCTYPE_TACHE, t, ignore_permissions=True, force=True)
    doc.reload()
    doc.responsable, doc.date_prevue = None, None
    _fermer(doc, rendu_a, remarque, "sans réparation", motif=motif, sans_reparation=1)
    return {"statut": doc.statut}


@frappe.whitelist()
def annuler_sortie(machine):
    """« Rendue » cliqué par erreur : le dossier reprend l'état que disent ses tâches. Une livraison clôturée,
    elle, fait foi — on ne revient pas dessus."""
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut != S_RENDUE:
        frappe.throw(_("Le dossier {0} n'est pas rendu ({1}).").format(machine, doc.statut))
    if doc.tache_livraison and frappe.db.get_value(DOCTYPE_TACHE, doc.tache_livraison, "status") == "Completed":
        frappe.throw(_("La livraison {0} est clôturée : la machine a bien été rendue.").format(doc.tache_livraison))
    doc.statut = S_REPAREE if doc.date_reparee and not doc.sans_reparation else S_RECEPTIONNEE
    doc.update({"date_rendue": None, "rendu_a": None, "rendue_par": None, "reglement_sortie": None, "motif_sortie": None,
                "sans_reparation": 0, "mode_restitution": None, "toujours_la_le": now_datetime()})
    doc.save()
    doc.add_comment("Info", _("↩️ Sortie annulée par {0}").format(frappe.utils.get_fullname(frappe.session.user)))
    return {"statut": synchroniser(machine)}


@frappe.whitelist()
def toujours_la(machine):
    """Réponse au rappel : la machine est toujours chez nous. Le compteur d'attente repart de zéro."""
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut in (S_RECEPTION, S_RENDUE):
        frappe.throw(_("Rien à confirmer pour un dossier « {0} ».").format(doc.statut))
    frappe.db.set_value(DOCTYPE, machine, {"toujours_la_le": now_datetime(), "rappel_depuis": None})
    doc.add_comment("Info", _("📍 Toujours au magasin — confirmé par {0}").format(frappe.utils.get_fullname(frappe.session.user)))
    return {"ok": True}


@frappe.whitelist()
def machines_du_client(client):
    """Les machines de ce client réparées et encore chez nous — pour le bandeau de sa commande ou de son BL.
    Sans droit sur les dossiers : liste vide, jamais d'erreur (le bandeau est un rappel, pas une fonction)."""
    if not client or not frappe.has_permission(DOCTYPE, "read"):
        return []
    return frappe.get_all(DOCTYPE, filters={"client": client, "statut": ["in", list(REPAREE)]},
                          fields=["name", "statut", "garantie", "date_reparee", "commande_client"], order_by="creation")


# ── Rappel du matin ──────────────────────────────────────────────────────────

LIBELLES_ATTENTE = {"a_rendre": "réparée, chez nous depuis {0} j", "sans_technicien": "reçue, sans technicien depuis {0} j",
                    "livraison": "livraison prévue il y a {0} j, pas clôturée"}


def role_suivi(user=None, cfg=None):
    """« suivi » pour la personne qui reçoit le rappel, « relance » pour celle qu'on relance, None pour les autres."""
    cfg = cfg or config()
    user = user or frappe.session.user
    return "suivi" if user and user == cfg["suivi"] else "relance" if user and user == cfg["relance"] else None


def machines_a_regarder(user=None, tout=False) -> list | None:
    """Les dossiers que CET utilisateur doit regarder, les plus anciens d'abord : pour la personne du suivi, tout ce
    qui attend depuis trop longtemps ; pour la personne relancée, ce qui est resté sans réponse. None : pas concerné.
    `tout` : la personne relancée voit elle aussi TOUT ce qui attend — c'est le compte de sa file « Machines de
    l'atelier » (elle y lisait 0 tant qu'aucun rappel n'était resté sans réponse, alors que 11 machines attendaient) ;
    l'alerte qui s'impose à son écran, elle, reste limitée à ce qui est resté sans réponse."""
    cfg = config()
    role = role_suivi(user, cfg)
    if not role:
        return None
    machines = frappe.get_all(DOCTYPE, filters={"statut": ["in", [S_RECEPTIONNEE, S_REPAREE, S_PRETE, S_LIVRAISON]]},
                              fields=CHAMPS_ATTENTE, order_by="creation")
    tri = a_rappeler(machines, getdate(nowdate()), cfg["rappel_jours"], cfg["relance_jours"])
    return tri["rappel"] if role == "suivi" or tout else tri["relance"]


@frappe.whitelist()
def mon_rappel():
    """Pour l'alerte à l'écran et la file « Machines de l'atelier » de « Mes appels par priorité » : ce que
    l'utilisateur connecté doit regarder. null pour qui n'est ni la personne du suivi ni celle de la relance."""
    machines = machines_a_regarder()
    if machines is None:
        return None
    cfg = config()
    return {"role": role_suivi(cfg=cfg), "n": len(machines), "rappel_jours": cfg["rappel_jours"],
            "suivi": frappe.utils.get_fullname(cfg["suivi"]) if cfg["suivi"] else "",
            "machines": [{"name": m["name"], "client": m.get("nom_client") or m.get("client") or "",
                          "jours": m["attente"]["jours"], "motif": m["attente"]["motif"],
                          "texte": LIBELLES_ATTENTE[m["attente"]["motif"]].format(m["attente"]["jours"])} for m in machines]}


def composer_rappel(machines, url, relance_de=None) -> str:
    """Le message du matin (markdown Raven). PURE. `relance_de` : nom de la personne restée sans réponse."""
    n = len(machines)
    tete = ("🔁 **Réparation osmoseurs — %d machine(s) sans réponse%s**" % (n, " de " + relance_de if relance_de else "")
            if relance_de is not None else "⏳ **Réparation osmoseurs — %d machine(s) attendent une réponse**" % n)
    lignes = ["- **%s** · %s — %s" % (m["name"], m.get("nom_client") or m.get("client") or "",
                                       LIBELLES_ATTENTE[m["attente"]["motif"]].format(m["attente"]["jours"]))
              for m in machines]
    pied = "Pour chacune : « Rendre », « Toujours au magasin » ou « Affecter » → [ouvrir l'écran](%s)" % url
    return "\n".join([tete, ""] + lignes + ["", pied])


def _notifier(user: str, texte: str, sujet: str) -> str:
    """Message privé Raven (bot), à défaut la cloche du Desk. -> le canal utilisé. Jamais bloquant."""
    try:
        if frappe.db.exists("DocType", "Raven Bot") and frappe.db.exists("Raven User", {"user": user}):
            if not frappe.db.exists("Raven Bot", BOT):
                frappe.get_doc({"doctype": "Raven Bot", "bot_name": BOT}).insert(ignore_permissions=True)
            frappe.get_doc("Raven Bot", BOT).send_direct_message(user, text=texte, markdown=True)
            return "raven"
    except Exception:
        frappe.log_error(frappe.get_traceback(), "reparation_osmoseur : rappel Raven")
    try:
        frappe.get_doc({"doctype": "Notification Log", "for_user": user, "type": "Alert", "subject": sujet,
                        "email_content": texte, "link": PAGE + "?verifier=1"}).insert(ignore_permissions=True)
        return "cloche"
    except Exception:
        frappe.log_error(frappe.get_traceback(), "reparation_osmoseur : rappel")
        return ""


def rappel_quotidien(envoyer=1):
    """Cron du matin : UN message à la personne du réglage (« Suivi des restitutions ») avec les machines qui
    attendent depuis trop longtemps ; celles restées sans réponse sont relancées à la seconde personne.
    Sans destinataire réglé : rien. `envoyer=0` : seulement le tri (essai). En developer_mode rien ne part."""
    cfg = config()
    aujourdhui = getdate(nowdate())
    machines = frappe.get_all(DOCTYPE, filters={"statut": ["in", [S_RECEPTIONNEE, S_REPAREE, S_PRETE, S_LIVRAISON]]},
                              fields=CHAMPS_ATTENTE, order_by="creation")
    tri = a_rappeler(machines, aujourdhui, cfg["rappel_jours"], cfg["relance_jours"])
    out = {"rappel": [m["name"] for m in tri["rappel"]], "relance": [m["name"] for m in tri["relance"]],
           "suivi": cfg["suivi"], "relance_a": cfg["relance"], "envoyes": []}
    if not cint(envoyer) or not cfg["suivi"] or not tri["rappel"]:
        return out
    # Le premier rappel est daté ici : à partir de lui, la machine restée sans réponse passe à la personne relancée
    # (file, alerte et message). Daté même quand l'envoi est muet : la relance ne dépend pas du canal.
    for m in tri["rappel"]:
        if not m.get("rappel_depuis"):
            frappe.db.set_value(DOCTYPE, m["name"], "rappel_depuis", aujourdhui, update_modified=False)
    if _sms_simule() and cint(envoyer) != 2:
        # Le dev est une copie de la prod, avec ses vrais comptes Raven : on n'y écrit à personne de soi-même
        # (même règle que les SMS). `envoyer=2` force l'envoi, pour un essai voulu.
        out["simule"] = True
        frappe.db.commit()
        return out
    url = frappe.utils.get_url(PAGE + "?verifier=1")
    sujet = "⏳ Réparation osmoseurs : %d machine(s) attendent une réponse" % len(tri["rappel"])
    out["envoyes"].append((cfg["suivi"], _notifier(cfg["suivi"], composer_rappel(tri["rappel"], url), sujet)))
    if tri["relance"] and cfg["relance"] and cfg["relance"] != cfg["suivi"]:
        texte = composer_rappel(tri["relance"], url, relance_de=frappe.utils.get_fullname(cfg["suivi"]))
        out["envoyes"].append((cfg["relance"], _notifier(cfg["relance"], texte, "🔁 " + sujet)))
    frappe.db.commit()
    return out


@frappe.whitelist()
def annuler_restitution(machine):
    """Retour à « Réparée » (à rendre) : la tâche de livraison encore ouverte est supprimée."""
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut not in EN_RESTITUTION:
        frappe.throw(_("Le dossier {0} n'est pas en restitution ({1}).").format(machine, doc.statut))
    if doc.tache_livraison and frappe.db.exists(DOCTYPE_TACHE, doc.tache_livraison):
        if frappe.db.get_value(DOCTYPE_TACHE, doc.tache_livraison, "status") == "Completed":
            frappe.throw(_("La livraison {0} est déjà clôturée.").format(doc.tache_livraison))
        frappe.delete_doc(DOCTYPE_TACHE, doc.tache_livraison, ignore_permissions=True, force=True)
    doc.reload()
    doc.statut, doc.mode_restitution, doc.tache_livraison, doc.date_livraison_prevue = S_REPAREE, None, None, None
    doc.save()
    return {"statut": doc.statut}


@frappe.whitelist()
def rouvrir_reception(machine):
    """Une réception clôturée par erreur, sans tâche encore faite, revient en réception."""
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut not in (S_RECEPTIONNEE, S_PLANIFIEE):
        frappe.throw(_("Impossible de rouvrir un dossier {0}.").format(doc.statut))
    # Le refus d'une clôture par le contrôle IA ne laisse rien à nettoyer ; une réouverture, si.
    if frappe.db.exists(DOCTYPE_TACHE, {CHAMP_TACHE: machine, "status": "Completed"}):
        frappe.throw(_("Une tâche de réparation est déjà clôturée sur ce dossier."))
    # Les tâches ouvertes sont SUPPRIMÉES, pas annulées (décision 01/10/2026) : une tâche annulée
    # resterait au calendrier du technicien et dans ses statistiques alors qu'elle n'a jamais existé.
    for t in frappe.get_all(DOCTYPE_TACHE, filters={CHAMP_TACHE: machine, "status": "Open"}, pluck="name"):
        frappe.delete_doc(DOCTYPE_TACHE, t, ignore_permissions=True, force=True)
    doc.reload()
    doc.statut = S_RECEPTION
    doc.date_cloture_reception = None
    doc.responsable = None
    doc.date_prevue = None
    doc.save()
    return {"statut": doc.statut}
