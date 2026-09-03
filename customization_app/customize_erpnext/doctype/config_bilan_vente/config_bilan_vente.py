# Copyright (c) 2026, Wassim koubaa and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ConfigBilanVente(Document):
    # La logique vit dans customization_app/bilan_vente.py (`regle()` / `set_regle()`).
    # Ce single ne porte que la règle de comptage, partagée avec l'onglet
    # « Partenaire Economiq » qui bâtit l'écriture de règlement sur ces chiffres.
    pass
