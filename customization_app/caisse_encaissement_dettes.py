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
        "dettes": [{"paiement": r.name, "commande": r.commande, "date": str(r.posting_date),
                    "montant": r.montant, "compte": r.paid_to} for r in rows],
        "total": round(sum(r.montant for r in rows), 3),
        "banques": [b for b in banques if b.strip()],
    }


@frappe.whitelist()
def encaisser(client, montant, mode, n_cheque=None, banque=None):
    """Crée le BROUILLON d'encaissement et retourne l'allocation calculée par le
    script d'enregistrement, pour confirmation par l'employé. Rien n'est soumis ici."""
    frappe.only_for(ROLES)
    montant = flt(montant, 3)
    if montant <= 0:
        frappe.throw(_("Le montant doit être positif."))
    if mode not in ("Espèces", "Chèque"):
        frappe.throw(_("Mode d'encaissement inconnu : {0}.").format(mode))
    if mode == "Chèque" and not ((n_cheque or "").strip() and (banque or "").strip()):
        frappe.throw(_("Pour un chèque, le numéro et la banque sont obligatoires."))

    total = round(sum(r.montant for r in _dettes(client)), 3)
    if not total:
        frappe.throw(_("Le client {0} n'a aucune dette encaissable.").format(client))
    if montant > total + 0.001:
        frappe.throw(_("Le montant ({0}) dépasse la somme des dettes du client ({1}).")
                     .format(montant, total))

    doc = frappe.new_doc("Encaissement Paiement")
    ligne = {"client": client, "type": mode, "date": nowdate(), "valeur_total": total}
    if mode == "Espèces":
        ligne["espece"] = montant
    else:
        ligne.update({"valeur_du_cheque": montant, "n_chèque": (n_cheque or "").strip(),
                      "banque": (banque or "").strip()})
    doc.append("dette_client", ligne)
    # L'insert déclenche « generartion_list dette » (After Save), qui remplit
    # dettes_a_encaisser en FIFO. On relit le document pour rendre l'allocation.
    doc.insert()
    doc.reload()

    allocation = [{
        "paiement": r.ref_paiement, "commande": r.bl or "",
        "montant": flt(r.espece if mode == "Espèces" else r.valeur_du_cheque, 3)
                   or flt(r.get("valeur"), 3),
    } for r in (doc.dettes_a_encaisser or [])]
    frappe.db.commit()
    return {"name": doc.name, "allocation": allocation, "total_dettes": total,
            "restant": round(total - montant, 3)}


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
