"""Majoration « paiement par carte » sur la commande client (demande du 18/11/2026).

La banque prend 3 % sur ce qui passe par le terminal de paiement. La société en garde 1 % à sa charge ; les
2 % restants sont ajoutés au prix de vente, sur la SEULE part réglée par « Carte de crédit » dans les termes
de paiement. Le taux est un réglage (« Bank Retenue Sync Settings » → « Majoration du prix pour un paiement
par carte (%) ») ; 0 ou réglage absent = aucune majoration.

CE QUE FAIT LE MODULE, À CHAQUE ENREGISTREMENT D'UNE COMMANDE EN BROUILLON (hook `validate`, après la marge
minimale) :
  1. il retrouve le prix DE BASE de chaque ligne (celui d'avant la majoration, gardé sur la ligne) ;
  2. il mesure la part carte : total de base − les échéances qui ne sont pas par carte ;
  3. majoration = part carte × taux, arrondie selon le réglage (par défaut : au dixième de dinar supérieur) ;
  4. il la RÉPARTIT sur tous les articles, dans la même proportion (aucune ligne « frais » à part) : chaque
     prix est multiplié par le même coefficient, et les millimes d'arrondi vont sur une seule ligne pour que
     le total tombe juste ;
  5. l'échéance par carte prend la majoration ; les autres échéances ne bougent pas.
Si la carte est retirée des termes de paiement, les prix de base reviennent tout seuls.

Exemple réel (SAL-ORD-2026-04724) : articles 284,000 + timbre 1,000 = 285,000, tout par carte. Majoration
5,700 ; les articles passent à 289,700, le timbre reste à 1,000, la carte encaisse 290,700. Sur 368,597, la
majoration de 7,372 est arrondie à 7,400 (dixième supérieur).
Paiement mixte : la majoration ne s'ajoute QUE sur l'échéance par carte — 341,000 dont 200,000 par carte
donnent espèces 141,000 et carte 204,000.

POURQUOI À L'ENREGISTREMENT ET PAS APRÈS : le prix d'une ligne ne se change plus une fois la commande
validée. Une commande validée qu'on bascule ensuite en carte n'est donc PAS majorée — elle le dit
(`avertir_apres_validation`).

LE PRIX MAJORÉ EST POSÉ COMME LA MARGE MINIMALE LE FAIT (`marge_minimale._relever` : remise réduite ou marge
sur le prix de liste), sans quoi ERPNext le recalculerait depuis le prix de liste au recalcul des totaux.
Chaque ligne garde son prix de base (`custom_prix_avant_carte`) et le prix posé (`custom_prix_avec_carte`) :
c'est ce qui rend le calcul rejouable à l'identique, et ce qui permet de reconnaître un prix retapé à la main
(il devient le nouveau prix de base).

LE PIÈGE DES RÈGLES DE PRIX : à chaque enregistrement, ERPNext ré-applique la règle de prix d'une ligne
(son prix retombe au prix de base) PUIS recalcule chaque échéance depuis sa part (%) du total — AVANT que ce
hook ne passe. Sur un échéancier à plusieurs lignes, les échéances hors carte en sortiraient réduites de
quelques dinars à chaque enregistrement. D'où `memoriser_echeancier` (hook `before_validate`) : les montants
tels qu'ils sont à l'écran sont gardés de côté, et ce sont eux qui font foi pour les échéances hors carte.

Les fonctions de calcul sont pures (pas de base, pas de `flt` : il vaut 0 hors site).
"""
from __future__ import annotations

import json
import math

import frappe
from frappe import _
from frappe.utils import cint, flt

MODE_CARTE = "Carte de crédit"
REGLAGE = "Bank Retenue Sync Settings"
CHAMP_TAUX_REGLAGE = "majoration_carte_taux"
CHAMP_MONTANT = "custom_majoration_carte"
CHAMP_TAUX = "custom_majoration_carte_taux"
CHAMP_AVANT = "custom_prix_avant_carte"
CHAMP_AVEC = "custom_prix_avec_carte"
TOL = 0.0005
CHAMP_PAS_REGLAGE = "majoration_carte_arrondi"
CHAMP_SENS_REGLAGE = "majoration_carte_arrondi_sens"
SENS_SUPERIEUR = "Supérieur"
#: L'arrondi de la majoration quand le réglage ne le dit pas : au dixième de dinar SUPÉRIEUR (demande du
#: 18/11/2026, « c'est plus simple ») — 7,372 -> 7,400 ; 5,700 -> 5,700 ; 0,240 -> 0,300.
PAS_ARRONDI = 0.1


# ----------------------------------------------------------------- calcul (fonctions pures)

def _r(v, decimales: int = 3) -> float:
    return round(float(v or 0) + 0.0, decimales)


def arrondir_au_pas(v: float, pas: float = PAS_ARRONDI, superieur: bool = True) -> float:
    """`v` arrondi à un multiple de `pas` : vers le HAUT (`superieur`), sinon au plus proche (la moitié monte).
    Une valeur déjà sur un multiple ne bouge pas. Pas nul : au millime."""
    if not pas or pas <= 0:
        return _r(v)
    crans = float(v or 0) / pas
    return _r((math.ceil(crans - 1e-9) if superieur else math.floor(crans + 0.5 + 1e-9)) * pas)


def montant_majoration(part_carte: float, taux: float, pas: float = PAS_ARRONDI, superieur: bool = True) -> float:
    """Ce qu'on ajoute pour `part_carte` réglés par carte : part × taux, arrondi selon le réglage (par défaut au
    dixième de dinar supérieur). 0 s'il n'y a rien à majorer."""
    part_carte, taux = float(part_carte or 0), float(taux or 0)
    if part_carte <= TOL or taux <= 0:
        return 0.0
    return arrondir_au_pas(part_carte * taux / 100.0, pas, superieur)


def tolerance_prix(prix: float) -> float:
    """L'écart en deçà duquel un prix est « le même » : 5 millimes, ou 0,002 % du prix s'il est élevé.

    Pas le millime : sur une ligne à règle de prix, ERPNext ne garde pas le prix mais la REMISE EN %, arrondie à
    trois décimales, et recalcule le prix à partir d'elle — un osmoseur à 650 avec 5 % de remise, majoré à
    629,812, revient à 629,811. Avec une tolérance au millime, ce prix passait pour « retapé à la main », devenait
    le nouveau prix de base, et la commande était majorée une seconde fois (constat du 18/11/2026 : une commande
    à prix ronds affichait 368,597 « sans carte »)."""
    return max(0.005, abs(float(prix or 0)) * 2e-5)


def prix_de_base(prix: float, avant: float, avec: float) -> float:
    """Le prix d'une ligne AVANT majoration.

    La ligne garde son prix de base (`avant`) et le prix majoré qu'on y a posé (`avec`). Tant que le prix
    affiché est l'un des deux (à `tolerance_prix` près), la base est `avant`. S'il est autre, quelqu'un a
    retapé le prix : c'est lui la nouvelle base."""
    prix, avant, avec = float(prix or 0), float(avant or 0), float(avec or 0)
    if avant and (abs(prix - avec) <= tolerance_prix(avec) or abs(prix - avant) <= tolerance_prix(avant)):
        return avant
    return prix


def prix_tenable(prix: float, prix_liste: float, decimales_remise: int = 3) -> float:
    """Le prix qu'ERPNext GARDERA sur une ligne à règle de prix, au plus près de `prix`.

    Sur ces lignes ERPNext recalcule : prix = prix de liste × (1 − remise %), la remise étant arrondie à
    `decimales_remise` décimales. On prend donc tout de suite ce prix-là pour faire les comptes : sinon la
    majoration posée et la majoration obtenue diffèrent de quelques millimes."""
    prix, prix_liste = float(prix or 0), float(prix_liste or 0)
    if prix_liste <= 0 or prix > prix_liste:
        return _r(prix)
    remise = round((1 - prix / prix_liste) * 100 + 0.0, decimales_remise)
    return _r(prix_liste * (1 - remise / 100.0))


#: Écart maximal (en millimes par unité) qu'une ligne peut prendre pour que le total tombe juste.
RETOUCHE_MAX = 60


def repartir(lignes: list, majoration: float) -> list:
    """Les prix majorés de `lignes` ([{base, qty}], plus `prix_liste` et `regle` pour une ligne à règle de
    prix), dans le même ordre.

    Répartition LINÉAIRE : chaque prix est multiplié par le même coefficient (1 + majoration ÷ total des
    lignes). Un prix s'arrondit au millime et se multiplie par une quantité : le total obtenu peut manquer
    la cible de quelques millimes. On les rattrape, pour que la majoration tombe JUSTE (elle est arrondie, la
    carte doit encaisser le montant annoncé) :
      1. sur UNE ligne dont la quantité divise l'écart (à l'unité : toujours) ;
      2. sinon sur DEUX lignes, de quelques millimes par unité chacune (`_retoucher_deux_lignes`).
    Une ligne à RÈGLE DE PRIX ne prend que les prix qu'ERPNext sait garder (`prix_tenable`) et ne sert pas aux
    retouches. Quand aucune combinaison n'existe — un seul article en 34 exemplaires : 4,000 ne se divise pas
    par 34 au millime — l'écart reste, au plus près. Sans majoration ou sans total positif : les prix de base."""
    bases = [float(l.get("base") or 0) for l in lignes]
    qtes = [float(l.get("qty") or 0) for l in lignes]
    total = _r(sum(_r(b * q) for b, q in zip(bases, qtes)))
    majoration = float(majoration or 0)
    if majoration <= 0 or total <= TOL:
        return bases
    coefficient = 1 + majoration / total
    prix = [_r(b * coefficient) for b in bases]
    figees = set()
    for i, l in enumerate(lignes):
        if l.get("regle") and float(l.get("prix_liste") or 0) > 0:
            prix[i] = prix_tenable(prix[i], l["prix_liste"], int(l.get("decimales_remise") or 3))
            figees.add(i)
    reste = _r(total + majoration - sum(_r(p * q) for p, q in zip(prix, qtes)))
    if abs(reste) >= 0.001:
        ordre = [i for i in sorted(range(len(lignes)), key=lambda i: -abs(prix[i] * qtes[i])) if i not in figees]
        for i in ordre:
            if qtes[i] <= 0:
                continue
            pas = reste / qtes[i]
            if abs(pas * 1000 - round(pas * 1000)) < 1e-6 and abs(pas) * 1000 <= RETOUCHE_MAX + 1e-6:
                prix[i] = _r(prix[i] + pas)
                return prix
        _retoucher_deux_lignes(prix, qtes, reste, ordre)
    return prix


def _retoucher_deux_lignes(prix: list, qtes: list, reste: float, ordre: list) -> bool:
    """Rattrape `reste` en retouchant DEUX lignes de a et b millimes par unité : a × q1 + b × q2 = reste.
    Lignes à quantité entière seulement, les plus grosses d'abord, et la retouche la plus petite possible.
    Modifie `prix` en place ; -> True si une combinaison a été trouvée."""
    cible = int(round(reste * 1000))
    entieres = [i for i in ordre if qtes[i] > 0 and abs(qtes[i] - round(qtes[i])) < 1e-9][:8]
    meilleur = None
    for x, i in enumerate(entieres):
        for j in entieres[x + 1:]:
            qi, qj = int(round(qtes[i])), int(round(qtes[j]))
            for a in range(-RETOUCHE_MAX, RETOUCHE_MAX + 1):
                restant = cible - a * qi
                if restant % qj:
                    continue
                b = restant // qj
                if abs(b) > RETOUCHE_MAX:
                    continue
                cout = abs(a) + abs(b)
                if meilleur is None or cout < meilleur[0]:
                    meilleur = (cout, i, a, j, b)
    if not meilleur:
        return False
    _cout, i, a, j, b = meilleur
    prix[i] = _r(prix[i] + a / 1000.0)
    prix[j] = _r(prix[j] + b / 1000.0)
    return True


# ----------------------------------------------------------------- réglage

def taux_regle() -> float:
    """Le taux réglé (%), 0 si le réglage n'existe pas encore (application bancaire absente ou pas migrée)."""
    try:
        return max(flt(frappe.db.get_single_value(REGLAGE, CHAMP_TAUX_REGLAGE)), 0.0)
    except Exception:
        return 0.0


def arrondi_regle() -> tuple:
    """(pas, superieur) : l'arrondi de la majoration choisi dans le réglage ; (0,1 ; supérieur) tant que les
    champs n'existent pas ou n'ont jamais été enregistrés. Un pas de 0 est un choix : pas d'arrondi."""
    # Lecture directe : `get_single_value` rend 0 pour un champ jamais enregistré, ce qui se lirait « pas d'arrondi ».
    try:
        lus = dict(frappe.db.sql("select field, value from tabSingles where doctype = %s and field in (%s, %s)",
                                 (REGLAGE, CHAMP_PAS_REGLAGE, CHAMP_SENS_REGLAGE)))
    except Exception:
        return PAS_ARRONDI, True
    pas = max(flt(lus[CHAMP_PAS_REGLAGE]), 0.0) if lus.get(CHAMP_PAS_REGLAGE) not in (None, "") else PAS_ARRONDI
    return pas, (lus.get(CHAMP_SENS_REGLAGE) or SENS_SUPERIEUR) == SENS_SUPERIEUR


def majoration_reglee(part_carte: float, taux: float) -> float:
    """La majoration d'une part carte, avec l'arrondi du réglage."""
    pas, superieur = arrondi_regle()
    return montant_majoration(part_carte, taux, pas, superieur)


@frappe.whitelist()
def taux_pour_ecran() -> dict:
    """Le taux réglé et le pas d'arrondi, pour l'écran : le bloc de paiement affiche tout de suite le montant de
    la carte majoration comprise, avant même la réponse de l'aperçu."""
    pas, superieur = arrondi_regle()
    return {"taux": taux_regle(), "pas": pas, "superieur": superieur}


def _champs_presents(doc) -> bool:
    return bool(doc.meta.has_field(CHAMP_MONTANT)) and bool(doc.meta.get_field("items")) and \
        frappe.get_meta(doc.doctype + " Item").has_field(CHAMP_AVANT)


# ----------------------------------------------------------------- application

def _total(doc) -> float:
    """Le total que l'échéancier doit égaler — même règle qu'ERPNext (`rounded_total` sinon `grand_total`)."""
    return flt(doc.get("rounded_total")) or flt(doc.get("grand_total"))


def _lignes_majorables(doc) -> list:
    return [l for l in (doc.get("items") or []) if l.item_code and not l.get("is_free_item")]


def _etat(doc, taux=None, total_avant=None, carte_avant=0.0, change=False) -> dict:
    majoration = flt(doc.get(CHAMP_MONTANT), 3)
    total = flt(_total(doc), 3)
    carte = flt(sum(flt(e.payment_amount) for e in (doc.get("payment_schedule") or [])
                    if e.mode_of_payment == MODE_CARTE), 3)
    return {
        "taux": flt(doc.get(CHAMP_TAUX)) if taux is None else flt(taux),
        "majoration": majoration,
        "total_avant": flt(total - majoration if total_avant is None else total_avant, 3),
        "total_apres": total,
        "carte_avant": flt(carte_avant, 3),
        "carte_apres": carte,
        # False = la commande telle qu'elle est porte déjà exactement cet état (rien n'a été retouché).
        "change": bool(change),
    }


def appliquer(doc, prevenir: bool = True) -> dict:
    """Pose (ou retire) la majoration carte sur une commande en BROUILLON. Rejouable : sur une commande déjà
    majorée et inchangée, rien ne bouge. Rend l'état (voir `_etat`)."""
    if doc.doctype != "Sales Order" or doc.docstatus != 0 or not _champs_presents(doc):
        return _etat(doc)
    echeances = doc.get("payment_schedule") or []
    par_carte = [e for e in echeances if e.mode_of_payment == MODE_CARTE]
    lignes = _lignes_majorables(doc)
    deja = flt(doc.get(CHAMP_MONTANT)) or any(flt(l.get(CHAMP_AVANT)) for l in (doc.get("items") or []))
    # Commande du site web : le client a déjà payé en ligne le prix affiché, on n'y touche pas.
    taux = 0.0 if doc.get("woocommerce_id") else taux_regle()
    if not deja and (not par_carte or taux <= 0):
        return _etat(doc, taux=0)                      # le cas de presque toutes les commandes : rien à faire

    avant_montant, photo = flt(doc.get(CHAMP_MONTANT), 3), _photo(doc)

    # 1. Retour aux prix de base, pour mesurer le total d'avant majoration.
    bases = [prix_de_base(l.rate, l.get(CHAMP_AVANT), l.get(CHAMP_AVEC)) for l in lignes]
    if any(abs(flt(l.rate) - b) > TOL for l, b in zip(lignes, bases)):
        for l, b in zip(lignes, bases):
            if abs(flt(l.rate) - b) > TOL:
                _poser(l, b)
        doc.calculate_taxes_and_totals()
    total_base = flt(_total(doc), 3)

    # 2. La part carte : ce que les autres échéances ne règlent pas. Leurs montants sont ceux de l'écran
    #    (`memoriser_echeancier`), pas ceux qu'ERPNext vient de recalculer sur un total intermédiaire.
    ecran = _montants_ecran(doc, echeances)
    for e, montant in zip(echeances, ecran):
        if e.mode_of_payment != MODE_CARTE and abs(flt(e.payment_amount) - montant) >= 0.001:
            e.payment_amount = montant
    autres = flt(sum(flt(e.payment_amount) for e in echeances if e.mode_of_payment != MODE_CARTE), 3)
    part_carte = flt(total_base - autres, 3) if par_carte else 0.0
    majoration = majoration_reglee(part_carte, taux)

    # 3. Répartition sur les articles.
    participantes = [(l, b) for l, b in zip(lignes, bases) if abs(b) > TOL and flt(l.qty)]
    if majoration > 0 and participantes:
        prix = repartir([{"base": b, "qty": flt(l.qty), "prix_liste": flt(l.price_list_rate),
                          "regle": bool(_a_une_regle(l)) and flt(l.discount_percentage) > 0,
                          "decimales_remise": l.precision("discount_percentage") or 3}
                         for l, b in participantes], majoration)
        for (l, b), p in zip(participantes, prix):
            if abs(flt(l.rate) - p) > TOL:
                _poser(l, p)
        doc.calculate_taxes_and_totals()
    else:
        majoration, participantes = 0.0, []
    total_final = flt(_total(doc), 3)
    # Ce qui a VRAIMENT été ajouté (au millime près de la cible, quand aucune ligne ne permet de tomber juste).
    majoration = flt(total_final - total_base, 3) if participantes else 0.0

    # 4. La trace sur les lignes et sur la commande.
    gardees = {id(l): b for l, b in participantes} if majoration > 0 else {}
    for l in doc.get("items") or []:
        l.set(CHAMP_AVANT, gardees.get(id(l), 0))
        l.set(CHAMP_AVEC, flt(l.rate) if id(l) in gardees else 0)
    doc.set(CHAMP_MONTANT, majoration)
    doc.set(CHAMP_TAUX, taux if majoration > 0 else 0)

    # 5. L'échéancier suit le total : la carte prend la majoration (ou, la carte retirée, la dernière
    #    échéance rend ce qu'elle portait en trop).
    _realigner_echeancier(doc, total_final, par_carte)

    # `change` : la commande est-elle sortie différente de ce qu'elle était en entrant ? (L'aperçu s'en
    # sert pour dire « appliquée » ou « sera appliquée à l'enregistrement ».)
    etat = _etat(doc, taux=taux if majoration > 0 else 0, total_avant=total_base, carte_avant=part_carte,
                 change=_photo(doc) != photo)
    if prevenir and abs(majoration - avant_montant) >= 0.001:
        _prevenir(etat, avant_montant)
    return etat


def _a_une_regle(ligne) -> bool:
    """La ligne porte-t-elle une règle de prix (champ `pricing_rules` : JSON d'une liste, vide ou non) ?"""
    brut = (ligne.get("pricing_rules") or "").strip()
    return bool(brut) and brut not in ("[]", "null")


def _photo(doc) -> tuple:
    """Ce que la majoration peut changer sur une commande : prix et trace des lignes, montant porté, échéances."""
    return (tuple((flt(l.rate, 3), flt(l.get(CHAMP_AVANT), 3), flt(l.get(CHAMP_AVEC), 3)) for l in (doc.get("items") or [])),
            flt(doc.get(CHAMP_MONTANT), 3),
            tuple(flt(e.payment_amount, 3) for e in (doc.get("payment_schedule") or [])))


def _poser(ligne, prix: float):
    from customization_app.marge_minimale import _relever

    _relever(ligne, flt(prix, 3))


def memoriser_echeancier(doc, method=None):
    """Hook `before_validate` : garde de côté les montants de l'échéancier tels qu'ils arrivent de l'écran,
    avant qu'ERPNext ne les recalcule depuis leurs parts (voir « le piège des règles de prix » en tête)."""
    if doc.doctype != "Sales Order" or doc.docstatus != 0:
        return
    echeances = doc.get("payment_schedule") or []
    if len(echeances) < 2 or not any(e.mode_of_payment == MODE_CARTE for e in echeances):
        return                                   # une seule échéance : elle vaut toujours le total
    doc.flags.majoration_carte_ecran = [(e.mode_of_payment, flt(e.payment_amount, 3)) for e in echeances]


def _montants_ecran(doc, echeances: list) -> list:
    """Les montants des échéances tels que l'écran les a envoyés ; à défaut (aperçu, script, échéancier
    refait entre-temps), ceux qu'elles portent."""
    garde = doc.flags.get("majoration_carte_ecran") or []
    if len(garde) == len(echeances) and all(m == e.mode_of_payment for (m, _montant), e in zip(garde, echeances)):
        return [montant for _mode, montant in garde]
    return [flt(e.payment_amount, 3) for e in echeances]


def _realigner_echeancier(doc, total: float, par_carte: list):
    """La somme des échéances redevient le total : l'écart va sur la carte (sa dernière échéance), sinon sur
    la dernière échéance. Les parts (%) sont refaites sur le nouveau total — ERPNext recalcule chaque montant
    depuis sa part à l'enregistrement suivant, une part fausse déferait donc le travail. Une échéance sans
    part (montant fixe : avoir client…) reste sans part."""
    echeances = doc.get("payment_schedule") or []
    if not echeances:
        return
    ecart = flt(total - sum(flt(e.payment_amount) for e in echeances), 3)
    if abs(ecart) >= 0.001:
        cible = (par_carte or echeances)[-1]
        cible.payment_amount = flt(flt(cible.payment_amount) + ecart, 3)
    for e in echeances:
        if flt(e.invoice_portion) and total:
            e.invoice_portion = flt(e.payment_amount) / total * 100
        e.base_payment_amount = flt(flt(e.payment_amount) * flt(doc.conversion_rate or 1), 3)
        if doc.docstatus == 0:
            e.outstanding = e.payment_amount
            if e.meta.has_field("base_outstanding"):
                e.base_outstanding = e.base_payment_amount


def _nombre(v) -> str:
    return "{:,.3f}".format(flt(v, 3)).replace(",", " ").replace(".", ",")


def _prevenir(etat: dict, avant: float):
    if etat["majoration"] > 0:
        message = _("Paiement par carte : majoration de {0} % sur la part réglée par carte, soit "
                    "<b>+ {1}</b>, répartie sur les prix de tous les articles.<br>"
                    "Total de la commande : {2} → <b>{3}</b>. Montant à passer par carte : <b>{4}</b>.").format(
            _taux_txt(etat["taux"]), _nombre(etat["majoration"]), _nombre(etat["total_avant"]),
            _nombre(etat["total_apres"]), _nombre(etat["carte_apres"]))
        indicateur = "blue"
    else:
        message = _("Plus de paiement par carte : la majoration de {0} a été retirée, les articles ont "
                    "retrouvé leurs prix. Total de la commande : <b>{1}</b>.").format(
            _nombre(avant), _nombre(etat["total_apres"]))
        indicateur = "orange"
    frappe.msgprint(message, title=_("Majoration paiement par carte"), indicator=indicateur)


def _taux_txt(taux) -> str:
    """2.0 -> « 2 » ; 2.5 -> « 2,5 »."""
    return ("%g" % flt(taux)).replace(".", ",")


# ----------------------------------------------------------------- hooks

def sales_order_validate(doc, method=None):
    appliquer(doc)


def _part_carte(doc) -> float:
    return flt(sum(flt(e.payment_amount) for e in (doc.get("payment_schedule") or [])
                   if e.mode_of_payment == MODE_CARTE), 3) if doc else 0.0


def avertir_apres_validation(doc, method=None):
    """Échéancier modifié sur une commande DÉJÀ validée : les prix ne se changent plus (le bon de livraison
    existe déjà, aux prix de la commande). On le DIT, sans bloquer — l'encaissement réel doit pouvoir se saisir :
      - une part passe à la carte : sa majoration n'est PAS appliquée ;
      - la carte est retirée d'une commande majorée : la majoration RESTE dans les prix.
    Seulement quand la part carte vient de changer : pas de message à chaque enregistrement de la commande."""
    if doc.doctype != "Sales Order" or doc.get("woocommerce_id") or not doc.meta.has_field(CHAMP_MONTANT):
        return
    avant, apres = _part_carte(doc.get_doc_before_save()), _part_carte(doc)
    deja = flt(doc.get(CHAMP_MONTANT), 3)
    if apres > avant + 0.005:
        taux = taux_regle()
        manque = majoration_reglee(apres - avant, taux)
        if manque >= 0.01:
            frappe.msgprint(
                _("Cette commande est déjà validée : ses prix ne peuvent plus être augmentés. La majoration de "
                  "{0} % pour paiement par carte (<b>{1}</b> sur {2} passés à la carte) n'est donc PAS appliquée : "
                  "le client paie {2} par carte, et la commission de la banque reste entièrement à votre charge. "
                  "Pour l'appliquer, annulez la commande et refaites-la avec le paiement par carte.").format(
                    _taux_txt(taux), _nombre(manque), _nombre(apres - avant)),
                title=_("Majoration paiement par carte"), indicator="orange")
    elif avant > apres + 0.005 and deja > 0:
        frappe.msgprint(
            _("Cette commande est déjà validée : ses prix ne peuvent plus être baissés. La majoration pour "
              "paiement par carte (<b>{0}</b>) RESTE donc dans les prix, alors que {1} ne passent plus par la "
              "carte. Pour la retirer, annulez la commande et refaites-la.").format(
                _nombre(deja), _nombre(avant - apres)),
            title=_("Majoration paiement par carte"), indicator="orange")


# ----------------------------------------------------------------- écran et impression

@frappe.whitelist()
def apercu(doc) -> dict:
    """La majoration que l'enregistrement posera sur la commande telle qu'elle est À L'ÉCRAN, sans rien
    enregistrer : même calcul que le hook, sur une copie. Rend l'état (`_etat`) plus `lignes` et `echeances` :
    les prix et les montants à poser à l'écran. Le formulaire les applique aussitôt
    (public/js/sales_order_paiement.js) : le vendeur voit les prix et le total augmenter dès qu'il choisit la
    carte, et lit le montant exact à passer sur le terminal avant d'enregistrer."""
    donnees = json.loads(doc) if isinstance(doc, str) else dict(doc or {})
    if donnees.get("doctype") != "Sales Order":
        frappe.throw(_("Aperçu réservé à la commande client."))
    frappe.has_permission("Sales Order", "read", throw=True)
    copie = frappe.get_doc(donnees)
    if copie.docstatus != 0:
        return _etat(copie)
    copie.calculate_taxes_and_totals()
    etat = appliquer(copie, prevenir=False)
    # Ce que l'écran doit porter pour être déjà dans l'état que l'enregistrement posera : le formulaire
    # l'applique tout de suite (prix des lignes, échéances), l'augmentation se voit donc EN TEMPS RÉEL.
    etat["lignes"] = [{"name": l.name, "rate": flt(l.rate, 3), "avant": flt(l.get(CHAMP_AVANT), 3),
                       "avec": flt(l.get(CHAMP_AVEC), 3)} for l in (copie.get("items") or [])]
    etat["echeances"] = [{"name": e.name, "payment_amount": flt(e.payment_amount, 3),
                          "invoice_portion": flt(e.invoice_portion)} for e in (copie.get("payment_schedule") or [])]
    return etat


def mention(doc) -> dict | None:
    """Pour le bas de la commande et du bon de livraison : {"montant", "taux", "taux_txt"} ou None.

    Commande : la majoration qu'elle porte. Bon de livraison (ou facture) : la part de la majoration de
    sa commande qui revient aux articles qu'il porte (au prorata des montants). Ne lève jamais : une
    impression ne doit pas échouer pour une mention."""
    try:
        if doc.doctype == "Sales Order":
            montant, taux = flt(doc.get(CHAMP_MONTANT), 3), flt(doc.get(CHAMP_TAUX))
        else:
            champ = {"Delivery Note": "against_sales_order", "Sales Invoice": "sales_order"}.get(doc.doctype)
            if not champ or doc.get("is_return") or not frappe.get_meta("Sales Order").has_field(CHAMP_MONTANT):
                return None
            par_commande: dict = {}
            for l in doc.get("items") or []:
                if l.get(champ):
                    par_commande[l.get(champ)] = par_commande.get(l.get(champ), 0.0) + flt(l.amount)
            montant, taux = 0.0, 0.0
            for commande, porte in par_commande.items():
                c = frappe.db.get_value("Sales Order", commande, [CHAMP_MONTANT, CHAMP_TAUX, "total"], as_dict=True)
                if not c or flt(c.get(CHAMP_MONTANT)) <= 0 or flt(c.total) <= 0:
                    continue
                montant += flt(c.get(CHAMP_MONTANT)) * min(max(porte / flt(c.total), 0.0), 1.0)
                taux = flt(c.get(CHAMP_TAUX))
            montant = flt(montant, 3)
        if montant < 0.001:
            return None
        return {"montant": montant, "taux": taux, "taux_txt": _taux_txt(taux)}
    except Exception:
        return None
