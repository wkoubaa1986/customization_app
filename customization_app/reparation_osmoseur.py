"""Réparation des osmoseurs déposés à l'atelier — écran « Réparation osmoseurs ».

Le processus, tel que demandé le 01/10/2026 :

1. RÉCEPTION : photo de la machine à l'arrivée, client, note. La réception se
   CLÔTURE sur une seconde photo (la machine avec son post-it qui porte le numéro
   de dossier), ou sur le code superviseur de Config Cloture Tache — un seul code
   à retenir dans l'app.
2. AFFECTATION AUTOMATIQUE : à la clôture, une tâche « Réparation » est créée pour
   le LENDEMAIN. On parcourt les responsables de réparation DANS L'ORDRE configuré
   (Config Reparation Osmoseur), on retient le premier disponible ce jour-là ; un
   jour n'a qu'UN SEUL opérateur et reçoit au plus N machines (3 par défaut). Jour
   plein ou personne de disponible → jour suivant.
3. VALIDATION : la clôture de la (des) tâche(s) liée(s) fait passer le dossier à
   « Réparée » — c'est-à-dire À RENDRE.
4. RESTITUTION (02/10/2026) : deux chemins. « Le client vient la chercher » → « Prête au
   magasin » (SMS « machine prête » proposé), puis « Rendue au client » à la remise (personne,
   photo facultative). « Planifier une livraison » → tâche Livraison au calendrier, dossier
   « Livraison planifiée » ; la clôture de cette tâche rend la machine toute seule, sa
   suppression ramène le dossier à « Réparée ».

Toute la décision est ici, côté serveur ; l'écran n'invente rien.
"""

from __future__ import annotations

import base64
import hmac
import json
import re
import unicodedata

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, now_datetime, nowdate

DOCTYPE = "Machine Reparation"
DOCTYPE_TACHE = "Tache de travail"
DOCTYPE_CONFIG = "Config Reparation Osmoseur"
CHAMP_TACHE = "machine_reparation"        # Custom Field sur la Tache de travail
TYPE_TACHE = "Réparation"

S_RECEPTION = "Réception en cours"
S_RECEPTIONNEE = "Réceptionnée"           # clôturée mais SANS tâche (personne à affecter)
S_PLANIFIEE = "Planifiée"
S_REPAREE = "Réparée"                     # = à rendre
S_PRETE = "Prête au magasin"              # le client vient la chercher
S_LIVRAISON = "Livraison planifiée"       # une tâche Livraison la ramène
S_RENDUE = "Rendue au client"
STATUTS = [S_RECEPTION, S_RECEPTIONNEE, S_PLANIFIEE, S_REPAREE, S_PRETE, S_LIVRAISON, S_RENDUE]
EN_RESTITUTION = (S_PRETE, S_LIVRAISON)

CHAMPS_PHOTO = {"arrivee": "photo_arrivee", "post_it": "photo_post_it", "remise": "photo_remise"}
TYPE_LIVRAISON = "Livraison"
DUREE_LIVRAISON = 30
SMS_PRET = ("Bonjour {nom}, votre appareil ({ref}) est repare et vous attend au magasin Aquaworld (Soukra).{garantie} "
            "Pour toute question : {tel}.")
SMS_LIVRAISON = ("Bonjour {nom}, votre appareil ({ref}) est repare : nous vous le livrons le {date} ({employe}).{garantie} "
                 "Pour toute question : {tel}.")
GARANTIES = ("Sous garantie", "Hors garantie")
MOIS_HISTORIQUE = 13                     # la dernière année + 1 mois (décision 01/10/2026)
NOTE_GARANTIE = "🆓 SOUS GARANTIE — réparation GRATUITE, ne rien facturer au client"
MARQUE_CONTROLE_INDISPONIBLE = "indisponible"
TYPES_ABSENCE = ("Congé", "Jour de récupération")
CRENEAUX_DEFAUT = ["09:00", "10:15", "11:30"]
DUREE_DEFAUT = 75                        # 1 h 15 par réparation (décision 01/10/2026)
MACHINES_PAR_JOUR_DEFAUT = 3
HORIZON_DEFAUT = 30


# ── Accès ────────────────────────────────────────────────────────────────────

def _lecture():
    frappe.has_permission(DOCTYPE, "read", throw=True)


def _ecriture(nom=None):
    frappe.has_permission(DOCTYPE, "write", doc=nom, throw=True)


# ── Configuration ────────────────────────────────────────────────────────────

def config() -> dict:
    """Les réglages lus du single, avec des défauts sûrs quand rien n'est enregistré."""
    cfg = frappe.get_cached_doc(DOCTYPE_CONFIG) if frappe.db.exists("DocType", DOCTYPE_CONFIG) else None
    responsables = [r.employee for r in (cfg.get("responsables") if cfg else []) or [] if r.employee]
    creneaux = [c.strip() for c in ((cfg and cfg.get("creneaux")) or "").split(",") if c.strip()] or CRENEAUX_DEFAUT
    return {
        "responsables": responsables,
        "machines_par_jour": cint(cfg and cfg.get("machines_par_jour")) or MACHINES_PAR_JOUR_DEFAUT,
        "creneaux": creneaux,
        "horizon_jours": cint(cfg and cfg.get("horizon_jours")) or HORIZON_DEFAUT,
        "duree_minutes": cint(cfg and cfg.get("duree_minutes")) or DUREE_DEFAUT,
    }


# ── Disponibilité ────────────────────────────────────────────────────────────

def _ferie(employee: str, jour) -> bool:
    """Férié ou repos hebdomadaire selon le calendrier de l'employé (HRMS)."""
    try:
        from hrms.hr.utils import get_holidays_for_employee
        return bool(get_holidays_for_employee(employee, jour, jour, raise_exception=False))
    except Exception:
        return getdate(jour).weekday() == 6   # sans HRMS : le dimanche seulement


def _en_conge(employee: str, jour) -> bool:
    if frappe.db.exists("Leave Application", {"employee": employee, "docstatus": 1, "status": "Approved",
                                              "from_date": ["<=", jour], "to_date": [">=", jour]}):
        return True
    return bool(frappe.db.exists(DOCTYPE_TACHE, {
        "custom_choix_du_staff": employee, "status": ["!=", "Cancelled"],
        "custom_type_dintervention": ["in", TYPES_ABSENCE],
        "starts_on": ["<", add_days(jour, 1)], "ends_on": [">=", f"{jour} 00:00:00"]}))


def disponible(employee: str, jour) -> bool:
    jour = getdate(jour)
    if frappe.db.get_value("Employee", employee, "status") != "Active":
        return False
    return not _ferie(employee, jour) and not _en_conge(employee, jour)


def taches_atelier_du_jour(jour) -> list[dict]:
    """Les tâches de réparation ATELIER (liées à un dossier) posées ce jour-là, hors annulées."""
    jour = getdate(jour)
    return frappe.get_all(DOCTYPE_TACHE, fields=["name", "custom_choix_du_staff", "starts_on", CHAMP_TACHE],
                          filters={CHAMP_TACHE: ["is", "set"], "status": ["!=", "Cancelled"],
                                   "starts_on": ["between", [f"{jour} 00:00:00", f"{jour} 23:59:59"]]},
                          order_by="starts_on")


def choisir_creneau(a_partir, cfg=None, est_disponible=disponible, taches_du_jour=taches_atelier_du_jour):
    """Le premier (jour, responsable, rang) qui respecte la règle, à partir de `a_partir` inclus.

    Règle : un seul opérateur par jour — s'il y a déjà des tâches atelier ce jour-là, c'est
    LEUR employé qui reçoit la machine suivante, tant que le plafond n'est pas atteint. Sinon
    le premier responsable disponible dans l'ordre configuré. Jour plein ou personne → lendemain.

    `est_disponible` et `taches_du_jour` sont injectables : la règle se teste sans base.
    Retourne None quand l'horizon est épuisé ou qu'aucun responsable n'est configuré.
    """
    cfg = cfg or config()
    if not cfg["responsables"]:
        return None
    jour = getdate(a_partir)
    for _i in range(cfg["horizon_jours"]):
        du_jour = taches_du_jour(jour)
        if du_jour:
            operateur = du_jour[0]["custom_choix_du_staff"]
            if len(du_jour) < cfg["machines_par_jour"] and operateur in cfg["responsables"]:
                return {"jour": jour, "employee": operateur, "rang": len(du_jour)}
        else:
            for emp in cfg["responsables"]:
                if est_disponible(emp, jour):
                    return {"jour": jour, "employee": emp, "rang": 0}
        jour = add_days(jour, 1)
    return None


# ── Lecture pour l'écran ─────────────────────────────────────────────────────

def _taches_par_machine(noms: list[str]) -> dict[str, list[dict]]:
    if not noms:
        return {}
    rows = frappe.get_all(DOCTYPE_TACHE, filters={CHAMP_TACHE: ["in", noms]},
                          fields=["name", CHAMP_TACHE, "status", "custom_choix_du_staff", "custom_employé",
                                  "starts_on", "custom_type_dintervention", "rapport_visite"],
                          order_by="starts_on")
    noms_emp = {r.custom_choix_du_staff for r in rows if r.custom_choix_du_staff}
    libelles = dict(frappe.get_all("Employee", filters={"name": ["in", list(noms_emp)]},
                                   fields=["name", "employee_name"], as_list=True)) if noms_emp else {}
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r[CHAMP_TACHE], []).append({
            "name": r.name, "status": r.status, "type": r.custom_type_dintervention,
            "employee": r.custom_choix_du_staff,
            "employe": libelles.get(r.custom_choix_du_staff) or r.get("custom_employé") or "",
            "starts_on": r.starts_on, "rapport": r.rapport_visite,
        })
    return out


@frappe.whitelist()
def get_data(statut=None, client=None, responsable=None, recherche=None, inclure_rendues=0):
    _lecture()
    filtres = {}
    if statut:
        filtres["statut"] = statut
    elif not cint(inclure_rendues):
        filtres["statut"] = ["!=", S_RENDUE]
    if client:
        filtres["client"] = client
    if responsable:
        filtres["responsable"] = responsable
    or_filtres = None
    if recherche:
        motif = f"%{recherche}%"
        or_filtres = [["name", "like", motif], ["nom_client", "like", motif], ["tel", "like", motif]]
    machines = frappe.get_all(DOCTYPE, filters=filtres, or_filters=or_filtres, limit_page_length=500,
                              fields=["name", "client", "nom_client", "tel", "statut", "date_reception", "note_reception", "photo_arrivee",
                                      "photo_post_it", "dispense_post_it", "date_cloture_reception",
                                      "controle_ia_ok", "controle_ia_resultat", "garantie", "commande_garantie", "responsable", "nom_responsable", "date_prevue", "date_reparee",
                                      "date_rendue", "commande_client", "modified", "mode_restitution", "sms_pret_le", "tache_livraison",
                                      "date_livraison_prevue", "rendu_a", "photo_remise", "remarque_restitution"],
                              order_by="creation desc")
    taches = _taches_par_machine([m.name for m in machines])
    aujourdhui = getdate(nowdate())
    for m in machines:
        m["taches"] = taches.get(m.name, [])
        ref = getdate(m.date_reparee) if m.date_reparee else getdate(m.date_reception or m.modified)
        m["age_jours"] = (aujourdhui - ref).days
    compteurs = dict(frappe.get_all(DOCTYPE, fields=["statut", "count(name) as n"], group_by="statut", as_list=True))
    cfg = config()
    return {
        "machines": machines,
        "kpis": {s: cint(compteurs.get(s)) for s in STATUTS},
        "statuts": STATUTS,
        "responsables": [{"name": e, "employee_name": frappe.db.get_value("Employee", e, "employee_name")}
                         for e in cfg["responsables"]],
        "config_ok": bool(cfg["responsables"]),
        "peut_configurer": "System Manager" in frappe.get_roles(),
    }


# ── Commandes du client (garantie) ───────────────────────────────────────────

def _articles_garantis(codes: set) -> set:
    """Les codes dont le groupe (ou un ancêtre) est coché « Sous garantie » — même règle que la
    mention du BL (jinja_methods.bl_sous_garantie)."""
    if not codes or not frappe.db.has_column("Item Group", "custom_sous_garantie"):
        return set()
    return {r[0] for r in frappe.db.sql("""
        SELECT DISTINCT i.name FROM `tabItem` i
        JOIN `tabItem Group` g ON g.name = i.item_group
        JOIN `tabItem Group` p ON g.lft >= p.lft AND g.rgt <= p.rgt
        WHERE i.name IN %(codes)s AND p.custom_sous_garantie = 1""", {"codes": tuple(codes)})}


@frappe.whitelist()
def commandes_client(client, mois=MOIS_HISTORIQUE):
    """Les commandes validées du client sur les N derniers mois, avec leurs articles (nom, image,
    composants de bundle) et un drapeau « appareil garanti » par article : c'est au vu de cette
    liste que l'accueil décide si la réparation est sous garantie."""
    _lecture()
    frappe.has_permission("Sales Order", "read", throw=True)
    depuis = frappe.utils.add_months(getdate(nowdate()), -cint(mois))
    commandes = frappe.get_all("Sales Order", filters={"customer": client, "docstatus": 1,
                                                        "transaction_date": [">=", depuis]},
                               fields=["name", "transaction_date", "delivery_date", "status", "grand_total",
                                       "per_delivered"], order_by="transaction_date desc", limit_page_length=50)
    if not commandes:
        return {"depuis": str(depuis), "commandes": []}
    noms = [c.name for c in commandes]
    lignes = frappe.get_all("Sales Order Item", filters={"parent": ["in", noms]}, order_by="parent, idx",
                            fields=["parent", "item_code", "item_name", "qty", "image"])
    packed = frappe.get_all("Packed Item", filters={"parent": ["in", noms], "parenttype": "Sales Order"},
                            fields=["parent", "item_code", "item_name", "qty"], order_by="parent, idx")
    codes = {l.item_code for l in lignes + packed if l.item_code}
    images = dict(frappe.get_all("Item", filters={"name": ["in", list(codes)]}, fields=["name", "image"], as_list=True)) if codes else {}
    garantis = _articles_garantis(codes)
    par_commande: dict[str, list] = {}
    for l in lignes:
        par_commande.setdefault(l.parent, []).append({
            "item_code": l.item_code, "item_name": l.item_name, "qty": l.qty,
            "image": l.image or images.get(l.item_code), "garanti": l.item_code in garantis, "composant": 0})
    for l in packed:
        par_commande.setdefault(l.parent, []).append({
            "item_code": l.item_code, "item_name": l.item_name, "qty": l.qty,
            "image": images.get(l.item_code), "garanti": l.item_code in garantis, "composant": 1})
    for c in commandes:
        c["articles"] = par_commande.get(c.name, [])
        c["garanti"] = any(a["garanti"] for a in c["articles"])
    return {"depuis": str(depuis), "commandes": commandes}


# ── Réception ────────────────────────────────────────────────────────────────

@frappe.whitelist()
def _verifier_garantie(garantie, commande_garantie, client):
    if garantie not in GARANTIES:
        frappe.throw(_("Choisissez : sous garantie ou hors garantie."))
    if commande_garantie and frappe.db.get_value("Sales Order", commande_garantie, "customer") != client:
        frappe.throw(_("La commande {0} n'est pas celle de ce client.").format(commande_garantie))


@frappe.whitelist()
def creer_reception(client, note=None, tel=None, garantie=None, commande_garantie=None):
    """Étape 1 : le dossier naît tout de suite — il faut un numéro pour le post-it, et un
    document pour y attacher les photos. La garantie se décide ICI, au vu des commandes."""
    frappe.has_permission(DOCTYPE, "create", throw=True)
    if not frappe.db.exists("Customer", client):
        frappe.throw(_("Client introuvable : {0}").format(client))
    _verifier_garantie(garantie, commande_garantie, client)
    doc = frappe.get_doc({
        "doctype": DOCTYPE, "client": client, "tel": tel, "note_reception": note, "statut": S_RECEPTION,
        "date_reception": now_datetime(), "garantie": garantie, "commande_garantie": commande_garantie or None,
    }).insert()
    return {"name": doc.name, "nom_client": doc.nom_client, "tel": doc.tel}


@frappe.whitelist()
def enregistrer_photo(machine, champ, file_url):
    """Pose l'URL d'une photo déjà téléversée (FileUploader côté client) sur le dossier."""
    _ecriture(machine)
    if champ not in CHAMPS_PHOTO:
        frappe.throw(_("Photo inconnue : {0}").format(champ))
    if not (file_url or "").startswith(("/files/", "/private/files/")):
        frappe.throw(_("Fichier invalide."))
    frappe.db.set_value(DOCTYPE, machine, CHAMPS_PHOTO[champ], file_url)
    return etat_reception(machine)


@frappe.whitelist()
def etat_reception(machine):
    _lecture()
    m = frappe.db.get_value(DOCTYPE, machine, ["name", "client", "nom_client", "tel", "statut", "photo_arrivee",
                                               "photo_post_it", "dispense_post_it", "note_reception"], as_dict=True)
    if not m:
        frappe.throw(_("Dossier introuvable."))
    m["peut_cloturer"] = bool(m.photo_arrivee) and bool(m.photo_post_it or m.dispense_post_it)
    m["post_it"] = texte_post_it(m)
    return m


def texte_post_it(m) -> dict:
    """Ce qui doit être écrit sur le post-it : nom du client, son téléphone, la référence."""
    tel = (m.get("tel") or "").strip()
    if not tel and m.get("client"):
        from customization_app.customize_erpnext.doctype.machine_reparation.machine_reparation import telephone_client
        tel = telephone_client(m.get("client"))
    return {"nom": m.get("nom_client") or "", "tel": tel, "ref": m.get("name")}


def _verifier_code(code: str) -> bool:
    """Le code superviseur de Config Cloture Tache — le même que pour les tâches."""
    from frappe.utils.password import get_decrypted_password
    attendu = get_decrypted_password("Config Cloture Tache", "Config Cloture Tache",
                                     "code_deverrouillage", raise_exception=False)
    if not attendu:
        frappe.throw(_("Aucun code superviseur n'est configuré (Config Cloture Tache)."))
    return hmac.compare_digest(str(code or ""), str(attendu))


@frappe.whitelist()
def enregistrer_details(machine, note=None, tel=None, commande_client=None, garantie=None, commande_garantie=None):
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if garantie:
        _verifier_garantie(garantie, commande_garantie, doc.client)
        doc.update({"garantie": garantie, "commande_garantie": commande_garantie or None})
    doc.update({"note_reception": note, "tel": tel, "commande_client": commande_client or None})
    doc.save()
    return {"ok": True}


@frappe.whitelist()
def cloturer_reception(machine, code=None):
    """Étape 2 : clôture sur la photo post-it OU le code superviseur, puis affectation."""
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut != S_RECEPTION:
        frappe.throw(_("Ce dossier n'est plus en réception ({0}).").format(doc.statut))
    if not doc.photo_arrivee:
        frappe.throw(_("La photo de la machine à l'arrivée est obligatoire."))
    if doc.garantie not in GARANTIES:
        frappe.throw(_("Décidez d'abord si la réparation est sous garantie (fiche du dossier)."))
    avertissement = None
    if code:
        if not _verifier_code(code):
            frappe.log_error("Code superviseur refusé — dossier %s, utilisateur %s"
                             % (machine, frappe.session.user), "reparation_osmoseur")
            frappe.throw(_("Code incorrect."))
        if not doc.photo_post_it:
            doc.dispense_post_it = 1
            doc.add_comment("Info", _("🔓 Réception clôturée sans photo post-it par {0} (code superviseur).")
                            .format(frappe.session.user))
        else:
            doc.add_comment("Info", _("🔓 Contrôle IA des photos passé outre par {0} (code superviseur).")
                            .format(frappe.session.user))
    elif not doc.photo_post_it:
        frappe.throw(_("Photo de la machine avec son post-it manquante (ou code superviseur)."))
    else:
        # Le post-it doit être JUSTE (nom, téléphone, référence) et la machine la MÊME que
        # celle de la photo d'arrivée : c'est l'IA qui relit les deux photos. Un service
        # indisponible ne bloque pas l'atelier — il est signalé et tracé sur le dossier.
        verdict = controler_photos(machine, doc=doc)
        if verdict.get("indisponible"):
            avertissement = verdict["message"]
        elif not verdict["ok"]:
            frappe.throw(_("Clôture refusée par le contrôle des photos :<br>• {0}<br><br>"
                           "Reprenez la photo, ou passez outre avec le code superviseur.")
                         .format("<br>• ".join(verdict["problemes"])), title=_("Photos non conformes"))
    doc.statut = S_RECEPTIONNEE
    doc.date_cloture_reception = now_datetime()
    doc.save()
    affectation = planifier(machine)
    return {"name": doc.name, "statut": frappe.db.get_value(DOCTYPE, machine, "statut"),
            "avertissement": avertissement, **affectation}


# ── Contrôle IA des photos ───────────────────────────────────────────────────

def _contenu_image(url: str):
    """(octets, mime) d'une photo du dossier, par le DocType File — seul chemin qui lise un
    fichier PRIVÉ (les photos téléversées le sont) et qui résiste à un déplacement du dossier."""
    nom = frappe.db.get_value("File", {"file_url": url}, "name")
    if not nom:
        return None, None
    contenu = frappe.get_doc("File", nom).get_content()
    if not contenu:
        return None, None
    if contenu[:8] == b"\x89PNG\r\n\x1a\n":
        mime = "image/png"
    elif contenu[:4] == b"RIFF" and contenu[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = "image/jpeg"
    return contenu, mime


_INSTRUCTIONS_IA = (
    "Tu contrôles la réception d'un appareil de traitement d'eau (osmoseur, adoucisseur, filtre) "
    "dans un atelier de réparation en Tunisie. On te donne DEUX photos : la photo 1 est l'appareil "
    "à son arrivée ; la photo 2 doit montrer LE MÊME appareil avec un post-it manuscrit collé "
    "dessus. Rends STRICTEMENT un objet JSON, sans texte autour : "
    '{"post_it_visible": <true si un post-it (papier collé avec écriture) est visible sur la photo 2>, '
    '"post_it_texte": "<tout le texte lu sur le post-it, tel quel, ou null>", '
    '"reference": "<la référence de dossier lue sur le post-it, de la forme OSM-AAAA-NNNN, ou null>", '
    '"telephone": "<le numéro de téléphone lu sur le post-it (chiffres), ou null>", '
    '"nom": "<le nom de client lu sur le post-it, ou null>", '
    '"meme_appareil": <true si les deux photos montrent le même appareil (même type, même forme, '
    "même couleur, mêmes détails visibles), false si ce sont des appareils différents ou si la photo "
    "2 ne montre pas d'appareil>, "
    '"appareil_commentaire": "<une phrase sur la ressemblance ou la différence>"}. '
    "Ne devine pas : si une information n'est pas lisible, mets null."
)


def _lire_photos_ia(arrivee_url: str, post_it_url: str) -> dict:
    """La lecture brute du modèle sur les deux photos. Le modèle LIT, il ne juge pas la
    concordance avec le dossier : la comparaison se fait ici (un modèle à qui l'on demande
    « est-ce bien OSM-2026-0001 ? » a tendance à dire oui)."""
    images = []
    for url in (arrivee_url, post_it_url):
        contenu, mime = _contenu_image(url)
        if not contenu:
            raise FileNotFoundError(url)
        images.append({"type": "input_image",
                       "image_url": "data:%s;base64,%s" % (mime, base64.b64encode(contenu).decode())})
    client, model = _client_ia()
    res = client.responses.create(
        model=model, instructions=_INSTRUCTIONS_IA,
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": "Photo 1 : appareil à l'arrivée."}, images[0],
            {"type": "input_text", "text": "Photo 2 : le même appareil avec son post-it."}, images[1]]}])
    texte = (res.output_text or "").strip().strip("`")
    if texte.lower().startswith("json"):
        texte = texte.split("\n", 1)[1]
    lu = json.loads(texte)
    return lu if isinstance(lu, dict) else {}


def _client_ia():
    """Même plomberie OpenAI que la caisse et le planning (clé + modèle d'AI Settings)."""
    try:
        from bank_retenue_sync.ai.invoice_extract import _get_client_model_temp
        client, model, _t = _get_client_model_temp()
        return client, model
    except ImportError:
        from customization_app.liste_commande_import import _model, _openai_client
        return _openai_client(), _model()


LECTEUR_IA = _lire_photos_ia     # injectable (tests)


def _normaliser(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", str(texte or ""))
    return "".join(c for c in texte if not unicodedata.combining(c)).upper()


def _chiffres(texte) -> str:
    return re.sub(r"\D", "", str(texte or ""))


def verdict_controle(lu: dict, attendu: dict) -> dict:
    """Confronte la lecture du modèle à ce que le post-it DOIT porter → {ok, problemes}.

    FONCTION PURE ET TOLÉRANTE : le modèle est censé rendre un objet, rien ne l'y oblige.
    - référence : égalité stricte après normalisation (espaces, casse, tirets) — c'est LA donnée
      qui identifie la machine dans l'atelier ;
    - téléphone : un des numéros du dossier doit apparaître dans les chiffres lus (ou l'inverse) ;
    - nom : au moins un mot significatif (≥ 3 lettres) du nom du client, sans accents ni casse ;
    - appareil : le modèle doit voir le même appareil sur les deux photos.
    """
    lu = lu if isinstance(lu, dict) else {}
    problemes = []
    texte_lu = _normaliser(lu.get("post_it_texte"))
    if not lu.get("post_it_visible"):
        problemes.append(_("aucun post-it visible sur la photo 2"))
    ref_att = re.sub(r"[\s-]", "", _normaliser(attendu.get("ref")))
    ref_lue = re.sub(r"[\s-]", "", _normaliser(lu.get("reference")))
    if not ref_lue and ref_att and ref_att in re.sub(r"[\s-]", "", texte_lu):
        ref_lue = ref_att
    if ref_lue != ref_att:
        problemes.append(_("référence lue « {0} » au lieu de {1}").format(lu.get("reference") or "—", attendu.get("ref")))
    numeros = [n for n in (_chiffres(x) for x in re.split(r"[/,;\s]+", str(attendu.get("tel") or ""))) if len(n) >= 6]
    tel_lu = _chiffres(lu.get("telephone")) or _chiffres(texte_lu)
    if numeros and not any(n in tel_lu or (len(tel_lu) >= 6 and tel_lu in n) for n in numeros):
        problemes.append(_("téléphone lu « {0} » ≠ {1}").format(lu.get("telephone") or "—", attendu.get("tel")))
    mots = [m for m in re.findall(r"[A-Z0-9]{3,}", _normaliser(attendu.get("nom"))) if m not in ("STE", "SARL", "SUARL", "LES", "DES")]
    nom_lu = _normaliser(lu.get("nom")) + " " + texte_lu
    if mots and not any(m in nom_lu for m in mots):
        problemes.append(_("nom lu « {0} » ≠ {1}").format(lu.get("nom") or "—", attendu.get("nom")))
    if not lu.get("meme_appareil"):
        problemes.append(_("l'appareil de la photo 2 ne ressemble pas à celui réceptionné ({0})")
                         .format(lu.get("appareil_commentaire") or _("pas de justification")))
    return {"ok": not problemes, "problemes": problemes, "lecture": lu}


@frappe.whitelist()
def controler_photos(machine, doc=None):
    """Relit les deux photos du dossier et compare au post-it attendu. Résultat tracé sur la fiche.

    -> {ok, problemes, message, indisponible}. `indisponible` = le service ou les fichiers n'ont
    pas pu être lus : ce n'est pas un refus, c'est un contrôle qui n'a pas eu lieu."""
    _ecriture(machine)
    doc = doc or frappe.get_doc(DOCTYPE, machine)
    if not (doc.photo_arrivee and doc.photo_post_it):
        frappe.throw(_("Les deux photos sont nécessaires au contrôle."))
    attendu = texte_post_it(doc)
    try:
        lu = LECTEUR_IA(doc.photo_arrivee, doc.photo_post_it)
    except Exception:
        frappe.log_error(title="Réparation osmoseurs : contrôle IA indisponible", message=frappe.get_traceback())
        message = _("Contrôle automatique des photos impossible (service indisponible) : vérifiez le post-it à l'œil.")
        frappe.db.set_value(DOCTYPE, machine, {"controle_ia_ok": 0, "controle_ia_resultat": "⚠️ " + message},
                            update_modified=False)   # le doc en cours de clôture se sauve juste après
        doc.controle_ia_ok, doc.controle_ia_resultat = 0, "⚠️ " + message
        return {"ok": False, "indisponible": True, "problemes": [], "message": message}
    v = verdict_controle(lu, attendu)
    resume = (_("✅ Post-it et appareil conformes") if v["ok"] else "❌ " + " ; ".join(v["problemes"]))
    detail = lu.get("post_it_texte") or ""
    if lu.get("appareil_commentaire"):
        detail += ("\n" if detail else "") + "Appareil : " + lu["appareil_commentaire"]
    frappe.db.set_value(DOCTYPE, machine, {"controle_ia_ok": 1 if v["ok"] else 0,
                                           "controle_ia_resultat": resume + ("\n" + detail if detail else "")},
                        update_modified=False)
    doc.controle_ia_ok = 1 if v["ok"] else 0
    doc.controle_ia_resultat = frappe.db.get_value(DOCTYPE, machine, "controle_ia_resultat")
    v["message"] = resume
    v["indisponible"] = False
    return v


# ── Affectation ──────────────────────────────────────────────────────────────

def _creer_tache(doc, employee: str, jour, rang: int, cfg: dict) -> str:
    creneaux = cfg["creneaux"]
    heure = creneaux[min(rang, len(creneaux) - 1)]
    nom_emp = frappe.db.get_value("Employee", employee, "employee_name") or employee
    garantie = doc.get("garantie") == "Sous garantie"
    titre = "🧰 Réparation atelier: %s%s\nClient: %s\n%s" % (
        doc.name, " 🆓 GARANTIE" if garantie else "", doc.nom_client or doc.client, nom_emp)
    sujet = "Réparation osmoseur %s" % doc.name
    if garantie:
        # En tête du sujet : c'est ce que le technicien lit en ouvrant la tâche, avant de valider.
        sujet = NOTE_GARANTIE + (" (commande %s)" % doc.commande_garantie if doc.get("commande_garantie") else "") + "\n" + sujet
    if doc.note_reception:
        sujet += "\n" + doc.note_reception
    tache = frappe.get_doc({
        "doctype": DOCTYPE_TACHE, "custom_type_dintervention": TYPE_TACHE,
        "custom_choix_du_staff": employee, "custom_employé": nom_emp,
        "custom_client": doc.client, "nom_client": doc.nom_client, "tel": doc.tel,
        "starts_on": "%s %s:00" % (jour, heure), "titre": titre, "subject": sujet,
        "dans_local": "Oui", "status": "Open", CHAMP_TACHE: doc.name,
        "commande_client": doc.get("commande_garantie") if garantie else None,
    })
    tache.flags.ignore_permissions = True
    tache.insert()
    # before_save fixe la fin par type (Réparation = 2 h) : l'atelier, lui, compte 1 h 15 par machine.
    fin = frappe.utils.add_to_date(frappe.utils.get_datetime(tache.starts_on), minutes=cfg["duree_minutes"])
    frappe.db.set_value(DOCTYPE_TACHE, tache.name, "ends_on", fin, update_modified=False)
    return tache.name


@frappe.whitelist()
def planifier(machine, a_partir=None):
    """Crée la tâche de réparation du dossier selon la règle du lendemain.

    Idempotent : un dossier qui a déjà une tâche ouverte n'en reçoit pas une seconde.
    Sans responsable configuré ou sans jour libre dans l'horizon, le dossier reste
    « Réceptionnée » et l'écran le signale — rien n'est inventé.
    """
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut in (S_RECEPTION, S_RENDUE):
        frappe.throw(_("Le dossier {0} ne peut pas être planifié ({1}).").format(machine, doc.statut))
    ouverte = frappe.db.get_value(DOCTYPE_TACHE, {CHAMP_TACHE: machine, "status": "Open"}, "name")
    if ouverte:
        return {"tache": ouverte, "deja": True, "message": _("Une tâche est déjà ouverte : {0}").format(ouverte)}
    cfg = config()
    if not cfg["responsables"]:
        return {"tache": None, "message": _("Aucun responsable de réparation configuré "
                                            "(Config Reparation Osmoseur) : dossier à affecter à la main.")}
    debut = getdate(a_partir) if a_partir else add_days(getdate(nowdate()), 1)
    choix = choisir_creneau(debut, cfg)
    if not choix:
        return {"tache": None, "message": _("Aucun responsable disponible dans les {0} prochains jours.")
                                            .format(cfg["horizon_jours"])}
    nom = _creer_tache(doc, choix["employee"], choix["jour"], choix["rang"], cfg)
    synchroniser(machine)
    return {"tache": nom, "employee": choix["employee"], "jour": str(choix["jour"]),
            "employe": frappe.db.get_value("Employee", choix["employee"], "employee_name"),
            "message": _("Tâche {0} créée pour le {1}").format(nom, frappe.format(choix["jour"], {"fieldtype": "Date"}))}


@frappe.whitelist()
def affecter_tache(machine, employee, date, heure="09:00"):
    """Affectation À LA MAIN d'une tâche supplémentaire (ou de remplacement)."""
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut == S_RECEPTION:
        frappe.throw(_("Clôturez d'abord la réception du dossier {0}.").format(machine))
    if not frappe.db.exists("Employee", {"name": employee, "status": "Active"}):
        frappe.throw(_("Employé inconnu ou inactif."))
    cfg = dict(config(), creneaux=[heure or "09:00"])
    nom = _creer_tache(doc, employee, getdate(date), 0, cfg)
    synchroniser(machine)
    return {"tache": nom}


# ── Cycle de vie ─────────────────────────────────────────────────────────────

def synchroniser(machine: str) -> str:
    """Recalcule le statut du dossier depuis ses tâches. Ne redescend jamais une machine rendue."""
    courant = frappe.db.get_value(DOCTYPE, machine, "statut")
    if not courant or courant in (S_RECEPTION, S_RENDUE):
        return courant
    if courant in EN_RESTITUTION:
        return _synchroniser_livraison(machine, courant)
    # Les tâches de RÉPARATION seulement : une livraison de retour ne dit rien de l'état de la réparation.
    taches = frappe.get_all(DOCTYPE_TACHE, filters={CHAMP_TACHE: machine, "status": ["!=", "Cancelled"],
                                                    "custom_type_dintervention": ["!=", TYPE_LIVRAISON]},
                            fields=["name", "status", "custom_choix_du_staff", "starts_on"], order_by="starts_on")
    maj = {}
    if not taches:
        nouveau = S_RECEPTIONNEE
        maj.update({"responsable": None, "date_prevue": None, "date_reparee": None})
    elif all(t.status == "Completed" for t in taches):
        nouveau = S_REPAREE
        derniere = taches[-1]
        maj.update({"responsable": derniere.custom_choix_du_staff, "date_prevue": getdate(derniere.starts_on)})
        if courant != S_REPAREE:
            maj["date_reparee"] = now_datetime()
    else:
        nouveau = S_PLANIFIEE
        prochaine = next(t for t in taches if t.status == "Open")
        maj.update({"responsable": prochaine.custom_choix_du_staff, "date_prevue": getdate(prochaine.starts_on),
                    "date_reparee": None})
    maj["statut"] = nouveau
    frappe.db.set_value(DOCTYPE, machine, maj)
    return nouveau


def on_tache_change(doc, method=None):
    """Hook on_update / after_delete de la Tache de travail : la clôture valide le dossier."""
    machine = doc.get(CHAMP_TACHE)
    if not machine:
        return
    try:
        synchroniser(machine)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "reparation_osmoseur.on_tache_change")


def _synchroniser_livraison(machine: str, courant: str) -> str:
    """Dossier en restitution par livraison : la tâche Livraison clôturée rend la machine ; disparue ou
    annulée, le dossier revient « Réparée » (à rendre). « Prête au magasin » ne bouge pas tout seul."""
    tache = frappe.db.get_value(DOCTYPE, machine, "tache_livraison")
    if courant != S_LIVRAISON or not tache:
        return courant
    etat = frappe.db.get_value(DOCTYPE_TACHE, tache, ["status", "custom_choix_du_staff"], as_dict=True)
    if etat and etat.status == "Completed":
        nom = frappe.db.get_value("Employee", etat.custom_choix_du_staff, "employee_name") or etat.custom_choix_du_staff or ""
        frappe.db.set_value(DOCTYPE, machine, {"statut": S_RENDUE, "date_rendue": now_datetime(),
                                               "rendu_a": _("livrée par {0}").format(nom)[:140]})
        return S_RENDUE
    if not etat or etat.status == "Cancelled":
        frappe.db.set_value(DOCTYPE, machine, {"statut": S_REPAREE, "tache_livraison": None, "date_livraison_prevue": None,
                                               "mode_restitution": None})
        return S_REPAREE
    return courant


def _sms_simule() -> bool:
    """Le dev porte les vrais numéros : en developer_mode on SIMULE, sauf `sms_reel_en_dev` dans site_config."""
    return bool(cint(frappe.conf.get("developer_mode"))) and not cint(frappe.conf.get("sms_reel_en_dev"))


def _envoyer_sms(doc, texte: str, libelle: str) -> dict:
    """Un SMS au(x) numéro(s) du dossier, tracé en commentaire ; simulé en dev."""
    from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import _send_sms_with_fallback, traiter_numero_tel
    from customization_app.sms_annulation import sans_accents

    numeros = traiter_numero_tel(doc.tel or "")
    if not numeros:
        frappe.throw(_("Aucun numéro mobile tunisien valide sur le dossier ({0}).").format(doc.tel or "—"))
    texte = sans_accents(texte)
    simule = _sms_simule()
    if not simule:
        _send_sms_with_fallback(["216%s" % n for n in numeros], texte)
    doc.db_set("sms_pret_le", now_datetime(), update_modified=False)
    doc.add_comment("Comment", _("📲 SMS « {0} » {1} à {2} : {3}")
                    .format(libelle, _("SIMULÉ (dev)") if simule else _("envoyé"), ", ".join(numeros), texte))
    return {"numeros": numeros, "simule": simule, "texte": texte}


@frappe.whitelist()
def envoyer_sms_pret(machine):
    """SMS « votre appareil est réparé, à retirer au magasin » au(x) numéro(s) du dossier."""
    from customization_app.sms_annulation import telephone_contact

    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut not in (S_REPAREE, S_PRETE):
        frappe.throw(_("Le SMS « machine prête » ne s'envoie que pour une machine réparée ({0}).").format(doc.statut))
    return _envoyer_sms(doc, SMS_PRET.format(nom=doc.nom_client or doc.client, ref=doc.name, tel=telephone_contact(),
                                             garantie=" Reparation hors garantie : a regler au retrait." if doc.garantie == "Hors garantie" else ""),
                        "machine prête")


@frappe.whitelist()
def restituer_magasin(machine, sms=0):
    """Le client viendra la chercher : « Prête au magasin », SMS proposé."""
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut != S_REPAREE:
        frappe.throw(_("Seule une machine réparée se met en attente au magasin ({0}).").format(doc.statut))
    doc.statut, doc.mode_restitution = S_PRETE, "Retrait au magasin"
    doc.save()
    out = {"statut": doc.statut, "sms": None}
    if cint(sms):
        out["sms"] = envoyer_sms_pret(machine)
    return out


@frappe.whitelist()
def adresses_client(client):
    """Les adresses du client, pour choisir où livrer."""
    _lecture()
    noms = frappe.get_all("Dynamic Link", filters={"link_doctype": "Customer", "link_name": client, "parenttype": "Address"},
                          pluck="parent", distinct=True)
    out = []
    for a in frappe.get_all("Address", filters={"name": ["in", noms or [""]], "disabled": 0},
                            fields=["name", "address_title", "address_line1", "address_line2", "city", "custom_secteur",
                                    "custom_lien_google_map", "is_shipping_address", "is_primary_address"]):
        a["libelle"] = ", ".join(x for x in (a.address_line1, a.address_line2, a.city) if x)
        out.append(a)
    out.sort(key=lambda a: (not a.is_shipping_address, not a.is_primary_address, a.name))
    return out


@frappe.whitelist()
def planifier_livraison(machine, employee, date, heure="09:00", adresse=None, note=None, sms=0):
    """Une tâche Livraison au calendrier ramène la machine chez le client ; sa clôture rend le dossier.
    `sms` : prévenir le client (date et livreur)."""
    from customization_app.sms_annulation import telephone_contact

    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut not in (S_REPAREE, S_PRETE):
        frappe.throw(_("Seule une machine réparée se livre ({0}).").format(doc.statut))
    if not frappe.db.exists("Employee", {"name": employee, "status": "Active"}):
        frappe.throw(_("Employé inconnu ou inactif : {0}").format(employee))
    adresses = adresses_client(doc.client)
    a = next((x for x in adresses if x.name == adresse), None) if adresse else (adresses[0] if adresses else None)
    nom_emp = frappe.db.get_value("Employee", employee, "employee_name") or employee
    garantie = doc.garantie == "Sous garantie"
    sujet = "Retour de l'osmoseur réparé %s%s" % (doc.name, " — " + NOTE_GARANTIE if garantie else " — hors garantie : encaisser la réparation à la livraison")
    if note:
        sujet += "\n" + note.strip()[:300]
    tache = frappe.get_doc({
        "doctype": DOCTYPE_TACHE, "custom_type_dintervention": TYPE_LIVRAISON,
        "custom_choix_du_staff": employee, "custom_employé": nom_emp,
        "custom_client": doc.client, "nom_client": doc.nom_client, "tel": doc.tel,
        "starts_on": "%s %s:00" % (getdate(date), str(heure or "09:00")[:5]),
        "titre": "%s\n🚚 Livraison: Client: %s\n%s" % ((a.custom_secteur if a and a.custom_secteur else ""), doc.nom_client or doc.client, nom_emp),
        "subject": sujet, "temps": "30 min", "status": "Open", CHAMP_TACHE: doc.name,
        "select_address": a.name if a else None, "details_adresse": (a.libelle if a else "")[:140],
        "secteur": a.custom_secteur if a else None, "google_map": (a.custom_lien_google_map if a else "") or "",
        "commande_client": doc.get("commande_garantie") if garantie else None,
    })
    tache.flags.ignore_permissions = True
    tache.flags.duree_fixee = True
    tache.insert()
    fin = frappe.utils.add_to_date(frappe.utils.get_datetime(tache.starts_on), minutes=DUREE_LIVRAISON)
    frappe.db.set_value(DOCTYPE_TACHE, tache.name, "ends_on", fin, update_modified=False)
    doc.reload()
    doc.statut, doc.mode_restitution = S_LIVRAISON, "Livraison"
    doc.tache_livraison, doc.date_livraison_prevue = tache.name, getdate(date)
    doc.save()
    out = {"statut": doc.statut, "tache": tache.name, "employe": nom_emp, "date": str(getdate(date)),
           "adresse": a.libelle if a else None, "sms": None}
    if cint(sms):
        out["sms"] = _envoyer_sms(doc, SMS_LIVRAISON.format(
            nom=doc.nom_client or doc.client, ref=doc.name, date=frappe.utils.formatdate(getdate(date), "dd/MM/yyyy"),
            employe=nom_emp, tel=telephone_contact(),
            garantie=" Reparation hors garantie : a regler a la livraison." if doc.garantie == "Hors garantie" else ""), "livraison")
    return out


@frappe.whitelist()
def rendre(machine, rendu_a=None, remarque=None):
    """Remise en main propre (au magasin, ou directement depuis « Réparée ») : la preuve de restitution."""
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut not in (S_REPAREE, S_PRETE):
        frappe.throw(_("Seule une machine réparée peut être rendue ({0}).").format(doc.statut))
    doc.statut = S_RENDUE
    doc.date_rendue = now_datetime()
    doc.mode_restitution = doc.mode_restitution or "Retrait au magasin"
    if rendu_a:
        doc.rendu_a = rendu_a.strip()[:140]
    if remarque:
        doc.remarque_restitution = remarque.strip()[:500]
    doc.save()
    return {"statut": doc.statut}


@frappe.whitelist()
def annuler_restitution(machine):
    """Retour à « Réparée » (à rendre) : la tâche de livraison encore ouverte est supprimée."""
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut not in EN_RESTITUTION:
        frappe.throw(_("Le dossier {0} n'est pas en restitution ({1}).").format(machine, doc.statut))
    if doc.tache_livraison and frappe.db.exists(DOCTYPE_TACHE, doc.tache_livraison):
        if frappe.db.get_value(DOCTYPE_TACHE, doc.tache_livraison, "status") == "Completed":
            frappe.throw(_("La livraison {0} est déjà clôturée.").format(doc.tache_livraison))
        frappe.delete_doc(DOCTYPE_TACHE, doc.tache_livraison, ignore_permissions=True, force=True)
    doc.reload()
    doc.statut, doc.mode_restitution, doc.tache_livraison, doc.date_livraison_prevue = S_REPAREE, None, None, None
    doc.save()
    return {"statut": doc.statut}


@frappe.whitelist()
def rouvrir_reception(machine):
    """Une réception clôturée par erreur, sans tâche encore faite, revient en réception."""
    _ecriture(machine)
    doc = frappe.get_doc(DOCTYPE, machine)
    if doc.statut not in (S_RECEPTIONNEE, S_PLANIFIEE):
        frappe.throw(_("Impossible de rouvrir un dossier {0}.").format(doc.statut))
    # Le refus d'une clôture par le contrôle IA ne laisse rien à nettoyer ; une réouverture, si.
    if frappe.db.exists(DOCTYPE_TACHE, {CHAMP_TACHE: machine, "status": "Completed"}):
        frappe.throw(_("Une tâche de réparation est déjà clôturée sur ce dossier."))
    # Les tâches ouvertes sont SUPPRIMÉES, pas annulées (décision 01/10/2026) : une tâche annulée
    # resterait au calendrier du technicien et dans ses statistiques alors qu'elle n'a jamais existé.
    for t in frappe.get_all(DOCTYPE_TACHE, filters={CHAMP_TACHE: machine, "status": "Open"}, pluck="name"):
        frappe.delete_doc(DOCTYPE_TACHE, t, ignore_permissions=True, force=True)
    doc.reload()
    doc.statut = S_RECEPTION
    doc.date_cloture_reception = None
    doc.responsable = None
    doc.date_prevue = None
    doc.save()
    return {"statut": doc.statut}
