"""Calendrier d'équipe — la page /app/calendrier-equipe (21/11/2026).

Demande : « ajouter un module meilleur que le calendrier de Frappe… mais je veux que mes fonctionnalités ne soient
pas perdues ». Le calendrier de Frappe (vue Calendrier de la liste « Tache de travail ») repose sur FullCalendar 3 ;
on n'y touche pas. Cette page, À CÔTÉ, affiche les mêmes données avec une bibliothèque récente (EventCalendar, MIT,
dans public/js/lib/event-calendar) : une colonne par employé, glisser une tâche d'une heure ou d'un employé à l'autre.

Rien n'est recalculé ici :
- les événements sont ceux de l'ancien calendrier, `api.get_custom_tache_events` — mêmes droits (frappe.get_list),
  mêmes tâches, réunions du suivi d'activité, jours fériés, ligne Aramex, couleurs ;
- un déplacement enregistre la tâche avec les droits de l'utilisateur, comme `frappe.desk.calendar.update_event`
  (les hooks de la tâche jouent) ; un changement d'employé suit les refus de « Gérer les interventions »
  (`taches_groupe.refus_transfert` : terminée, annulée, congé / récupération, employé fixe) et laisse le même
  commentaire ;
- une réunion se déplace par `reunion_activite.deplacer`, appelé par la page comme le fait l'ancien calendrier.

Les fonctions du haut sont pures (sans site) : tests/test_calendrier_equipe.py.
"""
from __future__ import annotations

import datetime

import frappe
from frappe import _
from frappe.utils import cint

DOCTYPE = "Tache de travail"
REUNION = "Reunion Activite"
SANS_EMPLOYE = "__aucun"          # la colonne « Non affecté »
FORMAT = "%Y-%m-%d %H:%M:%S"

# Ce que l'info-bulle de l'ancien calendrier montrait, repris tel quel
CHAMPS_BULLE = ("custom_type_dintervention", "custom_employé", "nom_client", "tel", "secteur", "info_secteur",
                "details_adresse", "google_map", "subject", "raison_annulation", "rapport_visite")


def _dt(valeur):
    """Date/heure en objet datetime, depuis ce que rend la base ou une chaîne. PURE."""
    if isinstance(valeur, datetime.datetime):
        return valeur
    if isinstance(valeur, datetime.date):
        return datetime.datetime(valeur.year, valeur.month, valeur.day)
    texte = str(valeur or "").strip()
    if not texte:
        return None
    for forme in ("%Y-%m-%d %H:%M:%S.%f", FORMAT, "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(texte, forme)
        except ValueError:
            continue
    return None


def bornes(debut, fin, journee: bool) -> tuple:
    """(début, fin) à donner au calendrier, en texte. PURE.

    Une tâche « toute la journée » occupe des JOURS entiers : du matin du premier jour au lendemain du dernier (fin
    exclue, la convention des calendriers). Une tâche horaire garde ses heures ; sans fin, ou fin avant le début,
    elle dure une heure pour rester visible."""
    d, f = _dt(debut), _dt(fin)
    if not d:
        return None, None
    if journee:
        d0 = datetime.datetime(d.year, d.month, d.day)
        dernier = f if f and f >= d else d
        # une fin à 00:00 pile marque déjà le lendemain exclu (jours fériés) — sauf si c'est le jour même
        if dernier.time() == datetime.time(0, 0) and dernier.date() > d.date():
            f0 = datetime.datetime(dernier.year, dernier.month, dernier.day)
        else:
            f0 = datetime.datetime(dernier.year, dernier.month, dernier.day) + datetime.timedelta(days=1)
        return d0.strftime(FORMAT), f0.strftime(FORMAT)
    if not f or f <= d:
        f = d + datetime.timedelta(hours=1)
    return d.strftime(FORMAT), f.strftime(FORMAT)


def vers_calendrier(e: dict, participants: list | None = None) -> dict | None:
    """Un événement de l'ancien calendrier (`get_custom_tache_events`) sous la forme que lit la page. PURE.
    `participants` : les employés d'une réunion (elle s'affiche dans la colonne de chacun)."""
    ferie, fond, reunion = bool(e.get("ferie")), bool(e.get("fond")), e.get("doctype") == REUNION
    journee = bool(cint(e.get("all_day"))) or (ferie and not fond)
    debut, fin = bornes(e.get("starts_on"), e.get("ends_on"), journee)
    if not debut:
        return None
    doctype = e.get("doctype") or DOCTYPE
    if ferie:
        genre = "fond" if fond else "ferie"
    else:
        genre = "reunion" if reunion else "tache"
    out = {
        "id": "%s|%s" % (doctype, e.get("name")), "doctype": doctype, "name": e.get("name"), "genre": genre,
        "debut": debut, "fin": fin, "journee": journee,
        "titre": e.get("titre") or "", "couleur": e.get("color") or "", "statut": e.get("status") or "",
        "employes": [], "reservation_app": cint(e.get("custom_reservation_app")),
        "heure_fixe": cint(e.get("custom_tournee_fixe")), "employe_fixe": cint(e.get("custom_employe_fixe")),
    }
    if genre == "tache":
        out["employes"] = [e.get("custom_choix_du_staff") or SANS_EMPLOYE]
        # le nom du client, écrit sur la tuile avec le secteur
        client = (e.get("nom_client") or e.get("custom_client") or "").strip()
        if client:
            out["client"] = client
        # Livraison confiée à Aramex : `api._annoter_aramex` a glissé la ligne « 📦 Aramex <bordereau> · client » dans le
        # titre — la page en fait une pastille sur la tuile (22/11/2026).
        if any(ligne.strip().startswith("📦 Aramex") for ligne in str(e.get("titre") or "").split("\n")):
            out["aramex"] = 1
    elif genre == "reunion":
        out["employes"] = [p for p in (participants or []) if p]
    for champ in CHAMPS_BULLE:
        if e.get(champ):
            out[champ] = e.get(champ)
    return out


def colonnes(actifs: list, evenements: list, noms: dict | None = None) -> list:
    """Les colonnes de la vue « Équipe » : tous les employés actifs, puis ceux qui ne le sont plus mais gardent une
    tâche dans la période (marqués « parti »), puis « Non affecté » s'il y en a. PURE.
    `actifs` : [{id, nom, couleur}] ; `noms` : {employé: nom} pour ceux qui ne sont pas actifs."""
    out = [dict(a, actif=1) for a in actifs]
    connus = {a["id"] for a in actifs}
    autres, sans = [], False
    for ev in evenements:
        if ev.get("genre") != "tache":
            continue
        for emp in ev.get("employes") or []:
            if emp == SANS_EMPLOYE:
                sans = True
            elif emp not in connus and emp not in autres:
                autres.append(emp)
    for emp in sorted(autres, key=lambda x: ((noms or {}).get(x) or x).lower()):
        out.append({"id": emp, "nom": (noms or {}).get(emp) or emp, "couleur": "#9ca3af", "actif": 0})
    if sans:
        out.append({"id": SANS_EMPLOYE, "nom": "Non affecté", "couleur": "#9ca3af", "actif": 0})
    return out


def decaler_jours(debut, fin, jours: int) -> tuple:
    """Une tâche « toute la journée » glissée de `jours` jours : mêmes heures, autres jours. PURE."""
    d, f = _dt(debut), _dt(fin)
    delta = datetime.timedelta(days=cint(jours))
    return (d + delta if d else None), (f + delta if f else None)


# ── Lecture ──────────────────────────────────────────────────────────────────

def _actifs() -> list:
    from customization_app import api

    out = []
    for e in frappe.get_all("Employee", filters={"status": "Active"}, fields=["name", "employee_name"],
                            order_by="employee_name"):
        couleur = api.compute_tache_color({"custom_choix_du_staff": e.name, "status": "Open", "owner": ""})
        out.append({"id": e.name, "nom": e.employee_name or e.name, "couleur": couleur})
    return out


def _exterieur() -> bool:
    """Le partenaire ou un sous-traitant : ils ne lisent que LEURS tâches (hooks de permission de la tâche) ; on ne
    leur montre donc pas non plus la liste de tous les employés en colonnes — seulement ceux de ce qu'ils voient."""
    from customization_app import api, sous_traitant

    if frappe.session.user == api.PARTNER_USER:
        return True
    return bool(sous_traitant.sous_traitant_courant(frappe.session.user)) and not sous_traitant._est_supervision()


@frappe.whitelist()
def contexte() -> dict:
    """Ce que la page doit savoir avant d'afficher : les droits de l'utilisateur et les listes des filtres."""
    frappe.has_permission(DOCTYPE, "read", throw=True)
    from customization_app import api

    meta = frappe.get_meta(DOCTYPE)
    types = [t for t in (meta.get_field("custom_type_dintervention").options or "").split("\n") if t.strip()]
    try:
        from customization_app.tournee_optimisation import ROLES as roles_optimiser
    except Exception:
        roles_optimiser = ()
    return {
        "employes": [] if _exterieur() else _actifs(),
        "types": types,
        # les icônes des titres de tâche (api.composer_titre), pour la ligne « 09:30 – 10:00 · 🔧 Entretien »
        "icones": {k: v.strip() for k, v in api.ICONES_INTERVENTION.items()},
        "sans_employe": SANS_EMPLOYE,
        "droits": {
            "ecrire": bool(frappe.has_permission(DOCTYPE, "write")),
            "creer": bool(frappe.has_permission(DOCTYPE, "create")),
            # mêmes règles que les boutons de l'ancien calendrier
            "generer_bl": frappe.session.user != api.PARTNER_USER,
            "optimiser": bool(set(roles_optimiser) & set(frappe.get_roles())),
        },
    }


def poser_annotations(evenements: list, etapes: dict, reprises: dict) -> list:
    """Pose sur les tâches ce que deux autres outils savent d'elles. PURE.
    `etapes` : {tâche: {...}} — où en est la vérification de stock que porte la tâche (elle n'est verte qu'à
    l'accord de l'employé et du responsable). `reprises` : {tâche: {...}} — le dossier de réparation né de cette
    intervention, quand le technicien est reparti avec la machine : ce n'est pas une annulation ordinaire."""
    for ev in evenements:
        if ev.get("genre") != "tache":
            continue
        if ev.get("statut") == "Open" and ev.get("name") in etapes:
            ev["verification"] = etapes[ev["name"]]
        if ev.get("statut") == "Cancelled" and ev.get("name") in reprises:
            ev["reprise"] = reprises[ev["name"]]
    return evenements


def annoter(evenements: list) -> list:
    """Les annotations lues en base : vérifications de stock ouvertes et machines reprises à l'atelier. Jamais
    bloquant : le calendrier s'affiche même si l'un des deux outils échoue."""
    ouvertes = [ev["name"] for ev in evenements if ev.get("genre") == "tache" and ev.get("statut") == "Open"]
    annulees = [ev["name"] for ev in evenements if ev.get("genre") == "tache" and ev.get("statut") == "Cancelled"]
    etapes, reprises = {}, {}
    try:
        from customization_app import stock_entrepots
        etapes = stock_entrepots.etapes_des_taches(ouvertes)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "calendrier_equipe : étapes des vérifications")
    try:
        from customization_app import reparation_osmoseur
        reprises = reparation_osmoseur.reprises_des_taches(annulees)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "calendrier_equipe : machines reprises")
    return poser_annotations(evenements, etapes, reprises)


@frappe.whitelist()
def evenements(start, end) -> dict:
    """Les événements de la période et les colonnes de la vue « Équipe »."""
    frappe.has_permission(DOCTYPE, "read", throw=True)
    from customization_app import api

    bruts = api.get_custom_tache_events(start, end, None)
    reunions = [e.get("name") for e in bruts if e.get("doctype") == REUNION]
    participants = {}
    if reunions:
        for p in frappe.get_all("Reunion Participant", filters={"parent": ["in", reunions], "parenttype": REUNION},
                                fields=["parent", "employe"], order_by="idx"):
            participants.setdefault(p.parent, []).append(p.employe)
    out = []
    for e in bruts:
        ev = vers_calendrier(e, participants.get(e.get("name")))
        if ev:
            out.append(ev)
    annoter(out)
    actifs = _actifs()
    if _exterieur():
        presents = {emp for ev in out if ev["genre"] == "tache" for emp in ev["employes"]}
        actifs = [a for a in actifs if a["id"] in presents]
    connus = {a["id"] for a in actifs}
    autres = {emp for ev in out if ev["genre"] == "tache" for emp in ev["employes"]} - connus - {SANS_EMPLOYE}
    noms = {}
    if autres:
        noms = {e.name: e.employee_name for e in frappe.get_all(
            "Employee", filters={"name": ["in", list(autres)]}, fields=["name", "employee_name"])}
    return {"evenements": out, "colonnes": colonnes(actifs, out, noms)}


# ── Écriture ─────────────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def deplacer(name, debut=None, fin=None, jours=None, employe=None) -> dict:
    """Une tâche glissée dans le calendrier : nouveau créneau (`debut`, `fin`), ou `jours` de décalage pour une
    tâche « toute la journée », et éventuellement un autre `employe` (déposée dans sa colonne).

    Enregistrée avec les droits de l'utilisateur, par save() : les hooks de la tâche jouent comme depuis la fiche."""
    from customization_app.taches_groupe import refus_transfert

    doc = frappe.get_doc(DOCTYPE, name)
    doc.check_permission("write")

    ancien = doc.get("custom_choix_du_staff") or None
    cible = None
    if employe is not None and (employe or None) != ancien:
        if not employe or employe == SANS_EMPLOYE:
            frappe.throw(_("Déposez la tâche dans la colonne d'un employé."))
        cible = frappe.db.get_value("Employee", employe, ["name", "employee_name", "status"], as_dict=True)
        if not cible:
            frappe.throw(_("Employé introuvable."))
        if cible.status != "Active":
            frappe.throw(_("{0} n'est plus un employé actif : on ne lui confie pas de tâche.").format(cible.employee_name))
        from customization_app import taches_groupe
        if not taches_groupe.employe_permis(cible.name, taches_groupe._liste_du_compte()):
            frappe.throw(_("{0} n'est pas dans la liste des employés de votre compte.").format(cible.employee_name), frappe.PermissionError)
        refus = refus_transfert(doc.status, doc.get("custom_type_dintervention"), ancien, cible.name,
                                fixe=cint(doc.get("custom_employe_fixe")))
        if refus:
            frappe.throw(_("Cette tâche ne change pas d'employé : {0}.").format(refus))
        doc.custom_choix_du_staff = cible.name

    if jours is not None and cint(jours):
        d, f = decaler_jours(doc.get("starts_on"), doc.get("ends_on"), cint(jours))
        doc.starts_on, doc.ends_on = d, f or doc.get("ends_on")
    elif debut:
        d, f = _dt(debut), _dt(fin)
        if not d:
            frappe.throw(_("Date de début illisible."))
        if not f or f <= d:
            # pas de fin donnée : la durée d'origine est gardée
            d0, f0 = _dt(doc.get("starts_on")), _dt(doc.get("ends_on"))
            duree = (f0 - d0) if d0 and f0 and f0 > d0 else datetime.timedelta(hours=1)
            f = d + duree
        doc.starts_on, doc.ends_on = d, f

    doc.save()

    if cible:
        nom_de = (frappe.db.get_value("Employee", ancien, "employee_name") if ancien else None) or ancien or "—"
        frappe.get_doc({
            "doctype": "Comment", "comment_type": "Info", "reference_doctype": DOCTYPE, "reference_name": name,
            "content": _("🔁 Transférée par {0} : {1} → {2}").format(
                frappe.session.user, frappe.utils.escape_html(nom_de),
                frappe.utils.escape_html(cible.employee_name or cible.name)),
        }).insert(ignore_permissions=True)
    return {"name": doc.name, "starts_on": str(doc.starts_on), "ends_on": str(doc.ends_on),
            "employe": doc.get("custom_choix_du_staff"), "transferee": bool(cible)}
