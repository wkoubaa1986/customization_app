"""Revue des dépenses à la collecte, accord préalable, « Où est l'argent liquide » (24/11/2026). PUR (pas de base).

Lancer dans le site (jamais `bench run-tests`) : `unittest customization_app.tests.test_caisse_controle_argent`
depuis frappe-bench/sites."""
import unittest
from datetime import date

from customization_app import argent_liquide as AL
from customization_app import caisse_accord as A
from customization_app import caisse_revue as R


def _l(name, montant, cle="citernesplast", **autres):
    d = {"name": name, "montant": montant, "cle": cle, "date": "2026-11-16", "saisi_par": "Jamel Aloui",
         "fournisseur": "CITERNES PLAST", "numero_facture": "2174", "photos": [{"url": "/x.jpg"}],
         "type": "Dépense avec facture"}
    d.update(autres)
    return d


class TestRevue(unittest.TestCase):
    def test_cle_tiers(self):
        self.assertEqual(R.cle_tiers("Citernes  Plast"), R.cle_tiers("CITERNES PLAST"))
        self.assertEqual(R.cle_tiers("Société Générale"), "societegenerale")

    def test_doublon_citernes_plast(self):
        jamel = _l("ACC-JV-2026-01064", 87.537)
        sadok = _l("ACC-PAY-2026-08472", 87.537, saisi_par="Sadok Bouziri")
        autre = _l("ACC-JV-1", 87.537, cle="ooredoo")
        s = R.signaux_depense(sadok, [jamel, sadok, autre], 200)
        self.assertEqual([x["code"] for x in s], ["doublon"])
        self.assertIn("ACC-JV-2026-01064 par Jamel Aloui le 16/11/2026", s[0]["texte"])
        self.assertEqual(R.signaux_depense(_l("A", 87.537), [_l("A", 87.537)], 200), [])   # elle-même : pas un doublon

    def test_autres_signaux(self):
        l = _l("X", 360, numero_facture="", photos=[], type="Dépense non facturée", payee_par_collegue=True,
               caisse="Jamel Aloui", saisi_par="Sadok Bouziri")
        codes = {x["code"]: x["niveau"] for x in R.signaux_depense(l, [], 200)}
        self.assertEqual(codes, {"collegue": "orange", "eleve": "orange", "sans_facture": "gris", "sans_photo": "orange"})
        self.assertEqual(R.signaux_depense(_l("Y", 360, numero_facture=""), [], 0)[0]["niveau"], "orange")  # facturée

    def test_verifier_revue(self):
        deps = [{"name": "A"}, {"name": "B"}]
        self.assertTrue(R.verifier_revue(deps, {"A": {"verdict": "vu"}}))
        self.assertTrue(R.verifier_revue(deps, {"A": {"verdict": "vu"}, "B": {"verdict": "conteste", "commentaire": "non"}}))
        self.assertEqual(R.verifier_revue(deps, {"A": {"verdict": "vu"},
                                                 "B": {"verdict": "conteste", "commentaire": "Pas de facture du tout"}}), [])
        self.assertEqual(R.lire_revue("pas du json"), {})


class TestAccord(unittest.TestCase):
    def test_seuil(self):
        self.assertTrue(A.doit_demander(250, 200, False))
        self.assertFalse(A.doit_demander(200, 200, False))        # au seuil : pas besoin
        self.assertFalse(A.doit_demander(250, 200, True))         # un responsable : jamais
        self.assertFalse(A.doit_demander(5000, 0, False))         # seuil 0 = désactivé


class TestArgentLiquide(unittest.TestCase):
    def test_categorie(self):
        self.assertEqual(AL.categorie("Sabah Espèces - A&S"), "Sabah Espèces")
        self.assertEqual(AL.categorie("BIAT - A&S"), "Banque")
        self.assertEqual(AL.categorie("Depenses personnelle - A&S"), "Dépenses personnelles")
        self.assertEqual(AL.categorie("Compte temporaire - A&S"), "Autres")

    def test_anciennete_premier_entre_premier_sorti(self):
        entrees = [(date(2026, 11, 18), 100), (date(2026, 11, 20), 50), (date(2026, 11, 21), 30)]
        self.assertEqual(AL.anciennete(entrees, 0, date(2026, 11, 24)), (date(2026, 11, 18), 6))
        self.assertEqual(AL.anciennete(entrees, 100, date(2026, 11, 24)), (date(2026, 11, 20), 4))
        self.assertEqual(AL.anciennete(entrees, 120, date(2026, 11, 24)), (date(2026, 11, 20), 4))   # en partie couvert
        self.assertEqual(AL.anciennete(entrees, 180, date(2026, 11, 24)), (None, 0))

    def test_montant_recu(self):
        self.assertEqual(AL.montant_recu({"especes_recues": 50, "especes_remises": 50}), 50)
        self.assertEqual(AL.montant_recu({"especes_recues": 0, "especes_remises": 521, "validation_seule": 1}), 521)  # directe
        self.assertEqual(AL.montant_recu({"especes_recues": 0, "especes_remises": 521, "validation_seule": 0}), 0)

    def test_saut_de_chaine_nejib_20_10(self):
        """CLO-1055 (20/10) compté avant CLO-1056 (19/10) : son ouverture ignore le 19/10 → +5 293 DT."""
        c = [
            {"caisse": "N", "name": "CLO-0993", "date_cloture": "2026-10-17", "solde_ouverture": 2006.782, "fond_conserve": 2003.782},
            {"caisse": "N", "name": "CLO-1056", "date_cloture": "2026-10-19", "solde_ouverture": 2003.782, "fond_conserve": -3289.218},
            {"caisse": "N", "name": "CLO-1055", "date_cloture": "2026-10-20", "solde_ouverture": 2003.782, "fond_conserve": 1988.782},
            {"caisse": "S", "name": "CLO-2", "date_cloture": "2026-10-20", "solde_ouverture": 0, "especes_comptees": 30,
             "especes_remises": 30, "fond_conserve": None},
            {"caisse": "S", "name": "CLO-3", "date_cloture": "2026-10-21", "solde_ouverture": 0, "fond_conserve": 0},
        ]
        sauts = AL.sauts_de_chaine(c)
        self.assertEqual([(s["cloture"], s["saut"]) for s in sauts], [("CLO-0993", 2006.782), ("CLO-1055", 5293.0)])
        self.assertEqual(AL.fond_de({"especes_comptees": 30, "especes_remises": 30, "fond_conserve": None}), 0)

    def test_reference(self):
        self.assertEqual(AL.reference("Ref : TT263168Q77X"), "TT263168Q77X")
        self.assertEqual(AL.reference("Versement espèces BIAT — réf. TT26306750847374\nSaisie via ChatGPT"), "TT26306750847374")
        self.assertEqual(AL.reference("Recette au 23-11-2026", "CHQ1"), "CHQ1")
