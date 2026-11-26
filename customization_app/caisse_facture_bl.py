"""Une FACTURE D'ACHAT qui couvre des BL, saisie à la caisse (26/11/2026).

POURQUOI. Le 26/11/2026, Sadok a reçu la facture SCPS FT26080301 (207,158 TTC, payée par chèque) : rien que de la
marchandise, sur deux BL — BL2615138 (27 réductions, déjà reçues le 07/09 : fiche FAS-0317, commande, réception) et
BL2615063 (30 + 4 réductions, jamais saisies). Le type « Facture d'achat » de la fenêtre ne savait rien des BL : la
reconnaissance des BL déjà reçus (25/11/2026, caisse_bl) n'existait que pour « Dépense avec facture ». Il l'a donc
saisie en DÉPENSE : stock juste, mais tout le HT est passé par « Charges Diverses », aucune facture d'achat n'existe,
rien au journal des achats ni au compte du fournisseur. Et en « Facture d'achat », le comptable devait penser seul à
« Rattacher des BL » — sinon les 27 réductions entraient en stock une deuxième fois.

CE QUE FAIT LA SAISIE (sa décision : « code en premier »). En « Facture d'achat », pièce = facture (pas un BL) :
  · l'analyse lit les BL que la facture cite ; chacun est cherché parmi les BL ouverts du fournisseur (caisse_bl,
    pour="achat") — un BL trouvé par son n° est coché d'office ;
  · un BL coché DÉJÀ REÇU n'est pas racheté : sa réception sera FACTURÉE (pas soldée contre une charge) ;
  · les lignes qui ne sont sur aucune réception (BL jamais saisi, ou facture sans BL) sont REÇUES ici : une commande
    d'achat sans taxe et sa réception (n° du BL sur la réception), SANS fiche à part dans la file des factures ;
  · la FACTURE D'ACHAT est préparée EN BROUILLON depuis toutes ces réceptions (prix des réceptions : ERPNext impose le
    même prix sur tout le circuit), avec son n°, sa date, la TVA lue, le timbre, le scan. Le comptable n'a plus qu'à
    vérifier et valider : les contrôles de bank_retenue_sync (scan PDF, totaux) jouent comme pour toute facture ;
  · le paiement de caisse reste ce qu'il est pour une « Facture d'achat » : une avance au fournisseur, qui devient le
    paiement de la facture à sa validation (caisse_depenses.pi_marquer_fiche_saisie), comme chaque fiche passe « Saisie ».

L'ÉCART ENTRE LA FACTURE ET LES RÉCEPTIONS (partage_hors_ht). Les prix unitaires ont 3 décimales et le prix d'un BL déjà
reçu peut porter le timbre (FAS-0317 : 87,291 reçus pour 86,289 facturés + 1,000 de timbre). Le TTC de la facture fait
foi : TVA = celle de la facture ; le reste (TTC − TVA − HT des réceptions) est le timbre s'il le couvre, puis une ligne
d'arrondi de quelques millimes ; au-delà d'une tolérance, c'est un vrai écart — dit sur la facture préparée, et refusé
s'il dépasse ce que le contrôle des totaux laisserait passer.

Les fonctions de règle sont PURES (testées sans base) ; le reste tourne dans la transaction de caisse_depenses.creer :
tout ou rien."""
from __future__ import annotations

import contextlib

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

FICHE = "Facture Achat a Saisir"
COMPTE_TIMBRE = "Timbre Fiscal - A&S"
# Les modes de la caisse → un « Mode of Payment » pour l'échéancier (obligatoire sur ce site).
MODES_ECHEANCIER = {"Espèces": "Espèces", "Chèque": "Chèque", "Carte de crédit": "Carte de crédit", "Virement": "Virement",
                    "Traite bancaire": "Traite bancaire LC", "Pas payé": "Dette non payée"}


# ── Règles pures ─────────────────────────────────────────────────────────────

def lignes_a_recevoir(articles) -> list:
    """Les lignes à recevoir, normalisées : [{item_code, qty, rate, designation, bl_cle, code}]. PURE. Une ligne sans
    article ou sans quantité est refusée par `controler_lignes`, pas écartée en silence : en facture d'achat, toute
    ligne est de la marchandise."""
    out = []
    for a in articles or []:
        a = a or {}
        out.append({"item_code": (a.get("item_code") or "").strip(), "qty": flt(a.get("qty"), 3), "rate": flt(a.get("rate"), 3),
                    "designation": (a.get("designation") or "").strip(), "bl_cle": (a.get("bl_cle") or "").strip(),
                    "code": (a.get("code") or "").strip()})
    return out


def controler_lignes(lignes) -> str:
    """"" si chaque ligne peut être reçue, sinon le motif. PURE."""
    for i, l in enumerate(lignes or [], 1):
        nom = l.get("designation") or l.get("code") or ("ligne %d" % i)
        if not l.get("item_code"):
            return "la ligne « %s » n'a pas d'article : choisissez-le, ou saisissez la pièce en « Dépense avec facture » " \
                   "si ce n'est pas de la marchandise" % nom
        if flt(l.get("qty")) <= 0:
            return "la ligne « %s » n'a pas de quantité" % nom
        if flt(l.get("rate")) < 0:
            return "la ligne « %s » a un prix négatif" % nom
    return ""


def grouper(lignes, cites=None, fiches_sans_recu=None) -> list:
    """Range les lignes à recevoir par BL. PURE. → [{cle, numero, date, fiche, lignes}], dans l'ordre d'apparition.
    `cites` : les BL lus sur la facture [{numero, date}] ; `fiches_sans_recu` : {clé de BL: fiche} des BL cochés qui
    n'ont pas encore de réception — leurs lignes se reçoivent SUR leur fiche (pas de 2e fiche pour le même BL).
    Une ligne sans rubrique de BL va dans le groupe de clé "" (« reçu à la facture »)."""
    from customization_app.caisse_bl import cle_numero_bl

    par_cle = {cle_numero_bl(c.get("numero")): c for c in cites or [] if cle_numero_bl(c.get("numero"))}
    fiches_sans_recu = fiches_sans_recu or {}
    groupes, rang = [], {}
    for l in lignes or []:
        cle = cle_numero_bl(l.get("bl_cle")) if l.get("bl_cle") else ""
        if cle not in rang:
            cite = par_cle.get(cle) or {}
            rang[cle] = len(groupes)
            groupes.append({"cle": cle, "numero": (cite.get("numero") or l.get("bl_cle") or "").strip() if cle else "",
                            "date": cite.get("date") or None, "fiche": fiches_sans_recu.get(cle), "lignes": []})
        groupes[rang[cle]]["lignes"].append(l)
    return groupes


def bls_en_double(groupes, fiches, coches) -> list:
    """Les lignes À RECEVOIR rangées sous un BL qui a DÉJÀ sa fiche chez ce fournisseur, sans que cette fiche soit
    cochée. PURE. → [{numero, fiche, motif}]. Vu à l'essai du 26/11/2026 : le BL reconnu décoché, ses 27 réductions
    déjà en stock auraient été reçues une 2e fois. Le même n° de BL chez le même fournisseur est la même livraison :
    on refuse, quel que soit l'état de la fiche (reçue, déjà couverte, ou encore sans commande — une 2e fiche pour le
    même BL finirait par deux réceptions).
    `fiches` : [{name, numero_bl, numero_facture, statut, purchase_receipt, pr_docstatus, purchase_invoice,
    depense_a_payer}] — les fiches de BL du fournisseur ; `coches` : les fiches cochées à l'écran."""
    from customization_app.caisse_bl import cles_fiche

    coches = set(coches or [])
    out = []
    for g in groupes or []:
        if not g.get("cle"):
            continue
        for f in fiches or []:
            if f.get("name") in coches or g["cle"] not in cles_fiche(f.get("numero_bl"), f.get("numero_facture")):
                continue
            if f.get("purchase_receipt") and cint(f.get("pr_docstatus")) == 1:
                motif = "déjà reçu en stock (réception %s)" % f["purchase_receipt"]
            elif f.get("purchase_invoice") or f.get("depense_a_payer") or (f.get("statut") or "À saisir") != "À saisir":
                motif = "déjà couvert (%s)" % (f.get("purchase_invoice") or f.get("depense_a_payer") or f.get("statut"))
            else:
                motif = "déjà saisi, pas encore reçu"
            out.append({"numero": g.get("numero") or f.get("numero_bl") or "?", "fiche": f.get("name"), "motif": motif})
            break
    return out


def tolerance_arrondi(nb_lignes) -> float:
    """Ce que les prix unitaires à 3 décimales peuvent déplacer : 5 millimes par ligne, 2 centimes de base. PURE."""
    return round(0.02 + 0.005 * max(0, cint(nb_lignes)), 3)


def partage_hors_ht(ttc, tva, ht_receptions, timbre_lu=0, nb_lignes=0) -> dict:
    """Ce que la facture porte en plus du HT des réceptions et de sa TVA. PURE. → {timbre, arrondi, ecart}.
    Le TTC de la facture fait foi. reste = TTC − TVA − HT des réceptions : le timbre s'il le couvre (un BL reçu avec le
    timbre dans son prix n'en laisse pas : FAS-0317), puis l'arrondi dans la tolérance ; au-delà, un écart à dire."""
    reste = round(flt(ttc) - flt(tva) - flt(ht_receptions), 3)
    tol = tolerance_arrondi(nb_lignes)
    timbre_lu = max(0.0, flt(timbre_lu, 3))
    timbre = timbre_lu if timbre_lu > 0 and reste >= timbre_lu - tol else 0.0
    apres = round(reste - timbre, 3)
    if abs(apres) <= tol:
        return {"timbre": timbre, "arrondi": apres, "ecart": 0.0}
    return {"timbre": timbre, "arrondi": 0.0, "ecart": apres}


def ecart_admis(ecart, ttc) -> bool:
    """Un écart que le contrôle des totaux de la facture d'achat laisserait passer (bank_retenue_sync : HT à 1 DT ou 1 %
    du TTC près). Au-delà, on refuse à la caisse : la facture préparée ne serait pas validable. PURE."""
    return abs(flt(ecart)) <= max(1.0, 0.01 * abs(flt(ttc)))


def mode_echeancier(mode) -> str:
    """Le « Mode of Payment » de l'échéancier pour un mode de caisse (« Mixte » ou inconnu → Espèces). PURE."""
    return MODES_ECHEANCIER.get((mode or "").strip(), "Espèces")


@contextlib.contextmanager
def _droits_de_preparation():
    """Le temps de DÉRIVER une pièce d'une autre (commande → réception → facture d'achat), la session lit avec tous les
    droits. La saisie se fait à la caisse : le caissier n'a pas à avoir le droit de créer une facture d'achat ni de lire
    une réception (essai du 26/11/2026 : PermissionError dans get_mapped_doc, qu'ERPNext appelle sans option). Seule la
    dérivation est dans ce bloc ; chaque pièce est ensuite insérée HORS du bloc, au nom du caissier."""
    user = frappe.session.user
    frappe.local.session.user = "Administrator"
    try:
        yield
    finally:
        frappe.local.session.user = user


# ── Accès base ───────────────────────────────────────────────────────────────

def valider(bls_couverts, supplier) -> list:
    """Chaque BL coché, relu SOUS VERROU (deux caisses, ou une dépense qui le solde au même moment, se sérialisent).
    → [{fiche, numero_bl, cles, purchase_order, purchase_receipt (ou None), recu}]. Refuse avec le motif."""
    from customization_app import caisse_bl as B

    noms = sorted({n for n in B._liste(bls_couverts) if n})
    if not noms:
        return []
    if not supplier:
        frappe.throw(_("Le fournisseur doit être confirmé pour facturer des BL."))
    out = []
    for nom in noms:
        f = frappe.db.get_value(FICHE, nom, B.CHAMPS_FICHE, as_dict=True, for_update=True)
        if not f:
            frappe.throw(_("Le BL {0} n'existe plus : rechargez la fenêtre.").format(nom))
        f = B._etat_fiche(f, verrou=True)
        motif = B.motif_non_traitable(f, supplier, pour="achat")
        if motif:
            frappe.throw(_("Le BL {0} ({1}) ne peut pas être facturé ici : {2}.").format(f.get("numero_bl") or "?", nom, motif),
                         title=_("BL non facturable"))
        recu = f.get("purchase_receipt") if cint(f.get("pr_docstatus")) == 1 else None
        out.append({"fiche": nom, "numero_bl": f.get("numero_bl") or "", "purchase_order": f.get("purchase_order"),
                    "purchase_receipt": recu, "recu": flt(f.get("recu")) if recu else 0.0,
                    "cles": sorted(B.cles_fiche(f.get("numero_bl"), f.get("numero_facture"))),
                    "items": [i.get("item_code") for i in f.get("items") or []] if recu else []})
    return out


def _fiches_bl(supplier: str) -> list:
    """Toutes les fiches de BL de ce fournisseur, avec l'état de leur réception (pour `bls_en_double`)."""
    fiches = frappe.get_all(FICHE, filters={"supplier": supplier, "est_bl": 1},
                            fields=["name", "numero_bl", "numero_facture", "statut", "purchase_receipt", "purchase_invoice",
                                    "depense_a_payer"])
    for f in fiches:
        f["pr_docstatus"] = frappe.db.get_value("Purchase Receipt", f.purchase_receipt, "docstatus") if f.purchase_receipt else None
    return fiches


def bls_deja_recus_sans_fiche(groupes, receptions) -> list:
    """Les groupes de lignes dont le n° de BL est celui d'une réception VALIDÉE du fournisseur (« BL du fournisseur ») :
    un BL reçu par une facture précédente, sans fiche. PURE. `receptions` : [{name, supplier_delivery_note}].
    → [{numero, reception}]."""
    from customization_app.caisse_bl import cle_numero_bl

    par_cle = {cle_numero_bl(r.get("supplier_delivery_note")): r.get("name") for r in receptions or []
               if cle_numero_bl(r.get("supplier_delivery_note"))}
    return [{"numero": g.get("numero") or "?", "reception": par_cle[g["cle"]]} for g in groupes or []
            if g.get("cle") and g["cle"] in par_cle]


def recevoir(groupe: dict, supplier: str, ctx: dict) -> dict:
    """Reçoit les lignes d'un BL jamais saisi (ou d'un BL coché sans réception) : une commande d'achat SANS taxe (toute
    la TVA est sur la facture) et sa réception — le stock entre. → {fiche (None pour un BL jamais saisi), numero_bl,
    purchase_order, purchase_receipt, ht, lignes}."""
    from customization_app import achats_magasin, caisse_depenses as C, caisse_stock_partiel as S

    lignes = groupe["lignes"]
    for l in lignes:
        if not frappe.db.get_value("Item", l["item_code"], "is_stock_item"):
            frappe.throw(_("{0} n'est pas un article de stock : il ne peut pas être reçu par une facture d'achat.")
                         .format(l["item_code"]))
    # Un BL JAMAIS SAISI n'a PAS de fiche à lui (retour du 26/11/2026 : une 3e ligne « À saisir » dans la file, prise pour
    # une nouvelle facture). Sa commande et sa réception naissent ici, rattachées à la facture ; son n° reste lisible
    # sur la réception (« BL du fournisseur ») et dans le commentaire de la facture préparée. Seul un BL coché qui avait
    # déjà sa fiche (photographié à la livraison, pas encore reçu) est reçu SUR cette fiche.
    nom = groupe.get("fiche")
    po = frappe.new_doc("Purchase Order")
    po.supplier = supplier
    po.company = C.COMPANY
    po.transaction_date = nowdate()
    po.schedule_date = nowdate()
    if nom:
        po.custom_fiche_caisse = nom
    po.set_warehouse = achats_magasin.ENTREPOT_ACHATS
    po.disable_rounded_total = 1
    for l in lignes:
        po.append("items", {"item_code": l["item_code"], "qty": l["qty"], "rate": l["rate"], "schedule_date": nowdate(),
                            "warehouse": achats_magasin.ENTREPOT_ACHATS, "item_tax_template": "", "item_tax_rate": "{}"})
    S._sans_taxes(po)
    po.flags.ignore_permissions = True
    po.insert()
    if flt(po.grand_total) != flt(po.net_total):
        S._sans_taxes(po)
        po.save()
    if flt(po.grand_total) != flt(po.net_total):
        frappe.throw(_("La commande des articles reçus porte encore une taxe ({0} pour {1} HT) : vérifiez le modèle de taxe "
                       "du fournisseur {2}.").format(po.grand_total, po.net_total, supplier))
    po.submit()   # avec une fiche : hooks de caisse_depenses (lien avec la fiche, réception automatique _po_recu_auto)
    if nom:
        recu = frappe.db.get_value(FICHE, nom, "purchase_receipt")
    else:
        from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
        with _droits_de_preparation():
            pr = make_purchase_receipt(po.name)
        S._sans_taxes(pr)
        if groupe.get("numero") and pr.meta.has_field("supplier_delivery_note"):
            pr.supplier_delivery_note = groupe["numero"]
        pr.flags.ignore_permissions = True
        pr.insert()
        pr.submit()
        recu = pr.name
    if not recu or frappe.db.get_value("Purchase Receipt", recu, "docstatus") != 1:
        # _po_recu_auto ne bloque jamais une commande saisie à la main ; ici, sans réception, pas de facture : tout s'annule.
        frappe.throw(_("La réception des articles du BL {0} n'a pas pu être créée (commande {1}) : rien n'est enregistré. "
                       "Voir le journal des erreurs « Caisse: échec reçu auto ».").format(groupe.get("numero") or "—", po.name))
    return {"fiche": nom, "numero_bl": groupe.get("numero") or "", "purchase_order": po.name, "purchase_receipt": recu,
            "ht": flt(frappe.db.get_value("Purchase Receipt", recu, "net_total"), 3), "lignes": lignes}


def _ligne_taxe(pi, compte, montant, description):
    pi.append("taxes", {"charge_type": "Actual", "account_head": compte, "tax_amount": montant, "description": description,
                        "category": "Total", "add_deduct_tax": "Add", "cost_center": pi.get("cost_center") or None})


def brouillon(supplier: str, receptions: list, ctx: dict) -> dict:
    """La facture d'achat EN BROUILLON, construite depuis les réceptions (le stock est déjà entré : « Mettre à jour le
    stock » reste décoché), aux prix des réceptions. Taxes : la TVA de la facture, le timbre, l'arrondi (partage_hors_ht).
    → {purchase_invoice, ht, tva, timbre, arrondi, ecart, ttc}."""
    from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice

    from customization_app import caisse_depenses as C, caisse_stock_partiel as S

    pi = None
    with _droits_de_preparation():
        for recu in receptions:
            pi = make_purchase_invoice(recu, target_doc=pi)
    if not pi or not pi.get("items"):
        frappe.throw(_("Aucune ligne à facturer sur les réceptions {0}.").format(", ".join(receptions)))
    pi.supplier = supplier
    pi.bill_no = (ctx.get("numero_facture") or "").strip()
    pi.bill_date = ctx.get("date_facture") or None
    pi.update_stock = 0
    pi.disable_rounded_total = 1
    # Les taxes sont celles de la FACTURE, pas celles des réceptions (un BL saisi par le comptable porte sa TVA, une
    # réception née ici n'en porte pas) : on repart de zéro, modèles de taxe d'article neutralisés.
    S._sans_taxes(pi)
    ht = flt(pi.net_total, 3)
    tva = flt(ctx.get("tva"), 3)
    part = partage_hors_ht(ctx.get("montant"), tva, ht, ctx.get("timbre"), len(pi.items))
    if tva > 0:
        sept = flt(ctx.get("taux_tva")) == 7
        _ligne_taxe(pi, C.COMPTE_TVA_7 if sept else C.COMPTE_TVA_19, tva, "TVA 7%" if sept else "TVA 19%")
    if part["timbre"] > 0:
        _ligne_taxe(pi, COMPTE_TIMBRE, part["timbre"], "Timbre Fiscal")
    if abs(part["arrondi"]) >= 0.0005:
        arrondi = frappe.db.get_value("Company", C.COMPANY, "round_off_account")
        if arrondi:
            _ligne_taxe(pi, arrondi, part["arrondi"], "Arrondi (prix unitaires à 3 décimales)")
    pi.run_method("set_missing_values")
    pi.calculate_taxes_and_totals()
    pi.set("payment_schedule", [])
    pi.append("payment_schedule", {"due_date": pi.get("due_date") or nowdate(), "invoice_portion": 100,
                                   "payment_amount": flt(pi.grand_total, 3),
                                   "mode_of_payment": mode_echeancier(ctx.get("mode"))})
    try:
        pi.set_advances()   # les avances des BL payés à la livraison (paiements sur leur commande)
    except Exception:
        pass
    pi.flags.ignore_permissions = True
    pi.insert()
    return {"purchase_invoice": pi.name, "ht": flt(pi.net_total, 3), "tva": tva, "timbre": part["timbre"],
            "arrondi": part["arrondi"], "ecart": part["ecart"], "ttc": flt(pi.grand_total, 3)}


def traiter(fiche_facture: str, supplier: str, bls_couverts, articles, ctx: dict) -> dict:
    """Dans la transaction de caisse_depenses.creer, après la fiche de la facture et son avance : contrôle les BL cochés,
    reçoit les lignes qui ne le sont pas, prépare la facture d'achat et y rattache toutes les fiches.
    `ctx` : {fournisseur, numero_facture, date_facture, montant, tva, taux_tva, timbre, description, photo, coins,
    cites (BL lus sur la facture), mode}.
    → {purchase_invoice, bls_factures: [...], bls_recus: [...], ecart, …}."""
    from customization_app import caisse_bl as B, caisse_depenses as C

    bls = valider(bls_couverts, supplier)
    lignes = lignes_a_recevoir(articles)
    motif = controler_lignes(lignes)
    if motif:
        frappe.throw(_("Facture d'achat : {0}.").format(motif), title=_("Article à choisir"))
    # Une ligne imprimée sous la rubrique d'un BL coché DÉJÀ REÇU ne se reçoit pas une 2e fois.
    recus = [b for b in bls if b["purchase_receipt"]]
    for l in lignes:
        cle = B.cle_numero_bl(l["bl_cle"]) if l["bl_cle"] else ""
        deja = next((b for b in recus if cle and cle in b["cles"]), None)
        if deja:
            frappe.throw(_("La ligne « {0} » est sous le BL {1}, déjà reçu en stock : elle ne se reçoit pas une 2e fois. "
                           "Décochez ce BL s'il n'est pas le bon.").format(l["designation"] or l["item_code"], deja["numero_bl"]))
    sans_recu = {cle: b["fiche"] for b in bls if not b["purchase_receipt"] for cle in b["cles"]}
    groupes = grouper(lignes, ctx.get("cites"), sans_recu)
    servis = {g["fiche"] for g in groupes if g["fiche"]}
    for b in bls:
        if not b["purchase_receipt"] and b["fiche"] not in servis:
            frappe.throw(_("Le BL {0} ({1}) n'a pas de réception et la facture ne porte aucune ligne à recevoir pour lui : "
                           "décochez-le, ou voyez le comptable.").format(b["numero_bl"] or "?", b["fiche"]))
        if not b["purchase_receipt"] and b["purchase_order"]:
            frappe.throw(_("Le BL {0} a déjà une commande ({1}) sans réception : voir le comptable.")
                         .format(b["numero_bl"] or "?", b["purchase_order"]))
    if not recus and not groupes:
        frappe.throw(_("Rien à facturer : ni BL déjà reçu coché, ni article à recevoir."))
    doubles = bls_en_double(groupes, _fiches_bl(supplier), [b["fiche"] for b in bls])
    if doubles:
        frappe.throw("<br>".join(_("Le BL {0} est déjà saisi (fiche {1} : {2}) : ses articles ne se reçoivent pas une 2e fois. "
                                   "Cochez ce BL, ou voyez le comptable.").format(x["numero"], x["fiche"], x["motif"])
                                 for x in doubles), title=_("BL déjà saisi"))
    deja = bls_deja_recus_sans_fiche(groupes, frappe.get_all(
        "Purchase Receipt", filters={"supplier": supplier, "docstatus": 1, "supplier_delivery_note": ["is", "set"]},
        fields=["name", "supplier_delivery_note"]))
    if deja:
        frappe.throw("<br>".join(_("Le BL {0} est déjà reçu en stock (réception {1}) : ses articles ne se reçoivent pas "
                                   "une 2e fois. Voyez le comptable.").format(x["numero"], x["reception"]) for x in deja),
                     title=_("BL déjà reçu"))
    nouveaux = [recevoir(g, supplier, ctx) for g in groupes]
    receptions = [b["purchase_receipt"] for b in recus] + [n["purchase_receipt"] for n in nouveaux]
    facture = brouillon(supplier, receptions, ctx)
    if facture["ecart"] and not ecart_admis(facture["ecart"], ctx.get("montant")):
        frappe.throw(_("Les réceptions font {0} HT ; avec la TVA ({1}) et le timbre, il reste un écart de {2} avec le TTC de la "
                       "facture ({3}). Vérifiez les quantités et les prix des lignes, et que tous les BL de la facture sont "
                       "cochés ; rien n'est enregistré.").format(facture["ht"], facture["tva"], facture["ecart"], ctx.get("montant")),
                     title=_("La facture ne tombe pas juste"))
    pi = facture["purchase_invoice"]
    # Le rattachement (hook on_update de la facture : fiche de la facture par son n°, fiches BL par leurs réceptions) est
    # vérifié ici et complété si besoin : c'est lui qui fera passer chaque fiche « Saisie » et l'avance en paiement.
    fiches = [fiche_facture] + [b["fiche"] for b in bls] + [n["fiche"] for n in nouveaux if n["fiche"]]
    for n in nouveaux:
        # Le scan de la facture suit la commande et la réception nées ici (elles n'ont pas de fiche à elles).
        if not n["fiche"]:
            C._copier_justificatifs(fiche_facture, "Purchase Receipt", n["purchase_receipt"])
    for nom in dict.fromkeys(fiches):
        if frappe.db.get_value(FICHE, nom, "purchase_invoice") != pi:
            frappe.db.set_value(FICHE, nom, "purchase_invoice", pi, update_modified=False)
            C._copier_justificatifs(nom, "Purchase Invoice", pi)
    numero = (ctx.get("numero_facture") or "").strip() or "?"
    for b in recus:
        frappe.get_doc(FICHE, b["fiche"]).add_comment(
            "Comment", _("🧾 Facturé par la facture {0} (facture d'achat {1}, préparée par la caisse) — réception {2}, "
                         "rien racheté.").format(numero, pi, b["purchase_receipt"]))
    detail = []
    if recus:
        detail.append(_("BL déjà reçus : {0}").format(", ".join("%s (%s)" % (b["numero_bl"] or "?", b["purchase_receipt"]) for b in recus)))
    if nouveaux:
        detail.append(_("reçus à la facture : {0}").format(", ".join("%s (%s)" % (n["numero_bl"] or _("sans BL"), n["purchase_receipt"])
                                                                     for n in nouveaux)))
    if abs(facture["arrondi"]) >= 0.0005:
        detail.append(_("arrondi {0}").format(facture["arrondi"]))
    if facture["ecart"]:
        detail.append(_("⚠ écart de {0} avec le TTC de la facture : à vérifier").format(facture["ecart"]))
    frappe.get_doc("Purchase Invoice", pi).add_comment(
        "Comment", _("🧾 Préparée par la caisse (fiche {0}, saisie par {1}) depuis les réceptions — {2}. À vérifier et valider.")
        .format(fiche_facture, frappe.session.user, " ; ".join(detail)))
    return dict(facture,
                bls_factures=[{"fiche": b["fiche"], "numero_bl": b["numero_bl"], "recu_doc": b["purchase_receipt"],
                               "cles": b["cles"], "recu": b["recu"]} for b in recus],
                bls_recus=[{"fiche": n["fiche"], "numero_bl": n["numero_bl"], "purchase_order": n["purchase_order"],
                            "purchase_receipt": n["purchase_receipt"], "ht": n["ht"], "lignes": n["lignes"]} for n in nouveaux])
