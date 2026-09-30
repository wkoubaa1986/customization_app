"""
Livraison partielle : la dette d'une commande suit ce qui a été LIVRÉ, pas ce
qui a été commandé.

Le cas (SAL-ORD-2026-03111, 30/09/2026)
--------------------------------------
Une commande de 3 850 DT sortie en cinq bons de livraison, chacun réglé au
passage : 1 910 DT livrés, 1 700 DT encaissés. L'échéancier porte pourtant une
ligne « Dette non payée » de 2 150 DT = commande − encaissé, parce que le tandem
de Server Scripts impose échéancier = TTC de la commande. Le client apparaît
donc avec 2 150 DT sur « Dettes - A&S », à la caisse et dans le flux virements,
alors qu'il ne doit que 1 910 − 1 700 = 210 DT : le reste est de la marchandise
jamais sortie, pas une dette.

Trois briques
-------------
1. Le BILAN (`bilan`, `contexte`) : livré (BL validés), réglé (paiements réels,
   hors « Dette non payée »), dette affichée, dette réelle, non livré. Il
   alimente le bandeau de la fiche commande et le motif d'anomalie
   « Livraison partielle, dette surévaluée » (commande_alertes, règle SQL
   partagée ci-dessous : une seule définition des trois sommes).

2. La RÉGULARISATION (`regulariser`) : quand le client ne prendra pas le reste,
   la commande est ramenée à ce qui a été livré et la dette au reste dû.
     a. les lignes d'articles passent aux quantités livrées, au tarif
        réellement sorti sur les BL (c'est le BL qui fait foi : 230 et 250 DT
        sur des BL d'une commande à 220) ; une ligne jamais livrée disparaît ;
     b. l'échéancier garde ses règlements et la ligne « Dette non payée »
        devient TTC réduit − réglé (ou disparaît si tout est réglé) ; la part
        de ce reste dû que l'on n'attend plus du client (rien, une partie ou
        tout, au choix de l'utilisateur) passe en « Perte de paiement » sur
        une ligne datée du jour ;
     c. le tandem de Server Scripts recrée alors les Payment Entry de dette
        (« Dettes - A&S ») et de perte (« Perte de non paiement - A&S ») aux
        bons montants — rien de comptable n'est écrit ici.
   La commande passe à 100 % livré, statut « À facturer » : la facture
   mensuelle tombera égale aux BL.

3. La LISTE (`livraisons_partielles`, bouton « Livraisons partielles » de
   la liste des commandes) : TOUTES les commandes validées livrées en partie
   (0 < livré < total), que leur dette soit surévaluée ou non — le motif
   d'anomalie ne retient que la dette surévaluée, alors que la décision de
   ramener la commande au livré se pose pour chacune. Chaque ligne porte le
   bouton « Régulariser » (même dialogue que la fiche) ou ce qui bloque
   encore (fermée, facturée, trop-perçu). Le livré s'entend NET des retours :
   un BL de retour (colis Aramex refusé en partie, article rendu) a un total
   négatif, et la commande devient une livraison partielle comme une autre ;
   ce qui est revenu est affiché à part.

Pièges connus, et comment ils sont contournés
----------------------------------------------
- « generer un echeancier de maintenace » (Before Save, document soumis)
  refuse toute sauvegarde où l'échéancier ≠ TTC : la réduction des articles
  (qui change le TTC) se fait sous le drapeau `custom_outil_de_paiement`, que
  ce script et son jumeau « re-generate payment after sales order » honorent
  (contrôle et régénération sautés) ; l'échéancier est réécrit ensuite, drapeau
  retiré, dans une seconde sauvegarde qui déclenche la régénération.
- ERPNext recalcule `payment_amount` depuis `invoice_portion` à chaque
  enregistrement : toutes les portions sont mises à zéro (cf. avoir_client).
- `update_child_qty_rate` (le « Mettre à jour les articles » d'ERPNext) fait
  le reste proprement : stock réservé, statut, packing list, contrôle
  « quantité ≥ livrée », suppression refusée d'une ligne livrée ou facturée.

Les fonctions de décision (`bilan`, `plan_articles`, `plan_echeancier`,
`erreur_regularisation`) sont PURES : aucune base, testables telles quelles.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from customization_app.avoir_client import MODE_DETTE, _millimes, date_echeance_libre
from customization_app.per_delivered_montant import MARGE

# Rôles autorisés à réduire une commande : c'est une décision commerciale.
ROLES = ("System Manager", "Accounts Manager", "Sales Manager")

# Millime : la précision monétaire du site.
TOLERANCE = 0.001

MOTIF = "Livraison partielle, dette surévaluée"

# Le reste dû sur le livré peut être ABSORBÉ au lieu d'attendre le client : la
# ligne d'échéancier prend ce mode et le tandem passe l'écriture sur
# « Perte de non paiement - A&S » (même mécanique que la caisse).
MODE_PERTE = "Perte de paiement"

# ── Les trois sommes, en SQL corrélé sur `so` (alias de tabSales Order) ──────
# Une seule définition, réutilisée par le CASE des anomalies (toute la base) et
# par le bilan d'une commande : les deux ne peuvent pas diverger.

# TTC des bons de livraison validés rattachés à la commande (même lecture que
# per_delivered_montant.total_bl_valides : aucun BL ne sert deux commandes).
SQL_LIVRE = """COALESCE((
    SELECT SUM(dn.grand_total) FROM `tabDelivery Note` dn
    WHERE dn.docstatus = 1 AND EXISTS (
        SELECT 1 FROM `tabDelivery Note Item` dni
        WHERE dni.parent = dn.name AND dni.against_sales_order = so.name)), 0)"""

# Ce qui est REVENU : total (positif) des BL de retour validés de la commande.
# Un retour a un grand_total négatif ; SQL_LIVRE l'inclut déjà, ce fragment le
# met à part pour l'affichage.
SQL_RETOUR = """COALESCE((
    SELECT -SUM(dn.grand_total) FROM `tabDelivery Note` dn
    WHERE dn.docstatus = 1 AND dn.is_return = 1 AND EXISTS (
        SELECT 1 FROM `tabDelivery Note Item` dni
        WHERE dni.parent = dn.name AND dni.against_sales_order = so.name)), 0)"""

# Argent réellement reçu : paiements validés alloués à la commande ou à ses
# factures, tout mode SAUF « Dette non payée » (qui n'encaisse rien).
SQL_REGLE = """COALESCE((
    SELECT SUM(per.allocated_amount)
    FROM `tabPayment Entry Reference` per
    JOIN `tabPayment Entry` pe ON pe.name = per.parent
    WHERE pe.docstatus = 1 AND pe.payment_type = 'Receive'
      AND pe.mode_of_payment <> %(mode_dette)s
      AND ((per.reference_doctype = 'Sales Order' AND per.reference_name = so.name)
           OR (per.reference_doctype = 'Sales Invoice' AND per.reference_name IN (
                SELECT sii.parent FROM `tabSales Invoice Item` sii
                WHERE sii.sales_order = so.name)))), 0)"""

# Ce que l'échéancier porte HORS dette : règlements, chèques, avoirs — lignes
# que la régularisation conserve. Un avoir client n'a pas de Payment Entry
# « Receive » (SQL_REGLE l'ignore) mais il a bien payé la commande.
SQL_REGLE_ECHEANCIER = """COALESCE((
    SELECT SUM(ps.payment_amount) FROM `tabPayment Schedule` ps
    WHERE ps.parent = so.name AND ps.parenttype = 'Sales Order'
      AND COALESCE(ps.mode_of_payment, '') <> %(mode_dette)s), 0)"""

# Ce que l'échéancier de la commande affiche comme dette.
SQL_DETTE_LIGNE = """COALESCE((
    SELECT SUM(ps.payment_amount) FROM `tabPayment Schedule` ps
    WHERE ps.parent = so.name AND ps.parenttype = 'Sales Order'
      AND ps.mode_of_payment = %(mode_dette)s), 0)"""

# Le TTC de référence de la commande, celui que le tandem compare à l'échéancier.
SQL_TOTAL = "IF(so.disable_rounded_total = 1, so.grand_total, so.rounded_total)"

# La condition du motif, telle que le CASE des anomalies l'insère : commande
# validée, non facturée, livrée en partie (ni rien, ni tout), et une dette
# affichée qui dépasse ce que le client doit sur le livré.
SQL_CONDITION_MOTIF = f"""(so.docstatus = 1
    AND so.per_billed = 0
    AND {SQL_LIVRE} > %(marge)s
    AND {SQL_LIVRE} < {SQL_TOTAL} - %(marge)s
    AND {SQL_DETTE_LIGNE} > GREATEST({SQL_LIVRE} - {SQL_REGLE}, 0) + %(marge)s)"""

PARAMS_SQL = {"mode_dette": MODE_DETTE, "marge": MARGE}


# ── Décision (fonctions pures) ───────────────────────────────────────────────


def bilan(total, livre, regle, dette_ligne):
    """Les chiffres de la commande, et ce qu'ils disent.

    `dette_reelle` = livré − réglé, jamais négative : un client qui a payé plus
    que le livré ne doit rien (c'est un trop-perçu, signalé à part).
    """
    total = _millimes(total)
    livre = _millimes(livre)
    regle = _millimes(regle)
    dette_ligne = _millimes(dette_ligne)
    dette_reelle = _millimes(max(livre - regle, 0))
    partielle = livre > MARGE and livre < total - MARGE
    return {
        "total": total,
        "livre": livre,
        "regle": regle,
        "non_livre": _millimes(max(total - livre, 0)),
        "dette_ligne": dette_ligne,
        "dette_reelle": dette_reelle,
        "trop_percu": _millimes(max(regle - livre, 0)),
        "partielle": partielle,
        "surevaluee": partielle and dette_ligne > dette_reelle + MARGE,
    }


def plan_articles(lignes_commande, lignes_bl):
    """Les lignes de la commande ramenées à ce que les BL ont sorti.

    `lignes_commande` : [{name, item_code, qty, delivered_qty, rate, uom,
                          conversion_factor, delivery_date}]
    `lignes_bl`       : [{so_detail, qty, amount}] — lignes des BL VALIDÉS.

    Retour : {"articles": [...] (format update_child_qty_rate), "total": Σ des
    nouveaux montants, "supprimees": [item_code]} — ou une erreur en clair dans
    "erreur" si les BL ne se laissent pas rapporter aux lignes de la commande.

    Le tarif d'une ligne devient Σ montants BL / Σ quantités BL, arrondi au
    millime : c'est ce que le client a réellement accepté à chaque sortie.
    """
    par_ligne = {}
    for bl in lignes_bl:
        cle = bl.get("so_detail")
        if not cle:
            return {"erreur": "Un bon de livraison n'est rattaché à aucune ligne de la "
                              "commande (échange d'article ?) : régularisation manuelle."}
        agg = par_ligne.setdefault(cle, {"qty": 0.0, "amount": 0.0})
        agg["qty"] = _q(agg["qty"] + flt(bl.get("qty")))
        agg["amount"] = _millimes(agg["amount"] + flt(bl.get("amount")))

    articles, supprimees, total = [], [], 0.0
    for ligne in lignes_commande:
        agg = par_ligne.pop(ligne.get("name"), None)
        qty_bl = _q(agg["qty"]) if agg else 0.0
        if qty_bl <= 0:
            supprimees.append(ligne.get("item_code"))
            continue
        if abs(qty_bl - _q(ligne.get("delivered_qty"))) > 0.001:
            return {"erreur": "Ligne %s : %s livrés d'après les BL, %s d'après la commande — "
                              "régularisation manuelle."
                              % (ligne.get("item_code"), qty_bl, _q(ligne.get("delivered_qty")))}
        rate = _millimes(agg["amount"] / qty_bl)
        montant = _millimes(rate * qty_bl)
        total = _millimes(total + montant)
        articles.append({
            "docname": ligne.get("name"),
            "item_code": ligne.get("item_code"),
            "qty": qty_bl,
            "rate": rate,
            "uom": ligne.get("uom"),
            "conversion_factor": ligne.get("conversion_factor") or 1,
            "delivery_date": str(ligne.get("delivery_date") or ""),
        })
    if par_ligne:
        return {"erreur": "Des BL pointent des lignes absentes de la commande : "
                          "régularisation manuelle."}
    if not articles:
        return {"erreur": "Aucune ligne livrée : rien à régulariser."}
    return {"articles": articles, "total": total, "supprimees": supprimees}


def plan_echeancier(lignes, nouveau_total, aujourd_hui=None, perte=0):
    """L'échéancier une fois la commande ramenée au livré.

    Les lignes qui ne sont pas des dettes (règlements reçus, chèques en
    portefeuille, avoirs) restent telles quelles ; le reste dû devient
    `nouveau_total − Σ conservées`.

    `perte` : la part de ce reste dû que l'on n'attend plus du client (0, une
    partie, ou tout). Elle passe en mode « Perte de paiement », datée
    d'aujourd'hui (date de l'écriture de perte) ; le tandem la comptabilise en
    perte. Le reste reste une « Dette non payée ».

    La ligne de dette existante est réutilisée (même uid : le tandem remplace
    sa Payment Entry au lieu d'en créer une à côté) : elle porte la dette qui
    subsiste, ou la perte s'il ne reste plus rien à attendre. Toute autre
    ligne nécessaire est neuve.

    Retour : {"conservees": [lignes], "dette": attendu du client, "perte": absorbé,
              "ligne_dette": nom réutilisé ou None, "mode": mode de cette ligne,
              "montant_ligne": son montant, "date_dette": date ou None (si elle
              change de date), "nouvelles": [dicts à ajouter],
              "supprimees": [noms]} ou {"erreur": …}.
    """
    conservees = [l for l in lignes if (l.get("mode_of_payment") or "") != MODE_DETTE]
    dettes = [l for l in lignes if (l.get("mode_of_payment") or "") == MODE_DETTE]
    regle = _millimes(sum(flt(l.get("payment_amount")) for l in conservees))
    reste = _millimes(flt(nouveau_total) - regle)

    if reste < -TOLERANCE:
        return {"erreur": "Le client a réglé %s de plus que ce qui lui a été livré : "
                          "l'échéancier ne peut pas descendre sous les règlements reçus. "
                          "Traitez d'abord le trop-perçu (avoir ou remboursement)."
                          % _lisible(-reste)}
    if reste <= TOLERANCE:
        reste = 0.0
    perte = _millimes(perte)
    if perte < -TOLERANCE:
        return {"erreur": "Le montant passé en perte ne peut pas être négatif."}
    if perte > reste + TOLERANCE:
        return {"erreur": "La perte demandée (%s) dépasse le reste dû sur le livré (%s)."
                          % (_lisible(perte), _lisible(reste))}
    perte = min(max(perte, 0.0), reste)
    dette = _millimes(reste - perte)
    if dette <= TOLERANCE:
        # Un millime de dette n'a pas de sens : tout part en perte.
        dette, perte = 0.0, reste
    if perte <= TOLERANCE:
        dette, perte = reste, 0.0

    plan = {"conservees": conservees, "dette": dette, "perte": perte,
            "ligne_dette": None, "mode": MODE_DETTE, "montant_ligne": 0.0,
            "date_dette": None, "nouvelles": [],
            "supprimees": [l.get("nom") for l in dettes]}
    if not reste:
        return plan

    # nowdate() lit le site : on ne le consulte que si une ligne doit être datée.
    def jour():
        return aujourd_hui or nowdate()
    dates = [l.get("due_date") for l in conservees]
    # La ligne réutilisée porte la dette qui subsiste, sinon la perte.
    if dette:
        mode_ligne, montant_ligne = MODE_DETTE, dette
    else:
        mode_ligne, montant_ligne = MODE_PERTE, perte
    plan["mode"], plan["montant_ligne"] = mode_ligne, montant_ligne

    if dettes:
        plan["ligne_dette"] = dettes[0].get("nom")
        plan["supprimees"] = [l.get("nom") for l in dettes[1:]]
        if mode_ligne == MODE_PERTE:
            plan["date_dette"] = date_echeance_libre(dates, jour())
            dates.append(plan["date_dette"])
        else:
            dates.append(dettes[0].get("due_date"))
    else:
        neuve = _ligne_neuve(mode_ligne, montant_ligne, date_echeance_libre(dates, jour()))
        plan["nouvelles"].append(neuve)
        dates.append(neuve["due_date"])

    if dette and perte:
        # Reste dû partagé : la perte prend une ligne à part, datée du jour
        # (ERPNext refuse deux échéances à la même date).
        plan["nouvelles"].append(_ligne_neuve(MODE_PERTE, perte, date_echeance_libre(dates, jour())))
    return plan


def _ligne_neuve(mode, montant, due_date):
    return {
        "due_date": due_date,
        "payment_amount": montant,
        "invoice_portion": 0,
        "mode_of_payment": mode,
        "description": ("Reste dû sur le livré absorbé en perte (commande régularisée)"
                        if mode == MODE_PERTE else "Reste dû sur le livré (commande régularisée)"),
    }


def erreur_regularisation(commande, bilan_commande, regle_echeancier=None):
    """Pourquoi la régularisation est refusée — ou None si elle peut se faire.

    `commande` : dict (docstatus, status, per_billed).
    `regle_echeancier` : ce que l'échéancier porte hors dette (règlements,
    avoirs). S'il dépasse le livré, l'échéancier ne pourra pas descendre au
    livré : même refus que le trop-perçu, vu depuis l'échéancier (cas d'une
    commande payée par avoir client, sans Payment Entry).
    """
    if flt(commande.get("docstatus")) != 1:
        return "La commande doit être validée."
    statut = commande.get("status") or ""
    if statut in ("Closed", "Cancelled", "On Hold"):
        return "La commande est fermée, annulée ou en attente : rien à régulariser."
    if flt(commande.get("per_billed")) > 0:
        return ("La commande est déjà facturée : la facture fixe le montant dû, "
                "passez par un avoir sur la facture.")
    if not bilan_commande.get("partielle"):
        if bilan_commande.get("livre", 0) <= MARGE:
            return "Rien n'a encore été livré sur cette commande."
        return "La commande est entièrement livrée : sa dette est déjà la bonne."
    if bilan_commande.get("trop_percu", 0) > TOLERANCE:
        return ("Le client a réglé %s de plus que le livré : traitez d'abord ce "
                "trop-perçu (avoir ou remboursement)." % _lisible(bilan_commande["trop_percu"]))
    if regle_echeancier is not None:
        exces = _millimes(flt(regle_echeancier) - flt(bilan_commande.get("livre")))
        if exces > TOLERANCE:
            return ("L'échéancier porte %s de règlements ou d'avoirs de plus que le livré : "
                    "traitez d'abord ce trop-perçu (avoir ou remboursement)." % _lisible(exces))
    return None


def ligne_livraison_partielle(commande):
    """Une commande de la liste des livraisons partielles : son bilan, ce qui
    est revenu, et ce qui empêche encore de la régulariser (None = rien).

    `commande` : dict (name, docstatus, status, per_billed, total, livre,
    regle, dette_ligne, retourne, regle_echeancier, + colonnes d'affichage
    reprises telles quelles : customer, customer_name, transaction_date,
    currency).
    """
    b = bilan(commande.get("total"), commande.get("livre"),
              commande.get("regle"), commande.get("dette_ligne"))
    empechement = erreur_regularisation(commande, b, commande.get("regle_echeancier"))
    b.update({
        "name": commande.get("name"),
        "customer": commande.get("customer"),
        "customer_name": commande.get("customer_name") or commande.get("customer"),
        "date": str(commande.get("transaction_date") or ""),
        "status": commande.get("status"),
        "devise": commande.get("currency"),
        "retourne": _millimes(commande.get("retourne")),
        "empechement": empechement,
        "regularisable": not empechement,
    })
    return b


def _q(valeur):
    """Une quantité arrondie au millième — `flt(x, 3)` lit le format du site et
    renvoie 0 hors site, ce qui casserait les tests purs."""
    return round(flt(valeur), 3)


def _lisible(valeur):
    texte = "{:,.3f}".format(_millimes(valeur))
    return texte.replace(",", " ").replace(".", ",") + " DT"


# ── Lecture ──────────────────────────────────────────────────────────────────


def _chiffres(nom_commande):
    """Les trois sommes d'une commande, par la règle SQL partagée."""
    ligne = frappe.db.sql(
        f"""SELECT {SQL_TOTAL} AS total, {SQL_LIVRE} AS livre, {SQL_REGLE} AS regle,
                   {SQL_DETTE_LIGNE} AS dette_ligne, {SQL_RETOUR} AS retourne,
                   {SQL_REGLE_ECHEANCIER} AS regle_echeancier
            FROM `tabSales Order` so WHERE so.name = %(nom)s""",
        dict(PARAMS_SQL, nom=nom_commande), as_dict=True)
    return ligne[0] if ligne else None


def _bons_de_livraison(nom_commande):
    return frappe.db.sql(
        """SELECT dn.name, dn.posting_date, dn.grand_total, dn.is_return, dn.return_against
           FROM `tabDelivery Note` dn
           WHERE dn.docstatus = 1 AND EXISTS (
               SELECT 1 FROM `tabDelivery Note Item` dni
               WHERE dni.parent = dn.name AND dni.against_sales_order = %s)
           ORDER BY dn.posting_date, dn.name""",
        nom_commande, as_dict=True)


def _lignes_bl(nom_commande):
    return frappe.db.sql(
        """SELECT dni.so_detail, dni.qty, dni.amount
           FROM `tabDelivery Note Item` dni
           JOIN `tabDelivery Note` dn ON dn.name = dni.parent
           WHERE dn.docstatus = 1 AND dni.against_sales_order = %s""",
        nom_commande, as_dict=True)


def _commande(sales_order, permission):
    if not sales_order:
        frappe.throw(_("Commande manquante."))
    so = frappe.get_doc("Sales Order", sales_order)
    so.check_permission(permission)
    return so


def _resume(so):
    return {"docstatus": so.docstatus, "status": so.status, "per_billed": flt(so.per_billed)}


@frappe.whitelist()
def contexte(sales_order):
    """Ce que la fiche commande affiche : le bilan livré / réglé / dette, les BL,
    et si le bouton « Régulariser sur le livré » a lieu d'être."""
    so = _commande(sales_order, "read")
    chiffres = _chiffres(so.name) or {}
    b = bilan(chiffres.get("total"), chiffres.get("livre"),
              chiffres.get("regle"), chiffres.get("dette_ligne"))
    empechement = erreur_regularisation(_resume(so), b, chiffres.get("regle_echeancier"))
    b.update({
        "sales_order": so.name,
        "devise": so.currency,
        "retourne": _millimes(chiffres.get("retourne")),
        "bons": [{"name": d.name, "date": str(d.posting_date or ""),
                  "montant": _millimes(d.grand_total), "retour": bool(d.is_return),
                  "retour_de": d.return_against or ""} for d in _bons_de_livraison(so.name)],
        "regularisable": not empechement and bool(frappe.get_roles() and
                                                  set(frappe.get_roles()) & set(ROLES)),
        "empechement": empechement,
    })
    return b


# Une liste à consulter, pas un rapport : au-delà, filtrer dans la liste.
MAX_LIGNES = 200


@frappe.whitelist()
def livraisons_partielles():
    """Les commandes validées livrées EN PARTIE : livré (net des retours)
    strictement entre 0 et le total. Toutes, dette surévaluée ou non : c'est
    la liste sur laquelle on décide de ramener une commande au livré.

    Chaque ligne dit si « Régulariser sur le livré » peut se faire, et sinon
    pourquoi. `regularisable` tient compte des rôles de l'utilisateur.
    """
    frappe.has_permission("Sales Order", "read", throw=True)
    lignes = frappe.db.sql(
        f"""SELECT so.name, so.customer, so.customer_name, so.transaction_date,
                   so.status, so.docstatus, so.per_billed, so.currency,
                   {SQL_TOTAL} AS total, {SQL_LIVRE} AS livre, {SQL_RETOUR} AS retourne,
                   {SQL_REGLE} AS regle, {SQL_DETTE_LIGNE} AS dette_ligne,
                   {SQL_REGLE_ECHEANCIER} AS regle_echeancier
            FROM `tabSales Order` so
            WHERE so.docstatus = 1
            HAVING livre > %(marge)s AND livre < total - %(marge)s
            ORDER BY so.transaction_date DESC, so.name DESC
            LIMIT {MAX_LIGNES}""",
        PARAMS_SQL, as_dict=True)
    roles_ok = bool(set(frappe.get_roles()) & set(ROLES))
    commandes = []
    for l in lignes:
        ligne = ligne_livraison_partielle(l)
        ligne["regularisable"] = ligne["regularisable"] and roles_ok
        commandes.append(ligne)
    return {"commandes": commandes, "roles_ok": roles_ok}


# ── Régularisation ───────────────────────────────────────────────────────────


def _lignes_commande(so):
    return [{
        "name": r.name, "item_code": r.item_code, "qty": _q(r.qty),
        "delivered_qty": _q(r.delivered_qty), "rate": flt(r.rate),
        "uom": r.uom, "conversion_factor": r.conversion_factor,
        "delivery_date": r.delivery_date,
    } for r in so.items]


def _lignes_echeancier(so):
    return [{
        "nom": r.name, "idx": r.idx, "mode_of_payment": r.mode_of_payment,
        "payment_amount": _millimes(r.payment_amount), "due_date": r.due_date,
    } for r in so.payment_schedule]


@frappe.whitelist()
def regulariser(sales_order, perte=0):
    """Ramène la commande au livré et sa dette au reste dû (cf. en-tête).

    `perte` : la part du reste dû que l'on n'attend plus du client — 0 (tout
    reste en dette), une partie, ou le reste dû entier. Elle passe en
    « Perte de paiement ».

    Tout se passe dans la transaction de la requête : une erreur à n'importe
    quelle étape rembobine l'ensemble, rien ne reste à moitié fait.
    """
    from erpnext.controllers.accounts_controller import update_child_qty_rate

    frappe.only_for(ROLES)
    perte = _millimes(perte)
    so = _commande(sales_order, "write")
    chiffres = _chiffres(so.name) or {}
    b = bilan(chiffres.get("total"), chiffres.get("livre"),
              chiffres.get("regle"), chiffres.get("dette_ligne"))
    empechement = erreur_regularisation(_resume(so), b, chiffres.get("regle_echeancier"))
    if empechement:
        frappe.throw(empechement)

    articles = plan_articles(_lignes_commande(so), _lignes_bl(so.name))
    if articles.get("erreur"):
        frappe.throw(articles["erreur"])
    # Le TTC recalculé par ERPNext doit retomber sur le TTC des BL : sinon un
    # écart de taxe ou de timbre se cacherait dans la dette.
    if abs(articles["total"] - b["livre"]) > MARGE:
        frappe.throw(_("Les lignes ramenées au livré font {0} alors que les BL font {1} : "
                       "écart à comprendre avant de régulariser.")
                     .format(_lisible(articles["total"]), _lisible(b["livre"])))

    # a. Les articles, sous le drapeau qui fait taire le contrôle « échéancier = TTC »
    #    et la régénération des paiements : le TTC change, l'échéancier pas encore.
    so.db_set("custom_outil_de_paiement", 1, update_modified=False)
    update_child_qty_rate("Sales Order", json.dumps(articles["articles"]), so.name)

    # b. L'échéancier, sur le TTC recalculé, drapeau retiré : cette sauvegarde-ci
    #    passe le contrôle et déclenche la régénération des PE de dette / perte.
    so = frappe.get_doc("Sales Order", so.name)
    so.db_set("custom_outil_de_paiement", 0, update_modified=False)
    nouveau_total = flt(so.grand_total if so.disable_rounded_total else so.rounded_total)
    plan = plan_echeancier(_lignes_echeancier(so), nouveau_total, perte=perte)
    if plan.get("erreur"):
        frappe.throw(plan["erreur"])

    a_supprimer = set(plan["supprimees"])
    so.payment_schedule = [r for r in so.payment_schedule if r.name not in a_supprimer]
    for r in so.payment_schedule:
        r.invoice_portion = 0
        if r.name == plan["ligne_dette"]:
            r.payment_amount = plan["montant_ligne"]
            r.mode_of_payment = plan["mode"]
            if plan["date_dette"]:
                r.due_date = plan["date_dette"]
    for neuve in plan["nouvelles"]:
        so.append("payment_schedule", neuve)
    for position, r in enumerate(so.payment_schedule, start=1):
        r.idx = position
    so.save()

    frappe.msgprint(
        _("Commande ramenée à {0} (livré). Dette : {1}. Perte de paiement : {2}. "
          "Lignes retirées : {3}.").format(
            _lisible(nouveau_total), _lisible(plan["dette"]), _lisible(plan["perte"]),
            ", ".join(articles["supprimees"]) or _("aucune")),
        indicator="green", alert=True)
    return {
        "sales_order": so.name,
        "total": _millimes(nouveau_total),
        "dette": plan["dette"],
        "perte": plan["perte"],
        "supprimees": articles["supprimees"],
    }
