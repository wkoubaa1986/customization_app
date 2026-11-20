"""Comparaison volontaire des géocodeurs, sans écrire sur Address ni sur les tâches.

Les clés restent côté serveur. Les résultats d'essai expirent après 24 h ; aucun
géocodeur n'est activé dans l'optimiseur par ces essais. Les coordonnées existantes
issues d'un lien servent de références à contrôler, pas de vérité terrain certifiée.
"""
from __future__ import annotations

import hashlib
import math
import statistics
import time

import frappe
import requests
from frappe.utils import cint, nowdate

from customization_app.tournee_optimisation import CONFIG, haversine_km, position_grossiere

PREFIXE = "geocodage_essais:"
TTL = 24 * 3600
MAX_ADRESSES = 50
MAX_APPELS_JOUR = 200
SERVICES = {
    "geoapify": {"nom": "Geoapify", "champ": "geoapify_cle", "url": "https://myprojects.geoapify.com/"},
    "locationiq": {"nom": "LocationIQ", "champ": "locationiq_cle", "url": "https://my.locationiq.com/"},
    "google": {"nom": "Google Geocoding", "champ": "google_geocoding_cle",
               "url": "https://console.cloud.google.com/apis/library/geocoding-backend.googleapis.com"},
    "nominatim": {"nom": "OpenStreetMap / Nominatim", "champ": None,
                  "url": "https://operations.osmfoundation.org/policies/nominatim/"},
}
SOURCES_REFERENCE = ("lien adresse", "lien adresse (plus code)", "lien tâche")


class ServiceIndisponible(Exception):
    """Message maîtrisé, jamais une URL de requête contenant la clé."""


def _autoriser():
    frappe.only_for(["System Manager", "Responsable magasin"])


def _cle(service):
    from frappe.utils.password import get_decrypted_password
    champ = SERVICES[service]["champ"]
    if not champ:
        return None
    valeur = get_decrypted_password(CONFIG, CONFIG, champ, raise_exception=False)
    if not valeur and service == "google":
        valeur = get_decrypted_password(CONFIG, CONFIG, "google_routes_cle", raise_exception=False)
    return valeur or None


def _compteur():
    cache = frappe.cache()
    return cache, cache.make_key(PREFIXE + "appels:" + nowdate())


def _consommer():
    cache, cle = _compteur()
    # INCR atomique ; un échec réseau consomme aussi une tentative. Ce plafond
    # concerne seulement les essais, pas les autres API du compte fournisseur.
    n = cache.incr(cle)
    cache.expire(cle, 2 * TTL)
    if n > MAX_APPELS_JOUR:
        raise ServiceIndisponible(f"Plafond des essais atteint ({MAX_APPELS_JOUR} appels par jour, tous services confondus).")


@frappe.whitelist()
def etat_services():
    _autoriser()
    cache, compteur = _compteur()
    return {
        "services": [dict(service=s, nom=v["nom"], disponible=not v["champ"] or bool(_cle(s)),
                          champ=v["champ"], url=v["url"]) for s, v in SERVICES.items()],
        "appels": min(cint(cache.get(compteur)), MAX_APPELS_JOUR),
        "plafond": MAX_APPELS_JOUR,
        "dernier": cache.get_value(PREFIXE + "dernier:" + frappe.session.user, expires=True),
    }


def texte_adresse(a):
    """Ne transmet ni le client, ni son téléphone, ni les commentaires internes."""
    morceaux = [a.get(k) for k in ("address_line1", "address_line2", "pincode", "city")]
    return ", ".join(dict.fromkeys(" ".join(str(x).split()) for x in morceaux if x and str(x).strip())) + ", Tunisie"


def _position(lat, lng):
    try:
        lat, lng = float(lat), float(lng)
    except (ValueError, TypeError):
        return None
    return (lat, lng) if math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180 else None


def _echantillon(limite):
    champs = ["name", "address_line1", "address_line2", "pincode", "city", "country", "custom_secteur",
              "custom_latitude", "custom_longitude", "custom_geocode_source"]
    base = {"address_line1": ["is", "set"], "country": "Tunisia"}
    # get_list respecte les permissions Address du demandeur. Aucune résolution
    # de lien ou mémorisation : ce sont uniquement des lectures.
    refs = frappe.get_list("Address", filters=dict(base, custom_geocode_source=["in", SOURCES_REFERENCE]),
                           fields=champs, order_by="modified desc, name asc", limit_page_length=limite * 4)
    autres = frappe.get_list("Address", filters=base, fields=champs,
                             order_by="modified desc, name asc", limit_page_length=limite * 8)
    selection, vus = [], set()

    def ajouter(liste, plafond, reference_seule=False):
        for a in liste:
            if len(selection) >= plafond:
                break
            texte = texte_adresse(a)
            if texte.casefold() in vus:
                continue
            p = _position(a.custom_latitude, a.custom_longitude)
            reference = (p if p and a.custom_geocode_source in SOURCES_REFERENCE
                         and p[0] and p[1] and not position_grossiere(p) else None)
            if reference_seule and not reference:
                continue
            vus.add(texte.casefold())
            selection.append({"adresse": a.name, "texte": texte, "secteur": a.custom_secteur,
                              "source_actuelle": a.custom_geocode_source, "reference": reference})

    ajouter(refs, max(1, limite * 2 // 3), reference_seule=True)
    sans_reference = [a for a in autres if a.custom_geocode_source not in SOURCES_REFERENCE]
    ajouter(sans_reference, limite)
    ajouter(autres, limite)
    return selection


@frappe.whitelist()
def echantillon(limite=12):
    _autoriser()
    return _echantillon(max(1, min(MAX_ADRESSES, cint(limite) or 12)))


def _candidat(lat, lng, libelle, precision, pays=None, confiance=None, partiel=False, adresse=None):
    p = _position(lat, lng)
    if not p or (pays and str(pays).lower() not in ("tn", "tunisia", "tunisie", "تونس")):
        return None
    return {"lat": p[0], "lng": p[1], "libelle": str(libelle or "")[:500], "precision": precision,
            "confiance": confiance, "partiel": bool(partiel), "adresse": adresse or {}}


def _adresse_google(composants):
    d = {t: c.get("long_name", "") for c in composants for t in c.get("types", [])}
    return {"numero": d.get("street_number", ""), "rue": d.get("route", ""),
            "villes": list(dict.fromkeys(d[t] for t in ("locality", "postal_town", "sublocality", "administrative_area_level_2", "administrative_area_level_1") if d.get(t))),
            "code_postal": d.get("postal_code", "")}


def _adresse_osm(d):
    return {"numero": d.get("house_number", ""), "rue": d.get("road") or d.get("pedestrian") or "",
            "villes": list(dict.fromkeys(d[t] for t in ("city", "town", "village", "municipality", "county", "state") if d.get(t))),
            "code_postal": d.get("postcode", "")}


def decoder(service, donnees):
    """Normalisation des réponses ; aucune position inventée si le résultat est vide."""
    out = []
    if service == "google":
        statut = donnees.get("status")
        if statut == "ZERO_RESULTS":
            return []
        if statut != "OK":
            message = str(donnees.get("error_message") or "").lower()
            if statut == "REQUEST_DENIED":
                raison = ("Google Geocoding API non activée dans le projet Google Cloud."
                          if "not activated" in message or "not enabled" in message else
                          "Accès Google refusé : vérifier l’activation de Geocoding API, la facturation et les restrictions de la clé.")
            else:
                raison = "Google Geocoding indisponible (quota ou réponse invalide)."
            raise ServiceIndisponible(raison)
        for r in donnees.get("results", [])[:3]:
            g = r.get("geometry") or {}
            p = g.get("location") or {}
            pays = next((c.get("short_name") for c in r.get("address_components", []) if "country" in c.get("types", [])), None)
            out.append(_candidat(p.get("lat"), p.get("lng"), r.get("formatted_address"),
                                  g.get("location_type", "inconnue"), pays, partiel=r.get("partial_match"),
                                  adresse=_adresse_google(r.get("address_components", []))))
    elif service == "geoapify":
        for r in donnees.get("results", [])[:3]:
            rank = r.get("rank") or {}
            out.append(_candidat(r.get("lat"), r.get("lon"), r.get("formatted"), r.get("result_type", "inconnue"),
                                  r.get("country_code"), rank.get("confidence")))
    else:
        if not isinstance(donnees, list):
            raise ServiceIndisponible("Réponse du service invalide.")
        for r in donnees[:3]:
            out.append(_candidat(r.get("lat"), r.get("lon"), r.get("display_name"),
                                  r.get("addresstype") or r.get("type") or "inconnue", (r.get("address") or {}).get("country_code"),
                                  adresse=_adresse_osm(r.get("address") or {})))
    return [r for r in out if r]


def _requete(service, texte, cle, nominatim_url=None):
    headers = {"User-Agent": "AquaWorld-ERPNext-Geocoding-Test/1.0 (manual comparison)"}
    if service == "google":
        url = "https://maps.googleapis.com/maps/api/geocode/json"
        params = {"key": cle, "address": texte, "components": "country:TN", "language": "fr"}
    elif service == "geoapify":
        url = "https://api.geoapify.com/v1/geocode/search"
        params = {"apiKey": cle, "text": texte, "filter": "countrycode:tn", "lang": "fr", "format": "json", "limit": 3}
    else:
        url = "https://eu1.locationiq.com/v1/search" if service == "locationiq" else (nominatim_url or "https://nominatim.openstreetmap.org/search")
        params = {"q": texte, "countrycodes": "tn", "format": "json", "addressdetails": 1, "limit": 3, "accept-language": "fr"}
        if service == "locationiq":
            params["key"] = cle
    try:
        r = requests.get(url, params=params, headers=headers, timeout=12, allow_redirects=False)
        if service == "locationiq" and r.status_code == 404:
            return []
        if r.status_code != 200:
            raise ServiceIndisponible("Service refusé ou indisponible (HTTP %s). Vérifier la clé et le quota." % r.status_code)
        return decoder(service, r.json())
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        # requests inclut souvent l'URL complète et sa clé dans l'exception.
        raise ServiceIndisponible("Service injoignable ou réponse invalide ; réessayer plus tard.") from None


def rechercher(service, texte):
    if service not in SERVICES:
        raise ServiceIndisponible("Service inconnu.")
    cle = _cle(service)
    if SERVICES[service]["champ"] and not cle:
        raise ServiceIndisponible("Clé non renseignée dans Config Optimisation Tournees.")
    cache = frappe.cache()
    # Changer de clé doit réellement tester le nouveau compte, sans réutiliser
    # le résultat du précédent. Seule une empreinte est dans la clé Redis.
    empreinte = hashlib.sha256((service + "|" + texte + "|" + (cle or "")).encode()).hexdigest()
    k = PREFIXE + "recherche:" + empreinte
    deja = cache.get_value(k, expires=True)
    if deja is not None:
        return {"candidats": deja, "cache": True}
    # Une seule requête à la fois pour ce banc de test, tous utilisateurs confondus.
    # Nominatim : pas d'autocomplétion, petit lot volontaire, jamais de tâche périodique.
    with cache.lock(cache.make_key(PREFIXE + "reseau"), timeout=30, blocking_timeout=30):
        deja = cache.get_value(k, expires=True)
        if deja is not None:
            return {"candidats": deja, "cache": True}
        dernier = cache.get_value(PREFIXE + "dernier_appel", expires=True) or 0
        time.sleep(max(0, 1.1 - (time.time() - dernier)))
        _consommer()
        try:
            candidats = _requete(service, texte, cle)
        finally:
            cache.set_value(PREFIXE + "dernier_appel", time.time(), expires_in_sec=60)
        cache.set_value(k, candidats, expires_in_sec=TTL)
    return {"candidats": candidats, "cache": False}


def bilan(lignes, services):
    resultats = []
    for s in services:
        cellules = [l["resultats"][s] for l in lignes if s in l.get("resultats", {})]
        ecarts = [c["ecart_m"] for c in cellules if c.get("ecart_m") is not None]
        resultats.append({"service": s, "nom": SERVICES[s]["nom"], "traites": len(cellules),
                          "trouves": sum(c["statut"] == "trouve" for c in cellules),
                          "erreurs": sum(c["statut"] == "indisponible" for c in cellules),
                          "references_comparees": len(ecarts),
                          "ecart_median_m": round(statistics.median(ecarts)) if ecarts else None,
                          "moins_200m": sum(e <= 200 for e in ecarts),
                          "plus_1000m": sum(e > 1000 for e in ecarts)})
    return resultats


def _sauver(identifiant, etat):
    frappe.cache().set_value(PREFIXE + "lot:" + identifiant, etat, expires_in_sec=TTL)


@frappe.whitelist(methods=["POST"])
def lancer(adresses, services):
    _autoriser()
    noms = frappe.parse_json(adresses) if isinstance(adresses, str) else adresses
    services = frappe.parse_json(services) if isinstance(services, str) else services
    if (not isinstance(noms, list) or not 1 <= len(noms) <= MAX_ADRESSES or any(not isinstance(n, str) or not n for n in noms)
            or not isinstance(services, list) or not services or any(not isinstance(s, str) or s not in SERVICES for s in services)):
        frappe.throw(f"Choisir 1 à {MAX_ADRESSES} adresses et au moins un service connu.")
    services = list(dict.fromkeys(services))
    lignes = []
    # Le lot correspond exactement à l'aperçu choisi, même si une nouvelle adresse
    # a été ajoutée entre l'aperçu et le lancement. Vérification individuelle des droits.
    for nom in dict.fromkeys(noms):
        a = frappe.get_doc("Address", nom)
        a.check_permission("read")
        if a.country != "Tunisia" or not a.address_line1:
            frappe.throw("Les essais portent sur des adresses renseignées en Tunisie.")
        p = _position(a.get("custom_latitude"), a.get("custom_longitude"))
        ref = p if p and p[0] and p[1] and a.get("custom_geocode_source") in SOURCES_REFERENCE and not position_grossiere(p) else None
        lignes.append({"adresse": a.name, "texte": texte_adresse(a), "secteur": a.get("custom_secteur"),
                       "source_actuelle": a.get("custom_geocode_source"), "reference": ref, "resultats": {}})
    identifiant = frappe.generate_hash(length=24)
    etat = {"id": identifiant, "utilisateur": frappe.session.user, "statut": "en_attente", "lignes": lignes,
            "services": services, "fait": 0, "total": len(lignes) * len(services), "bilan": []}
    _sauver(identifiant, etat)
    frappe.cache().set_value(PREFIXE + "dernier:" + frappe.session.user, identifiant, expires_in_sec=TTL)
    frappe.enqueue("customization_app.geocodage_essais.executer", identifiant=identifiant,
                   queue="long", timeout=1200, enqueue_after_commit=True)
    return {"id": identifiant, "adresses": len(lignes), "total": etat["total"]}


@frappe.whitelist()
def resultat(identifiant):
    _autoriser()
    etat = frappe.cache().get_value(PREFIXE + "lot:" + str(identifiant), expires=True)
    if not etat or etat["utilisateur"] != frappe.session.user:
        frappe.throw("Essai introuvable, expiré ou appartenant à un autre utilisateur.", frappe.PermissionError)
    return {k: v for k, v in etat.items() if k != "utilisateur"}


def executer(identifiant):
    etat = frappe.cache().get_value(PREFIXE + "lot:" + identifiant, expires=True)
    if not etat or etat["statut"] != "en_attente":
        return
    frappe.set_user(etat["utilisateur"])
    _autoriser()
    indisponibles = {}
    etat["statut"] = "en_cours"
    _sauver(identifiant, etat)
    try:
        for ligne in etat["lignes"]:
            # Les permissions peuvent avoir changé depuis le lancement.
            frappe.get_doc("Address", ligne["adresse"]).check_permission("read")
            for service in etat["services"]:
                try:
                    if service in indisponibles:
                        raise ServiceIndisponible(indisponibles[service])
                    r = rechercher(service, ligne["texte"])
                    candidats = r["candidats"]
                    c = {"statut": "trouve" if candidats else "introuvable", **r, "ecart_m": None}
                    if candidats and ligne["reference"]:
                        c["ecart_m"] = round(haversine_km(ligne["reference"], (candidats[0]["lat"], candidats[0]["lng"])) * 1000)
                except ServiceIndisponible as e:
                    indisponibles[service] = str(e)
                    c = {"statut": "indisponible", "message": str(e), "candidats": [], "ecart_m": None}
                ligne["resultats"][service] = c
                etat["fait"] += 1
                etat["bilan"] = bilan(etat["lignes"], etat["services"])
                _sauver(identifiant, etat)
        etat["statut"] = "termine"
    except Exception:
        # Ne pas journaliser une URL d'API, ni des adresses personnelles.
        etat["statut"] = "erreur"
        etat["message"] = "Essai interrompu. Vérifier les permissions et la disponibilité des services, puis relancer."
    _sauver(identifiant, etat)
