"""Tableau des appels : par utilisateur et par jour, ce qui a été fait au téléphone, et les rendez-vous pris —
rangé par PRIORITÉ d'appel. Et, pour chacun, ses priorités du jour (bloc en tête de l'onglet Appels).

POURQUOI PAS « APPEL JOURNALIER »
---------------------------------
L'ancien compteur (DocType « Appel journalier », Server Script « ajuster rendez vous pris par partenaire ») fait
appels restants à 8 h − appels restants à 19 h : un total global, sans utilisateur, et faussé dès qu'une liste est
créée dans la journée. Ici on compte les GESTES, chacun avec son auteur et son heure.

LES PRIORITÉS (ordre décidé le 13/10/2026, revu le 14/10 : Rattrapage avant le 2e appel, Requalification avant
Zone partenaire, + Relance paiements)
-----------------------------------------------------------------------------------------------------------------
1 Urgence · 2 Commandes à traiter · 3 Normal · 4 Rattrapage · 5 2e appel (Relance) · 6 Requalification ·
7 Zone partenaire · 8 Relance paiements. Les listes d'appels sont rangées selon leur `type_liste` ; « Commandes à
traiter » = /app/commandes-a-traiter ; « Rattrapage » = rapport Liste Appels Rattrapage ; « Relance paiements » =
/app/relance-paiements, chaque client qui doit de l'argent relancé UNE FOIS PAR SEMAINE (reste = ceux sans relance
depuis RELANCE_JOURS jours).

LES SOURCES (une action = un client ou une pièce, par utilisateur et par jour — jamais deux fois)
-------------------------------------------------------------------------------------------------
- urgence / normal / relance_liste / zone / requalif : une ligne de liste d'appels modifiée (résultat, « appelé »,
  réponse…) — historique (Version) de la liste, rangée selon le type de la liste ; le même client sur plusieurs
  lignes (un échéancier chacune) ne compte qu'une fois.
- commandes      : commentaire écrit sur une commande (hors traces automatiques), ou commande WEB « 1er / 2e appel
                   sans réponse » — dont l'auteur n'est pas enregistré (non attribué).
- rattrapage     : Liste Appels Rattrapage — « 📵 … sans réponse » (commentaire) ou client recontacté / planifié.
- relances       : relance paiement, par téléphone, SMS ou e-mail ([RELANCE-PAIEMENT] | <type> | …) ;
  relance_ras    : relance de retenue à la source ([RELANCE-RAS]), hors files.
- terrain        : « 📞 Appel au … » d'un technicien depuis Ma journée.
- client         : commentaire écrit sur une fiche client (hors relances et traces automatiques).
- activite       : note du suivi d'activité.
Rendez-vous pris : tâche créée AVEC un client (ni congé, ni récupération, ni tournée), par son auteur, rangée dans la
file d'où elle vient (`origines_rdv`) ; « dont liste » = venue d'une liste d'appels ; « Portail client » = réservée
par le client (/rdv).

Accès : le tableau, rôle « Tableau des appels » seulement — koubaawassim et Nejib (patch attribuer_tableau_appels) ;
qui l'a voit tout le monde. Les priorités du jour (`get_priorites`), rôles « Appels » et « Tableau des appels » :
chacun n'y voit que SES actions.
"""
from __future__ import annotations

import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_days, getdate, nowdate

from customization_app import commentaires_commande as CC
from customization_app import historique_client as HC

ROLES = ("Tableau des appels",)
ROLES_SUPERVISION = ROLES
ROLES_PRIORITES = ("Appels", "Tableau des appels")
NON_ATTRIBUE = "(non attribué)"
PORTAIL = "Guest"
JOURS_MAX = 93

#: Les priorités d'appel, dans l'ordre (13/10/2026 ; 14/10 : Rattrapage avant le 2e appel, Requalification avant
#: Zone partenaire, + Relance paiements).
PRIORITES = (
    ("urgence", "Urgence"),
    ("commandes", "Commandes à traiter"),
    ("normal", "Normal"),
    ("rattrapage", "Rattrapage"),
    ("relance_liste", "2e appel (Relance)"),
    ("requalif", "Requalification"),
    ("zone", "Zone partenaire"),
    ("relances", "Relance paiements (1×/semaine)"),
    ("ras", "Retenue à la source (certificats manquants)"),
    ("hors_secteur", "Hors secteur (quand tout le reste est fait)"),
)
#: Les autres suivis : comptés, mais hors des files d'appels.
AUTRES = (
    ("relance_ras", "Relances retenue à la source"),
    ("terrain", "Appels terrain"),
    ("client", "Commentaires clients"),
    ("activite", "Notes d’activité"),
)
SOURCES = PRIORITES + AUTRES
CLES = tuple(c for c, _l in SOURCES)
CLES_PRIORITES = tuple(c for c, _l in PRIORITES)
#: Rendez-vous par file d'origine ; « rdv_hors » = venu d'aucune file.
CLES_RDV = tuple("rdv_" + c for c in CLES_PRIORITES) + ("rdv_hors",)

#: `type_liste` d'une Liste Appelle Entretien → priorité. Un type inconnu est rangé en Normal.
TYPES_LISTE = {"Urgence": "urgence", "Normal": "normal", "Relance": "relance_liste",
               "Zone partenaire": "zone", "Requalification": "requalif", "Hors secteur": "hors_secteur",
               "Retenue à la source": "ras"}
CLES_LISTES = tuple(TYPES_LISTE.values())
MARQUEURS_RELANCE = {"[RELANCE-PAIEMENT]": "relances", "[RELANCE-RAS]": "relance_ras"}
#: Un client qui doit de l'argent est relancé une fois par semaine.
RELANCE_JOURS = 7

RAPPORT_RATTRAPAGE = "Liste Appels Rattrapage"
#: Où chaque priorité s'ouvre : (route, options) pour frappe.set_route.
OUVERTURE = {
    "commandes": (["commandes-a-traiter"], {}),
    "rattrapage": (["query-report", RAPPORT_RATTRAPAGE], {}),
    "relances": (["relance-paiements"], {}),
}
for _type, _cle in TYPES_LISTE.items():
    OUVERTURE[_cle] = (["List", "Liste Appelle Entretien"], {"docstatus": 0, "type_liste": _type})


# ── Fonctions pures ───────────────────────────────────────────────────────────

def lignes_traitees(versions: list) -> list:
    """[(par, jour, ligne, liste)] : chaque ligne d'appel modifiée par un humain, d'après l'historique. PURE.

    `versions` : [{"owner", "creation", "docname", "data"}] ; `data` = JSON du suivi Frappe, dont
    `row_changed` = [[table, idx, nom de ligne, [[champ, avant, après], …]], …]. Une modification compte si elle
    touche un champ de l'appel (HC.CHAMPS_LISTE) avec une nouvelle valeur non vide."""
    out = []
    for v in versions:
        try:
            data = json.loads(v["data"]) if isinstance(v["data"], str) else (v["data"] or {})
        except ValueError:
            continue
        jour = str(v["creation"])[:10]
        for rc in data.get("row_changed") or []:
            if len(rc) < 4 or rc[0] != "clients":
                continue
            champs = {c[0]: c[2] for c in rc[3] or [] if len(c) >= 3 and c[0] in HC.CHAMPS_LISTE and c[2] not in (None, "", 0)}
            if champs:
                out.append((v["owner"], jour, rc[2], v.get("docname")))
    return out


def priorite_de_liste(type_liste: str | None) -> str:
    """La priorité d'une liste d'appels selon son type. PURE."""
    return TYPES_LISTE.get(type_liste or "", "normal")


def type_relance(contenu: str) -> str | None:
    """'relances' (paiement : téléphone, SMS ou e-mail) | 'relance_ras' | None pour un commentaire
    « [RELANCE-…] | <type> | … ». PURE."""
    t = HC.texte(contenu or "", 200)
    return next((source for marqueur, source in MARQUEURS_RELANCE.items() if t.startswith(marqueur)), None)


def origines_rdv(rdv: list, evenements: list) -> list:
    """[(par, jour, tâche, priorité | None)] : la file d'appel d'où vient chaque rendez-vous. PURE.

    1. créé depuis une ligne de liste d'appels (« Prendre RDV ») → la priorité de cette liste ;
    2. sinon, le même client traité le même jour par la même personne dans une file → cette file (la première dans
       l'ordre des priorités s'il y en a plusieurs) ;
    3. sinon, rattaché à une commande → Commandes à traiter ;
    4. sinon None : hors files (le client a appelé lui-même, rendez-vous repris à la main…).
    `rdv` : [{"par", "jour", "tache", "client", "commande", "depuis_liste", "type_liste"}] ;
    `evenements` : [(source, par, jour, clé)] comme pour `agreger`."""
    traites = defaultdict(set)
    for source, par, jour, cle in evenements:
        if source in CLES_PRIORITES:
            traites[(par, jour, cle)].add(source)
    out = []
    for r in rdv:
        if r.get("depuis_liste"):
            origine = priorite_de_liste(r.get("type_liste"))
        else:
            files = traites.get((r["par"], r["jour"], r.get("client"))) or ()
            origine = next((c for c in CLES_PRIORITES if c in files), None)
            if not origine and r.get("commande"):
                origine = "commandes"
        out.append((r["par"], r["jour"], r["tache"], origine))
    return out


def agreger(evenements: list, rdv: list) -> dict:
    """{(par, jour): {source: n, "total", "listes", "rdv", "rdv_liste", "rdv_<priorité>", "rdv_hors"}}. PURE.

    `evenements` : [(source, par, jour, clé)] — dédoublonnés sur le quadruplet : un même client traité deux fois
    le même jour par la même personne pour la même source ne compte qu'une fois. « listes » = la somme des
    priorités venues des listes d'appels (base du taux de rendez-vous).
    `rdv` : [(par, jour, tâche, priorité d'origine | None)] — cf. `origines_rdv`."""
    cases = defaultdict(lambda: dict({c: 0 for c in CLES + CLES_RDV}, total=0, listes=0, rdv=0, rdv_liste=0))
    for source, par, jour, _cle in set(evenements):
        case = cases[(par, jour)]
        case[source] += 1
        case["total"] += 1
        case["listes"] += 1 if source in CLES_LISTES else 0
    for par, jour, _tache, origine in set(rdv):
        case = cases[(par, jour)]
        case["rdv"] += 1
        case["rdv_" + (origine if origine in CLES_PRIORITES else "hors")] += 1
        case["rdv_liste"] += 1 if origine in CLES_LISTES else 0
    return dict(cases)


def prochaine_priorite(restes: dict) -> str | None:
    """La première priorité, dans l'ordre, où il reste quelque chose à faire. PURE."""
    return next((c for c in CLES_PRIORITES if (restes.get(c) or 0) > 0), None)


# ── Lecture des actions ───────────────────────────────────────────────────────

def _bornes(du, au):
    au = getdate(au or nowdate())
    du = getdate(du or add_days(au, -6))
    if du > au:
        du, au = au, du
    if (au - du).days > JOURS_MAX:
        du = add_days(au, -JOURS_MAX)
    return str(du), str(add_days(au, 1)), str(au)


def _evenements_listes(debut, fin):
    versions = frappe.db.sql("""SELECT owner, creation, docname, data FROM `tabVersion`
                                WHERE ref_doctype = 'Liste Appelle Entretien' AND creation >= %s AND creation < %s
                                  AND owner NOT IN ('Administrator', 'Guest') AND data LIKE '%%row_changed%%'
                                ORDER BY creation""", (debut, fin), as_dict=True)
    traitees = lignes_traitees(versions)
    noms = list({t[2] for t in traitees})
    listes = list({t[3] for t in traitees if t[3]})
    clients = dict(frappe.db.sql("SELECT name, client FROM `tabAppelle Client` WHERE name IN %s", (noms,))) if noms else {}
    types = dict(frappe.db.sql("SELECT name, type_liste FROM `tabListe Appelle Entretien` WHERE name IN %s",
                               (listes,))) if listes else {}
    return [(priorite_de_liste(types.get(liste)), par, jour, clients.get(ligne) or ligne)
            for par, jour, ligne, liste in traitees]


def _evenements_commentaires(debut, fin):
    out = []
    for c in frappe.db.sql("""SELECT reference_doctype, reference_name, content, owner, creation, comment_type
                              FROM `tabComment`
                              WHERE creation >= %s AND creation < %s AND owner NOT IN ('Administrator', 'Guest')
                                AND comment_type IN ('Comment', 'Info')
                                AND reference_doctype IN ('Sales Order', 'Customer', 'Tache de travail')""",
                           (debut, fin), as_dict=True):
        jour, t = str(c.creation)[:10], HC.texte(c.content or "", 200)
        if not t:
            continue
        if c.reference_doctype == "Tache de travail":
            if c.comment_type == "Comment" and t.startswith("📵") and "Rattrapage" in t:
                out.append(("rattrapage", c.owner, jour, ("tache", c.reference_name)))
            elif c.comment_type == "Info" and t.startswith("📞 Appel"):
                out.append(("terrain", c.owner, jour, ("tache", c.reference_name)))
            continue
        if c.comment_type != "Comment":
            continue
        relance = type_relance(t) if c.reference_doctype == "Customer" else None
        if relance:
            out.append((relance, c.owner, jour, c.reference_name))
        elif not CC.est_automatique(t) and HC.nature_commentaire(t) != "auto":
            out.append(("commandes" if c.reference_doctype == "Sales Order" else "client", c.owner, jour, c.reference_name))
    return out


def _clients_des_taches(evenements):
    """Le rattrapage et les appels terrain portent sur une TÂCHE : on les ramène au client (un appel couvre toutes
    ses cartes de la liste — `marquer_sans_reponse` commente chacune)."""
    taches = list({e[3][1] for e in evenements if isinstance(e[3], tuple)})
    clients = dict(frappe.db.sql("SELECT name, IFNULL(custom_client, name) FROM `tabTache de travail` WHERE name IN %s",
                                 (taches,))) if taches else {}
    return [(s, p, j, clients.get(k[1], k[1]) if isinstance(k, tuple) else k) for s, p, j, k in evenements]


def _evenements_rattrapage_traites(debut, fin):
    """Client recontacté / planifié depuis le rattrapage (les « sans réponse » sont déjà comptés par leur 📵)."""
    if not frappe.db.has_column("Tache de travail", "recontacte_par"):
        return []
    return [("rattrapage", r.recontacte_par, str(r.recontacte_le)[:10], r.client)
            for r in frappe.db.sql("""SELECT recontacte_par, recontacte_le, IFNULL(custom_client, name) AS client
                                      FROM `tabTache de travail`
                                      WHERE recontacte_le >= %s AND recontacte_le < %s AND IFNULL(recontacte_par, '') <> ''
                                        AND IFNULL(recontacte, '') NOT LIKE '%%sans réponse%%'""", (debut, fin), as_dict=True)]


def _evenements_web(debut, fin):
    out = []
    for rang, champ in ((1, "custom_appel_1_sans_reponse"), (2, "custom_appel_2_sans_reponse")):
        if not frappe.db.has_column("Sales Order", champ):
            continue
        for nom, le in frappe.db.sql(f"SELECT name, `{champ}` FROM `tabSales Order` WHERE `{champ}` >= %s AND `{champ}` < %s",
                                     (debut, fin)):
            out.append(("commandes", NON_ATTRIBUE, str(le)[:10], (nom, rang)))
    return out


def _evenements_activite(debut, fin):
    if not frappe.db.table_exists("Activite Note"):
        return []
    return [("activite", r.auteur, str(r.date)[:10], r.parent)
            for r in frappe.db.sql("""SELECT auteur, date, parent FROM `tabActivite Note`
                                      WHERE date >= %s AND date < %s AND IFNULL(auteur, '') <> ''""", (debut, fin), as_dict=True)]


def _evenements(debut, fin, web=True):
    return (_evenements_listes(debut, fin) + _clients_des_taches(_evenements_commentaires(debut, fin))
            + _evenements_rattrapage_traites(debut, fin) + _evenements_activite(debut, fin)
            + (_evenements_web(debut, fin) if web else []))


def _rendez_vous(debut, fin):
    """Les rendez-vous de la période, avec de quoi retrouver leur file (`origines_rdv`)."""
    return [{"par": r.owner, "jour": str(r.creation)[:10], "tache": r.name, "client": r.client,
             "commande": r.commande, "depuis_liste": bool(r.depuis_liste), "type_liste": r.type_liste}
            for r in frappe.db.sql("""SELECT t.name, t.owner, t.creation, t.custom_client AS client,
                                             IFNULL(t.commande_client, '') AS commande,
                                             EXISTS(SELECT 1 FROM `tabAppelle Client` a WHERE a.tache_de_travail = t.name) AS depuis_liste,
                                             (SELECT l.type_liste FROM `tabAppelle Client` a
                                              JOIN `tabListe Appelle Entretien` l ON l.name = a.parent
                                              WHERE a.tache_de_travail = t.name LIMIT 1) AS type_liste
                                      FROM `tabTache de travail` t
                                      WHERE t.creation >= %s AND t.creation < %s AND IFNULL(t.custom_client, '') <> ''
                                        AND IFNULL(t.custom_type_dintervention, '') NOT IN %s
                                        AND t.owner <> 'Administrator'""",
                                   (debut, fin, HC.TYPES_HORS_CLIENT), as_dict=True)]


# ── Ce qui reste à faire, maintenant ──────────────────────────────────────────

def _restes_listes() -> dict:
    """Clients pas encore appelés dans les listes EN COURS (brouillons), par priorité."""
    out = {c: 0 for c in CLES_LISTES}
    for type_liste, n in frappe.db.sql("""SELECT l.type_liste, COUNT(DISTINCT a.client)
                                          FROM `tabAppelle Client` a JOIN `tabListe Appelle Entretien` l ON l.name = a.parent
                                          WHERE l.docstatus = 0 AND IFNULL(a.`a_été_appelé`, 0) = 0
                                            AND IFNULL(a.resume_appel, '') = ''
                                          GROUP BY l.type_liste"""):
        out[priorite_de_liste(type_liste)] += int(n or 0)
    return out


def _reste_commandes() -> int:
    """Commandes de la page « Commandes à traiter » (depuis le 01/07/2026) qui restent À LIVRER."""
    from customization_app.commandes_a_traiter import DEPUIS_DEFAUT
    return int(frappe.db.sql("""SELECT COUNT(*) FROM `tabSales Order`
                                WHERE docstatus = 1 AND transaction_date >= %s
                                  AND status IN ('To Deliver and Bill', 'To Deliver')""", DEPUIS_DEFAUT)[0][0] or 0)


def _reste_rattrapage():
    """Clients de la Liste Appels Rattrapage — le rapport lui-même fait foi (même filtre, même nombre)."""
    try:
        rapport = frappe.get_doc("Report", RAPPORT_RATTRAPAGE)
        res = rapport.execute_script({})
        lignes = res[1] if isinstance(res, (list, tuple)) and len(res) >= 2 and isinstance(res[1], list) else res
        return len({(l.get("custom_client") or l.get("name")) for l in (lignes or []) if isinstance(l, dict)})
    except Exception:
        frappe.log_error(title="Tableau des appels : rapport rattrapage", message=frappe.get_traceback())
        return None


def _reste_relances():
    """Clients de la page Relance Paiements Clients (dette, chèque ou traite impayés) qui n'ont pas été relancés —
    téléphone, SMS ou e-mail — depuis RELANCE_JOURS jours : chacun est relancé une fois par semaine."""
    try:
        from customization_app.api import RELANCE_MARKER, get_relance_clients
        clients = [c["customer"] for c in get_relance_clients()["clients"]]
    except Exception:
        frappe.log_error(title="Tableau des appels : clients à relancer", message=frappe.get_traceback())
        return None
    if not clients:
        return 0
    relances = set(frappe.db.sql_list("""SELECT DISTINCT reference_name FROM `tabComment`
                                         WHERE reference_doctype = 'Customer' AND comment_type = 'Comment'
                                           AND reference_name IN %s AND creation >= %s AND content LIKE %s""",
                                      (clients, add_days(nowdate(), 1 - RELANCE_JOURS), "%" + RELANCE_MARKER + "%")))
    return len(set(clients) - relances)


def restes() -> dict:
    """{priorité: nombre à faire maintenant} (None = inconnu)."""
    out = _restes_listes()
    out["commandes"] = _reste_commandes()
    out["rattrapage"] = _reste_rattrapage()
    out["relances"] = _reste_relances()
    return out


# ── API ───────────────────────────────────────────────────────────────────────

def _supervise():
    return bool(set(frappe.get_roles()) & set(ROLES_SUPERVISION))


@frappe.whitelist()
def get_priorites():
    """Les priorités du jour de l'utilisateur connecté : pour chaque file, dans l'ordre, ce qui reste à faire et ce
    qu'IL a fait aujourd'hui. Bloc « Mes appels par priorité » de l'onglet Appels."""
    frappe.only_for(ROLES_PRIORITES)
    moi, jour = frappe.session.user, nowdate()
    debut, fin = jour, str(add_days(jour, 1))
    evenements = [e for e in _evenements(debut, fin, web=False) if e[1] == moi]
    rdv = origines_rdv([r for r in _rendez_vous(debut, fin) if r["par"] == moi], evenements)
    case = agreger(evenements, rdv).get((moi, jour)) or {}
    reste = restes()
    suivante = prochaine_priorite(reste)
    files = []
    for rang, (cle, libelle) in enumerate(PRIORITES, start=1):
        route, options = OUVERTURE[cle]
        files.append({"rang": rang, "cle": cle, "libelle": libelle, "reste": reste.get(cle), "fait": case.get(cle, 0),
                      "rdv": case.get("rdv_" + cle, 0), "route": route, "options": options, "suivante": cle == suivante})
    autres = [{"cle": c, "libelle": l, "fait": case.get(c, 0)} for c, l in AUTRES if case.get(c)]
    return {"jour": jour, "files": files, "autres": autres, "total": case.get("total", 0), "rdv": case.get("rdv", 0),
            "rdv_liste": case.get("rdv_liste", 0), "rdv_hors": case.get("rdv_hors", 0), "suivante": suivante,
            "tableau": _supervise()}


@frappe.whitelist()
def get_tableau(du=None, au=None, utilisateur=None):
    """Le tableau de la période (7 derniers jours par défaut, 93 au plus)."""
    frappe.only_for(ROLES)
    debut, fin, au = _bornes(du, au)
    supervise = _supervise()
    if not supervise:
        utilisateur = frappe.session.user

    evenements = _evenements(debut, fin, web=supervise and not utilisateur)
    rdv = origines_rdv(_rendez_vous(debut, fin), evenements)
    if utilisateur:
        evenements = [e for e in evenements if e[1] == utilisateur]
        rdv = [r for r in rdv if r[0] == utilisateur]
    cases = agreger(evenements, rdv)

    noms = HC._noms_utilisateurs({p for p, _j in cases} - {NON_ATTRIBUE, PORTAIL})
    noms.update({NON_ATTRIBUE: _("Non attribué (commandes WEB)"), PORTAIL: _("Portail client (/rdv)")})

    cumuls = CLES + CLES_RDV + ("total", "listes", "rdv", "rdv_liste")
    par_jour, par_user = [], {}
    for (par, jour), case in cases.items():
        par_jour.append({"jour": jour, "user": par, "nom": noms.get(par, par), **case})
        cumul = par_user.setdefault(par, dict({c: 0 for c in cumuls}, user=par, nom=noms.get(par, par), jours=0))
        for k in cumuls:
            cumul[k] += case[k]
        cumul["jours"] += 1 if case["total"] else 0
    par_jour.sort(key=lambda x: (x["jour"], x["total"]), reverse=True)
    utilisateurs = sorted(par_user.values(), key=lambda x: (x["total"], x["rdv"]), reverse=True)

    jours, j = [], getdate(debut)
    while str(j) <= au:
        jours.append(str(j))
        j = add_days(j, 1)
    # Une série par personne, dans le même ordre pour les deux graphiques (actions, rendez-vous) : même couleur.
    series = [{"user": u["user"], "nom": u["nom"],
               "valeurs": [cases.get((u["user"], d), {}).get("total", 0) for d in jours],
               "rdv": [cases.get((u["user"], d), {}).get("rdv", 0) for d in jours]}
              for u in utilisateurs if u["total"] or u["rdv"]]
    totaux = {k: sum(u[k] for u in utilisateurs) for k in cumuls}

    choix = []
    if supervise:
        choix = [{"user": u, "nom": n} for u, n in sorted(HC._noms_utilisateurs(set(frappe.get_all(
            "Has Role", filters={"role": ["in", ["Appels", "Sales User", "Sales Manager", "System Manager"]],
                                 "parenttype": "User"}, pluck="parent")) - {"Administrator", "Guest"}).items(), key=lambda x: x[1])]
    return {"du": debut, "au": au, "supervise": supervise, "utilisateur": utilisateur or "",
            "priorites": [{"cle": c, "libelle": l, "rang": i} for i, (c, l) in enumerate(PRIORITES, start=1)],
            "autres": [{"cle": c, "libelle": l} for c, l in AUTRES],
            "sources": [{"cle": c, "libelle": l} for c, l in SOURCES], "restes": restes(), "jours": jours,
            "series": series, "par_utilisateur": utilisateurs, "par_jour": par_jour, "totaux": totaux, "choix": choix}
