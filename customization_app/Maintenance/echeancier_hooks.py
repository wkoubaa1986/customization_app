# -*- coding: utf-8 -*-
"""Échéancier de maintenance — ce qui se passe à l'ENREGISTREMENT d'un échéancier déjà validé (Before Save d'un document
soumis = événement Frappe `before_update_after_submit`, voir frappe/core/doctype/server_script/server_script_utils.py).

Reprise, le 26/11/2026, du Server Script « Update Schedule based on Real Date » (actif en base, jamais versionné, modifié
le 10/02/2024). Sa règle : pour chaque visite qui a une date réelle, la date prévue devient la date réelle et les visites
SUIVANTES du même article sont décalées du même écart (date réelle − date prévue), jusqu'à la visite réalisée suivante.
C'est le décalage d'un entretien SAISI À LA MAIN sur l'échéancier ; les autres chemins l'alignent déjà eux-mêmes :
- le passage de nuit (update_schedule.shift_schedule_for_delivery) pose date prévue = date réelle = livraison ;
- « Fait lui-même » à l'appel (liste_appels.solder_fait_lui_meme, vague A du 26/11/2026) fait de même à la date de l'appel ;
- l'annulation d'une commande (annulation_commande) efface la date réelle en SQL, sans enregistrer l'échéancier.

MESURÉ sur la copie de prod du 25/11/2026 (3 263 échéanciers soumis, le code du script lu en base et exécuté sur des
copies) : l'ancien script et cette reprise ne changent AUCUNE date aujourd'hui — aucune visite réalisée n'a une date
prévue différente de sa date réelle ; après un passage de nuit simulé (600 échéanciers) ou un « Fait lui-même » simulé
(1 464), rien non plus. La dernière saisie à la main date du 01/01/2026 (83 visites réalisées sans commande, 2023-2026).
Le script tournait pourtant à CHAQUE enregistrement (marquage des SMS, passage de nuit, prolongation). Il ne sert donc
plus qu'à une saisie à la main : rare, pas dangereux, mais fragile — d'où cette reprise plutôt qu'un simple arrêt.

Ce que la reprise fait de mieux que le script (même règle, sinon) :
- PAR ARTICLE, DANS L'ORDRE DES DATES et non dans l'ordre des lignes : le script suivait le tableau et remettait le
  décalage à zéro à chaque changement d'article ; or 51 échéanciers ont des lignes d'un article hors de l'ordre des dates
  (et la prolongation ajoute ses visites en fin de tableau). Sur 1 464 saisies simulées (la plus ancienne visite échue de
  chaque article faite 30 j après sa date), le script laissait 8 visites à leur place sur 2 échéanciers (MAT-MSH-2023-00840 :
  visites des 30/09 et 30/12/2026 non décalées) ;
- une visite repoussée d'avant l'entretien à après lui redevient NEUVE (SMS et appels effacés, sinon elle ne serait
  plus jamais relancée) et une trace 📅 est posée sur l'échéancier, relue par « Historique client » — exactement la
  règle du passage de nuit ;
- une visite qui a une date réelle passe « Fully Completed » : 76 visites saisies à la main en 2023-2026 étaient restées
  « Pending » (patch visites_faites_restees_en_attente) ; ça ne se reproduit plus ;
- une visite « Fully Completed » sans date réelle ne bouge jamais ;
- une date réelle DANS LE FUTUR (relecture du 26/11/2026) n'est ni soldée ni recalée, et rien ne bouge après elle :
  c'est une faute de frappe probable (2062 pour 2026 aurait repoussé les visites suivantes de 13 160 jours et effacé
  les relances de celles d'avant) ou une intervention inscrite avant d'avoir eu lieu — même règle que le patch
  visites_faites_restees_en_attente (a_corriger). Elle est journalisée et signalée à l'utilisateur, sans bloquer ; une
  fois la date passée, l'enregistrement suivant la traite normalement. 0 cas sur la copie ;
- jamais bloquant : une erreur est journalisée et l'enregistrement continue sans recalage.

Les deux peuvent coexister sans double décalage : après la reprise, chaque visite réalisée a date prévue = date réelle,
le script (qui passe APRÈS les hooks Python dans Document.run_method) ne trouve alors plus aucun écart.
"""
from __future__ import annotations

import frappe
from frappe.utils import add_days, date_diff, getdate, nowdate

FAIT = "Fully Completed"


def _g(r, k):
    return r.get(k) if isinstance(r, dict) else getattr(r, k, None)


def _s(r, k, v):
    if isinstance(r, dict):
        r[k] = v
    else:
        setattr(r, k, v)


def _faite(r) -> bool:
    return bool(_g(r, "actual_date")) or _g(r, "completion_status") == FAIT


def _future(r, aujourd_hui) -> bool:
    return bool(_g(r, "actual_date")) and getdate(_g(r, "actual_date")) > aujourd_hui


def plan_recalage(lignes, aujourd_hui=None) -> dict:
    """Ce que le recalage changerait, SANS rien modifier. PURE (`aujourd_hui` : nowdate() par défaut).

    Par article, visites triées par (date prévue, idx) : décalage = 0 au départ ; une visite qui a une date réelle
    fixe le décalage à (réelle − prévue) et prend sa date réelle pour date prévue ; une visite non faite qui la suit
    est décalée d'autant, jusqu'à la visite réalisée suivante (même règle que le Server Script). Une visite repoussée
    d'avant l'entretien (prévue ≤ date réelle) à après lui perd ses SMS et appels (update_schedule.CHAMPS_RELANCE).
    Une date réelle après `aujourd_hui` (relecture du 26/11/2026) : ni statut ni recalage, et le décalage en cours
    s'arrête là (dans le doute, rien ne bouge après elle) ; elle est rendue dans "futures", sauf si elle est déjà
    alignée et soldée (ce qu'écrit la nuit pour une livraison prévue dans les jours qui viennent : même résultat que
    le script, rien ne bouge).
    → {"dates": [{ligne, item_code, ancienne, reelle, decalage, deplacees: [(ligne, avant, après, {champ: valeur})]}],
       "statuts": [lignes qui ont une date réelle passée sans être « Fully Completed »],
       "futures": [lignes dont la date réelle est dans le futur, pas déjà alignées et soldées]}"""
    from customization_app.Maintenance.update_schedule import CHAMPS_RELANCE
    aujourd_hui = getdate(aujourd_hui or nowdate())
    par_article = {}
    statuts, futures = [], []
    for r in lignes or []:
        if _future(r, aujourd_hui):
            # Déjà alignée et soldée (le passage de nuit lit les commandes livrées jusqu'à fenetre_mois_apres après
            # aujourd'hui : date réelle = date prévue = livraison prévue) : rien à faire, rien à signaler.
            if getdate(_g(r, "actual_date")) != getdate(_g(r, "scheduled_date")) or _g(r, "completion_status") != FAIT:
                futures.append(r)
        elif _g(r, "actual_date") and _g(r, "completion_status") != FAIT:
            statuts.append(r)
        if _g(r, "item_code") and _g(r, "scheduled_date"):
            par_article.setdefault(_g(r, "item_code"), []).append(r)
    dates = []
    for code, rows in par_article.items():
        rows = sorted(rows, key=lambda r: (getdate(_g(r, "scheduled_date")), _g(r, "idx") or 0))
        decalage, courant = 0, None
        for r in rows:
            prevue = getdate(_g(r, "scheduled_date"))
            if _future(r, aujourd_hui):
                decalage, courant = 0, None
                continue
            if _g(r, "actual_date"):
                reelle = getdate(_g(r, "actual_date"))
                decalage = date_diff(reelle, prevue)
                courant = None
                if decalage:
                    courant = {"ligne": r, "item_code": code, "ancienne": prevue, "reelle": reelle, "decalage": decalage,
                               "deplacees": []}
                    dates.append(courant)
                continue
            if not decalage or _faite(r):
                continue
            apres = getdate(add_days(prevue, decalage))
            effaces = {}
            if prevue <= courant["reelle"] < apres:
                effaces = {c: _g(r, c) for c in CHAMPS_RELANCE if _g(r, c)}
            courant["deplacees"].append((r, prevue, apres, effaces))
    return {"dates": dates, "statuts": statuts, "futures": futures}


def appliquer_recalage(plan) -> int:
    """Applique un plan_recalage aux lignes (objets modifiés sur place). → nombre de lignes modifiées. PURE."""
    from customization_app.Maintenance.update_schedule import CHAMPS_RELANCE
    n = 0
    for r in plan.get("statuts") or []:
        _s(r, "completion_status", FAIT)
        n += 1
    for c in plan.get("dates") or []:
        _s(c["ligne"], "scheduled_date", c["reelle"])
        n += 1
        for r, _avant, apres, effaces in c["deplacees"]:
            _s(r, "scheduled_date", apres)
            for champ in CHAMPS_RELANCE:
                if champ in effaces:
                    _s(r, champ, None)
            n += 1
    return n


def texte_recalage(c) -> str:
    """La trace d'un recalage, en clair, au format des traces 📅 du passage de nuit (relues par « Historique client »).
    C'est la seule mémoire des SMS et appels effacés : l'échéancier n'a pas de suivi des modifications. PURE."""
    from customization_app.Maintenance.update_schedule import _effaces_en_clair, _jj
    tete = "📅 Visite {0} du {1} réalisée le {2} (date réelle saisie sur l’échéancier)".format(
        c["item_code"], _jj(c["ancienne"]), _jj(c["reelle"]))
    if not c["deplacees"]:
        return tete + "."
    bouts = []
    for _r, avant, apres, effaces in c["deplacees"]:
        b = "{0} → {1}".format(_jj(avant), _jj(apres))
        clair = _effaces_en_clair(effaces or {})
        if clair:
            b += " (effacés : {0})".format(clair)
        bouts.append(b)
    return "{0}. {1} visite(s) suivante(s) décalée(s) de {2} j : {3}.".format(tete, len(c["deplacees"]), c["decalage"],
                                                                         " ; ".join(bouts))


def texte_futures(nom, futures) -> str:
    """Le signalement d'une date réelle dans le futur (journal et message). PURE."""
    from customization_app.Maintenance.update_schedule import _jj
    return ("Échéancier {0} : date réelle dans le futur, visite ni soldée ni recalée (faute de frappe ? intervention "
            "pas encore faite ?) — {1}.").format(nom, " ; ".join(
                "{0} prévue le {1}, date réelle {2}".format(_g(r, "item_code"), _jj(_g(r, "scheduled_date")), _jj(_g(r, "actual_date")))
                for r in futures))


def _signaler_futures(doc, futures):
    """Journal + message à l'utilisateur, jamais bloquant."""
    try:
        texte = texte_futures(getattr(doc, "name", "?"), futures)
        frappe.log_error(title="Recalage date réelle : date dans le futur — %s" % getattr(doc, "name", "?"), message=texte)
        frappe.msgprint(texte, indicator="orange", alert=True)
    except Exception:
        pass


def before_update_after_submit(doc, method=None):
    """Hook doc_events « Maintenance Schedule » → before_update_after_submit (remplace le Server Script « Update Schedule
    based on Real Date »). Sans effet quand chaque visite réalisée a date prévue = date réelle, ce qui est le cas de tout
    ce qu'écrivent le passage de nuit et « Fait lui-même ». `doc.flags.sans_recalage` : ne rien faire (correction à la
    main d'une date prévue). Jamais bloquant."""
    if doc.flags.get("sans_recalage"):
        return
    try:
        plan = plan_recalage(doc.get("schedules"))
        if plan.get("futures"):
            _signaler_futures(doc, plan["futures"])
        if not (plan["dates"] or plan["statuts"]):
            return
        textes = [texte_recalage(c) for c in plan["dates"]]
        appliquer_recalage(plan)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Recalage date réelle — échéancier %s" % getattr(doc, "name", "?"))
        return
    doc.flags.recalage_dates = len(textes)
    for t in textes:
        try:
            doc.add_comment("Info", t)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Trace du recalage — échéancier %s" % getattr(doc, "name", "?"))
