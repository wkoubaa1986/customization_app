"""La marque « Outil de paiement » des commandes, restée à « 1 » après un encaissement, est remise à « 0 » (27/11/2026).

« Traitement des encaissement » pose cette marque sur la commande pour que « re-generate payment after sales order »
ne régénère pas les paiements de CET enregistrement. Elle devait être remise à zéro aussitôt, mais ne l'était qu'en
mémoire, après l'enregistrement : 103 commandes validées depuis le 01/10/2026 la portaient encore.

Jusqu'ici c'était sans effet sur la régénération (champ texte : « 1 » relu de la base n'égale pas l'entier 1 que le
script comparait). Le script corrigé reconnaît la marque relue (`cint`) — c'est ce qui répare « Régulariser » une
livraison partielle — et la remet lui-même à zéro. Sans ce patch, le PREMIER enregistrement de chacune de ces
commandes aurait sauté une régénération à tort. Les patchs passent avant la synchronisation des scripts : l'ordre
est le bon. Aucune écriture comptable, `modified` inchangé. Rejouable."""
import frappe


def execute():
    if not frappe.db.has_column("Sales Order", "custom_outil_de_paiement"):
        return
    frappe.db.sql("""update `tabSales Order` set custom_outil_de_paiement = '0'
                     where ifnull(custom_outil_de_paiement, '') not in ('', '0')""")
