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
            "actions": ["comptes_de_charge", "chercher_fournisseur", "chercher_client", "stock_article", "saisir_depense", "mes_saisies",
                        "saisir_depense_euros", "banques", "verser_en_banque", "rendez_vous", "creneaux_rendez_vous", "creer_rendez_vous"]}


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


# ── Versement d'espèces en banque ────────────────────────────────────────────

COMPTES_ESPECES = ("Sabah Espèces - A&S", "Espèces - A&S")
GROUPE_BANQUES = "Comptes bancaires - A&S"


def _banques() -> list[str]:
    """Les vraies banques où l'on verse des espèces : les comptes qui ont une fiche Bank Account de la société
    (Zitouna, BIAT) — pas les cartes, ni les comptes personnels ou partenaires du même groupe."""
    comptes = frappe.get_all("Bank Account", filters={"is_company_account": 1, "disabled": 0}, pluck="account")
    return frappe.get_all("Account", filters={"name": ["in", comptes or [""]], "parent_account": GROUPE_BANQUES, "is_group": 0,
                                              "disabled": 0, "account_currency": "TND"}, pluck="name", order_by="name")


@frappe.whitelist()
def banques():
    _acces()
    return [{"compte": b, "libelle": b.replace(" - A&S", "")} for b in _banques()] + \
           [{"caisse": c, "libelle": c.replace(" - A&S", "")} for c in COMPTES_ESPECES]


@frappe.whitelist(methods=["POST"])
def verser_en_banque(date, montant, banque, compte_especes=None, reference=None, valider=1):
    """Versement d'espèces : écriture de journal débit banque / crédit caisse (Sabah Espèces par défaut), avec la
    référence du bordereau. Validée par défaut (c'est elle que lisent « Espèce à verser » et la caisse espèces)."""
    _acces()
    compte_especes = compte_especes or COMPTES_ESPECES[0]
    e = []
    if banque not in _banques():
        e.append(_("Banque « {0} » inconnue : {1}.").format(banque, ", ".join(_banques())))
    if compte_especes not in COMPTES_ESPECES:
        e.append(_("Compte d'espèces « {0} » inconnu : {1}.").format(compte_especes, ", ".join(COMPTES_ESPECES)))
    m = flt(montant, 3)
    if m <= 0:
        e.append(_("Le montant doit être positif."))
    try:
        d = getdate(date)
        if d > getdate(nowdate()) or d < add_days(getdate(nowdate()), -JOURS_ARRIERE):
            e.append(_("La date {0} doit être dans les {1} derniers jours.").format(d, JOURS_ARRIERE))
    except Exception:
        e.append(_("Date illisible (attendu AAAA-MM-JJ)."))
    if e:
        frappe.throw(" ".join(e))
    libelle = _("Versement espèces {0}{1}").format(banque.replace(" - A&S", ""), (" — réf. " + reference.strip()) if (reference or "").strip() else "")
    je = frappe.get_doc({"doctype": "Journal Entry", "voucher_type": "Journal Entry", "company": CD.COMPANY, "posting_date": d,
                         "cheque_no": libelle[:140], "cheque_date": d,
                         "user_remark": "%s\n%s par %s" % (libelle, MARQUE, frappe.session.user),
                         "accounts": [{"account": banque, "debit_in_account_currency": m, "cost_center": CD.CC},
                                      {"account": compte_especes, "credit_in_account_currency": m, "cost_center": CD.CC}]})
    je.flags.ignore_permissions = True
    je.insert()
    if cint(valider):
        je.submit()
    return {"ecriture": je.name, "statut": _("Validée") if je.docstatus == 1 else _("Brouillon : à valider dans ERPNext"),
            "date": str(d), "montant": m, "banque": banque, "compte_especes": compte_especes, "url": _url("Journal Entry", je.name)}


# ── Dépenses en euros (page Banque de bank_retenue_sync) ─────────────────────

@frappe.whitelist(methods=["POST"])
def saisir_depense_euros(date, libelle, montant_eur, compte_charge=None, taux=None):
    """Une dépense payée depuis le compte en euros (pub Google, abonnement…) : taux du jour lu sur internet sauf
    `taux` fourni, écriture de journal multi-devises SOUMISE (même mécanique que la page « Dépenses en euros »)."""
    _acces()
    try:
        from bank_retenue_sync.api import depenses_euros as DE
    except Exception:
        frappe.throw(_("Les dépenses en euros ne sont pas disponibles sur ce site."))
    compte_charge = compte_charge or DE.COMPTE_CHARGE_DEFAUT
    if compte_charge not in set(CD.comptes_classifiables()) | {DE.COMPTE_CHARGE_DEFAUT}:
        frappe.throw(_("Compte de charge « {0} » inconnu : choisissez-le dans la liste des comptes de charge.").format(compte_charge))
    if flt(montant_eur) <= 0 or flt(montant_eur) > PLAFOND_DT:
        frappe.throw(_("Le montant en euros doit être positif et au plus {0}.").format(int(PLAFOND_DT)))
    if flt(taux) > 0:
        taux, source = flt(taux, 6), _("taux saisi")
    else:
        t = DE._taux()
        taux, source = t["taux"], t["source"]
    r = DE._creer([{"date": date, "libelle": libelle, "montant_eur": flt(montant_eur, 2), "compte_charge": compte_charge}],
                  taux, DE.COMPTE_EUROS, source, saisi_par="%s par %s" % (MARQUE, frappe.session.user), ignorer_permissions=True)
    c = r["crees"][0]
    return {"ecriture": c["name"], "statut": _("Validée"), "date": c["date"], "libelle": c["libelle"], "montant_eur": c["eur"],
            "taux": taux, "source_taux": source, "montant_tnd": c["tnd"], "compte_charge": compte_charge,
            "credit": DE.COMPTE_EUROS, "url": _url("Journal Entry", c["name"])}


# ── Rendez-vous (calendrier des tâches, portail de réservation) ───────────────

ETATS = {"Open": "Prévu", "Completed": "Terminé", "Cancelled": "Annulé"}
TYPES_RDV = ("Entretien", "Réparation")


def _taches(cond: str, vals: tuple, limite: int = 60) -> list[dict]:
    rows = frappe.db.sql(f"""select t.name, t.starts_on, t.ends_on, t.custom_type_dintervention as type_rdv, t.status,
                                    t.custom_client as client, t.nom_client, t.secteur, t.details_adresse as adresse, t.tel,
                                    e.employee_name as employe, t.commande_client as commande
                             from `tabTache de travail` t left join tabEmployee e on e.name = t.custom_choix_du_staff
                             where {cond} order by t.starts_on limit {int(limite)}""", vals, as_dict=True)
    return [{"tache": r.name, "debut": str(r.starts_on)[:16] if r.starts_on else None, "fin": str(r.ends_on)[11:16] if r.ends_on else None,
             "type": r.type_rdv, "etat": ETATS.get(r.status, r.status), "client": r.client, "nom_client": r.nom_client,
             "employe": r.employe, "secteur": r.secteur, "adresse": r.adresse, "telephone": r.tel, "commande": r.commande,
             "url": _url("Tache de travail", r.name)} for r in rows]


@frappe.whitelist()
def rendez_vous(date=None, jours=1, client=None):
    """Les rendez-vous (tâches de travail) d'un jour — ou des `jours` jours à partir de `date` — ou ceux d'un client
    (passés et à venir, les 20 derniers)."""
    _acces()
    if client:
        return _taches("t.custom_client = %s and t.status != 'Cancelled'", (client,), 20)[::-1]
    d = getdate(date or nowdate())
    return _taches("t.starts_on >= %s and t.starts_on < %s and t.status != 'Cancelled'",
                   (str(d) + " 00:00:00", str(add_days(d, max(1, cint(jours)))) + " 00:00:00"))


def _seance_portail(client):
    """Une séance du portail de réservation AU NOM du client, comme l'opératrice au téléphone : mêmes règles
    (secteur, capacité de la demi-journée, délais, types), SMS et e-mail de confirmation au client."""
    from customization_app import portail_rdv as PR
    from customization_app import portail_rdv_agent as PA
    PR._config()
    entree = PA._client_utilisable(client)
    return PR._ouvrir_session(PA._telephone(client), entree, agent="ChatGPT (%s)" % frappe.session.user, origine=None)


def _adresses(seance) -> list[dict]:
    out = []
    for a in seance.get("adresses") or []:
        if isinstance(a, dict):      # portail_rdv._adresses_du_client : adresse (nom), ligne, ville, gouvernorat, secteur
            out.append({"adresse": a.get("adresse") or a.get("name"),
                        "libelle": ", ".join(x for x in (a.get("ligne"), a.get("ville"), a.get("gouvernorat")) if x) or a.get("adresse"),
                        "secteur": a.get("secteur")})
        else:
            out.append({"adresse": a, "libelle": a, "secteur": None})
    return out


def _adresse_choisie(seance, adresse):
    """Une seule adresse → elle ; plusieurs sans choix → on les renvoie pour que l'assistant demande."""
    adresses = _adresses(seance)
    if adresse:
        return adresse, adresses
    if len(adresses) == 1:
        return adresses[0]["adresse"], adresses
    return None, adresses


@frappe.whitelist()
def creneaux_rendez_vous(client, type_intervention="Entretien", adresse=None):
    """Les demi-journées possibles pour ce client (adresse par défaut ou `adresse`), à proposer avant de réserver.
    Plusieurs adresses sans choix : la réponse les liste (`choisir_adresse`) au lieu de créneaux."""
    _acces()
    if type_intervention not in TYPES_RDV:
        frappe.throw(_("Type de rendez-vous : {0}.").format(" ou ".join(TYPES_RDV)))
    from customization_app import portail_rdv as PR
    seance = _seance_portail(client)
    choisie, adresses = _adresse_choisie(seance, adresse)
    base = {"client": seance["nom"], "adresses": adresses, "rendez_vous_existants": seance.get("rendez_vous")}
    if not choisie:
        return dict(base, choisir_adresse=True, jours=[], message=_("Ce client a plusieurs adresses : demandez laquelle."))
    dispo = PR.disponibilites(seance["jeton"], adresse=choisie, type_intervention=type_intervention)
    return dict(base, adresse=choisie, secteur=dispo.get("secteur"), hors_secteur=dispo.get("hors_secteur"), horaires=dispo.get("horaires"),
                ouvert_le=dispo.get("ouvert_le"), jours=dispo.get("jours"))


@frappe.whitelist(methods=["POST"])
def creer_rendez_vous(client, type_intervention, date, demi_journee, adresse=None, note=None):
    """Réserve un rendez-vous comme le portail : `demi_journee` = matin ou apres_midi, `date` AAAA-MM-JJ parmi les
    créneaux de creneaux_rendez_vous. Le client reçoit le SMS / e-mail de confirmation."""
    _acces()
    if type_intervention not in TYPES_RDV:
        frappe.throw(_("Type de rendez-vous : {0}.").format(" ou ".join(TYPES_RDV)))
    from customization_app import portail_rdv as PR
    seance = _seance_portail(client)
    choisie, adresses = _adresse_choisie(seance, adresse)
    if not choisie:
        frappe.throw(_("Ce client a plusieurs adresses, précisez-la : {0}.").format(" ; ".join(a["libelle"] for a in adresses)))
    r = PR.reserver(seance["jeton"], type_intervention, date, demi_journee, adresse=choisie,
                    note=((note or "").strip() + " — " + MARQUE).strip(" —"))
    r = dict(r or {})
    if r.get("tache"):
        r["url"] = _url("Tache de travail", r["tache"])
    return r


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
            base + "banques": _op("banques", "Comptes bancaires où verser et comptes d'espèces", {}),
            base + "verser_en_banque": _op("verser_en_banque", "Versement d'espèces d'une caisse vers une banque (écriture validée par défaut)", {
                "date": ["string", "Date du versement AAAA-MM-JJ"], "montant": ["number", "Montant en dinars"],
                "banque": ["string", "Compte bancaire exact renvoyé par banques (ex. BIAT - A&S)"],
                "compte_especes": ["string", "Caisse d'origine, Sabah Espèces - A&S par défaut"],
                "reference": ["string", "Référence du bordereau ou de l'opération bancaire"],
                "valider": ["integer", "1 (défaut) pour valider, 0 pour laisser en brouillon"],
            }, post=True, requis=["date", "montant", "banque"]),
            base + "saisir_depense_euros": _op("saisir_depense_euros", "Dépense payée en euros depuis le compte en euros (pub Google, abonnement) : taux du jour automatique, écriture validée", {
                "date": ["string", "Date AAAA-MM-JJ"], "libelle": ["string", "Objet de la dépense"],
                "montant_eur": ["number", "Montant en euros"],
                "compte_charge": ["string", "Compte de charge, Frais de Marketing - A&S par défaut"],
                "taux": ["number", "Taux EUR→TND à imposer ; vide = taux du jour sur internet"],
            }, post=True, requis=["date", "libelle", "montant_eur"]),
            base + "rendez_vous": _op("rendez_vous", "Rendez-vous d'un jour (ou de plusieurs jours), ou tous ceux d'un client", {
                "date": ["string", "Jour AAAA-MM-JJ (aujourd'hui par défaut)"], "jours": ["integer", "Nombre de jours à partir de la date, 1 par défaut"],
                "client": ["string", "Nom exact du client (renvoyé par chercher_client) pour ses rendez-vous passés et à venir"],
            }, requis=[]),
            base + "creneaux_rendez_vous": _op("creneaux_rendez_vous", "Demi-journées disponibles pour un client avant de réserver", {
                "client": ["string", "Nom exact du client"], "type_intervention": ["string", "Entretien ou Réparation"],
                "adresse": ["string", "Adresse du client si plusieurs (vide = la principale)"],
            }, requis=["client"]),
            base + "creer_rendez_vous": _op("creer_rendez_vous", "Réserver un rendez-vous pour un client (le client reçoit un SMS de confirmation)", {
                "client": ["string", "Nom exact du client"], "type_intervention": ["string", "Entretien ou Réparation"],
                "date": ["string", "Jour AAAA-MM-JJ parmi les créneaux"], "demi_journee": ["string", "matin ou apres_midi"],
                "adresse": ["string", "Adresse si plusieurs"], "note": ["string", "Note pour le technicien"],
            }, post=True, requis=["client", "type_intervention", "date", "demi_journee"]),
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
demande. Pour le stock ou un client, utilise stock_article / chercher_client et réponds brièvement.
Dépense payée en euros (pub Google, abonnement) : saisir_depense_euros, le taux du jour est pris automatiquement.
Versement d'espèces à la banque : banques pour les comptes, puis verser_en_banque après confirmation (date, montant, banque, référence).
Rendez-vous : rendez_vous pour voir ceux d'un jour ou d'un client ; pour en prendre un, chercher_client puis
creneaux_rendez_vous, propose 2 ou 3 demi-journées, et seulement après accord creer_rendez_vous (le client reçoit un SMS)."""
