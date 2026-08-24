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

PLANCHER (décision utilisateur, 19/08/2026) : la surveillance ne s'applique
qu'aux commandes À PARTIR DU 01/07/2026. L'historique antérieur n'est pas tenu
à ce régime — son motif se vide au recalcul. Et deux affinages par les
PAIEMENTS liés (Payment Entry validées allouées à la commande ou à ses
factures) :
  - « Main d'œuvre sans tâche » ne se lève pas si la commande (ou une facture)
    est VALIDÉE et qu'aucun paiement lié n'est parqué en dette : commande
    réglée sans dette = rien à planifier ;
  - « Livraison sans tâche » exige qu'un paiement lié soit encore sur un
    compte d'ATTENTE (Livraison Aramex ou Dettes) : tout encaissé = la
    livraison a eu lieu, l'alerte n'apprend rien ;
  - « Tâche ouverte en retard » d'une commande SOLDÉE (aucun paiement lié en
    attente) : la tâche est FERMÉE automatiquement (Completed) — l'intervention
    a eu lieu, elle n'avait simplement pas été clôturée. Voir
    fermer_taches_soldees(), appelée par la resynchronisation nocturne et par
    le hook Payment Entry.

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

# Plancher de surveillance : les commandes antérieures ne portent jamais d'anomalie.
DATE_DEBUT = "2026-07-01"

# Comptes d'attente des paiements (mêmes conventions que bank_retenue_sync).
COMPTE_DETTES = "Dettes - A&S"
COMPTE_ARAMEX = "Livraison Aramex - A&S"

MOTIF_TACHE_RETARD = "Tâche ouverte en retard"
MOTIF_TACHE_ANNULEE = "Tâche annulée, dette non payée"
MOTIF_MAIN_OEUVRE = "Main d'œuvre sans tâche"
MOTIF_LIVRAISON = "Livraison sans tâche"
MOTIF_NON_SOLDEE = "Tâche terminée, commande non soldée"

# Motif posé par annulation_tache.py quand la cascade annule la commande avec sa tâche.
# Il porte le NOM de la tâche (« Commande annulée avec tâche Tache-08055 ») : c'est donc
# un PRÉFIXE, pas un libellé fermé — la règle SQL le PRÉSERVE au lieu de le recalculer,
# et la couleur se résout par préfixe des deux côtés (cf. couleur_du_motif et le JS).
MOTIF_COMMANDE_ANNULEE = "Commande annulée avec tâche"


def couleur_du_motif(motif: str) -> str:
    if motif and motif.startswith(MOTIF_COMMANDE_ANNULEE):
        return "violet"
    return COULEURS.get(motif, "orange")

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

# Paiement validé alloué à la commande OU à l'une de ses factures, sur un compte donné.
# Le fragment est injecté dans _SQL_MOTIF avec sa condition de compte ({compte}) : la même
# jointure sert au motif « main d'œuvre » (dette) et au motif « livraison » (attente).
_SQL_PAIEMENT_LIE = """EXISTS (
                    SELECT 1 FROM `tabPayment Entry Reference` per
                    JOIN `tabPayment Entry` pe ON pe.name = per.parent
                    WHERE pe.docstatus = 1 AND {compte}
                      AND ((per.reference_doctype = 'Sales Order'
                            AND per.reference_name = so.name)
                           OR (per.reference_doctype = 'Sales Invoice'
                               AND per.reference_name IN (
                                   SELECT sii2.parent FROM `tabSales Invoice Item` sii2
                                   WHERE sii2.sales_order = so.name))))"""

_PAIEMENT_DETTE = _SQL_PAIEMENT_LIE.format(compte="pe.paid_to = %(compte_dettes)s")
_PAIEMENT_ATTENTE = _SQL_PAIEMENT_LIE.format(compte="pe.paid_to IN %(comptes_attente)s")
_PAIEMENT_QUELCONQUE = _SQL_PAIEMENT_LIE.format(compte="1 = 1")

# Source unique de la règle. %(clause)s restreint le périmètre : une commande,
# une poignée, ou toute la base.
_SQL_MOTIF = """
    SELECT so.name,
        CASE
            -- Une commande annulée PAR l'annulation de sa tâche (annulation_tache.py)
            -- garde le motif posé par la cascade : il porte le nom de la tâche, la règle
            -- ne peut pas le recalculer. AVANT le plancher — il doit survivre aussi sur
            -- une commande antérieure au 01/07/2026.
            WHEN so.docstatus = 2 AND so.custom_anomalie LIKE %(motif_annulee_like)s
            THEN so.custom_anomalie

            -- PLANCHER : la surveillance ne commence qu'au 01/07/2026. Le motif de
            -- l'historique antérieur se VIDE au recalcul (le plancher doit vivre dans le
            -- CASE, pas dans le WHERE, sinon les anciens motifs stockés resteraient).
            WHEN so.transaction_date < %(date_debut)s THEN ''

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

            -- Affinage (19/08/2026) : une commande VALIDÉE (elle-même ou par une facture)
            -- dont aucun paiement lié n'est parqué en dette est réglée — la main d'œuvre
            -- a été traitée autrement, l'alerte n'apprend rien. On ne garde le motif que
            -- si la commande n'est pas validée, OU si une dette liée subsiste.
            WHEN NOT EXISTS (
                    SELECT 1 FROM `tabTache de travail` t
                    WHERE t.commande_client = so.name AND t.status <> 'Cancelled')
                 AND EXISTS (
                    SELECT 1 FROM `tabSales Order Item` si
                    JOIN `tabItem` i ON i.name = si.item_code
                    WHERE si.parent = so.name AND i.item_group = %(main_oeuvre)s)
                 AND NOT (
                    (so.docstatus = 1
                     OR EXISTS (
                        SELECT 1 FROM `tabSales Invoice Item` sii
                        JOIN `tabSales Invoice` fac ON fac.name = sii.parent
                        WHERE sii.sales_order = so.name AND fac.docstatus = 1))
                    AND NOT {paiement_dette})
            THEN %(motif_main_oeuvre)s

            -- Affinage (19/08/2026, corrigé le soir même) : le motif se tait quand la
            -- livraison a manifestement eu lieu — des paiements liés existent et AUCUN
            -- n'attend sur Livraison Aramex ou Dettes. Une commande SANS AUCUN paiement
            -- (le brouillon WEB fraîchement arrivé) n'est pas « tout encaissé » : elle
            -- alerte comme avant — c'est précisément elle qu'il faut planifier.
            WHEN NOT EXISTS (
                    SELECT 1 FROM `tabTache de travail` t
                    WHERE t.commande_client = so.name AND t.status <> 'Cancelled')
                 AND EXISTS (
                    SELECT 1 FROM `tabSales Order Item` si
                    JOIN `tabItem` i ON i.name = si.item_code
                    WHERE si.parent = so.name AND i.item_group = %(livraison)s)
                 AND ({paiement_attente} OR NOT {paiement_quelconque})
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

# Injection des fragments de paiement ; {clause} reste un trou, rempli par _calculer.
_SQL_MOTIF = _SQL_MOTIF.format(paiement_dette=_PAIEMENT_DETTE,
                               paiement_attente=_PAIEMENT_ATTENTE,
                               paiement_quelconque=_PAIEMENT_QUELCONQUE,
                               clause="{clause}")


def _params(extra=None):
    p = {
        "date_debut": DATE_DEBUT,
        "compte_dettes": COMPTE_DETTES,
        "comptes_attente": (COMPTE_ARAMEX, COMPTE_DETTES),
        "main_oeuvre": GROUPE_MAIN_OEUVRE,
        "livraison": GROUPE_LIVRAISON,
        "motif_tache_retard": MOTIF_TACHE_RETARD,
        "motif_tache_annulee": MOTIF_TACHE_ANNULEE,
        "motif_annulee_like": MOTIF_COMMANDE_ANNULEE + "%",
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


def fermer_taches_soldees(noms=None):
    """
    Ferme (Completed) les tâches ouvertes EN RETARD des commandes SOLDÉES.

    Décision utilisateur (19/08/2026) : si aucun paiement lié à la commande ou
    à ses factures n'attend sur Dettes / Livraison Aramex, tout est encaissé —
    l'intervention a manifestement eu lieu, la tâche n'a simplement jamais été
    clôturée. On la ferme, et la commande sort du motif « Tâche ouverte en
    retard » par le recalcul que la clôture déclenche (on_tache_change).

    Même plancher que les anomalies : les commandes antérieures au 01/07/2026
    ne sont pas touchées. `noms` restreint à quelques commandes (hook paiement)
    ; sans lui, toute la base (resynchronisation nocturne, patch).
    """
    clause = "so.name = t.commande_client"
    params = _params()
    if noms:
        noms = [n for n in noms if n]
        if not noms:
            return 0
        clause += " AND so.name IN %(noms)s"
        params["noms"] = tuple(noms)

    taches = frappe.db.sql(
        f"""
        SELECT t.name FROM `tabTache de travail` t
        WHERE t.status = 'Open' AND t.starts_on < CURDATE()
          AND EXISTS (
            SELECT 1 FROM `tabSales Order` so
            WHERE {clause}
              AND so.docstatus < 2
              AND so.transaction_date >= %(date_debut)s
              AND NOT {_PAIEMENT_ATTENTE})
        """,
        params,
    )

    fermees = 0
    for (nom,) in taches:
        try:
            tache = frappe.get_doc("Tache de travail", nom)
            tache.status = "Completed"
            # save() déclenche on_tache_change -> recalcul de l'anomalie de la commande.
            tache.flags.ignore_permissions = True
            tache.save()
            fermees += 1
        except Exception:
            _sur_erreur(nom)
    return fermees


@frappe.whitelist()
def resynchroniser():
    """
    Bouton « Mettre à jour les anomalies » de la vue liste des commandes.

    Rejoue TOUTE la logique, actions comprises — pas un simple recalcul de
    libellé : les tâches en retard des commandes soldées sont FERMÉES (ce qui
    modifie les commandes), puis chaque commande est requalifiée.
    """
    frappe.only_for(("System Manager", "Accounts Manager", "Sales Manager"))
    fermees = fermer_taches_soldees()
    modifiees = _stocker(_calculer("so.docstatus < 2"))
    frappe.db.commit()
    return {"fermees": fermees, "modifiees": modifiees}


def recalculer_tout():
    """
    Recalcule toutes les commandes non annulées.

    Sert au patch de reprise et à la resynchronisation nocturne : un filet de
    sécurité si un événement a été manqué (import en masse, correction directe
    en base, suppression non hookée).
    """
    if not frappe.db.has_column("Sales Order", CHAMP):
        return 0
    # D'abord les clôtures automatiques : elles changent le motif des commandes.
    fermer_taches_soldees()
    modifiees = _stocker(_calculer("so.docstatus < 2"))
    frappe.db.commit()
    return modifiees


# ── Restitution pour la liste ────────────────────────────────────────────────


@frappe.whitelist()
def get_alertes(noms):
    """
    Décore les lignes de la liste des commandes, en un seul appel :

      couleur / libelle   l'anomalie, lue depuis le champ stocké — la couleur
                          affichée et le filtre portent ainsi toujours la même
                          valeur
      appels              nombre d'appels de confirmation sans réponse, pour
                          les commandes WEB

    Une commande sans anomalie mais déjà rappelée est donc retournée elle
    aussi. Les commandes sans rien à afficher sont absentes du résultat.
    """
    from customization_app.suivi_appels import CHAMPS as CHAMPS_APPELS, nb_appels

    if isinstance(noms, str):
        noms = json.loads(noms)
    noms = [n for n in (noms or []) if n][:MAX_NOMS]
    if not noms or not frappe.db.has_column("Sales Order", CHAMP):
        return {}

    suivi_appels = frappe.db.has_column("Sales Order", CHAMPS_APPELS[1])
    colonnes = [f"`{CHAMP}` AS motif"]
    if suivi_appels:
        colonnes += [f"`{c}` AS `{c}`" for c in CHAMPS_APPELS.values()]
    # « Retour colis » (Aramex) : un FAIT constaté, indépendant de l'anomalie
    # recalculée — il porte sa propre pastille.
    retour_colis = frappe.db.has_column("Sales Order", "custom_retour_colis")
    if retour_colis:
        colonnes += ["`custom_retour_colis` AS retour"]

    lignes = frappe.db.sql(
        f"""
        SELECT name, {', '.join(colonnes)} FROM `tabSales Order`
        WHERE name IN %(noms)s
    """,
        {"noms": tuple(noms)},
        as_dict=True,
    )

    resultat = {}
    for r in lignes:
        entree = {}
        if r.motif:
            entree["couleur"] = couleur_du_motif(r.motif)
            entree["libelle"] = r.motif
        if suivi_appels:
            n = nb_appels(r)
            if n:
                entree["appels"] = n
        if retour_colis and r.get("retour"):
            entree["retour"] = 1
        if entree:
            resultat[r.name] = entree
    return resultat


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


def on_payment_entry_change(doc, method=None):
    """Un paiement soumis ou annulé change les motifs « sans tâche » de ses commandes.

    Depuis le 19/08/2026, ces deux motifs regardent OÙ sont parqués les paiements liés
    (dette, comptes d'attente Aramex/Dettes) : l'encaissement d'une dette — qui remplace la
    Payment Entry — doit requalifier la commande tout de suite, pas à la resynchronisation
    de 04h00.
    """
    noms = set()
    factures = []
    for r in doc.get("references") or []:
        if r.reference_doctype == "Sales Order":
            noms.add(r.reference_name)
        elif r.reference_doctype == "Sales Invoice":
            factures.append(r.reference_name)
    if factures:
        noms.update(
            r[0]
            for r in frappe.db.sql(
                """SELECT DISTINCT sii.sales_order FROM `tabSales Invoice Item` sii
                   WHERE sii.parent IN %(factures)s AND sii.sales_order IS NOT NULL""",
                {"factures": tuple(factures)},
            )
        )
    noms = {n for n in noms if n}
    if not noms:
        return
    try:
        # L'encaissement qui solde la commande peut aussi clôturer ses tâches en retard.
        fermer_taches_soldees(list(noms))
        recalculer(list(noms))
    except Exception:
        _sur_erreur(", ".join(noms))
