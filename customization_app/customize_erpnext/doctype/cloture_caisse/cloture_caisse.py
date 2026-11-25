# Copyright (c) 2026, Wassim koubaa and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ClotureCaisse(Document):
    # L'instantané FIGÉ d'une caisse validée : document soumis, PDF attaché.
    # La logique (état avant/après, contrôles, PDF) vit dans
    # customization_app/caisse_cloture.py.

    def on_trash(self):
        # Comptage annulé (supprimé) : ses paiements redeviennent libres — le prochain comptage les reprend
        # (25/11/2026, caisse_paiements).
        from customization_app.caisse_paiements import delier
        delier(self.name)

    def on_cancel(self):
        from customization_app.caisse_paiements import delier
        delier(self.name)
