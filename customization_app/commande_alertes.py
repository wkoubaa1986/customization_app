"""
Anomalies des commandes client : détection, stockage et restitution.

Cinq situations se noient dans une liste de près de 10 000 commandes :

  Tâche ouverte en retard               une intervention dont la date est
                                        passée sans avoir été clôturée
  Tâche annulée, dette non payée        la dernière tâche a été annulée alors
                                        que le paiement reste dû
  Main d'œuvre sans tâche               une ligne de main d'œuvre, mais aucune
                                        intervention planifiée
  Livraison sans tâche                  une ligne de livraison, mais aucune
                                        intervention planifiée
  Tâche terminée, commande non soldée   une tâche terminée, alors que la
                                        commande n'est ni validée ni soldée

L'ordre du CASE fixe la priorité. Les recoupements sont marginaux par
construction : « en retard » exige une tâche ouverte, les deux motifs « sans
tâche » exigent qu'aucune ne soit active, et « annulée » que la dernière le
soit. Mesuré : aucun chevauchement entre « en retard » et « annulée ».

Le motif est stocké dans le champ `custom_anomalie` de la commande, pour être
filtrable et triable dans la liste et exploitable en rapport. Il est maintenu
par les hooks de Tache de travail, Delivery Note et Sales Order, avec une
resynchronisation complète chaque nuit en filet de sécurité.

La règle n'est écrite qu'à un seul endroit — _SQL_MOTIF — utilisé aussi bien
pour une commande que pour l'ensemble de la base, afin que le calcul unitaire
et le recalcul en masse ne puissent pas diverger.
"""

import json

import frappe

from customization_app.per_delivered_montant import MARGE

CHAMP = "custom_anomalie"

GROUPE_MAIN_OEUVRE = "Main d’œuvre"  # apostrophe typographique U+2019
GROUPE_LIVRAISON = "Livraison"

MODE_DETTE = "Dette non payée"

MOTIF_TACHE_RETARD = "Tâche ouverte en retard"
MOTIF_TACHE_ANNULEE = "Tâche annulée, dette non payée"
MOTIF_MAIN_OEUVRE = "Main d'œuvre sans tâche"
MOTIF_LIVRAISON = "Livraison sans tâche"
MOTIF_NON_SOLDEE = "Tâche terminée, commande non soldée"

COULEURS = {
    MOTIF_TACHE_RETARD: "jaune",
    MOTIF_TACHE_ANNULEE: "violet",
    MOTIF_MAIN_OEUVRE: "rouge",
    MOTIF_LIVRAISON: "rouge",
    MOTIF_NON_SOLDEE: "orange",
}

# Ordre d'affichage du champ Select, et donc du filtre de la liste.
MOTIFS = [
    MOTIF_TACHE_RETARD,
    MOTIF_TACHE_ANNULEE,
    MOTIF_MAIN_OEUVRE,
    MOTIF_LIVRAISON,
    MOTIF_NON_SOLDEE,
]

# Une page de liste Frappe affiche au plus 100 lignes.
MAX_NOMS = 100

# Source unique de la règle. %(clause)s restreint le périmètre : une commande,
# une poignée, ou toute la base.
_SQL_MOTIF = """
    SELECT so.name,
        CASE
            -- Une intervention dont la date est passée et qui n'a pas été
            -- clôturée. Exclusif des motifs « sans tâche », qui exigent
            -- justement qu'aucune tâche ne soit active.
            WHEN EXISTS (
                    SELECT 1 FROM `tabTache de travail` t
                    WHERE t.commande_client = so.name
                      AND t.status = 'Open' AND t.starts_on < CURDATE())
            THEN %(motif_tache_retard)s

            -- C'est la DERNIÈRE tâche qui décide : si elle est annulée, plus
            -- rien n'est prévu pour cette commande. Une tâche annulée puis
            -- replanifiée ne ressort donc pas, la dernière étant alors active.
            -- Restreint aux commandes dont le paiement reste dû : une commande
            -- annulée et déjà réglée n'appelle aucune action.
            WHEN (SELECT t.status FROM `tabTache de travail` t
                  WHERE t.commande_client = so.name
                  ORDER BY t.starts_on DESC, t.creation DESC LIMIT 1) = 'Cancelled'
                 AND (EXISTS (
                        SELECT 1 FROM `tabPayment Schedule` ps
                        WHERE ps.parent = so.name AND ps.parenttype = 'Sales Order'
                          AND ps.mode_of_payment = %(mode_dette)s)
                      OR EXISTS (
                        SELECT 1 FROM `tabSales Invoice Item` sii
                        JOIN `tabPayment Schedule` ps
                          ON ps.parent = sii.parent AND ps.parenttype = 'Sales Invoice'
                        WHERE sii.sales_order = so.name
                          AND ps.mode_of_payment = %(mode_dette)s))
            THEN %(motif_tache_annulee)s

            WHEN NOT EXISTS (
                    SELECT 1 FROM `tabTache de travail` t
                    WHERE t.commande_client = so.name AND t.status <> 'Cancelled')
                 AND EXISTS (
                    SELECT 1 FROM `tabSales Order Item` si
                    JOIN `tabItem` i ON i.name = si.item_code
                    WHERE si.parent = so.name AND i.item_group = %(main_oeuvre)s)
            THEN %(motif_main_oeuvre)s

            WHEN NOT EXISTS (
                    SELECT 1 FROM `tabTache de travail` t
                    WHERE t.commande_client = so.name AND t.status <> 'Cancelled')
                 AND EXISTS (
                    SELECT 1 FROM `tabSales Order Item` si
                    JOIN `tabItem` i ON i.name = si.item_code
                    WHERE si.parent = so.name AND i.item_group = %(livraison)s)
            THEN %(motif_livraison)s

            WHEN EXISTS (
                    SELECT 1 FROM `tabTache de travail` t
                    WHERE t.commande_client = so.name AND t.status = 'Completed')
                 AND (so.docstatus = 0 OR COALESCE((
                        SELECT SUM(dn.grand_total) FROM `tabDelivery Note` dn
                        WHERE dn.docstatus = 1 AND EXISTS (
                            SELECT 1 FROM `tabDelivery Note Item` dni
                            WHERE dni.parent = dn.name AND dni.against_sales_order = so.name)
                     ), 0) < so.grand_total - %(marge)s)
            THEN %(motif_non_soldee)s

            ELSE ''
        END AS motif
    FROM `tabSales Order` so
    WHERE {clause}
"""


def _params(extra=None):
    p = {
        "main_oeuvre": GROUPE_MAIN_OEUVRE,
        "livraison": GROUPE_LIVRAISON,
        "motif_tache_retard": MOTIF_TACHE_RETARD,
        "motif_tache_annulee": MOTIF_TACHE_ANNULEE,
        "mode_dette": MODE_DETTE,
        "motif_main_oeuvre": MOTIF_MAIN_OEUVRE,
        "motif_livraison": MOTIF_LIVRAISON,
        "motif_non_soldee": MOTIF_NON_SOLDEE,
        "marge": MARGE,
    }
    p.update(extra or {})
    return p


def _calculer(clause, extra=None):
    """Retourne {nom: motif} pour le périmètre décrit par `clause`."""
    lignes = frappe.db.sql(
        _SQL_MOTIF.format(clause=clause), _params(extra), as_dict=True
    )
    return {r.name: r.motif or "" for r in lignes}


def _stocker(motifs):
    """Écrit les motifs qui ont changé. Retourne le nombre de mises à jour."""
    if not motifs:
        return 0

    actuels = dict(
        frappe.db.sql(
            f"SELECT name, COALESCE(`{CHAMP}`, '') FROM `tabSales Order` WHERE name IN %(noms)s",
            {"noms": tuple(motifs)},
        )
    )

    # Regrouper par motif : une seule requête par valeur distincte plutôt
    # qu'une par commande, indispensable pour la resynchronisation complète.
    par_motif = {}
    for nom, motif in motifs.items():
        if actuels.get(nom, "") != motif:
            par_motif.setdefault(motif, []).append(nom)

    for motif, noms in par_motif.items():
        frappe.db.sql(
            f"UPDATE `tabSales Order` SET `{CHAMP}` = %(motif)s WHERE name IN %(noms)s",
            {"motif": motif, "noms": tuple(noms)},
        )

    return sum(len(v) for v in par_motif.values())


def recalculer(noms):
    """Recalcule et stocke le motif de quelques commandes."""
    noms = [n for n in (noms or []) if n]
    if not noms:
        return 0
    if not frappe.db.has_column("Sales Order", CHAMP):
        return 0
    return _stocker(_calculer("so.name IN %(noms)s", {"noms": tuple(noms)}))


def recalculer_tout():
    """
    Recalcule toutes les commandes non annulées.

    Sert au patch de reprise et à la resynchronisation nocturne : un filet de
    sécurité si un événement a été manqué (import en masse, correction directe
    en base, suppression non hookée).
    """
    if not frappe.db.has_column("Sales Order", CHAMP):
        return 0
    modifiees = _stocker(_calculer("so.docstatus < 2"))
    frappe.db.commit()
    return modifiees


# ── Restitution pour la liste ────────────────────────────────────────────────


@frappe.whitelist()
def get_alertes(noms):
    """
    Retourne {nom: {"couleur": ..., "libelle": ...}} pour les commandes en
    anomalie. Lit le champ stocké : la couleur affichée et le filtre portent
    ainsi toujours la même valeur.
    """
    if isinstance(noms, str):
        noms = json.loads(noms)
    noms = [n for n in (noms or []) if n][:MAX_NOMS]
    if not noms or not frappe.db.has_column("Sales Order", CHAMP):
        return {}

    lignes = frappe.db.sql(
        f"""
        SELECT name, `{CHAMP}` AS motif FROM `tabSales Order`
        WHERE name IN %(noms)s AND COALESCE(`{CHAMP}`, '') <> ''
    """,
        {"noms": tuple(noms)},
        as_dict=True,
    )
    return {
        r.name: {"couleur": COULEURS.get(r.motif, "orange"), "libelle": r.motif}
        for r in lignes
    }


# ── Hooks ────────────────────────────────────────────────────────────────────


def _sur_erreur(nom):
    frappe.log_error(title=f"Anomalie commande — {nom}", message=frappe.get_traceback())


def on_tache_change(doc, method=None):
    """Une tâche créée, modifiée ou supprimée change l'anomalie de sa commande."""
    noms = {doc.get("commande_client")}
    # Si la tâche a été rattachée à une autre commande, l'ancienne doit aussi
    # être réévaluée.
    avant = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
    if avant and avant.get("commande_client"):
        noms.add(avant.commande_client)
    noms = {n for n in noms if n}
    if not noms:
        return
    try:
        recalculer(list(noms))
    except Exception:
        _sur_erreur(", ".join(noms))


def on_delivery_note_change(doc, method=None):
    """Un BL validé ou annulé change le solde de ses commandes."""
    from customization_app.per_delivered_montant import _commandes_liees

    noms = _commandes_liees(doc)
    if not noms:
        return
    try:
        recalculer(list(noms))
    except Exception:
        _sur_erreur(", ".join(noms))


def on_sales_order_change(doc, method=None):
    """La validation d'une commande la fait sortir du motif « non validée »."""
    try:
        recalculer([doc.name])
    except Exception:
        _sur_erreur(doc.name)
