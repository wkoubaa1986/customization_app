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

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from customization_app.traitement_commandes import (
    _articles, _bordereaux, _commandes, _taches,
)

DEPUIS_DEFAUT = "2026-07-01"
PAGE_LENGTH = 50

# Statuts qui ne consomment plus de stock : la commande est finie ou morte.
STATUTS_CLOS = ("Completed", "Closed", "Cancelled")

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
    return {"statuts": statuts, "secteurs": secteurs,
            "depuis_defaut": DEPUIS_DEFAUT}


@frappe.whitelist()
def get_commandes(depuis=None, jusqu_a=None, recherche=None, statut=None,
                  origine=None, dispo=None, anomalie=None, tache=None,
                  secteur=None, tri=None, start=0, page_length=PAGE_LENGTH):
    """L'arriéré filtré. Tout est calculé sur l'ENSEMBLE puis découpé en pages :
    un filtre « article manquant » doit porter sur toutes les commandes, pas
    seulement sur celles de la page affichée."""
    _lecture()
    start, page_length = frappe.utils.cint(start), frappe.utils.cint(page_length)

    lignes = _commandes(getdate(depuis or DEPUIS_DEFAUT),
                        getdate(jusqu_a or nowdate()))
    noms = [l.name for l in lignes]
    articles = _articles(noms)
    taches = _taches(noms)
    bordereaux = _bordereaux(noms)

    secteurs = _secteurs(noms)
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
            "telephone": (l.contact_mobile or l.contact_phone
                          or (tel_client.get(l.customer) or "").split("\n")[0].strip()),
            "adresse": (l.shipping_address or l.address_display or "").replace("<br>", ", "),
            "secteur": secteurs.get(l.name, ("", ""))[0],
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

    out = _trier(_filtrer(out, recherche, statut, origine, dispo, anomalie,
                          tache, secteur), tri)
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
             secteur=None):
    def garde(c):
        if statut and c["statut"] != statut:
            return False
        # « — sans secteur » : les adresses jamais sectorisées, qu'on veut
        # pouvoir isoler pour les corriger.
        if secteur == "__vide__" and c["secteur"]:
            return False
        if secteur and secteur != "__vide__" and c["secteur"] != secteur:
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
        if recherche:
            aiguille = recherche.lower().strip()
            foin = " ".join([c["name"], c["client_nom"], c["telephone"],
                             c["adresse"], c["anomalie"]]
                            + [a["code"] + " " + a["article"] for a in c["articles"]])
            if aiguille not in foin.lower():
                return False
        return True

    return [c for c in lignes if garde(c)]


# ------------------------------------------------------------------ actions


@frappe.whitelist()
def chercher_articles(recherche=None, en_stock=1):
    """Le sélecteur d'articles de remplacement — on ne propose que ce qu'on a.

    Choix manuel assumé (décision 30/08) : pas de suggestion automatique, c'est
    le magasin qui sait ce qui remplace quoi.
    """
    _lecture()
    conditions = ["i.disabled = 0"]
    params = {}
    if recherche:
        conditions.append("(i.name LIKE %(r)s OR i.item_name LIKE %(r)s)")
        params["r"] = "%" + recherche.strip() + "%"
    if frappe.utils.cint(en_stock):
        conditions.append("b.qte > 0")
    return frappe.db.sql(
        """SELECT i.name AS code, i.item_name AS article, i.stock_uom AS unite,
                  IFNULL(b.qte, 0) AS stock
           FROM `tabItem` i
           LEFT JOIN (SELECT item_code, SUM(actual_qty) qte FROM `tabBin`
                      GROUP BY item_code) b ON b.item_code = i.name
           WHERE {conditions}
           ORDER BY b.qte DESC, i.name LIMIT 50""".format(
            conditions=" AND ".join(conditions)), params, as_dict=True)


@frappe.whitelist()
def annuler(noms, motif=None):
    """Annule les commandes sélectionnées — la cascade maison (BL, échéancier,
    tâches) se déclenche par les hooks d'`annulation_commande`.

    Un BROUILLON ne s'annule pas dans ERPNext (docstatus 0) : il est laissé tel
    quel et signalé, plutôt que supprimé dans le dos de l'utilisateur.
    """
    frappe.has_permission("Sales Order", "cancel", throw=True)
    noms = frappe.parse_json(noms) if isinstance(noms, str) else (noms or [])
    resultat = []
    for nom in [n for n in noms if n]:
        doc = frappe.get_doc("Sales Order", nom)
        if doc.docstatus == 0:
            resultat.append({"commande": nom, "etat": "brouillon — non annulé "
                             "(un brouillon se supprime, il ne s'annule pas)"})
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
