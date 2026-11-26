"""SMS de réveil des « À requalifier » (délai réglable, 25/11/2026) — règles pures : premier réveil 180 j après l'appel
au lieu de 365, réveils suivants toujours annuels (relecture du 26/11/2026), 0 = aucun réveil (sinon un SMS chaque
matin), trace qui dit le vrai délai, compteurs remis à l'entonnoir sans additionner le délai ni le mode."""
from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from customization_app.Maintenance import sms_reveil as S

TODAY = "2026-11-25"


class TestDelai(unittest.TestCase):
    def test_reglage_et_defaut(self):
        self.assertEqual(S.jours_reveil({}), 180)                               # tant que le champ n'existe pas
        self.assertEqual(S.jours_reveil({"jours_reveil_requalifies": 90}), 90)
        self.assertEqual(S.jours_reveil({"jours_reveil_requalifies": 0}), 0)
        self.assertEqual(S.JOURS_SILENCE, 365)                                  # règle d'origine, défaut de eligible() seule

    def test_eligible_a_180_jours(self):
        self.assertTrue(S.eligible("À requalifier", "Oui", "2026-05-01", None, TODAY, 180))       # 208 j
        self.assertTrue(S.eligible("À requalifier", "Oui", "2026-05-29", None, TODAY, 180))       # 180 j pile
        self.assertFalse(S.eligible("À requalifier", "Oui", "2026-05-30", None, TODAY, 180))      # 179 j
        self.assertFalse(S.eligible("À requalifier", "Oui", "2026-01-01", "2026-07-01", TODAY, 180))   # réveil il y a 147 j
        self.assertFalse(S.eligible("À requalifier", "Oui", "2026-05-01", None, TODAY))           # l'ancien délai (365) aurait attendu
        self.assertFalse(S.eligible("À requalifier", "Non", "2026-01-01", None, TODAY, 180))      # « Non » aux SMS
        self.assertFalse(S.eligible("À requalifier", "Oui", None, None, TODAY, 180))              # jamais de contact : pas un réveil

    def test_reveils_suivants_une_fois_par_an(self):
        """Relecture du 26/11/2026 : le réglage avance le PREMIER réveil, il ne fait pas répéter le SMS tous les 180 j."""
        appel, reveil = "2026-10-15", "2027-04-30"
        self.assertTrue(S.eligible("À requalifier", "Oui", appel, None, "2027-04-30", 180))        # 1er réveil : 197 j après l'appel
        self.assertFalse(S.eligible("À requalifier", "Oui", appel, reveil, "2027-10-27", 180))     # 180 j après le réveil : non
        self.assertFalse(S.eligible("À requalifier", "Oui", appel, reveil, "2028-04-28", 180))     # 364 j
        self.assertTrue(S.eligible("À requalifier", "Oui", appel, reveil, "2028-04-29", 180))      # 365 j (2028 bissextile)
        self.assertTrue(S.eligible("À requalifier", "Oui", None, reveil, "2028-04-29", 180))       # sans appel connu
        # Un délai réglé au-delà d'un an vaut aussi entre deux réveils.
        self.assertFalse(S.eligible("À requalifier", "Oui", appel, reveil, "2028-05-15", 400))
        self.assertTrue(S.eligible("À requalifier", "Oui", appel, reveil, "2028-06-03", 400))
        # Un appel après le réveil repousse le suivant de `jours` après cet appel.
        self.assertFalse(S.eligible("À requalifier", "Oui", "2028-01-10", reveil, "2028-05-01", 180))
        self.assertTrue(S.eligible("À requalifier", "Oui", "2028-01-10", reveil, "2028-07-08", 180))

    def test_zero_coupe_le_reveil_sans_toucher_la_base(self):
        self.assertEqual(S.candidats(TODAY, {"jours_reveil_requalifies": 0}), [])
        self.assertEqual(S.candidats(TODAY, {"jours_reveil_requalifies": -1}), [])

    def test_trace(self):
        self.assertEqual(S.texte_trace(TODAY, 180, "Bonjour"),
                         "📨 SMS de réveil envoyé le 25/11/2026 (aucun contact depuis au moins 180 j) : Bonjour")


class TestEntonnoir(unittest.TestCase):
    RESUME = {"candidats": 3, "envoyes": 2, "sans_numero": 1, "echecs": 0, "simules": True}
    ATTENDU = {"candidats": 3, "envoyes": 2, "sans_numero": 1, "echecs": 0, "delai": "180 j", "mode": "simulé"}

    def test_compteurs_remis_a_noter(self):
        faux = types.ModuleType("customization_app.entonnoir_relances")
        faux.noter = mock.Mock()
        with mock.patch.dict(sys.modules, {"customization_app.entonnoir_relances": faux}):
            S._noter_entonnoir(self.RESUME, 180)
        faux.noter.assert_called_once_with("reveil", self.ATTENDU)
        self.assertEqual(S.compteurs_entonnoir(dict(self.RESUME, simules=False), 90)["mode"], "réel")

    def test_deux_passages_le_meme_jour(self):
        """Relecture du 26/11/2026 : l'entonnoir additionne les nombres d'un passage à l'autre ; le délai et le mode, en
        texte, sont remplacés (en nombres : « jours = 360, simules = 2 » après le cron et une relance à la main)."""
        try:
            from customization_app import entonnoir_relances as E
        except ImportError:
            self.skipTest("module entonnoir_relances absent")
        c = S.compteurs_entonnoir(self.RESUME, 180)
        jour = E.maj_source(E.maj_source({}, "reveil", c, "10:05:00"), "reveil", c, "15:30:00")["reveil"]
        self.assertEqual(jour["compteurs"], {"candidats": 6, "envoyes": 4, "sans_numero": 2, "echecs": 0, "delai": "180 j", "mode": "simulé"})
        self.assertEqual(jour["passages"], 2)

    def test_sans_module_ni_erreur(self):
        faux = types.ModuleType("customization_app.entonnoir_relances")
        faux.noter = mock.Mock(side_effect=RuntimeError("entonnoir en panne"))
        with mock.patch.dict(sys.modules, {"customization_app.entonnoir_relances": faux}):
            S._noter_entonnoir(self.RESUME, 180)                                # ne lève jamais : le cron continue
        with mock.patch.dict(sys.modules, {"customization_app.entonnoir_relances": None}):
            S._noter_entonnoir(self.RESUME, 180)                                # module absent (ImportError)


if __name__ == "__main__":
    unittest.main()
