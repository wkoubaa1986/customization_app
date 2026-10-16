"""Présence à Aquaworld et domiciles d'après les NUITS du véhicule — onglet « Présence Aquaworld » du suivi terrain,
réservé au rôle « Sortie Circuit » (Wassim, Néjib), à côté de l'onglet « Sorties de circuit ».

Demande du 16/10/2026 : « détecte les arrêts la nuit ; estime l'heure d'arrivée à Aquaworld et l'heure de départ ;
affecte la position de la maison de chacun des employés », puis « statistiques globales par employé : quand partent-ils
d'Aquaworld (trajet Aquaworld → domicile le soir), trajet du matin domicile → Aquaworld, en distribution ; Jamel et
Sadok partent ensemble, Mohamed et Akram partent ensemble ».

LA LOGIQUE, en quatre temps, uniquement d'après le journal GPS (arrêts / trajets du véhicule) :
1. la NUIT D'AVANT = l'arrêt qui chevauche minuit au début du jour (commencé la veille, fini le matin) ; sa fin est
   l'heure de départ du matin, son point dit où le véhicule a dormi ;
2. la NUIT D'APRÈS = l'arrêt qui atteint la fin de la journée (23 h 59) ; son début est l'heure de retour du soir ;
3. entre les deux, les passages au MAGASIN (≤ 200 m du dépôt réglé) : le premier donne l'arrivée à Aquaworld, le
   dernier le départ d'Aquaworld, leur somme le temps passé sur place dans la journée ;
4. les TRAJETS : le matin, de la fin de la nuit d'avant au premier passage au Magasin ; le soir, du dernier départ du
   Magasin au début de la nuit d'après. On compte les arrêts intermédiaires (≥ 3 min) et on garde le plus long : le
   soir, c'est là qu'on dépose le collègue ; le matin, là qu'on le prend. Un trajet de plus de 3 h n'est pas un trajet
   domicile ↔ Aquaworld (le véhicule est passé par le terrain) : il sort des distributions.
Le lieu de chaque nuit (Domicile / Magasin / Autre) n'est PAS figé : il se lit à l'affichage contre le réglage des
tournées, pour qu'un domicile réglé après coup requalifie aussi l'historique.

QUI CONDUISAIT (16/10/2026) : les employés changent de voiture. L'estimation de là où habite chacun se fait sur TOUTES les
voitures confondues (`lieux_nuit_pure` : les endroits où les voitures dorment, c'est l'utilisateur qui dit quel endroit est
la maison de qui). Puis LA NUIT PRIME : un véhicule qui a dormi à ≤ 500 m (`fg.RAYON_NUIT_M`) du domicile réglé d'un employé
est le sien ce jour-là POUR LA PRÉSENCE (`fg.vehicules_du_jour(..., priorite='nuit')` au calcul, `reattribuer_pure` à la
lecture pour l'historique). Pour l'appariement des tâches et les sorties de circuit, ce sont les tâches qui disent qui
conduisait laquelle des voitures de l'équipage (priorité « taches », la nuit en secours) — mesuré le 16/10 : 136 passages
confirmés contre 78 en mettant la nuit d'abord.

⚠️ Un véhicule qui dort au Magasin (Hedi Chouchane, 3554) ne dit rien de l'arrivée du technicien : il vient autrement.
On montre alors « départ du Magasin » le matin et « retour au Magasin » le soir, sans inventer d'arrivée.

ÉQUIPAGES (Config Flotte GPS → « Qui rentre avec qui », une ligne par équipage : « Sadok Bouziri + Jamel Aloui ») :
un employé sans journée de véhicule à lui ce jour-là hérite de la journée de son coéquipier (mêmes heures, marqué
« avec X ») — ses nuits, elles, restent celles du conducteur et ne servent pas à son domicile ; son domicile se devine
à l'arrêt intermédiaire récurrent du trajet du soir (la dépose).

Les champs sont écrits sur `Journee Flotte GPS` (une ligne par véhicule et par jour) par l'appariement du soir
(`flotte_gps.apparier` → `_resume_journee`) et par le lot `calculer_periode` (bouton « Recalculer la période »,
≤ 62 jours, une lecture de la plateforme par jour).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import frappe
from frappe.utils import add_days, cint, flt, getdate, nowdate

from customization_app import flotte_circuit as fc
from customization_app import flotte_gps as fg
from customization_app import tournee_optimisation as to

RAYON_LIEU_M = fg.RAYON_LIEU_M                  # 200 m : « au Magasin », « au domicile »
FIN_JOUR_S = 90                                 # l'arrêt du soir atteint la fin du jour (23:59:59 au journal) à 90 s près
LIEU_M = fc.LIEU_M                              # deux nuits (ou deux déposes) à moins de 200 m = le même endroit
NUITS_MIN_DOMICILE = 3                          # en dessous, on ne propose pas un domicile
ARRET_TRAJET_MIN = 3                            # un arrêt de 3 min sur le trajet compte (dépose d'un collègue, café)
TRAJET_MAX_MIN = 180                            # au-delà, ce n'est pas un trajet domicile ↔ Aquaworld : le terrain s'y est glissé
PAS_TRANCHE_MIN = 30                            # distributions par demi-heure
MATIN_ARRIVEE_MAX = 12 * 60                     # un trajet du matin ARRIVE à Aquaworld avant midi ; après = demi-journée (après-midi)
SOIR_DEPART_MIN = 13 * 60                       # un trajet du soir quitte le Magasin après 13 h ; avant = demi-journée (matinée)
TRANCHES_MATIN = (7 * 60, 11 * 60)              # arrivées à Aquaworld : < 07:00 … ≥ 11:00
TRANCHES_SOIR = (15 * 60, 21 * 60)              # départs d'Aquaworld : < 15:00 … ≥ 21:00

CHAMPS = ("depart_matin", "arrivee_aquaworld", "depart_aquaworld", "retour_soir", "minutes_aquaworld",
          "nuit_avant_lat", "nuit_avant_lng", "nuit_lat", "nuit_lng",
          "arrets_matin", "matin_arret_lat", "matin_arret_lng", "matin_arret_minutes",
          "arrets_soir", "soir_arret_lat", "soir_arret_lng", "soir_arret_minutes")
_DATES = ("depart_matin", "arrivee_aquaworld", "depart_aquaworld", "retour_soir")
VIDE = {c: (None if c in _DATES else 0) for c in CHAMPS}


def _garde():
    fc._garde()


# ── Calcul pur ───────────────────────────────────────────────────────────────

def _m(a, b) -> float:
    return to.haversine_km(a, b) * 1000


def _pt(a: dict) -> tuple:
    return (flt(a.get("lat")), flt(a.get("lng")))


def nuits_pure(jour, arrets: list) -> tuple:
    """(nuit d'avant, nuit d'après) : l'arrêt qui chevauche minuit au début du jour, et celui qui atteint sa fin.
    Un véhicule immobile toute la journée rend le MÊME arrêt des deux côtés. PURE."""
    d0 = datetime.combine(getdate(jour), datetime.min.time())
    d1 = d0 + timedelta(days=1)
    arrets = sorted((a for a in arrets if a.get("arr") and a.get("dep")), key=lambda a: a["arr"])
    avant = next((a for a in arrets if a["arr"] < d0 < a["dep"]), None)
    apres = next((a for a in reversed(arrets) if a["arr"] < d1 and a["dep"] >= d1 - timedelta(seconds=FIN_JOUR_S)), None)
    return avant, apres


def _trajet(arrets: list, debut, fin) -> dict:
    """Les arrêts intermédiaires d'un trajet (≥ ARRET_TRAJET_MIN) : leur nombre et le plus long (la dépose). PURE."""
    inter = [a for a in arrets if a["arr"] >= debut and a["dep"] <= fin and cint(a.get("minutes")) >= ARRET_TRAJET_MIN]
    plus = max(inter, key=lambda a: cint(a["minutes"])) if inter else None
    return {"n": len(inter), "lat": flt(plus["lat"]) if plus else 0, "lng": flt(plus["lng"]) if plus else 0,
            "minutes": cint(plus["minutes"]) if plus else 0}


def presence_pure(jour, journal: dict, depot: tuple, rayon_m: float = RAYON_LIEU_M) -> dict:
    """Une journée d'un véhicule → heures de départ / arrivée / retour, points de nuit, arrêts des trajets. PURE.
    Rend aussi `immobile` (pas bougé de la journée) et `en_cours` (pas encore de nuit d'après : journée pas finie)."""
    d0 = datetime.combine(getdate(jour), datetime.min.time())
    d1 = d0 + timedelta(days=1)
    arrets = sorted((a for a in (journal or {}).get("arrets") or [] if a.get("arr") and a.get("dep")), key=lambda a: a["arr"])
    avant, apres = nuits_pure(jour, arrets)
    out = dict(VIDE, immobile=False, en_cours=False)
    if avant is not None:
        out["nuit_avant_lat"], out["nuit_avant_lng"] = _pt(avant)
        if avant is apres:                                       # immobile : ni départ ni retour
            out["immobile"] = True
            out["nuit_lat"], out["nuit_lng"] = _pt(avant)
            return out
        out["depart_matin"] = avant["dep"] if avant["dep"] < d1 else None
    if apres is not None:
        out["nuit_lat"], out["nuit_lng"] = _pt(apres)
        out["retour_soir"] = apres["arr"] if apres["arr"] >= d0 else None
    else:
        out["en_cours"] = True
    magasin = [a for a in arrets if a is not avant and a is not apres and a["dep"] > d0 and a["arr"] < d1
               and depot and _m(_pt(a), depot) <= rayon_m]
    if magasin:
        out["arrivee_aquaworld"] = max(magasin[0]["arr"], d0)
        out["depart_aquaworld"] = min(magasin[-1]["dep"], d1)
        out["minutes_aquaworld"] = sum(int((min(a["dep"], d1) - max(a["arr"], d0)).total_seconds() // 60) for a in magasin)
        if out["depart_matin"]:
            t = _trajet(arrets, out["depart_matin"], magasin[0]["arr"])
            out.update(arrets_matin=t["n"], matin_arret_lat=t["lat"], matin_arret_lng=t["lng"], matin_arret_minutes=t["minutes"])
        if out["retour_soir"]:
            t = _trajet(arrets, magasin[-1]["dep"], out["retour_soir"])
            out.update(arrets_soir=t["n"], soir_arret_lat=t["lat"], soir_arret_lng=t["lng"], soir_arret_minutes=t["minutes"])
    return out


def fenetres_trajets(jour, journal: dict, depot: tuple, max_min: int = TRAJET_MAX_MIN) -> list:
    """[(début, fin)] des trajets domicile ↔ Aquaworld d'un véhicule ce jour-là : le matin (départ de chez soi → 1er
    Magasin) et le soir (dernier Magasin → retour chez soi), seulement s'ils durent ≤ max_min (sinon le terrain s'y est
    glissé : ce ne sont pas des trajets). Sert aux sorties de circuit pour ne pas compter un arrêt en chemin comme un
    arrêt « en plus » de la journée. PURE."""
    p = presence_pure(jour, journal, depot)
    out = []
    for d, f in ((p.get("depart_matin"), p.get("arrivee_aquaworld")), (p.get("depart_aquaworld"), p.get("retour_soir"))):
        if d and f and f >= d and (f - d).total_seconds() / 60 <= max_min:
            out.append((d, f))
    return out


def lieu(point: tuple | None, depot: tuple | None, domicile: tuple | None, rayon_m: float = RAYON_LIEU_M) -> str:
    """« Magasin » / « Domicile » (réglé, ≤ 200 m) / « Autre » / « » sans point. PURE."""
    if not point or not point[0]:
        return ""
    if depot and _m(point, depot) <= rayon_m:
        return "Magasin"
    if domicile and _m(point, domicile) <= rayon_m:
        return "Domicile"
    return "Autre"


def _grouper(points: list, rayon_m: float = LIEU_M) -> list:
    """Regroupement par distance au premier point de l'endroit (pas par grille). points = [{lat, lng, date, minutes, passager}].
    `passagers` = combien de ces arrêts viennent d'une journée héritée d'un coéquipier : si c'est la majorité, l'arrêt est
    la dépose de l'employé lui-même (son domicile probable) ; sinon c'est lui qui dépose un coéquipier. PURE."""
    lieux = []
    for p in points:
        if not p.get("lat"):
            continue
        pt = (flt(p["lat"]), flt(p["lng"]))
        c = next((c for c in lieux if _m(pt, (c["lat"], c["lng"])) <= rayon_m), None)
        if c is None:
            c = {"lat": pt[0], "lng": pt[1], "n": 0, "passagers": 0, "derniere": None, "minutes": 0}
            lieux.append(c)
        c["n"] += 1
        c["passagers"] += 1 if p.get("passager") else 0
        c["minutes"] += cint(p.get("minutes"))
        d = str(p.get("date") or "")
        if d > str(c["derniere"] or ""):
            c["derniere"] = d
    return sorted(lieux, key=lambda c: -c["n"])


def lieux_nuit_pure(nuits: list, depot: tuple, departs: dict, noms: dict | None = None, rayon_m: float = RAYON_LIEU_M,
                    minimum: int = NUITS_MIN_DOMICILE) -> list:
    """Où dorment les voitures la nuit, TOUTES VOITURES CONFONDUES (demande du 16/10/2026 : « l'estimation de là où
    habite chacun doit être faite à partir de n'importe quelle voiture »). `nuits` = [{lat, lng, date, vehicule, employe,
    employe_nom}] (nuit d'avant et nuit d'après de chaque journée véhicule). Regroupement par distance (≤ LIEU_M) ; par
    endroit : nuits, première / dernière date, véhicules, employés auxquels les journées étaient attribuées (indice), et
    le statut : `magasin`, `domicile` (domicile réglé de `employe`, ≤ rayon), `a_regler` (≥ minimum nuits), `rare`.
    Les domiciles réglés qui n'ont aucune nuit sur la période sont ajoutés (n = 0) pour rester visibles. PURE."""
    noms = noms or {}
    lieux = []
    for n in nuits:
        if not n.get("lat"):
            continue
        pt = (flt(n["lat"]), flt(n["lng"]))
        c = next((c for c in lieux if _m(pt, (c["lat"], c["lng"])) <= LIEU_M), None)
        if c is None:
            c = {"lat": pt[0], "lng": pt[1], "n": 0, "premiere": None, "derniere": None, "vehicules": {}, "employes": {}}
            lieux.append(c)
        c["n"] += 1
        d = str(n.get("date") or "")
        if d and (c["premiere"] is None or d < c["premiere"]):
            c["premiere"] = d
        if d > str(c["derniere"] or ""):
            c["derniere"] = d
        if n.get("vehicule"):
            c["vehicules"][n["vehicule"]] = c["vehicules"].get(n["vehicule"], 0) + 1
        if n.get("employe"):
            nom = n.get("employe_nom") or noms.get(n["employe"]) or n["employe"]
            c["employes"][nom] = c["employes"].get(nom, 0) + 1
    for e, dom in (departs or {}).items():
        if dom and not any(_m((c["lat"], c["lng"]), dom) <= rayon_m for c in lieux):
            lieux.append({"lat": flt(dom[0]), "lng": flt(dom[1]), "n": 0, "premiere": None, "derniere": None, "vehicules": {}, "employes": {}})
    for c in lieux:
        pt = (c["lat"], c["lng"])
        regle = sorted((_m(pt, dom), e) for e, dom in (departs or {}).items() if dom and _m(pt, dom) <= rayon_m)
        c["employe"] = regle[0][1] if regle else None
        c["employe_nom"] = noms.get(c["employe"], c["employe"]) if c["employe"] else None
        c["magasin"] = bool(depot) and _m(pt, depot) <= rayon_m
        c["statut"] = "magasin" if c["magasin"] else "domicile" if c["employe"] else "a_regler" if c["n"] >= minimum else "rare"
        c["employes"] = dict(sorted(c["employes"].items(), key=lambda kv: -kv[1]))
        c["vehicules"] = dict(sorted(c["vehicules"].items(), key=lambda kv: -kv[1]))
        # Suggestion : l'employé le plus souvent attribué ces jours-là, s'il n'a pas déjà un domicile réglé ailleurs
        c["suggestion"] = next((n for n in c["employes"]), None) if c["statut"] == "a_regler" else None
    return sorted(lieux, key=lambda c: (-c["n"], c["lat"]))


def reattribuer_pure(lignes: list, departs: dict, noms: dict, rayon_m: float = fg.RAYON_NUIT_M, depot: tuple | None = None) -> list:
    """La nuit d'avant prime aussi à la lecture : une journée dont le véhicule a dormi à ≤ 500 m du domicile réglé d'un
    employé est la sienne, quoi qu'aient dit les tâches le soir du calcul (régler un domicile requalifie l'historique).
    L'ancien employé reste en `attribue_par_taches`. Un même employé ne prend qu'une journée par date (la plus proche). PURE."""
    out = [dict(l) for l in lignes]
    pris = set()
    cand = []
    for i, l in enumerate(out):
        if not flt(l.get("nuit_avant_lat")):
            continue
        pt = (flt(l["nuit_avant_lat"]), flt(l["nuit_avant_lng"]))
        if depot and _m(pt, depot) <= RAYON_LIEU_M:                 # a dormi au Magasin : les tâches décident
            continue
        for e, dom in (departs or {}).items():
            if not dom or (depot and _m(dom, depot) <= fg.DOMICILE_PROCHE_DEPOT_M):
                continue                                            # domicile collé au Magasin (Néjib) : pas une preuve
            if _m(pt, dom) <= rayon_m:
                cand.append((_m(pt, dom), i, e))
    for _, i, e in sorted(cand):
        l = out[i]
        if (str(l.get("date")), e) in pris or l.get("_fixe"):
            continue
        pris.add((str(l.get("date")), e))
        l["_fixe"] = True
        if l.get("employe") != e:
            l["attribue_par_taches"] = l.get("employe_nom") or l.get("employe") or None
            l["employe"], l["employe_nom"] = e, noms.get(e, e)
    for l in out:
        l.pop("_fixe", None)
    return out


def _dt(v):
    if not v or isinstance(v, datetime):
        return v or None
    return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S")


def _minutes(dt) -> int | None:
    dt = _dt(dt)
    return dt.hour * 60 + dt.minute if dt else None


def _duree(a, b) -> int | None:
    a, b = _dt(a), _dt(b)
    return int((b - a).total_seconds() // 60) if a and b and b >= a else None


def _hhmm(minutes) -> str:
    return "%02d:%02d" % (minutes // 60, minutes % 60) if minutes is not None else ""


def _mediane(valeurs: list):
    v = sorted(x for x in valeurs if x is not None)
    if not v:
        return None
    return v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) // 2


def tranches(valeurs: list, bornes: tuple, pas: int = PAS_TRANCHE_MIN) -> list:
    """Distribution par demi-heure entre deux bornes, avec une tranche ouverte à chaque bout : [[libellé, n]]. PURE."""
    debut, fin = bornes
    out = [["< %s" % _hhmm(debut), 0]] + [[_hhmm(m), 0] for m in range(debut, fin, pas)] + [["≥ %s" % _hhmm(fin), 0]]
    for v in valeurs:
        if v is None:
            continue
        if v < debut:
            out[0][1] += 1
        elif v >= fin:
            out[-1][1] += 1
        else:
            out[1 + (v - debut) // pas][1] += 1
    return out


def statistiques_pure(lignes: list) -> list:
    """Par employé : jours suivis, jours passés au Magasin, heures médianes (départ matin, arrivée / départ Aquaworld,
    retour soir), temps médian sur place, nuits par lieu. `lignes` = les journées déjà qualifiées (nuit_avant_lieu…) ;
    une ligne `passager` (héritée d'un coéquipier) compte pour les heures, pas pour les nuits. PURE."""
    par = {}
    for l in lignes:
        cle = l.get("employe") or l.get("vehicule") or "?"
        e = par.setdefault(cle, {"employe": l.get("employe"), "employe_nom": l.get("employe_nom") or l.get("vehicule") or "?",
                                 "jours": 0, "jours_magasin": 0, "immobiles": 0, "passager": 0, "depart_matin": [], "arrivee": [],
                                 "depart": [], "retour": [], "minutes": [], "nuits": {"Domicile": 0, "Magasin": 0, "Autre": 0}})
        e["jours"] += 1
        if l.get("immobile"):
            e["immobiles"] += 1
        if l.get("passager"):
            e["passager"] += 1
        if l.get("arrivee_aquaworld") or l.get("depart_aquaworld"):
            e["jours_magasin"] += 1
        e["depart_matin"].append(_minutes(l.get("depart_matin")))
        e["arrivee"].append(_minutes(l.get("arrivee_aquaworld")))
        e["depart"].append(_minutes(l.get("depart_aquaworld")))
        e["retour"].append(_minutes(l.get("retour_soir")))
        if l.get("minutes_aquaworld"):
            e["minutes"].append(cint(l["minutes_aquaworld"]))
        if not l.get("passager"):
            for k in ("nuit_avant_lieu", "nuit_lieu"):
                if l.get(k) in e["nuits"]:
                    e["nuits"][l[k]] += 1
    out = []
    for e in par.values():
        out.append({"employe": e["employe"], "employe_nom": e["employe_nom"], "jours": e["jours"], "jours_magasin": e["jours_magasin"],
                    "immobiles": e["immobiles"], "passager": e["passager"], "depart_matin": _hhmm(_mediane(e["depart_matin"])),
                    "arrivee": _hhmm(_mediane(e["arrivee"])), "depart": _hhmm(_mediane(e["depart"])), "retour": _hhmm(_mediane(e["retour"])),
                    "minutes_aquaworld": _mediane(e["minutes"]) or 0, "nuits": e["nuits"]})
    return sorted(out, key=lambda e: e["employe_nom"] or "")


def trajets_pure(lignes: list, max_min: int = TRAJET_MAX_MIN, depot: tuple | None = None) -> list:
    """Par employé, les trajets domicile ↔ Aquaworld et leurs distributions. PURE.

    MATIN (nuit d'avant hors Magasin, départ du matin ET arrivée à Aquaworld connus) : heure d'arrivée à Aquaworld par
    demi-heure, durée médiane du trajet, part de trajets directs (aucun arrêt ≥ 3 min), endroits où l'on s'arrête en
    chemin (prise en charge d'un collègue). SOIR (nuit d'après hors Magasin, départ d'Aquaworld ET retour connus) :
    heure de départ d'Aquaworld par demi-heure, durée, directs, endroits de dépose. Un trajet > max_min n'est pas un
    trajet domicile ↔ Aquaworld (le terrain s'y est glissé) : compté « par le terrain », hors distribution.
    `sans_passage` = rentré au domicile (ou parti de chez soi) sans passer du tout par Aquaworld ce jour-là.
    PRÉCISION (16/10/2026) : une arrivée à Aquaworld après midi n'est pas un trajet du matin (`demi_journee`), un départ du
    Magasin avant 13 h n'est pas un trajet du soir (`demi_journee`) ; une nuit à ≤ 1 km du Magasin (résidence de Néjib,
    830 m) n'est pas un domicile d'où l'on part : pas de trajet."""
    def colle_au_depot(lat, lng):
        return bool(depot) and bool(flt(lat)) and _m((flt(lat), flt(lng)), depot) <= fg.DOMICILE_PROCHE_DEPOT_M
    par = {}
    for l in lignes:
        if not l.get("employe"):
            continue
        e = par.setdefault(l["employe"], {"employe": l["employe"], "employe_nom": l.get("employe_nom") or l["employe"],
                                           "matin": {"heures": [], "durees": [], "directs": 0, "n": 0, "par_terrain": 0, "sans_passage": 0, "demi_journee": 0, "points": [], "magasin_heures": []},
                                           "soir": {"heures": [], "durees": [], "directs": 0, "n": 0, "par_terrain": 0, "sans_passage": 0, "demi_journee": 0, "points": [], "magasin_heures": []}})
        # La voiture a dormi au Magasin (ou l'employé était passager d'une telle voiture) : on ne voit pas l'arrivée, mais la
        # PREMIÈRE SORTIE du Magasin la borne (il était là avant) ; le soir, le DERNIER RETOUR au Magasin borne la fin du terrain.
        if (l.get("nuit_avant_lieu") == "Magasin" or colle_au_depot(l.get("nuit_avant_lat"), l.get("nuit_avant_lng"))) and l.get("premiere_sortie"):
            e["matin"]["magasin_heures"].append(_minutes(l["premiere_sortie"]))
        if (l.get("nuit_lieu") == "Magasin" or colle_au_depot(l.get("nuit_lat"), l.get("nuit_lng"))) and l.get("dernier_retour"):
            e["soir"]["magasin_heures"].append(_minutes(l["dernier_retour"]))
        # Matin : domicile → Aquaworld
        if l.get("nuit_avant_lieu") and l["nuit_avant_lieu"] != "Magasin" and l.get("depart_matin") and not colle_au_depot(l.get("nuit_avant_lat"), l.get("nuit_avant_lng")):
            m = e["matin"]
            if l.get("arrivee_aquaworld"):
                d = _duree(l["depart_matin"], l["arrivee_aquaworld"])
                if d is not None and d > max_min:
                    m["par_terrain"] += 1
                elif (_minutes(l["arrivee_aquaworld"]) or 0) >= MATIN_ARRIVEE_MAX:
                    m["demi_journee"] += 1
                elif d is not None:
                    m["n"] += 1
                    m["heures"].append(_minutes(l["arrivee_aquaworld"]))
                    m["durees"].append(d)
                    if not cint(l.get("arrets_matin")):
                        m["directs"] += 1
                    elif flt(l.get("matin_arret_lat")):
                        m["points"].append({"lat": l["matin_arret_lat"], "lng": l["matin_arret_lng"], "date": l.get("date"),
                                            "minutes": l.get("matin_arret_minutes"), "passager": bool(l.get("passager"))})
                else:
                    m["par_terrain"] += 1
            else:
                m["sans_passage"] += 1
        # Soir : Aquaworld → domicile
        if l.get("nuit_lieu") and l["nuit_lieu"] != "Magasin" and l.get("retour_soir") and not colle_au_depot(l.get("nuit_lat"), l.get("nuit_lng")):
            s = e["soir"]
            if l.get("depart_aquaworld"):
                d = _duree(l["depart_aquaworld"], l["retour_soir"])
                if d is not None and d > max_min:
                    s["par_terrain"] += 1
                elif (_minutes(l["depart_aquaworld"]) or 0) < SOIR_DEPART_MIN:
                    s["demi_journee"] += 1
                elif d is not None:
                    s["n"] += 1
                    s["heures"].append(_minutes(l["depart_aquaworld"]))
                    s["durees"].append(d)
                    if not cint(l.get("arrets_soir")):
                        s["directs"] += 1
                    elif flt(l.get("soir_arret_lat")):
                        s["points"].append({"lat": l["soir_arret_lat"], "lng": l["soir_arret_lng"], "date": l.get("date"),
                                            "minutes": l.get("soir_arret_minutes"), "passager": bool(l.get("passager"))})
                else:
                    s["par_terrain"] += 1
            else:
                s["sans_passage"] += 1
    out = []
    for e in par.values():
        for sens, bornes in (("matin", TRANCHES_MATIN), ("soir", TRANCHES_SOIR)):
            b = e[sens]
            b["mediane"] = _hhmm(_mediane(b["heures"]))
            b["premiere"] = _hhmm(min((h for h in b["heures"] if h is not None), default=None))
            b["derniere"] = _hhmm(max((h for h in b["heures"] if h is not None), default=None))
            b["duree_mediane"] = _mediane(b["durees"]) or 0
            b["tranches"] = tranches(b["heures"], bornes)
            b["lieux"] = _grouper(b["points"])[:3]
            mh = [x for x in b["magasin_heures"] if x is not None]
            b["magasin"] = {"n": len(mh), "mediane": _hhmm(_mediane(mh)), "premiere": _hhmm(min(mh, default=None)), "derniere": _hhmm(max(mh, default=None)),
                            "tranches": tranches(mh, bornes)}
            del b["heures"], b["durees"], b["points"], b["magasin_heures"]
        out.append(e)
    return sorted(out, key=lambda e: e["employe_nom"] or "")


# ── Équipages ────────────────────────────────────────────────────────────────

def _a_covoiturage() -> bool:
    """Le champ « Qui rentre avec qui » existe-t-il déjà (Single : pas de table, donc pas de has_column) ?"""
    try:
        return bool(frappe.get_meta(fg.CONFIG).has_field("covoiturage"))
    except Exception:
        return False


def equipages_pure(texte: str, employes: list) -> list:
    return fg.equipages_pure(texte, employes)


def equipages() -> list:
    return fg.equipages()


def en_poste_pure(employe: str, date, periodes: dict | None) -> bool:
    """L'employé était-il en poste ce jour-là ? `periodes` = {employé: (date d'embauche | None, date de départ | None)}.
    Sans information, oui. PURE."""
    if not periodes or employe not in periodes:
        return True
    d, f = periodes[employe]
    date = str(date)[:10]
    return not (d and date < str(d)[:10]) and not (f and date > str(f)[:10])


def heriter_pure(lignes: list, groupes: list, noms: dict, periodes: dict | None = None) -> list:
    """Un coéquipier sans journée de véhicule à lui ce jour-là hérite de celle de son équipage (mêmes heures), marquée
    `passager` + `avec` — s'il était EN POSTE ce jour-là (`periodes`, fiche Employé : Akram, embauché le 29/07, héritait des
    journées de Mohamed de juin-juillet, 16/10/2026). Les nuits restent au conducteur. PURE."""
    reelles = {(str(l.get("date")), l.get("employe")) for l in lignes if l.get("employe")}
    groupe_de = {}
    for g in groupes:
        for m in g:
            groupe_de.setdefault(m, set()).update(x for x in g if x != m)
    out = list(lignes)
    for l in lignes:
        e = l.get("employe")
        if not e or l.get("passager") or l.get("immobile"):        # une voiture qui n'a pas bougé n'a transporté personne
            continue
        for p in sorted(groupe_de.get(e, ())):
            if (str(l.get("date")), p) in reelles or not en_poste_pure(p, l.get("date"), periodes):
                continue
            v = dict(l, employe=p, employe_nom=noms.get(p, p), passager=True, avec=l.get("employe_nom") or e,
                     name="%s|%s" % (l.get("name"), p))
            v.pop("attribue_par_taches", None)                   # la note « par la nuit » concerne le conducteur, pas la copie
            reelles.add((str(l.get("date")), p))
            out.append(v)
    return out


# ── Calcul et écriture ───────────────────────────────────────────────────────

def calculer(jour=None, ecrire: bool = True, journaux: dict | None = None) -> dict:
    """Les présences d'un jour pour tous les véhicules, écrites sur Journee Flotte GPS (ligne créée si absente).
    `journaux` : le journal déjà lu (lire_journal) pour ne pas solliciter la plateforme deux fois (rattrapage)."""
    jour = getdate(jour or nowdate())
    cfg = fg.config()
    cfg_t = to.config()
    cboxes = cfg["cboxes"]
    if journaux is None:
        journaux = fg.lire_journal(jour, cboxes, cfg=cfg) if cboxes else {}
    taches = fg._taches(jour)
    emp_cbox = fg.vehicules_du_jour(taches, journaux, fg.defaut_du_jour(cfg, jour), cfg["rayon"], fg._lieux_communs(cfg_t),
                                    nuits={cb: fg.point_de_nuit(jour, j) for cb, j in journaux.items()}, domiciles=cfg_t["departs"], depot=cfg_t["depot"],
                                    groupes=fg.equipages(), priorite="nuit")
    for emp, cb in fg.defaut_du_jour(cfg, jour).items():         # véhicule sans employé reconnu : son conducteur habituel (en poste)
        if cb not in emp_cbox.values() and emp not in emp_cbox:
            emp_cbox[emp] = cb
    cbox_emp = fg.principal_par_cbox(emp_cbox)                   # la journée véhicule porte le conducteur principal
    bilan = {"jour": str(jour), "vehicules": 0, "lignes": [], "ecrit": ecrire}
    for cbox in cboxes:
        j = journaux.get(cbox)
        if not j or j.get("erreur"):
            continue
        p = presence_pure(jour, j, cfg_t["depot"])
        emp = cbox_emp.get(cbox)
        p.update(date=jour, cbox=cbox, vehicule=cfg["cbox_vehicule"].get(cbox) or cbox, employe=emp)
        bilan["vehicules"] += 1
        bilan["lignes"].append(p)
        if not ecrire:
            continue
        nom = frappe.db.exists(fg.JOURNEE, {"date": jour, "cbox": cbox})
        if nom:
            valeurs = {c: p[c] for c in CHAMPS}
            if emp and not frappe.db.get_value(fg.JOURNEE, nom, "employe"):      # journée sans employé : la nuit (ou le défaut) le désigne
                valeurs.update(employe=emp, employe_nom=frappe.db.get_value("Employee", emp, "employee_name"))
            if not flt(frappe.db.get_value(fg.JOURNEE, nom, "km")):              # ligne créée par ce lot avant l'appariement
                res = fg._resume_journee(jour, cbox, j, [], {}, cfg, depot=cfg_t["depot"])
                valeurs.update({k: res[k] for k in ("km", "minutes_conduite", "minutes_arrets", "nb_arrets", "premiere_sortie", "dernier_retour")})
            frappe.db.set_value(fg.JOURNEE, nom, valeurs, update_modified=False)
        else:
            # Journée pas encore écrite par l'appariement du soir : on la crée entière (km, conduite, arrêts, présence) ;
            # les compteurs de tâches seront posés par l'appariement, qui repasse dessus.
            doc = frappe.new_doc(fg.JOURNEE)
            doc.update(fg._resume_journee(jour, cbox, j, [], {}, cfg, depot=cfg_t["depot"]))
            doc.update({"employe": emp, "employe_nom": frappe.db.get_value("Employee", emp, "employee_name") if emp else None})
            doc.insert(ignore_permissions=True)
    return bilan


def calculer_periode(du, au) -> dict:
    """Lot : du → au inclus (bench execute customization_app.flotte_presence.calculer_periode --args "['2026-09-14','2026-10-15']")."""
    du, au = getdate(du), getdate(au)
    out = {"jours": 0, "vehicules": 0, "erreurs": []}
    j = du
    while j <= au:
        try:
            b = calculer(j)
            out["jours"] += 1
            out["vehicules"] += b["vehicules"]
            frappe.db.commit()
        except Exception as ex:
            out["erreurs"].append("%s : %s" % (j, str(ex)[:120]))
        j = add_days(j, 1)
    return out


# ── Rattrapage depuis la date de début des statistiques ──────────────────────
#
# Réglage « Statistiques depuis le » (Config Flotte GPS). Les onglets Sorties de circuit et Présence Aquaworld partent de
# cette date ; le bouton « Rattraper les jours manquants » calcule, en tâche de fond (file `long`), les jours de la période
# qui n'ont encore aucune présence écrite — présence ET écarts de circuit d'un coup, une seule lecture de la plateforme par
# jour. Un jour est « calculé » dès qu'une journée de véhicule porte un point de nuit ou une heure (immobile compris) ;
# un jour sans journal (plateforme muette, boîtiers éteints) reste « manquant » et sera retenté au prochain rattrapage.

CLE_RATTRAPAGE = "flotte_rattrapage"            # état d'avancement en cache (lu par les onglets) ; aussi l'identifiant du job


def jours_manquants_pure(du, au, calcules: set, aujourd_hui) -> list:
    """Les jours de [du, au] ∩ passé (hier au plus tard : la journée du jour n'est pas finie) sans calcul. PURE."""
    du, au = getdate(du), min(getdate(au), add_days(getdate(aujourd_hui), -1))
    out, j = [], du
    while j <= au:
        if str(j) not in calcules:
            out.append(str(j))
        j = add_days(j, 1)
    return out


def jours_manquants(du, au) -> list:
    rows = frappe.get_all(fg.JOURNEE, filters={"date": ["between", [getdate(du), getdate(au)]]},
                          or_filters=[["nuit_lat", "!=", 0], ["nuit_avant_lat", "!=", 0], ["depart_matin", "is", "set"]],
                          fields=["date"], distinct=True, limit_page_length=0)
    return jours_manquants_pure(du, au, {str(r.date) for r in rows}, nowdate())


def _etat(**maj) -> dict:
    etat = frappe.cache().get_value(CLE_RATTRAPAGE) or {}
    if maj:
        etat.update(maj)
        frappe.cache().set_value(CLE_RATTRAPAGE, etat, expires_in_sec=7 * 86400)
    return etat


def _rattraper_job(jours: list, taches: bool = False):
    """Tâche de fond : pour chaque jour, UNE lecture de la plateforme, puis présence + écarts de circuit — et, si `taches`,
    l'appariement des tâches (qui conduisait quoi, passage confirmé ou non) : « Tout recalculer » après un changement de
    domicile ou d'équipage, pour que l'historique suive les règles du jour.
    L'état complet est réécrit à chaque jour (pas fusionné) : un cache vidé en route (clear-cache, redéploiement) ne laisse
    pas un avancement sans total ni statut."""
    etat = {"statut": "en cours", "total": len(jours), "faits": 0, "erreurs": [], "ecarts": 0, "vehicules": 0, "jour": None,
            "du": jours[0] if jours else None, "au": jours[-1] if jours else None, "debut": str(frappe.utils.now_datetime())[:19],
            "taches": bool(taches)}
    _etat(**etat)
    for jour in jours:
        try:
            cfg = fg.config()
            journaux = fg.lire_journal(jour, cfg["cboxes"], cfg=cfg) if cfg["cboxes"] else {}
            if taches:
                fg.apparier(jour, journaux=journaux)                 # écrit aussi la journée véhicule (km, employé)
            b = calculer(jour, journaux=journaux)
            c = fc.calculer(jour, journaux=journaux)
            frappe.db.commit()
            etat["vehicules"] += b["vehicules"]
            etat["ecarts"] += c["ecarts"]
        except Exception as ex:
            frappe.db.rollback()
            etat["erreurs"].append("%s : %s" % (jour, str(ex)[:120]))
        etat["faits"] += 1
        etat["jour"] = str(jour)
        _etat(**etat)
    etat.update(statut="terminé", fin=str(frappe.utils.now_datetime())[:19])
    _etat(**etat)


@frappe.whitelist(methods=["POST"])
def rattraper(du=None, au=None, tout=0):
    """Bouton « Rattraper les jours manquants » : depuis le réglage (ou `du`) jusqu'à hier, en tâche de fond.
    `tout` = « Tout recalculer » : chaque jour de la période (pas seulement les manquants), appariement des tâches compris —
    après un changement de domicile, d'équipage ou de règle, pour que l'historique suive."""
    _garde()
    from frappe.utils.background_jobs import is_job_enqueued
    depuis = fg.stats_depuis()
    du = getdate(du) if du else depuis
    if not du:
        frappe.throw("Réglez d'abord « Statistiques depuis le » (Config Flotte GPS)")
    au = getdate(au or nowdate())
    if is_job_enqueued(CLE_RATTRAPAGE):
        return dict(_etat(), deja=True)
    jours = jours_manquants_pure(du, au, set(), nowdate()) if cint(tout) else jours_manquants(du, au)
    if not jours:
        return dict(_etat(statut="rien à faire", total=0, faits=0, jours=[]), jours=0)
    frappe.enqueue("customization_app.flotte_presence._rattraper_job", queue="long", timeout=10 * 3600, job_id=CLE_RATTRAPAGE,
                   enqueue_after_commit=True, jours=jours, taches=bool(cint(tout)))
    return dict(_etat(statut="en attente", total=len(jours), faits=0, erreurs=[], jour=None, du=jours[0], au=jours[-1]), jours=len(jours))


@frappe.whitelist()
def etat_rattrapage():
    """Où en est le rattrapage (lu toutes les quelques secondes par l'onglet tant qu'il tourne)."""
    _garde()
    from frappe.utils.background_jobs import is_job_enqueued
    return dict(_etat(), actif=is_job_enqueued(CLE_RATTRAPAGE))


# ── Page ─────────────────────────────────────────────────────────────────────

def _qualifier(l: dict, depot: tuple, departs: dict) -> dict:
    dom = departs.get(l.get("employe")) if l.get("employe") else None
    l["nuit_avant_lieu"] = lieu((flt(l.get("nuit_avant_lat")), flt(l.get("nuit_avant_lng"))), depot, dom)
    l["nuit_lieu"] = lieu((flt(l.get("nuit_lat")), flt(l.get("nuit_lng"))), depot, dom)
    # Immobile : une seule nuit des deux côtés (même point), ni départ, ni retour, ni passage au Magasin.
    l["immobile"] = bool(l.get("nuit_avant_lat") and l.get("nuit_lat") and not l.get("depart_matin") and not l.get("retour_soir")
                         and not l.get("arrivee_aquaworld")
                         and _m((flt(l["nuit_avant_lat"]), flt(l["nuit_avant_lng"])), (flt(l["nuit_lat"]), flt(l["nuit_lng"]))) <= LIEU_M)
    l["en_cours"] = bool(l.get("nuit_avant_lat") and not l.get("nuit_lat"))
    l["duree_matin"] = _duree(l.get("depart_matin"), l.get("arrivee_aquaworld")) if l["nuit_avant_lieu"] and l["nuit_avant_lieu"] != "Magasin" else None
    l["duree_soir"] = _duree(l.get("depart_aquaworld"), l.get("retour_soir")) if l["nuit_lieu"] and l["nuit_lieu"] != "Magasin" else None
    return l


@frappe.whitelist()
def presence(du=None, au=None, employe=None):
    """Les journées d'une période (heures, nuits qualifiées, coéquipiers hérités), statistiques et trajets par employé,
    domiciles détectés."""
    _garde()
    au = getdate(au or nowdate())
    depuis = fg.stats_depuis()
    du = getdate(du) if du else (depuis if depuis and depuis <= au else add_days(au, -29))
    cfg_t = to.config()
    depot, departs = cfg_t["depot"], cfg_t["departs"]
    lignes = frappe.get_all(fg.JOURNEE, filters={"date": ["between", [du, au]]}, order_by="date desc, employe_nom asc, vehicule asc",
                            limit_page_length=0, fields=["name", "date", "vehicule", "cbox", "employe", "employe_nom", "km", "premiere_sortie", "dernier_retour", *CHAMPS])
    noms = {e.name: e.employee_name for e in frappe.get_all("Employee", fields=["name", "employee_name"], limit_page_length=0)}
    lignes = reattribuer_pure([dict(l) for l in lignes], departs, noms, depot=depot)   # la nuit d'avant prime sur les tâches
    lignes = [_qualifier(l, depot, departs) for l in lignes]
    suivies = [l for l in lignes if l.get("nuit_avant_lat") or l.get("nuit_lat") or l.get("arrivee_aquaworld")]
    nuits = []
    for l in suivies:
        for k in ("nuit_avant", "nuit"):
            if l.get(k + "_lat"):
                nuits.append({"employe": l["employe"], "employe_nom": l["employe_nom"], "vehicule": l.get("vehicule"),
                              "lat": l[k + "_lat"], "lng": l[k + "_lng"], "date": l["date"]})
    groupes = equipages()
    periodes = {e.name: (e.date_of_joining, e.relieving_date) for e in frappe.get_all("Employee", fields=["name", "date_of_joining", "relieving_date"], limit_page_length=0)}
    toutes = heriter_pure(suivies, groupes, noms, periodes)
    toutes.sort(key=lambda l: (str(l["date"]), l.get("employe_nom") or "", l.get("vehicule") or ""), reverse=True)
    if employe:
        toutes = [l for l in toutes if l.get("employe") == employe]
    employes = sorted({(l["employe"], l["employe_nom"]) for l in toutes if l["employe"]}, key=lambda x: x[1] or "")
    # Techniciens proposés pour « Définir comme domicile » : conducteurs du réglage, membres d'équipage, employés des journées
    cfg_g = fg.config()
    techniciens = {e for e in cfg_g["employe_cbox"]} | {m for g in groupes for m in g} | {e for e, _ in employes} | set(departs)
    return {"du": str(du), "au": str(au), "stats_depuis": str(depuis) if depuis else None, "jours_manquants": len(jours_manquants(du, au)),
            "lignes": toutes, "non_calculees": len(lignes) - len(suivies),
            "stats": statistiques_pure(toutes), "trajets": trajets_pure(toutes, depot=depot),
            "lieux_nuit": lieux_nuit_pure(nuits, depot, departs, noms),
            "reglages": [{"employe": e, "employe_nom": noms.get(e, e), "lat": d[0], "lng": d[1]} for e, d in departs.items() if d],
            "techniciens": sorted(({"employe": e, "nom": noms.get(e, e)} for e in techniciens if e in noms), key=lambda x: x["nom"]),
            "equipages": [[noms.get(m, m) for m in g] for g in groupes],
            "covoiturage": (frappe.db.get_single_value(fg.CONFIG, "covoiturage") or "") if _a_covoiturage() else "",
            "employes": [{"employe": e, "nom": n} for e, n in employes], "depot": list(depot),
            "seuils": {"rayon_m": RAYON_LIEU_M, "lieu_m": LIEU_M, "nuits_min": NUITS_MIN_DOMICILE, "trajet_max_min": TRAJET_MAX_MIN,
                       "arret_trajet_min": ARRET_TRAJET_MIN, "rayon_nuit_m": fg.RAYON_NUIT_M}}


@frappe.whitelist(methods=["POST"])
def fixer_domicile(employe, lat, lng):
    """« Définir comme domicile » : le point (de nuit, ou de dépose du soir pour un passager) devient le point de départ
    de l'employé dans le réglage des tournées (table « Points de départ particuliers ») — le même réglage que sert la
    fenêtre d'optimisation. Il requalifie les nuits affichées, sort l'endroit des « Arrêts inexpliqués » des sorties de
    circuit et fait dire « au domicile » au suivi live. L'optimisation des tournées, elle, ne part du domicile que si on
    le choisit dans sa fenêtre (dépôt par défaut)."""
    _garde()
    lat, lng = flt(lat), flt(lng)
    if not (lat and lng):
        frappe.throw("Point sans coordonnées")
    if not frappe.db.exists("Employee", employe):
        frappe.throw("Employé inconnu : %s" % employe)
    cfg = frappe.get_single(to.CONFIG)
    lignes = [r for r in (cfg.get("departs") or []) if r.employe != employe]
    lignes.append({"employe": employe, "lien": "https://www.google.com/maps?q=%.6f,%.6f" % (lat, lng), "latitude": lat, "longitude": lng})
    cfg.set("departs", lignes)
    cfg.flags.ignore_permissions = True
    cfg.save()
    return {"employe": employe, "latitude": lat, "longitude": lng}


@frappe.whitelist()
def domiciles_regles():
    """Les domiciles réglés (table « Points de départ particuliers » de Config Optimisation Tournees), pour le bloc
    « Domiciles » de Config Flotte GPS : la même source que l'optimisation des tournées, le suivi GPS n'en a pas d'autre."""
    if not frappe.has_permission(fg.CONFIG, "read"):
        frappe.throw("Accès réservé", frappe.PermissionError)
    departs = to.config()["departs"]
    noms = {e.name: e.employee_name for e in frappe.get_all("Employee", filters={"name": ["in", list(departs) or [""]]}, fields=["name", "employee_name"])}
    return {"rayon_nuit_m": fg.RAYON_NUIT_M,
            "domiciles": sorted(({"employe": e, "employe_nom": noms.get(e, e), "lat": flt(d[0]), "lng": flt(d[1])} for e, d in departs.items() if d),
                                key=lambda x: x["employe_nom"] or "")}


@frappe.whitelist(methods=["POST"])
def definir_equipages(texte=None, stats_depuis=None):
    """Depuis l'onglet : « Qui rentre avec qui » (une ligne par équipage) et « Statistiques depuis le » (Config Flotte GPS)."""
    _garde()
    if texte is not None:
        frappe.db.set_single_value(fg.CONFIG, "covoiturage", (texte or "").strip())
    if stats_depuis is not None:
        frappe.db.set_single_value(fg.CONFIG, "stats_date_debut", getdate(stats_depuis) if stats_depuis else None)
    return {"equipages": equipages(), "stats_depuis": str(fg.stats_depuis() or "")}


@frappe.whitelist()
def recalculer(du, au):
    """Bouton « Recalculer la période » : au plus 62 jours d'un coup (chaque jour lit la plateforme GPS)."""
    _garde()
    du, au = getdate(du), getdate(au)
    if (au - du).days > 62:
        frappe.throw("Au plus deux mois à la fois")
    return calculer_periode(du, au)
