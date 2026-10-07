"""Packs « Entretien 2 ans » 7 étapes A (Alcalin + Minéral) : AJ, EP et V n'avaient PAS leurs 2 F-T33-M.

Constat du 07/10/2026 : P-E-2-an-7-A-DF porte bien F-T33-M × 2, mais P-E-2-an-7-A-AJ / -EP / -V s'arrêtaient à
F-T33-A × 2 — ils étaient identiques au pack 6-A. Leurs BL ne sortaient donc pas les minéraux du stock (3 + 11 + 3
ventes depuis 2025). Correction demandée par l'utilisateur : on ajoute la ligne manquante, calquée sur celle de -DF
(article, quantité, unité), juste après le T33-C. Les BL déjà validés ne sont pas touchés.

Idempotent : un pack qui porte déjà F-T33-M est laissé tel quel.
"""
import frappe

PACKS = ("P-E-2-an-7-A-AJ", "P-E-2-an-7-A-EP", "P-E-2-an-7-A-V")
REFERENCE = "P-E-2-an-7-A-DF"
MINERAL = "F-T33-M"


def _ligne_modele():
    if frappe.db.exists("Product Bundle", REFERENCE):
        for it in frappe.get_doc("Product Bundle", REFERENCE).items:
            if it.item_code == MINERAL:
                return {"item_code": MINERAL, "qty": it.qty, "uom": it.uom, "description": it.description}
    return {"item_code": MINERAL, "qty": 2, "uom": frappe.db.get_value("Item", MINERAL, "stock_uom")}


def execute():
    if not frappe.db.exists("Item", MINERAL):
        return
    modele = _ligne_modele()
    for nom in PACKS:
        if not frappe.db.exists("Product Bundle", nom):
            continue
        doc = frappe.get_doc("Product Bundle", nom)
        if any(it.item_code == MINERAL for it in doc.items):
            continue
        lignes = [it.as_dict() for it in doc.items]
        pos = next((i + 1 for i, it in enumerate(lignes) if it.item_code == "F-T33-C"), len(lignes))
        doc.set("items", [])
        for i, it in enumerate(lignes[:pos] + [modele] + lignes[pos:]):
            doc.append("items", {k: it.get(k) for k in ("item_code", "qty", "uom", "description")})
        doc.flags.ignore_permissions = True
        doc.save()
        doc.add_comment("Info", "Ajout de {0} × {1} (pack 7 étapes A sans minéral, aligné sur {2}) — patch du "
                                "07/10/2026".format(frappe.utils.flt(modele["qty"]), MINERAL, REFERENCE))
