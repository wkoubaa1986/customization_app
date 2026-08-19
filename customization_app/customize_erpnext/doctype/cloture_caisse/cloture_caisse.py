# Copyright (c) 2026, Wassim koubaa and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ClotureCaisse(Document):
    # L'instantané FIGÉ d'une caisse validée : document soumis, PDF attaché.
    # La logique (état avant/après, contrôles, PDF) vit dans
    # customization_app/caisse_cloture.py.
    pass
