"""Un bon de livraison ne sort jamais MOINS CHER que sa commande parce que la règle de prix s'est réappliquée.

Le cas (SAL-ORD-2026-04838, Amri Sadok, 27/11/2026)
--------------------------------------------------
La règle de prix « Réduction PP quantité B2B » donne 5 % sur des cartouches. Sur la commande, la marge minimale
(marge_minimale.py) a RELEVÉ deux lignes que cette remise faisait passer sous le plancher : PP 2,660 → 3,000 et
UDF 4,275 → 4,500. Le BL créé depuis la commande reprend bien ces prix… puis ERPNext, à l'enregistrement du BL,
RÉAPPLIQUE la règle de prix et recalcule le prix depuis le prix de liste : 2,660 et 4,275. Rien ne relève une
seconde fois (la marge minimale ne s'applique qu'aux commandes et aux devis) : le BL sort à 233,790 pour une
commande de 236,615, sous la marge plancher. Conséquences : pastille « Livraison partielle, dette surévaluée »
sur une commande entièrement livrée, et facture du mois (calculée sur les BL) inférieure à ce que le client doit.

La règle
--------
Sur une ligne de BL née d'une ligne de commande : si son prix est EXACTEMENT celui que la règle de prix vient de
recalculer (prix de liste − remise) et qu'il est INFÉRIEUR au prix de la commande, c'est la réapplication de la
règle — pas un choix de quelqu'un : la ligne reprend le prix de la commande. Cela couvre tout ce qui a monté le
prix sur la commande (marge minimale, majoration carte, prix retapé à la main).

Ce qui n'est PAS touché : un prix du BL différent du prix recalculé (quelqu'un l'a tapé : « c'est le BL qui fait
foi », cf. livraison_partielle), un prix du BL supérieur à celui de la commande, une ligne sans règle de prix,
une ligne gratuite, un BL de retour.

Deux usages : le hook `validate` du Delivery Note (après le validate d'ERPNext, donc après la réapplication), et
`corriger_bl_valides` — la reprise des BL DÉJÀ validés qui ont eu ce souci (patch `bl_prix_de_la_commande`).

LA FACTURE AUSSI (27/11/2026, sa question : « la création des factures peut-elle diverger de la commande ou de
plusieurs commandes, en assurant la marge ? »). Mesuré : 89 factures depuis le 27/10, aucune ne diffère de ses
commandes ni de ses BL — les facturations automatiques (mensuelle, Compte Pro, encaissement) recopient le prix du BL
et coupent les règles de prix (`ignore_pricing_rule`). Mais une facture créée À LA MAIN depuis un BL ou une commande
repasse par la règle de prix : essai sur le BL d'Amri Sadok corrigé (236,615) → facture à 233,790.
`apres_valeurs_manquantes` : une ligne de facture née d'une ligne de BL (sinon de commande), au prix exact de la règle
et sous celui de son BL, reprend le prix du BL. Chaque ligne suivant son propre BL, une facture de plusieurs
commandes est couverte ligne à ligne.

⚠️ OÙ CELA SE BRANCHE, ET POURQUOI PAS AILLEURS. Sur une facture, ERPNext contrôle l'échéancier et alloue les avances
DANS son validate, sur le total qu'il vient de calculer : corriger le prix après (hook `validate`) laisse un
échéancier et des avances calculés sur l'ancien total, et de toute façon ERPNext a déjà refusé la facture (« Total
Payment Amount in Payment Schedule must be equal to Grand / Rounded Total » : l'échéancier recopié de la commande ne
tombe plus sur le total baissé). La reprise se fait donc juste après `set_missing_values` — là où ERPNext rejoue la
règle de prix — par les classes surchargées de retour_echange (`SalesInvoiceEchange`, `DeliveryNoteEchange`) : à la
création depuis le BL comme à chaque enregistrement, totaux, avances et échéancier se calculent sur le bon prix.
⚠️ Ne PAS couper les règles de prix (`ignore_pricing_rule`) sur une facture qui en porte déjà : à l'enregistrement
suivant ERPNext retire TOUTES les remises de règle, y compris celles voulues (essai : 236,615 → 238,750).
"""
from __future__ import annotations

import frappe
from frappe.utils import flt

TOLERANCE = 0.0005
#: La marge minimale existe depuis le 27/10/2026 : avant, aucun prix de commande n'était relevé par le système.
DEPUIS = "2026-10-27"


def prix_de_la_regle(ligne: dict) -> float | None:
    """Le prix que la règle de prix donne à cette ligne : prix de liste moins la remise. None si la ligne ne porte
    pas de règle de prix, ou pas de prix de liste. Pur."""
    regles = str(ligne.get("pricing_rules") or "").strip()
    plr = flt(ligne.get("price_list_rate"))
    if regles in ("", "[]") or plr <= 0:
        return None
    # `round`, pas `flt(x, 6)` : hors site (tests purs), flt avec une précision rend 0.
    return round(plr * (1 - flt(ligne.get("discount_percentage")) / 100.0), 6)


def prix_a_reprendre(ligne_bl: dict, prix_commande) -> float | None:
    """Le prix de la commande, si la ligne du BL doit le reprendre ; None sinon. Pur.

    Elle le reprend quand son prix est celui que la règle vient de recalculer ET qu'il est sous celui de la
    commande. Un prix tapé sur le BL (différent du prix de la règle) est un choix : il reste."""
    prix_bl, prix_cmd = flt(ligne_bl.get("rate")), flt(prix_commande)
    if ligne_bl.get("is_free_item") or prix_cmd <= 0 or prix_bl >= prix_cmd - TOLERANCE:
        return None
    regle = prix_de_la_regle(ligne_bl)
    if regle is None or abs(prix_bl - regle) > 0.001:
        return None
    return prix_cmd


def _prix_des_lignes_de_commande(noms) -> dict:
    noms = [n for n in set(noms or ()) if n]
    if not noms:
        return {}
    return {r.name: r.rate for r in frappe.get_all("Sales Order Item", filters={"name": ["in", noms]},
                                                   fields=["name", "rate"], limit_page_length=0)}


def _prix_des_lignes_de_bl(noms) -> dict:
    noms = [n for n in set(noms or ()) if n]
    if not noms:
        return {}
    return {r.name: r.rate for r in frappe.get_all("Delivery Note Item", filters={"name": ["in", noms]},
                                                   fields=["name", "rate"], limit_page_length=0)}


def aligner(doc, recalculer: bool = True) -> list[dict]:
    """Remet à son prix de référence chaque ligne que la règle de prix a baissée. -> les lignes reprises.
    La référence : la ligne de COMMANDE pour un BL ; la ligne de BL, sinon de commande, pour une facture."""
    from customization_app.marge_minimale import _relever

    prix_cmd = _prix_des_lignes_de_commande([l.get("so_detail") for l in doc.items])
    prix_bl = _prix_des_lignes_de_bl([l.get("dn_detail") for l in doc.items]) if doc.doctype == "Sales Invoice" else {}
    faits = []
    for l in doc.items:
        reference = prix_bl.get(l.get("dn_detail")) if l.get("dn_detail") in prix_bl else prix_cmd.get(l.get("so_detail"))
        cible = prix_a_reprendre(l.as_dict(), reference) if reference else None
        if cible is None:
            continue
        faits.append({"item_code": l.item_code, "avant": flt(l.rate), "apres": cible, "qty": flt(l.qty)})
        _relever(l, cible)
    if faits and recalculer:
        doc.calculate_taxes_and_totals()
    return faits


def apres_valeurs_manquantes(doc):
    """Appelée par les classes de BL et de facture juste après `set_missing_values` d'ERPNext (qui vient de rejouer
    la règle de prix) : les lignes baissées reprennent leur prix de référence, AVANT le calcul des totaux. Ne lève
    jamais : une reprise qui échoue laisse le document tel qu'ERPNext l'a calculé."""
    # ⚠️ Pas de filtre « brouillon seulement » : à la VALIDATION le document porte déjà docstatus = 1 quand ERPNext
    # rejoue la règle de prix — c'est précisément là que le prix retombait (essai : facture validée à 233,790).
    if doc.get("is_return") or doc.docstatus == 2:
        return
    try:
        aligner(doc, recalculer=False)
    except Exception:
        frappe.log_error(title="Prix du BL / de la facture : reprise du prix de référence", message=frappe.get_traceback())


def delivery_note_validate(doc, method=None):
    """Hook `validate` du Delivery Note, APRÈS celui d'ERPNext : le filet de sécurité de `apres_valeurs_manquantes`
    (un BL construit sans passer par la classe surchargée, ou dont le prix aurait été recalculé plus tard dans le
    validate). Un BL n'a ni échéancier ni avances : refaire ses totaux ici est sans conséquence."""
    if doc.get("is_return"):
        return
    aligner(doc)


# ---------------------------------------------------------------------------
# Reprise des BL déjà validés
# ---------------------------------------------------------------------------

def _facture(bl: str) -> str | None:
    """La facture (brouillon ou validée) qui porte ce BL : un BL facturé ne se retouche pas."""
    lignes = frappe.db.sql("""select parent from `tabSales Invoice Item` where delivery_note = %s and docstatus < 2 limit 1""", bl)
    return lignes[0][0] if lignes else None


def bl_a_corriger(depuis: str = DEPUIS) -> list[dict]:
    """Les BL validés dont une ligne est sortie sous le prix de sa commande par réapplication de la règle de prix.
    -> [{bl, commande, facture, lignes: [{name, item_code, avant, apres, qty}], ecart}] ; lecture seule."""
    lignes = frappe.db.sql(
        """select di.name, di.parent bl, di.item_code, di.qty, di.rate, di.price_list_rate, di.discount_percentage,
                  di.pricing_rules, di.is_free_item, si.rate prix_commande, si.parent commande
           from `tabDelivery Note Item` di
           join `tabDelivery Note` dn on dn.name = di.parent and dn.docstatus = 1 and ifnull(dn.is_return, 0) = 0
           join `tabSales Order Item` si on si.name = di.so_detail
           where dn.posting_date >= %s and di.rate < si.rate - %s
           order by dn.posting_date, di.parent, di.idx""", (depuis, TOLERANCE), as_dict=True)
    par_bl = {}
    for l in lignes:
        cible = prix_a_reprendre(l, l.prix_commande)
        if cible is None:
            continue
        b = par_bl.setdefault(l.bl, {"bl": l.bl, "commande": l.commande, "lignes": [], "ecart": 0.0})
        b["lignes"].append({"name": l.name, "item_code": l.item_code, "avant": flt(l.rate), "apres": cible, "qty": flt(l.qty)})
        b["ecart"] = round(b["ecart"] + (cible - flt(l.rate)) * flt(l.qty), 3)
    for b in par_bl.values():
        b["facture"] = _facture(b["bl"])
    return list(par_bl.values())


def corriger_bl_valides(simuler: bool = True, depuis: str = DEPUIS) -> list[dict]:
    """Remet au prix de la commande les lignes concernées des BL validés, non facturés. Rien de comptable ne bouge :
    les écritures d'un BL suivent le coût du stock, pas le prix de vente. `simuler=True` : ne change rien.
    -> la liste de `bl_a_corriger`, chaque BL complété de {statut, avant, apres}."""
    from customization_app import commande_alertes, per_delivered_montant
    from customization_app.marge_minimale import _relever

    bilan = bl_a_corriger(depuis)
    for b in bilan:
        if b["facture"]:
            b["statut"] = "laissé : déjà sur la facture %s" % b["facture"]
            continue
        if simuler:
            b["statut"] = "à corriger (simulation)"
            continue
        doc = frappe.get_doc("Delivery Note", b["bl"])
        b["avant"] = flt(doc.grand_total)
        cibles = {l["name"]: l["apres"] for l in b["lignes"]}
        for l in doc.items:
            if l.name in cibles:
                _relever(l, cibles[l.name])
        doc.calculate_taxes_and_totals()
        if hasattr(doc, "set_total_in_words"):
            doc.set_total_in_words()
        # Document validé : on écrit les lignes et les totaux recalculés, sans repasser par la validation (qui
        # réappliquerait la règle de prix) ni toucher au stock. `modified` reste : rien n'a été « modifié » à l'écran.
        for enfant in list(doc.items) + list(doc.get("taxes") or []):
            enfant.db_update()
        doc.db_update()
        b["apres"] = flt(doc.grand_total)
        doc.add_comment("Info", "Prix remis à ceux de la commande %s : %s. À l’enregistrement du BL, la règle de prix "
                        "s’était réappliquée et avait baissé ces lignes sous le prix de la commande (%s → %s)."
                        % (b["commande"], ", ".join("%s %s → %s" % (l["item_code"], l["avant"], l["apres"]) for l in b["lignes"]),
                           b["avant"], b["apres"]))
        # Le livré de la commande et sa pastille d'anomalie se lisent sur le total du BL.
        per_delivered_montant.aligner_commande(b["commande"])
        commande_alertes.recalculer([b["commande"]])
        b["statut"] = "corrigé"
    return bilan
