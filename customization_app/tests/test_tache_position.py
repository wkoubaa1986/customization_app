"""Le lien « Maps » montré sur une tâche sans lien Google Map (customization_app.tache_position) — demande du
18/11/2026 : « il y a des cas où je ne vois pas le lien Google Map, or l'adresse a une latitude et une longitude »."""
from __future__ import annotations

import inspect
import os
import unittest

import customization_app
from customization_app import tache_position as P

BASE = os.path.dirname(inspect.getfile(customization_app))


class TestLienAMontrer(unittest.TestCase):
    def test_le_lien_de_la_tache_d_abord(self):
        r = P.lien_a_montrer("https://maps.app.goo.gl/x81hm4xM55yNaYU37", "https://maps.google.com/?q=1,2", 36.8, 10.1, "lien adresse")
        self.assertEqual(r, {"url": "https://maps.app.goo.gl/x81hm4xM55yNaYU37", "origine": "tache", "approximatif": False})

    def test_sinon_le_lien_de_l_adresse(self):
        r = P.lien_a_montrer("", "https://maps.google.com/?q=36.8667434,10.2549394", 36.86, 10.25, "lien adresse")
        self.assertEqual((r["origine"], r["approximatif"]), ("adresse", False))

    def test_sinon_la_position_dite_approximative_quand_elle_vient_du_texte(self):
        """L'adresse de Sami Dammak : pas de lien, une position « texte ≈ »."""
        r = P.lien_a_montrer(None, None, 36.865693, 10.168727, "texte ≈")
        self.assertEqual(r, {"url": "https://www.google.com/maps?q=36.865693,10.168727", "origine": "position", "approximatif": True})
        self.assertTrue(P.lien_a_montrer(None, None, 36.8, 10.1, "texte ≈ (lien mort)")["approximatif"])

    def test_une_position_lue_d_un_lien_n_est_pas_approximative(self):
        self.assertFalse(P.lien_a_montrer(None, None, 36.8, 10.1, "lien tâche")["approximatif"])

    def test_rien_a_montrer(self):
        self.assertIsNone(P.lien_a_montrer(None, None, 0, 0, "texte ≈"))
        self.assertIsNone(P.lien_a_montrer("", "", None, None, None))
        self.assertIsNone(P.lien_a_montrer("", "", "abc", "def", None))
        self.assertIsNone(P.lien_a_montrer("pas un lien", "", 0, 10.1, None))

    def test_un_texte_qui_n_est_pas_un_lien_n_est_jamais_rendu_cliquable(self):
        r = P.lien_a_montrer("javascript:alert(1)", "36.8, 10.1", 36.8, 10.1, "texte ≈")
        self.assertEqual(r["origine"], "position")
        self.assertTrue(r["url"].startswith("https://www.google.com/maps?q="))


class TestRienNEstEcritSurLaTache(unittest.TestCase):
    """Le lien de la tâche est recopié sur l'adresse et dispense de relever la position à la clôture : une position
    approximative ne doit jamais y être écrite."""

    def test_le_service_ne_fait_que_lire(self):
        source = inspect.getsource(P)
        for ecriture in ("set_value", ".save(", ".insert(", "db_set"):
            self.assertNotIn(ecriture, source)
        self.assertIn('frappe.has_permission("Tache de travail", "read", throw=True)', inspect.getsource(P.position))

    def test_l_ecran_ne_remplit_pas_le_champ(self):
        with open(os.path.join(BASE, "public", "js", "tache_position.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertNotIn("set_value", js)
        self.assertIn('frm.set_df_property(CHAMP, "description", sous_le_lien ? html : "");', js)
        self.assertIn('frm.set_df_property(ADRESSE, "description", sous_le_lien ? "" : html);', js)   # champ vide = masqué
        self.assertIn("/^https?:\\/\\//i.test(p.url)", js)
        self.assertIn("position approximative", js)

    def test_le_script_est_charge(self):
        with open(os.path.join(BASE, "hooks.py"), encoding="utf-8") as f:
            self.assertIn('_js("tache_position.js")', f.read())


if __name__ == "__main__":
    unittest.main()
