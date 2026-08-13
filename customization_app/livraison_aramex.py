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
    """Le dernier suivi connu, tel qu'il a ete range. -> dict ou None."""
    brut = frappe.db.get_value(DOCTYPE_SUIVI, reference, "payload")
    if not brut:
        return None
    try:
        return frappe.parse_json(brut)
    except Exception:
        return None


def _ranger_suivi(reference, suivi):
    """Range le suivi d'un colis. Un document par bordereau, mis a jour en place.

    Les champs deplies (statut, livre, derniere mise a jour) servent aux listes et aux rapports ;
    `payload` garde la reponse entiere, y compris les etapes que l'ecran dessine.
    """
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
        "payload": frappe.as_json(suivi),
    }
    if frappe.db.exists(DOCTYPE_SUIVI, reference):
        doc = frappe.get_doc(DOCTYPE_SUIVI, reference)
        doc.update(valeurs)
        doc.save(ignore_permissions=True)
    else:
        frappe.get_doc(dict(doctype=DOCTYPE_SUIVI, reference=reference,
                            **valeurs)).insert(ignore_permissions=True)


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
           ORDER BY pe.posting_date DESC, pe.name""",
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
                        "shipping_address", "status", "grand_total", "transaction_date"],
        "Sales Invoice": ["name", "customer_name", "contact_mobile", "contact_email",
                          "shipping_address", "status", "grand_total", "posting_date"],
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

    jusqu_a = getdate(to_date) if to_date else getdate(nowdate())
    depuis = getdate(from_date) if from_date else getdate(add_days(jusqu_a, -90))

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
            "adresse": _texte(piece.shipping_address if piece else None),
            "suivi": suivi,
            "alerte": alerte(suivi),
        })

    return {"periode": {"from": str(depuis), "to": str(jusqu_a)},
            "compte": COMPTE_ARAMEX, "colis": colis, "kpis": kpis(colis),
            "peut_voir_paiement": peut_voir_paiement()}


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
        else:
            _ranger_suivi(reference, suivi)
        out["suivis"][reference] = suivi
    return out
