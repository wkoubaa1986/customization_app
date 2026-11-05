"""Assistant Articles — second plugin ChatGPT (serveur MCP), SÉPARÉ, pour lire, créer et modifier les ARTICLES
(demande du 05/11/2026).

Distinct de l'assistant ChatGPT (assistant_chatgpt / mcp_chatgpt : dépenses, versements, rendez-vous) : autre point
d'entrée (mcp_articles.serveur), autre utilisateur technique, autre rôle, autre clé, autre jeu d'outils. On branche
l'un sans l'autre dans ChatGPT, on révoque l'un sans toucher l'autre. Seule la mécanique du protocole est partagée.

SÉCURITÉ. Utilisateur technique `articles.chatgpt@aquaworld.local` (patch ensure_assistant_articles), rôle « Assistant
Articles » SANS permission de doctype : l'API REST ordinaire lui est fermée, seules ces fonctions (frappe.only_for)
répondent. Ce qu'il ne fait JAMAIS : supprimer un article (on désactive, avec un motif), renommer un code, toucher au
stock (aucune écriture de stock), créer un modèle à variantes ou une variante. Chaque création et chaque modification
est faite sous cet utilisateur (historique « Version » d'ERPNext) et commentée « via ChatGPT » ; `mes_modifications`
les retrouve.

RÈGLES MÉTIER. Les articles sont nommés par leur CODE (Stock Settings) : code obligatoire, définitif. Groupe = groupe
FEUILLE de l'arbre (jamais un groupe parent). TVA par modèle de taxe, 7 % ou 19 %. Prix de VENTE TTC, un par liste de
prix (convention de la base, cf. analyse_articles) ; prix d'ACHAT HT sur « Achat standard ». La marge rapportée est
(PV HT − coût HT) / PV HT, coût = valorisation du stock (repli : dernier prix d'achat), comme la popup « Prix de vente ».
Un article créé a sa sortie principale au magasin (Item Default) et n'est PAS synchronisé au site web : il faut une
image, et c'est un humain qui coche. Un article déjà synchronisé (custom_sync_avec_woocommerce) voit nom et
descriptions repartir vers le site à la synchro de nuit : on le dit dans la réponse.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.utils import cint, flt, get_url

from customization_app import assistant_chatgpt as A
from customization_app import caisse_depenses as CD
from customization_app import ensembles_produits as E

ROLE = "Assistant Articles"
UTILISATEUR = "articles.chatgpt@aquaworld.local"
MARQUE = "via ChatGPT (articles)"
MAGASIN = "Magasins - A&S"
LISTE_VENTE = "Vente standard"
LISTE_ACHAT = "Achat standard"
TAUX_TVA = {7: "TVA 7% - A&S", 19: "TVA 19% - A&S"}
GROUPE_RACINE = "Tous les Groupes d'Articles"
MAX_RESULTATS = 20
TAILLE_IMAGE_MAX = 8 * 1024 * 1024
TYPES_IMAGE = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
CRITERES = ("sans_image", "sans_prix", "sans_description", "sans_tva", "sans_marque", "sans_prix_achat", "en_rupture", "en_rupture_sans_image")
PB = "Product Bundle"
MAX_LOT = 100

#: Champs que l'accès générique (lire_champs / modifier_champs) ne modifie JAMAIS : identité, nature de stock, séries,
#: immobilisations, et ceux qui ont leur propre outil avec sa règle (groupe, image, désactivation, synchro site).
CHAMPS_INTERDITS = {"name", "item_code", "naming_series", "has_variants", "variant_of", "variant_based_on", "is_stock_item",
                    "has_serial_no", "serial_no_series", "has_batch_no", "batch_number_series", "create_new_batch", "has_expiry_date",
                    "opening_stock", "valuation_rate", "last_purchase_rate", "total_projected_qty", "is_fixed_asset", "asset_category",
                    "asset_naming_series", "auto_create_assets", "is_grouped_asset", "customer", "is_customer_provided_item",
                    "disabled", "image", "item_group", "custom_sync_avec_woocommerce", "custom_emplacement_magasin", "stock_uom", "valuation_method"}
TYPES_MODIFIABLES = {"Data", "Small Text", "Text", "Long Text", "Text Editor", "Markdown Editor", "Check", "Int", "Float", "Currency",
                     "Percent", "Select", "Link", "Date"}
TYPES_LISIBLES = TYPES_MODIFIABLES | {"Attach Image", "Attach", "Read Only", "Datetime", "Time"}
VERS_OUTIL = {"item_group": "modifier_article / deplacer_articles", "image": "image_article", "disabled": "modifier_article (desactive, motif)",
              "custom_sync_avec_woocommerce": "modifier_article (sync_site_web)", "has_variants": "rendre_modele", "variant_of": "creer_variante"}
RX_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .+'’/_-]{0,139}$")

#: paramètre de l'outil → champ de la fiche Article (modification champ par champ, seulement ce qui est fourni)
CHAMPS = {"nom": "item_name", "description": "description", "groupe": "item_group", "marque": "brand",
          "volume_m3": "custom_volume_m3", "description_web_courte": "custom_web_short_description",
          "description_web_longue": "custom_web_long_description", "desactive": "disabled",
          "rupture_site_web": "custom_rupture_site_web", "sync_site_web": "custom_sync_avec_woocommerce"}
CASES = {"desactive", "rupture_site_web", "sync_site_web"}


# ── Règles pures ─────────────────────────────────────────────────────────────

def _nombre(x) -> float | None:
    """float(x) tolérant (virgule décimale, espaces) ; None si illisible. Pure."""
    if x is None or x == "":
        return None
    try:
        return float(str(x).replace(",", ".").replace(" ", ""))
    except ValueError:
        return None


def _entier(x) -> int | None:
    n = _nombre(x)
    return int(n) if n is not None and n == int(n) else None


def lire_prix(prix) -> dict:
    """{liste de prix: montant} depuis un objet, une chaîne JSON ou rien. Montant illisible → ValueError. Pure."""
    if not prix:
        return {}
    if isinstance(prix, str):
        prix = json.loads(prix)
    if not isinstance(prix, dict):
        raise ValueError(_("Les prix s'écrivent {\"Liste de prix\": montant, …}."))
    out = {}
    for liste, montant in prix.items():
        n = _nombre(montant)
        if n is None:
            raise ValueError(_("Prix illisible pour « {0} » : {1}.").format(liste, montant))
        out[str(liste).strip()] = n
    return out


def lire_composants(composants) -> dict:
    """{code composant: quantité > 0} depuis un objet, une chaîne JSON ou rien. Pure."""
    if not composants:
        return {}
    if isinstance(composants, str):
        composants = json.loads(composants)
    if not isinstance(composants, dict):
        raise ValueError(_("Les composants s'écrivent {\"CODE\": quantité, …}."))
    out = {}
    for code, q in composants.items():
        n = _nombre(q)
        if n is None or n <= 0:
            raise ValueError(_("Quantité illisible ou nulle pour « {0} » : {1}.").format(code, q))
        out[str(code).strip()] = n
    return out


def lire_codes(codes) -> list[str]:
    """Liste de codes depuis une liste, une chaîne JSON ou une chaîne « A, B, C ». Pure."""
    if not codes:
        return []
    if isinstance(codes, str):
        c = codes.strip()
        codes = json.loads(c) if c.startswith("[") else c.replace("\n", ",").split(",")
    return list(dict.fromkeys(str(x).strip() for x in codes if x is not None and str(x).strip()))


def verifier_article(code, nom, groupe, unite, taux_tva, marque, groupes_feuilles, unites, marques) -> list[str]:
    """Les refus d'une création, en clair (vide = acceptable). Pure."""
    e = []
    c = (code or "").strip()
    if not c or c != code or not RX_CODE.match(c):
        e.append(_("Code invalide : lettres, chiffres, tirets, points (140 caractères au plus, sans espace au bord)."))
    n = (nom or "").strip()
    if not n:
        e.append(_("Le nom de l'article est obligatoire."))
    elif len(n) > 140:
        e.append(_("Nom trop long (140 caractères au plus)."))
    if groupe not in groupes_feuilles:
        e.append(_("Groupe « {0} » inconnu ou groupe parent : choisir un groupe feuille renvoyé par groupes_articles.").format(groupe))
    if unite not in unites:
        e.append(_("Unité « {0} » inconnue : choisir parmi celles de references.").format(unite))
    if _entier(taux_tva) not in TAUX_TVA:
        e.append(_("Taux de TVA « {0} » : 7 ou 19.").format(taux_tva))
    if marque and marque not in marques:
        e.append(_("Marque « {0} » inconnue : choisir parmi celles de references (ou ne pas en mettre).").format(marque))
    return e


def verifier_prix(prix: dict, listes: dict) -> list[str]:
    """Refus sur des prix {liste: montant} face aux listes de prix {nom: {selling, buying}}. Pure."""
    e = []
    for liste, montant in prix.items():
        if liste not in listes:
            e.append(_("Liste de prix « {0} » inconnue : {1}.").format(liste, ", ".join(sorted(listes))))
        elif montant < 0:
            e.append(_("Prix négatif sur « {0} ».").format(liste))
    return e


def marge_pct(prix_ttc, taux_tva, cout_ht) -> float | None:
    """Marge (%) sur le prix de vente HT : (PV HT − coût) / PV HT. None si pas de coût ou pas de prix. Pure."""
    p, t, c = _nombre(prix_ttc) or 0.0, _nombre(taux_tva) or 0.0, _nombre(cout_ht) or 0.0
    if p <= 0 or c <= 0:
        return None
    ht = p / (1 + t / 100.0)
    return round((ht - c) / ht * 100.0, 1)


def chemins_groupes(groupes: list[dict]) -> dict:
    """{groupe feuille: « Parent > Sous-groupe > Feuille »} depuis [{name, parent_item_group, is_group}]. Pure."""
    parents = {g["name"]: g.get("parent_item_group") for g in groupes}
    out = {}
    for g in groupes:
        if g.get("is_group"):
            continue
        chemin, n = [], g["name"]
        while n and n != GROUPE_RACINE and n not in chemin:
            chemin.append(n)
            n = parents.get(n)
        out[g["name"]] = " > ".join(reversed(chemin))
    return out


def changements(valeurs: dict, actuel: dict) -> dict:
    """{champ ERPNext: nouvelle valeur} pour les paramètres fournis ET différents de la fiche. Pure."""
    out = {}
    for param, champ in CHAMPS.items():
        v = valeurs.get(param)
        if v is None or v == "":
            continue
        if param in CASES:
            v = 1 if str(v).strip().lower() in ("1", "true", "oui", "yes") else 0
        elif param == "volume_m3":
            v = _nombre(v)
        else:
            v = str(v).strip()
        if actuel.get(champ) != v:
            out[champ] = v
    return out


def coercer(champ: dict, valeur):
    """La valeur telle que la fiche la stocke, selon le type du champ {fieldtype, options, label} ; ValueError si elle ne
    convient pas (Select hors liste, nombre illisible, date illisible). L'existence d'un Link se vérifie à part. Pure."""
    t = champ.get("fieldtype")
    if t == "Check":
        return 1 if str(valeur).strip().lower() in ("1", "true", "oui", "yes", "vrai") else 0
    if t == "Int":
        n = _entier(valeur)
        if n is None:
            raise ValueError(_("« {0} » attend un nombre entier.").format(champ.get("label")))
        return n
    if t in ("Float", "Currency", "Percent"):
        n = _nombre(valeur)
        if n is None:
            raise ValueError(_("« {0} » attend un nombre.").format(champ.get("label")))
        return n
    if t == "Select":
        options = [o for o in (champ.get("options") or "").split("\n") if o]
        v = str(valeur).strip()
        if v not in options:
            raise ValueError(_("« {0} » : valeur « {1} » hors liste ({2}).").format(champ.get("label"), v, ", ".join(options)))
        return v
    if t == "Date":
        v = str(valeur).strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", v):
            raise ValueError(_("« {0} » attend une date AAAA-MM-JJ.").format(champ.get("label")))
        return v
    return str(valeur).strip() if valeur is not None else None


def modifiable(champ: dict) -> bool:
    """Un champ que modifier_champs accepte : type simple, ni interdit, ni en lecture seule, ni caché. Pure."""
    return champ.get("fieldtype") in TYPES_MODIFIABLES and champ.get("fieldname") not in CHAMPS_INTERDITS \
        and not champ.get("read_only") and not champ.get("hidden")


def url_acceptable(url: str) -> bool:
    """http(s), un hôte nommé, rien de local. La résolution DNS est contrôlée à part (_hote_public). Pure."""
    try:
        u = urlparse(url or "")
    except ValueError:
        return False
    return u.scheme in ("http", "https") and bool(u.hostname) and u.hostname not in ("localhost",) \
        and not u.hostname.endswith(".local") and not u.hostname.endswith(".internal")


# ── Accès ────────────────────────────────────────────────────────────────────

def _acces():
    frappe.only_for((ROLE, "System Manager"))


def _url(code):
    return get_url("/app/item/" + code)


def _article(code):
    if not code or not frappe.db.exists("Item", code):
        frappe.throw(_("Article « {0} » introuvable : chercher_article pour trouver le code exact.").format(code))
    return frappe.get_doc("Item", code)


def _listes() -> dict:
    return {p.name: {"selling": cint(p.selling), "buying": cint(p.buying), "devise": p.currency}
            for p in frappe.get_all("Price List", filters={"enabled": 1}, fields=["name", "selling", "buying", "currency"])}


def _groupes() -> list[dict]:
    return frappe.get_all("Item Group", fields=["name", "parent_item_group", "is_group"], order_by="lft")


def _unites() -> list[str]:
    """Les unités en usage sur les articles (pas les centaines d'unités livrées avec ERPNext)."""
    return frappe.db.sql_list("select distinct stock_uom from tabItem where stock_uom is not null order by stock_uom")


def _marques() -> list[str]:
    return frappe.get_all("Brand", pluck="name", order_by="name")


def _taux_tva(doc) -> int | None:
    for t in doc.get("taxes") or []:
        for taux, modele in TAUX_TVA.items():
            if t.item_tax_template == modele:
                return taux
    return None


def _cout(doc) -> float:
    from customization_app.api import _get_item_valuation_rate
    v = flt(_get_item_valuation_rate(doc.name, fallback=doc.valuation_rate), 3)
    return v if v > 0 else flt(doc.last_purchase_rate, 3)


def _prix(code) -> dict:
    """{liste: montant} des prix de l'article, toutes listes actives."""
    out = {}
    for r in frappe.get_all("Item Price", filters={"item_code": code}, fields=["price_list", "price_list_rate", "modified"],
                            order_by="modified desc"):
        out.setdefault(r.price_list, flt(r.price_list_rate, 3))      # le plus récent par liste
    return out


def _stock(code) -> list[dict]:
    return [{"entrepot": b.warehouse, "qte": flt(b.actual_qty, 3)} for b in
            frappe.get_all("Bin", filters={"item_code": code, "actual_qty": ["!=", 0]}, fields=["warehouse", "actual_qty"])]


def _commenter(doc, texte):
    doc.add_comment("Comment", "%s %s" % (texte, MARQUE))


def _abs(url):
    return get_url(url) if url and url.startswith("/") else url


# ── Lecture ──────────────────────────────────────────────────────────────────

@frappe.whitelist()
def moi():
    """Qui parle, les repères de la base article et ce que l'assistant sait faire."""
    _acces()
    n = frappe.db.sql("select count(*) n, sum(disabled) d from tabItem", as_dict=True)[0]
    return {"utilisateur": frappe.session.user, "societe": CD.COMPANY, "articles_actifs": cint(n.n) - cint(n.d),
            "articles_desactives": cint(n.d), "listes_de_prix": sorted(_listes()), "taux_tva": sorted(TAUX_TVA),
            "magasin_par_defaut": MAGASIN, "actions": list(OUTILS),
            "interdits": [_("supprimer un article ou un groupe (on désactive)"), _("renommer un code ou un groupe"), _("écriture de stock"),
                          _("changer la nature de stock, les séries, l'image ou le groupe autrement que par leur outil")]}


@frappe.whitelist()
def references():
    """Unités, marques, listes de prix, taux de TVA et entrepôts : les valeurs exactes à employer."""
    _acces()
    return {"unites": _unites(), "marques": _marques(), "listes_de_prix": _listes(), "taux_tva": sorted(TAUX_TVA),
            "entrepots": entrepots()}


@frappe.whitelist()
def entrepots():
    """Les entrepôts actifs, avec le nombre d'articles en stock et la quantité totale dans chacun."""
    _acces()
    rows = frappe.db.sql("""select w.name as entrepot, count(b.name) as articles, ifnull(sum(b.actual_qty), 0) as quantite
                            from tabWarehouse w left join tabBin b on b.warehouse = w.name and b.actual_qty > 0
                            where w.disabled = 0 and w.is_group = 0 group by w.name order by w.name""", as_dict=True)
    for r in rows:
        r.articles, r.quantite = cint(r.articles), flt(r.quantite, 3)
    return rows


@frappe.whitelist()
def groupes_articles(texte=None):
    """Les groupes FEUILLES (ceux qu'un article peut porter), avec leur chemin et leur nombre d'articles."""
    _acces()
    chemins = chemins_groupes(_groupes())
    comptes = dict(frappe.db.sql("select item_group, count(*) from tabItem where disabled = 0 group by item_group"))
    t = (texte or "").strip().lower()
    return [{"groupe": g, "chemin": c, "articles": cint(comptes.get(g))} for g, c in chemins.items()
            if not t or t in c.lower()]


@frappe.whitelist()
def chercher_article(texte=None, groupe=None, entrepot=None, en_rupture=0, sans_image=0, inclure_desactives=0, limite=MAX_RESULTATS):
    """Articles par mots (code, nom, description, marque, ordre libre) ou code-barres, croisés avec des filtres :
    groupe exact, entrepôt (stock présent là), en rupture (aucun stock nulle part), sans image, désactivés compris."""
    _acces()
    texte = (texte or "").strip()
    if not (texte or groupe or entrepot or cint(en_rupture) or cint(sans_image)):
        frappe.throw(_("Donner des mots à chercher ou au moins un filtre (groupe, entrepot, en_rupture, sans_image)."))
    filtres, vals = [], []
    if texte:
        sql, vals = A._mots(texte, ["i.name", "i.item_name", "i.description", "i.brand"])
        par_code_barres = frappe.get_all("Item Barcode", filters={"barcode": texte}, pluck="parent")
        ou = ""
        if par_code_barres:
            ou = " or i.name in (" + ", ".join(["%s"] * len(par_code_barres)) + ")"
            vals += par_code_barres
        filtres.append(f"(({sql}){ou})")
    if not cint(inclure_desactives):
        filtres.append("i.disabled = 0")
    if groupe:
        filtres.append("i.item_group = %s")
        vals.append(groupe)
    if entrepot:
        if not frappe.db.exists("Warehouse", entrepot):
            frappe.throw(_("Entrepôt « {0} » inconnu (references).").format(entrepot))
        filtres.append("exists (select 1 from tabBin b where b.item_code = i.name and b.warehouse = %s and b.actual_qty > 0)")
        vals.append(entrepot)
    if cint(en_rupture):
        filtres.append("i.is_stock_item = 1 and i.has_variants = 0 and ifnull((select sum(actual_qty) from tabBin b where b.item_code = i.name), 0) <= 0")
    if cint(sans_image):
        filtres.append("ifnull(i.image, '') = ''")
    vals.append(min(cint(limite) or MAX_RESULTATS, MAX_LOT))
    rows = frappe.db.sql(f"""select i.name as code, i.item_name as nom, i.item_group as groupe, i.stock_uom as unite, i.brand as marque,
                                    i.disabled as desactive, i.is_stock_item as stock_suivi, i.has_variants as modele,
                                    i.variant_of as variante_de, (i.image is not null and i.image != '') as image
                             from tabItem i where {' and '.join(filtres)}
                             order by i.disabled, i.item_group, i.item_name limit %s""", vals, as_dict=True)
    if rows:
        codes = [r.code for r in rows]
        stocks = dict(frappe.db.sql("select item_code, sum(actual_qty) from tabBin where item_code in %s group by item_code", (codes,)))
        prix = dict(frappe.db.sql("""select item_code, price_list_rate from `tabItem Price` where price_list = %s and item_code in %s""",
                                  (LISTE_VENTE, codes)))
        la = dict(frappe.db.sql("select item_code, actual_qty from tabBin where warehouse = %s and item_code in %s", (entrepot, codes))) if entrepot else {}
        ensembles = set(frappe.get_all(PB, filters={"new_item_code": ["in", codes]}, pluck="new_item_code"))
        for r in rows:
            r.stock_total = flt(stocks.get(r.code), 3)
            if entrepot:
                r.stock_entrepot = flt(la.get(r.code), 3)
            r.prix_vente = flt(prix.get(r.code), 3)
            r.image = bool(r.image)
            if r.code in ensembles:
                r.ensemble = True
    return rows


@frappe.whitelist()
def fiche_article(code):
    """La fiche complète d'un article : champs, TVA, prix et marges par liste, stock par entrepôt, image, variantes."""
    _acces()
    doc = _article(code)
    tva = _taux_tva(doc)
    cout = _cout(doc)
    listes = _listes()
    prix = {}
    for liste, montant in _prix(doc.name).items():
        prix[liste] = {"prix": montant, "ht": listes.get(liste, {}).get("buying") == 1 and not listes.get(liste, {}).get("selling"),
                       "marge_pct": marge_pct(montant, tva or 0, cout) if listes.get(liste, {}).get("selling") else None}
    stock = _stock(doc.name)
    f = {"code": doc.name, "nom": doc.item_name, "groupe": doc.item_group, "chemin_groupe": chemins_groupes(_groupes()).get(doc.item_group, doc.item_group),
         "marque": doc.brand, "unite": doc.stock_uom, "description": doc.description, "desactive": cint(doc.disabled),
         "stock_suivi": cint(doc.is_stock_item), "taux_tva": tva, "image": _abs(doc.image), "sync_site_web": cint(doc.get("custom_sync_avec_woocommerce")),
         "rupture_site_web": cint(doc.get("custom_rupture_site_web")), "codes_barres": [b.barcode for b in doc.get("barcodes") or []],
         "volume_m3": flt(doc.get("custom_volume_m3"), 4) or None, "emplacement_magasin": doc.get("custom_emplacement_magasin"),
         "description_web_courte": doc.get("custom_web_short_description"),
         "description_web_longue": (doc.get("custom_web_long_description") or "")[:1500] or None,
         "cout_ht": cout or None, "prix": prix, "stock": stock, "stock_total": flt(sum(s["qte"] for s in stock), 3),
         "cree_le": str(doc.creation)[:10], "modifie_le": str(doc.modified)[:10], "url": _url(doc.name)}
    if doc.has_variants:
        f["modele"] = True
        f["variantes"] = frappe.get_all("Item", filters={"variant_of": doc.name}, fields=["name as code", "item_name as nom", "disabled as desactive"])
    if doc.variant_of:
        f["variante_de"] = doc.variant_of
        f["attributs"] = {a.attribute: a.attribute_value for a in doc.get("attributes") or []}
    pb = frappe.db.get_value(PB, {"new_item_code": doc.name}, "name")
    if pb:
        f["ensemble"] = _ensemble(pb)
        if not cout and f["ensemble"]["cout_ht_composants"]:          # la marge d'un ensemble se mesure sur ses composants
            f["cout_ht"] = f["ensemble"]["cout_ht_composants"]
            for liste, v in prix.items():
                if listes.get(liste, {}).get("selling"):
                    v["marge_pct"] = marge_pct(v["prix"], tva or 0, f["cout_ht"])
    return f


def _ensemble(pb_name) -> dict:
    """Les composants d'un ensemble, avec stock au magasin, coût et exemplaires assemblables."""
    pb = frappe.get_doc(PB, pb_name)
    comp, cout = [], 0.0
    for l in pb.items:
        it = frappe.db.get_value("Item", l.item_code, ["item_name", "is_stock_item", "valuation_rate", "last_purchase_rate"], as_dict=True) or {}
        stock = flt(frappe.db.get_value("Bin", {"item_code": l.item_code, "warehouse": E.magasin()}, "actual_qty"), 3)
        c = _cout(frappe._dict(name=l.item_code, valuation_rate=it.get("valuation_rate"), last_purchase_rate=it.get("last_purchase_rate")))
        cout += c * flt(l.qty)
        comp.append({"code": l.item_code, "nom": it.get("item_name"), "qte": flt(l.qty, 3), "stock_magasin": stock,
                     "cout_ht_unitaire": c or None, "is_stock_item": cint(it.get("is_stock_item"))})
    return {"actif": 0 if pb.disabled else 1, "description": pb.description, "composants": comp, "cout_ht_composants": flt(cout, 3),
            "assemblables_au_magasin": E.assemblables([{"qty": c["qte"], "stock": c["stock_magasin"], "is_stock_item": c["is_stock_item"]} for c in comp])}


@frappe.whitelist()
def articles_a_completer(critere, groupe=None, limite=50):
    """Les articles actifs à qui il manque quelque chose : sans_image, sans_prix (Vente standard), sans_description,
    sans_tva, sans_marque, sans_prix_achat, en_rupture (aucun stock), en_rupture_sans_image."""
    _acces()
    if critere not in CRITERES:
        frappe.throw(_("Critère « {0} » inconnu : {1}.").format(critere, ", ".join(CRITERES)))
    cond = {"sans_image": "ifnull(i.image, '') = ''",
            "sans_prix": "not exists (select 1 from `tabItem Price` p where p.item_code = i.name and p.price_list = %(vente)s)",
            "sans_prix_achat": "not exists (select 1 from `tabItem Price` p where p.item_code = i.name and p.price_list = %(achat)s)",
            "sans_description": "(ifnull(i.description, '') = '' or i.description = i.item_name)",
            "sans_tva": "not exists (select 1 from `tabItem Tax` t where t.parent = i.name)",
            "sans_marque": "ifnull(i.brand, '') = ''",
            "en_rupture": "i.is_stock_item = 1 and ifnull((select sum(actual_qty) from tabBin b where b.item_code = i.name), 0) <= 0",
            "en_rupture_sans_image": "i.is_stock_item = 1 and ifnull(i.image, '') = '' and ifnull((select sum(actual_qty) from tabBin b where b.item_code = i.name), 0) <= 0"}[critere]
    vals = {"vente": LISTE_VENTE, "achat": LISTE_ACHAT, "groupe": groupe, "limite": min(cint(limite) or 50, 200)}
    g = " and i.item_group = %(groupe)s" if groupe else ""
    total = frappe.db.sql(f"select count(*) from tabItem i where i.disabled = 0 and i.has_variants = 0 and {cond}{g}", vals)[0][0]
    rows = frappe.db.sql(f"""select i.name as code, i.item_name as nom, i.item_group as groupe from tabItem i
                             where i.disabled = 0 and i.has_variants = 0 and {cond}{g} order by i.item_group, i.item_name limit %(limite)s""",
                         vals, as_dict=True)
    return {"critere": critere, "total": cint(total), "articles": rows}


@frappe.whitelist()
def mes_modifications(limite=20):
    """Les derniers articles créés ou modifiés par l'assistant."""
    _acces()
    rows = frappe.db.sql("""select name as code, item_name as nom, item_group as groupe, disabled as desactive, creation, modified, owner
                            from tabItem where owner = %s or modified_by = %s order by modified desc limit %s""",
                         (frappe.session.user, frappe.session.user, min(cint(limite) or 20, 100)), as_dict=True)
    return [{"code": r.code, "nom": r.nom, "groupe": r.groupe, "desactive": cint(r.desactive),
             "action": _("créé") if r.owner == frappe.session.user else _("modifié"),
             "quand": str(r.modified)[:16], "url": _url(r.code)} for r in rows]


# ── Écriture ─────────────────────────────────────────────────────────────────

def _poser_prix(doc, prix: dict, listes: dict) -> dict:
    """Crée ou met à jour l'Item Price de chaque liste ; renvoie {liste: {prix, marge_pct}}."""
    tva, cout, out = _taux_tva(doc) or 0, _cout(doc), {}
    for liste, montant in prix.items():
        info = listes[liste]
        existant = frappe.db.get_value("Item Price", {"item_code": doc.name, "price_list": liste}, "name")
        if existant:
            ip = frappe.get_doc("Item Price", existant)
            ip.price_list_rate = flt(montant, 3)
            ip.flags.ignore_permissions = True
            ip.save()
        else:
            ip = frappe.get_doc({"doctype": "Item Price", "item_code": doc.name, "price_list": liste, "selling": info["selling"],
                                 "buying": info["buying"], "currency": info["devise"], "uom": doc.sales_uom or doc.stock_uom,
                                 "price_list_rate": flt(montant, 3)})
            ip.flags.ignore_permissions = True
            ip.insert()
        out[liste] = {"prix": flt(montant, 3), "marge_pct": marge_pct(montant, tva, cout) if info["selling"] else None}
    return out


@frappe.whitelist(methods=["POST"])
def creer_article(code, nom, groupe, unite="Pièce", taux_tva=19, description=None, marque=None, prix=None,
                  stock_suivi=1, code_barres=None, volume_m3=None):
    """Crée un article simple (ni modèle ni variante) : code définitif, groupe feuille, TVA 7 ou 19, prix par liste.
    Sortie principale au magasin ; pas de synchronisation au site web."""
    _acces()
    groupes = chemins_groupes(_groupes())
    listes = _listes()
    erreurs = verifier_article(code, nom, groupe, unite, taux_tva, marque, set(groupes), set(_unites()), set(_marques()))
    if code and frappe.db.exists("Item", code):
        erreurs.append(_("Le code « {0} » existe déjà : fiche_article pour le voir, modifier_article pour le changer.").format(code))
    try:
        prix = lire_prix(prix)
    except ValueError as e:
        erreurs.append(str(e))
        prix = {}
    erreurs += verifier_prix(prix, listes)
    if erreurs:
        frappe.throw(" ".join(erreurs))
    doc = frappe.get_doc({
        "doctype": "Item", "item_code": code, "item_name": nom.strip(), "item_group": groupe, "stock_uom": unite,
        "description": (description or nom).strip(), "brand": marque or None, "is_stock_item": 1 if cint(stock_suivi) else 0,
        "include_item_in_manufacturing": 0, "custom_sync_avec_woocommerce": 0, "custom_volume_m3": _nombre(volume_m3) or 0,
        "item_defaults": [{"company": CD.COMPANY, "default_warehouse": MAGASIN}],
        "taxes": [{"item_tax_template": TAUX_TVA[_entier(taux_tva)]}],
        "barcodes": [{"barcode": str(code_barres).strip()}] if code_barres else []})
    doc.flags.ignore_permissions = True
    doc.insert()
    _commenter(doc, _("Article créé"))
    posés = _poser_prix(doc, prix, listes) if prix else {}
    a_faire = [_("image (image_article)")] + ([] if LISTE_VENTE in prix else [_("prix de vente (definir_prix)")])
    return dict(fiche_article(doc.name), cree=True, prix_poses=posés, a_faire=a_faire)


@frappe.whitelist(methods=["POST"])
def modifier_article(code, nom=None, description=None, groupe=None, marque=None, taux_tva=None, code_barres=None,
                     volume_m3=None, description_web_courte=None, description_web_longue=None, desactive=None,
                     rupture_site_web=None, sync_site_web=None, motif=None):
    """Modifie un article, seulement les champs fournis. desactive=1 exige un motif ; sync_site_web=1 exige une image."""
    _acces()
    doc = _article(code)
    valeurs = {"nom": nom, "description": description, "groupe": groupe, "marque": marque, "volume_m3": volume_m3,
               "description_web_courte": description_web_courte, "description_web_longue": description_web_longue,
               "desactive": desactive, "rupture_site_web": rupture_site_web, "sync_site_web": sync_site_web}
    chg = changements(valeurs, doc.as_dict())
    noms = {champ: param for param, champ in CHAMPS.items()}
    erreurs, modifie = [], [noms[c] for c in chg]
    if "item_group" in chg and chg["item_group"] not in chemins_groupes(_groupes()):
        erreurs.append(_("Groupe « {0} » inconnu ou groupe parent : choisir un groupe feuille renvoyé par groupes_articles.").format(chg["item_group"]))
    if "brand" in chg and chg["brand"] not in _marques():
        erreurs.append(_("Marque « {0} » inconnue (references).").format(chg["brand"]))
    if chg.get("disabled") == 1 and len((motif or "").strip()) < 5:
        erreurs.append(_("Désactiver un article demande un motif (cinq caractères au moins)."))
    if chg.get("custom_sync_avec_woocommerce") == 1 and not doc.image:
        erreurs.append(_("Pas d'image : la synchronisation au site web est impossible (image_article d'abord)."))
    if taux_tva is not None and taux_tva != "":
        t = _entier(taux_tva)
        if t not in TAUX_TVA:
            erreurs.append(_("Taux de TVA « {0} » : 7 ou 19.").format(taux_tva))
        elif t != _taux_tva(doc):
            doc.set("taxes", [{"item_tax_template": TAUX_TVA[t]}])
            modifie.append("taux_tva")
    if code_barres:
        cb = str(code_barres).strip()
        if cb not in [b.barcode for b in doc.get("barcodes") or []]:
            doc.append("barcodes", {"barcode": cb})
            modifie.append("code_barres")
    if erreurs:
        frappe.throw(" ".join(erreurs))
    if not modifie:
        return dict(fiche_article(doc.name), modifie=[], message=_("Rien à changer : la fiche a déjà ces valeurs."))
    for champ, v in chg.items():
        doc.set(champ, v)
    doc.flags.ignore_permissions = True
    doc.save()
    _commenter(doc, _("Modifié ({0}){1}").format(", ".join(modifie), (" — " + motif.strip()) if (motif or "").strip() else ""))
    r = dict(fiche_article(doc.name), modifie=modifie)
    if doc.get("custom_sync_avec_woocommerce") and {"item_name", "description", "custom_web_short_description", "custom_web_long_description"} & set(chg):
        r["note"] = _("Article synchronisé avec le site web : nom et descriptions y repartent à la synchronisation de nuit.")
    return r


@frappe.whitelist(methods=["POST"])
def definir_prix(code, prix):
    """Pose ou met à jour les prix d'un article : {"Vente standard": 750, "Compte Pro": 600, "Achat standard": 126.5}.
    Vente = TTC, achat = HT. Renvoie la marge de chaque prix de vente."""
    _acces()
    doc = _article(code)
    listes = _listes()
    try:
        prix = lire_prix(prix)
    except ValueError as e:
        frappe.throw(str(e))
    if not prix:
        frappe.throw(_("Aucun prix fourni."))
    erreurs = verifier_prix(prix, listes)
    if erreurs:
        frappe.throw(" ".join(erreurs))
    posés = _poser_prix(doc, prix, listes)
    _commenter(doc, _("Prix : {0}").format(", ".join("%s = %s" % (k, v["prix"]) for k, v in posés.items())))
    return {"code": doc.name, "nom": doc.item_name, "taux_tva": _taux_tva(doc), "cout_ht": _cout(doc) or None,
            "prix_poses": posés, "tous_les_prix": _prix(doc.name), "url": _url(doc.name)}


def _hote_public(hote: str) -> bool:
    try:
        infos = socket.getaddrinfo(hote, None)
    except socket.gaierror:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


@frappe.whitelist(methods=["POST"])
def image_article(code, url):
    """Télécharge une image (jpg, png, webp, 8 Mo au plus) depuis une URL publique et la met en image principale."""
    _acces()
    doc = _article(code)
    if not url_acceptable(url) or not _hote_public(urlparse(url).hostname):
        frappe.throw(_("URL refusée : il faut une adresse http(s) publique vers l'image elle-même."))
    import requests
    try:
        r = requests.get(url, timeout=20, stream=True, headers={"User-Agent": "AquaWorld-ERP/1.0"})
        r.raise_for_status()
    except requests.RequestException as e:
        frappe.throw(_("Téléchargement impossible : {0}").format(e))
    ext = TYPES_IMAGE.get((r.headers.get("Content-Type") or "").split(";")[0].strip().lower())
    if not ext:
        frappe.throw(_("Ce n'est pas une image jpg, png ou webp (type reçu : {0}).").format(r.headers.get("Content-Type")))
    contenu = r.raw.read(TAILLE_IMAGE_MAX + 1, decode_content=True)
    if len(contenu) > TAILLE_IMAGE_MAX:
        frappe.throw(_("Image trop lourde (8 Mo au plus)."))
    f = frappe.get_doc({"doctype": "File", "file_name": "%s.%s" % (re.sub(r"[^A-Za-z0-9_-]+", "_", doc.name), ext),
                        "attached_to_doctype": "Item", "attached_to_name": doc.name, "attached_to_field": "image",
                        "is_private": 0, "content": contenu})
    f.flags.ignore_permissions = True
    f.insert()
    doc.db_set("image", f.file_url)
    _commenter(doc, _("Image remplacée ({0} Ko)").format(len(contenu) // 1024))
    return {"code": doc.name, "image": _abs(f.file_url), "taille_ko": len(contenu) // 1024, "url": _url(doc.name)}


# ── Réorganiser : groupes et déplacements en lot ─────────────────────────────

@frappe.whitelist(methods=["POST"])
def creer_groupe(nom, parent):
    """Crée un groupe FEUILLE sous un groupe parent existant (un groupe qui porte déjà des articles ne peut pas devenir parent)."""
    _acces()
    nom = (nom or "").strip()
    if not nom or len(nom) > 140:
        frappe.throw(_("Nom de groupe obligatoire (140 caractères au plus)."))
    if frappe.db.exists("Item Group", nom):
        frappe.throw(_("Le groupe « {0} » existe déjà.").format(nom))
    p = frappe.db.get_value("Item Group", parent, ["is_group"], as_dict=True)
    if not p:
        frappe.throw(_("Groupe parent « {0} » inconnu.").format(parent))
    if not p.is_group:
        frappe.throw(_("« {0} » est un groupe feuille (il porte des articles) : il ne peut pas recevoir de sous-groupe. Choisir son parent.").format(parent))
    g = frappe.get_doc({"doctype": "Item Group", "item_group_name": nom, "parent_item_group": parent, "is_group": 0})
    g.flags.ignore_permissions = True
    g.insert()
    return {"groupe": g.name, "chemin": chemins_groupes(_groupes()).get(g.name), "articles": 0}


@frappe.whitelist(methods=["POST"])
def deplacer_articles(codes, groupe):
    """Range des articles (liste de codes, 100 au plus) dans un groupe feuille. Chaque fiche est enregistrée une à une :
    la réponse dit lesquels ont bougé et lesquels ont été refusés (et pourquoi)."""
    _acces()
    codes = lire_codes(codes)
    if not codes or len(codes) > MAX_LOT:
        frappe.throw(_("Donner de 1 à {0} codes d'articles.").format(MAX_LOT))
    if groupe not in chemins_groupes(_groupes()):
        frappe.throw(_("Groupe « {0} » inconnu ou groupe parent : choisir un groupe feuille renvoyé par groupes_articles.").format(groupe))
    deplaces, deja, refus = [], [], {}
    for code in codes:
        if not frappe.db.exists("Item", code):
            refus[code] = _("introuvable")
            continue
        doc = frappe.get_doc("Item", code)
        if doc.item_group == groupe:
            deja.append(code)
            continue
        frappe.db.savepoint("deplacer")
        try:
            ancien = doc.item_group
            doc.item_group = groupe
            doc.flags.ignore_permissions = True
            doc.save()
            _commenter(doc, _("Déplacé de « {0} » vers « {1} »").format(ancien, groupe))
            deplaces.append(code)
        except Exception as e:                      # une fiche ancienne mal formée ne bloque pas le lot
            frappe.db.rollback(save_point="deplacer")
            refus[code] = str(e)[:200]
    return {"groupe": groupe, "deplaces": deplaces, "deja_dans_le_groupe": deja, "refuses": refus}


# ── Ensembles de produits (Product Bundle) ───────────────────────────────────

def _verifier_composants(composants: dict, parent: str | None = None) -> list[str]:
    e = []
    for code in composants:
        if code == parent:
            e.append(_("Un ensemble ne peut pas se contenir lui-même."))
        elif not frappe.db.exists("Item", code):
            e.append(_("Composant « {0} » introuvable.").format(code))
        elif frappe.db.exists(PB, {"new_item_code": code}):
            e.append(_("« {0} » est lui-même un ensemble : il ne peut pas être composant.").format(code))
    return e


def _lignes_ensemble(composants: dict) -> list[dict]:
    return [{"item_code": c, "qty": q, "uom": frappe.db.get_value("Item", c, "stock_uom"),
             "description": frappe.db.get_value("Item", c, "description") or frappe.db.get_value("Item", c, "item_name")}
            for c, q in composants.items()]


@frappe.whitelist(methods=["POST"])
def creer_ensemble(code, composants, nom=None, groupe=None, prix=None, description=None, taux_tva=19):
    """Crée un ensemble de produits : sur un article parent existant NON stocké (code seul), ou sur un article créé ici
    (code + nom + groupe feuille). composants = {"CODE": quantité, …}. prix = prix par liste du parent (vente TTC)."""
    _acces()
    erreurs = []
    try:
        composants = lire_composants(composants)
    except ValueError as e:
        erreurs.append(str(e))
        composants = {}
    if not composants:
        erreurs.append(_("Un ensemble a au moins un composant avec une quantité."))
    erreurs += _verifier_composants(composants, code)
    listes = _listes()
    try:
        prix = lire_prix(prix)
    except ValueError as e:
        erreurs.append(str(e))
        prix = {}
    erreurs += verifier_prix(prix, listes)
    existe = bool(code) and frappe.db.exists("Item", code)
    if existe:
        it = frappe.db.get_value("Item", code, ["is_stock_item", "has_variants"], as_dict=True)
        if it.is_stock_item:
            erreurs.append(_("« {0} » est suivi en stock : l'article parent d'un ensemble est NON stocké (ce sont ses composants qui le sont).").format(code))
        if it.has_variants:
            erreurs.append(_("« {0} » est un modèle à variantes : impossible.").format(code))
        if frappe.db.exists(PB, {"new_item_code": code}):
            erreurs.append(_("« {0} » a déjà un ensemble : modifier_ensemble.").format(code))
    else:
        erreurs += verifier_article(code, nom, groupe, "Pièce", taux_tva, None, set(chemins_groupes(_groupes())), {"Pièce"}, set())
    if erreurs:
        frappe.throw(" ".join(erreurs))
    if not existe:
        item = frappe.get_doc({"doctype": "Item", "item_code": code, "item_name": nom.strip(), "item_group": groupe, "stock_uom": "Pièce",
                               "description": (description or nom).strip(), "is_stock_item": 0, "is_sales_item": 1,
                               "include_item_in_manufacturing": 0, "custom_sync_avec_woocommerce": 0,
                               "item_defaults": [{"company": CD.COMPANY, "default_warehouse": MAGASIN}],
                               "taxes": [{"item_tax_template": TAUX_TVA[_entier(taux_tva)]}]})
        item.flags.ignore_permissions = True
        item.insert()
        _commenter(item, _("Article parent d'ensemble créé"))
    else:
        item = frappe.get_doc("Item", code)
    _creer_bundle(item, composants, description)
    posés = _poser_prix(item, prix, listes) if prix else {}
    return dict(fiche_article(item.name), cree=True, prix_poses=posés)


def _creer_bundle(item, composants: dict, description=None):
    pb = frappe.get_doc({"doctype": PB, "new_item_code": item.name, "description": (description or "")[:140] or None,
                         "items": _lignes_ensemble(composants)})
    pb.flags.ignore_permissions = True
    pb.insert()
    _commenter(item, _("Ensemble créé ({0} composants)").format(len(composants)))


@frappe.whitelist(methods=["POST"])
def modifier_ensemble(code, composants=None, ajouter=None, retirer=None, actif=None, description=None):
    """Modifie l'ensemble d'un article parent : composants = nouvelle liste complète {"CODE": qté}, ou ajouter = {"CODE": qté}
    (ajoute ou change la quantité) et retirer = liste de codes ; actif = 1/0 ; description."""
    _acces()
    item = _article(code)
    pb_name = frappe.db.get_value(PB, {"new_item_code": item.name}, "name")
    if not pb_name:
        frappe.throw(_("« {0} » n'a pas d'ensemble : creer_ensemble.").format(code))
    pb = frappe.get_doc(PB, pb_name)
    actuels = {l.item_code: flt(l.qty, 6) for l in pb.items}
    try:
        nouveaux = lire_composants(composants) if composants else None
        plus = lire_composants(ajouter)
    except ValueError as e:
        frappe.throw(str(e))
    moins = lire_codes(retirer)
    cible = dict(nouveaux) if nouveaux is not None else dict(actuels)
    cible.update(plus)
    for c in moins:
        cible.pop(c, None)
    modifie = []
    if cible != actuels:
        if not cible:
            frappe.throw(_("Un ensemble garde au moins un composant."))
        erreurs = _verifier_composants(cible, item.name)
        if erreurs:
            frappe.throw(" ".join(erreurs))
        pb.set("items", _lignes_ensemble(cible))
        modifie.append("composants")
    if description is not None and description != "" and (pb.description or "") != description[:140]:
        pb.description = description[:140]
        modifie.append("description")
    if actif is not None and actif != "":
        a = 0 if cint(actif) else 1
        if a != cint(pb.disabled):
            pb.disabled = a
            modifie.append("actif")
    if not modifie:
        return dict(fiche_article(item.name), modifie=[], message=_("Rien à changer."))
    pb.flags.ignore_permissions = True
    pb.save()
    _commenter(item, _("Ensemble modifié ({0})").format(", ".join(modifie)))
    return dict(fiche_article(item.name), modifie=modifie)


# ── Modèles et variantes ─────────────────────────────────────────────────────

def _attributs() -> dict:
    """{attribut: {"valeurs": [...], "numerique": 0/1}}"""
    out = {a.name: {"valeurs": [], "numerique": cint(a.numeric_values)} for a in frappe.get_all("Item Attribute", fields=["name", "numeric_values"])}
    for v in frappe.get_all("Item Attribute Value", fields=["parent", "attribute_value", "abbr"], order_by="parent, idx"):
        if v.parent in out:
            out[v.parent]["valeurs"].append({"valeur": v.attribute_value, "abrege": v.abbr})
    return out


@frappe.whitelist()
def attributs_articles():
    """Les attributs de variantes (Taille, Couleur…) et leurs valeurs possibles, avec l'abrégé qui compose le code."""
    _acces()
    return _attributs()


def _verifier_attributs(attributs) -> tuple[list[str], list[str]]:
    noms = lire_codes(attributs)
    connus = _attributs()
    return noms, [_("Attribut « {0} » inconnu (attributs_articles).").format(a) for a in noms if a not in connus] + \
        ([_("Au moins un attribut.")] if not noms else [])


@frappe.whitelist(methods=["POST"])
def creer_modele(code, nom, groupe, attributs, unite="Pièce", taux_tva=19, description=None, marque=None, stock_suivi=1):
    """Crée un article MODÈLE (parent de variantes) avec ses attributs (liste de noms, attributs_articles).
    Un modèle ne se vend pas tel quel : ce sont ses variantes qui portent prix et stock."""
    _acces()
    erreurs = verifier_article(code, nom, groupe, unite, taux_tva, marque, set(chemins_groupes(_groupes())), set(_unites()), set(_marques()))
    if code and frappe.db.exists("Item", code):
        erreurs.append(_("Le code « {0} » existe déjà.").format(code))
    noms, e = _verifier_attributs(attributs)
    erreurs += e
    if erreurs:
        frappe.throw(" ".join(erreurs))
    doc = frappe.get_doc({
        "doctype": "Item", "item_code": code, "item_name": nom.strip(), "item_group": groupe, "stock_uom": unite,
        "description": (description or nom).strip(), "brand": marque or None, "is_stock_item": 1 if cint(stock_suivi) else 0,
        "has_variants": 1, "variant_based_on": "Item Attribute", "attributes": [{"attribute": a} for a in noms],
        "include_item_in_manufacturing": 0, "custom_sync_avec_woocommerce": 0,
        "item_defaults": [{"company": CD.COMPANY, "default_warehouse": MAGASIN}],
        "taxes": [{"item_tax_template": TAUX_TVA[_entier(taux_tva)]}]})
    doc.flags.ignore_permissions = True
    doc.insert()
    _commenter(doc, _("Modèle créé (attributs : {0})").format(", ".join(noms)))
    return dict(fiche_article(doc.name), cree=True, a_faire=[_("créer ses variantes (creer_variante)")])


@frappe.whitelist(methods=["POST"])
def rendre_modele(code, attributs):
    """Transforme un article normal en MODÈLE à variantes, avec ses attributs. Ses prix et son stock restent sur lui ;
    les variantes créées ensuite auront les leurs."""
    _acces()
    doc = _article(code)
    erreurs = []
    if doc.has_variants:
        erreurs.append(_("« {0} » est déjà un modèle.").format(code))
    if doc.variant_of:
        erreurs.append(_("« {0} » est une variante de {1} : impossible.").format(code, doc.variant_of))
    if frappe.db.exists(PB, {"new_item_code": doc.name}):
        erreurs.append(_("« {0} » est un ensemble de produits : impossible.").format(code))
    if doc.stock_ledger_created():                   # règle ERPNext : plus de changement de nature après un mouvement de stock
        erreurs.append(_("« {0} » a déjà eu des mouvements de stock : ERPNext interdit d'en faire un modèle. Créer un nouveau modèle "
                         "(creer_modele) et ses variantes, puis désactiver l'ancien article quand son stock est écoulé.").format(code))
    noms, e = _verifier_attributs(attributs)
    erreurs += e
    if erreurs:
        frappe.throw(" ".join(erreurs))
    doc.has_variants = 1
    doc.variant_based_on = "Item Attribute"
    doc.set("attributes", [{"attribute": a} for a in noms])
    doc.flags.ignore_permissions = True
    doc.save()
    _commenter(doc, _("Devenu modèle (attributs : {0})").format(", ".join(noms)))
    return dict(fiche_article(doc.name), modifie=["modele", "attributs"], a_faire=[_("créer ses variantes (creer_variante)")])


@frappe.whitelist(methods=["POST"])
def creer_variante(modele, valeurs, code=None, prix=None, code_barres=None, stock_suivi=None, composants=None, description=None):
    """Crée une variante d'un modèle : valeurs = {"Couleur": "Noir", "Taille": "Grand"} (une par attribut du modèle).
    Code composé par ERPNext (modèle + abrégés) sauf s'il est donné ; NOM et description composés par la règle du site
    (Server Script « Generation Description Variante » : « nom du modèle, valeur 1, valeur 2 »). La variante hérite du
    modèle (groupe, unité, TVA, sortie principale, suivi de stock…) ; ses prix sont les siens (prix).

    Montage « osmoseurs domestiques » : le MODÈLE est suivi en stock, chaque variante est un article NON stocké qui est un
    ENSEMBLE contenant le modèle lui-même + membrane + accessoires. `composants` = {"AP-C": 1, "M-75-DF": 1, …} fait
    tout en une fois : variante non stockée + ensemble. `stock_suivi` seul (0/1) force la nature sans ensemble."""
    _acces()
    from erpnext.controllers.item_variant import copy_attributes_to_variant, find_variant, make_variant_item_code, validate_item_variant_attributes
    tpl = _article(modele)
    if not tpl.has_variants:
        frappe.throw(_("« {0} » n'est pas un modèle : rendre_modele d'abord, ou creer_modele.").format(modele))
    try:
        valeurs = json.loads(valeurs) if isinstance(valeurs, str) else (valeurs or {})
        prix = lire_prix(prix)
    except ValueError as e:
        frappe.throw(str(e))
    if not isinstance(valeurs, dict):
        frappe.throw(_("Les valeurs s'écrivent {\"Attribut\": \"valeur\", …}."))
    attendus = [a.attribute for a in tpl.attributes]
    erreurs = [_("Attribut « {0} » absent du modèle (attendus : {1}).").format(a, ", ".join(attendus)) for a in valeurs if a not in attendus]
    erreurs += [_("Valeur manquante pour l'attribut « {0} ».").format(a) for a in attendus if not str(valeurs.get(a) or "").strip()]
    erreurs += verifier_prix(prix, _listes())
    if code and (not RX_CODE.match(code) or frappe.db.exists("Item", code)):
        erreurs.append(_("Code « {0} » invalide ou déjà pris.").format(code))
    try:
        composants = lire_composants(composants)
    except ValueError as e:
        erreurs.append(str(e))
        composants = {}
    erreurs += _verifier_composants(composants, code)
    if composants and stock_suivi is not None and stock_suivi != "" and cint(stock_suivi):
        erreurs.append(_("Une variante-ensemble n'est pas suivie en stock (ce sont ses composants qui le sont) : stock_suivi=0 ou rien."))
    if erreurs:
        frappe.throw(" ".join(erreurs))
    args = {a: str(valeurs[a]).strip() for a in attendus}
    try:
        validate_item_variant_attributes(tpl, args)
    except Exception as e:
        frappe.throw(_("Valeur d'attribut refusée : {0}").format(e))
    existante = find_variant(tpl.name, args)       # get_variant exige une permission que l'utilisateur technique n'a pas
    if existante:
        frappe.throw(_("Cette combinaison existe déjà : {0}.").format(existante))
    v = frappe.new_doc("Item")
    v.variant_based_on = "Item Attribute"
    v.set("attributes", [{"attribute": a, "attribute_value": args[a]} for a in attendus])
    copy_attributes_to_variant(tpl, v)
    v.custom_sync_avec_woocommerce = 0
    if composants:
        v.is_stock_item = 0
    elif stock_suivi is not None and stock_suivi != "":
        v.is_stock_item = 1 if cint(stock_suivi) else 0
    make_variant_item_code(tpl.item_code, tpl.item_name, v)
    if code:
        v.item_code = code.strip()
    if not v.item_code:
        frappe.throw(_("ERPNext n'a pas pu composer le code (abrégés manquants) : donner `code`."))
    v.description = v.item_name
    if code_barres:
        v.append("barcodes", {"barcode": str(code_barres).strip()})
    v.flags.ignore_permissions = True
    v.insert()
    _commenter(v, _("Variante de {0} créée ({1})").format(tpl.name, ", ".join("%s = %s" % kv for kv in args.items())))
    if composants:
        _creer_bundle(v, composants, description)
    posés = _poser_prix(v, prix, _listes()) if prix else {}
    return dict(fiche_article(v.name), cree=True, prix_poses=posés, a_faire=[_("image (image_article)")] + ([] if LISTE_VENTE in prix else [_("prix de vente (definir_prix)")]))


# ── Accès générique aux champs de la fiche ───────────────────────────────────

def _champs_meta() -> list[dict]:
    return [{"fieldname": f.fieldname, "label": f.label, "fieldtype": f.fieldtype, "options": f.options, "reqd": cint(f.reqd),
             "read_only": cint(f.read_only), "hidden": cint(f.hidden), "description": f.description}
            for f in frappe.get_meta("Item").fields if f.fieldtype in TYPES_LISIBLES]


@frappe.whitelist()
def champs_article():
    """Tous les champs simples de la fiche Article : nom technique, libellé, type, valeurs possibles, modifiable ou non
    (et par quel outil sinon)."""
    _acces()
    out = []
    for c in _champs_meta():
        m = modifiable(c)
        out.append({"champ": c["fieldname"], "libelle": c["label"], "type": c["fieldtype"],
                    "options": (c["options"] if c["fieldtype"] in ("Select", "Link") else None),
                    "modifiable": m, **({} if m else {"par": VERS_OUTIL.get(c["fieldname"], _("lecture seule"))})})
    return out


@frappe.whitelist()
def lire_champs(code, champs=None):
    """Les valeurs des champs simples d'un article (tous, ou ceux demandés : liste de noms techniques de champs_article)."""
    _acces()
    doc = _article(code)
    voulus = set(lire_codes(champs))
    meta = {c["fieldname"]: c for c in _champs_meta()}
    inconnus = [v for v in voulus if v not in meta]
    if inconnus:
        frappe.throw(_("Champs inconnus : {0} (champs_article).").format(", ".join(inconnus)))
    return {"code": doc.name, "champs": [{"champ": f, "libelle": c["label"], "valeur": doc.get(f), "modifiable": modifiable(c)}
                                         for f, c in meta.items() if (not voulus or f in voulus) and (voulus or doc.get(f) not in (None, "", 0))]}


@frappe.whitelist(methods=["POST"])
def modifier_champs(code, valeurs, motif=None):
    """Modifie n'importe quels champs simples de la fiche : valeurs = {"champ technique": valeur, …} (champs_article dit
    lesquels sont modifiables). Les champs protégés (code, nature de stock, groupe, image, désactivation, synchro site…)
    sont refusés et renvoient vers leur outil."""
    _acces()
    doc = _article(code)
    valeurs = json.loads(valeurs) if isinstance(valeurs, str) else (valeurs or {})
    if not isinstance(valeurs, dict) or not valeurs:
        frappe.throw(_("Les valeurs s'écrivent {\"champ\": valeur, …}."))
    meta = {c["fieldname"]: c for c in _champs_meta()}
    erreurs, chg = [], {}
    for champ, v in valeurs.items():
        c = meta.get(champ)
        if not c:
            erreurs.append(_("Champ « {0} » inconnu (champs_article).").format(champ))
        elif not modifiable(c):
            erreurs.append(_("Champ « {0} » protégé : {1}.").format(champ, VERS_OUTIL.get(champ, _("lecture seule"))))
        else:
            try:
                nv = coercer(c, v)
            except ValueError as e:
                erreurs.append(str(e))
                continue
            if c["fieldtype"] == "Link" and nv and not frappe.db.exists(c["options"], nv):
                erreurs.append(_("« {0} » : « {1} » n'existe pas dans {2}.").format(c["label"], nv, c["options"]))
            elif c["reqd"] and nv in (None, ""):
                erreurs.append(_("« {0} » est obligatoire.").format(c["label"]))
            elif doc.get(champ) != nv:
                chg[champ] = nv
    if erreurs:
        frappe.throw(" ".join(erreurs))
    if not chg:
        return {"code": doc.name, "modifie": [], "message": _("Rien à changer : la fiche a déjà ces valeurs.")}
    for champ, nv in chg.items():
        doc.set(champ, nv)
    doc.flags.ignore_permissions = True
    doc.save()
    _commenter(doc, _("Champs modifiés ({0}){1}").format(", ".join(meta[c]["label"] for c in chg), (" — " + motif.strip()) if (motif or "").strip() else ""))
    return {"code": doc.name, "modifie": list(chg), "valeurs": {c: doc.get(c) for c in chg}, "url": _url(doc.name)}


# ── Catalogue du plugin ──────────────────────────────────────────────────────

_PRIX = {"additionalProperties": {"type": "number"}}
#: Les outils, décrits une seule fois : {nom: (résumé, {param: [type, description(, complément de schéma)]}, POST ?, requis)}.
OUTILS = {
    "moi": ("Qui je suis, repères de la base article et ce que je sais faire", {}, False, None),
    "references": ("Unités, marques, listes de prix, taux de TVA et entrepôts : les valeurs exactes à employer", {}, False, None),
    "groupes_articles": ("Groupes d'articles feuilles (ceux qu'un article peut porter) avec leur chemin", {
        "texte": ["string", "Filtre sur le chemin du groupe (vide = tous)"]}, False, []),
    "entrepots": ("Les entrepôts, avec le nombre d'articles en stock et la quantité totale dans chacun", {}, False, None),
    "chercher_article": ("Chercher des articles : mots (code, nom, description, marque, ordre libre) ou code-barres, et/ou filtres groupe, entrepôt, en rupture, sans image", {
        "texte": ["string", "Mots à chercher, même partiels (facultatif si un filtre est donné)"], "groupe": ["string", "Limiter à un groupe exact"],
        "entrepot": ["string", "Limiter aux articles en stock dans cet entrepôt (references) ; la réponse donne la quantité qui s'y trouve"],
        "en_rupture": ["integer", "1 pour ne garder que les articles stockés sans aucun stock"],
        "sans_image": ["integer", "1 pour ne garder que les articles sans image"],
        "inclure_desactives": ["integer", "1 pour voir aussi les articles désactivés"], "limite": ["integer", "Nombre de résultats, 20 par défaut (100 au plus)"],
    }, False, []),
    "fiche_article": ("Fiche complète d'un article : champs, TVA, prix et marges par liste, stock par entrepôt, image, variantes", {
        "code": ["string", "Code exact de l'article (renvoyé par chercher_article)"]}, False, None),
    "articles_a_completer": ("Articles actifs à traiter : sans_image, sans_prix, sans_prix_achat, sans_description, sans_tva, sans_marque, en_rupture, en_rupture_sans_image (avec le total)", {
        "critere": ["string", "sans_image, sans_prix, sans_prix_achat, sans_description, sans_tva, sans_marque, en_rupture ou en_rupture_sans_image"],
        "groupe": ["string", "Limiter à un groupe exact"], "limite": ["integer", "Nombre, 50 par défaut"]}, False, ["critere"]),
    "mes_modifications": ("Derniers articles créés ou modifiés par l'assistant", {"limite": ["integer", "Nombre, 20 par défaut"]}, False, []),
    "creer_article": ("Créer un article simple : code définitif, nom, groupe feuille, unité, TVA 7 ou 19, prix par liste (vente TTC, achat HT). Jamais un modèle ni une variante", {
        "code": ["string", "Code de l'article, définitif (ex. FRP-1465), dans l'esprit des codes voisins du groupe"],
        "nom": ["string", "Nom commercial, 140 caractères au plus"],
        "groupe": ["string", "Groupe feuille exact (groupes_articles)"],
        "unite": ["string", "Unité de stock exacte (references), Pièce par défaut"],
        "taux_tva": ["integer", "7 ou 19 (19 par défaut)"],
        "description": ["string", "Description (le nom si vide)"], "marque": ["string", "Marque exacte (references), facultative"],
        "prix": ["object", "Prix par liste : {\"Vente standard\": 750, \"Compte Pro\": 600, \"Achat standard\": 126.5}", _PRIX],
        "stock_suivi": ["integer", "1 (défaut) si l'article a un stock, 0 pour un service ou une prestation"],
        "code_barres": ["string", "Code-barres, facultatif"], "volume_m3": ["number", "Volume unitaire en m³, facultatif"],
    }, True, ["code", "nom", "groupe"]),
    "modifier_article": ("Modifier un article, seulement les champs fournis. desactive=1 exige un motif ; sync_site_web=1 exige une image", {
        "code": ["string", "Code exact"], "nom": ["string", "Nouveau nom"], "description": ["string", "Nouvelle description"],
        "groupe": ["string", "Nouveau groupe feuille"], "marque": ["string", "Nouvelle marque"], "taux_tva": ["integer", "7 ou 19"],
        "code_barres": ["string", "Code-barres à ajouter"], "volume_m3": ["number", "Volume unitaire en m³"],
        "description_web_courte": ["string", "Description courte pour le site web"], "description_web_longue": ["string", "Description longue (HTML) pour le site web"],
        "desactive": ["integer", "1 pour désactiver (avec motif), 0 pour réactiver"],
        "rupture_site_web": ["integer", "1 pour afficher l'article en rupture sur le site, 0 pour revenir au stock réel"],
        "sync_site_web": ["integer", "1 pour synchroniser l'article avec le site web (image obligatoire), 0 pour arrêter"],
        "motif": ["string", "Pourquoi (obligatoire pour désactiver, gardé en commentaire)"],
    }, True, ["code"]),
    "definir_prix": ("Poser ou changer les prix d'un article par liste (vente TTC, achat HT) ; renvoie les marges", {
        "code": ["string", "Code exact"],
        "prix": ["object", "{\"Vente standard\": 750, \"Compte Pro\": 600, \"Achat standard\": 126.5} — seulement les listes à changer", _PRIX],
    }, True, ["code", "prix"]),
    "image_article": ("Mettre l'image principale d'un article depuis une URL publique (jpg, png, webp, 8 Mo au plus)", {
        "code": ["string", "Code exact"], "url": ["string", "Adresse http(s) publique de l'image elle-même"]}, True, None),
    "creer_groupe": ("Créer un groupe d'articles feuille sous un groupe parent existant", {
        "nom": ["string", "Nom du nouveau groupe"], "parent": ["string", "Groupe parent exact (un groupe qui a des sous-groupes)"]}, True, None),
    "deplacer_articles": ("Ranger des articles (1 à 100 codes) dans un groupe feuille ; la réponse dit lesquels ont bougé ou été refusés", {
        "codes": ["array", "Codes des articles à déplacer", {"items": {"type": "string"}}], "groupe": ["string", "Groupe feuille exact de destination"]}, True, None),
    "creer_ensemble": ("Créer un ensemble de produits (Product Bundle) : parent existant NON stocké (code seul) ou nouvel article (code + nom + groupe), composants {\"CODE\": qté}", {
        "code": ["string", "Code de l'article parent (existant non stocké, ou nouveau)"],
        "composants": ["object", "{\"CODE\": quantité, …} — les composants et leurs quantités", _PRIX],
        "nom": ["string", "Nom du nouvel article parent (si créé ici)"], "groupe": ["string", "Groupe feuille du nouvel article parent (si créé ici)"],
        "prix": ["object", "Prix par liste du parent, vente TTC : {\"Vente standard\": 250}", _PRIX],
        "description": ["string", "Description de l'ensemble (140 caractères)"], "taux_tva": ["integer", "7 ou 19 pour le nouvel article parent (19 par défaut)"],
    }, True, ["code", "composants"]),
    "attributs_articles": ("Les attributs de variantes (Taille, Couleur…) et leurs valeurs possibles", {}, False, None),
    "creer_modele": ("Créer un article MODÈLE (parent de variantes) avec ses attributs ; ses variantes porteront prix et stock", {
        "code": ["string", "Code du modèle, définitif"], "nom": ["string", "Nom du modèle"], "groupe": ["string", "Groupe feuille exact"],
        "attributs": ["array", "Noms exacts des attributs (attributs_articles)", {"items": {"type": "string"}}],
        "unite": ["string", "Unité de stock, Pièce par défaut"], "taux_tva": ["integer", "7 ou 19 (19 par défaut)"],
        "description": ["string", "Description"], "marque": ["string", "Marque exacte"], "stock_suivi": ["integer", "1 (défaut) ou 0"],
    }, True, ["code", "nom", "groupe", "attributs"]),
    "rendre_modele": ("Transformer un article normal SANS mouvement de stock en modèle à variantes, avec ses attributs (ERPNext l'interdit après un mouvement)", {
        "code": ["string", "Code exact de l'article"], "attributs": ["array", "Noms exacts des attributs", {"items": {"type": "string"}}]}, True, None),
    "creer_variante": ("Créer une variante d'un modèle : valeurs {\"Attribut\": \"valeur\"} pour chaque attribut ; code composé par ERPNext sauf s'il est donné, nom composé par le site ; avec composants, la variante est un ensemble non stocké (montage osmoseurs)", {
        "modele": ["string", "Code du modèle"], "valeurs": ["object", "{\"Couleur\": \"Noir\", \"Taille\": \"Grand\"}", {"additionalProperties": {"type": "string"}}],
        "code": ["string", "Code imposé (sinon modèle + abrégés) ; le nom est toujours « nom du modèle, valeurs » (règle du site)"],
        "prix": ["object", "Prix par liste de la variante (vente TTC)", _PRIX], "code_barres": ["string", "Code-barres, facultatif"],
        "stock_suivi": ["integer", "1 ou 0 pour forcer le suivi de stock de la variante (hérité du modèle sinon)"],
        "composants": ["object", "{\"AP-C\": 1, \"M-75-DF\": 1, …} : la variante devient un article NON stocké qui est un ENSEMBLE de ces composants (modèle lui-même compris), comme les osmoseurs domestiques", _PRIX],
        "description": ["string", "Description de l'ensemble (140 caractères), si composants"],
    }, True, ["modele", "valeurs"]),
    "champs_article": ("Tous les champs simples de la fiche Article : nom technique, libellé, type, valeurs possibles, modifiable ou non", {}, False, None),
    "lire_champs": ("Lire les champs simples d'un article (tous les champs renseignés, ou ceux demandés)", {
        "code": ["string", "Code exact"], "champs": ["array", "Noms techniques des champs voulus (vide = tous les champs renseignés)", {"items": {"type": "string"}}]}, True, ["code"]),
    "modifier_champs": ("Modifier n'importe quels champs simples de la fiche (champs_article dit lesquels) ; les champs protégés renvoient vers leur outil", {
        "code": ["string", "Code exact"], "valeurs": ["object", "{\"champ technique\": valeur, …}", {}],
        "motif": ["string", "Pourquoi, gardé en commentaire (facultatif)"]}, True, ["code", "valeurs"]),
    "modifier_ensemble": ("Modifier l'ensemble d'un article parent : liste complète, ou ajouter / retirer des composants, activer / désactiver", {
        "code": ["string", "Code de l'article parent"],
        "composants": ["object", "Nouvelle liste COMPLÈTE {\"CODE\": qté} (remplace tout)", _PRIX],
        "ajouter": ["object", "{\"CODE\": qté} à ajouter ou dont changer la quantité", _PRIX],
        "retirer": ["array", "Codes des composants à retirer", {"items": {"type": "string"}}],
        "actif": ["integer", "1 pour activer l'ensemble, 0 pour le désactiver"], "description": ["string", "Nouvelle description"],
    }, True, ["code"]),
}


def outils_mcp() -> list[dict]:
    return A.outils_mcp(OUTILS)


INSTRUCTIONS = """Tu es l'assistant ARTICLES de l'ERPNext d'Aqua World & Servicing (Tunisie, traitement de l'eau, dinars TND). Tu parles français.
Tu gères la base article : chercher, lire, créer, modifier, tarifer, illustrer. Rien d'autre (pas de ventes, de stock, de comptabilité).
LA RÉPONSE D'UN OUTIL FAIT FOI : rapporte-la telle quelle (code, nom, groupe, prix, marges, lien). N'invente rien.
Avant de créer : chercher_article pour éviter un doublon (même nom, même référence fabricant), groupes_articles pour le groupe FEUILLE exact,
references pour l'unité, la marque, les listes de prix. Propose un code dans l'esprit des articles voisins du groupe (fiche_article d'un voisin).
Récapitule en une ligne (code, nom, groupe, unité, TVA, prix) et demande confirmation AVANT creer_article. Le code est définitif.
Prix de VENTE = TTC, prix d'ACHAT = HT. Les montants s'écrivent en DT avec trois décimales (750,000 DT). Si la marge rapportée est faible
ou négative, dis-le. Les listes de prix de vente : Vente standard (particuliers), Compte Pro, Pro Grand Rayon, Quincaillerie, Technicien.
Pour modifier : fiche_article d'abord, puis modifier_article avec SEULEMENT les champs qui changent ; récapitule avant d'écrire.
Désactiver demande un motif ; on ne supprime jamais, on ne renomme jamais un code, on ne touche pas au stock.
Un article synchronisé avec le site web (sync_site_web) : préviens que nom et descriptions y repartiront la nuit suivante.
Image : image_article avec l'URL publique d'une image (jamais une page). Pas d'image → pas de synchronisation au site web.
Modèles et variantes : attributs_articles donne les attributs et leurs valeurs exactes. creer_modele (nouveau parent) ou rendre_modele
(un article existant devient parent, seulement s'il n'a jamais eu de mouvement de stock : sinon ERPNext refuse, il faut un nouveau modèle),
puis creer_variante pour chaque combinaison : ERPNext compose le code (modèle + abrégés) sauf si on l'impose, et le site compose le nom
(« nom du modèle, valeur 1, valeur 2 »). Récapitule attributs et valeurs avant d'écrire ; un modèle ne se vend pas tel quel, ce sont ses
variantes qui portent prix et stock. Jamais de modification d'attributs sur une variante existante (humain dans ERPNext).
Montage « osmoseurs domestiques » (AP-C, AP-M, SP-P…) : le MODÈLE est suivi en stock (c'est lui qui est acheté et compté), chaque
variante est un article NON stocké qui est un ENSEMBLE : le modèle lui-même ×1 + la membrane + les accessoires de la configuration.
Pour reproduire ce montage : creer_modele avec stock_suivi=1, puis creer_variante avec composants={"MODELE": 1, "membrane": 1, …} pour
chaque combinaison ; fiche_article d'une variante existante du même modèle donne la liste de composants à adapter. Pour un modèle qui
existe déjà (AP-C…), creer_variante avec composants suffit.
Autres champs de la fiche (poids, garantie, délais, commande minimale, champs SEO…) : champs_article liste tout avec le nom technique,
lire_champs lit, modifier_champs écrit {"champ": valeur} ; les champs protégés (code, nature de stock, groupe, image, désactivation,
synchro site) ont leur propre outil. Annonce le libellé du champ, pas son nom technique.
articles_a_completer sert à faire le ménage (sans image, sans prix, sans TVA, en rupture, en rupture sans image…) : propose de traiter par
petits lots, article par article. chercher_article accepte des filtres sans mots (groupe, entrepôt, en_rupture, sans_image) ; entrepots
donne la liste des entrepôts et ce qu'ils contiennent.
Réorganiser : groupes_articles montre l'arbre ; creer_groupe ajoute une feuille sous un parent ; deplacer_articles range des codes dans un
groupe feuille — liste les codes concernés et demande confirmation avant, surtout au-delà de dix articles.
Ensembles de produits (packs, kits d'entretien) : fiche_article d'un parent montre ses composants, leur stock au magasin, le coût des composants
et le nombre d'exemplaires assemblables. creer_ensemble : l'article parent est NON stocké (ce sont les composants qui le sont) ; un composant
n'est jamais lui-même un ensemble. Récapitule composants et quantités avant d'écrire. modifier_ensemble pour ajouter, retirer ou remplacer."""
