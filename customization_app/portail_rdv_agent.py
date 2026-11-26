"""Le magasin prend le rendez-vous À LA PLACE du client — même app, sans OTP.

Demande 31/08/2026 : au téléphone, l'opératrice ne peut pas demander au client
de lire un code SMS. Elle ouvre le portail elle-même, choisit le client, et
réserve pour lui.

POURQUOI RÉUTILISER LE PORTAIL ET NON UN ÉCRAN DE PLUS. Le calendrier interne
(« 📅 Prendre RDV ») pose une tâche à l'heure qu'on veut, sans les règles du
portail : secteur de l'adresse, capacité de la demi-journée, battements, quota
lointain, délai d'ouverture par type. Prendre le rendez-vous « comme le client »
est le SEUL moyen que l'organisation reste la même des deux côtés — un créneau
promis au téléphone vaut alors exactement un créneau pris en ligne.

CE QUI REMPLACE L'OTP. Le code SMS prouve que l'appelant possède le numéro.
Ici, la preuve est ailleurs : c'est un utilisateur du Desk, identifié par sa
session, autorisé à créer des tâches. Le jeton d'entrée est donc :
  - à usage borné (30 min) ;
  - LIÉ À L'UTILISATEUR qui l'a créé — un lien volé ne sert à rien sans le
    cookie de session correspondant ;
  - inutilisable par un invité : aucun point d'entrée ici n'est allow_guest.
Un client qui tomberait sur `/rdv?agent=…` retombe sur l'écran du téléphone.

⚠️ ET LE CLIENT EST QUAND MÊME PRÉVENU. Le SMS (et l'e-mail) de confirmation
partent comme pour une réservation en ligne : c'est le client qui se déplace,
pas l'opératrice — il doit avoir la trace écrite de son rendez-vous.
"""
from __future__ import annotations

import re

import frappe
from frappe import _

TICKET_TTL = 1800   # 30 min : le temps d'un appel, pas d'une journée
MAX_RESULTATS = 25


# ------------------------------------------------------------------ gardes


def _verifier_agent():
    """Utilisateur du Desk autorisé à poser des rendez-vous — sinon rien.

    Le droit choisi est « créer une tâche de travail » : c'est exactement ce
    que la réservation produit. Un rôle dédié se désynchroniserait du reste.
    """
    if frappe.session.user in (None, "", "Guest"):
        frappe.throw(_("Connectez-vous pour prendre un rendez-vous à la place "
                       "d'un client."), frappe.AuthenticationError)
    if not frappe.has_permission("Tache de travail", "create"):
        frappe.throw(_("Vous n'avez pas le droit de créer des interventions."),
                     frappe.PermissionError)
    # La recherche de client rend des fiches : exiger le droit de les lire, sans
    # quoi ce point d'entrée deviendrait un annuaire clients pour qui n'y a pas
    # accès par ailleurs.
    if not frappe.has_permission("Customer", "read"):
        frappe.throw(_("Vous n'avez pas accès aux fiches clients."),
                     frappe.PermissionError)


def _ticket(jeton):
    """Le jeton d'entrée, et il DOIT appartenir à l'utilisateur connecté."""
    donnees = frappe.cache().get_value("rdv_agent:%s" % (jeton or "")) or None
    if not donnees:
        frappe.throw(_("Lien expiré — rouvrez « RDV par l'app » depuis la "
                       "commande ou la liste."), frappe.AuthenticationError)
    if donnees.get("utilisateur") != frappe.session.user:
        frappe.throw(_("Ce lien a été ouvert par un autre utilisateur."),
                     frappe.PermissionError)
    return donnees


def _client_utilisable(client):
    fiche = frappe.db.get_value("Customer", client,
                                ["name", "customer_name", "disabled"], as_dict=True)
    if not fiche or fiche.disabled:
        frappe.throw(_("Client introuvable ou désactivé."))
    return {"client": fiche.name, "nom": fiche.customer_name or fiche.name}


def _telephone(client):
    """Le numéro du client — il part sur la tâche et porte le SMS de
    confirmation. Absent, la réservation reste possible : le portail
    n'enverra simplement pas de SMS (cf. portail_rdv._envoyer_sms)."""
    from customization_app.portail_rdv import _normaliser

    brut = frappe.db.get_value("Customer", client,
                               ["custom_liste_telephone", "mobile_no"], as_dict=True) or {}
    for champ in ("custom_liste_telephone", "mobile_no"):
        for morceau in re.split(r"[,;/\n]", brut.get(champ) or ""):
            numero = _normaliser(morceau)
            if numero:
                return numero
    return ""


def _identite():
    return frappe.db.get_value("User", frappe.session.user, "full_name") \
        or frappe.session.user


# ------------------------------------------------------------------ entrée


def origine_valide(origine):
    """L'origine d'une prise de rendez-vous depuis le Desk, ou None (pur).

    Deux formes : {"liste_appelle": <Liste Appelle Entretien>, "ligne": <nom de la ligne
    Appelle Client>} — la ligne prendra la tâche et « Rendez-vous pris » — ou
    {"tache_rattrapage": <Tache de travail>} — la tâche manquée passe « planifié » et sort du
    rapport Liste Appels Rattrapage. Tout le reste est ignoré : l'origine n'est qu'un
    rattachement de confort, jamais une règle de réservation."""
    if isinstance(origine, str):
        try:
            origine = frappe.parse_json(origine)
        except Exception:
            return None
    if not isinstance(origine, dict):
        return None
    if origine.get("liste_appelle") and origine.get("ligne"):
        return {"liste_appelle": str(origine["liste_appelle"]), "ligne": str(origine["ligne"])}
    if origine.get("tache_rattrapage"):
        return {"tache_rattrapage": str(origine["tache_rattrapage"])}
    return None


@frappe.whitelist()
def ouvrir(commande=None, client=None, origine=None):
    """Ouvre un lien d'accès au portail. -> {url}

    Depuis une commande, le client est déduit d'elle (et le type d'intervention
    qu'appellent ses lignes est transmis à titre d'indication). Depuis la liste,
    ni l'un ni l'autre : l'app fera chercher le client. `origine` (liste d'appels
    ou tâche de rattrapage, cf. `origine_valide`) suit la séance : à la
    réservation, la liste est mise à jour comme le fait le calendrier interne.
    """
    _verifier_agent()
    origine = origine_valide(origine)
    if origine and origine.get("liste_appelle") and not frappe.has_permission(
            "Liste Appelle Entretien", "write", doc=origine["liste_appelle"]):
        frappe.throw(_("Vous ne pouvez pas modifier cette liste d'appels."), frappe.PermissionError)
    if origine and origine.get("tache_rattrapage") and not frappe.has_permission(
            "Tache de travail", "write", doc=origine["tache_rattrapage"]):
        frappe.throw(_("Vous ne pouvez pas modifier cette tâche."), frappe.PermissionError)
    type_indicatif = None
    if commande:
        if not frappe.has_permission("Sales Order", "read", doc=commande):
            frappe.throw(_("Accès non autorisé à la commande {0}").format(commande),
                         frappe.PermissionError)
        client = frappe.db.get_value("Sales Order", commande, "customer")
        if not client:
            frappe.throw(_("Commande introuvable : {0}").format(commande))
        try:
            from customization_app.api import rdv_depuis_commande
            type_indicatif = (rdv_depuis_commande(commande) or {}).get("type_intervention")
        except Exception:
            # Une déduction de type qui échoue ne doit pas empêcher de prendre
            # le rendez-vous : ce n'est qu'une indication portée à l'écran.
            type_indicatif = None
    if client:
        _client_utilisable(client)

    # ⚠️ LE JETON CSRF DOIT EXISTER AVANT QUE /rdv NE SOIT RENDUE. Frappe ne le
    # fabrique QUE lorsqu'on le lui demande (get_csrf_token, appelé au boot du
    # Desk) ; une session qui ne l'a jamais réclamé fait écrire
    # `frappe.csrf_token = "None"` dans la page — et le premier POST du portail
    # part sans en-tête, donc en « Requête Invalide » (constaté 31/08). On le
    # force ici : cet appel précède forcément l'ouverture de la page.
    # Hors requête HTTP (console, tâche de fond, test), il n'y a pas de session
    # à compléter et `session_obj` n'existe pas : l'absence de jeton n'est alors
    # un problème pour personne — c'est le navigateur qui en a besoin.
    try:
        from frappe.sessions import get_csrf_token

        get_csrf_token()
    except Exception:
        pass

    jeton = frappe.generate_hash(length=32)
    frappe.cache().set_value("rdv_agent:%s" % jeton,
                             {"utilisateur": frappe.session.user,
                              "client": client or None,
                              "commande": commande or None,
                              "type": type_indicatif,
                              "origine": origine},
                             expires_in_sec=TICKET_TTL)
    return {"url": "/rdv?agent=%s" % jeton, "client": client, "commande": commande}


@frappe.whitelist()
def contexte(jeton):
    """Ce que l'app doit savoir en s'ouvrant : la séance du client, ou l'ordre
    de le faire chercher."""
    _verifier_agent()
    donnees = _ticket(jeton)
    entete = {"utilisateur": _identite(),
              "commande": donnees.get("commande"),
              "type": donnees.get("type"),
              "origine": donnees.get("origine")}
    if not donnees.get("client"):
        return {"agent": entete, "choisir": 1}
    return _seance(donnees["client"], entete)


@frappe.whitelist()
def chercher(jeton, recherche):
    """Les clients dont le NOM ou le TÉLÉPHONE ressemble à ce qui est tapé.

    Le portail public, lui, ne dit jamais si un numéro est connu (énumération) :
    ici c'est l'inverse qu'on veut — l'opératrice a le client en ligne et doit
    retrouver sa fiche, y compris quand il donne son numéro et non son nom.
    """
    _verifier_agent()
    _ticket(jeton)
    terme = (recherche or "").strip()
    if len(terme) < 2:
        frappe.throw(_("Tapez au moins 2 caractères."))
    chiffres = re.sub(r"\D", "", terme)
    lignes = frappe.db.sql(
        """SELECT name, customer_name, custom_liste_telephone, mobile_no
           FROM tabCustomer
           WHERE COALESCE(disabled, 0) = 0
             AND (customer_name LIKE %(mot)s OR name LIKE %(mot)s
                  OR (%(tel)s <> '' AND (
                        REPLACE(REPLACE(COALESCE(custom_liste_telephone, ''), ' ', ''),
                                '+216', '') LIKE %(num)s
                     OR REPLACE(COALESCE(mobile_no, ''), ' ', '') LIKE %(num)s)))
           ORDER BY customer_name
           LIMIT %(max)s""",
        {"mot": "%%%s%%" % terme, "tel": chiffres,
         "num": "%%%s%%" % (chiffres or "\x00"), "max": MAX_RESULTATS}, as_dict=True)
    return [{"client": l.name,
             "nom": l.customer_name or l.name,
             "telephone": (l.custom_liste_telephone or l.mobile_no or "")[:40]}
            for l in lignes]


@frappe.whitelist()
def session(jeton, client):
    """Ouvre la séance du portail sur le client choisi par l'opératrice."""
    _verifier_agent()
    donnees = _ticket(jeton)
    return _seance(client, {"utilisateur": _identite(),
                            "commande": donnees.get("commande"),
                            "type": donnees.get("type"),
                            "origine": donnees.get("origine")})


def _seance(client, entete):
    """La MÊME séance que celle d'un client venu par SMS — aucune règle en moins.

    C'est tout l'intérêt : capacité, secteurs, délais, types offerts, commandes
    encore sans intervention, tout est calculé par le portail lui-même.
    """
    from customization_app.portail_rdv import _config, _ouvrir_session

    _config()
    entree = _client_utilisable(client)
    seance = _ouvrir_session(_telephone(client), entree, agent=entete["utilisateur"],
                             origine=entete.get("origine"))
    seance["agent"] = entete
    # Le magasin voit le téléphone du client : il l'a en ligne, et il doit
    # pouvoir vérifier qu'il travaille sur la bonne fiche.
    seance["telephone"] = _telephone(client)
    return seance


# ------------------------------------------------------------------ historique du client
#
# Demande du 19/11/2026 : l'utilisateur qui prend le rendez-vous au téléphone doit voir d'un coup d'œil À QUI il
# parle — dernière intervention, entretien dû, pièces à proposer, ce qui s'est dit aux derniers appels — sans quitter
# l'app. Les données sont celles du panneau « 📜 Historique client » du Desk (`historique_client`) : une seule source,
# ici en version courte. Lecture seule. Le client, lui, ne voit jamais ce bloc.

LIMITE_INTERVENTIONS, LIMITE_APPELS, LIMITE_ACHATS, LIMITE_PIECES = 8, 6, 4, 4


def resume_historique(a_venir, passees, appels, echeanciers, achats, pieces) -> dict:
    """La version courte de l'historique, prête pour l'écran. PURE.

    -> {derniere, machines, prochaine_visite, pieces, a_venir, interventions, nb_interventions, appels, achats}."""
    derniere = next((i for i in passees if i.get("status") == "Completed"), None)
    machines, visites = [], []
    for e in echeanciers or []:
        if e.get("annule"):
            # 26/11/2026 : historique_client rend aussi les échéanciers ANNULÉS (plus supprimés) ; ni leurs machines ni leurs
            # visites ne sont celles du client aujourd'hui (machine rendue, commande annulée, « pas de système »…).
            continue
        noms = {m.get("item_code"): m.get("item_name") or m.get("item_code") for m in e.get("machines") or []}
        machines += [{"nom": m.get("item_name") or m.get("item_code"), "depuis": m.get("depuis") or ""}
                     for m in e.get("machines") or []]
        visites += [{"prevue": v.get("prevue"), "etat": v.get("etat"), "machine": noms.get(v.get("item_code")) or ""}
                    for v in e.get("visites") or [] if v.get("etat") in ("en attente", "à venir") and v.get("prevue")]
    # « En attente » (date dépassée, pas faite) passe avant « à venir » : c'est elle qu'il faut proposer au client.
    visites.sort(key=lambda v: (v["etat"] != "en attente", v["prevue"]))
    return {
        "derniere": ({"date": derniere["date"], "type": derniere["type"], "employe": derniere.get("employe") or ""}
                     if derniere else None),
        "machines": machines[:3],
        "prochaine_visite": visites[0] if visites else None,
        "pieces": [{"libelle": p.get("libelle"), "statut": p.get("statut"), "echeance": str(p.get("echeance") or ""),
                    "retard_jours": p.get("retard_jours") or 0}
                   for p in (pieces or {}).get("pieces") or [] if p.get("statut") in ("retard", "bientot")][:LIMITE_PIECES],
        "a_venir": [{"date": i["date"], "type": i["type"], "employe": i.get("employe") or ""} for i in a_venir][:5],
        "interventions": [{"date": i["date"], "type": i["type"], "status": i["status"], "employe": i.get("employe") or "",
                           "rapport": i.get("rapport") or "", "sujet": i.get("sujet") or "",
                           "raison": i.get("raison_annulation") or ""} for i in passees[:LIMITE_INTERVENTIONS]],
        "nb_interventions": len(passees),
        "appels": [{"date": a["date"], "titre": a.get("titre") or "", "par": a.get("par") or "",
                    "texte": a.get("texte") or "", "detail": a.get("detail") or ""} for a in (appels or [])[:LIMITE_APPELS]],
        "nb_appels": len(appels or []),
        "achats": [{"name": c["name"], "date": c.get("date") or "", "total": c.get("total") or 0,
                    "articles": [x.get("nom") or x.get("code") for x in c.get("articles") or []][:3],
                    "nb_articles": len(c.get("articles") or [])} for c in (achats or [])[:LIMITE_ACHATS]],
        "nb_achats": len(achats or []),
    }


@frappe.whitelist()
def historique(jeton, seance):
    """L'historique court du client de la séance en cours. -> {autorise: 0} pour un compte qui n'y a pas droit
    (le partenaire exécute des tâches chez nos clients, leur historique reste à nous)."""
    _verifier_agent()
    _ticket(jeton)
    from customization_app import historique_client as HC
    from customization_app import pieces_a_changer as PC
    from customization_app.portail_rdv import _session

    session = _session(seance)
    if not session.get("agent"):
        frappe.throw(_("Historique réservé au magasin."), frappe.PermissionError)
    if not HC._autorise():
        return {"autorise": 0}
    client = session["client"]
    taches = HC._taches(client)
    a_venir, passees = HC.interventions(taches)
    achats = HC.achats(client)
    commandes = frappe.get_all("Sales Order", filters={"customer": client, "docstatus": ["<", 2]}, pluck="name")
    appels = HC.appels_et_commentaires(client, commandes or [c["name"] for c in achats], [t.name for t in taches])
    resume = resume_historique(a_venir, passees, appels, HC.echeancier(client)["echeanciers"], achats,
                               PC.pour_clients([client]).get(client) or {})
    resume.update({"autorise": 1, "client": client, "fiche": "/app/customer/" + frappe.utils.quote(client)})
    return resume
