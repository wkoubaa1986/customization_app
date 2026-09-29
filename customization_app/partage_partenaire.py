"""
Partage d'une « Liste Appelle Entretien » avec le compte partenaire (Economiq).

À la création de la liste, le partenaire reçoit la liste, les échéanciers, les
clients, leurs contacts et adresses (lecture + écriture) et leurs commandes
(lecture seule) ; à la validation, tout lui est retiré.

Ce module remplace deux Server Scripts (« partager liste appelle avec
partenaire », After Insert ; « enlever pa », After Submit). Ils faisaient
`insert(ignore_permissions=True)` / `delete_doc(ignore_permissions=True)` sur
des DocShare — or DocShare vérifie À PART l'autorisation « Partager » de
l'utilisateur courant sur chaque document cible (Customer, Contact, Sales
Order…), que `ignore_permissions` ne couvre pas. Seuls les System Manager
passaient ; Salma (Maintenance Manager) prenait « Vous devez avoir
l'autorisation de "Partager" » à la validation (29/09/2026). Ici les partages
sont posés et retirés avec `ignore_share_permission` : c'est une règle métier
de la liste, pas un geste de l'utilisateur.
"""

import frappe
from frappe.share import add_docshare, get_share_name

from customization_app.api import PARTNER_USER

DOCTYPE = "Liste Appelle Entretien"
SANS_CONTROLE = {"ignore_share_permission": True}


def documents_lies(doc, get_all=None, get_value=None, exists=None, echeancier_doctype=None):
    """[(doctype, name, {"read","write","submit"})] à partager pour cette liste.

    Les accès sont injectables pour les tests. `submit` n'est jamais décidé ici
    (None) : `add_docshare` le déduit du DocType (cf. `perms_pour`).
    """
    get_all = get_all or frappe.get_all
    get_value = get_value or frappe.db.get_value
    exists = exists or frappe.db.exists
    echeancier_doctype = echeancier_doctype or _echeancier_doctype()

    vus = set()
    out = []

    def ajouter(doctype, name, write=1):
        if not name or (doctype, name) in vus:
            return
        vus.add((doctype, name))
        out.append((doctype, name, {"read": 1, "write": write}))

    ajouter(DOCTYPE, doc.name)
    for row in doc.get("clients") or []:
        ajouter(echeancier_doctype, row.get("échéancier_dentretien"))
        client = row.get("client")
        if not client or not exists("Customer", client):
            continue
        ajouter("Customer", client)
        for l in get_all("Dynamic Link",
                         filters={"link_doctype": "Customer", "link_name": client,
                                  "parenttype": ["in", ["Contact", "Address"]]},
                         fields=["parent", "parenttype"], limit_page_length=1000):
            ajouter(l["parenttype"], l["parent"])
        ajouter("Contact", get_value("Customer", client, "customer_primary_contact"))
        ajouter("Address", get_value("Customer", client, "customer_primary_address"))
        for so in get_all("Sales Order", filters={"customer": client, "docstatus": ["<", 2]},
                          pluck="name", limit_page_length=1000):
            ajouter("Sales Order", so, write=0)     # commandes en lecture seule
    return out


def perms_pour(doctype, perms, is_submittable):
    """Droits effectifs d'un partage : `submit` suit l'écriture sur un DocType soumettable."""
    write = int(bool(perms.get("write")))
    return {"read": 1, "write": write, "submit": int(bool(write and is_submittable))}


def _echeancier_doctype():
    return frappe.get_meta("Appelle Client").get_field("échéancier_dentretien").options


def partager(doc, method=None):
    """after_insert : le partenaire voit la liste et tout ce qu'il faut pour appeler."""
    for doctype, name, perms in documents_lies(doc):
        if get_share_name(doctype, name, PARTNER_USER, 0):
            continue
        p = perms_pour(doctype, perms, frappe.get_meta(doctype).is_submittable)
        add_docshare(doctype, name, user=PARTNER_USER, read=p["read"], write=p["write"],
                     submit=p["submit"], share=0, everyone=0, flags=SANS_CONTROLE, notify=0)


def retirer_partages(doc, method=None):
    """on_submit : la liste est close, le partenaire n'a plus accès à rien."""
    for doctype, name, _perms in documents_lies(doc):
        if not frappe.db.exists(doctype, name):
            continue
        share_name = get_share_name(doctype, name, PARTNER_USER, 0)
        if share_name:
            frappe.delete_doc("DocShare", share_name, flags=SANS_CONTROLE, ignore_permissions=True)
