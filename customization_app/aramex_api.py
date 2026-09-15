"""Client de l'API officielle Aramex (ShippingAPI.V2, dialecte JSON).

POURQUOI UN SECOND CHEMIN A COTE DU SCRAPING
---------------------------------------------
Le suivi Aramex passait par le service `tej-bank-service`, qui lit le site public avec un
Chromium en mode fenetre : 15 a 25 secondes par colis, quatre appels de front au maximum, des
« 500 » intermittents, et un bordereau muet abandonne au bout de trois passages. L'API
officielle repond en moins de deux secondes pour une LISTE de bordereaux, et elle sait aussi
creer une expedition — ce que le site public ne permettra jamais.

Les deux chemins COEXISTENT et se choisissent par la configuration (`Config Livraison
Aramex.source_suivi`). Ce module ne connait que l'API ; l'aiguillage vit dans
`livraison_aramex`, et les ecrans, le cron, les SMS et le constat de retour ne voient
aucune difference : `normaliser_suivi` rend EXACTEMENT le dictionnaire que le scraper
rendait (statut, livre, etapes, derniere_maj, destination, url).

⚠️ ARAMEX NE SE TROMPE JAMAIS EN HTTP. Un mauvais mot de passe, un compte inconnu, un
bordereau mal forme : la reponse est un 200 avec `HasErrors: true` et la liste
`Notifications[{Code, Message}]`. Lire le code HTTP seul, c'est prendre un refus pour un
succes. `_poster` regarde les deux.

Verifie le 14/09/2026 avec le compte 60519122 / TUN : login accepte, 48812240761 rendu
« SH014 Record created », 51330112761 livre (SH005) puis « SH239 Shipment charges paid »,
51330112960 revenu (SH069), 51330112713 dans `NonExistingWaybills`.
"""

from __future__ import annotations

import datetime
import re

import frappe
from frappe import _

DOCTYPE_CONFIG = "Config Livraison Aramex"
URL_DEFAUT = "https://ws.aramex.net/ShippingAPI.V2"
ROUTE_SUIVI = "/Tracking/Service_1_0.svc/json/TrackShipments"
ROUTE_CREATION = "/Shipping/Service_1_0.svc/json/CreateShipments"
ROUTE_ETIQUETTE = "/Shipping/Service_1_0.svc/json/PrintLabel"
ROUTE_ATTENTE = "/Shipping/Service_1_0.svc/json/HoldShipments"
URL_SUIVI_PUBLIC = "https://www.aramex.com/track/results?ShipmentNumber=%s"

# Modele d'etiquette. ⚠️ 9201 N'IMPRIME PAS LE MONTANT DU CONTRE-REMBOURSEMENT : verifie le
# 15/09/2026 sur le colis 50919841361 — 9729 montre « COD 56 TND », l'expediteur et son
# telephone (c'est l'etiquette du portail) ; 9800 et 9803 sont des declarations douanieres
# CN22 ; 9502, 9718, 9107, 9704 n'existent pas (ERR09).
REPORT_ID_DEFAUT = 9729

SOURCE_SCRAPING = "Scraping (tej-bank-service)"
SOURCE_API = "API Aramex"

# Une requete de suivi porte une liste : cinquante bordereaux par requete est une borne de
# prudence, pas une limite documentee.
PAQUET = 50
TIMEOUT = 60
# Nouvelles tentatives sur 5xx / reseau (voir `_poster`) : 3 essais, pauses 1 s puis 2 s.
TENTATIVES = 3
PAUSE_TENTATIVE = 1.0

# Decalage horaire ecrit dans les dates envoyees (format WCF « /Date(ms+0100)/ »). La Tunisie
# ne change pas d'heure ; Aramex repond en +0200 (heure de ses serveurs), ce qui n'a aucune
# importance : on lit l'instant, pas le fuseau.
DECALAGE_ENVOI = "+0100"

# Les jalons que l'ecran dessine, dans l'ordre du voyage. Six, comme la carte du site public
# a peu de choses pres : le scraper en comptait sept selon les colis, et « livre » ne se
# deduit JAMAIS du nombre de jalons franchis mais du code SH005.
JALONS = ("Créé", "Collecté", "Statut en transit", "En traitement en agence",
          "En cours de livraison", "Livré")
ETAPE_LIVRE = len(JALONS)

# Code Aramex -> (jalon atteint, libelle du statut).
# ⚠️ LES LIBELLES SONT CHOISIS POUR LES MOTS-CLES DEJA EN PLACE, PAS POUR LEUR ELEGANCE :
# `_couleur_aramex` (pastilles de la liste) cherche « créé », « return/retour/renvoy »,
# « refus/echec » ; `livraison_aramex._MOTS_ALERTE` cherche « echec », « retour », « refus »,
# « injoignable », « return » ; `retour_aramex._MOTS_RETOUR` cherche « return », « retour »,
# « renvoy ». Changer un libelle ici, c'est changer une couleur la-bas.
CODES = {
    "SH014": (1, "Créé"),                          # Record created
    "SH047": (2, "Collecté"),                      # Received at Origin Facility
    "SH001": (2, "En traitement en agence"),       # Under processing at operations facility
    "SH022": (3, "Statut en transit"),             # Departed Operations facility – In Transit
    "SH008": (None, "En attente en agence"),       # Shipment on Hold (frequent, sans gravite)
    "SH296": (None, "Adresse corrigée"),           # Delivery Address Corrected
    "SH003": (5, "En cours de livraison"),         # Out for Delivery
    "SH005": (ETAPE_LIVRE, "Livré"),               # Delivered
    "SH239": (ETAPE_LIVRE, "Livré"),               # Shipment charges paid (apres livraison)
    "SH069": (None, "Returned"),                   # Returned to Shipper
    "SH498": (None, "Retour en cours"),            # Pending Return to Shipper
    "SH033": (None, "Échec de livraison"),         # Attempted Delivery
    "SH294": (None, "Échec de livraison"),         # On Hold - Attempting to Contact Customer
}
# La description montree a l'ecran pour les evenements ordinaires — en francais, comme le site
# public que le scraping lisait. ⚠️ « Shipment on Hold » contient « hold », mot-cle d'alerte
# herite du scraping : traduit, il cesse d'alerter — a raison, une mise en attente en agence
# est un passage banal (trois fois sur un colis livre sans encombre). Les ECHECS gardent le
# texte anglais d'Aramex : c'est lui qui porte la raison (adresse, paiement refuse, injoignable).
DESCRIPTIONS = {
    "SH014": "Bordereau créé, colis pas encore remis à Aramex",
    "SH047": "Reçu à l'agence d'origine",
    "SH001": "En traitement à l'agence",
    "SH022": "Parti de l'agence — en transit",
    "SH008": "Colis en attente à l'agence",
    "SH296": "Adresse de livraison corrigée",
    "SH003": "En cours de livraison",
    "SH005": "Colis livré",
    "SH239": "Contre-remboursement remis par Aramex",
    "SH069": "Colis renvoyé à l'expéditeur",
    "SH498": "Retour à l'expéditeur en préparation",
}
CODES_LIVRE = {"SH005", "SH239"}
CODES_FRAIS_PAYES = {"SH239"}
CODES_RETOUR = {"SH069", "SH498"}
# Un evenement porteur d'un ProblemCode (A16 adresse, A18 paiement refuse, U13 injoignable…)
# est un echec de livraison quel que soit son code : le colis ne se resoudra pas seul.
CODES_ECHEC = {"SH033", "SH294"}

_RE_WCF = re.compile(r"/Date\((-?\d+)(?:([+-])(\d{2})(\d{2}))?\)/")


class AramexNonConfigure(frappe.ValidationError):
    """L'API n'est pas activee ou un identifiant manque : rien n'a ete appele."""


class AramexIndisponible(Exception):
    """L'API a ete appelee et n'a pas repondu utilement (reseau, login, HTTP) : le tour
    entier est a refaire par un autre chemin. Levee dans le thread principal seulement."""


# ------------------------------------------------------------------ configuration


def config():
    return frappe.get_cached_doc(DOCTYPE_CONFIG)


def api_active(cfg=None) -> bool:
    cfg = cfg or config()
    return bool(cfg.get("api_active"))


def suivi_par_api(cfg=None) -> bool:
    """Le suivi doit-il passer par l'API ? Faux tant que la case n'est pas cochee ET que la
    source n'est pas choisie : deux gestes distincts, pour que cocher l'API (afin de creer
    des bordereaux) ne bascule pas le suivi de 127 colis sans qu'on l'ait decide."""
    cfg = cfg or config()
    return api_active(cfg) and (cfg.get("source_suivi") or "") == SOURCE_API


def creation_active(cfg=None) -> bool:
    cfg = cfg or config()
    return api_active(cfg) and bool(cfg.get("creation_active"))


def base_url(cfg=None) -> str:
    cfg = cfg or config()
    return (cfg.get("api_url") or URL_DEFAUT).rstrip("/")


def client_info(cfg=None) -> dict:
    """Le bloc `ClientInfo` commun a tous les appels. Leve `AramexNonConfigure` plutot que
    d'envoyer une requete vouee a « ERR75 - Failed to login »."""
    cfg = cfg or config()
    if not api_active(cfg):
        frappe.throw(_("L'API Aramex n'est pas activée (Config Livraison Aramex)."),
                     AramexNonConfigure)
    mot_de_passe = cfg.get_password("api_password", raise_exception=False)
    pin = cfg.get_password("api_account_pin", raise_exception=False)
    manquants = [nom for nom, val in (("Utilisateur", cfg.get("api_username")),
                                       ("Mot de passe", mot_de_passe),
                                       ("Numéro de compte", cfg.get("api_account_number")),
                                       ("PIN du compte", pin)) if not val]
    if manquants:
        frappe.throw(_("Identifiants Aramex incomplets : {0}.").format(", ".join(manquants)),
                     AramexNonConfigure)
    return {
        "UserName": cfg.get("api_username"),
        "Password": mot_de_passe,
        "Version": cfg.get("api_version") or "1.0",
        "AccountNumber": str(cfg.get("api_account_number")).strip(),
        "AccountPin": str(pin).strip(),
        "AccountEntity": (cfg.get("api_account_entity") or "TUN").strip(),
        "AccountCountryCode": (cfg.get("api_account_country") or "TN").strip(),
        "Source": 24,
    }


TRANSACTION_VIDE = {"Reference1": "", "Reference2": "", "Reference3": "",
                    "Reference4": "", "Reference5": ""}


def transaction(reference1="") -> dict:
    return dict(TRANSACTION_VIDE, Reference1=reference1 or "")


# ------------------------------------------------------------------ dates WCF


def parse_date_wcf(texte):
    """« /Date(1789143900000+0200)/ » -> datetime NAIF a l'heure locale annoncee. None si
    le texte n'a pas cette forme. Fonction pure."""
    m = _RE_WCF.search(texte or "")
    if not m:
        return None
    ms = int(m.group(1))
    instant = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
    if m.group(2):
        signe = 1 if m.group(2) == "+" else -1
        decalage = datetime.timedelta(hours=int(m.group(3)), minutes=int(m.group(4))) * signe
        instant = instant + decalage
    return instant.replace(tzinfo=None)


def date_wcf(dt, decalage=DECALAGE_ENVOI) -> str:
    """datetime naif (heure locale) -> « /Date(ms+0100)/ ». Fonction pure.

    Le decalage est ECRIT dans la chaine et RETRANCHE de l'instant : Aramex lit
    `ms` comme un instant UTC et `+0100` comme l'heure a laquelle l'afficher."""
    signe = -1 if decalage.startswith("-") else 1
    delta = datetime.timedelta(hours=int(decalage[1:3]), minutes=int(decalage[3:5])) * signe
    utc = dt.replace(tzinfo=None) - delta
    ms = int((utc - datetime.datetime(1970, 1, 1)).total_seconds() * 1000)
    return "/Date(%d%s)/" % (ms, decalage)


def _date_affichee(dt) -> str:
    return dt.strftime("%d/%m/%Y %H:%M") if dt else ""


# ------------------------------------------------------------------ appel HTTP nu


def _texte_notifications(reponse) -> str:
    notes = (reponse or {}).get("Notifications") or []
    morceaux = []
    for n in notes:
        code = (n or {}).get("Code") or ""
        message = (n or {}).get("Message") or ""
        morceaux.append(("%s — %s" % (code, message)).strip(" —"))
    return " ; ".join(m for m in morceaux if m) or "erreur sans détail"


def _poster(url, corps, timeout=TIMEOUT) -> dict:
    """POST JSON, -> dict, JAMAIS d'exception.

    ⚠️ AUCUN ACCES A FRAPPE ICI : meme discipline que `livraison_aramex._appel`, pour que la
    fonction puisse un jour tourner dans un thread. Une reponse HTTP ≠ 200, un JSON illisible,
    un timeout ou `HasErrors` rendent `{"erreur": "..."}` ; le reste rend le JSON tel quel.
    """
    import time

    import requests

    # ⚠️ ARAMEX REPOND 503 PAR INTERMITTENCE (prod, 15/09/2026 : 2 appels sur 4 identiques a
    # quelques secondes d'intervalle). Sans nouvelle tentative, chaque 503 bascule tout le
    # paquet sur le scraping (21 colis, ~2 min, le clic « Interroger » finit en erreur 500).
    # On reessaie donc les erreurs de serveur (5xx) et de reseau, jamais les 4xx ni un refus
    # metier (HasErrors) : ceux-la ne changeront pas en attendant.
    r = None
    for tentative in range(TENTATIVES):
        try:
            r = requests.post(url, json=corps, timeout=timeout,
                              headers={"Content-Type": "application/json",
                                       "Accept": "application/json"})
        except Exception as e:
            r = None
            erreur = ("réseau : %s" % e)[:200]
        else:
            if r.status_code < 500:
                break
            erreur = "HTTP %s — %s" % (r.status_code, (r.text or "")[:160])
        if tentative + 1 < TENTATIVES:
            time.sleep(PAUSE_TENTATIVE * (tentative + 1))
    else:
        return {"erreur": erreur}
    if r.status_code != 200:
        return {"erreur": "HTTP %s — %s" % (r.status_code, (r.text or "")[:160])}
    try:
        donnees = r.json()
    except Exception:
        return {"erreur": "réponse illisible : %s" % (r.text or "")[:160]}
    if not isinstance(donnees, dict):
        return {"erreur": "réponse inattendue : %s" % str(donnees)[:160]}
    if donnees.get("HasErrors"):
        return {"erreur": _texte_notifications(donnees), "reponse": donnees}
    return donnees


# ------------------------------------------------------------------ suivi


def _evenement(brut) -> dict:
    dt = parse_date_wcf((brut or {}).get("UpdateDateTime"))
    return {
        "code": (brut or {}).get("UpdateCode") or "",
        "description": (brut or {}).get("UpdateDescription") or "",
        "date": _date_affichee(dt),
        "instant": dt.isoformat() if dt else None,
        "lieu": (brut or {}).get("UpdateLocation") or "",
        "commentaire": (brut or {}).get("Comments") or "",
        "probleme": (brut or {}).get("ProblemCode") or "",
        "poids": (brut or {}).get("ChargeableWeight") or "",
    }


def _libelle(evenement) -> str:
    """Le statut affiche pour UN evenement. Un echec porte la raison d'Aramex : « Échec de
    livraison — Attempted Delivery - Payment was Declined by Consignee » dit au vendeur quoi
    faire, « Échec de livraison » seul ne dit rien."""
    code = evenement["code"]
    jalon, libelle = CODES.get(code, (None, None))
    if code in CODES_ECHEC or (evenement.get("probleme") and code not in CODES_RETOUR
                                and code not in CODES_LIVRE):
        return "Échec de livraison — %s" % (evenement["description"] or code)
    if code in CODES_RETOUR:
        return "%s — %s" % (libelle, evenement["description"]) if code == "SH498" else libelle
    return libelle or evenement["description"] or code


def normaliser_suivi(reference, evenements_bruts) -> dict:
    """La liste `TrackingResults[].Value[]` d'Aramex -> LE dictionnaire que tout l'existant
    lit. Fonction pure : c'est elle qu'on teste, sur les historiques reels captures.

    Le jalon atteint est le MAXIMUM des jalons de l'historique (un « En traitement en
    agence » a l'arrivee ne fait pas reculer un colis deja « en transit ») ; le statut, lui,
    est celui du DERNIER evenement, parce que c'est ce qui se passe maintenant.
    """
    evenements = sorted((_evenement(b) for b in (evenements_bruts or [])),
                        key=lambda e: e["instant"] or "", reverse=True)
    if not evenements:
        return {"reference": reference, "statut": None, "livre": False,
                "etapes_franchies": 0, "etapes_total": ETAPE_LIVRE, "etapes": [],
                "derniere_maj": {"description": None, "date": None},
                "destination": {"pays": None, "ville": None},
                "url": URL_SUIVI_PUBLIC % reference, "source": "api",
                "code": None, "probleme": None, "frais_payes": False, "evenements": []}

    codes = {e["code"] for e in evenements}
    livre = bool(codes & CODES_LIVRE)
    jalon = max([CODES[e["code"]][0] or 0 for e in evenements if e["code"] in CODES] or [0])
    if livre:
        jalon = ETAPE_LIVRE
    dernier = evenements[0]
    statut = "Livré" if livre else _libelle(dernier)
    retour = bool(codes & CODES_RETOUR) and not livre
    if retour and dernier["code"] not in CODES_RETOUR:
        # Un colis revenu peut encore recevoir un evenement administratif apres le SH069 :
        # le retour reste ce qui compte pour l'ecran et le bouton « Retour reçu ».
        statut = CODES["SH069"][1]

    etapes = [{"libelle": lib, "franchie": i < jalon, "courante": i == jalon - 1}
              for i, lib in enumerate(JALONS)]
    lieu = dernier["lieu"]
    # « Branch Bizerte, Tunisia » -> ville « Branch Bizerte » : Aramex ne donne pas la ville
    # de destination dans le suivi, on montre le dernier lieu connu du colis.
    ville = lieu.split(",")[0].strip() if lieu else None
    pays = lieu.split(",")[-1].strip() if lieu and "," in lieu else None
    description = dernier["description"]
    if dernier["code"] in DESCRIPTIONS and not dernier["probleme"]:
        description = DESCRIPTIONS[dernier["code"]]
    if dernier["commentaire"]:
        description = "%s (%s)" % (description, dernier["commentaire"].strip())
    return {
        "reference": reference,
        "statut": statut,
        "livre": livre,
        "etapes_franchies": jalon,
        "etapes_total": ETAPE_LIVRE,
        "etapes": etapes,
        "derniere_maj": {"description": description, "date": dernier["date"],
                         "lieu": lieu},
        "destination": {"pays": pays, "ville": ville},
        "url": URL_SUIVI_PUBLIC % reference,
        "source": "api",
        "code": dernier["code"],
        "probleme": dernier["probleme"] or None,
        "frais_payes": bool(codes & CODES_FRAIS_PAYES),
        "poids": dernier["poids"] or None,
        "evenements": evenements,
    }


def track_shipments(references, timeout=TIMEOUT, cfg=None) -> dict:
    """Interroge l'API pour une liste de bordereaux. -> {reference: suivi}.

    Un bordereau inconnu d'Aramex (`NonExistingWaybills`) rend `{"erreur": "404 — …"}` :
    c'est la forme que `livraison_aramex.repond_vraiment` et `_ranger_echec` savent lire
    (une REPONSE, pas une panne — le compteur de tentatives monte, sans nouvel essai).

    Leve `AramexIndisponible` si un paquet entier echoue (reseau, login, HTTP) : l'appelant
    decide alors de se replier sur le scraping pour ce tour.
    """
    cfg = cfg or config()
    infos = client_info(cfg)
    url = base_url(cfg) + ROUTE_SUIVI
    references = [str(r) for r in dict.fromkeys(references or []) if r]
    out = {}
    for debut in range(0, len(references), PAQUET):
        paquet = references[debut:debut + PAQUET]
        corps = {"ClientInfo": infos, "GetLastTrackingUpdateOnly": False,
                 "Shipments": paquet, "Transaction": transaction()}
        reponse = _poster(url, corps, timeout)
        if reponse.get("erreur"):
            raise AramexIndisponible(reponse["erreur"])
        trouves = {}
        for r in reponse.get("TrackingResults") or []:
            cle = str((r or {}).get("Key") or "")
            if cle:
                trouves.setdefault(cle, []).extend((r or {}).get("Value") or [])
        inconnus = {str(x) for x in (reponse.get("NonExistingWaybills") or [])}
        for reference in paquet:
            if reference in trouves:
                out[reference] = normaliser_suivi(reference, trouves[reference])
            elif reference in inconnus:
                out[reference] = {"erreur": "404 — aucune expédition Aramex pour %s"
                                            % reference, "reference": reference}
            else:
                # Ni trouve ni declare inconnu : on ne sait pas, et on le dit comme tel
                # (un echec, donc reessayable), plutot que d'inventer un 404.
                out[reference] = {"erreur": "réponse sans résultat pour %s" % reference,
                                  "reference": reference}
    return out


# ------------------------------------------------------------------ expedition


def create_shipment(expedition, etiquette=True, timeout=TIMEOUT, cfg=None) -> dict:
    """Cree UNE expedition. -> {"numero", "etiquette_url", "reponse"} ou {"erreur"}.

    `expedition` est le corps construit par `aramex_expedition.construire_expedition` (sans
    ClientInfo ni LabelInfo : ils sont poses ici, pour que la construction reste pure).
    Une erreur PAR expedition (`Shipments[0].HasErrors`) est rendue comme une erreur : la
    creation est atomique, il n'y a qu'un colis par appel.
    """
    cfg = cfg or config()
    corps = {
        "ClientInfo": client_info(cfg),
        "LabelInfo": ({"ReportID": int(cfg.get("api_report_id") or REPORT_ID_DEFAUT),
                       "ReportType": "URL"} if etiquette else None),
        "Shipments": [expedition],
        "Transaction": transaction(expedition.get("Reference1")),
    }
    reponse = _poster(base_url(cfg) + ROUTE_CREATION, corps, timeout)
    if reponse.get("erreur"):
        # ⚠️ LE DETAIL EST SOUVENT DANS L'EXPEDITION, PAS EN TETE : « HasErrors: true » avec
        # des Notifications vides au sommet, et « ERR48 Consignee name is required » dans
        # Shipments[0].Notifications (constate le 15/09/2026). On va le chercher la.
        brut = reponse.get("reponse") or {}
        details = [_texte_notifications(s) for s in (brut.get("Shipments") or [])
                   if (s or {}).get("Notifications")]
        erreur = reponse["erreur"]
        if erreur == "erreur sans détail" and details:
            erreur = " ; ".join(details)
        elif details:
            erreur = "%s ; %s" % (erreur, " ; ".join(details))
        return {"erreur": erreur, "reponse": brut}
    colis = ((reponse.get("Shipments") or [None])[0]) or {}
    if colis.get("HasErrors"):
        return {"erreur": _texte_notifications(colis), "reponse": reponse}
    numero = str(colis.get("ID") or "").strip()
    if not numero:
        return {"erreur": "réponse sans numéro de bordereau", "reponse": reponse}
    return {"numero": numero,
            "etiquette_url": ((colis.get("ShipmentLabel") or {}).get("LabelURL") or None),
            "reponse": reponse}


def print_label(numero, timeout=TIMEOUT, cfg=None) -> dict:
    """-> {"etiquette_url"} ou {"erreur"}."""
    cfg = cfg or config()
    corps = {
        "ClientInfo": client_info(cfg),
        "LabelInfo": {"ReportID": int(cfg.get("api_report_id") or REPORT_ID_DEFAUT), "ReportType": "URL"},
        "OriginEntity": (cfg.get("api_account_entity") or "TUN").strip(),
        "ProductGroup": cfg.get("product_group") or "DOM",
        "ShipmentNumber": str(numero),
        "Transaction": transaction(),
    }
    reponse = _poster(base_url(cfg) + ROUTE_ETIQUETTE, corps, timeout)
    if reponse.get("erreur"):
        return {"erreur": reponse["erreur"]}
    url = ((reponse.get("ShipmentLabel") or {}).get("LabelURL")) or None
    return {"etiquette_url": url} if url else {"erreur": "réponse sans étiquette"}


def hold_shipment(numero, commentaire, timeout=TIMEOUT, cfg=None) -> dict:
    """Met un colis en attente chez Aramex (il n'existe pas d'annulation dans l'API).
    -> {"ok": True} ou {"erreur"}."""
    cfg = cfg or config()
    corps = {
        "ClientInfo": client_info(cfg),
        "ShipmentHolds": [{"ShipmentNumber": str(numero), "Comment": commentaire or ""}],
        "Transaction": transaction(),
    }
    reponse = _poster(base_url(cfg) + ROUTE_ATTENTE, corps, timeout)
    if reponse.get("erreur"):
        return {"erreur": reponse["erreur"]}
    for r in reponse.get("ShipmentHolds") or []:
        if (r or {}).get("HasErrors"):
            return {"erreur": _texte_notifications(r)}
    return {"ok": True}


# ------------------------------------------------------------------ villes


ROUTE_VILLES = "/Location/Service_1_0.svc/json/FetchCities"
ROUTE_ADRESSE = "/Location/Service_1_0.svc/json/ValidateAddress"
CLE_CACHE_VILLES = "aramex_villes_tn"
CACHE_VILLES_SECONDES = 24 * 3600


def _villes_embarquees() -> list:
    """La liste des 308 villes tunisiennes d'Aramex, capturee le 14/09/2026 : le secours quand
    l'API ne repond pas, pour que le dialogue garde sa liste."""
    import json
    import os

    chemin = os.path.join(os.path.dirname(__file__), "aramex_villes_tn.json")
    try:
        with open(chemin, encoding="utf-8") as f:
            return list(json.load(f))
    except Exception:
        return []


def fetch_cities(timeout=TIMEOUT, cfg=None) -> list:
    """Les villes qu'Aramex accepte pour le pays du compte. -> [str]. Leve
    `AramexIndisponible` si l'API ne repond pas.

    ⚠️ ARAMEX A SA PROPRE GRAPHIE, ET UNE VILLE INCONNUE FAIT REFUSER LE COLIS (ERR05 « City
    name is invalid »). « Soukra » n'existe pas, « La Soukra » oui ; « El Ghazala » n'existe pas
    du tout. Pour la Tunisie, il n'y a ni etats ni filtre par gouvernorat : la liste est plate.
    """
    cfg = cfg or config()
    corps = {"ClientInfo": client_info(cfg),
             "CountryCode": (cfg.get("api_account_country") or "TN").strip(),
             "State": "", "NameStartsWith": "", "Transaction": transaction()}
    reponse = _poster(base_url(cfg) + ROUTE_VILLES, corps, timeout)
    if reponse.get("erreur"):
        raise AramexIndisponible(reponse["erreur"])
    return [str(v) for v in (reponse.get("Cities") or []) if v]


def villes(cfg=None) -> list:
    """La liste des villes, depuis le cache (24 h), sinon l'API, sinon la copie embarquee.
    Ne leve jamais : sans liste, le dialogue ne pourrait pas s'ouvrir."""
    cache = frappe.cache()
    try:
        connues = cache.get_value(CLE_CACHE_VILLES)
        if connues:
            return list(connues)
    except Exception:
        pass
    try:
        connues = fetch_cities(cfg=cfg)
    except Exception:
        connues = []
    if connues:
        try:
            cache.set_value(CLE_CACHE_VILLES, connues, expires_in_sec=CACHE_VILLES_SECONDES)
        except Exception:
            pass
        return connues
    return _villes_embarquees()


def validate_address(adresse, timeout=TIMEOUT, cfg=None) -> dict:
    """Demande a Aramex si l'adresse passe. -> {"ok": True} ou {"erreur": "ERR05 — …"}.

    `adresse` : {ligne1, ville, gouvernorat, code_postal}. Un appel d'une seconde qui evite
    de creer un colis qu'Aramex refuserait de toute facon — ou pire, accepterait avec une ville
    approximative."""
    cfg = cfg or config()
    corps = {
        "ClientInfo": client_info(cfg),
        "Address": {"Line1": (adresse.get("ligne1") or "")[:50], "Line2": "", "Line3": "",
                    "City": adresse.get("ville") or "",
                    "StateOrProvinceCode": adresse.get("gouvernorat") or "",
                    "PostCode": adresse.get("code_postal") or "",
                    "CountryCode": (cfg.get("api_account_country") or "TN").strip()},
        "Transaction": transaction(),
    }
    reponse = _poster(base_url(cfg) + ROUTE_ADRESSE, corps, timeout)
    if reponse.get("erreur"):
        return {"erreur": reponse["erreur"]}
    return {"ok": True}


def telecharger(url, timeout=TIMEOUT) -> bytes | None:
    """Rapatrie l'etiquette (PDF) depuis l'URL rendue par Aramex. None si echec — un colis
    cree sans etiquette attachee reste un colis cree, l'etiquette se redemande."""
    import requests

    try:
        r = requests.get(url, timeout=timeout)
        return r.content if r.status_code == 200 and r.content else None
    except Exception:
        return None


@frappe.whitelist()
def tester_connexion():
    """Bouton de la configuration : un TrackShipments sur un bordereau bidon suffit a
    prouver que le login passe (la reponse est alors « inconnu », pas « ERR75 »).

    ⚠️ « 00000000 » EXISTE CHEZ ARAMEX (repondu « Picked Up From Shipper » le 14/09/2026) :
    n'importe quelle reponse sans erreur globale vaut connexion reussie, le numero ne sert
    qu'a avoir quelque chose a demander."""
    frappe.only_for(["System Manager", "Sales Manager"])
    sentinelle = "1"
    try:
        res = track_shipments([sentinelle])
    except (AramexIndisponible, AramexNonConfigure) as e:
        return {"ok": False, "erreur": str(e)}
    suivi = res.get(sentinelle) or {}
    return {"ok": True, "detail": suivi.get("erreur") or suivi.get("statut") or ""}
