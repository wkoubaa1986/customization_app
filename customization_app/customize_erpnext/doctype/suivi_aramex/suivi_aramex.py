"""Le dernier suivi connu d'un colis Aramex.

POURQUOI UN DOCTYPE ET PAS UN CACHE
------------------------------------
Premiere version : le suivi vivait dans le cache Redis. Il a disparu au premier `clear_cache` —
celui que declenche n'importe quel enregistrement de workspace, et chaque deploiement. L'ecran
serait donc reparti vide apres chaque mise en production, avec 1,9 seconde d'interrogation par
colis pour le remplir. Un suivi de livraison se consulte tous les jours : il doit survivre au
deploiement.

Le document garde aussi une trace : `modified` dit quand le colis a bouge pour la derniere fois,
ce qu'aucun cache ne saurait dire.
"""

from frappe.model.document import Document


class SuiviAramex(Document):
    pass
