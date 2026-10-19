"""Réunions d'équipe du Suivi d'activité — point d'avancement toutes les N semaines (demande du 19/10/2026).

Une réunion, c'est une date, une heure, un lieu et un lien visio (Jitsi, sans compte ni serveur, rien n'est
enregistré), les participants (présent / absent), et un compte-rendu : des notes horodatées dont certaines sont
des DÉCISIONS affectées à un ou plusieurs employés — chacune devient une activité de leur liste, avec sa date.

L'ordre du jour n'est PAS stocké : il est assemblé à l'ouverture depuis les activités, par participant :
en retard (activité ou étape), en cours, terminées depuis la réunion précédente, et les décisions de la
précédente avec leur état. C'est ce qui fait de la réunion un vrai point d'avancement, sans rien préparer.

Récurrence : réglage dans « Config Suivi Activite » (jour, toutes les N semaines, heure, durée, lieu, visio,
création N jours avant, rappel SMS). Le cron du matin crée la prochaine ; deux crons rappellent (veille 18:00,
jour 08:00) par notification Desk, et par SMS si coché — SIMULÉ en developer_mode (le dev porte les vrais numéros).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, now_datetime, nowdate

from customization_app import suivi_activite as SA

DOCTYPE = "Reunion Activite"
JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
PREFIXE_SALLE = "aquaworld-reunion"
DEFAUTS = {"reunion_active": 0, "reunion_jour": "Mercredi", "reunion_toutes_les": 2, "reunion_premiere_date": None,
           "reunion_heure": "09:00:00", "reunion_duree": 45, "reunion_lieu": "Magasin", "reunion_visio": 1,
           "reunion_creer_jours_avant": 7, "reunion_rappel_sms": 0}
STATUTS = ("Prévue", "Tenue", "Annulée")


# ── Réglage ──────────────────────────────────────────────────────────────────

def config() -> dict:
    """Le réglage avec ses défauts : un Single EXISTANT ne reçoit pas les défauts du DocType pour un champ
    ajouté après coup (tabSingles sans ligne) — on les pose ici, jamais en se fiant à get_single."""
    cfg = dict(DEFAUTS)
    if frappe.db.exists("DocType", SA.DOCTYPE_CONFIG):
        for champ, valeur in frappe.db.sql("select field, value from tabSingles where doctype = %s", SA.DOCTYPE_CONFIG):
            if champ in cfg and valeur not in (None, ""):
                cfg[champ] = valeur
    for k in ("reunion_active", "reunion_toutes_les", "reunion_duree", "reunion_visio", "reunion_creer_jours_avant",
              "reunion_rappel_sms"):
        cfg[k] = cint(cfg[k])
    cfg["reunion_toutes_les"] = max(1, cfg["reunion_toutes_les"]) or 2
    if cfg["reunion_jour"] not in JOURS:
        cfg["reunion_jour"] = DEFAUTS["reunion_jour"]
    cfg["reunion_heure"] = str(cfg["reunion_heure"] or DEFAUTS["reunion_heure"])[:8]
    return cfg


def _aligner(d, jour: str):
    """Le jour de semaine voulu, le jour même ou après."""
    d = getdate(d)
    voulu = JOURS.index(jour)
    return add_days(d, (voulu - d.weekday()) % 7)


def prochaine_date(cfg: dict, depuis=None, derniere=None):
    """Première occurrence >= `depuis` : repère = « à partir du » du réglage, sinon la dernière réunion
    (+ N semaines), sinon le prochain jour voulu. Les occurrences tombent toutes les N semaines sur le repère."""
    depuis = getdate(depuis or nowdate())
    pas = 7 * cfg["reunion_toutes_les"]
    repere = cfg.get("reunion_premiere_date") or derniere
    if not repere:
        return _aligner(depuis, cfg["reunion_jour"])
    repere = _aligner(repere, cfg["reunion_jour"])
    if depuis <= repere:
        return repere
    k = -(-(depuis - repere).days // pas)              # plafond
    return add_days(repere, k * pas)


def lien_visio(date) -> str:
    """Un salon Jitsi par réunion : la date pour s'y retrouver, un suffixe aléatoire pour qu'il ne se devine pas."""
    return "https://meet.jit.si/%s-%s-%s" % (PREFIXE_SALLE, getdate(date), frappe.generate_hash(length=4))


# ── Création ─────────────────────────────────────────────────────────────────

def _derniere(avant=None, statuts=("Prévue", "Tenue")):
    filtres = {"statut": ["in", list(statuts)]}
    if avant:
        filtres["date"] = ["<", str(getdate(avant))]
    rows = frappe.get_all(DOCTYPE, filters=filtres, fields=["name", "date"], order_by="date desc, heure desc", limit=1)
    return rows[0] if rows else None


def _derniere_avec(employes: set, avant):
    """La dernière réunion (Prévue/Tenue) avant `avant` qui réunissait EXACTEMENT ces participants — c'est elle
    la « précédente » d'un point d'équipe ou individuel, pas la dernière réunion de n'importe qui."""
    rows = frappe.get_all(DOCTYPE, filters={"statut": ["in", ["Prévue", "Tenue"]], "date": ["<", str(getdate(avant))]},
                          fields=["name", "date"], order_by="date desc, heure desc", limit=30)
    for r in rows:
        if set(frappe.get_all("Reunion Participant", filters={"parent": r.name, "parenttype": DOCTYPE}, pluck="employe")) == employes:
            return r
    return None


def creer_reunion(date, heure=None, cron: bool = False, cfg: dict | None = None, employes: list | None = None,
                  titre: str | None = None, lieu: str | None = None):
    """Une réunion pour `employes` (défaut : toute l'équipe du réglage) : point d'équipe, d'un groupe, ou d'une seule
    personne. La « précédente » (dont on relit les décisions) est la dernière réunion des MÊMES participants."""
    cfg = cfg or config()
    date = getdate(date)
    doc = frappe.new_doc(DOCTYPE)
    employes = [e for e in (employes or []) if e] or [e["name"] for e in SA.employes_parametres()]
    prec = _derniere_avec(set(employes), date)
    if not titre:
        if len(employes) == 1:
            titre = _("Point avec {0} · {1}").format(frappe.db.get_value("Employee", employes[0], "employee_name") or employes[0],
                                                     frappe.utils.formatdate(date, "dd/MM/yyyy"))
        else:
            titre = _("Réunion d'équipe · point du {0}").format(frappe.utils.formatdate(date, "dd/MM/yyyy"))
    doc.update({"titre": titre[:140], "date": date, "heure": heure or cfg["reunion_heure"], "duree_min": cfg["reunion_duree"],
                "lieu": lieu or cfg["reunion_lieu"], "statut": "Prévue", "creee_par_cron": 1 if cron else 0,
                "precedente": prec.name if prec else None,
                "lien_visio": lien_visio(date) if cfg["reunion_visio"] else None})
    for e in employes:
        doc.append("participants", {"employe": e})
    doc.insert(ignore_permissions=True)
    return doc


def cron_creer_prochaine():
    """Chaque matin : s'il n'y a pas encore de réunion prévue pour la prochaine occurrence et qu'elle tombe
    dans les N jours, on la crée (participants = employés du réglage)."""
    cfg = config()
    if not cfg["reunion_active"]:
        return None
    derniere = _derniere()
    d = prochaine_date(cfg, nowdate(), derniere.date if derniere else None)
    if (d - getdate(nowdate())).days > cfg["reunion_creer_jours_avant"]:
        return None
    if frappe.db.exists(DOCTYPE, {"date": str(d), "statut": ["!=", "Annulée"]}):
        return None
    doc = creer_reunion(d, cron=True, cfg=cfg)
    if not frappe.flags.in_test:
        frappe.db.commit()
    return doc.name


# ── Rappels ──────────────────────────────────────────────────────────────────

def _sms_simule() -> bool:
    return bool(cint(frappe.conf.get("developer_mode"))) and not cint(frappe.conf.get("sms_reel_en_dev"))


def heure_hm(h) -> str:
    """« 09:00 » depuis un Time lu en base (timedelta « 9:00:00 ») ou une chaîne."""
    parts = str(h or "").split(":")
    if len(parts) < 2:
        return ""
    return "%02d:%02d" % (cint(parts[0]), cint(parts[1]))


def texte_rappel(doc, quand: str) -> str:
    from customization_app.sms_annulation import sans_accents
    heure = heure_hm(doc.heure)
    t = "Aquaworld : reunion d'equipe %s a %s, %s." % ("demain" if quand == "veille" else "aujourd'hui", heure, doc.lieu or "")
    if doc.lien_visio:
        t += " Visio : %s" % doc.lien_visio
    return sans_accents(t)


def rappeler(doc, quand: str, cfg: dict | None = None) -> dict:
    """Notification Desk à chaque participant ; SMS en plus si le réglage le dit (simulé en dev)."""
    cfg = cfg or config()
    texte = texte_rappel(doc, quand)
    sujet = _("Réunion d'équipe {0} à {1}").format(_("demain") if quand == "veille" else _("aujourd'hui"), heure_hm(doc.heure))
    bilan = {"notifies": 0, "sms": 0, "simule": _sms_simule()}
    numeros = []
    for p in doc.participants:
        if p.utilisateur:
            frappe.get_doc({"doctype": "Notification Log", "for_user": p.utilisateur, "type": "Alert", "subject": sujet,
                            "email_content": texte, "document_type": DOCTYPE, "document_name": doc.name,
                            "from_user": "Administrator"}).insert(ignore_permissions=True)
            bilan["notifies"] += 1
        if cfg["reunion_rappel_sms"]:
            tel = frappe.db.get_value("Employee", p.employe, "cell_number")
            from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import traiter_numero_tel
            for n in traiter_numero_tel(tel or ""):
                numeros.append("216%s" % n)
        p.db_set("rappel_envoye", now_datetime(), update_modified=False)
    if numeros:
        if not bilan["simule"]:
            from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import _send_sms_with_fallback
            _send_sms_with_fallback(numeros, texte)
        bilan["sms"] = len(numeros)
        doc.add_comment("Comment", _("📲 Rappel SMS ({0}) {1} à {2} numéro(s)").format(
            quand, _("SIMULÉ (dev)") if bilan["simule"] else _("envoyé"), len(numeros)))
    return bilan


def cron_rappel_veille():
    return _cron_rappel(add_days(nowdate(), 1), "veille")


def cron_rappel_jour():
    return _cron_rappel(nowdate(), "jour")


def _cron_rappel(date, quand):
    cfg = config()
    bilans = []
    for name in frappe.get_all(DOCTYPE, filters={"date": str(getdate(date)), "statut": "Prévue"}, pluck="name"):
        try:
            bilans.append(rappeler(frappe.get_doc(DOCTYPE, name), quand, cfg))
            if not frappe.flags.in_test:
                frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title="Rappel réunion %s" % name, message=frappe.get_traceback())
    return bilans


# ── Lecture ──────────────────────────────────────────────────────────────────

def _acces_reunion(doc):
    """Responsable : tout. Employé : seulement une réunion où il est participant."""
    if SA.est_responsable():
        return
    if frappe.session.user not in {p.utilisateur for p in doc.participants}:
        frappe.throw(_("Vous n'êtes pas participant de cette réunion."), frappe.PermissionError)


@frappe.whitelist()
def get_reunions():
    SA._verifier_acces()
    cfg = config()
    champs = ["name", "titre", "date", "heure", "duree_min", "lieu", "lien_visio", "statut", "tenue_le", "creee_par_cron"]
    filtres = {}
    if not SA.est_responsable():
        mes = frappe.get_all("Reunion Participant", filters={"utilisateur": frappe.session.user, "parenttype": DOCTYPE}, pluck="parent")
        filtres = {"name": ["in", mes or [""]]}
    a_venir = frappe.get_all(DOCTYPE, filters=dict(filtres, statut="Prévue"), fields=champs, order_by="date asc, heure asc")
    passees = frappe.get_all(DOCTYPE, filters=dict(filtres, statut=["in", ["Tenue", "Annulée"]]), fields=champs,
                             order_by="date desc, heure desc", limit=20)
    noms = [r.name for r in a_venir + passees]
    presences, decisions = {}, {}
    if noms:
        for p in frappe.get_all("Reunion Participant", filters={"parent": ["in", noms], "parenttype": DOCTYPE},
                                fields=["parent", "nom", "present"], order_by="idx"):
            presences.setdefault(p.parent, []).append({"nom": p.nom, "present": p.present})
        for n in frappe.get_all("Reunion Note", filters={"parent": ["in", noms], "parenttype": DOCTYPE, "activite": ["is", "set"]},
                                fields=["parent", "activite"]):
            decisions.setdefault(n.parent, []).append(n.activite)
    etats = {}
    acts = sorted({a for l in decisions.values() for a in l})
    if acts:
        etats = {r.name: r.statut for r in frappe.get_all(SA.DOCTYPE, filters={"name": ["in", acts]}, fields=["name", "statut"])}
    for r in a_venir + passees:
        r["participants"] = presences.get(r.name, [])
        r["nb_presents"] = sum(1 for p in r["participants"] if p["present"] == "Présent")
        r["nb_decisions"] = len(decisions.get(r.name, []))
        r["nb_decisions_faites"] = sum(1 for a in decisions.get(r.name, []) if etats.get(a) == "Terminée")
    derniere = _derniere()
    return {"a_venir": a_venir, "passees": passees, "peut_gerer": SA.est_responsable(),
            "config": {k.replace("reunion_", ""): v for k, v in cfg.items()},
            "prochaine_date": str(prochaine_date(cfg, nowdate(), derniere.date if derniere else None)) if cfg["reunion_active"] else None}


def ordre_du_jour(doc) -> dict:
    """Par participant : ses activités ouvertes (retard d'abord), celles terminées depuis la réunion précédente ;
    plus les décisions de la précédente avec leur état. Rien n'est stocké : c'est l'état au moment de l'ouverture."""
    depuis = None
    if doc.precedente:
        depuis = frappe.db.get_value(DOCTYPE, doc.precedente, "date")
    depuis = getdate(depuis) if depuis else add_days(getdate(doc.date), -14)
    employes = [p.employe for p in doc.participants]
    if not employes:
        return {"employes": [], "kpis": {"retard": 0, "en_cours": 0, "terminees": 0}, "decisions_precedente": [], "depuis": str(depuis)}
    aff = frappe.get_all("Activite Affectation", filters={"employe": ["in", employes], "parenttype": SA.DOCTYPE},
                         fields=["parent", "employe"])
    noms = sorted({a.parent for a in aff})
    acts = {}
    if noms:
        for a in frappe.get_all(SA.DOCTYPE, filters={"name": ["in", noms]},
                                fields=["name", "titre", "noms_employes", "statut", "priorite", "avancement", "date_prevue", "date_fin"]):
            acts[a.name] = a
    etapes_retard, prochaine, total_etapes = {}, {}, {}
    aujourdhui = getdate(nowdate())
    if noms:
        for e in frappe.get_all("Activite Etape", filters={"parent": ["in", noms], "parenttype": SA.DOCTYPE},
                                fields=["parent", "fait", "libelle", "date_prevue"], order_by="parent, idx"):
            t = total_etapes.setdefault(e.parent, [0, 0]); t[1] += 1; t[0] += cint(e.fait)
            if not cint(e.fait):
                if e.date_prevue and getdate(e.date_prevue) < aujourdhui:
                    etapes_retard[e.parent] = etapes_retard.get(e.parent, 0) + 1
                prochaine.setdefault(e.parent, {"libelle": e.libelle, "date_prevue": e.date_prevue})
    par_emp = {}
    for a in aff:
        par_emp.setdefault(a.employe, []).append(a.parent)
    kpis = {"retard": 0, "en_cours": 0, "terminees": 0}
    vus_kpi = set()
    sortie = []
    for p in doc.participants:
        ouvertes, terminees = [], []
        for n in par_emp.get(p.employe, []):
            a = acts.get(n)
            if not a:
                continue
            ligne = dict(a)
            ligne["etapes"] = total_etapes.get(n, [0, 0])
            if a.statut in SA.FERMES:
                if a.statut == "Terminée" and a.date_fin and getdate(a.date_fin) >= depuis:
                    terminees.append(ligne)
                continue
            ligne["retard_jours"] = (aujourdhui - getdate(a.date_prevue)).days if a.date_prevue and getdate(a.date_prevue) < aujourdhui else 0
            ligne["etapes_retard"] = etapes_retard.get(n, 0)
            ligne["prochaine"] = prochaine.get(n)
            ligne["en_retard"] = bool(ligne["retard_jours"] or ligne["etapes_retard"])
            ouvertes.append(ligne)
            if n not in vus_kpi:
                vus_kpi.add(n)
                kpis["retard" if ligne["en_retard"] else "en_cours"] += 1
        for t in terminees:
            if ("t", t["name"]) not in vus_kpi:
                vus_kpi.add(("t", t["name"])); kpis["terminees"] += 1
        ouvertes.sort(key=lambda x: (not x["en_retard"], -x["retard_jours"], str(x.get("date_prevue") or "9999")))
        sortie.append({"employe": p.employe, "nom": p.nom, "present": p.present, "ouvertes": ouvertes, "terminees": terminees,
                       "nb_retard": sum(1 for x in ouvertes if x["en_retard"]),
                       "nb_en_cours": sum(1 for x in ouvertes if not x["en_retard"]), "nb_terminees": len(terminees)})
    decisions = []
    if doc.precedente:
        for n in frappe.get_all("Reunion Note", filters={"parent": doc.precedente, "parenttype": DOCTYPE, "activite": ["is", "set"]},
                                fields=["texte", "activite", "pour"], order_by="idx"):
            a = frappe.db.get_value(SA.DOCTYPE, n.activite, ["statut", "avancement", "date_prevue", "titre"], as_dict=True) or {}
            decisions.append({"texte": n.texte, "activite": n.activite, "pour": n.pour, "statut": a.get("statut"),
                              "avancement": a.get("avancement"), "date_prevue": a.get("date_prevue"),
                              "en_retard": bool(a.get("date_prevue") and a.get("statut") not in SA.FERMES
                                                and getdate(a["date_prevue"]) < aujourdhui)})
    return {"employes": sortie, "kpis": kpis, "decisions_precedente": decisions, "depuis": str(depuis)}


@frappe.whitelist()
def get_reunion(name):
    SA._verifier_acces()
    doc = frappe.get_doc(DOCTYPE, name)
    _acces_reunion(doc)
    d = doc.as_dict()
    noms = {n.auteur for n in doc.notes if n.auteur}
    libelles = dict(frappe.get_all("User", filters={"name": ["in", list(noms)]}, fields=["name", "full_name"], as_list=True)) if noms else {}
    for n in d.get("notes") or []:
        n["auteur_nom"] = libelles.get(n.get("auteur")) or n.get("auteur")
        if n.get("activite"):
            a = frappe.db.get_value(SA.DOCTYPE, n["activite"], ["statut", "avancement"], as_dict=True) or {}
            n["activite_statut"], n["activite_avancement"] = a.get("statut"), a.get("avancement")
    d["ordre_du_jour"] = ordre_du_jour(doc)
    d["peut_gerer"] = SA.est_responsable()
    d["moi"] = SA.employe_de()
    d["employes"] = SA.employes_parametres() if SA.est_responsable() else []
    if doc.precedente:
        d["precedente_date"] = frappe.db.get_value(DOCTYPE, doc.precedente, "date")
    return d


# ── Écriture ─────────────────────────────────────────────────────────────────

def _gerer():
    SA._verifier_acces()
    if not SA.est_responsable():
        frappe.throw(_("Seul un responsable peut faire cela."), frappe.PermissionError)


@frappe.whitelist()
def creer(date, heure=None, employes=None, titre=None, lieu=None):
    """Réunion à la date voulue pour les employés cochés (tous par défaut). Plusieurs réunions le même jour sont
    possibles (équipes différentes) ; refusée seulement si la même liste de participants a déjà une réunion prévue ce jour."""
    _gerer()
    employes = frappe.parse_json(employes) if isinstance(employes, str) and employes.startswith("[") else employes
    if isinstance(employes, str):
        employes = [employes]
    employes = [e for e in (employes or []) if e] or [e["name"] for e in SA.employes_parametres()]
    if not employes:
        frappe.throw(_("Choisissez au moins un participant."))
    for n in frappe.get_all(DOCTYPE, filters={"date": str(getdate(date)), "statut": "Prévue"}, pluck="name"):
        if set(frappe.get_all("Reunion Participant", filters={"parent": n, "parenttype": DOCTYPE}, pluck="employe")) == set(employes):
            frappe.throw(_("Une réunion avec exactement ces participants est déjà prévue ce jour-là ({0}).").format(n))
    doc = creer_reunion(date, heure, employes=employes, titre=titre, lieu=lieu)
    return {"name": doc.name}


@frappe.whitelist()
def modifier(name, data):
    """Date, heure, durée, lieu, lien, titre, synthèse ; participants (liste d'employés)."""
    _gerer()
    data = frappe.parse_json(data) if isinstance(data, str) else data
    doc = frappe.get_doc(DOCTYPE, name)
    for champ in ("titre", "date", "heure", "duree_min", "lieu", "lien_visio", "compte_rendu"):
        if champ in data:
            doc.set(champ, data.get(champ) or None)
    if data.get("employes") is not None:
        presents = {p.employe: p.present for p in doc.participants}
        doc.set("participants", [{"employe": e, "present": presents.get(e)} for e in data["employes"] if e])
        prec = _derniere_avec({e for e in data["employes"] if e}, doc.date)
        doc.precedente = prec.name if prec and prec.name != doc.name else None
    doc.save(ignore_permissions=True)
    return {"name": doc.name}


@frappe.whitelist()
def presence(name, employe, present):
    """Présent / Absent / (vide). Un employé ne marque que lui-même."""
    SA._verifier_acces()
    doc = frappe.get_doc(DOCTYPE, name)
    _acces_reunion(doc)
    if not SA.est_responsable() and employe != SA.employe_de():
        frappe.throw(_("Vous ne pouvez marquer que votre propre présence."), frappe.PermissionError)
    row = next((p for p in doc.participants if p.employe == employe), None)
    if not row:
        frappe.throw(_("Participant introuvable."))
    row.present = present if present in ("Présent", "Absent") else None
    doc.save(ignore_permissions=True)
    return {"present": row.present}


@frappe.whitelist()
def noter(name, texte, employes=None, date_prevue=None, priorite=None):
    """Une note du compte-rendu. Avec `employes` (responsable) : c'est une DÉCISION, qui devient une activité
    affectée à ces employés, datée — elle apparaît aussitôt dans leur liste et sera relue à la prochaine réunion."""
    SA._verifier_acces()
    texte = (texte or "").strip()
    if not texte:
        frappe.throw(_("La note est vide."))
    doc = frappe.get_doc(DOCTYPE, name)
    _acces_reunion(doc)
    employes = frappe.parse_json(employes) if isinstance(employes, str) and employes.startswith("[") else employes
    if isinstance(employes, str):
        employes = [employes]
    employes = [e for e in (employes or []) if e]
    note = {"date": now_datetime(), "auteur": frappe.session.user, "texte": texte}
    if employes:
        if not SA.est_responsable():
            frappe.throw(_("Seul un responsable transforme une décision en activité."), frappe.PermissionError)
        act = frappe.new_doc(SA.DOCTYPE)
        act.update({"titre": texte[:140], "priorite": priorite if priorite in SA.PRIORITES else "Normale",
                    "date_debut": nowdate(), "date_prevue": str(getdate(date_prevue)) if date_prevue else None,
                    "description": _("Décision de la réunion {0} du {1}.").format(doc.name, frappe.utils.formatdate(doc.date, "dd/MM/yyyy"))})
        for e in employes:
            act.append("affectations", {"employe": e})
        act.insert(ignore_permissions=True)
        note.update({"activite": act.name, "pour": act.noms_employes})
    doc.append("notes", note)
    doc.save(ignore_permissions=True)
    return {"activite": note.get("activite")}


@frappe.whitelist()
def supprimer_note(name, ligne):
    """Retire la note (l'activité créée, elle, reste : on la supprime depuis sa fiche si besoin)."""
    SA._verifier_acces()
    doc = frappe.get_doc(DOCTYPE, name)
    _acces_reunion(doc)
    row = next((n for n in doc.notes if n.name == ligne), None)
    if not row:
        frappe.throw(_("Note introuvable."))
    if not SA.est_responsable() and row.auteur != frappe.session.user:
        frappe.throw(_("Vous ne pouvez retirer que vos notes."), frappe.PermissionError)
    doc.remove(row)
    doc.save(ignore_permissions=True)
    return {"ok": True}


@frappe.whitelist()
def changer_statut(name, statut, compte_rendu=None):
    """Tenue (clôture, avec la synthèse), Annulée, ou retour à Prévue."""
    _gerer()
    if statut not in STATUTS:
        frappe.throw(_("Statut inconnu : {0}").format(statut))
    doc = frappe.get_doc(DOCTYPE, name)
    doc.statut = statut
    if compte_rendu is not None:
        doc.compte_rendu = compte_rendu or None
    doc.save(ignore_permissions=True)
    return {"statut": doc.statut, "tenue_le": doc.tenue_le}


@frappe.whitelist()
def supprimer(name):
    _gerer()
    frappe.delete_doc(DOCTYPE, name, ignore_permissions=True)
    return {"ok": True}
