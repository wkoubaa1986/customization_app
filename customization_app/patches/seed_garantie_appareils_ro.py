"""
Complète la liste des groupes « Sous garantie » avec les osmoseurs domestiques
et les pompes qui manquaient à l'amorçage initial.

Symptôme d'origine : deux bons de livraison portant le même osmoseur
(« RO domestique avec pompe ») affichaient la garantie pour l'un et pas pour
l'autre. La mention n'était en fait déclenchée que par un accessoire présent
dans le colis de l'un d'eux (un stérilisateur UV du groupe « Filtres UV »),
l'appareil lui-même n'étant couvert par aucun groupe.

Un patch Frappe ne s'exécute qu'une fois par site : cocher ici est donc sûr,
un décochage ultérieur depuis le Desk ne sera pas réverté.
"""

import frappe

CHAMP = "custom_sous_garantie"

GROUPES = (
    # Les osmoseurs domestiques — la cause du symptôme. « RO Consommables &
    # Kits d'entretien », quatrième enfant d'Osmoseurs Domestiques, reste
    # volontairement exclu : la mention dit « hors cartouches, filtres,
    # membranes et autres consommables », et ce groupe figure sur 3 067 BL.
    "RO domestique avec pompe",
    "RO domestique sans pompe",
    "RO flux direct",
    # Volontairement absents : « Pompes booster pour osmoseurs » et « Pompes
    # Volumétriques ». Ce sont des composants d'installation et non l'appareil
    # garanti ; les couvrir ajoutait la mention à 128 BL qui ne portent aucun
    # autre article sous garantie.
)


def execute():
    if not frappe.db.has_column("Item Group", CHAMP):
        return

    coches, absents = [], []
    for nom in GROUPES:
        if frappe.db.exists("Item Group", nom):
            frappe.db.set_value("Item Group", nom, CHAMP, 1, update_modified=False)
            coches.append(nom)
        else:
            absents.append(nom)

    frappe.db.commit()
    frappe.clear_cache()

    if absents:
        print(f"[seed_garantie_appareils_ro] groupes introuvables, ignorés : {', '.join(absents)}")
    print(f"[seed_garantie_appareils_ro] {len(coches)} groupe(s) ajouté(s) à la garantie.")
