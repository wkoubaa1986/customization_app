"""Dossier de réparation d'un osmoseur déposé à l'atelier.

Le cycle de vie (réception → planification → réparée → rendue) est piloté par
`customization_app.reparation_osmoseur` : la fiche ne porte que les données, les
transitions passent par l'écran « Réparation osmoseurs » et par la clôture des
tâches de travail liées.
"""

import frappe
from frappe.model.document import Document


class MachineReparation(Document):
    def validate(self):
        if not self.tel and self.client:
            self.tel = telephone_client(self.client)


def telephone_client(client: str) -> str:
    """Le champ « Liste Telephone » du client d'abord (une ligne par numéro), mobile_no sinon."""
    liste, mobile = frappe.db.get_value("Customer", client, ["custom_liste_telephone", "mobile_no"]) or ("", "")
    numeros = [n.strip() for n in (liste or "").replace("\\n", "\n").splitlines() if n.strip()]
    return " / ".join(numeros) if numeros else (mobile or "")
