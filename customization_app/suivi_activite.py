"""Suivi d'activité des employés — onglet « Suivi d'activité », page /app/suivi-activite.

Demande du 01/10/2026 : définir les activités à faire par employé, y joindre photos et
documents, une date de début et une date prévisionnelle, les améliorer par l'IA ; un onglet
réservé aux employés paramétrés (Config Suivi Activite), où chacun ne voit que SES activités.

Une activité est affectée à UN OU PLUSIEURS employés (table `affectations`) : c'est une seule
fiche partagée, et chaque employé affecté la voit dans sa liste.

Accès :
- rôle « Suivi Activité » (donné par le réglage) : les activités où il est affecté, création
  pour soi-même, pas de changement d'affectation ni de suppression ;
- rôle « Responsable Activité » (case Responsable du réglage) ou System Manager : tout voir,
  affecter à n'importe quel employé paramétré, supprimer.
La restriction est posée au niveau du DocType (permission_query_conditions + has_permission
dans hooks.py) : la vue liste, les rapports, les pièces jointes et l'API la respectent aussi,
pas seulement l'écran.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, now_datetime, nowdate

DOCTYPE = "Activite Employe"
DOCTYPE_CONFIG = "Config Suivi Activite"
ROLE_EMPLOYE = "Suivi Activité"
ROLE_RESPONSABLE = "Responsable Activité"
STATUTS = ["À faire", "En cours", "En attente", "Terminée", "Annulée"]
PRIORITES = ["Basse", "Normale", "Haute", "Urgente"]
FERMES = ("Terminée", "Annulée")
EXTENSIONS_PHOTO = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp")


# ── Qui est qui ──────────────────────────────────────────────────────────────

def est_responsable(user: str | None = None) -> bool:
    user = user or frappe.session.user
    if user == "Administrator":
        return True
    roles = frappe.get_roles(user)
    return "System Manager" in roles or ROLE_RESPONSABLE in roles


def employe_de(user: str | None = None) -> str | None:
    return frappe.db.get_value("Employee", {"user_id": user or frappe.session.user}, "name")


def employes_parametres() -> list[dict]:
    """Les employés du réglage, dans l'ordre de la liste."""
    if not frappe.db.exists("DocType", DOCTYPE_CONFIG):
        return []
    rows = frappe.get_all("Config Suivi Activite Employe", filters={"parent": DOCTYPE_CONFIG},
                          fields=["employee", "nom", "responsable"], order_by="idx")
    return [{"name": r.employee, "employee_name": r.nom or r.employee, "responsable": cint(r.responsable)}
            for r in rows]


def _verifier_acces():
    if not (est_responsable() or ROLE_EMPLOYE in frappe.get_roles()):
        frappe.throw(_("Le suivi d'activité n'est pas ouvert à votre compte (Config Suivi Activite)."),
                     frappe.PermissionError)


# ── Permissions du DocType (hooks.py) ────────────────────────────────────────

def permission_query_conditions(user=None):
    user = user or frappe.session.user
    if est_responsable(user):
        return ""
    return ("exists (select 1 from `tabActivite Affectation` aff where aff.parent = `tab{0}`.name "
            "and aff.parenttype = '{0}' and aff.utilisateur = {1})").format(DOCTYPE, frappe.db.escape(user))


def has_permission(doc, ptype=None, user=None, debug=False):
    user = user or frappe.session.user
    if est_responsable(user):
        return None                      # rien à restreindre : la permission du rôle s'applique
    if ptype == "create" or doc.get("__islocal"):
        return None                      # fiche neuve : validate impose l'employé du compte
    if ptype == "delete":
        return False
    # L'affectation ENREGISTRÉE fait foi, pas celle de la fiche en cours de modification : sinon
    # un employé qui retouche la liste perdrait l'accès avant même que validate ne refuse.
    affectes = set(frappe.get_all("Activite Affectation", filters={"parent": doc.get("name"),
                                                                  "parenttype": DOCTYPE}, pluck="utilisateur"))
    return user in affectes


# ── Rôles depuis le réglage ──────────────────────────────────────────────────

def synchroniser_roles(config) -> list[str]:
    """Aligne les deux rôles sur la liste du réglage. -> noms des employés sans compte."""
    voulus_emp, voulus_resp, sans_compte = set(), set(), []
    for r in config.get("employes") or []:
        user = frappe.db.get_value("Employee", r.employee, "user_id")
        if not user:
            sans_compte.append(r.nom or r.employee)
            continue
        voulus_emp.add(user)
        if cint(r.responsable):
            voulus_resp.add(user)
    for role, voulus in ((ROLE_EMPLOYE, voulus_emp), (ROLE_RESPONSABLE, voulus_resp)):
        if not frappe.db.exists("Role", role):
            continue
        actuels = set(frappe.get_all("Has Role", filters={"role": role, "parenttype": "User"}, pluck="parent"))
        # ⚠️ Lignes « Has Role » écrites DIRECTEMENT, sans User.save() : enregistrer un User
        # réenregistre son Contact, et le Server Script « update_list_tel » (Contact, After Save)
        # plante sur tout contact qui n'est pas celui d'un client — c'est le cas des employés.
        # Ici on ne change qu'un rôle : aucune raison de rejouer toute la fiche utilisateur.
        for user in voulus - actuels:
            frappe.get_doc({"doctype": "Has Role", "parent": user, "parenttype": "User",
                            "parentfield": "roles", "role": role}).db_insert()
            frappe.clear_cache(user=user)
        for user in actuels - voulus:
            if user != "Administrator":
                frappe.db.delete("Has Role", {"parent": user, "parenttype": "User", "role": role})
                frappe.clear_cache(user=user)
    frappe.clear_cache()                 # la liste des onglets est mise en cache par utilisateur
    return sans_compte


# ── Lecture ──────────────────────────────────────────────────────────────────

def _est_photo(url: str) -> bool:
    return (url or "").lower().split("?")[0].endswith(EXTENSIONS_PHOTO)


@frappe.whitelist()
def get_context():
    _verifier_acces()
    responsable = est_responsable()
    moi = employe_de()
    employes = employes_parametres() if responsable else (
        [{"name": moi, "employee_name": frappe.db.get_value("Employee", moi, "employee_name")}] if moi else [])
    return {"responsable": responsable, "moi": moi, "employes": employes,
            "statuts": STATUTS, "priorites": PRIORITES,
            "peut_configurer": "System Manager" in frappe.get_roles()}


@frappe.whitelist()
def get_activites(employe=None, recherche=None, inclure_fermees=0):
    """Les activités visibles par l'utilisateur, avec de quoi dessiner la liste."""
    _verifier_acces()
    filtres = []
    if not est_responsable():
        # double sécurité, en plus des hooks : seulement là où il est affecté
        filtres.append(["Activite Affectation", "utilisateur", "=", frappe.session.user])
    elif employe:
        filtres.append(["Activite Affectation", "employe", "=", employe])
    filtres_or = None
    if not cint(inclure_fermees):
        # Les activités closes depuis plus de 30 jours quittent l'écran (elles restent en liste Desk).
        filtres_or = [["statut", "not in", FERMES], ["date_fin", ">=", frappe.utils.add_days(nowdate(), -30)]]
    if recherche:
        filtres.append(["titre", "like", f"%{recherche}%"])
    rows = frappe.get_list(DOCTYPE, filters=filtres, or_filters=filtres_or, limit_page_length=500, distinct=True,
                           fields=["name", "titre", "noms_employes", "statut", "priorite", "avancement",
                                   "date_debut", "date_prevue", "date_fin", "modified", "ia_ameliore"],
                           order_by="date_prevue is null, date_prevue asc, modified desc")
    noms = [r.name for r in rows]
    compte = lambda dt: dict(frappe.get_all(dt, filters={"parent": ["in", noms], "parenttype": DOCTYPE},
                                            fields=["parent", "count(name) as n"], group_by="parent",
                                            as_list=True)) if noms else {}
    nb_fichiers, nb_notes = compte("Activite Fichier"), compte("Activite Note")
    etapes, vignettes, affectes, etapes_retard, prochaine = {}, {}, {}, {}, {}
    aujourdhui = getdate(nowdate())
    for e in (frappe.get_all("Activite Etape", filters={"parent": ["in", noms], "parenttype": DOCTYPE},
                             fields=["parent", "fait", "libelle", "date_prevue"], order_by="parent, idx") if noms else []):
        t = etapes.setdefault(e.parent, [0, 0]); t[1] += 1; t[0] += cint(e.fait)
        if not cint(e.fait):
            # Étape datée en retard (seulement tant que l'activité est ouverte, cf. plus bas) ; la première
            # étape non faite est « la prochaine », affichée dans la liste et l'ordre du jour des réunions.
            if e.date_prevue and getdate(e.date_prevue) < aujourdhui:
                etapes_retard[e.parent] = etapes_retard.get(e.parent, 0) + 1
            prochaine.setdefault(e.parent, {"libelle": e.libelle, "date_prevue": e.date_prevue})
    for f in (frappe.get_all("Activite Fichier", filters={"parent": ["in", noms], "parenttype": DOCTYPE,
                                                          "type_fichier": "Photo"},
                             fields=["parent", "fichier"], order_by="idx") if noms else []):
        vignettes.setdefault(f.parent, f.fichier)
    for a in (frappe.get_all("Activite Affectation", filters={"parent": ["in", noms], "parenttype": DOCTYPE},
                             fields=["parent", "employe", "nom"], order_by="idx") if noms else []):
        affectes.setdefault(a.parent, []).append({"employe": a.employe, "nom": a.nom})
    for r in rows:
        r["nb_fichiers"] = cint(nb_fichiers.get(r.name))
        r["nb_notes"] = cint(nb_notes.get(r.name))
        r["etapes"] = etapes.get(r.name, [0, 0])
        r["vignette"] = vignettes.get(r.name)
        r["employes"] = affectes.get(r.name, [])
        r["en_retard"] = bool(r.date_prevue and r.statut not in FERMES and getdate(r.date_prevue) < aujourdhui)
        r["etapes_retard"] = etapes_retard.get(r.name, 0) if r.statut not in FERMES else 0
        r["prochaine"] = prochaine.get(r.name) if r.statut not in FERMES else None
    kpis = {s: sum(1 for r in rows if r.statut == s) for s in STATUTS}
    kpis["En retard"] = sum(1 for r in rows if r["en_retard"])
    return {"activites": rows, "kpis": kpis}


def _doc(name, ptype="read"):
    _verifier_acces()
    doc = frappe.get_doc(DOCTYPE, name)
    doc.check_permission(ptype)
    return doc


@frappe.whitelist()
def get_activite(name):
    doc = _doc(name)
    d = doc.as_dict()
    for f in d.get("fichiers") or []:
        f["photo"] = f.get("type_fichier") == "Photo"
        f["nom"] = (f.get("fichier") or "").rsplit("/", 1)[-1]
    noms = {n.auteur for n in doc.notes if n.auteur}
    libelles = dict(frappe.get_all("User", filters={"name": ["in", list(noms)]}, fields=["name", "full_name"],
                                   as_list=True)) if noms else {}
    for n in d.get("notes") or []:
        n["auteur_nom"] = libelles.get(n.get("auteur")) or n.get("auteur")
    d["employes"] = [r.employe for r in doc.affectations]
    d["peut_supprimer"] = est_responsable()
    d["peut_reaffecter"] = est_responsable()
    return d


# ── Écriture ─────────────────────────────────────────────────────────────────

CHAMPS_EDITABLES = ("titre", "description", "priorite", "statut", "date_debut", "date_prevue")


@frappe.whitelist()
def enregistrer(data):
    """Crée ou met à jour une activité.

    `employes` : liste d'identifiants Employee (un ou plusieurs) — pris en compte pour un
    responsable seulement ; un employé crée pour lui-même et ne change pas l'affectation.
    """
    _verifier_acces()
    data = frappe.parse_json(data) if isinstance(data, str) else frappe._dict(data)
    responsable = est_responsable()
    if data.get("name"):
        doc = _doc(data["name"], "write")
    else:
        frappe.has_permission(DOCTYPE, "create", throw=True)
        doc = frappe.new_doc(DOCTYPE)
        if not responsable:
            moi = employe_de()
            if not moi:
                frappe.throw(_("Votre compte n'est rattaché à aucun employé."))
            doc.append("affectations", {"employe": moi})
    employes = data.get("employes")
    if isinstance(employes, str):
        employes = [employes]
    if responsable and employes is not None:
        doc.set("affectations", [{"employe": e} for e in employes if e])
    for champ in CHAMPS_EDITABLES:
        if champ in data:
            doc.set(champ, data.get(champ) or None)
    if data.get("statut") and data["statut"] not in STATUTS:
        frappe.throw(_("Statut inconnu : {0}").format(data["statut"]))
    for e in data.get("etapes") or []:            # étapes proposées par l'IA : « libellé » ou {libelle, date_prevue}
        ligne = _ligne_etape(e)
        if ligne:
            doc.append("etapes", ligne)
    if cint(data.get("ia_ameliore")):
        doc.ia_ameliore = 1
    doc.save()
    return {"name": doc.name}


@frappe.whitelist()
def changer_statut(name, statut):
    if statut not in STATUTS:
        frappe.throw(_("Statut inconnu : {0}").format(statut))
    doc = _doc(name, "write")
    doc.statut = statut
    doc.save()
    return {"statut": doc.statut, "avancement": doc.avancement}


def _ligne_etape(e) -> dict | None:
    """Une étape reçue de l'écran ou de l'IA : chaîne, ou {libelle, date_prevue}. None si vide."""
    if isinstance(e, dict):
        libelle, date = e.get("libelle"), e.get("date_prevue")
    else:
        libelle, date = e, None
    libelle = (libelle or "").strip()
    if not libelle:
        return None
    return {"libelle": libelle[:140], "date_prevue": str(getdate(date)) if date else None}


@frappe.whitelist()
def etape(name, action, libelle=None, ligne=None, date=None):
    """Ajouter / cocher-décocher / dater / supprimer une étape."""
    doc = _doc(name, "write")
    if action == "ajouter":
        nouvelle = _ligne_etape({"libelle": libelle, "date_prevue": date})
        if not nouvelle:
            frappe.throw(_("Écrivez l'étape."))
        doc.append("etapes", nouvelle)
    else:
        # Par l'identifiant de la ligne, pas par son rang : après une suppression, deux
        # lignes peuvent porter le même idx et le clic cocherait la mauvaise étape.
        row = next((e for e in doc.etapes if e.name == ligne), None)
        if not row:
            frappe.throw(_("Étape introuvable."))
        if action == "basculer":
            row.fait = 0 if cint(row.fait) else 1
            row.fait_le = now_datetime() if row.fait else None
            row.fait_par = frappe.session.user if row.fait else None
        elif action == "dater":
            row.date_prevue = str(getdate(date)) if date else None
        elif action == "supprimer":
            doc.remove(row)
        else:
            frappe.throw(_("Action inconnue."))
    doc.save()
    return {"avancement": doc.avancement, "statut": doc.statut, "date_prevue": doc.date_prevue}


@frappe.whitelist()
def ajouter_fichier(name, file_url, description=None):
    """Après le téléversement (FileUploader attaché à l'activité), la pièce entre dans la liste."""
    doc = _doc(name, "write")
    if not (file_url or "").startswith(("/files/", "/private/files/", "http")):
        frappe.throw(_("Fichier invalide."))
    if any(f.fichier == file_url for f in doc.fichiers):
        return {"deja": True}
    doc.append("fichiers", {"fichier": file_url, "type_fichier": "Photo" if _est_photo(file_url) else "Document",
                            "description": (description or "")[:140], "ajoute_par": frappe.session.user,
                            "ajoute_le": now_datetime()})
    doc.save()
    return {"deja": False}


@frappe.whitelist()
def supprimer_fichier(name, ligne):
    doc = _doc(name, "write")
    row = next((f for f in doc.fichiers if f.name == ligne), None)
    if not row:
        frappe.throw(_("Pièce introuvable."))
    if not est_responsable() and row.ajoute_par != frappe.session.user:
        frappe.throw(_("Vous ne pouvez retirer que les pièces que vous avez ajoutées."))
    doc.remove(row)
    doc.save()
    return {"ok": True}


@frappe.whitelist()
def ajouter_note(name, texte):
    if not (texte or "").strip():
        frappe.throw(_("La note est vide."))
    doc = _doc(name, "write")
    doc.append("notes", {"date": now_datetime(), "auteur": frappe.session.user, "texte": texte.strip()})
    doc.save()
    return {"ok": True}


@frappe.whitelist()
def supprimer(name):
    if not est_responsable():
        frappe.throw(_("Seul un responsable peut supprimer une activité."), frappe.PermissionError)
    frappe.delete_doc(DOCTYPE, name)
    return {"ok": True}


# ── IA ───────────────────────────────────────────────────────────────────────

def ajouter_jours_ouvres(depart, n: int):
    """Date + n jours ouvrés (dimanche chômé) — même règle que l'écran (sa_ajouter_jours_ouvres)."""
    d = getdate(depart or nowdate())
    reste = max(0, cint(n))
    while reste > 0:
        d = add_days(d, 1)
        if d.weekday() != 6:
            reste -= 1
    return d


def dater_etapes(etapes: list[dict], depart) -> list[dict]:
    """Pose `date_prevue` sur chaque étape d'après son `jour` (jour ouvré de fin, 1 = le premier jour).
    Un `jour` manquant ou non croissant reprend celui de l'étape d'avant (jamais de date qui recule)."""
    depart = getdate(depart or nowdate())
    courant = 1
    for e in etapes:
        j = cint(e.get("jour") or 0)
        courant = max(courant, j) if j else courant
        e["jour"] = courant
        e["date_prevue"] = str(ajouter_jours_ouvres(depart, courant - 1))
    return etapes


def prompt_amelioration(titre: str, description: str, employe: str | None, notes: list[str],
                        date_debut=None) -> tuple[str, str]:
    system = (
        "Tu aides une petite entreprise tunisienne de traitement de l'eau (osmoseurs, adoucisseurs, "
        "installation, entretien, vente) à rédiger les activités confiées à ses employés. "
        "On te donne un titre et une description souvent brefs ou mal rédigés. Rends STRICTEMENT un objet "
        'JSON, sans texte autour : {"titre": "<titre court et clair, verbe d\'action, 80 caractères max>", '
        '"description": "<description claire en français : objectif, contexte, résultat attendu ; 2 à 6 '
        'phrases ; ne rien inventer de factuel (noms, montants, dates) qui ne soit pas dans le texte>", '
        '"etapes": [{"etape": "<étape concrète et vérifiable, une action, un résultat visible>", '
        '"jour": <jour ouvré de FIN de l\'étape, entier à partir de 1, croissant d\'une étape à la suivante>}, ...] '
        "(3 à 8 étapes, dans l'ordre d'exécution ; les jours tiennent compte de la charge réelle d'un employé "
        "qui a d'autres tâches : une étape simple = 1 jour, une étape lourde = plusieurs jours ; dimanche chômé), "
        '"duree_jours": <jour de la dernière étape, entier>}. '
        "Garde le vocabulaire du métier et les mots de l'utilisateur quand ils sont précis."
    )
    user = "Titre : %s\nDescription : %s" % (titre or "", description or "(vide)")
    user += "\nDébut de l'activité : %s (jour 1)" % frappe.utils.formatdate(getdate(date_debut or nowdate()), "dd/MM/yyyy")
    if employe:
        user += "\nConfiée à : %s" % employe
    if notes:
        user += "\nNotes déjà prises :\n- " + "\n- ".join(notes[-10:])
    return system, user


def lire_reponse_ia(texte: str) -> dict:
    """Tolérant : le modèle est censé rendre un objet JSON, rien ne l'y oblige."""
    texte = (texte or "").strip().strip("`")
    if texte.lower().startswith("json"):
        texte = texte.split("\n", 1)[1] if "\n" in texte else ""
    try:
        d = json.loads(texte)
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    etapes = []
    for e in (d.get("etapes") or []) if isinstance(d.get("etapes"), list) else []:
        # Nouveau format {"etape", "jour"} ; l'ancien (une chaîne) reste lu, sans jour.
        if isinstance(e, dict):
            libelle, jour = e.get("etape") or e.get("libelle") or "", e.get("jour")
        else:
            libelle, jour = e, None
        libelle = str(libelle or "").strip()[:140]
        if not libelle:
            continue
        try:
            jour = max(0, min(365, int(jour or 0)))
        except (TypeError, ValueError):
            jour = 0
        etapes.append({"libelle": libelle, "jour": jour})
    try:
        duree = max(0, min(365, int(d.get("duree_jours") or 0)))
    except (TypeError, ValueError):
        duree = 0
    etapes = etapes[:12]
    duree = max(duree, max((e["jour"] for e in etapes), default=0))
    return {"titre": str(d.get("titre") or "").strip()[:140], "description": str(d.get("description") or "").strip(),
            "etapes": etapes, "duree_jours": duree}


@frappe.whitelist()
def ameliorer_ia(titre=None, description=None, employes=None, name=None, date_debut=None):
    """Propose une version améliorée (titre, description, étapes DATÉES, durée) — rien n'est enregistré :
    l'écran montre la proposition et l'utilisateur choisit ce qu'il garde, et peut retoucher chaque date."""
    _verifier_acces()
    notes = []
    if name:
        doc = _doc(name)
        titre = titre if titre is not None else doc.titre
        description = description if description is not None else doc.description
        employes = employes or [r.employe for r in doc.affectations]
        notes = [n.texte for n in doc.notes]
        date_debut = date_debut or doc.date_debut
    if len((titre or "") + (description or "")) < 4:
        frappe.throw(_("Écrivez d'abord quelques mots à améliorer."))
    employes = frappe.parse_json(employes) if isinstance(employes, str) and employes.startswith("[") else employes
    if isinstance(employes, str):
        employes = [employes]
    noms = ", ".join(frappe.db.get_value("Employee", e, "employee_name") or e for e in (employes or []))
    system, user = prompt_amelioration(titre, description, noms or None, notes, date_debut)
    from customization_app.liste_commande_import import _model, _openai_client
    client = _openai_client()
    params = {"model": _model(), "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": user}]}
    try:
        resp = client.chat.completions.create(temperature=0.3, **params)
    except Exception as e:
        if "temperature" not in str(e):
            raise
        resp = client.chat.completions.create(**params)
    prop = lire_reponse_ia(resp.choices[0].message.content)
    if not (prop["titre"] or prop["description"]):
        frappe.throw(_("L'IA n'a pas rendu de proposition exploitable, réessayez."))
    prop["date_debut"] = str(getdate(date_debut or nowdate()))
    dater_etapes(prop["etapes"], prop["date_debut"])
    if prop["etapes"]:
        prop["date_prevue"] = prop["etapes"][-1]["date_prevue"]
    elif prop["duree_jours"]:
        prop["date_prevue"] = str(ajouter_jours_ouvres(prop["date_debut"], prop["duree_jours"]))
    return prop

