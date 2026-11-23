"""Patch « catalogue adoucisseurs » (patches/catalogue_adoucisseurs.py) : règles pures et cohérence des données livrées.
L'essai complet (remise du dev dans l'état de la prod, patch, comparaison) : /workspace/development/mcp_articles_dev/essai_patch_prod.py.
Lancer : ../../env/bin/python -m unittest customization_app.tests.test_catalogue_adoucisseurs (depuis apps/customization_app)."""
import json
import os
import unittest

from customization_app.patches import catalogue_adoucisseurs as P

DONNEES = os.path.join(P.DOSSIER, "donnees.json")


class TestRegles(unittest.TestCase):
    def test_pareil(self):
        self.assertTrue(P.pareil(None, ""))
        self.assertTrue(P.pareil("1", 1))
        self.assertTrue(P.pareil("858.423000000", 858.423))
        self.assertFalse(P.pareil("a", "b"))
        self.assertFalse(P.pareil(0, 1))

    def test_cle_prix(self):
        a = {"item_code": "A", "price_list": "Vente standard", "uom": "Pièce", "customer": None, "valid_from": "2026-11-23"}
        self.assertEqual(P.cle_prix(a), P.cle_prix(dict(a, customer="", supplier=None)))
        self.assertNotEqual(P.cle_prix(a), P.cle_prix(dict(a, price_list="Technicien")))
        self.assertNotEqual(P.cle_prix(a), P.cle_prix(dict(a, valid_from="2026-12-01")))

    def test_bilan(self):
        b = P.Bilan()
        b.note("article créé"); b.note("article créé", 2); b.alerte("Prix X : laissé")
        self.assertEqual(b.comptes, {"article créé": 3})
        self.assertIn("Points à regarder : 1", b.texte())
        self.assertIn("! Prix X : laissé", b.texte())


class TestImages(unittest.TestCase):
    """En prod les images n'existent pas encore sur le disque : le patch les copie depuis ses données."""

    def _jouer(self, deja_sur_le_site=None):
        import tempfile
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            dossier, site = os.path.join(tmp, "donnees"), os.path.join(tmp, "site")
            os.makedirs(os.path.join(dossier, "fichiers", "public", "files"))
            with open(os.path.join(dossier, "fichiers", "public", "files", "a.png"), "wb") as f:
                f.write(b"image du patch")
            if deja_sur_le_site is not None:
                os.makedirs(os.path.join(site, "public", "files"))
                with open(os.path.join(site, "public", "files", "a.png"), "wb") as f:
                    f.write(deja_sur_le_site)
            bilan = P.Bilan()
            d = {"fichiers": [{"ligne": {"name": "f1", "file_name": "a.png"}, "chemin": "public/files/a.png"}]}
            with mock.patch.object(P.frappe, "get_site_path", return_value=site), mock.patch.object(P, "existe", return_value=True):
                P.remettre_fichiers(d, bilan, dossier)
            with open(os.path.join(site, "public", "files", "a.png"), "rb") as f:
                return bilan, f.read()

    def test_image_copiee_quand_elle_manque(self):
        bilan, contenu = self._jouer()
        self.assertEqual(contenu, b"image du patch")
        self.assertEqual(bilan.comptes.get("image copiée"), 1)

    def test_image_deja_la_jamais_ecrasee(self):
        bilan, contenu = self._jouer(deja_sur_le_site=b"une autre image, plus longue")
        self.assertEqual(contenu, b"une autre image, plus longue")
        self.assertEqual(bilan.comptes.get("image déjà sur le disque"), 1)
        self.assertEqual(len(bilan.alertes), 1)


@unittest.skipUnless(os.path.isfile(DONNEES), "données du patch pas encore générées")
class TestDonnees(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(DONNEES, encoding="utf-8") as f:
            cls.d = json.load(f)

    def test_rubriques(self):
        for cle in ("renommages", "attributs", "groupes", "articles_a_creer", "articles_a_modifier", "a_desactiver", "prix_a_creer",
                    "prix_a_modifier", "fichiers", "commentaires", "transformations", "liens", "genere_le", "prod_lue_le"):
            self.assertIn(cle, self.d, cle)

    def test_un_article_n_est_pas_a_la_fois_cree_et_retouche(self):
        crees = {a["name"] for a in self.d["articles_a_creer"]}
        self.assertEqual(len(crees), len(self.d["articles_a_creer"]))
        self.assertFalse(crees & {a["name"] for a in self.d["articles_a_modifier"]})
        self.assertFalse(crees & set(self.d["a_desactiver"]))

    def test_un_article_renomme_n_est_pas_recree(self):
        """Renommé = la même fiche sous un nouveau code : ses retouches se lisent sous le nouveau code."""
        crees = {a["name"] for a in self.d["articles_a_creer"]}
        for r in self.d["renommages"]:
            self.assertNotEqual(r["de"], r["vers"])
            self.assertNotIn(r["vers"], crees, r)
            self.assertNotIn(r["de"], crees, r)
            self.assertNotIn(r["de"], {a["name"] for a in self.d["articles_a_modifier"]}, r)

    def test_la_desactivation_ne_passe_que_par_sa_rubrique(self):
        """« disabled » d'un article existant n'est posé qu'à la fin, après la transformation de stock."""
        for a in self.d["articles_a_modifier"]:
            self.assertNotIn("disabled", a["champs"], a["name"])

    def test_chaque_retouche_porte_la_valeur_de_prod_et_celle_du_dev(self):
        for a in self.d["articles_a_modifier"] + self.d["prix_a_modifier"]:
            for champ, v in a["champs"].items():
                self.assertEqual(set(v), {"prod", "dev"}, (a["name"], champ))
                self.assertFalse(P.pareil(v["prod"], v["dev"]), (a["name"], champ))

    def test_chaque_prix_vise_un_article_connu(self):
        crees = {a["name"] for a in self.d["articles_a_creer"]}
        cites = {tuple(l) for l in self.d["liens"]}
        for p in self.d["prix_a_creer"]:
            self.assertTrue(p["item_code"] in crees or ("Item", p["item_code"]) in cites, p["name"])
        cles = [P.cle_prix(p) for p in self.d["prix_a_creer"]]
        self.assertEqual(len(cles), len(set(cles)))                       # jamais deux prix pour la même clé

    def test_chaque_image_est_livree(self):
        for f in self.d["fichiers"]:
            self.assertTrue(f["chemin"], f["ligne"]["name"])
            self.assertTrue(os.path.isfile(os.path.join(P.DOSSIER, "fichiers", f["chemin"])), f["chemin"])

    def test_l_article_transforme_est_cree_ou_existe(self):
        crees = {a["name"] for a in self.d["articles_a_creer"]}
        for t in self.d["transformations"]:
            self.assertIn(t["vers"], crees | {a["name"] for a in self.d["articles_a_modifier"]}, t)
            self.assertGreater(t["qte"], 0)

    def test_patch_inscrit(self):
        chemin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(P.__file__))), "patches.txt")
        with open(chemin, encoding="utf-8") as f:
            self.assertIn("customization_app.patches.catalogue_adoucisseurs", f.read().split())


if __name__ == "__main__":
    unittest.main()
