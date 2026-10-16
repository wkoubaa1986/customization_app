"""Présence à Aquaworld et domiciles d'après les NUITS du véhicule — onglet « Présence Aquaworld » du suivi terrain,
réservé au rôle « Sortie Circuit » (Wassim, Néjib), à côté de l'onglet « Sorties de circuit ».

Demande du 16/10/2026 : « détecte les arrêts la nuit ; estime l'heure d'arrivée à Aquaworld et l'heure de départ ;
affecte la position de la maison de chacun des employés ».

Lecture d'une journée, uniquement d'après le journal GPS (arrêts / trajets du véhicule) :
- la NUIT D'AVANT = l'arrêt qui chevauche minuit au début du jour (commencé la veille, fini le matin) ; sa fin est
  l'heure de départ du matin, son point dit où le véhicule a dormi ;
- la NUIT D'APRÈS = l'arrêt qui atteint la fin de la journée (23 h 59) ; son début est l'heure de retour du soir ;
- entre les deux, les passages au MAGASIN (≤ 200 m du dépôt réglé) : le premier donne l'arrivée à Aquaworld, le dernier
  le départ d'Aquaworld, leur somme le temps passé sur place dans la journée.
Le lieu de chaque nuit (Domicile / Magasin / Autre) n'est PAS figé : il se lit à l'affichage contre le réglage des
tournées, pour qu'un domicile réglé après coup requalifie aussi l'historique.

⚠️ Un véhicule qui dort au Magasin (Hedi Chouchane, 3554) ne dit rien de l'arrivée du technicien : il vient autrement.
On montre alors « départ du Magasin » le matin et « retour au Magasin » le soir, sans inventer d'arrivée.

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
LIEU_M = fc.LIEU_M                              # deux nuits à moins de 200 m = le même endroit
NUITS_MIN_DOMICILE = 3                          # en dessous, on ne propose pas un domicile

CHAMPS = ("depart_matin", "arrivee_aquaworld", "depart_aquaworld", "retour_soir", "minutes_aquaworld",
          "nuit_avant_lat", "nuit_avant_lng", "nuit_lat", "nuit_lng")
VIDE = {c: (0 if c in ("minutes_aquaworld", "nuit_avant_lat", "nuit_avant_lng", "nuit_lat", "nuit_lng") else None) for c in CHAMPS}


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


def presence_pure(jour, journal: dict, depot: tuple, rayon_m: float = RAYON_LIEU_M) -> dict:
    """Une journée d'un véhicule → heures de départ / arrivée / retour et points de nuit. PURE.
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


def domiciles_pure(nuits: list, depot: tuple, departs: dict, rayon_m: float = RAYON_LIEU_M, minimum: int = NUITS_MIN_DOMICILE) -> list:
    """Où dort le véhicule de chacun : regroupement des points de nuit par distance (≤ LIEU_M), par employé.
    `nuits` : [{employe, employe_nom, lat, lng, date}]. Rend, par employé, ses endroits (du plus fréquent au moins),
    le domicile réglé, et un verdict : `ok` (le réglage est l'endroit principal), `a_regler` (pas de réglage, un
    endroit principal ≥ minimum nuits), `different` (réglé ailleurs que l'endroit principal), `magasin` (le véhicule
    dort au Magasin : domicile indétectable), `chez_autre` (il dort au domicile réglé d'un collègue : véhicule rendu),
    `insuffisant`. PURE."""
    par_emp = {}
    noms = {n["employe"]: n.get("employe_nom") or n["employe"] for n in nuits if n.get("employe")}
    for n in nuits:
        if not n.get("lat") or not n.get("employe"):
            continue
        e = par_emp.setdefault(n["employe"], {"employe": n["employe"], "employe_nom": n.get("employe_nom") or n["employe"], "lieux": [], "nuits": 0})
        pt = (flt(n["lat"]), flt(n["lng"]))
        c = next((c for c in e["lieux"] if _m(pt, (c["lat"], c["lng"])) <= LIEU_M), None)
        if c is None:
            # Un endroit déjà connu — le Magasin, ou le domicile RÉGLÉ d'un collègue (Jamel qui rend la voiture de Sadok
            # chez Sadok) — n'est jamais proposé comme domicile de cet employé.
            chez = next((a for a, p in departs.items() if a != n["employe"] and _m(pt, p) <= rayon_m), None)
            c = {"lat": pt[0], "lng": pt[1], "n": 0, "derniere": None, "magasin": bool(depot) and _m(pt, depot) <= rayon_m,
                 "chez": chez, "chez_nom": noms.get(chez, chez) if chez else None}
            c["exclu"] = c["magasin"] or bool(chez)
            e["lieux"].append(c)
        c["n"] += 1
        e["nuits"] += 1
        d = str(n.get("date") or "")
        if d > str(c["derniere"] or ""):
            c["derniere"] = d
    out = []
    for e in par_emp.values():
        e["lieux"].sort(key=lambda c: -c["n"])
        regle = departs.get(e["employe"])
        e["regle"] = list(regle) if regle else None
        for c in e["lieux"]:
            c["part"] = round(100 * c["n"] / e["nuits"]) if e["nuits"] else 0
            c["regle"] = bool(regle) and _m((c["lat"], c["lng"]), regle) <= rayon_m
        principal = next((c for c in e["lieux"] if not c["exclu"]), None)
        e["principal"] = principal
        if e["lieux"][0]["exclu"] and (principal is None or principal["n"] < minimum):
            e["verdict"] = "magasin" if e["lieux"][0]["magasin"] else "chez_autre"
        elif principal is None or principal["n"] < minimum:
            e["verdict"] = "insuffisant"
        elif regle and principal["regle"]:
            e["verdict"] = "ok"
        elif regle:
            e["verdict"] = "different"
            e["ecart_m"] = round(_m((principal["lat"], principal["lng"]), regle))
        else:
            e["verdict"] = "a_regler"
        out.append(e)
    return sorted(out, key=lambda e: e["employe_nom"] or "")


def _minutes(dt) -> int | None:
    if not dt:
        return None
    dt = dt if isinstance(dt, datetime) else datetime.strptime(str(dt)[:19], "%Y-%m-%d %H:%M:%S")
    return dt.hour * 60 + dt.minute


def _hhmm(minutes) -> str:
    return "%02d:%02d" % (minutes // 60, minutes % 60) if minutes is not None else ""


def _mediane(valeurs: list):
    v = sorted(x for x in valeurs if x is not None)
    if not v:
        return None
    return v[len(v) // 2] if len(v) % 2 else (v[len(v) // 2 - 1] + v[len(v) // 2]) // 2


def statistiques_pure(lignes: list) -> list:
    """Par employé : jours suivis, jours passés au Magasin, heures médianes (départ matin, arrivée / départ Aquaworld,
    retour soir), temps médian sur place, nuits par lieu. `lignes` = les journées déjà qualifiées (nuit_avant_lieu…). PURE."""
    par = {}
    for l in lignes:
        cle = l.get("employe") or l.get("vehicule") or "?"
        e = par.setdefault(cle, {"employe": l.get("employe"), "employe_nom": l.get("employe_nom") or l.get("vehicule") or "?",
                                 "jours": 0, "jours_magasin": 0, "immobiles": 0, "depart_matin": [], "arrivee": [], "depart": [],
                                 "retour": [], "minutes": [], "nuits": {"Domicile": 0, "Magasin": 0, "Autre": 0}})
        e["jours"] += 1
        if l.get("immobile"):
            e["immobiles"] += 1
        if l.get("arrivee_aquaworld") or l.get("depart_aquaworld"):
            e["jours_magasin"] += 1
        e["depart_matin"].append(_minutes(l.get("depart_matin")))
        e["arrivee"].append(_minutes(l.get("arrivee_aquaworld")))
        e["depart"].append(_minutes(l.get("depart_aquaworld")))
        e["retour"].append(_minutes(l.get("retour_soir")))
        if l.get("minutes_aquaworld"):
            e["minutes"].append(cint(l["minutes_aquaworld"]))
        for k in ("nuit_avant_lieu", "nuit_lieu"):
            if l.get(k) in e["nuits"]:
                e["nuits"][l[k]] += 1
    out = []
    for e in par.values():
        out.append({"employe": e["employe"], "employe_nom": e["employe_nom"], "jours": e["jours"], "jours_magasin": e["jours_magasin"],
                    "immobiles": e["immobiles"], "depart_matin": _hhmm(_mediane(e["depart_matin"])), "arrivee": _hhmm(_mediane(e["arrivee"])),
                    "depart": _hhmm(_mediane(e["depart"])), "retour": _hhmm(_mediane(e["retour"])),
                    "minutes_aquaworld": _mediane(e["minutes"]) or 0, "nuits": e["nuits"]})
    return sorted(out, key=lambda e: e["employe_nom"] or "")


# ── Calcul et écriture ───────────────────────────────────────────────────────

def calculer(jour=None, ecrire: bool = True) -> dict:
    """Les présences d'un jour pour tous les véhicules, écrites sur Journee Flotte GPS (ligne créée si absente)."""
    jour = getdate(jour or nowdate())
    cfg = fg.config()
    cfg_t = to.config()
    cboxes = cfg["cboxes"]
    journaux = fg.lire_journal(jour, cboxes, cfg=cfg) if cboxes else {}
    taches = fg._taches(jour)
    emp_cbox = fg.vehicules_du_jour(taches, journaux, cfg["employe_cbox"], cfg["rayon"], fg._lieux_communs(cfg_t))
    for emp, cb in cfg["employe_cbox"].items():                  # véhicule sans employé reconnu : son conducteur habituel
        if cb not in emp_cbox.values() and emp not in emp_cbox:
            emp_cbox[emp] = cb
    cbox_emp = {cb: e for e, cb in emp_cbox.items()}
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
    return l


@frappe.whitelist()
def presence(du=None, au=None, employe=None):
    """Les journées d'une période (heures, nuits qualifiées), statistiques par employé et domiciles détectés."""
    _garde()
    au = getdate(au or nowdate())
    du = getdate(du or add_days(au, -29))
    cfg_t = to.config()
    depot, departs = cfg_t["depot"], cfg_t["departs"]
    filtres = {"date": ["between", [du, au]]}
    if employe:
        filtres["employe"] = employe
    lignes = frappe.get_all(fg.JOURNEE, filters=filtres, order_by="date desc, employe_nom asc, vehicule asc", limit_page_length=0,
                            fields=["name", "date", "vehicule", "cbox", "employe", "employe_nom", "km", *CHAMPS])
    lignes = [_qualifier(dict(l), depot, departs) for l in lignes]
    suivies = [l for l in lignes if l.get("nuit_avant_lat") or l.get("nuit_lat") or l.get("arrivee_aquaworld")]
    nuits = []
    for l in suivies:
        for k in ("nuit_avant", "nuit"):
            if l.get(k + "_lat"):
                nuits.append({"employe": l["employe"], "employe_nom": l["employe_nom"], "lat": l[k + "_lat"], "lng": l[k + "_lng"], "date": l["date"]})
    employes = sorted({(l["employe"], l["employe_nom"]) for l in lignes if l["employe"]}, key=lambda x: x[1] or "")
    return {"du": str(du), "au": str(au), "lignes": lignes, "non_calculees": len(lignes) - len(suivies),
            "stats": statistiques_pure(suivies), "domiciles": domiciles_pure(nuits, depot, departs),
            "employes": [{"employe": e, "nom": n} for e, n in employes], "depot": list(depot),
            "seuils": {"rayon_m": RAYON_LIEU_M, "lieu_m": LIEU_M, "nuits_min": NUITS_MIN_DOMICILE}}


@frappe.whitelist(methods=["POST"])
def fixer_domicile(employe, lat, lng):
    """« Définir comme domicile » : le point de nuit devient le point de départ de l'employé dans le réglage des tournées
    (table « Points de départ particuliers ») — le même réglage que sert la fenêtre d'optimisation. Il requalifie les nuits
    affichées, sort l'endroit des « Arrêts inexpliqués » des sorties de circuit et fait dire « au domicile » au suivi live.
    L'optimisation des tournées, elle, ne part du domicile que si on le choisit dans sa fenêtre (dépôt par défaut)."""
    _garde()
    lat, lng = flt(lat), flt(lng)
    if not (lat and lng):
        frappe.throw("Point de nuit sans coordonnées")
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
def recalculer(du, au):
    """Bouton « Recalculer la période » : au plus 62 jours d'un coup (chaque jour lit la plateforme GPS)."""
    _garde()
    du, au = getdate(du), getdate(au)
    if (au - du).days > 62:
        frappe.throw("Au plus deux mois à la fois")
    return calculer_periode(du, au)
