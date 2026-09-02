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

TYPE_LIVRAISON = "Livraison"

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
    if not cible and not _supervise():
        frappe.throw(_("Aucune fiche employé n'est rattachée à votre compte — "
                       "demandez au magasin de la lier."))
    # ⚠️ UN SUPERVISEUR SANS FICHE EMPLOYÉ N'EST PAS UNE ERREUR. Administrator et
    # la direction n'ont pas de journée à eux : lever ici affichait « journée
    # indisponible » sur un écran parfaitement sain (constaté 02/09/2026). On
    # rend None, l'écran propose la liste, et le System Manager choisit — il
    # peut voir et agir pour n'importe qui.
    return cible


def _numeros(brut):
    """Les numéros exploitables d'un champ texte, dédoublonnés et lisibles."""
    from customization_app.rappel_rdv import _numeros as extraire

    return extraire(brut)


def _articles(commande):
    """Ce qu'il y a à poser ou à livrer — avec la PHOTO et, pour une variante,
    sa configuration.

    Le technicien reconnaît un osmoseur à sa photo, pas à son code. Et quand
    l'article est une VARIANTE (335 en catalogue), c'est sa configuration qui
    dit ce qu'il doit poser : marque de membrane, nombre d'étages, mixage. Sans
    elle, deux variantes du même modèle sont indiscernables sur l'écran.

    Tout est chargé en DEUX requêtes pour la commande entière : une par ligne
    ferait vingt allers-retours sur un téléphone en 4G.
    """
    if not commande:
        return []
    lignes = frappe.get_all(
        "Sales Order Item", filters={"parent": commande},
        fields=["item_code", "item_name", "qty", "idx"],
        order_by="idx", limit_page_length=0)
    codes = [l.item_code for l in lignes if l.item_code]
    if not codes:
        return []
    articles = {i.name: i for i in frappe.get_all(
        "Item", filters={"name": ["in", codes]},
        fields=["name", "image", "variant_of"], limit_page_length=0)}
    attributs = {}
    for a in frappe.get_all(
            "Item Variant Attribute", filters={"parent": ["in", codes]},
            fields=["parent", "attribute", "attribute_value", "idx"],
            order_by="idx", limit_page_length=0):
        attributs.setdefault(a.parent, []).append(
            {"attribut": a.attribute, "valeur": a.attribute_value})
    out = []
    for l in lignes:
        i = articles.get(l.item_code) or {}
        out.append({
            "code": l.item_code,
            "article": l.item_name or l.item_code,
            "qte": l.qty,
            "image": i.get("image") or "",
            "variante": bool(i.get("variant_of")),
            "configuration": attributs.get(l.item_code, []),
        })
    return out


def _appels(tache):
    """Les appels déjà passés sur cette tâche, du plus récent au plus ancien."""
    out = []
    for c in frappe.get_all(
            "Comment",
            filters={"reference_doctype": DOCTYPE_TACHE, "reference_name": tache,
                     "comment_type": "Info", "content": ["like", "%" + PREFIXE_APPEL + "%"]},
            fields=["content", "creation", "owner"],
            order_by="creation desc", limit_page_length=20):
        texte = " ".join(re.sub(r"<[^>]+>", " ", c.content or "").split())
        # Le numéro est extrait pour être COMPTÉ par numéro : « appelé 3 fois »
        # devant un téléphone dit en un coup d'œil ce qu'un historique déroulé
        # oblige à reconstituer.
        numero = re.search(r"\bau\s+(\d[\d ]{5,})", texte)
        out.append({"quand": str(c.creation)[:16], "par": c.owner, "texte": texte,
                    "numero": re.sub(r"\D", "", numero.group(1)) if numero else ""})
    return out


# Le mode de paiement qui signifie « rien n'a été encaissé ». Il porte 68 % des
# lignes de dette du trimestre : le confondre avec un règlement fait annoncer
# « soldée » une commande dont l'argent est encore chez le client.
MODE_DETTE = "Dette non payée"


def _reglement(commande_infos, bordereau=""):
    """Ce qui a VRAIMENT été encaissé, et ce qui n'est qu'une dette.

    ⚠️ « PAYÉ » NE VEUT PAS DIRE « ENCAISSÉ ». Une commande peut porter un
    paiement de mode « Dette non payée » : la pièce existe, l'argent non. La
    carte annonçait « soldée » en vert sur 651 DT que personne n'avait touchés
    (constaté 02/09/2026).

    LA DETTE ARAMEX EST L'EXCEPTION (décision utilisateur) : le transporteur
    encaisse à la remise, il n'y a rien à réclamer sur place.

    ⚠️ MAIS SEULEMENT SI LE COLIS EST RÉELLEMENT PARTI. Une commande peut porter
    l'échéancier « Livraison Aramex » sans qu'aucun bordereau n'existe : le
    colis n'a pas voyagé, personne n'a rien encaissé, et c'est souvent NOTRE
    technicien qui se déplace — la tâche est alors une Installation. Le
    BORDEREAU est donc le discriminant, pas le compte seul (question posée le
    02/09/2026 ; le cas existe : Tache-08235, Installation sur WEB1-008367,
    406 DT sur le compte Aramex et aucun bordereau — l'écran annonçait
    « encaissé par Aramex » sur de l'argent que personne n'avait pris).

    Sur 120 jours : 29 livraisons avec bordereau (vraiment chez Aramex),
    5 livraisons sans, et 1 installation sans.
    """
    from customization_app.livraison_aramex import COMPTE_ARAMEX

    if not commande_infos:
        return None
    dette = dette_aramex = 0.0
    for p in commande_infos.get("paiements") or []:
        if (p.get("mode") or "") != MODE_DETTE:
            continue
        if (p.get("compte") or "") == COMPTE_ARAMEX and bordereau:
            dette_aramex += frappe.utils.flt(p.get("montant"))
        else:
            dette += frappe.utils.flt(p.get("montant"))
    total = frappe.utils.flt(commande_infos.get("total"))
    paye = frappe.utils.flt(commande_infos.get("total_paye"))
    return {
        "total": round(total, 3),
        "paye": round(paye, 3),
        "reste": round(total - paye, 3),
        # À encaisser sur place : la dette qui n'est pas celle d'Aramex.
        "dette": round(dette, 3),
        "dette_aramex": round(dette_aramex, 3),
        "paiements": commande_infos.get("paiements") or [],
    }


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
    taches = [] if not cible else frappe.get_all(
        DOCTYPE_TACHE,
        # Les tâches ANNULÉES ne remontent pas (demande 02/09/2026) : elles
        # n'appellent aucune action et allongent une liste qu'on parcourt au
        # pouce, entre deux interventions.
        filters={"custom_choix_du_staff": cible,
                 "status": ["!=", "Cancelled"],
                 "starts_on": ["between", ["%s 00:00:00" % jour, "%s 23:59:59" % jour]]},
        fields=["name", "status", "custom_type_dintervention", "starts_on", "ends_on",
                "custom_client", "nom_client", "tel", "details_adresse", "google_map",
                "secteur", "commande_client", "subject", "rapport_visite",
                "dispense_photos", "dans_local",
                "liste_photos_avant", "liste_photos_apres"],
        order_by="starts_on asc", limit_page_length=0)

    lignes = []
    for t in taches:
        aramex, bordereau = _aramex(t.commande_client)
        # ⚠️ LA RÈGLE ARAMEX NE VAUT QUE POUR UNE LIVRAISON (décision
        # utilisateur 02/09/2026). L'échéancier « Livraison Aramex » est posé
        # AUTOMATIQUEMENT à la création d'une commande web ; quand celle-ci
        # devient finalement une Installation, il ne décrit plus rien — notre
        # technicien se déplace, aucun colis ne part, et l'argent est à
        # encaisser sur place comme n'importe quelle dette.
        if t.custom_type_dintervention != TYPE_LIVRAISON:
            aramex, bordereau = False, ""
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
            # Les photos prises pendant l'intervention : une fois la tâche
            # terminée, elles sont la seule preuve visible de ce qui a été fait
            # — les afficher sur la carte évite d'ouvrir la fiche pour vérifier.
            "photos": _photos_de(t),
            "reglement": _reglement((exig or {}).get("commande_infos"), bordereau),
            "exigences": exig,
        })

    return {
        "jour": str(jour),
        "employe": cible,
        "employe_nom": (frappe.db.get_value("Employee", cible, "employee_name") or cible)
                       if cible else "",
        "sans_employe": not cible,
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
def tracer_appel(tache, numero, resultat=None):
    """Note qu'un appel a été passé.

    ⚠️ LE RÉSULTAT EST FACULTATIF (décision 02/09/2026, après usage). Demander
    « alors, ça a répondu ? » au retour d'appel s'interposait entre le
    technicien et son geste : le clic doit APPELER, un point. Ce qui reste, et
    qui suffit à décider, c'est le NOMBRE d'appels passés à ce numéro — « pas
    encore essayé » contre « essayé trois fois ».

    Le résultat reste accepté pour qui voudrait le renseigner ailleurs ; il est
    alors validé, parce qu'une valeur libre rendrait l'historique illisible.
    """
    doc = _ma_tache(tache)
    if resultat and resultat not in RESULTATS_APPEL:
        frappe.throw(_("Résultat d'appel inconnu."))
    numero = (numero or "").strip()[:20]
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info",
        "reference_doctype": DOCTYPE_TACHE, "reference_name": doc.name,
        "content": (_("{0} au {1} — {2}").format(PREFIXE_APPEL, numero, resultat)
                    if resultat else _("{0} au {1}").format(PREFIXE_APPEL, numero)),
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"appels": _appels(doc.name)}


# ------------------------------------------------- bordereau Aramex vs photo


def _photos_de(doc):
    """Les URL des photos de clôture de la tâche (avant + après).

    `doc` peut être un document Frappe comme une ligne de `get_all` : l'écran
    lit les mêmes photos que la vérification du bordereau, il n'y a aucune
    raison d'en avoir deux lectures.
    """
    from customization_app import cloture_tache as C

    urls = []
    for champ in C.CHAMPS.values():
        for ligne in ((doc.get(champ) if hasattr(doc, "get") else None) or "").split("\n"):
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
    """Écrit le bordereau SUR LA COMMANDE, puis le confronte à la photo.

    ⚠️ ON ENREGISTRE D'ABORD (décision utilisateur 02/09/2026, après essai sur
    le terrain). La première version REFUSAIT le numéro tant qu'aucune photo ne
    le confirmait : le technicien tenait son colis, tapait le numéro, et se
    voyait renvoyer un message au lieu d'un enregistrement. Le geste principal
    — inscrire le bordereau sur la commande — ne doit dépendre de rien.

    LA VÉRIFICATION RESTE, MAIS EN SECOND. S'il y a une photo, le numéro y est
    relu et tout désaccord est SIGNALÉ — le numéro reste enregistré, c'est
    l'humain qui tranche. Sans photo, ou si la lecture échoue, on le dit
    simplement : un service d'analyse indisponible ne doit pas empêcher un colis
    de partir.
    """
    doc = _ma_tache(tache)
    saisi = re.sub(r"\D", "", numero or "")
    if not saisi:
        frappe.throw(_("Saisissez le numéro de bordereau."))
    if not doc.commande_client:
        frappe.throw(_("Cette intervention n'a pas de commande liée."))

    photos = _photos_de(doc)
    if not photos:
        return _enregistrer_bordereau(
            doc, saisi, "saisi à la main, aucune photo à confronter",
            avertissement=_("Numéro enregistré. Aucune photo sur l'intervention : "
                            "il n'a pas pu être vérifié — pensez à photographier "
                            "le bordereau avant de clôturer."))

    lus = []
    for url in photos:
        try:
            lu = _lire_numero_sur_photo(url)
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Lecture bordereau %s" % doc.name[:50])
            return _enregistrer_bordereau(
                doc, saisi, "NON VÉRIFIÉ — lecture de la photo indisponible (%s)"
                            % str(e)[:100],
                avertissement=_("Numéro enregistré SANS vérification : la lecture "
                                "de la photo est indisponible."))
        if lu:
            lus.append(lu)
            if lu == saisi:
                return _enregistrer_bordereau(doc, saisi, "lu sur la photo")

    if not lus:
        return _enregistrer_bordereau(
            doc, saisi, "aucun numéro lisible sur les photos",
            avertissement=_("Numéro enregistré, mais aucun numéro n'a pu être lu "
                            "sur les photos — vérifiez qu'elles montrent bien le "
                            "bordereau, à plat et net."))
    return _enregistrer_bordereau(
        doc, saisi, "DÉSACCORD avec la photo (%s)" % ", ".join(lus),
        avertissement=_("⚠️ Numéro enregistré, mais il ne correspond pas à la "
                        "photo : on y lit {0}. Corrigez la saisie si c'est une "
                        "erreur.").format(", ".join(lus)))


# Ce que la création d'une commande web laisse dans la case du numéro : des
# bouchons, pas des numéros. On a le droit de les remplacer ; un vrai numéro,
# jamais.
BOUCHONS = ("", "x", "xx", "xxx", "xxxx", "xxxxx", "0", "00", "000", "0000", "00000")


def _bouchon(valeur):
    v = (valeur or "").strip().lower()
    return v in BOUCHONS


def _poser_sur_echeancier(commande, numero):
    """Écrit aussi le bordereau dans l'ÉCHÉANCIER de la commande. -> nb de lignes.

    ⚠️ C'EST LÀ QUE LE RESTE DE L'APP LE LIT. Le champ
    `Payment Schedule.custom__n_chèque__transaction` est celui que l'ancien
    rappel du soir consultait pour annoncer le numéro de suivi au client, et
    celui qu'on voit sur la fiche commande. Ne remplir que
    `custom_bordereau_aramex` laissait « xxxxx » sur l'échéancier —
    le colis paraissait sans numéro là où l'utilisateur regarde (constaté
    02/09/2026 sur WEB1-008369).

    ⚠️ ON N'ÉCRASE QUE LES BOUCHONS. « xxxxx », « 0000 », vide : ce sont les
    marques laissées à la création de la commande web. Un numéro déjà saisi —
    par le magasin ou par un encaissement Aramex — est une donnée, et on ne la
    remplace pas en silence.
    """
    poses = 0
    for ligne in frappe.get_all(
            "Payment Schedule", filters={"parent": commande, "parenttype": "Sales Order"},
            fields=["name", "custom__n_chèque__transaction"], limit_page_length=0):
        if not _bouchon(ligne.get("custom__n_chèque__transaction")):
            continue
        frappe.db.set_value("Payment Schedule", ligne["name"],
                            "custom__n_chèque__transaction", numero,
                            update_modified=False)
        poses += 1
    return poses


def _enregistrer_bordereau(doc, numero, comment, avertissement=None):
    """Pose le bordereau SUR LA COMMANDE — là où toute l'app le lit."""
    frappe.db.set_value("Sales Order", doc.commande_client,
                        "custom_bordereau_aramex", numero)
    poses = _poser_sur_echeancier(doc.commande_client, numero)
    if poses:
        comment += " · %d ligne(s) d'échéancier renseignée(s)" % poses
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
