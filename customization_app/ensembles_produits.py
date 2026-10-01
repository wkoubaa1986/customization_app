"""Ensembles de produits (Product Bundle) — page Stock « ensembles-produits » (demande du 01/10/2026).

Voir tous les ensembles d'un coup d'œil (photo, prix de vente, ventes de l'année, composants avec leur stock
au Magasin, exemplaires assemblables), les retrouver par nom, code, groupe ou composant, et les gérer sans
ouvrir les fiches : composants et quantités, activer / désactiver, dupliquer, créer (avec l'article parent
non stocké et son prix si besoin). Pensé pour le téléphone comme « Stock par entrepôt ».

Règles ERPNext respectées : l'article parent n'est pas suivi en stock (c'est le Product Bundle qui éclate
la vente en composants) ; un composant ne peut pas être lui-même un ensemble.
"""

from __future__ import annotations

import math

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from customization_app.stock_entrepots import magasin
from customization_app.zones_magasin import _societe

PB = "Product Bundle"
LISTE_PRIX = "Vente standard"
PAGE = 48
LIMITE_RECHERCHE = 25


def _lecture():
    frappe.has_permission(PB, "read", throw=True)


def _ecriture():
    if not frappe.has_permission(PB, "write"):
        frappe.throw(_("Modifier les ensembles de produits n'est pas ouvert à votre compte."), frappe.PermissionError)


def assemblables(composants: list[dict]) -> int | None:
    """Combien d'exemplaires le Magasin peut assembler : min(stock // qté) sur les composants suivis en stock.
    None si aucun composant n'est suivi. PURE. composants : [{qty, stock, is_stock_item}]."""
    bornes = []
    for c in composants:
        if not c.get("is_stock_item") or flt(c.get("qty")) <= 0:
            continue
        bornes.append(int(math.floor(max(flt(c.get("stock")), 0) / flt(c["qty"]))))
    return min(bornes) if bornes else None


def _prix(codes: list[str]) -> dict:
    if not codes:
        return {}
    return dict(frappe.db.sql("""select item_code, price_list_rate from `tabItem Price`
                                 where price_list = %s and item_code in %s and ifnull(customer, '') = ''""",
                              (LISTE_PRIX, codes)))


def _ventes(codes: list[str], depuis) -> dict:
    if not codes:
        return {}
    return dict(frappe.db.sql("""select soi.item_code, count(distinct soi.parent) from `tabSales Order Item` soi
                                 join `tabSales Order` so on so.name = soi.parent
                                 where so.docstatus = 1 and so.transaction_date >= %s and soi.item_code in %s
                                 group by soi.item_code""", (depuis, codes)))


def _stocks(codes: list[str], entrepot: str) -> dict:
    if not codes:
        return {}
    return dict(frappe.db.sql("select item_code, actual_qty from tabBin where warehouse = %s and item_code in %s",
                              (entrepot, codes)))


@frappe.whitelist()
def get_context():
    _lecture()
    groupes = frappe.db.sql_list("""select distinct i.item_group from `tabProduct Bundle` pb join tabItem i on i.name = pb.new_item_code
                                    order by i.item_group""")
    tous = frappe.db.sql_list("select name from `tabItem Group` where is_group = 0 order by name")
    return {"groupes": groupes, "groupes_articles": tous, "magasin": magasin(), "peut_modifier": bool(frappe.has_permission(PB, "write")),
            "total": frappe.db.count(PB), "desactives": frappe.db.count(PB, {"disabled": 1}),
            "annee": getdate(nowdate()).year}


@frappe.whitelist()
def get_ensembles(recherche=None, groupe=None, composant=None, etat="actifs", start=0, limite=PAGE):
    """Une page d'ensembles avec composants, stock au Magasin, assemblables, prix et ventes de l'année."""
    _lecture()
    conds, valeurs = [], {}
    if etat == "actifs":
        conds.append("pb.disabled = 0")
    elif etat == "desactives":
        conds.append("pb.disabled = 1")
    if groupe:
        conds.append("i.item_group = %(groupe)s")
        valeurs["groupe"] = groupe
    for n, mot in enumerate((recherche or "").split()):
        valeurs[f"m{n}"] = f"%{mot}%"
        conds.append(f"(pb.new_item_code like %(m{n})s or i.item_name like %(m{n})s or pb.description like %(m{n})s)")
    if composant:
        conds.append("exists (select 1 from `tabProduct Bundle Item` c where c.parent = pb.name and c.item_code = %(composant)s)")
        valeurs["composant"] = composant
    where = (" where " + " and ".join(conds)) if conds else ""
    total = frappe.db.sql(f"select count(*) from `tabProduct Bundle` pb join tabItem i on i.name = pb.new_item_code{where}", valeurs)[0][0]
    valeurs.update({"start": cint(start), "page": min(cint(limite) or PAGE, 200)})
    rows = frappe.db.sql(f"""select pb.name, pb.new_item_code as item_code, pb.disabled, pb.description, i.item_name, i.item_group, i.image,
                                    i.disabled as article_desactive
                             from `tabProduct Bundle` pb join tabItem i on i.name = pb.new_item_code{where}
                             order by pb.disabled, i.item_group, i.item_name limit %(page)s offset %(start)s""", valeurs, as_dict=True)
    noms = [r.name for r in rows]
    comps = frappe.db.sql("""select c.parent, c.item_code, c.qty, c.uom, i.item_name, i.image, i.is_stock_item, i.disabled
                             from `tabProduct Bundle Item` c join tabItem i on i.name = c.item_code
                             where c.parent in %s order by c.idx""", [noms or [""]], as_dict=True)
    codes_c = list({c.item_code for c in comps})
    stock = _stocks(codes_c, magasin())
    prix = _prix([r.item_code for r in rows])
    ventes = _ventes([r.item_code for r in rows], "%s-01-01" % getdate(nowdate()).year)
    par = {}
    for c in comps:
        c["stock"] = flt(stock.get(c.item_code), 6)
        par.setdefault(c.parent, []).append(c)
    for r in rows:
        r["composants"] = [{"item_code": c.item_code, "item_name": c.item_name, "qty": flt(c.qty, 6), "uom": c.uom, "image": c.image,
                            "stock": c["stock"], "is_stock_item": cint(c.is_stock_item), "disabled": cint(c.disabled)} for c in par.get(r.name, [])]
        r["assemblables"] = assemblables(r["composants"])
        r["prix"] = flt(prix.get(r.item_code), 3) if prix.get(r.item_code) is not None else None
        r["ventes"] = cint(ventes.get(r.item_code))
    fin = cint(start) + len(rows)
    return {"ensembles": rows, "total": total, "suivant": fin if fin < total else None}


@frappe.whitelist()
def rechercher_composants(txt, exclure=None):
    """Articles pouvant entrer dans un ensemble (actifs, pas eux-mêmes des ensembles), avec le stock au Magasin."""
    _lecture()
    txt = (txt or "").strip()
    if len(txt) < 2:
        return []
    valeurs = {"t": txt, "p": f"{txt}%", "n": LIMITE_RECHERCHE}
    conds = []
    for n, mot in enumerate(txt.split()):
        valeurs[f"m{n}"] = f"%{mot}%"
        conds.append(f"(i.name like %(m{n})s or i.item_name like %(m{n})s)")
    rows = frappe.db.sql(f"""select i.name as item_code, i.item_name, i.image, i.stock_uom as uom, i.is_stock_item
                             from tabItem i where i.disabled = 0 and i.has_variants = 0 and {" and ".join(conds)}
                               and not exists (select 1 from `tabProduct Bundle` pb where pb.new_item_code = i.name)
                             order by (i.name = %(t)s) desc, (i.name like %(p)s) desc, i.is_stock_item desc, length(i.name), i.name
                             limit %(n)s""", valeurs, as_dict=True)
    stock = _stocks([r.item_code for r in rows], magasin())
    for r in rows:
        r["stock"] = flt(stock.get(r.item_code), 6)
    return rows


@frappe.whitelist()
def rechercher_parents(txt):
    """Articles non stockés, actifs, sans ensemble : candidats à devenir un nouvel ensemble."""
    _lecture()
    txt = (txt or "").strip()
    if len(txt) < 2:
        return []
    valeurs = {"n": LIMITE_RECHERCHE}
    conds = []
    for n, mot in enumerate(txt.split()):
        valeurs[f"m{n}"] = f"%{mot}%"
        conds.append(f"(i.name like %(m{n})s or i.item_name like %(m{n})s)")
    return frappe.db.sql(f"""select i.name as item_code, i.item_name, i.image, i.item_group from tabItem i
                             where i.disabled = 0 and i.is_stock_item = 0 and i.has_variants = 0 and {" and ".join(conds)}
                               and not exists (select 1 from `tabProduct Bundle` pb where pb.new_item_code = i.name)
                             order by i.item_name limit %(n)s""", valeurs, as_dict=True)


def _lignes(lignes) -> list[dict]:
    brut = frappe.parse_json(lignes) if isinstance(lignes, str) else (lignes or [])
    cumul = {}
    for l in brut:
        code, q = l.get("item_code"), flt(l.get("qty"), 6)
        if code and q > 0:
            cumul[code] = flt(cumul.get(code, 0) + q, 6)
    if not cumul:
        frappe.throw(_("Un ensemble a au moins un composant avec une quantité."))
    for code in cumul:
        if frappe.db.exists(PB, {"new_item_code": code}):
            frappe.throw(_("{0} est lui-même un ensemble : il ne peut pas être composant.").format(code))
        if not frappe.db.exists("Item", code):
            frappe.throw(_("Article inconnu : {0}").format(code))
    return [{"item_code": c, "qty": q, "uom": frappe.db.get_value("Item", c, "stock_uom"),
             "description": frappe.db.get_value("Item", c, "description") or frappe.db.get_value("Item", c, "item_name")}
            for c, q in cumul.items()]


@frappe.whitelist(methods=["POST"])
def enregistrer_ensemble(name, lignes, description=None):
    """Remplace les composants de l'ensemble (et sa description)."""
    _ecriture()
    doc = frappe.get_doc(PB, name)
    doc.set("items", [])
    for l in _lignes(lignes):
        doc.append("items", l)
    if description is not None:
        doc.description = (description or "")[:140] or None
    doc.save()
    return doc.name


def _taxes_de_reference(modele: str | None, groupe: str) -> list[dict]:
    """Les lignes « Item Tax » à recopier sur un nouvel article parent."""
    candidats = [modele] if modele else []
    candidats += frappe.db.sql_list("""select pb.new_item_code from `tabProduct Bundle` pb join tabItem i on i.name = pb.new_item_code
                                       where i.item_group = %s and pb.disabled = 0 order by pb.modified desc limit 1""", groupe)
    candidats += frappe.db.sql_list("select new_item_code from `tabProduct Bundle` where disabled = 0 order by modified desc limit 1")
    for c in candidats:
        if not c:
            continue
        rows = frappe.get_all("Item Tax", filters={"parent": c, "parenttype": "Item"}, fields=["item_tax_template", "tax_category"])
        if rows:
            return [{"item_tax_template": r.item_tax_template, "tax_category": r.tax_category} for r in rows]
    return []


@frappe.whitelist(methods=["POST"])
def creer_ensemble(lignes, parent=None, nouveau=None, description=None):
    """Crée un ensemble sur un article parent existant (non stocké, sans ensemble) ou sur un article créé ici :
    nouveau = {item_code, item_name, item_group, prix} → Item non stocké, vendable, + prix de vente standard."""
    _ecriture()
    nouveau = frappe.parse_json(nouveau) if isinstance(nouveau, str) else nouveau
    if not parent and not nouveau:
        frappe.throw(_("Choisissez un article parent ou décrivez le nouvel article."))
    if nouveau:
        code = (nouveau.get("item_code") or "").strip()
        nom = (nouveau.get("item_name") or "").strip()
        if not code or not nom or not nouveau.get("item_group"):
            frappe.throw(_("Code, désignation et groupe sont obligatoires pour le nouvel article."))
        if frappe.db.exists("Item", code):
            frappe.throw(_("L'article {0} existe déjà : choisissez-le comme parent existant.").format(code))
        item = frappe.get_doc({"doctype": "Item", "item_code": code, "item_name": nom, "item_group": nouveau["item_group"],
                               "is_stock_item": 0, "is_sales_item": 1, "include_item_in_manufacturing": 0,
                               "stock_uom": nouveau.get("uom") or "Pièce", "description": nom})
        # La table « taxes » est obligatoire sur ce site : on recopie celle d'un ensemble de référence
        # (celui qu'on duplique, sinon un ensemble du même groupe, sinon n'importe lequel).
        for t in _taxes_de_reference(nouveau.get("modele"), nouveau["item_group"]):
            item.append("taxes", t)
        item.insert()
        parent = item.name
        if flt(nouveau.get("prix")) > 0:
            frappe.get_doc({"doctype": "Item Price", "item_code": parent, "price_list": LISTE_PRIX,
                            "price_list_rate": flt(nouveau["prix"], 3), "selling": 1}).insert()
    else:
        it = frappe.db.get_value("Item", parent, ["is_stock_item", "disabled"], as_dict=True)
        if not it:
            frappe.throw(_("Article parent inconnu : {0}").format(parent))
        if it.is_stock_item:
            frappe.throw(_("{0} est suivi en stock : un ensemble est un article NON stocké (ses composants le sont).").format(parent))
        if frappe.db.exists(PB, {"new_item_code": parent}):
            frappe.throw(_("{0} a déjà un ensemble.").format(parent))
    doc = frappe.get_doc({"doctype": PB, "new_item_code": parent, "description": (description or "")[:140] or None})
    for l in _lignes(lignes):
        doc.append("items", l)
    doc.insert()
    return doc.name


@frappe.whitelist(methods=["POST"])
def activer_ensemble(name, actif=1):
    _ecriture()
    frappe.db.set_value(PB, name, "disabled", 0 if cint(actif) else 1)
    return True


@frappe.whitelist(methods=["POST"])
def definir_prix(item_code, prix):
    """Prix de vente standard de l'article parent (créé s'il manque)."""
    _ecriture()
    nom = frappe.db.get_value("Item Price", {"item_code": item_code, "price_list": LISTE_PRIX, "customer": ["is", "not set"]}, "name")
    if nom:
        frappe.db.set_value("Item Price", nom, "price_list_rate", flt(prix, 3))
    else:
        frappe.get_doc({"doctype": "Item Price", "item_code": item_code, "price_list": LISTE_PRIX, "price_list_rate": flt(prix, 3),
                        "selling": 1}).insert()
    return flt(prix, 3)


@frappe.whitelist(methods=["POST"])
def supprimer_ensemble(name):
    """Supprime la définition de l'ensemble (l'article parent reste). Refusé s'il a été vendu : désactiver plutôt."""
    _ecriture()
    code = frappe.db.get_value(PB, name, "new_item_code")
    if frappe.db.exists("Sales Order Item", {"item_code": code}) or frappe.db.exists("Sales Invoice Item", {"item_code": code}):
        frappe.throw(_("{0} a déjà été vendu : désactivez l'ensemble plutôt que de le supprimer.").format(code))
    frappe.delete_doc(PB, name)
    return True
