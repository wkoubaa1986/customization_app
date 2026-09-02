"""Rappel SMS de la veille, et avis de remise Aramex du jour.

Reprend le Server Script « Rappelle Rendez vous » (cron 20 h), qui tombait
SEPT SOIRS SUR TRENTE — et comme il envoyait au fil de la boucle, tous les
clients situés APRÈS le rendez-vous fautif ne recevaient rien, sans le moindre
signalement. Trois causes relevées en août-septembre 2026, toutes de la même
famille — une donnée manquante que le code ne prévoyait pas :
  - « Sales Order None not found » : une livraison sans commande liée ;
  - « can only concatenate str (not NoneType) » : une tâche sans nom d'employé
    (29 en 60 jours), collé au message par concaténation ;
  - « NoneType object is not callable ».

LA RÈGLE DE CE MODULE : un rendez-vous qui échoue n'empêche jamais les
suivants. Chaque envoi est isolé, ce qui manque est SIGNALÉ au lieu d'arrêter
la tournée, et le passage rend un compte rendu — envoyés, sautés, et pourquoi.

CE QUI EST RAPPELÉ (décision utilisateur 02/09/2026). Les types sont nommés
UN PAR UN : l'ancien script rappelait « tout ce qui ne s'appelle pas
Livraison », si bien que « Autre » recevait des SMS que personne n'avait
décidés, et qu'un type créé demain en enverrait aussi. Ici, ajouter un type est
un geste conscient.
  - Entretien, Réparation, Installation, Visite : rappel la veille au soir ;
  - Livraison PAR NOTRE ÉQUIPE : rappel la veille, message dédié ;
  - Livraison ARAMEX : pas de rappel la veille — un avis de remise le soir du
    jour où la tâche est terminée, avec le numéro de suivi ;
  - « Autre » : rien.

L'HEURE EST ANNONCÉE COMME ESTIMATIVE. La moitié des rendez-vous vient du
portail, où le client a réservé une DEMI-JOURNÉE : l'heure posée sur la tâche
n'est qu'un point d'ancrage au calendrier, jamais une promesse. Le message le
dit en une ligne, au lieu du paragraphe d'excuses de l'ancien script — qui
coûtait un troisième segment de SMS sur chaque envoi.

LE CONSENTEMENT SMS NE S'APPLIQUE PAS ICI (décision utilisateur 02/09/2026).
`Customer.custom_envoi_sms = Non` couvre les RELANCES et les messages
commerciaux. Un rappel de rendez-vous est un message de SERVICE : le client a
pris ce rendez-vous, un technicien se déplace, et le prévenir n'est pas de la
prospection. Deux clients étaient écartés ce soir à ce titre — Lycée Pierre
Mendès France et Ben Ghorbel Fouzi — alors qu'ils attendent une intervention
demain. L'avis de remise Aramex n'a lui non plus jamais filtré le
consentement : les deux listes suivent maintenant la même règle.

LE NOM ET LE NUMÉRO DU TECHNICIEN VIENNENT DE LA MÊME SOURCE : la fiche
employé liée à la tâche. Le champ texte `custom_employé` de la tâche peut la
contredire — 29 tâches l'ont vide, et Tache-07413 affiche « Mohamed Hedi
Chouchane » alors que l'employé affecté est Jamel Aloui. Prendre le nom d'un
côté et le numéro de l'autre donnerait au client un nom avec le téléphone de
quelqu'un d'autre.
"""
from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, nowdate

DOCTYPE_TACHE = "Tache de travail"

# Les types rappelés la veille, nommés explicitement (cf. en-tête).
TYPES_RAPPELES = ("Entretien", "Réparation", "Installation", "Visite")
TYPE_LIVRAISON = "Livraison"
TERMES_ARAMEX = "Livraison Aramex"

SIGNATURE = "Aqua World - 98511119"
# Une seule phrase, et elle dit l'essentiel : ce n'est pas une heure ferme.
AVERTISSEMENT = "Horaire indicatif, il peut varier dans la journee."


# ------------------------------------------------------------------ outils


def simulation():
    """⛔ GARDE-FOU DEV. La base de dev porte les VRAIS numéros des clients et
    la VRAIE passerelle : sans cette garde, une recette de rappels expédie de
    vrais SMS (déjà arrivé le 28/08/2026). Même règle que sms_commandes."""
    return bool(cint(frappe.conf.get("developer_mode"))) \
        and not cint(frappe.conf.get("sms_groupe_reel_en_dev"))


def _numeros(brut):
    """Les numéros mobiles tunisiens d'un champ multi-lignes. Les fixes sortent.

    L'ancien script calculait le numéro nettoyé puis renvoyait la ligne BRUTE,
    espaces compris, et plantait (`itel_T[0]`) sur toute ligne non conforme.
    """
    out = []
    for ligne in re.split(r"[\n,;/]", brut or ""):
        chiffres = re.sub(r"\D", "", ligne)
        if chiffres.startswith("216"):
            chiffres = chiffres[3:]
        # Mobile tunisien : 8 chiffres, et pas un fixe (7…) ni un spécial (3…).
        if re.fullmatch(r"[2459]\d{7}", chiffres) and chiffres not in out:
            out.append(chiffres)
    return out


def _technicien(tache):
    """(nom, téléphone) pris sur la FICHE EMPLOYÉ — jamais sur le texte libre."""
    if not tache.get("custom_choix_du_staff"):
        return "", ""
    e = frappe.db.get_value("Employee", tache["custom_choix_du_staff"],
                            ["employee_name", "cell_number"], as_dict=True) or {}
    numeros = _numeros(e.get("cell_number"))
    return (e.get("employee_name") or "").strip(), (numeros[0] if numeros else "")


def _jour(valeur):
    return getdate(valeur).strftime("%d/%m")


def _heure(valeur):
    return str(valeur)[11:16] if valeur else ""


def _bordereau_aramex(commande):
    """Le numéro de suivi, lu OÙ IL FAIT FOI.

    L'ancien script prenait `payment_schedule[0].custom__n_chèque__transaction`,
    qui lève dès que l'échéancier est vide et ne dit rien du bordereau réel.
    La source maîtresse est le paiement posé sur le compte Aramex, le champ de
    la commande en repli — exactement la règle de l'écran Traitement.
    """
    from customization_app.traitement_commandes import aramex_des_commandes

    return (aramex_des_commandes([commande]).get(commande) or {}).get("bordereau") or ""


# ------------------------------------------------------------------ messages


def message_rendez_vous(tache):
    """Le rappel de la veille, pour Entretien / Réparation / Installation / Visite."""
    nom, tel = _technicien(tache)
    lignes = [
        "Bonsoir %s," % (tache.get("nom_client") or tache.get("custom_client") or ""),
        "Rappel : %s demain %s vers %s. %s" % (
            tache.get("custom_type_dintervention") or "rendez-vous",
            _jour(tache["starts_on"]), _heure(tache["starts_on"]), AVERTISSEMENT),
    ]
    if nom:
        lignes.append("Technicien : %s%s." % (nom, (" - " + tel) if tel else ""))
    lignes.append(SIGNATURE)
    return "\n".join(lignes)


def message_livraison(tache):
    """Livraison par NOTRE équipe : le client attend un passage, pas un colis."""
    nom, tel = _technicien(tache)
    lignes = [
        "Bonsoir %s," % (tache.get("nom_client") or tache.get("custom_client") or ""),
        "Votre commande sera livree demain %s vers %s. %s" % (
            _jour(tache["starts_on"]), _heure(tache["starts_on"]), AVERTISSEMENT),
    ]
    if nom:
        lignes.append("Livreur : %s%s." % (nom, (" - " + tel) if tel else ""))
    lignes.append(SIGNATURE)
    return "\n".join(lignes)


def message_aramex(tache, bordereau):
    """Avis de remise : le colis est parti, voici son suivi."""
    return "\n".join([
        "Bonsoir %s," % (tache.get("nom_client") or tache.get("custom_client") or ""),
        "Votre commande a ete remise aujourd'hui a ARAMEX pour livraison.",
        ("N de suivi : %s." % bordereau) if bordereau
        else "Le numero de suivi vous sera communique.",
        SIGNATURE,
    ])


# ------------------------------------------------------------------ collecte


def _taches(filtres):
    return frappe.get_all(
        DOCTYPE_TACHE, filters=filtres,
        fields=["name", "status", "commande_client", "custom_client", "nom_client",
                "custom_choix_du_staff", "custom_type_dintervention", "starts_on",
                "ends_on", "dans_local"],
        order_by="starts_on asc", limit_page_length=0)


def rendez_vous_de_demain():
    """Les rendez-vous à rappeler ce soir.

    ⚠️ « dans_local = Oui » EST EXCLU (règle rappelée le 02/09/2026) : une
    réparation faite dans nos locaux ne se rappelle pas — c'est le client qui a
    déposé son appareil, personne ne se déplace chez lui. Le champ n'est
    renseigné que sur Réparation, Visite et Autre ; ailleurs il est vide, et
    « vide » vaut « pas dans nos locaux ».
    """
    demain = add_days(getdate(nowdate()), 1)
    taches = _taches({"status": "Open",
                      "starts_on": ["between", ["%s 00:00:00" % demain,
                                                "%s 23:59:59" % demain]],
                      "custom_type_dintervention": ["in", list(TYPES_RAPPELES)
                                                    + [TYPE_LIVRAISON]]})
    return [t for t in taches
            if (t.get("dans_local") or "") != "Oui" and t.get("custom_client")]


def remises_aramex_du_jour():
    """Les livraisons Aramex terminées AUJOURD'HUI : le colis vient de partir."""
    jour = getdate(nowdate())
    taches = _taches({"status": "Completed",
                      "custom_type_dintervention": TYPE_LIVRAISON,
                      "starts_on": ["between", ["%s 00:00:00" % jour,
                                                "%s 23:59:59" % jour]]})
    return [t for t in taches
            if (t.get("dans_local") or "") != "Oui" and t.get("custom_client")]


def _est_aramex(commande):
    if not commande:
        return False
    return frappe.db.get_value("Sales Order", commande,
                               "payment_terms_template") == TERMES_ARAMEX


def preparer(tache, aramex=False):
    """(message, motif_de_saut) — l'un des deux est toujours None.

    Tout ce qui manque devient un MOTIF, jamais une exception : c'est ce qui
    empêche un rendez-vous bancal d'emporter la tournée entière.
    """
    if not tache.get("starts_on"):
        return None, "sans date"
    type_i = tache.get("custom_type_dintervention")

    if aramex:
        if not _est_aramex(tache.get("commande_client")):
            return None, "livraison de notre équipe, pas Aramex"
        return message_aramex(tache, _bordereau_aramex(tache["commande_client"])), None

    if type_i == TYPE_LIVRAISON:
        # Le colis Aramex n'est pas annoncé la veille : on ne sait pas quel
        # jour il sera présenté. Il aura son avis de remise le soir du départ.
        if _est_aramex(tache.get("commande_client")):
            return None, "livraison Aramex : avis envoyé le jour de la remise"
        return message_livraison(tache), None

    if type_i not in TYPES_RAPPELES:
        return None, "type non rappelé (%s)" % type_i
    return message_rendez_vous(tache), None


# ------------------------------------------------------------------ envoi


def _deja_envoye(tache, jour):
    """Un rappel déjà parti aujourd'hui pour cette tâche ne repart pas.

    Sans cette garde, relancer le passage à la main doublerait les SMS. La
    trace vit sur la tâche : six mois plus tard, on doit pouvoir dire ce qui a
    été envoyé, à qui, et quand.
    """
    return bool(frappe.db.exists("Comment", {
        "reference_doctype": DOCTYPE_TACHE, "reference_name": tache["name"],
        "comment_type": "Info", "content": ["like", "%%Rappel SMS%%%s%%" % jour]}))


def _tracer(tache, jour, texte, numeros, simule):
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info",
        "reference_doctype": DOCTYPE_TACHE, "reference_name": tache["name"],
        "content": _("📲 Rappel SMS du {0}{1} — {2}<br>{3}").format(
            jour, " (SIMULÉ, dev)" if simule else "", ", ".join(numeros),
            frappe.utils.escape_html(texte)),
    }).insert(ignore_permissions=True)


def _envoyer(numero, texte):
    from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import (
        envoyer_sms_verifie,
    )

    envoyer_sms_verifie(numero, texte, tentatives=2)


def _traiter(tache, jour, aramex, envoyer, resultat):
    """UN rendez-vous, de bout en bout. Ne lève jamais.

    C'est ici que se joue la solidité : chaque motif d'abandon est nommé et
    compté, aucun ne remonte assez haut pour interrompre les suivants.
    """
    def sauter(motif):
        resultat["sautes"].append({"tache": tache["name"], "motif": motif})

    try:
        if _deja_envoye(tache, jour):
            return sauter("rappel déjà envoyé aujourd'hui")

        texte, motif = preparer(tache, aramex=aramex)
        if motif:
            return sauter(motif)

        numeros = _numeros(frappe.db.get_value(
            "Customer", tache["custom_client"], "custom_liste_telephone"))
        if not numeros:
            return sauter("aucun numéro mobile exploitable")

        simule = simulation()
        # ⚠️ L'APERÇU N'ÉCRIT RIEN — NI SMS, NI TRACE. Tracer un envoi qui n'a
        # pas eu lieu ferait sauter le rendez-vous au vrai passage de 20 h, au
        # motif qu'il aurait « déjà été envoyé » : le client ne recevrait rien
        # parce que quelqu'un a voulu vérifier la liste (constaté en recette).
        if envoyer:
            if not simule:
                for numero in numeros:
                    _envoyer(numero, texte)
            _tracer(tache, jour, texte, numeros, simule)
            frappe.db.commit()
        resultat["envoyes"].append({"tache": tache["name"],
                                    "client": tache.get("nom_client")
                                              or tache["custom_client"],
                                    "numeros": numeros, "simule": simule,
                                    "message": texte})
    except Exception as e:
        # Le SEUL endroit où une exception est absorbée, et elle est nommée.
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Rappel RDV %s" % tache["name"][:60])
        resultat["echecs"].append({"tache": tache["name"],
                                   "erreur": "%s : %s" % (type(e).__name__, str(e)[:160])})


def executer(envoyer: bool = True) -> dict:
    """Le passage du soir. -> compte rendu {envoyes, sautes, echecs}.

    `envoyer=False` : tout est calculé et rendu, RIEN ne part ni ne se trace —
    c'est l'aperçu, pour vérifier une liste avant 20 h.
    """
    jour = str(getdate(nowdate()))
    resultat = {"jour": jour, "envoyes": [], "sautes": [], "echecs": [],
                "simulation": simulation()}

    for tache in rendez_vous_de_demain():
        _traiter(tache, jour, False, envoyer, resultat)
    for tache in remises_aramex_du_jour():
        _traiter(tache, jour, True, envoyer, resultat)

    resultat["resume"] = "%d envoyé(s), %d sauté(s), %d échec(s)" % (
        len(resultat["envoyes"]), len(resultat["sautes"]), len(resultat["echecs"]))
    if resultat["echecs"]:
        # Un échec ne bloque plus rien — raison de plus pour qu'il se VOIE.
        frappe.log_error(frappe.as_json(resultat["echecs"])[:2000],
                         "Rappel RDV : %d échec(s)" % len(resultat["echecs"]))
    return resultat


def cron_du_soir():
    """Point d'entrée du planificateur (20 h)."""
    return executer(envoyer=True)


@frappe.whitelist()
def apercu():
    """Ce que le passage de ce soir enverrait, sans rien envoyer."""
    frappe.only_for(("System Manager", "Maintenance Manager", "Sales Manager"))
    return executer(envoyer=False)
