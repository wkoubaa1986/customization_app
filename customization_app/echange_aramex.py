"""Échange d'articles avec un client livré par Aramex (demande du 19/11/2026).

LE CAS QUI L'A FAIT NAÎTRE. Warghui Ridha reçoit 3 robinets Acc020-B (WEB1-008697) ; ce n'était pas le bon modèle.
On lui renvoie 3 Acc020-A par Aramex sur une commande qui ne porte que « Livraison » (SAL-ORD-2026-04691), le
livreur reprend les 3 Acc020-B dans une pochette. Jusqu'ici : une consigne écrite dans la tâche, les Acc020-A partis
sans aucun document (stock faux pendant 4 jours), une correction de stock à la main à l'arrivée de la pochette
(MAT-STE-2026-00311) — et, faute de carte où cliquer, un « Retour reçu » constaté sur le colis d'un AUTRE client.

LA RÈGLE (ses mots : « à l'échange tu sors directement ce qui est échangé, et au retour tu fais entrer ce qui
rentre ») :
  1. DÉCLARER l'échange depuis la commande (bouton « 🔁 Échange d'articles ») : ce que j'envoie, ce que le client me
     renvoie. Ce que j'envoie SORT du stock (écriture « Échange - envoi »), à la date de VALIDATION de la commande :
     c'est ce jour-là que le colis a été préparé, même si l'échange est déclaré après coup.
  2. Tant que la pochette n'est pas revenue, l'échange est en tête de l'écran Livraisons Aramex — « retour attendu »,
     en alerte s'il traîne après la livraison.
  3. « 📦 Échange reçu » : la photo de la pochette est VÉRIFIÉE (IA + API Aramex, comme un retour de colis :
     `retour_aramex.verifier`), puis ce qui revient ENTRE en stock (écriture « Échange - retour »).
La facturation n'est pas touchée : le client a payé ses articles, seuls les modèles s'échangent.

Des écritures de stock, pas des bons de livraison : un BL de retour contre la commande d'origine la ferait passer pour
« retournée » et fausserait ce qui reste à facturer. Les échanges faits SUR PLACE avec un article « E-… » restent
l'affaire de `retour_echange`.

`lignes_propres`, `texte_lignes`, `jours_de_retard`, `date_de_validation`, `livraisons_du_client`,
`ecarts_avec_le_bl` sont PURES (testées sans site).
"""
from __future__ import annotations

import base64
import hashlib
import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime, nowdate

DOCTYPE = "Echange Aramex"
ATTENDU, RECU, ANNULE = "Retour attendu", "Reçu", "Annulé"
#: Qui déclare et reçoit un échange : ceux qui constatent déjà les retours, et le magasin.
ROLES = ("System Manager", "Accounts Manager", "Accounts User", "Sales Manager", "Sales User",
         "Stock Manager", "Stock User")
TYPE_ENVOI = ("Échange - envoi", "Material Issue")
TYPE_RETOUR = ("Échange - retour", "Material Receipt")
JOURS_ALERTE = 7             # après la livraison au client : la pochette devrait être revenue
VERIFICATION_TTL = 1800


# ── Règles pures ──────────────────────────────────────────────────────────────

def lignes_propres(lignes) -> list:
    """[{item_code, qty}] sans ligne vide, quantités > 0, un article répété additionné. PURE."""
    if isinstance(lignes, str):
        lignes = json.loads(lignes or "[]")
    total = {}
    for l in lignes or []:
        code = ((l or {}).get("item_code") or "").strip()
        qte = flt((l or {}).get("qty"))
        if code and qte > 0:
            total[code] = total.get(code, 0) + qte
    return [{"item_code": c, "qty": q} for c, q in total.items()]


def texte_lignes(lignes) -> str:
    """« 3 × Acc020-A, 1 × Acc021 ». PURE."""
    return ", ".join("%s × %s" % (("%g" % flt(l.get("qty"))), l.get("item_code")) for l in lignes or [])


def jours_de_retard(livre_le, aujourd_hui, tolerance=JOURS_ALERTE) -> int:
    """Depuis combien de jours la pochette aurait dû revenir (0 = pas en retard, ou colis pas encore livré). PURE."""
    if not livre_le:
        return 0
    ecart = (getdate(aujourd_hui) - getdate(livre_le)).days - tolerance
    return max(ecart, 0)


def livraisons_du_client(lignes) -> list:
    """Les bons de livraison récents d'un client, du plus récent au plus ancien, chacun avec ses articles :
    [{"bl", "date", "commande", "articles": [{item_code, item_name, qty}], "resume"}]. PURE.
    `lignes` : une ligne par (BL, article), dans l'ordre voulu des BL."""
    par_bl = {}
    for l in lignes or []:
        bl = par_bl.setdefault(l["bl"], {"bl": l["bl"], "date": str(l.get("posting_date") or ""),
                                         "commande": l.get("commande") or "", "articles": []})
        bl["articles"].append({"item_code": l["item_code"], "item_name": l.get("item_name") or "", "qty": flt(l.get("qty"))})
    for bl in par_bl.values():
        bl["resume"] = texte_lignes(bl["articles"])
    return list(par_bl.values())


def ecarts_avec_le_bl(attendus, articles_du_bl) -> list:
    """Ce que le client ne peut PAS renvoyer de ce bon de livraison : un article qui n'y est pas, ou plus que ce
    qu'il a reçu. -> liste de phrases (vide = tout est cohérent). PURE."""
    livre = {}
    for a in articles_du_bl or []:
        livre[a["item_code"]] = livre.get(a["item_code"], 0) + flt(a.get("qty"))
    out = []
    for l in attendus or []:
        if l["item_code"] not in livre:
            out.append("%s n'est pas sur ce bon de livraison" % l["item_code"])
        elif flt(l["qty"]) > livre[l["item_code"]]:
            out.append("%s : %g à renvoyer pour %g livré(s)" % (l["item_code"], flt(l["qty"]), livre[l["item_code"]]))
    return out


def date_de_validation(versions):
    """Le moment où la commande a été VALIDÉE, lu dans son historique (docstatus 0 → 1). -> « AAAA-MM-JJ HH:MM:SS »
    ou None. `versions` : [{"creation", "data"}] ; la première validation fait foi (une commande annulée puis
    refaite porte un autre nom). PURE."""
    for v in sorted(versions or [], key=lambda x: str(x.get("creation"))):
        try:
            changes = json.loads(v.get("data") or "{}").get("changed") or []
        except Exception:
            continue
        if any(len(c) >= 3 and c[0] == "docstatus" and cint(c[1]) == 0 and cint(c[2]) == 1 for c in changes):
            return str(v.get("creation"))[:19]
    return None


# ── Outils ────────────────────────────────────────────────────────────────────

def _date_de_sortie(commande):
    """La date de la sortie de stock = celle de la VALIDATION de la commande (décision du 19/11/2026) : c'est ce
    jour-là que le colis a été préparé. Un échange déclaré après coup ne date donc pas la sortie d'aujourd'hui.
    Commande encore en brouillon, ou validation introuvable dans l'historique : maintenant."""
    if frappe.db.get_value("Sales Order", commande, "docstatus") != 1:
        return None
    versions = frappe.db.sql("""SELECT creation, data FROM `tabVersion` WHERE ref_doctype = 'Sales Order'
                                AND docname = %s AND data LIKE '%%docstatus%%' ORDER BY creation""", (commande,), as_dict=True)
    le = date_de_validation(versions)
    # jamais dans le futur (horloge, fuseau) : une écriture de stock datée de demain serait refusée
    return le if le and le <= str(now_datetime())[:19] else None


def _type_ecriture(nom_et_but) -> str:
    """Le type d'écriture de stock dédié, créé une fois : les échanges se retrouvent ainsi d'un filtre."""
    nom, but = nom_et_but
    if not frappe.db.exists("Stock Entry Type", nom):
        frappe.get_doc({"doctype": "Stock Entry Type", "name": nom, "purpose": but}).insert(ignore_permissions=True)
    return nom


def _entrepot_defaut() -> str:
    from customization_app.aramex_stock import ENTREPOT_ARAMEX
    return ENTREPOT_ARAMEX


def _stock(code, entrepot) -> float:
    return flt(frappe.db.get_value("Bin", {"item_code": code, "warehouse": entrepot}, "actual_qty"))


def _verifier_articles(lignes, entrepot=None):
    """Des articles suivis en stock ; et, si `entrepot`, disponibles dans cet entrepôt."""
    for l in lignes:
        fiche = frappe.db.get_value("Item", l["item_code"], ["item_name", "is_stock_item", "disabled"], as_dict=True)
        if not fiche or fiche.disabled:
            frappe.throw(_("Article introuvable ou désactivé : {0}").format(l["item_code"]))
        if not cint(fiche.is_stock_item):
            frappe.throw(_("{0} n'est pas un article suivi en stock : il ne s'échange pas ici.").format(l["item_code"]))
        l["item_name"] = fiche.item_name
        if entrepot and _stock(l["item_code"], entrepot) < l["qty"]:
            frappe.throw(_("Stock insuffisant dans {0} : {1} — {2} disponible(s) pour {3} à envoyer.").format(
                entrepot, l["item_code"], "%g" % _stock(l["item_code"], entrepot), "%g" % l["qty"]))


def _ecriture_stock(type_ecriture, lignes, entrepot, remarque, le=None):
    """Une écriture de stock soumise : sortie (« envoi ») ou entrée (« retour ») des lignes dans l'entrepôt.
    `le` (« AAAA-MM-JJ HH:MM:SS ») : la date à lui donner ; sinon maintenant."""
    nom, but = type_ecriture
    doc = frappe.new_doc("Stock Entry")
    doc.update({"stock_entry_type": _type_ecriture(type_ecriture), "purpose": but, "remarks": remarque,
                "company": frappe.db.get_value("Warehouse", entrepot, "company")})
    if le:
        doc.update({"set_posting_time": 1, "posting_date": le[:10], "posting_time": le[11:19] or "00:00:00"})
    sortie = but == "Material Issue"
    for l in lignes:
        ligne = {"item_code": l["item_code"], "qty": l["qty"]}
        ligne["s_warehouse" if sortie else "t_warehouse"] = entrepot
        if not sortie:
            # Ce qui revient entre à sa valeur de stock actuelle ; un article jamais valorisé entre à zéro
            # plutôt que de bloquer le retour.
            taux = flt(frappe.db.get_value("Bin", {"item_code": l["item_code"], "warehouse": entrepot}, "valuation_rate")) \
                or flt(frappe.db.get_value("Item", l["item_code"], "valuation_rate"))
            ligne.update({"basic_rate": taux, "allow_zero_valuation_rate": 0 if taux else 1})
        doc.append("items", ligne)
    doc.flags.ignore_permissions = True
    doc.insert()
    doc.submit()
    return doc.name


def _commenter(doctype, name, texte):
    frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": doctype,
                    "reference_name": name, "content": texte}).insert(ignore_permissions=True)


def echange_ouvert_de(commande):
    """L'échange en attente de retour porté par cette commande, ou None."""
    if not commande or not frappe.db.table_exists(DOCTYPE):
        return None
    return frappe.db.get_value(DOCTYPE, {"commande": commande, "statut": ATTENDU}, "name")


def _bordereau(doc) -> str:
    """Le bordereau de l'envoi : celui gardé sur l'échange, sinon celui que la commande a reçu depuis."""
    from customization_app.livraison_aramex import reference_aramex

    return reference_aramex(doc.bordereau or frappe.db.get_value("Sales Order", doc.commande, "custom_bordereau_aramex")
                            or "") or ""


def bordereau_enregistre(commande, numero):
    """Appelé quand une commande reçoit son bordereau Aramex : l'échange qu'elle porte le retient. Jamais bloquant."""
    try:
        nom = echange_ouvert_de(commande)
        if nom:
            frappe.db.set_value(DOCTYPE, nom, "bordereau", numero, update_modified=False)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Échange Aramex : bordereau non reporté")


# ── Déclarer ──────────────────────────────────────────────────────────────────

JOURS_LIVRAISONS = 180       # jusqu'où remonter pour proposer le bon de livraison à corriger
MAX_LIVRAISONS = 15


def _livraisons(client, sauf_commande=None, bl=None) -> list:
    """Les bons de livraison validés du client (articles suivis en stock seulement — « Livraison » n'en est pas),
    hors la commande de l'échange elle-même. `bl` : celui-là seulement, sans limite de date."""
    lignes = frappe.db.sql(
        """SELECT dn.name AS bl, dn.posting_date, MAX(dni.against_sales_order) AS commande, dni.item_code,
                  MAX(dni.item_name) AS item_name, SUM(dni.qty) AS qty
           FROM `tabDelivery Note` dn JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
           JOIN `tabItem` i ON i.name = dni.item_code AND i.is_stock_item = 1
           WHERE dn.customer = %(c)s AND dn.docstatus = 1 AND dn.is_return = 0
             AND (%(bl)s <> '' AND dn.name = %(bl)s
                  OR %(bl)s = '' AND dn.posting_date >= DATE_SUB(CURDATE(), INTERVAL %(jours)s DAY))
             AND IFNULL(dni.against_sales_order, '') <> %(so)s
           GROUP BY dn.name, dni.item_code
           ORDER BY dn.posting_date DESC, dn.name DESC, MIN(dni.idx)""",
        {"c": client, "so": sauf_commande or "", "bl": bl or "", "jours": JOURS_LIVRAISONS}, as_dict=True)
    return livraisons_du_client(lignes)[:MAX_LIVRAISONS]


@frappe.whitelist()
def proposer(commande):
    """Ce que le dialogue de la commande doit savoir : l'échange déjà déclaré s'il y en a un, sinon ce que le client
    a reçu dernièrement (les articles qu'il est susceptible de renvoyer)."""
    frappe.only_for(ROLES)
    so = frappe.db.get_value("Sales Order", commande, ["name", "customer", "customer_name", "docstatus",
                                                       "custom_bordereau_aramex"], as_dict=True)
    if not so:
        frappe.throw(_("Commande introuvable."))
    existant = frappe.db.get_value(DOCTYPE, {"commande": commande, "statut": ["!=", ANNULE]}, ["name", "statut"], as_dict=True)
    livraisons = _livraisons(so.customer, sauf_commande=commande)
    dernier = livraisons[0] if livraisons else None
    return {
        "commande": so.name, "client": so.customer_name or so.customer, "bordereau": so.custom_bordereau_aramex or "",
        "existant": existant, "entrepot": _entrepot_defaut(),
        # La sortie de stock portera la date de validation de la commande : l'écran le dit avant de déclarer.
        "date_sortie": _date_de_sortie(commande),
        # Les bons de livraison du client : l'utilisateur choisit celui que l'échange corrige. Le dernier est
        # proposé d'office — c'est presque toujours lui.
        "livraisons": livraisons,
        "attendus": dernier["articles"] if dernier else [],
        "commande_origine": dernier["commande"] if dernier else None,
        "bl_origine": dernier["bl"] if dernier else None,
        "date_origine": dernier["date"] if dernier else None,
    }


@frappe.whitelist(methods=["POST"])
def declarer(commande, envoyes, attendus, entrepot=None, commande_origine=None, note=None, bl_origine=None):
    """Déclare l'échange : ce qui part SORT du stock ; ce qui revient est attendu.
    `bl_origine` : le bon de livraison que l'échange corrige — ce qui revient doit en venir."""
    frappe.only_for(ROLES)
    so = frappe.db.get_value("Sales Order", commande, ["name", "customer", "customer_name", "docstatus",
                                                       "custom_bordereau_aramex"], as_dict=True)
    if not so or so.docstatus == 2:
        frappe.throw(_("Commande introuvable ou annulée."))
    if frappe.db.exists(DOCTYPE, {"commande": commande, "statut": ["!=", ANNULE]}):
        frappe.throw(_("Un échange est déjà déclaré sur cette commande."))
    envoyes, attendus = lignes_propres(envoyes), lignes_propres(attendus)
    if not envoyes:
        frappe.throw(_("Indiquez ce que vous envoyez au client."))
    if not attendus:
        frappe.throw(_("Indiquez ce que le client vous renvoie."))
    entrepot = entrepot or _entrepot_defaut()
    if not frappe.db.exists("Warehouse", {"name": entrepot, "is_group": 0, "disabled": 0}):
        frappe.throw(_("Entrepôt inconnu : {0}").format(entrepot))
    _verifier_articles(attendus)
    _verifier_articles(envoyes, entrepot)
    bl_origine = (bl_origine or "").strip() or None
    if bl_origine:
        # Le bon de livraison choisi est bien celui de CE client, et ce qui revient en vient vraiment.
        du_bl = _livraisons(so.customer, sauf_commande=commande, bl=bl_origine)
        if not du_bl:
            frappe.throw(_("Le bon de livraison {0} n'est pas un bon validé de ce client.").format(bl_origine))
        ecarts = ecarts_avec_le_bl(attendus, du_bl[0]["articles"])
        if ecarts:
            frappe.throw(_("Ce que le client renvoie ne correspond pas au bon de livraison {0} : {1}.")
                         .format(bl_origine, " ; ".join(ecarts)))
        commande_origine = du_bl[0]["commande"] or commande_origine

    le = _date_de_sortie(commande)
    doc = frappe.get_doc({
        "doctype": DOCTYPE, "statut": ATTENDU, "commande": commande, "client": so.customer,
        "nom_client": so.customer_name or so.customer, "bordereau": so.custom_bordereau_aramex or None,
        "commande_origine": commande_origine or None, "bl_origine": bl_origine,
        "entrepot": entrepot, "note": (note or "").strip() or None,
        "envoyes": envoyes, "attendus": attendus, "envoye_le": le or now_datetime(), "envoye_par": frappe.session.user,
    })
    doc.flags.ignore_permissions = True
    doc.insert()
    sortie = _ecriture_stock(TYPE_ENVOI, envoyes, entrepot,
                             "Échange %s — %s (commande %s) : envoyé au client" % (doc.name, doc.nom_client, commande),
                             le=le)
    doc.db_set("sortie_stock", sortie, update_modified=False)
    _commenter("Sales Order", commande, _(
        "🔁 Échange {0} déclaré par {1} : envoyé {2} (sorti de {3}, écriture {4} datée du {5}) — retour attendu : {6}{7}.").format(
        doc.name, frappe.session.user, texte_lignes(envoyes), entrepot, sortie,
        (le or str(now_datetime()))[:16], texte_lignes(attendus),
        (" (du bon de livraison %s)" % bl_origine) if bl_origine else ""))
    frappe.db.commit()
    return {"echange": doc.name, "sortie_stock": sortie, "date_sortie": (le or str(now_datetime()))[:16]}


# ── Le bordereau de retour : le numéro de la pochette ─────────────────────────
#
# La pochette qui revient ne porte PAS le bordereau de notre envoi : Aramex lui en donne un nouveau, annoncé dans le
# suivi de notre colis (« Rtn HAWB 55087851044 »). C'est ce numéro-là qui est imprimé sur son étiquette — donc celui
# que l'employé peut comparer de ses yeux, et que la lecture de la photo retrouve. On le garde sur l'échange.

def _noter_retour(nom, numeros, statut="", date=""):
    """Garde sur l'échange le bordereau de retour annoncé et où il en est. Sans effet s'il n'y en a pas."""
    numeros = sorted(n for n in (numeros or []) if n)
    if not numeros:
        return
    suivi = " ".join(filter(None, [statut, ("le %s" % date) if date and statut else date]))
    frappe.db.set_value(DOCTYPE, nom, {"bordereau_retour": ", ".join(numeros), "retour_suivi": suivi[:140] or None},
                        update_modified=False)


@frappe.whitelist()
def suivre_retours():
    """Demande à Aramex, pour chaque échange en attente, le bordereau de la pochette et s'il nous a été livré.
    Appelé APRÈS l'affichage de l'écran (qui s'ouvre sans attendre le transporteur). -> la liste à jour."""
    frappe.only_for(ROLES)
    from customization_app import retour_aramex as R

    for nom in frappe.get_all(DOCTYPE, filters={"statut": ATTENDU}, pluck="name"):
        try:
            reference = _bordereau(frappe.get_doc(DOCTYPE, nom))
            if not reference:
                continue
            numeros = R.numeros_de_retour(R._suivi_frais(reference, timeout=20))
            statut, date = "", ""
            for numero in sorted(numeros):
                etat = R._suivi_frais(numero, timeout=20)
                statut = ("Livré chez nous" if etat.get("livre") else etat.get("statut")) or statut
                date = ((etat.get("evenements") or [{}])[0].get("date") or "") or date
            _noter_retour(nom, numeros, statut, date)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Échange Aramex : suivi de la pochette")
    frappe.db.commit()
    return en_cours()


# ── Recevoir ──────────────────────────────────────────────────────────────────

def _echange_attendu(echange):
    doc = frappe.get_doc(DOCTYPE, echange)
    if doc.statut != ATTENDU:
        frappe.throw(_("L'échange {0} n'attend plus de retour (statut : {1}).").format(echange, doc.statut))
    return doc


def _cle(echange) -> str:
    return "echange_aramex_verif:%s:%s" % (frappe.session.user, echange)


@frappe.whitelist()
def verifier_photo(echange, photo):
    """Dès la photo prise : est-ce bien la pochette de CET échange ? Rien n'est écrit."""
    frappe.only_for(ROLES)
    from customization_app import retour_aramex as R

    doc = _echange_attendu(echange)
    reference = _bordereau(doc)
    if not reference:
        # Échange remis sans Aramex : aucune étiquette à comparer.
        resultat = {"verdict": "sans_bordereau", "lu": {}, "attendu": {}, "autre": None}
    else:
        resultat = R.verifier(reference, photo)
        attendu = resultat.get("attendu") or {}
        _noter_retour(doc.name, attendu.get("retours"),
                      "Livré chez nous" if attendu.get("retour_livre") else attendu.get("retour_statut") or "",
                      attendu.get("retour_date") or "")
    resultat["empreinte"] = hashlib.sha1((photo or "").encode()).hexdigest()
    resultat["peut_forcer"] = bool(set(frappe.get_roles()) & set(R.ROLES_FORCER))
    frappe.cache().set_value(_cle(echange), resultat, expires_in_sec=VERIFICATION_TTL)
    return resultat


@frappe.whitelist(methods=["POST"])
def recevoir(echange, photo, photo_nom=None, note=None, forcer=0, entrepot=None):
    """La pochette est revenue : photo VÉRIFIÉE, puis ce que le client renvoie ENTRE en stock — dans l'entrepôt
    choisi à la réception (`entrepot`), à défaut celui d'où l'envoi était parti."""
    frappe.only_for(ROLES)
    from customization_app import retour_aramex as R

    doc = _echange_attendu(echange)
    # Où ranger ce qui revient se décide en le recevant : l'article renvoyé n'a pas forcément sa place là d'où
    # l'autre est parti (demande du 19/11/2026).
    entrepot = (entrepot or "").strip() or doc.entrepot
    if not frappe.db.exists("Warehouse", {"name": entrepot, "is_group": 0, "disabled": 0}):
        frappe.throw(_("Entrepôt inconnu ou désactivé : {0}").format(entrepot))
    if not (photo or "").strip():
        frappe.throw(_("La photo de la pochette revenue est obligatoire."))
    reference = _bordereau(doc)
    empreinte = hashlib.sha1(photo.encode()).hexdigest()
    verification = frappe.cache().get_value(_cle(echange)) or {}
    if verification.get("empreinte") != empreinte:
        verification = R.verifier(reference, photo) if reference else {"verdict": "sans_bordereau"}
    force = False
    if reference:
        choix = R.decision(verification.get("verdict"), (verification.get("attendu") or {}).get("retour_livre"),
                           peut_forcer=bool(set(frappe.get_roles()) & set(R.ROLES_FORCER)), forcer=cint(forcer))
        if not choix["autorise"]:
            autre = verification.get("autre") or {}
            frappe.throw({
                "autre": _("Cette photo n'est pas celle de l'échange de {0} : l'étiquette est celle de {1}. "
                           "Rien n'a été enregistré.").format(
                    doc.nom_client, autre.get("destinataire") or autre.get("client") or _("un autre envoi")),
                "illisible": _("L'étiquette Aramex n'est pas lisible sur la photo : reprenez-la. Rien n'a été enregistré."),
            }.get(verification.get("verdict"),
                  _("La photo n'a pas pu être vérifiée et Aramex n'annonce pas ce retour comme livré chez nous. "
                    "Rien n'a été enregistré.")))
        force = choix["force"]
        if force and not (note or "").strip():
            frappe.throw(_("Écrivez dans la note pourquoi vous recevez cet échange malgré la vérification."))
    frappe.cache().delete_value(_cle(echange))

    attendus = [{"item_code": l.item_code, "qty": flt(l.qty)} for l in doc.attendus]
    entree = _ecriture_stock(TYPE_RETOUR, attendus, entrepot,
                             "Échange %s — %s (commande %s) : revenu du client" % (doc.name, doc.nom_client, doc.commande))

    from frappe.utils.file_manager import save_file
    contenu = base64.b64decode(photo.split(",", 1)[-1])
    nom_fichier = photo_nom or "echange-%s.jpg" % doc.name
    fichier = save_file(nom_fichier, contenu, DOCTYPE, doc.name, is_private=1)
    save_file(nom_fichier, contenu, "Sales Order", doc.commande, is_private=1)

    resume = R._resume_verification(verification, force) if reference else "Échange sans bordereau Aramex : photo non comparée."
    frappe.db.set_value(DOCTYPE, doc.name, {
        "statut": RECU, "retour_stock": entree, "entrepot_retour": entrepot,
        "recu_le": now_datetime(), "recu_par": frappe.session.user,
        "bordereau": reference or None, "verification": resume,
        "note": " — ".join(filter(None, [doc.note, (note or "").strip()])) or None})
    _commenter("Sales Order", doc.commande, _(
        "📦 Échange {0} reçu le {1} par {2} : {3} rentré(s) dans {4} (écriture {5}). {6}{7}").format(
        doc.name, str(now_datetime())[:16], frappe.session.user, texte_lignes(attendus), entrepot, entree, resume,
        (" Note : %s" % note.strip()) if (note or "").strip() else ""))
    frappe.db.commit()
    return {"echange": doc.name, "retour_stock": entree, "entrepot": entrepot, "articles": texte_lignes(attendus),
            "photo": fichier.file_url}


@frappe.whitelist(methods=["POST"])
def annuler(echange, motif):
    """L'échange n'aura pas lieu (colis jamais parti, client qui garde l'article…) : ce qui était sorti rentre."""
    from customization_app import retour_aramex as R

    frappe.only_for(R.ROLES_FORCER)
    doc = _echange_attendu(echange)
    if not (motif or "").strip():
        frappe.throw(_("Écrivez pourquoi l'échange est annulé."))
    if doc.sortie_stock and frappe.db.get_value("Stock Entry", doc.sortie_stock, "docstatus") == 1:
        sortie = frappe.get_doc("Stock Entry", doc.sortie_stock)
        sortie.flags.ignore_permissions = True
        sortie.cancel()
    frappe.db.set_value(DOCTYPE, doc.name, {"statut": ANNULE,
                                            "note": " — ".join(filter(None, [doc.note, "Annulé : " + motif.strip()]))})
    _commenter("Sales Order", doc.commande, _("↩️ Échange {0} annulé par {1} : {2}. Les articles envoyés sont rentrés en stock.")
               .format(doc.name, frappe.session.user, motif.strip()))
    frappe.db.commit()
    return {"echange": doc.name}


# ── Ce que l'écran Livraisons Aramex affiche ──────────────────────────────────

def en_cours() -> list:
    """Les échanges dont la pochette n'est pas encore revenue, les plus anciens d'abord."""
    if not frappe.db.table_exists(DOCTYPE):
        return []
    from customization_app.livraison_aramex import DOCTYPE_SUIVI

    out = []
    for e in frappe.get_all(DOCTYPE, filters={"statut": ATTENDU}, order_by="envoye_le asc",
                            fields=["name", "commande", "nom_client", "bordereau", "envoye_le", "entrepot",
                                    "bordereau_retour", "retour_suivi"]):
        doc = frappe.get_doc(DOCTYPE, e.name)
        reference = _bordereau(doc)
        suivi = frappe.db.get_value(DOCTYPE_SUIVI, reference, ["statut", "livre", "derniere_date", "livre_notifie_le"],
                                    as_dict=True) if reference else None
        livre_le = (suivi.livre_notifie_le if suivi and cint(suivi.livre) else None)
        out.append({
            "name": e.name, "commande": e.commande, "client": e.nom_client, "bordereau": reference,
            "envoyes": texte_lignes([{"item_code": l.item_code, "qty": l.qty} for l in doc.envoyes]),
            "attendus": texte_lignes([{"item_code": l.item_code, "qty": l.qty} for l in doc.attendus]),
            "declare_le": str(e.envoye_le or "")[:10], "entrepot": e.entrepot or "",
            "bordereau_retour": e.bordereau_retour or "", "retour_suivi": e.retour_suivi or "",
            "suivi": (suivi.statut if suivi else ""), "livre": bool(suivi and cint(suivi.livre)),
            "retard": jours_de_retard(livre_le, nowdate()),
        })
    return out
