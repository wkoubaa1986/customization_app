"""
Clôture d'une Tache de travail : photos obligatoires, côté SERVEUR.

L'ancienne « obligation » vivait dans un setInterval jQuery qui guettait le
modal Caméra et bloquait son bouton « Valider » sous 3 images — elle ne
bloquait pas la clôture elle-même, cassait au moindre changement de DOM et ne
couvrait que le chemin caméra. Ici la règle vit dans before_save : passer en
Completed sans les photos requises est IMPOSSIBLE, quel que soit l'écran
(fiche, calendrier, mobile, API).

Exigences par type (décision utilisateur du 27/08/2026) :
  - Entretien    : filtres enlevés + nouveaux filtres (avant) et appareil final (après)
  - Installation : connecteur d'eau, appareil sous l'évier, robinet avec eau
  - Réparation   : pièces enlevées (avant) et appareil réparé (après)
  - Livraison    : produits envoyés + photo du bordereau si Aramex ; produits livrés sinon
  Visite, Autre et Tournée commerciale ne sont pas concernés (la tournée a déjà
  sa propre clôture verrouillée dans tournee.py).

Le déverrouillage : un code superviseur, défini dans Config Cloture Tache
(System Manager). Le bon code pose dispense_photos sur LA tâche, avec trace au
fil du document — la dispense est un fait visible, jamais silencieux.
"""
from __future__ import annotations

import hmac

import frappe
from frappe import _
from frappe.utils import cint

DOCTYPE_CONFIG = "Config Cloture Tache"
DOCTYPE_TACHE = "Tache de travail"

# Chaque type exige des minima par champ (liste_photos_avant / liste_photos_apres).
# Les « slots » guident l'écran : ils DISENT quoi photographier — le serveur, lui,
# ne peut compter que des photos, pas reconnaître un filtre sur l'image.
EXIGENCES = {
    "Entretien": {
        "avant": 2, "apres": 1,
        "slots": [
            {"label": "Filtres enlevés (avant)", "champ": "avant"},
            {"label": "Nouveaux filtres à mettre", "champ": "avant"},
            {"label": "Appareil final (après)", "champ": "apres"},
        ],
    },
    "Installation": {
        "avant": 1, "apres": 2,
        "slots": [
            # La pièce qui bifurque l'arrivée d'eau vers l'osmoseur : « vanne de
            # piquage » est le terme métier ; « arrivée d'eau » guide le novice.
            {"label": "Vanne de piquage sur l'arrivée d'eau", "champ": "avant"},
            {"label": "Appareil posé sous l'évier", "champ": "apres"},
            {"label": "Robinet avec eau qui coule", "champ": "apres"},
        ],
    },
    "Réparation": {
        "avant": 1, "apres": 1,
        "slots": [
            {"label": "Pièces enlevées", "champ": "avant"},
            {"label": "Appareil réparé (après)", "champ": "apres"},
        ],
    },
    # Livraison : deux variantes, résolues par la commande liée (voir _exigence_livraison).
}

CHAMPS = {"avant": "liste_photos_avant", "apres": "liste_photos_apres"}


def _nb_photos(texte):
    """Nombre de photos dans un champ liste (une URL par ligne, format « 📁 "url" »)."""
    return len([l for l in (texte or "").splitlines() if l.strip()])


def _est_livraison_aramex(commande):
    """La livraison part-elle chez Aramex ? Lu sur la commande liée : échéancier
    « Livraison Aramex » ou bordereau déjà saisi (écran Traitement des commandes)."""
    if not commande:
        return False
    v = frappe.db.get_value("Sales Order", commande,
                            ["payment_terms_template", "custom_bordereau_aramex"],
                            as_dict=True)
    if not v:
        return False
    return v.payment_terms_template == "Livraison Aramex" \
        or bool(v.custom_bordereau_aramex)


def _exigence_livraison(doc):
    if _est_livraison_aramex(doc.get("commande_client")):
        return {
            "avant": 1, "apres": 1,
            "slots": [
                {"label": "Produits envoyés (1 ou plusieurs)", "champ": "avant",
                 "multiple": True},
                {"label": "Bordereau Aramex", "champ": "apres"},
            ],
        }
    return {
        "avant": 1, "apres": 0,
        "slots": [
            {"label": "Produits livrés chez le client (1 ou plusieurs)",
             "champ": "avant", "multiple": True},
        ],
    }


def exigence_du_doc(doc):
    """L'exigence applicable à cette tâche, ou None si le type n'est pas concerné."""
    type_i = doc.get("custom_type_dintervention")
    if type_i == "Livraison":
        return _exigence_livraison(doc)
    return EXIGENCES.get(type_i)


# Le lien Google Map prouve OÙ le technicien était : exigé pour tout ce qui se
# passe chez le client. Une livraison Aramex ne s'y rend pas — c'est le
# transporteur qui livre — donc pas de position à exiger. Réparation : souvent
# au local (dans_local), on ne l'exige pas.
TYPES_GMAP = {"Installation", "Visite", "Entretien", "Livraison"}


def gmap_requis(doc):
    type_i = doc.get("custom_type_dintervention")
    if type_i not in TYPES_GMAP:
        return False
    if type_i == "Livraison" and _est_livraison_aramex(doc.get("commande_client")):
        return False
    return True


def regle_active():
    """La règle est ACTIVE PAR DÉFAUT : la config (single) n'a pas de ligne tant
    que personne ne l'a enregistrée, et l'absence de réglage ne doit pas ouvrir
    la porte — seul un « actif = 0 » explicite désactive.

    ⚠️ Lecture DIRECTE de tabSingles, pas get_single_value : celui-ci CASTE le
    Check et rend 0 aussi bien pour « décoché » que pour « jamais enregistré » —
    la règle naissait désactivée."""
    lignes = frappe.db.sql(
        "select value from tabSingles where doctype=%s and field='actif'",
        DOCTYPE_CONFIG)
    return True if not lignes else bool(cint(lignes[0][0]))


def verifier_photos_cloture(doc, method=None):
    """Appelée par before_save : bloque le PASSAGE à Completed sans les photos.

    Seulement la transition — une tâche déjà Completed se resauvegarde librement
    (recolorations, patchs, synchro arabe…), sinon chaque cron qui la touche
    exigerait des photos d'une intervention finie depuis des mois.
    """
    if doc.get("status") != "Completed":
        return
    avant_save = doc.get_doc_before_save()
    if avant_save and avant_save.get("status") == "Completed":
        return

    # États de la commande liée (demande 27/08) : une tâche ne se clôture pas
    # sur une commande en BROUILLON ni avec un BL en brouillon — la vente doit
    # être actée et la sortie de stock constatée. AVANT la dispense et hors
    # du réglage « actif » : le code superviseur couvre les preuves (photos,
    # position), jamais l'état des pièces.
    commande = doc.get("commande_client")
    if commande:
        etat = frappe.db.get_value("Sales Order", commande, "docstatus")
        if etat == 0:
            frappe.throw(
                _("La commande liée {0} est en BROUILLON : validez-la avant de "
                  "clôturer la tâche (bouton « Ouvrir » du dialogue de clôture).")
                .format(commande), title=_("Commande non validée"))
        if etat == 2:
            frappe.throw(
                _("La commande liée {0} est ANNULÉE : rattachez la bonne commande "
                  "ou annulez la tâche.").format(commande),
                title=_("Commande annulée"))
        bls_brouillon = [b[0] for b in frappe.db.sql(
            """SELECT DISTINCT dn.name FROM `tabDelivery Note` dn
               JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
               WHERE dni.against_sales_order = %(c)s AND dn.docstatus = 0""",
            {"c": commande})]
        if bls_brouillon:
            frappe.throw(
                _("Bon(s) de livraison en BROUILLON : {0} — validez-le(s) avant "
                  "de clôturer (bouton « Ouvrir pour valider » du dialogue).")
                .format(", ".join(bls_brouillon)),
                title=_("BL non validé"))

    if cint(doc.get("dispense_photos")):
        return
    if not regle_active():
        return

    # Position Google Map : obligatoire pour Installation / Visite / Entretien /
    # Livraison hors Aramex — le bouton « 📍 Ma position » de la fiche la pose
    # en un geste. Le code superviseur (dispense) couvre aussi ce manque.
    if gmap_requis(doc) and not (doc.get("google_map") or "").strip():
        frappe.throw(
            _("📍 Le lien Google Map est obligatoire pour clôturer une tâche "
              "{0} : utilisez le bouton « 📍 Ma position actuelle » sous le champ, "
              "ou un code superviseur.").format(doc.get("custom_type_dintervention")),
            title=_("Position manquante"))

    exigence = exigence_du_doc(doc)
    if not exigence:
        return

    manques = []
    for cle, minimum in (("avant", exigence["avant"]), ("apres", exigence["apres"])):
        if _nb_photos(doc.get(CHAMPS[cle])) < minimum:
            manques.append(cle)
    if not manques:
        return

    attendu = "<br>".join("• %s" % s["label"] for s in exigence["slots"])
    frappe.throw(
        _("📷 Photos obligatoires avant de clôturer cette tâche ({0}) :").format(
            doc.get("custom_type_dintervention"))
        + "<br>" + attendu
        + "<br><br>" + _("Utilisez le bouton « 📷 Photos de clôture » de la fiche, "
                         "ou un code superviseur pour clôturer sans photos."),
        title=_("Photos manquantes"))


# ------------------------------------------------------------------ écran


@frappe.whitelist()
def exigences(tache):
    """L'état de la clôture pour l'écran : quoi photographier, ce qui est déjà là."""
    frappe.has_permission(DOCTYPE_TACHE, "read", doc=tache, throw=True)
    doc = frappe.get_doc(DOCTYPE_TACHE, tache)
    exigence = exigence_du_doc(doc)
    requis_gmap = gmap_requis(doc)
    type_i = doc.get("custom_type_dintervention")
    # ⚠️ MIROIR des mandatory_depends_on du doctype (tache_de_travail.json) :
    # c'est LUI qui bloque au save — le dialogue ne fait que permettre de
    # remplir sur place, et le code superviseur ne dispense PAS de ces deux-là.
    commande_requise = (
        type_i in ("Installation", "Livraison", "Entretien", "Réparation")
        or cint(doc.get("afficher_commande")))
    rapport_requis = type_i in ("Entretien", "Réparation", "Installation", "Visite")

    # Résumé financier et logistique de la commande liée, pour le dialogue :
    # les paiements reçus (alloués à la commande OU à ses factures — le flux
    # réel encaisse souvent sur la facture) et les bons de livraison, avec leur
    # état — un BL en brouillon se valide depuis le popup avant de clôturer.
    commande_infos = None
    if doc.get("commande_client"):
        commande = doc.get("commande_client")
        paiements = frappe.db.sql(
            """SELECT pe.name, pe.posting_date, pe.mode_of_payment,
                      per.allocated_amount, pe.paid_to
               FROM `tabPayment Entry` pe
               JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
               WHERE pe.docstatus = 1
                 AND ((per.reference_doctype = 'Sales Order'
                       AND per.reference_name = %(c)s)
                      OR (per.reference_doctype = 'Sales Invoice'
                          AND per.reference_name IN (
                              SELECT sii.parent FROM `tabSales Invoice Item` sii
                              WHERE sii.sales_order = %(c)s)))
               ORDER BY pe.posting_date""", {"c": commande}, as_dict=True)
        bls = frappe.db.sql(
            """SELECT DISTINCT dn.name, dn.docstatus, dn.grand_total
               FROM `tabDelivery Note` dn
               JOIN `tabDelivery Note Item` dni ON dni.parent = dn.name
               WHERE dni.against_sales_order = %(c)s AND dn.docstatus < 2
               ORDER BY dn.creation""", {"c": commande}, as_dict=True)
        commande_infos = {
            "total": frappe.utils.flt(
                frappe.db.get_value("Sales Order", commande, "grand_total"), 3),
            "paiements": [{
                "paiement": p.name,
                "date": str(p.posting_date),
                "mode": p.mode_of_payment,
                "montant": frappe.utils.flt(p.allocated_amount, 3),
                "compte": p.paid_to,
            } for p in paiements],
            "total_paye": round(sum(frappe.utils.flt(p.allocated_amount)
                                    for p in paiements), 3),
            "bls": [{"bl": b.name, "brouillon": b.docstatus == 0,
                     "total": frappe.utils.flt(b.grand_total, 3)} for b in bls],
        }

    # Pré-remplissage d'une NOUVELLE commande depuis le dialogue : client de la
    # tâche + magasin de l'EMPLOYÉ affecté, et les mêmes règles que le flux
    # historique du formulaire (hors Installation/Livraison : vente directe
    # sans BL/facture, taxes sans timbre fiscal).
    nouvelle_commande = {}
    if doc.get("custom_client"):
        nouvelle_commande["customer"] = doc.get("custom_client")
    if doc.get("starts_on"):
        nouvelle_commande["delivery_date"] = str(doc.get("starts_on"))[:10]
    entrepot = frappe.db.get_value(
        "Employee", doc.get("custom_choix_du_staff"), "custom_warehouse") \
        if doc.get("custom_choix_du_staff") else None
    if entrepot:
        nouvelle_commande["set_warehouse"] = entrepot
    if type_i not in ("Installation", "Livraison"):
        nouvelle_commande["custom_type_de_transaction"] = "Vente directe sans BL et Facture"
        nouvelle_commande["taxes_and_charges"] = "Vente Standard Sans Timbre Fiscale - A&S"

    return {
        "nouvelle_commande": nouvelle_commande,
        "commande_infos": commande_infos,
        "client": doc.get("custom_client"),
        "commande_requise": commande_requise,
        "commande": doc.get("commande_client"),
        "commande_brouillon": bool(doc.get("commande_client")) and frappe.db.get_value(
            "Sales Order", doc.get("commande_client"), "docstatus") == 0,
        "commande_annulee": bool(doc.get("commande_client")) and frappe.db.get_value(
            "Sales Order", doc.get("commande_client"), "docstatus") == 2,
        "rapport_requis": rapport_requis,
        "rapport": bool((doc.get("rapport_visite") or "").strip()),
        "rapport_texte": doc.get("rapport_visite") or "",
        "actif": regle_active(),
        "type": doc.get("custom_type_dintervention"),
        # Une Visite n'exige aucune photo mais bien une position : elle est
        # « concernée » aussi — le dialogue ne montre alors que la ligne 📍.
        "concerne": bool(exigence) or requis_gmap,
        "gmap_requis": requis_gmap,
        "gmap": bool((doc.get("google_map") or "").strip()),
        "dispense": bool(cint(doc.get("dispense_photos"))),
        "slots": (exigence or {}).get("slots", []),
        "minima": {"avant": (exigence or {}).get("avant", 0),
                   "apres": (exigence or {}).get("apres", 0)},
        "photos": {"avant": _nb_photos(doc.get("liste_photos_avant")),
                   "apres": _nb_photos(doc.get("liste_photos_apres"))},
    }


@frappe.whitelist()
def enregistrer_photo(tache, champ, file_url):
    """Range une photo déjà téléversée dans le bon champ de la tâche.

    Le fichier est attaché par le FileUploader (doctype/docname) ; ici on ne fait
    qu'ajouter sa ligne au champ liste — même format « 📁 "url" » que le flux
    historique des boutons Photos, pour que tout ce qui lit ces champs continue
    de fonctionner. db_set : pas de hooks, pas de modified — le brancardier ne
    réécrit pas le dossier médical.
    """
    frappe.has_permission(DOCTYPE_TACHE, "write", doc=tache, throw=True)
    if champ not in CHAMPS:
        frappe.throw(_("Champ photo inconnu."))
    if not (file_url or "").startswith(("/files/", "/private/files/")):
        frappe.throw(_("URL de fichier invalide."))

    nom_champ = CHAMPS[champ]
    existant = frappe.db.get_value(DOCTYPE_TACHE, tache, nom_champ) or ""
    ligne = '📁 "%s"' % file_url
    if ligne in existant:
        # ⚠️ PAS UN SKIP SILENCIEUX. Frappe déduplique les fichiers par contenu :
        # envoyer deux fois la même image rend la même URL, et l'ignorer sans
        # rien dire laissait le compteur figé — « ça ne se charge pas ». Chaque
        # prise exigée doit être une photo DIFFÉRENTE, et l'écran doit le dire.
        return {"photos": _nb_photos(existant), "deja": True}
    valeur = "\n".join(filter(None, [existant, ligne]))
    frappe.db.set_value(DOCTYPE_TACHE, tache, nom_champ, valeur, update_modified=False)
    frappe.db.commit()
    return {"photos": _nb_photos(valeur), "deja": False}


@frappe.whitelist()
def completer_champs(tache, commande=None, rapport=None):
    """Renseigne la commande liée et/ou le rapport d'intervention depuis le
    dialogue de clôture — db_set (le save final rejouera les hooks), même
    mécanique que enregistrer_photo. La commande doit exister et appartenir au
    client de la tâche quand celui-ci est renseigné : lier la commande d'un
    autre client serait pire qu'un champ vide."""
    frappe.has_permission(DOCTYPE_TACHE, "write", doc=tache, throw=True)
    doc = frappe.db.get_value(DOCTYPE_TACHE, tache, ["custom_client"], as_dict=True)
    if not doc:
        frappe.throw(_("Tâche introuvable."))

    maj = {}
    if commande:
        client_commande = frappe.db.get_value("Sales Order", commande, "customer")
        if not client_commande:
            frappe.throw(_("Commande introuvable."))
        if doc.custom_client and client_commande != doc.custom_client:
            frappe.throw(_("La commande {0} appartient à {1}, pas au client de la tâche.")
                         .format(commande, client_commande))
        maj["commande_client"] = commande
    if rapport is not None and str(rapport).strip():
        maj["rapport_visite"] = str(rapport).strip()
    if maj:
        frappe.db.set_value(DOCTYPE_TACHE, tache, maj, update_modified=False)
        frappe.db.commit()
    return {"maj": list(maj)}


@frappe.whitelist()
def deverrouiller(tache, code):
    """Le code superviseur lève l'obligation de photos pour CETTE tâche.

    Le code vit dans Config Cloture Tache (champ Password, System Manager). La
    dispense se pose sur la tâche avec trace nominative au fil du document : on
    saura toujours qui a ouvert la porte, et pour quelle intervention.
    """
    frappe.has_permission(DOCTYPE_TACHE, "write", doc=tache, throw=True)
    if not frappe.db.exists(DOCTYPE_TACHE, tache):
        frappe.throw(_("Tâche introuvable."))

    from frappe.utils.password import get_decrypted_password
    attendu = get_decrypted_password(DOCTYPE_CONFIG, DOCTYPE_CONFIG,
                                     "code_deverrouillage", raise_exception=False)
    if not attendu:
        frappe.throw(_("Aucun code superviseur n'est configuré "
                       "(Config Cloture Tache)."))
    if not hmac.compare_digest(str(code or ""), str(attendu)):
        # La tentative ratée se journalise : un code qu'on devine à force
        # d'essayer n'est plus un code.
        frappe.log_error("Code superviseur refusé — tâche %s, utilisateur %s"
                         % (tache, frappe.session.user), "cloture_tache")
        frappe.throw(_("Code incorrect."))

    frappe.db.set_value(DOCTYPE_TACHE, tache, "dispense_photos", 1,
                        update_modified=False)
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info",
        "reference_doctype": DOCTYPE_TACHE, "reference_name": tache,
        "content": _("🔓 Clôture sans photos déverrouillée par {0} (code superviseur).")
                   .format(frappe.session.user),
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"dispense": True}
