"""Écran « Ma journée » — le technicien mène ses interventions au bout, seul.

Demande 02/09/2026 : un bouton, par employé, qui montre les rendez-vous de sa
journée et lui permet de TOUT faire de là — voir ce qu'il doit poser, ouvrir
l'adresse dans Maps, appeler le client d'un doigt, saisir le bordereau Aramex,
et clôturer.

CE QUI N'EST PAS RÉÉCRIT. Les règles de clôture — photos avant/après par type,
position GPS, compte rendu, code superviseur — vivent dans `cloture_tache` et
n'ont pas de copie ici : cet écran les INTERROGE (`exigences`) et clôture par
un `save()` ordinaire, donc `verifier_photos_cloture` s'applique comme depuis
la fiche. Un écran qui contournerait ces contrôles serait une porte dérobée.

TROIS DÉCISIONS DE L'UTILISATEUR, LE 02/09/2026 :
  - l'écran est CELUI DE CHACUN : on l'ouvre sur sa propre journée, pour la
    finir. Ceux qui pilotent déjà tout le planning (magasin, direction) peuvent
    regarder la journée d'un autre ;
  - un appel passé depuis l'écran est TRACÉ, et l'écran demande son résultat :
    savoir qu'on a composé un numéro sans savoir si quelqu'un a décroché ne
    sert à rien ;
  - le numéro Aramex saisi doit CONCORDER avec la photo du bordereau, sinon il
    est refusé. C'est le choix explicite de l'utilisateur, la lecture d'image
    n'étant pas infaillible ; voir `verifier_bordereau` pour ce qui se passe
    quand la photo est illisible ou le service indisponible.
"""
from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import cint, getdate, nowdate

DOCTYPE_TACHE = "Tache de travail"

# Ceux qui voient la journée d'un AUTRE.
#
# ⚠️ MESURÉ, PAS SUPPOSÉ (02/09/2026). « Sales Manager », « Maintenance Manager »
# et « Gestionaire Activité » sont portés par les TECHNICIENS eux-mêmes — Jamel
# Aloui, Sadok Bouziri, Akram, Mohamed Hedi Chouchane les ont tous. Les mettre
# ici ouvrait la journée de chacun à tout le monde, l'inverse de ce qui est
# demandé. Seul « System Manager » sépare réellement la direction du terrain :
# personne d'autre ne l'a. Élargir cette liste, c'est rouvrir la brèche.
ROLES_SUPERVISION = ("System Manager",)

RESULTATS_APPEL = ("Répondu", "Sans réponse", "À rappeler")
PREFIXE_APPEL = "📞 Appel"


def _employe_de_l_utilisateur():
    return frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")


def _supervise():
    return bool(set(frappe.get_roles()) & set(ROLES_SUPERVISION))


def _employe_demande(employe):
    """L'employé dont on a le droit de regarder la journée.

    Sans droit de supervision, la réponse est TOUJOURS soi-même — quel que soit
    ce que l'écran demande. Un employé ne lit pas la tournée de ses collègues.
    """
    mien = _employe_de_l_utilisateur()
    if employe and employe != mien and not _supervise():
        frappe.throw(_("Vous ne pouvez consulter que votre propre journée."),
                     frappe.PermissionError)
    cible = employe or mien
    if not cible:
        frappe.throw(_("Aucune fiche employé n'est rattachée à votre compte — "
                       "demandez au magasin de la lier."))
    return cible


def _numeros(brut):
    """Les numéros exploitables d'un champ texte, dédoublonnés et lisibles."""
    from customization_app.rappel_rdv import _numeros as extraire

    return extraire(brut)


def _articles(commande):
    """Ce qu'il y a à poser ou à livrer. Vide si la tâche n'a pas de commande."""
    if not commande:
        return []
    return [{"code": l.item_code, "article": l.item_name or l.item_code,
             "qte": l.qty}
            for l in frappe.get_all(
                "Sales Order Item", filters={"parent": commande},
                fields=["item_code", "item_name", "qty", "idx"],
                order_by="idx", limit_page_length=0)]


def _appels(tache):
    """Les appels déjà passés sur cette tâche, du plus récent au plus ancien."""
    out = []
    for c in frappe.get_all(
            "Comment",
            filters={"reference_doctype": DOCTYPE_TACHE, "reference_name": tache,
                     "comment_type": "Info", "content": ["like", "%" + PREFIXE_APPEL + "%"]},
            fields=["content", "creation", "owner"],
            order_by="creation desc", limit_page_length=20):
        texte = re.sub(r"<[^>]+>", " ", c.content or "")
        out.append({"quand": str(c.creation)[:16], "par": c.owner,
                    "texte": " ".join(texte.split())})
    return out


def _aramex(commande):
    """(concerné, bordereau connu) — la règle de l'écran Traitement, pas une copie."""
    if not commande:
        return False, ""
    from customization_app.traitement_commandes import aramex_des_commandes

    info = aramex_des_commandes([commande]).get(commande) or {}
    return bool(info.get("aramex")), info.get("bordereau") or ""


@frappe.whitelist()
def ma_journee(date=None, employe=None):
    """Les interventions de la journée, avec tout ce qu'il faut pour les mener."""
    from customization_app import cloture_tache as C

    cible = _employe_demande(employe)
    jour = getdate(date or nowdate())
    taches = frappe.get_all(
        DOCTYPE_TACHE,
        filters={"custom_choix_du_staff": cible,
                 "starts_on": ["between", ["%s 00:00:00" % jour, "%s 23:59:59" % jour]]},
        fields=["name", "status", "custom_type_dintervention", "starts_on", "ends_on",
                "custom_client", "nom_client", "tel", "details_adresse", "google_map",
                "secteur", "commande_client", "subject", "rapport_visite",
                "dispense_photos", "dans_local"],
        order_by="starts_on asc", limit_page_length=0)

    lignes = []
    for t in taches:
        aramex, bordereau = _aramex(t.commande_client)
        try:
            exig = C.exigences(t.name)
        except Exception:
            # L'état de clôture ne doit jamais empêcher d'AFFICHER la journée.
            exig = {}
        lignes.append({
            "tache": t.name,
            "statut": t.status,
            "type": t.custom_type_dintervention,
            "debut": str(t.starts_on)[11:16] if t.starts_on else "",
            "fin": str(t.ends_on)[11:16] if t.ends_on else "",
            "client": t.nom_client or t.custom_client,
            "client_id": t.custom_client,
            "telephones": _numeros(t.tel),
            "adresse": (t.details_adresse or "").replace("\n", ", "),
            "google_map": t.google_map or "",
            "secteur": t.secteur or "",
            "commande": t.commande_client,
            "articles": _articles(t.commande_client),
            "note": t.subject or "",
            "rapport": t.rapport_visite or "",
            "aramex": aramex,
            "bordereau": bordereau,
            "appels": _appels(t.name),
            "exigences": exig,
        })

    return {
        "jour": str(jour),
        "employe": cible,
        "employe_nom": frappe.db.get_value("Employee", cible, "employee_name") or cible,
        "supervise": _supervise(),
        "employes": ([{"nom": e.name, "libelle": e.employee_name}
                      for e in frappe.get_all("Employee", filters={"status": "Active"},
                                              fields=["name", "employee_name"],
                                              order_by="employee_name")]
                     if _supervise() else []),
        "lignes": lignes,
        "resultats_appel": list(RESULTATS_APPEL),
    }


def _ma_tache(tache):
    """La tâche doit être la sienne — ou l'utilisateur doit superviser."""
    doc = frappe.get_doc(DOCTYPE_TACHE, tache)
    if doc.custom_choix_du_staff != _employe_de_l_utilisateur() and not _supervise():
        frappe.throw(_("Cette intervention ne vous est pas affectée."),
                     frappe.PermissionError)
    return doc


@frappe.whitelist()
def tracer_appel(tache, numero, resultat):
    """Note qu'un appel a été passé, et ce qu'il a donné.

    Le RÉSULTAT fait tout l'intérêt de la trace : « on a composé le numéro » ne
    dit pas si le client a décroché, et c'est cela qui décide de rappeler,
    d'attendre, ou d'annuler.
    """
    doc = _ma_tache(tache)
    if resultat not in RESULTATS_APPEL:
        frappe.throw(_("Résultat d'appel inconnu."))
    numero = (numero or "").strip()[:20]
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info",
        "reference_doctype": DOCTYPE_TACHE, "reference_name": doc.name,
        "content": _("{0} au {1} — {2}").format(PREFIXE_APPEL, numero, resultat),
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"appels": _appels(doc.name)}


# ------------------------------------------------- bordereau Aramex vs photo


def _photos_de(doc):
    """Les URL des photos de clôture de la tâche (avant + après)."""
    from customization_app import cloture_tache as C

    urls = []
    for champ in C.CHAMPS.values():
        for ligne in (doc.get(champ) or "").split("\n"):
            # Le format posé par `enregistrer_photo` est « 📁 "url" » ; le repli
            # couvre une URL nue, PRIVÉE comprise — les photos de clôture le sont.
            trouve = (re.search(r'"([^"]+)"', ligne)
                      or re.search(r"(/(?:private/)?files/\S+)", ligne))
            if trouve:
                urls.append(trouve.group(1))
    return urls


def _lire_numero_sur_photo(url):
    """Le numéro de bordereau lu sur UNE photo. -> str, ou "" si illisible.

    On ne demande pas au modèle de juger la concordance : il rend ce qu'il LIT,
    la comparaison se fait ici. Un modèle à qui l'on demande « est-ce bien
    51330112912 ? » a tendance à dire oui.
    """
    from bank_retenue_sync.ai.invoice_extract import _get_client_model_temp

    import base64

    # Par le DocType File : c'est le seul chemin qui sache lire un fichier PRIVÉ
    # (les photos de clôture le sont) et qui résiste à un déplacement du dossier.
    nom = frappe.db.get_value("File", {"file_url": url}, "name")
    if not nom:
        return ""
    contenu = frappe.get_doc("File", nom).get_content()
    if not contenu:
        return ""

    client, model, _t = _get_client_model_temp()
    res = client.responses.create(
        model=model,
        instructions=(
            "Tu lis la photo d'un bordereau de transport ARAMEX en Tunisie. "
            "Rends STRICTEMENT en JSON : {\"numero\": <le numéro de suivi du "
            "colis, uniquement des chiffres, tel qu'imprimé ; null si tu ne le "
            "vois pas>}. Le numéro de suivi Aramex compte 10 à 12 chiffres et "
            "figure sous le code-barres. Ne rends jamais un numéro de téléphone, "
            "un montant, ni une date."),
        input=[{"role": "user", "content": [
            {"type": "input_image",
             "image_url": "data:image/jpeg;base64,%s" % base64.b64encode(contenu).decode()}]}],
    )
    try:
        lu = frappe.parse_json(res.output_text or "{}").get("numero")
    except Exception:
        lu = None
    return re.sub(r"\D", "", str(lu or ""))


@frappe.whitelist()
def verifier_bordereau(tache, numero):
    """Confronte le numéro saisi à la photo du bordereau, puis l'enregistre.

    L'UTILISATEUR A CHOISI DE BLOQUER (02/09/2026) : un numéro qui ne concorde
    pas n'est pas enregistré. Deux situations ne sont pourtant PAS un désaccord,
    et les traiter comme tel enfermerait le technicien :
      - AUCUNE PHOTO, ou aucun numéro lisible dessus : il n'y a rien à comparer.
        On refuse, mais en disant quoi faire — reprendre la photo — parce que la
        clôture d'une livraison Aramex exige de toute façon ce cliché.
      - SERVICE DE LECTURE INDISPONIBLE (clé absente, panne réseau) : ce n'est
        pas un écart, c'est une panne de notre côté. Bloquer là-dessus
        immobiliserait des colis réels pour une raison qui n'a rien à voir avec
        eux. On enregistre, en le SIGNALANT et en le traçant.
    """
    doc = _ma_tache(tache)
    saisi = re.sub(r"\D", "", numero or "")
    if not saisi:
        frappe.throw(_("Saisissez le numéro de bordereau."))
    if not doc.commande_client:
        frappe.throw(_("Cette intervention n'a pas de commande liée."))

    photos = _photos_de(doc)
    if not photos:
        frappe.throw(_("Photographiez d'abord le bordereau : sans la photo, le "
                       "numéro ne peut pas être vérifié."))

    lus, panne = [], None
    for url in photos:
        try:
            lu = _lire_numero_sur_photo(url)
        except Exception as e:
            panne = str(e)[:160]
            frappe.log_error(frappe.get_traceback(), "Lecture bordereau %s" % doc.name[:50])
            break
        if lu:
            lus.append(lu)
            if lu == saisi:
                return _enregistrer_bordereau(doc, saisi, "lu sur la photo")

    if panne:
        return _enregistrer_bordereau(
            doc, saisi, "NON VÉRIFIÉ — lecture de la photo indisponible (%s)" % panne,
            avertissement=_("Le numéro a été enregistré SANS vérification : la "
                            "lecture de la photo est indisponible."))
    if not lus:
        frappe.throw(_("Aucun numéro n'a pu être lu sur la ou les photos. "
                       "Reprenez la photo du bordereau, bien à plat et nette."))
    frappe.throw(_("Le numéro saisi ({0}) ne correspond pas à la photo : "
                   "on y lit {1}. Corrigez la saisie, ou reprenez la photo.")
                 .format(saisi, ", ".join(lus)))


def _enregistrer_bordereau(doc, numero, comment, avertissement=None):
    """Pose le bordereau SUR LA COMMANDE — là où toute l'app le lit."""
    frappe.db.set_value("Sales Order", doc.commande_client,
                        "custom_bordereau_aramex", numero)
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info",
        "reference_doctype": DOCTYPE_TACHE, "reference_name": doc.name,
        "content": _("📦 Bordereau Aramex {0} enregistré — {1}").format(numero, comment),
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"bordereau": numero, "avertissement": avertissement}


# ------------------------------------------------------------------ clôture


@frappe.whitelist()
def cloturer(tache, rapport_visite=None):
    """Termine l'intervention depuis l'écran, sous LES MÊMES règles que la fiche.

    Aucun `ignore_permissions` : le passage à « Completed » déclenche
    `verifier_photos_cloture`, qui refuse si les photos, la position ou le
    compte rendu manquent. L'écran n'a aucun pouvoir que la fiche n'ait pas.
    """
    doc = _ma_tache(tache)
    if doc.status == "Completed":
        return {"tache": doc.name, "statut": doc.status, "deja": True}
    if rapport_visite:
        doc.rapport_visite = rapport_visite
    doc.status = "Completed"
    doc.save()
    frappe.db.commit()
    return {"tache": doc.name, "statut": doc.status}
