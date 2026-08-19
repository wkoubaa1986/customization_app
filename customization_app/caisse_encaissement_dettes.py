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
        if r.commande:
            for dt in ("Sales Order", "Sales Invoice"):
                ttc = frappe.db.get_value(dt, r.commande, "grand_total")
                if ttc is not None:
                    r.commande_doctype = dt
                    r.commande_ttc = flt(ttc, 3)
                    break
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
                    "date": str(r.posting_date), "montant": r.montant, "compte": r.paid_to}
                   for r in rows],
        "total": round(sum(r.montant for r in rows), 3),
        "banques": [b for b in banques if b.strip()],
    }


@frappe.whitelist()
def encaisser(client, montant, mode, n_cheque=None, banque=None, dettes=None,
              photo=None, photo_nom=None):
    """Crée le BROUILLON d'encaissement et retourne l'allocation, pour confirmation.

    `dettes` : les PE de dette SÉLECTIONNÉES par l'employé (JSON) — le FIFO par
    défaut du dialogue les coche toutes, mais il peut en écarter. L'allocation est
    construite ICI, en FIFO par date sur la sélection, et le drapeau
    `custom_allocation_manuelle` empêche le Server Script de la régénérer.
    `photo` : la photo du chèque (dataURL), OBLIGATOIRE pour un chèque — attachée
    au document. Rien n'est soumis ici.
    """
    frappe.only_for(ROLES)
    montant = flt(montant, 3)
    if montant <= 0:
        frappe.throw(_("Le montant doit être positif."))
    if mode not in ("Espèces", "Chèque"):
        frappe.throw(_("Mode d'encaissement inconnu : {0}.").format(mode))
    n_cheque = (n_cheque or "").strip()
    if mode == "Chèque":
        # Convention tunisienne : un numéro de chèque porte 7 chiffres.
        if not re.fullmatch(r"\d{7}", n_cheque):
            frappe.throw(_("Le numéro de chèque doit comporter exactement 7 chiffres "
                           "(reçu : « {0} »).").format(n_cheque or "vide"))
        if not (banque or "").strip():
            frappe.throw(_("Pour un chèque, la banque est obligatoire."))
        if not photo:
            frappe.throw(_("Pour un chèque, la photo du chèque est obligatoire."))

    toutes = _dettes(client)
    if not toutes:
        frappe.throw(_("Le client {0} n'a aucune dette encaissable.").format(client))
    selection = json.loads(dettes) if isinstance(dettes, str) else (dettes or [])
    par_nom = {r.name: r for r in toutes}
    choisies = [par_nom[n] for n in selection if n in par_nom] or toutes
    total_selection = round(sum(r.montant for r in choisies), 3)
    if montant > total_selection + 0.001:
        frappe.throw(_("Le montant ({0}) dépasse la somme des dettes sélectionnées ({1}) : "
                       "sélectionnez plus de dettes ou réduisez le montant.")
                     .format(montant, total_selection))

    doc = frappe.new_doc("Encaissement Paiement")
    doc.custom_allocation_manuelle = 1
    ligne = {"client": client, "type": mode, "date": nowdate(), "valeur_total": total_selection}
    if mode == "Espèces":
        ligne["espece"] = montant
    else:
        ligne.update({"valeur_du_cheque": montant, "n_chèque": n_cheque,
                      "banque": (banque or "").strip()})
    doc.append("dette_client", ligne)

    # Allocation FIFO (par date) sur la SÉLECTION — mêmes champs que les lignes du
    # Server Script : `valeur` = dette totale, `espece`/`valeur_du_cheque` = portion.
    reste = montant
    allocation = []
    for r in sorted(choisies, key=lambda x: (str(x.posting_date), x.name)):
        if reste <= 0.0005:
            break
        portion = round(min(reste, r.montant), 3)
        reste = round(reste - portion, 3)
        bl = r.reference_no if r.reference_no and frappe.db.exists(
            "Sales Order", r.reference_no) else None
        row = {"ref_paiement": r.name, "emmeteur": r.party_name, "valeur": r.montant,
               "bl": bl, "date": nowdate(), "type": mode}
        if mode == "Espèces":
            row["espece"] = portion
        else:
            row.update({"n_chèque": n_cheque, "banque": (banque or "").strip(),
                        "valeur_du_cheque": portion})
        doc.append("dettes_a_encaisser", row)
        allocation.append({"paiement": r.name, "commande": r.commande or "",
                           "montant": portion, "dette_totale": r.montant})
    doc.insert()

    if photo:
        contenu = photo.split(",", 1)[-1]
        from frappe.utils.file_manager import save_file
        save_file(photo_nom or f"cheque-{n_cheque}.jpg", base64.b64decode(contenu),
                  "Encaissement Paiement", doc.name, is_private=1)

    frappe.db.commit()
    return {"name": doc.name, "allocation": allocation, "total_dettes": total_selection,
            "restant": round(total_selection - montant, 3)}


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
