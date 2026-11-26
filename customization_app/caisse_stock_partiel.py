"""Une dépense de caisse dont UNE PARTIE part en stock (décision utilisateur 10/11/2026).

Le technicien achète sur bon de livraison (ou facture) des pièces dont certaines vont en stock et
le reste est consommé ou n'est pas un article. Le fournisseur facture au mois. À la caisse, il
saisit la pièce comme une « Dépense avec facture », coche les articles qui partent en stock, et
paie le tout en une fois. Comptablement (règle utilisateur, 10/11/2026 au soir) :

  · UNE SEULE écriture de paiement, au montant payé : Cr caisse / banque, Dr TVA (TOUTE la TVA),
    Dr compte de charge (TOUT le HT) — la dépense est entière, ses calculs et ceux du dossier
    comptable restent ceux d'une dépense ;
  · la part stock est VALORISÉE HT : une commande d'achat SANS TVA pour les articles cochés, et
    une écriture « le compte de charge finance la commande » : Dr Créditeurs (avance sur la
    commande, au sens ERPNext) / Cr compte de charge, pour le HT des articles. La commande est
    soumise, le reçu entre le stock ; l'avance s'impute sur la facture d'achat finale ;
  · AUCUNE facture d'achat n'est créée ici (décision utilisateur 10/11/2026, confirmée).
    - BL : la fiche d'achat attend la facture mensuelle (« Achats BL » de la caisse, ou
      « Rattacher des BL » sur une facture d'achat), seule ou avec d'autres BL du fournisseur ;
      l'écriture est une AVANCE sur la commande (Dr Créditeurs), elle s'impute sur cette facture,
      dont le numéro se pose sur la dépense.
    - FACTURE : la dépense EST la facture, rien ne reste à saisir : la commande est fermée après
      réception, la fiche passe « Saisie », et l'écriture solde directement « Stock reçus mais non
      facturés » (crédité par le reçu) contre le compte de charge — aucun en-cours fournisseur.
  · sans arrondi : commande et facture gardent les millimes (71,116 et non 71,000), sinon l'avance
    se coupe en deux lignes.

Le contrôle scan/saisie de la facture d'achat (bank_retenue_sync) déduit les parts dépense
(HT hors stock, toute la TVA) des totaux lus. Les fonctions de calcul sont pures.
"""
from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

from customization_app.caisse_bl import cle_numero_bl

PRECISION = 3
#: L'écriture « le compte de charge finance la commande » de la part stock d'un BL : aucune ligne de caisse, donc pas
#: « Dépense caisse — » (que lisent le rapport de caisse, l'argent liquide et l'anti-doublon).
PREFIXE_FINANCEMENT = "Part stock — "


# ----------------------------------------------------------------------------- pur
def lignes_valides(articles) -> list:
    """Les lignes cochées, normalisées : [{item_code, qty, rate, designation}]. Pure."""
    if isinstance(articles, str):
        articles = json.loads(articles) if articles.strip() else []
    out = []
    for a in articles or []:
        code = (a.get("item_code") or "").strip()
        qty, rate = flt(a.get("qty")), flt(a.get("rate"))
        if not code or qty <= 0 or rate < 0:
            continue
        out.append({"item_code": code, "qty": qty, "rate": round(rate, PRECISION),
                    "designation": (a.get("designation") or "").strip(),
                    "bl_cle": (a.get("bl_cle") or a.get("bl") or "").strip(),
                    # La mémoire des articles (correspondance_articles, 25/11/2026) : le code du fournisseur et ce que
                    # la lecture avait proposé — l'enregistrement apprend le choix, et sait s'il corrige la mémoire.
                    "code": str(a.get("code") or "").strip(),
                    "suggestion_item": a.get("suggestion_item") or None,
                    "suggestion_source": a.get("suggestion_source") or "",
                    "suggestion_cle": a.get("suggestion_cle") or None})
    return out


def ventiler_stock(montant, tva, lignes) -> dict:
    """La part stock (HT des articles cochés) et ce que la facture d'achat ne portera pas. Pure.

    La dépense reste ENTIÈRE (tout le HT, toute la TVA) ; `depense_*` est ce qu'il faut déduire du
    scan de la facture finale pour retrouver la facture d'achat (HT du stock, sans TVA).
    """
    stock_ht = round(sum(l["qty"] * l["rate"] for l in lignes), PRECISION)
    ht_total = round(flt(montant) - flt(tva), PRECISION)
    return {"stock_ht": stock_ht, "ht_total": ht_total,
            "depense_ht": round(ht_total - stock_ht, PRECISION),
            "depense_tva": round(flt(tva), PRECISION),
            "depense_ttc": round(flt(montant) - stock_ht, PRECISION)}


def remarque(v: dict, nb_lignes: int) -> str:
    return ("Part mise en stock : %s HT (%d article(s)) — commande d'achat sans TVA, financée par "
            "le compte de charge ; la dépense porte tout le HT (%s) et toute la TVA (%s)"
            % (v["stock_ht"], nb_lignes, v["ht_total"], v["depense_tva"]))


# ----------------------------------------------------------------------------- préparation
def preparer(articles_stock, montant, tva) -> dict | None:
    """Contrôle et ventile. -> dict (ventiler_stock + lignes), ou None si rien n'est coché."""
    lignes = lignes_valides(articles_stock)
    if not lignes:
        return None
    for l in lignes:
        est_stock = frappe.db.get_value("Item", l["item_code"], "is_stock_item")
        if est_stock is None:
            frappe.throw(_("L'article {0} n'existe pas.").format(l["item_code"]))
        if not cint(est_stock):
            frappe.throw(_("L'article {0} n'est pas un article de stock.").format(l["item_code"]))
    v = ventiler_stock(montant, tva, lignes)
    if v["stock_ht"] <= 0:
        return None
    if v["stock_ht"] >= v["ht_total"] - 0.0005:
        frappe.throw(_("Tout le HT part en stock ({0} sur {1}) : saisissez la pièce en "
                       "« Facture d'achat », pas en dépense.").format(v["stock_ht"], v["ht_total"]))
    v["lignes"] = lignes
    return v


def avance_en_journal(fiche_nom) -> bool:
    """Vrai quand l'avance de cette fiche est déjà une écriture d'avance SUR la commande (part
    stock d'une dépense) : les hooks ne doivent pas la transformer en paiement."""
    return bool(fiche_nom and frappe.db.get_value("Facture Achat a Saisir", fiche_nom, "depense_a_payer"))


# ----------------------------------------------------------------------------- création
def compte_stock_recu_non_facture() -> str:
    """Le compte « Stock Reçus Mais Non Facturés » de la société (réglage ERPNext)."""
    from customization_app import caisse_depenses as C
    compte = frappe.db.get_value("Company", C.COMPANY, "stock_received_but_not_billed")
    if not compte:
        frappe.throw(_("Le compte « Stock reçus mais non facturés » n'est pas défini sur la société."))
    return compte


def creer_part_stock(stock: dict, ctx: dict) -> dict:
    """La commande (sans TVA), l'avance financée par le compte de charge, la fiche d'achat ; et la
    facture d'achat si la pièce en est une. Appelé par caisse_depenses.creer, dans sa transaction."""
    from customization_app import achats_magasin, caisse_depenses as C

    supplier = C._supplier(ctx.get("fournisseur"), matricule=ctx.get("matricule"), supplier=ctx.get("supplier"))
    if not supplier:
        frappe.throw(_("Le fournisseur est obligatoire pour mettre des articles en stock."))
    description = (ctx["description"] + " — part stock")[:140]
    est_bl = cint(ctx.get("est_bl"))

    fiche = frappe.get_doc({
        "doctype": "Facture Achat a Saisir",
        "fournisseur": (ctx.get("fournisseur") or "").strip(), "supplier": supplier,
        "montant": stock["stock_ht"],
        "numero_facture": (ctx.get("numero_facture") or "").strip(),
        "date_facture": ctx.get("date_facture") or None,
        "est_bl": est_bl, "numero_bl": (ctx.get("numero_bl") or "").strip(),
        "mode_paiement": "Pas payé", "description": description,
        "saisi_par": frappe.session.user,
        "depense_a_payer": ctx.get("depense"),
        "part_depense_ht": stock["depense_ht"], "part_depense_tva": stock["depense_tva"],
        "part_depense_ttc": stock["depense_ttc"],
    })
    fiche.insert(ignore_permissions=True)
    if ctx.get("photo"):
        C._attacher_scan(ctx["photo"], "facture-%s" % fiche.name, "Facture Achat a Saisir", fiche.name, coins=ctx.get("coins"))

    po = frappe.new_doc("Purchase Order")
    po.supplier = supplier
    po.company = C.COMPANY
    po.transaction_date = nowdate()
    po.schedule_date = nowdate()
    po.custom_fiche_caisse = fiche.name
    po.set_warehouse = achats_magasin.ENTREPOT_ACHATS
    po.disable_rounded_total = 1
    for l in stock["lignes"]:
        # SANS TVA : la part stock est valorisée HT, toute la TVA est dans la dépense. Un modèle de
        # taxe d'article ou de fournisseur ajouterait une ligne de TVA tout seul : on le neutralise.
        po.append("items", {"item_code": l["item_code"], "qty": l["qty"], "rate": l["rate"],
                            "schedule_date": nowdate(), "warehouse": achats_magasin.ENTREPOT_ACHATS,
                            "item_tax_template": "", "item_tax_rate": "{}"})
    _sans_taxes(po)
    po.flags.ignore_permissions = True
    po.insert()
    if flt(po.grand_total) != flt(po.net_total):
        _sans_taxes(po)
        po.save()
    if flt(po.grand_total) != flt(po.net_total):
        frappe.throw(_("La commande de la part stock porte encore une taxe ({0} pour {1} HT) : "
                       "vérifiez le modèle de taxe du fournisseur {2}.").format(po.grand_total, po.net_total, supplier))
    po.submit()   # hooks : lien fiche + reçu automatique (l'avance n'est pas convertie : cf. avance_en_journal)

    if est_bl:
        # BL : avance ERPNext sur la commande (le compte de charge la finance), imputée à la facture mensuelle.
        remarques = list(ctx["remarques"]) + [
            _("Part stock de la dépense {0} : le compte de charge finance la commande {1} ({2} HT)")
            .format(ctx.get("depense") or "", po.name, stock["stock_ht"])]
        debit = {"account": C.COMPTE_CREDITEURS, "party_type": "Supplier", "party": supplier,
                 "debit_in_account_currency": stock["stock_ht"], "is_advance": "Yes",
                 "reference_type": "Purchase Order", "reference_name": po.name, "cost_center": C.CC}
        # Préfixe à part (relecture du 25/11/2026) : « Dépense caisse — » faisait lire au rapport de caisse une
        # dépense « Carte de crédit » de la part stock, alors que cette écriture ne touche aucune caisse.
        je = C._ecriture([debit, {"account": ctx["compte"], "credit_in_account_currency": stock["stock_ht"],
                                  "cost_center": C.CC}], description, remarques, prefixe=PREFIXE_FINANCEMENT)
        frappe.db.set_value("Facture Achat a Saisir", fiche.name, "journal_entry", je.name, update_modified=False)
        if ctx.get("photo"):
            C._attacher_scan(ctx["photo"], "facture-%s" % fiche.name, "Journal Entry", je.name, coins=ctx.get("coins"))
        je_nom = je.name
    else:
        # Facture : la dépense EST la facture. La réception automatique a crédité « Stock reçus mais non facturés » ;
        # on la solde contre la charge, et commande ET réception sont fermées (25/11/2026 : la réception restait
        # « à facturer » pour toujours) — le même geste que pour un BL déjà reçu (caisse_bl).
        je_nom = solder_reception(fiche.name, dict(ctx, part_stock=stock["stock_ht"]))["journal_entry"]

    recu = frappe.db.get_value("Facture Achat a Saisir", fiche.name, "purchase_receipt")
    return {"fiche_achat": fiche.name, "journal_entry": je_nom, "purchase_order": po.name,
            "purchase_receipt": recu}


def solder_reception(fiche_nom: str, ctx: dict) -> dict:
    """Solde la réception d'une fiche d'achat contre le compte de charge d'une dépense payée comme facture : Dr « Stock
    reçus mais non facturés » / Cr charge, à la VALEUR DU REÇU (ce qu'il a crédité) ; ferme commande et réception ;
    la fiche passe « Saisie », rattachée à la dépense. Écriture « Réception soldée — » : hors du rapport de caisse
    (aucune ligne de caisse) et hors du dossier comptable (la dépense est la facture, entière).

    Utilisé par la part stock d'une facture (`creer_part_stock`) et par les BL déjà reçus qu'une facture couvre
    (`caisse_bl.couvrir_par_depense`). Dans la transaction de `caisse_depenses.creer` : tout ou rien.
    `ctx` : {compte, numero_facture, depense, fournisseur, numero_bl?, photo?, coins?}.
    -> {fiche, recu (valeur soldée), journal_entry, recu_doc, commande}"""
    from customization_app import caisse_bl as B, caisse_depenses as C

    fiche = frappe.get_doc("Facture Achat a Saisir", fiche_nom)
    # Lectures VERROUILLANTES : sous REPEATABLE READ, une lecture simple verrait l'état d'avant une saisie concurrente.
    verrou = frappe.db.get_value("Facture Achat a Saisir", fiche_nom, ["statut", "purchase_receipt", "purchase_order"],
                                 as_dict=True, for_update=True)
    if verrou.statut != "À saisir":
        frappe.throw(_("La fiche {0} est déjà « {1} » : sa réception ne se solde pas une 2e fois.")
                     .format(fiche_nom, verrou.statut))
    recu = verrou.purchase_receipt
    etat = frappe.db.get_value("Purchase Receipt", recu, ["docstatus", "status", "per_billed"], as_dict=True,
                               for_update=True) if recu else None
    if not etat or cint(etat.docstatus) != 1:
        frappe.throw(_("La réception d'achat de la fiche {0} n'existe pas ou n'est pas validée : rien n'est "
                       "enregistré (créez-la depuis la commande {1}, puis recommencez).")
                     .format(fiche_nom, verrou.purchase_order or "?"))
    if etat.status == "Closed" or flt(etat.per_billed) > 0:
        frappe.throw(_("La réception {0} est déjà fermée ou facturée : elle ne se solde pas une 2e fois.").format(recu))
    valeur = B.valeur_recu(recu)
    if valeur <= 0:
        frappe.throw(_("La réception {0} n'a rien à solder.").format(recu))
    numero_facture = (ctx.get("numero_facture") or "").strip()
    description = B.libelle_solde(ctx.get("numero_bl") or (fiche.numero_bl if cint(fiche.est_bl) else ""),
                                  ctx.get("fournisseur") or fiche.fournisseur or fiche.supplier, numero_facture)
    remarques = [description,
                 _("Solde de la réception {0} (commande {1}, fiche {2}) : {3} HT").format(
                     recu, verrou.purchase_order or "—", fiche.name, valeur),
                 _("Couverte par la dépense {0}{1}").format(
                     ctx.get("depense") or "?", _(" — facture n° {0}").format(numero_facture) if numero_facture else "")]
    if ctx.get("part_stock") is not None:
        remarques.append(_("Part stock de la dépense : commande sans TVA ; la dépense porte tout le HT et toute la TVA"))
    remarques.append(_("Saisie caisse par {0}").format(frappe.session.user))
    je = C._ecriture(B.lignes_solde(valeur, ctx["compte"], compte_stock_recu_non_facture(), C.CC),
                     description, remarques, prefixe=B.PREFIXE_SOLDE)
    if verrou.purchase_order:
        etat_po = frappe.db.get_value("Purchase Order", verrou.purchase_order, ["docstatus", "status"], as_dict=True,
                                      for_update=True)
        if etat_po and cint(etat_po.docstatus) == 1 and etat_po.status != "Closed":
            po = frappe.get_doc("Purchase Order", verrou.purchase_order)
            po.flags.ignore_permissions = True
            po.update_status("Closed")
    pr = frappe.get_doc("Purchase Receipt", recu)
    pr.flags.ignore_permissions = True
    pr.update_status("Closed")
    valeurs = {"statut": "Saisie", "journal_entry": je.name, "depense_a_payer": ctx.get("depense") or fiche.depense_a_payer}
    if numero_facture:
        valeurs["numero_facture"] = numero_facture
    frappe.db.set_value("Facture Achat a Saisir", fiche.name, valeurs, update_modified=False)
    if ctx.get("photo"):
        C._attacher_scan(ctx["photo"], "facture-%s" % fiche.name, "Journal Entry", je.name, coins=ctx.get("coins"))
    for doctype, nom in (("Purchase Order", verrou.purchase_order), ("Purchase Receipt", recu)):
        if nom:
            frappe.get_doc(doctype, nom).add_comment("Comment", _("🧾 Soldé par la caisse ({0}) : facture {1}, dépense {2}.")
                                                     .format(je.name, numero_facture or "?", ctx.get("depense") or "?"))
    return {"fiche": fiche.name, "recu": valeur, "journal_entry": je.name, "recu_doc": recu,
            "commande": verrou.purchase_order}


def _sans_taxes(doc) -> None:
    """Aucune TVA sur la pièce d'achat de la part stock (toute la TVA est dans la dépense) : les
    modèles de taxe d'article et de fournisseur sont neutralisés, puis les totaux recalculés."""
    doc.taxes_and_charges = ""
    doc.set("taxes", [])
    for it in doc.items:
        it.item_tax_template, it.item_tax_rate = "", "{}"
    doc.calculate_taxes_and_totals()


# ----------------------------------------------------------------------------- lecture photo
def _suggerer_article(designation: str):
    """Un article de stock dont le nom contient le mot le plus long de la désignation, sinon None."""
    mots = sorted((m for m in (designation or "").replace("/", " ").split() if len(m) >= 4), key=len, reverse=True)
    for mot in mots[:3]:
        code = frappe.db.get_value("Item", {"item_name": ["like", "%%%s%%" % mot], "disabled": 0, "is_stock_item": 1}, "name")
        if code:
            return code
    return None


def prix_nets(lignes, remise_globale=0.0, total_ht_net=0.0) -> dict:
    """Le prix unitaire HT NET de chaque ligne lue : remise de la ligne, puis remise globale répartie au prorata.
    Fonction pure.

    POURQUOI (25/11/2026, facture d'outillage « … TOTAL » : « elle n'a pas lu la remise ») : la part stock entrait
    au prix BRUT — la consigne de lecture disait d'ignorer la remise globale et ne demandait pas celle des lignes.
    Le stock doit entrer au prix réellement payé.
    `lignes` : [{designation, quantite, prix_unitaire_ht (brut), remise_pct, montant_ht (net de sa remise)}] ;
    `remise_globale` : remise HT sur le total ; `total_ht_net` : total HT après toutes les remises.
    -> {lignes: [{designation, qty, rate (net), rate_brut, remise_pct, montant_ht}], remise_globale, facteur,
        somme_lignes (nettes de leur remise, avant la remise globale), total_ht_net}"""
    nettes = []
    for l in lignes or []:
        qty = flt(l.get("quantite")) or 1
        brut, montant = flt(l.get("prix_unitaire_ht")), flt(l.get("montant_ht"))
        pct = min(max(flt(l.get("remise_pct")), 0.0), 100.0)
        par_montant = montant / qty if montant > 0 else 0.0
        par_remise = brut * (1 - pct / 100) if brut > 0 else 0.0
        # Le montant imprimé de la ligne fait foi (il porte la remise telle que la facture l'a calculée) — sauf s'il
        # n'est que le brut recopié alors qu'une remise est lue.
        if par_montant and not (pct > 0 and brut and abs(par_montant - brut) < 0.0005):
            net = par_montant
        else:
            net = par_remise or par_montant
        brut = brut or net
        nettes.append({"designation": (l.get("designation") or "").strip(), "qty": qty, "net": net, "brut": brut,
                       "bl": str(l.get("bl") or "").strip(), "code": str(l.get("code_article") or l.get("code") or "").strip()})
    somme = sum(x["qty"] * x["net"] for x in nettes)
    remise_globale, total_ht_net = max(flt(remise_globale), 0.0), max(flt(total_ht_net), 0.0)
    tolerance = max(0.5, 0.002 * somme)
    # La « Remise » du pied de facture n'est souvent que le RÉCAPITULATIF des remises de lignes (26/11/2026, SCPS
    # FT26080301 : 33 % sur chaque ligne, « Remise 85,328 » en bas, total HT 173,242 = la somme des lignes nettes) :
    # l'appliquer en plus divisait les prix par deux (2,545 lu 1,291). Le total HT net de la facture tranche ; sans lui,
    # une remise globale égale à la somme des remises de lignes n'est pas appliquée une 2e fois.
    remises_lignes = sum(x["qty"] * max(x["brut"] - x["net"], 0.0) for x in nettes)
    facteur = 1.0
    if total_ht_net > 0:
        if somme - total_ht_net > tolerance:
            # Les lignes font plus que le total net de la facture : une remise globale ou non détaillée, répartie.
            facteur = total_ht_net / somme
    elif remise_globale > 0 and somme > remise_globale and abs(remise_globale - remises_lignes) > tolerance:
        facteur = (somme - remise_globale) / somme
    out = []
    for x in nettes:
        rate = round(x["net"] * facteur, PRECISION)
        remise = round((1 - rate / x["brut"]) * 100, 2) if x["brut"] and rate < x["brut"] - 0.0005 else 0.0
        out.append({"designation": x["designation"], "qty": x["qty"], "rate": rate,
                    "rate_brut": round(x["brut"], PRECISION), "remise_pct": remise,
                    "montant_ht": round(x["qty"] * rate, PRECISION),
                    # La rubrique « TOTAL BL N° … » de la facture sous laquelle la ligne est imprimée (25/11/2026) :
                    # une ligne d'un BL déjà reçu ne se remet pas en stock (caisse_bl.ligne_deja_recue).
                    "bl": x["bl"], "bl_cle": cle_numero_bl(x["bl"]), "code": x["code"]})
    return {"lignes": out, "remise_globale": round(somme * (1 - facteur), PRECISION) if facteur < 1 else 0.0,
            "facteur": round(facteur, 6), "somme_lignes": round(somme, PRECISION),
            "total_ht_net": round(total_ht_net, PRECISION)}


def lire_lignes(contenu, mimetype) -> dict:
    """Les lignes d'articles d'une pièce (facture ou BL), lues par OpenAI, aux prix NETS des remises (prix_nets). Sans
    contrôle de droits : l'API de la fenêtre (`lire_articles`) et la tâche d'apprentissage (correspondance_articles)
    l'appellent. -> {lignes: [{designation, code, qty, rate, …, bl, bl_cle}], remise_globale, …}"""
    from customization_app import caisse_depenses as C
    from bank_retenue_sync.ai.invoice_extract import _get_client_model_temp

    client, model, _t = _get_client_model_temp()
    res = client.responses.create(
        model=model,
        instructions=(
            "Tu lis les LIGNES D'ARTICLES d'une facture ou d'un bon de livraison tunisien. "
            "Réponds STRICTEMENT en JSON : {\"lignes\": [{\"designation\": <libellé de la ligne>, "
            "\"code_article\": <le code ou la référence de l'article chez le FOURNISSEUR (colonne « Article », "
            "« Réf. », « Code »), tel qu'écrit ; null s'il n'y en a pas>, "
            "\"quantite\": <nombre>, \"prix_unitaire_ht\": <prix unitaire HT AVANT remise>, "
            "\"remise_pct\": <remise de la ligne en %, 0 si aucune>, "
            "\"montant_ht\": <montant HT de la ligne APRÈS sa remise>, "
            "\"bl\": <le n° du bon de livraison sous lequel la ligne est imprimée (rubrique « TOTAL BL N° … »), "
            "tel qu'écrit ; null si la facture ne regroupe pas de BL>}], "
            "\"remise_globale_ht\": <remise appliquée sur le total HT, en dinars, 0 si aucune>, "
            "\"total_ht_net\": <total HT après toutes les remises, avant TVA et timbre>}. "
            "Les colonnes « Remise », « Rem. », « R% », « Taux remise » donnent la remise de la ligne ; une ligne "
            "« Remise » ou « Escompte » dans le pied de facture est la remise globale. "
            "Montants en dinars avec trois décimales, virgule décimale dans le document : « 12,500 » vaut 12.5. "
            "Ne mets pas en lignes d'articles les totaux, la TVA, le timbre ni la remise globale."),
        input=[{"role": "user", "content": [
            C._bloc_document(contenu, mimetype),
            {"type": "input_text", "text": "Liste les lignes d'articles avec quantité, prix unitaire HT, remise et "
                                           "montant HT net ; puis la remise globale et le total HT net."}]}])
    texte = (res.output_text or "").strip().strip("`")
    if texte.lower().startswith("json"):
        texte = texte.split("\n", 1)[1]
    try:
        lu = json.loads(texte)
    except Exception:
        lu = {}
    lu = lu if isinstance(lu, dict) else {}
    return prix_nets(lu.get("lignes") or [], lu.get("remise_globale_ht"), lu.get("total_ht_net"))


@frappe.whitelist()
def lire_articles(photo, supplier=None):
    """Les lignes d'articles de la pièce photographiée, avec l'article proposé pour chacune : appris (la mémoire des
    articles du fournisseur, correspondance_articles) ou deviné (les mots de la désignation). L'employé coche, corrige,
    confirme : rien n'est créé ici."""
    from customization_app import caisse_depenses as C
    from customization_app import correspondance_articles as CA

    frappe.only_for(C.ROLES)
    if not photo:
        frappe.throw(_("Aucune photo à lire."))
    contenu, mimetype = C._decoder(photo)
    out = lire_lignes(contenu, mimetype)
    if supplier and not frappe.db.exists("Supplier", supplier):
        supplier = None
    CA.proposer_pour_lignes(supplier, out["lignes"])
    return out
