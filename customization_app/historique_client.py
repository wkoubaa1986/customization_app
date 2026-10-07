"""Historique d'un client pour l'opératrice qui l'appelle (demande utilisateur du 07/10/2026).

Un seul panneau, ouvert par le bouton 📜 de « Liste Appelle Entretien », « Liste Appels Rattrapage » et
« Commandes à traiter » : pièces à proposer, interventions à venir, appels et commentaires, interventions passées,
échéancier d'entretien (avec ses actions), achats.

D'OÙ VIENT CHAQUE LIGNE (cartographié sur la base de prod du 07/10/2026)
-------------------------------------------------------------------------
- Appels des listes : `Appelle Client` (ligne d'une « Liste Appelle Entretien »). La ligne ne dit ni QUI ni QUAND :
  on le lit dans le suivi des modifications de la liste (`tabVersion`, track_changes actif) — 99 % des lignes appelées
  y figurent. `modified_by` de la liste ne vaut rien : c'est presque toujours Administrator (validation du cron 07:00).
- Appels sans réponse : commentaires « 📵 … » des tâches (Liste Appels Rattrapage), « 📞 Appel au … » (le technicien
  appelle depuis Ma journée), champs `custom_appel_1/2_sans_reponse` des commandes web.
- Commentaires : ceux écrits par une personne sur le client, ses commandes et ses tâches. Les traces posées par le code
  (📨, 📲, 🗺️, 🧹…) sont écartées. Les notes écrites sur une LISTE d'appels ne disent pas de quel client elles parlent :
  elles ne sont pas reprises — d'où la « note d'appel », enregistrée sur la fiche client.
- Échéancier : lignes de `Maintenance Schedule Detail` (SMS 1 et 2 avec leur statut, 1er appel, appelé, réalisée par
  quelle commande) + commentaires Info de l'échéancier (📅 décalage, 🧹 visites retirées) et du client (⏸️ / ✅).
  ⚠️ Avant le 07/10/2026, un décalage effaçait les SMS et appels des visites repoussées SANS TRACE : l'historique ancien
  de l'échéancier est donc incomplet. Depuis, chaque décalage laisse un commentaire 📅.

LECTURE SEULE, sauf `ajouter_note`.
"""
from __future__ import annotations

import html as html_lib
import json
import re

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, getdate, now_datetime, nowdate, strip_html

from customization_app import pieces_a_changer as PC

PLACEHOLDER_RAPPORT = "Indiquez vos remarques sur l'intervention et le client:"
TYPES_HORS_CLIENT = ("Congé", "Jour de récupération", "Tournée commerciale")
# Débuts de commentaires posés par le code (en plus de commentaires_commande.AUTOMATIQUES).
AUTOMATIQUES = ("SMS d'annulation", "SMS d’annulation", "BL consolidé", "📨", "📲", "🤝", "↩️", "📱", "✅", "⏸️", "🧹",
                "🔁", "🗺️", "🗺", "📅", "🔔")
APPELS = ("📵", "📞")
RELANCES = ("[RELANCE-PAIEMENT]", "[RELANCE-RAS]")
CHAMPS_LISTE = ("a_été_appelé", "resume_appel", "reponse_client", "intéressé_par_le_service_dentretien",
                "intéressé_par_le_service_de_relance")
LIMITE = 60


def _guard(client: str):
    if not client or not frappe.db.exists("Customer", client):
        frappe.throw(_("Client introuvable : {0}").format(client))
    if not frappe.has_permission("Customer", "read"):
        frappe.throw(_("Accès non autorisé"), frappe.PermissionError)


def texte(html: str, longueur: int = 600) -> str:
    """Texte lisible d'un commentaire (sans HTML), tronqué. PURE."""
    # html.unescape : une note saisie ici est échappée à l'écriture (&apos;, &lt;…) et doit se relire en clair.
    t = html_lib.unescape(strip_html(html or "")).replace("\xa0", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= longueur else t[: longueur - 1].rstrip() + "…"


def nature_commentaire(t: str) -> str:
    """'appel' | 'relance' | 'auto' | 'note'. PURE."""
    t = (t or "").lstrip()
    if t.startswith(APPELS):
        return "appel"
    if t.startswith(RELANCES):
        return "relance"
    if t.startswith(AUTOMATIQUES):
        return "auto"
    return "note"


def premiers_passages(versions: list, lignes: set) -> dict:
    """{nom de ligne: {"par", "le", "champs"}} — la PREMIÈRE modification de chaque ligne d'appel. PURE.

    `versions` : [{"owner", "creation", "data"}] triées par date ; `data` = JSON du suivi Frappe, dont
    `row_changed` = [[table, idx, nom de ligne, [[champ, avant, après], …]], …]."""
    out = {}
    for v in versions:
        try:
            data = json.loads(v["data"]) if isinstance(v["data"], str) else (v["data"] or {})
        except ValueError:
            continue
        for rc in data.get("row_changed") or []:
            if len(rc) < 4 or rc[2] not in lignes or rc[2] in out:
                continue
            champs = {c[0]: c[2] for c in rc[3] or [] if len(c) >= 3 and c[0] in CHAMPS_LISTE}
            if champs:
                out[rc[2]] = {"par": v["owner"], "le": v["creation"], "champs": champs}
    return out


# ── Lecture ───────────────────────────────────────────────────────────────────

def _noms_utilisateurs(users: set) -> dict:
    users = {u for u in users if u}
    if not users:
        return {}
    return dict(frappe.db.sql("SELECT name, COALESCE(NULLIF(full_name, ''), name) FROM `tabUser` WHERE name IN %(u)s",
                              {"u": list(users)}))


def entete(client: str) -> dict:
    c = frappe.db.get_value("Customer", client, ["name", "customer_name", "customer_group", "custom_liste_telephone",
                                                 "custom_statut_relance", "custom_gere_par_partenaire"], as_dict=True) or {}
    return {"name": c.get("name"), "nom": c.get("customer_name") or client, "groupe": c.get("customer_group"),
            "telephone": (c.get("custom_liste_telephone") or "").replace("\n", " · ").strip(" ·"),
            "statut_relance": c.get("custom_statut_relance"), "partenaire": cint(c.get("custom_gere_par_partenaire"))}


def _taches(client: str) -> list:
    return frappe.db.sql(
        """SELECT t.name, t.custom_type_dintervention AS type, t.status, t.starts_on, t.commande_client,
                  COALESCE(e.employee_name, t.custom_employé) AS employe, t.rapport_visite, t.raison_annulation, t.subject
           FROM `tabTache de travail` t
           LEFT JOIN `tabEmployee` e ON e.name = t.custom_choix_du_staff
           WHERE t.custom_client = %(c)s AND IFNULL(t.custom_type_dintervention, '') NOT IN %(hors)s
           ORDER BY t.starts_on DESC""",
        {"c": client, "hors": TYPES_HORS_CLIENT}, as_dict=True)


def interventions(taches: list) -> tuple[list, list]:
    """→ (à venir, passées). Une tâche ouverte dont la date est passée est « non réalisée »."""
    maintenant = now_datetime()
    a_venir, passees = [], []
    for t in taches:
        rapport = (t.rapport_visite or "").strip()
        if rapport == PLACEHOLDER_RAPPORT:
            rapport = ""
        ligne = {"name": t.name, "type": t.type or "", "status": t.status, "date": str(t.starts_on or ""),
                 "commande": t.commande_client or "", "employe": t.employe or "", "rapport": texte(rapport, 400),
                 "raison_annulation": t.raison_annulation or "", "sujet": texte(t.subject or "", 200)}
        if t.status == "Open" and t.starts_on and get_datetime(t.starts_on) >= maintenant:
            a_venir.append(ligne)
        else:
            if t.status == "Open":
                ligne["status"] = "Non réalisée"
            passees.append(ligne)
    a_venir.sort(key=lambda x: x["date"])
    return a_venir, passees[:LIMITE]


def _appels_des_listes(client: str) -> list:
    rows = frappe.db.sql(
        """SELECT a.name, a.parent, l.type_liste, l.date, l.docstatus, a.`a_été_appelé` AS appele, a.resume_appel,
                  a.reponse_client, a.`intéressé_par_le_service_dentretien` AS entretien
           FROM `tabAppelle Client` a JOIN `tabListe Appelle Entretien` l ON l.name = a.parent
           WHERE a.client = %(c)s AND l.docstatus < 2
           ORDER BY l.date DESC""", {"c": client}, as_dict=True)
    if not rows:
        return []
    listes = list({r.parent for r in rows})
    noms = {r.name for r in rows}
    conditions = " OR ".join(["data LIKE %s"] * len(noms))
    versions = frappe.db.sql(
        f"""SELECT owner, creation, data FROM `tabVersion`
            WHERE ref_doctype = 'Liste Appelle Entretien' AND docname IN %s AND ({conditions})
            ORDER BY creation""",
        tuple([tuple(listes)] + ['%"' + n + '",[[%' for n in noms]), as_dict=True)
    passages = premiers_passages(versions, noms)
    out = []
    for r in rows:
        p = passages.get(r.name) or {}
        if not cint(r.appele) and not r.resume_appel:
            if r.docstatus == 0:
                out.append({"date": str(r.date or ""), "genre": "liste", "par": "", "source": r.parent,
                            "titre": "Dans la liste %s en cours — pas encore appelé" % (r.type_liste or ""),
                            "texte": ""})
            continue
        out.append({"date": str(p.get("le") or r.date or ""), "genre": "appel", "par": p.get("par") or "",
                    "source": r.parent, "titre": "%s — %s" % (r.type_liste or "Liste", r.resume_appel or "appelé"),
                    "texte": texte(r.reponse_client or "", 400),
                    "detail": ("Entretien : %s" % r.entretien) if r.entretien else ""})
    return out


def _commentaires(client: str, commandes: list, taches: list) -> list:
    cibles = [("Customer", [client]), ("Sales Order", commandes), ("Tache de travail", taches)]
    out = []
    for doctype, noms in cibles:
        if not noms:
            continue
        for c in frappe.get_all("Comment", filters={"reference_doctype": doctype, "reference_name": ["in", noms],
                                                     "comment_type": "Comment"},
                                fields=["reference_name", "content", "creation", "owner"], limit_page_length=0):
            t = texte(c.content)
            nature = nature_commentaire(t)
            if not t or nature == "auto":
                continue
            if nature == "relance":
                t = t.replace("[RELANCE-PAIEMENT]", "Relance paiement").replace("[RELANCE-RAS]", "Relance retenue")
            out.append({"date": str(c.creation), "genre": "appel" if nature == "appel" else nature, "par": c.owner,
                        "source": c.reference_name if doctype != "Customer" else "", "doctype": doctype,
                        "titre": {"appel": "Appel", "relance": "Relance", "note": "Commentaire"}[nature], "texte": t})
    return out


def _appels_commandes(commandes: list) -> list:
    if not commandes:
        return []
    out = []
    for so in frappe.get_all("Sales Order", filters={"name": ["in", commandes]},
                             fields=["name", "custom_appel_1_sans_reponse", "custom_appel_2_sans_reponse"],
                             limit_page_length=0):
        for champ, rang in (("custom_appel_1_sans_reponse", "1er"), ("custom_appel_2_sans_reponse", "2e")):
            if so.get(champ):
                out.append({"date": str(so.get(champ)), "genre": "appel", "par": "", "source": so.name,
                            "doctype": "Sales Order", "titre": "📵 %s appel sans réponse (commande)" % rang, "texte": ""})
    return out


def appels_et_commentaires(client: str, commandes: list, taches: list) -> list:
    tout = _appels_des_listes(client) + _commentaires(client, commandes, taches) + _appels_commandes(commandes)
    # Un client sur plusieurs échéanciers avait une ligne PAR échéancier dans les anciennes listes : le même appel,
    # répété. On garde un exemplaire par (liste, résultat, minute).
    vus, uniques = set(), []
    for x in tout:
        cle = (x["source"], x["titre"], x["texte"], x["date"][:16])
        if cle not in vus:
            vus.add(cle)
            uniques.append(x)
    tout = uniques
    noms = _noms_utilisateurs({x["par"] for x in tout})
    for x in tout:
        x["par"] = noms.get(x["par"], x["par"])
    tout.sort(key=lambda x: x["date"], reverse=True)
    return tout[:LIMITE]


def echeancier(client: str) -> dict:
    """Échéanciers soumis du client : machines, visites (passées et prochaines) avec leurs actions, et journal."""
    ms = frappe.get_all("Maintenance Schedule", filters={"customer": client, "docstatus": 1},
                        fields=["name", "transaction_date"], order_by="transaction_date desc", limit_page_length=0)
    if not ms:
        return {"echeanciers": [], "journal": []}
    noms = [m.name for m in ms]
    machines = {}
    for it in frappe.get_all("Maintenance Schedule Item", filters={"parent": ["in", noms]},
                             fields=["parent", "item_code", "item_name", "sales_order", "start_date"], limit_page_length=0):
        machines.setdefault(it.parent, []).append({"item_code": it.item_code, "item_name": it.item_name,
                                                   "commande": it.sales_order or "", "depuis": str(it.start_date or "")})
    horizon = str(getdate(frappe.utils.add_days(nowdate(), 200)))
    visites = {}
    for d in frappe.get_all("Maintenance Schedule Detail", filters={"parent": ["in", noms]},
                            fields=["parent", "item_code", "scheduled_date", "actual_date", "completion_status",
                                    "custom_sms_1", "custom_sms1_status", "custom_sms_2", "custom_sms2_status",
                                    "custom_1er_appel", "custom_appelle", "custom_sales_order"],
                            order_by="scheduled_date asc", limit_page_length=0):
        if str(d.scheduled_date or "") > horizon:
            continue
        faite = bool(d.actual_date) or d.completion_status == "Fully Completed"
        etat = "réalisée" if faite else ("à venir" if str(d.scheduled_date or "") > nowdate() else "en attente")
        visites.setdefault(d.parent, []).append({
            "item_code": d.item_code, "prevue": str(d.scheduled_date or ""), "faite_le": str(d.actual_date or ""),
            "etat": etat, "commande": d.custom_sales_order or "",
            "sms1": str(d.custom_sms_1 or ""), "sms1_statut": d.custom_sms1_status or "",
            "sms2": str(d.custom_sms_2 or ""), "sms2_statut": d.custom_sms2_status or "",
            "premier_appel": str(d.custom_1er_appel or ""), "appele": str(d.custom_appelle or "")})
    journal = []
    for c in frappe.get_all("Comment", filters={"reference_doctype": "Maintenance Schedule", "reference_name": ["in", noms],
                                                 "comment_type": "Info"},
                            fields=["reference_name", "content", "creation"], limit_page_length=0):
        journal.append({"date": str(c.creation), "source": c.reference_name, "texte": texte(c.content, 500)})
    for c in frappe.get_all("Comment", filters={"reference_doctype": "Customer", "reference_name": client,
                                                 "comment_type": "Info"},
                            fields=["content", "creation"], limit_page_length=0):
        t = texte(c.content, 500)
        if t.startswith(("⏸️", "✅")):
            journal.append({"date": str(c.creation), "source": "", "texte": t})
    journal.sort(key=lambda x: x["date"], reverse=True)
    return {"echeanciers": [{"name": m.name, "depuis": str(m.transaction_date or ""), "machines": machines.get(m.name, []),
                             "visites": visites.get(m.name, [])} for m in ms],
            "journal": journal[:30]}


def achats(client: str) -> list:
    lignes = frappe.db.sql(
        """SELECT so.name, so.transaction_date, so.delivery_date, so.status, so.grand_total,
                  soi.item_code, soi.item_name, soi.qty, i.item_group
           FROM `tabSales Order` so JOIN `tabSales Order Item` soi ON soi.parent = so.name
           LEFT JOIN `tabItem` i ON i.name = soi.item_code
           WHERE so.customer = %(c)s AND so.docstatus = 1
           ORDER BY so.transaction_date DESC, so.name, soi.idx""", {"c": client}, as_dict=True)
    commandes = {}
    for l in lignes:
        c = commandes.setdefault(l.name, {"name": l.name, "date": str(l.delivery_date or l.transaction_date or ""),
                                          "status": l.status, "total": l.grand_total, "articles": []})
        machine = PC.machine_de_la_ligne(l.item_code, l.item_group, [])
        c["articles"].append({"code": l.item_code, "nom": l.item_name, "qte": l.qty, "machine": bool(machine),
                              "main_doeuvre": (l.item_group or "") == "Main d’œuvre"})
    return list(commandes.values())[:40]


@frappe.whitelist()
def get_historique(client: str) -> dict:
    _guard(client)
    taches = _taches(client)
    a_venir, passees = interventions(taches)
    liste_achats = achats(client)
    commandes = [c["name"] for c in liste_achats]
    toutes_commandes = frappe.get_all("Sales Order", filters={"customer": client, "docstatus": ["<", 2]}, pluck="name")
    pieces = PC.pour_clients([client]).get(client) or {}
    return {
        "client": entete(client),
        "pieces": pieces,
        "a_venir": a_venir,
        "appels": appels_et_commentaires(client, toutes_commandes or commandes, [t.name for t in taches]),
        "interventions": passees,
        "echeancier": echeancier(client),
        "achats": liste_achats,
    }


@frappe.whitelist()
def get_pieces_lot(clients) -> dict:
    """{client: {"texte", "pieces", "revendeur"}} pour les cartes d'un écran (une page de liste)."""
    if isinstance(clients, str):
        clients = json.loads(clients) if clients.strip().startswith("[") else [clients]
    if not frappe.has_permission("Customer", "read"):
        frappe.throw(_("Accès non autorisé"), frappe.PermissionError)
    clients = [c for c in clients or [] if c][:300]
    res = PC.pour_clients(clients)
    return {c: {"texte": r["texte"], "revendeur": r["revendeur"],
                "pieces": [p for p in r["pieces"] if p["statut"] in ("retard", "bientot")]}
            for c, r in res.items()}


@frappe.whitelist(methods=["POST"])
def ajouter_note(client: str, note: str) -> dict:
    """Note d'appel sur la fiche client (commentaire de sa timeline)."""
    _guard(client)
    note = (note or "").strip()
    if len(note) < 2:
        frappe.throw(_("Note vide."))
    c = frappe.get_doc("Customer", client).add_comment("Comment", frappe.utils.escape_html(note))
    return {"name": c.name, "date": str(c.creation), "par": frappe.utils.get_fullname(frappe.session.user)}
