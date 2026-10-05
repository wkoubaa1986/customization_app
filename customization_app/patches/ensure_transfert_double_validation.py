"""Transferts : trajets permis + double validation en va-et-vient (page Stock par entrepôt, 05/10/2026).

1. Deux champs sur Stock Entry : le côté qui doit répondre (« Au tour de » : Employé / Magasin) et
   l'historique des propositions (JSON). Libellés des champs du 02/10 mis au goût du va-et-vient.
2. Les demandes déjà en attente (brouillons Magasin → véhicule d'avant ce patch) entrent dans le circuit :
   la demande du Magasin est le 1er tour, au tour de l'employé.
3. Réglage Config Stock Entrepot, posé SEULEMENT s'il est vide (jamais écrasé ensuite) :
   Hall, Articles défectueux, et les trajets demandés le 05/10/2026 —
   Hall ↔ Magasin (immédiat, responsable) ; Magasin ↔ véhicule et véhicule ↔ Articles défectueux (double
   validation) ; Magasin → Articles défectueux (immédiat, pratique courante). Tout le reste est interdit,
   véhicule → Hall en premier. Écrit en base directement : la validation du réglage (vérifications, cibles)
   ne doit pas pouvoir faire échouer le migrate du déploiement.
"""

import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

CONFIG = "Config Stock Entrepot"
TRAJET = "Config Stock Entrepot Trajet"
HALL = "Hall - A&S"
DEFECTUEUX = "Articles defectueux - A&S"
TRAJETS = [
    ("Magasin", "Hall", 0),
    ("Hall", "Magasin", 0),
    ("Magasin", "Stock véhicule", 1),
    ("Stock véhicule", "Magasin", 1),
    ("Stock véhicule", "Articles défectueux", 1),
    ("Articles défectueux", "Stock véhicule", 1),
    ("Magasin", "Articles défectueux", 0),
]


def execute():
    _champs()
    _demandes_en_attente()
    _reglage()
    frappe.db.commit()


def _champs():
    create_custom_fields(
        {
            "Stock Entry": [
                {
                    "fieldname": "custom_validation_tour",
                    "label": "Au tour de",
                    "fieldtype": "Select",
                    "options": "\nEmployé\nMagasin",
                    "insert_after": "custom_validation_employe",
                    "read_only": 1,
                    "no_copy": 1,
                    "in_standard_filter": 1,
                    "module": "Customize erpnext",
                    "description": "Double validation : le côté qui doit accepter (ou changer) les quantités proposées.",
                },
                {
                    "fieldname": "custom_validation_historique",
                    "label": "Propositions (double validation)",
                    "fieldtype": "Long Text",
                    "insert_after": "custom_ecart_reception",
                    "read_only": 1,
                    "hidden": 1,
                    "no_copy": 1,
                    "allow_on_submit": 1,
                    "module": "Customize erpnext",
                },
            ]
        },
        ignore_validate=True,
    )
    libelles = {
        "custom_validation_employe": ("Employé du véhicule (double validation)",
                                      "Trajet en double validation : reste en brouillon jusqu’à l’accord de cet employé "
                                      "et d’un responsable magasin sur les quantités."),
        "custom_valide_par": ("Accord final par", None),
        "custom_valide_le": ("Accord final le", None),
        "custom_ecart_reception": ("Écarts (demandé → validé)", None),
    }
    for champ, (label, description) in libelles.items():
        nom = f"Stock Entry-{champ}"
        if frappe.db.exists("Custom Field", nom):
            frappe.db.set_value("Custom Field", nom, "label", label)
            if description:
                frappe.db.set_value("Custom Field", nom, "description", description)
    frappe.clear_cache(doctype="Stock Entry")


def _demandes_en_attente():
    for se in frappe.get_all("Stock Entry", filters={"docstatus": 0, "custom_validation_employe": ["is", "set"]},
                             fields=["name", "owner", "creation", "custom_validation_tour"]):
        if se.custom_validation_tour:
            continue
        lignes = {d.item_code: d.qty for d in frappe.get_all("Stock Entry Detail", filters={"parent": se.name},
                                                             fields=["item_code", "qty"], order_by="idx")}
        histo = [{"cote": "Magasin", "par": se.owner, "nom": frappe.utils.get_fullname(se.owner),
                  "le": str(se.creation)[:16], "lignes": lignes}]
        frappe.db.set_value("Stock Entry", se.name, {"custom_validation_tour": "Employé",
                                                     "custom_validation_historique": json.dumps(histo, ensure_ascii=False)},
                            update_modified=False)


def _reglage():
    if not frappe.db.exists("DocType", TRAJET):
        return
    for champ, wh in (("entrepot_hall", HALL), ("entrepot_defectueux", DEFECTUEUX)):
        if not frappe.db.get_single_value(CONFIG, champ) and frappe.db.exists("Warehouse", wh):
            frappe.db.set_single_value(CONFIG, champ, wh)
    if frappe.db.count(TRAJET, {"parent": CONFIG, "parenttype": CONFIG}):
        return
    for idx, (depuis, vers, double) in enumerate(TRAJETS, 1):
        frappe.get_doc({"doctype": TRAJET, "parent": CONFIG, "parenttype": CONFIG, "parentfield": "trajets",
                        "idx": idx, "depuis": depuis, "vers": vers, "double_validation": double}).db_insert()
