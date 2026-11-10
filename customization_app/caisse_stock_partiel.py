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

PRECISION = 3


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
                    "designation": (a.get("designation") or "").strip()})
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

    # Le compte de charge finance la commande.
    remarques = list(ctx["remarques"]) + [
        _("Part stock de la dépense {0} : le compte de charge finance la commande {1} ({2} HT)")
        .format(ctx.get("depense") or "", po.name, stock["stock_ht"])]
    if est_bl:
        # BL : avance ERPNext sur la commande, imputée à la facture mensuelle.
        debit = {"account": C.COMPTE_CREDITEURS, "party_type": "Supplier", "party": supplier,
                 "debit_in_account_currency": stock["stock_ht"], "is_advance": "Yes",
                 "reference_type": "Purchase Order", "reference_name": po.name, "cost_center": C.CC}
    else:
        # Facture : la dépense est la facture ; on solde le compte crédité par le reçu.
        debit = {"account": compte_stock_recu_non_facture(), "debit_in_account_currency": stock["stock_ht"],
                 "cost_center": C.CC}
        remarques.append(_("Pièce déjà facturée : commande fermée, stock reçu soldé contre la charge"))
    je = C._ecriture([debit, {"account": ctx["compte"], "credit_in_account_currency": stock["stock_ht"],
                              "cost_center": C.CC}], description, remarques)
    frappe.db.set_value("Facture Achat a Saisir", fiche.name, "journal_entry", je.name, update_modified=False)
    if not est_bl:
        po.reload()
        po.flags.ignore_permissions = True
        po.update_status("Closed")
        frappe.db.set_value("Facture Achat a Saisir", fiche.name, "statut", "Saisie", update_modified=False)
    if ctx.get("photo"):
        C._attacher_scan(ctx["photo"], "facture-%s" % fiche.name, "Journal Entry", je.name, coins=ctx.get("coins"))

    recu = frappe.db.get_value("Facture Achat a Saisir", fiche.name, "purchase_receipt")
    return {"fiche_achat": fiche.name, "journal_entry": je.name, "purchase_order": po.name,
            "purchase_receipt": recu}


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


@frappe.whitelist()
def lire_articles(photo):
    """Les lignes d'articles de la pièce photographiée, lues par OpenAI, avec un article de stock
    suggéré quand un nom y ressemble. L'employé coche, corrige, confirme : rien n'est créé ici."""
    from customization_app import caisse_depenses as C

    frappe.only_for(C.ROLES)
    if not photo:
        frappe.throw(_("Aucune photo à lire."))
    from bank_retenue_sync.ai.invoice_extract import _get_client_model_temp

    contenu, mimetype = C._decoder(photo)
    client, model, _t = _get_client_model_temp()
    res = client.responses.create(
        model=model,
        instructions=(
            "Tu lis les LIGNES D'ARTICLES d'une facture ou d'un bon de livraison tunisien. "
            "Réponds STRICTEMENT en JSON : {\"lignes\": [{\"designation\": <libellé de la ligne>, "
            "\"quantite\": <nombre>, \"prix_unitaire_ht\": <nombre, hors TVA>, \"montant_ht\": <nombre>}]}. "
            "Montants en dinars avec trois décimales, virgule décimale dans le document : « 12,500 » vaut 12.5. "
            "Ignore les lignes de totaux, TVA, timbre, remise globale."),
        input=[{"role": "user", "content": [
            C._bloc_document(contenu, mimetype),
            {"type": "input_text", "text": "Liste les lignes d'articles avec quantité et prix unitaire HT."}]}])
    texte = (res.output_text or "").strip().strip("`")
    if texte.lower().startswith("json"):
        texte = texte.split("\n", 1)[1]
    try:
        lu = json.loads(texte).get("lignes") or []
    except Exception:
        lu = []
    out = []
    for l in lu:
        designation = (l.get("designation") or "").strip()
        qty = flt(l.get("quantite")) or 1
        rate = flt(l.get("prix_unitaire_ht"))
        if not rate and flt(l.get("montant_ht")) and qty:
            rate = round(flt(l.get("montant_ht")) / qty, PRECISION)
        out.append({"designation": designation, "qty": qty, "rate": round(rate, PRECISION),
                    "montant_ht": round(qty * rate, PRECISION),
                    "item_code": _suggerer_article(designation)})
    return out
