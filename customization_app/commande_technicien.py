"""La commande simplifiée du technicien en écran simplifié, depuis la clôture d'une intervention (18/11/2026).

« Pour les techniciens en mode simple, quand il ouvre une nouvelle commande : une commande plus simple, dans laquelle
le client est déjà prédéfini, la date aussi, le choix des articles plus simple et principalement des articles de
son stock ; il peut ajouter un article hors de son stock, mais il faut qu'il le mentionne ; les images, le prix et
la quantité ; et il aura la partie termes de paiement. »

Le bouton « ➕ Créer une nouvelle commande » de la clôture ouvrait le formulaire complet de la commande — trop lourd
sur un téléphone. Pour lui, il ouvre un écran en trois gestes (commande_technicien.js) : ses articles, le total, le
règlement. Ce module ne réinvente AUCUN calcul : il fabrique la commande comme le formulaire le ferait (ERPNext pose
la liste de prix du client, les règles de prix, la TVA ; nos hooks posent la marge minimale et la majoration carte)
et la valide sous l'identité du technicien — les paiements naissent donc comme pour toute commande d'un employé.

  - `articles`  : SON stock d'abord (et la main d'œuvre) ; « hors de mon stock » est un choix explicite — la ligne
                  est alors servie par le magasin, marquée sur l'écran et tracée en commentaire de la commande ;
  - `apercu`    : le total exact, tel que la commande le portera — elle est enregistrée puis défaite dans la même
                  transaction (point de reprise), rien ne reste ;
  - `creer`     : la commande, validée, liée à la tâche.
Ristourne et avoir du client ne sont PAS gérés ici : le formulaire complet reste accessible depuis l'écran.
"""
from __future__ import annotations

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

MODE_CARTE = "Carte de crédit"
MODES = ("Espèces", "Chèque", MODE_CARTE, "Virement", "Traite bancaire LC", "Dette non payée")
#: ce que chaque moyen exige — les mêmes règles que le bloc de paiement de la commande (sales_order_paiement.js)
REGLES = {
    "Chèque": {"piece": "N° du chèque (7 chiffres)", "format": r"^\d{7}$", "requise": True, "banque": True},
    MODE_CARTE: {"piece": "N° de transaction (ticket du terminal)", "requise": True},
    "Virement": {"piece": "Référence du virement"},
    "Traite bancaire LC": {"piece": "N° de la traite", "banque": True},
}
CHAMP_PIECE, CHAMP_BANQUE = "custom__n_chèque__transaction", "custom_banque"
TOLERANCE = 0.005
PAR_PAGE = 30
RECHERCHE_MIN = 2            # lettres à taper avant que la liste d'articles s'affiche (demande du 18/11/2026)


# ── Règles pures ─────────────────────────────────────────────────────────────

def lignes_demandees(articles: list, stock: dict, services=()) -> tuple[list, list]:
    """Les lignes de la commande depuis le panier. -> (lignes, erreurs). PURE.

    `articles` : [{item_code, qty, hors_stock}] ; `stock` : {code: quantité dans SON entrepôt} ; `services` : la
    main d'œuvre, jamais comptée dans un stock. Une ligne de son stock ne dépasse pas ce qu'il a ; une ligne « hors
    de mon stock » n'est pas contrôlée (le magasin la sert) mais doit avoir été marquée comme telle."""
    cumul, ordre = {}, []
    for a in articles or []:
        code, qte = str(a.get("item_code") or "").strip(), flt(a.get("qty"))
        if not code:
            continue
        cle = (code, bool(cint(a.get("hors_stock"))))
        if cle not in cumul:
            ordre.append(cle)
        cumul[cle] = cumul.get(cle, 0.0) + qte
    lignes, erreurs = [], []
    for code, hors in ordre:
        qte = cumul[(code, hors)]
        if qte <= 0:
            erreurs.append(_("{0} : quantité invalide.").format(code))
            continue
        service = code in services
        if not hors and not service and qte > flt(stock.get(code)) + 1e-9:
            erreurs.append(_("{0} : {1} demandé, {2} dans votre stock. Diminuez la quantité, ou ajoutez-le "
                             "« hors de mon stock ».").format(code, _nombre(qte), _nombre(stock.get(code))))
            continue
        lignes.append({"item_code": code, "qty": qte, "hors_stock": bool(hors) and not service, "service": service})
    if not lignes and not erreurs:
        erreurs.append(_("Ajoutez au moins un article."))
    return lignes, erreurs


def echeances_demandees(paiements: list, total: float) -> tuple[list, list]:
    """Le règlement saisi -> (lignes d'échéancier, erreurs). PURE.

    `paiements` : [{mode, montant, numero, banque}]. Le montant d'une carte est la part de la commande qu'elle
    règle : la majoration s'y ajoute à la validation (majoration_carte). La somme doit faire le total."""
    lignes, erreurs = [], []
    for p in paiements or []:
        mode, montant = str(p.get("mode") or "").strip(), flt(p.get("montant"))
        if mode not in MODES:
            erreurs.append(_("Moyen de paiement non permis : {0}.").format(mode or "?"))
            continue
        if montant <= 0:
            erreurs.append(_("{0} : montant invalide.").format(mode))
            continue
        regle = REGLES.get(mode) or {}
        numero, banque = str(p.get("numero") or "").strip(), str(p.get("banque") or "").strip()
        if regle.get("requise") and not numero:
            erreurs.append(_("{0} : {1} est obligatoire.").format(mode, regle["piece"]))
            continue
        if numero and regle.get("format") and not re.match(regle["format"], numero):
            erreurs.append(_("{0} : {1}.").format(mode, regle["piece"]))
            continue
        ligne = {"mode_of_payment": mode, "payment_amount": round(montant, 3)}
        if numero:
            ligne[CHAMP_PIECE] = numero
        if banque and regle.get("banque"):
            ligne[CHAMP_BANQUE] = banque
        lignes.append(ligne)
    if not lignes and not erreurs:
        erreurs.append(_("Indiquez comment le client règle."))
    elif lignes and not erreurs and abs(sum(l["payment_amount"] for l in lignes) - flt(total)) > TOLERANCE:
        erreurs.append(_("Les règlements font {0}, la commande {1}.").format(
            _nombre(sum(l["payment_amount"] for l in lignes)), _nombre(total)))
    return lignes, erreurs


def echeances_apercu(paiements: list, total: float) -> list:
    """Le règlement en cours de saisie, s'il couvre déjà la commande — pour montrer le total EXACT avec la majoration
    carte avant même que les numéros de pièce soient saisis. Sinon [] (l'aperçu se fait alors tout en espèces). PURE."""
    lignes = [{"mode_of_payment": str(p.get("mode") or "").strip(), "payment_amount": round(flt(p.get("montant")), 3)}
              for p in (paiements or []) if str(p.get("mode") or "").strip() in MODES and flt(p.get("montant")) > 0]
    if not lignes or abs(sum(l["payment_amount"] for l in lignes) - flt(total)) > TOLERANCE:
        return []
    return lignes


def _nombre(v) -> str:
    return ("%.3f" % flt(v)).rstrip("0").rstrip(".").replace(".", ",") or "0"


# ── Contexte ─────────────────────────────────────────────────────────────────

def _contexte(tache: str):
    """(tâche, employé) — réservé au technicien à écran simplifié, sur SES interventions."""
    from customization_app import sous_traitant as ST

    emp = ST.employe_courant()
    if not emp:
        frappe.throw(_("Réservé aux techniciens à écran simplifié."), frappe.PermissionError)
    doc = frappe.get_doc(ST.DOCTYPE_TACHE, tache)
    if doc.get("custom_choix_du_staff") != emp.name and not ST._est_supervision():
        frappe.throw(_("Cette intervention ne vous est pas affectée."), frappe.PermissionError)
    if not doc.get("custom_client"):
        frappe.throw(_("Cette intervention n'a pas de client : demandez au bureau de le renseigner."))
    return doc, emp


def _magasin() -> str:
    from customization_app import stock_entrepots
    return stock_entrepots.magasin()


def _liste_de_prix(client: str) -> str:
    return (frappe.db.get_value("Customer", client, "default_price_list")
            or frappe.db.get_single_value("Selling Settings", "selling_price_list") or "Vente standard")


def _prix(codes: list, liste: str) -> dict:
    """Le prix catalogue du jour de chaque article dans la liste du client (indicatif : l'aperçu donne l'exact)."""
    from customization_app.accueil_technicien import prix_du_jour

    if not codes:
        return {}
    par_code = {}
    for p in frappe.get_all("Item Price", filters={"price_list": liste, "selling": 1, "item_code": ["in", codes]},
                            fields=["item_code", "price_list_rate", "uom", "valid_from", "valid_upto", "customer"]):
        par_code.setdefault(p.item_code, []).append(p)
    unites = dict(frappe.get_all("Item", filters={"name": ["in", codes]}, fields=["name", "stock_uom"], as_list=True))
    jour = str(getdate(nowdate()))
    return {c: prix_du_jour(par_code.get(c), unites.get(c), jour) for c in codes}


@frappe.whitelist()
def preparer(tache):
    """Ce que l'écran affiche d'emblée : le client, la date, les moyens de paiement, la main d'œuvre du type."""
    from customization_app import majoration_carte as MC
    from customization_app import sous_traitant as ST

    doc, emp = _contexte(tache)
    liste = _liste_de_prix(doc.custom_client)
    mo = ST.main_oeuvre_par_defaut(doc.get("custom_type_dintervention"))
    if mo:
        mo["prix"] = _prix([mo["item_code"]], liste).get(mo["item_code"]) or mo.get("prix") or 0
    return {"tache": doc.name, "client": doc.custom_client,
            "nom_client": frappe.db.get_value("Customer", doc.custom_client, "customer_name") or doc.custom_client,
            "date": nowdate(), "type": doc.get("custom_type_dintervention") or "", "entrepot": emp.custom_warehouse or "",
            "liste_prix": liste, "modes": list(MODES), "regles": REGLES, "mode_carte": MODE_CARTE,
            "carte": MC.taux_pour_ecran(), "main_oeuvre": mo,
            # une remise se justifie, comme sur la clôture mobile du sous-traitant (mêmes motifs, même règle)
            "motifs_remise": list(ST.MOTIFS_REMISE), "commentaire_min": ST.COMMENTAIRE_MIN, "recherche_min": RECHERCHE_MIN,
            # les banques proposées : celles du champ de l'échéancier de la commande
            "banques": [b for b in (frappe.get_meta("Payment Schedule").get_field(CHAMP_BANQUE).options or "").split("\n") if b]
                       if frappe.get_meta("Payment Schedule").has_field(CHAMP_BANQUE) else []}


@frappe.whitelist()
def articles(tache, txt=None, hors_stock=0, debut=0):
    """Les articles proposés : SON stock (et la main d'œuvre), ou — choix explicite — ceux qu'il n'a pas."""
    from customization_app import sous_traitant as ST
    from customization_app.accueil_technicien import mots

    doc, emp = _contexte(tache)
    if len(str(txt or "").strip()) < RECHERCHE_MIN:
        return {"suite": False, "articles": [], "trop_court": True}     # la liste ne s'affiche qu'à partir de 2 lettres
    stock = ST._mon_stock(emp)
    hors = bool(cint(hors_stock))
    conditions, valeurs = ["i.disabled = 0", "i.has_variants = 0", "i.is_sales_item = 1"], []
    for mot in mots(txt):
        motif = "%" + mot.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append("(i.item_code like %s or i.item_name like %s)")
        valeurs += [motif, motif]
    services = ST._codes_main_oeuvre()
    if hors:
        exclus = list(set(stock) | set(services)) or [""]
        conditions.append("i.name not in (%s)" % ", ".join(["%s"] * len(exclus)))
        valeurs += exclus
    else:
        gardes = list(set(stock) | set(services)) or [""]
        conditions.append("i.name in (%s)" % ", ".join(["%s"] * len(gardes)))
        valeurs += gardes
    lignes = frappe.db.sql(
        "select i.name, i.item_name, i.image, i.stock_uom from `tabItem` i where " + " and ".join(conditions)
        + " order by i.item_name, i.name limit %s offset %s", valeurs + [PAR_PAGE + 1, max(0, cint(debut))], as_dict=True)
    suite, lignes = len(lignes) > PAR_PAGE, lignes[:PAR_PAGE]
    codes = [l.name for l in lignes]
    prix = _prix(codes, _liste_de_prix(doc.custom_client))
    au_magasin = {}
    if hors and codes:
        au_magasin = dict(frappe.get_all("Bin", filters={"warehouse": _magasin(), "item_code": ["in", codes]},
                                         fields=["item_code", "actual_qty"], as_list=True))
    return {"suite": suite, "articles": [{
        "item_code": l.name, "item_name": l.item_name or l.name, "image": l.image or "", "uom": l.stock_uom or "",
        "service": l.name in services, "hors_stock": hors,
        "stock": None if l.name in services else flt(au_magasin.get(l.name) if hors else stock.get(l.name)),
        "prix": prix.get(l.name)} for l in lignes]}


# ── La commande ──────────────────────────────────────────────────────────────

def _lire(valeur) -> list:
    return (json.loads(valeur) if isinstance(valeur, str) else valeur) or []


def _lire_dict(valeur) -> dict:
    valeur = (json.loads(valeur) if isinstance(valeur, str) and valeur.strip() else valeur) or {}
    return valeur if isinstance(valeur, dict) else {}


def _commande(doc, emp, lignes: list, remise=None):
    """La commande telle que le formulaire la poserait : client et date d'office, ses articles depuis SON entrepôt,
    les lignes « hors de mon stock » depuis le magasin. ERPNext pose la liste de prix du client, les prix et la TVA."""
    from customization_app import cloture_tache as C

    pre = (C.exigences(doc.name) or {}).get("nouvelle_commande") or {}
    jour = nowdate()
    so = frappe.new_doc("Sales Order")
    so.update({"customer": doc.custom_client, "transaction_date": jour, "delivery_date": jour,
               "company": frappe.defaults.get_global_default("company")})
    for champ in ("custom_type_de_transaction", "taxes_and_charges"):
        if pre.get(champ):
            so.set(champ, pre[champ])
    magasin = _magasin()
    if emp.custom_warehouse and not any(l["hors_stock"] for l in lignes):
        so.set_warehouse = emp.custom_warehouse
    for l in lignes:
        so.append("items", {"item_code": l["item_code"], "qty": l["qty"], "delivery_date": jour,
                            "warehouse": magasin if l["hors_stock"] else (emp.custom_warehouse or magasin)})
    so.run_method("set_missing_values")
    if not so.get("taxes") and so.get("taxes_and_charges"):
        so.append_taxes_from_master()
    so.calculate_taxes_and_totals()
    if remise and flt((remise or {}).get("montant")) > 0:
        # La même règle que la clôture mobile du sous-traitant : sur le total, jamais plus que lui, motif et
        # commentaire obligatoires — gardés sur la commande.
        from customization_app import sous_traitant as ST
        ST._appliquer_remise(so, remise)
    return so


def _poser_echeancier(so, echeances: list):
    """L'échéancier de la commande = le règlement saisi, tout au jour J (echeances_meme_jour)."""
    total = flt(so.grand_total) or 1
    so.payment_terms_template = None
    so.set("payment_schedule", [])
    for e in echeances:
        so.append("payment_schedule", dict(e, due_date=nowdate(), invoice_portion=flt(e["payment_amount"]) * 100 / total))


def _lignes_valides(doc, emp, articles) -> list:
    from customization_app import sous_traitant as ST

    lignes, erreurs = lignes_demandees(_lire(articles), ST._mon_stock(emp), ST._codes_main_oeuvre())
    if erreurs:
        frappe.throw("<br>".join(erreurs), title=_("Articles"))
    return lignes


def _resume(so, lignes: list) -> dict:
    hors = {l["item_code"] for l in lignes if l["hors_stock"]}
    majoration = flt(so.get("custom_majoration_carte"), 3)
    return {"total": flt(so.grand_total, 3), "ht": flt(so.net_total, 3),
            # `base` : le total SANS la majoration carte — c'est lui que les règlements saisis doivent couvrir.
            "base": flt(flt(so.grand_total) - majoration, 3), "remise": flt(so.get("discount_amount"), 3),
            "taxes": flt(so.total_taxes_and_charges, 3), "majoration": majoration,
            "lignes": [{"item_code": i.item_code, "item_name": i.item_name, "qty": flt(i.qty), "prix": flt(i.rate, 3),
                        "montant": flt(i.amount, 3), "hors_stock": i.item_code in hors} for i in so.items],
            "echeances": [{"mode": e.mode_of_payment, "montant": flt(e.payment_amount, 3)} for e in so.payment_schedule]}


def _messages() -> list:
    """Ce que les règles ont dit en chemin (prix relevé pour la marge, etc.) — rendu à l'écran, pas en fenêtres."""
    from frappe.utils import strip_html

    textes = []
    for m in frappe.local.message_log or []:
        m = m if isinstance(m, dict) else frappe.parse_json(m)
        t = " ".join(strip_html(str(m.get("message") or "")).split())
        if t and t not in textes:
            textes.append(t)
    frappe.clear_messages()
    return textes


@frappe.whitelist()
def apercu(tache, articles=None, remise=None, paiements=None):
    """Le total EXACT du panier : la commande est enregistrée en brouillon puis défaite dans la même transaction
    (point de reprise) — toutes les règles ont joué (prix du client, marge minimale, TVA, remise), rien ne reste.
    Si le règlement saisi couvre déjà la commande, il est posé aussi : la majoration carte s'applique alors à la
    commande comme dans le formulaire (prix relevés, total augmenté) et l'écran montre le vrai montant."""
    doc, emp = _contexte(tache)
    lignes = _lignes_valides(doc, emp, articles)
    remise = _lire_dict(remise)
    frappe.db.savepoint("apercu_commande_technicien")
    try:
        so = _commande(doc, emp, lignes, remise)
        base = flt(so.grand_total, 3)
        # Une vente s'enregistre avec un moyen de paiement : à défaut de règlement complet, tout en espèces.
        _poser_echeancier(so, echeances_apercu(_lire(paiements), base)
                          or [{"mode_of_payment": "Espèces", "payment_amount": base}])
        so.insert()
        resume = _resume(so, lignes)
    finally:
        frappe.db.rollback(save_point="apercu_commande_technicien")
    resume["notes"] = _messages()
    return resume


@frappe.whitelist(methods=["POST"])
def creer(tache, articles=None, paiements=None, remise=None):
    """La commande, VALIDÉE (une tâche ne se clôture pas sur un brouillon), liée à la tâche. Sous l'identité du
    technicien : les paiements naissent comme pour toute commande d'un employé."""
    doc, emp = _contexte(tache)
    if doc.get("commande_client") and frappe.db.get_value("Sales Order", doc.commande_client, "docstatus") != 2:
        frappe.throw(_("Cette intervention a déjà une commande : {0}.").format(doc.commande_client))
    lignes = _lignes_valides(doc, emp, articles)
    so = _commande(doc, emp, lignes, _lire_dict(remise))
    echeances, erreurs = echeances_demandees(_lire(paiements), so.grand_total)
    if erreurs:
        frappe.throw("<br>".join(erreurs), title=_("Règlement"))
    _poser_echeancier(so, echeances)
    so.insert()
    so = frappe.get_doc("Sales Order", so.name)
    so.submit()
    frappe.db.set_value("Tache de travail", doc.name, "commande_client", so.name, update_modified=False)
    hors = [l for l in lignes if l["hors_stock"]]
    if hors:
        frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": "Sales Order",
                        "reference_name": so.name,
                        "content": _("📦 Articles pris HORS du stock de {0} (à sortir du magasin) : {1}").format(
                            frappe.utils.escape_html(emp.employee_name),
                            ", ".join("%s × %s" % (frappe.utils.escape_html(l["item_code"]), _nombre(l["qty"])) for l in hors))
                        }).insert(ignore_permissions=True)
    from customization_app import sous_traitant as ST
    ST._tracer_remise(so)
    so.reload()
    resume = _resume(so, lignes)
    resume.update({"name": so.name, "notes": _messages()})
    return resume
