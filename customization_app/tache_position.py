"""Le lien « Maps » à montrer sur une tâche de travail qui n'a pas de lien Google Map (demande du 18/11/2026).

« Sur la tâche de travail, il y a des cas où je ne vois pas le lien Google Map, or quand j'ouvre [l'adresse] je
trouve latitude et longitude. »

Pourquoi le champ est vide : le « Lien Google Map » de la tâche est le lien EXACT du client — celui de son adresse,
ou la position relevée par le technicien à la clôture. Beaucoup d'adresses n'en ont pas ; elles ont seulement une
position « texte ≈ », calculée d'après l'adresse écrite pour ordonner les tournées : approximative (un quartier, une
rue), pas le point du client.

Ce module ne remplit donc PAS le champ de la tâche avec cette position : le lien de la tâche est recopié sur
l'adresse (cloture_tache.propager_google_map_vers_adresse) et dispense le technicien de relever sa position — y
écrire une position approximative la ferait passer pour exacte, pour toujours. Il rend seulement un lien À MONTRER
sous le champ : la position de l'adresse ouverte dans Maps, dite « approximative » quand elle l'est.
"""
from __future__ import annotations

import frappe

SOURCE_APPROXIMATIVE = "texte"       # custom_geocode_source : « texte ≈ », « texte ≈ (lien mort) »…


def est_un_lien(texte) -> bool:
    return str(texte or "").strip().lower().startswith(("http://", "https://"))


def lien_des_coordonnees(latitude, longitude) -> str:
    """Le point dans Google Maps. PURE."""
    return "https://www.google.com/maps?q=%.6f,%.6f" % (float(latitude), float(longitude))


def lien_a_montrer(lien_tache=None, lien_adresse=None, latitude=None, longitude=None, source=None):
    """Ce que l'écran montre sous « Lien Google Map ». PURE.

    -> None s'il n'y a rien à montrer, sinon {url, origine, approximatif} avec `origine` :
       « tache »   : le lien de la tâche elle-même (on le rend cliquable) ;
       « adresse » : la tâche n'a pas de lien mais son adresse en a un (il sera repris à l'enregistrement) ;
       « position »: ni l'une ni l'autre, mais l'adresse a une latitude et une longitude."""
    if est_un_lien(lien_tache):
        return {"url": str(lien_tache).strip(), "origine": "tache", "approximatif": False}
    if est_un_lien(lien_adresse):
        return {"url": str(lien_adresse).strip(), "origine": "adresse", "approximatif": False}
    try:
        lat, lng = float(latitude or 0), float(longitude or 0)
    except (TypeError, ValueError):
        return None
    if not lat or not lng:
        return None
    return {"url": lien_des_coordonnees(lat, lng), "origine": "position",
            "approximatif": str(source or "").strip().lower().startswith(SOURCE_APPROXIMATIVE)}


@frappe.whitelist()
def position(adresse=None, lien_tache=None):
    """Le lien à montrer pour une tâche dont l'adresse est `adresse` et le lien `lien_tache` (peut être vide)."""
    frappe.has_permission("Tache de travail", "read", throw=True)
    a = None
    if adresse and frappe.db.exists("Address", adresse):
        a = frappe.db.get_value("Address", adresse, ["custom_lien_google_map", "custom_latitude", "custom_longitude",
                                                     "custom_geocode_source"], as_dict=True)
    a = a or frappe._dict()
    return lien_a_montrer(lien_tache, a.get("custom_lien_google_map"), a.get("custom_latitude"),
                          a.get("custom_longitude"), a.get("custom_geocode_source"))
