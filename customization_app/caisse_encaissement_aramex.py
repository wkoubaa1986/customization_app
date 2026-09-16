"""
Encaissement des colis Aramex LIVRÉS depuis la caisse journalière (demande utilisateur
16/09/2026).

LE CYCLE D'UN CONTRE-REMBOURSEMENT
----------------------------------
À l'expédition, la commande reçoit un paiement d'ATTENTE : mode « Dette non payée », compte
« Livraison Aramex - A&S », libellé « Aramex N: <bordereau> » (posé par la caisse ou par
`aramex_expedition._poser_paiement_attente`). L'argent est chez le transporteur. Quand Aramex
reverse les fonds — par VIREMENT groupé le plus souvent, parfois par CHÈQUE ou TRAITE — ce
paiement d'attente doit devenir un vrai encaissement sur son compte :

    Virement        -> « STE430127B - Zitouna - A&S », mode Virement ;
    Chèque          -> « Chèques - A&S », mode Chèque (portefeuille, remise en banque ensuite) ;
    Traite bancaire -> « Traite Bancaire - A&S », mode Traite bancaire LC (idem).

CE QUE FAIT CE MODULE, ET CE QU'IL NE FAIT PAS
----------------------------------------------
Il liste les paiements d'attente dont le colis est LIVRÉ (Suivi Aramex `livre`), laisse
l'employé en cocher plusieurs et saisir UNE pièce (mode, n°, banque, date, photo), puis, pour
chaque colis : annule et supprime le paiement d'attente (convention maison, cf.
`retour_aramex`), recrée le même paiement — mêmes références, même montant — sur le compte du
mode choisi, et aligne la ligne « Dette non payée » du calendrier de paiement de la commande.

PLUSIEURS PIÈCES, CHACUNE AVEC SON MONTANT (demande utilisateur 16/09/2026). La remise se
répartit sur les colis cochés dans l'ordre (FIFO : colis dans l'ordre de la liste, pièces dans
l'ordre de saisie) — un colis peut être couvert par deux pièces, une pièce peut couvrir
plusieurs colis. Puis l'ÉCART :
  - total reçu < total des colis : le reste de chaque colis non couvert devient une DETTE
    RESTANTE du client (paiement « Dette non payée » sur Dettes - A&S, ligne d'échéancier
    « Dette non payée »), comme le reliquat de l'encaissement des dettes ;
  - total reçu > total des colis : l'excédent devient un AVOIR CLIENT — un paiement NON AFFECTÉ
    sur la fiche du client (mode et compte de la pièce), à imputer sur sa prochaine facture.
    Il faut que tous les colis cochés soient du même client, sinon on refuse.
Les frais qu'Aramex retient sur sa remise se traitent par la facture Aramex (bank_retenue_sync).

Il ne touche PAS aux colis qu'un brouillon d'« Encaissement Paiement » (préparé par
bank_retenue_sync à la détection d'un virement Aramex) revendique déjà : ils sont montrés
grisés, « en cours de rapprochement ».
"""

import base64
import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from customization_app.livraison_aramex import COMPTE_ARAMEX, DOCTYPE_SUIVI, reference_aramex

ROLES = ("System Manager", "Accounts Manager", "Accounts User",
         "Sales Manager", "Sales User")

MODE_ATTENTE = "Dette non payée"
CHAMP_NUMERO_ECHEANCIER = "custom__n_chèque__transaction"
COMPTE_DETTES = "Dettes - A&S"

#: Les trois modes de remise d'Aramex, avec le paiement qu'ils produisent.
#:   mode      : le Mode of Payment du nouveau paiement (et de la ligne d'échéancier) ;
#:   compte    : son compte `paid_to` ;
#:   papier    : une pièce physique entre en portefeuille -> photo obligatoire ;
#:   rx        : la forme du numéro ; banque_obligatoire : pour le chèque seulement
#:               (décision utilisateur 2026-08-20 pour les dettes, reprise ici).
MODES = {
    "Virement": {"mode": "Virement", "compte": "STE430127B - Zitouna - A&S", "papier": False,
                 "rx": r"[A-Za-z0-9/\-]{3,30}", "banque_obligatoire": False,
                 "libelle": "référence du virement", "attendu": "3 à 30 lettres ou chiffres"},
    "Chèque": {"mode": "Chèque", "compte": "Chèques - A&S", "papier": True,
               "rx": r"\d{7}", "banque_obligatoire": True,
               "libelle": "numéro de chèque", "attendu": "exactement 7 chiffres"},
    "Traite bancaire": {"mode": "Traite bancaire LC", "compte": "Traite Bancaire - A&S",
                        "papier": True, "rx": r"\d{4,20}", "banque_obligatoire": False,
                        "libelle": "numéro de traite", "attendu": "4 à 20 chiffres"},
}

#: Les refus opposables à une sélection (mêmes conventions que `caisse_encaissement_dettes`).
REFUS_DISPARUS = "disparus"
REFUS_EN_COURS = "en_cours"
REFUS_AUCUN = "aucun"


# ------------------------------------------------------------------ règles pures


def motif_refus_piece(mode, numero, banque, photo, dispense=False):
    """Pourquoi cette pièce ne peut pas être acceptée — "" si elle est bonne.

    ⚠️ FONCTION PURE (aucune base) : c'est LA règle de saisie, et elle se teste telle quelle.
    `dispense` : code de dispense vérifié -> la photo d'un chèque / d'une traite n'est plus exigée.
    """
    spec = MODES.get(mode or "")
    if not spec:
        return "mode de remise inconnu : %s" % (mode or "vide")
    numero = (numero or "").strip()
    if not re.fullmatch(spec["rx"], numero):
        return "le %s doit comporter %s (reçu : « %s »)" % (
            spec["libelle"], spec["attendu"], numero or "vide")
    if spec["banque_obligatoire"] and not (banque or "").strip():
        return "pour un chèque, la banque est obligatoire"
    if spec["papier"] and not photo and not dispense:
        return "la photo de la pièce (%s) est obligatoire, ou le code de dispense" % mode.lower()
    return ""


def libelle_reference(mode, numero, banque, bordereau):
    """Le `reference_no` du paiement recréé.

    Il COMMENCE par « Aramex N: <bordereau> » — `reference_aramex` continue de lire le colis,
    et la commande garde sa trace — puis nomme la pièce. La référence bancaire y figure en
    clair : c'est elle que l'identification bancaire retrouve en sous-chaîne (le lien est la
    clé bancaire, jamais le montant).
    """
    numero = (numero or "").strip()
    banque = (banque or "").strip()
    tete = "Aramex N: %s" % bordereau
    if mode == "Virement":
        return "%s / Virement reçu N: %s" % (tete, numero)
    piece = "Chèque N° %s" % numero if mode == "Chèque" else "Traite N° %s" % numero
    if banque:
        piece += " - %s" % banque
    return "%s / %s" % (tete, piece)


def lignes_echeancier_a_aligner(lignes, bordereau, montant):
    """Les lignes du calendrier de paiement à faire passer sur le mode de la remise.

    `lignes` : [{name, mode_of_payment, custom__n_chèque__transaction, payment_amount}].
    D'abord celles « Dette non payée » qui PORTENT le bordereau (ce que `_definir_bordereau`
    aligne à l'expédition) ; à défaut, celles « Dette non payée » du MONTANT exact du colis —
    une commande peut avoir été expédiée avant que le numéro ne soit posé. Jamais les autres :
    une avance en espèces sur la même commande n'a rien à voir avec la remise Aramex.
    """
    attente = [l for l in (lignes or []) if (l.get("mode_of_payment") or "") == MODE_ATTENTE]
    if not attente:
        return []
    avec_numero = [l for l in attente
                   if bordereau and bordereau in (l.get(CHAMP_NUMERO_ECHEANCIER) or "")]
    if avec_numero:
        return [l["name"] for l in avec_numero]
    return [l["name"] for l in attente
            if abs(_arrondi(l.get("payment_amount")) - _arrondi(montant)) <= 0.001]


def _arrondi(valeur):
    """round() et non flt(x, 3) : les fonctions pures tournent sans site."""
    return round(float(valeur or 0), 3)


def repartir(colis, paiements):
    """Répartit les pièces sur les colis, dans l'ordre — FONCTION PURE.

    `colis` : [{paiement, montant, client}] dans l'ordre de la liste ;
    `paiements` : [{montant, …}] dans l'ordre de saisie.
    -> {"allocations": [{colis, piece, portion}], "reliquats": [{colis, montant}],
        "excedents": [{piece, montant}]}
    Un colis partiellement couvert donne un reliquat (dette restante) ; ce qui reste des pièces
    une fois tous les colis couverts est un excédent (avoir client), pièce par pièce.
    """
    restes = [_arrondi(p.get("montant")) for p in paiements]
    allocations, reliquats = [], []
    i = 0
    for ic, c in enumerate(colis):
        reste_colis = _arrondi(c.get("montant"))
        while reste_colis > 0.0005 and i < len(restes):
            if restes[i] <= 0.0005:
                i += 1
                continue
            portion = _arrondi(min(restes[i], reste_colis))
            restes[i] = _arrondi(restes[i] - portion)
            reste_colis = _arrondi(reste_colis - portion)
            allocations.append({"colis": ic, "piece": i, "portion": portion})
        if reste_colis > 0.0005:
            reliquats.append({"colis": ic, "montant": reste_colis})
    excedents = [{"piece": j, "montant": r} for j, r in enumerate(restes) if r > 0.0005]
    return {"allocations": allocations, "reliquats": reliquats, "excedents": excedents}


def trier_selection(disponibles, selection):
    """(colis retenus, refus) — `refus` vaut None ou (code, en_cause).

    Comme pour les dettes : une sélection qui cite un colis absent de la liste veut dire que
    celle-ci a bougé (déjà encaissé ?) -> on refuse TOUT et on redemande une liste à jour.
    Un colis « en cours » (revendiqué par un brouillon d'encaissement) est refusé nommément.
    """
    par_nom = {c["paiement"]: c for c in disponibles}
    if not selection:
        return [], (REFUS_AUCUN, [])
    disparus = [n for n in selection if n not in par_nom]
    if disparus:
        return [], (REFUS_DISPARUS, disparus)
    en_cours = [n for n in selection if par_nom[n].get("en_cours")]
    if en_cours:
        return [], (REFUS_EN_COURS, en_cours)
    return [par_nom[n] for n in selection], None


# ------------------------------------------------------------------ lecture


def _en_cours_de_rapprochement():
    """Les paiements Aramex qu'un brouillon d'« Encaissement Paiement » revendique déjà
    (bank_retenue_sync prépare ce brouillon quand un virement Aramex paraît au relevé)."""
    if not frappe.db.exists("DocType", "Liste Aramex"):
        return set()
    rows = frappe.db.sql(
        """SELECT la.ref_paiement FROM `tabListe Aramex` la
           JOIN `tabEncaissement Paiement` ep ON ep.name = la.parent
           WHERE ep.docstatus = 0 AND IFNULL(la.ref_paiement, '') != ''""")
    return {r[0] for r in rows}


def _colis(tous=False):
    rows = frappe.db.sql(
        """SELECT pe.name, pe.party, pe.party_name, pe.paid_amount, pe.posting_date,
                  pe.reference_no, pe.mode_of_payment,
                  (SELECT per.reference_doctype FROM `tabPayment Entry Reference` per
                    WHERE per.parent = pe.name ORDER BY per.idx LIMIT 1) AS commande_doctype,
                  (SELECT per.reference_name FROM `tabPayment Entry Reference` per
                    WHERE per.parent = pe.name ORDER BY per.idx LIMIT 1) AS commande
           FROM `tabPayment Entry` pe
           WHERE pe.docstatus = 1 AND pe.payment_type = 'Receive'
             AND pe.paid_to = %(compte)s
             AND IFNULL(pe.reference_no, '') REGEXP 'Aramex[[:space:]]*N[^0-9]*[0-9]{6,}'
           ORDER BY pe.posting_date DESC, pe.name DESC""",
        {"compte": COMPTE_ARAMEX}, as_dict=True)
    numeros = {}
    for r in rows:
        r.bordereau = reference_aramex(r.reference_no) or ""
        if r.bordereau:
            numeros[r.bordereau] = True
    suivis = {}
    if numeros:
        for s in frappe.get_all(DOCTYPE_SUIVI,
                                filters={"name": ("in", list(numeros))},
                                fields=["name", "livre", "statut", "derniere_date",
                                        "retour_recu_le"]):
            suivis[s.name] = s
    en_cours = _en_cours_de_rapprochement()
    out = []
    for r in rows:
        s = suivis.get(r.bordereau) or {}
        if s.get("retour_recu_le"):
            continue        # colis revenu : son paiement d'attente est déjà traité ailleurs
        livre = bool(cint(s.get("livre")))
        if not tous and not livre:
            continue
        out.append({
            "paiement": r.name, "bordereau": r.bordereau,
            "client": r.party, "client_nom": r.party_name or r.party,
            "commande": r.commande or "", "commande_doctype": r.commande_doctype or "",
            "montant": flt(r.paid_amount, 3), "date_paiement": str(r.posting_date),
            "livre": livre, "statut": s.get("statut") or "",
            "livre_le": s.get("derniere_date") or "",
            "en_cours": r.name in en_cours,
        })
    return out


@frappe.whitelist()
def colis(tous=0):
    """Les colis Aramex encaissables : LIVRÉS par défaut, tous les paiements d'attente avec
    `tous=1` (un suivi peut manquer). -> {colis, total, banques}"""
    frappe.only_for(ROLES)
    from customization_app.caisse_encaissement_dettes import banques

    lignes = _colis(tous=bool(cint(tous)))
    return {"colis": lignes,
            "total": flt(sum(c["montant"] for c in lignes if not c["en_cours"]), 3),
            "banques": banques()}


# ------------------------------------------------------------------ écriture


def _valider_piece(mode, numero, banque, photo, date, dispense=False, montant=None):
    motif = motif_refus_piece(mode, numero, banque, photo, dispense)
    if motif:
        frappe.throw(_("Pièce refusée : {0}.").format(motif))
    try:
        jour = getdate(date or nowdate())
    except Exception:
        frappe.throw(_("Date de la remise invalide : {0}.").format(date))
    if jour > getdate(nowdate()):
        frappe.throw(_("La date de la remise ne peut pas être dans le futur."))
    return {"mode": mode, "numero": (numero or "").strip(), "banque": (banque or "").strip(),
            "photo": photo, "date": jour, "montant": _arrondi(montant)}


def _valider_pieces(paiements, dispense=False):
    """Les pièces de la remise, normalisées ; montant positif exigé, doublon (mode, n°, banque)
    refusé — deux pièces au même numéro seraient deux paiements portant la même référence."""
    if isinstance(paiements, str):
        paiements = json.loads(paiements or "[]")
    pieces, vus = [], set()
    for i, p in enumerate(paiements or [], start=1):
        pc = _valider_piece(p.get("mode"), p.get("n_piece") or p.get("numero"), p.get("banque"),
                            p.get("photo"), p.get("date"), dispense, p.get("montant"))
        pc["photo_nom"] = p.get("photo_nom")
        if pc["montant"] <= 0:
            frappe.throw(_("Paiement {0} : le montant doit être positif.").format(i))
        cle = (pc["mode"], pc["numero"], pc["banque"])
        if cle in vus:
            frappe.throw(_("Paiement {0} : la pièce {1} n° {2} est saisie deux fois.")
                         .format(i, pc["mode"], pc["numero"]))
        vus.add(cle)
        pieces.append(pc)
    if not pieces:
        frappe.throw(_("Ajoutez au moins un paiement."))
    return pieces


def _piece_libelle(piece):
    """« Traite N° 4521 - Amen Bank » / « Chèque N° 1234567 - BIAT » / « Virement N: FT… »."""
    return libelle_reference(piece["mode"], piece["numero"], piece["banque"], "x").split(" / ", 1)[1]


def _nouvelle_ligne_echeancier(modele, valeurs, idx):
    """Insère une ligne de calendrier de paiement à côté de `modele` (dict de la ligne
    existante), sans SO.save() — les Server Scripts régénéreraient les paiements."""
    row = frappe.get_doc({
        "doctype": "Payment Schedule", "parent": modele["parent"], "parenttype": "Sales Order",
        "parentfield": "payment_schedule", "idx": idx, "docstatus": 1,
        "invoice_portion": 0, "due_date": valeurs.get("due_date"),
        "mode_of_payment": valeurs.get("mode_of_payment"),
        "payment_amount": valeurs.get("payment_amount"),
        "base_payment_amount": valeurs.get("payment_amount"),
        "outstanding": valeurs.get("payment_amount"),
        "base_outstanding": valeurs.get("payment_amount"),
        CHAMP_NUMERO_ECHEANCIER: valeurs.get(CHAMP_NUMERO_ECHEANCIER),
        "custom_banque": valeurs.get("custom_banque"),
        "description": valeurs.get("description"),
    })
    row.flags.ignore_permissions = True
    row.db_insert()
    return row.name


def _aligner_echeancier(commande, bordereau, montant, portions, reliquat=0):
    """La ligne « Dette non payée » du colis devient les pièces de la remise ; s'il y a
    plusieurs portions ou un reliquat, des lignes sont ajoutées à côté. db_set / db_insert,
    jamais SO.save() (les Server Scripts « Generation payement » régénéreraient les paiements).
    `portions` : [(piece, portion)] dans l'ordre. -> noms des lignes touchées."""
    if not commande or not frappe.db.exists("Sales Order", commande):
        return []
    lignes = frappe.get_all("Payment Schedule",
                            filters={"parent": commande, "parenttype": "Sales Order"},
                            fields=["name", "parent", "idx", "mode_of_payment",
                                    CHAMP_NUMERO_ECHEANCIER, "payment_amount", "due_date"],
                            order_by="idx asc")
    noms = lignes_echeancier_a_aligner(lignes, bordereau, montant)
    if not noms:
        return []
    cible = next(l for l in lignes if l["name"] == noms[0])
    valeurs = []
    for piece, portion in portions:
        spec = MODES[piece["mode"]]
        valeurs.append({"mode_of_payment": spec["mode"], "payment_amount": portion,
                        "due_date": piece["date"], CHAMP_NUMERO_ECHEANCIER: piece["numero"],
                        "custom_banque": piece["banque"] or None,
                        "description": _("Remise Aramex du colis {0}").format(bordereau)})
    if reliquat > 0.0005:
        valeurs.append({"mode_of_payment": MODE_ATTENTE, "payment_amount": reliquat,
                        "due_date": nowdate(), CHAMP_NUMERO_ECHEANCIER: bordereau,
                        "custom_banque": None,
                        "description": _("Reste non couvert par la remise Aramex du colis {0}")
                        .format(bordereau)})
    touchees = []
    if valeurs:
        frappe.db.set_value("Payment Schedule", cible["name"], valeurs[0], update_modified=False)
        touchees.append(cible["name"])
        idx = cible["idx"]
        for v in valeurs[1:]:
            idx += 1
            # Décaler les lignes suivantes pour garder un ordre lisible.
            for l in lignes:
                if l["idx"] >= idx and l["name"] not in touchees:
                    frappe.db.set_value("Payment Schedule", l["name"], "idx", l["idx"] + 1,
                                        update_modified=False)
                    l["idx"] += 1
            touchees.append(_nouvelle_ligne_echeancier(cible, v, idx))
    return touchees


def _commandes_du_paiement(pe):
    """Les commandes (Sales Order) qu'un paiement d'attente couvre : ses références commande,
    plus celles des lignes de ses références facture. Sans doublon, dans l'ordre."""
    out = []
    for r in pe.references or []:
        if r.reference_doctype == "Sales Order" and r.reference_name not in out:
            out.append(r.reference_name)
        elif r.reference_doctype == "Sales Invoice":
            for so in frappe.get_all("Sales Invoice Item",
                                     filters={"parent": r.reference_name,
                                              "sales_order": ("!=", "")},
                                     pluck="sales_order", distinct=True):
                if so not in out:
                    out.append(so)
    return out


def _paiement_depuis(pe, montant):
    """Une copie du paiement d'attente ramenée à `montant` : mêmes références, allocations à
    l'échelle (le dernier reste absorbe l'arrondi)."""
    nouveau = frappe.copy_doc(pe)
    total_ref = _arrondi(sum(flt(r.allocated_amount) for r in nouveau.references))
    reste = _arrondi(montant)
    for i, r in enumerate(nouveau.references):
        if i == len(nouveau.references) - 1 or total_ref <= 0:
            r.allocated_amount = reste
        else:
            part = _arrondi(montant * flt(r.allocated_amount) / total_ref)
            r.allocated_amount = part
            reste = _arrondi(reste - part)
    nouveau.paid_amount = nouveau.received_amount = _arrondi(montant)
    if hasattr(nouveau, "custom_remise_en_banque"):
        nouveau.custom_remise_en_banque = None
    nouveau.flags.ignore_permissions = True
    return nouveau


def _convertir(colis_ligne, portions, reliquat):
    """Remplace le paiement d'attente d'UN colis par ses encaissements réels.

    `portions` : [(piece, portion)] — un paiement par portion (mode, compte, libellé de la
    pièce) ; `reliquat` : la part non couverte, qui devient une DETTE RESTANTE du client
    (« Dette non payée » sur Dettes - A&S, libellée par la commande — la forme que
    l'encaissement des dettes reconnaît). Le paiement d'attente est ANNULÉ PUIS SUPPRIMÉ
    (convention maison). Tout se joue dans la transaction de l'appel.
    """
    pe = frappe.get_doc("Payment Entry", colis_ligne["paiement"])
    if pe.docstatus != 1:
        frappe.throw(_("Le paiement {0} n'est pas validé.").format(pe.name))
    if pe.paid_to != COMPTE_ARAMEX:
        frappe.throw(_("Le paiement {0} n'est plus sur le compte {1} — il a déjà été "
                       "encaissé.").format(pe.name, COMPTE_ARAMEX))
    bordereau = reference_aramex(pe.reference_no) or colis_ligne.get("bordereau") or ""
    montant = flt(pe.paid_amount, 3)
    commandes = _commandes_du_paiement(pe)

    nouveaux = []
    for piece, portion in portions:
        spec = MODES[piece["mode"]]
        n = _paiement_depuis(pe, portion)
        n.mode_of_payment = spec["mode"]
        n.paid_to = spec["compte"]
        n.paid_to_account_currency = frappe.db.get_value(
            "Account", spec["compte"], "account_currency") or n.paid_to_account_currency
        n.posting_date = n.reference_date = piece["date"]
        n.reference_no = libelle_reference(piece["mode"], piece["numero"], piece["banque"],
                                           bordereau)
        nouveaux.append((piece, n))
    dette = None
    if reliquat > 0.0005:
        dette = _paiement_depuis(pe, reliquat)
        dette.mode_of_payment = MODE_ATTENTE
        dette.paid_to = COMPTE_DETTES
        dette.paid_to_account_currency = frappe.db.get_value(
            "Account", COMPTE_DETTES, "account_currency") or dette.paid_to_account_currency
        dette.posting_date = dette.reference_date = nowdate()
        dette.reference_no = commandes[0] if commandes else "Reliquat Aramex N: %s" % bordereau

    ancien = pe.name
    pe.flags.ignore_permissions = True
    pe.flags.ignore_links = True
    pe.cancel()
    pe.delete(ignore_permissions=True)

    crees = []
    for piece, n in nouveaux:
        n.insert(ignore_permissions=True)
        n.submit()
        crees.append({"nom": n.name, "piece": piece, "montant": flt(n.paid_amount, 3)})
    if dette is not None:
        dette.insert(ignore_permissions=True)
        dette.submit()

    echeancier = []
    for commande in commandes:
        echeancier += _aligner_echeancier(commande, bordereau, montant, portions, reliquat)

    detail = ", ".join("%s %s (%s)" % (_piece_libelle(c["piece"]),
                                       frappe.format_value(c["montant"], {"fieldtype": "Currency"}),
                                       c["nom"]) for c in crees)
    for commande in commandes:
        frappe.get_doc({
            "doctype": "Comment", "comment_type": "Info",
            "reference_doctype": "Sales Order", "reference_name": commande,
            "content": _("💰 Colis Aramex {0} encaissé par {1} : {2} — paiement d'attente {3} "
                         "remplacé.{4}{5}").format(
                bordereau, frappe.session.user, detail, ancien,
                _(" Reste non couvert : {0} en dette restante ({1}).").format(
                    frappe.format_value(reliquat, {"fieldtype": "Currency"}), dette.name)
                if dette is not None else "",
                _(" Calendrier de paiement aligné.") if echeancier else ""),
        }).insert(ignore_permissions=True)

    return {"paiement": ancien, "bordereau": bordereau,
            "commande": ", ".join(commandes) or colis_ligne.get("commande") or "",
            "client": colis_ligne.get("client_nom") or pe.party_name or pe.party,
            "montant": montant,
            "paiements": [{"nom": c["nom"], "montant": c["montant"],
                           "piece": _piece_libelle(c["piece"])} for c in crees],
            "reliquat": reliquat if dette is not None else 0,
            "dette": dette.name if dette is not None else "",
            "echeancier": len(echeancier)}


def _avoir(modele_pe, client, piece, montant, bordereaux):
    """L'excédent d'une pièce : un paiement NON AFFECTÉ sur le client (mode et compte de la
    pièce). C'est l'avoir : il s'impute sur la prochaine facture du client."""
    spec = MODES[piece["mode"]]
    n = frappe.get_doc({
        "doctype": "Payment Entry", "payment_type": "Receive", "party_type": "Customer",
        "party": client, "company": modele_pe.company,
        "paid_from": modele_pe.paid_from,
        "paid_from_account_currency": modele_pe.paid_from_account_currency,
        "paid_to": spec["compte"],
        "paid_to_account_currency": frappe.db.get_value("Account", spec["compte"],
                                                        "account_currency") or "TND",
        "mode_of_payment": spec["mode"], "posting_date": piece["date"],
        "reference_date": piece["date"], "paid_amount": montant, "received_amount": montant,
        "source_exchange_rate": 1, "target_exchange_rate": 1,
        "reference_no": "Avoir client — excédent de la remise Aramex (%s) / %s" % (
            ", ".join(bordereaux), _piece_libelle(piece)),
    })
    n.flags.ignore_permissions = True
    n.setup_party_account_field()
    n.set_missing_values()
    n.insert(ignore_permissions=True)
    n.submit()
    return n.name


@frappe.whitelist()
def encaisser(colis, paiements=None, mode=None, n_piece=None, banque=None, date=None,
              photo=None, photo_nom=None, code_sans_photo=None):
    """Encaisse les colis sélectionnés avec les pièces de la remise d'Aramex.

    `colis` : les paiements d'attente cochés (JSON, dans l'ordre de la liste) ;
    `paiements` : [{mode, montant, n_piece, banque, date, photo, photo_nom}] (JSON). Les anciens
    arguments (`mode`, `n_piece`…) valent une pièce unique du montant total sélectionné.
    Répartition FIFO (cf. `repartir`) ; reste -> dette restante ; excédent -> avoir client.
    """
    frappe.only_for(ROLES)
    from customization_app import caisse_pieces

    dispense = caisse_pieces.dispense_photo(code_sans_photo)
    selection = json.loads(colis) if isinstance(colis, str) else (colis or [])
    choisis, refus = trier_selection(_colis(tous=True), selection)
    if refus and refus[0] == REFUS_DISPARUS:
        frappe.throw(_("Ces colis ne sont plus dans la liste : {0}. Elle a changé depuis son "
                       "affichage — ils viennent peut-être d'être encaissés. RIEN n'a été "
                       "enregistré : rouvrez la fenêtre pour la recharger.")
                     .format(", ".join(str(n) for n in refus[1])))
    if refus and refus[0] == REFUS_EN_COURS:
        frappe.throw(_("Ces colis sont déjà dans un encaissement en brouillon (rapprochement "
                       "bancaire en cours) : {0}. Validez ou supprimez ce brouillon d'abord.")
                     .format(", ".join(str(n) for n in refus[1])))
    if refus:
        frappe.throw(_("Sélectionnez au moins un colis."))
    total_colis = _arrondi(sum(c["montant"] for c in choisis))

    if isinstance(paiements, str):
        paiements = json.loads(paiements or "[]")
    if not paiements:
        paiements = [{"mode": mode, "n_piece": n_piece, "banque": banque, "date": date,
                      "photo": photo, "photo_nom": photo_nom, "montant": total_colis}]
    pieces = _valider_pieces(paiements, dispense)
    total_pieces = _arrondi(sum(p["montant"] for p in pieces))

    plan = repartir(choisis, pieces)
    clients = {c["client"] for c in choisis}
    if plan["excedents"] and len(clients) > 1:
        frappe.throw(_("Le total reçu ({0}) dépasse le total des colis ({1}) et la sélection "
                       "mêle plusieurs clients : l'avoir ne saurait à qui aller. Réduisez les "
                       "montants ou ne cochez que les colis d'un seul client.")
                     .format(total_pieces, total_colis))

    modele = frappe.get_doc("Payment Entry", choisis[0]["paiement"])
    conversions = []
    for ic, c in enumerate(choisis):
        portions = [(pieces[a["piece"]], a["portion"]) for a in plan["allocations"]
                    if a["colis"] == ic]
        reliquat = next((r["montant"] for r in plan["reliquats"] if r["colis"] == ic), 0)
        conversions.append(_convertir(c, portions, reliquat))
    avoirs = []
    for e in plan["excedents"]:
        avoirs.append({"nom": _avoir(modele, next(iter(clients)), pieces[e["piece"]],
                                     e["montant"], [c["bordereau"] for c in choisis]),
                       "montant": e["montant"], "piece": _piece_libelle(pieces[e["piece"]])})

    # Photos : sur chaque paiement né de la pièce ; trace de la dispense s'il n'y en a pas.
    from frappe.utils.file_manager import save_file
    for i, p in enumerate(pieces):
        cibles = [x["nom"] for cv in conversions for x in cv["paiements"]
                  if x["piece"] == _piece_libelle(p)]
        cibles += [a["nom"] for a in avoirs if a["piece"] == _piece_libelle(p)]
        if p.get("photo"):
            contenu = base64.b64decode(p["photo"].split(",", 1)[-1])
            prefixe = {"Chèque": "cheque", "Traite bancaire": "traite"}.get(p["mode"], "remise")
            nom_fichier = p.get("photo_nom") or "%s-aramex-%s.jpg" % (prefixe, p["numero"])
            for nom in cibles:
                save_file(nom_fichier, contenu, "Payment Entry", nom, is_private=1)
        elif dispense and MODES[p["mode"]]["papier"]:
            for nom in cibles:
                caisse_pieces.tracer_dispense("Payment Entry", nom)
    frappe.db.commit()

    avertissements = caisse_pieces.avertissements([
        {"mode": p["mode"], "numero": p["numero"], "montant": p["montant"], "photo": p.get("photo")}
        for p in pieces])
    return {"conversions": conversions, "avoirs": avoirs,
            "reliquats": [{"bordereau": cv["bordereau"], "montant": cv["reliquat"],
                           "dette": cv["dette"]} for cv in conversions if cv["reliquat"]],
            "pieces": [{"mode": p["mode"], "numero": p["numero"], "banque": p["banque"],
                        "montant": p["montant"], "date": str(p["date"])} for p in pieces],
            "total_colis": total_colis, "total_pieces": total_pieces,
            "avertissements": avertissements}
