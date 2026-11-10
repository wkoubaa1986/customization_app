"""Une dépense de caisse dont UNE PARTIE part en stock (décision utilisateur 10/11/2026).

Le technicien achète sur bon de livraison (ou facture) des pièces dont certaines vont en stock et
le reste est consommé ou n'est pas un article. Le fournisseur facture au mois. À la caisse, il
saisit la pièce comme une « Dépense avec facture », coche les articles qui partent en stock, et
paie le tout en une fois. Le paiement se répartit :

  · la part des articles cochés devient une AVANCE sur une COMMANDE d'achat créée pour eux
    (fiche « Facture Achat a Saisir » + écriture d'avance, exactement le circuit des BL
    « Facture d'achat ») ; la commande est soumise, le reçu entre le stock, et l'avance devient
    un paiement de la commande (hooks de caisse_depenses) ;
  · le reste part en DÉPENSE sur le compte de charge, avec sa TVA, comme toute dépense facturée.

La facture mensuelle du fournisseur se crée ensuite depuis les reçus (« Rattacher des BL ») pour
les parts stock, et s'agrafe aux parts dépense (« Rattacher une facture aux BL »). Le contrôle
scan/saisie de la facture d'achat (bank_retenue_sync) déduit les parts dépense des totaux lus.

Les fonctions de calcul sont pures ; la création des pièces s'appuie sur caisse_depenses.
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


def taux_effectif(montant, tva, taux_tva) -> float:
    """Le taux de TVA à appliquer à la part stock : celui lu (7, 13, 19) sinon celui que les
    totaux impliquent, 0 si la pièce ne porte pas de TVA. Pure."""
    if flt(tva) <= 0:
        return 0.0
    if flt(taux_tva) > 0:
        return flt(taux_tva)
    ht = flt(montant) - flt(tva)
    return round(100.0 * flt(tva) / ht, PRECISION) if ht > 0 else 0.0


def ventiler_stock(montant, tva, taux_tva, lignes) -> dict:
    """Ce qui part en stock et ce qui reste en dépense, HT / TVA / TTC. Pure.

    La TVA de la part stock ne dépasse jamais la TVA de la pièce ; le reste absorbe les centimes.
    """
    stock_ht = round(sum(l["qty"] * l["rate"] for l in lignes), PRECISION)
    taux = taux_effectif(montant, tva, taux_tva)
    stock_tva = min(round(stock_ht * taux / 100.0, PRECISION), flt(tva)) if taux else 0.0
    stock_ttc = round(stock_ht + stock_tva, PRECISION)
    depense_ttc = round(flt(montant) - stock_ttc, PRECISION)
    depense_tva = round(flt(tva) - stock_tva, PRECISION)
    return {"stock_ht": stock_ht, "stock_tva": stock_tva, "stock_ttc": stock_ttc, "taux": taux,
            "depense_ht": round(depense_ttc - depense_tva, PRECISION),
            "depense_tva": depense_tva, "depense_ttc": depense_ttc}


def partager_regs(regs, avance) -> tuple:
    """Les règlements payés, coupés en deux listes : ce qui couvre l'avance (part stock), puis le
    reste (part dépense). Un règlement à cheval figure dans les deux, avec le même chèque. Pure."""
    reste = round(flt(avance), PRECISION)
    pour_avance, pour_depense = [], []
    for r in regs or []:
        m = round(flt(r.get("montant")), PRECISION)
        if reste <= 0:
            pour_depense.append(dict(r))
            continue
        part = min(m, reste)
        pour_avance.append(dict(r, montant=part))
        if m - part > 0.0005:
            pour_depense.append(dict(r, montant=round(m - part, PRECISION)))
        reste = round(reste - part, PRECISION)
    return pour_avance, pour_depense


def remarque(v: dict, nb_lignes: int) -> str:
    return ("Part mise en stock : %s HT + %s TVA = %s TTC (%d article(s), avance sur commande d'achat) ; "
            "part dépense : %s HT + %s TVA = %s TTC"
            % (v["stock_ht"], v["stock_tva"], v["stock_ttc"], nb_lignes,
               v["depense_ht"], v["depense_tva"], v["depense_ttc"]))


# ----------------------------------------------------------------------------- préparation
def preparer(articles_stock, montant, tva, taux_tva, paid, unpaid, mode, regs) -> dict | None:
    """Contrôle et ventile. -> dict (voir ventiler_stock + regs_avance / regs_depense / lignes),
    ou None si aucun article n'est coché."""
    from customization_app import caisse_depenses as C

    lignes = lignes_valides(articles_stock)
    if not lignes:
        return None
    for l in lignes:
        est_stock = frappe.db.get_value("Item", l["item_code"], "is_stock_item")
        if est_stock is None:
            frappe.throw(_("L'article {0} n'existe pas.").format(l["item_code"]))
        if not cint(est_stock):
            frappe.throw(_("L'article {0} n'est pas un article de stock.").format(l["item_code"]))
    if mode == C.MODE_PAS_PAYE or flt(unpaid) > 0:
        frappe.throw(_("Une part mise en stock exige un paiement complet : l'avance sur la commande "
                       "d'achat se prélève sur ce qui est payé aujourd'hui."))
    if any(r.get("mode") == C.MODE_TRAITE for r in regs or []):
        frappe.throw(_("Une part mise en stock ne se règle pas par traite : la traite ne se partage "
                       "pas entre l'avance et la dépense."))
    v = ventiler_stock(montant, tva, taux_tva, lignes)
    if v["stock_ttc"] <= 0:
        return None
    if v["stock_ttc"] >= flt(montant) - 0.0005:
        frappe.throw(_("Tout le montant part en stock ({0} sur {1}) : saisissez la pièce en "
                       "« Facture d'achat », pas en dépense.").format(v["stock_ttc"], montant))
    regs_avance, regs_depense = partager_regs(paid, v["stock_ttc"])
    v.update({"lignes": lignes, "regs_avance": regs_avance, "regs_depense": regs_depense})
    return v


# ----------------------------------------------------------------------------- création
def creer_part_stock(stock: dict, ctx: dict) -> dict:
    """L'avance, la fiche d'achat et la commande pour la part stock. -> {fiche_achat, journal_entry,
    purchase_order, purchase_receipt}. Appelé par caisse_depenses.creer, dans sa transaction."""
    from customization_app import achats_magasin, caisse_depenses as C

    supplier = C._supplier(ctx.get("fournisseur"), matricule=ctx.get("matricule"), supplier=ctx.get("supplier"))
    if not supplier:
        frappe.throw(_("Le fournisseur est obligatoire pour mettre des articles en stock."))
    description = (ctx["description"] + " — part stock")[:140]
    remarques = list(ctx["remarques"]) + [_("Avance sur commande d'achat (part stock) : {0}").format(stock["stock_ttc"])]
    C._remarques_paiements(remarques, stock["regs_avance"])
    je = C._ecriture(C._lignes_credit(stock["regs_avance"]) + [
        {"account": C.COMPTE_CREDITEURS, "party_type": "Supplier", "party": supplier,
         "debit_in_account_currency": stock["stock_ttc"], "cost_center": C.CC},
    ], description, remarques)
    fiche = frappe.get_doc({
        "doctype": "Facture Achat a Saisir",
        "fournisseur": (ctx.get("fournisseur") or "").strip(), "supplier": supplier,
        "montant": stock["stock_ttc"],
        "numero_facture": (ctx.get("numero_facture") or "").strip(),
        "date_facture": ctx.get("date_facture") or None,
        "est_bl": 1, "numero_bl": (ctx.get("numero_bl") or "").strip(),
        "mode_paiement": C._mode_global(stock["regs_avance"]),
        "journal_entry": je.name, "description": description,
        "saisi_par": frappe.session.user,
        "depense_a_payer": ctx.get("depense"),
        "part_depense_ht": stock["depense_ht"], "part_depense_tva": stock["depense_tva"],
        "part_depense_ttc": stock["depense_ttc"],
    })
    fiche.insert(ignore_permissions=True)
    if ctx.get("photo"):
        C._attacher_scan(ctx["photo"], "facture-%s" % fiche.name, "Facture Achat a Saisir", fiche.name, coins=ctx.get("coins"))
        C._attacher_scan(ctx["photo"], "facture-%s" % fiche.name, "Journal Entry", je.name, coins=ctx.get("coins"))
    C._attacher_cheques(stock["regs_avance"], je.name, dispense=ctx.get("dispense", False))

    po = frappe.new_doc("Purchase Order")
    po.supplier = supplier
    po.company = C.COMPANY
    po.transaction_date = nowdate()
    po.schedule_date = nowdate()
    po.custom_fiche_caisse = fiche.name
    po.set_warehouse = achats_magasin.ENTREPOT_ACHATS
    for l in stock["lignes"]:
        po.append("items", {"item_code": l["item_code"], "qty": l["qty"], "rate": l["rate"],
                            "schedule_date": nowdate(), "warehouse": achats_magasin.ENTREPOT_ACHATS})
    if stock["taux"] > 0:
        compte_tva = C.COMPTE_TVA_7 if flt(stock["taux"]) == 7 else C.COMPTE_TVA_19
        po.append("taxes", {"charge_type": "On Net Total", "account_head": compte_tva,
                            "rate": stock["taux"], "description": "TVA %s%%" % flt(stock["taux"]),
                            "cost_center": C.CC})
    po.flags.ignore_permissions = True
    po.insert()
    po.submit()   # hooks : lien fiche, avance -> paiement de la commande, reçu automatique
    recu = frappe.db.get_value("Facture Achat a Saisir", fiche.name, "purchase_receipt")
    return {"fiche_achat": fiche.name, "journal_entry": je.name, "purchase_order": po.name,
            "purchase_receipt": recu}


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
