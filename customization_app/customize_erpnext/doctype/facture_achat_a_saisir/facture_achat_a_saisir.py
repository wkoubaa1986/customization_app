# Copyright (c) 2026, Wassim koubaa and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class FactureAchataSaisir(Document):
    # File d'attente des factures d'achat capturées en caisse (photo + métadonnées),
    # à transformer en vraies Purchase Invoice par le comptable. La logique vit dans
    # customization_app/caisse_depenses.py.
    pass
