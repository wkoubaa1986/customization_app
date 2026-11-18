"""Tâches nouvelles ou modifiées depuis 8h30 le jour même (customization_app.tache_changement) — demande du
18/11/2026 : rouge clair « nouvelle tâche à faire », orange clair avec la modification."""
from __future__ import annotations

import inspect
import os
import unittest

import customization_app
from customization_app import tache_changement as T

BASE = os.path.dirname(inspect.getfile(customization_app))
JOUR = "2026-11-19"
RAD, SADOK = "HR-EMP-00013", "HR-EMP-00001"


def etat(heure="2026-11-19 10:00:00", type_="Entretien", employe=RAD):
    return {"starts_on": heure, "custom_type_dintervention": type_, "custom_choix_du_staff": employe}


class TestNouvelle(unittest.TestCase):
    def test_creee_le_jour_meme_apres_8h30(self):
        a = T.alerte(JOUR, "2026-11-19 11:20:45", None, RAD)
        self.assertEqual(a, {"genre": "nouvelle", "texte": "Nouvelle tâche à faire", "detail": "créée à 11:20"})
        self.assertEqual(T.alerte(JOUR, "2026-11-19 08:30:00", None, RAD)["genre"], "nouvelle")      # 8h30 pile compte

    def test_creee_avant_8h30_ou_la_veille_rien(self):
        self.assertIsNone(T.alerte(JOUR, "2026-11-19 08:29:59", None, RAD))
        self.assertIsNone(T.alerte(JOUR, "2026-11-18 16:00:00", None, RAD))

    def test_confiee_a_lui_le_jour_meme_apres_8h30(self):
        """Un transfert en cours de journée : pour lui, c'est une tâche de plus."""
        m = T.marque_apres(None, etat(employe=SADOK), etat(employe=RAD), "2026-11-19 09:40:00", "bureau")
        a = T.alerte(JOUR, "2026-11-10 09:00:00", m, RAD, {SADOK: "Sadok Bouziri"})
        self.assertEqual((a["genre"], a["detail"]), ("nouvelle", "confiée à vous à 09:40 (avant : Sadok Bouziri)"))
        self.assertIsNone(T.alerte(JOUR, "2026-11-10 09:00:00", m, SADOK))                           # pas pour celui qui l'a perdue


class TestModifiee(unittest.TestCase):
    def test_heure_modifiee_apres_8h30(self):
        m = T.marque_apres(None, etat(), etat("2026-11-19 14:00:00"), "2026-11-19 11:05:00", "bureau")
        a = T.alerte(JOUR, "2026-11-10 09:00:00", m, RAD)
        self.assertEqual(a, {"genre": "modifiee", "texte": "Heure modifiée : 10:00 → 14:00", "detail": "à 11:05"})

    def test_type_modifie(self):
        m = T.marque_apres(None, etat(), etat(type_="Réparation"), "2026-11-19 09:00:00")
        self.assertEqual(T.alerte(JOUR, "2026-11-10 09:00:00", m, RAD)["texte"], "Type modifié : Entretien → Réparation")

    def test_heure_et_type(self):
        m = T.marque_apres(None, etat(), etat("2026-11-19 15:30:00", "Réparation"), "2026-11-19 09:00:00")
        self.assertEqual(T.alerte(JOUR, "2026-11-10 09:00:00", m, RAD)["texte"],
                         "Heure modifiée : 10:00 → 15:30 · Type modifié : Entretien → Réparation")

    def test_une_tache_ramenee_d_un_autre_jour_dit_d_ou_elle_vient(self):
        m = T.marque_apres(None, etat("2026-11-20 10:00:00"), etat("2026-11-19 16:00:00"), "2026-11-19 12:00:00")
        self.assertEqual(T.alerte(JOUR, "2026-11-10 09:00:00", m, RAD)["texte"], "Heure modifiée : 20/11 10:00 → 16:00")

    def test_modifiee_la_veille_ou_avant_8h30_rien(self):
        self.assertIsNone(T.marque_apres(None, etat(), etat("2026-11-19 14:00:00"), "2026-11-18 17:00:00"))
        self.assertIsNone(T.marque_apres(None, etat(), etat("2026-11-19 14:00:00"), "2026-11-19 08:10:00"))

    def test_deux_changements_gardent_l_heure_d_origine(self):
        m = T.marque_apres(None, etat(), etat("2026-11-19 12:00:00"), "2026-11-19 09:00:00")
        m = T.marque_apres(m, etat("2026-11-19 12:00:00"), etat("2026-11-19 15:00:00"), "2026-11-19 10:00:00")
        self.assertEqual(T.alerte(JOUR, "", m, RAD), {"genre": "modifiee", "texte": "Heure modifiée : 10:00 → 15:00", "detail": "à 10:00"})

    def test_remise_comme_avant_plus_rien(self):
        m = T.marque_apres(None, etat(), etat("2026-11-19 12:00:00"), "2026-11-19 09:00:00")
        m = T.marque_apres(m, etat("2026-11-19 12:00:00"), etat(), "2026-11-19 09:30:00")
        self.assertEqual(m, {})
        self.assertIsNone(T.alerte(JOUR, "", m, RAD))

    def test_un_enregistrement_sans_changement_ne_touche_a_rien(self):
        m = T.marque_apres(None, etat(), etat("2026-11-19 12:00:00"), "2026-11-19 09:00:00")
        self.assertIsNone(T.marque_apres(m, etat("2026-11-19 12:00:00"), etat("2026-11-19 12:00:00"), "2026-11-19 09:10:00"))
        self.assertIsNone(T.marque_apres(None, etat(), etat(), "2026-11-19 09:10:00"))

    def test_la_marque_d_un_autre_jour_tombe(self):
        m = T.marque_apres(None, etat(), etat("2026-11-19 12:00:00"), "2026-11-19 09:00:00")
        # la veille au soir, la tâche est repoussée au 20 : ce n'est plus une surprise pour le 20
        m2 = T.marque_apres(m, etat("2026-11-19 12:00:00"), etat("2026-11-20 12:00:00"), "2026-11-19 18:00:00")
        self.assertEqual(m2, {})
        self.assertIsNone(T.alerte("2026-11-20", "", m, RAD))          # et une vieille marque ne vaut que pour son jour

    def test_la_nouvelle_passe_avant_la_modifiee(self):
        m = T.marque_apres(None, etat(), etat("2026-11-19 14:00:00"), "2026-11-19 11:05:00")
        self.assertEqual(T.alerte(JOUR, "2026-11-19 09:00:00", m, RAD)["genre"], "nouvelle")

    def test_une_marque_illisible_ne_casse_rien(self):
        self.assertEqual(T.lire("pas du json"), {})
        self.assertIsNone(T.alerte(JOUR, "", "pas du json", RAD))


class TestBranchements(unittest.TestCase):
    def _lire(self, *chemin):
        with open(os.path.join(BASE, *chemin), encoding="utf-8") as f:
            return f.read()

    def test_le_seuil_est_8h30(self):
        self.assertEqual(T.HEURE_DEBUT, "08:30:00")
        self.assertEqual(T.seuil("2026-11-19"), "2026-11-19 08:30:00")

    def test_la_tache_note_ses_changements_sans_jamais_bloquer(self):
        self.assertIn('"customization_app.tache_changement.noter"],', self._lire("hooks.py"))
        source = inspect.getsource(T.noter)
        self.assertIn("doc.get_doc_before_save()", source)
        self.assertIn("except Exception:", source)
        import json
        champs = [f["fieldname"] for f in json.loads(self._lire("customize_erpnext", "doctype", "tache_de_travail", "tache_de_travail.json"))["fields"]]
        self.assertIn(T.CHAMP, champs)

    def test_ma_journee_signale_seulement_les_taches_a_faire(self):
        from customization_app import planning_employe as PE
        source = inspect.getsource(PE._alerte_du_jour)
        self.assertIn('if t.status != "Open":', source)
        self.assertIn('"alerte": _alerte_du_jour(t, jour, cible),', inspect.getsource(PE.ma_journee))

    def test_les_couleurs_de_la_carte(self):
        js = self._lire("public", "js", "ma_journee.js")
        self.assertIn(".mj-carte.mj-nouvelle { background:#fef2f2;", js)      # rouge clair
        self.assertIn(".mj-carte.mj-modifiee { background:#fff7ed;", js)      # orange clair
        self.assertIn('al.genre === "nouvelle" ? "🆕" : "✏️"', js)


if __name__ == "__main__":
    unittest.main()
