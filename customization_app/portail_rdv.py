"""
Portail public de prise de rendez-vous — /rdv.

Le client reçoit un lien, saisit SON numéro de téléphone, reçoit un code par
SMS (OTP), puis réserve : Entretien / Réparation (tout client connu) ou
Installation (client AVEC commande — il la choisit). Le créneau est une
DEMI-JOURNÉE (date + matin/après-midi, décision 27/08/2026) : la tâche est
créée FERME au calendrier (custom_reservation_app=1), l'heure exacte et le
staff définitif restant au magasin.

SÉCURITÉ — tout est public (allow_guest), donc chaque porte est gardée :
  - l'OTP (6 chiffres, 5 min) vit dans le cache redis, jamais renvoyé au client ;
  - 3 envois de code max par numéro / 15 min, et par IP ;
  - 5 essais de vérification max par code ;
  - un numéro inconnu reçoit la MÊME réponse qu'un envoi réussi — le portail
    ne confirme jamais qu'un numéro est client (pas d'énumération) ;
  - la session est un jeton aléatoire de 32 octets, 30 min, en cache.

Réglages : single « Config Portail RDV » — actif, employé par défaut (le
champ staff de la tâche est obligatoire ; le magasin réaffecte), heures des
demi-journées.
"""
from __future__ import annotations

import random
import re
from contextlib import contextmanager

import frappe
from frappe import _
from frappe.utils import (add_days, cint, formatdate, get_datetime, getdate,
                          nowdate)

DOCTYPE_CONFIG = "Config Portail RDV"
DOCTYPE_TACHE = "Tache de travail"

TYPES_TOUT_CLIENT = ("Entretien", "Réparation")
TYPE_AVEC_COMMANDE = "Installation"
# La LIVRAISON par notre équipe (30/08/2026) : comme l'installation, elle se
# réserve sur UNE commande — mais seulement si le magasin l'a autorisée sur
# cette commande (`custom_livraison_equipe`). Aramex livre tout le reste.
TYPE_LIVRAISON = "Livraison"
TYPES_AVEC_COMMANDE = (TYPE_AVEC_COMMANDE, TYPE_LIVRAISON)

OTP_TTL = 300           # 5 minutes
SESSION_TTL = 1800      # 30 minutes
INSCRIPTION_TTL = 900   # 15 minutes pour remplir le formulaire d'inscription
ENVOIS_MAX = 3          # par numéro, sur 15 min
# Par IP : bien plus large. Les opérateurs mobiles tunisiens font du NAT de
# groupe (CGNAT) : des dizaines de clients LÉGITIMES partagent la même adresse
# publique — un plafond serré (9) bloquait des clients innocents « Trop de
# demandes » alors qu'ils n'avaient rien demandé (constaté : OTP jamais reçus).
ENVOIS_MAX_IP = 30
ENVOIS_FENETRE = 900
ESSAIS_MAX = 5

HORIZON_JOURS = 60      # on ne réserve pas au-delà de deux mois

MESSAGE_GENERIQUE = ("Si ce numéro est connu de nos services, un code vient "
                     "de lui être envoyé par SMS.")


# ------------------------------------------------------------------ outils


def _normaliser(telephone):
    """« +216 58 175 057 » -> « 58175057 ». None si ce n'est pas un numéro TN."""
    chiffres = re.sub(r"\D", "", telephone or "")
    if chiffres.startswith("216"):
        chiffres = chiffres[3:]
    return chiffres if re.fullmatch(r"[2-9]\d{7}", chiffres) else None


def _cache():
    return frappe.cache()


def _compteur_depasse(cle, maximum, fenetre):
    """Incrémente un compteur à fenêtre glissante. True si la limite est atteinte."""
    c = _cache()
    valeur = cint(c.get_value(cle) or 0) + 1
    c.set_value(cle, valeur, expires_in_sec=fenetre)
    return valeur > maximum


def _clients_du_numero(numero):
    """TOUTES les fiches client qui portent ce numéro — un même téléphone sert
    parfois à plusieurs clients (famille, entreprise) : quand il y en a plus
    d'une, l'écran fait CHOISIR après l'OTP (demande 29/08, ex. 98985900).
    Celles qui ont des commandes sortent en premier."""
    lignes = frappe.db.sql(
        """SELECT name, customer_name FROM tabCustomer
           WHERE COALESCE(disabled, 0) = 0
             AND (REPLACE(REPLACE(COALESCE(custom_liste_telephone, ''), ' ', ''),
                          '+216', '') LIKE %(motif)s
                  OR REPLACE(COALESCE(mobile_no, ''), ' ', '') LIKE %(motif)s)""",
        {"motif": "%%%s%%" % numero}, as_dict=True)
    if len(lignes) > 1:
        avec_commande = {l.name for l in lignes if frappe.db.exists(
            "Sales Order", {"customer": l.name, "docstatus": ["<", 2]})}
        lignes.sort(key=lambda l: (l.name not in avec_commande,
                                   (l.customer_name or l.name).lower()))
    return [{"client": l.name, "nom": l.customer_name or l.name} for l in lignes]


def _config():
    valeurs = frappe.db.get_singles_dict(DOCTYPE_CONFIG) or {}
    if valeurs.get("actif") is not None and not cint(valeurs.get("actif")):
        frappe.throw(_("La prise de rendez-vous en ligne est momentanément fermée."))
    return valeurs


def _session(jeton):
    donnees = _cache().get_value("rdv_session:%s" % (jeton or "")) or None
    if not donnees:
        frappe.throw(_("Session expirée — recommencez avec votre numéro."),
                     frappe.AuthenticationError)
    return donnees


def _commandes_du_client(client):
    """Les commandes du client — `sans_tache` dit si une intervention est déjà
    planifiée : l'installation ne se réserve QUE sur une commande encore sans
    tâche (décision 27/08)."""
    return frappe.db.sql(
        """SELECT so.name, so.transaction_date, so.grand_total, so.status,
                  so.docstatus,
                  IFNULL(so.custom_livraison_equipe, 0) AS livraison_equipe,
                  NOT EXISTS (
                      SELECT 1 FROM `tabTache de travail` t
                      WHERE t.commande_client = so.name
                        AND t.status != 'Cancelled') AS sans_tache
           FROM `tabSales Order` so
           WHERE so.customer = %(c)s AND so.docstatus < 2
             AND so.status != 'Closed'
           ORDER BY so.transaction_date DESC
           LIMIT 10""", {"c": client}, as_dict=True)


def _rendez_vous_du_client(client):
    """Les rendez-vous du client, passés et à venir — l'historique du portail."""
    lignes = frappe.get_all(
        DOCTYPE_TACHE,
        filters={"custom_client": client},
        fields=["name", "custom_type_dintervention", "starts_on", "status",
                "custom_reservation_app"],
        order_by="starts_on desc", limit_page_length=10)
    etats = {"Open": _("Prévu"), "Completed": _("Terminé"), "Cancelled": _("Annulé")}
    demain = add_days(getdate(nowdate()), 1)
    return [{
        "tache": l.name,
        "type": l.custom_type_dintervention,
        "date": str(l.starts_on)[:16] if l.starts_on else "",
        "etat": etats.get(l.status, l.status),
        "en_ligne": bool(l.custom_reservation_app),
        # À venir : encore ouvert et pas encore passé — c'est lui que le rappel
        # de l'onglet Réserver affiche, même le jour J.
        "a_venir": l.status == "Open" and bool(l.starts_on)
                   and l.starts_on >= frappe.utils.now_datetime(),
        # Modifiable : encore ouvert et non verrouillé. Le verrou ne couvre que
        # la JOURNÉE EN COURS (décision 29/08) : un RDV passé resté Open — raté,
        # non honoré — se replanifie en ligne ; la NOUVELLE date reste ≥ demain.
        "modifiable": l.status == "Open" and bool(l.starts_on)
                      and not _rdv_verrouille(l.starts_on),
    } for l in lignes]


def _adresses_du_client(client):
    """Les adresses du client (Dynamic Link), prêtes pour l'écran."""
    lignes = frappe.db.sql(
        """SELECT a.name, a.address_line1, a.city, a.state, a.pincode, a.country,
                  a.custom_state_s, a.custom_villes_s, a.custom_secteur,
                  a.custom_lien_google_map
           FROM tabAddress a
           JOIN `tabDynamic Link` dl ON dl.parent = a.name
                AND dl.parenttype = 'Address'
           WHERE dl.link_doctype = 'Customer' AND dl.link_name = %(c)s
             AND COALESCE(a.disabled, 0) = 0
           ORDER BY a.modified DESC""", {"c": client}, as_dict=True)
    # UN SEUL rendez-vous en cours PAR TYPE, PAR CLIENT ET PAR ADRESSE
    # (décision 28/08) : une Installation prévue n'empêche pas un Entretien au
    # même endroit — seul un second RDV du MÊME type est refusé, il se déplace.
    # -> {adresse: {type: {tache, date}}}
    en_cours = {}
    for t in frappe.db.sql(
            """SELECT name, select_address, starts_on, custom_type_dintervention
               FROM `tabTache de travail`
               WHERE custom_client = %(c)s AND status = 'Open'
                 AND starts_on >= NOW() AND select_address IS NOT NULL
               ORDER BY starts_on""", {"c": client}, as_dict=True):
        en_cours.setdefault(t.select_address, {}).setdefault(
            t.custom_type_dintervention,
            {"tache": t.name, "date": str(t.starts_on)[:16],
             "type": t.custom_type_dintervention})
    return [{
        "adresse": l.name,
        "ligne": l.address_line1 or "",
        "gouvernorat": l.custom_state_s or l.state or "",
        "ville": l.custom_villes_s or l.city or "",
        "code_postal": l.pincode or "",
        "secteur": l.custom_secteur or "",
        "lien_maps": l.custom_lien_google_map or "",
        "rdv_en_cours": en_cours.get(l.name) or {},
    } for l in lignes]


def _gouvernorat_adresse(adresse):
    """Le gouvernorat d'une adresse, custom_state_s OU state — la moitié des
    adresses Sousse/Monastir n'ont QUE state : sans ce repli, le déplacement
    d'un RDV en zone partenaire se déclarait « hors secteur »."""
    if not adresse:
        return None
    v = frappe.db.get_value("Address", adresse, ["custom_state_s", "state"],
                            as_dict=True)
    return (v.custom_state_s or v.state) if v else None


def _adresse_du_client(client, adresse):
    """L'adresse N'EST au client que si un Dynamic Link le dit — sinon refus."""
    if not adresse or not frappe.db.exists(
            "Dynamic Link", {"parent": adresse, "parenttype": "Address",
                             "link_doctype": "Customer", "link_name": client}):
        frappe.throw(_("Choisissez une de vos adresses."))
    return frappe.get_doc("Address", adresse)


@contextmanager
def _verrou_placement():
    """Sérialise les placements du portail — le calcul du créneau et l'écriture
    de la tâche doivent être indivisibles quand plusieurs clients réservent en
    même temps. Court (quelques ms) : un seul verrou pour tout le portail
    suffit, et évite d'avoir à raisonner sur des verrous par jour ou par
    employé (le remplaçant peut changer en cours de calcul).

    ⚠️ ET UNE VUE FRAÎCHE DE LA BASE. MariaDB lit en REPEATABLE READ : la
    transaction a figé son instantané AU PREMIER SELECT, donc AVANT le verrou.
    Sans ce commit, celui qui entre en second calcule sans voir le rendez-vous
    que le premier vient de confirmer — et les deux tombent sur la même minute
    chez le même technicien (constaté en test de concurrence, 28/08).
    """
    from frappe.utils.synchronization import filelock

    with filelock("portail_rdv_placement", timeout=20):
        frappe.db.commit()
        yield


def _envoyer_sms(numero, texte):
    """SMS de service du portail (confirmation, déplacement, annulation).

    ⛔ GARDE-FOU DEV — même règle que sms_commandes._executer : la base dev
    porte les VRAIS numéros des clients et la VRAIE passerelle ; sans cette
    garde, un test de réservation en dev envoie un vrai SMS de confirmation
    (constaté le 29/08/2026). En developer_mode on SIMULE, sauf
    `sms_groupe_reel_en_dev` posé dans site_config.json.
    """
    if cint(frappe.conf.get("developer_mode")) \
            and not cint(frappe.conf.get("sms_groupe_reel_en_dev")):
        _journal_otp(numero, "SIMULÉ (dev) — %s" % texte[:100])
        return
    from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import (
        _send_sms_with_fallback,
    )
    _send_sms_with_fallback([numero], texte)


def _journal_otp(numero, evenement):
    """Trace consultable par le support (Error Log, titre « RDV OTP ») : quand
    un client dit « je ne reçois pas le code », on doit pouvoir répondre —
    numéro inconnu ? passerelle en erreur ? limite atteinte ? Le titre reste
    COURT et FIXE : log_error prend le TITRE en premier, et un titre de plus
    de 140 caractères le fait échouer lui-même (piège connu)."""
    try:
        frappe.log_error(title="RDV OTP", message="%s — %s" % (numero, evenement))
    except Exception:
        pass


def _envoyer_otp_sms(numero, texte):
    """Envoi de l'OTP en direct sur la passerelle, avec VÉRIFICATION.

    La chaîne habituelle (_send_sms_with_fallback) ne remonte JAMAIS un échec :
    son repli avale les erreurs HTTP et ne lit pas le corps de la réponse — or
    la passerelle répond 200 même quand elle refuse (crédit épuisé, numéro
    rejeté). Pour l'OTP c'est inacceptable : le client verrait « code envoyé »
    et attendrait un SMS perdu. Ici : appel direct, réponse JSON exigée, corps
    journalisé, et EXCEPTION dès que la réponse sent l'erreur — l'écran dit
    alors « réessayez » au lieu de mentir.
    """
    if cint(frappe.conf.get("developer_mode")) \
            and not cint(frappe.conf.get("sms_groupe_reel_en_dev")):
        _journal_otp(numero, "SIMULÉ (dev) — OTP non envoyé")
        return

    from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import (
        envoyer_sms_verifie,
    )
    try:
        # 2 tentatives seulement : le client attend derrière la requête HTTP —
        # au-delà, mieux vaut lui dire « réessayez » que le faire patienter.
        corps = envoyer_sms_verifie(numero, texte, tentatives=2)
        _journal_otp(numero, "passerelle → %s" % corps)
    except Exception as e:
        _journal_otp(numero, "ÉCHEC passerelle → %s" % str(e)[:200])
        raise


# Prix main d'œuvre affichés au choix du type : l'osmoseur DOMESTIQUE en
# principal (le cas courant), la liste complète dépliable. Préfixes des codes
# articles du groupe Main d'œuvre.
ARTICLES_MO = {
    "Entretien": {"principal": "M-E-OD", "prefixe": "M-E-"},
    "Réparation": {"principal": "M-R-OD", "prefixe": "M-R-"},
    "Installation": {"principal": "M-I-OD", "prefixe": "M-I-"},
}
LISTE_PRIX = "Vente standard"


def _tarifs():
    lignes = frappe.db.sql(
        """SELECT i.name, i.item_name, ip.price_list_rate
           FROM tabItem i
           JOIN `tabItem Price` ip ON ip.item_code = i.name
                AND ip.price_list = %(pl)s AND ip.selling = 1
           WHERE i.disabled = 0 AND (i.name LIKE 'M-E-%%'
                 OR i.name LIKE 'M-R-%%' OR i.name LIKE 'M-I-%%')""",
        {"pl": LISTE_PRIX}, as_dict=True)
    par_code = {l.name: l for l in lignes}
    out = {}
    for type_i, cfg in ARTICLES_MO.items():
        principal = par_code.get(cfg["principal"])
        out[type_i] = {
            "principal": frappe.utils.flt(principal.price_list_rate, 3) if principal else None,
            "liste": sorted(
                [{"nom": l.item_name, "prix": frappe.utils.flt(l.price_list_rate, 3)}
                 for l in lignes if l.name.startswith(cfg["prefixe"])],
                key=lambda x: x["prix"]),
        }
    return out


# ------------------------------------------------------------------ endpoints


@frappe.whitelist(allow_guest=True, methods=["POST"])
def envoyer_otp(telephone):
    """Étape 1 : le numéro. Réponse IDENTIQUE que le numéro soit client ou non."""
    _config()
    numero = _normaliser(telephone)
    if not numero:
        frappe.throw(_("Numéro invalide — 8 chiffres, ex. 98 000 000."))

    # MODE TEST — dev uniquement, DOUBLE verrou : la case de la config ET le
    # developer_mode du site. En production le second manque toujours : le code
    # ne peut pas fuir, même case cochée par erreur.
    mode_test = bool(cint(frappe.db.get_single_value(DOCTYPE_CONFIG, "mode_test"))) \
        and bool(cint(frappe.conf.get("developer_mode")))

    # L'anti-abus ne s'applique pas en mode test : on enchaîne les essais de
    # dev sans SMS — le limiteur n'y protège rien et bloquait les tests.
    ip = getattr(frappe.local, "request_ip", None) or "?"
    if not mode_test and (
            _compteur_depasse("rdv_envois_num:%s" % numero, ENVOIS_MAX, ENVOIS_FENETRE)
            or _compteur_depasse("rdv_envois_ip:%s" % ip, ENVOIS_MAX_IP, ENVOIS_FENETRE)):
        _journal_otp(numero, "limite d'envois atteinte (ip %s)" % ip)
        frappe.throw(_("Trop de demandes — réessayez dans quelques minutes."))

    clients = _clients_du_numero(numero)
    code_test = None
    # Depuis le 29/08 un numéro INCONNU reçoit aussi son code : après l'OTP, le
    # portail lui propose de créer sa fiche client (auto-inscription). L'OTP
    # continue de prouver la possession du numéro avant toute création.
    if not clients:
        _journal_otp(numero, "numéro inconnu — parcours d'auto-inscription")
    code = "%06d" % random.SystemRandom().randint(0, 999999)
    _cache().set_value("rdv_otp:%s" % numero,
                       {"code": code, "clients": clients},
                       expires_in_sec=OTP_TTL)
    _cache().delete_value("rdv_essais:%s" % numero)
    if mode_test:
        code_test = code
    else:
        try:
            _envoyer_otp_sms(numero, "Code Aqua World : %s (valable 5 minutes)." % code)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "portail_rdv envoi OTP")
            frappe.throw(_("Envoi du SMS impossible pour le moment — réessayez."))
    reponse = {"message": MESSAGE_GENERIQUE}
    if code_test:
        reponse["code_test"] = code_test
    return reponse


@frappe.whitelist(allow_guest=True, methods=["POST"])
def verifier_otp(telephone, code):
    """Étape 2 : le code. -> jeton de session + ce que le client peut réserver."""
    _config()
    numero = _normaliser(telephone)
    if not numero:
        frappe.throw(_("Numéro invalide."))
    if _compteur_depasse("rdv_essais:%s" % numero, ESSAIS_MAX, OTP_TTL):
        _cache().delete_value("rdv_otp:%s" % numero)
        frappe.throw(_("Trop d'essais — redemandez un code."))

    attendu = _cache().get_value("rdv_otp:%s" % numero)
    if not attendu or str(code or "").strip() != attendu.get("code"):
        frappe.throw(_("Code incorrect ou expiré."))

    _cache().delete_value("rdv_otp:%s" % numero)
    clients = attendu.get("clients") or []
    # Numéro vérifié mais inconnu de la base : parcours d'auto-inscription —
    # un jeton dédié couvre le temps de remplir le formulaire (nom, type,
    # adresse), puis inscrire_client() crée la fiche et ouvre la session.
    if not clients:
        jeton_inscription = frappe.generate_hash(length=32)
        _cache().set_value("rdv_inscription:%s" % jeton_inscription,
                           {"telephone": numero}, expires_in_sec=INSCRIPTION_TTL)
        return {"inscription": 1, "jeton_inscription": jeton_inscription}
    # UN téléphone peut porter PLUSIEURS fiches client (famille, entreprise —
    # demande 29/08) : le code prouve la possession du numéro, l'écran fait
    # ensuite choisir la fiche. Un seul compte -> session directe, comme avant.
    if len(clients) > 1:
        jeton_choix = frappe.generate_hash(length=32)
        _cache().set_value("rdv_choix:%s" % jeton_choix,
                           {"clients": clients, "telephone": numero},
                           expires_in_sec=OTP_TTL)
        return {"choix": clients, "jeton_choix": jeton_choix}
    return _ouvrir_session(numero, clients[0])


@frappe.whitelist(allow_guest=True, methods=["POST"])
def choisir_client(jeton_choix, client):
    """Étape 2 bis : le numéro porte plusieurs fiches — le client choisit la
    sienne. Seuls les comptes attachés au numéro VÉRIFIÉ sont acceptés."""
    _config()
    donnees = _cache().get_value("rdv_choix:%s" % (jeton_choix or ""))
    if not donnees:
        frappe.throw(_("Session expirée — recommencez avec votre numéro."),
                     frappe.AuthenticationError)
    entree = next((c for c in donnees["clients"] if c["client"] == client), None)
    if not entree:
        frappe.throw(_("Choisissez un des comptes proposés."))
    _cache().delete_value("rdv_choix:%s" % jeton_choix)
    return _ouvrir_session(donnees["telephone"], entree)


def _ouvrir_session(numero, entree):
    """Ouvre la session du portail pour UNE fiche client et rend tout ce que
    l'écran doit savoir (types, commandes, adresses, historique, tarifs)."""
    jeton = frappe.generate_hash(length=32)
    _cache().set_value("rdv_session:%s" % jeton,
                       {"client": entree["client"], "nom": entree["nom"],
                        "telephone": numero},
                       expires_in_sec=SESSION_TTL)

    commandes = _commandes_du_client(entree["client"])
    return {
        "jeton": jeton,
        "nom": entree["nom"],
        # L'installation s'offre dès qu'une commande est encore sans tâche ; la
        # livraison exige EN PLUS l'autorisation posée sur cette commande.
        "types": list(TYPES_TOUT_CLIENT)
                 + ([TYPE_AVEC_COMMANDE]
                    if any(c.sans_tache for c in commandes) else [])
                 + ([TYPE_LIVRAISON]
                    if any(c.sans_tache and c.livraison_equipe for c in commandes)
                    else []),
        "commandes": commandes,
        "adresses": _adresses_du_client(entree["client"]),
        "rendez_vous": _rendez_vous_du_client(entree["client"]),
        "tarifs": _tarifs(),
        # Bulle d'aide : proposée seulement si le magasin l'a activée.
        "assistant": bool(cint((frappe.db.get_singles_dict(DOCTYPE_CONFIG)
                                or {}).get("assistant_actif"))),
        "ouvertures": _ouvertures_par_type(_config()),
        "date_min": str(add_days(getdate(nowdate()), 1)),
        "date_max": str(add_days(getdate(nowdate()), HORIZON_JOURS)),
    }


def _rdv_verrouille(starts_on):
    """Le verrou ne vaut QUE pour la journée en cours (décision 29/08) : un RDV
    d'aujourd'hui dont l'heure est passée ne se touche plus (le technicien est
    peut-être en route), mais un RDV resté Open d'un jour PASSÉ — raté, non
    honoré — redevient modifiable/annulable : le client le replanifie lui-même
    au lieu de rester coincé avec un rendez-vous fantôme."""
    debut = frappe.utils.get_datetime(starts_on)
    return getdate(debut) == getdate(nowdate()) \
        and debut < frappe.utils.now_datetime()


def _rdv_deplacable(session, tache):
    """La tâche du client, encore ouverte et non verrouillée — sinon refus.
    La nouvelle date, elle, ne peut être qu'à partir de demain (contrôle dans
    deplacer_rdv)."""
    doc = frappe.get_doc(DOCTYPE_TACHE, tache)
    if doc.get("custom_client") != session["client"]:
        frappe.throw(_("Ce rendez-vous n'est pas le vôtre."))
    if doc.get("status") != "Open" or not doc.get("starts_on") \
            or _rdv_verrouille(doc.starts_on):
        frappe.throw(_("Ce rendez-vous ne peut plus être modifié en ligne — "
                       "appelez-nous."))
    return doc


def _ouverture_a_annoncer(config, contexte, type_intervention):
    """La date d'ouverture du type SI elle repousse vraiment le premier créneau
    au-delà du délai habituel — sinon rien à dire au client."""
    from customization_app import portail_rdv_planning as planning

    ouverture = planning.ouverture_type(config, type_intervention)
    if not ouverture:
        return None
    delai = (contexte or {}).get("delai_jours") or planning.delai_standard(config)
    return str(ouverture) if ouverture > add_days(getdate(nowdate()), delai) else None


def _ouvertures_par_type(config):
    """{type: date d'ouverture} — seulement les types qui ouvrent plus tard que
    le délai standard, pour que l'écran l'affiche sur le bouton du type."""
    from customization_app import portail_rdv_planning as planning

    minimum = add_days(getdate(nowdate()), planning.delai_standard(config))
    out = {}
    for type_i in TYPES_TOUT_CLIENT + TYPES_AVEC_COMMANDE:
        ouverture = planning.ouverture_type(config, type_i)
        if ouverture and ouverture > minimum:
            out[type_i] = str(ouverture)
    return out


@frappe.whitelist(allow_guest=True, methods=["POST"])
def assistant(jeton, question, historique=None):
    """L'assistant du portail — la logique vit dans `portail_rdv_assistant`.
    Ce relais garde UNE seule adresse d'API pour tout l'écran client.
    (import local : l'assistant importe ce module.)"""
    from customization_app.portail_rdv_assistant import demander

    return demander(jeton, question, historique)


@frappe.whitelist(allow_guest=True, methods=["POST"])
def disponibilites(jeton, adresse=None, type_intervention=None, tache=None):
    """La grille des demi-journées faisables — pour une NOUVELLE réservation
    (adresse + type), ou pour DÉPLACER un rendez-vous existant (tache : son
    type et son secteur sont repris, et il ne compte pas contre lui-même)."""
    from customization_app import portail_rdv_planning as planning

    config = _config()
    session = _session(jeton)
    exclure = None
    if tache:
        doc = _rdv_deplacable(session, tache)
        type_intervention = doc.get("custom_type_dintervention")
        secteur = doc.get("secteur")
        exclure = doc.name
        contexte = planning.contexte_partenaire(
            config, _gouvernorat_adresse(doc.get("select_address")))
    else:
        doc_adresse = _adresse_du_client(session["client"], adresse)
        secteur = doc_adresse.get("custom_secteur")
        contexte = planning.contexte_partenaire(
            config, doc_adresse.get("custom_state_s") or doc_adresse.get("state"))
    hors = (not secteur or secteur == planning.HORS_SECTEUR) and not contexte
    return {
        "secteur": secteur or "",
        "hors_secteur": hors,
        # Zone partenaire : le délai minimum est plus long, l'écran le dit.
        "partenaire": bool(contexte),
        "delai_jours": (contexte or {}).get("delai_jours"),
        # Les horaires affichés sur les boutons suivent la config (fenêtres
        # réglables) — plus de « 09:30 – 12:30 » en dur à l'écran.
        "horaires": planning.fenetres_libellees(),
        # Type pas encore ouvert : l'écran l'annonce au lieu de laisser une
        # grille vide sans explication.
        "ouvert_le": _ouverture_a_annoncer(config, contexte, type_intervention),
        "jours": [] if hors else planning.disponibilites(
            config, secteur, type_intervention, exclure=exclure, contexte=contexte),
    }


@frappe.whitelist(allow_guest=True, methods=["POST"])
def deplacer_rdv(jeton, tache, date, demi_journee):
    """Déplace un rendez-vous OUVERT à venir sur un nouveau créneau — mêmes
    règles que la réservation (moteur complet), l'ancien créneau se libère.
    Trace au fil de la tâche + SMS de confirmation."""
    from customization_app import portail_rdv_planning as planning

    config = _config()
    session = _session(jeton)
    doc = _rdv_deplacable(session, tache)

    contexte = planning.contexte_partenaire(
        config, _gouvernorat_adresse(doc.get("select_address")))
    # ⚠️ `jour` sert plus bas (placer, SMS, retour) : le refactor délai de
    # v5.21 avait perdu cette affectation — NameError sur TOUT déplacement.
    jour = getdate(date)
    ouvert_le = planning.premier_jour(
        config, contexte, doc.get("custom_type_dintervention"))
    if jour < ouvert_le:
        frappe.throw(_("Choisissez une date à partir du {0}.").format(
            formatdate(ouvert_le, "dd/MM/yyyy")))

    ancien = str(doc.starts_on)[:16]
    type_i = doc.get("custom_type_dintervention")
    # Sous verrou comme la réservation : deux clients peuvent viser le même
    # créneau au même instant (l'un en réservant, l'autre en déplaçant).
    with _verrou_placement():
        employe, starts_on, duree = planning.placer(
            config, jour, demi_journee, doc.get("secteur"), type_i,
            exclure=doc.name, contexte=contexte)

        import datetime as _dt
        nom_employe = frappe.db.get_value("Employee", employe, "employee_name") or employe
        icones = {"Entretien": "🔧 ", "Installation": "🔨 ", "Réparation": "🧰 "}
        doc.custom_choix_du_staff = employe
        doc.starts_on = starts_on
        doc.ends_on = starts_on + _dt.timedelta(minutes=duree)
        doc.temps = planning.TEMPS_LIBELLE.get(type_i) or doc.temps
        doc.titre = "%s\n%s%s: Client: %s\n%s" % (
            doc.get("secteur") or "", icones.get(type_i, "☕ "), type_i,
            session["client"], nom_employe)
        setattr(doc, "custom_employé", nom_employe)
        doc.flags.ignore_permissions = True
        doc.save()
        frappe.db.commit()

    libelle_demi = _("matin") if demi_journee == "matin" else _("après-midi")
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info",
        "reference_doctype": DOCTYPE_TACHE, "reference_name": doc.name,
        "content": _("📲 Rendez-vous déplacé par le client via le portail : "
                     "du {0} au {1} ({2}).").format(ancien, str(starts_on)[:16],
                                                    libelle_demi),
    }).insert(ignore_permissions=True)
    frappe.db.commit()

    try:
        _envoyer_sms(session["telephone"],
                     "Aqua World : votre rendez-vous %s est déplacé au %s (%s). "
                     "Nous vous confirmerons l'heure exacte." % (
                         type_i, jour.strftime("%d/%m/%Y"), libelle_demi))
    except Exception:
        frappe.log_error(frappe.get_traceback(), "portail_rdv SMS déplacement")

    return {"tache": doc.name, "date": str(jour), "demi_journee": libelle_demi,
            "type": type_i}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def annuler_rdv(jeton, tache):
    """Annule (SUPPRIME) un rendez-vous encore ouvert et pas commencé.

    La tâche est retirée du calendrier — le créneau se libère pour tout le
    monde. La trace ne peut pas rester sur un document supprimé : elle est
    posée sur la FICHE CLIENT, où le magasin la retrouve.
    """
    _config()
    session = _session(jeton)
    doc = _rdv_deplacable(session, tache)

    resume = "%s du %s" % (doc.get("custom_type_dintervention") or _("Rendez-vous"),
                           str(doc.starts_on)[:16])
    employe = doc.get("custom_employé") or doc.get("custom_choix_du_staff") or ""
    frappe.delete_doc(DOCTYPE_TACHE, doc.name, ignore_permissions=True,
                      delete_permanently=True)
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info",
        "reference_doctype": "Customer", "reference_name": session["client"],
        "content": _("📲 Rendez-vous ANNULÉ par le client via le portail : "
                     "{0} ({1}, {2}) — la tâche a été supprimée du calendrier.")
                   .format(resume, employe, tache),
    }).insert(ignore_permissions=True)
    frappe.db.commit()

    try:
        _envoyer_sms(session["telephone"],
                     "Aqua World : votre rendez-vous %s est bien annulé. "
                     "Vous pouvez en reprendre un quand vous le souhaitez." % resume)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "portail_rdv SMS annulation")

    return {"annule": tache,
            "rendez_vous": _rendez_vous_du_client(session["client"])}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def referentiel_adresses(jeton):
    """Gouvernorats et villes de la sectorisation — pour le formulaire d'adresse.
    Derrière le jeton : le référentiel ne sort pas pour les anonymes. Accepte le
    jeton de SESSION ou celui d'INSCRIPTION (le nouveau client saisit son
    adresse avant d'avoir une session)."""
    if not _cache().get_value("rdv_inscription:%s" % (jeton or "")):
        _session(jeton)
    from customization_app.sectorisation import VILLES_PAR_GOUVERNORAT
    return VILLES_PAR_GOUVERNORAT


@frappe.whitelist(allow_guest=True, methods=["POST"])
def inscrire_client(jeton_inscription, nom, type_client, matricule_fiscale=None,
                    email=None, ligne=None, gouvernorat=None, ville=None,
                    code_postal=None, lien_maps=None):
    """Auto-inscription (29/08) : le numéro est VÉRIFIÉ par OTP mais inconnu —
    le client crée sa fiche lui-même, façon quick entry : nom, type
    (Individuel / Société — matricule fiscale obligatoire pour une société) et
    son adresse (pays : Tunisie, sectorisation imposée comme partout). La fiche
    est créée directement puis la session s'ouvre."""
    from customization_app.sectorisation import secteur_de

    _config()
    donnees = _cache().get_value("rdv_inscription:%s" % (jeton_inscription or ""))
    if not donnees:
        frappe.throw(_("Session expirée — recommencez avec votre numéro."),
                     frappe.AuthenticationError)
    numero = donnees["telephone"]

    nom = (nom or "").strip()
    if len(nom) < 3:
        frappe.throw(_("Écrivez votre nom complet (ou la raison sociale)."))
    types = {"Individuel": "Individual", "Société": "Company"}
    if type_client not in types:
        frappe.throw(_("Choisissez le type : Individuel ou Société."))
    matricule = (matricule_fiscale or "").strip()
    if type_client == "Société" and not matricule:
        frappe.throw(_("Le matricule fiscal est obligatoire pour une société."))
    from frappe.utils import validate_email_address
    email = (email or "").strip()
    if not email or not validate_email_address(email):
        frappe.throw(_("Écrivez une adresse e-mail valide."))
    ligne = (ligne or "").strip()
    if not ligne:
        frappe.throw(_("Écrivez l'adresse (rue, résidence…)."))
    secteur = secteur_de(gouvernorat, ville)
    if not secteur:
        frappe.throw(_("Choisissez un gouvernorat et une ville de la liste."))
    if _clients_du_numero(numero):
        # créé entre-temps (double clic, second onglet) : on ne duplique pas
        frappe.throw(_("Ce numéro est déjà associé à un compte — reconnectez-vous."))

    client = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": nom,
        "customer_type": types[type_client],
        # groupe FEUILLE obligatoire — « Individuel » porte l'essentiel des
        # fiches (même les sociétés) ; territoire aligné sur l'existant
        "customer_group": (
            frappe.db.exists("Customer Group", {"name": "Individuel", "is_group": 0})
            and "Individuel"
            or frappe.db.get_value("Customer Group", {"is_group": 0}, "name")),
        "territory": (frappe.db.exists("Territory", "Tunisia") and "Tunisia"
                      or frappe.db.get_single_value("Selling Settings", "territory")),
        "mobile_no": numero,
        # email_id + mobile_no → ERPNext crée le Contact principal (create_primary_contact)
        "email_id": email,
        "tax_id": matricule or None,
    })
    client.flags.ignore_permissions = True
    client.flags.ignore_mandatory = True
    client.insert()

    adresse = frappe.get_doc({
        "doctype": "Address",
        "address_title": "%s-%s" % (nom, ville),
        "address_type": "Shipping",
        "address_line1": ligne,
        "city": ville,
        "state": gouvernorat,
        "country": "Tunisia",
        "pincode": (code_postal or "").strip() or None,
        "custom_state_s": gouvernorat,
        "custom_villes_s": ville,
        "custom_secteur": secteur,
        "custom_lien_google_map": (lien_maps or "").strip() or None,
        "phone": numero,
        "custom_reservation_app": 1,
        "links": [{"link_doctype": "Customer", "link_name": client.name}],
    })
    adresse.flags.ignore_permissions = True
    adresse.insert()
    frappe.db.commit()

    _cache().delete_value("rdv_inscription:%s" % jeton_inscription)
    _journal_otp(numero, "auto-inscription : client %s (%s) créé" % (client.name, type_client))
    return _ouvrir_session(numero, {"client": client.name,
                                    "nom": client.customer_name or client.name})


@frappe.whitelist(allow_guest=True, methods=["POST"])
def enregistrer_adresse(jeton, ligne, gouvernorat, ville, code_postal=None,
                        lien_maps=None, adresse=None):
    """Crée ou modifie UNE adresse du client — même sectorisation que le quick
    entry client : gouvernorat → ville → secteur imposé par la table, jamais
    choisi librement. `adresse` = nom d'une adresse existante pour la modifier
    (l'appartenance est revérifiée). Marquée custom_reservation_app."""
    from customization_app.sectorisation import secteur_de

    session = _session(jeton)
    ligne = (ligne or "").strip()
    if not ligne:
        frappe.throw(_("Écrivez l'adresse (rue, résidence…)."))
    secteur = secteur_de(gouvernorat, ville)
    if not secteur:
        frappe.throw(_("Choisissez un gouvernorat et une ville de la liste."))

    if adresse:
        doc = _adresse_du_client(session["client"], adresse)
    else:
        doc = frappe.get_doc({
            "doctype": "Address",
            "address_title": "%s-%s" % (session["nom"], ville),
            "address_type": "Shipping",
            "links": [{"link_doctype": "Customer", "link_name": session["client"]}],
        })

    doc.update({
        "address_line1": ligne,
        "city": ville,
        "state": gouvernorat,
        "country": "Tunisia",
        "pincode": (code_postal or "").strip() or None,
        "custom_state_s": gouvernorat,
        "custom_villes_s": ville,
        "custom_secteur": secteur,
        "custom_lien_google_map": (lien_maps or "").strip() or None,
        "phone": session["telephone"],
        "custom_reservation_app": 1,
    })
    doc.flags.ignore_permissions = True
    doc.save()
    frappe.db.commit()
    return {"adresse": doc.name, "secteur": secteur,
            "adresses": _adresses_du_client(session["client"])}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def commande_details(jeton, commande):
    """Le détail d'UNE commande du client connecté : articles, images, prix.

    L'appartenance est revérifiée — le jeton d'un client n'ouvre jamais la
    commande d'un autre. Seules les images PUBLIQUES (/files/) sont renvoyées :
    un fichier privé ne s'afficherait pas chez un invité, autant ne pas fuiter
    son chemin.
    """
    session = _session(jeton)
    if not frappe.db.exists("Sales Order",
                            {"name": commande, "customer": session["client"]}):
        frappe.throw(_("Cette commande n'est pas la vôtre."))

    lignes = frappe.db.sql(
        """SELECT soi.item_name, soi.qty, soi.rate, soi.amount,
                  COALESCE(NULLIF(soi.image, ''), i.image) AS image
           FROM `tabSales Order Item` soi
           LEFT JOIN tabItem i ON i.name = soi.item_code
           WHERE soi.parent = %(c)s
           ORDER BY soi.idx""", {"c": commande}, as_dict=True)
    return {
        "articles": [{
            "article": l.item_name,
            "qte": frappe.utils.flt(l.qty, 2),
            "prix": frappe.utils.flt(l.rate, 3),
            "montant": frappe.utils.flt(l.amount, 3),
            "image": l.image if (l.image or "").startswith("/files/") else None,
        } for l in lignes],
        "total": frappe.utils.flt(
            frappe.db.get_value("Sales Order", commande, "grand_total"), 3),
    }


@frappe.whitelist(allow_guest=True, methods=["POST"])
def reserver(jeton, type_intervention, date, demi_journee, adresse=None,
             commande=None, note=None, contact_nom=None, contact_tel=None,
             confirmer_second=0):
    """Étape 3 : la réservation. Crée la tâche FERME au calendrier — SUR une
    adresse choisie du client (décision 27/08 : l'adresse d'abord, la
    sectorisation suit sur la tâche)."""
    config = _config()
    session = _session(jeton)
    doc_adresse = _adresse_du_client(session["client"], adresse)

    # Un second rendez-vous du MÊME type à la même adresse ne passe qu'avec une
    # CONFIRMATION EXPLICITE (décision 29/08, assouplit le refus du 28/08) :
    # l'écran demande d'abord « modifier l'existant, ou en prendre un autre ? »
    # et n'envoie confirmer_second=1 que si le client a choisi le second.
    deja = frappe.db.sql(
        """SELECT name, starts_on FROM `tabTache de travail`
           WHERE custom_client = %(c)s AND select_address = %(a)s
             AND custom_type_dintervention = %(t)s
             AND status = 'Open' AND starts_on >= NOW()
           ORDER BY starts_on LIMIT 1""",
        {"c": session["client"], "a": doc_adresse.name, "t": type_intervention},
        as_dict=True)
    if deja and not cint(confirmer_second):
        frappe.throw(_("Vous avez déjà un rendez-vous {0} prévu à cette adresse "
                       "(le {1}) — vous pouvez le déplacer depuis « Mes RDV », "
                       "ou confirmer que vous en voulez un autre en plus.")
                     .format(type_intervention, str(deja[0].starts_on)[:16]))

    if type_intervention not in TYPES_TOUT_CLIENT + TYPES_AVEC_COMMANDE:
        frappe.throw(_("Type de rendez-vous inconnu."))
    if demi_journee not in ("matin", "apres_midi"):
        frappe.throw(_("Choisissez matin ou après-midi."))
    from customization_app import portail_rdv_planning as planning
    contexte = planning.contexte_partenaire(
        config, doc_adresse.get("custom_state_s") or doc_adresse.get("state"))
    delai = (contexte or {}).get("delai_jours") or planning.delai_standard(config)
    jour = getdate(date)
    ouvert_le = planning.premier_jour(config, contexte, type_intervention)
    if jour < ouvert_le or jour > add_days(getdate(nowdate()), HORIZON_JOURS):
        frappe.throw(_("Les rendez-vous « {0} » sont acceptés à partir du {1}, "
                       "et jusqu'à deux mois.").format(
            type_intervention, formatdate(ouvert_le, "dd/MM/yyyy")))

    if type_intervention in TYPES_AVEC_COMMANDE:
        if not commande:
            frappe.throw(_("Choisissez la commande concernée par {0}.").format(
                _("la livraison") if type_intervention == TYPE_LIVRAISON
                else _("l'installation")))
        if not frappe.db.exists("Sales Order",
                                {"name": commande, "customer": session["client"]}):
            frappe.throw(_("Cette commande n'est pas la vôtre."))
        if frappe.db.exists("Tache de travail",
                            {"commande_client": commande,
                             "status": ["!=", "Cancelled"]}):
            frappe.throw(_("Cette commande a déjà une intervention planifiée — "
                           "choisissez-en une autre."))
        # L'autorisation se vérifie AU MOMENT DE RÉSERVER, pas seulement à
        # l'affichage : le type est envoyé par le navigateur, il ne fait pas foi.
        if type_intervention == TYPE_LIVRAISON and not frappe.db.get_value(
                "Sales Order", commande, "custom_livraison_equipe"):
            frappe.throw(_("Cette commande n'est pas livrée par notre équipe — "
                           "appelez-nous pour organiser la livraison."))

    # Le moteur applique TOUTES les règles (secteur de l'adresse, capacité avec
    # battements, journées 8/9, quota lointain, remplaçants, dimanche) et rend
    # l'employé + l'heure de début empilée.
    #
    # ⚠️ SOUS VERROU. Plusieurs clients réservent en même temps : entre le
    # calcul du créneau et l'insertion de la tâche, un autre pourrait prendre
    # la même place — deux techniciens promis à la même minute. Le verrou
    # sérialise les placements du portail ; ils durent quelques millisecondes.
    with _verrou_placement():
        employe, starts_on, duree = planning.placer(
            config, jour, demi_journee,
            doc_adresse.get("custom_secteur"), type_intervention, contexte=contexte)

        libelle_demi = _("matin") if demi_journee == "matin" else _("après-midi")
        # Le TITRE du calendrier est composé côté FICHE par le Client Script
        # (update_title_and_color) — une tâche insérée par API resterait « null »
        # au calendrier. On le compose donc ici, même gabarit que la fiche.
        nom_employe = frappe.db.get_value("Employee", employe, "employee_name") or employe
        icones = {"Entretien": "🔧 ", "Installation": "🔨 ", "Réparation": "🧰 "}
        secteur_tache = doc_adresse.get("custom_secteur") or ""
        titre = "%s\n%s%s: Client: %s\n%s" % (
            secteur_tache, icones.get(type_intervention, "☕ "), type_intervention,
            session["client"], nom_employe)
        tache = frappe.get_doc({
            "titre": titre,
            "custom_employé": nom_employe,
            "doctype": DOCTYPE_TACHE,
            "custom_type_dintervention": type_intervention,
            "custom_choix_du_staff": employe,
            "starts_on": starts_on,
            "status": "Open",
            "custom_client": session["client"],
            "nom_client": session["nom"],
            "tel": session["telephone"],
            "custom_reservation_app": 1,
            "commande_client": commande or None,
            "afficher_commande": 1 if commande else 0,
            # L'adresse choisie irrigue la tâche : sélection, texte, secteur et
            # lien Maps — le calendrier et la tournée s'en servent tels quels.
            # Qui demander sur place — souvent quelqu'un d'autre que le titulaire
            # du compte (gardien, conjoint, responsable de site).
            "custom_contact_arrivee": " · ".join(filter(None, [
                (contact_nom or "").strip()[:80],
                _normaliser(contact_tel) or (contact_tel or "").strip()[:20]])) or None,
            "select_address": doc_adresse.name,
            "details_adresse": ", ".join(filter(None, [
                doc_adresse.address_line1, doc_adresse.city, doc_adresse.state])),
            "secteur": doc_adresse.get("custom_secteur"),
            "google_map": doc_adresse.get("custom_lien_google_map"),
            # La demi-journée choisie DOIT se lire sur la tâche : l'heure posée
            # n'est qu'un point d'ancrage au calendrier, pas une promesse.
            "subject": _("RDV portail — {0} ({1}){2}").format(
                type_intervention, libelle_demi,
                (" — " + str(note).strip()[:200]) if note and str(note).strip() else ""),
        })
        tache.temps = planning.TEMPS_LIBELLE.get(type_intervention)
        tache.flags.ignore_permissions = True
        tache.insert()
        # Le hook de création fixe ends_on par SA table de durées (Réparation 120') —
        # le portail impose LES SIENNES (décision 27/08 : Réparation 60').
        import datetime as _dt
        tache.db_set("ends_on", starts_on + _dt.timedelta(minutes=duree),
                     update_modified=False)
        frappe.db.commit()

    try:
        _envoyer_sms(session["telephone"],
                     "Aqua World : votre rendez-vous %s du %s (%s) est enregistré. "
                     "Nous vous confirmerons l'heure exacte." % (
                         type_intervention, jour.strftime("%d/%m/%Y"), libelle_demi))
    except Exception:
        frappe.log_error(frappe.get_traceback(), "portail_rdv SMS confirmation")

    return {"tache": tache.name, "date": str(jour), "demi_journee": libelle_demi,
            "type": type_intervention}
