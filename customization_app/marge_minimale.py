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

Jamais relevés : lignes gratuites, lignes à 0 DT (échange, garantie — option), articles sans coût connu
(services, main d'œuvre), groupes d'articles exclus par le réglage, commandes du site web (option : le client
a déjà payé le prix affiché en ligne).
"""
from __future__ import annotations

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


def reglage() -> dict:
    """Le réglage, avec des valeurs sûres si le document n'a jamais été enregistré."""
    try:
        c = frappe.get_cached_doc(CONFIG)
    except Exception:
        return {"actif": 0}
    return {"actif": int(c.get("actif") or 0), "taux": flt(c.get("taux") or 0),
            "base": c.get("base") or BASE_PRIX, "source_cout": c.get("source_cout") or SOURCE_VALO,
            "ignorer_web": int(c.get("ignorer_web") if c.get("ignorer_web") is not None else 1),
            "ignorer_prix_zero": int(c.get("ignorer_prix_zero") if c.get("ignorer_prix_zero") is not None else 1),
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


def arrondir_sup(v: float, decimales: int = 3) -> float:
    """Arrondi vers le haut au millime : le prix relevé ne redescend jamais sous le plancher."""
    f = 10 ** decimales
    return math.ceil(flt(v) * f - 1e-9) / f


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
    appliquer(doc)


def appliquer(doc) -> list[dict]:
    """Relève les lignes sous la marge plancher ; renvoie les corrections faites (pour le message)."""
    r = reglage()
    if not r["actif"] or r["taux"] <= 0:
        return []
    if r["ignorer_web"] and doc.get("woocommerce_id"):
        return []
    corrections = []
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
        facteur_tva = (flt(l.rate) / flt(l.net_rate)) if flt(l.net_rate) > 0 and flt(l.rate) > 0 else 1.0
        plancher_ttc = arrondir_sup(plancher_ht(cout, r["taux"], r["base"]) * facteur_tva, l.precision("rate"))
        if flt(l.rate) + TOLERANCE >= plancher_ttc:
            # Au-dessus du plancher. Si la ligne avait été relevée et qu'on est exactement sur le plancher,
            # c'est notre prix : on garde la trace. Sinon l'utilisateur a mis un prix à lui : trace effacée.
            if not (l.get(CHAMP_FLAG) and abs(flt(l.rate) - plancher_ttc) <= TOLERANCE):
                _oublier(l)
            continue
        avant = flt(l.rate)
        _relever(l, plancher_ttc)
        if not l.get(CHAMP_FLAG):
            l.set(CHAMP_AVANT, avant)
        l.set(CHAMP_FLAG, 1)
        corrections.append({"item_code": l.item_code, "item_name": l.item_name, "avant": avant, "apres": plancher_ttc})
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
def etat_commande(name: str) -> dict:
    """Pour le formulaire : les lignes relevées d'une commande, et le taux en vigueur."""
    r = reglage()
    lignes = frappe.get_all("Sales Order Item", filters={"parent": name, CHAMP_FLAG: 1},
                            fields=["name", "idx", "item_code", "item_name", "rate", CHAMP_AVANT])
    return {"taux": r.get("taux"), "actif": r.get("actif"), "lignes": lignes}
