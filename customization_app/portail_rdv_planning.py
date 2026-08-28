"""
Moteur de planification du portail /rdv — la logique métier du 27/08/2026.

RÈGLES (décisions utilisateur) :
  - matinée 09:30 → 12:30 (180 min), après-midi 13:30 → 17:30 (240 min) ;
  - durées : Entretien 30 min, Réparation 60 min, Installation 75 min,
    et TOUJOURS 30 min de battement entre deux interventions ;
  - un employé travaille UNE demi-journée dans UN SEUL secteur (le matin et
    l'après-midi peuvent différer) ;
  - adresse « Hors Secteur » : pas de réservation en ligne (grisé à l'écran) ;
  - secteurs 8 et 9 : la JOURNÉE ENTIÈRE de l'employé est allouée au secteur ;
  - secteur 7 : demi-journée possible, l'autre demi-journée du même employé ne
    peut être que secteur 3, 4 (limitrophes) ou 7 ;
  - secteurs lointains 7/8/9 : UNE journée par semaine PAR secteur — si un jour
    de la semaine porte déjà ce secteur, seules ses demi-journées restantes
    sont proposables ;
  - employés : les N PREMIERS de la liste de la config travaillent les RDV en
    ligne ; un employé bloqué toute la journée est remplacé par le suivant de
    la liste, et ainsi de suite ;
  - dimanche fermé, sauf autorisation ponctuelle en config avec un employé
    dédié ;
  - réservation à partir de DEMAIN.

Le placement est un empilement séquentiel depuis le début de la fenêtre :
somme des durées existantes + 30 min entre chacune. Les heures réelles des
tâches posées au Desk ne sont pas re-péroutées — l'heure du portail est un
ANCRAGE, le magasin garde la main.
"""
from __future__ import annotations

import datetime

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime, getdate

DOCTYPE_TACHE = "Tache de travail"

FENETRES = {
    "matin": ("09:30:00", "12:30:00"),
    "apres_midi": ("13:30:00", "17:30:00"),
}
BATTEMENT = 30
DUREES = {"Entretien": 30, "Réparation": 60, "Installation": 75}
TEMPS_LIBELLE = {"Entretien": "30 min", "Réparation": "1 heure",
                 "Installation": "1 heure, 15 min"}

SECTEURS_JOURNEE_COMPLETE = {"Secteur 8", "Secteur 9"}
SECTEUR_COMBINABLE = "Secteur 7"
COMPAGNONS_SECTEUR_7 = {"Secteur 3", "Secteur 4", "Secteur 7"}
SECTEURS_LOINTAINS = {"Secteur 7", "Secteur 8", "Secteur 9"}
HORS_SECTEUR = "Hors Secteur"

HORIZON_PLANNING = 21   # trois semaines de grille proposées au client


# ------------------------------------------------------------------ lecture


def _liste_employes(config):
    lignes = frappe.get_all(
        "Portail RDV Employe",
        filters={"parent": "Config Portail RDV", "parenttype": "Config Portail RDV"},
        fields=["employe"], order_by="idx")
    liste = [l.employe for l in lignes if l.employe]
    if not liste and config.get("employe_defaut"):
        liste = [config.get("employe_defaut")]
    return liste


def delai_standard(config):
    """Le délai minimum avant un rendez-vous, en jours (config, 1 par défaut :
    réservable dès demain)."""
    return cint(config.get("delai_jours")) or 1


def contexte_partenaire(config, gouvernorat):
    """La ZONE PARTENAIRE (Sousse, Monastir… — décision 28/08/2026).

    Ces gouvernorats sont « Hors Secteur » pour l'équipe de Tunis, mais un
    employé PARTENAIRE les couvre : leurs clients peuvent donc réserver en
    ligne, avec ses propres règles — lui seul, un délai plus long (le
    déplacement se prépare), et le dimanche seulement s'il le travaille.
    -> dict ou None si l'adresse n'est pas dans la zone / aucun partenaire.
    """
    cible = (gouvernorat or "").strip().casefold()
    if not cible or not frappe.db.table_exists("Portail RDV Partenaire"):
        return None
    for ligne in frappe.get_all(
            "Portail RDV Partenaire",
            filters={"parent": "Config Portail RDV",
                     "parenttype": "Config Portail RDV"},
            fields=["employe", "gouvernorats", "delai_jours", "dimanche"],
            order_by="idx"):
        couverts = {g.strip().casefold()
                    for g in (ligne.gouvernorats or "").split(",") if g.strip()}
        if ligne.employe and cible in couverts:
            return {
                "employes": [ligne.employe],
                "delai_jours": cint(ligne.delai_jours) or 3,
                "dimanche": bool(cint(ligne.dimanche)),
            }
    return None


def _nb_actifs(config, liste):
    nb = cint(config.get("nb_employes"))
    return min(nb, len(liste)) if nb else len(liste)


def _taches_periode(employes, debut, fin, exclure=None):
    """{(employe, date): {"matin": [durées], "apres_midi": [durées],
    "secteurs": {"matin": set, "apres_midi": set}}} en UNE requête.
    `exclure` : nom d'une tâche à IGNORER — celle qu'on est en train de
    déplacer ne doit pas compter contre son propre nouveau créneau."""
    if not employes:
        return {}
    lignes = frappe.db.sql(
        """SELECT name, custom_choix_du_staff AS employe, starts_on, ends_on,
                  custom_type_dintervention AS type_i, secteur,
                  `toute_la_journée` AS jour_entier
           FROM `tabTache de travail`
           WHERE status != 'Cancelled'
             AND custom_choix_du_staff IN %(qui)s
             AND starts_on >= %(debut)s AND starts_on < %(fin)s""",
        {"qui": tuple(employes), "debut": str(debut),
         "fin": str(add_days(fin, 1))}, as_dict=True)
    out = {}
    for l in lignes:
        if not l.starts_on or (exclure and l.name == exclure):
            continue
        jour = l.starts_on.date()
        cle = (l.employe, jour)
        entree = out.setdefault(cle, {"matin": [], "apres_midi": [],
                                      "secteurs": {"matin": set(), "apres_midi": set()},
                                      "jour_entier": False})
        # ⚠️ « TOUTE LA JOURNÉE » COCHÉE = journée prise, quelles que soient les
        # heures saisies. C'est ainsi que sont posés les JOURS DE RÉCUPÉRATION
        # (tâche « Autre », 10:00-12:00 mais cochée) : lire les seules heures
        # laissait l'après-midi ouvert et proposait le technicien absent.
        if cint(l.jour_entier):
            entree["jour_entier"] = True
            continue
        fin_reelle = l.ends_on or (l.starts_on + datetime.timedelta(
            minutes=DUREES.get(l.type_i, 60)))
        # Une intervention à cheval compte dans LES DEUX demi-journées : celle
        # de 12:30 à 13:45 mange aussi le début de l'après-midi.
        for demi, (h_debut, h_fin) in FENETRES.items():
            ws = get_datetime("%s %s" % (jour, h_debut))
            we = get_datetime("%s %s" % (jour, h_fin))
            if l.starts_on < we and fin_reelle > ws:
                entree[demi].append((l.starts_on, fin_reelle))
                if l.secteur:
                    entree["secteurs"][demi].add(l.secteur)
    return out


def _jours_conges(employes, debut, fin):
    """{(employe, date)} — congés et jours de récupération APPROUVÉS (Leave
    Application, hrms) : l'employé est indisponible TOUTE la journée, son
    remplaçant dans la liste prend le relais (précision utilisateur 27/08).
    Un congé d'une demi-journée bloque la journée entière — prudence : on ne
    promet pas un créneau à un technicien qui ne sera peut-être pas là."""
    if not employes:
        return set()
    lignes = frappe.db.sql(
        """SELECT employee, from_date, to_date FROM `tabLeave Application`
           WHERE docstatus = 1 AND status = 'Approved'
             AND employee IN %(qui)s
             AND from_date <= %(fin)s AND to_date >= %(debut)s""",
        {"qui": tuple(employes), "debut": str(debut), "fin": str(fin)},
        as_dict=True)
    out = set()
    for l in lignes:
        jour = max(getdate(l.from_date), getdate(debut))
        dernier = min(getdate(l.to_date), getdate(fin))
        while jour <= dernier:
            out.add((l.employee, jour))
            jour = add_days(jour, 1)
    return out


def _jours_secteurs_lointains(debut, fin):
    """{(lundi_iso, secteur): set(dates)} — les journées déjà consommées par
    chaque secteur lointain, TOUTES équipes confondues (la sortie lointaine est
    une journée de l'entreprise, pas d'un employé)."""
    lignes = frappe.db.sql(
        """SELECT DISTINCT DATE(starts_on) AS jour, secteur
           FROM `tabTache de travail`
           WHERE status != 'Cancelled' AND secteur IN %(loin)s
             AND starts_on >= %(debut)s AND starts_on < %(fin)s""",
        {"loin": tuple(SECTEURS_LOINTAINS),
         "debut": str(add_days(debut, -7)), "fin": str(add_days(fin, 8))},
        as_dict=True)
    out = {}
    for l in lignes:
        jour = getdate(l.jour)
        lundi = add_days(jour, -jour.weekday())
        out.setdefault((str(lundi), l.secteur), set()).add(jour)
    return out


# ------------------------------------------------------------------ règles


def _premier_creneau(intervalles, jour, demi, duree):
    """Le PREMIER trou réel de la demi-journée qui absorbe `duree` minutes,
    avec 30 min de battement autour des interventions existantes (demande
    27/08 : « toujours prendre le premier créneau disponible »).
    -> datetime de début, ou None. Les heures RÉELLES des tâches font foi —
    l'empilement estimé posait des RDV en fin de fenêtre alors que le début
    était libre."""
    debut_f, fin_f = FENETRES[demi]
    ws = get_datetime("%s %s" % (jour, debut_f))
    we = get_datetime("%s %s" % (jour, fin_f))
    pas = datetime.timedelta(minutes=duree)
    battement = datetime.timedelta(minutes=BATTEMENT)

    occupes = sorted((max(d, ws - battement), min(f, we + battement))
                     for d, f in intervalles if f > ws - battement and d < we + battement)
    curseur = ws
    for d, f in occupes:
        if curseur + pas + battement <= d:
            return curseur
        if f + battement > curseur:
            curseur = f + battement
    if curseur + pas <= we:
        return curseur
    return None


def groupes_secteurs(config):
    """Les groupes de secteurs combinables sur une même demi-journée, lus dans
    la config (une ligne = un groupe, secteurs séparés par des virgules).
    Vide = règle stricte « un seul secteur par demi-journée »."""
    groupes = []
    for ligne in (config.get("groupes_secteurs") or "").splitlines():
        membres = {m.strip() for m in ligne.split(",") if m.strip()}
        if len(membres) > 1:
            groupes.append(membres)
    return groupes


def _compagnons(config, secteur):
    """Les secteurs qu'on accepte à côté de celui-ci sur la même demi-journée."""
    permis = {secteur}
    for groupe in groupes_secteurs(config):
        if secteur in groupe:
            permis |= groupe
    return permis


def _demi_faisable(entree, jour, demi, secteur, duree, config=None):
    """L'employé peut-il prendre ce secteur, cette durée, sur cette demi-journée ?
    -> datetime de début (premier trou réel), ou None."""
    entree = entree or {"matin": [], "apres_midi": [],
                        "secteurs": {"matin": set(), "apres_midi": set()},
                        "jour_entier": False}
    # Journée entière prise (récupération, formation, congé posé en tâche) :
    # rien ne se réserve ce jour-là chez cet employé.
    if entree.get("jour_entier"):
        return None
    autre = "apres_midi" if demi == "matin" else "matin"
    sect_ici = entree["secteurs"][demi]
    sect_autre = entree["secteurs"][autre]

    # Un seul secteur par demi-journée — sauf secteurs déclarés COMBINABLES
    # dans la config (ex. « Secteur 1, Secteur 2 »). Les tâches HISTORIQUES du
    # Desk mélangent parfois plusieurs secteurs : on n'exige pas la pureté du
    # passé, on refuse seulement d'AJOUTER un secteur incompatible avec ceux
    # où l'employé va déjà cette demi-journée.
    if sect_ici and not sect_ici <= _compagnons(config or {}, secteur):
        return None
    # Journée allouée à un secteur 8/9 : rien d'autre ce jour-là.
    if any(s in SECTEURS_JOURNEE_COMPLETE and s != secteur for s in sect_ici | sect_autre):
        return None
    if secteur in SECTEURS_JOURNEE_COMPLETE:
        # L'autre demi-journée doit être vide ou déjà sur CE secteur.
        if sect_autre and sect_autre != {secteur}:
            return None
    if secteur == SECTEUR_COMBINABLE:
        # Secteur 7 : l'autre demi-journée seulement vide ou 3/4/7.
        if any(s not in COMPAGNONS_SECTEUR_7 for s in sect_autre):
            return None
    if SECTEUR_COMBINABLE in sect_autre and secteur not in COMPAGNONS_SECTEUR_7:
        return None

    return _premier_creneau(entree[demi], jour, demi, duree)


def _employe_bloque_jour(entree, jour):
    """Plus de place nulle part sur la journée (même pour 30 min) — ou journée
    entière prise (jour de récupération)."""
    entree = entree or {"matin": [], "apres_midi": []}
    if entree.get("jour_entier"):
        return True
    return all(_premier_creneau(entree.get(d) or [], jour, d, min(DUREES.values())) is None
               for d in FENETRES)


def _pool_du_jour(config, liste, taches, jour, conges=frozenset(), contexte=None):
    """Les N premiers employés NON bloqués ce jour-là — le suivant de la liste
    remplace un employé dont la journée est pleine OU qui est en congé /
    récupération (Leave Application approuvée).

    En ZONE PARTENAIRE, pas de remplaçant : il n'y a que lui, et le dimanche
    dépend de son propre réglage."""
    if contexte:
        if jour.weekday() == 6 and not contexte["dimanche"]:
            return []
        return [e for e in contexte["employes"]
                if (e, jour) not in conges
                and not _employe_bloque_jour(taches.get((e, jour)), jour)]
    if jour.weekday() == 6:  # dimanche
        if not cint(config.get("autoriser_dimanche")):
            return []
        dimanche = config.get("employe_dimanche")
        if not dimanche or (dimanche, jour) in conges:
            return []
        return [dimanche]
    nb = _nb_actifs(config, liste)
    pool = []
    for employe in liste:
        if len(pool) >= nb:
            break
        if (employe, jour) in conges:
            continue
        if _employe_bloque_jour(taches.get((employe, jour)), jour):
            continue
        pool.append(employe)
    return pool


def _quota_lointain_ok(lointains, jour, secteur):
    """1 journée/semaine PAR secteur lointain : si la semaine a déjà sa journée,
    seule CETTE journée reste proposable."""
    if secteur not in SECTEURS_LOINTAINS:
        return True
    lundi = add_days(jour, -jour.weekday())
    jours = lointains.get((str(lundi), secteur), set())
    return not jours or jour in jours


# ------------------------------------------------------------------ moteur


def disponibilites(config, secteur, type_intervention, horizon=HORIZON_PLANNING,
                   exclure=None, contexte=None):
    """La grille des demi-journées faisables. -> [{date, matin, apres_midi}].

    `contexte` : zone partenaire (employé dédié, délai propre, dimanche) — la
    zone est « Hors Secteur » pour Tunis, mais couverte par le partenaire."""
    duree = DUREES.get(type_intervention)
    if not duree:
        return []
    if not contexte and (not secteur or secteur == HORS_SECTEUR):
        return []

    liste = contexte["employes"] if contexte else _liste_employes(config)
    if not liste:
        return []
    debut = add_days(getdate(), contexte["delai_jours"] if contexte
                     else delai_standard(config))
    fin = add_days(getdate(), horizon)
    dimanche_inclus = [] if contexte else ([config.get("employe_dimanche")]
                                           if config.get("employe_dimanche") else [])
    tout_le_monde = list(dict.fromkeys(liste + dimanche_inclus))
    taches = _taches_periode(tout_le_monde, debut, fin, exclure=exclure)
    conges = _jours_conges(tout_le_monde, debut, fin)
    lointains = _jours_secteurs_lointains(debut, fin)

    jours = []
    jour = debut
    while jour <= fin:
        matin = apres_midi = False
        # Le quota « une journée par semaine » ne concerne QUE les secteurs
        # lointains de Tunis : la zone partenaire a son propre employé.
        if contexte or _quota_lointain_ok(lointains, jour, secteur):
            for employe in _pool_du_jour(config, liste, taches, jour, conges, contexte):
                entree = taches.get((employe, jour))
                if not matin and _demi_faisable(entree, jour, "matin", secteur,
                                                duree, config) is not None:
                    matin = True
                if not apres_midi and _demi_faisable(entree, jour, "apres_midi", secteur,
                                                     duree, config) is not None:
                    apres_midi = True
                if matin and apres_midi:
                    break
        jours.append({"date": str(jour), "matin": matin, "apres_midi": apres_midi})
        jour = add_days(jour, 1)
    return jours


def placer(config, jour, demi, secteur, type_intervention, exclure=None,
           contexte=None):
    """Choisit l'employé (ordre de la liste = priorité) et l'heure de début.
    -> (employe, starts_on, duree_minutes) ou lève."""
    duree = DUREES.get(type_intervention)
    if not duree:
        frappe.throw(_("Type de rendez-vous inconnu."))
    if not contexte and (not secteur or secteur == HORS_SECTEUR):
        frappe.throw(_("Cette adresse est hors secteur — appelez-nous pour "
                       "organiser l'intervention."))
    if demi not in FENETRES:
        frappe.throw(_("Choisissez matin ou après-midi."))

    liste = contexte["employes"] if contexte else _liste_employes(config)
    tout_le_monde = liste + ([] if contexte else ([config.get("employe_dimanche")]
                                                 if config.get("employe_dimanche") else []))
    taches = _taches_periode(tout_le_monde, jour, jour, exclure=exclure)
    conges = _jours_conges(tout_le_monde, jour, jour)
    lointains = {} if contexte else _jours_secteurs_lointains(jour, jour)
    if not contexte and not _quota_lointain_ok(lointains, jour, secteur):
        frappe.throw(_("Ce secteur a déjà sa journée cette semaine-là — "
                       "choisissez un autre créneau proposé."))

    # L'ordre de la liste est la PRIORITÉ : le premier employé éligible prend,
    # et pour lui, le premier trou réel de la demi-journée.
    for employe in _pool_du_jour(config, liste, taches, jour, conges, contexte):
        starts_on = _demi_faisable(taches.get((employe, jour)), jour, demi,
                                   secteur, duree, config)
        if starts_on is not None:
            return employe, starts_on, duree

    frappe.throw(_("Ce créneau vient d'être pris — choisissez-en un autre."))
