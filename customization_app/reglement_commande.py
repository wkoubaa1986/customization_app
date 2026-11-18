"""Comment une commande est payée — pour le bas de la commande et du bon de livraison (demande du 18/11/2026).

« Je veux, dans les BL et commandes, montrer la majoration par carte sur le montant payé, et dire comment la
commande est payée ; pour un BL, faire référence au paiement de la commande à laquelle il est lié ; pour les
factures, ne rien changer. »

`resume(doc)` rend, pour chaque commande concernée, les lignes de son échéancier dites en clair : « Espèces
100,000 », « Chèque 40,000 (n° 1234567, BIAT) », « Carte bancaire 102,000 (100,000 + majoration de 2 % :
2,000) », « Avoir client 78,000 », « Reste à payer 24,083 ». La majoration carte est celle que porte la commande
(customization_app.majoration_carte), montrée sur le paiement par carte — répartie entre eux s'il y en a
plusieurs. Une commande : elle-même. Un bon de livraison : la ou les commandes de ses lignes.

« Si la valeur du BL est égale à celle de sa commande, ne pas mentionner la commande, dire juste Règlement —
pareil pour la ristourne ; sinon, faire référence à la commande et à son total » (18/11/2026) : un BL qui livre
toute la commande s'imprime comme elle ; un BL partiel dit « Ce BL fait partie de la commande SAL-ORD-… (total
264.083), réglée ainsi : … » et « Ristourne appliquée sur la commande SAL-ORD-… ».

« Aussi, s'il y a une ristourne appliquée ou un avoir appliqué, le mentionner à la fin » : l'avoir est une ligne
du règlement (« Avoir client utilisé 78,000 ») ; la ristourne — la remise supplémentaire de la commande quand
« Appliquer Ristourne » est à Oui — est dite à part (`ristourne_txt`), elle est déjà déduite du total.

Place sur la page (18/11/2026, « pourquoi c'est devenu divisé en deux en PDF ») : le pied de ces modèles tient tout
juste sur une page — le tableau des articles est complété par des lignes vides jusqu'à 7. Trois lignes de texte
en plus faisaient passer tout le pied sur une deuxième page. `hauteur` estime le nombre de lignes imprimées du
bloc ; le modèle retire autant de lignes VIDES du tableau (jamais une ligne d'article) : la page garde sa hauteur.

Lecture seule ; ne lève jamais (une impression ne doit pas échouer pour un bloc d'information).
"""
from __future__ import annotations

import frappe
from frappe.utils import flt

MODE_CARTE = "Carte de crédit"
#: Ce qui s'imprime pour chaque moyen de paiement (à défaut : le nom du moyen).
LIBELLES = {
    "Carte de crédit": "Carte bancaire",
    "Dette non payée": "Reste à payer",
    "Traite bancaire LC": "Traite",
    "Retenue a la source vente": "Retenue à la source",
    "Avoir client": "Avoir client utilisé",
}
#: Caractères par ligne imprimée au bas de la page (11 px, texte aligné à droite) — estimation PRUDENTE : compter
#: une ligne de trop retire une ligne vide de plus, sans dommage ; en compter une de moins ferait déborder.
LARGEUR_LIGNE = 95
CHAMP_PIECE = "custom__n_chèque__transaction"
CHAMP_BANQUE = "custom_banque"


def nombre(v) -> str:
    """285.5 -> « 285.500 » : le format des montants de ces modèles d'impression."""
    return "{:,.3f}".format(float(v or 0))


def taux_txt(taux) -> str:
    return ("%g" % float(taux or 0)).replace(".", ",")


def lignes_de_reglement(echeances: list, majoration: float = 0.0, taux: float = 0.0) -> list:
    """Les lignes à imprimer pour un échéancier. PURE.

    `echeances` : [{mode_of_payment, payment_amount, pièce, banque}]. Les échéances à 0 sont passées. La
    majoration est montrée sur le paiement par carte : « 100,000 + majoration de 2 % : 2,000 » ; s'il y a
    plusieurs paiements par carte, elle est répartie au prorata, le dernier prenant l'arrondi."""
    payees = [e for e in (echeances or []) if abs(float(e.get("payment_amount") or 0)) >= 0.0005]
    cartes = [e for e in payees if e.get("mode_of_payment") == MODE_CARTE]
    total_carte = sum(float(e.get("payment_amount") or 0) for e in cartes)
    majoration = float(majoration or 0) if total_carte > 0 else 0.0
    reste, out = round(majoration, 3), []
    for e in payees:
        mode, montant = e.get("mode_of_payment") or "", float(e.get("payment_amount") or 0)
        details = []
        if mode == MODE_CARTE and majoration > 0:
            part = reste if e is cartes[-1] else round(majoration * montant / total_carte, 3)
            reste = round(reste - part, 3)
            if part >= 0.001:
                avec_taux = (" de %s %%" % taux_txt(taux)) if float(taux or 0) > 0 else ""
                details.append("%s + majoration%s : %s" % (nombre(montant - part), avec_taux, nombre(part)))
        piece, banque = str(e.get(CHAMP_PIECE) or "").strip(), str(e.get(CHAMP_BANQUE) or "").strip()
        if piece and mode != "Dette non payée":
            details.append(("n° %s" % piece) + ((", %s" % banque) if banque else ""))
        out.append({"mode": mode, "libelle": LIBELLES.get(mode, mode or "Paiement"), "montant": round(montant, 3),
                    "montant_txt": nombre(montant), "detail": " ; ".join(details)})
    return out


def texte_reglement(titre: str, lignes: list) -> str:
    """Le règlement tel qu'il s'imprime, en texte : « Règlement : Espèces 50.000 · Chèque 40.000 (n° …) ». PURE."""
    morceaux = [("%s %s" % (l["libelle"], l["montant_txt"])) + ((" (%s)" % l["detail"]) if l.get("detail") else "")
                for l in (lignes or [])]
    return ("%s : %s" % (titre, " · ".join(morceaux))) if morceaux else ""


def lignes_imprimees(texte: str, largeur: int = LARGEUR_LIGNE) -> int:
    """Nombre de lignes qu'occupe un texte au bas de la page (0 s'il est vide). PURE."""
    n = len((texte or "").strip())
    return 0 if not n else -(-n // max(1, int(largeur)))


def hauteur(titre: str, lignes: list, texte_ristourne: str = "") -> int:
    """Lignes imprimées du bloc : le règlement (replié sur la largeur) + la ligne de ristourne. PURE."""
    return lignes_imprimees(texte_reglement(titre, lignes)) + lignes_imprimees(texte_ristourne)


def ristourne_appliquee(choix, remise) -> float:
    """Le montant de ristourne que porte la commande : sa remise supplémentaire quand « Appliquer Ristourne » est
    à Oui ; 0 sinon. PURE."""
    oui = choix in (1, True, "Oui", "Yes", "1")
    remise = round(float(remise or 0), 3)
    return remise if oui and remise >= 0.001 else 0.0


def meme_valeur(total_document, total_commande) -> bool:
    """Le bon de livraison vaut-il toute sa commande ? (au centime près : les arrondis de taxes). PURE."""
    return abs(float(total_document or 0) - float(total_commande or 0)) < 0.005


def titres(commande: str, total_commande, entier: bool) -> dict:
    """Les mots qui introduisent le règlement et la ristourne. PURE.

    `entier` : le document imprimé est la commande, ou un BL de même valeur — on dit « Règlement », sans citer
    la commande. Sinon (BL partiel, ou BL de plusieurs commandes) on cite la commande et son total."""
    if entier:
        return {"titre": "Règlement", "titre_ristourne": "Ristourne appliquée", "note_ristourne": "déjà déduite du total"}
    return {"titre": "Ce BL fait partie de la commande %s (total %s), réglée ainsi" % (commande, nombre(total_commande)),
            "titre_ristourne": "Ristourne appliquée sur la commande %s" % commande,
            "note_ristourne": "déjà déduite du total de la commande"}


def _commandes_du_document(doc) -> list:
    """Les commandes dont il faut dire le règlement : la commande elle-même, ou celles d'un bon de livraison."""
    if doc.doctype == "Sales Order":
        return [doc]
    if doc.doctype != "Delivery Note" or doc.get("is_return"):
        return []
    noms = []
    for l in doc.get("items") or []:
        if l.get("against_sales_order") and l.against_sales_order not in noms:
            noms.append(l.against_sales_order)
    if not noms and doc.get("custom_commande"):
        noms = [doc.custom_commande]
    return [frappe.get_doc("Sales Order", n) for n in noms[:4] if frappe.db.exists("Sales Order", n)]


def resume(doc) -> list:
    """[{commande, entier, titre, lignes, total_txt, ristourne_txt, titre_ristourne, note_ristourne, hauteur}] — une
    entrée par commande ; [] s'il n'y a rien à dire."""
    try:
        out = []
        commandes = [c for c in _commandes_du_document(doc) if c.docstatus != 2]
        for commande in commandes:
            entier = doc.doctype == "Sales Order" or (
                len(commandes) == 1 and meme_valeur(doc.get("grand_total"), commande.get("grand_total")))
            echeances = [{"mode_of_payment": e.mode_of_payment, "payment_amount": e.payment_amount,
                          CHAMP_PIECE: e.get(CHAMP_PIECE), CHAMP_BANQUE: e.get(CHAMP_BANQUE)}
                         for e in (commande.get("payment_schedule") or [])]
            lignes = lignes_de_reglement(echeances, flt(commande.get("custom_majoration_carte")),
                                         flt(commande.get("custom_majoration_carte_taux")))
            if not lignes and not ristourne_appliquee(commande.get("custom_appliquer_ristourne"), commande.get("discount_amount")):
                continue
            total = round(sum(l["montant"] for l in lignes), 3)
            ristourne = ristourne_appliquee(commande.get("custom_appliquer_ristourne"), commande.get("discount_amount"))
            t = titres(commande.name, commande.get("grand_total"), entier)
            ristourne_txt = nombre(ristourne) if ristourne else ""
            phrase_ristourne = ("%s : %s (%s)" % (t["titre_ristourne"], ristourne_txt, t["note_ristourne"])) if ristourne else ""
            out.append({"commande": commande.name, "entier": entier, "lignes": lignes, "total": total,
                        "total_txt": nombre(total), "ristourne": ristourne, "ristourne_txt": ristourne_txt,
                        "hauteur": hauteur(t["titre"], lignes, phrase_ristourne), **t})
        return out
    except Exception:
        return []
