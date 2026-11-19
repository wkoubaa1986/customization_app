# Copyright (c) 2026, Wassim and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class EchangeAramex(Document):
    """Un échange d'articles avec un client livré par Aramex. Ce document ne porte que la trace ; toute la logique
    (sortie de stock à l'envoi, entrée au retour, photo vérifiée) vit dans customization_app/echange_aramex.py."""
