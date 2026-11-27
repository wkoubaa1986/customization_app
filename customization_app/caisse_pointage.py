"""Le POINTAGE des chèques et des traites au comptage d'une caisse (27/11/2026).

Sa question : « et les traites, pourquoi elles ne sont pas dans les caisses aussi, et les chèques, comment les employés
le montrent ? ». Le constat :

  · un employé écrit le montant de chaque chèque / traite qu'il remet (« 455+955 »), et le collecteur confirme ce
    qu'il reçoit — mais rien ne comparait ce qu'il DÉCLARE à ce qu'il a ENCAISSÉ ;
  · le collecteur principal et la direction (« validation seule ») ne déclaraient rien du tout — or c'est le collecteur
    qui tient les pièces : un chèque de 3 000 DT encaissé par lui le 23/11 n'est passé par aucun comptage.

Désormais chaque comptage POINTE les pièces, à l'aveugle comme les espèces (les montants attendus ne s'affichent
qu'après la saisie) :

  · le collecteur principal : toutes les pièces qu'il a EN MAIN — la liste « pas encore déposé » de la page « Où est
    l'argent liquide » (ses encaissements, ceux des employés collectés, les pièces en attente d'échéance) ;
  · une autre caisse en validation seule (la direction) : les pièces qu'elle a encaissées dans la journée ;
  · un employé : les pièces qu'il a encaissées dans sa période, en face de celles qu'il remet.

Le résultat (pièces manquantes, pièces en trop) est noté sur la clôture. Il ne bloque rien : il se voit — à la
collecte pour un employé, sur « Où est l'argent liquide » pour le collecteur.

Les fonctions pures n'utilisent pas `frappe.utils.flt` (hors site, il rend 0).
"""
from __future__ import annotations

import json

TYPES = (("Chèque", "chèque", "chèques"), ("Traite", "traite", "traites"))
MODE_DU_TYPE = {"Chèque": "Chèque", "Traite": "Traite bancaire LC"}


def _m(valeur) -> float:
    try:
        return round(float(valeur or 0), 3)
    except (TypeError, ValueError):
        return 0.0


def _dt(valeur) -> str:
    entier, _sep, millimes = f"{_m(valeur):,.3f}".replace(",", " ").partition(".")
    return f"{entier},{millimes}"


# ---------------------------------------------------------------------------------------------------------------- pur
def comparer(attendues, declarees) -> dict:
    """Les montants déclarés en face des montants attendus, pièce par pièce (au millime). PURE.
    -> {manquantes: [montants attendus que personne n'a déclarés], en_trop: [montants déclarés qui ne sont pas
    attendus]} — un même montant attendu deux fois doit être déclaré deux fois."""
    reste = sorted(_m(v) for v in attendues or [])
    en_trop = []
    for v in sorted(_m(x) for x in declarees or []):
        if v in reste:
            reste.remove(v)
        else:
            en_trop.append(v)
    return {"manquantes": reste, "en_trop": en_trop}


def pointer(attendu, declare, nombres=None) -> dict:
    """Le pointage complet d'un comptage. PURE.
    `attendu` : {"Chèque": [montants], "Traite": [montants]} ; `declare` : la même forme, ou None pour un type quand
    seul un NOMBRE de pièces a été saisi (ancienne saisie) — alors `nombres[type]` est comparé au nombre attendu.
    -> {"Chèque": {attendues, declarees, manquantes, en_trop, nb_attendu, nb_declare}, "Traite": {...}, ecarts}"""
    out, ecarts = {}, 0
    for cle, _un, _des in TYPES:
        att = [_m(v) for v in (attendu or {}).get(cle) or []]
        dec = (declare or {}).get(cle)
        if dec is None:
            nb = int((nombres or {}).get(cle) or 0)
            ligne = {"attendues": att, "declarees": None, "manquantes": [], "en_trop": [],
                     "nb_attendu": len(att), "nb_declare": nb}
            ecarts += abs(len(att) - nb)
        else:
            dec = [_m(v) for v in dec]
            ligne = dict(comparer(att, dec), attendues=att, declarees=dec, nb_attendu=len(att), nb_declare=len(dec))
            ecarts += len(ligne["manquantes"]) + len(ligne["en_trop"])
        out[cle] = ligne
    out["ecarts"] = ecarts
    return out


def resume(pointage) -> str:
    """« il manque 1 chèque (3 000,000) ; 1 traite en trop (500,000) », ou "" quand tout y est. PURE."""
    if not pointage or not pointage.get("ecarts"):
        return ""
    morceaux = []
    for cle, un, des in TYPES:
        l = pointage.get(cle) or {}
        if l.get("declarees") is None and l.get("nb_attendu") != l.get("nb_declare"):
            morceaux.append("%d %s attendu(s), %d déclaré(s)" % (l.get("nb_attendu") or 0, des, l.get("nb_declare") or 0))
            continue
        for liste, gabarit in ((l.get("manquantes"), "il manque %d %s (%s)"), (l.get("en_trop"), "%d %s en trop (%s)")):
            if liste:
                morceaux.append(gabarit % (len(liste), un if len(liste) == 1 else des, " + ".join(_dt(v) for v in liste)))
    return " ; ".join(morceaux)


def totaux(attendu) -> dict:
    """{"Chèque": {nb, total}, "Traite": {nb, total}} — ce que la fenêtre montre APRÈS la saisie. PURE."""
    return {cle: {"nb": len((attendu or {}).get(cle) or []), "total": _m(sum(_m(v) for v in (attendu or {}).get(cle) or []))}
            for cle, _un, _des in TYPES}


def en_texte(pointage) -> str:
    return json.dumps(pointage, ensure_ascii=False)


def lire(texte):
    """Le pointage noté sur une clôture, ou None."""
    try:
        return json.loads(texte) if texte else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------------------------------------- avec le site
def attendu_pour(caisse, mesures, tiroir=None) -> dict:
    """Ce que le comptage de cette caisse doit pointer : {"Chèque": [montants], "Traite": [montants], source}.
    `source` : « main » (le collecteur principal : toutes les pièces qu'il a en main) ou « jour » (les pièces que la
    caisse a encaissées dans la période comptée)."""
    if tiroir:
        from customization_app import argent_liquide as A
        from customization_app import caisse_collecte as CC
        from frappe.utils import nowdate

        pf = A._portefeuille(nowdate(), CC.config())
        out = {cle: [] for cle, _un, _des in TYPES}
        for p in pf["pieces"]:
            if p["chez_principal"] and not p["depose_le"] and p["type"] in out:
                out[p["type"]].append(_m(p["montant"]))
        return dict(out, source="main")
    from customization_app import caisse_paiements as CP

    out = {cle: [] for cle, _un, _des in TYPES}
    par_mode = {mode: cle for cle, mode in MODE_DU_TYPE.items()}
    for l in CP.pris_par((mesures or {}).get("data"), caisse):
        cle = par_mode.get(l["mode"])
        if cle:
            out[cle].append(_m(l["montant"]))
    return dict(out, source="jour")
