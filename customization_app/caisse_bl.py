"""Les BL DÉJÀ REÇUS qu'une facture couvre, reconnus à la saisie de la dépense (demande du 25/11/2026).

POURQUOI. Une facture « à terme » regroupe les BL d'un fournisseur : « TOTAL BL N° B26001620 DU 07/08/2026 »,
« TOTAL BL N° B26001723 DU 20/08/2026 »… Certains de ces BL sont déjà dans l'outil : photographiés à la livraison
(« Facture d'achat » sur BL → fiche « Facture Achat a Saisir », commande, réception : le STOCK EST ENTRÉ). Le
25/11/2026, la facture MH2C F26000463 a été saisie en « Dépense avec facture / En partie achat » : la fenêtre ne
savait rien des BL, la réduction déjà reçue le 07/09 avec le BL B26001723 a été RACHETÉE (commande et réception
neuves, +5 une deuxième fois) et la fiche du BL est restée « À saisir ».

LE MÉCANISME (sa décision du 25/11/2026 : « vas-y »).
  · L'analyse de la facture lit les BL qu'elle cite (n°, date, sous-totaux) — même appel IA que la classification.
  · Chaque n° cité est cherché parmi les BL OUVERTS DU MÊME FOURNISSEUR (fiches est_bl « À saisir », sans facture
    d'achat). Le n° imprimé est la clé : un BL TROUVÉ par son n° est coché d'office ; un BL dont le montant tombe
    sur un sous-total cité est « possible », un BL dont les articles et quantités sont tous sur la facture est
    « probable » — proposés, jamais cochés ; les autres BL ouverts sont listés, cochables à la main.
  · PLUSIEURS BL par facture : la même opération répétée BL par BL, dans la même saisie et la même transaction.
  · À l'enregistrement, chaque BL coché : AUCUN nouvel achat (le stock est déjà là) ; sa réception est SOLDÉE à sa
    valeur (Dr « Stock reçus mais non facturés » / Cr compte de charge, une écriture « Réception soldée — » par BL) ;
    sa commande et sa réception sont fermées ; sa fiche passe « Saisie » avec le n° de la facture. La dépense, elle,
    reste ENTIÈRE (une écriture, tout le HT, toute la TVA) : la facture est payée telle qu'elle est.
  · L'écart entre la valeur du reçu et le HT facturé (le timbre réparti dans le prix du BL : 9,750 reçus, 8,750
    facturés) sort de la charge avec le solde (choix proposé et retenu le 25/11/2026).
  · Bloqués en v1, avec leur motif : BL payé à la livraison, BL déjà couvert par une dépense ou une facture d'achat,
    commande en brouillon ou sans réception, réception fermée ou déjà facturée en partie, autre fournisseur.
  · Un article déjà reçu sur un BL coché ne peut pas être remis en stock par la même facture (refus).
  · Défaire : `rouvrir_bls`, réservé au comptable.

Les fonctions pures (clés, rapprochement, contrôles) n'utilisent pas `frappe.utils.flt` : hors site, il rend 0.
"""
from __future__ import annotations

import json
import re
import unicodedata

import frappe

PRECISION = 3
#: Préfixe des écritures qui soldent une réception (≠ « Dépense caisse — » : le rapport de caisse, l'argent liquide
#: et l'anti-doublon ne lisent que ce dernier — une écriture sans ligne de caisse n'y a pas sa place).
PREFIXE_SOLDE = "Réception soldée — "
#: Écart toléré entre le TTC cité par la facture pour un BL et le montant de sa fiche : au-delà d'un dinar (le timbre),
#: le BL reste coché mais l'employé confirme.
TOLERANCE_TTC = 1.0
#: Contrôle global : la valeur des réceptions soldées et de la part stock nouvelle ne dépasse pas le HT de la facture,
#: à 1 DT par BL (timbre réparti dans le prix) et 1 % près.
TOLERANCE_PAR_BL = 1.0
TOLERANCE_RELATIVE = 0.01

TROUVE, POSSIBLE, PROBABLE, AUTRE = "trouve", "possible", "probable", "autre"
CONFORME, AU_TIMBRE, A_VERIFIER = "conforme", "timbre", "a_verifier"


def _m(valeur) -> float:
    try:
        return round(float(valeur or 0), PRECISION)
    except (TypeError, ValueError):
        return 0.0


def _dt(valeur) -> str:
    entier, _, millimes = f"{_m(valeur):,.3f}".replace(",", " ").partition(".")
    return f"{entier},{millimes}"


# ---------------------------------------------------------------------------------------------------------------- pur
_RX_DATE = re.compile(r"\s+DU\s+\d{1,2}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*\d{2,4}.*$")
_RX_PREFIXES = re.compile(r"^(?:TOTAL\b|BON\s+DE\s+LIVRAISON\b|B\s*\.?\s*L\s*\.?(?=[\s\d:N.#-]|$)|N\s*O\s*\.?|NUM(?:ERO)?\b|[:#.\s-])\s*")


def cle_numero_bl(texte) -> str:
    """Le n° d'un BL réduit à ce qui l'identifie. PURE.
    « TOTAL BL N° B26001723 DU 20/08/2026 », « BL N° B26001723 », « bl n°b26001723 » et « B26001723 » → « B26001723 » ;
    « BL2615138 », « BL 2615138 », « N° BL2615138 », « B.L. 0002615138 » → « 2615138 »."""
    # « N° » : le signe ° n'a pas d'équivalent ASCII et disparaîtrait (« N B26001723 » → « NB26001723 ») — il devient O.
    txt = re.sub(r"[°º]", "O", str(texte or ""))
    txt = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode().upper().strip()
    txt = _RX_DATE.sub("", txt)
    precedent = None
    while txt and txt != precedent:
        precedent = txt
        txt = _RX_PREFIXES.sub("", txt, count=1)
    txt = re.sub(r"[^A-Z0-9]", "", txt)
    return txt.lstrip("0") or txt


def cles_fiche(numero_bl, numero_facture=None) -> set:
    """Les clés sous lesquelles une fiche de BL peut être citée. PURE. Le n° du BL a longtemps été recopié dans le
    n° de facture de la fiche (FAS-0319 : numero_facture = numero_bl = B26001723) : les deux comptent."""
    return {c for c in (cle_numero_bl(numero_bl), cle_numero_bl(numero_facture)) if c}


def paye_a_la_livraison(f: dict) -> bool:
    """Le BL a déjà été payé (à la livraison, ou par une avance sur sa commande). PURE."""
    return bool((f.get("mode_paiement") or "Pas payé") != "Pas payé" or f.get("journal_entry")
                or f.get("payment_entry") or _m(f.get("po_advance")) > 0)


def motif_non_traitable(f: dict, supplier=None, pour: str = "depense") -> str:
    """Pourquoi ce BL ne peut pas être soldé par la dépense — "" s'il le peut. PURE.

    `pour` = "achat" (26/11/2026, caisse_facture_bl) : le BL est couvert par une vraie FACTURE D'ACHAT, préparée depuis
    sa réception. Un BL payé à la livraison y est admis : son avance s'impute sur la facture (c'est le circuit
    d'origine des BL) ; en dépense il reste bloqué, la facture le paierait une 2e fois.

    `f` : la fiche et ce qu'on sait de sa commande / réception — {est_bl, supplier, statut, mode_paiement,
    journal_entry, payment_entry, purchase_invoice, depense_a_payer, purchase_order, po_docstatus, po_status,
    po_advance, purchase_receipt, pr_docstatus, pr_status, pr_per_billed}."""
    if not int(f.get("est_bl") or 0):
        return "ce n’est pas un bon de livraison"
    if supplier and f.get("supplier") and f.get("supplier") != supplier:
        return "autre fournisseur"
    if f.get("purchase_invoice"):
        return "déjà rattaché à la facture d’achat %s" % f["purchase_invoice"]
    if f.get("depense_a_payer"):
        return "déjà couvert par la dépense %s" % f["depense_a_payer"]
    if (f.get("statut") or "À saisir") != "À saisir":
        return "fiche « %s »" % f.get("statut")
    if pour != "achat" and paye_a_la_livraison(f):
        return "payé à la livraison : voir le comptable (la facture ne doit pas le payer une 2e fois)"
    if f.get("purchase_order"):
        if int(f.get("po_docstatus") or 0) != 1:
            return "commande %s en brouillon : voir le comptable" % f["purchase_order"]
        if not f.get("purchase_receipt") or int(f.get("pr_docstatus") or 0) != 1:
            return "commande %s sans réception : voir le comptable" % f["purchase_order"]
        if f.get("po_status") == "Closed":
            return "commande %s fermée" % f["purchase_order"]
    if f.get("purchase_receipt"):
        if int(f.get("pr_docstatus") or 0) != 1:
            return "réception %s non validée" % f["purchase_receipt"]
        if f.get("pr_status") == "Closed":
            return "réception %s fermée" % f["purchase_receipt"]
        if _m(f.get("pr_per_billed")) > 0:
            return "réception %s déjà facturée en partie : voir le comptable" % f["purchase_receipt"]
    return ""


def controle_ttc(cite_ttc, montant_fiche) -> str | None:
    """Le TTC cité par la facture pour ce BL, contre le montant de sa fiche. PURE."""
    if not _m(cite_ttc) or not _m(montant_fiche):
        return None
    ecart = abs(_m(cite_ttc) - _m(montant_fiche))
    return CONFORME if ecart <= 0.01 else AU_TIMBRE if ecart <= TOLERANCE_TTC else A_VERIFIER


def rapprocher_bls(cites, ouverts, clos=(), lignes=None) -> dict:
    """Range les BL de ce fournisseur face à la facture. PURE.

    `cites` : [{numero, date, total_ht, total_ttc}] lus sur la facture ;
    `ouverts` : [{name, numero_bl, numero_facture, montant, date_facture, recu (R : valeur du reçu à solder),
                  items: [{item_code, qty}], motif ("" si traitable)}] — BL du fournisseur pas encore couverts ;
    `clos` : [{name, numero_bl, numero_facture, couvert_par}] — BL du fournisseur déjà couverts ;
    `lignes` : [{item_code, qty, code, designation, bl_cle}] lues sur la facture (« En partie achat »), TOUTES et dans
               l'ordre de l'écran : le niveau « probable », et les lignes de la rubrique de chaque BL appariées à sa
               réception (`appariees`, cf. `articles_de_la_rubrique`).
    -> {bls: [carte], inconnus: [cité sans fiche], deja_couverts: [cité déjà couvert], coches: [noms]} ; chaque carte
       porte `recus` (les lignes de sa réception : item_code, item_name, qty) et `appariees`."""
    cites = [dict(c, cle=cle_numero_bl(c.get("numero"))) for c in (cites or []) if cle_numero_bl(c.get("numero"))]
    cartes, pris, cites_servis = [], set(), set()

    def carte(f, niveau, cite=None):
        bloquant = bool(f.get("motif"))
        recus = [{"item_code": i["item_code"], "item_name": i.get("item_name") or "", "qty": _m(i.get("qty"))}
                 for i in f.get("items") or [] if i.get("item_code")]
        cles = cles_fiche(f.get("numero_bl"), f.get("numero_facture"))
        c = {"name": f["name"], "numero_bl": f.get("numero_bl") or f.get("numero_facture") or "",
             "date": str(f.get("date_facture") or "")[:10], "montant": _m(f.get("montant")), "recu": _m(f.get("recu")),
             "niveau": niveau, "motif": f.get("motif") or "", "bloquant": bloquant,
             # Déjà payé à la livraison : en « Facture d'achat » il se facture quand même (son paiement est une avance
             # que la facture reprend) — l'écran prévient de ne payer que le reste.
             "paye_livraison": paye_a_la_livraison(f),
             "coche": niveau == TROUVE and not bloquant, "cite": None, "controle": None, "ecart_ht": None,
             "items": [r["item_code"] for r in recus], "recus": recus,
             "appariees": articles_de_la_rubrique(cles, lignes, recus),
             "cles": sorted(cles)}
        if cite:
            c["cite"] = {k: cite.get(k) for k in ("numero", "date", "total_ht", "total_ttc")}
            c["controle"] = controle_ttc(cite.get("total_ttc"), f.get("montant"))
            if _m(cite.get("total_ht")) and c["recu"]:
                c["ecart_ht"] = _m(c["recu"] - _m(cite.get("total_ht")))
        pris.add(f["name"])
        cartes.append(c)

    # 1. TROUVÉ : le n° imprimé sur la facture est celui de la fiche.
    for cite in cites:
        for f in ouverts or []:
            if f["name"] not in pris and cite["cle"] in cles_fiche(f.get("numero_bl"), f.get("numero_facture")):
                carte(f, TROUVE, cite)
                cites_servis.add(cite["cle"])
                break
    deja = []
    for cite in cites:
        if cite["cle"] in cites_servis:
            continue
        for f in clos or []:
            if cite["cle"] in cles_fiche(f.get("numero_bl"), f.get("numero_facture")):
                deja.append(dict(cite, fiche=f["name"], couvert_par=f.get("couvert_par") or ""))
                cites_servis.add(cite["cle"])
                break
    # 2. POSSIBLE : un sous-total cité (n° inconnu) tombe sur le montant d'une fiche ouverte.
    for cite in cites:
        if cite["cle"] in cites_servis or not _m(cite.get("total_ttc")):
            continue
        f = next((f for f in ouverts or [] if f["name"] not in pris
                  and abs(_m(f.get("montant")) - _m(cite.get("total_ttc"))) <= 0.01), None)
        if f:
            carte(f, POSSIBLE, cite)
            cites_servis.add(cite["cle"])
    # 3. PROBABLE : tous les articles du reçu (même article, même quantité) sont sur la facture.
    sur_facture = {}
    for l in lignes or []:
        if l.get("item_code"):
            sur_facture[l["item_code"]] = _m(sur_facture.get(l["item_code"], 0) + _m(l.get("qty")))
    for f in ouverts or []:
        items = [i for i in f.get("items") or [] if i.get("item_code")]
        if f["name"] in pris or not items or not sur_facture:
            continue
        if all(abs(sur_facture.get(i["item_code"], 0) - _m(i.get("qty"))) < 0.0005 for i in items):
            carte(f, PROBABLE)
    # 4. AUTRE : les autres BL ouverts du fournisseur, cochables à la main.
    for f in ouverts or []:
        if f["name"] not in pris:
            carte(f, AUTRE)
    inconnus = [{k: c.get(k) for k in ("numero", "date", "total_ht", "total_ttc")} for c in cites
                if c["cle"] not in cites_servis]
    return {"bls": cartes, "inconnus": inconnus, "deja_couverts": deja,
            "coches": [c["name"] for c in cartes if c["coche"]]}


def articles_de_la_rubrique(cles, lignes, recus) -> list:
    """Quel article a été REÇU pour chaque ligne imprimée sous la rubrique de ce BL (« TOTAL BL N° … »). PURE.

    Demande du 25/11/2026 : la ligne « REDUCTION LAITON SPECIAL » grisée « déjà reçue (BL B26001723) » montrait encore
    la devinette tuy-028, alors que la réception de ce BL dit BRS-SP-15-12. Même règle SÛRE que l'apprentissage
    (`correspondance_articles.apparier` : une ligne de chaque côté et la même quantité, ou autant de lignes et des
    quantités uniques) — l'écran montre exactement ce que l'enregistrement retiendra.
    `cles` : clés du n° de ce BL ; `lignes` : TOUTES les lignes lues, dans l'ordre de l'écran ; `recus` : [{item_code,
    item_name, qty}] de sa réception. -> [{i (rang de la ligne à l'écran), item_code, item_name}]."""
    from customization_app.correspondance_articles import apparier

    rang = {id(l): i for i, l in enumerate(lignes or []) if isinstance(l, dict)}   # le rang À L'ÉCRAN
    rubrique = [l for l in lignes or [] if isinstance(l, dict) and cles
                and cle_numero_bl(l.get("bl_cle") or l.get("bl")) in cles]
    if not rubrique or not recus:
        return []
    noms = {r["item_code"]: r.get("item_name") or "" for r in recus if r.get("item_code")}
    return sorted(({"i": rang[id(ligne)], "item_code": item_code, "item_name": noms.get(item_code, "")}
                   for ligne, item_code in apparier(rubrique, recus)), key=lambda x: x["i"])


def ligne_deja_recue(ligne: dict, bls_coches: list) -> str | None:
    """Le BL déjà reçu EN STOCK qui contient cette ligne d'articles, ou None. PURE.
    `ligne` : {item_code, bl_cle} ; `bls_coches` : [{numero_bl, cles, items, recu}]. Une ligne lue sous la rubrique d'un
    BL coché est reçue ; sans rubrique, une ligne dont l'article est dans la réception d'un BL coché aussi. Un BL SANS
    réception (fiche photographiée, commande pas encore faite) n'a rien fait entrer en stock : ses lignes restent libres
    d'y être mises par la facture (relecture du 25/11/2026)."""
    cle = cle_numero_bl(ligne.get("bl_cle") or ligne.get("bl"))
    if cle:
        for b in bls_coches or []:
            if cle in set(b.get("cles") or ()):
                return (b.get("numero_bl") or "?") if b.get("recu", True) else None
        return None
    for b in bls_coches or []:
        if b.get("recu", True) and ligne.get("item_code") and ligne.get("item_code") in set(b.get("items") or ()):
            return b.get("numero_bl") or "?"
    return None


def controle_total(somme_recus, stock_ht, ht_total, nb_bls) -> str:
    """"" si la valeur des réceptions soldées et de la part stock nouvelle tient dans le HT de la facture. PURE."""
    limite = _m(ht_total) + TOLERANCE_PAR_BL * int(nb_bls or 0) + TOLERANCE_RELATIVE * _m(ht_total)
    total = _m(_m(somme_recus) + _m(stock_ht))
    if total > limite + 0.0005:
        return ("Les BL cochés (%s HT reçus)%s dépassent le HT de la facture (%s) : un BL ne fait sans doute pas partie "
                "de cette facture." % (_dt(somme_recus), " et la part stock (%s HT)" % _dt(stock_ht) if _m(stock_ht) else "",
                                       _dt(ht_total)))
    return ""


def lignes_solde(recu, compte_charge, compte_srbnb, cc) -> list:
    """L'écriture qui solde une réception : Dr « Stock reçus mais non facturés » / Cr compte de charge. PURE."""
    r = _m(recu)
    return [{"account": compte_srbnb, "debit_in_account_currency": r, "cost_center": cc},
            {"account": compte_charge, "credit_in_account_currency": r, "cost_center": cc}]


def remarque_bls(soldes: list) -> str:
    """La mention de la dépense. PURE. ⚠️ Jamais « BL n° … » : `caisse_depenses.lire_saisie` y lirait que la pièce
    EST un BL (« Corriger » la rouvrirait en BL)."""
    morceaux = []
    for s in soldes or []:
        detail = ("réception %s soldée %s HT" % (s["recu_doc"], _dt(s["recu"]))) if s.get("recu_doc") else "sans réception"
        morceaux.append("%s (%s, %s)" % (s.get("numero_bl") or "?", s["fiche"], detail))
    return "Couvre des BL déjà reçus : " + " ; ".join(morceaux) if morceaux else ""


def libelle_solde(numero_bl, fournisseur, numero_facture) -> str:
    """La description de l'écriture de solde (après le préfixe). PURE."""
    bouts = ["BL %s" % numero_bl if numero_bl else "BL", (fournisseur or "").strip()]
    if numero_facture:
        bouts.append("Fact. %s" % numero_facture)
    return " — ".join(b for b in bouts if b)


def _liste(valeur) -> list:
    if isinstance(valeur, str):
        valeur = valeur.strip()
        return json.loads(valeur) if valeur else []
    return list(valeur or [])


# --------------------------------------------------------------------------------------------------------- avec le site
def compte_srbnb() -> str:
    from customization_app import caisse_stock_partiel as S
    return S.compte_stock_recu_non_facture()


def valeur_recu(recu: str) -> float:
    """Ce que la réception a crédité (net) sur « Stock reçus mais non facturés » : ce qu'il faut solder."""
    if not recu:
        return 0.0
    v = frappe.db.sql("""select coalesce(sum(credit - debit), 0) from `tabGL Entry`
                         where account = %s and voucher_type = 'Purchase Receipt' and voucher_no = %s
                           and is_cancelled = 0""", (compte_srbnb(), recu))[0][0]
    return _m(v)


def _etat_fiche(f, verrou: bool = False) -> dict:
    """La fiche et l'état de sa commande / réception, pour `motif_non_traitable`. `verrou` : lectures verrouillantes
    (FOR UPDATE), qui voient le dernier état commité — sous REPEATABLE READ, une lecture simple ne voit que l'instantané
    pris au début de la transaction (cf. `valider`)."""
    f = dict(f)
    if f.get("purchase_order"):
        po = frappe.db.get_value("Purchase Order", f["purchase_order"], ["docstatus", "status", "advance_paid"],
                                 as_dict=True, for_update=verrou) or {}
        f.update(po_docstatus=po.get("docstatus"), po_status=po.get("status"), po_advance=po.get("advance_paid"))
    if f.get("purchase_receipt"):
        pr = frappe.db.get_value("Purchase Receipt", f["purchase_receipt"], ["docstatus", "status", "per_billed"],
                                 as_dict=True, for_update=verrou) or {}
        f.update(pr_docstatus=pr.get("docstatus"), pr_status=pr.get("status"), pr_per_billed=pr.get("per_billed"))
        f["items"] = frappe.get_all("Purchase Receipt Item", filters={"parent": f["purchase_receipt"]},
                                    fields=["item_code", "item_name", "qty"], order_by="idx")
        f["recu"] = valeur_recu(f["purchase_receipt"]) if int(f.get("pr_docstatus") or 0) == 1 else 0.0
    return f


CHAMPS_FICHE = ["name", "supplier", "fournisseur", "statut", "est_bl", "numero_bl", "numero_facture", "montant",
                "date_facture", "mode_paiement", "journal_entry", "payment_entry", "purchase_invoice", "depense_a_payer",
                "purchase_order", "purchase_receipt", "description"]


def _ouverts(supplier: str, pour: str = "depense") -> list:
    """Les BL de ce fournisseur pas encore couverts par une facture, avec leur motif s'ils ne sont pas soldables."""
    fiches = frappe.get_all("Facture Achat a Saisir", order_by="creation asc", fields=CHAMPS_FICHE,
                            filters={"supplier": supplier, "est_bl": 1, "statut": "À saisir",
                                     "purchase_invoice": ["is", "not set"]})
    out = []
    for f in fiches:
        f = _etat_fiche(f)
        f["motif"] = motif_non_traitable(f, supplier, pour)
        out.append(f)
    return out


def _clos(supplier: str, cles: set) -> list:
    """Les BL de ce fournisseur DÉJÀ couverts dont la clé est citée : la facture est sans doute déjà saisie."""
    if not cles:
        return []
    out = []
    for f in frappe.get_all("Facture Achat a Saisir", fields=["name", "numero_bl", "numero_facture", "purchase_invoice",
                                                             "depense_a_payer", "statut"],
                            filters={"supplier": supplier, "est_bl": 1, "statut": ["!=", "À saisir"]}):
        if cles & cles_fiche(f.numero_bl, f.numero_facture):
            out.append({"name": f.name, "numero_bl": f.numero_bl, "numero_facture": f.numero_facture,
                        "couvert_par": f.purchase_invoice or f.depense_a_payer or f.statut})
    return out


def rapprocher_pour(supplier: str, cites=None, lignes=None, pour: str = "depense") -> dict:
    """Le rapprochement complet pour la fenêtre Dépense (fournisseur connu). `pour` = "achat" : la pièce est saisie en
    « Facture d'achat » (motif_non_traitable)."""
    cites = _liste(cites)
    lignes = _liste(lignes)
    cles = {cle_numero_bl(c.get("numero")) for c in cites} - {""}
    out = rapprocher_bls(cites, _ouverts(supplier, pour), _clos(supplier, cles), lignes)
    out["supplier"] = supplier
    out["pour"] = pour
    issues_apprentissage(supplier, lignes, out["bls"])
    return out


def issues_apprentissage(supplier, lignes, cartes) -> None:
    """Ce que l'ENREGISTREMENT fera de chaque ligne appariée à une réception, pour que l'écran le dise tel quel
    (relecture du 25/11/2026 : l'écran devinait d'après la proposition, l'apprentissage écrit sur la première clé de la
    ligne — le code s'il existe). Ajoute à chaque entrée de `appariees` : `cle` (la clé où `apprendre` écrira),
    `issue` ("creer" | "confirmer" | "corriger" : une autre correspondance existe, elle est GARDÉE | "rien"), et pour une
    correspondance existante `ancien` / `fois`. Lecture seule."""
    from customization_app import correspondance_articles as CA

    for carte in cartes or []:
        for p in carte.get("appariees") or []:
            l = lignes[p["i"]] if 0 <= p["i"] < len(lignes) and isinstance(lignes[p["i"]], dict) else {}
            toutes = CA.cles(supplier, l.get("code"), l.get("designation"))
            p["cle"] = toutes[0] if toutes else ""
            if not toutes or not CA._article_valide(p["item_code"]):
                p["issue"] = "rien"
                continue
            ex = frappe.db.get_value(CA.DOCTYPE, {"cle": p["cle"]}, ["item_code", "fois"], as_dict=True)
            p["issue"] = CA.decision(ex, p["item_code"])
            if ex:
                p["ancien"], p["fois"] = ex.item_code, int(ex.fois or 0)


def fournisseur_par_bl(candidats: list, cites) -> str | None:
    """Parmi les fournisseurs candidats (nom approché), celui — et lui seul — qui a un BL au n° cité. Sinon None."""
    cles = {cle_numero_bl(c.get("numero")) for c in _liste(cites)} - {""}
    if not cles:
        return None
    trouves = []
    for c in candidats or []:
        for f in frappe.get_all("Facture Achat a Saisir", fields=["numero_bl", "numero_facture"],
                                filters={"supplier": c["name"], "est_bl": 1}):
            if cles & cles_fiche(f.numero_bl, f.numero_facture):
                trouves.append(c["name"])
                break
    return trouves[0] if len(trouves) == 1 else None


@frappe.whitelist()
def bls_ouverts(supplier, bls_cites=None, lignes=None, pour=None):
    """La fenêtre Dépense : les BL du fournisseur face à la facture (après un choix de fournisseur, ou la lecture
    des lignes d'articles). Lecture seule. `pour` = "achat" : la pièce est saisie en « Facture d'achat »."""
    from customization_app import caisse_depenses as C

    frappe.only_for(C.ROLES)
    pour = "achat" if pour == "achat" else "depense"
    if not supplier or not frappe.db.exists("Supplier", supplier):
        return {"bls": [], "inconnus": _liste(bls_cites), "deja_couverts": [], "coches": [], "supplier": None,
                "pour": pour}
    return rapprocher_pour(supplier, bls_cites, lignes, pour)


def valider(bls_couverts, supplier, lignes_stock, stock_ht, ht_total, confirme_ecarts=0, cites=None,
            deja_soldes=0.0, nb_deja=0) -> list:
    """Avant toute écriture, dans la transaction de `creer` : chaque BL coché est relu SOUS VERROU et contrôlé.
    -> [{fiche, numero_bl, recu_doc (réception), commande, recu (valeur à solder)}], dans l'ordre des noms."""
    from frappe import _

    noms = sorted({n for n in _liste(bls_couverts) if n})
    if not noms:
        return []
    if not supplier:
        frappe.throw(_("Le fournisseur doit être confirmé pour solder des BL."))
    # Deux caisses qui soldent le même BL : la 2e attend que la 1re ait commité, puis lit — SOUS VERROU, donc à jour —
    # un statut « Saisie » et refuse. ⚠️ Pas de relecture simple après ce verrou : sous REPEATABLE READ (MariaDB), elle
    # rendrait l'instantané d'avant l'attente (« À saisir ») et le BL serait soldé deux fois (relecture du 25/11/2026).
    verrouillees = {r.name: r for r in frappe.db.sql(
        "select %s from `tabFacture Achat a Saisir` where name in %%s order by name for update"
        % ", ".join("`%s`" % c for c in CHAMPS_FICHE), (tuple(noms),), as_dict=True)}
    cites = _liste(cites)
    out, infos = [], []
    for nom in noms:
        f = verrouillees.get(nom)
        if not f:
            frappe.throw(_("La fiche {0} n'existe pas.").format(nom))
        f = _etat_fiche(f, verrou=True)
        motif = motif_non_traitable(f, supplier)
        if motif:
            frappe.throw(_("Le BL {0} ({1}) ne peut pas être soldé par cette dépense : {2}.")
                         .format(f.get("numero_bl") or nom, nom, motif))
        if f.get("purchase_receipt") and _m(f.get("recu")) <= 0:
            frappe.throw(_("La réception {0} du BL {1} n'a rien à solder.").format(f["purchase_receipt"], f.get("numero_bl")))
        cite = next((c for c in cites if cle_numero_bl(c.get("numero"))
                     in cles_fiche(f.get("numero_bl"), f.get("numero_facture"))), None)
        if cite and controle_ttc(cite.get("total_ttc"), f.get("montant")) == A_VERIFIER and not int(confirme_ecarts or 0):
            frappe.throw(_("Le BL {0} : la facture dit {1} TTC, la fiche {2}. Confirmez pour le solder quand même.")
                         .format(f.get("numero_bl"), _dt(cite.get("total_ttc")), _dt(f.get("montant"))))
        infos.append({"numero_bl": f.get("numero_bl") or nom, "cles": sorted(cles_fiche(f.get("numero_bl"), f.get("numero_facture"))),
                      "items": [i.get("item_code") for i in f.get("items") or []],
                      "recu": bool(f.get("purchase_receipt"))})
        out.append({"fiche": nom, "numero_bl": f.get("numero_bl") or "", "recu_doc": f.get("purchase_receipt") or None,
                    "commande": f.get("purchase_order") or None, "recu": _m(f.get("recu")),
                    # Les clés de la fiche AVANT le solde, qui remplace son numero_facture par celui de la facture :
                    # l'apprentissage y retrouve la rubrique du BL (relecture du 25/11/2026).
                    "cles": sorted(cles_fiche(f.get("numero_bl"), f.get("numero_facture")))})
    for l in lignes_stock or []:
        bl = ligne_deja_recue(l, infos)
        if bl:
            frappe.throw(_("L'article {0} est déjà entré en stock avec le BL {1} : décochez-le, le BL le couvre.")
                         .format(l.get("item_code"), bl))
    erreur = controle_total(_m(sum(o["recu"] for o in out) + _m(deja_soldes)), stock_ht, ht_total, len(out) + int(nb_deja or 0))
    if erreur:
        frappe.throw(erreur)
    return out


def couvrir_par_depense(bls: list, ctx: dict) -> list:
    """Solde chaque BL validé. Dans la transaction de `creer` (tout ou rien). -> ce qui a été fait, BL par BL."""
    from customization_app import caisse_stock_partiel as S

    faits = []
    for b in bls or []:
        if b.get("recu_doc"):
            r = S.solder_reception(b["fiche"], dict(ctx, numero_bl=b.get("numero_bl")))
        else:
            # BL sans commande ni réception : rien à solder, la fiche est seulement close.
            valeurs = {"statut": "Saisie", "depense_a_payer": ctx.get("depense")}
            if (ctx.get("numero_facture") or "").strip():
                valeurs["numero_facture"] = ctx["numero_facture"].strip()
            frappe.db.set_value("Facture Achat a Saisir", b["fiche"], valeurs, update_modified=False)
            r = {"fiche": b["fiche"], "recu": 0.0, "journal_entry": None, "recu_doc": None, "commande": None}
        frappe.get_doc("Facture Achat a Saisir", b["fiche"]).add_comment(
            "Comment", "🧾 Couvert par la facture %s (dépense %s)%s." % (
                ctx.get("numero_facture") or "?", ctx.get("depense") or "?",
                ", réception soldée par %s" % r["journal_entry"] if r.get("journal_entry") else ""))
        faits.append(dict(r, numero_bl=b.get("numero_bl"), cles=b.get("cles") or []))
    return faits


ROLES_ROUVRIR = ("System Manager", "Accounts Manager")


def _supplier_de_la_depense(dap) -> str | None:
    """Le fournisseur (fiche) d'une dépense : celui de ses BL couverts, de sa part stock, ou rapproché par son nom."""
    from customization_app import caisse_depenses as C

    for f in frappe.get_all("Facture Achat a Saisir", filters={"depense_a_payer": dap.name}, pluck="supplier"):
        if f:
            return f
    if dap.get("fiche_achat"):
        s = frappe.db.get_value("Facture Achat a Saisir", dap.fiche_achat, "supplier")
        if s:
            return s
    return C._rapprocher_fournisseur(dap.get("fournisseur")).get("certain") if dap.get("fournisseur") else None


def _bls_soldes(dap) -> list:
    """Les BL couverts par cette dépense (pas sa propre fiche de part stock) : soldés (écriture « Réception soldée — »)
    ou clos sans réception (pas d'écriture)."""
    out = []
    for f in frappe.get_all("Facture Achat a Saisir", filters={"depense_a_payer": dap.name, "est_bl": 1, "statut": "Saisie",
                                                              "name": ["!=", dap.get("fiche_achat") or ""]},
                            fields=["name", "numero_bl", "statut", "journal_entry", "purchase_receipt", "purchase_invoice"]):
        if f.purchase_invoice:
            continue
        je = frappe.db.get_value("Journal Entry", f.journal_entry, ["cheque_no", "total_debit"], as_dict=True) \
            if f.journal_entry else None
        if je and not (je.cheque_no or "").startswith(PREFIXE_SOLDE):
            continue
        out.append(dict(f, solde=_m(je.total_debit) if je else 0.0))
    return out


@frappe.whitelist()
def bls_de_la_depense(depense):
    """Pour le comptable, sur la fiche de dépense : les BL qu'elle couvre (à rouvrir) et les BL encore ouverts de son
    fournisseur (à rattacher après coup). Rien à rattacher à une dépense qui est elle-même un BL."""
    frappe.only_for(ROLES_ROUVRIR)
    dap = frappe.get_doc("Depense A Payer", depense)
    couverts = _bls_soldes(dap)
    if cint_(dap.est_bl):
        return {"couverts": couverts, "supplier": None, "ouverts": []}
    supplier = _supplier_de_la_depense(dap)
    # Fournisseur sûr : ses BL ouverts. Sinon (le nom de la dépense n'est qu'un texte), ceux des fournisseurs qui lui
    # ressemblent — le comptable reconnaît le sien ; l'enregistrement exige qu'ils soient tous du même.
    fournisseurs = [supplier] if supplier else [c["name"] for c in C_rapprocher(dap.get("fournisseur"))]
    ouverts = []
    for f in fournisseurs:
        for b in rapprocher_pour(f)["bls"]:
            if not b["recu"] and not b["bloquant"]:
                # Après coup, plus de part stock possible : un BL sans réception d'abord réceptionné, puis rattaché.
                b = dict(b, bloquant=True, motif="pas encore de réception : créez d'abord sa commande (le stock entre), puis rattachez")
            ouverts.append(dict(b, supplier=f))
    return {"couverts": couverts, "supplier": supplier or ", ".join(fournisseurs), "ouverts": ouverts}


def cint_(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def C_rapprocher(nom) -> list:
    """Les fournisseurs candidats d'un nom de fournisseur lu (texte libre)."""
    from customization_app import caisse_depenses as C

    return (C._rapprocher_fournisseur(nom).get("candidats") or []) if nom else []


@frappe.whitelist(methods=["POST"])
def couvrir_bls_apres(depense, fiches):
    """Rattache après coup des BL déjà reçus à une dépense avec facture déjà enregistrée (le comptable : BL oublié à la
    saisie, ou rouvert parce que le mauvais avait été coché). Mêmes contrôles et même solde qu'à la saisie — dont le
    refus d'un BL dont un article a été remis en stock par la part stock de la dépense."""
    from frappe import _
    from customization_app import caisse_depenses as C

    frappe.only_for(ROLES_ROUVRIR)
    dap = frappe.get_doc("Depense A Payer", depense)
    if cint_(dap.est_bl):
        frappe.throw(_("Cette dépense est elle-même un BL : un BL ne couvre pas d'autres BL, seule une facture le peut."))
    je = frappe.get_doc("Journal Entry", dap.journal_entry) if dap.journal_entry else None
    if not je or je.docstatus != 1 or not (je.cheque_no or "").startswith((C.PREFIXE_CAISSE, C.PREFIXE_A_PAYER)) \
            or not dap.compte_charge:
        frappe.throw(_("La dépense {0} n'a pas d'écriture de caisse validée : rien ne peut s'y rattacher.").format(dap.name))
    ht_total = _m(sum(a.debit_in_account_currency for a in je.accounts if a.account == dap.compte_charge))
    soldes = _bls_soldes(dap)
    supplier = _supplier_de_la_depense(dap)
    if not supplier:
        # Fournisseur de la dépense incertain : celui des BL cochés, à condition qu'il soit unique.
        des_fiches = {frappe.db.get_value("Facture Achat a Saisir", n, "supplier") for n in _liste(fiches)}
        if len(des_fiches) != 1 or None in des_fiches:
            frappe.throw(_("Les BL cochés doivent être d'un seul et même fournisseur."))
        supplier = des_fiches.pop()
    for n in _liste(fiches):
        if not frappe.db.get_value("Facture Achat a Saisir", n, "purchase_receipt"):
            frappe.throw(_("Le BL {0} n'a pas encore de réception : créez d'abord sa commande (le stock entre), puis "
                           "rattachez.").format(n))
    bls = valider(fiches, supplier, _liste(dap.articles_stock), _m(dap.part_stock_ht), ht_total,
                  deja_soldes=sum(f["solde"] for f in soldes), nb_deja=len(soldes))
    faits = couvrir_par_depense(bls, {"compte": dap.compte_charge, "numero_facture": dap.numero_facture,
                                      "depense": dap.name, "fournisseur": dap.fournisseur})
    dap.add_comment("Comment", "📦 BL rattachés après coup par %s : %s." % (
        frappe.session.user, ", ".join(f.get("numero_bl") or f["fiche"] for f in faits)))
    frappe.db.commit()
    return {"soldes": faits}


@frappe.whitelist(methods=["POST"])
def rouvrir_bls(depense):
    """Défait les BL soldés par une dépense : annule leurs écritures de solde, rouvre commandes et réceptions, remet les
    fiches « À saisir ». Réservé au comptable. La dépense elle-même ne bouge pas."""
    from frappe import _

    frappe.only_for(ROLES_ROUVRIR)
    if not depense or not frappe.db.exists("Depense A Payer", depense):
        frappe.throw(_("Dépense inconnue."))
    faits = []
    dap = frappe.get_doc("Depense A Payer", depense)
    for f in _bls_soldes(dap):
        f = frappe._dict(f)
        f.purchase_order = frappe.db.get_value("Facture Achat a Saisir", f.name, "purchase_order")
        if f.journal_entry:
            je = frappe.get_doc("Journal Entry", f.journal_entry)
            if je.docstatus == 1:
                je.flags.ignore_permissions = True
                je.cancel()
        if f.purchase_order and frappe.db.get_value("Purchase Order", f.purchase_order, "status") == "Closed":
            po = frappe.get_doc("Purchase Order", f.purchase_order)
            po.flags.ignore_permissions = True
            po.update_status("Submitted")
        if f.purchase_receipt and frappe.db.get_value("Purchase Receipt", f.purchase_receipt, "status") == "Closed":
            pr = frappe.get_doc("Purchase Receipt", f.purchase_receipt)
            pr.flags.ignore_permissions = True
            pr.update_status("Submitted")
        frappe.db.set_value("Facture Achat a Saisir", f.name, {
            "statut": "À saisir", "numero_facture": f.numero_bl or "", "journal_entry": None,
            "depense_a_payer": None}, update_modified=False)
        frappe.get_doc("Facture Achat a Saisir", f.name).add_comment(
            "Comment", "↩ BL rouvert par %s : il n'est plus couvert par la dépense %s." % (frappe.session.user, depense))
        faits.append(f.name)
    frappe.db.commit()
    return {"rouverts": faits}
