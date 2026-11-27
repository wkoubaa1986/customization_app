"""Le tiroir du collecteur d'après les caisses validées : date de départ et montant figé (27/11/2026).

Sa demande : « je veux en plus une valeur de ce que Nejib doit avoir en caisse espèces en remontant les caisses validées
[…] tu peux commencer à le calculer à partir de 2 jours avant ». Départ : le 25/11/2026 (le comptage à l'aveugle date du
24/11, le suivi « un paiement = une clôture » du 25/11). Le montant de départ est celui du soir du 24/11 d'après le
compte « Espèces » (caisse_tiroir.base_a_figer), FIGÉ dans Config Caisse : il ne se recalcule plus, même si une écriture
antérieure est saisie après coup. Ne touche à rien si la date est déjà posée."""
import frappe

DEPUIS = "2026-11-25"


def execute():
    from customization_app import caisse_tiroir as CT

    if CT.reglage_caisses()[0]:
        return
    base = CT.base_a_figer(frappe.utils.add_days(DEPUIS, -1))
    for champ, valeur in (("caisses_validees_depuis", DEPUIS), ("caisses_validees_base", base)):
        frappe.db.sql("DELETE FROM `tabSingles` WHERE doctype = 'Config Caisse' AND field = %s", champ)
        frappe.db.sql("INSERT INTO `tabSingles` (doctype, field, value) VALUES ('Config Caisse', %s, %s)", (champ, str(valeur)))
    frappe.clear_cache(doctype="Config Caisse")
    frappe.log_error(title="Tiroir du collecteur : départ des caisses validées",
                     message="Depuis le %s, montant de départ figé : %s DT (compte « Espèces » au soir de la veille, "
                             "moins les fonds gardés par les autres caisses)." % (DEPUIS, base))
    frappe.db.commit()
