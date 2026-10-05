"""Heures de la tournée proposée recalculées avec Google, trafic compris (05/10/2026).

L'optimisation choisit l'ordre et les affectations avec OSRM (gratuit, sans trafic), CORRIGÉ par le rapport
Google / OSRM appris à chaque calcul (`coef_osrm` du réglage). Ensuite, sans bouton, chaque trajet de la tournée
choisie est mesuré par Google à SON heure de départ, avec le trafic habituel de ce jour et de cette heure (Routes API
« Compute Routes », TRAFFIC_AWARE_OPTIMAL), et les heures sont recalculées :
    départ du Magasin = début de la 1re tâche − trajet − stationnement ;
    tâche suivante   = fin de la précédente + rangement + trajet + stationnement ;
    retour           = fin de la dernière + rangement + trajet.
Une tâche fixe garde son heure (retard signalé si on ne peut pas y être) ; une tâche déplaçable prend l'heure
calculée, dans sa fenêtre et hors de la pause. Sans Google (clé absente, plafond du jour, jour passé, refus), le trajet
est celui d'OSRM corrigé — et l'écran le dit.

Coût : facturé par requête (palier Pro), 5 000 gratuites par mois. Plafond quotidien du réglage (150 par défaut →
au plus 4 650 par mois ; compteur du jour en base) et cache de 24 h par trajet (même origine, destination et heure
à 5 min près). La clé vit dans le réglage (champ Password, chiffré) et ne quitte jamais le serveur.
"""
from __future__ import annotations

import datetime

import frappe
import pytz
import requests
from frappe import _
from frappe.utils import cint, flt, get_datetime, getdate, now_datetime

from customization_app.tournee_optimisation import CONFIG, _hm

URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
CHAMPS = "routes.duration,routes.staticDuration,routes.distanceMeters"
PLAFOND_DEFAUT = 150
CACHE_SECONDES = 24 * 3600
PREFIXE = "tournee_google:"              # clés Redis du cache des trajets — les tests en prennent un autre


class LimiteAtteinte(Exception):
    pass


def cle() -> str | None:
    from frappe.utils.password import get_decrypted_password
    return get_decrypted_password(CONFIG, CONFIG, "google_routes_cle", raise_exception=False) or None


def plafond() -> int:
    # get_single_value rend 0 pour un Int jamais saisi : 0 = le défaut.
    return cint(frappe.db.get_single_value(CONFIG, "google_routes_max_jour")) or PLAFOND_DEFAUT


def heure_utc(jour, minutes: int, fuseau: str) -> str:
    """« 2026-10-06 » + 510 min, heure de Tunis → « 2026-10-06T08:30:00Z » (ce que Google attend). PURE."""
    local = pytz.timezone(fuseau).localize(datetime.datetime.combine(getdate(jour), datetime.time()) + datetime.timedelta(minutes=minutes))
    return local.astimezone(pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _secondes(texte) -> int:
    return int(float(str(texte or "0s").rstrip("s")))


def _compte_du_jour() -> int:
    """Appels Google faits aujourd'hui. En BASE, dans deux champs cachés du réglage — pas dans le cache Redis, que
    `bench clear-cache` (joué à chaque déploiement) vide : le plafond repartirait de zéro en pleine journée. Ni dans
    les valeurs globales (`set_global`), dont chaque écriture vide TOUT le cache du site."""
    jour = frappe.db.get_single_value(CONFIG, "google_compte_jour")
    return cint(frappe.db.get_single_value(CONFIG, "google_compte")) if jour and str(getdate(jour)) == frappe.utils.nowdate() else 0


def _compter() -> int:
    """Un appel de plus aujourd'hui → le total du jour ; LimiteAtteinte au-delà du plafond."""
    n = _compte_du_jour() + 1
    if n > plafond():
        raise LimiteAtteinte()
    frappe.db.set_single_value(CONFIG, {"google_compte_jour": frappe.utils.nowdate(), "google_compte": n}, update_modified=False)
    return n


def appels_du_jour() -> int:
    return _compte_du_jour()


def mesurer(o, d, depart_iso: str, cle_api: str) -> dict:
    """Un trajet mesuré par Google : {minutes (trafic), minutes_libre (sans trafic), km, cache}. Cache 24 h."""
    k = "%strajet:%.5f,%.5f:%.5f,%.5f:%s" % (PREFIXE, o[0], o[1], d[0], d[1], depart_iso)
    # expires=True : sans lui, frappe.cache garde la 1re valeur lue (même vide) pour toute la requête.
    deja = frappe.cache().get_value(k, expires=True)
    if deja:
        return dict(deja, cache=True)
    _compter()
    corps = {"origin": {"location": {"latLng": {"latitude": o[0], "longitude": o[1]}}},
             "destination": {"location": {"latLng": {"latitude": d[0], "longitude": d[1]}}},
             "travelMode": "DRIVE", "routingPreference": "TRAFFIC_AWARE_OPTIMAL", "departureTime": depart_iso}
    r = requests.post(URL, json=corps, timeout=12, headers={"X-Goog-Api-Key": cle_api, "X-Goog-FieldMask": CHAMPS})
    if r.status_code != 200:
        try:
            msg = r.json().get("error", {}).get("message") or r.text[:300]
        except ValueError:
            msg = r.text[:300]
        raise frappe.ValidationError(_("Google a refusé le calcul ({0}) : {1}").format(r.status_code, msg))
    route = (r.json().get("routes") or [{}])[0]
    res = {"minutes": round(_secondes(route.get("duration")) / 60), "minutes_libre": round(_secondes(route.get("staticDuration")) / 60),
           "km": round(cint(route.get("distanceMeters")) / 1000, 1)}
    frappe.cache().set_value(k, res, expires_in_sec=CACHE_SECONDES)
    return dict(res, cache=False)


# ── Recalage des heures d'une tournée ───────────────────────────────────────

PAS = 5                                  # les heures proposées tombent sur 5 min (comme l'optimisation)


def _au_pas(m: int) -> int:
    return -(-int(m) // PAS) * PAS


def recaler(depot: dict, arrets: list, mesure, marge: int, rangement: int, debut_jour: int, fin_jour: int,
            premiere: int = 0, pause: tuple | None = None) -> dict:
    """Les heures d'une tournée dans l'ordre donné, trajet par trajet. PURE (`mesure` injectée).

    `depot` / chaque arrêt : {"pos": (lat, lng), "noeud": n} ; un arrêt porte aussi `debut` (heure prévue, minutes),
    `service`, `mobile`, `fenetre` ((a, b) ou None) et `sans_lieu` (tâche au Magasin : ni stationnement ni rangement).
    `mesure(origine, destination, depart)` → {"minutes", "km", "source"} ; `depart` en minutes depuis minuit.
    → {"arrets": [{debut, fin, trajet, km, source, attente, retard, hors_fenetre}], "depart", "retour", "trajets",
       "km", "retards", "depasse"}."""
    out, pos, libre, depart_jour = [], depot, None, None
    for a in arrets:
        arrivee_marge = 0 if a.get("sans_lieu") else cint(marge)
        if libre is None:
            # 1er arrêt : on part du Magasin juste à temps (mesuré à l'heure de départ probable).
            m = mesure(pos, a, max(debut_jour, a["debut"] - arrivee_marge - 30))
            depart_jour = max(debut_jour, a["debut"] - arrivee_marge - m["minutes"]) if a["mobile"] else a["debut"] - arrivee_marge - m["minutes"]
            arrivee = depart_jour + m["minutes"] + arrivee_marge
        else:
            m = mesure(pos, a, libre)
            arrivee = libre + m["minutes"] + arrivee_marge
        if a["mobile"]:
            debut = _au_pas(max(arrivee, premiere, (a.get("fenetre") or (0, 0))[0]))
            if pause and debut < pause[1] and debut + a["service"] > pause[0]:
                debut = pause[1]                     # pas de tâche à cheval sur la pause
        else:
            debut = a["debut"]                       # fixe : son heure, quoi qu'il arrive
        fin = debut + a["service"]
        out.append({"debut": debut, "fin": fin, "trajet": m["minutes"], "km": m.get("km", 0), "source": m["source"],
                    "attente": max(0, debut - arrivee), "retard": max(0, arrivee - debut) if not a["mobile"] else 0,
                    "hors_fenetre": bool(a["mobile"] and a.get("fenetre") and debut > a["fenetre"][1])})
        libre = fin + (0 if a.get("sans_lieu") else cint(rangement))
        pos = a
    if libre is None:
        return {"arrets": [], "depart": None, "retour": None, "trajets": 0, "km": 0, "retards": 0, "depasse": False}
    m = mesure(pos, depot, libre)
    retour = libre + m["minutes"]
    trajets = sum(x["trajet"] for x in out) + m["minutes"]
    return {"arrets": out, "depart": depart_jour, "retour": retour, "retour_trajet": m["minutes"], "retour_source": m["source"],
            "trajets": trajets, "km": round(sum(x["km"] for x in out) + m.get("km", 0), 1),
            "retards": sum(1 for x in out if x["retard"] or x["hors_fenetre"]), "depasse": retour > fin_jour}


def recaler_proposition(employes: list, jour, cfg: dict, mn_brut: list, mn: list, km: list) -> dict:
    """Recale, en place, les heures proposées de chaque employé (voir le module). Google si possible, sinon OSRM
    corrigé (+ heures de pointe) trajet par trajet ; apprend au passage le rapport Google / OSRM. -> le bilan."""
    from customization_app.tournee_optimisation import facteur_pointe

    cle_api = cle()
    fuseau = frappe.utils.get_system_timezone()
    maintenant = now_datetime()
    etat = {"google": 0, "osrm": 0, "nouveaux": 0, "limite": False, "erreur": None, "libre": 0.0, "osrm_brut": 0.0}

    def mesure(o, d, depart):
        (la, lo), (lb, ld) = o["pos"], d["pos"]
        if abs(la - lb) < 1e-6 and abs(lo - ld) < 1e-6:
            return {"minutes": 0, "km": 0.0, "source": "sur place"}
        depart_dt = get_datetime("%s %s:00" % (getdate(jour), _hm(max(0, min(depart, 24 * 60 - 1)))))
        if cle_api and not frappe.flags.get("tournee_sans_reseau") and not etat["limite"] and not etat["erreur"] and depart_dt > maintenant:
            try:
                g = mesurer((la, lo), (lb, ld), heure_utc(jour, depart - depart % PAS, fuseau), cle_api)
            except LimiteAtteinte:
                etat["limite"] = True
            except Exception as e:                       # clé refusée, réseau… : la suite se fait sans Google
                etat["erreur"] = str(e)[:200]
            else:
                etat["google"] += 1
                etat["nouveaux"] += 0 if g["cache"] else 1
                if mn_brut[o["noeud"]][d["noeud"]]:
                    etat["libre"] += g["minutes_libre"]
                    etat["osrm_brut"] += mn_brut[o["noeud"]][d["noeud"]]
                return {"minutes": g["minutes"], "km": g["km"], "source": "google"}
        etat["osrm"] += 1
        return {"minutes": int(round(mn[o["noeud"]][d["noeud"]] * facteur_pointe(depart, cfg["pointes"]))),
                "km": round(km[o["noeud"]][d["noeud"]], 1), "source": "osrm"}

    base = getdate(jour).strftime("%Y-%m-%d")
    for e in employes:
        arrets = e["apres"]["arrets"]
        depot = {"pos": tuple(e["depart_point"]), "noeud": e["depot_noeud"]}
        etapes = [{"pos": (a["lat"], a["lng"]), "noeud": a["noeud"], "debut": a["debut_min"], "service": a["service"],
                   "mobile": a["mobile"], "fenetre": a.get("fenetre"), "sans_lieu": a["position"] == "dépôt"} for a in arrets]
        r = recaler(depot, etapes, mesure, cfg["marge"], cfg["rangement"], e["journee_min"][0], e["journee_min"][1],
                    cfg["premiere"], cfg["pause"])
        for a, x in zip(arrets, r["arrets"]):
            a.update({"debut": _hm(x["debut"]), "fin": _hm(x["fin"]), "starts_on": "%s %s:00" % (base, _hm(x["debut"])),
                      "ends_on": "%s %s:00" % (base, _hm(x["fin"])), "trajet": x["trajet"], "trajet_km": x["km"],
                      "trajet_source": x["source"], "attente": x["attente"], "retard": x["retard"], "hors_fenetre": x["hors_fenetre"],
                      "decale": a["mobile"] and x["debut"] != a["original_min"], "ecart_min": x["debut"] - a["original_min"]})
        if r["arrets"]:
            e["apres"]["fin"] = _hm(max(x["fin"] for x in r["arrets"]))
        e["horaires"] = {"depart": _hm(r["depart"]) if r["depart"] is not None else None,
                         "retour": _hm(r["retour"]) if r["retour"] is not None else None,
                         "trajets": r["trajets"], "km": r["km"], "retards": r["retards"], "depasse": r["depasse"],
                         "retour_trajet": r.get("retour_trajet"), "retour_source": r.get("retour_source"),
                         "fin_journee": _hm(e["journee_min"][1])}
    coef = apprendre_coef(etat["libre"], etat["osrm_brut"])
    raison = None
    if etat["osrm"] and not etat["google"]:
        raison = ("clé Google non réglée" if not cle_api else "sans réseau" if frappe.flags.get("tournee_sans_reseau")
                  else "plafond du jour atteint" if etat["limite"]
                  else "Google indisponible : %s" % etat["erreur"] if etat["erreur"] else "jour passé : Google n’estime que l’avenir")
    elif etat["osrm"] and (etat["limite"] or etat["erreur"]):
        raison = "plafond du jour atteint en cours de calcul" if etat["limite"] else "Google indisponible : %s" % etat["erreur"]
    return {"source": "google" if etat["google"] and not etat["osrm"] else "osrm" if not etat["google"] else "mixte",
            "trajets_google": etat["google"], "trajets_osrm": etat["osrm"], "raison": raison, "nouveaux_appels": etat["nouveaux"],
            "appels_du_jour": appels_du_jour(), "plafond": plafond(), "coef": coef}


def apprendre_coef(google_libre: float, osrm_brut: float) -> float:
    """Rapport « Google sans trafic ÷ OSRM » des trajets mesurés, lissé (70 % l'ancien, 30 % la mesure) et borné
    à [1 ; 2]. N'apprend que sur au moins 30 min de trajets OSRM. -> le coefficient en vigueur."""
    actuel = min(2.0, max(1.0, flt(frappe.db.get_single_value(CONFIG, "coef_osrm")) or 1.0))
    if osrm_brut < 30 or not google_libre:
        return actuel
    nouveau = round(min(2.0, max(1.0, 0.7 * actuel + 0.3 * (google_libre / osrm_brut))), 2)
    if nouveau != actuel:
        frappe.db.set_single_value(CONFIG, "coef_osrm", nouveau, update_modified=False)
    return nouveau
