"""Rendez-vous pris : combien entrent dans le calendrier, et PAR OÙ (demande du 19/11/2026).

Trois canaux, et chaque rendez-vous n'appartient qu'à un seul :
  - « manuel »       : posé à la main dans le calendrier (« 📅 Prendre RDV », fiche de tâche, liste d'appels…) ;
  - « app_magasin »  : pris avec l'app par un utilisateur, à la place du client (« 📱 RDV avec l'app ») ;
  - « app_client »   : pris par le client lui-même sur le portail /rdv (code SMS).

CE QUI FAIT FOI. L'app coche `custom_reservation_app` sur la tâche qu'elle crée ; l'auteur de la tâche est alors
l'utilisateur du magasin, ou « Guest » quand le client a réservé seul (vérifié sur 580 tâches : aucune exception). Le
sujet « RDV portail… » posé par l'app départage le seul cas tordu : un utilisateur connecté au Desk qui réserve
lui-même par le portail public avec un code SMS.

QU'EST-CE QU'UN RENDEZ-VOUS. La même définition que le Tableau des appels (`tableau_appels._rendez_vous`) : une tâche
de travail AVEC un client, hors congés, jours de récupération et tournées commerciales, hors tâches créées par
« Administrator » (traitements automatiques). Les deux écrans doivent donner le même total.

DEUX LECTURES DE LA DATE : « pris le » (création de la tâche — par défaut, c'est l'activité de prise de rendez-vous) ou
« prévu le » (date de l'intervention — c'est la charge du calendrier).

⚠️ LIMITE CONNUE. Un rendez-vous que le client ANNULE depuis le portail est supprimé du calendrier
(`portail_rdv.annuler_rdv`) : il ne peut plus être compté ici. Les annulations faites par le magasin, elles, gardent la
tâche (statut « Cancelled ») et sont comptées, avec leur part affichée.

`canal`, `pas_auto`, `periodes`, `agreger` sont PURES (testées sans site). Accès : rôle « Relances » (l'onglet) et
System Manager.
"""
from __future__ import annotations

import datetime

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, nowdate

from customization_app import historique_client as HC
from customization_app.reparation_osmoseur import RAISON_REPRISE

ROLES = ("Relances", "System Manager")
CANAUX = (
    ("manuel", "Calendrier, à la main"),
    ("app_magasin", "App, par un utilisateur"),
    ("app_client", "App, par le client"),
)
CLES = tuple(c for c, _l in CANAUX)
INVITE = "Guest"
JOURS_MAX = 400              # treize mois : de quoi comparer un mois à celui de l'an passé
JOURS_DEFAUT = 30
BASES = {"prise": "creation", "prevue": "starts_on"}
PAS = ("jour", "semaine", "mois")
SANS_TYPE = "(sans type)"


# ── Règles pures ──────────────────────────────────────────────────────────────

def canal(app, auteur, sujet=None) -> str:
    """Le canal d'un rendez-vous. PURE."""
    if not cint(app):
        return "manuel"
    if auteur == INVITE or (sujet or "").startswith("RDV portail"):
        return "app_client"
    return "app_magasin"


def pas_auto(debut, fin) -> str:
    """Le regroupement lisible pour cette durée (bornes incluses) : au plus une trentaine de barres. PURE."""
    jours = (getdate(fin) - getdate(debut)).days + 1
    return "jour" if jours <= 31 else "semaine" if jours <= 182 else "mois"


def _debut_periode(jour: datetime.date, pas: str) -> datetime.date:
    if pas == "semaine":
        return jour - datetime.timedelta(days=jour.weekday())
    if pas == "mois":
        return jour.replace(day=1)
    return jour


def _suivante(debut: datetime.date, pas: str) -> datetime.date:
    if pas == "semaine":
        return debut + datetime.timedelta(days=7)
    if pas == "mois":
        return (debut.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return debut + datetime.timedelta(days=1)


def periodes(debut, fin, pas: str) -> list[dict]:
    """Toutes les périodes de `debut` à `fin` (bornes incluses), même vides : [{cle, du, au, libelle}]. PURE.
    `du`/`au` sont bornés à l'intervalle demandé (une semaine entamée ne déborde pas)."""
    debut, fin = getdate(debut), getdate(fin)
    out, d = [], _debut_periode(debut, pas)
    while d <= fin:
        suivant = _suivante(d, pas)
        du, au = max(d, debut), min(suivant - datetime.timedelta(days=1), fin)
        libelle = (d.strftime("%m/%Y") if pas == "mois"
                   else "sem. du " + d.strftime("%d/%m") if pas == "semaine" else d.strftime("%d/%m"))
        out.append({"cle": str(d), "du": str(du), "au": str(au), "libelle": libelle})
        d = suivant
    return out


def _vide() -> dict:
    return dict({c: 0 for c in CLES}, total=0, annules=0)


def agreger(lignes: list, debut, fin, pas: str) -> dict:
    """Les compteurs de la période. PURE.

    `lignes` : [{"date" (jour retenu), "auteur", "app", "sujet", "type", "annule"}].
    → {"totaux", "periodes": [...], "utilisateurs": {auteur: compteurs}, "types": {type: compteurs},
       "annules": {canal: n}}. Les utilisateurs ne portent que « manuel » et « app_magasin » : un rendez-vous pris par
       le client n'a pas d'utilisateur."""
    cases = {p["cle"]: dict(p, **_vide()) for p in periodes(debut, fin, pas)}
    totaux, utilisateurs, types = _vide(), {}, {}
    annules = {c: 0 for c in CLES}
    for l in lignes:
        jour = getdate(l["date"])
        case = cases.get(str(_debut_periode(jour, pas)))
        if case is None:
            continue
        c = canal(l.get("app"), l.get("auteur"), l.get("sujet"))
        cibles = [totaux, case, types.setdefault(l.get("type") or SANS_TYPE, _vide())]
        if c != "app_client":
            cibles.append(utilisateurs.setdefault(l.get("auteur") or "", _vide()))
        for cible in cibles:
            cible[c] += 1
            cible["total"] += 1
            cible["annules"] += 1 if l.get("annule") else 0
        annules[c] += 1 if l.get("annule") else 0
    return {"totaux": totaux, "periodes": list(cases.values()), "utilisateurs": utilisateurs, "types": types,
            "annules": annules}


# ── Lecture ───────────────────────────────────────────────────────────────────

def _bornes(du, au):
    au = getdate(au or nowdate())
    du = getdate(du or add_days(au, -(JOURS_DEFAUT - 1)))
    if du > au:
        du, au = au, du
    if (au - du).days >= JOURS_MAX:
        du = add_days(au, -(JOURS_MAX - 1))
    return du, au


def _lignes(du, au, base: str) -> list:
    """Les rendez-vous dont la date retenue (`base`) tombe entre `du` et `au` inclus."""
    colonne = BASES[base]      # liste blanche : jamais une valeur venue de l'écran dans la requête
    return [{"date": str(r.jour)[:10], "auteur": r.owner, "app": r.app, "sujet": r.subject, "type": r.type_i,
             # Une intervention fermée parce que le technicien est reparti avec la machine (23/11/2026) n'est pas un
             # rendez-vous annulé : la machine suit son dossier dans Réparation osmoseurs.
             "annule": r.status == "Cancelled" and r.raison != RAISON_REPRISE}
            for r in frappe.db.sql(
                """SELECT t.{c} AS jour, t.owner, IFNULL(t.custom_reservation_app, 0) AS app, t.subject, t.status,
                          t.raison_annulation AS raison,
                          t.custom_type_dintervention AS type_i
                   FROM `tabTache de travail` t
                   WHERE t.{c} >= %s AND t.{c} < %s AND IFNULL(t.custom_client, '') <> ''
                     AND IFNULL(t.custom_type_dintervention, '') NOT IN %s
                     AND t.owner <> 'Administrator'""".format(c=colonne),
                (str(du), str(add_days(au, 1)), HC.TYPES_HORS_CLIENT), as_dict=True)]


def _triees(compteurs: dict, cle: str, noms: dict | None = None) -> list:
    return sorted(({cle: k, "nom": (noms or {}).get(k, k), **v} for k, v in compteurs.items()),
                  key=lambda x: (-x["total"], str(x["nom"])))


@frappe.whitelist()
def get_tableau(du=None, au=None, base="prise", pas=None):
    """Le tableau des rendez-vous pris (30 derniers jours par défaut), et la période précédente de même durée pour
    situer les chiffres."""
    frappe.only_for(ROLES)
    base = base if base in BASES else "prise"
    du, au = _bornes(du, au)
    pas = pas if pas in PAS else pas_auto(du, au)

    resultat = agreger(_lignes(du, au, base), du, au, pas)
    duree = (au - du).days + 1
    avant_au = add_days(du, -1)
    avant_du = add_days(avant_au, -(duree - 1))
    avant = agreger(_lignes(avant_du, avant_au, base), avant_du, avant_au, "mois")["totaux"]

    noms = HC._noms_utilisateurs(set(resultat["utilisateurs"]))
    return {
        "du": str(du), "au": str(au), "base": base, "pas": pas,
        "canaux": [{"cle": c, "libelle": _(l)} for c, l in CANAUX],
        "totaux": resultat["totaux"], "annules": resultat["annules"],
        "avant": dict(avant, du=str(avant_du), au=str(avant_au)),
        "periodes": resultat["periodes"],
        "utilisateurs": _triees(resultat["utilisateurs"], "user", noms),
        "types": _triees(resultat["types"], "type"),
        "hors": list(HC.TYPES_HORS_CLIENT),
    }
