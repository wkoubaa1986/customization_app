"""Zones du magasin sur deux niveaux (espace > zone), un article dans plusieurs zones.

- crée la table `custom_zones_magasin` (Table MultiSelect) sur l'article ; « Emplacement Magasin »
  (texte) passe en lecture seule et affiche les noms complets des zones joints par « / » ;
- crée les espaces « Magasin » et « Hall » ;
- reprend l'existant (texte libre) avec des règles simples, corrigeables ensuite depuis la page :
  * un morceau « Hall » désigne l'espace Hall ; « Hall - J2-J3 » = zone J2-J3 du Hall ;
  * « Sedda » (la mezzanine) n'apparaît qu'avec Hall : c'est une zone du Hall, même seule ;
  * sans espace nommé, le code est une zone du Magasin (« A 4 » -> « Magasin - A4 ») ;
  * « Hall » seul range l'article directement dans l'espace Hall ;
  ex. « V-3 / Hall / Sedda » -> « Hall - V3 » + « Hall - Sedda » ;
- supprime les zones d'une reprise antérieure (sans espace, plus utilisées).
Écritures directes (pas d'Item.save : articles anciens, validations lourdes). Idempotent.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from customization_app.customize_erpnext.doctype.zone_magasin.zone_magasin import (LIAISON, SEPARATEUR,
                                                                                nom_complet, normaliser_code)

TEXTE = "custom_emplacement_magasin"
TABLE = "custom_zones_magasin"
ESPACES = ("Magasin", "Hall")
DANS_LE_HALL = ("sedda",)


def _espace(nom):
    if not frappe.db.exists("Zone Magasin", nom):
        frappe.get_doc({"doctype": "Zone Magasin", "est_espace": 1, "code": nom}).insert(ignore_permissions=True)
    elif not frappe.db.get_value("Zone Magasin", nom, "est_espace"):
        frappe.db.set_value("Zone Magasin", nom, {"est_espace": 1, "espace": None, "code": nom, "libelle": nom})
    return nom


def _zone(espace, code):
    nom = nom_complet(espace, code)
    existe = frappe.db.sql("select name, espace from `tabZone Magasin` where lower(name) = lower(%s)", nom)
    if existe:
        if not existe[0][1]:                    # même nom qu'une zone à plat d'une reprise antérieure
            frappe.db.set_value("Zone Magasin", existe[0][0], {"espace": espace, "code": code, "est_espace": 0})
        return existe[0][0]
    frappe.get_doc({"doctype": "Zone Magasin", "espace": espace, "code": code}).insert(ignore_permissions=True)
    return nom


def interpreter(texte: str) -> list[tuple]:
    """Texte libre -> [(espace, code ou None)] ; code None = rangé dans l'espace lui-même."""
    morceaux = [m.strip() for m in str(texte or "").split("/") if m.strip()]
    bas = [m.lower() for m in morceaux]
    hall = any(b == "hall" or b.startswith("hall -") or b in DANS_LE_HALL for b in bas)
    espace = "Hall" if hall else "Magasin"
    out = []
    for m, b in zip(morceaux, bas):
        if b == "hall":
            continue
        code = m[len("hall -"):].strip() if b.startswith("hall -") else m
        code = normaliser_code(code)
        if code and (espace, code.lower()) not in {(e, (c or "").lower()) for e, c in out}:
            out.append((espace, code))
    return out or ([(espace, None)] if hall else [])


def execute():
    if not frappe.db.exists("DocType", "Zone Magasin") or not frappe.db.exists("DocType", "Article Zone Magasin"):
        return
    create_custom_fields({"Item": [{
        "fieldname": TABLE, "fieldtype": "Table MultiSelect", "label": "Zones magasin", "options": "Article Zone Magasin",
        "insert_after": TEXTE, "module": "Customize erpnext",
        "description": "Un article peut être rangé dans plusieurs zones (Stock > Zones & sorties d’articles)."}]},
        ignore_validate=True)
    if frappe.db.exists("Custom Field", "Item-" + TEXTE):
        cf = frappe.get_doc("Custom Field", "Item-" + TEXTE)
        if not cf.read_only:
            cf.read_only = 1
            cf.description = "Rempli à partir des zones magasin de l'article."
            cf.save(ignore_permissions=True)

    for e in ESPACES:
        _espace(e)
    for item, texte in frappe.db.sql(f"select name, {TEXTE} from tabItem where ifnull({TEXTE}, '') != ''"):
        if frappe.db.exists("Article Zone Magasin", {"parent": item, "parenttype": "Item"}):
            continue                                            # déjà repris
        voulues = [_espace(e) if c is None else _zone(e, c) for e, c in interpreter(texte)]
        for i, z in enumerate(voulues, 1):
            frappe.get_doc({"doctype": "Article Zone Magasin", "parent": item, "parenttype": "Item",
                            "parentfield": TABLE, "idx": i, "zone": z}).db_insert()
        frappe.db.set_value("Item", item, TEXTE, SEPARATEUR.join(voulues) or None, update_modified=False)

    # Reprise antérieure (zones à plat, sans espace) : supprimées une fois inutilisées.
    for z in frappe.get_all("Zone Magasin", filters={"est_espace": 0}, fields=["name", "espace"]):
        if not z.espace and not frappe.db.exists("Article Zone Magasin", {"zone": z.name}):
            frappe.delete_doc("Zone Magasin", z.name, ignore_permissions=True, force=True)
    frappe.clear_cache(doctype="Item")
    frappe.db.commit()
