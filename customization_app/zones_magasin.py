"""Zones du magasin et sortie principale des articles — page Stock « Zones & sorties d’articles ».

Demande du 01/10/2026, précisée le soir même :
- deux niveaux : des ESPACES (Magasin, Hall…) qui contiennent des ZONES, nommées « Espace - Code »
  (« Magasin - A1 », « Hall - K3 ») — noms uniques, le même code peut exister dans deux espaces ;
- un article peut être rangé dans PLUSIEURS zones, et l'on affecte dans les deux sens : depuis
  l'article (ses zones) ou depuis la zone (ses articles) ;
- les zones de l'article s'affichent dans « Emplacement Magasin » (texte, « K-1 / Hall ») ;
- même page : la SORTIE PRINCIPALE de l'article (entrepôt par défaut de la société : Magasin ou
  Hall seulement depuis le 12/10/2026), prise
  automatiquement par une commande, un BL ou une facture qui n'impose pas d'entrepôt. Un document
  qui en impose un (stock du véhicule d'un employé, profil de caisse) garde la main — même règle
  dans customization_app.get_item_details.

Stockage : table `custom_zones_magasin` (Article Zone Magasin) de l'article. Les lots écrivent
les lignes DIRECTEMENT (pas d'Item.save : un article ancien mal formé ferait échouer tout le
lot pour une simple étiquette de rangement) puis réécrivent le texte d'emplacement.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt

from customization_app.customize_erpnext.doctype.zone_magasin.zone_magasin import (SEPARATEUR, decouper_codes,
                                                                                nom_complet, normaliser_code)

DOCTYPE_ZONE = "Zone Magasin"
DOCTYPE_LIEN = "Article Zone Magasin"
TEXTE = "custom_emplacement_magasin"
TABLE = "custom_zones_magasin"
SANS_ZONE = "__sans__"
SANS_SORTIE = "__sans__"
# Filtre « tout l'espace » : l'espace ET toutes ses zones. Un nom de zone ne contient jamais « / ».
TOUT_ESPACE = "tout/"
PAGE = 60
MAX_LOT = 5000
# Demande du 12/10/2026 : la sortie principale d'un article est le Magasin ou le Hall, rien d'autre
# (ni le stock d'un employé, ni les articles défectueux). Vérifié par la page ET à l'enregistrement
# de la fiche article (item_validate) : la table « Item Defaults » ne contourne pas la règle.
SORTIES_AUTORISEES = ("Magasins - A&S", "Hall - A&S")


def _lecture():
    frappe.has_permission("Item", "read", throw=True)


def _ecriture():
    if not frappe.has_permission("Item", "write"):
        frappe.throw(_("Modifier les articles n'est pas ouvert à votre compte."), frappe.PermissionError)


def _societe() -> str:
    return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value("Global Defaults", "default_company") \
        or frappe.get_all("Company", pluck="name", limit=1)[0]


def entrepots_de_sortie(societe: str | None = None) -> list[dict]:
    """Les entrepôts qui peuvent servir de sortie : Magasin ou Hall, actifs, de la société."""
    return frappe.get_all("Warehouse", filters={"name": ["in", SORTIES_AUTORISEES], "is_group": 0, "disabled": 0,
                                                "company": societe or _societe()},
                          fields=["name", "warehouse_name"], order_by="lft")


# ── Lien article ↔ zones ─────────────────────────────────────────────────────

def zones_de(item: str) -> list[str]:
    return frappe.get_all(DOCTYPE_LIEN, filters={"parent": item, "parenttype": "Item"}, pluck="zone", order_by="idx")


def _resynchroniser(item: str):
    """Renumérote les lignes et réécrit « Emplacement Magasin » d'après les zones de l'article."""
    lignes = frappe.get_all(DOCTYPE_LIEN, filters={"parent": item, "parenttype": "Item"}, fields=["name", "zone"],
                            order_by="idx")
    for i, l in enumerate(lignes, 1):
        frappe.db.set_value(DOCTYPE_LIEN, l.name, "idx", i, update_modified=False)
    frappe.db.set_value("Item", item, TEXTE, SEPARATEUR.join(l.zone for l in lignes) or None)
    frappe.clear_document_cache("Item", item)


def _ajouter(item: str, zone: str) -> bool:
    if frappe.db.exists(DOCTYPE_LIEN, {"parent": item, "parenttype": "Item", "zone": zone}):
        return False
    idx = cint(frappe.db.sql(f"select max(idx) from `tab{DOCTYPE_LIEN}` where parent = %s", item)[0][0]) + 1
    frappe.get_doc({"doctype": DOCTYPE_LIEN, "parent": item, "parenttype": "Item", "parentfield": TABLE,
                    "idx": idx, "zone": zone}).db_insert()
    return True


def _retirer(item: str, zone: str) -> bool:
    noms = frappe.get_all(DOCTYPE_LIEN, filters={"parent": item, "parenttype": "Item", "zone": zone}, pluck="name")
    for n in noms:
        frappe.db.delete(DOCTYPE_LIEN, {"name": n})
    return bool(noms)


def item_validate(doc, method=None):
    """Hook Item.validate : la fiche article (table « Zones magasin ») réécrit aussi le texte, et sa
    sortie principale (Item Defaults de la société) ne peut être que le Magasin ou le Hall."""
    societe = _societe()
    for d in doc.get("item_defaults") or []:
        if d.company == societe and d.default_warehouse and d.default_warehouse not in SORTIES_AUTORISEES:
            frappe.throw(_("Sortie principale (entrepôt par défaut) : Magasin ou Hall seulement — « {0} » refusé.")
                         .format(d.default_warehouse))
    vues, lignes = set(), []
    for r in doc.get(TABLE) or []:
        if r.zone and r.zone not in vues:
            vues.add(r.zone)
            lignes.append(r)
    doc.set(TABLE, lignes)
    doc.set(TEXTE, SEPARATEUR.join(r.zone for r in lignes) or None)


# ── Lecture ──────────────────────────────────────────────────────────────────

def _zones() -> list[dict]:
    """Toutes les zones (espaces compris), avec leur nombre d'articles actifs, espaces d'abord.
    Un espace porte aussi `total` : ses articles, directement ou dans l'une de ses zones (sans doublon)."""
    comptes = dict(frappe.db.sql(f"""select l.zone, count(distinct l.parent) from `tab{DOCTYPE_LIEN}` l
                                     join tabItem i on i.name = l.parent and i.disabled = 0
                                     where l.parenttype = 'Item' group by l.zone"""))
    totaux = dict(frappe.db.sql(f"""select coalesce(z.espace, z.name), count(distinct l.parent) from `tab{DOCTYPE_LIEN}` l
                                    join `tab{DOCTYPE_ZONE}` z on z.name = l.zone
                                    join tabItem i on i.name = l.parent and i.disabled = 0
                                    where l.parenttype = 'Item' group by coalesce(z.espace, z.name)"""))
    return [{"name": z.name, "code": z.code, "espace": z.espace, "est_espace": cint(z.est_espace),
             "articles": cint(comptes.get(z.name)), "total": cint(totaux.get(z.name)) if z.est_espace else None}
            for z in frappe.get_all(DOCTYPE_ZONE, fields=["name", "code", "espace", "est_espace"],
                                    order_by="est_espace desc, espace, code")]


def _groupes() -> list[dict]:
    """L'arbre des groupes d'articles, dans l'ordre de l'arbre (racine exclue) : profondeur et nombre
    d'articles actifs de la BRANCHE (sous-groupes compris). Les branches vides sont omises."""
    groupes = frappe.get_all("Item Group", fields=["name", "is_group", "lft", "rgt"], order_by="lft")
    comptes = dict(frappe.db.sql("select item_group, count(*) from tabItem where disabled = 0 group by item_group"))
    out, pile = [], []
    for g in groupes:
        while pile and pile[-1] < g.lft:
            pile.pop()
        profondeur = len(pile)
        pile.append(g.rgt)
        if profondeur == 0:                         # « Tous les Groupes d'Articles » = pas de filtre
            continue
        n = sum(cint(comptes.get(x.name)) for x in groupes if g.lft <= x.lft and x.rgt <= g.rgt)
        if n:
            out.append({"name": g.name, "profondeur": profondeur - 1, "is_group": cint(g.is_group), "articles": n})
    return out


@frappe.whitelist()
def get_context():
    _lecture()
    from customization_app.aramex_stock import ENTREPOT_ARAMEX

    societe = _societe()
    sans = frappe.db.sql(f"""select count(*) from tabItem i where i.disabled = 0 and i.is_stock_item = 1
                             and not exists (select 1 from `tab{DOCTYPE_LIEN}` l
                                             where l.parent = i.name and l.parenttype = 'Item')""")[0][0]
    return {"zones": _zones(), "entrepots": entrepots_de_sortie(societe), "groupes": _groupes(), "societe": societe,
            "sans_zone": sans, "entrepot_aramex": ENTREPOT_ARAMEX,
            "peut_modifier": bool(frappe.has_permission("Item", "write"))}


def _filtres(recherche, groupe, zone, sortie, stockes):
    """Clause SQL des filtres de la vue « Par article » (alias i = Item, d = Item Default de la société)."""
    conditions, valeurs = ["i.disabled = 0"], {"societe": _societe()}
    if cint(stockes):
        conditions.append("i.is_stock_item = 1")
    if recherche:
        conditions.append("(i.name like %(q)s or i.item_name like %(q)s)")
        valeurs["q"] = f"%{recherche}%"
    if groupe:
        # Un groupe parent prend toute sa branche (lft/rgt de l'arbre des groupes).
        bornes = frappe.db.get_value("Item Group", groupe, ["lft", "rgt"])
        if bornes:
            conditions.append("i.item_group in (select g.name from `tabItem Group` g"
                              " where g.lft >= %(g_lft)s and g.rgt <= %(g_rgt)s)")
            valeurs.update(g_lft=bornes[0], g_rgt=bornes[1])
        else:
            conditions.append("i.item_group = %(groupe)s")
            valeurs["groupe"] = groupe
    lien = f"select 1 from `tab{DOCTYPE_LIEN}` l where l.parent = i.name and l.parenttype = 'Item'"
    if zone == SANS_ZONE:
        conditions.append(f"not exists ({lien})")
    elif zone and zone.startswith(TOUT_ESPACE):
        espace = zone[len(TOUT_ESPACE):]
        valeurs["zones"] = [espace] + frappe.get_all(DOCTYPE_ZONE, filters={"espace": espace}, pluck="name")
        conditions.append(f"exists ({lien} and l.zone in %(zones)s)")
    elif zone:
        conditions.append(f"exists ({lien} and l.zone = %(zone)s)")
        valeurs["zone"] = zone
    if sortie == SANS_SORTIE:
        conditions.append("ifnull(d.default_warehouse, '') = ''")
    elif sortie:
        conditions.append("d.default_warehouse = %(sortie)s")
        valeurs["sortie"] = sortie
    jointure = "left join `tabItem Default` d on d.parent = i.name and d.parenttype = 'Item' and d.company = %(societe)s"
    return f"from tabItem i {jointure} where {' and '.join(conditions)}", valeurs


@frappe.whitelist()
def get_articles(recherche=None, groupe=None, zone=None, sortie=None, stockes=1, start=0, limite=PAGE, codes_seulement=0):
    """Une page d'articles actifs avec leurs zones, leur sortie principale et leur stock par entrepôt.
    `codes_seulement` : tous les codes du filtre (action de groupe sur « tout le filtre »)."""
    _lecture()
    base, valeurs = _filtres(recherche, groupe, zone, sortie, stockes)
    if cint(codes_seulement):
        codes = [r[0] for r in frappe.db.sql(f"select i.name {base} order by i.item_name limit {MAX_LOT + 1}", valeurs)]
        if len(codes) > MAX_LOT:
            frappe.throw(_("Plus de {0} articles : affinez le filtre.").format(MAX_LOT))
        return {"codes": codes}
    total = frappe.db.sql(f"select count(*) {base}", valeurs)[0][0]
    valeurs.update({"start": cint(start), "page": min(cint(limite) or PAGE, 200)})
    rows = frappe.db.sql(f"""select i.name as item_code, i.item_name, i.item_group, i.image, i.stock_uom,
                                    d.default_warehouse as sortie
                             {base} order by i.item_name limit %(page)s offset %(start)s""", valeurs, as_dict=True)
    codes = [r.item_code for r in rows]
    stocks, zones = {}, {}
    for b in (frappe.get_all("Bin", filters={"item_code": ["in", codes], "actual_qty": ["!=", 0]},
                             fields=["item_code", "warehouse", "actual_qty"]) if codes else []):
        stocks.setdefault(b.item_code, []).append({"entrepot": b.warehouse, "qte": flt(b.actual_qty)})
    for l in (frappe.get_all(DOCTYPE_LIEN, filters={"parent": ["in", codes], "parenttype": "Item"},
                             fields=["parent", "zone"], order_by="idx") if codes else []):
        zones.setdefault(l.parent, []).append(l.zone)
    for r in rows:
        r["stocks"] = sorted(stocks.get(r.item_code, []), key=lambda s: -s["qte"])
        r["zones"] = zones.get(r.item_code, [])
    fin = cint(start) + len(rows)
    return {"articles": rows, "total": total, "suivant": fin if fin < total else None}


# ── Zones : ajout, renommage (fusion), suppression ───────────────────────────

@frappe.whitelist()
def creer_zones(texte, espace=None):
    """Ajoute d'un coup une ou plusieurs zones dans un espace (« A1, A2, A3 » ou une par ligne),
    ou, sans espace, un ou plusieurs espaces (« Hall, Dépôt »)."""
    _ecriture()
    codes = decouper_codes(texte)
    if not codes:
        frappe.throw(_("Écrivez un code (plusieurs : séparez par des virgules)."))
    if espace and not frappe.db.get_value(DOCTYPE_ZONE, espace, "est_espace"):
        frappe.throw(_("Espace inconnu : {0}").format(espace))
    creees, deja = [], []
    for code in codes:
        nom = nom_complet(espace, code)
        existe = frappe.db.sql(f"select name from `tab{DOCTYPE_ZONE}` where lower(name) = lower(%s)", nom)
        if existe:
            deja.append(existe[0][0])
            continue
        frappe.get_doc({"doctype": DOCTYPE_ZONE, "est_espace": 0 if espace else 1, "espace": espace,
                        "code": code}).insert()
        creees.append(nom)
    return {"creees": creees, "deja": deja, "zones": _zones()}


def _articles_de(zones: list[str]) -> set[str]:
    return set(frappe.get_all(DOCTYPE_LIEN, filters={"zone": ["in", zones], "parenttype": "Item"}, pluck="parent")) \
        if zones else set()


def _renommer_doc(ancien: str, nouveau: str, **champs):
    frappe.rename_doc(DOCTYPE_ZONE, ancien, nouveau, force=True)
    frappe.db.set_value(DOCTYPE_ZONE, nouveau, dict(champs, libelle=nouveau))


@frappe.whitelist()
def renommer_zone(ancien, nouveau):
    """Change le code d'une zone ou le nom d'un espace.

    Zone : si le nouveau nom complet existe déjà dans l'espace, les deux zones FUSIONNENT (ses
    articles rejoignent l'autre). Espace : toutes ses zones suivent (« Hall - K1 » -> « Dépôt - K1 »)."""
    _ecriture()
    z = frappe.get_doc(DOCTYPE_ZONE, ancien)
    code = normaliser_code(nouveau)
    if not code or code == z.code:
        return {"zones": _zones(), "zone": ancien}
    if "/" in code:
        frappe.throw(_("Une zone est un seul endroit : pas de « / » dans son nom."))
    if z.est_espace:
        if frappe.db.sql(f"select 1 from `tab{DOCTYPE_ZONE}` where lower(name) = lower(%s)", code):
            frappe.throw(_("« {0} » existe déjà.").format(code))
        enfants = frappe.get_all(DOCTYPE_ZONE, filters={"espace": ancien}, fields=["name", "code"])
        touches = _articles_de([ancien] + [e.name for e in enfants])
        _renommer_doc(ancien, code, code=code)
        for e in enfants:
            _renommer_doc(e.name, nom_complet(code, e.code), espace=code)
        for item in touches:
            _resynchroniser(item)
        return {"zones": _zones(), "zone": code}
    cible = nom_complet(z.espace, code)
    existe = frappe.db.sql(f"select name from `tab{DOCTYPE_ZONE}` where lower(name) = lower(%s) and name != %s",
                           (cible, ancien))
    touches = _articles_de([ancien])
    if existe:
        cible = existe[0][0]
        for item in touches:
            _retirer(item, ancien)
            _ajouter(item, cible)
        frappe.delete_doc(DOCTYPE_ZONE, ancien)
    else:
        _renommer_doc(ancien, cible, code=code)
    for item in touches:
        _resynchroniser(item)
    return {"zones": _zones(), "zone": cible, "fusion": bool(existe)}


@frappe.whitelist()
def supprimer_zone(zone):
    """Retire la zone de tous ses articles puis la supprime ; un espace emporte ses zones."""
    _ecriture()
    zones = [zone] + frappe.get_all(DOCTYPE_ZONE, filters={"espace": zone}, pluck="name")
    touches = _articles_de(zones)
    for item in touches:
        for z in zones:
            _retirer(item, z)
        _resynchroniser(item)
    for z in reversed(zones):                       # les zones avant leur espace (lien)
        frappe.delete_doc(DOCTYPE_ZONE, z)
    return {"zones": _zones(), "liberes": len(touches), "supprimees": len(zones)}


# ── Affectations ─────────────────────────────────────────────────────────────

def _liste(items) -> list[str]:
    items = frappe.parse_json(items) if isinstance(items, str) else items
    items = list(dict.fromkeys(i for i in (items or []) if i))
    if not items:
        frappe.throw(_("Sélectionnez au moins un article."))
    inconnus = set(items) - set(frappe.get_all("Item", filters={"name": ["in", items]}, pluck="name"))
    if inconnus:
        frappe.throw(_("Articles introuvables : {0}").format(", ".join(sorted(inconnus))))
    return items


def _zone_existante(zone: str):
    if not zone or not frappe.db.exists(DOCTYPE_ZONE, zone):
        frappe.throw(_("Zone inconnue : {0}").format(zone))


@frappe.whitelist()
def ajouter_a_zone(zone, items):
    """Depuis la zone (ou la barre de lot) : range ces articles AUSSI dans la zone."""
    _ecriture()
    _zone_existante(zone)
    items = _liste(items)
    ajoutes = [i for i in items if _ajouter(i, zone)]
    for i in ajoutes:
        _resynchroniser(i)
    return {"ajoutes": len(ajoutes), "deja": len(items) - len(ajoutes), "zones": _zones()}


@frappe.whitelist()
def retirer_de_zone(zone, items):
    _ecriture()
    _zone_existante(zone)
    items = _liste(items)
    retires = [i for i in items if _retirer(i, zone)]
    for i in retires:
        _resynchroniser(i)
    return {"retires": len(retires), "zones": _zones()}


@frappe.whitelist()
def definir_zones(item, zones):
    """Depuis l'article : fixe exactement ses zones (une, plusieurs ou aucune), dans l'ordre donné."""
    _ecriture()
    item = _liste([item])[0]
    zones = frappe.parse_json(zones) if isinstance(zones, str) else (zones or [])
    zones = list(dict.fromkeys(z for z in zones if z))
    for z in zones:
        _zone_existante(z)
    frappe.db.delete(DOCTYPE_LIEN, {"parent": item, "parenttype": "Item"})
    for z in zones:
        _ajouter(item, z)
    _resynchroniser(item)
    return {"zones": zones_de(item), "toutes": _zones()}


@frappe.whitelist()
def definir_sortie(items, entrepot):
    """Fixe la sortie principale (entrepôt par défaut de la société) des articles."""
    _ecriture()
    items = _liste(items)
    societe = _societe()
    if entrepot not in {e.name for e in entrepots_de_sortie(societe)}:
        frappe.throw(_("Sortie principale : Magasin ou Hall seulement (« {0} » refusé ou désactivé).").format(entrepot))
    for item in items:
        ligne = frappe.db.get_value("Item Default", {"parent": item, "parenttype": "Item", "company": societe}, "name")
        if ligne:
            frappe.db.set_value("Item Default", ligne, "default_warehouse", entrepot)
        else:
            idx = cint(frappe.db.sql("select max(idx) from `tabItem Default` where parent=%s", item)[0][0]) + 1
            frappe.get_doc({"doctype": "Item Default", "parent": item, "parenttype": "Item", "parentfield": "item_defaults",
                            "idx": idx, "company": societe, "default_warehouse": entrepot}).db_insert()
        frappe.db.set_value("Item", item, "modified", frappe.utils.now(), update_modified=False)
        frappe.clear_document_cache("Item", item)
    return {"modifies": len(items)}
