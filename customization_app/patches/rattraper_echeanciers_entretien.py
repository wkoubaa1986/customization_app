"""Données (15/10/2026) : des entretiens faits mais jamais inscrits dans l'échéancier. Trois causes, corrigées dans
update_schedule : la commande d'un client « Non » n'était jamais lue, une commande avec porte-filtre créait un doublon
« porte-filtre » et s'arrêtait là, une commande sortie de la fenêtre de 4 mois n'était jamais rattrapée. Résultat : ~190
commandes 2025-2026 sans visite marquée, des clients relancés puis requalifiés après un entretien.

1) les doublons porte-filtre sans visite réalisée sont retirés (trace sur le client) ;
2) les commandes livrées depuis 2025, postérieures à la dernière visite réalisée du client, sont rejouées sur les
   échéanciers existants (jamais de création). Rejouable sans effet une fois propre."""
import frappe

from customization_app.Maintenance import update_schedule as U


def execute():
    doublons = U.retirer_echeanciers_accessoires_en_double()
    print("rattraper_echeanciers_entretien : doublons porte-filtre retirés %(supprimes)s, erreurs %(erreurs)s" % doublons)
    resume = U.rattraper_commandes_entretien(depuis="2025-01-01")
    print("rattraper_echeanciers_entretien : %s" % resume["log"])
    frappe.db.commit()
