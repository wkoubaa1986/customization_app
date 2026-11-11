"""Assistant « Nouvelle liste de prix » (page /app/liste-prix-assistant, 11/11/2026).

Le formulaire « Liste prix documents » demande de remplir un tableau de groupes, de cliquer
« Obtenir Articles », puis de retoucher des centaines de lignes. L'assistant fait la même pièce en
trois gestes : pour qui (client ou liste de prix), quoi (recherche ou famille entière), créer.

Rien n'est recalculé ici : la recherche, les prix, la TVA, les remises par quantité et la forme des
lignes viennent de `booking_ristourne.catalogue_client`, le module de la fiche client — une liste
faite par un employé et une liste faite par le client depuis sa fiche sont donc identiques.
Le document créé est soumis et s'imprime avec « Liste prix client » ou « Liste des prix ».
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.utils import cint, flt

DOCTYPE = "Liste prix documents"
ARTICLES_MAX = 1500          # la plus grosse liste faite à la main à ce jour : 1 286 lignes
RESULTATS_MAX = 40
BRANCHE_EXCLUE = "Services & Interventions"
FORMATS = ["Liste prix client", "Liste des prix"]
PROMO_VIDE = {"group_rules": [], "item_rules": [], "group_descriptions": [], "item_descriptions": [],
              "multi_item_rules": [], "multi_item_sets": []}


def _catalogue():
    from booking_ristourne import catalogue_client

    return catalogue_client


def _garde():
    for droit in ("create", "submit"):
        frappe.has_permission(DOCTYPE, droit, throw=True)


def _liste_valide(price_list: str) -> str:
    price_list = (price_list or "").strip()
    if not price_list or not frappe.db.get_value("Price List", {"name": price_list, "selling": 1, "enabled": 1}):
        frappe.throw(_("Choisissez une liste de prix de vente active."))
    return price_list


# ── Fonctions pures (testées sans site) ──────────────────────────────────────

def quantites_demandees(articles, maximum: int = ARTICLES_MAX) -> dict:
    """{code: quantité ≥ 1} dans l'ordre reçu ; un code répété cumule. `articles` : JSON ou liste de
    {item_code, qte} / de codes."""
    if isinstance(articles, str):
        try:
            articles = json.loads(articles)
        except ValueError:
            frappe.throw(_("Liste d'articles illisible."))
    out: dict = {}
    for a in articles or []:
        code = (a.get("item_code") if isinstance(a, dict) else a) or ""
        code = str(code).strip()
        if not code:
            continue
        qte = max(cint(a.get("qte") or a.get("qty")) if isinstance(a, dict) else 0, 1)
        out[code] = out.get(code, 0) + qte
    if len(out) > maximum:
        frappe.throw(_("Une liste ne peut pas dépasser {0} articles ({1} demandés).").format(maximum, len(out)))
    return out


def cumuler_familles(groupes: list, directs: dict) -> list:
    """Arbre aplati des familles, dans l'ordre de l'arbre (lft), avec le nombre d'articles de chaque
    famille ET de sa descendance. `groupes` : [{name, parent, lft, rgt}] ; `directs` : {famille: n}.
    Les familles vides sont retirées."""
    groupes = sorted(groupes, key=lambda g: g["lft"])
    out, pile = [], []          # pile : ancêtres ouverts (rgt)
    for g in groupes:
        while pile and pile[-1] < g["lft"]:
            pile.pop()
        total = sum(n for nom, n in ((x["name"], directs.get(x["name"], 0)) for x in groupes
                                     if g["lft"] <= x["lft"] and x["rgt"] <= g["rgt"]))
        if total:
            out.append({"name": g["name"], "niveau": len(pile), "nb": total,
                        "feuille": g["rgt"] - g["lft"] == 1})
        pile.append(g["rgt"])
    return out


def ligne_apercu(fiche: dict, ligne: dict) -> dict:
    """Ce que l'écran affiche pour une ligne : la fiche prix + la ligne de document calculée."""
    return {
        "item_code": fiche["item_code"], "item_name": fiche["item_name"], "item_group": fiche["item_group"] or "",
        "image": fiche.get("image"), "brand": fiche.get("brand"),
        "prix_ttc": fiche["prix_ttc"], "prix_standard_ttc": fiche.get("prix_standard_ttc"),
        "remise_pct": fiche.get("remise_pct") or 0,
        "qte": ligne["custom_quantite"],
        "palier": ligne["custom_remise_quantite"], "prochain": ligne["custom_prochain_palier"],
        "total": flt(ligne["custom_total_ttc"]) if ligne["custom_total_ttc"] != "" else None,
    }


# ── Lecture ──────────────────────────────────────────────────────────────────

def _fiches(codes: list, price_list: str) -> list:
    """Fiches prix des articles simples et des variantes (jamais un modèle), regroupées par famille.
    Comme à la vente, une variante sans prix propre prend celui de son modèle."""
    C = _catalogue()
    if not codes:
        return []
    items = frappe.get_all("Item", filters={"item_code": ["in", codes], "disabled": 0, "is_sales_item": 1,
                                            "has_variants": 0}, fields=C.CHAMPS_ITEM, limit_page_length=0)
    par_code = {i.item_code: i for i in items}
    codes = [c for c in codes if c in par_code]
    modeles = sorted({i.variant_of for i in items if i.variant_of})

    def prix(liste):
        p = C._prix(codes + modeles, liste)
        for c in codes:
            m = par_code[c].variant_of
            if c not in p and m in p:
                p[c] = p[m]
        return p

    prix_liste = prix(price_list)
    prix_standard = prix_liste if price_list == C.PRIX_STANDARD else prix(C.PRIX_STANDARD)
    tva = C._taux_tva(codes)
    remises = C._remises_quantite([{"item_code": c, "item_group": par_code[c].item_group} for c in codes], price_list)
    fiches = [C._fiche_prix(par_code[c], prix_liste, prix_standard, tva, remises) for c in codes]
    ordre = {}
    for f in fiches:
        ordre.setdefault(f["item_group"] or "", len(ordre))
    return sorted(fiches, key=lambda f: ordre[f["item_group"] or ""])


def _calcul(articles, price_list: str) -> dict:
    """Les lignes de document pour ces articles : {fiches, lignes, quantites, sans_prix}."""
    C = _catalogue()
    quantites = quantites_demandees(articles)
    fiches = _fiches(list(quantites), price_list)
    sans_prix = [f["item_code"] for f in fiches if f["prix_ttc"] is None]
    fiches = [f for f in fiches if f["prix_ttc"] is not None]
    cumuls = C._cumuls_mixtes(fiches, quantites)
    lignes = [C._ligne_document(f, quantites[f["item_code"]], cumuls) for f in fiches]
    connus = {f["item_code"] for f in fiches} | set(sans_prix)
    return {"fiches": fiches, "lignes": lignes, "quantites": quantites, "sans_prix": sans_prix,
            "introuvables": [c for c in quantites if c not in connus]}


@frappe.whitelist()
def contexte() -> dict:
    """Listes de prix de vente, arbre des familles (hors services) et formats d'impression."""
    _garde()
    listes = frappe.get_all("Price List", filters={"selling": 1, "enabled": 1}, pluck="name", order_by="name")
    groupes = frappe.get_all("Item Group", fields=["name", "parent_item_group as parent", "lft", "rgt"],
                             limit_page_length=0)
    exclu = next((g for g in groupes if g["name"] == BRANCHE_EXCLUE), None)
    racine = min(groupes, key=lambda g: g["lft"]) if groupes else None
    groupes = [g for g in groupes
               if g is not racine and not (exclu and exclu["lft"] <= g["lft"] and g["rgt"] <= exclu["rgt"])]
    directs = dict(frappe.db.sql("""select item_group, count(*) from `tabItem`
                                    where disabled = 0 and is_sales_item = 1 and has_variants = 0
                                    group by item_group"""))
    return {"listes": listes, "defaut": _catalogue().PRIX_STANDARD,
            "familles": cumuler_familles(groupes, directs),
            "formats": [f for f in FORMATS if frappe.db.exists("Print Format", f)],
            "maximum": ARTICLES_MAX}


@frappe.whitelist()
def liste_du_client(client: str) -> dict:
    _garde()
    frappe.has_permission("Customer", "read", client, throw=True)
    return {"liste": _catalogue().liste_de_prix_du_client(client),
            "nom": frappe.db.get_value("Customer", client, "customer_name") or client}


@frappe.whitelist()
def rechercher(query: str, price_list: str, item_group: str = None) -> dict:
    """Même recherche que la fiche client (microservice, sinon mots-clés ERPNext), aux prix de la liste."""
    _garde()
    C = _catalogue()
    price_list = _liste_valide(price_list)
    query = (query or "").strip()[:200]
    if len(query) < 2:
        return {"results": [], "source": None}
    item_group = (item_group or "").strip() or None
    corrections: dict = {}
    codes = C._appeler_product_search(query, RESULTATS_MAX, item_group)
    source = "ia"
    if not codes:
        codes = C._recherche_locale(query, RESULTATS_MAX, item_group, corrections)
        source = "erpnext"
    results = C._details_articles(codes, price_list)
    return {"results": results, "source": source, "corrections": corrections if results else {}}


@frappe.whitelist()
def famille(item_group: str, price_list: str) -> dict:
    """Tous les articles vendables d'une famille et de ses sous-familles qui ont un prix dans la liste."""
    _garde()
    C = _catalogue()
    price_list = _liste_valide(price_list)
    if not frappe.db.exists("Item Group", item_group):
        frappe.throw(_("Famille inconnue."))
    codes = frappe.get_all("Item", filters={"item_group": ["in", C._sous_groupes(item_group)], "disabled": 0,
                                            "is_sales_item": 1, "has_variants": 0},
                           pluck="name", order_by="item_group, item_name", limit_page_length=0)
    fiches = _fiches(codes, price_list)
    avec = [f for f in fiches if f["prix_ttc"] is not None]
    for f in avec:
        f.pop("description", None)
    return {"famille": item_group, "results": avec, "sans_prix": len(fiches) - len(avec)}


@frappe.whitelist()
def apercu(articles, price_list: str) -> dict:
    """La liste telle qu'elle sera créée : prix de la liste, remise, paliers de quantité, total."""
    _garde()
    c = _calcul(articles, _liste_valide(price_list))
    lignes = [ligne_apercu(f, l) for f, l in zip(c["fiches"], c["lignes"])]
    return {"lignes": lignes, "total": round(sum(l["total"] or 0 for l in lignes), 3),
            "sans_prix": c["sans_prix"], "introuvables": c["introuvables"]}


# ── Création ─────────────────────────────────────────────────────────────────

def _referant() -> str | None:
    employe = frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"})
    return employe or frappe.db.get_single_value("AquaWorld Settings", "employee") or None


def url_pdf(name: str, format_impression: str) -> str:
    return "/api/method/frappe.utils.print_format.download_pdf?" + urlencode(
        {"doctype": DOCTYPE, "name": name, "format": format_impression, "no_letterhead": 1})


@frappe.whitelist(methods=["POST"])
def creer(articles, price_list: str, client: str = None) -> dict:
    """Crée et soumet la « Liste prix documents ». Les articles sans prix dans la liste sont écartés
    (et nommés dans la réponse) : une ligne sans prix n'a rien à faire sur une liste de prix."""
    _garde()
    C = _catalogue()
    price_list = _liste_valide(price_list)
    client = (client or "").strip() or None
    if client:
        frappe.has_permission("Customer", "read", client, throw=True)
    c = _calcul(articles, price_list)
    if not c["lignes"]:
        frappe.throw(_("Aucun de ces articles n'a de prix dans la liste « {0} ».").format(price_list))

    groupes = []
    for f in c["fiches"]:
        if f["item_group"] and f["item_group"] not in groupes:
            groupes.append(f["item_group"])
    lignes = []
    for f, l in zip(c["fiches"], c["lignes"]):
        # `rem` : la remise face au prix standard, lue par le format « Liste des prix ».
        lignes.append({**l, "rem": flt(f.get("remise_pct"))})

    doc = frappe.get_doc({
        "doctype": DOCTYPE,
        "naming_series": C.NAMING_SERIES,
        "liste_des_prix": price_list,
        "custom_client": client,
        "référant": _referant(),
        "génrer_automatique": 0,
        "promo_json": json.dumps(PROMO_VIDE),
        "group_articles": [{"goup_articles": g} for g in groupes],
        "liste_articles": lignes,
    })
    doc.insert()
    doc.submit()
    formats = [f for f in FORMATS if frappe.db.exists("Print Format", f)]
    return {"name": doc.name, "nb_articles": len(lignes), "sans_prix": c["sans_prix"],
            "pdf": [{"format": f, "url": url_pdf(doc.name, f)} for f in formats]}
