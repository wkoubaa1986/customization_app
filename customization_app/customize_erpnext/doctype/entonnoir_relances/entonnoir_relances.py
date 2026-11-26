"""Entonnoir Relances : une ligne par jour, écrite par customization_app.entonnoir_relances (photo de 07:30 / 10:30 et
noter() des crons de la relance), lue par la page « Journaux des relances ». Créé le 25/11/2026 pour voir une baisse
des listes d'appel en 1 jour au lieu de 2 mois. Aucune logique ici : la ligne n'est jamais saisie à la main."""
from frappe.model.document import Document


class EntonnoirRelances(Document):
    pass
