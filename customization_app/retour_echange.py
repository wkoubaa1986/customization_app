"""
Échange d'article : la pièce reprise rentre en stock par un BL retour créé
automatiquement à la validation du BL d'échange.

Le mécanisme d'avant (inchangé pour l'historique)
--------------------------------------------------
Un échange (robinet trèfle → luxe inox, citerne 3,2 G → 4 G…) se fait par un
article « E-… » du groupe Echange : un Product Bundle qui porte la pièce
reprise à −1 et la pièce remise à +1, au prix de la différence. ERPNext ignore
la ligne négative d'un bundle (aucun packed item, aucun mouvement), et la
pièce reprise est le plus souvent à l'intérieur d'un article de stock (le
kit AP-M / AP-C acheté monté, sans nomenclature) : elle n'a jamais été une
ligne du BL d'origine. Jusqu'ici deux Server Scripts faisaient le travail :
« Validation Stock negative » (Before Insert) remplaçait la ligne « E-… » par
la pièce remise et créait une réconciliation de stock pour la pièce reprise,
« Valider Le bon livraison avec valeur negatif » (After Submit) la validait
— en basculant « Commande client requise » pour tout le site entre-temps.
Une réconciliation écrase une quantité au lieu de tracer un mouvement, sans
client ni BL d'origine (69 pièces, 107 lignes, ~1 300 DT sur « Ajustement du
Stock »). Ces deux scripts sont éteints par le patch
`desactiver_server_scripts_echange` ; les BL et réconciliations passés
restent tels quels (« cancel bon de livraison » les annule toujours ensemble).

Le mécanisme d'aujourd'hui
--------------------------
1. Le BL d'échange est un BL normal depuis la commande, avec la ligne
   « E-… » rattachée à sa ligne de commande : elle porte le prix de la
   différence et fait passer la commande à livrée. Ce qui SORT physiquement
   est écrit en clair (demande utilisateur 30/09 : « c'est comme ça qu'on
   assure que le stock est juste ») : chaque pièce remise (ligne positive du
   bundle) devient une LIGNE du BL à 0 DT, marquée `custom_echange_composant`
   = l'article « E-… », SANS commande (ERPNext exige commande et ligne de
   commande ensemble, et la pièce n'est pas une ligne de la commande) — c'est
   elle qui mouvemente le stock. Les packed
   items du bundle d'échange sont retirés (sinon la pièce sortirait deux
   fois). Ces lignes sont posées avant la validation ERPNext
   (`poser_composants`) et remises à 0 DT après (`retirer_pieces_reprises`),
   ERPNext ayant repris le tarif client entre-temps. Elles suivent le BL sur
   la facture (même champ, même remise à 0).
2. À la validation de ce BL (`on_submit`), les lignes négatives des bundles
   qu'il porte donnent les pièces reprises ; un BL retour (`is_return`) est
   créé ET validé dans la même transaction : même client, même date, même
   entrepôt et même livreur, contre le BL d'origine du kit quand on le
   retrouve, pièces à −1 et prix 0 (la différence de prix est déjà sur la
   ligne « E-… »). Stock : +1 au coût moyen, imputé au coût des ventes.
   Si le retour échoue, le BL d'échange n'est pas validé non plus.
3. Les deux BL se connaissent (`custom_retour_echange` sur l'échange, Link ;
   `custom_echange_de` sur le retour, Data — pas un Link, pour que
   l'annulation de l'échange ne soit pas bloquée par le contrôle de liens).
   Annuler l'échange annule ET supprime son retour (il n'a de sens qu'avec
   l'échange ; un retour « Annulé » qui traîne n'apporte rien) ; le retour
   ne s'annule pas seul.

Pièges connus, et comment ils sont contournés
----------------------------------------------
- « Commande client requise » (Selling Settings) refuse toute ligne de BL
  sans commande, et une pièce absente de la commande ne peut pas s'y
  rattacher (ERPNext exige le même article) : `DeliveryNoteEchange` lève
  ce contrôle pour les seuls retours d'échange.
- À l'insertion, le tarif client est repris sur la ligne (33 DT TTC pour un
  robinet) : le prix est remis à 0 après `insert`, avant validation.
- « L'article n'existe pas dans le BL d'origine » : simple message jusqu'à
  ERPNext 15.122, ERREUR bloquante pour un BL depuis 15.123 (#59820). Le BL
  d'origine est donc choisi parmi ceux qui ont livré la pièce reprise
  (`_candidats_origine`) ; sans aucun, le retour se fait sans référence. Le
  message résiduel (pièce livrée dans un kit) reste retiré de l'affichage.
- Le retour est daté comme le BL d'échange ; le BL d'origine doit être
  antérieur ou du même instant, sinon on se passe de référence. Sans
  référence, ERPNext ne sait pas ANNULER le retour (il va chercher le
  « Delivery Note None » pour y recalculer le % retourné) : `_sans_origine`
  retire cette mise à jour du retour avant de l'annuler — rien à mettre à
  jour quand il n'y a pas d'origine.
- La ligne négative du bundle devient un packed item à −1 sur le BL (et sur
  la facture), et ERPNext refuse alors la validation (« quantity must be
  positive ») : `retirer_pieces_reprises` (hook validate du BL et de la
  facture) enlève TOUS les packed items des bundles d'échange — la pièce
  reprise ne mouvemente jamais le stock ici, la pièce remise sort par sa
  propre ligne. Seule la ligne « E-… » garde l'information de l'échange.
  Si malgré tout une entrée de stock de la pièce reprise existait sur le BL
  d'échange, le retour la compterait deux fois : on refuse dans ce cas.
- « Commande client requise » (Selling Settings) refuserait les lignes
  composants, sans commande : `DeliveryNoteEchange` et `SalesInvoiceEchange`
  les exemptent, sur le BL comme sur la facture (où la marque est reprise de
  la ligne de BL avant la validation, la facturation mensuelle ne la
  recopiant pas).
- ERPNext regénère les packed items et reprend les tarifs à CHAQUE
  validation : les deux hooks sont idempotents (les lignes composants sont
  reconnues à leur marque, ajustées ou retirées si la ligne « E-… » change).
- Le retour vaut 0 DT et n'a pas de commande sur ses lignes : la
  facturation mensuelle, la règle « livré au montant » et les alertes de
  commande ne le voient pas.

Les fonctions de décision (`pieces_reprises`, `bundles_echange`,
`packed_items_a_garder`, `composants_attendus`, `plan_composants`,
`choisir_bl_origine`) sont PURES : aucune base, testables telles quelles.
"""

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, nowtime

from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
from erpnext.stock.doctype.delivery_note.delivery_note import DeliveryNote

CHAMP_RETOUR = "custom_retour_echange"   # sur le BL d'échange : son BL retour
CHAMP_ECHANGE = "custom_echange_de"      # sur le BL retour : le BL d'échange
CHAMP_COMPOSANT = "custom_echange_composant"  # sur une ligne : l'article « E-… » dont elle sort la pièce

TOLERANCE = 0.001


# ── Décision (pur) ───────────────────────────────────────────────────────────


def pieces_reprises(lignes, bundles):
    """Les pièces que le client rend, d'après les lignes négatives des bundles.

    `lignes`  : [{"item_code", "qty", "warehouse"}] — les lignes du BL.
    `bundles` : {article: [{"item_code", "qty"}]} — la composition des
                Product Bundle de ces articles (absent = pas un bundle).

    Retour : [{"item_code", "qty" (positive), "warehouse"}], une entrée par
    (pièce, entrepôt), quantité = Σ |qty bundle| × qty ligne. Les lignes
    positives des bundles (pièces remises) ne concernent pas le retour.
    """
    cumul = {}
    for ligne in lignes:
        composition = bundles.get(ligne.get("item_code")) or []
        qte_ligne = flt(ligne.get("qty"))
        if qte_ligne <= 0:
            continue
        for composant in composition:
            q = flt(composant.get("qty"))
            if q >= 0:
                continue
            cle = (composant.get("item_code"), ligne.get("warehouse"))
            cumul[cle] = cumul.get(cle, 0.0) + (-q) * qte_ligne
    return [{"item_code": code, "qty": round(q, 3), "warehouse": wh}
            for (code, wh), q in cumul.items() if q > TOLERANCE]


def bundles_echange(bundles):
    """Les articles dont le bundle est un ÉCHANGE : au moins une ligne négative
    (la pièce reprise). Un kit ordinaire n'a que du positif."""
    return {code for code, composition in bundles.items()
            if any(flt(c.get("qty")) < 0 for c in composition)}


def packed_items_a_garder(packed_items, echanges=None):
    """Les packed items qui restent sur le document.

    Ceux d'un bundle d'échange (`parent_item` dans `echanges`) partent tous :
    la pièce reprise (négative) rentre par le BL retour, la pièce remise
    (positive) sort par sa propre ligne à 0 DT. À défaut d'`echanges` connus,
    on retire au moins les quantités négatives (ERPNext les refuse)."""
    echanges = echanges or set()
    return [p for p in packed_items
            if p.get("parent_item") not in echanges and flt(p.get("qty")) > 0]


def composants_attendus(lignes, bundles):
    """Les lignes composants qu'un BL d'échange doit porter : une par
    (article « E-… », pièce remise, entrepôt), quantité = qty bundle × qty
    ligne. Sans commande : ERPNext exige commande et ligne de commande
    ensemble, et la pièce remise n'est pas une ligne de la commande.

    `lignes`  : [{"item_code", "qty", "warehouse"}] — les lignes du BL qui ne
                sont PAS elles-mêmes des composants.
    Retour : [{"item_code", "qty", "warehouse", "echange"}].
    """
    cumul = {}
    for ligne in lignes:
        code = ligne.get("item_code")
        composition = bundles.get(code) or []
        if not any(flt(c.get("qty")) < 0 for c in composition):
            continue
        qte_ligne = flt(ligne.get("qty"))
        if qte_ligne <= 0:
            continue
        for composant in composition:
            q = flt(composant.get("qty"))
            if q <= 0:
                continue
            cle = (code, composant.get("item_code"), ligne.get("warehouse"))
            cumul[cle] = cumul.get(cle, 0.0) + q * qte_ligne
    return [{"echange": e, "item_code": piece, "warehouse": wh, "qty": round(q, 3)}
            for (e, piece, wh), q in cumul.items() if q > TOLERANCE]


def plan_composants(existantes, attendues):
    """Ce qu'il faut faire des lignes composants déjà sur le document pour
    retomber sur `attendues` : {"ajouter": [...], "ajuster": [(nom, qty)],
    "retirer": [noms]}.

    `existantes` : [{"nom", "item_code", "qty", "warehouse", "echange"}] —
    les lignes marquées.
    Idempotent : rien à faire quand tout concorde déjà.
    """
    def cle(l):
        return (l.get("echange"), l.get("item_code"), l.get("warehouse"))

    restantes = {cle(a): a for a in attendues}
    plan = {"ajouter": [], "ajuster": [], "retirer": []}
    for e in existantes:
        attendue = restantes.pop(cle(e), None)
        if attendue is None:
            plan["retirer"].append(e.get("nom"))
        elif abs(flt(attendue["qty"]) - flt(e.get("qty"))) > TOLERANCE:
            plan["ajuster"].append((e.get("nom"), attendue["qty"]))
    plan["ajouter"] = list(restantes.values())
    return plan


def choisir_bl_origine(candidats, instant_retour):
    """Le BL contre lequel faire le retour, ou None.

    `candidats` : [{"name", "instant" (datetime), "meme_commande" (bool)}] —
    des BL validés, non retours, du même client, autres que le BL d'échange.
    On prend d'abord ceux qui ont livré la même commande, puis le plus
    récent ; jamais un BL postérieur au retour (ERPNext le refuse).
    """
    valables = [c for c in candidats if c.get("instant") and c["instant"] <= instant_retour]
    if not valables:
        return None
    return max(valables, key=lambda c: (bool(c.get("meme_commande")), c["instant"]))["name"]


# ── Lecture ──────────────────────────────────────────────────────────────────


def _bundles(codes):
    if not codes:
        return {}
    lignes = frappe.db.sql(
        """SELECT pb.new_item_code AS article, pbi.item_code, pbi.qty
           FROM `tabProduct Bundle Item` pbi
           JOIN `tabProduct Bundle` pb ON pb.name = pbi.parent
           WHERE pb.disabled = 0 AND pb.new_item_code IN %(codes)s
           ORDER BY pbi.idx""",
        {"codes": tuple(codes)}, as_dict=True)
    out = {}
    for l in lignes:
        out.setdefault(l.article, []).append({"item_code": l.item_code, "qty": l.qty})
    return out


def _candidats_origine(doc, codes):
    """Les BL du client qui ont LIVRÉ les pièces reprises `codes` (en ligne ou dans un kit).

    Depuis ERPNext 15.123 (#59820, déployé le 20/10/2026), un retour dont la pièce n'est pas
    sur le BL d'origine est REFUSÉ, plus seulement signalé : prendre « le BL le plus récent du
    client » faisait échouer l'échange dès que sa dernière livraison était autre chose (cas réel
    MAT-DN-2026-05813 : robinet chromé livré en juillet, osmoseur livré la veille). On ne retient
    donc que les BL où chaque pièce reprise a été livrée et n'est pas déjà entièrement retournée.
    """
    if not codes:
        return []
    commandes = tuple({r.against_sales_order for r in doc.items if r.against_sales_order}) or ("",)
    candidats = frappe.db.sql(
        """SELECT dn.name, TIMESTAMP(dn.posting_date, dn.posting_time) AS instant,
                  EXISTS (SELECT 1 FROM `tabDelivery Note Item` dni
                          WHERE dni.parent = dn.name
                            AND dni.against_sales_order IN %(commandes)s) AS meme_commande
           FROM `tabDelivery Note` dn
           WHERE dn.docstatus = 1 AND dn.is_return = 0 AND dn.customer = %(client)s
             AND dn.name <> %(moi)s
           ORDER BY dn.posting_date DESC, dn.posting_time DESC
           LIMIT 100""",
        {"commandes": commandes, "client": doc.customer, "moi": doc.name}, as_dict=True)
    if not candidats:
        return []
    livrees = _pieces_livrees([c["name"] for c in candidats], codes)
    return candidats_ayant_livre(candidats, livrees, codes)


def candidats_ayant_livre(candidats, livrees, codes):
    """Ne garde que les candidats dont `livrees[name]` couvre TOUTES les pièces `codes`."""
    voulues = set(codes)
    return [c for c in candidats if voulues <= (livrees.get(c["name"]) or set())]


def _pieces_livrees(noms, codes):
    """{BL: {pièces encore retournables}} parmi `codes`.

    Livré = lignes du BL + articles d'ensemble (packed items) ; déjà repris = retours validés
    contre ce BL. On ne se fie pas à `returned_qty` / `per_returned` du BL d'origine : ERPNext
    ne les tient à jour qu'à certaines conditions (en pratique jamais ici, constaté sur les
    retours saisis à la main), alors que son contrôle « déjà retourné » cumule bien les retours.
    """
    livrees = frappe.db.sql(
        """SELECT parent, item_code, SUM(qty) AS qty FROM `tabDelivery Note Item`
           WHERE parent IN %(noms)s AND item_code IN %(codes)s GROUP BY parent, item_code
           UNION ALL
           SELECT parent, item_code, SUM(qty) FROM `tabPacked Item`
           WHERE parent IN %(noms)s AND item_code IN %(codes)s GROUP BY parent, item_code""",
        {"noms": tuple(noms), "codes": tuple(codes)}, as_dict=True)
    retournees = frappe.db.sql(
        """SELECT r.return_against AS parent, ri.item_code, SUM(-ri.qty) AS qty
           FROM `tabDelivery Note` r JOIN `tabDelivery Note Item` ri ON ri.parent = r.name
           WHERE r.is_return = 1 AND r.docstatus = 1 AND r.return_against IN %(noms)s
             AND ri.item_code IN %(codes)s
           GROUP BY r.return_against, ri.item_code""",
        {"noms": tuple(noms), "codes": tuple(codes)}, as_dict=True)
    return pieces_encore_retournables(livrees, retournees)


def pieces_encore_retournables(livrees, retournees):
    """{BL: {pièces}} dont la quantité livrée dépasse la quantité déjà reprise.
    `livrees` / `retournees` : [{"parent", "item_code", "qty"}]."""
    solde = {}
    for l in livrees:
        cle = (l["parent"], l["item_code"])
        solde[cle] = solde.get(cle, 0.0) + flt(l["qty"])
    for r in retournees:
        cle = (r["parent"], r["item_code"])
        if cle in solde:
            solde[cle] -= flt(r["qty"])
    out = {}
    for (bl, code), reste in solde.items():
        if reste > TOLERANCE:
            out.setdefault(bl, set()).add(code)
    return out


def _entrees_deja_faites(nom_bl, codes):
    """Entrées de stock de ces pièces sur le BL d'échange lui-même : il n'y en a
    jamais (ERPNext ignore la ligne négative d'un bundle) ; s'il y en avait, le
    retour compterait la pièce deux fois."""
    return frappe.db.sql(
        """SELECT DISTINCT item_code FROM `tabStock Ledger Entry`
           WHERE voucher_no = %s AND is_cancelled = 0 AND actual_qty > 0
             AND item_code IN %s""",
        (nom_bl, tuple(codes)), pluck=True)


def _lignes_origine(origine, codes):
    """{pièce: nom de sa première ligne sur le BL d'origine}. Pièce livrée dans un kit
    (packed item) : pas de ligne, pas de lien."""
    out = {}
    for l in frappe.db.sql(
        """SELECT name, item_code FROM `tabDelivery Note Item`
           WHERE parent = %(origine)s AND item_code IN %(codes)s ORDER BY idx""",
        {"origine": origine, "codes": tuple(codes)}, as_dict=True):
        out.setdefault(l.item_code, l.name)
    return out


# ── Le BL retour ─────────────────────────────────────────────────────────────


def _retirer_avertissement_article_absent():
    """ERPNext signale (sans bloquer) qu'une pièce n'était pas sur le BL
    d'origine : normal ici, la pièce était dans le kit."""
    log = frappe.local.message_log
    if not log:
        return
    frappe.local.message_log = [
        m for m in log
        if "does not exist in Delivery Note" not in str(m.get("message") if isinstance(m, dict) else m)
    ]


def creer_retour(doc):
    """Crée et valide le BL retour des pièces reprises du BL d'échange `doc`.
    Retourne son nom, ou None si le BL ne porte aucun échange."""
    lignes = [{"item_code": r.item_code, "qty": r.qty, "warehouse": r.warehouse or doc.set_warehouse}
              for r in doc.items]
    pieces = pieces_reprises(lignes, _bundles([l["item_code"] for l in lignes]))
    if not pieces:
        return None
    # Le magasinier peut choisir où la pièce reprise rentre (popup d'échange de la page Stock,
    # cf. sorties_magasin.approuver) : un robinet usagé va aux Articles défectueux, pas au Magasin.
    if doc.flags.get("entrepot_reprise"):
        for p in pieces:
            p["warehouse"] = doc.flags.entrepot_reprise

    codes = [p["item_code"] for p in pieces]
    doublons = _entrees_deja_faites(doc.name, codes)
    if doublons:
        frappe.throw(_("Le BL {0} a déjà fait entrer {1} en stock : le retour d'échange "
                       "compterait la pièce deux fois.").format(doc.name, ", ".join(doublons)))

    instant = get_datetime(f"{doc.posting_date} {doc.posting_time or nowtime()}")
    origine = choisir_bl_origine(_candidats_origine(doc, codes), instant)

    ret = frappe.new_doc("Delivery Note")
    ret.update({
        "customer": doc.customer,
        "company": doc.company,
        "posting_date": doc.posting_date,
        "posting_time": doc.posting_time,
        "set_posting_time": 1,
        "is_return": 1,
        "return_against": origine,
        "set_warehouse": doc.set_warehouse,
        "selling_price_list": doc.selling_price_list,
        "currency": doc.currency,
        "custom_livré_par": doc.get("custom_livré_par"),
        "custom_véhicle": doc.get("custom_véhicle"),
        "custom_commande": doc.get("custom_commande"),
        CHAMP_ECHANGE: doc.name,
        "remarks": _("Reprise de pièce(s) de l'échange {0}").format(doc.name),
    })
    lignes_origine = _lignes_origine(origine, codes) if origine else {}
    for p in pieces:
        ret.append("items", {
            "item_code": p["item_code"], "qty": -p["qty"], "warehouse": p["warehouse"],
            "rate": 0, "price_list_rate": 0, "discount_percentage": 0,
            # La ligne d'origine : ERPNext contrôle alors la quantité retournée LIGNE par ligne
            # (clé item_code + dn_detail) et le retour garde la trace de ce qu'il reprend.
            "dn_detail": lignes_origine.get(p["item_code"]),
        })
    ret.flags.ignore_permissions = True
    ret.flags.retour_echange = True
    ret.insert()
    # set_missing_values a repris le tarif client : la pièce reprise vaut 0,
    # la différence de prix est sur la ligne « E-… » du BL d'échange.
    for r in ret.items:
        r.price_list_rate = 0
        r.discount_percentage = 0
        r.discount_amount = 0
        r.rate = 0
    ret.save()
    ret.submit()
    _retirer_avertissement_article_absent()
    return ret.name


# ── Hooks ────────────────────────────────────────────────────────────────────


def _lignes_composants(doc):
    return [r for r in doc.items if r.get(CHAMP_COMPOSANT)]


def _marquer_depuis_bl(doc):
    """Facture : une ligne reprise d'un BL (dn_detail) hérite de la marque de
    sa ligne de BL — la facturation mensuelle ne recopie pas le champ."""
    sans_marque = {r.dn_detail: r for r in doc.items
                   if r.get("dn_detail") and not r.get(CHAMP_COMPOSANT)}
    if not sans_marque:
        return
    marques = frappe.db.sql(
        f"""SELECT name, `{CHAMP_COMPOSANT}` AS echange FROM `tabDelivery Note Item`
            WHERE name IN %(noms)s AND COALESCE(`{CHAMP_COMPOSANT}`, '') <> ''""",
        {"noms": tuple(sans_marque)}, as_dict=True)
    for m in marques:
        sans_marque[m.name].set(CHAMP_COMPOSANT, m.echange)


def poser_composants(doc, method=None):
    """before_validate du BL : les pièces remises des bundles d'échange sont
    (ré)écrites en lignes du BL. ERPNext complète ensuite ces lignes (libellé,
    unité, compte) comme n'importe quelle ligne saisie."""
    if doc.get("is_return"):
        return
    lignes = [{"item_code": r.item_code, "qty": r.qty,
               "warehouse": r.warehouse or doc.get("set_warehouse")}
              for r in doc.items if not r.get(CHAMP_COMPOSANT)]
    bundles = _bundles([l["item_code"] for l in lignes])
    attendues = composants_attendus(lignes, bundles) if bundles else []
    existantes = [{"nom": r.name, "item_code": r.item_code, "qty": r.qty,
                   "warehouse": r.warehouse, "echange": r.get(CHAMP_COMPOSANT)}
                  for r in _lignes_composants(doc)]
    plan = plan_composants(existantes, attendues)
    if not (plan["ajouter"] or plan["ajuster"] or plan["retirer"]):
        return

    a_retirer = set(plan["retirer"])
    a_ajuster = dict(plan["ajuster"])
    doc.items = [r for r in doc.items if r.name not in a_retirer]
    for r in doc.items:
        if r.name in a_ajuster:
            r.qty = a_ajuster[r.name]
    for a in plan["ajouter"]:
        stock_uom = frappe.db.get_value("Item", a["item_code"], "stock_uom")
        doc.append("items", {
            "item_code": a["item_code"], "qty": a["qty"], "warehouse": a["warehouse"],
            "uom": stock_uom, "stock_uom": stock_uom, "conversion_factor": 1,
            "rate": 0, "price_list_rate": 0, "discount_percentage": 0,
            CHAMP_COMPOSANT: a["echange"],
        })
    for position, r in enumerate(doc.items, start=1):
        r.idx = position


def marquer_composants_facture(doc, method=None):
    """before_validate de la facture : la marque des lignes composants est
    reprise du BL AVANT le contrôle « Commande client requise »."""
    if not doc.get("is_return"):
        _marquer_depuis_bl(doc)


def retirer_pieces_reprises(doc, method=None):
    """validate du BL et de la facture, APRÈS celui d'ERPNext :
      - les packed items des bundles d'échange sont retirés (la pièce reprise
        rentre par le BL retour, la pièce remise sort par sa ligne) ;
      - les lignes composants sont remises à 0 DT — ERPNext vient de leur
        reprendre le tarif client — et les totaux recalculés.
    Un retour n'a ni l'un ni l'autre."""
    if doc.get("is_return"):
        return
    if doc.doctype == "Sales Invoice":
        _marquer_depuis_bl(doc)
    change = False

    if doc.get("packed_items"):
        echanges = bundles_echange(_bundles({p.parent_item for p in doc.packed_items if p.parent_item}))
        garder = packed_items_a_garder(
            [{"qty": p.qty, "parent_item": p.parent_item, "_row": p} for p in doc.packed_items],
            echanges)
        if len(garder) != len(doc.packed_items):
            doc.packed_items = [g["_row"] for g in garder]
            for position, p in enumerate(doc.packed_items, start=1):
                p.idx = position

    for r in _lignes_composants(doc):
        if flt(r.rate) or flt(r.price_list_rate) or flt(r.amount) \
                or flt(r.discount_percentage) or flt(r.discount_amount):
            r.price_list_rate = 0
            r.discount_percentage = 0
            r.discount_amount = 0
            r.rate = 0
            r.amount = 0
            change = True
        if not r.get("description"):
            r.description = _("Pièce remise dans l'échange {0}").format(r.get(CHAMP_COMPOSANT))
    if change:
        doc.calculate_taxes_and_totals()



def on_submit_bl(doc, method=None):
    """Validation d'un BL : s'il porte un échange, sa reprise rentre en stock."""
    if doc.is_return:
        return
    deja = doc.get(CHAMP_RETOUR)
    if deja and frappe.db.get_value("Delivery Note", deja, "docstatus") == 1:
        return
    nom = creer_retour(doc)
    if nom:
        doc.db_set(CHAMP_RETOUR, nom, update_modified=False)
        frappe.msgprint(_("Pièce(s) reprise(s) en stock par le BL retour {0}.").format(nom),
                        indicator="green", alert=True)


def _sans_origine(ret):
    """Un retour sans BL d'origine : la mise à jour du % retourné de l'origine
    (status_updater à `percent_join_field_parent = return_against`) n'a
    aucune cible et fait planter ERPNext — on la retire de ce document."""
    if not ret.get("return_against"):
        ret.status_updater = [e for e in (ret.status_updater or [])
                              if not e.get("percent_join_field_parent")]
    return ret


def before_cancel_bl(doc, method=None):
    """Annuler l'échange annule puis supprime sa reprise ; la reprise ne
    s'annule pas seule."""
    if doc.is_return:
        echange = doc.get(CHAMP_ECHANGE)
        if echange and not doc.flags.annulation_echange \
                and frappe.db.get_value("Delivery Note", echange, "docstatus") == 1:
            frappe.throw(_("Ce BL retour est la reprise automatique de l'échange {0} : "
                           "annulez ce BL d'échange, la reprise suivra.").format(echange))
        return
    retour = doc.get(CHAMP_RETOUR)
    if not retour or not frappe.db.exists("Delivery Note", retour):
        return
    ret = _sans_origine(frappe.get_doc("Delivery Note", retour))
    ret.flags.ignore_permissions = True
    ret.flags.ignore_links = True
    ret.flags.annulation_echange = True
    if ret.docstatus == 1:
        ret.cancel()
    # Le retour n'existe que par l'échange : une fois annulé, il disparaît.
    # Le lien Link du BL d'échange est vidé (il pointerait sur un fantôme) et
    # la trace reste dans le fil de commentaires.
    doc.db_set(CHAMP_RETOUR, None, update_modified=False)
    doc.set(CHAMP_RETOUR, None)
    frappe.delete_doc("Delivery Note", retour, ignore_permissions=True, force=True)
    doc.add_comment("Info", _("BL retour de reprise {0} annulé et supprimé avec l'échange.").format(retour))


class SalesInvoiceEchange(SalesInvoice):
    """« Commande client requise » ne s'applique pas aux lignes composants d'un
    échange sur la facture non plus (même contrôle qu'ERPNext, ces lignes en
    moins)."""

    def so_dn_required(self):
        if self.is_return or not _lignes_composants(self):
            return super().so_dn_required()
        prev_doc_field_map = {
            "Sales Order": ["so_required", "is_pos"],
            "Delivery Note": ["dn_required", "update_stock"],
        }
        for key, value in prev_doc_field_map.items():
            if frappe.db.get_single_value("Selling Settings", value[0]) == "Yes":
                if frappe.get_value("Customer", self.customer, value[0]):
                    continue
                for d in self.get("items"):
                    if d.get(CHAMP_COMPOSANT) or not d.item_code or self.get(value[1]):
                        continue
                    if not d.get(key.lower().replace(" ", "_")):
                        frappe.throw(_("{0} is mandatory for Item {1}").format(key, d.item_code))


class DeliveryNoteEchange(DeliveryNote):
    """« Commande client requise » ne s'applique ni aux retours d'échange (la
    pièce reprise n'est pas une ligne de commande, elle était dans le kit) ni
    aux lignes composants d'un échange (la pièce remise non plus : elles
    reprennent la commande de la ligne « E-… » quand il y en a une)."""

    def so_required(self):
        if self.is_return and (self.get(CHAMP_ECHANGE) or self.flags.retour_echange):
            return
        if frappe.db.get_single_value("Selling Settings", "so_required") == "Yes":
            for d in self.get("items"):
                if not d.against_sales_order and not d.get(CHAMP_COMPOSANT):
                    frappe.throw(_("Sales Order required for Item {0}").format(d.item_code))
