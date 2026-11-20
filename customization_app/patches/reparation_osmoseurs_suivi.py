"""Réparation osmoseurs : suivi des restitutions (20/11/2026). Joué une fois.

1. Le réglage reçoit ses valeurs de départ, SEULEMENT là où rien n'est saisi : le rappel du matin à la responsable
   SAV, la relance au gérant de l'app, 3 jours chacun. Tout se change ensuite dans « Config Reparation Osmoseur ».
2. Les dossiers ouverts sont recalculés avec la règle corrigée : une tâche liée au dossier est sa réparation quel que
   soit son type. OSM-2026-0012 (tâche retypée « Livraison », clôturée le 13/11) était resté « Réceptionnée » : il
   passe « Réparée — à rendre », daté de la clôture de sa tâche et non du jour de la migration.

Rien d'autre n'est touché : aucun dossier n'est fermé ici — les machines déjà parties restent à confirmer une à une
depuis l'écran.
"""
import frappe

SUIVI = "salmaaquaworldservicing@gmail.com"      # Salma Ben Saïd — responsable relation client, administration et SAV
RELANCE = "koubaawassim@gmail.com"


def execute():
    from customization_app import reparation_osmoseur as RO

    if not frappe.db.exists("DocType", RO.DOCTYPE_CONFIG) or not frappe.get_meta(RO.DOCTYPE_CONFIG).get_field("suivi_restitutions"):
        return
    cfg = frappe.get_single(RO.DOCTYPE_CONFIG)
    for champ, valeur in (("suivi_restitutions", SUIVI), ("relance_a", RELANCE)):
        if not cfg.get(champ) and frappe.db.get_value("User", valeur, "enabled"):
            frappe.db.set_single_value(RO.DOCTYPE_CONFIG, champ, valeur)
    for champ, valeur in (("rappel_jours", RO.RAPPEL_JOURS_DEFAUT), ("relance_jours", RO.RELANCE_JOURS_DEFAUT)):
        if not cfg.get(champ):
            frappe.db.set_single_value(RO.DOCTYPE_CONFIG, champ, valeur)
    frappe.clear_document_cache(RO.DOCTYPE_CONFIG, RO.DOCTYPE_CONFIG)

    for m in frappe.get_all(RO.DOCTYPE, filters={"statut": ["in", [RO.S_RECEPTIONNEE, RO.S_PLANIFIEE, RO.S_REPAREE]]},
                            fields=["name", "statut"]):
        nouveau = RO.synchroniser(m.name)
        if nouveau == RO.S_REPAREE and m.statut != RO.S_REPAREE:
            cloture = frappe.db.sql("""SELECT MAX(modified) FROM `tabTache de travail`
                                       WHERE machine_reparation = %s AND status = 'Completed'""", m.name)[0][0]
            if cloture:
                frappe.db.set_value(RO.DOCTYPE, m.name, "date_reparee", cloture, update_modified=False)
