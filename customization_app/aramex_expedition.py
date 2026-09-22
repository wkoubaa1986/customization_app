"""Creation du bordereau Aramex depuis la commande, par l'API officielle.

AVANT : LE BORDEREAU SE SAISISSAIT A LA MAIN, ET LE PAIEMENT AUSSI
-------------------------------------------------------------------
Le colis partait avec un bordereau etabli sur le portail Aramex ; quelqu'un recopiait le
numero sur la commande (ecran Traitement) ou dans le libelle d'un paiement « Dette non payee »
pose sur le compte « Livraison Aramex - A&S » (« Aramex N: 5133… », et parfois « Aramex N:
0000 » quand le numero n'etait pas encore connu). Quatre personnes le faisaient, chacune a sa
facon.

MAINTENANT : LA COMMANDE DEMANDE SON BORDEREAU A ARAMEX
--------------------------------------------------------
Un bouton sur la commande soumise construit l'expedition (destinataire, adresse, telephone,
contre-remboursement, poids), l'envoie a `CreateShipments`, recoit le numero et l'etiquette
PDF, puis fait EXACTEMENT ce que la saisie manuelle faisait — par le meme chemin
(`traitement_commandes.definir_bordereau`) : champ sur la commande, alignement du paiement
Aramex s'il existe, trace en commentaire. A quoi s'ajoutent l'etiquette attachee, le suivi
« Cree » range tout de suite (la pastille de la liste n'attend pas 16h) et, sur demande, le
paiement d'attente pose avec le BON numero.

LE CONTRE-REMBOURSEMENT EST EN ESPECES, SAUF MENTION EXPLICITE
--------------------------------------------------------------
Aramex encaisse en especes. La case « Cheque autorise » du dialogue, decochee par defaut,
transmet l'autorisation en instruction au transporteur et la garde sur la commande. Le
paiement d'attente, lui, reste en « Dette non payee » dans les deux cas : c'est la TRACE de
l'argent chez Aramex, pas le mode par lequel le client a paye.

⚠️ CREER UN BORDEREAU, C'EST CREER UN VRAI COLIS CHEZ ARAMEX. En `developer_mode`, l'appel
est refuse sauf cle `aramex_creation_reelle_en_dev` dans site_config.json ou URL de bac a
sable (ws.dev.aramex.net). Les incidents SMS/e-mail du dev vers de vrais clients ont appris
que « c'est le dev » ne protege de rien.
"""

from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, nowdate

from customization_app import aramex_api
from customization_app.livraison_aramex import (COMPTE_ARAMEX, DOCTYPE_SUIVI, PRECISION,
                                                _ranger_suivi)
from customization_app.traitement_commandes import _definir_bordereau, aramex_des_commandes

MODE_PAIEMENT_ATTENTE = "Dette non payée"
ORIGINE_API = "créé par l'API Aramex"
ROLES_ATTENTE = ("System Manager", "Sales Manager")
LONGUEUR_DESCRIPTION = 60


class CreationRefusee(frappe.ValidationError):
    pass


# ------------------------------------------------------------------ lecture


def _commande(nom):
    if not nom or not frappe.db.exists("Sales Order", nom):
        frappe.throw(_("Commande introuvable."))
    return frappe.get_doc("Sales Order", nom)


def _adresse(so):
    """L'adresse de LIVRAISON de la commande, a defaut celle de facturation — et on le dit."""
    nom = so.shipping_address_name or so.customer_address
    if not nom:
        return None, False
    a = frappe.db.get_value("Address", nom,
                            ["name", "address_line1", "address_line2", "city", "state",
                             "custom_state_s", "pincode", "country", "phone"], as_dict=True)
    return a, bool(a and not so.shipping_address_name)


def proposition_adresse(a, liste_villes) -> dict:
    """Ce que le dialogue pre-remplit pour UNE adresse (dict Address, ou None) : texte, ville
    Aramex mise en correspondance, gouvernorat, code postal. Fonction pure."""
    a = a or {}
    ville_commande = a.get("city") or ""
    gouvernorat = a.get("custom_state_s") or a.get("state") or ""
    corr = ville_aramex(ville_commande, gouvernorat, liste_villes)
    return {
        "adresse": ", ".join(x for x in (a.get("address_line1"), a.get("address_line2")) if x),
        # La ville envoyee a Aramex est la SIENNE ; celle de la commande reste visible a cote.
        "ville": corr["ville"] or ville_commande,
        "ville_commande": ville_commande,
        "ville_certitude": corr["certitude"],
        "ville_candidats": corr["candidats"],
        "gouvernorat": gouvernorat,
        "code_postal": a.get("pincode") or "",
    }


def _adresses_du_client(so, courante, liste_villes) -> list:
    """Toutes les adresses actives de la fiche client, pretes a etre choisies dans le dialogue,
    la proposee (`courante`) en tete. Choisir ici ne change que le COLIS, jamais la commande."""
    noms = frappe.get_all("Dynamic Link", pluck="parent", filters={
        "parenttype": "Address", "link_doctype": "Customer", "link_name": so.customer})
    if not noms:
        return []
    lignes = frappe.get_all("Address", filters={"name": ["in", noms], "disabled": 0},
                            fields=["name", "address_type", "address_line1", "address_line2",
                                    "city", "state", "custom_state_s", "pincode"],
                            order_by="creation")
    lignes.sort(key=lambda a: a.name != courante)
    out = []
    for a in lignes:
        prop = proposition_adresse(a, liste_villes)
        out.append(dict(prop, name=a.name, courante=(a.name == courante),
                        libelle="%s · %s" % (_(a.address_type or "Address"),
                                             ", ".join(x for x in (prop["adresse"], prop["ville_commande"]) if x))))
    return out


def _telephones(so):
    """(telephone, telephone2) du destinataire : les numeros de la commande (contact), puis
    `mobile_no` du client, puis « Liste Telephone » de la fiche client — le champ que les SMS,
    le portail et les relances lisent deja. Aramex en recoit deux : le premier est celui que
    le livreur appelle, le second son repli.

    ⚠️ Sans Liste Telephone, un client dont aucun numero de contact n'est coche « principal »
    arrivait avec un telephone VIDE alors que sa fiche en porte deux (SAL-ORD-2026-03861,
    22/09/2026) — et le dialogue refusait meme le numero tape a la main.
    """
    client = frappe.db.get_value("Customer", so.customer, ["mobile_no", "custom_liste_telephone"],
                                 as_dict=True) or {}
    return choisir_telephones([so.contact_mobile, so.contact_phone, client.get("mobile_no")],
                              client.get("custom_liste_telephone"))


def numeros_de_la_liste(brut) -> list:
    """Les numeros a 8 chiffres d'un champ « Liste Telephone » (un par ligne, ou separes par
    virgule, point-virgule, slash), dans l'ordre du champ, sans doublon. Fonction pure."""
    nums = []
    for morceau in re.split(r"[;\n/,]+", brut or ""):
        n = normaliser_telephone(morceau)
        if len(n) == 8 and n not in nums:
            nums.append(n)
    return nums


def choisir_telephones(candidats, liste) -> tuple:
    """(telephone, telephone2) : le premier numero a 8 chiffres parmi `candidats` (dans
    l'ordre) puis dans `liste`, et le suivant distinct comme repli. S'il n'y a aucun numero
    valide, on rend le premier candidat non vide tel quel — pour qu'il s'affiche et se
    corrige, plutot que de disparaitre. Fonction pure."""
    nums = []
    for c in [normaliser_telephone(c) for c in candidats] + numeros_de_la_liste(liste):
        if len(c) == 8 and c not in nums:
            nums.append(c)
    if not nums:
        brut = next((normaliser_telephone(c) for c in candidats if normaliser_telephone(c)), "")
        return brut, ""
    return nums[0], (nums[1] if len(nums) > 1 else "")


def normaliser_telephone(tel) -> str:
    """Ne garde que les chiffres, sans indicatif +216 : Aramex Tunisie veut le numero local.
    « +216 24 992 312 » -> « 24992312 ». Fonction pure."""
    chiffres = re.sub(r"\D", "", tel or "")
    if chiffres.startswith("00216"):
        chiffres = chiffres[5:]
    elif chiffres.startswith("216") and len(chiffres) == 11:
        chiffres = chiffres[3:]
    return chiffres


def _avances_hors_aramex(so) -> float:
    """Ce que le client a DEJA paye, hors paiement d'attente Aramex.

    ⚠️ `advance_paid` NE SUFFIT PAS : le paiement d'attente pose sur « Livraison Aramex - A&S »
    est alloue a la commande comme une avance — sur les commandes Aramex existantes,
    `advance_paid == grand_total` alors que le client n'a rien verse. Le reste a payer se
    calcule donc sur les paiements des AUTRES comptes, vises directement ou par la facture.
    """
    lignes = frappe.db.sql(
        """SELECT SUM(per.allocated_amount) AS m
           FROM `tabPayment Entry` pe
           JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
           WHERE pe.docstatus = 1 AND pe.paid_to != %(aramex)s
             AND ((per.reference_doctype = 'Sales Order' AND per.reference_name = %(so)s)
                  OR (per.reference_doctype = 'Sales Invoice' AND per.reference_name IN
                      (SELECT DISTINCT sii.parent FROM `tabSales Invoice Item` sii
                       JOIN `tabSales Invoice` si ON si.name = sii.parent AND si.docstatus = 1
                       WHERE sii.sales_order = %(so)s)))""",
        {"aramex": COMPTE_ARAMEX, "so": so.name}, as_dict=True)
    return flt(lignes[0].m if lignes else 0, PRECISION)


def _paiement_aramex(so):
    """Le paiement d'attente Aramex deja pose sur la commande (le plus recent), ou None."""
    from customization_app.traitement_commandes import _bordereaux

    return (_bordereaux([so.name]).get(so.name) or {}).get("payment_entry")


def _poids(so, cfg) -> float:
    total = sum(flt(i.get("total_weight")) for i in so.items)
    if total > 0 and (so.items[0].get("weight_uom") or "").lower() in ("kg", "kilogramme", ""):
        return round(total, 2)
    return flt(cfg.get("poids_defaut")) or 0.5


# ------------------------------------------------------------------ villes Aramex

# Nos villes (sectorisation.py, saisies sur les adresses) -> la ville Aramex. Construite sur
# les 103 villes reellement utilisees par les commandes Aramex depuis juin 2026 : ce qui ne se
# retrouve ni a l'identique ni par inclusion. Cle normalisee (voir `_normaliser_ville`).
# ⚠️ « El Ghazala » (Ariana) n'existe pas chez Aramex, et la ressemblance avec « Ghezala »
# (Bizerte, a 60 km) est un piege pour la comparaison floue : d'ou l'alias explicite vers la
# delegation, Raoued.
ALIAS_VILLES = {
    "el ghazala": "Raoued",
    "cite el ghazala": "Raoued",
    "cite ennasr 2": "Ariana",
    "cite ennasr": "Ariana",
    "ennasr": "Ariana",
    "soukra": "La Soukra",
    "majaz al bab": "Mejez El Bab",
    "medjez el bab": "Mejez El Bab",
    "djerba houmt souk": "Houmet Essouk",
    "houmt souk": "Houmet Essouk",
    "ksar hellal": "Ksar Helal",
    "jammel": "Jemmal",
    "manouba": "Mannouba",
    "kalaa kebira": "Kalaa El Kebira",
    "kalaa sghira": "Kalaa Essghira",
    "chebba": "La Chebba",
    "ksour essef": "Ksour Essaf",
    "boumhel el bassatine": "Bou Mhel El Bassatine",
    "msaken": "Msaken",
    "goulette": "La Goulette",
    "marsa": "La Marsa",
    "bardo": "Le Bardo",
    "kef": "Le Kef",
    "medina": "La Medina",
    "sousse ville": "Sousse",
    "sfax ville": "Sfax",
    "tunis ville": "Tunis",
    "djoumime": "Joumine",
    "kef east": "Le Kef",
    "kef ouest": "Le Kef",
    "ksar": "El Ksar",
    "gammarth": "La Marsa",
    "el jam": "El Jem",
    "fahs": "El Fahs",
    "ezzouhour": "Cite Ezzouhour",
    "medina jedida": "Nouvelle Medina",
    "jelma": "Jilma",
    "saouaf": "Saouef",
    "skhira": "Esskhira",
    "hammam el ghezaz": "Hammam El Ghezaz",
    "hammamet nord": "Hammamet",
    "hammamet sud": "Hammamet",
    "nabeul ville": "Nabeul",
    "monastir ville": "Monastir",
}
# Les gouvernorats dont le chef-lieu est une ville Aramex : dernier recours quand rien d'autre
# ne correspond — le colis part au moins vers la bonne region, et le dialogue le DIT.
SEUIL_APPROCHE = 0.86


def _normaliser_ville(texte) -> str:
    """« Cité El-Khadra » -> « cite el khadra ». Sans accents, sans tirets, espaces simples."""
    import re
    import unicodedata

    t = "".join(c for c in unicodedata.normalize("NFD", (texte or "").lower())
                if not unicodedata.combining(c))
    t = re.sub(r"[-_/,.'’]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def ville_aramex(ville, gouvernorat, villes) -> dict:
    """La ville Aramex a mettre sur le colis pour la ville de la commande. Fonction pure.
    -> {"ville": str|None, "certitude": "exacte"|"alias"|"contenue"|"approchee"|"gouvernorat"|None,
        "candidats": [str]}.

    Dans l'ordre : la meme ville (a la graphie pres) ; un alias connu ; une ville Aramex
    contenue en mot entier dans la notre (« Sfax Ville » -> Sfax, « Djerba Midoun » -> Midoun,
    « El Menzah 6 » -> El Menzah — la plus longue gagne) ; une ressemblance forte, rendue comme
    PROPOSITION a confirmer ; le chef-lieu du gouvernorat, signale comme tel.
    """
    import difflib

    par_cle = {}
    for v in villes or []:
        par_cle.setdefault(_normaliser_ville(v), v)
    cle = _normaliser_ville(ville)
    if not cle:
        return {"ville": None, "certitude": None, "candidats": []}
    if cle in par_cle:
        return {"ville": par_cle[cle], "certitude": "exacte", "candidats": []}
    alias = ALIAS_VILLES.get(cle)
    if alias and _normaliser_ville(alias) in par_cle:
        return {"ville": par_cle[_normaliser_ville(alias)], "certitude": "alias", "candidats": []}
    mots = cle.split()
    contenues = []
    for k, v in par_cle.items():
        km = k.split()
        n = len(km)
        if n and any(mots[i:i + n] == km for i in range(len(mots) - n + 1)):
            contenues.append((n, len(k), v))
    if contenues:
        contenues.sort(reverse=True)
        return {"ville": contenues[0][2], "certitude": "contenue",
                "candidats": [c[2] for c in contenues[1:4]]}
    proches = difflib.get_close_matches(cle, list(par_cle), n=4, cutoff=SEUIL_APPROCHE)
    if proches:
        return {"ville": par_cle[proches[0]], "certitude": "approchee",
                "candidats": [par_cle[p] for p in proches[1:]]}
    cle_gouv = _normaliser_ville(gouvernorat)
    if cle_gouv and cle_gouv in par_cle:
        return {"ville": par_cle[cle_gouv], "certitude": "gouvernorat", "candidats": []}
    if cle_gouv and ALIAS_VILLES.get(cle_gouv) and _normaliser_ville(ALIAS_VILLES[cle_gouv]) in par_cle:
        return {"ville": par_cle[_normaliser_ville(ALIAS_VILLES[cle_gouv])],
                "certitude": "gouvernorat", "candidats": []}
    return {"ville": None, "certitude": None, "candidats": []}


def _ville_canonique(ville, villes):
    """La graphie exacte d'Aramex pour une ville choisie dans la liste, ou None."""
    cle = _normaliser_ville(ville)
    for v in villes or []:
        if _normaliser_ville(v) == cle:
            return v
    return None


def description_marchandise(items, defaut, longueur=LONGUEUR_DESCRIPTION) -> str:
    """« Osmoseur 5 étapes, Filtre sédiment ×2 » tronque a 60 caracteres. Fonction pure.

    La ligne « Livraison » (les frais de port factures au client) n'est pas une marchandise :
    elle est ecartee — Aramex n'a pas a lire « Livraison » sur le colis qu'il livre."""
    morceaux = []
    for i in items or []:
        nom = (i.get("item_name") or i.get("item_code") or "").strip()
        if not nom or nom.lower().startswith("livraison"):
            continue
        qte = flt(i.get("qty"))
        morceaux.append("%s ×%d" % (nom, qte) if qte and qte != 1 else nom)
    texte = ", ".join(morceaux) or (defaut or "")
    return texte if len(texte) <= longueur else texte[:longueur - 1].rstrip() + "…"


# ------------------------------------------------------------------ construction (pure)


def construire_expedition(commande, expediteur, destinataire, colis, maintenant) -> dict:
    """Le corps d'UNE expedition `CreateShipments`, SANS ClientInfo ni LabelInfo. Fonction
    pure : `aramex_api.create_shipment` complete et envoie.

    `expediteur`  : {nom, societe, telephone, email, adresse, ville, code_postal, compte}
    `destinataire`: {nom, telephone, telephone2, email, adresse, ville, gouvernorat,
                     code_postal} — `telephone2` (facultatif) part en PhoneNumber2, le repli
                     du livreur quand le premier ne repond pas.
    `colis`       : {cod, poids, pieces, description, product_group, product_type,
                     cheque_autorise, entite}
    `maintenant`  : datetime naif, heure locale.
    """
    cod = flt(colis.get("cod"), PRECISION)
    instructions = ""
    if colis.get("cheque_autorise") and cod > 0:
        instructions = "Chèque accepté pour le contre-remboursement (au nom de %s)" % (
            expediteur.get("societe") or expediteur.get("nom") or "")

    def _partie(p, compte=""):
        return {
            "Reference1": "", "Reference2": "", "AccountNumber": compte or "",
            "PartyAddress": {
                "Line1": (p.get("adresse") or "")[:50], "Line2": "", "Line3": "",
                "City": p.get("ville") or "",
                "StateOrProvinceCode": p.get("gouvernorat") or "",
                "PostCode": p.get("code_postal") or "", "CountryCode": "TN",
                "Longitude": 0, "Latitude": 0, "BuildingNumber": None, "BuildingName": None,
                "Floor": None, "Apartment": None, "POBox": None, "Description": None,
            },
            "Contact": {
                "Department": "", "PersonName": p.get("nom") or "", "Title": "",
                # ⚠️ ARAMEX EXIGE LA SOCIETE (« ERR48 Consignee name is required », constate le
                # 15/09/2026 sur WEB1-008413) : pour un particulier, c'est son nom — il
                # s'imprime deux fois sur l'etiquette, et c'est le prix du refus evite.
                "CompanyName": p.get("societe") or p.get("nom") or "",
                "PhoneNumber1": p.get("telephone") or "", "PhoneNumber1Ext": "",
                "PhoneNumber2": p.get("telephone2") or "", "PhoneNumber2Ext": "", "FaxNumber": "",
                "CellPhone": p.get("telephone") or "", "EmailAddress": p.get("email") or "",
                "Type": "",
            },
        }

    date = aramex_api.date_wcf(maintenant)
    return {
        "Reference1": commande, "Reference2": "", "Reference3": "",
        "Shipper": _partie(expediteur, expediteur.get("compte")),
        "Consignee": _partie(destinataire),
        "ThirdParty": _partie({}),
        "ShippingDateTime": date, "DueDate": date,
        "Comments": instructions, "PickupLocation": "",
        "OperationsInstructions": instructions, "AccountingInstrcutions": "",
        "Details": {
            "Dimensions": None,
            "ActualWeight": {"Unit": "KG", "Value": flt(colis.get("poids")) or 0.5},
            "ChargeableWeight": None,
            "DescriptionOfGoods": colis.get("description") or "",
            "GoodsOriginCountry": "TN",
            "NumberOfPieces": cint(colis.get("pieces")) or 1,
            "ProductGroup": colis.get("product_group") or "DOM",
            "ProductType": colis.get("product_type") or "ONP",
            "PaymentType": "P", "PaymentOptions": "",
            "CustomsValueAmount": None,
            "CashOnDeliveryAmount": ({"CurrencyCode": "TND", "Value": cod} if cod > 0 else None),
            "InsuranceAmount": None, "CashAdditionalAmount": None,
            "CashAdditionalAmountDescription": "", "CollectAmount": None,
            "Services": "CODS" if cod > 0 else "",
            "Items": [],
        },
        "Attachments": [], "ForeignHAWB": "", "TransportType": 0, "PickupGUID": "",
        "Number": None, "ScheduledDelivery": None,
    }


def motifs_de_refus(destinataire, colis) -> list:
    """Ce qui empeche de creer le colis. Fonction pure. On corrige, on ne cree pas un colis faux."""
    motifs = []
    if not (destinataire.get("nom") or "").strip():
        motifs.append(_("Nom du destinataire manquant."))
    tel = destinataire.get("telephone") or ""
    if len(tel) < 8:
        motifs.append(_("Téléphone du destinataire manquant ou incomplet (8 chiffres attendus)."))
    if not (destinataire.get("adresse") or "").strip():
        motifs.append(_("Adresse de livraison manquante."))
    if not (destinataire.get("ville") or "").strip():
        motifs.append(_("Ville de livraison manquante."))
    if flt(colis.get("cod")) < 0:
        motifs.append(_("Montant du contre-remboursement négatif."))
    if flt(colis.get("poids")) <= 0:
        motifs.append(_("Poids du colis nul."))
    return motifs


# ------------------------------------------------------------------ API


@frappe.whitelist()
def etat(commande):
    """Ce que la fiche commande doit savoir pour montrer ses boutons. Aucun appel externe."""
    frappe.has_permission("Sales Order", "read", doc=commande, throw=True)
    try:
        cfg = aramex_api.config()
    except Exception:
        return {"api_active": False}
    info = aramex_des_commandes([commande]).get(commande) or {}
    so = frappe.db.get_value("Sales Order", commande,
                             ["docstatus", "custom_etiquette_aramex", "custom_statut_aramex"],
                             as_dict=True) or {}
    return {
        "api_active": aramex_api.api_active(cfg),
        "creation_active": aramex_api.creation_active(cfg),
        "aramex": bool(info.get("aramex")),
        "bordereau": info.get("bordereau") or "",
        "etiquette": so.get("custom_etiquette_aramex") or "",
        "statut": so.get("custom_statut_aramex") or "",
        "soumise": cint(so.get("docstatus")) == 1,
        "peut_attente": any(r in frappe.get_roles() for r in ROLES_ATTENTE),
    }


@frappe.whitelist()
def preparer(commande):
    """Tout ce que le dialogue montre avant d'envoyer : destinataire, adresse, telephone,
    contre-remboursement (reste a payer), poids, description — et les motifs qui bloquent.
    Aucun appel a Aramex ici : c'est l'appel a blanc."""
    frappe.has_permission("Sales Order", "write", doc=commande, throw=True)
    return _preparer(_commande(commande), aramex_api.config())


def _preparer(so, cfg):
    commande = so.name
    adresse, de_facturation = _adresse(so)
    avances = _avances_hors_aramex(so)
    cod = max(0.0, flt(so.grand_total, PRECISION) - avances)
    liste_villes = aramex_api.villes(cfg)
    ville_commande = (adresse or {}).get("city") or ""
    gouvernorat = (adresse or {}).get("custom_state_s") or (adresse or {}).get("state") or ""
    correspondance = ville_aramex(ville_commande, gouvernorat, liste_villes)
    societe = frappe.db.get_value("Customer", so.customer, "customer_type") == "Company"
    telephone, telephone2 = _telephones(so)
    destinataire = dict({
        "nom": so.customer_name or so.customer,
        "societe": (so.customer_name or so.customer) if societe else "",
        "telephone": telephone,
        "telephone2": telephone2,
        "email": so.contact_email or "",
    }, **proposition_adresse(adresse, liste_villes))
    colis = {
        "cod": round(cod, PRECISION),
        "poids": _poids(so, cfg),
        "pieces": cint(cfg.get("pieces_defaut")) or 1,
        "description": description_marchandise(
            [i.as_dict() for i in so.items], cfg.get("description_defaut")),
        "cheque_autorise": cint(so.get("custom_aramex_cheque_autorise")),
    }
    info = aramex_des_commandes([commande]).get(commande) or {}
    pe = _paiement_aramex(so)
    # Deux listes, parce que le dialogue les traite differemment : `a_corriger` se repare
    # DANS le formulaire (telephone, adresse, ville…) et ne bloque pas le bouton — le serveur
    # revalide a la creation sur ce qui a ete saisi ; `motifs` bloque quoi qu'on tape.
    # ⚠️ Avant (jusqu'a 5.86.1), tout etait dans `motifs` : un telephone manquant rendait le
    # bouton inerte MEME apres l'avoir tape dans le champ prevu pour ca.
    a_corriger = motifs_de_refus(destinataire, colis)
    if ville_commande and not correspondance["ville"]:
        a_corriger.append(_("Ville « {0} » inconnue d'Aramex : choisissez-la dans la liste.")
                          .format(ville_commande))
    motifs = []
    if not aramex_api.creation_active(cfg):
        motifs.append(_("La création de bordereaux n'est pas activée (Config Livraison Aramex)."))
    if info.get("bordereau"):
        motifs.append(_("Un bordereau existe déjà : {0}.").format(info["bordereau"]))
    if not info.get("aramex"):
        motifs.append(_("Cette commande n'est pas en livraison Aramex (échéancier)."))
    expediteur_ok = bool(cfg.get("expediteur_nom") and cfg.get("expediteur_telephone")
                         and cfg.get("expediteur_adresse") and cfg.get("expediteur_ville"))
    if not expediteur_ok:
        motifs.append(_("Expéditeur incomplet dans Config Livraison Aramex (nom, téléphone, adresse, ville)."))
    return {
        "destinataire": destinataire,
        "villes": liste_villes,
        "adresses": _adresses_du_client(so, (adresse or {}).get("name"), liste_villes),
        "adresse_de_facturation": de_facturation,
        "colis": colis,
        "total": flt(so.grand_total, PRECISION),
        "avances": avances,
        "paiement_aramex": pe,
        "poser_paiement": 0 if pe else 1,
        "brouillon": cint(so.docstatus) == 0,
        "motifs": motifs,
        "a_corriger": a_corriger,
        "garde_fou_dev": _bloque_en_dev(cfg),
    }


def _bloque_en_dev(cfg) -> bool:
    if not frappe.conf.get("developer_mode"):
        return False
    if frappe.conf.get("aramex_creation_reelle_en_dev"):
        return False
    return "ws.dev.aramex.net" not in aramex_api.base_url(cfg)


def _expediteur(cfg) -> dict:
    return {
        "nom": cfg.get("expediteur_nom") or "",
        "societe": cfg.get("expediteur_societe") or cfg.get("expediteur_nom") or "",
        "telephone": normaliser_telephone(cfg.get("expediteur_telephone")),
        "email": cfg.get("expediteur_email") or "",
        "adresse": cfg.get("expediteur_adresse") or "",
        "ville": cfg.get("expediteur_ville") or "",
        "code_postal": cfg.get("expediteur_code_postal") or "",
        "compte": str(cfg.get("api_account_number") or "").strip(),
    }


@frappe.whitelist()
def creer_bordereau(commande, destinataire=None, colis=None, poser_paiement=1):
    """Cree l'expedition chez Aramex et fait suivre la commande. -> dict.

    `destinataire` / `colis` : les valeurs du dialogue (JSON), relues par l'employe — elles
    priment sur ce que `preparer` avait propose, sauf le montant, qui ne peut pas depasser
    le reste a payer.
    """
    frappe.has_permission("Sales Order", "write", doc=commande, throw=True)
    return _creer(commande, destinataire, colis, poser_paiement)


def _creer(commande, destinataire=None, colis=None, poser_paiement=1, origine=None):
    """Le travail de `creer_bordereau`, SANS le controle de droit sur la commande : « Ma
    journee » l'appelle pour le technicien, dont le droit se lit sur SA TACHE (il n'ecrit pas
    les commandes — mais il tient le colis). `origine` complete la trace."""
    so = _commande(commande)
    cfg = aramex_api.config()
    # ⚠️ UN BROUILLON EST VALIDE D'ABORD (decision utilisateur 15/09/2026) : dans le flux reel,
    # le colis part au moment ou la commande web est validee. On soumet donc la commande —
    # ses Server Scripts posent l'echeancier et le paiement d'attente « Aramex N: xxxx » comme
    # a la main — PUIS on demande le numero a Aramex et on l'aligne partout. Si la validation
    # echoue (echeancier incomplet, cheque sans numero…), rien n'est envoye a Aramex.
    if cint(so.docstatus) == 2:
        frappe.throw(_("La commande est annulée."), CreationRefusee)
    brouillon = cint(so.docstatus) == 0
    if not aramex_api.creation_active(cfg):
        frappe.throw(_("La création de bordereaux n'est pas activée (Config Livraison Aramex)."),
                     CreationRefusee)
    if _bloque_en_dev(cfg):
        frappe.throw(_("Site de développement : la création d'un VRAI colis chez Aramex est "
                       "refusée (clé aramex_creation_reelle_en_dev ou URL ws.dev.aramex.net)."),
                     CreationRefusee)
    info = aramex_des_commandes([commande]).get(commande) or {}
    if info.get("bordereau"):
        frappe.throw(_("Cette commande porte déjà le bordereau {0}.").format(info["bordereau"]),
                     CreationRefusee)
    if not info.get("aramex"):
        frappe.throw(_("Cette commande n'est pas en livraison Aramex."), CreationRefusee)

    if brouillon:
        # Le droit de valider se lit chez l'appelant (fiche : droits de la commande ;
        # Ma journee : la tache du technicien) — ici on execute la decision.
        so.flags.ignore_permissions = True
        so.submit()
        so = _commande(commande)
        frappe.get_doc({
            "doctype": "Comment", "comment_type": "Info",
            "reference_doctype": "Sales Order", "reference_name": commande,
            "content": _("✅ Commande validée par {0} pour la création du bordereau Aramex{1}.")
            .format(frappe.session.user, (" (%s)" % origine) if origine else ""),
        }).insert(ignore_permissions=True)

    base = _preparer(so, cfg)
    destinataire = dict(base["destinataire"], **(frappe.parse_json(destinataire) or {}))
    destinataire["telephone"] = normaliser_telephone(destinataire.get("telephone"))
    destinataire["telephone2"] = normaliser_telephone(destinataire.get("telephone2"))
    if destinataire["telephone2"] == destinataire["telephone"]:
        destinataire["telephone2"] = ""
    colis = dict(base["colis"], **(frappe.parse_json(colis) or {}))
    cod_max = base["colis"]["cod"]
    cod = flt(colis.get("cod"), PRECISION)
    if cod > cod_max + 0.0005:
        frappe.throw(_("Le contre-remboursement ({0}) dépasse le reste à payer de la commande "
                       "({1}).").format(cod, cod_max), CreationRefusee)
    colis["cod"] = cod
    colis["cheque_autorise"] = cint(colis.get("cheque_autorise"))
    colis["product_group"] = cfg.get("product_group") or "DOM"
    colis["product_type"] = cfg.get("product_type") or "ONP"
    # Ce qui se corrige dans le dialogue est revalide ICI, sur la saisie ; ce qui bloque
    # (expediteur incomplet, creation inactive…) bloque toujours.
    motifs = motifs_de_refus(destinataire, colis) + list(base["motifs"])
    # ⚠️ LA VILLE DOIT ETRE UNE VILLE ARAMEX, A LA GRAPHIE PRES. Une ville « presque » bonne
    # fait refuser le colis (ERR05) ; une ville d'un autre gouvernorat qui ressemble le fait
    # partir au mauvais endroit. On exige donc un choix dans la liste, puis on demande
    # confirmation a Aramex lui-meme (ValidateAddress) avant de creer.
    canonique = _ville_canonique(destinataire.get("ville"), base.get("villes"))
    if not canonique:
        suggestion = ville_aramex(destinataire.get("ville"), destinataire.get("gouvernorat"),
                                  base.get("villes"))
        motifs.append(_("Ville « {0} » inconnue d'Aramex{1}.").format(
            destinataire.get("ville") or "",
            (" — " + _("proposition : {0}").format(suggestion["ville"]))
            if suggestion.get("ville") else ""))
    else:
        destinataire["ville"] = canonique
    if motifs:
        frappe.throw("<br>".join(motifs), CreationRefusee)
    verdict = aramex_api.validate_address({
        "ligne1": destinataire.get("adresse"), "ville": destinataire["ville"],
        "gouvernorat": destinataire.get("gouvernorat"),
        "code_postal": destinataire.get("code_postal")}, cfg=cfg)
    if verdict.get("erreur"):
        frappe.throw(_("Aramex refuse cette adresse : {0}").format(verdict["erreur"]),
                     CreationRefusee)

    expedition = construire_expedition(commande, _expediteur(cfg), destinataire, colis,
                                       now_datetime())
    res = aramex_api.create_shipment(expedition, cfg=cfg)
    if res.get("erreur"):
        frappe.log_error(title="Aramex : création refusée %s" % commande,
                         message=frappe.as_json({"erreur": res["erreur"],
                                                 "expedition": expedition,
                                                 "reponse": res.get("reponse")}))
        frappe.throw(_("Aramex a refusé la création : {0}").format(res["erreur"]),
                     CreationRefusee)
    numero = res["numero"]

    # Le meme chemin que la saisie manuelle : champ, alignement du paiement, trace.
    _definir_bordereau(commande, numero,
                       origine=(ORIGINE_API + (" (%s)" % origine if origine else "")))
    frappe.db.set_value("Sales Order", commande, {
        "custom_aramex_cheque_autorise": colis["cheque_autorise"],
        "custom_statut_aramex": "Créé",
    }, update_modified=False)

    etiquette = _attacher_etiquette(commande, numero, res.get("etiquette_url"))
    _ranger_creation(numero, res.get("etiquette_url"), colis, commande, destinataire)

    pe = None
    # Apres une validation, le Server Script a en general deja pose le paiement d'attente
    # (`paiement_aramex` le voit) : on ne le double pas.
    if cint(poser_paiement) and not base.get("paiement_aramex") and cod > 0:
        pe = _poser_paiement_attente(so, numero, cod)

    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info",
        "reference_doctype": "Sales Order", "reference_name": commande,
        "content": _("📦 Colis Aramex {0} : contre-remboursement {1} TND ({2}), {3} kg, "
                     "{4} pièce(s), vers {5} — {6}{7}").format(
            numero, colis["cod"],
            _("chèque autorisé") if colis["cheque_autorise"] else _("espèces"),
            colis["poids"], colis["pieces"], destinataire.get("ville") or "",
            _("étiquette attachée") if etiquette else _("étiquette non récupérée"),
            (" · " + _("paiement d'attente {0}").format(pe)) if pe
            else (" · " + _("paiement d'attente {0} aligné").format(base.get("paiement_aramex")))
            if base.get("paiement_aramex") else ""),
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"bordereau": numero, "etiquette": etiquette, "etiquette_url": res.get("etiquette_url"),
            "payment_entry": pe, "cod": colis["cod"]}


def _attacher_etiquette(commande, numero, url):
    """Rapatrie le PDF et l'attache a la commande. -> file_url ou None (jamais d'exception)."""
    if not url:
        return None
    contenu = aramex_api.telecharger(url)
    if not contenu:
        return None
    try:
        from frappe.utils.file_manager import save_file

        f = save_file("aramex-%s.pdf" % numero, contenu, "Sales Order", commande, is_private=0)
        frappe.db.set_value("Sales Order", commande, "custom_etiquette_aramex", f.file_url,
                            update_modified=False)
        return f.file_url
    except Exception:
        frappe.log_error(title="Aramex : étiquette %s" % numero, message=frappe.get_traceback())
        return None


def _ranger_creation(numero, etiquette_url, colis, commande=None, destinataire=None):
    """Le suivi « Créé » range tout de suite : la pastille de la liste n'attend pas le cron.
    Et ce qui a ete envoye a Aramex (commande, destinataire, ville, montant, pieces, poids)
    reste sur le suivi : c'est la matiere du manifeste de fin de journee."""
    evenement = {"UpdateCode": "SH014", "UpdateDescription": "Record created.",
                 "UpdateDateTime": aramex_api.date_wcf(now_datetime()),
                 "UpdateLocation": "", "Comments": "", "ProblemCode": "",
                 "ChargeableWeight": str(colis.get("poids") or "")}
    _ranger_suivi(numero, aramex_api.normaliser_suivi(numero, [evenement]))
    destinataire = destinataire or {}
    frappe.db.set_value(DOCTYPE_SUIVI, numero, {
        "etiquette_url": etiquette_url or None,
        "cree_le": now_datetime(),
        "cree_par": frappe.session.user,
        "commande": commande,
        "destinataire": destinataire.get("nom"),
        "ville": destinataire.get("ville"),
        "cod": flt(colis.get("cod"), PRECISION),
        "pieces": cint(colis.get("pieces")) or 1,
        "poids": flt(colis.get("poids")),
    }, update_modified=False)


def _poser_paiement_attente(so, numero, montant):
    """Le paiement d'attente que la caisse posait a la main : mode « Dette non payee », compte
    « Livraison Aramex - A&S », libelle « Aramex N: <numero> », alloue a la commande.

    Construit par `get_payment_entry` d'ERPNext — le meme que le bouton « Paiement » de la
    fiche — puis redirige vers le compte Aramex : comptes, devise et references viennent de
    lui, pas d'une recopie. Soumis dans la foulee, comme le faisait la caisse.
    """
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    pe = get_payment_entry("Sales Order", so.name, party_amount=montant,
                           ignore_permissions=True)
    pe.mode_of_payment = MODE_PAIEMENT_ATTENTE
    pe.paid_to = COMPTE_ARAMEX
    pe.paid_to_account_currency = frappe.db.get_value("Account", COMPTE_ARAMEX,
                                                      "account_currency") or "TND"
    pe.posting_date = nowdate()
    pe.reference_no = "Aramex N: %s" % numero
    pe.reference_date = nowdate()
    pe.paid_amount = pe.received_amount = flt(montant, PRECISION)
    for r in pe.references:
        r.allocated_amount = flt(montant, PRECISION)
    pe.setup_party_account_field()
    pe.set_missing_values()
    pe.insert(ignore_permissions=True)
    pe.submit()
    return pe.name


@frappe.whitelist()
def etiquette(commande):
    """Redemande l'etiquette a Aramex (PrintLabel) et l'attache. -> {etiquette, etiquette_url}."""
    frappe.has_permission("Sales Order", "write", doc=commande, throw=True)
    info = aramex_des_commandes([commande]).get(commande) or {}
    numero = info.get("bordereau")
    if not numero:
        frappe.throw(_("Aucun bordereau Aramex sur cette commande."))
    res = aramex_api.print_label(numero)
    if res.get("erreur"):
        frappe.throw(_("Aramex n'a pas rendu l'étiquette : {0}").format(res["erreur"]))
    fichier = _attacher_etiquette(commande, numero, res["etiquette_url"])
    if frappe.db.exists(DOCTYPE_SUIVI, numero):
        frappe.db.set_value(DOCTYPE_SUIVI, numero, "etiquette_url", res["etiquette_url"],
                            update_modified=False)
    frappe.db.commit()
    return {"etiquette": fichier, "etiquette_url": res["etiquette_url"]}


@frappe.whitelist()
def mettre_en_attente(commande, commentaire):
    """Suspend le colis chez Aramex (il n'existe pas d'annulation dans l'API). Reserve aux
    responsables, commentaire obligatoire : c'est un geste qui se justifie."""
    frappe.only_for(list(ROLES_ATTENTE))
    if not (commentaire or "").strip():
        frappe.throw(_("Indiquez pourquoi le colis est mis en attente."))
    cfg = aramex_api.config()
    if _bloque_en_dev(cfg):
        frappe.throw(_("Site de développement : action sur un vrai colis refusée."))
    info = aramex_des_commandes([commande]).get(commande) or {}
    numero = info.get("bordereau")
    if not numero:
        frappe.throw(_("Aucun bordereau Aramex sur cette commande."))
    res = aramex_api.hold_shipment(numero, commentaire, cfg=cfg)
    if res.get("erreur"):
        frappe.throw(_("Aramex a refusé la mise en attente : {0}").format(res["erreur"]))
    frappe.get_doc({
        "doctype": "Comment", "comment_type": "Info",
        "reference_doctype": "Sales Order", "reference_name": commande,
        "content": _("⏸️ Colis Aramex {0} mis en attente chez le transporteur : {1}").format(
            numero, frappe.utils.escape_html(commentaire.strip())),
    }).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True, "bordereau": numero}


@frappe.whitelist()
def a_blanc(commande):
    """Le corps EXACT qui partirait, sans l'envoyer — pour relire adresse, telephone, montant
    sur des commandes reelles avant la premiere creation. Reserve aux responsables."""
    frappe.only_for(list(ROLES_ATTENTE))
    base = preparer(commande)
    cfg = aramex_api.config()
    colis = dict(base["colis"], product_group=cfg.get("product_group") or "DOM",
                 product_type=cfg.get("product_type") or "ONP")
    return {"motifs": base["motifs"],
            "expedition": construire_expedition(commande, _expediteur(cfg),
                                                base["destinataire"], colis, now_datetime())}
