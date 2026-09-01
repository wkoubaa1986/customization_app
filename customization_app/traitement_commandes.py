"""
Écran « Traitement des commandes » (page Desk traitement-commandes).

Toutes les commandes client depuis une date, avec sous les yeux ce qu'il faut
pour les traiter SANS ouvrir chaque fiche : client, téléphone, adresse, total
TTC, articles, tâche de travail éventuelle, et — pour une livraison Aramex —
le bordereau et son dernier suivi connu.

Les actions de la liste écrivent directement sur la commande liée :
  - « Traitée » pose custom_commande_traitee (un FAIT manuel, jamais recalculé —
    contrairement à custom_anomalie que commande_alertes.py requalifie en cron) ;
  - « Appel sans réponse » réutilise suivi_appels.enregistrer_appel (WEB) ;
  - « Créer une tâche » insère une Tache de travail déjà liée à la commande ;
  - « Interroger » réutilise livraison_aramex.rafraichir ;
  - « SMS suivi » envoie le même SMS que la synchro du soir, mais à la demande.

L'écran ne calcule rien : tout vient d'ici, en UNE passe par table (pas une
requête par ligne — la période peut couvrir des centaines de commandes).
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from customization_app.livraison_aramex import (
    COMPTE_ARAMEX,
    _lire_suivi,
    _texte,
    alerte,
    reference_aramex,
)
from customization_app.suivi_appels import CHAMPS as CHAMPS_APPELS

PAYMENT_TERMS_ARAMEX = "Livraison Aramex"
PRECISION = 3

# Les pastilles Aramex/tâche de la vue liste ne regardent que les commandes
# à partir de cette date (demande du 27/08/2026).
DEPUIS_INFOS_LISTE = "2026-07-01"

CHAMP_TRAITEE = "custom_commande_traitee"


def _lecture():
    frappe.has_permission("Sales Order", "read", throw=True)


# ------------------------------------------------------------------ données


def _commandes(depuis, jusqu_a):
    return frappe.get_all(
        "Sales Order",
        filters={"transaction_date": ["between", [depuis, jusqu_a]]},
        fields=[
            "name", "transaction_date", "customer", "customer_name",
            "contact_mobile", "contact_phone", "shipping_address",
            "address_display", "grand_total", "currency", "status", "docstatus",
            "payment_terms_template", "woocommerce_id",
            "custom_anomalie", "custom_retour_colis", CHAMP_TRAITEE,
            "custom_bordereau_aramex",
            "custom_appel_1_sans_reponse", "custom_appel_2_sans_reponse",
        ],
        # Du plus ancien au plus récent : la commande qui traîne depuis trois
        # semaines passe avant celle d'hier — c'est elle qu'on risque d'oublier.
        order_by="transaction_date asc, creation asc",
        limit_page_length=0,
    )


def _articles(noms):
    """{commande: [{article, qte, montant}]} — une seule requête pour tout l'écran."""
    out = {}
    if not noms:
        return out
    for l in frappe.get_all(
            "Sales Order Item",
            filters={"parent": ["in", noms], "parenttype": "Sales Order"},
            fields=["parent", "item_code", "item_name", "qty", "amount", "idx"],
            order_by="parent, idx",
            limit_page_length=0):
        out.setdefault(l.parent, []).append({
            "article": l.item_name or l.item_code,
            "code": l.item_code,
            "qte": flt(l.qty, PRECISION),
            "montant": flt(l.amount, PRECISION),
        })
    return out


def _taches(noms):
    """{commande: [tâches]} — l'employé affiché est le nom RH, pas le matricule."""
    out = {}
    if not noms:
        return out
    taches = frappe.get_all(
        "Tache de travail",
        filters={"commande_client": ["in", noms]},
        fields=["name", "commande_client", "custom_type_dintervention",
                "custom_choix_du_staff", "status", "temps", "starts_on"],
        order_by="starts_on desc, creation desc",
        limit_page_length=0,
    )
    matricules = {t.custom_choix_du_staff for t in taches if t.custom_choix_du_staff}
    noms_rh = dict(frappe.get_all(
        "Employee", filters={"name": ["in", list(matricules)]},
        fields=["name", "employee_name"], as_list=True)) if matricules else {}
    for t in taches:
        out.setdefault(t.commande_client, []).append({
            "tache": t.name,
            "type": t.custom_type_dintervention,
            "employe": noms_rh.get(t.custom_choix_du_staff) or t.custom_choix_du_staff or "",
            "statut": t.status,
            "temps": t.temps,
            "date": str(t.starts_on) if t.starts_on else None,
        })
    return out


def _bordereaux(noms):
    """{commande: {bordereau, payment_entry}} via les paiements sur le compte Aramex.

    Le bordereau vit dans le `reference_no` du Payment Entry (« Aramex N: 513… ») —
    même clé que l'écran Livraisons Aramex. S'il y a plusieurs paiements, le plus
    récent gagne : c'est lui qui porte le bordereau réellement en cours.

    ⚠️ LE PAIEMENT POINTE SOUVENT LA FACTURE, PAS LA COMMANDE. Le flux réel
    facture puis encaisse : le Payment Entry Aramex référence alors la Sales
    Invoice — ne regarder que reference_doctype='Sales Order' affichait
    « sans bordereau » sur des colis parfaitement suivis (constaté 27/08 sur
    une commande Terminé). Les deux chemins sont donc couverts.
    """
    out = {}
    if not noms:
        return out
    lignes = frappe.db.sql(
        """SELECT per.reference_name AS commande, pe.name, pe.reference_no,
                  pe.posting_date, pe.creation
           FROM `tabPayment Entry` pe
           JOIN `tabPayment Entry Reference` per
                ON per.parent = pe.name AND per.reference_doctype = 'Sales Order'
           WHERE pe.docstatus = 1 AND pe.paid_to = %(compte)s
             AND per.reference_name IN %(noms)s
           UNION ALL
           SELECT DISTINCT sii.sales_order AS commande, pe.name, pe.reference_no,
                  pe.posting_date, pe.creation
           FROM `tabPayment Entry` pe
           JOIN `tabPayment Entry Reference` per
                ON per.parent = pe.name AND per.reference_doctype = 'Sales Invoice'
           JOIN `tabSales Invoice Item` sii ON sii.parent = per.reference_name
           WHERE pe.docstatus = 1 AND pe.paid_to = %(compte)s
             AND sii.sales_order IN %(noms)s
           ORDER BY posting_date ASC, creation ASC""",
        {"compte": COMPTE_ARAMEX, "noms": tuple(noms)}, as_dict=True)
    for l in lignes:
        out[l.commande] = {
            "bordereau": reference_aramex(l.reference_no),
            "reference_brute": l.reference_no,
            "payment_entry": l.name,
        }
    return out


@frappe.whitelist()
def get_data(from_date=None, to_date=None):
    """Les commandes de la période, prêtes à traiter. Aucun appel au transporteur ici :
    le suivi affiché est le dernier CONNU, le frais se demande explicitement."""
    _lecture()

    depuis = getdate(from_date) if from_date else add_days(getdate(nowdate()), -30)
    jusqu_a = getdate(to_date) if to_date else getdate(nowdate())

    lignes = _commandes(depuis, jusqu_a)
    noms = [l.name for l in lignes]
    articles = _articles(noms)
    taches = _taches(noms)
    bordereaux = _bordereaux(noms)

    # Téléphone : celui saisi sur la commande d'abord (commande web = numéro du
    # client lui-même), la fiche client sinon — elle peut dater, mais vaut mieux que rien.
    sans_tel = {l.customer for l in lignes if not (l.contact_mobile or l.contact_phone)}
    tel_client = dict(frappe.get_all(
        "Customer", filters={"name": ["in", list(sans_tel)]},
        fields=["name", "mobile_no"], as_list=True)) if sans_tel else {}

    commandes = []
    for l in lignes:
        aramex = bordereaux.get(l.name)
        # Le paiement sur le compte Aramex reste la référence maîtresse ; à
        # défaut, le numéro saisi sur la commande elle-même (écran Traitement).
        bordereau_pe = (aramex or {}).get("bordereau")
        bordereau = bordereau_pe or reference_aramex(l.custom_bordereau_aramex)
        suivi = _lire_suivi(bordereau) if bordereau else None
        appels = sum(1 for c in CHAMPS_APPELS.values() if l.get(c))
        commandes.append({
            "name": l.name,
            "date": str(l.transaction_date),
            "statut": l.status,
            "docstatus": l.docstatus,
            "client": l.customer,
            "client_nom": l.customer_name or l.customer,
            "telephone": l.contact_mobile or l.contact_phone
                         or tel_client.get(l.customer) or "",
            "adresse": _texte(l.shipping_address or l.address_display),
            "ttc": flt(l.grand_total, PRECISION),
            "devise": l.currency,
            "articles": articles.get(l.name, []),
            "taches": taches.get(l.name, []),
            "anomalie": l.custom_anomalie,
            "retour_colis": bool(l.custom_retour_colis),
            "traitee": bool(l.get(CHAMP_TRAITEE)),
            "web": bool(l.woocommerce_id),
            "appels": appels,
            # ⚠️ le flag Aramex vient du MODE DE PAIEMENT de la commande OU d'un
            # paiement déjà posé sur le compte Aramex : une commande brouillon a
            # déjà son échéancier « Livraison Aramex » bien avant tout paiement.
            "aramex": l.payment_terms_template == PAYMENT_TERMS_ARAMEX or bool(aramex)
                      or bool(bordereau),
            "bordereau": bordereau,
            # Le numéro venu du paiement ne se corrige pas à la légère : l'UI le dit.
            "bordereau_pe": bool(bordereau_pe),
            "reference_brute": (aramex or {}).get("reference_brute"),
            "suivi": suivi,
            "alerte": alerte(suivi),
        })

    return {
        "periode": {"from": str(depuis), "to": str(jusqu_a)},
        "commandes": commandes,
        "kpis": kpis(commandes),
    }


def kpis(commandes):
    def somme(seq):
        return round(sum(flt(c["ttc"], PRECISION) for c in seq), PRECISION)

    a_traiter = [c for c in commandes if not c["traitee"] and c["statut"] not in
                 ("Completed", "Closed", "Cancelled")]
    aramex = [c for c in commandes if c["aramex"]]
    aramex_en_cours = [c for c in aramex if not (c["suivi"] or {}).get("livre")]
    return {
        "total": len(commandes), "montant_total": somme(commandes),
        "a_traiter": len(a_traiter), "montant_a_traiter": somme(a_traiter),
        "sans_tache": len([c for c in a_traiter if not c["taches"]]),
        "aramex": len(aramex),
        "aramex_en_cours": len(aramex_en_cours),
        "aramex_alertes": len([c for c in aramex if c["alerte"]]),
        "anomalies": len([c for c in commandes if c["anomalie"]]),
    }


@frappe.whitelist()
def statuts_aramex():
    """Les statuts Aramex réellement présents en base — options de la
    multisélection « Statut Aramex » de la vue liste."""
    _lecture()
    return [r[0] for r in frappe.db.sql(
        """SELECT DISTINCT custom_statut_aramex FROM `tabSales Order`
           WHERE COALESCE(custom_statut_aramex, '') != '' ORDER BY 1""")]


@frappe.whitelist()
def infos_liste(noms):
    """Les pastilles enrichies de la VUE LISTE des commandes : Aramex + tâches.

    Appelée en lot par sales_order_list_alertes.js (mêmes lignes que get_alertes) ;
    ne renvoie une entrée que pour les commandes qui ont quelque chose à montrer.
    """
    _lecture()
    noms = frappe.parse_json(noms) if isinstance(noms, str) else (noms or [])
    if not noms:
        return {}

    # Décision 27/08 : seulement les commandes récentes — l'historique d'avant
    # juillet 2026 n'a rien à montrer, et on s'épargne ses jointures.
    lignes = frappe.get_all(
        "Sales Order",
        filters={"name": ["in", noms],
                 "transaction_date": [">=", DEPUIS_INFOS_LISTE]},
        fields=["name", "payment_terms_template", "custom_bordereau_aramex"])
    noms = [l.name for l in lignes]
    if not noms:
        return {}
    bordereaux = _bordereaux(noms)
    taches = _taches(noms)

    out = {}
    for so in lignes:
        pe = bordereaux.get(so.name) or {}
        bordereau = pe.get("bordereau") or reference_aramex(so.custom_bordereau_aramex)
        info = {}
        if so.payment_terms_template == PAYMENT_TERMS_ARAMEX or bordereau:
            suivi = (_lire_suivi(bordereau) if bordereau else None) or {}
            info["aramex"] = {
                "bordereau": bordereau,
                "statut": suivi.get("statut"),
                "livre": bool(suivi.get("livre")),
                "erreur": suivi.get("erreur"),
                "url": suivi.get("url"),
                "maj": ((suivi.get("derniere_maj") or {}).get("description")) or "",
            }
        if taches.get(so.name):
            # Les deux plus récentes suffisent à la liste — la fiche montre tout.
            info["taches"] = taches[so.name][:2]
        if info:
            out[so.name] = info
    return out


def _commandes_avec_bordereau():
    """{commande: bordereau} pour toutes les commandes depuis DEPUIS_INFOS_LISTE —
    paiement Aramex d'abord, champ saisi sur la commande sinon."""
    lignes = frappe.get_all(
        "Sales Order",
        filters={"transaction_date": [">=", DEPUIS_INFOS_LISTE]},
        fields=["name", "custom_bordereau_aramex"],
        limit_page_length=0)
    noms = [l.name for l in lignes]
    pes = _bordereaux(noms)
    out = {}
    for l in lignes:
        bordereau = (pes.get(l.name) or {}).get("bordereau") \
            or reference_aramex(l.custom_bordereau_aramex)
        if bordereau:
            out[l.name] = bordereau
    return out


@frappe.whitelist()
def actualiser_statuts_aramex():
    """Le bouton « 🚚 Actualiser Aramex » de la liste des commandes.

    Interroge Aramex pour TOUS les colis des commandes depuis DEPUIS_INFOS_LISTE
    — SAUF ceux déjà livrés ou revenus (leur suivi ne changera plus, et chaque
    appel épargné coûte ~2 s) — puis matérialise le dernier statut connu dans
    custom_statut_aramex de chaque commande : c'est lui que filtre la liste.
    """
    from customization_app.livraison_aramex import rafraichir
    from customization_app.livraison_aramex import _sans_accent
    from customization_app.retour_aramex import _MOTS_RETOUR

    par_commande = _commandes_avec_bordereau()

    a_interroger = set()
    for bordereau in par_commande.values():
        suivi = _lire_suivi(bordereau) or {}
        if suivi.get("livre"):
            continue
        texte = _sans_accent(suivi.get("statut") or "")
        if any(m in texte for m in _MOTS_RETOUR):
            continue
        a_interroger.add(bordereau)

    resultat = rafraichir(references=list(a_interroger), tout=1,
                          limite=len(a_interroger) or 1) if a_interroger else {}

    # Matérialiser le statut frais sur CHAQUE commande du périmètre — y compris
    # les livrées/revenues qu'on n'a pas interrogées : leur dernier connu suffit.
    statuts = {}
    for commande, bordereau in par_commande.items():
        suivi = _lire_suivi(bordereau) or {}
        statut = suivi.get("statut") or ""
        frappe.db.set_value("Sales Order", commande, "custom_statut_aramex",
                            statut, update_modified=False)
        if statut:
            statuts[statut] = statuts.get(statut, 0) + 1
    frappe.db.commit()

    return {"commandes": len(par_commande),
            "interroges": resultat.get("interroges", 0),
            "erreurs": resultat.get("erreurs", 0),
            "sautes": len(par_commande) - len(a_interroger),
            "statuts": statuts}


# ------------------------------------------------------------------ actions


@frappe.whitelist()
def marquer_traitee(commande, traitee=1):
    """Pose (ou retire) le fait « commande traitée », avec trace au fil de la fiche.

    db_set plutôt qu'un save : la commande peut être soumise, et l'on ne veut ni
    déclencher les hooks de mise à jour ni toucher au champ modified — même
    mécanique que suivi_appels.enregistrer_appel.
    """
    frappe.has_permission("Sales Order", "write", doc=commande, throw=True)
    if not frappe.db.exists("Sales Order", commande):
        frappe.throw(_("Commande introuvable."))

    traitee = 1 if cint(traitee) else 0
    frappe.db.set_value("Sales Order", commande, CHAMP_TRAITEE, traitee,
                        update_modified=False)
    texte = (_("✅ Commande marquée traitée depuis l'écran Traitement des commandes.")
             if traitee else
             _("↩️ Traitement rouvert depuis l'écran Traitement des commandes."))
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info",
        "reference_doctype": "Sales Order", "reference_name": commande,
        "content": texte,
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"traitee": bool(traitee)}


@frappe.whitelist()
def creer_tache(commande, type_intervention, staff, starts_on, temps=None, titre=None):
    """Insère une Tache de travail déjà liée à la commande, client et téléphone repris
    de la pièce. La logique métier (couleurs, contrôles) vit dans les hooks existants
    de Tache de travail — on insère un document normal, rien de contourné."""
    frappe.has_permission("Tache de travail", "create", throw=True)

    so = frappe.db.get_value(
        "Sales Order", commande,
        ["customer", "customer_name", "contact_mobile", "contact_phone"], as_dict=True)
    if not so:
        frappe.throw(_("Commande introuvable."))

    tache = frappe.get_doc({
        "doctype": "Tache de travail",
        "custom_type_dintervention": type_intervention,
        "custom_choix_du_staff": staff,
        "starts_on": starts_on,
        "temps": temps or None,
        "titre": titre or None,
        "subject": titre or "%s — %s" % (type_intervention, so.customer_name or so.customer),
        "status": "Open",
        "custom_client": so.customer,
        "nom_client": so.customer_name,
        "tel": so.contact_mobile or so.contact_phone,
        "commande_client": commande,
        "afficher_commande": 1,
    })
    tache.insert()
    frappe.db.commit()
    return {"tache": tache.name}


@frappe.whitelist()
def definir_bordereau(commande, bordereau):
    """Enregistre le numéro de suivi Aramex saisi depuis l'écran.

    Le numéro vit sur la COMMANDE (custom_bordereau_aramex) ; si un paiement sur
    le compte Aramex existe déjà, son reference_no est aligné aussi — c'est lui
    que lisent l'écran Livraisons Aramex, le retour de colis et la synchro du
    soir, et deux numéros différents pour un même colis seraient un piège.

    db_set des deux côtés : commande et paiement sont soumis, on ne veut ni
    hooks de mise à jour ni champ modified touché. La trace vit en commentaire.
    """
    frappe.has_permission("Sales Order", "write", doc=commande, throw=True)
    if not frappe.db.exists("Sales Order", commande):
        frappe.throw(_("Commande introuvable."))

    numero = reference_aramex(bordereau or "")
    if not numero:
        frappe.throw(_("Numéro de bordereau invalide : 8 à 20 chiffres attendus."))

    ancien = reference_aramex(
        frappe.db.get_value("Sales Order", commande, "custom_bordereau_aramex"))
    frappe.db.set_value("Sales Order", commande, "custom_bordereau_aramex", numero,
                        update_modified=False)

    # Aligner le paiement Aramex éventuel — le plus récent, comme _bordereaux().
    pe_alignee = None
    aramex = _bordereaux([commande]).get(commande)
    if aramex and aramex.get("bordereau") != numero:
        pe_alignee = aramex["payment_entry"]
        ancien = ancien or aramex.get("bordereau")
        frappe.db.set_value("Payment Entry", pe_alignee, "reference_no",
                            "Aramex N: %s" % numero, update_modified=False)

    texte = _("🚚 Bordereau Aramex {0} enregistré depuis l'écran Traitement des commandes.").format(numero)
    if ancien and ancien != numero:
        texte += " " + _("(remplace {0})").format(ancien)
    if pe_alignee:
        texte += " " + _("Paiement {0} aligné.").format(pe_alignee)
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info",
        "reference_doctype": "Sales Order", "reference_name": commande,
        "content": texte,
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"bordereau": numero, "payment_entry": pe_alignee}


@frappe.whitelist()
def envoyer_sms_suivi(commande):
    """Le SMS de suivi Aramex, à la demande — même texte et même trace que l'envoi
    automatique du soir (livraison_aramex.prevenir), mais SANS ses garde-fous de
    cadence : ici quelqu'un regarde la commande et décide. Le doublon éventuel se
    confirme côté écran (sms_envoye_le est renvoyé dans le suivi)."""
    frappe.has_permission("Sales Order", "write", doc=commande, throw=True)
    from customization_app.livraison_aramex import modele_sms, prevenir

    aramex = _bordereaux([commande]).get(commande)
    bordereau = (aramex or {}).get("bordereau") or reference_aramex(
        frappe.db.get_value("Sales Order", commande, "custom_bordereau_aramex"))
    if not bordereau:
        frappe.throw(_("Aucun bordereau Aramex sur cette commande."))
    suivi = _lire_suivi(bordereau)
    if not suivi or suivi.get("erreur"):
        frappe.throw(_("Pas de suivi exploitable : interrogez d'abord Aramex."))

    so = frappe.db.get_value(
        "Sales Order", commande,
        ["customer", "customer_name", "contact_mobile", "contact_phone", "grand_total"],
        as_dict=True)
    telephone = so.contact_mobile or so.contact_phone \
        or frappe.db.get_value("Customer", so.customer, "mobile_no")
    if not telephone:
        frappe.throw(_("Aucun téléphone sur la commande ni sur la fiche client."))

    res = prevenir({
        "reference": bordereau,
        "telephone": telephone,
        "customer": so.customer,
        "customer_name": so.customer_name,
        "montant": flt(so.grand_total, PRECISION),
        "suivi": suivi,
    }, modele_sms())
    if res.get("statut") != "envoye":
        frappe.throw(_("Échec de l'envoi : {0}").format(res.get("erreur") or ""))
    frappe.db.commit()
    return res


def aramex_des_commandes(noms):
    """{commande: {"aramex": bool, "bordereau": str}} — pour un lot de commandes.

    MÊME RÈGLE QUE L'ÉCRAN TRAITEMENT, et volontairement au même endroit : une
    commande est « Aramex » par son ÉCHÉANCIER ou par un paiement déjà posé sur
    le compte Aramex, et son bordereau vit d'abord dans ce paiement, à défaut
    dans le champ saisi sur la commande. Recopier ce raisonnement ailleurs, c'est
    se garantir qu'un jour le calendrier annoncera un bordereau que la liste des
    commandes ne connaît pas.
    """
    if not noms:
        return {}
    noms = list({n for n in noms if n})
    if not noms:
        return {}
    bordereaux = _bordereaux(noms)
    out = {}
    for l in frappe.get_all(
            "Sales Order", filters={"name": ["in", noms]},
            fields=["name", "payment_terms_template", "custom_bordereau_aramex"],
            limit_page_length=0):
        paiement = bordereaux.get(l.name)
        bordereau = (paiement or {}).get("bordereau") \
            or reference_aramex(l.custom_bordereau_aramex)
        out[l.name] = {
            "aramex": l.payment_terms_template == PAYMENT_TERMS_ARAMEX
                      or bool(paiement) or bool(bordereau),
            "bordereau": bordereau or "",
        }
    return out
