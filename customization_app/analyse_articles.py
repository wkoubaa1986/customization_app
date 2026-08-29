"""
Backend de l'outil « Analyse Articles » (page Desk analyse-articles).

Analyse prix/marges des articles actifs : valorisation HT (moyenne pondérée du
stock, repli sur Item.valuation_rate), dernière TVA applicable (Item Tax
Template, repli groupe d'article et ancêtres), prix de vente TTC par liste de
prix de vente active, marge TTC par liste. Pour les Product Bundle : coût de
revient calculé depuis les composants et prix « calculé » par liste (somme des
prix des composants), côte à côte avec le prix propre du bundle.

Convention métier : les Item Price de vente sont stockés TTC.
Marge % = (PV TTC − valorisation TTC) / PV TTC × 100.

Réservé à un seul utilisateur. Le calcul d'une ligne passe par build_item_row()
— point d'entrée unique pour les futures règles/actions.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

ALLOWED_USER = "koubaawassim@gmail.com"
PAGE_LENGTH = 50
SORTABLE_FIELDS = {"item_code": "name", "item_name": "item_name",
                   "item_group": "item_group"}


def _guard():
    if frappe.session.user != ALLOWED_USER:
        frappe.throw(_("Accès réservé."), frappe.PermissionError)


# ---------------------------------------------------------------- fetchers bulk

def _selling_price_lists():
    return frappe.get_all("Price List", filters={"selling": 1, "enabled": 1},
                          fields=["name", "currency"], order_by="name")


def _fetch_items(search=None, item_group=None, start=0, page_length=None,
                 order_by="item_code", order_dir="asc"):
    filters = {"disabled": 0}
    if item_group:
        # groupe parent → inclure tous ses sous-groupes (arbre nested set)
        lft, rgt = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"]) or (None, None)
        if lft is not None:
            groupes = frappe.get_all("Item Group", pluck="name",
                                     filters={"lft": [">=", lft], "rgt": ["<=", rgt]})
            filters["item_group"] = ["in", groupes]
        else:
            filters["item_group"] = item_group
    or_filters = None
    if search:
        like = f"%{search}%"
        or_filters = [["Item", "name", "like", like],
                      ["Item", "item_name", "like", like],
                      ["Item", "description", "like", like]]

    field = SORTABLE_FIELDS.get(order_by, "name")
    direction = "desc" if str(order_dir).lower() == "desc" else "asc"
    kwargs = dict(filters=filters, or_filters=or_filters)
    total = len(frappe.get_all("Item", pluck="name", **kwargs))
    items = frappe.get_all(
        "Item",
        fields=["name", "item_name", "item_group", "description", "image",
                "valuation_rate", "stock_uom", "is_stock_item"],
        order_by=f"`tabItem`.`{field}` {direction}",
        limit_start=int(start or 0),
        limit_page_length=page_length or 0,
        **kwargs,
    )
    return items, total


def _bundle_map(item_codes):
    """{code parent: [{item_code, qty}, ...]} pour les bundles actifs."""
    if not item_codes:
        return {}
    bundles = frappe.get_all("Product Bundle",
                             filters={"new_item_code": ["in", item_codes], "disabled": 0},
                             fields=["name", "new_item_code"])
    if not bundles:
        return {}
    parent_of = {b.name: b.new_item_code for b in bundles}
    rows = frappe.get_all("Product Bundle Item",
                          filters={"parent": ["in", list(parent_of)]},
                          fields=["parent", "item_code", "qty"],
                          order_by="parent, idx")
    out = {}
    for r in rows:
        out.setdefault(parent_of[r.parent], []).append(
            {"item_code": r.item_code, "qty": flt(r.qty)})
    return out


def _valuation_map(item_codes, item_fallback):
    """{code: valorisation HT} — moyenne pondérée tabBin (qté>0), repli
    Item.valuation_rate (item_fallback: {code: valuation_rate}), puis
    valorisation du dernier mouvement de stock (article sans stock ni taux
    sur la fiche : on garde la dernière valorisation connue)."""
    out = {}
    if item_codes:
        for code, rate in frappe.db.sql(
                """SELECT item_code,
                          SUM(actual_qty * valuation_rate) / SUM(actual_qty)
                   FROM `tabBin`
                   WHERE actual_qty > 0 AND item_code IN %(codes)s
                   GROUP BY item_code""",
                {"codes": item_codes}):
            out[code] = flt(rate)
    for code in item_codes or []:
        if not out.get(code):
            out[code] = flt(item_fallback.get(code))

    missing = [c for c in item_codes or [] if not out.get(c)]
    if missing:
        for code, rate in frappe.db.sql(
                """SELECT item_code, valuation_rate FROM (
                       SELECT item_code, valuation_rate,
                              ROW_NUMBER() OVER (
                                  PARTITION BY item_code
                                  ORDER BY posting_datetime DESC, creation DESC
                              ) AS rn
                       FROM `tabStock Ledger Entry`
                       WHERE item_code IN %(codes)s
                         AND is_cancelled = 0
                         AND valuation_rate > 0
                   ) dernier WHERE rn = 1""",
                {"codes": missing}):
            out[code] = flt(rate)
    return out


def _group_ancestry():
    """{groupe: [groupe, parent, grand-parent, ...]} (racine exclue en pratique
    car sans taxes la remontée s'arrête faute de lignes)."""
    rows = frappe.get_all("Item Group", fields=["name", "parent_item_group"])
    parent = {r.name: r.parent_item_group for r in rows}
    chains = {}
    for g in parent:
        chain, cur, seen = [], g, set()
        while cur and cur not in seen:
            chain.append(cur)
            seen.add(cur)
            cur = parent.get(cur)
        chains[g] = chain
    return chains


def _tax_rate_map(item_codes, item_groups):
    """{code: {"tva": float|None, "template": str|None}}.
    Réplique bulk de _get_item_tax_template : lignes Item Tax de l'article
    (valid_from <= aujourd'hui ou vide, tri valid_from desc), sinon repli sur
    le groupe d'article puis ses ancêtres. Taux = max des tax_rate du template."""
    chains = _group_ancestry()
    group_parents = set()
    for g in item_groups:
        group_parents.update(chains.get(g, [g]))

    parents = list(item_codes) + list(group_parents)
    if not parents:
        return {}
    taxes = frappe.get_all(
        "Item Tax",
        filters={"parenttype": ["in", ["Item", "Item Group"]],
                 "parent": ["in", parents],
                 "parentfield": "taxes"},
        fields=["parent", "parenttype", "item_tax_template", "valid_from"],
    )
    today = getdate(nowdate())
    by_parent = {}
    for t in taxes:
        by_parent.setdefault((t.parenttype, t.parent), []).append(t)

    def pick(parenttype, parent):
        rows = by_parent.get((parenttype, parent)) or []
        valid = [r for r in rows if not r.valid_from or getdate(r.valid_from) <= today]
        if not valid:
            return None
        valid.sort(key=lambda r: getdate(r.valid_from) if r.valid_from else getdate("1900-01-01"),
                   reverse=True)
        return valid[0].item_tax_template

    templates = set()
    chosen = {}
    item_group_of = dict(zip(item_codes, item_groups))
    for code in item_codes:
        tpl = pick("Item", code)
        if not tpl:
            for g in chains.get(item_group_of.get(code), []):
                tpl = pick("Item Group", g)
                if tpl:
                    break
        chosen[code] = tpl
        if tpl:
            templates.add(tpl)

    rate_of = {}
    if templates:
        for d in frappe.get_all("Item Tax Template Detail",
                                filters={"parent": ["in", list(templates)]},
                                fields=["parent", "tax_rate"]):
            rate_of[d.parent] = max(rate_of.get(d.parent, 0.0), flt(d.tax_rate))

    return {code: {"tva": rate_of.get(tpl) if tpl else None, "template": tpl}
            for code, tpl in chosen.items()}


def _price_map(item_codes, price_list_names):
    """{(item_code, price_list): rate TTC} — dernier prix valide par couple
    (valid_from <= aujourd'hui ou vide ; valid_upto >= aujourd'hui ou vide),
    tri valid_from desc, creation desc."""
    if not (item_codes and price_list_names):
        return {}
    rows = frappe.get_all(
        "Item Price",
        filters={"selling": 1,
                 "item_code": ["in", item_codes],
                 "price_list": ["in", price_list_names]},
        fields=["item_code", "price_list", "price_list_rate",
                "valid_from", "valid_upto", "creation"],
        order_by="valid_from desc, creation desc",
    )
    today = getdate(nowdate())
    out = {}
    for r in rows:
        if r.valid_from and getdate(r.valid_from) > today:
            continue
        if r.valid_upto and getdate(r.valid_upto) < today:
            continue
        out.setdefault((r.item_code, r.price_list), flt(r.price_list_rate))
    return out


# ---------------------------------------------------------------- calcul lignes

def _marge(pv_ttc, cout_ttc):
    if pv_ttc is None or flt(pv_ttc) <= 0:
        return None
    return round((flt(pv_ttc) - flt(cout_ttc)) / flt(pv_ttc) * 100, 2)


def _val_ttc(val_ht, tva):
    return round(flt(val_ht) * (1 + flt(tva) / 100), 3)


def build_item_row(item, ctx):
    """Construit la ligne d'analyse d'un article. ctx : dict avec
    price_lists, valuation, taxes, prices, bundles, item_info."""
    code = item["name"]
    tva = (ctx["taxes"].get(code) or {}).get("tva")
    val_ht = flt(ctx["valuation"].get(code))
    val_ttc = _val_ttc(val_ht, tva or 0)

    row = {
        "item_code": code,
        "item_name": item.get("item_name"),
        "item_group": item.get("item_group"),
        "description": item.get("description"),
        "image": item.get("image"),
        "val_ht": round(val_ht, 3),
        "tva": tva,
        "val_ttc": val_ttc,
        "is_bundle": code in ctx["bundles"],
        "prices": {},
    }

    components = ctx["bundles"].get(code)
    if components:
        cout_ht = cout_ttc = 0.0
        comp_rows = []
        for c in components:
            c_code, qty = c["item_code"], flt(c["qty"])
            c_tva = (ctx["taxes"].get(c_code) or {}).get("tva")
            c_ht = flt(ctx["valuation"].get(c_code))
            c_ttc = _val_ttc(c_ht, c_tva or 0)
            cout_ht += c_ht * qty
            cout_ttc += c_ttc * qty
            info = ctx["item_info"].get(c_code, {})
            comp_rows.append({
                "item_code": c_code,
                "item_name": info.get("item_name"),
                "qty": qty,
                "val_ht": round(c_ht, 3),
                "tva": c_tva,
                "val_ttc": c_ttc,
                "prices": {pl["name"]: ctx["prices"].get((c_code, pl["name"]))
                           for pl in ctx["price_lists"]},
            })
        bundle = {"cout_ht": round(cout_ht, 3), "cout_ttc": round(cout_ttc, 3),
                  "prices": {}, "components": comp_rows}
        for pl in ctx["price_lists"]:
            pl_name = pl["name"]
            own = ctx["prices"].get((code, pl_name))
            calc, complete = 0.0, True
            for c in components:
                p = ctx["prices"].get((c["item_code"], pl_name))
                if p is None:
                    complete = False
                    break
                calc += flt(p) * flt(c["qty"])
            calc_ttc = round(calc, 3) if complete else None
            bundle["prices"][pl_name] = {
                "own_ttc": own,
                "own_marge": _marge(own, cout_ttc),
                "calc_ttc": calc_ttc,
                "calc_marge": _marge(calc_ttc, cout_ttc),
            }
        row["bundle"] = bundle
        # sur la ligne principale : la marge du bundle s'appuie sur son coût calculé
        for pl in ctx["price_lists"]:
            own = ctx["prices"].get((code, pl["name"]))
            row["prices"][pl["name"]] = {"pv_ttc": own, "marge": _marge(own, cout_ttc)}
    else:
        for pl in ctx["price_lists"]:
            pv = ctx["prices"].get((code, pl["name"]))
            row["prices"][pl["name"]] = {"pv_ttc": pv, "marge": _marge(pv, val_ttc)}
    return row


def _filtre_marge(rows, marge):
    """Filtre les lignes d'analyse : « lt:X » = au moins une liste de prix avec
    une marge sous X % ; « sans_prix » = aucun prix de vente nulle part."""
    if not marge:
        return rows
    if marge == "sans_prix":
        return [r for r in rows
                if all(p.get("pv_ttc") is None for p in r["prices"].values())]
    if marge.startswith("lt:"):
        seuil = flt(marge.split(":", 1)[1])
        return [r for r in rows
                if any(p.get("marge") is not None and flt(p["marge"]) < seuil
                       for p in r["prices"].values())]
    return rows


def compute_analysis(search=None, item_group=None, start=None, page_length=None,
                     order_by="item_code", order_dir="asc", marge=None):
    if marge:
        # la marge est une valeur calculée : on construit tout, on filtre,
        # puis on pagine en mémoire
        complet = compute_analysis(search, item_group, None, None,
                                   order_by, order_dir)
        rows = _filtre_marge(complet["rows"], marge)
        debut = int(start or 0)
        fin = debut + page_length if page_length else None
        return {"total": len(rows), "start": debut, "page_length": page_length,
                "price_lists": complet["price_lists"], "rows": rows[debut:fin]}

    items, total = _fetch_items(search, item_group, start or 0, page_length,
                                order_by, order_dir)
    price_lists = _selling_price_lists()
    page_codes = [i.name for i in items]

    bundles = _bundle_map(page_codes)
    component_codes = {c["item_code"] for comps in bundles.values() for c in comps}
    extra_codes = sorted(component_codes - set(page_codes))

    item_info = {i.name: i for i in items}
    if extra_codes:
        for i in frappe.get_all("Item", filters={"name": ["in", extra_codes]},
                                fields=["name", "item_name", "item_group",
                                        "valuation_rate"]):
            item_info[i.name] = i

    needed = page_codes + extra_codes
    valuation = _valuation_map(needed, {c: item_info[c].get("valuation_rate")
                                        for c in needed if c in item_info})
    taxes = _tax_rate_map(needed, [item_info.get(c, {}).get("item_group") for c in needed])
    prices = _price_map(needed, [pl["name"] for pl in price_lists])

    ctx = {"price_lists": price_lists, "valuation": valuation, "taxes": taxes,
           "prices": prices, "bundles": bundles, "item_info": item_info}
    return {
        "total": total,
        "start": int(start or 0),
        "page_length": page_length,
        "price_lists": price_lists,
        "rows": [build_item_row(i, ctx) for i in items],
    }


# ---------------------------------------------------------------- API

@frappe.whitelist()
def get_filters():
    _guard()
    groupes = frappe.get_all("Item Group",
                             fields=["name", "parent_item_group", "is_group"],
                             order_by="lft")
    parent = {g.name: g.parent_item_group for g in groupes}
    out = []
    for g in groupes:
        depth, cur = 0, parent.get(g.name)
        while cur:
            depth += 1
            cur = parent.get(cur)
        out.append({"name": g.name, "is_group": g.is_group, "depth": depth,
                    "label": (" " * depth) + g.name})
    return {
        "item_groups": out,
        "price_lists": _selling_price_lists(),
    }


@frappe.whitelist()
def get_analysis(search=None, item_group=None, start=0, page_length=PAGE_LENGTH,
                 order_by="item_code", order_dir="asc", marge=None):
    _guard()
    return compute_analysis(search=search or None, item_group=item_group or None,
                            start=frappe.utils.cint(start),
                            page_length=frappe.utils.cint(page_length) or PAGE_LENGTH,
                            order_by=order_by, order_dir=order_dir,
                            marge=marge or None)


@frappe.whitelist()
def download_excel(search=None, item_group=None, marge=None):
    from frappe.utils.xlsxutils import make_xlsx

    _guard()
    data = compute_analysis(search=search or None, item_group=item_group or None,
                            marge=marge or None)
    pls = [pl["name"] for pl in data["price_lists"]]

    header = ["Code article", "Désignation", "Groupe", "Bundle",
              "Valorisation HT", "TVA %", "Valorisation TTC"]
    for pl in pls:
        header += [f"{pl} — PV TTC", f"{pl} — Marge %",
                   f"{pl} — PV calculé TTC", f"{pl} — Marge calc. %"]
    rows = [[f"Analyse Articles — {nowdate()}"], [], header]

    def fmt(v):
        return "" if v is None else v

    for r in data["rows"]:
        b = r.get("bundle")
        line = [r["item_code"], r["item_name"] or "", r["item_group"] or "",
                "Oui" if r["is_bundle"] else "Non",
                b["cout_ht"] if b else r["val_ht"],
                fmt(r["tva"]),
                b["cout_ttc"] if b else r["val_ttc"]]
        for pl in pls:
            p = r["prices"].get(pl) or {}
            line += [fmt(p.get("pv_ttc")), fmt(p.get("marge"))]
            if b:
                bp = b["prices"].get(pl) or {}
                line += [fmt(bp.get("calc_ttc")), fmt(bp.get("calc_marge"))]
            else:
                line += ["", ""]
        rows.append(line)

    xlsx = make_xlsx(rows, "Analyse Articles")
    frappe.response["filename"] = f"analyse-articles-{nowdate()}.xlsx"
    frappe.response["filecontent"] = xlsx.getvalue()
    frappe.response["type"] = "binary"


# ---------------------------------------------------------------- changement de prix
#
# Décisions (29/08/2026) :
# - On ne MODIFIE jamais un Item Price existant : on le CLÔT (valid_upto = hier)
#   et on en CRÉE un nouveau (valid_from = aujourd'hui). La résolution de prix
#   d'ERPNext filtrant par date de transaction, toute commande datée d'avant —
#   soumise OU brouillon — retombe sur l'ancien tarif même si elle recharge ses
#   prix. Les commandes soumises gardent de toute façon leurs taux enregistrés.
# - Exception : une ligne de prix créée AUJOURD'HUI ne peut pas être close hier ;
#   son taux est alors modifié sur place (changement du jour même, assumé).
# - Cascade bundles : tout Product Bundle ACTIF contenant un article modifié
#   voit son prix propre remplacé par le PRIX CALCULÉ (somme des composants au
#   nouveau tarif) — sauf si un composant n'a pas de prix (bundle signalé
#   « incomplet », non touché).

def _bundles_contenant(item_codes):
    """{code_bundle: [{item_code, qty}, …] (composition COMPLÈTE)} pour les
    bundles actifs dont au moins un composant figure dans item_codes."""
    if not item_codes:
        return {}
    parents = frappe.get_all("Product Bundle Item",
                             filters={"item_code": ["in", item_codes]},
                             pluck="parent", distinct=True)
    if not parents:
        return {}
    bundles = frappe.get_all("Product Bundle",
                             filters={"name": ["in", parents], "disabled": 0},
                             fields=["name", "new_item_code"])
    if not bundles:
        return {}
    parent_of = {b.name: b.new_item_code for b in bundles}
    rows = frappe.get_all("Product Bundle Item",
                          filters={"parent": ["in", list(parent_of)]},
                          fields=["parent", "item_code", "qty"],
                          order_by="parent, idx")
    out = {}
    for r in rows:
        out.setdefault(parent_of[r.parent], []).append(
            {"item_code": r.item_code, "qty": flt(r.qty)})
    return out


def _resoudre_changements(cible):
    """-> [{item_code, item_name, price_list, ancien, nouveau}] depuis la cible :
    - mode « liste »  : changements explicites [{item_code, price_list, nouveau}] ;
    - mode « bloc »   : les articles COCHÉS (item_codes) ou, sans sélection,
      tout le filtre courant (search/groupe) ; une ou plusieurs listes de prix
      (price_lists, ou price_list seule), une opération (« pct » ±x %,
      « montant » ±x, « fixe » = x)."""
    cible = frappe.parse_json(cible) if isinstance(cible, str) else cible
    noms_dict = {}

    if cible.get("mode") == "bloc":
        pls = cible.get("price_lists") or (
            [cible["price_list"]] if cible.get("price_list") else [])
        operation, valeur = cible.get("operation"), flt(cible.get("valeur"))
        if not pls or operation not in ("pct", "montant", "fixe"):
            frappe.throw(_("Liste de prix et opération requises."))
        selection = cible.get("item_codes") or []
        if selection:
            items = frappe.get_all("Item", filters={"name": ["in", selection]},
                                   fields=["name", "item_name"])
        elif cible.get("marge"):
            # même périmètre que l'affichage : le filtre marge est calculé
            complet = compute_analysis(search=cible.get("search") or None,
                                       item_group=cible.get("item_group") or None)
            items = [frappe._dict(name=r["item_code"], item_name=r["item_name"])
                     for r in _filtre_marge(complet["rows"], cible["marge"])]
        else:
            items, _total = _fetch_items(search=cible.get("search") or None,
                                         item_group=cible.get("item_group") or None,
                                         page_length=0)
        codes = [i.name for i in items]
        noms_dict = {i.name: i.item_name for i in items}
        prix = _price_map(codes, pls)
        changements = []
        for pl in pls:
            for code in codes:
                ancien = prix.get((code, pl))
                if operation == "fixe":
                    nouveau = valeur
                elif ancien is None:
                    continue        # pas de prix à faire varier
                elif operation == "pct":
                    nouveau = round(flt(ancien) * (1 + valeur / 100), 3)
                else:
                    nouveau = round(flt(ancien) + valeur, 3)
                if nouveau < 0:
                    nouveau = 0.0
                changements.append({"item_code": code, "price_list": pl,
                                    "ancien": ancien, "nouveau": nouveau})
    else:
        changements = []
        bruts = cible.get("changements") or []
        codes = [c.get("item_code") for c in bruts]
        listes = list({c.get("price_list") for c in bruts})
        prix = _price_map(codes, listes)
        for c in bruts:
            changements.append({
                "item_code": c["item_code"], "price_list": c["price_list"],
                "ancien": prix.get((c["item_code"], c["price_list"])),
                "nouveau": flt(c["nouveau"]),
            })
        noms_dict = dict(frappe.get_all("Item", filters={"name": ["in", codes]},
                                        fields=["name", "item_name"], as_list=True))

    for c in changements:
        c["item_name"] = noms_dict.get(c["item_code"]) or c["item_code"]
    # Ne garder que les vrais changements
    return [c for c in changements
            if c["ancien"] is None or round(flt(c["ancien"]), 3) != round(c["nouveau"], 3)]


def _cascade_bundles(changements):
    """-> [{bundle, item_name, price_list, ancien, nouveau, incomplet}] : le
    nouveau prix propre (= prix calculé) des bundles actifs touchés."""
    par_liste = {}
    for c in changements:
        par_liste.setdefault(c["price_list"], {})[c["item_code"]] = c["nouveau"]

    resultat = []
    codes_changes = list({c["item_code"] for c in changements})
    bundles = _bundles_contenant(codes_changes)
    if not bundles:
        return resultat
    tous_composants = list({comp["item_code"] for comps in bundles.values() for comp in comps})
    noms = dict(frappe.get_all("Item", filters={"name": ["in", list(bundles)]},
                               fields=["name", "item_name"], as_list=True))
    directs = {(c["item_code"], c["price_list"]) for c in changements}

    for pl, nouveaux in par_liste.items():
        prix = _price_map(tous_composants + list(bundles), [pl])
        for bundle_code, comps in bundles.items():
            if (bundle_code, pl) in directs:
                continue        # prix du bundle changé explicitement : il prime
            total, incomplet = 0.0, False
            for comp in comps:
                p = nouveaux.get(comp["item_code"], prix.get((comp["item_code"], pl)))
                if p is None:
                    incomplet = True
                    break
                total += flt(p) * comp["qty"]
            resultat.append({
                "bundle": bundle_code,
                "item_name": noms.get(bundle_code) or bundle_code,
                "price_list": pl,
                "ancien": prix.get((bundle_code, pl)),
                "nouveau": None if incomplet else round(total, 3),
                "incomplet": incomplet,
            })
    return resultat


def _poser_prix(item_code, price_list, nouveau):
    """Clôt les lignes de prix valides aujourd'hui et crée la nouvelle."""
    today = getdate(nowdate())
    lignes = frappe.get_all(
        "Item Price",
        filters={"selling": 1, "item_code": item_code, "price_list": price_list},
        fields=["name", "valid_from", "valid_upto"])
    hier = frappe.utils.add_days(today, -1)
    for l in lignes:
        vf = getdate(l.valid_from) if l.valid_from else None
        vu = getdate(l.valid_upto) if l.valid_upto else None
        if (vf and vf > today) or (vu and vu < today):
            continue            # pas valide aujourd'hui : on n'y touche pas
        if vf and vf >= today:
            # créée aujourd'hui : impossible de la clore hier → maj sur place
            frappe.db.set_value("Item Price", l.name, "price_list_rate", nouveau)
            return l.name
        frappe.db.set_value("Item Price", l.name, "valid_upto", hier)

    doc = frappe.get_doc({
        "doctype": "Item Price", "selling": 1,
        "item_code": item_code, "price_list": price_list,
        "price_list_rate": nouveau, "valid_from": today,
    })
    doc.insert(ignore_permissions=True)
    return doc.name


@frappe.whitelist()
def apercu_prix(cible, maj_bundles=1):
    """Ce qui VA changer — avant d'appliquer. Rendu article par article, plus
    la cascade sur les bundles actifs concernés."""
    _guard()
    changements = _resoudre_changements(cible)
    bundles = _cascade_bundles(changements) if frappe.utils.cint(maj_bundles) else []
    return {"changements": changements, "bundles": bundles}


@frappe.whitelist()
def appliquer_prix(cible, maj_bundles=1, bundles_exclus=None):
    """Applique les changements (articles puis bundles), à la manière ERPNext :
    nouvelle ligne de prix datée d'aujourd'hui, l'ancienne close à hier — les
    commandes antérieures, brouillons compris, ne sont jamais affectées.
    `bundles_exclus` : codes de bundles décochés dans l'aperçu (non réalignés)."""
    _guard()
    changements = _resoudre_changements(cible)
    if not changements:
        frappe.throw(_("Aucun changement de prix à appliquer."))
    bundles = _cascade_bundles(changements) if frappe.utils.cint(maj_bundles) else []
    exclus = set(frappe.parse_json(bundles_exclus) if isinstance(bundles_exclus, str)
                 else (bundles_exclus or []))
    bundles = [b for b in bundles if b["bundle"] not in exclus]

    for c in changements:
        _poser_prix(c["item_code"], c["price_list"], c["nouveau"])
    faits_bundles = 0
    for b in bundles:
        if b["nouveau"] is None:
            continue
        _poser_prix(b["bundle"], b["price_list"], b["nouveau"])
        faits_bundles += 1
    frappe.db.commit()
    return {"articles": len(changements), "bundles": faits_bundles,
            "bundles_incomplets": [b["bundle"] for b in bundles if b["incomplet"]],
            "changements": changements, "detail_bundles": bundles}
