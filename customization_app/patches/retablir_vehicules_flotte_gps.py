"""Suivi GPS : les deux véhicules retirés par erreur le 10/11/2026 reviennent dans Config Flotte GPS. Joué une fois.

Ce jour-là, la procédure de départ d'Akram et de Mohamed Hedi a supprimé leurs lignes du réglage — et avec elles les
véhicules eux-mêmes (une ligne = un véhicule, son boîtier et son conducteur habituel). Depuis, la page « Suivi des
techniciens » ne montrait plus que la 5957 TU 232, et ni journées ni statistiques n'étaient enregistrées pour les deux
autres. Les boîtiers sont toujours actifs sur la plateforme (vérifié le 20/11/2026).

Les véhicules reviennent SANS conducteur habituel : le suivi les attribue de lui-même (arrêts chez les clients, lieu
de la nuit), et le réglage permet de désigner leur conducteur. Un boîtier déjà présent n'est pas touché. La procédure
de départ, elle, garde désormais le véhicule et n'efface que le nom (depart_employe.LIGNES_A_GARDER).
"""
import frappe

CONFIG, TABLE = "Config Flotte GPS", "Config Flotte GPS Vehicule"
#: (libellé d'avant le retrait — celui des journées enregistrées jusqu'au 09/11/2026 —, boîtier)
VEHICULES = (("261 TU 3647 Changan New Star", "861032062184305"),
             ("261 TU 3554 Cenntro Logistar 100 (électrique)", "356153596769338"))


def manquants(presents, vehicules=VEHICULES) -> list:
    """Les véhicules dont le boîtier n'est pas (ou plus) au réglage. PURE."""
    connus = {str(c or "").strip() for c in presents}
    return [(v, c) for v, c in vehicules if c not in connus]


def execute():
    if not frappe.db.exists("DocType", TABLE):
        return
    presents = frappe.get_all(TABLE, filters={"parent": CONFIG, "parenttype": CONFIG}, pluck="cbox")
    if not presents:
        return                              # réglage vide : un site sans flotte, rien à rétablir
    a_remettre = manquants(presents)
    if not a_remettre:
        return
    doc = frappe.get_single(CONFIG)
    for vehicule, cbox in a_remettre:
        doc.append("vehicules", {"vehicule": vehicule, "cbox": cbox, "actif": 1})
    doc.flags.ignore_permissions = True
    doc.flags.ignore_mandatory = True
    doc.save()
    frappe.clear_document_cache(CONFIG, CONFIG)
