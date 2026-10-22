"""Écran « Commandes à traiter » — l'arriéré des commandes vu article par article.

Le besoin (30/08/2026) : la demande web déborde. En regardant UN écran, on veut
savoir, commande par commande, si on a encore les articles demandés — et sinon
décider tout de suite : annuler, proposer un article de remplacement, ou envoyer
le lien de prise de rendez-vous pour l'installation.

On ne réinvente rien : les commandes, les articles, les tâches liées et les
bordereaux viennent de `traitement_commandes` (mêmes fonctions que les pastilles
de la vue liste), les anomalies de `custom_anomalie`, et les envois de
`sms_commandes`. Ce module ajoute LE STOCK et la décision qui va avec.

DISPONIBILITÉ — la règle (décisions utilisateur du 30/08) :
  stock      = somme de `tabBin.actual_qty` sur TOUS les entrepôts ;
  engagé     = ce qui reste à livrer sur les AUTRES commandes soumises ouvertes ;
  disponible = stock - engagé.
Un article est « manquant » quand il reste à en livrer plus que le disponible.
Les articles NON stockés (Installation, Livraison, Échange…) n'ont pas de stock
et ne sont JAMAIS comptés manquants : les afficher en rouge noierait le signal.
"""
from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import add_days, flt, get_datetime, getdate, now_datetime, nowdate

from customization_app.suivi_appels import CHAMPS as CHAMPS_APPELS, LIBELLES as LIBELLES_APPELS
from customization_app.traitement_commandes import (
    _articles, _bordereaux, _commandes, _taches,
)

DEPUIS_DEFAUT = "2026-07-01"
PAGE_LENGTH = 50

# Statuts qui ne consomment plus de stock : la commande est finie ou morte.
STATUTS_CLOS = ("Completed", "Closed", "Cancelled")
# Une commande validée qui attend encore une livraison.
STATUTS_A_LIVRER = ("To Deliver and Bill", "To Deliver")

# « À TRAITER » — UNE définition, partagée par l'écran, le compteur de l'onglet Appels et son
# bouton « Ouvrir › » (demande 22/10/2026 : trois définitions différentes désorientaient
# l'utilisatrice — l'écran montrait 2 329 commandes, le compteur 18). Ce qui attend encore
# une action humaine :
#   - un BROUILLON : commande jamais validée (web à confirmer, devis du magasin) ;
#   - une commande validée pas encore entièrement livrée ;
#   - une commande ouverte qui porte une ANOMALIE (tâche en retard, tâche annulée…).
SQL_A_TRAITER = """(so.docstatus = 0 OR (so.docstatus = 1
    AND so.status NOT IN ('Completed', 'Closed', 'Cancelled')
    AND (so.status IN ('To Deliver and Bill', 'To Deliver') OR IFNULL(so.custom_anomalie, '') <> '')))"""

# Les vues de l'écran : le RESTE À FAIRE (à traiter, moins ce qui a déjà été traité
# aujourd'hui — c'est le chiffre de l'onglet Appels), tout l'à-traiter, ou tout l'historique.
VUE_RESTE, VUE_A_TRAITER, VUE_TOUTES = "a_traiter", "a_traiter_tout", "toutes"
VUE_DEFAUT = VUE_RESTE

# Traces posées par les actions de l'écran : une commande qui en reçoit une aujourd'hui a été
# traitée aujourd'hui (et l'onglet Appels le compte en « fait »).
MARQUEURS_ACTIONS = ("📨 Envoi groupé", "🚚 Livraison par notre équipe",
                     "❌ Annulée depuis « Commandes à traiter »")


# SUIVI d'une commande, posé en un clic depuis l'écran (demande 22/10/2026) : un statut et une
# DATE DE REPRISE. Tant que la date n'est pas atteinte, la commande sort du reste à faire ; à la
# date, elle revient en tête. Remplace les commentaires « le client ne répond pas », « en
# instance », « rappel après-midi » qui ne faisaient jamais revenir la commande.
SUIVI_NE_REPOND_PAS, SUIVI_A_RAPPELER, SUIVI_EN_INSTANCE = "Ne répond pas", "À rappeler", "En instance"
SUIVIS = (SUIVI_NE_REPOND_PAS, SUIVI_A_RAPPELER, SUIVI_EN_INSTANCE)
CHAMPS_SUIVI = ("custom_suivi", "custom_suivi_le", "custom_suivi_note", "custom_suivi_par")
HEURE_RAPPEL = "09:00:00"           # « ne répond pas » : on rappelle le lendemain matin


def _suivis(noms) -> dict:
    """{commande: {suivi, le, note, par}} — vide tant que les champs n'existent pas (avant le patch)."""
    noms = [n for n in (noms or []) if n]
    if not noms or not frappe.db.has_column("Sales Order", "custom_suivi"):
        return {}
    return {r.name: r for r in frappe.get_all(
        "Sales Order", filters={"name": ["in", noms], "custom_suivi": ["is", "set"]},
        fields=["name"] + list(CHAMPS_SUIVI), limit_page_length=0)}


def programmee_plus_tard(suivi_le, maintenant=None) -> bool:
    """La date de reprise n'est pas encore atteinte : la commande attend, elle n'est pas « à faire »."""
    return bool(suivi_le) and get_datetime(suivi_le) > (maintenant or now_datetime())


def est_a_traiter(docstatus, statut, anomalie) -> bool:
    """La même règle que SQL_A_TRAITER, pour une commande déjà chargée."""
    if docstatus == 0:
        return True
    if docstatus != 1 or statut in STATUTS_CLOS:
        return False
    return statut in STATUTS_A_LIVRER or bool(anomalie)


def commentaire_traite(comment_type: str, texte: str) -> bool:
    """Ce commentaire du jour vaut-il « commande traitée » ? Un commentaire écrit à la main, ou la
    trace d'une action de l'écran — pas une trace automatique (SMS d'annulation, BL…)."""
    from customization_app import commentaires_commande as CC
    from customization_app import historique_client as HC

    t = HC.texte(texte or "", 200)
    if not t:
        return False
    if comment_type == "Info":
        return t.startswith(MARQUEURS_ACTIONS)
    return comment_type == "Comment" and not CC.est_automatique(t) and HC.nature_commentaire(t) != "auto"


def traitees_aujourdhui(noms) -> set:
    """Les commandes qui ont déjà reçu un traitement humain AUJOURD'HUI : commentaire écrit, action
    de l'écran (envoi, livraison autorisée), ou appel de confirmation sans réponse noté."""
    noms = [n for n in (noms or []) if n]
    if not noms:
        return set()
    jour = nowdate()
    out = {c.reference_name for c in frappe.db.sql(
        """SELECT reference_name, comment_type, content FROM `tabComment`
           WHERE reference_doctype = 'Sales Order' AND reference_name IN %(noms)s
             AND creation >= %(jour)s AND owner NOT IN ('Administrator', 'Guest')
             AND comment_type IN ('Comment', 'Info')""",
        {"noms": tuple(noms), "jour": jour}, as_dict=True)
        if commentaire_traite(c.comment_type, c.content)}
    for champ in CHAMPS_APPELS.values():
        if frappe.db.has_column("Sales Order", champ):
            out |= set(frappe.db.sql_list(
                f"SELECT name FROM `tabSales Order` WHERE name IN %(noms)s AND `{champ}` >= %(jour)s",
                {"noms": tuple(noms), "jour": jour}))
    return out


def reste_a_faire() -> dict:
    """Le chiffre de l'onglet Appels : {a_traiter, traitees, programmees, reste} — même définition
    que la vue par défaut de l'écran : à traiter − traitées aujourd'hui − programmées plus tard."""
    noms = frappe.db.sql_list(
        f"SELECT so.name FROM `tabSales Order` so WHERE so.transaction_date >= %s AND {SQL_A_TRAITER}",
        (DEPUIS_DEFAUT,))
    traitees = traitees_aujourdhui(noms)
    maintenant = now_datetime()
    programmees = {n for n, s in _suivis(noms).items() if programmee_plus_tard(s.custom_suivi_le, maintenant)}
    return {"a_traiter": len(noms), "traitees": len(traitees), "programmees": len(programmees),
            "reste": len(set(noms) - traitees - programmees)}

# Valeur d'échange pour « fiche client sans groupe » — 353 commandes sur la
# période : elles ne doivent pas disparaître silencieusement d'un filtre.
SANS_GROUPE = "__sans_groupe__"

# Notre équipe ne livre que les secteurs 1 à 7. Les secteurs 8 et 9 mobilisent
# la journée entière d'un technicien (règle du moteur de planification) et
# « Hors Secteur » n'est pas desservi : y ouvrir la réservation en ligne
# promettrait au client un créneau qu'on ne peut pas tenir.
SECTEURS_LIVRAISON = {"Secteur %d" % n for n in range(1, 8)}

MODE_PAIEMENT_DEFAUT = "Espèces"

# Ordres de tri proposés : par date, par valeur, ou les deux combinés.
# [(champ, décroissant), …] — le 2e critère départage le 1er.
TRIS = {
    "date_asc": [("date", False)],
    "date_desc": [("date", True)],
    "ttc_desc": [("ttc", True)],
    "ttc_asc": [("ttc", False)],
    "date_ttc": [("date", False), ("ttc", True)],
    "ttc_date": [("ttc", True), ("date", False)],
}
TRI_DEFAUT = "date_asc"


def _lecture():
    frappe.has_permission("Sales Order", "read", throw=True)


# ------------------------------------------------------------------ stock


def _stock(codes):
    """{article: quantité physique, tous entrepôts confondus}."""
    if not codes:
        return {}
    lignes = frappe.get_all(
        "Bin", filters={"item_code": ["in", list(codes)]},
        fields=["item_code", "sum(actual_qty) as qte"],
        group_by="item_code", limit_page_length=0)
    return {l.item_code: flt(l.qte) for l in lignes}


def _articles_stockes(codes):
    """Les articles réellement STOCKÉS — les services en sont exclus."""
    if not codes:
        return set()
    return {i.name for i in frappe.get_all(
        "Item", filters={"name": ["in", list(codes)], "is_stock_item": 1},
        fields=["name"], limit_page_length=0)}


def _engage(codes):
    """{article: reste à livrer sur les commandes soumises ENCORE ouvertes}.

    Un article peut être en stock et déjà promis ailleurs : sans ce calcul,
    l'écran dirait « disponible » pour une pièce vendue trois fois.
    """
    if not codes:
        return {}
    lignes = frappe.db.sql(
        """SELECT soi.item_code, SUM(soi.qty - soi.delivered_qty) AS reste
           FROM `tabSales Order Item` soi
           JOIN `tabSales Order` so ON so.name = soi.parent
           WHERE so.docstatus = 1 AND so.status NOT IN %(clos)s
             AND soi.item_code IN %(codes)s AND soi.qty > soi.delivered_qty
           GROUP BY soi.item_code""",
        {"clos": STATUTS_CLOS, "codes": tuple(codes)}, as_dict=True)
    return {l.item_code: flt(l.reste) for l in lignes}


def _secteurs(noms):
    """{commande: (secteur, adresse)} — le secteur vient de l'ADRESSE DE
    LIVRAISON (celle où le technicien se déplace), la facturation en repli.

    C'est la même sectorisation que le portail de rendez-vous
    (`Address.custom_secteur`) : une commande à traiter et un RDV parlent donc
    du même découpage, sinon on trierait la tournée sur deux cartes.
    """
    out = {}
    if not noms:
        return out
    commandes = frappe.get_all(
        "Sales Order", filters={"name": ["in", noms]},
        fields=["name", "shipping_address_name", "customer_address"],
        limit_page_length=0)
    adresses = {c.shipping_address_name or c.customer_address for c in commandes}
    adresses.discard(None)
    secteurs = {a.name: a.custom_secteur for a in frappe.get_all(
        "Address", filters={"name": ["in", list(adresses)]},
        fields=["name", "custom_secteur"], limit_page_length=0)} if adresses else {}
    for c in commandes:
        adresse = c.shipping_address_name or c.customer_address
        out[c.name] = (secteurs.get(adresse) or "", adresse or "")
    return out


def _groupes_clients(clients):
    """{client: groupe} — le « type de client » (Individuel, Compte Pro,
    Technicien, Quincaillerie…). Vide pour les fiches sans groupe, qui sont
    nombreuses et doivent rester choisissables telles quelles."""
    if not clients:
        return {}
    return {c.name: (c.customer_group or "") for c in frappe.get_all(
        "Customer", filters={"name": ["in", list(clients)]},
        fields=["name", "customer_group"], limit_page_length=0)}


def _livraison_equipe(noms):
    """Les commandes que NOTRE équipe livre — celles dont le client peut
    réserver un créneau de livraison sur le portail."""
    if not noms:
        return set()
    return {c.name for c in frappe.get_all(
        "Sales Order",
        filters={"name": ["in", noms], "custom_livraison_equipe": 1},
        fields=["name"], limit_page_length=0)}


def _groupe_et_descendants(motif):
    """Le groupe d'articles correspondant au motif, ET toute sa descendance.

    ⚠️ « Main d’œuvre » s'écrit avec une apostrophe TYPOGRAPHIQUE en base : un
    nom écrit en dur avec une apostrophe droite ne matcherait jamais (piège déjà
    payé sur un filtre de cette app). On le retrouve donc par motif, et on prend
    les sous-groupes par lft/rgt pour qu'un futur découpage n'échappe pas au
    filtre.
    """
    parent = frappe.db.sql_list(
        "SELECT name FROM `tabItem Group` WHERE name LIKE %s ORDER BY lft LIMIT 1",
        (motif,))
    if not parent:
        return []
    lft, rgt = frappe.db.get_value("Item Group", parent[0], ["lft", "rgt"])
    return frappe.db.sql_list(
        "SELECT name FROM `tabItem Group` WHERE lft >= %s AND rgt <= %s", (lft, rgt))


def _prestations(noms):
    """{commande: {"livraison": bool, "main_oeuvre": bool}} — ce que la commande
    contient comme PRESTATION, par groupe d'articles.

    Sert à répondre « qu'est-ce qu'il y a à faire sur cette commande ? » :
    une livraison à assurer, une intervention de main d'œuvre, ou rien du tout
    (une simple vente de pièces, que le client vient chercher).
    """
    out = {}
    if not noms:
        return out
    familles = {"livraison": _groupe_et_descendants("Livraison"),
                "main_oeuvre": _groupe_et_descendants("Main d%uvre")}
    groupes = {g: cle for cle, liste in familles.items() for g in liste}
    if not groupes:
        return out
    for l in frappe.db.sql(
            """SELECT DISTINCT soi.parent AS commande, i.item_group AS groupe
               FROM `tabSales Order Item` soi
               JOIN `tabItem` i ON i.name = soi.item_code
               WHERE soi.parenttype = 'Sales Order'
                 AND soi.parent IN %(noms)s AND i.item_group IN %(groupes)s""",
            {"noms": tuple(noms), "groupes": tuple(groupes)}, as_dict=True):
        cle = groupes.get(l.groupe)
        if cle:
            out.setdefault(l.commande, {})[cle] = True
    return out


def _reste_a_livrer(noms):
    """{commande: {n° de ligne: reste à livrer}} — une commande soldée ne manque de rien.

    Par LIGNE et non par article : un même article sur deux lignes voyait la seconde écraser
    la première, et les deux affichaient le même reste."""
    out = {}
    if not noms:
        return out
    for l in frappe.get_all(
            "Sales Order Item", filters={"parent": ["in", noms],
                                         "parenttype": "Sales Order"},
            fields=["parent", "idx", "qty", "delivered_qty"],
            limit_page_length=0):
        reste = flt(l.qty) - flt(l.delivered_qty)
        out.setdefault(l.parent, {})[l.idx] = max(reste, 0)
    return out


# ------------------------------------------------------------------ lecture


def _enrichir_articles(commande, lignes, contexte, vivante=True, brouillon=False):
    """Chaque ligne d'article reçoit son stock et son verdict.

    Une commande MORTE (annulée, fermée, terminée) n'a plus rien à livrer : elle ne manque de
    rien — elle gonflait « Rupture réelle ». Un BROUILLON n'est pas dans l'engagé (qui ne compte
    que les commandes validées) : retirer son propre reste surestimait son disponible.

    ⚠️ DEUX PROBLÈMES DIFFÉRENTS, à ne jamais confondre (mesuré le 30/08 : sur
    54 commandes signalées, 42 l'étaient à cause de ça et 10 seulement étaient
    de vraies ruptures) :
      • `stock_negatif` — la fiche de stock est FAUSSE (livraisons sans écriture
        de stock, jusqu'à -123 sur une référence). L'article est peut-être en
        rayon : il faut corriger l'inventaire, surtout pas annuler la commande.
      • `manque` avec un stock connu et positif — là, on n'a vraiment pas la
        quantité : c'est le cas qui justifie un remplacement ou une annulation.
    """
    stock, engage, stockes, restes = contexte
    reste_cde = restes.get(commande, {})
    manquants = reels = negatifs = 0
    out = []
    for a in lignes:
        code = a["code"]
        est_stocke = code in stockes
        reste = reste_cde.get(a.get("idx"), a["qte"]) if vivante else 0
        qte_stock = stock.get(code, 0)
        # L'engagement inclut CETTE commande quand elle est validée : on le retire
        # pour ne pas se concurrencer soi-même. Un brouillon n'y est pas.
        dispo = qte_stock - (engage.get(code, 0) if brouillon
                             else max(engage.get(code, 0) - reste, 0))
        manque = bool(est_stocke and reste > 0 and dispo < reste)
        negatif = bool(est_stocke and qte_stock < 0)
        if manque:
            manquants += 1
            if negatif:
                negatifs += 1
            else:
                reels += 1
        out.append(dict(a, stocke=est_stocke, stock=qte_stock,
                        dispo=dispo if est_stocke else None,
                        reste=reste, manque=manque, stock_negatif=negatif))
    return out, manquants, reels, negatifs


@frappe.whitelist()
def get_filtres():
    """Les valeurs proposées dans la barre de filtres — lues du réel, pas en dur."""
    _lecture()
    statuts = frappe.db.sql_list(
        """SELECT DISTINCT status FROM `tabSales Order`
           WHERE transaction_date >= %s ORDER BY status""", (DEPUIS_DEFAUT,))
    # Les secteurs réellement portés par les adresses des commandes de la
    # période — pas la table théorique : on ne propose pas un filtre vide.
    secteurs = frappe.db.sql_list(
        """SELECT DISTINCT a.custom_secteur
           FROM `tabSales Order` so
           JOIN `tabAddress` a
             ON a.name = COALESCE(so.shipping_address_name, so.customer_address)
           WHERE so.transaction_date >= %s AND IFNULL(a.custom_secteur, '') != ''
           ORDER BY a.custom_secteur""", (DEPUIS_DEFAUT,))
    # Les types de clients réellement présents, avec leur volume : on coche
    # en connaissance de cause, et « sans groupe » reste visible.
    groupes = [{"valeur": (l.customer_group or SANS_GROUPE),
                "libelle": l.customer_group or "— sans type",
                "n": l.n}
               for l in frappe.db.sql(
                   """SELECT c.customer_group, COUNT(*) AS n
                      FROM `tabSales Order` so
                      JOIN `tabCustomer` c ON c.name = so.customer
                      WHERE so.transaction_date >= %s
                      GROUP BY c.customer_group ORDER BY n DESC""",
                   (DEPUIS_DEFAUT,), as_dict=True)]
    # Les types d'anomalie, dans l'ordre de commande_alertes (le même que le filtre de la liste).
    from customization_app.commande_alertes import MOTIF_COMMANDE_ANNULEE, MOTIFS
    return {"statuts": statuts, "secteurs": secteurs, "groupes": groupes,
            "anomalies": list(MOTIFS) + [MOTIF_COMMANDE_ANNULEE],
            "depuis_defaut": DEPUIS_DEFAUT}


def _envois(noms):
    """{commande: {n, dernier, par, canal}} — les relances déjà parties.

    POURQUOI RELIRE LES COMMENTAIRES plutôt que de tenir un champ. `sms_commandes`
    pose une trace sur CHAQUE commande touchée, avec le canal, l'auteur et le
    texte : c'est déjà la mémoire de l'envoi, et elle vaut pour tous les chemins
    (envoi groupé depuis la liste des commandes comme depuis cet écran). Un
    champ en plus se désynchroniserait du jour où quelqu'un enverrait par
    l'autre porte.

    ⚠️ LE MOTIF NE PORTE NI ACCENT NI EMOJI. La trace commence par « 📨 Envoi
    groupé » ; chercher cette chaîne telle quelle expose aux surprises de
    collation (déjà rencontré sur « Commandes à traiter » comparé en SQL).
    « Envoi group » suffit à l'identifier et ne peut rien attraper d'autre.
    """
    if not noms:
        return {}
    out = {}
    for c in frappe.db.sql(
            """SELECT reference_name, content, creation, owner
               FROM tabComment
               WHERE comment_type = 'Info' AND reference_doctype = 'Sales Order'
                 AND reference_name IN %(noms)s AND content LIKE '%%Envoi group%%'
               ORDER BY creation""",
            {"noms": tuple(noms)}, as_dict=True):
        texte = c.content or ""
        # Le canal se lit dans la trace elle-même : « SMS : envoyé … · E-mail : — ».
        bloc = texte.split("<br>")[0]
        sms = "SMS : envoy" in bloc or "SMS : SIMUL" in bloc
        mail = "E-mail : envoy" in bloc or "E-mail : SIMUL" in bloc
        e = out.setdefault(c.reference_name, {"n": 0, "dernier": None, "par": "",
                                              "sms": False, "email": False})
        e["n"] += 1
        e["dernier"] = str(c.creation)[:16]
        e["par"] = c.owner
        e["sms"] = e["sms"] or sms
        e["email"] = e["email"] or mail
    return out


def _liste_cochee(valeur):
    """Un filtre à cases à cocher : JSON de l'écran -> set, ou None.

    None et l'ensemble vide ne veulent PAS dire la même chose : None = le
    filtre n'a rien envoyé, donc on n'écarte rien ; set() = l'utilisateur a
    tout décoché, donc il ne doit rien voir. Confondre les deux ferait
    réapparaître toute la liste au moment où il vient de la vider.
    """
    if isinstance(valeur, str):
        valeur = frappe.parse_json(valeur) if valeur.strip() else None
    return set(valeur) if valeur is not None else None


@frappe.whitelist()
def get_commandes(depuis=None, jusqu_a=None, recherche=None, statut=None,
                  origine=None, dispo=None, anomalie=None, tache=None,
                  secteur=None, livraison=None, prestation=None, client=None, commentaire=None,
                  groupes=None, envoi=None, tri=None, start=0,
                  page_length=PAGE_LENGTH, vue=None, suivi=None):
    """L'arriéré filtré. Tout est calculé sur l'ENSEMBLE puis découpé en pages :
    un filtre « article manquant » doit porter sur toutes les commandes, pas
    seulement sur celles de la page affichée.

    `vue` : VUE_RESTE (défaut — le reste à faire, le même chiffre que l'onglet Appels),
    VUE_A_TRAITER (y compris ce qui a déjà été traité aujourd'hui) ou VUE_TOUTES (tout
    l'historique de la période, l'ancien comportement). Appliquée AVANT les calculs : on ne
    calcule le stock que de la centaine de commandes à traiter, pas des 2 300 de la période."""
    _lecture()
    start, page_length = frappe.utils.cint(start), frappe.utils.cint(page_length)

    # `groupes` et `secteur` arrivent en JSON depuis l'écran. Aucune valeur =
    # AUCUN filtre (tout est affiché) ; une liste vide reste une liste vide,
    # c'est-à-dire « rien coché, donc rien à montrer » — les deux cas sont
    # distincts.
    groupes = _liste_cochee(groupes)
    secteur = _liste_cochee(secteur)

    lignes = _commandes(getdate(depuis or DEPUIS_DEFAUT),
                        getdate(jusqu_a or nowdate()))
    # Combien de commandes ce client a-t-il sur la PÉRIODE ? Compté avant la vue et les
    # filtres : le badge dit la même chose que la fenêtre « N commandes », qui les liste toutes.
    par_client = {}
    for l in lignes:
        par_client[l.customer] = par_client.get(l.customer, 0) + 1

    vue = vue if vue in (VUE_RESTE, VUE_A_TRAITER, VUE_TOUTES) else VUE_DEFAUT
    a_traiter = [l for l in lignes if est_a_traiter(l.docstatus, l.status, l.custom_anomalie)]
    traitees = traitees_aujourdhui([l.name for l in a_traiter])
    suivis = _suivis([l.name for l in lignes])
    maintenant = now_datetime()
    programmees = {l.name for l in a_traiter
                   if programmee_plus_tard((suivis.get(l.name) or {}).get("custom_suivi_le"), maintenant)}
    if vue == VUE_RESTE:
        lignes = [l for l in a_traiter if l.name not in traitees and l.name not in programmees]
    elif vue == VUE_A_TRAITER:
        lignes = a_traiter
    noms = [l.name for l in lignes]
    articles = _articles(noms)
    taches = _taches(noms)
    bordereaux = _bordereaux(noms)

    secteurs = _secteurs(noms)
    envois = _envois(noms)
    livraisons = _livraison_equipe(noms)
    prestations = _prestations(noms)
    groupes_cl = _groupes_clients({l.customer for l in lignes if l.customer})
    codes = {a["code"] for lot in articles.values() for a in lot}
    contexte = (_stock(codes), _engage(codes), _articles_stockes(codes),
                _reste_a_livrer(noms))

    sans_tel = {l.customer for l in lignes if not (l.contact_mobile or l.contact_phone)}
    tel_client = dict(frappe.get_all(
        "Customer", filters={"name": ["in", list(sans_tel)]},
        fields=["name", "custom_liste_telephone"], as_list=True)) if sans_tel else {}

    out = []
    for l in lignes:
        lot, manquants, reels, negatifs = _enrichir_articles(
            l.name, articles.get(l.name, []), contexte,
            vivante=(l.docstatus != 2 and l.status not in STATUTS_CLOS),
            brouillon=(l.docstatus == 0))
        out.append({
            "name": l.name,
            "date": str(l.transaction_date),
            "statut": l.status,
            "docstatus": l.docstatus,
            "client": l.customer,
            "client_nom": l.customer_name or l.customer,
            "groupe_client": groupes_cl.get(l.customer, ""),
            "telephone": (l.contact_mobile or l.contact_phone
                          or (tel_client.get(l.customer) or "").split("\n")[0].strip()),
            "adresse": (l.shipping_address or l.address_display or "").replace("<br>", ", "),
            "secteur": secteurs.get(l.name, ("", ""))[0],
            # Ce qui a DÉJÀ été envoyé sur cette commande : sans ça, on relance
            # deux fois le même client sans le savoir.
            "envoi": envois.get(l.name),
            # Les appels de confirmation restés SANS RÉPONSE (commandes web) :
            # « on a écrit » et « on a essayé de joindre » ne se remplacent pas,
            # et c'est le second qui justifie d'annuler.
            "appels": [{"rang": rang, "libelle": LIBELLES_APPELS.get(rang, ""),
                        "date": str(l.get(champ))[:16]}
                       for rang, champ in sorted(CHAMPS_APPELS.items()) if l.get(champ)],
            "livraison_equipe": l.name in livraisons,
            "a_livraison": bool(prestations.get(l.name, {}).get("livraison")),
            "a_main_oeuvre": bool(prestations.get(l.name, {}).get("main_oeuvre")),
            "ttc": flt(l.grand_total, 3),
            "devise": l.currency or "TND",
            "articles": lot,
            "manquants": manquants,
            "manques_reels": reels,
            "stock_negatif": negatifs,
            "taches": taches.get(l.name, []),
            "anomalie": l.custom_anomalie or "",
            # 💬 dernier commentaire humain (commentaires_commande) : texte, date, auteur.
            "commentaire": l.get("custom_dernier_commentaire") or "",
            "commentaire_le": str(l.get("custom_commentaire_le") or "")[:16],
            "commentaire_par": l.get("custom_commentaire_par") or "",
            "aramex_sb": bool(l.get("custom_aramex_sans_bordereau")),
            "bordereau": (bordereaux.get(l.name) or {}).get("bordereau")
                         or l.custom_bordereau_aramex or "",
            "web": bool(l.woocommerce_id),
            "traitee_aujourdhui": l.name in traitees,
            **_suivi_de_ligne(suivis.get(l.name), maintenant),
        })

    for c in out:
        c["commandes_client"] = par_client.get(c["client"], 1)

    # Combien de commandes de la VUE portent chaque type d'anomalie (avant les filtres) : le menu
    # « Anomalie » l'affiche à côté de chaque type.
    from customization_app.commande_alertes import MOTIF_COMMANDE_ANNULEE
    par_anomalie = {}
    for c in out:
        if c["anomalie"]:
            cle = MOTIF_COMMANDE_ANNULEE if c["anomalie"].startswith(MOTIF_COMMANDE_ANNULEE) else c["anomalie"]
            par_anomalie[cle] = par_anomalie.get(cle, 0) + 1

    out = _trier(_filtrer(out, recherche, statut, origine, dispo, anomalie,
                          tache, secteur, livraison, prestation, client,
                          groupes, envoi, commentaire, suivi), tri)
    if vue == VUE_RESTE:
        # Les commandes dont la date de reprise est arrivée passent devant : on les a promises.
        out.sort(key=lambda c: 0 if c.get("suivi_echu") else 1)
    total = len(out)
    page = out[start:start + page_length] if page_length else out
    return {
        "lignes": page,
        "total": total,
        "kpis": {
            "commandes": total,
            "manque_reel": len([c for c in out if c["manques_reels"]]),
            "stock_negatif": len([c for c in out if c["stock_negatif"]]),
            "sans_tache": len([c for c in out if not c["taches"]]),
            "livraison_equipe": len([c for c in out if c["livraison_equipe"]]),
            "clients_multi": len({c["client"] for c in out
                                  if c["commandes_client"] > 1}),
            "anomalies": len([c for c in out if c["anomalie"]]),
            "ttc": round(sum(c["ttc"] for c in out), 3),
            # Les deux chiffres de l'onglet Appels, sur la même définition, filtres de l'écran
            # NON appliqués : « reste à faire » = à traiter − traitées aujourd'hui.
            "a_traiter": len(a_traiter),
            "traitees_aujourdhui": len(traitees),
            "programmees": len(programmees),
            "reste": len({l.name for l in a_traiter} - traitees - programmees),
            "par_anomalie": par_anomalie,
            "a_reprendre": len([l for l in a_traiter if l.name not in programmees
                                and (suivis.get(l.name) or {}).get("custom_suivi")]),
        },
        "vue": vue,
    }


def _trier(lignes, tri):
    """Tri stable à plusieurs critères : on trie par le critère le MOINS
    important d'abord, le tri suivant préserve l'ordre obtenu (Python garantit
    la stabilité). C'est ce qui permet « date puis valeur » en deux passes."""
    for champ, decroissant in reversed(TRIS.get(tri or TRI_DEFAUT,
                                                TRIS[TRI_DEFAUT])):
        lignes.sort(key=lambda c: c[champ], reverse=decroissant)
    return lignes


def _suivi_de_ligne(s, maintenant) -> dict:
    """Le suivi tel que l'écran l'affiche ; `suivi_echu` = la date de reprise est atteinte."""
    if not s or not s.get("custom_suivi"):
        return {"suivi": "", "suivi_le": "", "suivi_note": "", "suivi_par": "", "suivi_echu": False}
    le = s.get("custom_suivi_le")
    return {"suivi": s.custom_suivi, "suivi_le": str(le or "")[:16], "suivi_note": s.get("custom_suivi_note") or "",
            "suivi_par": s.get("custom_suivi_par") or "", "suivi_echu": not programmee_plus_tard(le, maintenant)}


def _filtrer(lignes, recherche, statut, origine, dispo, anomalie, tache,
             secteur=None, livraison=None, prestation=None, client=None,
             groupes=None, envoi=None, commentaire=None, suivi=None):
    def garde(c):
        if statut and c["statut"] != statut:
            return False
        # Suivi : à reprendre maintenant (date atteinte), programmé plus tard, un type, ou aucun.
        if suivi == "echu" and not (c.get("suivi") and c.get("suivi_echu")):
            return False
        if suivi == "programme" and not (c.get("suivi") and not c.get("suivi_echu")):
            return False
        if suivi == "sans" and c.get("suivi"):
            return False
        if suivi in SUIVIS and c.get("suivi") != suivi:
            return False
        # 💬 commenté ou pas (le dernier commentaire humain de la commande).
        if commentaire == "avec" and not c.get("commentaire"):
            return False
        if commentaire == "sans" and c.get("commentaire"):
            return False
        # Secteurs cochés (multi-sélection). « __vide__ » désigne les adresses
        # jamais sectorisées, qu'on veut pouvoir isoler pour les corriger.
        if secteur is not None and (c["secteur"] or "__vide__") not in secteur:
            return False
        # Relances déjà parties : le tri le plus utile de cet écran est
        # « qui n'a pas encore été prévenu ».
        if envoi == "oui" and not c["envoi"]:
            return False
        if envoi == "non" and c["envoi"]:
            return False
        if origine == "web" and not c["web"]:
            return False
        if origine == "magasin" and c["web"]:
            return False
        if dispo == "manquant" and not c["manques_reels"]:
            return False
        if dispo == "negatif" and not c["stock_negatif"]:
            return False
        if dispo == "complet" and c["manquants"]:
            return False
        if anomalie == "avec" and not c["anomalie"]:
            return False
        if anomalie == "sans" and c["anomalie"]:
            return False
        # Un TYPE d'anomalie précis. Par préfixe : « Commande annulée avec tâche » porte le nom de
        # la tâche à la suite (Tache-08055…).
        if anomalie and anomalie not in ("avec", "sans") and not (c["anomalie"] or "").startswith(anomalie):
            return False
        if tache == "sans" and c["taches"]:
            return False
        if tache == "avec" and not c["taches"]:
            return False
        if livraison == "avec" and not c["livraison_equipe"]:
            return False
        if livraison == "sans" and c["livraison_equipe"]:
            return False
        # Ce que la commande contient à FAIRE : une livraison, une main d'œuvre,
        # ou rien (vente de pièces à emporter).
        if prestation == "livraison" and not c["a_livraison"]:
            return False
        if prestation == "installation" and not c["a_main_oeuvre"]:
            return False
        if prestation == "sans" and (c["a_livraison"] or c["a_main_oeuvre"]):
            return False
        if groupes is not None and (c["groupe_client"] or SANS_GROUPE) not in groupes:
            return False
        if client == "multi" and c["commandes_client"] < 2:
            return False
        if client == "unique" and c["commandes_client"] > 1:
            return False
        if recherche:
            aiguille = recherche.lower().strip()
            foin = " ".join([c["name"], c["client"], c["client_nom"],
                             c["groupe_client"], c["telephone"],
                             c["adresse"], c["anomalie"], c.get("commentaire") or ""]
                            + [a["code"] + " " + a["article"] for a in c["articles"]])
            if aiguille not in foin.lower():
                return False
        return True

    return [c for c in lignes if garde(c)]


# ------------------------------------------------------------------ actions


def _base_boutique():
    """L'adresse de la boutique en ligne, lue de la config WooCommerce.

    ⚠️ Ne PAS filtrer sur `enable_sync` : le restore de prod le met à 0 en dev
    (la boutique ne doit jamais être touchée depuis le dev) — or l'URL reste
    parfaitement valide pour fabriquer un lien de lecture.
    """
    return (frappe.db.get_value("WooCommerce Server", {},
                                "woocommerce_server_url") or "").rstrip("/")


def _liens_boutique(codes):
    """{article: lien vers sa fiche sur le site} pour les articles synchronisés.

    On construit le lien depuis l'ID WooCommerce (`?post_type=product&p=ID`) :
    WordPress redirige vers le vrai permalien, donc pas besoin de stocker ni de
    deviner le slug — un article renommé garde un lien valide.
    """
    base = _base_boutique()
    if not base or not codes:
        return {}
    lignes = frappe.get_all(
        "Item WooCommerce Server",
        filters={"parent": ["in", list(codes)], "parenttype": "Item",
                 "woocommerce_id": ["!=", ""]},
        fields=["parent", "woocommerce_id"], limit_page_length=0)
    return {l.parent: "%s/?post_type=product&p=%s" % (base, l.woocommerce_id)
            for l in lignes if l.woocommerce_id}


@frappe.whitelist()
def chercher_articles(recherche=None, en_stock=0):
    """Le sélecteur d'articles de remplacement.

    Choix manuel assumé (décision 30/08) : pas de suggestion automatique, c'est
    le magasin qui sait ce qui remplace quoi. Chaque article part avec son LIEN
    BOUTIQUE : le client doit pouvoir voir la photo et le prix de ce qu'on lui
    propose, pas seulement un code article.

    CE QU'ON PEUT PROPOSER (règle 30/08) : n'importe quel article ACTIF qui
    n'est pas marqué « rupture de stock site web ». C'est ce drapeau qui fait
    foi, pas la quantité en magasin — c'est lui qui décide de la présence au
    catalogue en ligne, et proposer un lien vers une fiche retirée du site
    serait une impasse pour le client. La quantité reste AFFICHÉE, pour
    décider en connaissance de cause.
    """
    _lecture()
    conditions = ["i.disabled = 0", "IFNULL(i.custom_rupture_site_web, 0) = 0"]
    params = {}
    if recherche:
        conditions.append("(i.name LIKE %(r)s OR i.item_name LIKE %(r)s)")
        params["r"] = "%" + recherche.strip() + "%"
    if frappe.utils.cint(en_stock):
        conditions.append("b.qte > 0")
    lignes = frappe.db.sql(
        """SELECT i.name AS code, i.item_name AS article, i.stock_uom AS unite,
                  IFNULL(b.qte, 0) AS stock
           FROM `tabItem` i
           LEFT JOIN (SELECT item_code, SUM(actual_qty) qte FROM `tabBin`
                      GROUP BY item_code) b ON b.item_code = i.name
           WHERE {conditions}
           ORDER BY b.qte DESC, i.name LIMIT 50""".format(
            conditions=" AND ".join(conditions)), params, as_dict=True)
    liens = _liens_boutique({l.code for l in lignes})
    for l in lignes:
        l["lien"] = liens.get(l.code, "")
    return lignes


def _sauvegarder(doc, motif):
    """Archive la commande AVANT sa disparition : un fichier JSON rouvrable et
    une trace lisible, tous deux attachés à la FICHE CLIENT.

    Pourquoi le client et pas la commande : le document va être supprimé, et
    tout ce qui y était rattaché partirait avec lui. Une suppression sans trace
    est irrattrapable — six mois plus tard, personne ne peut plus dire ce que
    contenait la commande ni pourquoi elle a disparu.
    """
    contenu = json.dumps(doc.as_dict(), indent=1, ensure_ascii=False, default=str)
    fichier = frappe.get_doc({
        "doctype": "File",
        "file_name": "%s-supprimee.json" % doc.name,
        "attached_to_doctype": "Customer",
        "attached_to_name": doc.customer,
        "is_private": 1,
        "content": contenu,
    })
    fichier.flags.ignore_permissions = True
    fichier.insert()

    articles = ", ".join("%s ×%s" % (l.item_code, flt(l.qty)) for l in doc.items)
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info",
        "reference_doctype": "Customer", "reference_name": doc.customer,
        "content": _("🗑️ Commande <b>{0}</b> du {1} SUPPRIMÉE par {2} — {3}<br>"
                     "Total : {4} {5} · Articles : {6}<br>"
                     "Sauvegarde complète : <a href=\"{7}\">{8}</a>").format(
            doc.name, doc.transaction_date, frappe.session.user,
            frappe.utils.escape_html(motif or "sans motif"),
            flt(doc.grand_total, 3), doc.currency or "TND",
            frappe.utils.escape_html(articles)[:300],
            fichier.file_url, fichier.file_name),
    }).insert(ignore_permissions=True)
    return fichier.file_url


@frappe.whitelist()
def annuler(noms, motif=None):
    """Annule les commandes sélectionnées — la cascade maison (BL, échéancier,
    tâches) se déclenche par les hooks d'`annulation_commande`.

    (Le décorateur avait glissé sur `_sauvegarder`, inséré au-dessus le 30/08 : le bouton
    « ❌ Annuler » de l'écran était refusé par Frappe, et une fonction interne était exposée.)

    Un BROUILLON (commande web jamais validée) suit la chaîne COMPLÈTE demandée
    le 30/08 : valider → annuler → supprimer, après sauvegarde. ERPNext ne sait
    pas « annuler » un brouillon ; le valider d'abord fait passer la commande
    par la vraie cascade d'annulation (BL, échéancier, tâches) au lieu de la
    faire disparaître en silence.
    """
    frappe.has_permission("Sales Order", "cancel", throw=True)
    frappe.has_permission("Sales Order", "delete", throw=True)
    noms = frappe.parse_json(noms) if isinstance(noms, str) else (noms or [])
    resultat = []
    for nom in [n for n in noms if n]:
        doc = frappe.get_doc("Sales Order", nom)
        if doc.docstatus == 0:
            try:
                sauvegarde = _sauvegarder(doc, motif)
                doc.flags.ignore_permissions = True
                # Le mode de paiement est obligatoire sur les lignes
                # d'échéancier de ce site ; un brouillon incomplet bloquerait
                # la validation. Les commandes web l'ont, pas toutes les autres.
                for ligne in doc.payment_schedule or []:
                    if not ligne.mode_of_payment:
                        ligne.mode_of_payment = MODE_PAIEMENT_DEFAUT
                chemin = "validé, annulé puis supprimé"
                try:
                    doc.submit()
                    doc.reload()
                    # Le SMS automatique d'annulation (commandes web) partirait pour une commande
                    # supprimée l'instant d'après : la tâche échouerait. On ne l'envoie pas.
                    doc.flags.sans_sms_annulation_auto = True
                    doc.cancel()
                    doc.reload()
                except Exception as e:
                    # La validation est impossible (données incomplètes) : on
                    # honore quand même l'intention. Un brouillon n'a JAMAIS
                    # touché la comptabilité ni le stock — le supprimer
                    # directement ne laisse rien derrière, et la sauvegarde est
                    # déjà faite. On le DIT, au lieu de le masquer.
                    frappe.db.rollback()
                    doc = frappe.get_doc("Sales Order", nom)
                    chemin = ("supprimé sans validation possible (%s)"
                              % str(e).strip()[:60])
                frappe.delete_doc("Sales Order", nom, force=1,
                                  ignore_permissions=True, delete_permanently=True)
                frappe.db.commit()
                resultat.append({"commande": nom,
                                 "etat": "brouillon %s — sauvegarde %s"
                                         % (chemin, sauvegarde)})
            except Exception as e:
                frappe.db.rollback()
                resultat.append({"commande": nom,
                                 "etat": "échec sur le brouillon : %s" % str(e)[:150]})
            continue
        if doc.docstatus == 2:
            resultat.append({"commande": nom, "etat": "déjà annulée"})
            continue
        try:
            doc.cancel()
            if motif:
                frappe.get_doc({
                    "doctype": "Comment", "comment_type": "Info",
                    "reference_doctype": "Sales Order", "reference_name": nom,
                    "content": _("❌ Annulée depuis « Commandes à traiter » par {0} — {1}")
                               .format(frappe.session.user, frappe.utils.escape_html(motif)),
                }).insert(ignore_permissions=True)
            frappe.db.commit()
            resultat.append({"commande": nom, "etat": "annulée"})
        except Exception as e:
            frappe.db.rollback()
            resultat.append({"commande": nom, "etat": "échec : %s" % str(e)[:150]})
    return resultat


def appels_sans_reponse(nom: str) -> int:
    """Appels restés sans réponse sur cette commande, d'après ses commentaires (📵 et texte libre)."""
    return frappe.db.sql("""SELECT COUNT(*) FROM `tabComment`
                            WHERE reference_doctype = 'Sales Order' AND reference_name = %(nom)s
                              AND comment_type = 'Comment'
                              AND (content LIKE '%%📵%%' OR LOWER(content) LIKE '%%ne répond pas%%'
                                   OR LOWER(content) LIKE '%%ne repond pas%%' OR LOWER(content) LIKE '%%injoignable%%')""",
                         {"nom": nom})[0][0] or 0


def _quand(dt) -> str:
    return frappe.utils.format_datetime(dt, "dd/MM/yyyy HH:mm")


@frappe.whitelist()
def poser_suivi(noms, suivi, le=None, note=None):
    """Pose le suivi en un clic. « Ne répond pas » : rappel le lendemain 9 h (et, pour une commande
    web, l'appel sans réponse est horodaté — 1er puis 2e). « À rappeler » / « En instance » : la date
    de reprise est obligatoire, pour que rien ne reste en attente sans fin. Chaque suivi laisse un
    commentaire (il devient le 💬 de la commande, et compte en « fait aujourd'hui »)."""
    from customization_app.suivi_appels import CHAMPS as RANGS, est_commande_web

    frappe.has_permission("Sales Order", "write", throw=True)
    if suivi not in SUIVIS:
        frappe.throw(_("Suivi inconnu : {0}").format(suivi))
    noms = frappe.parse_json(noms) if isinstance(noms, str) else (noms or [])
    noms = [n for n in noms if n]
    maintenant = now_datetime()
    if suivi == SUIVI_NE_REPOND_PAS and not le:
        le = get_datetime("%s %s" % (add_days(nowdate(), 1), HEURE_RAPPEL))
    if not le:
        frappe.throw(_("Choisissez la date de reprise."))
    le = get_datetime(le)
    if le < maintenant.replace(hour=0, minute=0, second=0, microsecond=0):
        frappe.throw(_("La date de reprise est déjà passée."))
    note = (note or "").strip()[:140]

    resultats = []
    for nom in noms:
        if frappe.db.get_value("Sales Order", nom, "docstatus") == 2:
            resultats.append({"commande": nom, "etat": "annulée — ignorée"})
            continue
        frappe.db.set_value("Sales Order", nom, {
            "custom_suivi": suivi, "custom_suivi_le": le, "custom_suivi_note": note,
            "custom_suivi_par": frappe.session.user}, update_modified=False)
        n_appels = 0
        if suivi == SUIVI_NE_REPOND_PAS:
            # Le Nᵉ appel sans réponse, TOUTES commandes : les 📵 déjà posés et les anciens
            # commentaires écrits à la main (« le client ne répond pas », « injoignable »).
            n_appels = appels_sans_reponse(nom) + 1
            if est_commande_web(nom):
                faits = frappe.db.get_value("Sales Order", nom, list(RANGS.values()), as_dict=True) or {}
                rang = next((r for r in sorted(RANGS) if not faits.get(RANGS[r])), None)
                if rang:
                    frappe.db.set_value("Sales Order", nom, RANGS[rang], maintenant, update_modified=False)
        texte = {
            SUIVI_NE_REPOND_PAS: "📵 Ne répond pas (%s) — rappel le %s" % (
                "1er appel sans réponse" if n_appels == 1 else "%de appel sans réponse" % n_appels, _quand(le)),
            SUIVI_A_RAPPELER: "⏰ À rappeler le %s" % _quand(le),
            SUIVI_EN_INSTANCE: "⌛ En instance jusqu'au %s" % _quand(le),
        }[suivi] + ((" — " + note) if note else "")
        frappe.get_doc({"doctype": "Comment", "comment_type": "Comment", "reference_doctype": "Sales Order",
                        "reference_name": nom, "content": frappe.utils.escape_html(texte)}).insert(ignore_permissions=True)
        client, createur = frappe.db.get_value("Sales Order", nom, ["customer_name", "owner"])
        resultats.append({"commande": nom, "etat": texte, "appels": n_appels, "client": client,
                          # Au 2ᵉ appel sans réponse, l'écran propose d'annuler en prévenant le client ;
                          # pour une commande SAISIE (non web), il fait d'abord vérifier avec qui l'a créée
                          # — elle a pu être conclue autrement (autre numéro, réglée au magasin…).
                          "deuxieme_appel": n_appels >= 2,
                          "web": est_commande_web(nom),
                          "createur": frappe.utils.get_fullname(createur) if createur else ""})
    frappe.db.commit()
    return resultats


@frappe.whitelist()
def retirer_suivi(noms):
    """Retire le suivi : la commande redevient « à faire » tout de suite."""
    frappe.has_permission("Sales Order", "write", throw=True)
    noms = frappe.parse_json(noms) if isinstance(noms, str) else (noms or [])
    for nom in [n for n in noms if n]:
        frappe.db.set_value("Sales Order", nom, {c: None for c in CHAMPS_SUIVI}, update_modified=False)
    frappe.db.commit()
    return {"retires": len(noms)}


@frappe.whitelist()
def autoriser_livraison(noms, autoriser=1):
    """Ouvre (ou retire) la LIVRAISON PAR NOTRE ÉQUIPE sur des commandes.

    C'est cette autorisation qui fait apparaître le type « Livraison » (20 min)
    dans le portail de rendez-vous, pour ce client et sur cette commande. Le
    champ est `allow_on_submit` : une commande déjà soumise s'autorise sans
    l'annuler.
    """
    frappe.has_permission("Sales Order", "write", throw=True)
    noms = frappe.parse_json(noms) if isinstance(noms, str) else (noms or [])
    noms = [n for n in noms if n]
    valeur = 1 if frappe.utils.cint(autoriser) else 0
    # GARDE-FOU (demande 30/08) : on n'autorise que les secteurs 1 à 7. Un clic
    # de trop sur une commande Hors Secteur ouvrirait au client un créneau que
    # personne ne peut honorer — et c'est le client qui le découvrirait.
    # Le RETRAIT, lui, n'est jamais bloqué : on doit toujours pouvoir revenir.
    secteurs = _secteurs(noms) if valeur else {}
    faites, resultats = [], []
    for nom in noms:
        if frappe.db.get_value("Sales Order", nom, "docstatus") == 2:
            resultats.append({"commande": nom, "etat": "commande annulée — ignorée"})
            continue
        secteur = secteurs.get(nom, ("", ""))[0]
        if valeur and secteur not in SECTEURS_LIVRAISON:
            resultats.append({"commande": nom, "etat": "refusé — %s (nous livrons "
                              "les secteurs 1 à 7)"
                              % (("adresse en « %s »" % secteur) if secteur
                                 else "adresse sans secteur")})
            continue
        frappe.db.set_value("Sales Order", nom, "custom_livraison_equipe", valeur)
        # Une trace sur la commande : elle dit qui a ouvert la réservation, et la commande
        # compte comme traitée aujourd'hui (onglet Appels). Elle n'en laissait aucune.
        frappe.get_doc({
            "doctype": "Comment", "comment_type": "Info",
            "reference_doctype": "Sales Order", "reference_name": nom,
            "content": (_("🚚 Livraison par notre équipe autorisée par {0} — {1}").format(frappe.session.user, secteur)
                        if valeur else _("🚚 Livraison par notre équipe retirée par {0}").format(frappe.session.user)),
        }).insert(ignore_permissions=True)
        faites.append(nom)
        resultats.append({"commande": nom,
                          "etat": ("autorisée — %s" % secteur) if valeur
                                  else "autorisation retirée"})
    frappe.db.commit()
    return {"commandes": faites, "resultats": resultats, "autorise": bool(valeur),
            "refuses": len(resultats) - len(faites)}


@frappe.whitelist()
def annuler_et_informer(noms, motif, modele, sujet=None, sms=1, email=1,
                        remplacements=None):
    """Annule les commandes PUIS prévient les clients — dans cet ordre, et
    seulement ceux dont l'annulation a réellement abouti.

    L'ordre n'est pas un détail : annoncer « votre commande est annulée » à un
    client dont l'annulation a échoué (brouillon, document verrouillé) serait
    un mensonge que personne ne rattrape. On envoie donc APRÈS, et uniquement
    aux commandes effectivement annulées ; les autres sont rendues à l'écran
    avec leur motif d'échec.
    """
    from customization_app import sms_commandes

    noms = frappe.parse_json(noms) if isinstance(noms, str) else (noms or [])
    noms = [n for n in noms if n]
    # Les BROUILLONS sont supprimés par l'annulation : leurs destinataires (numéros, e-mails,
    # articles) sont lus AVANT, sinon leurs clients ne seraient jamais prévenus (22/10/2026).
    brouillons = [n for n in noms if frappe.db.get_value("Sales Order", n, "docstatus") == 0]
    instantane = {l["commande"]: l for l in sms_commandes._destinataires(brouillons)} if brouillons else {}

    # Le message personnalisé remplace le SMS automatique d'annulation des commandes web :
    # sinon le client en recevait DEUX.
    frappe.flags.sans_sms_annulation_auto = True
    try:
        annulations = annuler(noms, motif)
    finally:
        frappe.flags.sans_sms_annulation_auto = False

    faites = [a["commande"] for a in annulations if a["etat"] == "annulée"]
    supprimes = [a["commande"] for a in annulations
                 if a["commande"] in instantane and a["etat"].startswith("brouillon")]
    envoi = None
    if faites or supprimes:
        lignes = (sms_commandes._destinataires(faites) if faites else []) \
            + [instantane[n] for n in supprimes]
        envoi = sms_commandes.envoyer([l["commande"] for l in lignes], modele, sujet=sujet,
                                      sms=sms, email=email, remplacements=remplacements,
                                      lignes=lignes)
    return {"annulations": annulations, "informes": faites + supprimes, "envoi": envoi}


@frappe.whitelist()
def get_commandes_client(client, depuis=None, jusqu_a=None):
    """Toutes les commandes de CE client sur la période — pour la fenêtre
    « N commandes » de l'écran.

    On repasse par `get_commandes` au lieu de refaire une requête : le stock,
    les manques, les tâches et les anomalies doivent être calculés EXACTEMENT
    comme dans la liste, sinon la fenêtre annoncerait un manque que la ligne
    juste derrière ne montre pas.
    """
    _lecture()
    res = get_commandes(depuis=depuis, jusqu_a=jusqu_a, page_length=0, vue=VUE_TOUTES)
    lignes = [l for l in res["lignes"] if l["client"] == client]
    return {
        "client": client,
        "client_nom": (lignes[0]["client_nom"] if lignes else client),
        "lignes": lignes,
        "total_ttc": round(sum(flt(l["ttc"]) for l in lignes), 3),
        "devise": (lignes[0]["devise"] if lignes else "TND"),
    }
