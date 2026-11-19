"""Échange d'articles avec un client livré par Aramex (demande du 19/11/2026).

LE CAS QUI L'A FAIT NAÎTRE. Warghui Ridha reçoit 3 robinets Acc020-B (WEB1-008697) ; ce n'était pas le bon modèle.
On lui renvoie 3 Acc020-A par Aramex sur une commande qui ne porte que « Livraison » (SAL-ORD-2026-04691), le
livreur reprend les 3 Acc020-B dans une pochette. Jusqu'ici : une consigne écrite dans la tâche, les Acc020-A partis
sans aucun document (stock faux pendant 4 jours), une correction de stock à la main à l'arrivée de la pochette
(MAT-STE-2026-00311) — et, faute de carte où cliquer, un « Retour reçu » constaté sur le colis d'un AUTRE client.

LA RÈGLE (ses mots : « à l'échange tu sors directement ce qui est échangé, et au retour tu fais entrer ce qui
rentre ») :
  1. DÉCLARER l'échange depuis la commande (bouton « 🔁 Échange d'articles ») : ce que j'envoie, ce que le client me
     renvoie. Ce que j'envoie SORT du stock tout de suite (écriture « Échange - envoi »).
  2. Tant que la pochette n'est pas revenue, l'échange est en tête de l'écran Livraisons Aramex — « retour attendu »,
     en alerte s'il traîne après la livraison.
  3. « 📦 Échange reçu » : la photo de la pochette est VÉRIFIÉE (IA + API Aramex, comme un retour de colis :
     `retour_aramex.verifier`), puis ce qui revient ENTRE en stock (écriture « Échange - retour »).
La facturation n'est pas touchée : le client a payé ses articles, seuls les modèles s'échangent.

Des écritures de stock, pas des bons de livraison : un BL de retour contre la commande d'origine la ferait passer pour
« retournée » et fausserait ce qui reste à facturer. Les échanges faits SUR PLACE avec un article « E-… » restent
l'affaire de `retour_echange`.

`lignes_propres`, `texte_lignes`, `jours_de_retard` sont PURES (testées sans site).
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


# ── Outils ────────────────────────────────────────────────────────────────────

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


def _ecriture_stock(type_ecriture, lignes, entrepot, remarque):
    """Une écriture de stock soumise : sortie (« envoi ») ou entrée (« retour ») des lignes dans l'entrepôt."""
    nom, but = type_ecriture
    doc = frappe.new_doc("Stock Entry")
    doc.update({"stock_entry_type": _type_ecriture(type_ecriture), "purpose": but, "remarks": remarque,
                "company": frappe.db.get_value("Warehouse", entrepot, "company")})
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
    recus = frappe.db.sql(
        """SELECT dn.name AS bl, dn.posting_date, dni.against_sales_order AS commande, dni.item_code, dni.item_name,
                  SUM(dni.qty) AS qty
           FROM `tabDelivery Note` dn JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
           JOIN `tabItem` i ON i.name = dni.item_code AND i.is_stock_item = 1
           WHERE dn.customer = %(c)s AND dn.docstatus = 1 AND dn.is_return = 0
             AND dn.posting_date >= DATE_SUB(CURDATE(), INTERVAL 120 DAY)
             AND IFNULL(dni.against_sales_order, '') <> %(so)s
           GROUP BY dn.name, dni.item_code ORDER BY dn.posting_date DESC, dn.name DESC, dni.idx""",
        {"c": so.customer, "so": commande}, as_dict=True)
    dernier = recus[0].bl if recus else None
    return {
        "commande": so.name, "client": so.customer_name or so.customer, "bordereau": so.custom_bordereau_aramex or "",
        "existant": existant, "entrepot": _entrepot_defaut(),
        # Le dernier bon de livraison du client : c'est presque toujours lui qu'on corrige.
        "attendus": [{"item_code": r.item_code, "item_name": r.item_name, "qty": flt(r.qty)} for r in recus if r.bl == dernier],
        "commande_origine": (recus[0].commande if recus else None), "bl_origine": dernier,
        "date_origine": str(recus[0].posting_date) if recus else None,
    }


@frappe.whitelist(methods=["POST"])
def declarer(commande, envoyes, attendus, entrepot=None, commande_origine=None, note=None):
    """Déclare l'échange : ce qui part SORT du stock maintenant ; ce qui revient est attendu."""
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

    doc = frappe.get_doc({
        "doctype": DOCTYPE, "statut": ATTENDU, "commande": commande, "client": so.customer,
        "nom_client": so.customer_name or so.customer, "bordereau": so.custom_bordereau_aramex or None,
        "commande_origine": commande_origine or None, "entrepot": entrepot, "note": (note or "").strip() or None,
        "envoyes": envoyes, "attendus": attendus, "envoye_le": now_datetime(), "envoye_par": frappe.session.user,
    })
    doc.flags.ignore_permissions = True
    doc.insert()
    sortie = _ecriture_stock(TYPE_ENVOI, envoyes, entrepot,
                             "Échange %s — %s (commande %s) : envoyé au client" % (doc.name, doc.nom_client, commande))
    doc.db_set("sortie_stock", sortie, update_modified=False)
    _commenter("Sales Order", commande, _(
        "🔁 Échange {0} déclaré par {1} : envoyé {2} (sorti de {3}, écriture {4}) — retour attendu : {5}.").format(
        doc.name, frappe.session.user, texte_lignes(envoyes), entrepot, sortie, texte_lignes(attendus)))
    frappe.db.commit()
    return {"echange": doc.name, "sortie_stock": sortie}


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
    resultat["empreinte"] = hashlib.sha1((photo or "").encode()).hexdigest()
    resultat["peut_forcer"] = bool(set(frappe.get_roles()) & set(R.ROLES_FORCER))
    frappe.cache().set_value(_cle(echange), resultat, expires_in_sec=VERIFICATION_TTL)
    return resultat


@frappe.whitelist(methods=["POST"])
def recevoir(echange, photo, photo_nom=None, note=None, forcer=0):
    """La pochette est revenue : photo VÉRIFIÉE, puis ce que le client renvoie ENTRE en stock."""
    frappe.only_for(ROLES)
    from customization_app import retour_aramex as R

    doc = _echange_attendu(echange)
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
    entree = _ecriture_stock(TYPE_RETOUR, attendus, doc.entrepot,
                             "Échange %s — %s (commande %s) : revenu du client" % (doc.name, doc.nom_client, doc.commande))

    from frappe.utils.file_manager import save_file
    contenu = base64.b64decode(photo.split(",", 1)[-1])
    nom_fichier = photo_nom or "echange-%s.jpg" % doc.name
    fichier = save_file(nom_fichier, contenu, DOCTYPE, doc.name, is_private=1)
    save_file(nom_fichier, contenu, "Sales Order", doc.commande, is_private=1)

    resume = R._resume_verification(verification, force) if reference else "Échange sans bordereau Aramex : photo non comparée."
    frappe.db.set_value(DOCTYPE, doc.name, {
        "statut": RECU, "retour_stock": entree, "recu_le": now_datetime(), "recu_par": frappe.session.user,
        "bordereau": reference or None, "verification": resume,
        "note": " — ".join(filter(None, [doc.note, (note or "").strip()])) or None})
    _commenter("Sales Order", doc.commande, _(
        "📦 Échange {0} reçu le {1} par {2} : {3} rentré(s) dans {4} (écriture {5}). {6}{7}").format(
        doc.name, str(now_datetime())[:16], frappe.session.user, texte_lignes(attendus), doc.entrepot, entree, resume,
        (" Note : %s" % note.strip()) if (note or "").strip() else ""))
    frappe.db.commit()
    return {"echange": doc.name, "retour_stock": entree, "entrepot": doc.entrepot, "articles": texte_lignes(attendus),
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
                            fields=["name", "commande", "nom_client", "bordereau", "envoye_le", "entrepot"]):
        doc = frappe.get_doc(DOCTYPE, e.name)
        reference = _bordereau(doc)
        suivi = frappe.db.get_value(DOCTYPE_SUIVI, reference, ["statut", "livre", "derniere_date", "livre_notifie_le"],
                                    as_dict=True) if reference else None
        livre_le = (suivi.livre_notifie_le if suivi and cint(suivi.livre) else None)
        out.append({
            "name": e.name, "commande": e.commande, "client": e.nom_client, "bordereau": reference,
            "envoyes": texte_lignes([{"item_code": l.item_code, "qty": l.qty} for l in doc.envoyes]),
            "attendus": texte_lignes([{"item_code": l.item_code, "qty": l.qty} for l in doc.attendus]),
            "declare_le": str(e.envoye_le or "")[:10],
            "suivi": (suivi.statut if suivi else ""), "livre": bool(suivi and cint(suivi.livre)),
            "retard": jours_de_retard(livre_le, nowdate()),
        })
    return out
