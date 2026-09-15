"""Manifeste Aramex : la feuille signee par le coursier a l'enlevement, une par tournee.

Le portail et l'application PC d'Aramex numerotaient ces manifestes (502, 504…) ; l'API,
elle, n'en produit pas. Le document reprend donc la meme feuille (« Manifest Report ») :
numero, date, total colis / poids, une ligne par bordereau, signatures. Il se genere depuis
/manifeste-aramex en cochant les colis crees et pas encore enleves ; chaque Suivi Aramex
rattache (`Suivi Aramex.manifeste`) sort de la selection suivante.
"""

from frappe.model.document import Document


class ManifesteAramex(Document):
    pass
