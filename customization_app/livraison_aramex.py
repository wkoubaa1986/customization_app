"""Suivi des livraisons Aramex, depuis les paiements qui les portent.

D'OU VIENT LA LISTE, ET POURQUOI DE LA
---------------------------------------
Quand une commande part chez Aramex en contre-remboursement, la piece est soldee contre le compte
« Livraison Aramex - A&S » : l'argent n'est pas encaisse, il est chez le transporteur. Ce compte
est donc, sans qu'on l'ait voulu, la liste exacte des colis en circulation ET de ce qu'Aramex nous
doit. C'est cette liste que l'ecran affiche — pas une saisie parallele qu'il faudrait tenir a jour.

LA REFERENCE EST DANS LE LIBELLE DU PAIEMENT
---------------------------------------------
`reference_no` porte « Aramex N: 51330112234 ». Le numero de bordereau s'en extrait, et c'est lui
qu'on presente au service de suivi. Certains paiements portent « Aramex N: 000 » — un colis parti
sans bordereau saisi : on le SIGNALE au lieu d'interroger le service pour rien (il repond 400).

⚠️ LE SUIVI COUTE 1,9 SECONDE PAR COLIS, ET IL EST RANGE, PAS MIS EN CACHE
---------------------------------------------------------------------------
Interroger 35 colis a chaque ouverture de page ferait attendre plus d'une minute : le suivi est donc
garde et rafraichi a la demande. Il l'a d'abord ete dans le cache Redis — il a disparu au premier
`clear_cache`, celui que declenche n'importe quel enregistrement de workspace et chaque
deploiement. Un ecran de suivi qui repart vide apres chaque mise en production ne sert a rien : le
dernier etat connu vit donc dans le doctype « Suivi Aramex ».
Un colis LIVRE n'est jamais re-interroge : son etat est definitif.
"""

import re

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, now_datetime, nowdate

# Le compte qui porte les colis en circulation. Un seul aujourd'hui, verifie sur les 35 ecritures.
COMPTE_ARAMEX = "Livraison Aramex - A&S"
ROUTE_SUIVI = "/aramex/suivi/%s"
PRECISION = 3

# Le suivi est range dans un document, pas dans un cache : voir le doctype « Suivi Aramex ».
DOCTYPE_SUIVI = "Suivi Aramex"

# Le service impose 8 a 20 chiffres : la meme regle ici evite un aller-retour pour un 400.
_RE_REFERENCE = re.compile(r"(\d{8,20})")

# Ce qui, dans la derniere mise a jour, appelle une action humaine plutot qu'une attente.
_MOTS_ALERTE = ("tentative", "report", "retour", "refus", "annul", "incident", "absent",
                "injoignable", "non disponible")

LIMITE_RAFRAICHISSEMENT = 25

# Au-dela, la tache quotidienne cesse d'interroger un bordereau muet : 21 secondes par appel sans
# reponse, tous les jours, pour une reference qu'Aramex ne connaitra jamais. Le bouton « Interroger »
# passe outre : c'est un geste humain, il a le droit de reessayer.
TENTATIVES_MAX = 3

STATUT_INTROUVABLE = "Introuvable chez Aramex"
STATUT_INDISPONIBLE = "Suivi indisponible"

DOCTYPE_CONFIG = "Config Livraison Aramex"
# ⚠️ LE LIBELLE NE PREJUGE PAS DU STATUT. Premiere version : « est en cours d'acheminement :
# {statut} ». Deux colis reels sont revenus avec le statut « Returned » — le message aurait annonce
# un acheminement a des clients dont le colis revenait vers nous. Le modele se contente donc de
# rapporter ce que dit Aramex, quel que soit ce qu'il dit.
MODELE_SMS_DEFAUT = ("Bonjour {client}, suivi de votre colis {reference} (montant {montant} TND) "
                     "chez Aramex : {statut}. AquaWorld & Servicing.")

# ⚠️ « CREE » N'EST PAS UNE NOUVELLE POUR LE CLIENT. C'est l'etape 1 : le bordereau existe, le
# colis est encore chez nous et Aramex ne l'a pas ramasse. Prevenir a ce stade, c'est annoncer un
# depart qui n'a pas eu lieu — et le client appelle le lendemain. Le SMS part a partir de la
# deuxieme etape (« Collectee »), donc quand le colis a reellement bouge.
ETAPE_MINIMALE_SMS = 2


def reference_aramex(reference_no):
    """« Aramex N: 51330112234 » -> « 51330112234 ». None si aucun bordereau lisible.

    Fonction pure : c'est elle qu'on teste. « Aramex N: 000 » rend None — trois zeros ne sont pas
    un bordereau, et le service le refuserait.
    """
    m = _RE_REFERENCE.search(reference_no or "")
    return m.group(1) if m else None


def alerte(suivi):
    """La derniere mise a jour demande-t-elle une action ? -> texte ou None.

    Un colis « en transit » se laisse attendre. Un colis dont le client etait absent, qui a ete
    refuse ou qui repart en retour ne se resoudra pas tout seul : c'est le seul cas ou ce tableau
    doit reclamer quelqu'un.
    """
    if not suivi or suivi.get("livre"):
        return None
    if suivi.get("erreur") or suivi.get("statut") == STATUT_INTROUVABLE:
        # Aramex ne connait pas le bordereau saisi chez nous : le client attend un colis dont le
        # transporteur n'a pas trace. C'est l'alerte la plus serieuse de cet ecran.
        return suivi.get("erreur") or STATUT_INTROUVABLE
    description = ((suivi.get("derniere_maj") or {}).get("description") or "").lower()
    return ((suivi.get("derniere_maj") or {}).get("description")
            if any(mot in description for mot in _MOTS_ALERTE) else None)


# ------------------------------------------------------------------ service


def _client_service():
    """L'acces au service de suivi vit dans bank_retenue_sync, qui detient l'URL, la cle et le
    journal des appels. On l'emprunte plutot que de recopier des secrets ici."""
    try:
        from bank_retenue_sync.bank.movements import _base_url, _headers

        return _base_url, _headers
    except Exception:
        frappe.throw(_("L'app bank_retenue_sync est requise pour interroger le suivi Aramex."))


def _lire_suivi(reference):
    """Le dernier etat connu du colis. -> dict ou None.

    Un echec est un etat, pas une absence : le rendre permet a l'ecran de distinguer « Aramex ne
    connait pas ce bordereau » — qui appelle une correction — de « on n'a jamais demande ».
    """
    doc = frappe.db.get_value(DOCTYPE_SUIVI, reference,
                              ["payload", "statut", "derniere_erreur", "tentatives"], as_dict=1)
    if not doc:
        return None
    if doc.payload:
        try:
            return frappe.parse_json(doc.payload)
        except Exception:
            pass
    if doc.derniere_erreur:
        return {"erreur": doc.derniere_erreur, "statut": doc.statut, "livre": False,
                "tentatives": doc.tentatives or 0, "reference": reference}
    return None


def _ranger_suivi(reference, suivi):
    """Range le suivi d'un colis. Un document par bordereau, mis a jour en place.

    Les champs deplies (statut, livre, derniere mise a jour) servent aux listes et aux rapports ;
    `payload` garde la reponse entiere, y compris les etapes que l'ecran dessine.

    ⚠️ LES ECHECS SE RANGENT AUSSI, ET C'EST LE PLUS UTILE. Huit colis rendent « 404 — aucune
    expedition pour cette reference » : Aramex ne connait pas le bordereau saisi chez nous. Ce
    n'est pas une panne, c'est une anomalie a corriger — bordereau mal saisi, ou expedition jamais
    creee — et le client, lui, attend. Chaque appel sans reponse coutant 21 secondes, on compte les
    tentatives pour cesser d'insister (`TENTATIVES_MAX`) tout en gardant le constat a l'ecran.
    """
    if (suivi or {}).get("erreur"):
        return _ranger_echec(reference, suivi["erreur"])
    maj = (suivi or {}).get("derniere_maj") or {}
    valeurs = {
        "statut": (suivi or {}).get("statut"),
        "livre": 1 if (suivi or {}).get("livre") else 0,
        "etapes_franchies": (suivi or {}).get("etapes_franchies") or 0,
        "etapes_total": (suivi or {}).get("etapes_total") or 0,
        "derniere_description": maj.get("description"),
        "derniere_date": maj.get("date"),
        "destination": ((suivi or {}).get("destination") or {}).get("ville"),
        "url": (suivi or {}).get("url"),
        "consulte_le": now_datetime(),
        "tentatives": 0,                 # une reponse efface l'ardoise
        "derniere_erreur": None,
        "payload": frappe.as_json(suivi),
    }
    if frappe.db.exists(DOCTYPE_SUIVI, reference):
        doc = frappe.get_doc(DOCTYPE_SUIVI, reference)
        doc.update(valeurs)
        doc.save(ignore_permissions=True)
    else:
        frappe.get_doc(dict(doctype=DOCTYPE_SUIVI, reference=reference,
                            **valeurs)).insert(ignore_permissions=True)


def _ranger_echec(reference, erreur):
    """Garde le constat d'echec et compte les tentatives."""
    valeurs = {"statut": STATUT_INTROUVABLE if "404" in (erreur or "") else STATUT_INDISPONIBLE,
               "livre": 0, "derniere_erreur": erreur, "consulte_le": now_datetime()}
    if frappe.db.exists(DOCTYPE_SUIVI, reference):
        doc = frappe.get_doc(DOCTYPE_SUIVI, reference)
        doc.update(valeurs)
        doc.tentatives = (doc.tentatives or 0) + 1
        doc.save(ignore_permissions=True)
    else:
        frappe.get_doc(dict(doctype=DOCTYPE_SUIVI, reference=reference, tentatives=1,
                            **valeurs)).insert(ignore_permissions=True)


def _tentatives(reference):
    return frappe.db.get_value(DOCTYPE_SUIVI, reference, "tentatives") or 0


def interroger(reference, timeout=60):
    """Demande le suivi d'UN bordereau au service. -> dict, jamais d'exception.

    Une erreur de transport n'est pas une absence de colis : elle est rendue telle quelle pour que
    l'ecran distingue « je ne sais pas » de « rien a signaler ».
    """
    import requests

    _base_url, _headers = _client_service()
    try:
        r = requests.get(_base_url() + ROUTE_SUIVI % reference, headers=_headers(), timeout=timeout)
        if r.status_code == 200:
            return r.json()
        detail = ""
        try:
            detail = (r.json() or {}).get("detail") or ""
        except Exception:
            detail = (r.text or "")[:160]
        return {"erreur": "%s — %s" % (r.status_code, detail), "reference": reference}
    except Exception as e:
        return {"erreur": str(e)[:160], "reference": reference}


def _lecture():
    """⚠️ LE DROIT SE LIT SUR LA COMMANDE, PAS SUR LE PAIEMENT.

    Un chargé de vente ne lit pas les Payment Entry — seuls « Accounts User » et « Accounts
    Manager » le peuvent. Exiger ce droit ici fermait l'écran à ceux dont c'est justement le
    travail : suivre les colis de leurs propres commandes. Le droit demandé est donc celui de la
    COMMANDE, qui est le sujet de la page.

    L'ecriture de paiement reste lue en SQL pour construire la liste — c'est assume : le montant
    affiche est la contre-valeur du colis, l'argent de la commande du vendeur, pas une donnee
    comptable qui lui serait etrangere. Le LIEN vers l'ecriture, lui, n'est propose qu'a ceux qui
    peuvent l'ouvrir (`peut_voir_paiement`), pour ne pas offrir une porte qui se refermera.
    """
    if not frappe.has_permission("Sales Order", "read"):
        frappe.throw(_("Accès non autorisé"), frappe.PermissionError)


def peut_voir_paiement():
    return bool(frappe.has_permission("Payment Entry", "read"))


# ------------------------------------------------------------------ donnees


def _paiements(depuis, jusqu_a):
    return frappe.db.sql(
        """SELECT pe.name, pe.posting_date, pe.party, pe.paid_amount, pe.reference_no,
                  pe.mode_of_payment, per.reference_doctype, per.reference_name
           FROM `tabPayment Entry` pe
           LEFT JOIN `tabPayment Entry Reference` per
                  ON per.parent = pe.name AND per.allocated_amount != 0
           WHERE pe.docstatus = 1 AND pe.paid_to = %(compte)s
             AND pe.posting_date BETWEEN %(depuis)s AND %(jusqu_a)s
           -- Du plus ancien au plus recent : un colis parti il y a trois semaines passe avant
           -- celui d'hier, c'est lui qui risque de s'etre perdu.
           ORDER BY pe.posting_date ASC, pe.name""",
        {"compte": COMPTE_ARAMEX, "depuis": depuis, "jusqu_a": jusqu_a}, as_dict=True)


def _pieces(lignes):
    """Contact et adresse, lus sur la piece que le colis transporte — commande ou facture.

    La commande web porte deja le telephone, l'email et l'adresse de livraison saisis par le client
    lui-meme : ce sont les bons, pas ceux de la fiche client qui peut dater.
    """
    par_doctype = {}
    for l in lignes:
        if l.get("reference_doctype") and l.get("reference_name"):
            par_doctype.setdefault(l["reference_doctype"], set()).add(l["reference_name"])
    out = {}
    champs = {
        "Sales Order": ["name", "customer_name", "contact_mobile", "contact_email",
                        "shipping_address", "address_display", "status", "grand_total",
                        "transaction_date"],
        "Sales Invoice": ["name", "customer_name", "contact_mobile", "contact_email",
                          "shipping_address", "address_display", "status", "grand_total",
                          "posting_date"],
    }
    for doctype, noms in par_doctype.items():
        if doctype not in champs:
            continue
        for r in frappe.get_all(doctype, filters={"name": ("in", list(noms))},
                                fields=champs[doctype]):
            out[(doctype, r.name)] = r
    return out


def _texte(html):
    """L'adresse de livraison est stockee en HTML : on la rend lisible sans balises."""
    return re.sub(r"\s*\n\s*", "\n", re.sub(r"<br\s*/?>", "\n", html or "")).strip()


@frappe.whitelist()
def get_data(from_date=None, to_date=None):
    """Les colis, leur destinataire et leur dernier suivi CONNU. Aucun appel au service ici.

    L'ecran doit s'ouvrir tout de suite ; le suivi frais se demande ensuite, explicitement.
    """
    _lecture()

    # L'annee civile entiere, du 1er janvier au 31 decembre : un suivi de livraison se lit sur
    # l'exercice, pas sur une fenetre glissante ou un colis de janvier disparaitrait en avril.
    annee = getdate(nowdate()).year
    depuis = getdate(from_date) if from_date else getdate("%s-01-01" % annee)
    jusqu_a = getdate(to_date) if to_date else getdate("%s-12-31" % annee)

    lignes = _paiements(depuis, jusqu_a)
    pieces = _pieces(lignes)
    from customization_app.retenue_source import _coordonnees_des_contacts

    coord = _coordonnees_des_contacts(list({l.party for l in lignes if l.party}))

    colis = []
    for l in lignes:
        piece = pieces.get((l.reference_doctype, l.reference_name))
        reference = reference_aramex(l.reference_no)
        suivi = _lire_suivi(reference) if reference else None
        contact = coord.get(l.party, {})
        colis.append({
            "payment_entry": l.name,
            "posting_date": str(l.posting_date),
            "montant": flt(l.paid_amount, PRECISION),
            "reference": reference,
            "reference_brute": l.reference_no,
            "piece_doctype": l.reference_doctype,
            "piece": l.reference_name,
            "piece_statut": (piece.status if piece else None),
            "customer": l.party,
            "customer_name": (piece.customer_name if piece else None) or l.party,
            "telephone": (piece.contact_mobile if piece else None)
                         or ", ".join(contact.get("telephones", [])),
            "email": (piece.contact_email if piece else None)
                     or ", ".join(contact.get("emails", [])),
            # ⚠️ A DEFAUT D'ADRESSE DE LIVRAISON, CELLE DE FACTURATION. Quatre colis sur
            # trente-trois affichaient « adresse non renseignee » alors que le client en avait
            # une : la piece ne portait pas de `shipping_address_name`, mais bien une adresse.
            # On le DIT (`adresse_de_facturation`) : livrer a l'adresse de facturation est un
            # choix, pas une evidence.
            "adresse": _texte((piece.shipping_address if piece else None)
                              or (piece.address_display if piece else None)),
            "adresse_de_facturation": bool(piece and not piece.shipping_address
                                           and piece.address_display),
            "suivi": suivi,
            "alerte": alerte(suivi),
        })

    return {"periode": {"from": str(depuis), "to": str(jusqu_a)},
            "compte": COMPTE_ARAMEX, "colis": colis, "kpis": kpis(colis),
            "peut_voir_paiement": peut_voir_paiement(), "sms_auto": sms_actif()}


def kpis(colis):
    """Les chiffres de l'ecran. `chez_aramex` est le seul qui parle d'argent : c'est ce que le
    transporteur detient pour nous, et il ne figure nulle part ailleurs."""
    def somme(seq):
        return round(sum(flt(c["montant"], PRECISION) for c in seq), PRECISION)

    livres = [c for c in colis if (c["suivi"] or {}).get("livre")]
    en_route = [c for c in colis if c["suivi"] and not (c["suivi"] or {}).get("livre")
                and not (c["suivi"] or {}).get("erreur")]
    alertes = [c for c in colis if c["alerte"]]
    sans_reference = [c for c in colis if not c["reference"]]
    inconnus = [c for c in colis if c["reference"] and not c["suivi"]]
    return {"total": len(colis), "montant_total": somme(colis),
            "livres": len(livres), "montant_livres": somme(livres),
            "en_route": len(en_route), "montant_en_route": somme(en_route),
            "alertes": len(alertes), "montant_alertes": somme(alertes),
            "sans_reference": len(sans_reference),
            "suivi_inconnu": len(inconnus),
            "chez_aramex": somme([c for c in colis if not (c["suivi"] or {}).get("livre")])}


@frappe.whitelist()
def rafraichir(references=None, limite=None, tout=0):
    """Interroge le service pour les colis dont le suivi manque ou a expire. -> {interroges, ...}.

    Sans `tout`, les colis DEJA LIVRES sont sautes : leur suivi ne changera plus, et chaque appel
    epargne coute 1,9 seconde a l'utilisateur qui attend devant son ecran.
    """
    _lecture()
    if isinstance(references, str):
        references = frappe.parse_json(references)
    references = [r for r in (references or []) if r]
    limite = frappe.utils.cint(limite) or LIMITE_RAFRAICHISSEMENT

    out = {"interroges": 0, "erreurs": 0, "ignores": 0, "suivis": {}}
    for reference in references:
        if out["interroges"] >= limite:
            out["ignores"] += 1
            continue
        connu = _lire_suivi(reference)
        if connu and connu.get("livre") and not frappe.utils.cint(tout):
            out["suivis"][reference] = connu
            continue
        suivi = interroger(reference)
        out["interroges"] += 1
        if suivi.get("erreur"):
            out["erreurs"] += 1
        _ranger_suivi(reference, suivi)
        out["suivis"][reference] = suivi
    return out


# ------------------------------------------------------------------ information du client


def sms_actif():
    return bool(frappe.db.get_single_value(DOCTYPE_CONFIG, "sms_auto"))


def modele_sms():
    return (frappe.db.get_single_value(DOCTYPE_CONFIG, "modele_sms") or "").strip() \
        or MODELE_SMS_DEFAUT


def doit_prevenir(suivi, deja_envoye, telephone):
    """Faut-il prevenir ce client ? Fonction pure : c'est elle qu'on teste.

    QUATRE CONDITIONS, ET CHACUNE REPOND A UNE FACON DE SE TROMPER
    --------------------------------------------------------------
    - `deja_envoye` : UN SEUL SMS PAR BORDEREAU. Le colis peut rester quinze jours dans le tableau
      et la synchronisation passe chaque soir : sans cette date posee une fois pour toutes, le
      client recevrait quinze messages pour un seul colis.
    - `livre` : demande explicitement. On ne derange pas quelqu'un pour lui annoncer ce qu'il sait
      deja — il a le colis en main.
    - `erreur` : un bordereau qu'Aramex ne connait pas n'a AUCUN statut a annoncer. Ecrire au
      client que son colis est introuvable, c'est lui transmettre notre probleme de saisie.
    - `telephone` : sans numero, il n'y a rien a envoyer, et l'echec doit se voir comme tel.
    - l'etape : entre la 2e et la derniere. Voir `ETAPE_MINIMALE_SMS` — un colis simplement
      « Cree » n'a pas quitte nos locaux, l'annoncer serait faux. La borne haute est deja tenue
      par `livre`, qui ecarte la 7e.
    """
    if deja_envoye or not telephone:
        return False
    if not suivi or suivi.get("livre") or suivi.get("erreur"):
        return False
    if int(suivi.get("etapes_franchies") or 0) < ETAPE_MINIMALE_SMS:
        return False
    return bool(suivi.get("statut"))


def message_sms(colis, modele=None):
    """Le texte envoye. Pure, donc relisible sans rien lancer."""
    suivi = colis.get("suivi") or {}
    return (modele or MODELE_SMS_DEFAUT).format(
        client=colis.get("customer_name") or colis.get("customer") or "",
        reference=colis.get("reference") or "",
        statut=suivi.get("statut") or "",
        montant=flt(colis.get("montant"), 3),
        destination=(suivi.get("destination") or {}).get("ville") or "")


def prevenir(colis, modele=None):
    """Envoie le SMS et pose la trace. -> dict. Ne leve pas : un SMS rate n'annule pas un suivi."""
    from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import (
        _send_sms_with_fallback,
    )
    from customization_app.retenue_source import separer

    numeros = separer(colis.get("telephone"))
    texte = message_sms(colis, modele)
    try:
        _send_sms_with_fallback(numeros, texte)
    except Exception as e:
        return {"reference": colis["reference"], "statut": "echec", "erreur": str(e)[:160]}
    frappe.db.set_value(DOCTYPE_SUIVI, colis["reference"], {
        "sms_envoye_le": now_datetime(),
        "sms_statut": (colis.get("suivi") or {}).get("statut"),
        "sms_destinataires": ", ".join(numeros)}, update_modified=False)
    return {"reference": colis["reference"], "statut": "envoye",
            "a": ", ".join(numeros), "message": texte}


def prevenir_les_clients(annee=None):
    """Previent une fois chaque client dont le colis a bouge et n'est pas arrive.

    Appelee juste apres la synchronisation de 16h, et seulement si le reglage est coche.
    """
    if not sms_actif():
        return {"actif": False, "envoyes": 0, "detail": []}
    annee = annee or getdate(nowdate()).year
    data = get_data("%s-01-01" % annee, "%s-12-31" % annee)
    envoyes = {r.name for r in frappe.get_all(DOCTYPE_SUIVI,
                                              filters={"sms_envoye_le": ["is", "set"]},
                                              fields=["name"], limit_page_length=0)}
    modele = modele_sms()
    out = {"actif": True, "envoyes": 0, "echecs": 0, "detail": []}
    for colis in data["colis"]:
        if not colis.get("reference"):
            continue
        if not doit_prevenir(colis.get("suivi"), colis["reference"] in envoyes,
                             colis.get("telephone")):
            continue
        res = prevenir(colis, modele)
        out["detail"].append(res)
        out["envoyes" if res["statut"] == "envoye" else "echecs"] += 1
    frappe.db.commit()
    return out


@frappe.whitelist()
def basculer_sms(actif):
    """Coche ou decoche l'envoi automatique depuis l'ecran."""
    frappe.only_for(["System Manager", "Sales Manager"])
    frappe.db.set_single_value(DOCTYPE_CONFIG, "sms_auto", 1 if frappe.utils.cint(actif) else 0)
    frappe.db.commit()
    return {"sms_auto": sms_actif()}


# ------------------------------------------------------------------ tache quotidienne


def rafraichir_tout(limite=200, annee=None):
    """Actualise le suivi de tous les colis de l'annee qui ne sont pas encore livres.

    Appelee par le planificateur, sans utilisateur devant l'ecran : elle ne demande donc aucun
    droit et se contente de journaliser. Un colis LIVRE n'est jamais re-interroge — son etat est
    definitif — si bien qu'en regime etabli seuls les colis en circulation coutent un appel.
    """
    annee = annee or getdate(nowdate()).year
    lignes = _paiements(getdate("%s-01-01" % annee), getdate("%s-12-31" % annee))
    references, vus = [], set()
    for l in lignes:
        reference = reference_aramex(l.reference_no)
        if reference and reference not in vus:
            vus.add(reference)
            references.append(reference)

    connus = {r.name: r for r in frappe.get_all(DOCTYPE_SUIVI,
                                               fields=["name", "livre", "tentatives"],
                                               limit_page_length=0)}
    livres = [r for r in references if (connus.get(r) or {}).get("livre")]
    # Un bordereau qu'Aramex ignore depuis trois passages ne se reveillera pas : on garde le
    # constat a l'ecran, on cesse d'y consacrer 21 secondes par jour.
    muets = [r for r in references if r not in livres
             and ((connus.get(r) or {}).get("tentatives") or 0) >= TENTATIVES_MAX]
    a_faire = [r for r in references if r not in livres and r not in muets]

    out = {"colis": len(references), "deja_livres": len(livres), "abandonnes": len(muets),
           "interroges": 0, "erreurs": 0, "restants": 0}
    for reference in a_faire:
        if out["interroges"] >= limite:
            out["restants"] += 1
            continue
        suivi = interroger(reference)
        out["interroges"] += 1
        if suivi.get("erreur"):
            out["erreurs"] += 1
        _ranger_suivi(reference, suivi)
    frappe.db.commit()
    return out


def run_cron():
    """Point d'entree du planificateur (tous les jours a 16h00).

    ⚠️ NE LEVE JAMAIS. Une panne du service de suivi ne doit pas faire echouer la file des taches
    planifiees ni polluer le journal d'erreurs d'une trace par colis : elle est resumee une fois.
    """
    try:
        res = rafraichir_tout()
        # L'ordre compte : on ne previent qu'apres avoir su, sinon on annoncerait le statut de la
        # veille.
        res["sms"] = prevenir_les_clients()
        if res["erreurs"] or res["restants"]:
            frappe.log_error(
                title="Suivi Aramex : %s erreur(s), %s restant(s)" % (res["erreurs"],
                                                                      res["restants"]),
                message=frappe.as_json(res))
        return res
    except Exception:
        frappe.log_error(title="Suivi Aramex : tache quotidienne",
                         message=frappe.get_traceback())
