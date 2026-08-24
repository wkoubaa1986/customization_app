"""Annulation d'une tâche de travail : proposer d'annuler la commande liée.

LE GESTE MÉTIER
---------------
Une intervention annulée (Entretien, Réparation…) laisse derrière elle une
commande souvent déjà équipée par les automatismes maison : échéancier et
Payment Entries du tandem, BL généré, parfois une facture. Les nettoyer un par
un s'oublie. Quand l'utilisateur passe la tâche à « Cancelled » depuis son
formulaire, on lui PROPOSE — jamais d'office — d'annuler la commande et tout
son cortège (voir public/js/tache_annulation.js pour le dialogue).

CE QUI EST FAIT, ET PAR QUI
---------------------------
  - factures liées : leurs Payment Entries supprimées, facture annulée puis
    SUPPRIMÉE (le Server Script ne connaît que les pièces qui référencent la
    commande, pas celles de la facture) ;
  - commande : cancel() — le Server Script « cancel sales order payment »
    (Before Cancel) supprime déjà les PE et JE qui la référencent et annule
    ses BL ; on s'appuie dessus au lieu de le dupliquer, puis on balaie les
    brouillons de paiement qu'il ignore (il filtre docstatus = 1) ;
  - BL : une fois annulé par le script, il est SUPPRIMÉ ici ;
  - traçabilité : le motif « Commande annulée avec tâche <nom> » est posé dans
    `custom_anomalie` — la pastille en haut de la fiche, celle des anomalies de
    commande_alertes, qui le PRÉSERVE au recalcul — plus un commentaire portant
    le LIEN de la tâche (une pastille ne peut pas contenir de lien).

GARDE-FOUS — tout écart laisse la commande INTACTE, message seulement :
  - plusieurs BL, ou un BL d'un TTC différent de la commande (livraison
    partielle, échange) : « la commande a plusieurs BL, rien n'est annulé » ;
  - un paiement ou une écriture PARTAGÉS avec une autre pièce (chèque global
    couvrant deux commandes) : les supprimer emporterait l'argent d'une autre
    commande ;
  - commande en brouillon, déjà annulée, ou absente : rien à faire.

La comparaison BL/commande se fait sur le TTC (grand_total), comme
per_delivered_montant : sur les données réelles, 1 461 BL sur 1 544 sont au
millime près ; les autres sont précisément les cas à ne pas toucher.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, get_url_to_form

TOLERANCE_TTC = 0.005


# ------------------------------------------------------------------ diagnostic

def _bls_de(commande: str) -> list:
    return frappe.db.sql(
        """select distinct dn.name, dn.docstatus, dn.grand_total
           from `tabDelivery Note` dn
           join `tabDelivery Note Item` dni on dni.parent = dn.name
           where dni.against_sales_order = %s and dn.docstatus < 2""",
        commande, as_dict=True)


def _factures_de(commande: str) -> list:
    return frappe.db.sql(
        """select distinct si.name, si.docstatus
           from `tabSales Invoice` si
           join `tabSales Invoice Item` sii on sii.parent = si.name
           where sii.sales_order = %s and si.docstatus < 2""",
        commande, as_dict=True)


def _paiements_vers(cibles: list) -> list:
    """Payment Entries (brouillons compris) dont une référence vise une des cibles."""
    if not cibles:
        return []
    return frappe.db.sql(
        """select distinct pe.name, pe.docstatus
           from `tabPayment Entry Reference` per
           join `tabPayment Entry` pe on pe.name = per.parent
           where per.reference_name in %s and pe.docstatus < 2""",
        (tuple(cibles),), as_dict=True)


def _ecritures_vers(commande: str) -> list:
    """Journal Entries (avoir client du tandem, etc.) référençant la commande."""
    return frappe.db.sql(
        """select distinct je.name, je.docstatus
           from `tabJournal Entry Account` jea
           join `tabJournal Entry` je on je.name = jea.parent
           where jea.reference_type = 'Sales Order' and jea.reference_name = %s
             and je.docstatus < 2""",
        commande, as_dict=True)


def _pieces_partagees(commande: str, factures: list) -> list:
    """Paiements/écritures qui référencent AUSSI autre chose que la commande et
    ses factures : les supprimer emporterait l'argent d'une autre pièce."""
    cibles = {commande} | {f.name for f in factures}
    partages = []
    for p in _paiements_vers(list(cibles)):
        refs = set(frappe.db.get_all("Payment Entry Reference",
                                     filters={"parent": p.name}, pluck="reference_name"))
        if refs - cibles:
            partages.append(p.name)
    for je in _ecritures_vers(commande):
        refs = set(frappe.db.get_all(
            "Journal Entry Account",
            filters={"parent": je.name, "reference_type": ["is", "set"]},
            pluck="reference_name"))
        if refs - cibles - {None, ""}:
            partages.append(je.name)
    return partages


def _diagnostic(commande: str) -> dict:
    so = frappe.db.get_value(
        "Sales Order", commande,
        ["name", "docstatus", "status", "grand_total", "customer", "customer_name"],
        as_dict=True)
    if not so:
        return {"cas": "commande_introuvable", "commande": commande}
    out = {"commande": commande, "client": so.customer_name or so.customer,
           "total": flt(so.grand_total, 3), "statut_commande": so.status,
           "bls": [], "factures": [], "nb_paiements": 0}
    if so.docstatus == 2:
        out["cas"] = "deja_annulee"
        return out
    if so.docstatus == 0:
        out["cas"] = "brouillon"
        return out

    bls = _bls_de(commande)
    factures = _factures_de(commande)
    out["bls"] = [b.name for b in bls]
    out["factures"] = [f.name for f in factures]

    if len(bls) > 1:
        out["cas"] = "plusieurs_bl"
        return out
    if bls and abs(flt(bls[0].grand_total, 3) - flt(so.grand_total, 3)) > TOLERANCE_TTC:
        # BL partiel ou échange : même refus que « plusieurs BL », le message le précise.
        out["cas"] = "bl_different"
        out["total_bl"] = flt(bls[0].grand_total, 3)
        return out

    partages = _pieces_partagees(commande, factures)
    if partages:
        out["cas"] = "paiement_partage"
        out["pieces_partagees"] = partages
        return out

    cibles = [commande] + [f.name for f in factures]
    out["nb_paiements"] = len(_paiements_vers(cibles)) + len(_ecritures_vers(commande))
    out["cas"] = "cascade"
    return out


@frappe.whitelist()
def impact_annulation(tache: str, commande: str = None):
    """Ce qui arriverait à la commande si la tâche était annulée — pour le dialogue."""
    commande = commande or frappe.db.get_value("Tache de travail", tache, "commande_client")
    if not commande:
        return {"cas": "sans_commande"}
    return _diagnostic(commande)


# ------------------------------------------------------------------ exécution

def _supprimer_paiements(cibles: list) -> int:
    """Annule et supprime toute PE (brouillon compris) visant une des cibles."""
    n = 0
    for p in _paiements_vers(cibles):
        pe = frappe.get_doc("Payment Entry", p.name)
        if pe.docstatus == 1:
            pe.flags.ignore_permissions = True
            pe.cancel()
        frappe.delete_doc("Payment Entry", p.name, force=True, ignore_permissions=True)
        n += 1
    return n


@frappe.whitelist()
def annuler_commande_de_tache(tache: str):
    """Exécute la cascade, APRÈS que la tâche a été enregistrée « Cancelled ».

    Le diagnostic est refait ici : c'est lui qui fait foi, pas celui montré au
    dialogue — l'état a pu bouger entre les deux. Tout écart annule l'appel
    (throw = rollback complet), la commande reste intacte.
    """
    t = frappe.get_doc("Tache de travail", tache)
    t.check_permission("write")
    if t.status != "Cancelled":
        frappe.throw(_("La tâche {0} n'est pas annulée (statut {1}).").format(tache, t.status))
    commande = t.commande_client
    if not commande:
        frappe.throw(_("La tâche {0} n'a pas de commande liée.").format(tache))

    so = frappe.get_doc("Sales Order", commande)
    so.check_permission("cancel")

    diag = _diagnostic(commande)
    if diag["cas"] != "cascade":
        frappe.throw(_("La commande {0} n'est plus annulable ({1}) — rien n'a été fait.")
                     .format(commande, diag["cas"]))

    # 1. Factures : leurs paiements d'abord (le Server Script ne voit que ceux de la
    #    commande), puis annulation et suppression de la facture.
    paiements = 0
    for f in _factures_de(commande):
        paiements += _supprimer_paiements([f.name])
        si = frappe.get_doc("Sales Invoice", f.name)
        if si.docstatus == 1:
            si.flags.ignore_permissions = True
            si.cancel()
        frappe.delete_doc("Sales Invoice", f.name, force=True, ignore_permissions=True)

    # 2. La commande : cancel() déclenche « cancel sales order payment » (Before
    #    Cancel), qui supprime PE et JE soumis la référençant et annule ses BL.
    #    ⚠️ reload() d'abord : l'annulation de la facture vient de mettre à jour
    #    per_billed sur la commande — sans relecture, cancel() échoue sur un
    #    conflit de version (Document has been modified), constaté au premier test.
    bls = [b.name for b in _bls_de(commande)]
    paiements += len(_paiements_vers([commande])) + len(_ecritures_vers(commande))
    so.reload()
    so.flags.ignore_permissions = True
    so.cancel()

    # 3. Balayage : les brouillons de paiement échappent au script (il filtre
    #    docstatus = 1) — déjà comptés ci-dessus, on ne les recompte pas.
    _supprimer_paiements([commande])

    # 4. Les BL annulés par le script sont supprimés — c'est la demande : un BL
    #    d'une intervention annulée ne doit pas rester en base.
    for dn in bls:
        if frappe.db.exists("Delivery Note", dn):
            frappe.delete_doc("Delivery Note", dn, force=True, ignore_permissions=True)

    # 5. Traçabilité : le motif (pastille en haut de la fiche) + un commentaire avec le
    #    lien de la tâche. Posé APRÈS cancel() — le hook on_cancel vient de recalculer
    #    l'anomalie (« Tâche annulée, dette non payée ») ; la règle de commande_alertes
    #    préserve ensuite ce motif à tout recalcul (branche dédiée du CASE).
    from customization_app.commande_alertes import CHAMP, MOTIF_COMMANDE_ANNULEE
    motif = "%s %s" % (MOTIF_COMMANDE_ANNULEE, tache)
    frappe.db.set_value("Sales Order", commande, CHAMP, motif, update_modified=False)
    so.add_comment("Comment", _(
        "Commande annulée suite à l'annulation de la tâche de travail "
        "<a href=\"{0}\">{1}</a> : BL supprimé(s) : {2} — {3} paiement(s)/écriture(s) "
        "supprimé(s) — facture(s) supprimée(s) : {4}.").format(
        get_url_to_form("Tache de travail", tache), tache,
        ", ".join(bls) or _("aucun"), paiements,
        ", ".join(diag["factures"]) or _("aucune")))

    return {"commande": commande, "bls_supprimes": bls,
            "factures_supprimees": diag["factures"],
            "paiements_supprimes": paiements, "motif": motif}
