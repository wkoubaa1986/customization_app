"""Stock par entrepôt — page Stock « stock-entrepots », pensée pour le téléphone (demande du 01/10/2026).

Quatre onglets :
- Solde : le stock d'un entrepôt (ou de tous), article par article. Un employé dont la fiche porte un
  entrepôt (Employee.custom_warehouse : le stock de son véhicule) le voit par défaut.
- Sorties : sur une période, les sorties de stock par article ; pour chaque sortie, la pièce (BL,
  facture, transfert…), la commande et les tâches de cette commande (Tache de travail.commande_client).
- Transfert (responsable magasin) : écriture de stock « Transfer interne » d'un entrepôt à un autre,
  soumise directement ; seuls les articles suivis en stock sont proposés.
- Remise à zéro (responsable magasin) : un entrepôt actif autre que le Magasin (réglage « Config Stock
  Entrepot ») est ramené à zéro par transferts avec le Magasin — les quantités négatives sont apportées
  depuis le Magasin, les positives y retournent. L'écart d'inventaire ne vit plus que sur le Magasin.

Responsable magasin = rôle « Responsable magasin » (ou System Manager).
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, get_first_day, getdate, nowdate

from customization_app.zones_magasin import _societe

CONFIG = "Config Stock Entrepot"
CONFIG_EXCLU = "Config Stock Entrepot Exclu"
MAGASIN_DEFAUT = "Magasins - A&S"
TYPE_TRANSFERT = "Transfer interne"
PURPOSE = "Material Transfer"
MARQUE = "Stock par entrepôt"
RESPONSABLES = {"Responsable magasin", "System Manager"}
LECTEURS = RESPONSABLES | {"Stock User", "Stock Manager"}
LIMITE_RECHERCHE = 30
LIMITE_DETAIL = 300

LIBELLES_PIECE = {"Delivery Note": "BL", "Sales Invoice": "Facture", "Stock Entry": "Écriture de stock",
                  "Stock Reconciliation": "Rapprochement", "Purchase Receipt": "Retour fournisseur",
                  "Purchase Invoice": "Retour fournisseur", "POS Invoice": "Ticket de caisse"}


# ── Accès ────────────────────────────────────────────────────────────────────

def est_responsable(user: str | None = None) -> bool:
    return bool(RESPONSABLES & set(frappe.get_roles(user)))


def _lecture():
    if not LECTEURS & set(frappe.get_roles()):
        frappe.throw(_("La page Stock par entrepôt n'est pas ouverte à votre compte."), frappe.PermissionError)


def _responsable():
    if not est_responsable():
        frappe.throw(_("Réservé au responsable magasin."), frappe.PermissionError)


# ── Réglage et entrepôts ─────────────────────────────────────────────────────

def magasin() -> str:
    """L'entrepôt qui porte les écarts (réglage), « Magasins - A&S » tant que le réglage n'est pas fait."""
    return frappe.db.get_single_value(CONFIG, "entrepot_magasin") or MAGASIN_DEFAUT


def exclus() -> set[str]:
    return set(frappe.get_all(CONFIG_EXCLU, filters={"parent": CONFIG, "parenttype": CONFIG}, pluck="entrepot"))


def entrepots() -> list[dict]:
    """Entrepôts actifs (hors groupes) de la société, avec les employés qui y sont rattachés."""
    employes = {}
    for e in frappe.get_all("Employee", filters={"status": "Active", "custom_warehouse": ["is", "set"]},
                            fields=["employee_name", "custom_warehouse"], order_by="employee_name"):
        employes.setdefault(e.custom_warehouse, []).append(e.employee_name)
    m = magasin()
    return [{"name": w.name, "libelle": w.warehouse_name, "employes": employes.get(w.name, []), "magasin": w.name == m}
            for w in frappe.get_all("Warehouse", filters={"is_group": 0, "disabled": 0, "company": _societe()},
                                    fields=["name", "warehouse_name"], order_by="lft")]


def _verifier_entrepot(entrepot: str) -> None:
    w = frappe.db.get_value("Warehouse", entrepot, ["is_group", "disabled", "company"], as_dict=True)
    if not w or w.is_group or w.disabled or w.company != _societe():
        frappe.throw(_("Entrepôt inutilisable : {0}").format(entrepot))


@frappe.whitelist()
def get_context():
    _lecture()
    liste = entrepots()
    actifs = {w["name"] for w in liste}
    emp = frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"},
                              ["employee_name", "custom_warehouse"], as_dict=True)
    mien = emp.custom_warehouse if emp and emp.custom_warehouse in actifs else None
    m = magasin()
    return {"entrepots": liste, "mien": mien, "employe": emp.employee_name if emp else None,
            "defaut": mien or (m if m in actifs else (liste[0]["name"] if liste else None)),
            "magasin": m, "responsable": est_responsable(), "aujourdhui": nowdate()}


# ── Recherche d'articles ─────────────────────────────────────────────────────

def _filtre_mots(texte: str | None, valeurs: dict, prefixe: str = "i") -> str:
    """« membrane 600 » : chaque mot doit apparaître dans le code ou le nom, dans n'importe quel ordre."""
    conds = []
    for n, mot in enumerate((texte or "").split()):
        valeurs[f"m{n}"] = f"%{mot}%"
        conds.append(f"({prefixe}.name like %(m{n})s or {prefixe}.item_name like %(m{n})s)")
    return " and ".join(conds)


# ── Solde ────────────────────────────────────────────────────────────────────

@frappe.whitelist()
def get_solde(entrepot=None, recherche=None, negatifs=0):
    """Stock non nul, par article. Sans entrepôt : tous les entrepôts, la quantité de chacun sous l'article."""
    _lecture()
    valeurs = {"societe": _societe()}
    conds = ["abs(b.actual_qty) > 0.000001", "w.company = %(societe)s"]
    if entrepot:
        conds.append("b.warehouse = %(entrepot)s")
        valeurs["entrepot"] = entrepot
    if cint(negatifs):
        conds.append("b.actual_qty < 0")
    mots = _filtre_mots(recherche, valeurs)
    if mots:
        conds.append(mots)
    rows = frappe.db.sql(f"""select b.item_code, i.item_name, i.image, i.stock_uom, i.custom_emplacement_magasin as zones,
                                    b.warehouse, w.warehouse_name, b.actual_qty, b.stock_value
                             from tabBin b
                             join tabItem i on i.name = b.item_code
                             join tabWarehouse w on w.name = b.warehouse
                             where {" and ".join(conds)}
                             order by i.item_name, b.item_code, w.lft""", valeurs, as_dict=True)
    voir_valeur = est_responsable()
    articles, par_code = [], {}
    for r in rows:
        a = par_code.get(r.item_code)
        if not a:
            a = par_code[r.item_code] = {"item_code": r.item_code, "item_name": r.item_name, "image": r.image,
                                         "uom": r.stock_uom, "zones": r.zones, "qte": 0.0, "entrepots": []}
            articles.append(a)
        a["qte"] = flt(a["qte"] + flt(r.actual_qty), 6)
        a["entrepots"].append({"entrepot": r.warehouse, "libelle": r.warehouse_name, "qte": flt(r.actual_qty, 6)})
    out = {"articles": articles, "negatifs": sum(1 for a in articles if a["qte"] < 0),
           "positifs": sum(1 for a in articles if a["qte"] > 0)}
    if voir_valeur:
        out["valeur"] = flt(sum(flt(r.stock_value) for r in rows), 3)
    return out


# ── Sorties ──────────────────────────────────────────────────────────────────

def _periode(debut, fin) -> tuple[str, str]:
    fin = getdate(fin or nowdate())
    debut = getdate(debut or get_first_day(fin))
    if debut > fin:
        debut, fin = fin, debut
    return str(debut), str(fin)


def _conditions_sorties(entrepot, debut, fin, valeurs: dict) -> list[str]:
    valeurs.update({"debut": debut, "fin": fin, "societe": _societe()})
    conds = ["sle.is_cancelled = 0", "sle.actual_qty < 0", "sle.posting_date between %(debut)s and %(fin)s",
             "sle.company = %(societe)s"]
    if entrepot:
        conds.append("sle.warehouse = %(entrepot)s")
        valeurs["entrepot"] = entrepot
    return conds


@frappe.whitelist()
def get_sorties(entrepot=None, debut=None, fin=None, recherche=None):
    """Sorties de stock de la période, totalisées par article (le détail se charge à l'ouverture d'un article)."""
    _lecture()
    debut, fin = _periode(debut, fin)
    valeurs = {}
    conds = _conditions_sorties(entrepot, debut, fin, valeurs)
    mots = _filtre_mots(recherche, valeurs)
    if mots:
        conds.append(mots)
    rows = frappe.db.sql(f"""select sle.item_code, i.item_name, i.image, i.stock_uom as uom,
                                    sum(-sle.actual_qty) as qte, count(*) as mouvements,
                                    max(sle.posting_date) as derniere
                             from `tabStock Ledger Entry` sle
                             join tabItem i on i.name = sle.item_code
                             where {" and ".join(conds)}
                             group by sle.item_code, i.item_name, i.image, i.stock_uom
                             order by qte desc, i.item_name""", valeurs, as_dict=True)
    for r in rows:
        r.qte = flt(r.qte, 6)
        r.derniere = str(r.derniere)
    return {"articles": rows, "debut": debut, "fin": fin, "mouvements": sum(cint(r.mouvements) for r in rows)}


@frappe.whitelist()
def get_sorties_article(item_code, entrepot=None, debut=None, fin=None):
    """Chaque sortie de l'article sur la période : pièce, commande, client, tâches de la commande."""
    _lecture()
    debut, fin = _periode(debut, fin)
    valeurs = {"item": item_code, "limite": LIMITE_DETAIL}
    conds = _conditions_sorties(entrepot, debut, fin, valeurs) + ["sle.item_code = %(item)s"]
    rows = frappe.db.sql(f"""select sle.posting_date, sle.posting_time, sle.warehouse, sle.voucher_type, sle.voucher_no,
                                    sle.voucher_detail_no, -sle.actual_qty as qte
                             from `tabStock Ledger Entry` sle
                             where {" and ".join(conds)}
                             order by sle.posting_date desc, sle.posting_time desc, sle.creation desc
                             limit %(limite)s""", valeurs, as_dict=True)
    return {"lignes": enrichir_sorties(rows), "limite": LIMITE_DETAIL}


def _par_nom(doctype: str, noms, champs: list[str]) -> dict:
    noms = list({n for n in noms if n})
    if not noms:
        return {}
    return {r.name: r for r in frappe.get_all(doctype, filters={"name": ["in", noms]}, fields=["name"] + champs)}


def enrichir_sorties(rows: list[dict]) -> list[dict]:
    """Ajoute à chaque sortie du grand livre sa pièce lisible, sa commande, son client et ses tâches."""
    par_type = {}
    for r in rows:
        par_type.setdefault(r.voucher_type, []).append(r)
    bl_lignes = _par_nom("Delivery Note Item", [r.voucher_detail_no for r in par_type.get("Delivery Note", [])],
                         ["against_sales_order"])
    bls = _par_nom("Delivery Note", [r.voucher_no for r in par_type.get("Delivery Note", [])],
                   ["customer_name", "custom_commande", "`custom_livré_par` as livre_par", "is_return"])
    fa_lignes = _par_nom("Sales Invoice Item", [r.voucher_detail_no for r in par_type.get("Sales Invoice", [])],
                         ["sales_order"])
    fas = _par_nom("Sales Invoice", [r.voucher_no for r in par_type.get("Sales Invoice", [])],
                   ["customer_name", "is_return"])
    se_lignes = _par_nom("Stock Entry Detail", [r.voucher_detail_no for r in par_type.get("Stock Entry", [])],
                         ["t_warehouse"])
    ses = _par_nom("Stock Entry", [r.voucher_no for r in par_type.get("Stock Entry", [])],
                   ["stock_entry_type", "purpose", "remarks"])
    livreurs = _par_nom("Employee", [b.livre_par for b in bls.values()], ["employee_name"])

    out = []
    for r in rows:
        l = {"date": str(r.posting_date), "heure": str(r.posting_time or "")[:5], "entrepot": r.warehouse,
             "qte": flt(r.qte, 6), "type": r.voucher_type, "libelle": LIBELLES_PIECE.get(r.voucher_type, r.voucher_type),
             "piece": r.voucher_no, "commande": None, "client": None, "vers": None, "par": None, "taches": []}
        if r.voucher_type == "Delivery Note":
            bl = bls.get(r.voucher_no) or {}
            l["commande"] = (bl_lignes.get(r.voucher_detail_no) or {}).get("against_sales_order") or bl.get("custom_commande")
            l["client"] = bl.get("customer_name")
            l["par"] = (livreurs.get(bl.get("livre_par")) or {}).get("employee_name")
            if bl.get("is_return"):
                l["libelle"] = "BL retour"
        elif r.voucher_type == "Sales Invoice":
            fa = fas.get(r.voucher_no) or {}
            l["commande"] = (fa_lignes.get(r.voucher_detail_no) or {}).get("sales_order")
            l["client"] = fa.get("customer_name")
        elif r.voucher_type == "Stock Entry":
            se = ses.get(r.voucher_no) or {}
            l["vers"] = (se_lignes.get(r.voucher_detail_no) or {}).get("t_warehouse")
            l["libelle"] = "Transfert" if se.get("purpose") == PURPOSE else (se.get("stock_entry_type") or l["libelle"])
            l["remarque"] = (se.get("remarks") or "")[:140]
        out.append(l)

    taches = _taches_des_commandes({l["commande"] for l in out if l["commande"]})
    for l in out:
        if l["commande"]:
            proches = sorted(taches.get(l["commande"], []), key=lambda t: abs((getdate(t["date"] or l["date"])
                                                                               - getdate(l["date"])).days))
            l["taches"] = proches[:3]
    return out


def _taches_des_commandes(commandes: set[str]) -> dict[str, list[dict]]:
    if not commandes:
        return {}
    taches = frappe.get_all("Tache de travail", filters={"commande_client": ["in", list(commandes)]},
                            fields=["name", "commande_client", "custom_type_dintervention", "custom_choix_du_staff",
                                    "status", "starts_on"], order_by="starts_on desc")
    noms = _par_nom("Employee", [t.custom_choix_du_staff for t in taches], ["employee_name"])
    out = {}
    for t in taches:
        out.setdefault(t.commande_client, []).append({
            "name": t.name, "type": t.custom_type_dintervention, "statut": t.status,
            "employe": (noms.get(t.custom_choix_du_staff) or {}).get("employee_name") or t.custom_choix_du_staff,
            "date": str(getdate(t.starts_on)) if t.starts_on else None})
    return out


# ── Transfert entre entrepôts (responsable) ──────────────────────────────────

def _type_transfert() -> str:
    if frappe.db.get_value("Stock Entry Type", TYPE_TRANSFERT, "purpose") == PURPOSE:
        return TYPE_TRANSFERT
    return PURPOSE


def _quantites(codes: list[str], entrepots_: list[str]) -> dict[tuple[str, str], float]:
    if not codes or not entrepots_:
        return {}
    return {(b.item_code, b.warehouse): flt(b.actual_qty, 6)
            for b in frappe.get_all("Bin", filters={"item_code": ["in", codes], "warehouse": ["in", entrepots_]},
                                    fields=["item_code", "warehouse", "actual_qty"])}


@frappe.whitelist()
def rechercher_articles(txt, source=None, cible=None):
    """Articles suivis en stock (actifs) dont le code ou le nom contient chaque mot tapé, code exact d'abord."""
    _responsable()
    txt = (txt or "").strip()
    if len(txt) < 2:
        return []
    valeurs = {"t": txt, "p": f"{txt}%", "n": LIMITE_RECHERCHE}
    rows = frappe.db.sql(f"""select i.name as item_code, i.item_name, i.image, i.stock_uom as uom,
                                    i.custom_emplacement_magasin as zones
                             from tabItem i
                             where i.is_stock_item = 1 and i.disabled = 0 and {_filtre_mots(txt, valeurs)}
                             order by (i.name = %(t)s) desc, (i.name like %(p)s) desc, length(i.name), i.name
                             limit %(n)s""", valeurs, as_dict=True)
    qtes = _quantites([r.item_code for r in rows], [w for w in (source, cible) if w])
    for r in rows:
        r["qte_source"] = qtes.get((r.item_code, source), 0.0)
        r["qte_cible"] = qtes.get((r.item_code, cible), 0.0)
    return rows


def ecriture_transfert(source: str, cible: str, lignes: list[tuple[str, float]], remarque: str):
    """Crée et soumet un transfert source → cible. lignes : [(item_code, qte)], qte > 0."""
    doc = frappe.new_doc("Stock Entry")
    doc.update({"stock_entry_type": _type_transfert(), "purpose": PURPOSE, "company": _societe(),
                "from_warehouse": source, "to_warehouse": cible, "remarks": remarque})
    for item_code, qte in lignes:
        doc.append("items", {"item_code": item_code, "qty": qte, "s_warehouse": source, "t_warehouse": cible})
    avant = len(frappe.local.message_log)
    doc.insert()
    doc.submit()
    # Entrer dans un stock négatif fait afficher par ERPNext, ARTICLE PAR ARTICLE, « The stock … was negative
    # on … » (stock_ledger.validate_previous_sle_qty) : c'est précisément le but d'une remise à zéro, et
    # l'ajustement de valorisation est annoncé dans l'aperçu. On retire CET avertissement seulement — une
    # vraie erreur lève avant d'arriver ici, avec son message.
    titre = _("Warning on Negative Stock")
    frappe.local.message_log[avant:] = [m for m in frappe.local.message_log[avant:]
                                        if not (isinstance(m, dict) and m.get("title") == titre)]
    return doc


def _articles_transferables(codes: list[str]) -> dict[str, str | None]:
    """{code: motif de refus ou None} — un transfert n'accepte que des articles actifs suivis en stock."""
    infos = _par_nom("Item", codes, ["disabled", "is_stock_item"])
    motifs = {}
    for c in codes:
        i = infos.get(c)
        motifs[c] = (_("article inconnu") if not i else _("article désactivé") if i.disabled
                     else _("article non suivi en stock") if not i.is_stock_item else None)
    return motifs


@frappe.whitelist(methods=["POST"])
def creer_transfert(source, cible, lignes, remarque=None):
    """Transfert d'articles d'un entrepôt à un autre, soumis directement. lignes : [{item_code, qte}]."""
    _responsable()
    _verifier_entrepot(source)
    _verifier_entrepot(cible)
    if source == cible:
        frappe.throw(_("L’entrepôt de départ et celui d’arrivée sont les mêmes."))
    cumul = {}
    for l in frappe.parse_json(lignes) if isinstance(lignes, str) else (lignes or []):
        qte = flt(l.get("qte"), 6)
        if l.get("item_code") and qte > 0:
            cumul[l["item_code"]] = flt(cumul.get(l["item_code"], 0) + qte, 6)
    if not cumul:
        frappe.throw(_("Ajoutez au moins un article avec une quantité."))
    refus = {c: m for c, m in _articles_transferables(list(cumul)).items() if m}
    if refus:
        frappe.throw("<br>".join(f"{frappe.utils.escape_html(c)} : {m}" for c, m in refus.items()))
    texte = f"{MARQUE} — transfert" + (f" — {remarque.strip()}" if (remarque or "").strip() else "")
    doc = ecriture_transfert(source, cible, list(cumul.items()), texte)
    return {"name": doc.name, "lignes": len(cumul)}


@frappe.whitelist()
def transferts_recents(limite=15, entrepot=None):
    """Derniers transferts (soumis ou annulés), lignes comprises."""
    _responsable()
    filtres = {"purpose": PURPOSE, "docstatus": ["in", [1, 2]], "company": _societe()}
    ou = [["from_warehouse", "=", entrepot], ["to_warehouse", "=", entrepot]] if entrepot else None
    entetes = frappe.get_all("Stock Entry", filters=filtres, or_filters=ou,
                             fields=["name", "posting_date", "posting_time", "docstatus", "from_warehouse", "to_warehouse",
                                     "remarks", "owner"], order_by="creation desc", limit=min(cint(limite) or 15, 50))
    if not entetes:
        return []
    lignes = {}
    for d in frappe.get_all("Stock Entry Detail", filters={"parent": ["in", [e.name for e in entetes]]},
                            fields=["parent", "item_code", "item_name", "qty", "s_warehouse", "t_warehouse"], order_by="idx"):
        lignes.setdefault(d.parent, []).append({"item_code": d.item_code, "item_name": d.item_name, "qte": flt(d.qty, 6),
                                                "de": d.s_warehouse, "vers": d.t_warehouse})
    return [{"name": e.name, "date": str(e.posting_date), "heure": str(e.posting_time or "")[:5], "docstatus": e.docstatus,
             "de": e.from_warehouse, "vers": e.to_warehouse, "remarque": e.remarks or "",
             "par": frappe.utils.get_fullname(e.owner), "lignes": lignes.get(e.name, [])} for e in entetes]


@frappe.whitelist(methods=["POST"])
def annuler_transfert(name):
    _responsable()
    doc = frappe.get_doc("Stock Entry", name)
    if doc.purpose != PURPOSE or doc.docstatus != 1:
        frappe.throw(_("{0} n’est pas un transfert soumis.").format(name))
    doc.cancel()
    return True


# ── Remise à zéro d'un entrepôt (responsable) ────────────────────────────────

def _verifier_cible_zero(entrepot: str) -> None:
    _verifier_entrepot(entrepot)
    if entrepot == magasin():
        frappe.throw(_("Le Magasin porte les écarts : il n’est jamais remis à zéro."))
    if entrepot in exclus():
        frappe.throw(_("{0} est exclu de la remise à zéro (réglage).").format(entrepot))
    _verifier_entrepot(magasin())


def lignes_a_zero(entrepot: str) -> list[dict]:
    """Le stock non nul de l'entrepôt, avec le mouvement qui le ramène à zéro et, le cas échéant, le motif
    qui empêche de le faire (article désactivé ou plus suivi en stock)."""
    rows = frappe.db.sql("""select b.item_code, i.item_name, i.image, i.stock_uom as uom, i.disabled, i.is_stock_item,
                                   b.actual_qty, b.stock_value
                            from tabBin b join tabItem i on i.name = b.item_code
                            where b.warehouse = %s and abs(b.actual_qty) > 0.000001
                            order by (b.actual_qty < 0) desc, i.item_name""", entrepot, as_dict=True)
    out = []
    for r in rows:
        qte = flt(r.actual_qty, 6)
        out.append({"item_code": r.item_code, "item_name": r.item_name, "image": r.image, "uom": r.uom, "qte": qte,
                    "valeur": flt(r.stock_value, 3), "sens": "apport" if qte < 0 else "retour",
                    "bloque": _("article désactivé") if r.disabled else _("article non suivi en stock")
                    if not r.is_stock_item else None})
    return out


@frappe.whitelist()
def entrepots_a_zero():
    """Les entrepôts qu'on peut remettre à zéro (actifs, hors Magasin et hors exclus), avec leur état."""
    _responsable()
    m, ex = magasin(), exclus()
    liste = [w for w in entrepots() if w["name"] != m and w["name"] not in ex]
    stats = {}
    if liste:
        for s in frappe.db.sql("""select warehouse, sum(actual_qty < 0) as negatifs, sum(actual_qty > 0) as positifs,
                                         sum(case when actual_qty < 0 then -actual_qty else 0 end) as qte_neg,
                                         sum(case when actual_qty > 0 then actual_qty else 0 end) as qte_pos,
                                         sum(stock_value) as valeur
                                  from tabBin where warehouse in %(w)s and abs(actual_qty) > 0.000001
                                  group by warehouse""", {"w": [w["name"] for w in liste]}, as_dict=True):
            stats[s.warehouse] = s
    for w in liste:
        s = stats.get(w["name"]) or {}
        w.update({"negatifs": cint(s.get("negatifs")), "positifs": cint(s.get("positifs")),
                  "qte_neg": flt(s.get("qte_neg"), 6), "qte_pos": flt(s.get("qte_pos"), 6),
                  "valeur": flt(s.get("valeur"), 3)})
    return {"magasin": m, "exclus": sorted(ex), "entrepots": liste}


@frappe.whitelist()
def apercu_zero(entrepot):
    """Ce que la remise à zéro va transférer, et l'ajustement de valorisation qu'ERPNext passera.

    Un stock négatif garde une valeur comptable (négative) calculée aux taux de ses sorties ; l'apport du
    Magasin entre au taux du Magasin. La différence part au compte d'ajustement de stock : on l'estime
    ici (taux actuel du Magasin) pour qu'elle ne surprenne pas. Les retours, eux, sortent et entrent au
    même taux : aucun ajustement."""
    _responsable()
    _verifier_cible_zero(entrepot)
    m = magasin()
    lignes = lignes_a_zero(entrepot)
    taux = {b.item_code: flt(b.valuation_rate)
            for b in frappe.get_all("Bin", filters={"warehouse": m, "item_code": ["in", [l["item_code"] for l in lignes]]},
                                    fields=["item_code", "valuation_rate"])} if lignes else {}
    for l in lignes:
        l["ajustement"] = flt(-l["valeur"] + l["qte"] * taux.get(l["item_code"], 0), 3) if l["sens"] == "apport" else 0.0
    return {"entrepot": entrepot, "magasin": m, "lignes": lignes}


@frappe.whitelist()
def get_quantites(items, source=None, cible=None):
    """{code: [qte source, qte cible]} — pour remettre à jour le panier quand on change d'entrepôt."""
    _responsable()
    codes = frappe.parse_json(items) if isinstance(items, str) else (items or [])
    qtes = _quantites(codes, [w for w in (source, cible) if w])
    return {c: [qtes.get((c, source), 0.0), qtes.get((c, cible), 0.0)] for c in codes}


@frappe.whitelist(methods=["POST"])
def remettre_a_zero(entrepot, items=None):
    """Ramène l'entrepôt à zéro (tous ses articles, ou ceux cochés) : un transfert Magasin → entrepôt pour
    les quantités négatives, un transfert entrepôt → Magasin pour les positives. Les quantités sont RELUES
    au moment de valider : ce qui a bougé depuis l'aperçu est pris tel quel."""
    _responsable()
    _verifier_cible_zero(entrepot)
    m = magasin()
    choisis = set(frappe.parse_json(items) if isinstance(items, str) else items) if items else None
    lignes = [l for l in lignes_a_zero(entrepot) if not l["bloque"] and (choisis is None or l["item_code"] in choisis)]
    if not lignes:
        frappe.throw(_("Rien à remettre à zéro dans {0}.").format(entrepot))
    apports = [(l["item_code"], -l["qte"]) for l in lignes if l["qte"] < 0]
    retours = [(l["item_code"], l["qte"]) for l in lignes if l["qte"] > 0]
    ecritures = []
    if apports:
        ecritures.append(ecriture_transfert(m, entrepot, apports,
                                            f"{MARQUE} — remise à zéro de {entrepot} : apport du {m} (stock négatif)").name)
    if retours:
        ecritures.append(ecriture_transfert(entrepot, m, retours,
                                            f"{MARQUE} — remise à zéro de {entrepot} : retour au {m}").name)
    restant = [l for l in lignes_a_zero(entrepot) if choisis is None or l["item_code"] in choisis]
    return {"ecritures": ecritures, "apports": len(apports), "retours": len(retours),
            "restant": [l["item_code"] for l in restant if not l["bloque"]]}
