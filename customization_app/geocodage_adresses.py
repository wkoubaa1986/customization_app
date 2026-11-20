"""Localisation opérationnelle : Google et Nominatim privé, sans moyenne de points.

HTTP seul dans les threads ; permissions, Redis et MariaDB restent sur le thread
Frappe. Les tournées peuvent utiliser une estimation, distincte d'un GPS confirmé.
"""
from __future__ import annotations

import hashlib
import math
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from urllib.parse import urlsplit

import frappe
from redis.exceptions import LockError
from frappe.utils import add_days, cint, get_datetime, now_datetime

CONFIG = "Config Optimisation Tournees"
PREFIXE = "geocodage_adresses:v1:"
TTL = 24 * 3600
CHAMPS_ADRESSE = ("address_line1", "address_line2", "pincode", "city", "country", "custom_lien_google_map")
CHAMPS_ETAT = ("custom_geocode_statut", "custom_geocode_motif", "custom_geocode_signature",
               "custom_geocode_expire_le", "custom_geocode_valide_par")
FIABLES = ("Fiable", "Validée")


def source_google(source):
    return (source or "").startswith(("texte google", "lien google lieu"))


def signature(a):
    contenu = [str(a.get(k) or "").strip() for k in CHAMPS_ADRESSE]
    return hashlib.sha256(frappe.as_json(contenu).encode()).hexdigest()


def position(lat, lng):
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    return (lat, lng) if math.isfinite(lat) and math.isfinite(lng) and 30 <= lat <= 38 and 7 <= lng <= 12 else None


def normaliser(s):
    s = "".join(c for c in unicodedata.normalize("NFKD", str(s or "").casefold()) if not unicodedata.combining(c))
    return " ".join(re.findall(r"[^\W_]+", s))


def mots_rue(s):
    inutiles = {"rue", "avenue", "av", "route", "rte", "boulevard", "bd", "de", "du", "des", "la", "le", "les", "l", "d"}
    return {t for t in normaliser(s).split() if t not in inutiles and not t.isdigit()}


def qualite(c, a):
    """Qualité catégorielle prudente, jamais une probabilité de précision."""
    if not position(c.get("lat"), c.get("lng")):
        return 0, "Position hors de la zone attendue."
    detail = c.get("adresse") or {}
    if c.get("precision") == "APPROXIMATE" or (c.get("partiel") and not detail.get("rue") and not detail.get("numero")):
        return 1, "Ville ou quartier trouvé, mais pas le lieu précis de l’intervention."
    ville = normaliser(a.get("city"))
    villes = [normaliser(x) for x in detail.get("villes", [])]
    if not ville or ville not in villes:
        return 1, "Ville non confirmée par le résultat."
    if a.get("pincode") and detail.get("code_postal") and normaliser(a.get("pincode")) != normaliser(detail["code_postal"]):
        return 1, "Le code postal ne correspond pas."
    numero = re.match(r"\s*(\d+[A-Za-z]?)\b", str(a.get("address_line1") or ""))
    if not numero:
        return 1, "Adresse sans numéro : bâtiment à confirmer."
    if numero and normaliser(numero[1]) != normaliser(detail.get("numero")):
        return 1, "Numéro de rue non confirmé."
    rue = mots_rue(detail.get("rue"))
    saisie = mots_rue(a.get("address_line1"))
    if not rue or not saisie or len(rue & saisie) / len(rue | saisie) < 0.65:
        return 1, "Rue non confirmée complètement."
    if c.get("partiel"):
        return 1, "Correspondance partielle de l’adresse."
    if c.get("precision") not in ("ROOFTOP", "house", "building"):
        return 1, "Résultat à l’échelle d’une rue, d’un quartier ou d’une ville."
    if not detail.get("numero"):
        return 1, "Bâtiment sans numéro confirmé."
    return 3, "Bâtiment, rue et ville concordants."


def choisir(reponses, adresse):
    """Sélection d'un point existant ; les coordonnées ne sont jamais moyennées."""
    from customization_app.tournee_optimisation import haversine_km
    candidats, messages = [], []
    for service, r in reponses.items():
        if r.get("message"):
            messages.append(r["message"])
        for i, original in enumerate(r.get("candidats", [])):
            c = dict(original, service=service, rang=i)
            niveau, motif = qualite(c, adresse)
            c.update(niveau=niveau, motif=motif)
            if niveau:
                candidats.append(c)
    if not candidats:
        return {"statut": "Indisponible" if messages else "Introuvable", "point": None, "choix": None,
                "motif": "Aucune position exploitable." + (" " + " ".join(dict.fromkeys(messages)) if messages else ""),
                "candidats": [], "distance_services_m": None}
    candidats.sort(key=lambda c: (-c["niveau"], c["rang"], c["service"] != "google"))
    meilleur = candidats[0]
    distance = None
    autres = [c for c in candidats if c["service"] != meilleur["service"]]
    if autres:
        distance = round(haversine_km((meilleur["lat"], meilleur["lng"]), (autres[0]["lat"], autres[0]["lng"])) * 1000)
    ambigus = any(c["service"] == meilleur["service"] and c["niveau"] == 3
                 and haversine_km((meilleur["lat"], meilleur["lng"]), (c["lat"], c["lng"])) > 0.2 for c in candidats[1:])
    fiable = meilleur["niveau"] == 3 and not ambigus and (distance is None or distance <= 200)
    motif = ("Les services proposent des positions éloignées : confirmation nécessaire." if distance is not None and distance > 200
             else "Plusieurs bâtiments possibles : confirmation nécessaire." if ambigus else meilleur["motif"])
    return {"statut": "Fiable" if fiable else "À vérifier", "point": [meilleur["lat"], meilleur["lng"]] if fiable else None,
            "choix": meilleur, "motif": motif, "candidats": candidats, "distance_services_m": distance}


def url_nominatim(valeur):
    """Endpoint explicitement configuré par un administrateur ; aucun endpoint public implicite."""
    valeur = (valeur or "").strip().rstrip("/")
    if not valeur:
        return None
    p = urlsplit(valeur)
    if (p.scheme not in ("http", "https") or not p.hostname or p.username or p.password or p.query or p.fragment
            or p.hostname.lower() == "nominatim.openstreetmap.org" or p.hostname.lower().endswith(".nominatim.openstreetmap.org")):
        raise ValueError("Renseignez l’URL HTTP(S) de votre instance Nominatim privée, sans identifiants ni paramètres.")
    if p.hostname in ("169.254.169.254", "metadata.google.internal"):
        raise ValueError("Cette adresse ne correspond pas à un serveur Nominatim.")
    return valeur if p.path.rstrip("/").endswith("/search") else valeur + "/search"


def reglages():
    d = frappe.get_single(CONFIG)
    return {"actif": bool(cint(d.get("geocodage_texte_actif"))), "nominatim": url_nominatim(d.get("nominatim_prive_url"))}


def rechercher(adresse, reseau=True):
    """Google et Nominatim privé peuvent répondre simultanément. Aucun contexte Frappe dans les threads."""
    from customization_app import geocodage_essais as g
    cfg = reglages()
    if not cfg["actif"]:
        return choisir({"google": {"message": "Recherche externe désactivée dans les réglages d’optimisation."}}, adresse)
    if adresse.get("country") != "Tunisia":
        return choisir({"google": {"message": "La recherche est limitée aux adresses en Tunisie."}}, adresse)
    texte = g.texte_adresse(adresse)
    services = [("google", g._cle("google"), None)]
    if cfg["nominatim"]:
        services.append(("nominatim", None, cfg["nominatim"]))
    cache, reponses, attentes = frappe.cache(), {}, []
    # Ordre des verrous identique entre requêtes. Google n'attend pas le retour
    # HTTP Nominatim ; chaque fournisseur reste sérialisé pour toute l'application.
    with ExitStack() as locks:
        for service, cle, endpoint in services:
            if service == "google" and not cle:
                reponses[service] = {"message": "Clé Google Geocoding absente."}
                continue
            h = hashlib.sha256(frappe.as_json([service, texte, cle, endpoint]).encode()).hexdigest()
            k = PREFIXE + "reponse:" + h
            panne = PREFIXE + "panne:" + hashlib.sha256(frappe.as_json([service, cle, endpoint]).encode()).hexdigest()
            resultat = cache.get_value(k, expires=True)
            if resultat is not None:
                reponses[service] = resultat
                continue
            if not reseau:
                continue
            refus = cache.get_value(panne, expires=True)
            if refus:
                reponses[service] = refus
                continue
            try:
                locks.enter_context(cache.lock(cache.make_key(PREFIXE + "reseau:" + service), timeout=90, blocking_timeout=35))
            except LockError:
                reponses[service] = {"message": "Recherche déjà en cours. Réessayez dans quelques instants."}
                continue
            resultat = cache.get_value(k, expires=True)
            if resultat is not None:
                reponses[service] = resultat
                continue
            refus = cache.get_value(panne, expires=True)
            if refus:
                reponses[service] = refus
                continue
            if service == "nominatim":
                dernier = cache.get_value(PREFIXE + "dernier_nominatim", expires=True) or 0
                time.sleep(max(0, 1.1 - (time.time() - dernier)))
            try:
                g._consommer()       # plafond atomique partagé avec le comparateur
            except g.ServiceIndisponible as e:
                reponses[service] = {"message": str(e)}
                continue
            attentes.append((service, cle, endpoint, k, panne))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [(s, k, panne, pool.submit(g._requete, s, texte, cle, endpoint)) for s, cle, endpoint, k, panne in attentes]
            for s, k, panne, futur in futures:
                try:
                    obtenu_le = str(now_datetime())
                    resultat = {"candidats": [dict(c, obtenu_le=obtenu_le) for c in futur.result()]}
                except g.ServiceIndisponible as e:
                    resultat = {"message": str(e), "candidats": []}
                except Exception:
                    resultat = {"message": "Service de localisation indisponible.", "candidats": []}
                if s == "nominatim":
                    cache.set_value(PREFIXE + "dernier_nominatim", time.time(), expires_in_sec=60)
                if resultat.get("message"):
                    cache.set_value(panne, resultat, expires_in_sec=300)
                cache.set_value(k, resultat, expires_in_sec=300 if resultat.get("message") else TTL)
                reponses[s] = resultat
    return choisir(reponses, adresse)


def position_memorisee(a):
    from customization_app.tournee_optimisation import position_grossiere
    p = position(a.get("custom_latitude"), a.get("custom_longitude"))
    if not p or position_grossiere(p):
        return None
    statut = a.get("custom_geocode_statut") or ""
    empreinte = a.get("custom_geocode_signature")
    if empreinte and empreinte != signature(a):
        return None
    expiration = a.get("custom_geocode_expire_le")
    if expiration and get_datetime(expiration) <= now_datetime():
        return None
    source = a.get("custom_geocode_source") or ""
    if statut in FIABLES and empreinte:
        return p
    if not statut and source in ("lien adresse", "lien adresse (plus code)", "lien tâche", "position validée"):
        return p
    return None


def _etat_position(p, source="position enregistrée"):
    return {"statut": "Validée", "point": list(p), "choix": None, "motif": source,
            "candidats": [], "distance_services_m": None}


def _verrouiller(nom):
    frappe.db.sql("select name from tabAddress where name=%s for update", (nom,))
    return frappe.get_doc("Address", nom)


def _memoriser(a, resultat, confirme=False):
    if not frappe.has_permission("Address", "write", doc=a):
        return False
    courant = _verrouiller(a.name)
    # Un changement d'adresse ou une validation GPS pendant l'appel réseau gagne.
    if signature(courant) != signature(a) or str(courant.modified) != str(a.modified):
        return False
    if position_memorisee(courant):
        return False
    valeurs = {"custom_geocode_statut": "Validée" if confirme else resultat["statut"],
               "custom_geocode_motif": resultat["motif"], "custom_geocode_signature": signature(a)}
    if resultat.get("point"):
        c = resultat.get("choix") or {}
        service = c.get("service", "lien adresse")
        origine = ("lien google lieu" + (" confirmé" if confirme else "")) if c.get("origine") == "lien Google Maps" else (
            ("texte " + service + (" confirmé" if confirme else " estimé" if resultat.get("estimee") else " fiable")) if c else service)
        valeurs.update(custom_latitude=resultat["point"][0], custom_longitude=resultat["point"][1],
                       custom_geocode_source=origine,
                       custom_geocode_expire_le=add_days(get_datetime(c.get("obtenu_le")) if c.get("obtenu_le") else now_datetime(), 30) if service == "google" else None,
                       custom_geocode_valide_par=frappe.session.user if confirme else None)
    frappe.db.set_value("Address", a.name, valeurs, update_modified=False)
    return True


def resoudre_adresse(nom, texte=True, reseau=True):
    from customization_app.tournee_optimisation import position_grossiere
    from customization_app.geocodage_liens import resoudre_lien
    if not nom:
        return choisir({}, {})
    a = frappe.get_doc("Address", nom)
    p = position_memorisee(a)
    if p:
        return _etat_position(p, a.get("custom_geocode_source"))
    lien = a.get("custom_lien_google_map")
    if lien and a.get("custom_geocode_motif") != "Adresse modifiée : position à vérifier.":
        resultat = resoudre_lien(lien, reseau=reseau)
        p = resultat.get("point") if resultat else None
        if p and not position_grossiere(p):
            if reseau:
                _memoriser(a, resultat)
            return resultat
    if not texte or not reseau or a.country != "Tunisia":
        return choisir({}, a)
    if (a.get("custom_geocode_statut") in ("À vérifier", "Introuvable")
            and a.get("custom_geocode_signature") == signature(a)
            and not a.get("custom_geocode_expire_le")):
        # Une adresse déjà reconnue ambiguë attend une correction/confirmation.
        # Le bouton de diagnostic permet toujours une nouvelle recherche volontaire.
        # Reprendre le diagnostic des réponses en cache après une évolution du
        # contrôle, sans renvoyer l'adresse au fournisseur ni valider un point.
        cache = rechercher(a, reseau=False)
        if cache.get("statut") == "À vérifier":
            if cache.get("motif") != a.get("custom_geocode_motif"):
                _memoriser(a, cache)
            return cache
        return {"statut": a.custom_geocode_statut, "point": None, "choix": None,
                "motif": a.get("custom_geocode_motif") or "Position à confirmer.",
                "candidats": [], "distance_services_m": None}
    # L'empreinte invalide les erreurs persistantes et les anciens géocodages
    # dès que le texte change. Les coordonnées historiques approximatives restent
    # visibles sur la fiche mais ne sont plus traitées comme un point fiable.
    resultat = rechercher(a)
    if reglages()["actif"]:
        _memoriser(a, resultat)
    return resultat


def estimation_memorisee(a):
    """Une estimation reste à vérifier sur la fiche, mais sert au calcul automatique."""
    if (a.get("custom_geocode_source") not in ("texte google estimé", "texte nominatim estimé")
            or a.get("custom_geocode_statut") != "À vérifier"
            or a.get("custom_geocode_signature") != signature(a)):
        return None
    expiration = a.get("custom_geocode_expire_le")
    if source_google(a.get("custom_geocode_source")) and not expiration:
        return None
    if expiration and get_datetime(expiration) <= now_datetime():
        return None
    return position(a.get("custom_latitude"), a.get("custom_longitude"))


def meilleur_point_estime(candidats, a, secteur=None, centres=None):
    """Choisit un résultat existant et plausible, jamais une moyenne de coordonnées."""
    from customization_app.tournee_optimisation import plausible
    admissibles = []
    for c in candidats:
        p = position(c.get("lat"), c.get("lng"))
        if not p or not plausible(p, secteur or a.get("custom_secteur"), centres):
            continue
        if c.get("service") == "google" and (not c.get("obtenu_le")
                or add_days(get_datetime(c["obtenu_le"]), 30) <= now_datetime()):
            continue
        admissibles.append(c)

    def score(c):
        detail = c.get("adresse") or {}
        ville = normaliser(a.get("city"))
        concorde = bool(ville and ville in [normaliser(v) for v in detail.get("villes", [])])
        rue, saisie = mots_rue(detail.get("rue")), mots_rue(a.get("address_line1"))
        rue_commune = len(rue & saisie) / len(rue | saisie) if rue and saisie else 0
        precision = {"ROOFTOP": 4, "house": 4, "building": 4, "RANGE_INTERPOLATED": 3,
                     "street": 2, "GEOMETRIC_CENTER": 2, "APPROXIMATE": 1}.get(c.get("precision"), 1)
        return (c.get("niveau", 1), concorde, rue_commune, precision, not c.get("partiel"),
                c.get("service") == "google", -c.get("rang", 0))

    return max(admissibles, key=score) if admissibles else None


def resoudre_pour_tournee(nom, reseau=True, secteur=None, centres=None):
    """GPS en priorité, puis meilleur géocodage disponible, même approximatif.

    La confirmation humaine reste facultative. Les approximations sont conservées
    avec leur provenance et expiration, jamais enregistrées comme « Validée ».
    """
    from customization_app.tournee_optimisation import plausible
    a = frappe.get_doc("Address", nom)
    p = position_memorisee(a)
    if p:
        return dict(_etat_position(p, a.get("custom_geocode_source")), source=a.get("custom_geocode_source"), estimee=False)
    p = estimation_memorisee(a)
    if p and plausible(p, secteur or a.get("custom_secteur"), centres):
        return {"point": list(p), "source": a.custom_geocode_source, "estimee": True, "statut": "À vérifier",
                "motif": a.get("custom_geocode_motif")}
    r = resoudre_adresse(nom, reseau=reseau)
    if r.get("point"):
        courant = frappe.get_doc("Address", nom)
        return dict(r, source=courant.get("custom_geocode_source"), estimee=False)
    # Une ancienne décision ambiguë sans cache ne doit pas arrêter le calcul :
    # une nouvelle recherche est autorisée, avec les mêmes quotas et cache.
    if not r.get("candidats"):
        r = rechercher(a, reseau=reseau)
    c = meilleur_point_estime(r.get("candidats", []), a, secteur, centres)
    if not c:
        return dict(r, point=None)
    resultat = dict(r, point=[c["lat"], c["lng"]], choix=c, estimee=True, statut="À vérifier",
                    source="texte " + c["service"] + " estimé",
                    motif="Position estimée automatiquement pour les trajets. " + (c.get("motif") or "Lieu exact non confirmé."))
    if reseau:
        if not _memoriser(a, resultat):
            # Un GPS ou une adresse modifiée pendant la recherche prend le dessus.
            courant = frappe.get_doc("Address", nom)
            p = position_memorisee(courant)
            if p:
                return dict(_etat_position(p), source=courant.get("custom_geocode_source"), estimee=False)
            if signature(courant) != signature(a) or str(courant.modified) != str(a.modified):
                return choisir({}, courant)
    return resultat


def adresse_modifiee(doc, method=None):
    precedent = doc.get_doc_before_save()
    if precedent and any(doc.get(k) != precedent.get(k) for k in CHAMPS_ADRESSE):
        doc.custom_geocode_statut = "À vérifier"
        doc.custom_geocode_motif = ("Lien modifié : nouvelle lecture nécessaire." if doc.get("custom_lien_google_map") != precedent.get("custom_lien_google_map")
                                   else "Adresse modifiée : position à vérifier.")
        doc.custom_geocode_signature = ""
        doc.custom_geocode_valide_par = None


def purger_expirees():
    """Coordonnées issues de Google : conservation limitée à 30 jours."""
    if not frappe.db.has_column("Address", "custom_geocode_expire_le"):
        return
    frappe.db.sql("""update tabAddress set custom_latitude=0, custom_longitude=0,
        custom_geocode_statut='À vérifier', custom_geocode_motif='Position expirée : nouvelle recherche nécessaire.',
        custom_geocode_signature='', custom_geocode_expire_le=null, custom_geocode_valide_par=null
        where custom_geocode_expire_le is not null and custom_geocode_expire_le <= %s
        and (custom_geocode_source like 'texte google%%' or custom_geocode_source like 'lien google lieu%%')""", (now_datetime(),))


def _acces(nom):
    from customization_app.tournee_optimisation import _superviseur
    _superviseur()
    a = frappe.get_doc("Address", nom)
    a.check_permission("read")
    a.check_permission("write")
    return a


@frappe.whitelist(methods=["POST"])
def diagnostiquer(adresse):
    a = _acces(adresse)
    # Les coordonnées déjà présentes dans un lien passent aussi avant le texte.
    resultat = resoudre_adresse(a.name, texte=False, reseau=True)
    if not resultat.get("point"):
        resultat = rechercher(a)
    jeton = frappe.generate_hash(length=32)
    frappe.cache().set_value(PREFIXE + "validation:" + jeton,
                             {"adresse": a.name, "signature": signature(a), "modified": str(a.modified),
                              "utilisateur": frappe.session.user, "resultat": resultat}, expires_in_sec=15 * 60)
    from customization_app.geocodage_essais import texte_adresse
    return dict(resultat, jeton=jeton, signature=signature(a), nominatim_actif=bool(reglages()["nominatim"]),
                adresse_saisie=texte_adresse(a), lien_present=bool(a.get("custom_lien_google_map")))


@frappe.whitelist()
def etat_adresse(adresse):
    a = _acces(adresse)
    p = position_memorisee(a)
    return {"signature": signature(a), "version": str(a.modified), "point": list(p) if p else None,
            "actif": reglages()["actif"], "nominatim_actif": bool(reglages()["nominatim"])}


@frappe.whitelist(methods=["POST"])
def confirmer(adresse, jeton, candidat):
    a = _acces(adresse)
    lot = frappe.cache().get_value(PREFIXE + "validation:" + str(jeton), expires=True)
    if not lot or lot["adresse"] != a.name or lot["utilisateur"] != frappe.session.user:
        frappe.throw("La proposition a expiré. Relancez la recherche.", frappe.PermissionError)
    courant = _verrouiller(a.name)
    if signature(courant) != lot["signature"] or str(courant.modified) != lot["modified"]:
        frappe.throw("L’adresse a changé. Relancez la recherche avant de confirmer.")
    if position_memorisee(courant):
        frappe.throw("Une position fiable est déjà enregistrée. Elle est conservée.")
    if not re.fullmatch(r"\d+", str(candidat)):
        frappe.throw("Position proposée invalide.")
    i = int(candidat)
    candidats = lot["resultat"].get("candidats", [])
    if not 0 <= i < len(candidats):
        frappe.throw("Position proposée invalide.")
    c = candidats[i]
    resultat = {"statut": "Validée", "point": [c["lat"], c["lng"]], "choix": c,
                "motif": "Position contrôlée et confirmée par un utilisateur."}
    if not _memoriser(courant, resultat, confirme=True):
        frappe.throw("La position n’a pas été enregistrée ; rechargez l’adresse.")
    frappe.cache().delete_value(PREFIXE + "validation:" + str(jeton))
    return {"statut": "Validée"}


@frappe.whitelist(methods=["POST"])
def enregistrer_gps(adresse, empreinte, version, latitude=None, longitude=None, lien=None):
    a = _acces(adresse)
    detail_lien = None
    if lien:
        u = urlsplit(lien.strip())
        h = (u.hostname or "").lower()
        if (u.scheme not in ("http", "https") or u.username or u.password
                or not (h == "maps.app.goo.gl" or (h == "goo.gl" and u.path.startswith("/maps"))
                        or (h in ("google.com", "www.google.com") and u.path.startswith("/maps"))
                        or h == "maps.google.com")):
            frappe.throw("Collez un lien de partage Google Maps ou saisissez les coordonnées GPS.")
        from customization_app.geocodage_liens import resoudre_lien
        detail_lien = resoudre_lien(lien.strip())
        p = detail_lien.get("point") if detail_lien else None
        if not p:
            frappe.throw("Le lien ne contient pas de position lisible. Utilisez un lien de partage ou les coordonnées GPS.")
        latitude, longitude = p
    p = position(latitude, longitude)
    if not p:
        frappe.throw("Saisissez des coordonnées GPS valides en Tunisie.")
    courant = _verrouiller(a.name)
    if signature(courant) != empreinte or str(courant.modified) != str(version):
        frappe.throw("L’adresse a changé : rechargez la fiche.")
    valeurs = {"custom_latitude": p[0], "custom_longitude": p[1],
                        "custom_geocode_source": "position validée", "custom_geocode_statut": "Validée",
                        "custom_geocode_motif": "Coordonnées GPS renseignées et confirmées.",
                        "custom_geocode_expire_le": None, "custom_geocode_valide_par": frappe.session.user}
    if lien:
        courant.custom_lien_google_map = lien.strip()
        valeurs["custom_lien_google_map"] = lien.strip()
        c = (detail_lien or {}).get("choix") or {}
        if c.get("service") == "google":
            valeurs.update(custom_geocode_source="lien google lieu confirmé",
                           custom_geocode_expire_le=add_days(get_datetime(c["obtenu_le"]), 30))
    valeurs["custom_geocode_signature"] = signature(courant)
    frappe.db.set_value("Address", courant.name, valeurs)
    return {"statut": "Validée"}
