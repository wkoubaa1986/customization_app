"""Liens Google Maps : coordonnées, Plus Codes et identifiants de lieux.

Les identifiants extraits sont vérifiés par Geocoding API, jamais remplacés par
une recherche du nom du lieu. Aucun contenu HTML Google Maps n'est collecté.
"""
from __future__ import annotations

import base64
import hashlib
import re
import struct
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

import frappe
import requests
from frappe.utils import now_datetime
from redis.exceptions import LockError

PREFIXE = "geocodage_liens:v1:"
TYPES_PRECIS = {"establishment", "point_of_interest", "premise", "subpremise", "street_address"}


def lien_google(url):
    try:
        p = urlsplit(url or "")
        host = (p.hostname or "").lower()
        return (p.scheme in ("https", "http") and not p.username and not p.password and p.port in (None, 80, 443)
                and (host in ("maps.app.goo.gl", "maps.google.com", "consent.google.com")
                     or (host == "goo.gl" and p.path.startswith("/maps"))
                     or (host in ("google.com", "www.google.com", "google.fr", "www.google.fr", "www.google.tn")
                         and p.path.startswith("/maps"))))
    except ValueError:
        return False


def identifiant_lieu(url):
    """Les paires hexadécimales sont un format observé, non garanti par Google.
    Leur conversion ne devient exploitable qu'après retour du MÊME ID par l'API.
    Les identifiants multiples (itinéraire à plusieurs étapes) sont refusés.
    """
    if not lien_google(url):
        return None
    p = urlsplit(url)
    if "/maps/dir" in p.path:
        return None
    params = parse_qs(p.query)
    ids = set(params.get("query_place_id", []) + params.get("place_id", []))
    for q in params.get("q", []) + params.get("query", []):
        if q.startswith("place_id:"):
            ids.add(q[9:])
    for brut in re.findall(r"!1s([^!/?&#]+)", unquote(p.path)):
        m = re.fullmatch(r"0x([0-9a-fA-F]{1,16}):0x([0-9a-fA-F]{1,16})", brut)
        if m:
            b = b"\x0a\x12\x09" + struct.pack("<Q", int(m[1], 16)) + b"\x11" + struct.pack("<Q", int(m[2], 16))
            ids.add(base64.urlsafe_b64encode(b).decode().rstrip("="))
        elif re.fullmatch(r"[A-Za-z0-9_-]{10,512}", brut):
            ids.add(brut)
    if len(ids) == 1:
        candidat = ids.pop()
        return candidat if re.fullmatch(r"[A-Za-z0-9_-]{10,512}", candidat) else None
    return None


def _cible_directe(url):
    from customization_app.tournee_optimisation import coordonnees_du_lien, coordonnees_plus_code
    if "/maps/dir" in urlsplit(url).path:
        return None
    pid = identifiant_lieu(url)
    params = parse_qs(urlsplit(url).query)
    contient_id = (params.get("query_place_id") or params.get("place_id") or "!1s" in unquote(urlsplit(url).path)
                   or any(q.startswith("place_id:") for q in params.get("q", []) + params.get("query", [])))
    if contient_id and not pid:
        return None
    # @lat,lng définit la caméra. Avec un identifiant de lieu, ne pas prendre
    # le centre de l'écran pour le point désigné. !3d/!4d reste le point explicite.
    sans_camera = re.sub(r"@[^/]+", "", url) if pid else url
    p = coordonnees_du_lien(sans_camera) or coordonnees_plus_code(url)
    if p:
        return {"point": list(p)}
    return {"place_id": pid} if pid else None


def cible_lien(url, reseau=True):
    if not lien_google(url) or "/maps/dir" in urlsplit(url).path:
        return None
    cible = _cible_directe(url)
    if cible:
        return cible
    cache = frappe.cache()
    k = PREFIXE + "cible:" + hashlib.sha256(url.encode()).hexdigest()
    deja = cache.get_value(k, expires=True)
    if deja is not None:
        return deja or None
    if not reseau:
        return None
    # Les échecs de l'ancien lecteur ne sont pas repris : « sans coordonnées »
    # ne signifie pas « lien mort ».
    courant = url
    try:
        for _ in range(5):
            if not lien_google(courant):
                break
            if urlsplit(courant).hostname == "consent.google.com":
                courant = parse_qs(urlsplit(courant).query).get("continue", [""])[0]
                if not lien_google(courant):
                    break
                cible = _cible_directe(courant)
                if cible:
                    break
            r = requests.head(courant, allow_redirects=False, timeout=6,
                              headers={"User-Agent": "AquaWorld-ERPNext-Maps-Link/1.0"})
            if r.status_code not in (301, 302, 303, 307, 308) or not r.headers.get("location"):
                break
            courant = urljoin(courant, r.headers["location"])
            if not lien_google(courant):
                break
            cible = _cible_directe(courant)
            if cible:
                break
    except requests.RequestException:
        cible = None
    cache.set_value(k, cible or {}, expires_in_sec=86400 if cible else 300)
    return cible


def _decoder_lieu(data, pid):
    from customization_app.geocodage_adresses import position
    if data.get("status") != "OK":
        return None
    resultats = data.get("results") or []
    if len(resultats) != 1:
        return None
    r = resultats[0]
    geometry = r.get("geometry") or {}
    loc = geometry.get("location") or {}
    p = position(loc.get("lat"), loc.get("lng"))
    if (r.get("place_id") != pid or r.get("partial_match") or not p
            or not TYPES_PRECIS.intersection(r.get("types") or [])
            or geometry.get("location_type") not in ("ROOFTOP", "GEOMETRIC_CENTER", "RANGE_INTERPOLATED")):
        return None
    c = {"lat": p[0], "lng": p[1], "service": "google", "origine": "lien Google Maps",
         "place_id": pid, "precision": geometry.get("location_type"), "partiel": False,
         "libelle": r.get("formatted_address") or "Lieu du lien Google Maps", "obtenu_le": str(now_datetime())}
    return {"statut": "Fiable", "point": list(p), "choix": c, "candidats": [],
            "motif": "Position du lieu désigné par le lien Google Maps, identifiant confirmé par Google.",
            "distance_services_m": None}


def _geocoder_lieu(pid, reseau=True):
    from customization_app import geocodage_adresses as a, geocodage_essais as g
    if not a.reglages()["actif"]:
        return None
    cle = g._cle("google")
    if not cle:
        return None
    cache = frappe.cache()
    k = PREFIXE + "lieu:" + hashlib.sha256((pid + "|" + cle).encode()).hexdigest()
    panne = a.PREFIXE + "panne:" + hashlib.sha256(frappe.as_json(["google", cle, None]).encode()).hexdigest()
    deja = cache.get_value(k, expires=True)
    if deja is not None:
        return deja or None
    if not reseau:
        return None
    try:
        with cache.lock(cache.make_key(a.PREFIXE + "reseau:google"), timeout=90, blocking_timeout=35):
            deja = cache.get_value(k, expires=True)
            if deja is not None:
                return deja or None
            if cache.get_value(panne, expires=True):
                return None
            g._consommer()
            r = requests.get("https://maps.googleapis.com/maps/api/geocode/json",
                             params={"key": cle, "place_id": pid, "language": "fr"}, timeout=12, allow_redirects=False)
            data = r.json() if r.status_code == 200 else {}
            if r.status_code != 200 or data.get("status") in ("REQUEST_DENIED", "OVER_DAILY_LIMIT", "OVER_QUERY_LIMIT", "UNKNOWN_ERROR"):
                cache.set_value(panne, {"message": "Google Geocoding indisponible ; vérifier la clé et le quota.", "candidats": []}, expires_in_sec=300)
            resultat = _decoder_lieu(data, pid)
            cache.set_value(k, resultat or {}, expires_in_sec=86400 if resultat else 300)
            return resultat
    except (g.ServiceIndisponible, requests.RequestException, ValueError, TypeError, AttributeError, LockError):
        # Ne jamais exposer une URL contenant la clé dans une erreur utilisateur.
        return None


def resoudre_lien(url, reseau=True):
    from customization_app.geocodage_adresses import _etat_position, position
    cible = cible_lien((url or "").strip(), reseau=reseau)
    if not cible:
        return None
    if cible.get("point"):
        p = position(*cible["point"])
        return _etat_position(p, "lien adresse") if p else None
    return _geocoder_lieu(cible["place_id"], reseau=reseau)
