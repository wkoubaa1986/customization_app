"""Amorçage des DONNÉES du suivi GPS pour la prod (demande du 16/10/2026 : « tout est prêt pour migrer en prod avec les
données pour les domiciles et tout ? »). Posé UNE fois si le réglage est vide ; ensuite le réglage fait foi.

- Domiciles décidés par l'utilisateur le 16/10 d'après les nuits des véhicules (juin → octobre 2026) : Sadok, Akram (depuis
  son embauche, 29/07), Mohamed Hedi Chouchane (lieu A), Jamel (lieu B), Néjib (lieu C, à 830 m du Magasin). Écrits dans
  « Points de départ particuliers » de Config Optimisation Tournees via doc.save() → le hook remplit aussi la section
  « Domicile » des fiches Employé (domicile_employe.depuis_config).
- « Statistiques depuis le » = 01/06/2026 (Config Flotte GPS), période par défaut des trois onglets.
Après le déploiement, un clic sur « ♻️ Tout recalculer » (onglet Présence Aquaworld) reconstruit l'historique.
"""

import frappe

DOMICILES = {
    "HR-EMP-00001": (36.963242, 10.217246),   # Sadok Bouziri
    "HR-EMP-00010": (36.837018, 10.192400),   # Akram
    "HR-EMP-00006": (36.894600, 10.213800),   # Mohamed Hedi Chouchane (lieu A)
    "HR-EMP-00002": (36.887700, 10.318300),   # Jamel Aloui (lieu B)
    "HR-EMP-00003": (36.859900, 10.251400),   # Nejib Koubaa (lieu C)
}
STATS_DEPUIS = "2026-06-01"


def execute():
    frappe.reload_doc("customize_erpnext", "doctype", "config_optimisation_tournees")
    frappe.reload_doc("customize_erpnext", "doctype", "config_optimisation_tournees_depart")
    frappe.reload_doc("customize_erpnext", "doctype", "config_flotte_gps")
    cfg = frappe.get_single("Config Optimisation Tournees")
    if not (cfg.get("departs") or []):
        lignes = []
        for employe, (lat, lng) in DOMICILES.items():
            if frappe.db.exists("Employee", employe):
                lignes.append({"employe": employe, "lien": "https://www.google.com/maps?q=%.6f,%.6f" % (lat, lng), "latitude": lat, "longitude": lng})
        if lignes:
            cfg.set("departs", lignes)
            cfg.flags.ignore_permissions = True
            cfg.save()                                   # on_update → fiches Employé (section Domicile)
    # ⚠️ get_single_value d'un Date à NULL rend « 0001-01-01 » (vrai) : passer par stats_depuis(), qui ignore l'an 1 —
    # en prod le 16/10 la date n'avait pas été posée pour cette raison (posée à la main ensuite).
    from customization_app.flotte_gps import stats_depuis
    if not stats_depuis():
        frappe.db.set_single_value("Config Flotte GPS", "stats_date_debut", STATS_DEPUIS)
    frappe.db.commit()
