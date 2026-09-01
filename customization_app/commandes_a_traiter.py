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
from frappe.utils import flt, getdate, nowdate

from customization_app.suivi_appels import CHAMPS as CHAMPS_APPELS, LIBELLES as LIBELLES_APPELS
from customization_app.traitement_commandes import (
    _articles, _bordereaux, _commandes, _taches,
)

DEPUIS_DEFAUT = "2026-07-01"
PAGE_LENGTH = 50

# Statuts qui ne consomment plus de stock : la commande est finie ou morte.
STATUTS_CLOS = ("Completed", "Closed", "Cancelled")

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
    """{commande: {article: reste à livrer}} — une commande soldée ne manque de rien."""
    out = {}
    if not noms:
        return out
    for l in frappe.get_all(
            "Sales Order Item", filters={"parent": ["in", noms],
                                         "parenttype": "Sales Order"},
            fields=["parent", "item_code", "qty", "delivered_qty"],
            limit_page_length=0):
        reste = flt(l.qty) - flt(l.delivered_qty)
        out.setdefault(l.parent, {})[l.item_code] = max(reste, 0)
    return out


# ------------------------------------------------------------------ lecture


def _enrichir_articles(commande, lignes, contexte):
    """Chaque ligne d'article reçoit son stock et son verdict.

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
        reste = reste_cde.get(code, a["qte"])
        qte_stock = stock.get(code, 0)
        # L'engagement inclut CETTE commande : on le retire pour ne pas se
        # concurrencer soi-même.
        dispo = qte_stock - max(engage.get(code, 0) - reste, 0)
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
    return {"statuts": statuts, "secteurs": secteurs, "groupes": groupes,
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
                  secteur=None, livraison=None, prestation=None, client=None,
                  groupes=None, envoi=None, tri=None, start=0,
                  page_length=PAGE_LENGTH):
    """L'arriéré filtré. Tout est calculé sur l'ENSEMBLE puis découpé en pages :
    un filtre « article manquant » doit porter sur toutes les commandes, pas
    seulement sur celles de la page affichée."""
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
            l.name, articles.get(l.name, []), contexte)
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
            "aramex_sb": bool(l.get("custom_aramex_sans_bordereau")),
            "bordereau": (bordereaux.get(l.name) or {}).get("bordereau")
                         or l.custom_bordereau_aramex or "",
            "web": bool(l.woocommerce_id),
        })

    # Combien de commandes ce client a-t-il sur la période ? Compté sur
    # l'ENSEMBLE, avant filtrage : le badge doit dire la même chose que je
    # regarde les ruptures, un secteur, ou tout.
    par_client = {}
    for c in out:
        par_client[c["client"]] = par_client.get(c["client"], 0) + 1
    for c in out:
        c["commandes_client"] = par_client.get(c["client"], 1)

    out = _trier(_filtrer(out, recherche, statut, origine, dispo, anomalie,
                          tache, secteur, livraison, prestation, client,
                          groupes, envoi), tri)
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
        },
    }


def _trier(lignes, tri):
    """Tri stable à plusieurs critères : on trie par le critère le MOINS
    important d'abord, le tri suivant préserve l'ordre obtenu (Python garantit
    la stabilité). C'est ce qui permet « date puis valeur » en deux passes."""
    for champ, decroissant in reversed(TRIS.get(tri or TRI_DEFAUT,
                                                TRIS[TRI_DEFAUT])):
        lignes.sort(key=lambda c: c[champ], reverse=decroissant)
    return lignes


def _filtrer(lignes, recherche, statut, origine, dispo, anomalie, tache,
             secteur=None, livraison=None, prestation=None, client=None,
             groupes=None, envoi=None):
    def garde(c):
        if statut and c["statut"] != statut:
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
                             c["adresse"], c["anomalie"]]
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


@frappe.whitelist()
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


def annuler(noms, motif=None):
    """Annule les commandes sélectionnées — la cascade maison (BL, échéancier,
    tâches) se déclenche par les hooks d'`annulation_commande`.

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

    annulations = annuler(noms, motif)
    faites = [a["commande"] for a in annulations if a["etat"] == "annulée"]
    envoi = None
    if faites:
        envoi = sms_commandes.envoyer(faites, modele, sujet=sujet,
                                      sms=sms, email=email,
                                      remplacements=remplacements)
    return {"annulations": annulations, "informes": faites, "envoi": envoi}


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
    res = get_commandes(depuis=depuis, jusqu_a=jusqu_a, page_length=0)
    lignes = [l for l in res["lignes"] if l["client"] == client]
    return {
        "client": client,
        "client_nom": (lignes[0]["client_nom"] if lignes else client),
        "lignes": lignes,
        "total_ttc": round(sum(flt(l["ttc"]) for l in lignes), 3),
        "devise": (lignes[0]["devise"] if lignes else "TND"),
    }
