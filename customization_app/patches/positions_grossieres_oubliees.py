"""Les adresses qui ont appris une position RÉSEAU (grille 1/37°, bouton « 📍 Ma position » sans GPS précis) depuis
le lien d'une tâche : on l'oublie, pour qu'elles soient re-géocodées — et que les prochaines tâches du client ne
soient plus jugées « aucun passage » à 2 km du vrai point (Zahira Jrad 14/10/2026). 15/10/2026."""
import frappe


def execute():
    if not frappe.db.has_column("Address", "custom_latitude"):
        return
    from customization_app.tournee_optimisation import position_grossiere

    n = 0
    for a in frappe.get_all("Address", filters={"custom_geocode_source": "lien tâche", "custom_latitude": ["is", "set"]},
                            fields=["name", "custom_latitude", "custom_longitude"]):
        if position_grossiere((a.custom_latitude, a.custom_longitude)):
            # Les champs Float ne prennent pas NULL : 0 = « pas de position » partout (`_geocoder_adresse`, `coordonnees_tache`).
            frappe.db.set_value("Address", a.name, {"custom_latitude": 0, "custom_longitude": 0, "custom_geocode_source": None},
                                update_modified=False)
            n += 1
    print("positions grossières oubliées sur %d adresse(s)" % n)
