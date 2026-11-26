"""Entonnoir des relances d'entretien : une photo par jour, pour voir une baisse en 1 jour au lieu de 2 mois.

Pourquoi (analyse « cartographie entretien » du 25/11/2026, copie de prod, amélioration n° 17) : la baisse des listes
d'appel d'octobre ne s'est vue qu'en novembre. Rien ne gardait la trace jour par jour : les comptes des crons ne sont
écrits que dans des fichiers du serveur de prod, l'Error Log ne garde que 30 jours, et un passage qui plante s'affiche
« Complete » (run_safely avale l'exception). Exemples mesurés : la liste Hors secteur LIST-06-11-2026-00004 est restée
en brouillon du 11/11 au 25/11 (183 clients relancés par SMS, jamais appelés), la Zone partenaire 25 jours en octobre,
sans aucune alerte ; 38 échéanciers n'avaient plus aucune visite à venir.

Ce que fait le module :
- `noter(source, compteurs, jour=None)` : les crons y déposent leurs compteurs du jour (« nuit », « listes », « sms »…),
  un enregistrement « Entonnoir Relances » par jour. Ne lève jamais : un cron ne doit pas tomber pour son journal ;
- `indicateurs(jour)` : la photo du jour lue en base, avec les fonctions de sélection EXISTANTES (aucune règle des
  listes ni des SMS n'est réécrite ici) ;
- `cron_photo()` : la photo enregistrée chaque jour (07:30 après les listes, 10:30 après les SMS) ; un e-mail
  (Config Relances › e-mail d'alerte) quand un signal rouge NOUVEAU apparaît ;
- `vue_page()` : ce que montre la page « Journaux des relances » (entonnoir du jour, tendance, signaux rouges).

Relecture du 26/11/2026 : SMS comptés d'après leur statut d'envoi (« Failed » à part : la panne WinSMS de décembre
2025 à avril 2026 ne se voyait pas) ; e-mail sur un signal qui passe d'orange à rouge ; plantage = titre exact du
filet run_safely ; brouillon Urgence en orange et seuil commun avec l'alerte de 07:00 ; référence des lignes écrêtée ;
crons arrêtés ou absents signalés une fois ; la page n'écrit plus d'Error Log ; rendez-vous du calendrier et de l'app
comptés dans les lignes appelées.

Les fonctions marquées PURE ne lisent pas la base (testées sans site).
"""
from __future__ import annotations

import datetime as _dt
import json
import statistics
from collections import Counter
from decimal import Decimal

import frappe
from frappe.utils import add_months, cint, get_datetime, getdate, now_datetime

from customization_app import relances_config as RC

DOCTYPE = "Entonnoir Relances"
LISTE_MAX = 50              # éléments gardés dans une liste fusionnée (noms de commandes en erreur…)

# Réglages de l'entonnoir (Config Relances ; les champs viennent après — défauts ici). Un seuil à 0 coupe son signal.
# Relecture du 26/11/2026 : le seuil des brouillons n'est plus un réglage de l'entonnoir. C'est `jours_brouillon_alerte`,
# celui de l'alerte de 07:00 (creation_liste_appelle) : avec deux réglages, la page et l'e-mail de 07:00 pouvaient ne
# plus donner le même verdict sur la même liste.
DEFAUTS_ENTONNOIR = {
    "entonnoir_seuil_lignes_pct": 60,        # lignes du jour < N % de la moyenne du même jour des semaines passées
    "entonnoir_semaines_reference": 4,       # … sur les N dernières semaines
    "entonnoir_min_moyenne_lignes": 3,       # pas de signal « lignes » si cette moyenne est sous N (mardi ≈ 1 ligne)
    "entonnoir_ecretage_reference": 2,       # chaque valeur de référence ramenée à N × la médiane des N semaines (0 = non)
    "entonnoir_seuil_echecs_sms_pct": 50,    # SMS en échec (Failed) ≥ N % des envois → rouge, en dessous orange (0 = non)
    "entonnoir_types_flux": "Normal\nUrgence",   # les listes du flux quotidien (sans les lots Hors secteur / Relance)
    "entonnoir_jours_tendance": 14,          # lignes du tableau de tendance
    "entonnoir_alerte_email": 1,             # e-mail quand un signal rouge nouveau apparaît (0 = jamais)
}
JOURS_BROUILLON_ALERTE = 3  # repli de creation_liste_appelle.JOURS_BROUILLON_ALERTE

STATUTS = ("relançable", "À requalifier", "Non", "Perdu", "reporté", "partenaire")
# Codes de creation_liste_appelle.MOTIFS_EXCLUSION → libellés des statuts ci-dessus (mêmes mots sur la page).
LIBELLES_MOTIFS = {"partenaire": "partenaire", "perdu": "Perdu", "a_requalifier": "À requalifier", "non_interesse": "Non",
                   "reporte": "reporté", "autre": "autre"}
CLASSES = (("normal", "Normal"), ("urgence", "Urgence"), ("zone", "Zone partenaire"), ("hors", "Hors secteur"))
JOURS_SEMAINE = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
JOURS_PLURIEL = ("lundis", "mardis", "mercredis", "jeudis", "vendredis", "samedis", "dimanches")
# Types dont un brouillon empêche la liste suivante : lus dans creation_liste_appelle.TYPES_BLOQUES_PAR_BROUILLON
# (types_bloquants) ; cette copie ne sert que si le module ne se charge pas. L'Urgence ne bloque rien.
TYPES_BLOQUANTS = ("Relance", "Zone partenaire", "Normal", "Requalification", "Hors secteur")
# Statuts d'envoi posés sur la visite par relance_maintenance_sms (marquer_visites) : « Success », « Failed »,
# « Invalid Number ». Vide = lignes de décembre 2023, d'avant le statut (154 sur la copie) : comptées comme parties.
SMS_REUSSI, SMS_INVALIDE = ("Success", ""), "Invalid Number"
FREQUENCES = {"Daily": "0 0 * * *", "Daily Long": "0 0 * * *", "Daily Maintenance": "0 0 * * *",
              "Hourly": "0 * * * *", "Hourly Long": "0 * * * *", "Weekly": "0 0 * * 0", "Weekly Long": "0 0 * * 0",
              "Monthly": "0 0 1 * *", "Monthly Long": "0 0 1 * *"}
TOLERANCE_MIN = 45          # un passage prévu à 07:00 sans trace à 07:45 est « manquant »
FENETRE_H = 3               # une trace (journal, erreur) se rattache au passage prévu dans les 3 h
CLE_LISTES, CLE_SMS = "creation_liste_appelle.run_cron", "relance_maintenance_sms.run_cron"
# Le titre EXACT que chaque cron donne à run_safely : seul ce titre au Journal des erreurs dit « le passage a planté ».
# Relecture du 26/11/2026 : creation_liste_appelle journalise aussi « Cron - Génération listes : alerte du brouillon … »,
# « … : alertes des brouillons », « … : entonnoir non mémorisé », des erreurs qui n'arrêtent PAS le passage ; le motif
# « Cron - Génération listes% » les classait en plantage (faux rouge et e-mail).
TITRES_FILET = {
    "update_schedule.run_cron": "Cron - Mise à jour échéancier maintenance",
    "creation_liste_appelle.run_cron": "Cron - Génération listes d'appels",
    "relance_maintenance_sms.run_cron": "Cron - Relance maintenance SMS",
    "rappel_rdv.cron_du_soir": "Cron - Rappel des rendez-vous du lendemain",
    "qualification_appels.run_cron": "Cron - Qualification IA des réponses d'appel",
    "sms_reveil.run_cron": "Cron - SMS de réveil",
    "entonnoir_relances.cron_photo": "Cron - Entonnoir des relances",
}

# Libellés des compteurs notés par les crons (page) ; une clé inconnue s'affiche telle quelle.
LIBELLES_SOURCES = {"nuit": "Passage de nuit (échéanciers)", "listes": "Listes d’appels (07:00)", "sms": "SMS de relance (10:00)",
                    "qualification": "Qualification IA (06:30)", "reveil": "SMS de réveil (10:05)",
                    "annulations_commande": "Annulations de commande", "retours_machines": "Retours de machines",
                    "patch_visites_faites": "Patch : visites faites restées en attente",
                    "patch_sel_porte_membrane": "Patch : sel / porte-membrane"}
# Le jour où la nuit a commencé à créer l'échéancier d'une machine vendue à un client « Non » (lot 4).
DEBUT_ECHEANCIER_DES_NON = "2026-11-26"
LIBELLES_CLES = {"total_sales_orders": "commandes lues", "new_ms_created": "échéanciers créés",
                 "updated_from_consumables": "visites soldées par un achat", "guessed": "échéanciers devinés",
                 "no_link": "achats sans échéancier", "already_covered": "commandes déjà couvertes", "errors": "erreurs",
                 "cleanup": "nettoyage", "corriges": "B2B corrigés", "supprimes": "supprimés", "erreurs": "erreurs",
                 "prolonges": "échéanciers prolongés", "requalifies": "clients passés « À requalifier »",
                 # Passage SMS de 10:00 (26/11/2026).
                 "sms_evites": "SMS évités (un par client)", "clients_plusieurs_sms": "clients à plusieurs SMS (ancien envoi)",
                 "envois_candidats": "SMS à envoyer", "visites_marquees": "visites marquées",
                 "marquages_secours": "échéanciers marqués sans validation (fiche refusée)",
                 "sms_hors_plafond": "SMS coupés par le plafond", "clients_exclus": "clients exclus",
                 "exclus_partenaire": "exclus : partenaire", "exclus_a_requalifier": "exclus : À requalifier",
                 "exclus_perdu": "exclus : Perdu", "exclus_non_interesse": "exclus : Non à l'entretien",
                 "refus_sms": "clients « Non aux SMS » (visites marquées sans envoi)", "exclus_reporte": "exclus : relance reportée",
                 "exclus_rendez_vous": "exclus : rendez-vous", "exclus_autre": "exclus : autre",
                 # Passage de nuit, lots 4 et 5 (26/11/2026).
                 "new_ms_non_interesses": "échéanciers créés pour des clients « Non »",
                 "devines_non_interesses": "échéanciers devinés pour des clients « Non » qui paient un entretien",
                 "devines_annules": "devinettes annulées (vraie machine)", "avances_evitees": "avances évitées (sans main-d’œuvre)",
                 "porte_filtres_avec_machine": "porte-filtres soldés avec la machine",
                 "achats_anterieurs": "achats antérieurs à la machine",
                 "pas_de_systeme": "commandes lues sans création (réponse « pas de système »)",
                 "achats_avant_annulation": "achats antérieurs à l’annulation d’un échéancier (pas de devinette)",
                 "annules": "annulés", "brouillons_laisses": "brouillons B2B laissés",
                 # Retours de machines / annulations de commande.
                 "echeanciers_retablis": "échéanciers rétablis (machine revenue)",
                 "lignes_listes_brouillon": "lignes de listes brouillon dégagées"}


# ═════════════════════════════════════════════════════════════════════════════
# Réglages
# ═════════════════════════════════════════════════════════════════════════════

def types_bloquants() -> tuple:
    """Les types de liste dont un brouillon bloque la suivante : ceux de creation_liste_appelle (la règle elle-même),
    sinon la copie TYPES_BLOQUANTS."""
    try:
        from customization_app.Maintenance import creation_liste_appelle as CL
        return tuple(CL.TYPES_BLOQUES_PAR_BROUILLON)
    except Exception:
        return TYPES_BLOQUANTS


def reglages(cfg: dict | None = None) -> dict:
    """Les réglages de l'entonnoir, lus par RC.config().get(clé, défaut) : tant que les champs n'existent pas dans
    Config Relances (et dans RC.DEFAUTS, sans quoi config() ne les rend pas), ce sont les défauts ci-dessus. PURE avec
    `cfg` (sauf la lecture des types bloquants dans creation_liste_appelle).
    `seuil_brouillon_jo` = `jours_brouillon_alerte` de l'alerte de 07:00 (un seul réglage pour la même règle, 26/11/2026)."""
    if cfg is None:
        try:
            cfg = RC.config()
        except Exception:
            cfg = {}
    out = {}
    for cle, defaut in DEFAUTS_ENTONNOIR.items():
        v = cfg.get(cle, defaut)
        if isinstance(defaut, int):
            out[cle] = cint(v) if v not in (None, "") else defaut
        else:
            out[cle] = v if str(v or "").strip() else defaut
    out["types_flux"] = RC.liste(out["entonnoir_types_flux"])
    try:
        from customization_app.Maintenance import creation_liste_appelle as CL
        repli = cint(getattr(CL, "JOURS_BROUILLON_ALERTE", JOURS_BROUILLON_ALERTE))
    except Exception:
        repli = JOURS_BROUILLON_ALERTE
    v = cfg.get("jours_brouillon_alerte", repli)
    out["seuil_brouillon_jo"] = cint(v) if v not in (None, "") else repli
    out["types_bloquants"] = list(types_bloquants())
    return out


# ═════════════════════════════════════════════════════════════════════════════
# Règles pures
# ═════════════════════════════════════════════════════════════════════════════

def propre(v, profondeur: int = 0):
    """Une valeur sûre pour JSON : dates en texte, ensembles triés, Decimal en nombre, le reste en texte court. PURE.
    Un compteur « bizarre » (objet, date, set) ne doit jamais faire échouer `noter`."""
    if profondeur > 8:
        return str(v)[:200]
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if v == v and v not in (float("inf"), float("-inf")) else None
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (_dt.date, _dt.datetime, _dt.time, _dt.timedelta)):
        return str(v)
    if isinstance(v, dict):
        return {str(k): propre(x, profondeur + 1) for k, x in v.items()}
    if isinstance(v, (set, frozenset)):
        return sorted((propre(x, profondeur + 1) for x in v), key=str)
    if isinstance(v, (list, tuple)):
        return [propre(x, profondeur + 1) for x in v]
    return str(v)[:200]


def _nombre(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def fusionner(ancien, nouveau):
    """Les compteurs d'un 2e passage le même jour (bouton « Générer », relance à la main) S'AJOUTENT au premier : un
    compteur dit ce qui a été FAIT dans la journée ; écraser 30 SMS du cron par les 0 d'une relance à la main ferait
    croire qu'aucun SMS n'est parti. Dictionnaires fusionnés clé par clé, nombres additionnés, listes réunies (sans
    doublon, LISTE_MAX derniers), le reste (texte, vrai/faux) remplacé par la dernière valeur. PURE."""
    if isinstance(ancien, dict) and isinstance(nouveau, dict):
        out = dict(ancien)
        for k, v in nouveau.items():
            out[k] = fusionner(out[k], v) if k in out else v
        return out
    if _nombre(ancien) and _nombre(nouveau):
        return ancien + nouveau
    if isinstance(ancien, list) and isinstance(nouveau, list):
        out = list(ancien)
        for x in nouveau:
            if x not in out:
                out.append(x)
        return out[-LISTE_MAX:]
    return nouveau


def maj_source(sources: dict | None, source: str, compteurs: dict, heure: str) -> dict:
    """Les sources du jour avec le passage de `source` ajouté : nombre de passages, heures du premier et du dernier,
    compteurs cumulés (fusionner) et compteurs du dernier passage tels quels (un compteur qui est une photo — « 412
    candidats » — se lit là, sans cumul). PURE."""
    out = dict(sources or {})
    avant = out.get(source) if isinstance(out.get(source), dict) else {}
    out[source] = {"passages": cint(avant.get("passages")) + 1, "premier": avant.get("premier") or heure, "dernier": heure,
                   "compteurs": fusionner(avant.get("compteurs") or {}, compteurs), "dernier_passage": compteurs}
    return out


def jours_ouvres_entre(debut, fin) -> int:
    """Jours ouvrés PLEINS (lundi–samedi) strictement entre le jour de `debut` et celui de `fin` : ni le jour de départ
    ni le jour en cours ne comptent. Modifié un vendredi → 2 le mardi (samedi, lundi), 3 le mercredi. PURE.
    Même règle que l'alerte de 07:00 des brouillons immobiles (creation_liste_appelle.jours_ouvres_ecoules, 25/11/2026) :
    la page et l'e-mail donnent le même nombre. Le dimanche ne compte pas (ni liste ni SMS)."""
    if not debut or not fin:
        return 0
    j, f = getdate(debut) + _dt.timedelta(days=1), getdate(fin)
    n = 0
    while j < f:
        n += j.weekday() != 6
        j += _dt.timedelta(days=1)
    return n


def nombre(x, decimales: int = 1) -> str:
    """12.25 → « 12,3 » (affichage à la française). PURE."""
    return (("%." + str(int(decimales)) + "f") % float(x or 0)).replace(".", ",")


def statut_relance(client: dict, jour, relancer_non: bool = False) -> str:
    """Le statut de relance d'un client, un seul, dans l'ordre de l'analyse du 25/11/2026 (partenaire, À requalifier,
    Perdu, Non à l'entretien, reporté, sinon relançable). `client` : {gere, statut, interesse, reporte_au}. PURE."""
    client = client or {}
    if cint(client.get("gere")):
        return "partenaire"
    statut = (client.get("statut") or "").strip()
    if statut == "À requalifier":
        return "À requalifier"
    if statut == "Perdu":
        return "Perdu"
    if not relancer_non and (client.get("interesse") or "").strip().upper() == "NON":
        return "Non"
    reporte = client.get("reporte_au")
    if reporte and getdate(reporte) > getdate(jour):
        return "reporté"
    return "relançable"


def reference_meme_jour(par_jour: dict, jour, semaines: int = 4, ecretage: float = 0):
    """Moyenne du MÊME jour de la semaine sur les `semaines` précédentes → (moyenne, valeurs, plafond) ; (None, [], None)
    sans donnée. PURE. Pourquoi le même jour : la liste du mardi est vide par construction (aucun SMS le dimanche : 0 à
    2 lignes Normal les mardis d'octobre-novembre 2026) et le lundi est la plus grosse (7 à 31) ; contre la moyenne de
    tous les jours, chaque mardi serait rouge.
    `ecretage` > 0 : chaque valeur est ramenée à `ecretage` × la médiane des valeurs (`plafond`, None si rien n'est
    ramené). Relecture du 26/11/2026 : la liste de rattrapage de 100 lignes du vendredi 25/09/2026 portait la moyenne de
    référence à 25-30 pendant 4 semaines, et 22 jours sur 57 du 20/09 au 25/11 sortaient en rouge pour cette seule
    raison (vendredis à 9, 9 et 6 lignes). La moyenne décidée reste, sans le jour hors norme."""
    vals = []
    for k in range(1, max(1, cint(semaines)) + 1):
        d = str(getdate(jour) - _dt.timedelta(days=7 * k))
        if d in par_jour:
            vals.append(par_jour[d] or 0)
    if not vals:
        return None, [], None
    plafond = None
    if ecretage and ecretage > 0:
        p = ecretage * statistics.median(vals)
        if any(v > p for v in vals):
            plafond = p
    retenues = [min(v, plafond) for v in vals] if plafond is not None else vals
    return sum(retenues) / len(retenues), vals, plafond


def appels_des_versions(versions) -> dict:
    """{jour: {lignes « Appelle Client » passées à « a été appelé » ce jour-là}} depuis les Version des listes. PURE.
    Les cases n'ont pas de date ; la visite marquée `custom_appelle` en perd (21 visites marquées contre 28 lignes
    cochées le 23/11/2026) : la trace des enregistrements est la meilleure mesure du travail de la journée. Elle ne voit
    pas les lignes cochées par frappe.db.set_value (« Prendre RDV » du calendrier, RDV pris dans l'app) : voir
    ajouter_rdv_sans_version."""
    out = {}
    for creation, data in versions or ():
        try:
            d = json.loads(data or "{}")
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        jour = str(getdate(creation))
        for rc in d.get("row_changed") or []:
            if not isinstance(rc, (list, tuple)) or len(rc) < 4 or rc[0] != "clients":
                continue
            for ch in rc[3] or []:
                if isinstance(ch, (list, tuple)) and len(ch) >= 3 and ch[0] == "a_été_appelé" and cint(ch[2]) and not cint(ch[1]):
                    out.setdefault(jour, set()).add(rc[2])
        for ad in d.get("added") or []:
            if isinstance(ad, (list, tuple)) and len(ad) >= 2 and ad[0] == "clients" and isinstance(ad[1], dict) \
                    and cint(ad[1].get("a_été_appelé")) and ad[1].get("name"):
                out.setdefault(jour, set()).add(ad[1]["name"])
    return out


def ajouter_rdv_sans_version(appels: dict, rdv, marques) -> dict:
    """Ajoute aux appels du jour les lignes qui ont pris un rendez-vous SANS laisser de Version : {jour: nombre ajouté}.
    PURE (complète `appels` en place). `rdv` : [(ligne, jour de création de sa tâche)] des lignes cochées « appelé »
    qui portent une tâche ; `marques` : les lignes qu'une Version a déjà vues passer à « appelé » (quel que soit le jour).
    Relecture du 26/11/2026 : « Prendre RDV » du calendrier et le RDV pris dans l'app (portail_rdv._lier_origine)
    cochent la ligne par frappe.db.set_value, sans Version ; sur la copie, 73 des 108 lignes « Rendez-vous pris »
    cochées depuis le 01/10/2026 n'ont aucune Version (LIST-24-11-2026-00001 : RDV Tache-09562 à 10:03, liste validée à
    10:06 sans changement de ligne). Le jour retenu est celui de la tâche, créée à la prise du rendez-vous. Une ligne
    déjà vue dans une Version garde le jour de sa Version (pas de double compte)."""
    marques = set(marques or ())
    ajout = {}
    for ligne, jour in rdv or ():
        if not ligne or not jour or ligne in marques:
            continue
        j = str(getdate(jour))
        deja = appels.setdefault(j, set())
        if ligne not in deja:
            deja.add(ligne)
            ajout[j] = ajout.get(j, 0) + 1
    return ajout


def classe_statut_sms(statut) -> str:
    """Le statut d'envoi posé sur la visite → « ok » · « invalide » (pas de numéro) · « echec » (WinSMS n'a pas
    répondu « Successfully Send »). PURE. Un statut inconnu compte comme un échec : ce compte sert à voir une panne."""
    s = (statut or "").strip()
    if s in SMS_REUSSI:
        return "ok"
    if s in (SMS_INVALIDE, "Refus SMS"):           # « Refus SMS » : client « Non aux SMS », rien n'est parti (26/11/2026)
        return "invalide"
    return "echec"


def passages_prevus(cron_format: str, jour, frequence: str | None = None) -> list:
    """Les heures de passage prévues par le planificateur un jour donné (croniter, comme Frappe). PURE."""
    fmt = (cron_format or "").strip() or FREQUENCES.get(frequence or "", "")
    if not fmt:
        return []
    from croniter import croniter
    j = getdate(jour)
    debut = _dt.datetime.combine(j, _dt.time.min) - _dt.timedelta(seconds=1)
    fin = _dt.datetime.combine(j, _dt.time.max)
    it, out = croniter(fmt, debut), []
    while len(out) < 48:
        t = it.get_next(_dt.datetime)
        if t > fin:
            break
        out.append(t)
    return out


def etat_passages(prevus, journaux, plantages, maintenant, tolerance_min: int = TOLERANCE_MIN, fenetre_h: int = FENETRE_H) -> list:
    """Chaque passage prévu → son état. PURE. `journaux` : [{status, creation}] du Scheduled Job Log ; `plantages` :
    [creation] des Error Log au titre exact du filet run_safely (est_plantage : un plantage s'y voit, le journal dit
    « Complete »).
    États : a_venir · en_cours · ok · plantage · echec (Failed) · interrompu (« Start » depuis plus de 3 h) · manquant."""
    maintenant = get_datetime(maintenant)
    out = []
    for t in prevus:
        t = get_datetime(t)
        proches = [j for j in journaux or () if t - _dt.timedelta(minutes=10) <= get_datetime(j["creation"]) <= t + _dt.timedelta(hours=fenetre_h)]
        if not proches:
            etat = "a_venir" if maintenant < t + _dt.timedelta(minutes=tolerance_min) else "manquant"
            out.append({"heure": str(t)[11:16], "prevu": str(t)[:16], "etat": etat})
            continue
        j = min(proches, key=lambda x: abs((get_datetime(x["creation"]) - t).total_seconds()))
        debut = get_datetime(j["creation"])
        statut = j.get("status") or ""
        if statut == "Failed":
            etat = "echec"
        elif statut == "Start":
            etat = "interrompu" if maintenant - debut > _dt.timedelta(hours=fenetre_h) else "en_cours"
        else:
            etat = "ok"
        if etat in ("ok", "en_cours") and any(debut - _dt.timedelta(minutes=1) <= get_datetime(p) <= debut + _dt.timedelta(hours=fenetre_h)
                                              for p in plantages or ()):
            etat = "plantage"
        out.append({"heure": str(t)[11:16], "prevu": str(t)[:16], "etat": etat, "statut": statut, "debut": str(debut)[:19]})
    return out


ETATS_KO = ("manquant", "echec", "interrompu", "plantage")


def _resolu(flux_jour: dict, cle: str):
    """Le passage `cle` du jour est-il passé (bien ou mal) ? True / False (pas encore, pas de passage ce jour-là, ou
    cron ARRÊTÉ) / None (cron absent du planificateur ou passages illisibles : le repli lundi–samedi s'applique). PURE.
    Relecture du 26/11/2026 : un cron arrêté (« Stopped ») retombait sur le repli, d'où un rouge « aucun SMS » neuf
    chaque jour (un e-mail par jour) ; il a désormais son propre signal stable (« arret:<cle> »)."""
    for p in (flux_jour or {}).get("passages") or []:
        if p.get("cle") != cle:
            continue
        if p.get("installe"):
            prevus = p.get("prevus") or []
            if not prevus:
                return False
            return all(h["etat"] not in ("a_venir", "en_cours") for h in prevus)
        if p.get("arrete"):
            return False
        return None
    return None


def _jj(d) -> str:
    d = getdate(d)
    return "%s %02d/%02d" % (JOURS_SEMAINE[d.weekday()], d.day, d.month)


def jour_sms_a_controler(flux: dict, jour, maintenant):
    """Le dernier jour dont le passage SMS de 10:00 est fini : aujourd'hui s'il est passé, sinon le dernier jour de
    passage avant. Sans trace du planificateur (cron absent), lundi–samedi et 10:30 passées. Cron ARRÊTÉ : None (le
    signal « arret:<cle> » le dit, une fois). PURE."""
    jour, maintenant = getdate(jour), get_datetime(maintenant)
    for k in range(0, 8):
        d = jour - _dt.timedelta(days=k)
        fj = flux.get(str(d))
        if fj is None:
            return None
        r = _resolu(fj, CLE_SMS)
        if r is None:
            r = d.weekday() != 6 and (d < maintenant.date() or (maintenant.hour, maintenant.minute) >= (10, 30))
        if r:
            return str(d)
    return None


def sms_du_jour(f: dict) -> dict:
    """Les SMS d'un jour par issue (visites marquées) : {ok, echec, invalide, envois}. PURE. `envois` = réussis +
    échecs (un numéro invalide n'est pas un envoi : c'est une fiche sans téléphone)."""
    f = f or {}
    ok = cint(f.get("sms1")) + cint(f.get("sms2"))
    echec = cint(f.get("sms1_echec")) + cint(f.get("sms2_echec"))
    invalide = cint(f.get("sms1_invalide")) + cint(f.get("sms2_invalide"))
    return {"ok": ok, "echec": echec, "invalide": invalide, "envois": ok + echec}


def cles_rouges(signaux_liste) -> set:
    """Les clés des signaux ROUGES d'une photo enregistrée. PURE. Relecture du 26/11/2026 : les précédents étaient pris
    tous niveaux confondus ; un brouillon orange (encore travaillé) qui devenait rouge (immobile) gardait la même clé
    et l'e-mail ne partait jamais (LIST-06-11-2026-00004 : orange le 14/11, rouge le 16/11, aucun e-mail)."""
    return {s.get("cle") for s in signaux_liste or () if isinstance(s, dict) and s.get("niveau") == "rouge" and s.get("cle")}


def signaux(photo: dict, flux: dict, reg: dict, maintenant, ordonnanceur_inactif: bool = False) -> list:
    """Les signaux de la photo, rouges d'abord. PURE (photo + flux par jour + réglages).
    Rouges (demande du 25/11/2026) : brouillon d'un type BLOQUANT immobile depuis ≥ 3 jours ouvrés (même règle et même
    réglage que l'alerte de 07:00), échéancier dont un article n'a plus de visite à venir, passage en échec / manquant /
    planté, cron suivi arrêté ou absent du planificateur, planificateur à l'arrêt, aucun SMS RÉUSSI un jour de passage,
    SMS en échec au-dessus du seuil, lignes du jour sous 60 % de la moyenne (écrêtée) du même jour des 4 dernières
    semaines.
    Oranges : brouillon ouvert depuis ≥ 3 jours ouvrés mais encore travaillé (il bloque quand même son type), brouillon
    Urgence immobile (il ne bloque rien), quelques SMS en échec, erreurs sur des éléments d'un passage, machine livrée à
    un client « Oui » restée sans échéancier après la nuit.
    Relecture du 26/11/2026 : SMS comptés d'après leur statut (de décembre 2025 à avril 2026, AUCUN SMS réussi sur la
    copie, ~1 350 visites « Failed », et l'ancien compte n'aurait rien vu) ; Urgence en orange ; crons arrêtés."""
    out = []
    photo, flux = photo or {}, flux or {}
    jour = getdate(photo.get("jour"))
    veille = jour - _dt.timedelta(days=1)
    appel = photo.get("appel") or {}
    attente = {v.get("type"): cint(v.get("en_attente")) for v in (appel.get("a_appeler") or [])}
    bloquants = set(reg.get("types_bloquants") or TYPES_BLOQUANTS)

    seuil = cint(reg.get("seuil_brouillon_jo"))
    if seuil > 0:
        for b in photo.get("brouillons") or []:
            age, inactif = cint(b.get("age_jo")), b.get("inactif_jo")
            immobile = cint(inactif if inactif is not None else age)
            if age < seuil and immobile < seuil:
                continue
            bloque = b.get("type") in bloquants
            if inactif is None:          # jour passé reconstitué : seule la date de création est connue
                texte = "Brouillon %s « %s » ouvert depuis %d jour(s) ouvré(s) ce jour-là (créé le %s) : %d ligne(s) appelée(s) sur %d aujourd’hui" % (
                    b.get("name"), b.get("type"), age, _jj(b.get("cree_le")), cint(b.get("appelees")), cint(b.get("lignes")))
            elif immobile >= seuil:
                texte = "Brouillon %s « %s » immobile depuis %d jour(s) ouvré(s) (créé le %s) : %d ligne(s) appelée(s) sur %d" % (
                    b.get("name"), b.get("type"), immobile, _jj(b.get("cree_le")), cint(b.get("appelees")), cint(b.get("lignes")))
            else:
                texte = "Brouillon %s « %s » ouvert depuis %d jour(s) ouvré(s), encore travaillé : %d ligne(s) appelée(s) sur %d" % (
                    b.get("name"), b.get("type"), age, cint(b.get("appelees")), cint(b.get("lignes")))
            if bloque:
                texte += " ; aucune nouvelle liste « %s » tant qu’il est ouvert" % b.get("type")
            else:
                texte += " ; ce type ne bloque aucune liste"
            if attente.get(b.get("type")):
                texte += " — %d client(s) attendent derrière" % attente[b.get("type")]
            out.append({"cle": "brouillon:%s" % b.get("name"), "niveau": "rouge" if (bloque and immobile >= seuil) else "orange",
                        "texte": texte, "lien": "/app/liste-appelle-entretien/%s" % b.get("name")})

    sv = photo.get("sans_visite_a_venir") or {}
    muets = cint(sv.get("a_prolonger") if "a_prolonger" in sv else sv.get("echeanciers"))
    if muets:
        texte = "%d échéancier(s) ont un article sans visite à venir que la prolongation de nuit doit ranimer (%d client(s) relançable(s))" % (
            muets, cint(sv.get("relancables")))
        if cint(sv.get("geles")):
            texte += " ; %d autre(s) laissé(s) gelé(s) exprès (jumeau / porte-filtre), sans alerte" % cint(sv["geles"])
        out.append({"cle": "sans_visite", "niveau": "rouge", "lien": "/app/maintenance-schedule", "texte": texte})

    if ordonnanceur_inactif:
        out.append({"cle": "ordonnanceur", "niveau": "rouge", "lien": "/app/scheduled-job-log",
                    "texte": "Le planificateur est en pause ou désactivé sur ce site : aucun cron ne passe"})
    # Un cron arrêté ou absent : UN signal à clé stable (un seul e-mail), lu sur le jour de la photo (état présent).
    for p in (flux.get(str(jour)) or {}).get("passages") or []:
        if p.get("arrete"):
            out.append({"cle": "arret:%s" % p["cle"], "niveau": "rouge", "lien": "/app/scheduled-job-type/%s" % p["cle"],
                        "texte": "%s est arrêté (« Stopped ») dans le planificateur : aucun passage tant qu’il l’est" % p.get("titre")})
        elif p.get("absent"):
            out.append({"cle": "arret:%s" % p["cle"], "niveau": "rouge", "lien": "/app/scheduled-job-type",
                        "texte": "%s est absent du planificateur (tâche planifiée %s introuvable : hooks.py ou migration)" % (
                            p.get("titre"), p["cle"])})
    for d in (veille, jour):
        for p in (flux.get(str(d)) or {}).get("passages") or []:
            for h in p.get("prevus") or []:
                if h["etat"] not in ETATS_KO or (ordonnanceur_inactif and h["etat"] == "manquant"):
                    continue
                quoi = {"manquant": "pas de passage", "echec": "passage en échec (Failed)", "interrompu": "passage interrompu (resté « Start »)",
                        "plantage": "passage planté (erreur « %s » au Journal des erreurs)" % (p.get("filet") or "Cron - …")}[h["etat"]]
                out.append({"cle": "passage:%s:%s" % (p["cle"], h["prevu"]), "niveau": "rouge", "lien": "/app/scheduled-job-log",
                            "texte": "%s : %s le %s à %s" % (p.get("titre"), quoi, _jj(d), h["heure"])})
            if cint(p.get("erreurs_elements")):
                out.append({"cle": "erreurs:%s:%s" % (p["cle"], d), "niveau": "orange", "lien": "/app/error-log",
                            "texte": "%s : %d erreur(s) sur des éléments le %s (%s)" % (
                                p.get("titre"), cint(p["erreurs_elements"]), _jj(d), ", ".join(p.get("exemples_erreurs") or [])[:160])})

    d_sms = jour_sms_a_controler(flux, jour, maintenant)
    if d_sms:
        s = sms_du_jour(flux.get(d_sms))
        if s["ok"] == 0:
            if s["echec"] or s["invalide"]:
                texte = ("Aucun SMS de relance RÉUSSI le %s, jour de passage du cron de 10:00 : %d envoi(s) en échec (« Failed »), "
                         "%d sans numéro valide — crédit WinSMS épuisé ou API hors service ?") % (_jj(d_sms), s["echec"], s["invalide"])
            else:
                texte = "Aucun SMS de relance (SMS 1 ni SMS 2) le %s, jour de passage du cron de 10:00" % _jj(d_sms)
            out.append({"cle": "sms:%s" % d_sms, "niveau": "rouge", "lien": "/app/journaux-relances", "texte": texte})
        elif s["echec"] and cint(reg.get("entonnoir_seuil_echecs_sms_pct")) > 0:
            part = 100.0 * s["echec"] / s["envois"]
            rouge = part >= cint(reg.get("entonnoir_seuil_echecs_sms_pct"))
            out.append({"cle": "sms_echecs:%s" % d_sms, "niveau": "rouge" if rouge else "orange", "lien": "/app/journaux-relances",
                        "texte": "SMS de relance du %s : %d en échec (« Failed ») sur %d envoi(s), soit %s %% (seuil rouge : %d %%)" % (
                            _jj(d_sms), s["echec"], s["envois"], nombre(part, 0), cint(reg.get("entonnoir_seuil_echecs_sms_pct")))})

    pct, mini = cint(reg.get("entonnoir_seuil_lignes_pct")), cint(reg.get("entonnoir_min_moyenne_lignes"))
    fj = flux.get(str(jour))
    resolu = _resolu(fj, CLE_LISTES) if fj is not None else False
    if resolu is None:
        m = get_datetime(maintenant)
        resolu = jour.weekday() != 6 and (jour < m.date() or (m.hour, m.minute) >= (7, 30))
    if pct > 0 and fj is not None and resolu:
        ref, vals, plafond = reference_meme_jour({d: (v or {}).get("lignes_flux", 0) for d, v in flux.items()}, jour,
                                                 reg.get("entonnoir_semaines_reference", 4), reg.get("entonnoir_ecretage_reference", 0))
        n = cint(fj.get("lignes_flux"))
        if ref is not None and ref >= max(mini, 0.0001) and n < ref * pct / 100.0:
            ecrete = " ; valeurs ramenées à %s (%d × la médiane)" % (nombre(plafond), cint(reg.get("entonnoir_ecretage_reference"))) if plafond is not None else ""
            out.append({"cle": "lignes:%s" % jour, "niveau": "rouge", "lien": "/app/liste-appelle-entretien",
                        "texte": "Lignes du jour (%s) : %d, contre %s en moyenne les %d derniers %s (%s%s) — sous le seuil de %d %% (%s)" % (
                            " + ".join(reg.get("types_flux") or []), n, nombre(ref), len(vals), JOURS_PLURIEL[jour.weekday()],
                            ", ".join(str(v) for v in vals), ecrete, pct, nombre(ref * pct / 100.0))})

    mach = ((photo.get("flux") or {}).get("veille") or {}).get("machines") or {}
    if mach.get("sans"):
        out.append({"cle": "machines:%s" % veille, "niveau": "orange", "lien": "/app/delivery-note",
                    "texte": "%d machine(s) livrée(s) le %s à un client suivi sans échéancier de sa famille après la nuit : %s" % (
                        len(mach["sans"]), _jj(veille), ", ".join(mach["sans"][:6]))})
    return sorted(out, key=lambda s: 0 if s["niveau"] == "rouge" else 1)


def scalaires(photo: dict, sig: list, reg: dict) -> dict:
    """Les chiffres-clés gardés en colonnes (liste, rapport, graphique de l'enregistrement du jour). PURE.
    `brouillons_anciens` = brouillons en rouge (bloquants immobiles, 26/11/2026) ; `sms_envoyes` = SMS RÉUSSIS du jour,
    `sms_echecs` = envois en échec (« Failed »)."""
    photo = photo or {}
    ve = photo.get("visites_echues") or {}
    appel = photo.get("appel") or {}
    sv = photo.get("sans_visite_a_venir") or {}
    fj = (photo.get("flux") or {}).get("jour") or {}
    s = sms_du_jour(fj)
    return {
        "visites_echues": cint(ve.get("lignes")),
        "visites_relancables": cint(((ve.get("par_statut") or {}).get("relançable") or {}).get("lignes")),
        "candidats": cint(appel.get("candidats")),
        "a_appeler": sum(cint(v.get("clients")) for v in appel.get("a_appeler") or []),
        "en_attente": sum(cint(v.get("en_attente")) for v in appel.get("a_appeler") or []),
        "echeanciers_sans_visite": cint(sv.get("a_prolonger") if "a_prolonger" in sv else sv.get("echeanciers")),
        "brouillons_anciens": sum(1 for x in sig or [] if x.get("niveau") == "rouge" and str(x.get("cle") or "").startswith("brouillon:")),
        "lignes_creees": cint(fj.get("lignes")),
        "sms_envoyes": s["ok"],
        "sms_echecs": s["echec"],
        "nb_signaux": sum(1 for x in sig or [] if x.get("niveau") == "rouge"),
    }


def tendance(jour, n: int, flux: dict, stockes: dict, maintenant=None) -> list:
    """Les `n` derniers jours, du plus récent au plus ancien : les flux relus en base (listes, appels, SMS, machines
    → échéanciers) et, quand la photo du jour a été enregistrée, les stocks de ce jour-là (visites échues, clients en
    attente, échéanciers sans visite, brouillons bloqués) — un stock ne se reconstitue pas après coup. `sms_ko` : aucun
    SMS RÉUSSI un jour où le passage de 10:00 a eu lieu (ou aurait dû) ; un cron arrêté n'en attend pas. PURE."""
    out = []
    jour = getdate(jour)
    maintenant = get_datetime(maintenant or _dt.datetime.combine(jour, _dt.time.max))
    for k in range(max(1, cint(n))):
        d = jour - _dt.timedelta(days=k)
        f = flux.get(str(d)) or {}
        veille = flux.get(str(d - _dt.timedelta(days=1))) or {}
        # Une ligne créée par un noter() sans photo ce jour-là a ses colonnes à 0 : ce ne sont pas des stocks mesurés.
        st = stockes.get(str(d)) or {}
        s = {cle: v for cle, v in st.items() if cle not in ("sources", "name", "jour")} if st.get("photo_le") else None
        attendu = _resolu(f, CLE_SMS)
        if attendu is None:
            attendu = d.weekday() != 6 and (d < maintenant.date() or (maintenant.hour, maintenant.minute) >= (10, 30))
        sms = sms_du_jour(f)
        out.append({"jour": str(d), "libelle": _jj(d), "dimanche": d.weekday() == 6,
                    "sms_ko": bool(attendu) and not sms["ok"],
                    "listes": f.get("listes") or {}, "lignes": cint(f.get("lignes")), "lignes_flux": cint(f.get("lignes_flux")),
                    "appelees": cint(f.get("appelees")), "appelees_rdv": cint(f.get("appelees_rdv")), "validees": cint(f.get("validees")),
                    "sms1": cint(f.get("sms1")), "sms2": cint(f.get("sms2")), "sms_echecs": sms["echec"], "sms_invalides": sms["invalide"],
                    "machines_veille": (veille.get("machines") or {}).get("lignes"), "machines_attendues": (veille.get("machines") or {}).get("attendues"),
                    "nes": f.get("nes") or {}, "erreurs": cint(f.get("erreurs")),
                    "passages_ko": sum(1 for p in f.get("passages") or [] for h in p.get("prevus") or [] if h["etat"] in ETATS_KO),
                    "stock": s})
    return out


# ═════════════════════════════════════════════════════════════════════════════
# Lectures en base
# ═════════════════════════════════════════════════════════════════════════════

def _a_colonne(doctype: str, colonne: str) -> bool:
    try:
        return bool(frappe.db.has_column(doctype, colonne))
    except Exception:
        return False


def _colonnes_client(alias: str = "c") -> str:
    """Les champs de relance du client en SQL ; un champ absent vaut sa valeur neutre (site sans le champ)."""
    a = alias
    parts = [
        ("max(ifnull(%s.`custom_gere_par_partenaire`, 0)) as gere" % a) if _a_colonne("Customer", "custom_gere_par_partenaire") else "0 as gere",
        ("max(ifnull(%s.custom_statut_relance, '')) as statut" % a) if _a_colonne("Customer", "custom_statut_relance") else "'' as statut",
        "max(ifnull(%s.`custom_intéressé_par_le_service_entretien`, '')) as interesse" % a,
        ("max(%s.custom_relance_reportee_au) as reporte_au" % a) if _a_colonne("Customer", "custom_relance_reportee_au") else "null as reporte_au",
    ]
    return ", ".join(parts)


def visites_echues(jour, cfg: dict | None = None) -> dict:
    """Le stock des visites échues non faites au matin de `jour`, par statut de relance du client. Le 25/11/2026 : 4 574
    visites chez 1 345 clients, dont 65 % de plus de 6 mois et 44 % chez des clients exclus. Pour un jour passé,
    reconstitué d'après les dates (visites réalisées depuis, lignes ajoutées depuis) ; le statut du client est celui
    d'aujourd'hui (il n'a pas d'historique)."""
    cfg = cfg or RC.config()
    jour = getdate(jour)
    rows = frappe.db.sql("""select ms.customer, count(*) as lignes, sum(d.scheduled_date < %(six)s) as vieilles, """ + _colonnes_client() + """
                            from `tabMaintenance Schedule Detail` d
                            join `tabMaintenance Schedule` ms on ms.name = d.parent and ms.docstatus = 1
                            left join `tabCustomer` c on c.name = ms.customer
                            where d.scheduled_date < %(jour)s and (d.actual_date is null or d.actual_date >= %(jour)s)
                              and d.creation < %(lendemain)s
                            group by ms.customer""",
                         {"jour": jour, "lendemain": jour + _dt.timedelta(days=1), "six": add_months(jour, -6)}, as_dict=True)
    relancer_non = bool(cint(cfg.get("relancer_non_interesses")))
    par = {s: {"lignes": 0, "clients": 0} for s in STATUTS}
    total = vieilles = 0
    for r in rows:
        s = statut_relance(r, jour, relancer_non)
        par[s]["lignes"] += cint(r.lignes)
        par[s]["clients"] += 1
        total += cint(r.lignes)
        vieilles += cint(r.vieilles)
    return {"lignes": total, "clients": len(rows), "plus_6_mois": vieilles, "par_statut": par}


def _infos_clients(noms) -> dict:
    noms = tuple(n for n in noms if n)
    if not noms:
        return {}
    rows = frappe.db.sql("select c.name, " + _colonnes_client() + " from `tabCustomer` c where c.name in %(n)s group by c.name",
                         {"n": noms}, as_dict=True)
    return {r.name: r for r in rows}


def _clients_en_brouillon() -> dict:
    """{client: {types des brouillons qui le portent}}."""
    out = {}
    for client, t in frappe.db.sql("""select a.client, ifnull(l.type_liste, 'Normal') from `tabAppelle Client` a
                                      join `tabListe Appelle Entretien` l on l.name = a.parent
                                      where l.docstatus = 0 and a.parenttype = 'Liste Appelle Entretien' and ifnull(a.client, '') <> ''"""):
        out.setdefault(client, set()).add(t)
    return out


def entonnoir_appel(jour, cfg: dict | None = None) -> dict:
    """L'entonnoir des listes d'appel AUJOURD'HUI, avec les fonctions de creation_liste_appelle elles-mêmes
    (echeances_a_appeler, repartir) : candidats → exclus par motif → rendez-vous → appel de moins de 60 j → à appeler
    par type, et pour chacun : déjà dans un brouillon, ou en attente (derrière un brouillon de son type, ou à la
    prochaine liste). Le 25/11/2026 à 12:26 : 412 candidats, 99 exclus, 5 RDV, 120 appelés récemment, 4 Normal, 1 Zone,
    183 Hors secteur dont 129 en attente derrière LIST-06-11-2026-00004. Aucune règle n'est réécrite ici."""
    import inspect

    from customization_app import partenaire_clients as PC
    from customization_app.Maintenance import creation_liste_appelle as CL
    cfg = cfg or RC.config()
    jour = getdate(jour)
    # Les étapes d'avant le regroupement (visites SMS 2 non appelées, clients écartés par une intervention récente) ne
    # sont comptées que si echeances_a_appeler sait les rendre (paramètre `stats`, lot « listes » du 25/11/2026).
    etapes = {}
    try:
        avec_stats = "stats" in inspect.signature(CL.echeances_a_appeler).parameters
    except (TypeError, ValueError):
        avec_stats = False
    clients = (CL.echeances_a_appeler(jour, cfg, stats=etapes) if avec_stats else CL.echeances_a_appeler(jour, cfg)) or {}
    exclus = RC.clients_exclus_relance(cfg)
    rdv = RC.clients_avec_rdv(cfg)
    zones = PC.zones_partenaires()
    urg = CL.get_urgence_context(jour, cfg) or {}
    exclus_ici = [c for c in clients if c in exclus]
    motifs = Counter()
    # Le motif de chaque exclu : celui du passage de 07:00 (CL.motifs_des_exclus, lot « listes » du 25/11/2026) quand il
    # existe — l'entonnoir et le journal des listes donnent alors les mêmes nombres ; sinon le statut de relance.
    par_client = None
    if hasattr(CL, "motifs_des_exclus"):
        try:
            par_client = CL.motifs_des_exclus(exclus_ici, cfg, jour) or {}
        except Exception:
            par_client = None
    if par_client is not None:
        for c in exclus_ici:
            m = par_client.get(c) or "autre"
            motifs[LIBELLES_MOTIFS.get(m, m)] += 1
    else:
        infos = _infos_clients(exclus_ici)
        relancer_non = bool(cint(cfg.get("relancer_non_interesses")))
        for c in exclus_ici:
            m = statut_relance(infos.get(c) or {}, jour, relancer_non)
            motifs["autre" if m == "relançable" else m] += 1
    normal, zone, urgence, ecartes, hors = CL.repartir(
        clients, jour, cfg, exclus, zones, lambda c: PC.zone_partenaire_du_client(c, zones), rdv,
        urg.get("secteur_urgence") or [], urgence_active=bool(urg.get("gener_urgence")))
    en_brouillon = _clients_en_brouillon()
    par_classe = {"normal": normal, "urgence": urgence, "zone": zone, "hors": hors}
    a_appeler = []
    for cle, libelle in CLASSES:
        groupe = par_classe.get(cle) or {}
        dans = sum(1 for c in groupe if c in en_brouillon)
        a_appeler.append({"type": libelle, "clients": len(groupe), "dans_un_brouillon": dans, "en_attente": len(groupe) - dans})
    ecartes = dict(ecartes or {})
    classes = sum(v["clients"] for v in a_appeler)
    out = {"candidats": len(clients), "exclus": {"total": len(exclus_ici), "motifs": dict(motifs)},
           "rdv": cint(ecartes.get("rdv")), "appel_recent": cint(ecartes.get("recent")),
           "autres_ecartes": max(0, len(clients) - len(exclus_ici) - classes - cint(ecartes.get("rdv")) - cint(ecartes.get("recent"))),
           "a_appeler": a_appeler, "jours_entre_deux_appels": cint(cfg.get("jours_entre_deux_appels")),
           "jours_apres_intervention": cint(cfg.get("jours_apres_intervention"))}
    if etapes:
        out["avant"] = {"visites": cint(etapes.get("visites")), "clients": cint(etapes.get("clients")),
                        "intervention_recente": cint(etapes.get("intervention_recente"))}
    try:
        out["requalification_a_lister"] = len(CL.clients_requalification_a_lister(jour))
        out["rappels_echus"] = len(RC.clients_reportes_echus(jour, statut=""))
    except Exception:
        pass
    return out


def a_prolonger_selon_la_nuit(jour):
    """Les échéanciers que la prolongation de nuit doit ranimer (update_schedule.echeanciers_a_prolonger, lot 1 du
    25-26/11/2026), ou None si cette fonction n'existe pas encore. Pourquoi : la nuit laisse EXPRÈS gelés les articles
    qui doublent un autre flux du client (jumeaux, porte-filtres) ; sur la copie du 25/11/2026, 41 échéanciers ont un
    article sans visite à venir mais 36 seulement sont à prolonger — compter les 5 gelés laisserait le signal rouge
    allumé pour toujours."""
    try:
        from customization_app.Maintenance import update_schedule as US
    except Exception:
        return None
    fn = getattr(US, "echeanciers_a_prolonger", None)
    if fn is None:
        return None
    return set(fn(str(getdate(jour))) or [])


def resume_sans_visite(rows, a_prolonger, infos: dict, jour) -> dict:
    """Le résumé des échéanciers muets. PURE. `rows` : [{parent, item_code, customer}] (articles sans visite à venir) ;
    `a_prolonger` : ceux que la nuit doit ranimer (None = règle des gelés inconnue : tous) ; `infos` : statut des
    clients. Le signal rouge porte sur `a_prolonger` (doit valoir 0 après la nuit) ; `geles` = laissés gelés exprès."""
    rows = list(rows or [])
    tous = sorted({r["parent"] for r in rows})
    regle = a_prolonger is not None
    ap = sorted(set(tous) & set(a_prolonger)) if regle else tous
    client_de = {r["parent"]: r.get("customer") for r in rows}
    clients_ap = {client_de.get(n) for n in ap if client_de.get(n)}
    return {"echeanciers": len(tous), "articles": len(rows), "clients": len({r.get("customer") for r in rows if r.get("customer")}),
            "a_prolonger": len(ap), "geles": len(tous) - len(ap), "regle_gel": regle,
            "relancables": sum(1 for c in clients_ap if statut_relance((infos or {}).get(c) or {}, jour) == "relançable"),
            "exemples": ap[:8], "exemples_geles": sorted(set(tous) - set(ap))[:8]}


def echeanciers_sans_visite(jour, journaliser: bool = True) -> dict:
    """Les échéanciers soumis dont au moins un article n'a plus AUCUNE visite à venir après `jour` (même règle que la
    prolongation corrigée le 25/11/2026 : update_schedule.articles_a_prolonger). Mesure indépendante du code qu'elle
    contrôle (41 échéanciers muets sur la copie du 25/11/2026), puis, parmi eux, ceux que la nuit doit ranimer
    (36 : 5 sont laissés gelés exprès) : `a_prolonger` doit tomber à 0 après chaque nuit.
    `journaliser=False` (la page, lecture seule) : une erreur de la règle des gelés n'écrit pas d'Error Log, elle est
    rendue dans `erreur_regle_gel` (relecture du 26/11/2026 : chaque ouverture de la page écrivait une erreur, que la
    photo suivante comptait ensuite en orange)."""
    jour = getdate(jour)
    rows = frappe.db.sql("""select x.parent, x.item_code, ms.customer from (
                                select d.parent, d.item_code,
                                       sum(d.scheduled_date > %(jour)s and (d.actual_date is null or d.actual_date > %(jour)s)
                                           and ifnull(d.completion_status, '') <> 'Fully Completed') as a_venir
                                from `tabMaintenance Schedule Detail` d
                                join `tabMaintenance Schedule` ms on ms.name = d.parent
                                where ms.docstatus = 1 and ifnull(d.item_code, '') <> '' and d.scheduled_date is not null
                                  and d.creation < %(lendemain)s
                                group by d.parent, d.item_code) x
                            join `tabMaintenance Schedule` ms on ms.name = x.parent
                            where x.a_venir = 0 order by x.parent""",
                         {"jour": jour, "lendemain": jour + _dt.timedelta(days=1)}, as_dict=True)
    a_prolonger, erreur = None, None
    if rows:
        try:
            a_prolonger = a_prolonger_selon_la_nuit(jour)
        except Exception as e:
            erreur = str(e)[:200] or e.__class__.__name__
            if journaliser:
                frappe.log_error(frappe.get_traceback(), "Entonnoir relances : échéanciers à prolonger")
    infos = _infos_clients({r.customer for r in rows if r.customer})
    out = resume_sans_visite(rows, a_prolonger, infos, jour)
    if erreur:
        out["erreur_regle_gel"] = erreur
    return out


def brouillons(jour, aujourd_hui) -> list:
    """Les listes en brouillon au matin de `jour`, avec leur âge et leur immobilité en jours ouvrés pleins. Un brouillon
    BLOQUE la liste suivante de son type (sauf Urgence) : LIST-06-11-2026-00004 (Hors secteur, 100 lignes, 45 appelées,
    plus touchée depuis le 11/11) retenait 129 autres clients le 25/11/2026. Pour un jour passé : brouillons de l'époque
    d'après la date de validation (date_fin), sans l'immobilité (seule la dernière modification est connue)."""
    jour, aujourd_hui = getdate(jour), getdate(aujourd_hui)
    passe = jour < aujourd_hui
    cond = ("l.creation < %(lendemain)s and (l.docstatus = 0 or (l.docstatus = 1 and l.date_fin > %(jour)s))" if passe
            else "l.docstatus = 0")
    rows = frappe.db.sql("""select l.name, ifnull(l.type_liste, 'Normal') as type, l.creation, l.modified, l.titre,
                                   count(a.name) as lignes, sum(ifnull(a.`a_été_appelé`, 0)) as appelees
                            from `tabListe Appelle Entretien` l
                            left join `tabAppelle Client` a on a.parent = l.name and a.parenttype = 'Liste Appelle Entretien'
                            where """ + cond + """ group by l.name order by l.creation""",
                         {"jour": jour, "lendemain": jour + _dt.timedelta(days=1)}, as_dict=True)
    out = []
    for r in rows:
        out.append({"name": r.name, "type": r.type, "titre": r.titre, "cree_le": str(r.creation)[:16],
                    "modifie_le": None if passe else str(r.modified)[:16],
                    "age_jo": jours_ouvres_entre(r.creation, jour),
                    "inactif_jo": None if passe else jours_ouvres_entre(r.modified, jour),
                    "lignes": cint(r.lignes), "appelees": cint(r.appelees)})
    return out


def crons_suivis() -> list:
    """Les crons de la relance : ceux du réglage (RC.CRONS) + qualification IA, SMS de réveil et la photo elle-même,
    chacun avec ses motifs d'erreur et le titre exact de son filet run_safely (TITRES_FILET, voir est_plantage)."""
    out, vus = [], set()
    base = list(getattr(RC, "CRONS", []) or [])
    base += [{"cle": "qualification_appels.run_cron", "titre": "Qualification IA des réponses", "erreurs": ("Cron - Qualification IA%", "Qualification IA%")},
             {"cle": "sms_reveil.run_cron", "titre": "SMS de réveil", "erreurs": ("Cron - SMS de réveil%", "SMS réveil%")},
             {"cle": "entonnoir_relances.cron_photo", "titre": "Photo de l’entonnoir", "erreurs": ("Cron - Entonnoir des relances%", "Entonnoir relances%")}]
    for c in base:
        if c.get("cle") in vus:
            continue
        vus.add(c["cle"])
        motifs = tuple(c.get("erreurs") or ())
        filet = TITRES_FILET.get(c["cle"])
        out.append({"cle": c["cle"], "titre": c.get("titre") or c["cle"], "filet": filet,
                    "motifs": motifs + ((filet,) if filet and not any(_like(m, filet) for m in motifs) else ())})
    return out


def _like(motif: str, texte: str) -> bool:
    """LIKE « préfixe% » de MariaDB, en Python (motifs du réglage). PURE."""
    m = motif or ""
    return (texte or "").startswith(m[:-1]) if m.endswith("%") else (texte or "") == m


def est_plantage(titre: str, cron: dict) -> bool:
    """Ce titre d'Error Log dit-il que le passage du cron a PLANTÉ ? PURE. Oui seulement pour le titre exact de son
    filet run_safely (`filet`). Sans filet connu : un motif « Cron - … » sans « : » après le nom du cron. Relecture du
    26/11/2026 : « Cron - Génération listes : alerte du brouillon LIST-… » n'arrête pas le passage de 07:00 (une
    erreur sur un élément) ; elle était classée plantage par le seul préfixe, d'où un faux rouge et un e-mail."""
    titre = titre or ""
    filet = (cron or {}).get("filet")
    if filet:
        return titre == filet
    for m in (cron or {}).get("motifs") or ():
        if m.startswith("Cron - ") and _like(m, titre):
            reste = titre[len(m.rstrip("%")):]
            if ":" not in reste:
                return True
    return False


def _charger_passages(debut, fin) -> dict:
    """Types de tâche planifiée, journaux et erreurs des crons suivis sur [debut, fin], en trois requêtes."""
    crons = crons_suivis()
    types = {r.name: r for r in frappe.get_all("Scheduled Job Type", filters={"name": ["in", [c["cle"] for c in crons]]},
                                                fields=["name", "method", "frequency", "cron_format", "stopped", "creation"])}
    d0 = _dt.datetime.combine(getdate(debut), _dt.time.min) - _dt.timedelta(hours=1)
    d1 = _dt.datetime.combine(getdate(fin), _dt.time.max) + _dt.timedelta(hours=FENETRE_H)
    journaux = {}
    if types:
        for r in frappe.db.sql("""select scheduled_job_type, status, creation from `tabScheduled Job Log`
                                  where scheduled_job_type in %(t)s and creation between %(d0)s and %(d1)s""",
                               {"t": tuple(types), "d0": d0, "d1": d1}, as_dict=True):
            journaux.setdefault(r.scheduled_job_type, []).append({"status": r.status, "creation": r.creation})
    motifs = sorted({m for c in crons for m in c["motifs"]})
    erreurs = []
    if motifs:
        cond = " or ".join(["method like %s"] * len(motifs))
        erreurs = frappe.db.sql("select name, method, creation from `tabError Log` where creation between %s and %s and (" + cond + ")",
                                tuple([d0, d1] + motifs), as_dict=True)
    return {"crons": crons, "types": types, "journaux": journaux, "erreurs": erreurs}


def _passages_du_jour(jour, charge: dict, maintenant) -> list:
    """Pour un jour : chaque cron suivi, ses passages prévus et leur état, ses erreurs sur des éléments. PURE (sur
    `charge`, lu par _charger_passages). `installe` : présent et en marche ; `arrete` : « Stopped » coché ; `absent` :
    pas de tâche planifiée de ce nom (hooks.py, migration). Un arrêté ou un absent n'a pas de passage prévu."""
    jour = getdate(jour)
    out = []
    for c in charge["crons"]:
        t = charge["types"].get(c["cle"])
        mien = [e for e in charge["erreurs"] if any(_like(m, e.get("method")) for m in c.get("motifs") or ())]
        plantages = [e.get("creation") for e in mien if est_plantage(e.get("method"), c)]
        elements = [e for e in mien if getdate(e.get("creation")) == jour and not est_plantage(e.get("method"), c)]
        arrete = bool(t) and bool(cint(t.get("stopped")))
        installe = bool(t) and not arrete
        prevus = passages_prevus(t.get("cron_format"), jour, t.get("frequency")) if installe else []
        # Pas de passage attendu avant que la tâche planifiée existe (relecture finale du 26/11/2026 : sinon la 1re photo
        # après le déploiement criait « pas de passage » pour la photo elle-même, e-mail compris, et 14 jours en rouge).
        if prevus and t.get("creation"):
            nee = get_datetime(t.get("creation"))
            prevus = [p for p in prevus if p >= nee]
        out.append({"cle": c["cle"], "titre": c["titre"], "filet": c.get("filet"), "installe": installe, "arrete": arrete,
                    "absent": not t,
                    "prevus": etat_passages(prevus, charge["journaux"].get(c["cle"]) or [], plantages, maintenant),
                    "erreurs_elements": len(elements),
                    "exemples_erreurs": sorted({(e.get("method") or "")[:60] for e in elements})[:3]})
    return out


def _flux_vide() -> dict:
    return {"listes": {}, "lignes": 0, "lignes_flux": 0, "validees": 0, "appelees": 0, "appelees_rdv": 0,
            "sms1": 0, "sms2": 0, "sms1_echec": 0, "sms2_echec": 0, "sms1_invalide": 0, "sms2_invalide": 0, "sms_clients": 0,
            "machines": {"lignes": 0, "clients": 0, "b2b": 0, "non": 0, "attendues": 0, "suivies": 0, "sans": []},
            "nes": {"total": 0, "machine": 0, "devine": 0}, "erreurs": 0, "passages": []}


def _familles_suivies(clients, fams) -> dict:
    """{client: {familles de ses échéanciers soumis}}."""
    clients = tuple(c for c in clients if c)
    out = {}
    if not clients:
        return out
    for client, groupe in frappe.db.sql("""select ms.customer, i.item_group from `tabMaintenance Schedule Item` msi
                                          join `tabMaintenance Schedule` ms on ms.name = msi.parent
                                          left join `tabItem` i on i.name = msi.item_code
                                          where ms.docstatus = 1 and ms.customer in %(c)s""", {"c": clients}):
        out.setdefault(client, set()).add(RC.famille_du_groupe_machine(groupe, fams))
    return out


def flux(debut, fin, cfg: dict | None = None, maintenant=None, journaliser: bool = True) -> dict:
    """Ce qui s'est passé chaque jour de [debut, fin] — {AAAA-MM-JJ: {...}} — relu en base, une requête par sujet :
    listes créées par type et leurs lignes, listes validées, lignes cochées « appelé » (Version, plus les rendez-vous
    pris au calendrier ou dans l'app, sans Version : `appelees_rdv`), SMS 1 / SMS 2 par issue (dates et statuts portés
    par les visites : `sms1` = réussis, `sms1_echec` = « Failed », `sms1_invalide` = sans numéro ; des minimums, un
    décalage en efface 7 à 14 %), machines livrées par BL et couvertes par un échéancier de leur famille, échéanciers
    nés (« deviné » = sa visite réalisée date d'avant sa naissance : l'achat de consommables qui l'a fait naître),
    erreurs et passages des crons. `journaliser=False` (la page) : une erreur de lecture des passages n'écrit rien, elle
    est rendue dans `erreur_passages` de chaque jour."""
    cfg = cfg or RC.config()
    reg = reglages(cfg)
    debut, fin = getdate(debut), getdate(fin)
    maintenant = maintenant or now_datetime()
    jours = [debut + _dt.timedelta(days=k) for k in range((fin - debut).days + 1)]
    out = {str(j): _flux_vide() for j in jours}
    p = {"debut": debut, "fin": fin, "d0": _dt.datetime.combine(debut, _dt.time.min),
         "d1": _dt.datetime.combine(fin + _dt.timedelta(days=1), _dt.time.min)}
    types_flux = set(reg["types_flux"])

    for j, t, n, lignes in frappe.db.sql("""select date(l.creation), ifnull(l.type_liste, 'Normal'), count(distinct l.name), count(a.name)
                                            from `tabListe Appelle Entretien` l
                                            left join `tabAppelle Client` a on a.parent = l.name and a.parenttype = 'Liste Appelle Entretien'
                                            where l.creation >= %(d0)s and l.creation < %(d1)s and l.docstatus < 2
                                            group by 1, 2""", p):
        f = out.get(str(j))
        if f is None:
            continue
        f["listes"][t] = {"listes": cint(n), "lignes": cint(lignes)}
        f["lignes"] += cint(lignes)
        if t in types_flux:
            f["lignes_flux"] += cint(lignes)
    for j, n in frappe.db.sql("""select date_fin, count(*) from `tabListe Appelle Entretien` where docstatus = 1
                                 and date_fin between %(debut)s and %(fin)s group by 1""", p):
        if str(j) in out:
            out[str(j)]["validees"] = cint(n)
    appels = appels_des_versions(frappe.db.sql("""select creation, data from `tabVersion` where ref_doctype = 'Liste Appelle Entretien'
                                                  and creation >= %(d0)s and creation < %(d1)s""", p))
    # Rendez-vous pris sans Version (calendrier « Prendre RDV », app) : le jour de leur tâche ; une ligne déjà vue dans
    # une Version de sa liste, à n'importe quelle date, n'est pas recomptée (ajouter_rdv_sans_version).
    rdv = frappe.db.sql("""select a.name, a.parent, t.creation from `tabAppelle Client` a
                           join `tabTache de travail` t on t.name = a.tache_de_travail
                           where a.parenttype = 'Liste Appelle Entretien' and ifnull(a.`a_été_appelé`, 0) = 1
                             and t.creation >= %(d0)s and t.creation < %(d1)s""", p, as_dict=True)
    if rdv:
        marques = set()
        for lignes in appels_des_versions(frappe.db.sql("""select creation, data from `tabVersion` where ref_doctype = 'Liste Appelle Entretien'
                                                           and docname in %(n)s""", {"n": tuple({r.parent for r in rdv})})).values():
            marques |= lignes
        for j, n in ajouter_rdv_sans_version(appels, [(r.name, r.creation) for r in rdv], marques).items():
            if j in out:
                out[j]["appelees_rdv"] = n
    for j, lignes in appels.items():
        if j in out:
            out[j]["appelees"] = len(lignes)
    suffixes = {"ok": "", "echec": "_echec", "invalide": "_invalide"}
    for champ, statut, cle in (("custom_sms_1", "custom_sms1_status", "sms1"), ("custom_sms_2", "custom_sms2_status", "sms2")):
        for j, st, n in frappe.db.sql("select d." + champ + ", ifnull(d." + statut + ", ''), count(*) from `tabMaintenance Schedule Detail` d where d."
                                      + champ + " between %(debut)s and %(fin)s group by 1, 2", p):
            if str(j) in out:
                out[str(j)][cle + suffixes[classe_statut_sms(st)]] += cint(n)
    # Clients joints : ceux d'un SMS RÉUSSI (statut « Success », ou vide avant décembre 2023).
    for j, n in frappe.db.sql("""select x.j, count(distinct x.customer) from (
                                     select d.custom_sms_1 as j, ms.customer from `tabMaintenance Schedule Detail` d
                                     join `tabMaintenance Schedule` ms on ms.name = d.parent where d.custom_sms_1 between %(debut)s and %(fin)s
                                       and ifnull(d.custom_sms1_status, '') in %(reussi)s
                                     union all
                                     select d.custom_sms_2 as j, ms.customer from `tabMaintenance Schedule Detail` d
                                     join `tabMaintenance Schedule` ms on ms.name = d.parent where d.custom_sms_2 between %(debut)s and %(fin)s
                                       and ifnull(d.custom_sms2_status, '') in %(reussi)s) x
                                 group by x.j""", dict(p, reussi=SMS_REUSSI)):
        if str(j) in out:
            out[str(j)]["sms_clients"] = cint(n)

    fams = RC.familles()
    groupes = tuple(sorted(RC.groupes_machines(fams)))
    if groupes:
        try:
            from customization_app.Maintenance.update_schedule import FAMILLES_ACCESSOIRES as ACCESSOIRES
        except Exception:
            ACCESSOIRES = ("PF",)
        b2b = set(cfg.get("groupes_b2b_liste") or [])
        rows = frappe.db.sql("""select dn.name as dn, dn.posting_date as j, dn.customer, dni.item_code, i.item_group,
                                       c.customer_group as cg, c.`custom_intéressé_par_le_service_entretien` as inter
                                from `tabDelivery Note Item` dni
                                join `tabDelivery Note` dn on dn.name = dni.parent
                                join `tabItem` i on i.name = dni.item_code
                                left join `tabCustomer` c on c.name = dn.customer
                                where dn.docstatus = 1 and ifnull(dn.is_return, 0) = 0 and dn.posting_date between %(debut)s and %(fin)s
                                  and i.item_group in %(g)s""", dict(p, g=groupes), as_dict=True)
        suivies = _familles_suivies({r.customer for r in rows}, fams)
        try:
            from customization_app.Maintenance.update_schedule import clients_generiques
            generiques = set(clients_generiques(cfg))
        except Exception:
            generiques = {"PASSAGER"}
        vus, clients_jour = set(), {}
        for r in rows:
            if (r.dn, r.item_code) in vus or str(r.j) not in out:
                continue
            vus.add((r.dn, r.item_code))
            m = out[str(r.j)]["machines"]
            m["lignes"] += 1
            clients_jour.setdefault(str(r.j), set()).add(r.customer)
            if (r.cg or "") in b2b:
                m["b2b"] += 1
                continue
            if r.customer in generiques:                  # comptoir : jamais d'échéancier (réglage clients_generiques)
                m["non"] += 1
                continue
            if (r.inter or "").strip().upper() != "OUI":
                m["non"] += 1
                # Depuis le 26/11/2026 la machine d'un « Non » reçoit son échéancier (sans relance) : elle est attendue.
                # Avant, elle n'en avait pas, voulu : les jours reconstitués ne doivent pas virer à l'orange.
                if str(r.j) < DEBUT_ECHEANCIER_DES_NON:
                    continue
            m["attendues"] += 1
            fam = RC.famille_du_groupe_machine(r.item_group, fams)
            familles = suivies.get(r.customer) or set()
            if fam in familles or (fam in ACCESSOIRES and familles):
                m["suivies"] += 1
            elif r.dn not in m["sans"]:
                m["sans"].append(r.dn)
        for j, cl in clients_jour.items():
            out[j]["machines"]["clients"] = len(cl)
    for j, _name, devine in frappe.db.sql("""select date(ms.creation), ms.name,
                                                    max(d.actual_date is not null and d.actual_date <= date(ms.creation))
                                             from `tabMaintenance Schedule` ms
                                             left join `tabMaintenance Schedule Detail` d on d.parent = ms.name
                                             where ms.docstatus = 1 and ms.creation >= %(d0)s and ms.creation < %(d1)s
                                             group by ms.name""", p):
        f = out.get(str(j))
        if f is None:
            continue
        f["nes"]["total"] += 1
        f["nes"]["devine" if cint(devine) else "machine"] += 1

    try:
        charge = _charger_passages(debut, fin)
        for j in jours:
            out[str(j)]["passages"] = _passages_du_jour(j, charge, maintenant)
        for e in charge["erreurs"]:
            if str(getdate(e.creation)) in out:
                out[str(getdate(e.creation))]["erreurs"] += 1
    except Exception as e:
        if journaliser:
            frappe.log_error(frappe.get_traceback(), "Entonnoir relances : passages des crons")
        for j in jours:
            out[str(j)]["passages"] = []
            out[str(j)]["erreur_passages"] = str(e)[:200] or e.__class__.__name__
    return out


def ordonnanceur_inactif() -> bool:
    try:
        from frappe.utils.scheduler import is_scheduler_inactive
        return bool(is_scheduler_inactive(verbose=False))
    except Exception:
        return False


def indicateurs(jour=None, journaliser: bool = True, maintenant=None, flux_jours: dict | None = None) -> dict:
    """La photo de l'entonnoir pour `jour` (aujourd'hui par défaut), lue en base sans rien écrire. Chaque partie est
    calculée à part : une partie en erreur vaut None et n'empêche pas les autres (`erreurs_calcul`). L'entonnoir des
    appels n'existe que pour aujourd'hui (il dépend de l'état présent des visites) ; pour un jour passé, les stocks
    sont reconstitués d'après les dates (`reconstitue`)."""
    maintenant = get_datetime(maintenant or now_datetime())
    aujourd_hui = maintenant.date()
    jour = min(getdate(jour or aujourd_hui), aujourd_hui)
    veille = jour - _dt.timedelta(days=1)
    cfg = RC.config()
    photo = {"version": 1, "jour": str(jour), "veille": str(veille), "calcule_le": str(maintenant)[:19],
             "reconstitue": jour < aujourd_hui, "erreurs_calcul": []}

    def section(nom, fn):
        try:
            photo[nom] = fn()
        except Exception as e:
            photo[nom] = None
            photo["erreurs_calcul"].append("%s : %s" % (nom, str(e)[:200]))
            if journaliser:
                frappe.log_error(frappe.get_traceback(), "Entonnoir relances : partie %s" % nom)

    section("visites_echues", lambda: visites_echues(jour, cfg))
    section("appel", lambda: entonnoir_appel(jour, cfg) if jour == aujourd_hui else None)
    section("sans_visite_a_venir", lambda: echeanciers_sans_visite(jour, journaliser=journaliser))
    section("brouillons", lambda: brouillons(jour, aujourd_hui))

    def _flux():
        fl = (flux_jours if flux_jours and str(jour) in flux_jours and str(veille) in flux_jours
              else flux(veille, jour, cfg, maintenant, journaliser=journaliser))
        return {"veille": fl.get(str(veille)), "jour": fl.get(str(jour))}
    section("flux", _flux)
    # Erreurs rendues sans Error Log par les parties (journaliser=False) : visibles sur la page, jamais muettes.
    sv = photo.get("sans_visite_a_venir") or {}
    if sv.get("erreur_regle_gel"):
        photo["erreurs_calcul"].append("règle des gelés (update_schedule.echeanciers_a_prolonger) : %s" % sv["erreur_regle_gel"])
    fj = (photo.get("flux") or {}).get("jour") or {}
    if fj.get("erreur_passages"):
        photo["erreurs_calcul"].append("passages des crons : %s" % fj["erreur_passages"])
    photo["ordonnanceur_inactif"] = ordonnanceur_inactif()
    return photo


# ═════════════════════════════════════════════════════════════════════════════
# Enregistrement du jour
# ═════════════════════════════════════════════════════════════════════════════

def _table_prete() -> bool:
    try:
        return bool(getattr(frappe.local, "db", None)) and bool(frappe.db.table_exists(DOCTYPE))
    except Exception:
        return False


def _ligne_du_jour(nom: str) -> dict:
    """La ligne du jour verrouillée (FOR UPDATE : deux crons ne s'écrasent pas, et la lecture voit le dernier état
    validé, pas l'instantané d'une longue transaction), créée si besoin."""
    def lire():
        rows = frappe.db.sql("select name, sources, signaux from `tabEntonnoir Relances` where name = %s for update", (nom,), as_dict=True)
        return rows[0] if rows else None
    ligne = lire()
    if ligne:
        return ligne
    frappe.db.savepoint("entonnoir_creer")
    try:
        frappe.get_doc({"doctype": DOCTYPE, "jour": nom, "sources": "{}"}).insert(ignore_permissions=True)
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
        frappe.db.rollback(save_point="entonnoir_creer")     # créée entre-temps par un autre cron : on la relit
    return lire() or {"name": nom, "sources": "{}", "signaux": None}


def _charger_json(texte, defaut):
    try:
        v = json.loads(texte or "")
        return v if isinstance(v, type(defaut)) else defaut
    except Exception:
        return defaut


def _json(v) -> str:
    return json.dumps(propre(v), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def noter(source: str, compteurs: dict, jour=None) -> bool:
    """Mémorise les compteurs du jour d'une source (« nuit », « listes », « sms »…) dans l'enregistrement du jour :
    un enregistrement par jour, les compteurs de chaque source en JSON, fusionnés si la source passe deux fois (voir
    fusionner). Ne lève JAMAIS et ne valide rien (le commit du cron appelant s'en charge) : sous savepoint, une erreur
    annule seulement ce qu'il écrivait, la journalise et rend False. Avant la migration (table absente) : False, sans
    bruit. Contrat partagé du 25/11/2026 :
        from customization_app.entonnoir_relances import noter
        noter("nuit", summary)"""
    try:
        if not _table_prete():
            return False
        src = (str(source or "").strip() or "inconnu")[:60]
        valeurs = propre(compteurs if isinstance(compteurs, dict) else {"valeur": compteurs})
        nom = str(getdate(jour)) if jour else str(now_datetime().date())
        heure = str(now_datetime())[11:19]
    except Exception:
        return False
    try:
        frappe.db.savepoint("entonnoir_noter")
    except Exception:
        return False
    try:
        ligne = _ligne_du_jour(nom)
        sources = maj_source(_charger_json(ligne.get("sources"), {}), src, valeurs, heure)
        frappe.db.set_value(DOCTYPE, nom, "sources", _json(sources), update_modified=True)
        return True
    except Exception:
        try:
            frappe.db.rollback(save_point="entonnoir_noter")
            frappe.log_error(frappe.get_traceback(), "Entonnoir relances : noter %s" % str(source)[:60])
        except Exception:
            pass
        return False


def enregistrements(debut, fin) -> dict:
    """{AAAA-MM-JJ: colonnes de l'enregistrement} entre deux jours ; {} tant que la table n'existe pas."""
    if not _table_prete():
        return {}
    champs = ["name", "jour", "photo_le", "sources"] + list(scalaires({}, [], {}).keys())
    out = {}
    for r in frappe.get_all(DOCTYPE, filters={"jour": ["between", [str(getdate(debut)), str(getdate(fin))]]}, fields=champs,
                            order_by="jour desc", limit_page_length=0):
        r = dict(r)
        r["sources"] = _charger_json(r.get("sources"), {})
        out[str(getdate(r["jour"]))] = propre(r)
    return out


def _signaux_precedents(nom: str, ligne: dict) -> set:
    """Les clés des signaux ROUGES de la dernière photo enregistrée : celle du jour, sinon celle de la veille (cles_rouges :
    un orange qui devient rouge est un signal rouge nouveau, et il envoie son e-mail)."""
    brut = (ligne or {}).get("signaux")
    deja = _charger_json(brut, [])
    if brut in (None, ""):       # pas encore de photo aujourd'hui (ligne créée par un noter) : celle de la veille
        veille = str(getdate(nom) - _dt.timedelta(days=1))
        rows = frappe.db.sql("select signaux from `tabEntonnoir Relances` where name = %s", (veille,))
        deja = _charger_json(rows[0][0], []) if rows else []
    return cles_rouges(deja)


def corps_alerte(nouveaux: list, photo: dict) -> str:
    """Le texte de l'e-mail : les signaux nouveaux, puis le lien de la page. PURE."""
    lignes = "".join("<li>%s</li>" % frappe.utils.escape_html(s.get("texte") or "") for s in nouveaux)
    return ("<p>Entonnoir des relances d’entretien, photo du %s à %s : %d signal(aux) rouge(s) nouveau(x).</p><ul>%s</ul>"
            "<p>Détail : page « Journaux des relances » (/app/journaux-relances).</p>"
            % (_jj(photo.get("jour")), str(photo.get("calcule_le") or "")[11:16], len(nouveaux), lignes))


def enregistrer_photo(jour=None, alerter: bool = True) -> dict:
    """Le cron de 07:30 et 10:30 : la photo du jour, ses signaux et ses chiffres-clés dans l'enregistrement du jour
    (les compteurs notés par les crons sont gardés) ; un e-mail si un signal ROUGE est nouveau depuis la photo
    précédente — un signal qui dure (échéanciers muets) n'est envoyé qu'une fois."""
    cfg = RC.config()
    reg = reglages(cfg)
    maintenant = now_datetime()
    j = min(getdate(jour or maintenant.date()), maintenant.date())
    profondeur = max(cint(reg["entonnoir_jours_tendance"]), 7 * max(1, cint(reg["entonnoir_semaines_reference"])) + 1, 2)
    fl = flux(j - _dt.timedelta(days=profondeur - 1), j, cfg, maintenant)
    photo = indicateurs(j, journaliser=True, maintenant=maintenant, flux_jours=fl)
    sig = signaux(photo, fl, reg, maintenant, ordonnanceur_inactif=photo.get("ordonnanceur_inactif"))
    res = {"jour": str(j), "signaux": sum(1 for s in sig if s["niveau"] == "rouge"), "nouveaux": 0, "enregistre": False}
    if not _table_prete():
        return res
    nom = str(j)
    frappe.db.savepoint("entonnoir_photo")
    try:
        ligne = _ligne_du_jour(nom)
        precedents = _signaux_precedents(nom, ligne)
        valeurs = dict(scalaires(photo, sig, reg), indicateurs=_json(photo), signaux=_json(sig), photo_le=maintenant)
        frappe.db.set_value(DOCTYPE, nom, valeurs, update_modified=True)
    except Exception:
        frappe.db.rollback(save_point="entonnoir_photo")      # rien de partiel ; run_safely journalise et prévient
        raise
    res["enregistre"] = True
    nouveaux = [s for s in sig if s["niveau"] == "rouge" and s["cle"] not in precedents]
    res["nouveaux"] = len(nouveaux)
    if nouveaux and alerter and cint(reg.get("entonnoir_alerte_email")):
        RC.alerter("[ERPNext] Relances d’entretien : %d signal(aux) rouge(s) nouveau(x)" % len(nouveaux), corps_alerte(nouveaux, photo), cfg)
    return res


def cron_photo():
    """Point d'entrée du planificateur (« 30 7,10 * * * ») : dans le filet run_safely, un plantage est journalisé et
    envoyé par e-mail sans arrêter les autres tâches."""
    from customization_app.utils.run_safely import run_safely
    return run_safely("Cron - Entonnoir des relances", enregistrer_photo)


# ═════════════════════════════════════════════════════════════════════════════
# Page « Journaux des relances »
# ═════════════════════════════════════════════════════════════════════════════

def vue_page(jour=None) -> dict:
    """Tout ce que la section « Entonnoir du jour » affiche, calculé à l'ouverture (lecture seule) : la photo d'aujourd'hui,
    les signaux, la tendance des derniers jours (flux relus en base + stocks des photos enregistrées), les compteurs
    notés par les crons ce jour-là."""
    cfg = RC.config()
    reg = reglages(cfg)
    maintenant = now_datetime()
    j = min(getdate(jour or maintenant.date()), maintenant.date())
    n = max(1, cint(reg["entonnoir_jours_tendance"]))
    profondeur = max(n, 7 * max(1, cint(reg["entonnoir_semaines_reference"])) + 1, 2)
    fl = flux(j - _dt.timedelta(days=profondeur - 1), j, cfg, maintenant, journaliser=False)
    photo = indicateurs(j, journaliser=False, maintenant=maintenant, flux_jours=fl)
    sig = signaux(photo, fl, reg, maintenant, ordonnanceur_inactif=photo.get("ordonnanceur_inactif"))
    stockes = enregistrements(j - _dt.timedelta(days=n - 1), j)
    du_jour = stockes.get(str(j)) or {}
    return propre({"photo": photo, "signaux": sig, "tendance": tendance(j, n, fl, stockes, maintenant), "sources": du_jour.get("sources") or {},
                   "photo_enregistree": du_jour.get("photo_le"), "table_prete": _table_prete(), "reglages": reg,
                   "libelles_sources": LIBELLES_SOURCES, "libelles_cles": LIBELLES_CLES, "statuts": list(STATUTS)})
