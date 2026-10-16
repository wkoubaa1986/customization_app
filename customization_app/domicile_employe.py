"""Domicile des employés : UNE valeur, visible à deux endroits (demande du 16/10/2026).

- la fiche **Employee** (section « Domicile (suivi GPS et tournées) » : lien Google Maps, latitude, longitude) ;
- la table **« Points de départ particuliers »** de Config Optimisation Tournees — la source que lisent l'optimisation des
  tournées, le suivi GPS (nuits « au domicile », arrêts ignorés en sortie de circuit, véhicule attribué à celui chez qui il a
  dormi à ≤ 500 m) et l'onglet Présence Aquaworld (« Définir comme domicile » → `flotte_presence.fixer_domicile`).

Modifier l'un met l'autre à jour (hooks `on_update` des deux DocTypes, garde anti-boucle `frappe.flags.sync_domicile`) :
- Employee enregistré avec un lien ou des coordonnées → la ligne de la table est (ré)écrite ; vidé → la ligne est retirée ;
- Config Optimisation Tournees enregistrée → chaque fiche Employé reçoit sa ligne ; une fiche sans ligne est vidée.
"""

import frappe
from frappe.utils import flt

CONFIG = "Config Optimisation Tournees"
CHAMPS = ("custom_domicile_lien", "custom_domicile_latitude", "custom_domicile_longitude")


def _a_les_champs() -> bool:
    return frappe.db.has_column("Employee", "custom_domicile_latitude")


def fusionner_pure(lignes: list, employe: str, lat: float, lng: float, lien: str | None) -> list:
    """La table des points de départ avec la ligne de `employe` remplacée (ou retirée si pas de coordonnées). PURE.
    `lignes` = [{employe, lien, latitude, longitude}]."""
    out = [dict(r) for r in lignes if r.get("employe") != employe]
    if lat and lng:
        out.append({"employe": employe, "lien": lien or "https://www.google.com/maps?q=%.6f,%.6f" % (lat, lng), "latitude": flt(lat), "longitude": flt(lng)})
    return out


def depuis_employe(doc, method=None):
    """Hook Employee.on_update : la fiche → la table des points de départ."""
    if frappe.flags.get("sync_domicile") or not _a_les_champs():
        return
    avant = doc.get_doc_before_save()
    if avant and all(str(avant.get(c) or "") == str(doc.get(c) or "") for c in CHAMPS):
        return
    lat, lng, lien = flt(doc.custom_domicile_latitude), flt(doc.custom_domicile_longitude), (doc.custom_domicile_lien or "").strip()
    if lien and not (lat and lng):
        from customization_app.tournee_optimisation import resoudre_lien
        c = resoudre_lien(lien)
        if not c:
            frappe.throw("Le lien du domicile ne donne pas de coordonnées : collez un lien Google Maps ouvert sur le lieu.")
        lat, lng = c
        doc.db_set({"custom_domicile_latitude": lat, "custom_domicile_longitude": lng}, update_modified=False)
    cfg = frappe.get_single(CONFIG)
    lignes = [{"employe": r.employe, "lien": r.lien, "latitude": r.latitude, "longitude": r.longitude} for r in (cfg.get("departs") or [])]
    cfg.set("departs", fusionner_pure(lignes, doc.name, lat, lng, lien))
    cfg.flags.ignore_permissions = True
    frappe.flags.sync_domicile = True
    try:
        cfg.save()
    finally:
        frappe.flags.sync_domicile = False


def depuis_config(doc, method=None):
    """Hook Config Optimisation Tournees.on_update : la table → les fiches Employé (sans toucher `modified`)."""
    if frappe.flags.get("sync_domicile") or not _a_les_champs():
        return
    frappe.flags.sync_domicile = True
    try:
        regles = {r.employe: r for r in (doc.get("departs") or []) if r.employe}
        for e in frappe.get_all("Employee", fields=["name", *CHAMPS], limit_page_length=0):
            r = regles.get(e.name)
            cible = {"custom_domicile_lien": (r.lien or "https://www.google.com/maps?q=%.6f,%.6f" % (flt(r.latitude), flt(r.longitude))) if r else None,
                     "custom_domicile_latitude": flt(r.latitude) if r else 0, "custom_domicile_longitude": flt(r.longitude) if r else 0}
            if any(str(e.get(k) or "") != str(v or "") and not (k != "custom_domicile_lien" and flt(e.get(k)) == flt(v)) for k, v in cible.items()):
                frappe.db.set_value("Employee", e.name, cible, update_modified=False)
    finally:
        frappe.flags.sync_domicile = False
