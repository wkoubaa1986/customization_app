"""Manifeste Aramex : la feuille que signe le coursier a l'enlevement.

POURQUOI ICI ET PAS CHEZ ARAMEX
--------------------------------
L'application PC d'Aramex produisait un « Manifest Report » numerote (502, 504…) — voir les
PDF attaches aux taches Livraison, ex. « mani 12-08.pdf ». L'API n'a pas d'operation
equivalente : elle cree des colis, rend l'etiquette, demande un enlevement, bloque, suit. La
feuille se construit donc ici, a l'identique, depuis ce que le suivi a garde a la creation.

COMMENT ON S'EN SERT
--------------------
/manifeste-aramex propose les colis CREES ET PAS ENCORE ENLEVES (suivi « Créé », pas encore
sur un manifeste) ; on coche ceux qu'on remet au coursier, « Générer » cree le document
« Manifeste Aramex » (numero MAN-AAAA-NNNNN), rattache chaque Suivi Aramex, et ouvre la
feuille a imprimer. Un colis rattache ne se propose plus ; un manifeste se rouvre et se
reimprime depuis la liste.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, formatdate, getdate, nowdate

from customization_app.livraison_aramex import DOCTYPE_SUIVI, PRECISION, _commandes_du_bordereau

DOCTYPE_MANIFESTE = "Manifeste Aramex"
# Un colis est « a enlever » tant que son dernier evenement est la creation (SH014) — ou que
# le suivi n'a rien d'autre a dire que « Créé ». Des qu'Aramex l'a recu, il n'a plus rien a
# faire sur un manifeste.
STATUTS_A_ENLEVER = ("Créé", "Cree")


def _lecture():
    if frappe.session.user == "Guest" or not frappe.has_permission("Sales Order", "read"):
        frappe.throw(_("Accès non autorisé"), frappe.PermissionError)


def _completer(s) -> dict:
    """Une ligne de manifeste depuis un Suivi Aramex, completee par la commande quand le suivi
    ne sait pas (bordereau saisi a la main : ni destinataire, ni ville, ni montant)."""
    commande = s.get("commande")
    if not commande:
        commandes = _commandes_du_bordereau(s.name)
        commande = commandes[0] if commandes else None
    so = frappe.db.get_value("Sales Order", commande,
                             ["customer_name", "grand_total", "contact_mobile", "contact_phone",
                              "shipping_address_name", "customer_address"], as_dict=True) \
        if commande else None
    ville = s.get("ville")
    if not ville and so:
        adresse = so.shipping_address_name or so.customer_address
        ville = frappe.db.get_value("Address", adresse, "city") if adresse else ""
    return {
        "bordereau": s.name,
        "commande": commande or "",
        "destinataire": s.get("destinataire") or (so.customer_name if so else "") or "",
        "telephone": (so.contact_mobile or so.contact_phone) if so else "",
        "ville": ville or "",
        "poids": flt(s.get("poids")),
        "pieces": cint(s.get("pieces")) or 1,
        "cod": flt(s.get("cod")) if s.get("cod") else (flt(so.grand_total, PRECISION) if so else 0),
        "cree_le": str(s.get("cree_le") or s.get("creation") or ""),
        "statut": s.get("statut") or "",
        "par_api": bool(s.get("cree_le")),
    }


@frappe.whitelist()
def candidats():
    """Les colis a proposer : crees (par l'API ou saisis) et pas encore enleves ni manifestes."""
    _lecture()
    suivis = frappe.get_all(
        DOCTYPE_SUIVI,
        filters={"livre": 0, "manifeste": ("in", ["", None])},
        or_filters={"cree_le": ("is", "set"), "statut": ("in", list(STATUTS_A_ENLEVER))},
        fields=["name", "statut", "cree_le", "creation", "commande", "destinataire", "ville",
                "cod", "pieces", "poids", "code"],
        order_by="cree_le desc, creation desc", limit_page_length=0)
    aujourd_hui = nowdate()
    out = []
    for s in suivis:
        # Un colis cree par l'API mais deja parti (Aramex l'a scanne) ne se remet pas sur une
        # feuille : il n'a plus rien a faire avec le coursier.
        if s.cree_le and s.statut and s.statut not in STATUTS_A_ENLEVER:
            continue
        ligne = _completer(s)
        ligne["coche"] = bool(s.cree_le and str(getdate(s.cree_le)) == aujourd_hui)
        out.append(ligne)
    return out


@frappe.whitelist()
def generer(bordereaux, notes=None):
    """Cree le manifeste pour les bordereaux coches et les y rattache. -> {name}."""
    _lecture()
    if not frappe.has_permission(DOCTYPE_MANIFESTE, "create"):
        frappe.throw(_("Vous n'avez pas le droit de générer un manifeste."), frappe.PermissionError)
    bordereaux = frappe.parse_json(bordereaux) if isinstance(bordereaux, str) else (bordereaux or [])
    bordereaux = [str(b).strip() for b in bordereaux if str(b).strip()]
    if not bordereaux:
        frappe.throw(_("Cochez au moins un colis."))
    deja = frappe.get_all(DOCTYPE_SUIVI, filters={"name": ("in", bordereaux),
                                                  "manifeste": ("is", "set")},
                          fields=["name", "manifeste"])
    if deja:
        frappe.throw(_("Déjà sur un manifeste : {0}.").format(
            ", ".join("%s (%s)" % (d.name, d.manifeste) for d in deja)))
    suivis = {s.name: s for s in frappe.get_all(
        DOCTYPE_SUIVI, filters={"name": ("in", bordereaux)},
        fields=["name", "statut", "cree_le", "creation", "commande", "destinataire", "ville",
                "cod", "pieces", "poids"])}
    # Un bordereau saisi a la main sans que le suivi ait encore ete demande n'a pas de
    # document Suivi Aramex : on le cree, en « Créé » — c'est un colis qui part, le cron de
    # 16h dira la suite.
    for b in [b for b in bordereaux if b not in suivis]:
        doc_s = frappe.get_doc({"doctype": DOCTYPE_SUIVI, "reference": b, "statut": "Créé",
                                "livre": 0, "etapes_franchies": 1, "etapes_total": 6})
        doc_s.insert(ignore_permissions=True)
        suivis[b] = frappe._dict(name=b, statut="Créé", cree_le=None, creation=doc_s.creation,
                                 commande=None, destinataire=None, ville=None, cod=None,
                                 pieces=None, poids=None)

    doc = frappe.new_doc(DOCTYPE_MANIFESTE)
    doc.date = nowdate()
    doc.genere_par = frappe.session.user
    doc.notes = notes or ""
    for b in bordereaux:
        l = _completer(suivis[b])
        doc.append("lignes", {k: l[k] for k in ("bordereau", "commande", "destinataire",
                                               "telephone", "ville", "poids", "pieces", "cod")})
    doc.total_colis = len(doc.lignes)
    doc.total_pieces = sum(cint(l.pieces) or 1 for l in doc.lignes)
    doc.total_poids = round(sum(flt(l.poids) for l in doc.lignes), 2)
    doc.total_cod = round(sum(flt(l.cod) for l in doc.lignes), PRECISION)
    doc.insert(ignore_permissions=True)
    for b in bordereaux:
        frappe.db.set_value(DOCTYPE_SUIVI, b, "manifeste", doc.name, update_modified=False)
    for b in bordereaux:
        commande = suivis[b].get("commande") or (_commandes_du_bordereau(b) or [None])[0]
        if commande:
            frappe.get_doc({
                "doctype": "Comment", "comment_type": "Info",
                "reference_doctype": "Sales Order", "reference_name": commande,
                "content": _("🧾 Colis Aramex {0} sur le manifeste {1} du {2}.").format(
                    b, doc.name, formatdate(doc.date, "dd/MM/yyyy")),
            }).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"name": doc.name}


@frappe.whitelist()
def retirer(bordereau):
    """Enleve un colis d'un manifeste (coche par erreur) : le manifeste se recalcule, le colis
    redevient proposable. Un manifeste vide est supprime."""
    _lecture()
    if not frappe.has_permission(DOCTYPE_MANIFESTE, "write"):
        frappe.throw(_("Accès non autorisé"), frappe.PermissionError)
    nom = frappe.db.get_value(DOCTYPE_SUIVI, bordereau, "manifeste")
    if not nom:
        frappe.throw(_("Ce colis n'est sur aucun manifeste."))
    doc = frappe.get_doc(DOCTYPE_MANIFESTE, nom)
    doc.lignes = [l for l in doc.lignes if l.bordereau != bordereau]
    frappe.db.set_value(DOCTYPE_SUIVI, bordereau, "manifeste", None, update_modified=False)
    if not doc.lignes:
        frappe.delete_doc(DOCTYPE_MANIFESTE, nom, ignore_permissions=True)
        frappe.db.commit()
        return {"name": None}
    doc.total_colis = len(doc.lignes)
    doc.total_pieces = sum(cint(l.pieces) or 1 for l in doc.lignes)
    doc.total_poids = round(sum(flt(l.poids) for l in doc.lignes), 2)
    doc.total_cod = round(sum(flt(l.cod) for l in doc.lignes), PRECISION)
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return {"name": nom}


def get_data(name) -> dict:
    """Le manifeste a imprimer, avec l'expediteur."""
    _lecture()
    doc = frappe.get_doc(DOCTYPE_MANIFESTE, name)
    return {
        "name": doc.name, "date": str(doc.date), "notes": doc.notes or "",
        "genere_par": doc.genere_par,
        "lignes": [l.as_dict() for l in doc.lignes],
        "total_colis": doc.total_colis, "total_pieces": doc.total_pieces,
        "total_poids": doc.total_poids, "total_cod": doc.total_cod,
        "expediteur": expediteur(),
    }


def liste(limite=30) -> list:
    return frappe.get_all(DOCTYPE_MANIFESTE, fields=["name", "date", "total_colis",
                                                     "total_poids", "total_cod", "genere_par"],
                          order_by="creation desc", limit_page_length=limite)


def expediteur() -> dict:
    cfg = frappe.db.get_singles_dict("Config Livraison Aramex") or {}
    return {"societe": cfg.get("expediteur_societe") or cfg.get("expediteur_nom") or "",
            "compte": cfg.get("api_account_number") or "",
            "adresse": ", ".join(x for x in (cfg.get("expediteur_adresse"),
                                             cfg.get("expediteur_ville")) if x),
            "telephone": cfg.get("expediteur_telephone") or ""}
