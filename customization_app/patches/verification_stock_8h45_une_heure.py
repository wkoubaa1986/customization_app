"""Vérification de stock : une heure à partir de 8 h 45 (sa demande du 20/11/2026).

Le réglage de prod disait autre chose (sur la copie du 18/11/2026 : 09:00, « 1 heure, 30 min ») et les tâches déjà
posées au calendrier gardaient l'ancien créneau. Ce patch, une fois :
1. met le réglage à 08:45 pour chaque stock et la durée à « 1 heure » (Config Stock Entrepot) — écrit champ par
   champ : le réglage refuse une sauvegarde complète tant qu'une ligne vise un stock sans employé actif ;
2. remet à 08:45 – 09:45, LE MÊME JOUR, toutes les tâches de vérification de stock encore ouvertes : celles de la
   planification (« 🧾 Vérification stock … ») et les anciennes « Vérification Stock nizar ».

La tâche « 📦 Départ de … » (vérification FINALE d'un départ : compter, fermer, transférer — 2 heures) n'est pas
une vérification ordinaire : elle n'est pas touchée. `modified` des tâches n'est pas modifié.
"""
import frappe
from frappe.utils import add_to_date, get_datetime, getdate

TACHE = "Tache de travail"
LIGNE = "Config Stock Entrepot Verification"


def creneau(starts_on, heure="08:45", minutes=60):
    """(début, fin) : le jour de la tâche, à `heure`, pour `minutes`. PURE."""
    debut = get_datetime("%s %s:00" % (getdate(starts_on), heure))
    return debut, add_to_date(debut, minutes=minutes)


def taches_ouvertes():
    noms = frappe.get_all(TACHE, filters={"status": "Open", "titre": ["like", "🧾 Vérification stock%"]}, pluck="name")
    noms += frappe.get_all(TACHE, filters={"status": "Open", "titre": "Vérification Stock nizar"}, pluck="name")
    return sorted(set(noms))


def execute():
    from customization_app.stock_entrepots import CONFIG, DUREE_VERIFICATION, DUREES_MIN, HEURE_VERIFICATION

    if frappe.db.exists("DocType", CONFIG) and frappe.db.exists("DocType", LIGNE):
        for nom in frappe.get_all(LIGNE, filters={"parent": CONFIG, "parenttype": CONFIG}, pluck="name"):
            frappe.db.set_value(LIGNE, nom, "heure", HEURE_VERIFICATION + ":00", update_modified=False)
        frappe.db.set_single_value(CONFIG, "duree_verification", DUREE_VERIFICATION, update_modified=False)
        frappe.clear_document_cache(CONFIG, CONFIG)
    minutes = DUREES_MIN[DUREE_VERIFICATION]
    n = 0
    for nom in taches_ouvertes():
        t = frappe.db.get_value(TACHE, nom, ["starts_on", "ends_on", "temps"], as_dict=True)
        if not t.starts_on:
            continue
        debut, fin = creneau(t.starts_on, HEURE_VERIFICATION, minutes)
        if (get_datetime(t.starts_on), t.ends_on and get_datetime(t.ends_on), t.temps) != (debut, fin, DUREE_VERIFICATION):
            frappe.db.set_value(TACHE, nom, {"starts_on": debut, "ends_on": fin, "temps": DUREE_VERIFICATION}, update_modified=False)
            n += 1
    print("  vérification de stock : réglage à %s pour %s, %d tâche(s) ouverte(s) remise(s) à ce créneau" % (HEURE_VERIFICATION, DUREE_VERIFICATION, n))
