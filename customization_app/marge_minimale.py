"""Marge minimale de vente (demande du 27/10/2026).

Une commande client ne vend jamais un article sous une marge plancher, réglable (15 % par défaut, réglage
« Config Marge Minimale »). À chaque enregistrement, pour chaque ligne : on prend le prix tel que la liste de
prix ET sa règle de prix l'ont posé (ERPNext vient de le recalculer dans `validate`), on le compare au coût de
l'article, et s'il laisse moins que la marge plancher, le prix de la ligne est relevé au prix qui donne
exactement cette marge. Sinon le prix reste celui de la liste. La ligne relevée garde la trace : case
« Prix relevé (marge mini) » cochée et prix d'origine conservé ; le formulaire les met en évidence.

Pourquoi dans `validate` et pas avant : ERPNext ré-applique la règle de prix (remise) à chaque validation
(`set_missing_item_details`), puis recalcule `rate` à partir de la remise. Relever le prix avant serait
écrasé ; le hook `validate` de l'app passe APRÈS le validate du contrôleur, on corrige donc le résultat final
puis on refait les totaux (`calculate_taxes_and_totals`).

Les prix de vente sont TTC (TVA incluse dans le prix, `included_in_print_rate`) ; les coûts sont HT. La marge
se mesure donc sur `net_rate` (HT), et le plancher TTC se déduit du rapport rate / net_rate de la ligne, qui
porte la TVA de l'article (7 %, 19 %…). Le coût d'un ensemble (Product Bundle) est la somme des coûts de ses
composants (`packed_items`).

EN TEMPS RÉEL AUSSI : le formulaire (public/js/sales_order_marge.js) appelle `plancher_ligne` dès qu'un article,
une quantité, un prix ou une remise change, et relève le prix sous les yeux de l'utilisateur ; le hook `validate`
reste le filet de sécurité (API, import, script). Même calcul des deux côtés. Le prix relevé est arrondi vers le
haut au pas réglé (0,5 DT par défaut : 25,99 → 26,00).

DOCUMENT EXONÉRÉ DE TVA (modèle « Vente Exoneration d'impot » : aucune taxe incluse dans le prix) : le prix de
liste est TTC, le document est HT. Sans rien faire, ERPNext prend le prix TTC pour un prix HT et le client est
surfacturé ; les commandes exonérées portaient des prix HT tapés à la main, incohérents (S-S25 : 22,4 ou 28 pour
35 TTC = 29,41 HT). Ici le prix de liste de la ligne devient TTC ÷ (1 + TVA de l'article), sur commande et devis,
en temps réel et à l'enregistrement (`convertir_prix_ht`). Le plancher de marge est alors HT (facteur TVA = 1).

Jamais relevés : lignes gratuites, lignes à 0 DT (échange, garantie — option), articles sans coût connu
(services, main d'œuvre), groupes d'articles exclus par le réglage, commandes du site web (option : le client
a déjà payé le prix affiché en ligne).
"""
from __future__ import annotations

import json
import math

import frappe
from frappe import _
from frappe.utils import flt

CONFIG = "Config Marge Minimale"
CHAMP_FLAG = "custom_marge_corrigee"
CHAMP_AVANT = "custom_prix_avant_marge"
BASE_PRIX = "Prix de vente HT"
BASE_COUT = "Coût d'achat"
SOURCE_VALO = "Valorisation du stock"
SOURCE_ACHAT = "Dernier prix d'achat"
TOLERANCE = 0.0005
ARRONDI_DEFAUT = 0.5


def reglage() -> dict:
    """Le réglage, avec des valeurs sûres si le document n'a jamais été enregistré."""
    try:
        c = frappe.get_cached_doc(CONFIG)
    except Exception:
        return {"actif": 0}
    return {"actif": int(c.get("actif") or 0), "taux": flt(c.get("taux") or 0),
            "arrondi": flt(c.get("arrondi")) if c.get("arrondi") is not None else ARRONDI_DEFAUT,
            "base": c.get("base") or BASE_PRIX, "source_cout": c.get("source_cout") or SOURCE_VALO,
            "ignorer_web": int(c.get("ignorer_web") if c.get("ignorer_web") is not None else 1),
            "ignorer_prix_zero": int(c.get("ignorer_prix_zero") if c.get("ignorer_prix_zero") is not None else 1),
            "prix_ht_exonere": int(c.get("prix_ht_exonere") if c.get("prix_ht_exonere") is not None else 1),
            "groupes_exclus": {r.item_group for r in (c.get("groupes_exclus") or []) if r.item_group}}


# ----------------------------------------------------------------- calcul (fonctions pures)

def plancher_ht(cout: float, taux: float, base: str = BASE_PRIX) -> float:
    """Le prix HT minimal pour la marge `taux` (%) sur un coût `cout`.
    Sur le prix de vente : prix = coût / (1 − taux) ; sur le coût : prix = coût × (1 + taux)."""
    cout, t = flt(cout), flt(taux) / 100.0
    if cout <= 0:
        return 0.0
    if base == BASE_COUT:
        return cout * (1 + t)
    if t >= 1:
        raise ValueError("taux de marge sur le prix de vente >= 100 %")
    return cout / (1 - t)


def marge(prix_ht: float, cout: float, base: str = BASE_PRIX) -> float | None:
    """La marge (%) d'un prix HT sur un coût, selon la base ; None si incalculable."""
    prix_ht, cout = flt(prix_ht), flt(cout)
    if cout <= 0 or prix_ht <= 0:
        return None
    if base == BASE_COUT:
        return (prix_ht - cout) / cout * 100
    return (prix_ht - cout) / prix_ht * 100


def arrondir_sup(v: float, pas: float = 0.0, decimales: int = 3) -> float:
    """Arrondi vers le haut : au pas donné (0,5 → 25,99 devient 26,00), sinon au millime. Le prix relevé ne
    redescend jamais sous le plancher."""
    pas = flt(pas)
    if pas > 0:
        return flt(math.ceil(flt(v) / pas - 1e-9) * pas, decimales)
    f = 10 ** decimales
    return math.ceil(flt(v) * f - 1e-9) / f


def facteur_tva(item_tax_rate, taxes) -> float:
    """TTC / HT d'une ligne, comme ERPNext le calcule : les taxes « incluses dans le prix » sur le net, au taux
    du modèle de taxes de l'article (`item_tax_rate`, JSON) sinon celui de la ligne de taxes. Fonction pure."""
    try:
        carte = json.loads(item_tax_rate) if isinstance(item_tax_rate, str) else (item_tax_rate or {})
    except ValueError:
        carte = {}
    total = 0.0
    for t in taxes or []:
        t = t if isinstance(t, dict) else t.as_dict()
        if not t.get("included_in_print_rate") or t.get("charge_type") not in ("On Net Total", None):
            continue
        total += flt(carte.get(t.get("account_head"), t.get("rate")))
    return 1 + total / 100.0


def est_exonere(taxes) -> bool:
    """Le document a des taxes mais aucune n'est incluse dans le prix : document HT (exonération)."""
    lignes = [t if isinstance(t, dict) else t.as_dict() for t in taxes or []]
    return bool(lignes) and not any(t.get("included_in_print_rate") for t in lignes)


def taux_tva_article(item_code: str, item_tax_rate=None) -> float | None:
    """La TVA (%) de l'article : d'abord `item_tax_rate` de la ligne (JSON posé par ERPNext), sinon le modèle de
    taxes de la fiche article. None si inconnue."""
    try:
        carte = json.loads(item_tax_rate) if isinstance(item_tax_rate, str) else (item_tax_rate or {})
    except ValueError:
        carte = {}
    if carte:
        return flt(sum(flt(v) for v in carte.values()))
    modele = frappe.db.get_value("Item Tax", {"parent": item_code, "parenttype": "Item"}, "item_tax_template", order_by="idx")
    if not modele:
        return None
    return flt(sum(flt(d.tax_rate) for d in frappe.get_cached_doc("Item Tax Template", modele).taxes))


def prix_liste(item_code: str, price_list: str, uom: str | None = None) -> float | None:
    """Le prix de la liste pour l'article (unité de la ligne d'abord, sinon sans unité / unité de stock)."""
    if not item_code or not price_list:
        return None
    prix = frappe.get_all("Item Price", filters={"item_code": item_code, "price_list": price_list, "selling": 1},
                          fields=["price_list_rate", "uom"], order_by="valid_from desc, modified desc")
    if not prix:
        return None
    pour_uom = [p for p in prix if uom and p.uom == uom]
    sans = [p for p in prix if not p.uom or p.uom == frappe.get_cached_value("Item", item_code, "stock_uom")]
    choix = (pour_uom or sans or prix)[0]
    return flt(choix.price_list_rate)


def prix_liste_ht(item_code: str, price_list: str, uom: str | None, item_tax_rate=None, decimales: int = 3) -> float | None:
    """Prix de liste TTC ramené en HT avec la TVA de l'article. None si prix ou TVA inconnus."""
    ttc, tva = prix_liste(item_code, price_list, uom), taux_tva_article(item_code, item_tax_rate)
    if ttc is None or tva is None:
        return None
    return flt(ttc / (1 + tva / 100.0), decimales)


def plancher_ttc(cout: float, facteur: float, r: dict, decimales: int = 3) -> float:
    """Le prix TTC minimal d'une ligne : plancher HT × facteur TVA, arrondi vers le haut au pas réglé."""
    return arrondir_sup(plancher_ht(cout, r["taux"], r["base"]) * flt(facteur or 1), r.get("arrondi", 0), decimales)


def plancher_exonere(cout: float, tva_article: float | None, r: dict, decimales: int = 3) -> float:
    """Document exonéré (prix HT) : l'arrondi se fait sur le prix TTC qu'aurait la ligne avec la TVA de l'article,
    puis on redescend en HT — le même client paie la même base, exonéré ou non (demande du 28/10/2026).
    UDF : plancher HT 3,695 × 1,19 = 4,397 → 4,500 → HT 3,782."""
    f = 1 + flt(tva_article or 0) / 100.0
    return flt(plancher_ttc(cout, f, r, decimales) / f, decimales)


# ----------------------------------------------------------------- coûts

def _cout_article(item_code: str, warehouse: str | None, source: str) -> float:
    """Coût unitaire HT : valorisation du Bin de l'entrepôt de la ligne, sinon celle de l'article, sinon le
    dernier prix d'achat (ou l'inverse si la source réglée est le dernier prix d'achat). 0 = inconnu."""
    valo = 0.0
    if warehouse:
        valo = flt(frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "valuation_rate"))
    if not valo:
        valo = flt(frappe.db.get_value("Item", item_code, "valuation_rate"))
    achat = flt(frappe.db.get_value("Item", item_code, "last_purchase_rate"))
    ordre = (achat, valo) if source == SOURCE_ACHAT else (valo, achat)
    return next((c for c in ordre if c > 0), 0.0)


def cout_ligne(doc, ligne, source: str) -> float:
    """Coût unitaire HT de la ligne : l'article lui-même s'il est suivi en stock, sinon la somme des
    composants de l'ensemble (packed_items) ramenée à l'unité. 0 = pas de coût connu (service…)."""
    if frappe.get_cached_value("Item", ligne.item_code, "is_stock_item"):
        # La valorisation est par unité de stock ; la ligne peut être vendue dans une autre unité (carton…).
        return _cout_article(ligne.item_code, ligne.get("warehouse"), source) * (flt(ligne.get("conversion_factor")) or 1.0)
    composants = [p for p in (doc.get("packed_items") or [])
                  if (p.get("parent_detail_docname") and p.parent_detail_docname == ligne.name)
                  or (not p.get("parent_detail_docname") and p.parent_item == ligne.item_code)]
    if not composants or not flt(ligne.qty):
        return 0.0
    total = sum(_cout_article(p.item_code, p.get("warehouse") or ligne.get("warehouse"), source) * flt(p.qty)
                for p in composants)
    return total / flt(ligne.qty)


# ----------------------------------------------------------------- hook

def sales_order_validate(doc, method=None):
    """Commande client et devis : prix HT sur document exonéré, puis marge minimale."""
    convertir_prix_ht(doc)
    appliquer(doc)


def convertir_prix_ht(doc) -> list[dict]:
    """Document exonéré : chaque ligne prend pour prix de liste le TTC de la liste ÷ (1 + TVA de l'article).
    Le prix de la ligne suit : remise de règle de prix gardée en %, prix tapé à la main gardé tel quel (c'est
    déjà un HT voulu), prix de liste pur recalculé. Renvoie les lignes converties."""
    r = reglage()
    if not r.get("actif") or not r.get("prix_ht_exonere") or not est_exonere(doc.get("taxes")):
        return []
    faites = []
    for l in doc.get("items") or []:
        if not l.item_code or flt(l.price_list_rate) <= 0:
            continue
        ht = prix_liste_ht(l.item_code, doc.get("selling_price_list"), l.get("uom"), l.get("item_tax_rate"), l.precision("price_list_rate"))
        if ht is None or abs(flt(l.price_list_rate) - ht) <= TOLERANCE:
            continue
        avant_plr, avant_rate = flt(l.price_list_rate), flt(l.rate)
        # Prix « à la main » : soit incohérent avec liste × remise (posé par l'API ou un script), soit une remise
        # ou une marge sans règle de prix (tapé à l'écran, ERPNext en a déduit la remise). Il est gardé tel quel.
        attendu = avant_plr + flt(l.margin_rate_or_amount) if l.get("margin_type") == "Amount" else \
            avant_plr * (1 - flt(l.discount_percentage) / 100.0)
        manuel = abs(avant_rate - attendu) > TOLERANCE or \
            (not l.get("pricing_rules") and (flt(l.discount_percentage) != 0 or l.get("margin_type")))
        l.price_list_rate = ht
        if manuel and avant_rate > 0:
            _relever(l, avant_rate)                   # gardé ; remise / marge refaites sur le HT
        else:
            l.margin_type, l.margin_rate_or_amount, l.rate_with_margin = "", 0, 0
            l.rate = flt(ht * (1 - flt(l.discount_percentage) / 100.0), l.precision("rate"))
            l.discount_amount = flt(ht - l.rate, l.precision("discount_amount"))
        faites.append({"item_code": l.item_code, "plr_avant": avant_plr, "plr": ht, "rate": l.rate})
    if faites:
        doc.calculate_taxes_and_totals()
    return faites


def appliquer(doc) -> list[dict]:
    """Relève les lignes sous la marge plancher ; renvoie les corrections faites (pour le message)."""
    r = reglage()
    if not r["actif"] or r["taux"] <= 0:
        return []
    if r["ignorer_web"] and doc.get("woocommerce_id"):
        return []
    corrections = []
    exonere = est_exonere(doc.get("taxes"))
    for l in doc.get("items") or []:
        if not l.item_code or l.get("is_free_item"):
            continue
        if r["ignorer_prix_zero"] and flt(l.rate) <= 0:
            _oublier(l)
            continue
        if r["groupes_exclus"] and l.get("item_group") in r["groupes_exclus"]:
            _oublier(l)
            continue
        cout = cout_ligne(doc, l, r["source_cout"])
        if cout <= 0:
            _oublier(l)
            continue
        if exonere:
            plancher = plancher_exonere(cout, taux_tva_article(l.item_code, l.get("item_tax_rate")), r, l.precision("rate"))
        else:
            facteur = (flt(l.rate) / flt(l.net_rate)) if flt(l.net_rate) > 0 and flt(l.rate) > 0 \
                else facteur_tva(l.get("item_tax_rate"), doc.get("taxes"))
            plancher = plancher_ttc(cout, facteur, r, l.precision("rate"))
        if flt(l.rate) + TOLERANCE >= plancher:
            # Au-dessus du plancher. Si la ligne avait été relevée et qu'on est exactement sur le plancher,
            # c'est notre prix : on garde la trace. Sinon l'utilisateur a mis un prix à lui : trace effacée.
            if not (l.get(CHAMP_FLAG) and abs(flt(l.rate) - plancher) <= TOLERANCE):
                _oublier(l)
            continue
        avant = flt(l.rate)
        _relever(l, plancher)
        if not l.get(CHAMP_FLAG):
            l.set(CHAMP_AVANT, avant)
        l.set(CHAMP_FLAG, 1)
        corrections.append({"item_code": l.item_code, "item_name": l.item_name, "avant": avant, "apres": plancher})
    if corrections:
        avant_total = flt(doc.grand_total)
        doc.calculate_taxes_and_totals()
        _realigner_echeancier(doc, flt(doc.grand_total) - avant_total)
        _prevenir(corrections, r["taux"])
    return corrections


def _realigner_echeancier(doc, delta: float):
    """ERPNext a contrôlé l'échéancier (somme = total) AVANT que le prix ne soit relevé : la dernière échéance
    absorbe l'écart, sinon les paiements générés par les Server Scripts (« Generation payement », par ligne
    d'échéancier) seraient faux. Les parts (%) sont refaites sur le nouveau total."""
    lignes = doc.get("payment_schedule") or []
    if not lignes or abs(delta) < TOLERANCE:
        return
    derniere = lignes[-1]
    derniere.payment_amount = flt(derniere.payment_amount) + delta
    total = flt(doc.grand_total)
    for l in lignes:
        l.base_payment_amount = flt(l.payment_amount * flt(doc.conversion_rate or 1), l.precision("base_payment_amount"))
        if hasattr(l, "outstanding") and doc.docstatus == 0:
            l.outstanding = l.payment_amount
        l.invoice_portion = flt(l.payment_amount / total * 100, l.precision("invoice_portion")) if total else 0


def _oublier(l):
    if l.get(CHAMP_FLAG) or l.get(CHAMP_AVANT):
        l.set(CHAMP_FLAG, 0)
        l.set(CHAMP_AVANT, 0)


def _relever(l, prix: float):
    """Pose `prix` comme prix de la ligne d'une façon qu'ERPNext ne défait pas au recalcul : sous le prix de
    liste, c'est une remise réduite (la règle de prix reste liée à la ligne) ; au-dessus, une marge
    « Amount » sur le prix de liste, sans remise."""
    plr = flt(l.price_list_rate)
    if plr > 0 and prix <= plr + TOLERANCE:
        l.margin_type = ""
        l.margin_rate_or_amount = 0
        l.rate_with_margin = 0
        l.discount_amount = flt(plr - prix, l.precision("discount_amount"))
        l.discount_percentage = flt((plr - prix) / plr * 100, l.precision("discount_percentage"))
    elif plr > 0:
        l.margin_type = "Amount"
        l.margin_rate_or_amount = flt(prix - plr, l.precision("margin_rate_or_amount"))
        l.discount_amount = 0
        l.discount_percentage = 0
    else:
        l.discount_amount = 0
        l.discount_percentage = 0
    l.rate = prix


def _prevenir(corrections: list[dict], taux: float):
    lignes = "".join("<li>%s : %s → <b>%s</b></li>" % (
        frappe.utils.escape_html("%s%s" % (c["item_code"], " — " + c["item_name"] if c.get("item_name") else "")),
        frappe.format(c["avant"], {"fieldtype": "Currency"}), frappe.format(c["apres"], {"fieldtype": "Currency"}))
        for c in corrections)
    frappe.msgprint(_("Prix relevés à la marge minimale ({0} %) :").format(flt(taux)) + "<ul>%s</ul>" % lignes,
                    title=_("Marge minimale"), indicator="orange")


@frappe.whitelist()
def plancher_ligne(ligne, taxes=None, woocommerce_id=None) -> dict:
    """Temps réel (formulaire) : le prix TTC minimal d'une ligne telle qu'elle est à l'écran, ou None si la
    ligne n'est jamais relevée. `ligne` : {item_code, warehouse, qty, conversion_factor, item_tax_rate, item_group,
    is_free_item, rate, composants: [{item_code, qty, warehouse}] (ensemble)} ; `taxes` : les lignes de taxes du
    document (account_head, rate, charge_type, included_in_print_rate)."""
    ligne = json.loads(ligne) if isinstance(ligne, str) else (ligne or {})
    taxes = json.loads(taxes) if isinstance(taxes, str) else (taxes or [])
    r = reglage()
    out = {"actif": r["actif"], "taux": r.get("taux"), "plancher": None, "prix_liste_ht": None,
           "exonere": est_exonere(taxes)}
    if not r["actif"] or not ligne.get("item_code"):
        return out
    if out["exonere"] and r.get("prix_ht_exonere") and ligne.get("price_list"):
        out["prix_liste_ht"] = prix_liste_ht(ligne["item_code"], ligne["price_list"], ligne.get("uom"), ligne.get("item_tax_rate"))
    if r["taux"] <= 0 or (r["ignorer_web"] and woocommerce_id):
        return out
    if ligne.get("is_free_item") or (r["ignorer_prix_zero"] and flt(ligne.get("rate")) <= 0):
        return out
    groupe = ligne.get("item_group") or frappe.get_cached_value("Item", ligne["item_code"], "item_group")
    if r["groupes_exclus"] and groupe in r["groupes_exclus"]:
        return out
    doc_like = frappe._dict(packed_items=[frappe._dict(c, parent_detail_docname="x") for c in ligne.get("composants") or []])
    l = frappe._dict(ligne, name="x")
    cout = cout_ligne(doc_like, l, r["source_cout"])
    if cout <= 0:
        return out
    if out["exonere"]:
        out["plancher"] = plancher_exonere(cout, taux_tva_article(ligne["item_code"], ligne.get("item_tax_rate")), r)
    else:
        out["plancher"] = plancher_ttc(cout, facteur_tva(ligne.get("item_tax_rate"), taxes), r)
    return out


@frappe.whitelist()
def etat_commande(name: str, doctype: str = "Sales Order") -> dict:
    """Les lignes relevées d'une commande (ou d'un devis), et le taux en vigueur."""
    r = reglage()
    lignes = frappe.get_all(doctype + " Item", filters={"parent": name, CHAMP_FLAG: 1},
                            fields=["name", "idx", "item_code", "item_name", "rate", CHAMP_AVANT])
    return {"taux": r.get("taux"), "actif": r.get("actif"), "lignes": lignes}
