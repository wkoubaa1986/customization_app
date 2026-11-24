import frappe
from frappe.model.document import Document


class AccordDepenseCaisse(Document):
    """Demande d'accord avant une dépense en espèces au-delà du seuil de Config Caisse (24/11/2026).
    Voir customization_app.caisse_accord."""
    pass
