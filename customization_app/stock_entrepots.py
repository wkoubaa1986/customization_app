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
from frappe.utils import add_days, cint, flt, get_datetime, get_first_day, getdate, now_datetime, nowdate

from customization_app.zones_magasin import _societe

CONFIG = "Config Stock Entrepot"
CONFIG_EXCLU = "Config Stock Entrepot Exclu"
CONFIG_SEUIL = "Config Stock Entrepot Seuil"
CONFIG_VERIF = "Config Stock Entrepot Verification"
VERIF = "Verification Stock"
# Statuts d'une vérification (validation MUTUELLE, décision 02/10/2026) : l'employé compte et termine
# (« À valider ») ; un responsable magasin AUTRE que lui vérifie et valide avec SES quantités (« À confirmer ») ;
# l'employé les accepte telles quelles → rapprochement de stock sur le véhicule et « Terminée » — ou en change
# une, et la fiche revient au responsable (« À valider ») : le dernier mot est au responsable.
EN_COURS, A_VALIDER, A_CONFIRMER, TERMINEE = "En cours", "À valider", "À confirmer", "Terminée"
OUVERTS = (EN_COURS, A_VALIDER, A_CONFIRMER)
CIBLE = "Stock Cible"
JOURS = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")
MAGASIN_DEFAUT = "Magasins - A&S"
TYPE_TRANSFERT = "Transfer interne"
PURPOSE = "Material Transfer"
MARQUE = "Stock par entrepôt"
RESPONSABLES = {"Responsable magasin", "System Manager"}
LECTEURS = RESPONSABLES | {"Stock User", "Stock Manager"}
LIMITE_RECHERCHE = 30
LIMITE_DETAIL = 300
# Transfert Magasin → stock d'un employé : l'écriture reste en BROUILLON (le stock ne bouge pas) jusqu'à ce que
# l'employé confirme la réception, ligne par ligne (double validation, demande du 02/10/2026).
CHAMP_VALIDEUR = "custom_validation_employe"
CHAMP_VALIDE_LE = "custom_valide_le"
CHAMP_VALIDE_PAR = "custom_valide_par"
CHAMP_ECART = "custom_ecart_reception"

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
                              ["name", "employee_name", "custom_warehouse"], as_dict=True)
    mien = emp.custom_warehouse if emp and emp.custom_warehouse in actifs else None
    m = magasin()
    return {"entrepots": liste, "mien": mien, "employe": emp.employee_name if emp else None,
            "employe_id": emp.name if emp else None,
            "a_valider": len(transferts_a_valider()) if _champs_validation() else 0,
            "defaut": mien or (m if m in actifs else (liste[0]["name"] if liste else None)),
            "magasin": m, "responsable": est_responsable(), "aujourdhui": nowdate(),
            "seuils": seuils(), "verification": bool(est_responsable() or mien),
            "cibles": frappe.get_all(CIBLE, pluck="entrepot") if frappe.db.exists("DocType", CIBLE) else []}


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
def get_solde(entrepot=None, recherche=None, negatifs=0, a_reappro=0):
    """Stock non nul, par article. Sans entrepôt : tous les entrepôts, la quantité de chacun sous l'article.
    Avec un entrepôt qui a un seuil (réglage) : chaque article porte `a_reappro` (stock < seuil, les
    articles à zéro compris) et la quantité `a_transferer` (cible − stock) ; `a_reappro=1` ne garde qu'eux."""
    _lecture()
    valeurs = {"societe": _societe()}
    seuil = seuils().get(entrepot) if entrepot else None
    # Sans le filtre : le stock non nul seulement. Avec « à réapprovisionner » : tout ce qui est sous le
    # seuil, articles à zéro compris (ils ont déjà été stockés ici : une ligne de Bin existe).
    conds = ["w.company = %(societe)s", "b.actual_qty < %(seuil)s" if cint(a_reappro) and seuil else "abs(b.actual_qty) > 0.000001"]
    if seuil:
        valeurs["seuil"] = seuil["seuil"]
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
    cible = stock_cible(entrepot) if entrepot else {}
    if seuil or cible:
        for a in articles:
            if a["item_code"] in cible:
                # Stock cible de l'entrepôt (article par article) : il prime sur le seuil global.
                a["cible"] = cible[a["item_code"]]
                a["a_reappro"] = a["qte"] < a["cible"]
                a["a_transferer"] = flt(max(a["cible"] - a["qte"], 0), 6)
            elif seuil:
                a["seuil"] = seuil["seuil"]
                a["a_reappro"] = a["qte"] < seuil["seuil"]
                a["a_transferer"] = flt(max(seuil["cible"] - a["qte"], 0), 6) if a["a_reappro"] else 0
    out = {"articles": articles, "negatifs": sum(1 for a in articles if a["qte"] < 0),
           "positifs": sum(1 for a in articles if a["qte"] > 0),
           "a_reappro": cint(frappe.db.sql("select count(*) from tabBin where warehouse = %s and actual_qty < %s",
                                            (entrepot, seuil["seuil"]))[0][0]) if seuil else None}
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


def ecriture_transfert(source: str, cible: str, lignes: list[tuple[str, float]], remarque: str,
                       valideur: str | None = None):
    """Crée et soumet un transfert source → cible. lignes : [(item_code, qte)], qte > 0.
    Avec `valideur` (un employé), l'écriture reste en brouillon : elle attend sa validation."""
    doc = frappe.new_doc("Stock Entry")
    doc.update({"stock_entry_type": _type_transfert(), "purpose": PURPOSE, "company": _societe(),
                "from_warehouse": source, "to_warehouse": cible, "remarks": remarque})
    for item_code, qte in lignes:
        doc.append("items", {"item_code": item_code, "qty": qte, "s_warehouse": source, "t_warehouse": cible})
    avant = len(frappe.local.message_log)
    # Le droit a été vérifié par le rôle (Responsable magasin). Frappe, lui, refuserait l'écriture à un
    # responsable qui n'a pas le droit de LIRE la fiche Employé du valideur (permission utilisateur « son
    # propre employé » : Hedi en prod, 02/10/2026) — le lien « Validation attendue de » le bloquait.
    doc.flags.ignore_permissions = True
    if valideur:
        doc.set(CHAMP_VALIDEUR, valideur)
        doc.insert()
        return doc
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
    valideur = _valideur_requis(source, cible)
    doc = ecriture_transfert(source, cible, list(cumul.items()), texte, valideur=valideur.name if valideur else None)
    if not valideur:
        return {"name": doc.name, "lignes": len(cumul)}
    doc.add_comment("Comment", _("En attente de validation de {0} : le stock ne bouge qu’à sa confirmation.")
                    .format(valideur.employee_name))
    _prevenir(valideur.user_id, _("📥 Transfert {0} à valider : {1} article(s) de {2} vers votre stock")
              .format(doc.name, len(cumul), se_court(source)), doc.name)
    return {"name": doc.name, "lignes": len(cumul), "en_attente": True,
            "employe": valideur.name, "employe_nom": valideur.employee_name}


# ── Double validation (Magasin → stock d'un employé) ─────────────────────────

def se_court(wh: str) -> str:
    return (wh or "").rsplit(" - ", 1)[0]


def _champs_validation() -> bool:
    return frappe.db.has_column("Stock Entry", CHAMP_VALIDEUR)


def _valideur_requis(source: str, cible: str):
    """L'employé qui doit confirmer, ou None : départ du Magasin vers le stock d'un employé AUTRE que
    celui qui fait le transfert (le responsable qui charge son propre véhicule n'a rien à se confirmer)."""
    if source != magasin() or not _champs_validation():
        return None
    emp = _employe_du_stock(cible)
    if not emp or not emp.user_id or emp.user_id == frappe.session.user:
        return None
    return emp


def _prevenir(user: str, sujet: str, name: str):
    """Cloche du Desk + rafraîchissement en direct de la page Stock de l'intéressé. Jamais bloquant."""
    try:
        frappe.get_doc({"doctype": "Notification Log", "for_user": user, "type": "Alert", "subject": sujet,
                        "document_type": "Stock Entry", "document_name": name}).insert(ignore_permissions=True)
        frappe.publish_realtime("se_transferts_a_valider", {"name": name}, user=user, after_commit=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "stock_entrepots: notification")


def stock_entry_before_submit(doc, method=None):
    """Hook `before_submit` de Stock Entry : un transfert qui attend la confirmation d'un employé ne se soumet
    QUE par `valider_transfert`, qui remplit « Réception confirmée par » juste avant. Le formulaire Stock Entry,
    la liste, un script ou un import ne peuvent pas le passer en force. Le 02/10/2026, MAT-STE-2026-00104
    (Magasin → Stock Akram) avait été soumis depuis le formulaire par le demandeur lui-même, sans Akram :
    la double validation n'existait que dans la page, pas dans le DocType."""
    if not doc.get(CHAMP_VALIDEUR) or doc.get(CHAMP_VALIDE_PAR):
        return
    nom = frappe.db.get_value("Employee", doc.get(CHAMP_VALIDEUR), "employee_name") or doc.get(CHAMP_VALIDEUR)
    frappe.throw(_("{0} attend la confirmation de {1} : le stock ne bouge qu’à sa validation, depuis la page "
                   "« Stock par entrepôt » (Ma journée → transferts à valider). Pour y renoncer, annulez la "
                   "demande depuis l’onglet Transfert ; pour passer outre, demandez-lui de valider.")
                 .format(doc.name, nom), title=_("Validation attendue"))


def _peut_valider(doc) -> bool:
    emp = _mon_employe()
    return bool(emp and emp.name == doc.get(CHAMP_VALIDEUR)) or est_responsable()


def _images(codes: list[str]) -> dict[str, str | None]:
    return {i.name: i.image for i in frappe.get_all("Item", filters={"name": ["in", codes]}, fields=["name", "image"])} \
        if codes else {}


@frappe.whitelist()
def transferts_a_valider(entrepot=None):
    """Les transferts en attente : ceux que MON employé doit confirmer ; tous pour le responsable."""
    _lecture()
    if not _champs_validation():
        return []
    filtres = {"purpose": PURPOSE, "docstatus": 0, "company": _societe(), CHAMP_VALIDEUR: ["is", "set"]}
    if entrepot:
        filtres["to_warehouse"] = entrepot
    if not est_responsable():
        emp = _mon_employe()
        if not emp:
            return []
        filtres[CHAMP_VALIDEUR] = emp.name
    entetes = frappe.get_all("Stock Entry", filters=filtres,
                             fields=["name", "creation", "from_warehouse", "to_warehouse", "remarks", "owner", CHAMP_VALIDEUR],
                             order_by="creation asc")
    return [_detail_transfert(e) for e in entetes]


def _detail_transfert(e) -> dict:
    lignes = frappe.get_all("Stock Entry Detail", filters={"parent": e.name},
                            fields=["item_code", "item_name", "qty", "uom"], order_by="idx")
    images = _images([l.item_code for l in lignes])
    valideur = frappe.db.get_value("Employee", e.get(CHAMP_VALIDEUR), "employee_name") if e.get(CHAMP_VALIDEUR) else None
    return {"name": e.name, "quand": str(e.creation)[:16], "de": e.from_warehouse, "vers": e.to_warehouse,
            "remarque": (e.remarks or "").replace(f"{MARQUE} — transfert", "").strip(" —"),
            "par": frappe.utils.get_fullname(e.owner), "employe": e.get(CHAMP_VALIDEUR), "employe_nom": valideur,
            "age_h": round((now_datetime() - get_datetime(e.creation)).total_seconds() / 3600, 1),
            "lignes": [{"item_code": l.item_code, "item_name": l.item_name, "qte": flt(l.qty, 6), "uom": l.uom,
                        "image": images.get(l.item_code)} for l in lignes]}


@frappe.whitelist(methods=["POST"])
def valider_transfert(name, lignes=None):
    """L'employé confirme la réception. `lignes` : [{item_code, qte}] = ce qu'il a RÉELLEMENT reçu ; une
    quantité baissée est tracée (commentaire + champ), 0 = article non reçu (il reste au Magasin). Si rien
    n'est reçu, la demande est supprimée. Le responsable est prévenu de tout écart."""
    _lecture()
    doc = frappe.get_doc("Stock Entry", name)
    if doc.purpose != PURPOSE or doc.docstatus != 0 or not doc.get(CHAMP_VALIDEUR):
        frappe.throw(_("{0} n’est pas un transfert en attente de validation.").format(name))
    if not _peut_valider(doc):
        frappe.throw(_("Seul {0} peut valider ce transfert.")
                     .format(frappe.db.get_value("Employee", doc.get(CHAMP_VALIDEUR), "employee_name")), frappe.PermissionError)
    recues = {}
    for l in (frappe.parse_json(lignes) if isinstance(lignes, str) else (lignes or [])):
        if l.get("item_code"):
            recues[l["item_code"]] = max(flt(l.get("qte"), 6), 0)
    ecarts, gardees = [], []
    for it in doc.items:
        qte = recues.get(it.item_code, flt(it.qty, 6))
        if qte > flt(it.qty, 6):
            frappe.throw(_("{0} : reçu {1} alors que {2} étaient envoyés — on ne reçoit pas plus qu’envoyé.")
                         .format(it.item_code, qte, it.qty))
        if qte != flt(it.qty, 6):
            ecarts.append(f"{it.item_name or it.item_code} : envoyé {flt(it.qty, 6):g}, reçu {qte:g}")
        if qte > 0:
            it.qty = qte
            gardees.append(it)
    qui = _mon_employe()
    nom = (qui.employee_name if qui else None) or frappe.utils.get_fullname(frappe.session.user)
    if not gardees:
        texte = _("Rien reçu : demande {0} supprimée par {1}").format(name, nom)
        _prevenir(doc.owner, "⚠️ " + texte + " — " + " ; ".join(ecarts), name)
        frappe.delete_doc("Stock Entry", name, ignore_permissions=True)
        return {"name": name, "supprime": True, "ecarts": ecarts}
    doc.items = gardees
    for i, it in enumerate(doc.items, 1):
        it.idx = i
    doc.set(CHAMP_VALIDE_LE, now_datetime())
    doc.set(CHAMP_VALIDE_PAR, frappe.session.user)
    doc.set(CHAMP_ECART, " ; ".join(ecarts))
    doc.flags.ignore_permissions = True
    doc.save()
    doc.submit()
    doc.add_comment("Comment", _("Réception confirmée par {0}").format(nom)
                    + (("<br>⚠️ " + "<br>".join(ecarts)) if ecarts else ""))
    if ecarts:
        _prevenir(doc.owner, _("⚠️ Transfert {0} reçu avec écart par {1} : {2}").format(name, nom, " ; ".join(ecarts)), name)
    return {"name": doc.name, "lignes": len(gardees), "ecarts": ecarts}


@frappe.whitelist()
def transferts_recents(limite=15, entrepot=None):
    """Derniers transferts (soumis ou annulés), lignes comprises."""
    _responsable()
    avec = _champs_validation()
    filtres = {"purpose": PURPOSE, "docstatus": ["in", [1, 2]], "company": _societe()}
    ou = [["from_warehouse", "=", entrepot], ["to_warehouse", "=", entrepot]] if entrepot else None
    champs = ["name", "posting_date", "posting_time", "docstatus", "from_warehouse", "to_warehouse", "remarks", "owner", "creation"]
    if avec:
        champs += [CHAMP_VALIDEUR, CHAMP_VALIDE_LE, CHAMP_VALIDE_PAR, CHAMP_ECART]
    entetes = frappe.get_all("Stock Entry", filters=filtres, or_filters=ou, fields=champs,
                             order_by="creation desc", limit=min(cint(limite) or 15, 50))
    if avec:
        # Les demandes en attente (brouillons) d'abord : c'est ce qui appelle une action.
        attente = frappe.get_all("Stock Entry", filters=dict(filtres, docstatus=0, **{CHAMP_VALIDEUR: ["is", "set"]}),
                                 or_filters=ou, fields=champs, order_by="creation asc")
        entetes = attente + entetes
    if not entetes:
        return []
    lignes = {}
    for d in frappe.get_all("Stock Entry Detail", filters={"parent": ["in", [e.name for e in entetes]]},
                            fields=["parent", "item_code", "item_name", "qty", "uom", "s_warehouse", "t_warehouse"], order_by="idx"):
        lignes.setdefault(d.parent, []).append({"item_code": d.item_code, "item_name": d.item_name, "qte": flt(d.qty, 6),
                                                "uom": d.uom, "de": d.s_warehouse, "vers": d.t_warehouse})
    images = _images(sorted({l["item_code"] for ls in lignes.values() for l in ls}))
    for ls in lignes.values():
        for l in ls:
            l["image"] = images.get(l["item_code"])
    employes = _par_nom("Employee", [e.get(CHAMP_VALIDEUR) for e in entetes if e.get(CHAMP_VALIDEUR)], ["employee_name"]) if avec else {}
    out = []
    for e in entetes:
        emp = employes.get(e.get(CHAMP_VALIDEUR)) if avec else None
        out.append({"name": e.name, "date": str(e.posting_date), "heure": str(e.posting_time or "")[:5], "docstatus": e.docstatus,
                    "de": e.from_warehouse, "vers": e.to_warehouse, "remarque": e.remarks or "",
                    "par": frappe.utils.get_fullname(e.owner), "lignes": lignes.get(e.name, []),
                    "attente": {"employe": e.get(CHAMP_VALIDEUR), "employe_nom": emp.employee_name if emp else e.get(CHAMP_VALIDEUR),
                                "age_h": round((now_datetime() - get_datetime(e.creation)).total_seconds() / 3600, 1)}
                               if e.docstatus == 0 else None,
                    "validation": {"par": frappe.utils.get_fullname(e.get(CHAMP_VALIDE_PAR)), "le": str(e.get(CHAMP_VALIDE_LE))[:16],
                                   "ecart": e.get(CHAMP_ECART) or ""} if e.get(CHAMP_VALIDE_LE) else None})
    return out


@frappe.whitelist(methods=["POST"])
def annuler_transfert(name):
    _responsable()
    doc = frappe.get_doc("Stock Entry", name)
    if doc.purpose == PURPOSE and doc.docstatus == 0 and doc.get(CHAMP_VALIDEUR):
        # Demande encore en attente : rien n'a bougé, on la retire (l'employé est prévenu).
        emp = _employe_du_stock(doc.to_warehouse)
        if emp and emp.user_id:
            _prevenir(emp.user_id, _("Transfert {0} annulé par {1} avant votre validation")
                      .format(name, frappe.utils.get_fullname(frappe.session.user)), name)
        frappe.delete_doc("Stock Entry", name, ignore_permissions=True)
        return True
    if doc.purpose != PURPOSE or doc.docstatus != 1:
        frappe.throw(_("{0} n’est pas un transfert soumis.").format(name))
    doc.flags.ignore_permissions = True
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


# ── Réapprovisionnement : seuils par entrepôt (réglage) ─────────────────────

def seuils() -> dict:
    """{entrepot: {"seuil", "cible"}} — un article sous le seuil est « à réapprovisionner »."""
    out = {}
    for r in frappe.get_all(CONFIG_SEUIL, filters={"parent": CONFIG, "parenttype": CONFIG},
                            fields=["entrepot", "seuil", "cible"]):
        out[r.entrepot] = {"seuil": flt(r.seuil), "cible": flt(r.cible) if flt(r.cible) >= flt(r.seuil) else flt(r.seuil)}
    return out


# ── Vérification des stocks des employés ─────────────────────────────────────
#
# Chaque semaine, le jour fixé dans le réglage pour un stock d'employé, une fiche « Verification Stock »
# est ouverte avec la photographie du stock (quantité système et taux du moment), et deux tâches sont
# créées : l'employé du stock et le responsable principal du magasin. On compte, on saisit, on termine :
# l'écart (compté − système) est gardé ligne à ligne et valorisé — AUCUN mouvement de stock, aucun
# rapprochement : la fiche est la trace, le Magasin reste la seule référence.

def _employe_du_stock(entrepot: str):
    return frappe.db.get_value("Employee", {"custom_warehouse": entrepot, "status": "Active"},
                               ["name", "employee_name", "user_id"], as_dict=True)


def _mon_employe():
    return frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"},
                               ["name", "employee_name", "custom_warehouse"], as_dict=True)


def _acces_verification(entrepot: str):
    """Responsable magasin, ou l'employé dont c'est le stock."""
    if est_responsable():
        return
    emp = _mon_employe()
    if not emp or emp.custom_warehouse != entrepot:
        frappe.throw(_("La vérification de {0} est réservée au responsable magasin et à l'employé de ce stock.")
                     .format(entrepot), frappe.PermissionError)


def _heure(h) -> str:
    """« HH:MM » depuis un champ Time (timedelta en base, chaîne « 9:00:00 » ou « 09:00 »)."""
    if hasattr(h, "total_seconds"):
        total = int(h.total_seconds())
        return "%02d:%02d" % (total // 3600, (total % 3600) // 60)
    parts = str(h or "09:00").split(":")
    return "%02d:%02d" % (int(parts[0] or 0), int(parts[1] or 0) if len(parts) > 1 else 0)


def config_verification() -> dict:
    cfg = frappe.get_single(CONFIG) if frappe.db.exists("DocType", CONFIG) else None
    lignes = [{"entrepot": r.entrepot, "jour": r.jour, "heure": _heure(r.heure), "actif": cint(r.actif)}
              for r in (cfg.get("verifications") if cfg else []) or []]
    return {"responsable": cfg.responsable_verification if cfg else None,
            "duree": (cfg.duree_verification if cfg else None) or "1 heure", "lignes": lignes}


def prochaine_date(jour: str, depuis) -> str:
    """La prochaine occurrence (aujourd'hui compris) du jour de la semaine `jour` (« Lundi »…). PURE."""
    depuis = getdate(depuis)
    cible = JOURS.index(jour)
    return str(add_days(depuis, (cible - depuis.weekday()) % 7))


def calculer_ecarts(lignes: list[dict]) -> dict:
    """Écarts d'une liste de lignes {qte_systeme, qte_comptee, taux} — seules les lignes comptées comptent.
    PURE : renvoie les lignes complétées (ecart, valeur_ecart) et le bilan."""
    nb_comptes = nb_ecarts = 0
    valeur = manquants = 0.0
    for l in lignes:
        if l.get("qte_comptee") is None or l.get("qte_comptee") == "":
            l["ecart"] = None
            l["valeur_ecart"] = None
            continue
        nb_comptes += 1
        l["ecart"] = flt(flt(l["qte_comptee"]) - flt(l.get("qte_systeme")), 6)
        l["valeur_ecart"] = flt(l["ecart"] * flt(l.get("taux")), 3)
        if abs(l["ecart"]) > 1e-6:
            nb_ecarts += 1
            valeur += l["valeur_ecart"]
            if l["ecart"] < 0:
                manquants += -l["valeur_ecart"]
    return {"nb_comptes": nb_comptes, "nb_ecarts": nb_ecarts, "valeur_ecarts": flt(valeur, 3),
            "valeur_manquants": flt(manquants, 3)}


def _photographie(entrepot: str) -> list[dict]:
    """Le stock de l'entrepôt au moment d'ouvrir la vérification : quantité système et taux, par article."""
    seuil = seuils().get(entrepot)
    rows = frappe.db.sql("""select b.item_code, i.item_name, b.actual_qty, b.valuation_rate, i.custom_emplacement_magasin zones
                            from tabBin b join tabItem i on i.name = b.item_code
                            where b.warehouse = %s and i.disabled = 0 and (abs(b.actual_qty) > 0.000001 or b.actual_qty < %s)
                            order by i.item_name""", (entrepot, seuil["seuil"] if seuil else 0), as_dict=True)
    return [{"item_code": r.item_code, "item_name": r.item_name, "qte_systeme": flt(r.actual_qty, 6),
             "taux": flt(r.valuation_rate, 3), "zones": r.zones} for r in rows]


DUREES_MIN = {"15 min": 15, "30 min": 30, "45 min": 45, "1 heure": 60, "1 heure, 15 min": 75, "1 heure, 30 min": 90,
              "1 heure, 45 min": 105, ">=2 heures": 120}


def _creer_tache(employe: str, entrepot_libelle: str, quand, duree: str, role: str) -> str:
    """Tâche de vérification : titre lisible dans le calendrier (il affichait « null ») et une vraie durée
    (fin = début + durée du réglage, 1 heure par défaut) — demande utilisateur 01/10/2026."""
    from frappe.utils import add_to_date

    from customization_app.api import compute_tache_color

    nom = frappe.db.get_value("Employee", employe, "employee_name") or employe
    doc = frappe.get_doc({
        "doctype": "Tache de travail", "custom_type_dintervention": "Autre", "custom_choix_du_staff": employe,
        "starts_on": quand, "ends_on": add_to_date(quand, minutes=DUREES_MIN.get(duree, 60)), "temps": duree, "status": "Open",
        "titre": "🧾 Vérification stock\n%s\n%s" % (entrepot_libelle, nom),
        "subject": "Vérification du stock %s (%s) — compter les articles dans Stock › Vérif." % (entrepot_libelle, role),
    })
    try:
        doc.color = compute_tache_color(doc)
    except Exception:
        pass
    doc.flags.ignore_permissions = True
    # Sans ce drapeau, api._fixer_duree_a_la_creation remplace la fin par la durée du TYPE (« Autre » :
    # 2 h par défaut) et la durée du réglage est perdue (constaté le 02/10/2026).
    doc.flags.duree_fixee = True
    doc.insert()
    return doc.name


def ouvrir_verification(entrepot: str, date_prevue, avec_taches: bool = True) -> str:
    """Ouvre la fiche de ce stock pour cette date (une seule par stock et par date : réutilisée).
    ⚠️ Pas de photographie du stock ici : créée jusqu'à six jours à l'avance (rendez-vous de la semaine à
    venir), la fiche se remplit à la PREMIÈRE OUVERTURE de la feuille de comptage (`_assurer_photo`)."""
    existante = frappe.db.get_value(VERIF, {"entrepot": entrepot, "date": date_prevue, "statut": ["in", OUVERTS]}, "name")
    if existante:
        return existante
    emp = _employe_du_stock(entrepot)
    cfg = config_verification()
    doc = frappe.get_doc({"doctype": VERIF, "entrepot": entrepot, "date": date_prevue, "statut": "En cours",
                          "employe": emp.name if emp else None, "responsable": cfg["responsable"]})
    if avec_taches:
        libelle = frappe.db.get_value("Warehouse", entrepot, "warehouse_name") or entrepot
        heure = next((l["heure"] for l in cfg["lignes"] if l["entrepot"] == entrepot), "09:00")
        quand = get_datetime("%s %s:00" % (date_prevue, heure))
        if emp:
            doc.tache_employe = _creer_tache(emp.name, libelle, quand, cfg["duree"], "employé du stock")
        if cfg["responsable"] and (not emp or cfg["responsable"] != emp.name):
            doc.tache_responsable = _creer_tache(cfg["responsable"], libelle, quand, cfg["duree"], "responsable magasin")
    doc.flags.ignore_permissions = True
    doc.insert()
    return doc.name


def planifier_verifications():
    """Cron quotidien : pour chaque stock, la PROCHAINE vérification (dans les 7 jours à venir, aujourd'hui
    compris) existe avec ses deux tâches — les rendez-vous de la semaine à venir sont donc créés d'avance
    (demande utilisateur 01/10/2026). Idempotent : une fiche par stock et par date."""
    if not frappe.db.exists("DocType", VERIF):
        return []
    aujourd = getdate(nowdate())
    crees = []
    for l in config_verification()["lignes"]:
        if not l["actif"]:
            continue
        date_cible = prochaine_date(l["jour"], aujourd)
        if frappe.db.exists(VERIF, {"entrepot": l["entrepot"], "date": date_cible}):
            continue
        if frappe.db.get_value("Warehouse", l["entrepot"], "disabled") or not _employe_du_stock(l["entrepot"]):
            continue
        try:
            crees.append(ouvrir_verification(l["entrepot"], date_cible))
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(frappe.get_traceback(), "Vérification stock %s" % l["entrepot"][:40])
    return crees


def _assurer_photo(v):
    """La photographie du stock (quantité système, taux) est prise à la première ouverture du comptage."""
    if v.statut != EN_COURS or v.get("lignes"):
        return
    for l in _photographie(v.entrepot):
        v.append("lignes", l)
    v.nb_lignes = len(v.lignes)
    v.flags.ignore_permissions = True
    v.save()


def _resume(v) -> dict:
    return {"name": v.name, "entrepot": v.entrepot, "date": str(v.date), "statut": v.statut, "employe": v.employe,
            "prevue": str(v.date) > nowdate(), "photo": bool(v.get("lignes")),
            "nb_lignes": cint(v.nb_lignes), "nb_comptes": cint(v.nb_comptes), "nb_ecarts": cint(v.nb_ecarts),
            "valeur_ecarts": flt(v.valeur_ecarts, 3), "valeur_manquants": flt(v.valeur_manquants, 3),
            "termine_le": str(v.termine_le or "")[:16], "tache_employe": v.tache_employe, "tache_responsable": v.tache_responsable,
            "valide_employe_par": frappe.utils.get_fullname(v.valide_employe_par) if v.valide_employe_par else None,
            "valide_employe_le": str(v.valide_employe_le or "")[:16],
            "valide_responsable_par": frappe.utils.get_fullname(v.valide_responsable_par) if v.valide_responsable_par else None,
            "valide_responsable_le": str(v.valide_responsable_le or "")[:16],
            "rapprochement": v.rapprochement, "renvois": cint(v.renvois), "rafraichi_le": str(v.rafraichi_le or "")[:16],
            "nb_ajustes": sum(1 for l in (v.get("lignes") or []) if l.ajuste),
            "nb_a_recompter": sum(1 for l in (v.get("lignes") or []) if l.a_recompter),
            # Ce que l'utilisateur connecté peut faire sur cette fiche, calculé ici pour que l'écran n'ait rien à deviner.
            "actions": _actions_possibles(v)}


@frappe.whitelist()
def verifications_etat():
    """Les stocks d'employés (tous pour le responsable, le sien pour un employé) : planning, fiche en cours,
    dernières fiches terminées."""
    _lecture()
    cfg = config_verification()
    moi = _mon_employe()
    noms = {e.custom_warehouse: e for e in frappe.get_all("Employee", filters={"status": "Active", "custom_warehouse": ["is", "set"]},
                                                          fields=["name", "employee_name", "custom_warehouse"])}
    actifs = {w["name"]: w for w in entrepots()}
    out = []
    for wh, emp in noms.items():
        if wh not in actifs:
            continue
        if not est_responsable() and (not moi or moi.custom_warehouse != wh):
            continue
        plan = next((l for l in cfg["lignes"] if l["entrepot"] == wh), None)
        en_cours = frappe.db.get_value(VERIF, {"entrepot": wh, "statut": ["in", OUVERTS]}, "name", order_by="date asc")
        dernieres = [_resume(frappe.get_doc(VERIF, v.name)) for v in frappe.get_all(VERIF, filters={"entrepot": wh, "statut": TERMINEE},
                                                                                 fields=["name"], order_by="termine_le desc", limit=6)]
        out.append({"entrepot": wh, "libelle": actifs[wh]["libelle"], "employe": emp.employee_name, "employe_id": emp.name,
                    "jour": plan["jour"] if plan else None, "heure": plan["heure"] if plan else None,
                    "actif": bool(plan and plan["actif"]),
                    "prochaine": prochaine_date(plan["jour"], nowdate()) if plan and plan["actif"] else None,
                    "en_cours": _resume(frappe.get_doc(VERIF, en_cours)) if en_cours else None,
                    "dernieres": dernieres})
    return {"stocks": out, "responsable": cfg["responsable"],
            "responsable_nom": frappe.db.get_value("Employee", cfg["responsable"], "employee_name") if cfg["responsable"] else None,
            "peut_planifier": est_responsable()}


@frappe.whitelist()
def verifications_a_traiter():
    """Les vérifications qui attendent un geste de l'utilisateur connecté : « à valider » (responsable magasin,
    comptage d'un employé) ou « à accepter » (employé, quantités validées par le responsable). Pour le bandeau
    de l'onglet Solde et le badge de l'onglet Vérif."""
    _lecture()
    actifs = {w["name"]: w["libelle"] for w in entrepots()}
    out = []
    for nom in frappe.get_all(VERIF, filters={"statut": ["in", [A_VALIDER, A_CONFIRMER]]}, pluck="name", order_by="date asc"):
        v = frappe.get_doc(VERIF, nom)
        if v.entrepot not in actifs:
            continue
        actions = _actions_possibles(v)
        if "valider" in actions:
            geste, texte = "valider", _("comptage de {0} du {1} à VALIDER : {2} écart(s)").format(
                frappe.utils.get_fullname(v.valide_employe_par) if v.valide_employe_par else "?", frappe.utils.formatdate(v.date), cint(v.nb_ecarts))
        elif "confirmer" in actions:
            nb = sum(1 for l in v.lignes if l.ajuste)
            geste, texte = "accepter", _("quantités validées par {0} à ACCEPTER{1}").format(
                frappe.utils.get_fullname(v.valide_responsable_par) if v.valide_responsable_par else "?",
                _(" — {0} quantité(s) ajustée(s)").format(nb) if nb else _(" (aucun changement)"))
        else:
            continue
        out.append({"name": v.name, "entrepot": v.entrepot, "libelle": actifs[v.entrepot], "geste": geste, "texte": texte})
    return out


@frappe.whitelist(methods=["POST"])
def commencer_verification(entrepot):
    """Ouvre (ou reprend) la vérification du stock, hors planning : pas de tâches créées."""
    _acces_verification(entrepot)
    _verifier_entrepot(entrepot)
    # Une fiche déjà ouverte (celle de la semaine, même prévue dans quelques jours) est reprise : compter un
    # peu avant le rendez-vous ne crée pas de doublon.
    existante = frappe.db.get_value(VERIF, {"entrepot": entrepot, "statut": ["in", OUVERTS]}, "name", order_by="date asc")
    return existante or ouvrir_verification(entrepot, nowdate(), avec_taches=False)


@frappe.whitelist()
def detail_verification(name):
    v = frappe.get_doc(VERIF, name)
    _acces_verification(v.entrepot)
    _assurer_photo(v)
    if v.statut in (A_VALIDER, A_CONFIRMER) and _rafraichir_systeme(v):
        _renvoi_auto(v, [l.item_code for l in v.lignes if l.a_recompter])   # des articles ont bougé : à recompter
    # ⚠️ Un Float vaut 0 en base, jamais None : « pas compté » se lit sur le drapeau `compte`.
    images = dict(frappe.get_all("Item", filters={"name": ["in", [l.item_code for l in v.lignes] or [""]]},
                                 fields=["name", "image"], as_list=True))
    return {"fiche": _resume(v), "lignes": [{"item_code": l.item_code, "item_name": l.item_name, "qte_systeme": flt(l.qte_systeme, 6),
                                             "image": images.get(l.item_code),
                                             "qte_comptee": flt(l.qte_comptee, 6) if l.compte else None,
                                             "qte_employe": flt(l.qte_employe, 6) if l.ajuste else None, "ajuste": cint(l.ajuste),
                                             "a_recompter": cint(l.a_recompter),
                                             "ecart": flt(l.ecart, 6) if l.compte else None, "taux": flt(l.taux, 3),
                                             "valeur_ecart": flt(l.valeur_ecart, 3) if l.compte else None,
                                             "zones": l.zones, "commentaire": l.commentaire}
                                            for l in v.lignes]}


def _dict_json(val) -> dict:
    """Un argument d'écran : dict, chaîne JSON, ou rien (None, « », « null » — un `null` JS arrive en chaîne
    vide, et `parse_json("")` lève)."""
    if isinstance(val, dict):
        return val
    if not val or str(val).strip() in ("", "null", "undefined"):
        return {}
    return frappe.parse_json(val) or {}


def _appliquer_comptage(v, comptes: dict):
    """comptes : {item_code: {"qte_comptee": x|None, "commentaire": s}} — recalcule écarts et bilan."""
    lignes = []
    for l in v.lignes:
        c = comptes.get(l.item_code)
        if c is not None:
            q = c.get("qte_comptee")
            l.compte = 1 if q not in (None, "") else 0
            l.qte_comptee = flt(q) if l.compte else 0
            l.commentaire = (c.get("commentaire") or "")[:140] or None
            if l.compte:
                l.a_recompter = 0
        lignes.append({"qte_systeme": l.qte_systeme, "qte_comptee": l.qte_comptee if l.compte else None, "taux": l.taux})
    bilan = calculer_ecarts(lignes)
    for l, calc in zip(v.lignes, lignes):
        l.ecart = calc["ecart"] or 0
        l.valeur_ecart = calc["valeur_ecart"] or 0
    v.update(bilan)
    v.nb_lignes = len(v.lignes)


@frappe.whitelist(methods=["POST"])
def enregistrer_verification(name, comptes):
    """Sauvegarde du comptage en cours (on peut s'interrompre et reprendre)."""
    v = frappe.get_doc(VERIF, name)
    _acces_verification(v.entrepot)
    if v.statut != EN_COURS:
        frappe.throw(_("Cette vérification n’est plus au comptage ({0}).").format(v.statut))
    _assurer_photo(v)
    _appliquer_comptage(v, _dict_json(comptes))
    v.flags.ignore_permissions = True
    v.save()
    return _resume(v)


@frappe.whitelist(methods=["POST"])
def terminer_verification(name, comptes=None, note=None):
    """Fin du comptage = validation de l'employé : écarts figés et valorisés, fiche « À valider » par un
    responsable magasin. Le stock ne bouge qu'à SA validation (rapprochement)."""
    v = frappe.get_doc(VERIF, name)
    _acces_verification(v.entrepot)
    if v.statut != EN_COURS:
        frappe.throw(_("Cette vérification n’est plus au comptage ({0}).").format(v.statut))
    _assurer_photo(v)
    _appliquer_comptage(v, _dict_json(comptes))
    if not v.nb_comptes:
        frappe.throw(_("Aucun article compté : saisissez au moins une quantité."))
    restants = [l.item_name or l.item_code for l in v.lignes if l.a_recompter]
    if restants:
        frappe.throw(_("À recompter avant de terminer (stock modifié depuis le comptage) : {0}").format(", ".join(restants)))
    for l in v.lignes:
        l.ajuste, l.qte_employe = 0, (l.qte_comptee if l.compte else 0)
    v.statut = A_VALIDER
    v.valide_employe_par, v.valide_employe_le = frappe.session.user, now_datetime()
    v.valide_responsable_par = v.valide_responsable_le = None
    if note:
        v.note = note[:500]
    v.flags.ignore_permissions = True
    v.save()
    if v.tache_employe and frappe.db.get_value("Tache de travail", v.tache_employe, "status") == "Open":
        frappe.db.set_value("Tache de travail", v.tache_employe, "status", "Completed")
    cfg = config_verification()
    resp = frappe.db.get_value("Employee", cfg["responsable"], "user_id") if cfg["responsable"] else None
    if resp and resp != frappe.session.user:
        _prevenir(resp, _("🧾 Vérification {0} ({1}) à valider : {2} écart(s)").format(v.name, se_court(v.entrepot), cint(v.nb_ecarts)), v.name)
    return _resume(v)


def _rafraichir_systeme(v) -> list[str]:
    """Relit les quantités système du stock (décision utilisateur 02/10/2026). Un article qui a BOUGÉ depuis
    le comptage perd son comptage : il est « à recompter » (le compté d'hier ne dit plus rien du stock
    d'aujourd'hui). Rend les articles concernés ; les écarts des autres sont recalculés."""
    qtes = _quantites([l.item_code for l in v.lignes], [v.entrepot])
    bouges = []
    for l in v.lignes:
        nouveau = qtes.get((l.item_code, v.entrepot), 0.0)
        if abs(flt(nouveau, 6) - flt(l.qte_systeme, 6)) > 1e-6:
            if l.compte:
                l.commentaire = (_("Stock modifié depuis le comptage ({0} → {1}) : à recompter")
                                 .format(flt(l.qte_systeme, 6), flt(nouveau, 6)))[:140]
                l.compte, l.qte_comptee, l.qte_employe, l.ajuste, l.a_recompter = 0, 0, 0, 0, 1
                bouges.append(l.item_code)
            l.qte_systeme = nouveau
    _appliquer_comptage(v, {})
    v.rafraichi_le = now_datetime()
    v.flags.ignore_permissions = True
    v.save()
    return bouges


def _renvoi_auto(v, bouges: list[str]) -> dict:
    """Des articles ont bougé : leur comptage est effacé, la fiche repart au comptage chez l'employé."""
    v.statut = EN_COURS
    v.valide_employe_par = v.valide_employe_le = v.valide_responsable_par = v.valide_responsable_le = None
    v.renvois = cint(v.renvois) + 1
    v.note = ((v.note + "\n") if v.note else "") + _("Renvoi automatique : {0} article(s) ont bougé depuis le comptage, à recompter.").format(len(bouges))
    v.flags.ignore_permissions = True
    v.save()
    if v.tache_employe and frappe.db.get_value("Tache de travail", v.tache_employe, "status") == "Completed":
        frappe.db.set_value("Tache de travail", v.tache_employe, "status", "Open")
    emp = _employe_du_stock(v.entrepot)
    if emp and emp.user_id and emp.user_id != frappe.session.user:
        _prevenir(emp.user_id, _("🧾 Vérification {0} : {1} article(s) ont bougé depuis votre comptage, à recompter")
                  .format(v.name, len(bouges)), v.name)
    return dict(_resume(v), renvoye=True, a_recompter=bouges)


def _est_employe_du_stock(entrepot: str) -> bool:
    moi = _mon_employe()
    return bool(moi and moi.custom_warehouse == entrepot)


def _actions_possibles(v) -> list[str]:
    """Les boutons de l'utilisateur connecté sur cette fiche : compter / terminer (En cours), valider /
    renvoyer (À valider, responsable AUTRE que l'employé du stock), confirmer / recompter (À confirmer,
    employé du stock)."""
    if v.statut == EN_COURS:
        return ["compter"]
    if v.statut == A_VALIDER:
        return ["valider", "renvoyer"] if est_responsable() and not _est_employe_du_stock(v.entrepot) else []
    if v.statut == A_CONFIRMER:
        return ["confirmer", "recompter"] if _est_employe_du_stock(v.entrepot) else []
    return []


def _rapprochement(v):
    """Le rapprochement de stock du véhicule : la quantité comptée devient la quantité système, pour les
    articles comptés dont l'écart n'est pas nul (décision utilisateur 02/10/2026 : rapprochement sur le véhicule,
    l'écart est un gain / une perte d'inventaire). Rien à rapprocher → None."""
    lignes = [l for l in v.lignes if l.compte and abs(flt(l.ecart, 6)) > 1e-6]
    if not lignes:
        return None
    societe = _societe()
    doc = frappe.new_doc("Stock Reconciliation")
    doc.update({"purpose": "Stock Reconciliation", "company": societe,
                "expense_account": frappe.db.get_value("Company", societe, "stock_adjustment_account"),
                "cost_center": frappe.db.get_value("Company", societe, "cost_center")})
    for l in lignes:
        taux = flt(l.taux, 6) or flt(frappe.db.get_value("Item", l.item_code, "valuation_rate"), 6)
        doc.append("items", {"item_code": l.item_code, "warehouse": v.entrepot, "qty": flt(l.qte_comptee, 6),
                             "valuation_rate": taux if flt(l.qte_comptee, 6) > 0 else 0})
    doc.flags.ignore_permissions = True
    doc.insert()
    doc.submit()
    doc.add_comment("Comment", _("Vérification du stock {0} ({1}) : comptage validé par {2}, validé par {3}.")
                    .format(v.entrepot, v.name, frappe.utils.get_fullname(v.valide_employe_par),
                            frappe.utils.get_fullname(v.valide_responsable_par)))
    return doc.name


def _cloturer(v):
    """Les deux validations sont là : rapprochement, fiche terminée, tâches fermées."""
    v.rapprochement = _rapprochement(v)
    v.statut = TERMINEE
    v.termine_le, v.termine_par = now_datetime(), frappe.session.user
    v.flags.ignore_permissions = True
    v.save()
    for t in (v.tache_employe, v.tache_responsable):
        if t and frappe.db.get_value("Tache de travail", t, "status") == "Open":
            frappe.db.set_value("Tache de travail", t, "status", "Completed")


@frappe.whitelist(methods=["POST"])
def valider_verification(name, comptes=None, note=None):
    """Validation du responsable magasin (jamais l'employé du stock lui-même). Sans changement de quantité :
    rapprochement et clôture. Avec un ajustement : la fiche repasse à l'employé (« À confirmer »), l'ajustement
    tracé ligne à ligne (quantité de l'employé conservée)."""
    v = frappe.get_doc(VERIF, name)
    _responsable()
    if _est_employe_du_stock(v.entrepot):
        frappe.throw(_("Votre propre stock : un autre responsable magasin doit valider ce comptage."), frappe.PermissionError)
    if v.statut != A_VALIDER:
        frappe.throw(_("Cette vérification n’est pas à valider ({0}).").format(v.statut))
    comptes = _dict_json(comptes)
    bouges = _rafraichir_systeme(v)
    if bouges:
        return _renvoi_auto(v, bouges)
    avant = {l.item_code: (cint(l.compte), flt(l.qte_comptee, 6)) for l in v.lignes}
    _appliquer_comptage(v, comptes)
    ajustes = []
    for l in v.lignes:
        if (cint(l.compte), flt(l.qte_comptee, 6)) != avant[l.item_code]:
            l.ajuste = 1
            l.qte_employe = avant[l.item_code][1]
            ajustes.append("%s : %g → %g" % (l.item_name or l.item_code, avant[l.item_code][1], flt(l.qte_comptee, 6)))
    v.valide_responsable_par, v.valide_responsable_le = frappe.session.user, now_datetime()
    if note:
        v.note = ((v.note + "\n") if v.note else "") + note[:500]
    # Toujours vers l'employé : la validation est mutuelle, il accepte les quantités du responsable (ou en change
    # une, et la fiche revient ici).
    v.statut = A_CONFIRMER
    v.valide_employe_par = v.valide_employe_le = None
    v.flags.ignore_permissions = True
    v.save()
    emp = _employe_du_stock(v.entrepot)
    if emp and emp.user_id:
        _prevenir(emp.user_id, (_("🧾 Vérification {0} validée par {1} : {2} quantité(s) ajustée(s), à confirmer")
                                if ajustes else _("🧾 Vérification {0} validée par {1}, à confirmer"))
                  .format(v.name, frappe.utils.get_fullname(frappe.session.user), len(ajustes)), v.name)
    return dict(_resume(v), ajustes=ajustes)


@frappe.whitelist(methods=["POST"])
def confirmer_verification(name, comptes=None):
    """L'employé du stock répond aux quantités du responsable. Sans changement : validation mutuelle acquise,
    rapprochement, clôture. S'il en change une (`comptes`) : la fiche revient au responsable (« À valider »),
    le changement tracé en commentaire — c'est lui qui a le dernier mot."""
    v = frappe.get_doc(VERIF, name)
    if not _est_employe_du_stock(v.entrepot):
        frappe.throw(_("Seul l’employé de ce stock confirme les quantités validées."), frappe.PermissionError)
    if v.statut != A_CONFIRMER:
        frappe.throw(_("Cette vérification n’est pas à confirmer ({0}).").format(v.statut))
    bouges = _rafraichir_systeme(v)
    if bouges:
        return _renvoi_auto(v, bouges)
    comptes = _dict_json(comptes)
    avant = {l.item_code: (cint(l.compte), flt(l.qte_comptee, 6)) for l in v.lignes}
    _appliquer_comptage(v, comptes)
    changes = []
    for l in v.lignes:
        if (cint(l.compte), flt(l.qte_comptee, 6)) != avant[l.item_code]:
            l.qte_employe, l.ajuste = flt(l.qte_comptee, 6), 0
            l.commentaire = (_("Modifié par l’employé après validation ({0} → {1})")
                             .format(avant[l.item_code][1], flt(l.qte_comptee, 6)))[:140]
            changes.append("%s : %g → %g" % (l.item_name or l.item_code, avant[l.item_code][1], flt(l.qte_comptee, 6)))
    v.valide_employe_par, v.valide_employe_le = frappe.session.user, now_datetime()
    if changes:
        v.statut = A_VALIDER
        v.valide_responsable_par = v.valide_responsable_le = None
        v.flags.ignore_permissions = True
        v.save()
        cfg = config_verification()
        resp = frappe.db.get_value("Employee", cfg["responsable"], "user_id") if cfg["responsable"] else None
        if resp and resp != frappe.session.user:
            _prevenir(resp, _("🧾 Vérification {0} : {1} quantité(s) modifiée(s) par l’employé, à revalider")
                      .format(v.name, len(changes)), v.name)
        return dict(_resume(v), changes=changes)
    _cloturer(v)
    return dict(_resume(v), changes=[])


@frappe.whitelist(methods=["POST"])
def renvoyer_verification(name, motif=None):
    """Retour au comptage : par le responsable (« À valider ») ou par l'employé qui conteste un ajustement
    (« À confirmer »). Les validations tombent, les quantités saisies restent pour être corrigées."""
    v = frappe.get_doc(VERIF, name)
    _acces_verification(v.entrepot)
    if v.statut not in (A_VALIDER, A_CONFIRMER):
        frappe.throw(_("Cette vérification n’est pas en attente ({0}).").format(v.statut))
    if v.statut == A_VALIDER and not est_responsable():
        frappe.throw(_("Seul un responsable magasin renvoie un comptage à valider."), frappe.PermissionError)
    if v.statut == A_CONFIRMER and not _est_employe_du_stock(v.entrepot):
        frappe.throw(_("Seul l’employé de ce stock recompte une fiche à confirmer."), frappe.PermissionError)
    v.statut = EN_COURS
    v.valide_employe_par = v.valide_employe_le = v.valide_responsable_par = v.valide_responsable_le = None
    v.renvois = cint(v.renvois) + 1
    if motif:
        v.note = ((v.note + "\n") if v.note else "") + _("Renvoi par {0} : {1}").format(frappe.utils.get_fullname(frappe.session.user), motif[:300])
    v.flags.ignore_permissions = True
    v.save()
    if v.tache_employe and frappe.db.get_value("Tache de travail", v.tache_employe, "status") == "Completed":
        frappe.db.set_value("Tache de travail", v.tache_employe, "status", "Open")
    emp = _employe_du_stock(v.entrepot)
    if emp and emp.user_id and emp.user_id != frappe.session.user:
        _prevenir(emp.user_id, _("🧾 Vérification {0} renvoyée au comptage par {1}").format(v.name, frappe.utils.get_fullname(frappe.session.user)), v.name)
    return _resume(v)


@frappe.whitelist()
def historique_verifications(entrepot, limite=12):
    _acces_verification(entrepot)
    return [_resume(frappe.get_doc(VERIF, v.name)) for v in frappe.get_all(VERIF, filters={"entrepot": entrepot, "statut": TERMINEE},
                                                                       fields=["name"], order_by="termine_le desc", limit=min(cint(limite) or 12, 60))]


@frappe.whitelist(methods=["POST"])
def planifier_maintenant():
    """Bouton du responsable : exécute le cron tout de suite (utile le jour même ou pour tester)."""
    _responsable()
    return planifier_verifications()


# ── Stock cible par entrepôt d'employé ───────────────────────────────────────
#
# « Ce que le véhicule doit contenir » : article → quantité cible, approvisionné depuis le Magasin.
# Le réassort = cible − stock actuel, préparé en un geste dans l'onglet Transfert (demande 01/10/2026).

def stock_cible(entrepot: str) -> dict:
    if not entrepot or not frappe.db.exists("DocType", CIBLE) or not frappe.db.exists(CIBLE, entrepot):
        return {}
    return {r.item_code: flt(r.qte_cible) for r in frappe.get_all("Stock Cible Ligne", filters={"parent": entrepot, "parenttype": CIBLE},
                                                                   fields=["item_code", "qte_cible"])}


def _infos_articles(codes: list[str]) -> dict:
    if not codes:
        return {}
    return {i.name: i for i in frappe.get_all("Item", filters={"name": ["in", codes]},
                                              fields=["name", "item_name", "image", "stock_uom", "custom_emplacement_magasin"])}


@frappe.whitelist()
def get_stock_cible(entrepot, avec_modele=0):
    """Le stock cible de l'entrepôt avec, pour chaque article, le stock actuel ici et au Magasin, et le manque.
    `avec_modele` : sans cible propre, le modèle générique du réglage tient lieu de cible (`modele` = True)."""
    _responsable()
    _verifier_entrepot(entrepot)
    source = (frappe.db.get_value(CIBLE, entrepot, "source") if frappe.db.exists(CIBLE, entrepot) else None) or magasin()
    cible = stock_cible(entrepot)
    modele = False
    if not cible and cint(avec_modele):
        cible, modele = modele_stock_cible(), True
    infos = _infos_articles(list(cible))
    qtes = _quantites(list(cible), [entrepot, source])
    lignes = []
    for code, q in cible.items():
        i = infos.get(code)
        if not i:
            continue
        actuel = qtes.get((code, entrepot), 0.0)
        lignes.append({"item_code": code, "item_name": i.item_name, "image": i.image, "uom": i.stock_uom, "zones": i.custom_emplacement_magasin,
                       "qte_cible": q, "qte": actuel, "qte_source": qtes.get((code, source), 0.0), "manque": flt(max(q - actuel, 0), 6)})
    lignes.sort(key=lambda l: (l["item_name"] or l["item_code"]))
    return {"entrepot": entrepot, "source": source, "lignes": lignes, "modele": modele,
            "a_reassortir": sum(1 for l in lignes if l["manque"] > 0), "unites": flt(sum(l["manque"] for l in lignes), 6)}


def _ecrire_cible(entrepot: str, lignes: dict, source: str | None = None):
    doc = frappe.get_doc(CIBLE, entrepot) if frappe.db.exists(CIBLE, entrepot) else frappe.get_doc({"doctype": CIBLE, "entrepot": entrepot})
    if source:
        doc.source = source
    doc.set("lignes", [])
    for code, q in lignes.items():
        doc.append("lignes", {"item_code": code, "qte_cible": flt(q, 6)})
    doc.flags.ignore_permissions = True
    doc.save()


@frappe.whitelist(methods=["POST"])
def definir_stock_cible(entrepot, lignes, source=None):
    """Remplace le stock cible : lignes = [{item_code, qte_cible}] (qte_cible 0 ou vide = retiré)."""
    _responsable()
    _verifier_entrepot(entrepot)
    if entrepot == magasin():
        frappe.throw(_("Le Magasin est la source du réassort : il n'a pas de stock cible."))
    brut = frappe.parse_json(lignes) if isinstance(lignes, str) else (lignes or [])
    cible = {}
    for l in brut:
        q = flt(l.get("qte_cible"))
        if l.get("item_code") and q > 0:
            cible[l["item_code"]] = q
    refus = {c: m for c, m in _articles_transferables(list(cible)).items() if m}
    if refus:
        frappe.throw("<br>".join(f"{frappe.utils.escape_html(c)} : {m}" for c, m in refus.items()))
    _ecrire_cible(entrepot, cible, source)
    return get_stock_cible(entrepot)


@frappe.whitelist(methods=["POST"])
def ajouter_au_stock_cible(entrepot, items, qte_cible=None):
    """Ajoute des articles au stock cible (sans toucher ceux déjà présents). Sans quantité : la cible du
    seuil global de l'entrepôt, sinon le stock actuel, sinon 1."""
    _responsable()
    _verifier_entrepot(entrepot)
    codes = frappe.parse_json(items) if isinstance(items, str) else (items or [])
    cible = stock_cible(entrepot)
    seuil = seuils().get(entrepot)
    qtes = _quantites(codes, [entrepot])
    ajoutes = 0
    for c in codes:
        if c in cible:
            continue
        q = flt(qte_cible) if qte_cible else (seuil["cible"] if seuil else max(qtes.get((c, entrepot), 0.0), 1))
        cible[c] = q
        ajoutes += 1
    _ecrire_cible(entrepot, cible)
    return {"ajoutes": ajoutes, "total": len(cible)}


# ── Modèle générique de stock cible (réglage) ────────────────────────────────

def modele_stock_cible() -> dict:
    """Le stock cible générique du réglage : {article: quantité}."""
    if not frappe.db.exists("DocType", CONFIG):
        return {}
    return {r.item_code: flt(r.qte_cible) for r in frappe.get_all("Stock Cible Ligne", filters={"parent": CONFIG, "parenttype": CONFIG},
                                                                   fields=["item_code", "qte_cible"]) if flt(r.qte_cible) > 0}


def fusionner_cible(existant: dict, modele: dict, remplacer: bool = False) -> dict:
    """Le stock cible après application du modèle : les articles du modèle sont ajoutés ; ceux déjà présents
    gardent leur quantité, sauf `remplacer`. Les articles hors modèle restent. PURE."""
    out = dict(existant)
    for code, q in modele.items():
        if remplacer or code not in out:
            out[code] = q
    return out


# ── Saisie en bloc d'une liste d'articles (stock cible générique, stock cible d'un véhicule) ──
#
# « Coller une liste » : une ligne par article — code ou désignation, puis la quantité (séparée par une
# tabulation, « ; », « , », « x », « × » ou un espace). Sans quantité : la quantité par défaut du dialogue.

def parser_liste_articles(texte: str, qte_defaut: float = 1.0) -> list[tuple[str, float]]:
    """PURE. « C-10'-CTO 10 », « Cartouche, UDF 10'\t5 », « PF-10' x 3 », « A-C » → [(référence, quantité)]."""
    import re
    out = []
    for brute in (texte or "").splitlines():
        ligne = brute.strip().strip("-•*· ").strip()
        if not ligne:
            continue
        m = re.match(r"^(.*?)(?:\s*[\t;]\s*|\s+[x×X]\s*|\s+)(\d+(?:[.,]\d+)?)\s*$", ligne)
        if m and m.group(1).strip():
            out.append((m.group(1).strip().rstrip(",;"), flt(m.group(2).replace(",", "."), 6)))
        else:
            out.append((ligne.rstrip(",;"), flt(qte_defaut, 6)))
    return out


@frappe.whitelist()
def resoudre_liste_articles(texte, qte_defaut=1):
    """Chaque ligne collée → l'article suivi en stock qu'elle désigne : code exact (casse ignorée), puis désignation
    exacte, puis code-barres. Rend les lignes reconnues, les références inconnues et les articles non suivis."""
    _responsable()
    lignes, inconnus, non_suivis, vus = [], [], [], set()
    for ref, qte in parser_liste_articles(texte, flt(qte_defaut) or 1.0):
        code = (frappe.db.get_value("Item", {"name": ref}, "name")
                or frappe.db.get_value("Item", {"item_name": ref}, "name")
                or frappe.db.get_value("Item Barcode", {"barcode": ref}, "parent"))
        if not code:
            inconnus.append(ref)
            continue
        i = frappe.db.get_value("Item", code, ["name", "item_name", "stock_uom", "is_stock_item", "disabled"], as_dict=True)
        if not i.is_stock_item or i.disabled:
            non_suivis.append(f"{i.name} ({i.item_name})")
            continue
        if i.name in vus:
            continue
        vus.add(i.name)
        lignes.append({"item_code": i.name, "item_name": i.item_name, "uom": i.stock_uom, "qte": qte})
    return {"lignes": lignes, "inconnus": inconnus, "non_suivis": non_suivis}


def articles_non_suivis(codes: list[str]) -> list[str]:
    """Les codes qui ne sont pas des articles actifs suivis en stock — refusés dans un stock cible."""
    if not codes:
        return []
    ok = set(frappe.get_all("Item", filters={"name": ["in", codes], "is_stock_item": 1, "disabled": 0}, pluck="name"))
    return [c for c in codes if c not in ok]


@frappe.whitelist()
def get_modele_stock_cible():
    _responsable()
    m = modele_stock_cible()
    return {"articles": len(m), "unites": flt(sum(m.values()), 6)}


@frappe.whitelist(methods=["POST"])
def appliquer_modele_cible(entrepot, remplacer=0):
    """Applique le modèle générique au stock cible du véhicule (ajout, ou remplacement des quantités)."""
    _responsable()
    _verifier_entrepot(entrepot)
    if entrepot == magasin():
        frappe.throw(_("Le Magasin est la source du réassort : il n'a pas de stock cible."))
    modele = modele_stock_cible()
    if not modele:
        frappe.throw(_("Le stock cible générique est vide : remplissez-le dans Config Stock Entrepot."))
    _ecrire_cible(entrepot, fusionner_cible(stock_cible(entrepot), modele, cint(remplacer)))
    return get_stock_cible(entrepot)
