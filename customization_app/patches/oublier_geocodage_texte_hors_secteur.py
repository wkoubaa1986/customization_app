"""Géocodage par texte hors secteur (16/10/2026) : Nominatim lisait « Cité Ennasr, Ariana » à Mateur (51 km). Les positions
« texte ≈ » à plus de 30 km du centre de leur secteur sont oubliées (latitude / longitude à 0, source « texte ≈ (hors
secteur) ») : la tâche retombe sur le centre du secteur, et le géocodage ne les retentera pas. Les adresses Hors Secteur ou
sans secteur ne sont pas touchées."""

import frappe


def execute():
    if not frappe.db.has_column("Address", "custom_latitude"):
        return
    from customization_app.tournee_optimisation import TEXTE_HORS_SECTEUR, _centres_secteurs, plausible
    centres = _centres_secteurs()
    rows = frappe.get_all("Address", filters={"custom_geocode_source": ["like", "texte%"], "custom_latitude": ["!=", 0]},
                          fields=["name", "custom_latitude", "custom_longitude", "custom_secteur"], limit_page_length=0)
    n = 0
    for a in rows:
        if not plausible((a.custom_latitude, a.custom_longitude), a.custom_secteur, centres):
            frappe.db.set_value("Address", a.name, {"custom_latitude": 0, "custom_longitude": 0, "custom_geocode_source": TEXTE_HORS_SECTEUR},
                                update_modified=False)
            n += 1
    frappe.db.commit()
    print("géocodage texte hors secteur oublié :", n)
