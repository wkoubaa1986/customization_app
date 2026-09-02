"""Annuler une facture rend le paiement à la ou aux commandes qui l'ont générée.

LE BESOIN (02/09/2026, mot de l'utilisateur) : « lorsque je crée une facture le
paiement devient lié à la facture ; lorsque j'annule la facture ça doit se
réaffecter à la commande qui a généré ou aux commandes qui ont généré la
facture, avec les proportions de base ».

CE QUI SE PASSAIT AVANT. Deux Server Scripts s'en chargeaient, et tous les deux
manquaient leur cible :
  - ils cherchaient le numéro de commande dans le LIBELLÉ du paiement, en ne
    retenant que les mots commençant par « SAL-ORD ». Les commandes web
    s'appelant « WEB1-… », elles n'étaient JAMAIS reconnues : rien n'était
    réaffecté, le paiement finissait entièrement non alloué et la commande
    gardait une avance que plus aucun paiement ne soutenait — l'argent comptait
    deux fois. Reproduit sur ACC-SINV-2026-01247 / WEB1-008041 (679 DT).
  - sur une commande classique, ils SUPPRIMAIENT le paiement pour le recréer
    sous un nouveau numéro (ACC-PAY-2026-06176 → 06379), avec Administrator
    pour auteur et la date du jour. Tout ce qui citait l'ancien numéro cassait,
    à commencer par le registre bancaire de bank_retenue_sync, qui stocke le
    NOM du Payment Entry.

CE QUE FAIT CELUI-CI, ET POURQUOI.

1. LA COMMANDE VIENT DES LIGNES DE LA FACTURE, pas d'un texte libre.
   `Sales Invoice Item.sales_order` porte l'information, exacte, quelle que
   soit la série de numérotation. Deviner dans des remarques rédigées pour un
   humain, c'était se condamner au premier changement de formulation.

2. LE PRORATA VIENT DES MONTANTS DE CES LIGNES. Une facture peut naître de
   plusieurs commandes (75 en base, jusqu'à huit) : chacune reprend sa part.
   Les lignes SANS commande (466 factures en portent) ne peuvent être rendues
   à personne — leur part reste non allouée sur le paiement, ce qui est la
   vérité et se voit.

3. LE PAIEMENT N'EST PAS SUPPRIMÉ : il est annulé puis AMENDÉ. L'original reste
   en base, annulé et consultable, et le nouveau lui est rattaché par
   `amended_from`. On perd le numéro (Frappe l'incrémente en « -1 »), pas la
   trace — et le rapprochement bancaire se refait seul au passage suivant,
   l'amendement conservant la référence bancaire.

⚠️ ORDRE DES OPÉRATIONS. Le plan est calculé en `before_cancel`, PENDANT que
les affectations existent encore : à `on_cancel`, ERPNext les a déjà retirées
(`unlink_payment_on_cancellation_of_invoice`) et il serait trop tard pour
savoir qui avait payé quoi.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt

PRECISION = 3
# En deçà, une part n'est pas une part : c'est un résidu d'arrondi qu'ERPNext
# refuserait d'allouer.
MINIMUM = 0.001


def _parts_par_commande(doc):
    """{commande: part}, part ∈ ]0,1], calculée sur les MONTANTS des lignes.

    Le dénominateur est le total de TOUTES les lignes, y compris celles sans
    commande : sinon on rendrait aux commandes une part d'argent qui ne vient
    pas d'elles.
    """
    total, par_commande = 0.0, {}
    for l in doc.get("items") or []:
        montant = flt(l.amount)
        total += montant
        if l.get("sales_order"):
            par_commande[l.sales_order] = par_commande.get(l.sales_order, 0.0) + montant
    if total <= 0:
        return {}
    return {so: m / total for so, m in par_commande.items() if m > 0}


def _capacite(commande, deja=None):
    """Ce que la commande peut encore recevoir en avance, sans dépasser son total.

    ERPNext refuse une avance qui excède la commande. Le dépassement éventuel
    n'est pas perdu : il reste non alloué sur le paiement.

    ⚠️ ON NE LIT PAS `advance_paid`. Ce champ est dénormalisé et reste à sa
    valeur d'avant la facturation : une commande dont l'avance a été transférée
    à la facture l'affiche encore pleine. S'y fier faisait conclure « capacité
    nulle » et ne rendait RIEN — exactement le cas que ce module doit réparer
    (constaté sur WEB1-007819 et SAL-ORD-2026-03325). La vérité est dans les
    affectations réelles des paiements soumis.
    """
    so = frappe.db.get_value("Sales Order", commande,
                             ["grand_total", "docstatus"], as_dict=True)
    if not so or so.docstatus != 1:
        return 0.0
    return max(0.0, flt(so.grand_total, PRECISION)
               - _avance_reelle(commande) - flt(deja or 0, PRECISION))


def _avance_reelle(commande):
    """L'avance que les paiements SOUMIS portent réellement sur cette commande."""
    return flt(frappe.db.sql(
        """SELECT COALESCE(SUM(per.allocated_amount), 0)
           FROM `tabPayment Entry Reference` per
           JOIN `tabPayment Entry` pe ON pe.name = per.parent
           WHERE per.reference_doctype = 'Sales Order'
             AND per.reference_name = %s AND pe.docstatus = 1""",
        (commande,))[0][0], PRECISION)


def _realigner_avance(commande):
    """Remet `Sales Order.advance_paid` sur ses pieds avant d'y affecter un paiement.

    ⚠️ SANS QUOI ERPNEXT REFUSE. Le champ est dénormalisé et reste à sa valeur
    d'avant la facturation : la commande affiche son avance pleine alors
    qu'aucun paiement ne la référence plus. `validate_allocated_amount` s'y fie
    et répond « Sales Order … has already been fully paid » — le paiement se
    retrouvait annulé sans être rejoué (constaté 02/09 sur SAL-ORD-2026-02587).

    On écrit la SOMME RÉELLE des affectations, pas une valeur devinée : c'est
    la même lecture que celle qui sert à calculer la capacité.
    """
    reelle = _avance_reelle(commande)
    if abs(flt(frappe.db.get_value("Sales Order", commande, "advance_paid"),
               PRECISION) - reelle) < 0.001:
        return
    frappe.db.set_value("Sales Order", commande, "advance_paid", reelle,
                        update_modified=False)


def _repartir(montant, parts, deja):
    """Répartit `montant` entre les commandes, au prorata, sans jamais dépasser.

    Le reste d'arrondi va à la plus grosse part : réparti au millime près sur
    huit commandes, il finirait sinon par manquer un millime au total, et
    l'écart se lirait comme une erreur de saisie.
    """
    montant = flt(montant, PRECISION)
    ordre = sorted(parts.items(), key=lambda kv: -kv[1])
    lignes, distribue = [], 0.0
    for commande, part in ordre:
        voulu = flt(montant * part, PRECISION)
        possible = min(voulu, _capacite(commande, deja.get(commande, 0.0)),
                       flt(montant - distribue, PRECISION))
        if possible < MINIMUM:
            continue
        lignes.append({"commande": commande, "montant": flt(possible, PRECISION)})
        distribue = flt(distribue + possible, PRECISION)
        deja[commande] = flt(deja.get(commande, 0.0) + possible, PRECISION)

    reste = flt(montant - distribue, PRECISION)
    if lignes and 0 < reste < 0.01:
        premiere = lignes[0]
        marge = _capacite(premiere["commande"],
                          deja.get(premiere["commande"], 0.0))
        if marge >= reste:
            premiere["montant"] = flt(premiere["montant"] + reste, PRECISION)
            deja[premiere["commande"]] = flt(deja[premiere["commande"]] + reste, PRECISION)
            reste = 0.0
    return lignes, reste


def _affectations_sur_la_facture(nom):
    """{payment_entry: montant alloué à cette facture} — lu AVANT le détachement."""
    out = {}
    for r in frappe.db.sql(
            """SELECT per.parent, per.allocated_amount
               FROM `tabPayment Entry Reference` per
               JOIN `tabPayment Entry` pe ON pe.name = per.parent
               WHERE per.reference_doctype = 'Sales Invoice'
                 AND per.reference_name = %s AND pe.docstatus = 1""",
            (nom,), as_dict=True):
        out[r.parent] = flt(out.get(r.parent, 0.0) + flt(r.allocated_amount), PRECISION)
    return out


def before_cancel_sales_invoice(doc, method=None):
    """Retient QUI avait payé QUOI, tant que l'information existe encore."""
    parts = _parts_par_commande(doc)
    affectations = _affectations_sur_la_facture(doc.name)
    if not parts or not affectations:
        doc.flags.reaffectation = []
        return

    deja = {}
    plan = []
    for paiement, montant in sorted(affectations.items()):
        lignes, reste = _repartir(montant, parts, deja)
        if lignes:
            plan.append({"paiement": paiement, "lignes": lignes,
                         "non_alloue": reste, "montant": montant})
    doc.flags.reaffectation = plan


def on_cancel_sales_invoice(doc, method=None):
    """Rend le paiement aux commandes, une fois la facture détachée.

    Un échec ici ne doit PAS empêcher l'annulation de la facture : elle est
    déjà décidée, et bloquer laisserait l'utilisateur devant une facture
    ni annulée ni réaffectée. On journalise et on prévient à l'écran.
    """
    plan = doc.flags.get("reaffectation") or []
    if not plan:
        return
    faits = []
    for entree in plan:
        try:
            faits.append(_reaffecter(entree))
        except Exception:
            frappe.log_error(frappe.get_traceback(),
                             "Réaffectation paiement %s" % entree["paiement"][:60])
            frappe.msgprint(
                _("Le paiement {0} n'a pas pu être réaffecté automatiquement — "
                  "il reste non alloué, à rattacher à la main.")
                .format(entree["paiement"]), indicator="orange", alert=True)
    if faits:
        frappe.msgprint(
            _("Paiement(s) réaffecté(s) aux commandes d'origine :") + "<br>"
            + "<br>".join(faits), indicator="green", alert=True)


def _reaffecter(entree):
    """Annule le paiement et le rejoue, affecté aux commandes. -> compte rendu.

    ⚠️ AMENDEMENT, PAS SUPPRESSION. `frappe.copy_doc` + `amended_from` laisse
    l'original annulé en base : la piste d'audit reste, et le nouveau document
    garde la référence bancaire, donc le rapprochement se refait tout seul.

    ⚠️ TOUT OU RIEN. Entre l'annulation et le rejeu, le paiement n'existe plus
    au grand livre. Si le rejeu échoue, le laisser dans cet état retirerait de
    l'argent de la comptabilité sans que personne ne l'ait décidé — c'est
    arrivé en recette. Le point de reprise annule alors l'annulation elle-même :
    on retrouve exactement l'état d'avant, et l'écran le dit.
    """
    frappe.db.savepoint("reaffectation")
    try:
        return _rejouer(entree)
    except Exception:
        frappe.db.rollback(save_point="reaffectation")
        raise


def _rejouer(entree):
    ancien = frappe.get_doc("Payment Entry", entree["paiement"])
    if ancien.docstatus != 1:
        return ""
    # Les autres affectations du paiement (autres factures) sont conservées :
    # à ce stade, ERPNext a déjà retiré CELLE de la facture annulée.
    gardees = [{"reference_doctype": r.reference_doctype,
                "reference_name": r.reference_name,
                "total_amount": r.total_amount,
                "outstanding_amount": r.outstanding_amount,
                "allocated_amount": r.allocated_amount}
               for r in (ancien.get("references") or [])]

    ancien.flags.ignore_permissions = True
    ancien.flags.ignore_links = True
    ancien.cancel()

    # Les affectations viennent de disparaître avec l'annulation : c'est le
    # moment de remettre `advance_paid` sur ses pieds, sinon la validation du
    # nouveau paiement croira les commandes déjà soldées.
    for l in entree["lignes"]:
        _realigner_avance(l["commande"])

    neuf = frappe.copy_doc(ancien)
    neuf.amended_from = ancien.name
    neuf.set("references", [])
    for r in gardees:
        neuf.append("references", r)
    for l in entree["lignes"]:
        neuf.append("references", {
            "reference_doctype": "Sales Order",
            "reference_name": l["commande"],
            "allocated_amount": l["montant"],
        })
    neuf.flags.ignore_permissions = True
    neuf.flags.ignore_links = True
    neuf.insert()
    neuf.submit()
    # PAS de commit ici : il détruirait le point de reprise, et l'annulation de
    # la facture porte déjà la transaction jusqu'au bout.

    detail = ", ".join("%s (%s)" % (l["commande"], l["montant"]) for l in entree["lignes"])
    reste = flt(entree.get("non_alloue"), PRECISION)
    return "%s → %s%s" % (
        neuf.name, detail,
        (_(" — {0} restent non alloués").format(reste) if reste >= 0.01 else ""))
