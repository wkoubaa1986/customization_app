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

Ce module est donc un simple FRONT : il fabrique le document avec UNE ligne de
paiement, laisse le script d'enregistrement calculer l'allocation, la montre à
l'employé pour confirmation, puis soumet. Le montant est plafonné à la somme
des dettes du client — un trop-perçu n'a pas de sens ici.
"""

import base64
import json
import re

import frappe
from frappe import _
from frappe.utils import flt, nowdate

COMPTE_DETTES = "Dettes - A&S"
COMPTE_ARAMEX = "Livraison Aramex - A&S"
ROLES = ("System Manager", "Accounts Manager", "Accounts User",
         "Sales Manager", "Sales User")

# Même règle que « generartion_list dette » : un reliquat Aramex SANS numéro de
# suivi exploitable est une dette comme une autre ; avec un numéro, il attend
# sa remise Aramex et ne se traite pas ici.
_RX_SUIVI = re.compile(r"Aramex\s*N[^0-9]*[0-9]{6,}")


def _dettes(client):
    """Les dettes encaissables du client, plus anciennes d'abord."""
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
        r.montant = flt(r.paid_amount, 3)
        # La commande vit dans les references ; à défaut, `reference_no` la porte
        # (patron du script « Traitement des encaissement »).
        r.commande = r.commande or (r.reference_no or "").strip()
        r.commande_doctype = ""
        r.commande_ttc = 0.0
        r.commande_date = ""
        if r.commande:
            for dt, champ_date in (("Sales Order", "transaction_date"),
                                   ("Sales Invoice", "posting_date")):
                meta = frappe.db.get_value(dt, r.commande, ["grand_total", champ_date],
                                           as_dict=True)
                if meta:
                    r.commande_doctype = dt
                    r.commande_ttc = flt(meta.grand_total, 3)
                    r.commande_date = str(meta.get(champ_date) or "")
                    break
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
        "dettes": [{"paiement": r.name, "commande": r.commande,
                    "commande_doctype": r.commande_doctype, "commande_ttc": r.commande_ttc,
                    "commande_date": r.commande_date,
                    "date": str(r.posting_date), "montant": r.montant, "compte": r.paid_to}
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


@frappe.whitelist()
def encaisser(client, montant=None, mode=None, n_cheque=None, banque=None, dettes=None,
              photo=None, photo_nom=None, paiements=None):
    """Crée le BROUILLON d'encaissement et retourne l'allocation, pour confirmation.

    `paiements` : la liste des paiements reçus (JSON) — PLUSIEURS chèques et/ou
    traites et/ou espèces pour la même sélection de dettes, chacun avec son
    montant, son numéro, sa banque et sa photo. Les anciens arguments (`montant`,
    `mode`, `n_cheque`…) restent acceptés et valent une liste d'une seule ligne.
    `dettes` : les PE de dette SÉLECTIONNÉES par l'employé (JSON) — le FIFO par
    défaut du dialogue les coche toutes, mais il peut en écarter. L'allocation est
    construite ICI — dettes en FIFO par date de commande, paiements dans l'ordre
    de saisie — et le drapeau `custom_allocation_manuelle` empêche le Server
    Script de la régénérer. Rien n'est soumis ici.
    """
    frappe.only_for(ROLES)
    if isinstance(paiements, str):
        paiements = json.loads(paiements)
    if not paiements:
        paiements = [{"mode": mode, "montant": montant, "n_piece": n_cheque,
                      "banque": banque, "photo": photo, "photo_nom": photo_nom}]
    lignes_paiement = _valider_paiements(paiements)
    total = round(sum(p["montant"] for p in lignes_paiement), 3)

    toutes = _dettes(client)
    if not toutes:
        frappe.throw(_("Le client {0} n'a aucune dette encaissable.").format(client))
    selection = json.loads(dettes) if isinstance(dettes, str) else (dettes or [])
    par_nom = {r.name: r for r in toutes}
    choisies = [par_nom[n] for n in selection if n in par_nom] or toutes
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
        bl = r.reference_no if r.reference_no and frappe.db.exists(
            "Sales Order", r.reference_no) else None
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

    frappe.db.commit()

    # Vérification OpenAI des photos (chèques et traites), APRÈS le commit : le
    # brouillon existe déjà, une panne du modèle ne peut plus rien lui faire.
    avertissements = []
    for p in lignes_paiement:
        if p["mode"] != "Espèces" and p.get("photo"):
            avertissements += _verifier_photo(p)

    return {"name": doc.name, "allocation": allocation, "total_dettes": total_selection,
            "total_paiements": total, "restant": round(total_selection - total, 3),
            "avertissements": avertissements}


@frappe.whitelist()
def valider(name):
    """Soumet le brouillon : le script « Traitement des encaissement » consomme les
    dettes, réécrit les échéanciers (reliquat recréé si partiel) et crée le paiement."""
    frappe.only_for(ROLES)
    doc = frappe.get_doc("Encaissement Paiement", name)
    if doc.docstatus != 0:
        frappe.throw(_("{0} n'est plus un brouillon.").format(name))
    doc.submit()
    frappe.db.commit()
    return {"name": doc.name}


@frappe.whitelist()
def abandonner(name):
    """L'employé a refermé le dialogue sans confirmer : le brouillon ne doit pas
    rester (sa clé consommerait les dettes aux yeux du prochain calcul)."""
    frappe.only_for(ROLES)
    doc = frappe.get_doc("Encaissement Paiement", name)
    if doc.docstatus != 0:
        frappe.throw(_("{0} n'est plus un brouillon.").format(name))
    frappe.delete_doc("Encaissement Paiement", name, ignore_permissions=True)
    frappe.db.commit()
    return {"supprime": name}
