"""Sorties du Magasin — approbation des BL par le MAGASINIER PRINCIPAL (demande du 27/10/2026).

Un BL qui contient des articles du Magasin (Server Script « Verif BL du magasin » → `custom_requires_store_approval`)
reste « En attente validation magasin » (workflow « Validation Magasin », en base) jusqu'à son approbation.
Jusqu'ici n'importe qui pouvait approuver (patch workflow_bl_approve_tous du 27/08). Désormais :
- APPROUVER et REJETER sont réservés au rôle « Magasinier principal » (Hedi ibidhii et Wassim Koubaa, posés par le
  patch ensure_sorties_magasin) ;
- le magasinier EN TITRE (employé du réglage « responsable magasin », Hedi) est PRÉVENU de façon intrusive : cloche du
  Desk, événement temps réel, et un bandeau rouge en haut de toutes ses pages tant qu'un BL attend
  (public/js/bl_magasin_alerte.js), plus une relance quotidienne — les autres magasiniers principaux approuvent sans
  être alertés ;
- en son absence, n'importe quel employé peut faire une « Sortie exceptionnelle » (action du workflow ouverte à tous,
  bouton de l'onglet BL de la page Stock) : le BL est validé, le stock sort, et la sortie est TRACÉE sur le BL
  (`custom_sortie_verification` = « À vérifier », sorti par, le). Le magasinier retrouve ces sorties dans l'onglet BL
  et les vérifie à son retour : « Conforme » ou « Écart » (note), l'employé étant prévenu d'un écart.
Un BL approuvé par un magasinier principal n'est pas à vérifier. Les BL de retour ne sont pas concernés.
"""

from __future__ import annotations

from datetime import timedelta

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow, get_transitions
from frappe.utils import add_days, cint, flt, get_datetime, now_datetime

from customization_app import stock_entrepots as S

ROLE_PRINCIPAL = "Magasinier principal"
PRINCIPAUX_DEFAUT = ("hedi.ibidhi@gmail.com", "koubaawassim@gmail.com")
WORKFLOW = "Validation Magasin"
EN_ATTENTE, APPROUVE, REJETE = "En attente validation magasin", "Approved", "Rejected"
ACTION_APPROUVER, ACTION_REJETER, ACTION_EXCEPTIONNELLE = "Approve", "Reject", "Sortie exceptionnelle"
A_VERIFIER, CONFORME, ECART = "À vérifier", "Conforme", "Écart"
CHAMP_FLAG = "custom_requires_store_approval"
CHAMP_VERIF = "custom_sortie_verification"
CHAMP_SORTI_PAR, CHAMP_SORTI_LE = "custom_sorti_par", "custom_sorti_le"
CHAMP_VERIFIE_PAR, CHAMP_VERIFIE_LE, CHAMP_NOTE = "custom_verifie_par", "custom_verifie_le", "custom_verification_note"
EVENEMENT = "sm_bl_magasin"
LIMITE_HISTO = 30
RELANCE_APPROBATION_H = 4      # relance quotidienne : BL en attente depuis plus de 4 h
RELANCE_VERIFICATION_J = 2     # sorties exceptionnelles non vérifiées depuis plus de 2 jours


# ── Accès ─────────────────────────────────────────────────────────────────────

def principaux() -> list[str]:
    """Les utilisateurs actifs qui portent le rôle Magasinier principal."""
    porteurs = frappe.get_all("Has Role", filters={"role": ROLE_PRINCIPAL, "parenttype": "User"}, pluck="parent")
    if not porteurs:
        return []
    return frappe.get_all("User", filters={"name": ["in", porteurs], "enabled": 1}, pluck="name", order_by="name")


def alertes_pour() -> list[str]:
    """Qui reçoit le bandeau, la cloche et les relances : le MAGASINIER EN TITRE seulement — l'employé du réglage
    « responsable magasin » (Config Stock Entrepot, Hedi), s'il porte le rôle. Wassim, lui aussi magasinier principal,
    garde le droit d'approuver sans être alerté (« pour moi je veux pas avoir ça », 27/10). Réglage vide ou employé
    sans le rôle : tous les magasiniers principaux."""
    tous = principaux()
    emp = frappe.db.get_single_value(S.CONFIG, "responsable_verification")
    user = frappe.db.get_value("Employee", emp, "user_id") if emp else None
    return [user] if user and user in tous else tous


def est_principal(user: str | None = None) -> bool:
    """Rôle réellement posé sur la fiche User (pas `frappe.get_roles`, qui donne TOUS les rôles à Administrator :
    « seulement Hedi et moi », 27/10) — Administrator est donc un employé comme un autre ici."""
    return bool(frappe.db.exists("Has Role", {"parent": user or frappe.session.user, "parenttype": "User",
                                              "role": ROLE_PRINCIPAL}))


def _principal():
    if not est_principal():
        frappe.throw(_("Réservé au magasinier principal ({0}).").format(ROLE_PRINCIPAL), frappe.PermissionError)


def _champs() -> bool:
    return frappe.db.has_column("Delivery Note", CHAMP_VERIF)


# ── Hooks Delivery Note ───────────────────────────────────────────────────────

def _concerne(doc) -> bool:
    return bool(cint(doc.get(CHAMP_FLAG))) and not cint(doc.get("is_return")) and _champs()


def delivery_note_on_update(doc, method=None):
    """after_insert / on_update : un BL du Magasin qui ENTRE en attente d'approbation → le magasinier est prévenu."""
    if doc.docstatus != 0 or doc.get("workflow_state") != EN_ATTENTE or not _concerne(doc):
        return
    avant = doc.get_doc_before_save() if method == "on_update" else None
    if avant is not None and avant.get("workflow_state") == EN_ATTENTE:
        return
    _prevenir(alertes_pour(), _("🧾 BL {0} ({1}) à approuver : {2} article(s) du Magasin")
              .format(doc.name, doc.get("customer_name") or doc.customer, len(doc.items)), doc.name)


def delivery_note_on_submit(doc, method=None):
    """on_submit : qui a fait sortir les articles. Un magasinier principal = approbation normale ; quelqu'un
    d'autre (sortie exceptionnelle, ou validation automatique de la caisse / d'un retour Aramex) = sortie À VÉRIFIER."""
    if not _concerne(doc):
        return
    user = frappe.session.user
    valeurs = {CHAMP_SORTI_PAR: user, CHAMP_SORTI_LE: now_datetime()}
    if est_principal(user):
        valeurs[CHAMP_VERIF] = None
        doc.db_set(valeurs, update_modified=False)
        return
    valeurs[CHAMP_VERIF] = A_VERIFIER
    doc.db_set(valeurs, update_modified=False)
    doc.add_comment("Comment", _("Sortie exceptionnelle : BL validé par {0} sans le magasinier principal — à vérifier.")
                    .format(frappe.utils.get_fullname(user)))
    _prevenir([u for u in alertes_pour() if u != user],
              _("⚠️ Sortie exceptionnelle {0} par {1} : {2} article(s) à vérifier")
              .format(doc.name, frappe.utils.get_fullname(user), len(doc.items)), doc.name)


def _prevenir(users: list[str], sujet: str, name: str):
    """Cloche du Desk + rafraîchissement en direct (bandeau et onglet BL). Jamais bloquant."""
    for user in users:
        try:
            log = {"doctype": "Notification Log", "for_user": user, "type": "Alert", "subject": sujet}
            if name:
                log.update({"document_type": "Delivery Note", "document_name": name})
            frappe.get_doc(log).insert(ignore_permissions=True)
            frappe.publish_realtime(EVENEMENT, {"name": name}, user=user, after_commit=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "sorties_magasin: notification")


# ── Lecture ───────────────────────────────────────────────────────────────────

def _lignes(names: list[str]) -> dict[str, list[dict]]:
    if not names:
        return {}
    rows = frappe.get_all("Delivery Note Item", filters={"parent": ["in", names]},
                          fields=["parent", "item_code", "item_name", "qty", "uom", "warehouse", "against_sales_order"],
                          order_by="parent, idx")
    codes = list({r.item_code for r in rows})
    images = S._images(codes)
    # Stock de l'entrepôt de sortie, pour les articles suivis en stock : « stock après la sortie » (le BL validé a
    # déjà sorti la quantité ; pour un BL en attente, on montre stock actuel → après).
    suivis = set(frappe.get_all("Item", filters={"name": ["in", codes], "is_stock_item": 1}, pluck="name"))
    stocks = {(b.item_code, b.warehouse): flt(b.actual_qty, 6) for b in frappe.get_all(
        "Bin", filters={"item_code": ["in", list(suivis) or [""]], "warehouse": ["in", list({r.warehouse for r in rows if r.warehouse}) or [""]]},
        fields=["item_code", "warehouse", "actual_qty"])} if suivis else {}
    out = {}
    for r in rows:
        suivi = r.item_code in suivis
        out.setdefault(r.parent, []).append({"item_code": r.item_code, "item_name": r.item_name, "qte": flt(r.qty, 6),
                                             "uom": r.uom, "entrepot": r.warehouse, "image": images.get(r.item_code),
                                             "commande": r.against_sales_order, "suivi": suivi,
                                             "stock": stocks.get((r.item_code, r.warehouse), 0.0) if suivi else None})
    return out


CHAMPS_BL = ["name", "customer", "customer_name", "posting_date", "creation", "owner", "workflow_state", "docstatus",
             "custom_commande", "custom_livré_par", CHAMP_VERIF, CHAMP_SORTI_PAR, CHAMP_SORTI_LE,
             CHAMP_VERIFIE_PAR, CHAMP_VERIFIE_LE, CHAMP_NOTE]


def _bls(filtres: dict, order_by: str = "creation asc", limite: int | None = None) -> list[dict]:
    entetes = frappe.get_all("Delivery Note", filters=filtres, fields=CHAMPS_BL, order_by=order_by, limit=limite)
    lignes = _lignes([e.name for e in entetes])
    noms = {}
    def nom(u):
        if u and u not in noms:
            noms[u] = frappe.utils.get_fullname(u)
        return noms.get(u)
    out = []
    for e in entetes:
        ls = lignes.get(e.name, [])
        commande = e.custom_commande or next((l["commande"] for l in ls if l["commande"]), None)
        livre = frappe.db.get_value("Employee", e.get("custom_livré_par"), "employee_name") if e.get("custom_livré_par") else None
        quand = e.get(CHAMP_SORTI_LE) or e.creation
        out.append({"name": e.name, "client": e.customer_name or e.customer, "date": str(e.posting_date),
                    "creation": str(e.creation)[:16], "par": nom(e.owner), "commande": commande, "livre_par": livre,
                    "statut": e.workflow_state, "docstatus": e.docstatus,
                    "lignes": ls, "nb": len(ls), "unites": flt(sum(l["qte"] for l in ls), 6),
                    "verification": e.get(CHAMP_VERIF), "sorti_par": nom(e.get(CHAMP_SORTI_PAR)),
                    "sorti_par_user": e.get(CHAMP_SORTI_PAR), "sorti_le": str(e.get(CHAMP_SORTI_LE) or "")[:16] or None,
                    "verifie_par": nom(e.get(CHAMP_VERIFIE_PAR)), "verifie_le": str(e.get(CHAMP_VERIFIE_LE) or "")[:16] or None,
                    "note": e.get(CHAMP_NOTE),
                    "age_h": round((now_datetime() - get_datetime(quand)).total_seconds() / 3600, 1) if quand else None})
    return out


def _en_attente() -> list[dict]:
    return _bls({"docstatus": 0, "workflow_state": EN_ATTENTE, CHAMP_FLAG: 1, "is_return": 0})


@frappe.whitelist()
def compteur():
    """Pour le bandeau d'alerte du magasinier EN TITRE (toutes les pages du Desk) ; None pour tous les autres,
    y compris les autres magasiniers principaux."""
    if frappe.session.user not in alertes_pour() or not _champs():
        return None
    return {"a_approuver": frappe.db.count("Delivery Note", {"docstatus": 0, "workflow_state": EN_ATTENTE, CHAMP_FLAG: 1,
                                                             "is_return": 0}),
            "a_verifier": frappe.db.count("Delivery Note", {"docstatus": 1, CHAMP_VERIF: A_VERIFIER}),
            "ecarts": frappe.db.count("Delivery Note", {"docstatus": 1, CHAMP_VERIF: ECART})}


@frappe.whitelist()
def etat():
    """Onglet BL de la page Stock : les BL à approuver (tous) ; pour le magasinier principal, les sorties
    exceptionnelles à vérifier, les écarts non réglés et l'historique ; pour les autres, leurs propres sorties."""
    S._lecture()
    if not _champs():
        frappe.throw(_("Champs de vérification absents : lancer le patch ensure_sorties_magasin (bench migrate)."))
    principal = est_principal()
    out = {"principal": principal, "principaux": [frappe.utils.get_fullname(u) for u in principaux()],
           "a_approuver": _en_attente(), "a_verifier": [], "ecarts": [], "historique": [], "miennes": []}
    if principal:
        out["a_verifier"] = _bls({"docstatus": 1, CHAMP_VERIF: A_VERIFIER})
        out["ecarts"] = _bls({"docstatus": 1, CHAMP_VERIF: ECART}, "creation desc")
        out["historique"] = _bls({"docstatus": 1, CHAMP_FLAG: 1, "is_return": 0, CHAMP_SORTI_PAR: ["is", "set"],
                                  CHAMP_VERIF: ["in", ["", CONFORME]]}, f"{CHAMP_SORTI_LE} desc", LIMITE_HISTO)
    else:
        out["miennes"] = _bls({"docstatus": 1, CHAMP_SORTI_PAR: frappe.session.user,
                               CHAMP_VERIF: ["in", [A_VERIFIER, CONFORME, ECART]]}, f"{CHAMP_SORTI_LE} desc", LIMITE_HISTO)
    return out


# ── Actions ───────────────────────────────────────────────────────────────────

def _en_attente_doc(name):
    doc = frappe.get_doc("Delivery Note", name)
    if doc.docstatus != 0 or doc.get("workflow_state") != EN_ATTENTE:
        frappe.throw(_("{0} n’est pas en attente d’approbation du Magasin.").format(name))
    return doc


def actions_possibles(name) -> list[str]:
    return [t.get("action") for t in get_transitions(frappe.get_doc("Delivery Note", name))]


@frappe.whitelist(methods=["POST"])
def approuver(name):
    """Approbation normale par le magasinier principal : le BL est validé, le stock sort, rien à vérifier."""
    _principal()
    doc = _en_attente_doc(name)
    apply_workflow(doc, ACTION_APPROUVER)
    return _bls({"name": name})[0]


@frappe.whitelist(methods=["POST"])
def rejeter(name, motif=None):
    _principal()
    doc = _en_attente_doc(name)
    if (motif or "").strip():
        doc.add_comment("Comment", _("Rejeté par le magasinier principal : {0}").format(motif.strip()))
    apply_workflow(doc, ACTION_REJETER)
    return {"name": name, "statut": REJETE}


@frappe.whitelist(methods=["POST"])
def sortie_exceptionnelle(name, motif=None):
    """Magasinier absent : l'employé valide lui-même le BL (le stock sort) ; la sortie est tracée « À vérifier »
    par le hook on_submit. Un magasinier principal passe par l'approbation normale."""
    S._lecture()
    doc = _en_attente_doc(name)
    if est_principal():
        apply_workflow(doc, ACTION_APPROUVER)
        return _bls({"name": name})[0]
    if (motif or "").strip():
        doc.add_comment("Comment", _("Motif de la sortie exceptionnelle : {0}").format(motif.strip()))
    apply_workflow(doc, ACTION_EXCEPTIONNELLE)
    if (motif or "").strip():
        frappe.db.set_value("Delivery Note", name, CHAMP_NOTE, _("Motif : {0}").format(motif.strip()), update_modified=False)
    return _bls({"name": name})[0]


@frappe.whitelist(methods=["POST"])
def verifier(name, resultat, note=None):
    """Le magasinier principal vérifie une sortie exceptionnelle : Conforme, ou Écart (note obligatoire) — un écart
    reste listé jusqu'à ce qu'il soit passé Conforme (réglé). L'employé est prévenu d'un écart."""
    _principal()
    if resultat not in (CONFORME, ECART):
        frappe.throw(_("Résultat inconnu : {0}").format(resultat))
    doc = frappe.get_doc("Delivery Note", name)
    if doc.docstatus != 1 or doc.get(CHAMP_VERIF) not in (A_VERIFIER, ECART):
        frappe.throw(_("{0} n’est pas une sortie exceptionnelle à vérifier.").format(name))
    note = (note or "").strip()
    if resultat == ECART and not note:
        frappe.throw(_("Décrivez l’écart constaté."))
    ancienne = (doc.get(CHAMP_NOTE) or "").strip()
    texte = note if not ancienne or note == ancienne else (f"{ancienne}\n{note}" if note else ancienne)
    doc.db_set({CHAMP_VERIF: resultat, CHAMP_VERIFIE_PAR: frappe.session.user, CHAMP_VERIFIE_LE: now_datetime(),
                CHAMP_NOTE: texte or None}, update_modified=False)
    doc.add_comment("Comment", (_("Sortie exceptionnelle vérifiée : conforme.") if resultat == CONFORME
                                else _("Sortie exceptionnelle vérifiée : ÉCART — {0}").format(note)))
    sorti_par = doc.get(CHAMP_SORTI_PAR)
    if resultat == ECART and sorti_par and sorti_par != frappe.session.user:
        _prevenir([sorti_par], _("⚠️ Écart sur votre sortie exceptionnelle {0} : {1}").format(name, note[:120]), name)
    return _bls({"name": name})[0]


# ── Relance quotidienne ───────────────────────────────────────────────────────

def relance_quotidienne():
    """Cron daily : ce qui attend le magasinier principal depuis trop longtemps lui est rappelé (cloche + direct)."""
    if not _champs():
        return
    users = alertes_pour()
    if not users:
        return
    attente = frappe.db.count("Delivery Note", {"docstatus": 0, "workflow_state": EN_ATTENTE, CHAMP_FLAG: 1, "is_return": 0,
                                                "creation": ["<", now_datetime() - timedelta(hours=RELANCE_APPROBATION_H)]})
    verif = frappe.db.count("Delivery Note", {"docstatus": 1, CHAMP_VERIF: A_VERIFIER,
                                              CHAMP_SORTI_LE: ["<", add_days(now_datetime(), -RELANCE_VERIFICATION_J)]})
    if not attente and not verif:
        return
    morceaux = []
    if attente:
        morceaux.append(_("{0} BL du Magasin à approuver").format(attente))
    if verif:
        morceaux.append(_("{0} sortie(s) exceptionnelle(s) à vérifier").format(verif))
    _prevenir(users, "⏰ " + " · ".join(morceaux), "")
