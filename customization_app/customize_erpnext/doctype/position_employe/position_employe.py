"""Un relevé de la position du téléphone d'un employé, pris au moment d'une action dans l'app.

Écrit par `customization_app.flotte_positions.enregistrer` seulement (jamais à la main) ; lu par le suivi terrain
pour dire qui est dans quel véhicule et où est un client sans position connue.
"""
from frappe.model.document import Document


class PositionEmploye(Document):
    pass
