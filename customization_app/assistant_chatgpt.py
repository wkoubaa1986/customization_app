"""Assistant ChatGPT — les actions qu'un GPT personnalisé peut faire sur ERPNext (demande du 02/11/2026).

L'utilisateur parle à son app ChatGPT (« voilà la photo de la facture, ajoute cette charge ») ; le GPT lit l'image
LUI-MÊME et appelle ici des actions ÉTROITES, décrites par le schéma OpenAPI de `openapi()`. ERPNext ne reçoit jamais
l'image, seulement des données structurées, et ne fait que ce que ces actions permettent.

SÉCURITÉ. Un utilisateur technique `assistant.chatgpt@aquaworld.local` (patch ensure_assistant_chatgpt), rôle
« Assistant ChatGPT » SANS aucune permission de doctype : l'API REST ordinaire lui est fermée, seules ces fonctions
(gardées par `frappe.only_for`) répondent. Clé API générée dans la fiche de l'utilisateur (« API Access »), passée par
ChatGPT en en-tête `Authorization: token clé:secret`. Garde-fous : plafond par dépense, date dans les 90 jours,
compte de charge parmi ceux de la caisse, mode de paiement connu, tout est tracé « via ChatGPT ».

LA DÉPENSE suit les règles de la caisse (caisse_depenses) : débit du compte de charge (HT) et de la TVA de son taux,
crédit du compte du mode (Espèces → caisse ; carte, virement, chèque → Zitouna), référence préfixée « Dépense caisse — »
pour que la caisse journalière la voie comme les autres. En BROUILLON par défaut : l'utilisateur la valide dans ERPNext ;
`valider=1` la soumet directement.
"""
from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_url, getdate, nowdate

from customization_app import caisse_depenses as CD

ROLE = "Assistant ChatGPT"
UTILISATEUR = "assistant.chatgpt@aquaworld.local"
MARQUE = "Saisie via ChatGPT"
MODES = ("Espèces", "Carte de crédit", "Virement", "Chèque")
PLAFOND_DT = 5000.0
JOURS_ARRIERE = 90
MAX_RESULTATS = 10


# ── Règles pures ─────────────────────────────────────────────────────────────

def verifier_depense(date, libelle, montant_ttc, compte_charge, mode, tva, comptes_autorises, aujourd_hui) -> list[str]:
    """Les refus, en clair (vide = acceptable). Pure."""
    e = []
    try:
        d = getdate(date)
    except Exception:
        d = None
    if not d:
        e.append(_("Date illisible (attendu AAAA-MM-JJ)."))
    else:
        if d > getdate(aujourd_hui):
            e.append(_("La date {0} est dans le futur.").format(d))
        if d < add_days(getdate(aujourd_hui), -JOURS_ARRIERE):
            e.append(_("La date {0} a plus de {1} jours : à saisir dans ERPNext directement.").format(d, JOURS_ARRIERE))
    if not (libelle or "").strip():
        e.append(_("Le libellé est obligatoire (fournisseur ou objet de la dépense)."))
    m = flt(montant_ttc, 3)
    if m <= 0:
        e.append(_("Le montant TTC doit être positif."))
    elif m > PLAFOND_DT:
        e.append(_("Montant {0} DT au-dessus du plafond de {1} DT de l'assistant : à saisir dans ERPNext.").format(m, int(PLAFOND_DT)))
    if compte_charge not in comptes_autorises:
        e.append(_("Compte de charge « {0} » inconnu : choisissez-le dans la liste des comptes de charge.").format(compte_charge))
    if mode not in MODES:
        e.append(_("Mode de paiement « {0} » inconnu : {1}.").format(mode, ", ".join(MODES)))
    t = flt(tva, 3)
    if t < 0 or (m > 0 and t >= m):
        e.append(_("La TVA ({0}) doit rester inférieure au montant TTC ({1}).").format(t, m))
    return e


def lignes_depense(montant_ttc, tva, taux_tva, compte_charge, mode) -> list[dict]:
    """Les lignes de l'écriture : charge HT, TVA à son taux, crédit du mode. Pure (comptes de la caisse)."""
    m, t = flt(montant_ttc, 3), flt(tva, 3)
    lignes = [{"account": compte_charge, "debit_in_account_currency": flt(m - t, 3), "cost_center": CD.CC}]
    if t > 0:
        lignes.append({"account": CD.COMPTE_TVA_7 if flt(taux_tva) == 7 else CD.COMPTE_TVA_19,
                       "debit_in_account_currency": t, "cost_center": CD.CC})
    lignes.append({"account": CD.compte_credit(mode), "credit_in_account_currency": m, "cost_center": CD.CC})
    return lignes


# ── Accès ────────────────────────────────────────────────────────────────────

def _acces():
    frappe.only_for((ROLE, "System Manager"))


def _url(doctype, name):
    return get_url("/app/%s/%s" % (frappe.scrub(doctype).replace("_", "-"), name))


def _mots(texte: str, colonnes: list[str]) -> tuple[str, list]:
    """Condition SQL « chaque mot apparaît dans l'une des colonnes » (ordre libre : « citerne 3.2 » trouve
    « Citerne, 3.2G »). → (sql, valeurs). Pure."""
    mots = [m for m in (texte or "").replace(",", " ").split() if m][:6]
    if not mots:
        return "1=0", []
    conds, vals = [], []
    for m in mots:
        conds.append("(" + " or ".join("ifnull(%s, '') like %%s" % c for c in colonnes) + ")")
        vals += ["%" + m + "%"] * len(colonnes)
    return " and ".join(conds), vals


# ── Actions de lecture ───────────────────────────────────────────────────────

@frappe.whitelist()
def moi():
    """Qui parle, et ce que l'assistant sait faire."""
    _acces()
    return {"utilisateur": frappe.session.user, "societe": CD.COMPANY, "aujourd_hui": nowdate(), "plafond_dt": PLAFOND_DT,
            "modes_de_paiement": list(MODES),
            "actions": ["comptes_de_charge", "chercher_fournisseur", "chercher_client", "stock_article", "saisir_depense", "mes_saisies"]}


@frappe.whitelist()
def comptes_de_charge():
    """Les comptes de charge que la caisse accepte (mêmes que l'analyse de facture de la caisse)."""
    _acces()
    return [{"compte": c, "libelle": c.replace(" - A&S", "")} for c in CD.comptes_classifiables()]


@frappe.whitelist()
def chercher_fournisseur(texte):
    _acces()
    sql, vals = _mots(texte, ["supplier_name", "name", "tax_id"])
    return frappe.db.sql(f"""select name, supplier_name, tax_id from tabSupplier where disabled = 0 and {sql}
                             order by supplier_name limit {MAX_RESULTATS}""", vals, as_dict=True)


@frappe.whitelist()
def chercher_client(texte):
    _acces()
    sql, vals = _mots(texte, ["c.customer_name", "c.name", "c.custom_liste_telephone"])
    rows = frappe.db.sql(f"""select c.name, c.customer_name, c.custom_liste_telephone as telephones,
                                    (select max(so.transaction_date) from `tabSales Order` so where so.customer = c.name and so.docstatus = 1) derniere_commande
                             from tabCustomer c where c.disabled = 0 and {sql}
                             order by c.customer_name limit {MAX_RESULTATS}""", vals, as_dict=True)
    for r in rows:
        r.derniere_commande = str(r.derniere_commande) if r.derniere_commande else None
    return rows


@frappe.whitelist()
def stock_article(texte):
    """Articles correspondant au texte, avec le stock par entrepôt et le prix de vente standard."""
    _acces()
    sql, vals = _mots(texte, ["item_name", "name"])
    items = frappe.db.sql(f"""select name, item_name, stock_uom from tabItem where disabled = 0 and is_stock_item = 1 and has_variants = 0
                              and {sql} order by item_name limit 5""", vals, as_dict=True)
    for i in items:
        i.stock = [{"entrepot": b.warehouse, "qte": flt(b.actual_qty, 3)} for b in
                   frappe.get_all("Bin", filters={"item_code": i.name, "actual_qty": ["!=", 0]}, fields=["warehouse", "actual_qty"])]
        i.total = flt(sum(s["qte"] for s in i.stock), 3)
        i.prix_vente = flt(frappe.db.get_value("Item Price", {"item_code": i.name, "price_list": "Vente standard", "selling": 1}, "price_list_rate"), 3)
    return items


# ── Action d'écriture ────────────────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def saisir_depense(date, libelle, montant_ttc, compte_charge, mode="Espèces", tva=0, taux_tva=0, fournisseur=None,
                   reference=None, valider=0):
    """Une dépense (charge) : écriture de journal selon les règles de la caisse, en BROUILLON sauf `valider=1`."""
    _acces()
    comptes = set(CD.comptes_classifiables())
    erreurs = verifier_depense(date, libelle, montant_ttc, compte_charge, mode, tva, comptes, nowdate())
    if erreurs:
        frappe.throw(" ".join(erreurs))
    libelle = (libelle or "").strip()
    remarque = libelle + ((" — " + fournisseur.strip()) if (fournisseur or "").strip() else "") \
        + ((" — réf. " + reference.strip()) if (reference or "").strip() else "")
    je = frappe.get_doc({
        "doctype": "Journal Entry", "voucher_type": "Journal Entry", "company": CD.COMPANY, "posting_date": getdate(date),
        "cheque_no": (CD.PREFIXE_CAISSE + libelle)[:140], "cheque_date": getdate(date),
        "user_remark": "%s\n%s par %s (%s%s)" % (remarque, MARQUE, frappe.session.user, mode,
                                                 (", TVA %s à %s %%" % (flt(tva, 3), flt(taux_tva))) if flt(tva) > 0 else ""),
        "accounts": lignes_depense(montant_ttc, tva, taux_tva, compte_charge, mode)})
    je.flags.ignore_permissions = True
    je.insert()
    if cint(valider):
        je.submit()
    return {"ecriture": je.name, "statut": _("Validée") if je.docstatus == 1 else _("Brouillon : à valider dans ERPNext"),
            "date": str(je.posting_date), "libelle": libelle, "montant_ttc": flt(montant_ttc, 3), "tva": flt(tva, 3),
            "compte_charge": compte_charge, "mode": mode, "credit": CD.compte_credit(mode), "url": _url("Journal Entry", je.name)}


@frappe.whitelist()
def mes_saisies(limite=20):
    """Les dernières écritures passées par l'assistant, brouillons compris."""
    _acces()
    rows = frappe.db.sql("""select name, posting_date, cheque_no as libelle, total_debit as montant, docstatus
                            from `tabJournal Entry` where user_remark like %s and docstatus < 2
                            order by creation desc limit %s""", ("%" + MARQUE + "%", cint(limite) or 20), as_dict=True)
    return [{"ecriture": r.name, "date": str(r.posting_date), "libelle": (r.libelle or "").replace(CD.PREFIXE_CAISSE, ""),
             "montant": flt(r.montant, 3), "statut": _("Validée") if r.docstatus == 1 else _("Brouillon"),
             "url": _url("Journal Entry", r.name)} for r in rows]


# ── Schéma OpenAPI pour le GPT ───────────────────────────────────────────────

def _op(fn: str, summary: str, params: dict, post: bool = False, requis: list | None = None) -> dict:
    props = {k: {"type": v[0], "description": v[1]} for k, v in params.items()}
    if post:
        return {"post": {"operationId": fn, "summary": summary,
                         "requestBody": {"required": True, "content": {"application/json": {"schema": {
                             "type": "object", "properties": props, "required": requis or list(params)}}}},
                         "responses": {"200": {"description": "OK"}}}}
    return {"get": {"operationId": fn, "summary": summary,
                    "parameters": [{"name": k, "in": "query", "required": k in (requis or list(params)),
                                    "schema": {"type": v[0]}, "description": v[1]} for k, v in params.items()],
                    "responses": {"200": {"description": "OK"}}}}


def schema_openapi(serveur: str) -> dict:
    """Le schéma à coller dans le GPT (Actions). Pure."""
    base = "/api/method/customization_app.assistant_chatgpt."
    return {
        "openapi": "3.1.0",
        "info": {"title": "Aqua World ERP — assistant", "version": "1.0.0",
                 "description": "Actions étroites sur ERPNext d'Aqua World & Servicing : dépenses, comptes de charge, fournisseurs, clients, stock."},
        "servers": [{"url": serveur}],
        "paths": {
            base + "moi": _op("moi", "Qui je suis et ce que je peux faire", {}),
            base + "comptes_de_charge": _op("comptes_de_charge", "Liste des comptes de charge acceptés (à utiliser avant saisir_depense)", {}),
            base + "chercher_fournisseur": _op("chercher_fournisseur", "Chercher un fournisseur par nom ou matricule fiscal",
                                               {"texte": ["string", "Nom ou matricule, même partiel"]}),
            base + "chercher_client": _op("chercher_client", "Chercher un client par nom ou téléphone",
                                          {"texte": ["string", "Nom ou numéro, même partiel"]}),
            base + "stock_article": _op("stock_article", "Stock d'un article par entrepôt et prix de vente",
                                        {"texte": ["string", "Nom ou code d'article, même partiel"]}),
            base + "saisir_depense": _op("saisir_depense", "Enregistrer une dépense (charge) : écriture de journal en brouillon, sauf valider=1", {
                "date": ["string", "Date de la dépense AAAA-MM-JJ (pas dans le futur, 90 jours au plus)"],
                "libelle": ["string", "Objet de la dépense, court, avec le fournisseur si connu"],
                "montant_ttc": ["number", "Montant TTC en dinars"],
                "compte_charge": ["string", "Nom exact d'un compte renvoyé par comptes_de_charge"],
                "mode": ["string", "Espèces, Carte de crédit, Virement ou Chèque (Espèces par défaut)"],
                "tva": ["number", "Montant de TVA inclus dans le TTC, 0 si inconnu"],
                "taux_tva": ["number", "Taux de TVA : 19 ou 7 (0 si inconnu)"],
                "fournisseur": ["string", "Nom du fournisseur tel que lu sur la pièce"],
                "reference": ["string", "Numéro de facture ou de reçu"],
                "valider": ["integer", "1 pour soumettre tout de suite, 0 (défaut) pour laisser en brouillon"],
            }, post=True, requis=["date", "libelle", "montant_ttc", "compte_charge"]),
            base + "mes_saisies": _op("mes_saisies", "Dernières écritures passées par l'assistant", {"limite": ["integer", "Nombre, 20 par défaut"]}, requis=[]),
        },
        "components": {"securitySchemes": {"cleApi": {"type": "apiKey", "in": "header", "name": "Authorization",
                                                       "description": "token <clé>:<secret> de l'utilisateur assistant"}}},
        "security": [{"cleApi": []}],
    }


@frappe.whitelist(allow_guest=True)
def openapi():
    """Le schéma, sans secret : collé tel quel dans le GPT (ou importé par son URL)."""
    return schema_openapi(get_url())


INSTRUCTIONS_GPT = """Tu es l'assistant ERPNext d'Aqua World & Servicing (Tunisie, dinars TND). Tu parles français.
Quand on t'envoie une photo de facture ou de reçu : lis fournisseur, date, montant TTC, TVA et son taux, numéro de pièce.
Appelle comptes_de_charge pour choisir le compte exact. Récapitule en une ligne (date, libellé, TTC, TVA, compte, mode) et
demande confirmation AVANT d'appeler saisir_depense. Mode « Espèces » sauf indication. Laisse en brouillon (valider=0) sauf
si on te dit « valide ». Après la saisie, donne le numéro d'écriture et son lien. Ne devine jamais un montant illisible :
demande. Pour le stock ou un client, utilise stock_article / chercher_client et réponds brièvement."""
