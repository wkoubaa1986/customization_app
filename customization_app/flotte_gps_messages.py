"""Flotte GPS — tenir le client au courant, d'après la position des véhicules (voir flotte_gps.py).

Toutes les 2 min (cron) : pour chaque technicien, la prochaine intervention reçoit un SMS « en route » quand il vient
vers elle (arrivée estimée à moins de `sms_delai_max` min), et un SMS « retard » quand l'arrivée estimée dépasse
l'heure annoncée de plus que la tolérance. Un seul message de chaque type par intervention, dans la plage horaire du
réglage. Le rôle Appels (Salma) est alerté des retards prévisibles (notification Desk), une fois par intervention.
Chaque message est journalisé (Message Client GPS) avec son texte et son verdict, et visible sur la page Suivi terrain.

⛔ GARDE-FOU DEV — même règle que sms_taches : la base dev porte les vrais numéros et la vraie passerelle, donc en
developer_mode tout est SIMULÉ (journalisé, rien ne part), sauf `sms_groupe_reel_en_dev` dans site_config.json.
En prod, rien ne part tant que les cases du réglage ne sont pas cochées.
"""

from datetime import datetime, timedelta

import frappe
from frappe.utils import cint, get_datetime, getdate, now_datetime, nowdate

from customization_app import flotte_gps as fg

JOURNAL = "Message Client GPS"
EN_ROUTE, RETARD, ALERTE, NON_FAITE = "En route", "Retard", "Alerte interne", "Non faite"
INTERNES = (ALERTE, NON_FAITE)
DEFAUT_EN_ROUTE = ("Bonjour {nom_client}, votre technicien {technicien} est en route, arrivée estimée vers {heure_estimee}."
                   " Suivre : {lien} - Aqua World")
DEFAUT_RETARD = ("Bonjour {nom_client}, votre technicien {technicien} a du retard : arrivée estimée vers {heure_estimee}"
                 " au lieu de {heure_annoncee}. Desoles. Suivre : {lien} - Aqua World")
ETAPES_A_VENIR = ("en route", "à venir", "en retard", "sur place")


def reglage() -> dict:
    v = lambda champ: frappe.db.get_single_value(fg.CONFIG, champ)  # noqa: E731
    return {"en_route": cint(v("sms_en_route")), "retard": cint(v("sms_retard")), "lien": cint(v("sms_lien")) if v("sms_lien") is not None else 1,
            "alerte": cint(v("alerte_salma")) if v("alerte_salma") is not None else 1,
            "delai_max": cint(v("sms_delai_max")) or 45, "tolerance": cint(v("ecart_tolere")) or 30,
            # Un champ Time jamais saisi vaut timedelta(0) en base : 0 = « pas réglé » → défaut.
            "heure_min": fg.to._minutes(v("sms_heure_min"), 8 * 60) or 8 * 60, "heure_max": fg.to._minutes(v("sms_heure_max"), 19 * 60) or 19 * 60,
            "url": (v("url_suivi") or "").strip().rstrip("/") or frappe.utils.get_url()}


def simulation() -> bool:
    return bool(cint(frappe.conf.get("developer_mode"))) and not cint(frappe.conf.get("sms_groupe_reel_en_dev"))


# ── Lien de suivi ────────────────────────────────────────────────────────────

def code_suivi(tache: str) -> str:
    """Le code du lien public d'une tâche, créé à la première demande (sans toucher `modified`)."""
    code = frappe.db.get_value(fg.TACHE, tache, "custom_gps_code")
    if not code:
        code = frappe.generate_hash(length=12)
        frappe.db.set_value(fg.TACHE, tache, "custom_gps_code", code, update_modified=False)
    return code


def lien_suivi(tache: str, cfg: dict | None = None) -> str:
    return "%s/suivi/%s" % ((cfg or reglage())["url"], code_suivi(tache))


# ── Décision (pure) ──────────────────────────────────────────────────────────

def decider(employes: list, maintenant: datetime, cfg: dict, deja: dict) -> list:
    """Les messages à produire maintenant → [{tache, type, eta, ecart, employe, client, raison}].
    `employes` : sortie de flotte_gps._suivi ; `deja` : {tache: {types déjà envoyés ou simulés}}.
    Règles : « en route » pour la PROCHAINE intervention d'un technicien en mouvement (ou déjà en route), arrivée
    estimée dans ≤ delai_max min ; « retard » dès que l'arrivée estimée dépasse l'heure annoncée de plus que la
    tolérance (pas si le client vient de recevoir « en route » avec la même heure) ; alerte interne sur le même
    critère. Un message par type et par tâche ; SMS seulement dans la plage horaire."""
    minute = maintenant.hour * 60 + maintenant.minute
    dans_plage = cfg["heure_min"] <= minute <= cfg["heure_max"]
    out = []
    for e in employes:
        # Sautée (non faite, une suivante déjà faite) : jamais de SMS automatique, une alerte interne pour la reprogrammer.
        for t in e["taches"]:
            if t.get("etape") == "sautée" and cfg["alerte"] and NON_FAITE not in deja.get(t["name"], set()):
                out.append({"tache": t["name"], "type": NON_FAITE, "eta": get_datetime(t["debut"]), "ecart": None, "employe": e["employe"],
                            "client": t.get("client") or "", "raison": "non faite à l'heure prévue, à reprogrammer (rappeler le client)"})
        restantes = [t for t in e["taches"] if t.get("etape") in ETAPES_A_VENIR and t.get("eta")]
        if not restantes:
            continue
        prochaine = restantes[0]
        for t in restantes:
            faits = deja.get(t["name"], set())
            eta, debut = get_datetime(t["eta"]), get_datetime(t["debut"])
            ecart = round((eta - debut).total_seconds() / 60)
            base = {"tache": t["name"], "eta": eta, "ecart": ecart, "employe": e["employe"], "client": t.get("client") or ""}
            # « En route » seulement si le créneau n'est pas déjà dépassé : une intervention en retard a reçu (ou reçoit)
            # « retard » avec l'heure estimée, et le technicien va peut-être à la suivante (on ne sait pas vers qui il roule).
            if t is prochaine and cfg["en_route"] and dans_plage and EN_ROUTE not in faits and t["etape"] in ("en route", "à venir") \
                    and e.get("etat") == "en mouvement" and (eta - maintenant).total_seconds() / 60 <= cfg["delai_max"]:
                out.append(dict(base, type=EN_ROUTE, raison="prochaine intervention, arrivée dans %d min" % max(0, (eta - maintenant).total_seconds() // 60)))
                faits = faits | {EN_ROUTE}
            if ecart > cfg["tolerance"]:
                if cfg["retard"] and dans_plage and RETARD not in faits and not (EN_ROUTE in faits and t is prochaine and any(
                        m["type"] == EN_ROUTE for m in out if m["tache"] == t["name"])):
                    out.append(dict(base, type=RETARD, raison="arrivée estimée %d min après l'heure annoncée" % ecart))
                if cfg["alerte"] and ALERTE not in faits:
                    out.append(dict(base, type=ALERTE, raison="retard prévisible de %d min" % ecart))
    return out


# ── Rendu & envoi ────────────────────────────────────────────────────────────

def _modele(type_: str) -> str:
    champ = "suivi_en_route" if type_ == EN_ROUTE else "suivi_retard"
    texte = frappe.db.get_single_value("Config Modeles SMS", champ) if frappe.db.exists("DocType", "Config Modeles SMS") else None
    return (texte or "").strip() or (DEFAUT_EN_ROUTE if type_ == EN_ROUTE else DEFAUT_RETARD)


def rendre(type_: str, m: dict, ligne: dict, cfg: dict) -> str:
    valeurs = {"nom_client": ligne.get("nom_client") or "", "technicien": (ligne.get("technicien") or "").split(" ")[0],
               "heure_estimee": get_datetime(m["eta"]).strftime("%H:%M"), "heure_annoncee": ligne.get("heure") or "",
               "lien": lien_suivi(m["tache"], cfg) if cfg["lien"] else ""}
    texte = _modele(type_)
    for k, v in valeurs.items():
        texte = texte.replace("{%s}" % k, str(v))
    return " ".join(texte.split())


def _journaliser(m: dict, statut: str, texte: str = "", telephone: str = "", detail: str = ""):
    frappe.get_doc({"doctype": JOURNAL, "tache": m["tache"], "client": m.get("client"), "telephone": telephone, "type": m["type"],
                    "statut": statut, "heure": now_datetime(), "eta": m.get("eta"), "ecart": m.get("ecart"), "employe": m.get("employe"),
                    "texte": texte, "detail": detail}).insert(ignore_permissions=True)


def _alerter_appels(m: dict, ligne: dict):
    """Notification Desk (cloche) aux utilisateurs du rôle Appels : retard prévisible chez tel client."""
    users = frappe.get_all("Has Role", filters={"role": "Appels", "parenttype": "User"}, pluck="parent")
    users = [u for u in set(users) if frappe.db.get_value("User", u, "enabled")]
    if m["type"] == NON_FAITE:
        sujet = "⛔ Intervention non faite : %s (%s), prévue %s — à reprogrammer, rappeler le client" % (
            ligne.get("nom_client") or m["tache"], (ligne.get("technicien") or "").split(" ")[0], ligne.get("heure") or "?")
    else:
        sujet = "⚠️ Retard prévisible : %s (%s) — arrivée estimée %s au lieu de %s" % (
            ligne.get("nom_client") or m["tache"], (ligne.get("technicien") or "").split(" ")[0], get_datetime(m["eta"]).strftime("%H:%M"), ligne.get("heure") or "?")
    for u in users:
        frappe.get_doc({"doctype": "Notification Log", "for_user": u, "type": "Alert", "subject": sujet,
                        "document_type": fg.TACHE, "document_name": m["tache"]}).insert(ignore_permissions=True)
    frappe.publish_realtime("flotte_gps_retard", {"tache": m["tache"], "sujet": sujet}, after_commit=True)
    return users


def executer(messages: list, cfg: dict, ecrire: bool = True) -> list:
    """Envoie (ou simule) et journalise. Rend les verdicts."""
    from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import envoyer_sms_verifie
    from customization_app.sms_taches import _destinataires

    simule = simulation()
    lignes = {l["tache"]: l for l in _destinataires(list({m["tache"] for m in messages}))} if messages else {}
    verdicts = []
    for m in messages:
        ligne = lignes.get(m["tache"]) or {}
        if m["type"] in INTERNES:
            users = _alerter_appels(m, ligne) if ecrire else []
            v = dict(m, statut="Interne", texte="", telephone="", detail="notifiés : %s" % ", ".join(users))
        else:
            texte = rendre(m["type"], m, ligne, cfg)
            numeros = ligne.get("numeros") or []
            if not numeros:
                v = dict(m, statut="Échec", texte=texte, telephone="", detail="aucun numéro de téléphone")
            elif simule:
                v = dict(m, statut="Simulé", texte=texte, telephone=", ".join(numeros), detail="🧪 SIMULÉ (dev) — rien n'est parti")
            else:
                recus, refuses = [], []
                for numero in numeros:
                    try:
                        envoyer_sms_verifie(numero, texte)
                        recus.append(numero)
                    except Exception as e:
                        refuses.append("%s (%s)" % (numero, str(e)[:80]))
                v = dict(m, statut="Envoyé" if recus else "Échec", texte=texte, telephone=", ".join(numeros),
                         detail=" · ".join(filter(None, ["✅ " + ", ".join(recus) if recus else "", "❌ " + " ; ".join(refuses) if refuses else ""])))
        if ecrire:
            _journaliser(v, v["statut"], v["texte"], v["telephone"], v["detail"])
        verdicts.append(v)
    if ecrire:
        frappe.db.commit()
    return verdicts


def _deja(jour) -> dict:
    out = {}
    for r in frappe.get_all(JOURNAL, filters={"heure": ["between", ["%s 00:00:00" % jour, "%s 23:59:59" % jour]], "statut": ["!=", "Échec"]},
                            fields=["tache", "type"]):
        out.setdefault(r.tache, set()).add(r.type)
    return out


def evaluer(ecrire: bool = True) -> dict:
    """Un tour du moteur : ce qui doit partir maintenant, envoyé/simulé si `ecrire`, sinon seulement listé (aperçu)."""
    cfg = reglage()
    if not (cfg["en_route"] or cfg["retard"] or cfg["alerte"]):
        return {"messages": [], "simulation": simulation(), "actif": False}
    s = fg._suivi(nowdate())
    maintenant = get_datetime(s["maintenant"])
    messages = decider(s["employes"], maintenant, cfg, _deja(nowdate()))
    return {"messages": executer(messages, cfg, ecrire=ecrire), "simulation": simulation(), "actif": True, "maintenant": maintenant}


def cron():
    cfg = fg.config()
    if not (cfg["utilisateur"] and cfg["mot_de_passe"] and cfg["cboxes"]):
        return
    try:
        evaluer(ecrire=True)
    except Exception:
        frappe.log_error(frappe.get_traceback()[-1500:], "Flotte GPS : messages clients")


@frappe.whitelist()
def apercu():
    """Page : ce qui partirait maintenant (sans rien envoyer ni journaliser)."""
    fg._garde()
    r = evaluer(ecrire=False)
    for m in r["messages"]:
        m["eta"] = str(m["eta"])
    return r


@frappe.whitelist(methods=["POST"])
def simuler():
    """Page, en dev : un tour du moteur, journalisé comme « Simulé »."""
    fg._garde()
    if not simulation():
        frappe.throw("Simulation réservée au developer_mode : en production, le moteur tourne tout seul (cron).")
    r = evaluer(ecrire=True)
    for m in r["messages"]:
        m["eta"] = str(m["eta"])
    frappe.cache().delete_value("flotte_gps:suivi:%s" % nowdate())
    return r


# ── Page publique /suivi/<code> ──────────────────────────────────────────────

@frappe.whitelist(allow_guest=True)
def etat_suivi(code: str):
    """Ce que le client voit : son intervention, l'état, l'arrivée estimée, le nombre d'interventions avant la sienne,
    et la position du technicien seulement quand il vient vers lui (en route / sur place)."""
    code = (code or "").strip()
    if not code or len(code) < 8:
        return {"erreur": "lien invalide"}
    t = frappe.db.get_value(fg.TACHE, {"custom_gps_code": code}, ["name", "starts_on", "custom_choix_du_staff", "nom_client", "status",
                                                                  "custom_type_dintervention", "details_adresse"], as_dict=True)
    if not t:
        return {"erreur": "lien invalide"}
    jour = getdate(t.starts_on)
    out = {"client": t.nom_client, "type": t.custom_type_dintervention, "annonce": str(t.starts_on)[11:16], "date": jour.strftime("%d/%m/%Y"),
           "statut": t.status, "etape": "", "eta": None, "avant": 0, "technicien": "", "position": None, "adresse": None,
           "maintenant": now_datetime().strftime("%H:%M"), "support": frappe.db.get_single_value("Config Portail RDV", "tel_support") or ""}
    if t.status == "Cancelled":
        out["etape"] = "annulée"
        return out
    if jour != getdate(nowdate()):
        out["etape"] = "passée" if jour < getdate(nowdate()) else "à venir"
        return out
    s = fg._suivi(jour)
    for e in s["employes"]:
        if e["employe"] != t.custom_choix_du_staff:
            continue
        out["technicien"] = (e["nom"] or "").split(" ")[0]
        for x in e["taches"]:
            if x["name"] != t.name:
                continue
            out["etape"] = x["etape"]
            out["eta"] = get_datetime(x["eta"]).strftime("%H:%M") if x.get("eta") else None
            out["eta_depart"] = get_datetime(x["eta_depart"]).strftime("%H:%M") if x.get("eta_depart") else None
            if x["etape"] == "passée":
                out["reel"] = {"arrivee": get_datetime(x["gps_live"]["arrivee"]).strftime("%H:%M"), "depart": get_datetime(x["gps_live"]["depart"]).strftime("%H:%M")}
            if x["etape"] == "sur place":
                out["arrivee"] = get_datetime(x["arrivee_reelle"]).strftime("%H:%M")
            out["avant"] = len([y for y in e["taches"] if y.get("etape") in ("sur place", "en route", "à venir", "en retard") and y["debut"] < x["debut"]])
            if x.get("lat"):
                out["adresse"] = [x["lat"], x["lng"]]
            if e.get("position") and x["etape"] in ("en route", "sur place") or (e.get("position") and out["avant"] == 0 and x["etape"] in ("à venir", "en retard")):
                out["position"] = [e["position"]["lat"], e["position"]["lng"]]
                out["position_heure"] = get_datetime(e["position"]["heure"]).strftime("%H:%M") if e["position"].get("heure") else ""
                # Le trajet routier de la voiture jusqu'au client (OSRM, mis en cache), et sa durée.
                if out["adresse"] and x["etape"] != "sur place":
                    out["trajet"] = fg._trace_routiere([tuple(out["position"]), tuple(out["adresse"])], fg.to.config()["osrm"])
                    out["route_min"] = x.get("route_min")
    return out
