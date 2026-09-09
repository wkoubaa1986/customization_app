"""
Encaissement des ANCIENNES DETTES depuis la caisse journalière.

LE PRINCIPE : ON NE RÉINVENTE RIEN
----------------------------------
Toute la mécanique existe déjà dans l'« Encaissement Paiement » (Outil
d'encaissement) et ses deux Server Scripts :

  - « generartion_list dette » (After Save) : à partir du PAIEMENT saisi
    (table `dette_client`), il répartit le montant sur les dettes du client en
    FIFO — les PE « Dette non payée » sur `Dettes - A&S`, plus les reliquats
    Aramex sans numéro de suivi — et remplit `dettes_a_encaisser` ;
  - « Traitement des encaissement » (After Submit) : il consomme ces dettes —
    réécrit l'échéancier des commandes (la ligne « Dette non payée » devient
    Espèces/Chèque, un RELIQUAT de dette est recréé si le paiement ne couvre
    pas tout), supprime les anciennes PE de dette et crée LE paiement
    (Espèces - A&S, ou Chèques - A&S en attente de remise pour un chèque).

Ce module est donc un simple FRONT : il fabrique le document, construit
l'allocation, LE VALIDE dans la foulée et rend le compte rendu à l'employé. Le
montant est plafonné à la somme des dettes du client — un trop-perçu n'a pas de
sens ici.

⚠️ EN UN SEUL GESTE, SANS BROUILLON (décision utilisateur 09/09/2026) : création
et validation partagent la même transaction. Un échec du Server Script rembobine
l'ensemble et ne laisse RIEN derrière — l'ancien enchaînement (créer, confirmer,
valider) semait un ENC en brouillon à chaque tentative ratée.
"""

import base64
import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

COMPTE_DETTES = "Dettes - A&S"
COMPTE_ARAMEX = "Livraison Aramex - A&S"
ROLES = ("System Manager", "Accounts Manager", "Accounts User",
         "Sales Manager", "Sales User")

# Même règle que « generartion_list dette » : un reliquat Aramex SANS numéro de
# suivi exploitable est une dette comme une autre ; avec un numéro, il attend
# sa remise Aramex et ne se traite pas ici.
_RX_SUIVI = re.compile(r"Aramex\s*N[^0-9]*[0-9]{6,}")

#: Les motifs qui interdisent d'encaisser une dette. Libellés en clair (pas de
#: `_()`) : ils traversent l'API vers le dialogue ET se testent hors site.
MOTIF_COMMANDE_ANNULEE = "commande annulée"
MOTIF_FACTURE_ANNULEE = "facture annulée"


def _motif_non_encaissable(documents):
    """Le motif qui interdit d'encaisser cette dette, et le document responsable.

    ⚠️ FONCTION PURE (aucune base) : c'est LA règle, et elle se teste telle quelle.

    `documents` : les pièces auxquelles la dette est attachée, dans l'ordre où on
    les nomme — `(doctype, nom, docstatus)`. Il y en a DEUX quand « Facturation
    Auto » est passé par là : la dette porte alors la FACTURE dans ses références
    tout en gardant la COMMANDE dans `reference_no`. L'une comme l'autre suffit à
    bloquer l'encaissement.
    → `(motif, nom du document en cause)` ; `("", "")` si la dette est encaissable.

    Une dette dont la commande — ou la facture — est ANNULÉE ne peut plus être
    encaissée : le paiement créé par « Traitement des encaissement » porterait un
    lien vers ce document annulé, et Frappe le refuse (`CancelledLinkError`,
    « Impossible de lier le document annulé ») APRÈS avoir supprimé des dettes et
    réécrit des échéanciers.

    Une dette sans document identifié reste encaissable : rien ne dit qu'elle est
    annulée, et le script sait la traiter par la référence de son paiement. Une
    commande encore en brouillon n'est pas annulée non plus — elle est seulement
    impropre au champ `bl`, ce dont `encaisser` se charge.
    """
    for doctype, nom, docstatus in documents:
        if not nom or not doctype:
            continue
        if cint(docstatus) == 2:
            return ((MOTIF_FACTURE_ANNULEE if doctype == "Sales Invoice"
                     else MOTIF_COMMANDE_ANNULEE), nom)
    return "", ""


#: Les deux refus opposables à une sélection de dettes, dans l'ordre où ils
#: s'appliquent : le détaillé d'abord, le générique ensuite.
REFUS_BLOQUEES = "bloquees"
REFUS_AUCUNE = "aucune"


def _trier_selection(toutes, selection):
    """Les dettes retenues pour l'encaissement, ou le refus à opposer à l'employé.

    ⚠️ FONCTION PURE (aucune base) : elle porte l'ORDRE des refus, et c'est lui
    qui se teste. Un client dont l'UNIQUE dette est annulée doit lire pourquoi —
    quelle dette, quel document — et non « aucune dette encaissable », qui ne
    nomme rien et laisse l'employé sans prise.

    `toutes` : les dettes du client (`_dettes`), chacune avec son `motif`.
    `selection` : les dettes cochées ; vide, l'employé prend tout l'encaissable.
    → `(choisies, refus)`, `refus` valant `None` ou `(code, dettes_en_cause)`.
    """
    par_nom = {r["name"]: r for r in toutes}
    encaissables = [r for r in toutes if not r["motif"]]
    # `par_nom` porte TOUTES les dettes, bloquées comprises : une dette annulée
    # explicitement cochée doit être REFUSÉE avec son motif, pas ignorée en silence.
    choisies = [par_nom[n] for n in selection if n in par_nom] or encaissables
    bloquees = [r for r in choisies if r["motif"]]
    if bloquees:
        return [], (REFUS_BLOQUEES, bloquees)
    if not choisies:
        return [], (REFUS_AUCUNE, [])
    return choisies, None


#: Les documents auxquels une dette peut être attachée, et le champ qui porte
#: leur date. L'ordre est celui de la recherche : une commande d'abord.
_DOCUMENTS = (("Sales Order", "transaction_date"), ("Sales Invoice", "posting_date"))


def _document(doctype, nom, champ_date):
    """La fiche minimale d'un document lié (`None` s'il n'existe pas).

    ⚠️ SEUL POINT DE LECTURE de `_qualifier` : les tests le remplacent pour jouer
    la qualification d'une dette sans ouvrir de base."""
    return frappe.db.get_value(doctype, nom, ["grand_total", champ_date, "docstatus"],
                               as_dict=True)


def _qualifier(r):
    """Attache à UNE dette sa commande (ou sa facture), puis son motif de blocage.

    Deux pièces sont interrogées, pas une : celle des références du paiement ET
    celle de `reference_no`. « Facturation Auto » recopie le paiement en gardant
    `reference_no` (la COMMANDE) mais remplace ses références par la FACTURE — la
    commande annulée d'une dette facturée passait alors inaperçue jusqu'à l'échec
    de la validation, la dette restant cochable dans le dialogue.
    """
    r.montant = flt(r.paid_amount, 3)
    # La commande vit dans les references ; à défaut, `reference_no` la porte
    # (patron du script « Traitement des encaissement »).
    r.commande = r.commande or (r.reference_no or "").strip()
    r.commande_doctype = ""
    r.commande_ttc = 0.0
    r.commande_date = ""
    r.commande_docstatus = None
    candidats = []
    if r.commande:
        for dt, champ_date in _DOCUMENTS:
            meta = _document(dt, r.commande, champ_date)
            if meta:
                r.commande_doctype = dt
                r.commande_ttc = flt(meta.grand_total, 3)
                r.commande_date = str(meta.get(champ_date) or "")
                r.commande_docstatus = cint(meta.docstatus)
                candidats.append((dt, r.commande, meta.docstatus))
                break
    # La commande d'origine, quand la dette a été facturée depuis (`reference_no`
    # reste la commande alors que les références portent la facture).
    origine = (r.reference_no or "").strip()
    if origine and origine != r.commande:
        meta = _document("Sales Order", origine, "transaction_date")
        if meta:
            candidats.append(("Sales Order", origine, meta.docstatus))
    r.motif, r.document_bloquant = _motif_non_encaissable(candidats)
    return r


def _dettes(client):
    """Les dettes du client, plus anciennes d'abord, chacune avec son `motif` —
    vide si elle est encaissable (voir `_motif_non_encaissable`)."""
    rows = frappe.db.sql(
        """
        SELECT pe.name, pe.paid_amount, pe.posting_date, pe.reference_no, pe.paid_to,
               pe.party_name,
               (SELECT per.reference_name FROM `tabPayment Entry Reference` per
                WHERE per.parent = pe.name ORDER BY per.idx LIMIT 1) AS commande
        FROM `tabPayment Entry` pe
        WHERE pe.docstatus = 1 AND pe.party_type = 'Customer' AND pe.party = %(client)s
          AND (pe.paid_to = %(dettes)s
               OR (pe.paid_to = %(aramex)s
                   AND IFNULL(pe.reference_no, '') NOT REGEXP 'Aramex[[:space:]]*N[^0-9]*[0-9]{6,}'))
        ORDER BY pe.posting_date, pe.creation
        """,
        {"client": client, "dettes": COMPTE_DETTES, "aramex": COMPTE_ARAMEX},
        as_dict=True,
    )
    for r in rows:
        _qualifier(r)
    # L'ordre des dettes suit la DATE DE LA COMMANDE (décision utilisateur 19/08) :
    # c'est elle qui dit l'ancienneté réelle — la dette n'est que son enregistrement.
    # Repli sur la date de la dette quand la commande n'en a pas.
    rows.sort(key=lambda r: (r.commande_date or str(r.posting_date), str(r.posting_date), r.name))
    return rows


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def recherche_client(doctype, txt, searchfield, start, page_len, filters):
    """Recherche d'un client par NOM ou par NUMÉRO DE TÉLÉPHONE (fiche client et
    contacts liés). Sert le champ Link du dialogue de la caisse."""
    return frappe.db.sql(
        """
        SELECT c.name, c.customer_name, IFNULL(c.mobile_no, '')
        FROM `tabCustomer` c
        WHERE c.disabled = 0
          AND (c.customer_name LIKE %(txt)s
               OR c.name LIKE %(txt)s
               OR IFNULL(c.mobile_no, '') LIKE %(txt)s
               OR EXISTS (
                   SELECT 1 FROM `tabContact` ct
                   JOIN `tabDynamic Link` dl ON dl.parent = ct.name
                        AND dl.parenttype = 'Contact'
                   WHERE dl.link_doctype = 'Customer' AND dl.link_name = c.name
                     AND (IFNULL(ct.mobile_no, '') LIKE %(txt)s
                          OR IFNULL(ct.phone, '') LIKE %(txt)s)))
        ORDER BY c.customer_name
        LIMIT %(start)s, %(page_len)s
        """,
        {"txt": f"%{txt}%", "start": start, "page_len": page_len},
    )


@frappe.whitelist()
def banques():
    """La MÊME liste déroulante de banques que l'outil d'encaissement (les options du
    champ banque de « Liste des Dettes client ») — une seule source, jamais deux listes."""
    frappe.only_for(ROLES)
    options = (frappe.get_meta("Liste des Dettes client")
               .get_field("banque").options or "").split("\n")
    return [b for b in options if b.strip()]


@frappe.whitelist()
def dettes_client(client):
    """Les dettes du client et leur commande, pour l'affichage du dialogue."""
    frappe.only_for(ROLES)
    rows = _dettes(client)
    banques = (frappe.get_meta("Liste des Dettes client")
               .get_field("banque").options or "").split("\n")
    return {
        # `encaissable`/`motif` : le dialogue grise la dette dont le document est
        # annulé plutôt que de laisser l'employé la cocher et échouer à la validation.
        "dettes": [{"paiement": r.name, "commande": r.commande,
                    "commande_doctype": r.commande_doctype, "commande_ttc": r.commande_ttc,
                    "commande_date": r.commande_date,
                    "date": str(r.posting_date), "montant": r.montant, "compte": r.paid_to,
                    "encaissable": not r.motif, "motif": r.motif,
                    "document_bloquant": r.document_bloquant}
                   for r in rows],
        "total": round(sum(r.montant for r in rows), 3),
        "banques": [b for b in banques if b.strip()],
    }


#: Les modes offerts par la caisse. « Traite bancaire » suit le circuit du chèque
#: (n° + banque + photo, compte d'attente « Traite Bancaire - A&S », remise ensuite).
MODES = ("Espèces", "Chèque", "Traite bancaire")

#: Un numéro de chèque tunisien porte 7 chiffres ; une traite n'a pas de format
#: unique — on exige des chiffres, de 4 à 20.
_RX_NUMERO = {"Chèque": r"\d{7}", "Traite bancaire": r"\d{4,20}"}


def _valider_paiements(paiements):
    """Contrôle chaque ligne de paiement et rend la liste normalisée.

    ⚠️ (N°, BANQUE) EST LA CLÉ D'APPARIEMENT DU SERVER SCRIPT « Traitement des
    encaissement » : deux chèques (ou deux traites) qui la partageraient seraient
    FUSIONNÉS par lui — un seul paiement créé pour deux papiers reçus. On refuse
    donc le doublon ici, avant que rien n'existe.
    """
    vus = set()
    normalises = []
    for i, p in enumerate(paiements, start=1):
        mode = (p.get("mode") or "").strip()
        montant = flt(p.get("montant"), 3)
        numero = (p.get("n_piece") or p.get("n_cheque") or "").strip()
        banque = (p.get("banque") or "").strip()
        if mode not in MODES:
            frappe.throw(_("Ligne {0} : mode d'encaissement inconnu ({1}).").format(i, mode))
        if montant <= 0:
            frappe.throw(_("Ligne {0} : le montant doit être positif.").format(i))
        if mode != "Espèces":
            libelle = _("chèque") if mode == "Chèque" else _("traite")
            if not re.fullmatch(_RX_NUMERO[mode], numero):
                attendu = (_("exactement 7 chiffres") if mode == "Chèque"
                           else _("4 à 20 chiffres"))
                frappe.throw(_("Ligne {0} : le numéro de {1} doit comporter {2} "
                               "(reçu : « {3} »).").format(i, libelle, attendu,
                                                           numero or _("vide")))
            # Décision utilisateur 2026-08-20 : la banque n'est obligatoire QUE pour
            # un chèque — une traite peut se saisir sans (le papier ne la porte pas
            # toujours lisiblement).
            if mode == "Chèque" and not banque:
                frappe.throw(_("Ligne {0} : pour un chèque, la banque est obligatoire.")
                             .format(i))
            if not p.get("photo"):
                frappe.throw(_("Ligne {0} : la photo du/de la {1} est obligatoire.")
                             .format(i, libelle))
            cle = (mode, numero, banque)
            if cle in vus:
                frappe.throw(_("Ligne {0} : le numéro {1} ({2}) est saisi deux fois pour "
                               "le même mode.").format(i, numero, banque))
            vus.add(cle)
        normalises.append({"mode": mode, "montant": montant, "numero": numero,
                           "banque": banque, "photo": p.get("photo"),
                           "photo_nom": p.get("photo_nom")})
    return normalises


def _verifier_photo(p):
    """Lit la photo du chèque / de la traite avec OpenAI et la confronte au saisi.

    -> liste d'avertissements (vide si tout concorde). ⚠️ JAMAIS BLOQUANT, décision
    utilisateur 2026-08-20 : une panne OpenAI, une photo illisible ou un désaccord
    n'empêchent pas l'encaissement — l'employé est averti, il tranche. Même
    plomberie que la classification des dépenses (`caisse_depenses._classifier`).
    """
    libelle = _("chèque") if p["mode"] == "Chèque" else _("traite")
    etiquette = "%s n°%s" % (libelle, p["numero"])
    # Un PDF ou tout autre non-image est accepté comme pièce jointe, mais le modèle
    # vision ne lit que des images — vu en prod le 20/08/2026 (400 « unsupported
    # MIME type application/pdf » maquillé en « service indisponible »).
    if not (p.get("photo") or "").startswith("data:image/"):
        return [_("{0} : la pièce jointe n'est pas une photo (PDF ?) — vérification "
                  "automatique impossible, contrôle à l'œil.").format(etiquette)]
    try:
        from bank_retenue_sync.ai.invoice_extract import _get_client_model_temp

        client_ia, model, _t = _get_client_model_temp()
        res = client_ia.responses.create(
            model=model,
            instructions=(
                "Tu lis la photo d'un chèque ou d'une traite (lettre de change) "
                "bancaire tunisien(ne). Réponds STRICTEMENT en JSON : "
                '{"montant": <montant en dinars lu en chiffres sur le document, '
                'null si illisible>, "numero": "<numéro du document, chiffres '
                'uniquement, null si illisible>", "lisible": <true si la photo '
                "montre bien un chèque ou une traite exploitable, false sinon>}."),
            input=[{"role": "user", "content": [
                {"type": "input_image", "image_url": p["photo"]},
                {"type": "input_text",
                 "text": "Lis le montant et le numéro de ce document (%s)." % libelle}]}])
        texte = (res.output_text or "").strip().strip("`")
        if texte.lower().startswith("json"):
            texte = texte.split("\n", 1)[1]
        lu = json.loads(texte)
    except Exception:
        frappe.log_error(title="Caisse : vérification photo indisponible",
                         message=frappe.get_traceback())
        return [_("{0} : la vérification automatique de la photo n'a pas pu être "
                  "faite (service indisponible).").format(etiquette)]

    return _comparer_photo(lu, p, etiquette, libelle)


def _comparer_photo(lu, p, etiquette, libelle):
    """Confronte la lecture du modèle au saisi → liste d'avertissements.

    ⚠️ FONCTION PURE ET TOLÉRANTE. Le modèle est censé rendre un objet JSON, mais
    rien ne l'y oblige : « null », « [] » ou un nombre passent `json.loads` sans
    broncher, et lire `.get` dessus lèverait une AttributeError — APRÈS que
    l'encaissement est enregistré. On n'exploite donc que ce qui est exploitable,
    et on le dit à l'employé plutôt que de casser.
    """
    if not isinstance(lu, dict):
        return [_("{0} : la lecture automatique n'a rien rendu d'exploitable — "
                  "contrôle à l'œil.").format(etiquette)]
    avert = []
    if not lu.get("lisible", True):
        avert.append(_("{0} : la photo semble illisible ou ne montre pas un {1}.")
                     .format(etiquette, libelle))
    montant_lu = lu.get("montant")
    if montant_lu is not None:
        try:
            montant_lu = flt(montant_lu, 3)
        except Exception:
            montant_lu = None
    if montant_lu and abs(montant_lu - p["montant"]) > 0.001:
        avert.append(_("{0} : montant saisi {1} ≠ montant lu sur la photo {2}.")
                     .format(etiquette, p["montant"], montant_lu))
    numero_lu = re.sub(r"\D", "", str(lu.get("numero") or ""))
    if numero_lu and p["numero"] and p["numero"] != numero_lu \
            and p["numero"] not in numero_lu and numero_lu not in p["numero"]:
        avert.append(_("{0} : numéro saisi {1} ≠ numéro lu sur la photo {2}.")
                     .format(etiquette, p["numero"], numero_lu))
    return avert


def _avertissements_photos(lignes_paiement):
    """Les avertissements de lecture des photos — JAMAIS une exception.

    ⚠️ APPELÉ APRÈS LE COMMIT : l'encaissement est enregistré, définitivement. Une
    panne ici (modèle, réseau, réponse inattendue) ne doit surtout pas ressembler à
    un échec : l'employé recommencerait, et le client paierait deux fois. Tout ce
    qui casse devient un avertissement.
    """
    avertissements = []
    for p in lignes_paiement:
        if p["mode"] == "Espèces" or not p.get("photo"):
            continue
        try:
            avertissements += _verifier_photo(p)
        except Exception:
            avertissements.append(
                _("La vérification automatique des photos n'a pas abouti — "
                  "l'encaissement est bien enregistré, contrôlez les pièces à l'œil."))
            try:
                frappe.log_error(title="Caisse : vérification photo en échec",
                                 message=frappe.get_traceback())
            except Exception:
                pass    # journaliser ne doit pas non plus faire échouer l'après-coup
    return avertissements


def _soumettre(doc):
    """Soumet l'encaissement — le script « Traitement des encaissement » consomme les
    dettes, réécrit les échéanciers (reliquat recréé si partiel) et crée le paiement.

    Un lien vers un document annulé fait tout échouer, et Frappe dit « Impossible de
    lier le document annulé » sans nommer l'encaissement ni dire quoi faire. On
    rembobine — le script a pu supprimer des dettes et réécrire des échéanciers avant
    de buter, RIEN ne doit rester à moitié écrit — et on nomme les deux.
    """
    try:
        doc.submit()
    except frappe.CancelledLinkError as e:
        frappe.db.rollback()
        frappe.throw(_("Encaissement {0} refusé : il faudrait relier un document annulé "
                       "({1}). Rien n'a été enregistré — retirez de la sélection la dette "
                       "qui porte ce document, ou faites-le rétablir.")
                     .format(doc.name, re.sub(r"<[^>]+>", " ", str(e)).strip()))


@frappe.whitelist()
def encaisser(client, montant=None, mode=None, n_cheque=None, banque=None, dettes=None,
              photo=None, photo_nom=None, paiements=None, soumettre=0):
    """Encaisse les dettes sélectionnées : construit le document, l'attache aux
    photos et LE VALIDE dans la foulée (`soumettre`), puis rend l'allocation obtenue.

    `paiements` : la liste des paiements reçus (JSON) — PLUSIEURS chèques et/ou
    traites et/ou espèces pour la même sélection de dettes, chacun avec son
    montant, son numéro, sa banque et sa photo. Les anciens arguments (`montant`,
    `mode`, `n_cheque`…) restent acceptés et valent une liste d'une seule ligne.
    `dettes` : les PE de dette SÉLECTIONNÉES par l'employé (JSON) — le FIFO par
    défaut du dialogue les coche toutes, mais il peut en écarter. L'allocation est
    construite ICI — dettes en FIFO par date de commande, paiements dans l'ordre
    de saisie — et le drapeau `custom_allocation_manuelle` empêche le Server
    Script de la régénérer.
    `soumettre` : validation immédiate, ce que demande le dialogue de la caisse.
    ⚠️ LE DÉFAUT RESTE 0 — un dialogue déjà ouvert au moment du déploiement appelle
    cette méthode SANS le paramètre, puis propose de confirmer ou d'annuler : s'il
    recevait un document déjà soumis, l'employé pourrait croire annuler une
    opération pourtant enregistrée (et `valider`/`abandonner` la refuseraient).
    L'ancien enchaînement en deux temps reste donc le contrat par défaut.
    """
    frappe.only_for(ROLES)
    if isinstance(paiements, str):
        paiements = json.loads(paiements)
    if not paiements:
        paiements = [{"mode": mode, "montant": montant, "n_piece": n_cheque,
                      "banque": banque, "photo": photo, "photo_nom": photo_nom}]
    lignes_paiement = _valider_paiements(paiements)
    total = round(sum(p["montant"] for p in lignes_paiement), 3)

    selection = json.loads(dettes) if isinstance(dettes, str) else (dettes or [])
    choisies, refus = _trier_selection(_dettes(client), selection)
    if refus and refus[0] == REFUS_BLOQUEES:
        # Le document nommé est CELUI QUI BLOQUE : pour une dette facturée, c'est
        # la commande d'origine, pas la facture affichée en face de la dette.
        details = ", ".join("{0} ({1} : {2})".format(
            r.name, r.motif, r.document_bloquant or r.commande) for r in refus[1])
        frappe.throw(_("Ces dettes ne peuvent pas être encaissées : {0}. Décochez-les — "
                       "un document annulé ne peut plus recevoir de paiement.")
                     .format(details))
    if refus:
        frappe.throw(_("Le client {0} n'a aucune dette encaissable.").format(client))
    total_selection = round(sum(r.montant for r in choisies), 3)
    if total > total_selection + 0.001:
        frappe.throw(_("Le total des paiements ({0}) dépasse la somme des dettes "
                       "sélectionnées ({1}) : sélectionnez plus de dettes ou réduisez "
                       "les montants.").format(total, total_selection))

    doc = frappe.new_doc("Encaissement Paiement")
    doc.custom_allocation_manuelle = 1
    for p in lignes_paiement:
        ligne = {"client": client, "type": p["mode"], "date": nowdate(),
                 "valeur_total": total_selection}
        if p["mode"] == "Espèces":
            ligne["espece"] = p["montant"]
        else:
            ligne.update({"valeur_du_cheque": p["montant"], "n_chèque": p["numero"],
                          "banque": p["banque"]})
        doc.append("dette_client", ligne)

    # Allocation en DOUBLE FIFO : les dettes par DATE DE COMMANDE (repli : date de
    # dette), les paiements dans l'ordre de saisie. Une dette couverte par deux
    # pièces donne DEUX lignes d'allocation — le Server Script rattache chaque
    # ligne à sa pièce par (n°, banque). Mêmes champs que ses lignes générées :
    # `valeur` = dette totale, `espece`/`valeur_du_cheque` = portion.
    file_paiements = [dict(p, reste=p["montant"]) for p in lignes_paiement]
    i_paiement = 0
    allocation = []
    for r in sorted(choisies,
                    key=lambda x: (x.commande_date or str(x.posting_date),
                                   str(x.posting_date), x.name)):
        reste_dette = r.montant
        # ⚠️ NE POSER `bl` QUE SUR UNE COMMANDE SOUMISE. Le champ est un lien : une
        # commande annulée le fait refuser à l'insert (« Impossible de lier le
        # document annulé »), et le script sait de toute façon retrouver la cible
        # par la référence du paiement quand `bl` est vide.
        bl = None
        if r.reference_no and cint(frappe.db.get_value(
                "Sales Order", r.reference_no, "docstatus")) == 1:
            bl = r.reference_no
        while reste_dette > 0.0005 and i_paiement < len(file_paiements):
            p = file_paiements[i_paiement]
            if p["reste"] <= 0.0005:
                i_paiement += 1
                continue
            portion = round(min(p["reste"], reste_dette), 3)
            p["reste"] = round(p["reste"] - portion, 3)
            reste_dette = round(reste_dette - portion, 3)
            row = {"ref_paiement": r.name, "emmeteur": r.party_name, "valeur": r.montant,
                   "bl": bl, "date": nowdate(), "type": p["mode"]}
            if p["mode"] == "Espèces":
                row["espece"] = portion
            else:
                row.update({"n_chèque": p["numero"], "banque": p["banque"],
                            "valeur_du_cheque": portion})
            doc.append("dettes_a_encaisser", row)
            allocation.append({"paiement": r.name, "commande": r.commande or "",
                               "montant": portion, "dette_totale": r.montant,
                               "mode": p["mode"],
                               "piece": ("%s - %s" % (p["numero"], p["banque"])
                                         if p["mode"] != "Espèces" else "")})
        if i_paiement >= len(file_paiements):
            break
    doc.insert()

    from frappe.utils.file_manager import save_file
    for p in lignes_paiement:
        if not p.get("photo"):
            continue
        contenu = p["photo"].split(",", 1)[-1]
        prefixe = "cheque" if p["mode"] == "Chèque" else "traite"
        save_file(p.get("photo_nom") or f"{prefixe}-{p['numero']}.jpg",
                  base64.b64decode(contenu), "Encaissement Paiement", doc.name,
                  is_private=1)

    # ⚠️ PAS DE BROUILLON (décision utilisateur 09/09/2026). L'encaissement se valide
    # d'un seul geste : le commit n'intervient qu'APRÈS la soumission. Si le
    # « Traitement des encaissement » échoue, Frappe rembobine tout et il ne reste
    # RIEN — ni brouillon à reprendre, ni photo, ni paiement à moitié créé. C'est
    # l'inverse de l'ancien enchaînement (insert + commit, puis validation séparée),
    # qui laissait un ENC en brouillon à chaque échec.
    if cint(soumettre):
        _soumettre(doc)

    frappe.db.commit()

    # Vérification OpenAI des photos (chèques et traites), APRÈS le commit : le
    # document existe déjà, une panne du modèle ne peut plus rien lui faire. Ce sont
    # des AVERTISSEMENTS, jamais un blocage — ils sont rendus à l'employé, qui
    # contrôle la pièce et annule l'encaissement lui-même s'il y a lieu.
    avertissements = _avertissements_photos(lignes_paiement)

    return {"name": doc.name, "allocation": allocation, "total_dettes": total_selection,
            "total_paiements": total, "restant": round(total_selection - total, 3),
            "avertissements": avertissements, "valide": bool(cint(soumettre))}


@frappe.whitelist()
def valider(name):
    """Soumet un brouillon resté en attente : encaissement créé avec `soumettre=0`,
    ou repris d'avant la validation immédiate (l'ENC en brouillon de la prod)."""
    frappe.only_for(ROLES)
    doc = frappe.get_doc("Encaissement Paiement", name)
    if doc.docstatus != 0:
        frappe.throw(_("{0} n'est plus un brouillon.").format(name))
    _soumettre(doc)
    frappe.db.commit()
    return {"name": doc.name}


@frappe.whitelist()
def abandonner(name):
    """Supprime un brouillon d'encaissement resté en plan — ceux d'avant la
    validation immédiate, ou créés avec `soumettre=0`. Un brouillon qui traîne
    fausse le prochain calcul de dettes."""
    frappe.only_for(ROLES)
    doc = frappe.get_doc("Encaissement Paiement", name)
    if doc.docstatus != 0:
        frappe.throw(_("{0} n'est plus un brouillon.").format(name))
    frappe.delete_doc("Encaissement Paiement", name, ignore_permissions=True)
    frappe.db.commit()
    return {"supprime": name}
