"""Prix de vente sur les vérifications de stock (29/10/2026) : les fiches existantes reçoivent, ligne par ligne,
le tarif standard ACTUEL de l'article (approximation : le tarif du jour, pas celui du comptage) et leurs totaux
au prix de vente, pour que le bilan par stock (primes) couvre aussi l'historique."""
import frappe
from frappe.utils import flt


def execute():
    if not frappe.db.exists("DocType", "Verification Stock") or not frappe.db.has_column("Verification Stock Ligne", "prix_vente"):
        return
    frappe.db.sql("""UPDATE `tabVerification Stock Ligne` l JOIN `tabItem` i ON i.name = l.item_code
                     SET l.prix_vente = i.standard_rate, l.valeur_vente = ROUND(l.ecart * i.standard_rate, 3)
                     WHERE IFNULL(l.prix_vente, 0) = 0""")
    frappe.db.sql("""UPDATE `tabVerification Stock` v
                     SET v.valeur_ecarts_vente = (SELECT IFNULL(SUM(l.valeur_vente), 0) FROM `tabVerification Stock Ligne` l
                                                  WHERE l.parent = v.name AND l.compte = 1 AND ABS(l.ecart) > 0.000001),
                         v.valeur_manquants_vente = (SELECT IFNULL(SUM(-l.valeur_vente), 0) FROM `tabVerification Stock Ligne` l
                                                     WHERE l.parent = v.name AND l.compte = 1 AND l.ecart < -0.000001)""")
