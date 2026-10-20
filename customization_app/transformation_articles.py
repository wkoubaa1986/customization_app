"""
Transformation d'articles (Page Desk « transformation-articles »).

Remplace l'usage du Rapprochement de stock pour « fabriquer » un article à partir
d'autres, ou récupérer les composants d'un article : une seule Écriture de stock
de type Reconditionnement (purpose Repack) sort les articles consommés du magasin
et y fait entrer les articles obtenus, la valeur passant des uns aux autres.

Règles :
- Seuls les articles dont le stock est suivi sont proposés (modèles compris).
- Entrepôt PAR LIGNE : chaque article part de (ou arrive dans) sa sortie
  principale (Item Default de la société, réglée dans Zones & sorties) ; sans
  sortie principale, le magasin choisi en haut de page. L'utilisateur peut
  changer l'entrepôt d'une ligne (demande du 20/10/2026). Le stock disponible
  est contrôlé dans l'entrepôt de chaque ligne (ERPNext refuserait de toute façon).
- Valeur : la valeur des articles obtenus = valeur des articles consommés
  (taux réels du grand livre, relevés après insertion du brouillon). Elle est
  répartie sur les articles obtenus au prorata de leur taux de valorisation
  actuel ; un taux saisi à la main sur une ligne est respecté, le reste de la
  valeur se répartit sur les autres lignes. ERPNext exige un taux manuel dès
  qu'il y a plusieurs articles obtenus : on le pose toujours.
- L'écriture est soumise directement ; « Annuler » depuis la page annule
  l'écriture (ERPNext remet quantités et valeurs).
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

from customization_app.zones_magasin import _societe as societe_courante
from customization_app.zones_magasin import entrepots_de_sortie

TYPE_ECRITURE = "Reconditionnement"
PURPOSE = "Repack"
MAGASIN_DEFAUT = "Magasins - A&S"
MARQUE = "Transformation d’articles"
PRECISION = 3
LIMITE_RECHERCHE = 30


# --------------------------------------------------------------------------- règles pures

def repartir_valeur(total: float, lignes: list[dict]) -> list[float]:
    """Taux unitaire de chaque ligne obtenue pour que la somme des valeurs = `total`.

    lignes : [{"qty", "taux_fixe" (None si libre), "taux_ref"}]. Les lignes à taux
    fixe gardent leur taux ; le reste de la valeur se répartit sur les lignes
    libres au prorata de qty × taux_ref (à parts égales par unité si aucune
    référence). Une valeur restante négative donne des taux à 0."""
    total = flt(total)
    fixe = sum(flt(l["qty"]) * flt(l["taux_fixe"]) for l in lignes if l.get("taux_fixe") is not None)
    restant = max(total - fixe, 0.0)
    libres = [l for l in lignes if l.get("taux_fixe") is None]
    poids = [flt(l["qty"]) * flt(l.get("taux_ref")) for l in libres]
    if libres and sum(poids) <= 0:
        poids = [flt(l["qty"]) for l in libres]
    somme = sum(poids) or 1.0
    taux = []
    i = 0
    for l in lignes:
        if l.get("taux_fixe") is not None:
            taux.append(flt(l["taux_fixe"], PRECISION))
        else:
            valeur = restant * poids[i] / somme
            taux.append(flt(valeur / flt(l["qty"]), PRECISION) if flt(l["qty"]) else 0.0)
            i += 1
    return taux


def resoudre_entrepot(ligne: dict, sortie: str | None, defaut: str) -> str:
    """Entrepôt d'une ligne : celui choisi sur la ligne, sinon la sortie principale
    de l'article, sinon le magasin de la page."""
    return (ligne.get("warehouse") or "").strip() or (sortie or "").strip() or defaut


def options_entrepot(sorties: list[str], defaut: str, courant: str | None = None) -> list[str]:
    """Choix proposés sur une ligne : les sorties autorisées, le magasin de la page et
    l'entrepôt déjà posé sur la ligne (jamais perdu), sans doublon, dans cet ordre."""
    out = []
    for w in [*sorties, defaut, courant or ""]:
        if w and w not in out:
            out.append(w)
    return out


def alertes_stock(consommes: list[dict]) -> list[str]:
    """Une alerte par article consommé au-delà du stock disponible."""
    out = []
    for l in consommes:
        if flt(l["qty"]) > flt(l.get("stock")) + 1e-9:
            out.append(_("{0} : {1} demandés, {2} en stock").format(l["item_code"], flt(l["qty"]), flt(l.get("stock"))))
    return out


def ecart_taux(taux_propose: float, taux_ref: float) -> float | None:
    """Écart relatif (en %) entre le taux proposé et le taux habituel, None sans référence."""
    if not flt(taux_ref):
        return None
    return flt((flt(taux_propose) - flt(taux_ref)) / flt(taux_ref) * 100, 1)


# --------------------------------------------------------------------------- accès données

def _verifier_acces():
    if not frappe.has_permission("Stock Entry", "create"):
        frappe.throw(_("Accès non autorisé"), frappe.PermissionError)


def _magasins() -> list[str]:
    return frappe.get_all("Warehouse", filters={"is_group": 0, "disabled": 0}, pluck="name", order_by="name")


def _sorties_principales(codes: list[str]) -> dict[str, str]:
    """Sortie principale de chaque article (Item Default de la société), vide si aucune."""
    if not codes:
        return {}
    rows = frappe.get_all("Item Default", filters={"parent": ["in", codes], "parenttype": "Item", "company": societe_courante()},
                          fields=["parent", "default_warehouse"])
    return {r.parent: r.default_warehouse for r in rows if r.default_warehouse}


def _sorties_autorisees() -> list[str]:
    return [w.name for w in entrepots_de_sortie()]


def _infos_articles(codes: list[str], warehouses) -> dict[str, dict]:
    """image, nom, unité, sortie principale, et par entrepôt demandé : stock et taux de
    valorisation (repli : taux de l'article). `warehouses` : un nom ou une liste."""
    if not codes:
        return {}
    if isinstance(warehouses, str):
        warehouses = [warehouses]
    warehouses = sorted({w for w in warehouses if w})
    items = frappe.db.sql("""
        SELECT i.name, i.item_name, i.image, i.stock_uom, i.valuation_rate AS taux_article, i.has_variants,
               (SELECT COUNT(*) FROM `tabProduct Bundle` pb WHERE pb.new_item_code = i.name) AS est_bundle
        FROM `tabItem` i WHERE i.name IN %(codes)s
    """, {"codes": tuple(codes)}, as_dict=True)
    bins = frappe.db.sql("""
        SELECT item_code, warehouse, actual_qty, valuation_rate FROM `tabBin`
        WHERE item_code IN %(codes)s AND warehouse IN %(ws)s
    """, {"codes": tuple(codes), "ws": tuple(warehouses)}, as_dict=True) if warehouses else []
    par_bin = {(b.item_code, b.warehouse): b for b in bins}
    sorties = _sorties_principales(codes)
    out = {}
    for r in items:
        stocks = {}
        for w in warehouses:
            b = par_bin.get((r.name, w))
            stocks[w] = {"stock": flt(b.actual_qty if b else 0, PRECISION),
                         "taux": flt((b.valuation_rate if b else 0) or r.taux_article, PRECISION)}
        out[r.name] = {
            "item_code": r.name, "item_name": r.item_name, "image": r.image or "", "uom": r.stock_uom,
            "sortie": sorties.get(r.name, ""), "stocks": stocks, "taux_article": flt(r.taux_article, PRECISION),
            "est_bundle": bool(r.est_bundle), "modele": bool(r.has_variants),
        }
    return out


def _stock_dans(info: dict, warehouse: str) -> dict:
    """{stock, taux} d'un article dans un entrepôt (taux de l'article si jamais stocké là)."""
    return (info or {}).get("stocks", {}).get(warehouse) or {"stock": 0.0, "taux": flt((info or {}).get("taux_article"), PRECISION)}


@frappe.whitelist()
def rechercher(txt, warehouse=None):
    """Articles suivis en stock dont le code ou le nom contient `txt` (code exact d'abord)."""
    _verifier_acces()
    warehouse = warehouse or MAGASIN_DEFAUT
    txt = (txt or "").strip()
    if len(txt) < 2:
        return []
    # Multi-mots : « membrane 600 » trouve « Membrane, 3013-600 GPD » — chaque mot doit
    # apparaître dans le code ou le nom, dans n'importe quel ordre.
    mots = [m for m in txt.split() if m]
    params = {"t": txt, "p": f"{txt}%", "n": LIMITE_RECHERCHE}
    conds = []
    for i, m in enumerate(mots):
        params[f"m{i}"] = f"%{m}%"
        conds.append(f"(name LIKE %(m{i})s OR item_name LIKE %(m{i})s)")
    codes = frappe.db.sql_list(f"""
        SELECT name FROM `tabItem`
        WHERE is_stock_item = 1 AND disabled = 0
          AND {" AND ".join(conds)}
        ORDER BY (name = %(t)s) DESC, (name LIKE %(p)s) DESC, LENGTH(name), name
        LIMIT %(n)s
    """, params)
    sorties = _sorties_principales(codes)
    infos = _infos_articles(codes, [warehouse, *sorties.values()])
    out = []
    for c in codes:
        if c not in infos:
            continue
        i = dict(infos[c])
        # L'article sera pris (ou rangé) dans sa sortie principale : on montre le stock de là.
        i["warehouse"] = resoudre_entrepot({}, i["sortie"], warehouse)
        i.update(_stock_dans(i, i["warehouse"]))
        i.pop("stocks", None)
        out.append(i)
    return out


@frappe.whitelist()
def composants(item_code):
    """Composants (suivis en stock) du Product Bundle de cet article, sinon []."""
    _verifier_acces()
    bundle = frappe.db.get_value("Product Bundle", {"new_item_code": item_code}, "name")
    if not bundle:
        return []
    rows = frappe.get_all("Product Bundle Item", filters={"parent": bundle}, fields=["item_code", "qty"], order_by="idx")
    suivis = set(frappe.get_all("Item", filters={"name": ["in", [r.item_code for r in rows]], "is_stock_item": 1}, pluck="name"))
    return [{"item_code": r.item_code, "qty": flt(r.qty)} for r in rows if r.item_code in suivis]


def _lire(lignes) -> list[dict]:
    if isinstance(lignes, str):
        lignes = json.loads(lignes or "[]")
    out = []
    for l in lignes or []:
        if not l.get("item_code") or flt(l.get("qty")) <= 0:
            continue
        taux = l.get("taux_fixe")
        out.append({"item_code": l["item_code"], "qty": flt(l["qty"]),
                    "taux_fixe": flt(taux) if taux not in (None, "", 0, "0") else None,
                    "warehouse": (l.get("warehouse") or "").strip() or None})
    return out


@frappe.whitelist()
def simuler(consommes, obtenus, warehouse=None):
    """Aperçu avant validation : stocks, taux, valeurs, répartition, alertes."""
    _verifier_acces()
    warehouse = warehouse or MAGASIN_DEFAUT
    consommes, obtenus = _lire(consommes), _lire(obtenus)
    codes = [l["item_code"] for l in consommes + obtenus]
    sorties = _sorties_principales(codes)
    for l in consommes + obtenus:
        l["sortie"] = sorties.get(l["item_code"], "")
        l["warehouse"] = resoudre_entrepot(l, l["sortie"], warehouse)
    infos = _infos_articles(codes, [l["warehouse"] for l in consommes + obtenus])
    for l in consommes:
        i = infos.get(l["item_code"], {})
        s = _stock_dans(i, l["warehouse"])
        l.update({"item_name": i.get("item_name"), "image": i.get("image"), "uom": i.get("uom"),
                  "stock": s["stock"], "taux": s["taux"], "est_bundle": i.get("est_bundle", False)})
        l["valeur"] = flt(l["qty"] * l["taux"], PRECISION)
    total_conso = flt(sum(l["valeur"] for l in consommes), PRECISION)
    for l in obtenus:
        i = infos.get(l["item_code"], {})
        s = _stock_dans(i, l["warehouse"])
        l.update({"item_name": i.get("item_name"), "image": i.get("image"), "uom": i.get("uom"),
                  "stock": s["stock"], "taux_ref": s["taux"], "est_bundle": i.get("est_bundle", False)})
    taux = repartir_valeur(total_conso, obtenus)
    for l, t in zip(obtenus, taux):
        l["taux"] = t
        l["valeur"] = flt(l["qty"] * t, PRECISION)
        l["ecart_pct"] = ecart_taux(t, l["taux_ref"])
    total_obt = flt(sum(l["valeur"] for l in obtenus), PRECISION)
    alertes = alertes_stock(consommes)
    if consommes and not obtenus:
        alertes.append(_("Aucun article obtenu : ajoutez au moins un article dans « J’obtiens »."))
    if obtenus and not consommes:
        alertes.append(_("Aucun article consommé : ajoutez au moins un article dans « Je consomme »."))
    memes = {(l["item_code"], l["warehouse"]) for l in consommes} & {(l["item_code"], l["warehouse"]) for l in obtenus}
    if memes:
        alertes.append(_("Même article des deux côtés, dans le même entrepôt : {0}").format(
            ", ".join(sorted(f"{c} ({w})" for c, w in memes))))
    return {"warehouse": warehouse, "consommes": consommes, "obtenus": obtenus,
            "total_consomme": total_conso, "total_obtenu": total_obt,
            "ecart_valeur": flt(total_conso - total_obt, PRECISION), "alertes": alertes,
            "valide": not alertes and bool(consommes) and bool(obtenus)}


def _assurer_type_ecriture() -> str:
    """Le type « Reconditionnement » (purpose Repack) n'existe pas sur ce site : on le crée une fois."""
    existant = frappe.db.get_value("Stock Entry Type", {"purpose": PURPOSE}, "name")
    if existant:
        return existant
    frappe.get_doc({"doctype": "Stock Entry Type", "name": TYPE_ECRITURE, "purpose": PURPOSE,
                    "add_to_transit": 0}).insert(ignore_permissions=True)
    return TYPE_ECRITURE


@frappe.whitelist()
def valider(consommes, obtenus, warehouse=None, posting_date=None, remarque=None):
    """Crée et soumet l'écriture Reconditionnement. Renvoie son nom et le récapitulatif."""
    _verifier_acces()
    warehouse = warehouse or MAGASIN_DEFAUT
    sim = simuler(consommes, obtenus, warehouse)
    if not sim["valide"]:
        frappe.throw("<br>".join(sim["alertes"]) or _("Rien à transformer."))
    doc = frappe.new_doc("Stock Entry")
    doc.update({
        "stock_entry_type": _assurer_type_ecriture(), "purpose": PURPOSE,
        "posting_date": posting_date or nowdate(), "set_posting_time": 1 if posting_date else 0,
        "remarks": f"{MARQUE}" + (f" — {remarque.strip()}" if (remarque or "").strip() else ""),
    })
    for l in sim["consommes"]:
        doc.append("items", {"item_code": l["item_code"], "qty": l["qty"], "s_warehouse": l["warehouse"]})
    for l in sim["obtenus"]:
        # ⚠️ Avec set_basic_rate_manually, ERPNext saute la ligne dans set_basic_rate() et ne
        # recalcule PAS basic_amount : il faut le poser soi-même, sinon la valeur entrée est 0.
        doc.append("items", {"item_code": l["item_code"], "qty": l["qty"], "t_warehouse": l["warehouse"],
                             "is_finished_item": 1, "set_basic_rate_manually": 1,
                             "basic_rate": l["taux"], "basic_amount": flt(l["qty"] * l["taux"], PRECISION)})
    doc.insert()
    # Les taux de sortie RÉELS viennent du grand livre (posés par ERPNext à l'insertion) : on
    # recale les taux des articles obtenus dessus pour que rien ne parte en écart de stock.
    total_reel = flt(sum(flt(d.basic_amount) for d in doc.items if d.s_warehouse), PRECISION)
    lignes = [{"qty": l["qty"], "taux_fixe": l.get("taux_fixe"), "taux_ref": l["taux_ref"]} for l in sim["obtenus"]]
    taux = repartir_valeur(total_reel, lignes)
    for d, t in zip([d for d in doc.items if d.t_warehouse], taux):
        d.basic_rate = t
        d.basic_amount = flt(flt(d.transfer_qty or d.qty) * t, PRECISION)
    doc.save()
    doc.submit()
    return {"name": doc.name, "total_consomme": total_reel,
            "total_obtenu": flt(sum(flt(d.basic_amount) for d in doc.items if d.t_warehouse), PRECISION)}


@frappe.whitelist()
def annuler(name):
    """Annule une écriture Reconditionnement créée par la page."""
    _verifier_acces()
    doc = frappe.get_doc("Stock Entry", name)
    if doc.purpose != PURPOSE:
        frappe.throw(_("{0} n’est pas une écriture de reconditionnement.").format(name))
    if doc.docstatus != 1:
        frappe.throw(_("{0} n’est pas soumise.").format(name))
    doc.cancel()
    return True


@frappe.whitelist()
def historique(limite=20, warehouse=None):
    """Dernières transformations (écritures Repack), avec leurs lignes, pour relecture, annulation ou inversion."""
    _verifier_acces()
    filtres = {"purpose": PURPOSE, "docstatus": ["in", [1, 2]]}
    entetes = frappe.get_all("Stock Entry", filters=filtres, fields=["name", "posting_date", "docstatus", "remarks", "owner", "creation"],
                             order_by="posting_date desc, creation desc", limit=cint(limite) or 20)
    if not entetes:
        return []
    lignes = frappe.get_all("Stock Entry Detail", filters={"parent": ["in", [e.name for e in entetes]]},
                            fields=["parent", "item_code", "item_name", "qty", "s_warehouse", "t_warehouse", "basic_rate", "basic_amount"],
                            order_by="idx")
    par = {}
    for l in lignes:
        par.setdefault(l.parent, {"consommes": [], "obtenus": []})
        cible = "consommes" if l.s_warehouse else "obtenus"
        par[l.parent][cible].append({"item_code": l.item_code, "item_name": l.item_name, "qty": flt(l.qty),
                                     "taux": flt(l.basic_rate, PRECISION), "valeur": flt(l.basic_amount, PRECISION),
                                     "warehouse": l.s_warehouse or l.t_warehouse})
    out = []
    for e in entetes:
        d = par.get(e.name, {"consommes": [], "obtenus": []})
        magasins = {l["warehouse"] for l in d["consommes"] + d["obtenus"]}
        if warehouse and magasins and warehouse not in magasins:
            continue
        out.append({"name": e.name, "date": str(e.posting_date), "docstatus": e.docstatus, "remarque": e.remarks or "",
                    "owner": e.owner, "warehouse": ", ".join(sorted(magasins)), **d,
                    "valeur": flt(sum(l["valeur"] for l in d["consommes"]), PRECISION)})
    return out


@frappe.whitelist()
def get_context():
    """Ce qu'il faut pour ouvrir la page : magasins, magasin par défaut, historique."""
    _verifier_acces()
    magasins = _magasins()
    defaut = MAGASIN_DEFAUT if MAGASIN_DEFAUT in magasins else (magasins[0] if magasins else "")
    return {"magasins": magasins, "magasin_defaut": defaut, "sorties": _sorties_autorisees(), "aujourdhui": nowdate(),
            "currency": frappe.get_cached_value("Company", frappe.defaults.get_global_default("company"), "default_currency") or "TND",
            "historique": historique(20), "modeles": modeles()}


# --------------------------------------------------------------------------- modèles réutilisables

def multiplier_lignes(lignes: list[dict], facteur: float) -> list[dict]:
    """Quantités d'un modèle unitaire × nombre d'applications (jamais < 1)."""
    f = max(flt(facteur), 1.0)
    return [{"item_code": l["item_code"], "qty": flt(flt(l["qty"]) * f, PRECISION), "warehouse": l.get("warehouse") or None}
            for l in lignes]


@frappe.whitelist()
def modeles():
    """Tous les modèles, avec leurs lignes unitaires."""
    _verifier_acces()
    entetes = frappe.get_all("Modele Transformation", fields=["name", "nom", "remarque", "modified"], order_by="nom")
    if not entetes:
        return []
    lignes = frappe.get_all("Modele Transformation Ligne", filters={"parent": ["in", [e.name for e in entetes]]},
                            fields=["parent", "parentfield", "item_code", "item_name", "qty", "warehouse"], order_by="idx")
    par = {}
    for l in lignes:
        par.setdefault(l.parent, {"consommes": [], "obtenus": []})[l.parentfield].append(
            {"item_code": l.item_code, "item_name": l.item_name, "qty": flt(l.qty), "warehouse": l.warehouse or None})
    return [{"name": e.name, "nom": e.nom, "remarque": e.remarque or "", **par.get(e.name, {"consommes": [], "obtenus": []})}
            for e in entetes]


@frappe.whitelist()
def enregistrer_modele(nom, consommes, obtenus, remarque=None, facteur=1):
    """Enregistre (ou remplace, même nom) la transformation courante comme modèle UNITAIRE :
    les quantités saisies sont divisées par `facteur` (nombre d'applications qu'elles représentent)."""
    _verifier_acces()
    nom = (nom or "").strip()
    if not nom:
        frappe.throw(_("Donnez un nom au modèle."))
    f = max(flt(facteur), 1.0)
    conso, obt = _lire(consommes), _lire(obtenus)
    if not conso or not obt:
        frappe.throw(_("Un modèle a au moins un article consommé et un article obtenu."))
    doc = frappe.get_doc("Modele Transformation", nom) if frappe.db.exists("Modele Transformation", nom) \
        else frappe.new_doc("Modele Transformation")
    doc.nom = nom
    doc.remarque = remarque
    doc.set("consommes", [{"item_code": l["item_code"], "qty": flt(l["qty"] / f, PRECISION), "warehouse": l.get("warehouse")} for l in conso])
    doc.set("obtenus", [{"item_code": l["item_code"], "qty": flt(l["qty"] / f, PRECISION), "warehouse": l.get("warehouse")} for l in obt])
    doc.save()
    return doc.name


@frappe.whitelist()
def supprimer_modele(name):
    _verifier_acces()
    frappe.delete_doc("Modele Transformation", name)
    return True


@frappe.whitelist()
def appliquer_modele(name, facteur=1):
    """Lignes du modèle × facteur, prêtes à charger dans l'éditeur (rien n'est écrit)."""
    _verifier_acces()
    m = next((x for x in modeles() if x["name"] == name), None)
    if not m:
        frappe.throw(_("Modèle introuvable : {0}").format(name))
    return {"consommes": multiplier_lignes(m["consommes"], facteur),
            "obtenus": multiplier_lignes(m["obtenus"], facteur), "nom": m["nom"]}
