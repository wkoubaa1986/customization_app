"""
Rattrapage : les liens Google Map relevés sur les tâches, reportés sur les adresses.

Le dialogue de clôture exige depuis toujours un lien Google Map (bouton
« 📍 Ma position ») et l'écrit sur la Tache de travail — mais ce lien n'était
jamais reporté sur l'Address sélectionnée. Résultat : des centaines
d'interventions localisées, et autant d'adresses toujours muettes ; à chaque
nouvelle intervention le technicien devait se relocaliser.

cloture_tache.propager_google_map_vers_adresse ferme la boucle pour les
clôtures à venir ; ce patch récupère l'historique.

Périmètre volontairement étroit, comme pour la propagation en temps réel : SEULES
les adresses SANS lien sont remplies — un lien déjà saisi (souvent corrigé à la
main) n'est jamais écrasé. La tâche source est la plus récente qui référence
l'adresse, les Completed d'abord : une intervention terminée a forcément vu le
technicien sur place, un rendez-vous encore ouvert peut porter un lien provisoire.
Les tâches annulées sont ignorées.
"""

import frappe

from customization_app.cloture_tache import CHAMP_GMAP_ADRESSE


def _rang(tache):
    """Clé de tri des tâches candidates : Completed d'abord, puis la plus récente."""
    return (1 if tache.get("status") == "Completed" else 0,
            str(tache.get("modified") or ""))


def liens_par_adresse(taches):
    """Le lien retenu pour chaque adresse, à partir des tâches candidates.

    Fonction pure (testée sans base) : les tâches sans adresse ou sans lien sont
    écartées, et pour une même adresse la tâche de meilleur rang l'emporte.
    """
    meilleures = {}
    for tache in taches:
        adresse = tache.get("select_address")
        if not adresse or not (tache.get("google_map") or "").strip():
            continue
        courante = meilleures.get(adresse)
        if courante is None or _rang(tache) > _rang(courante):
            meilleures[adresse] = tache
    return {adresse: tache["google_map"].strip()
            for adresse, tache in meilleures.items()}


def execute():
    # Au migrate, les patches passent AVANT la synchro des fixtures : sur un site
    # neuf le champ custom de l'adresse peut ne pas exister encore.
    if not frappe.db.has_column("Address", CHAMP_GMAP_ADRESSE):
        print("[propager_google_map_taches_vers_adresses] champ "
              f"{CHAMP_GMAP_ADRESSE} absent de Address : rien à faire.")
        return

    taches = frappe.db.sql(
        """SELECT t.name, t.select_address, t.google_map, t.status, t.modified
           FROM `tabTache de travail` t
           JOIN `tabAddress` a ON a.name = t.select_address
           WHERE t.status != 'Cancelled'
             AND TRIM(COALESCE(t.google_map, '')) != ''
             AND TRIM(COALESCE(a.custom_lien_google_map, '')) = ''""",
        as_dict=True)

    liens = liens_par_adresse(taches)
    for adresse, lien in liens.items():
        frappe.db.set_value("Address", adresse, CHAMP_GMAP_ADRESSE, lien,
                            update_modified=False)

    frappe.db.commit()
    print(f"[propager_google_map_taches_vers_adresses] {len(liens)} adresse(s) "
          f"complétée(s) depuis {len(taches)} tâche(s) localisée(s).")
