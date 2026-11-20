"""Gouvernorat / ville / secteur d'une adresse (20/11/2026) : les champs de la fiche et les champs standard restent
alignés. Les classes « Pures » tournent sans la base ; « Document » enregistre de vraies adresses, annulées."""
import unittest

from customization_app import adresse_sectorisation as S
from customization_app.patches import aligner_gouvernorat_ville_adresses as P
from customization_app.sectorisation import VILLES_PAR_GOUVERNORAT, normaliser_nom

TN = "Tunisia"


def adr(**k):
    base = {"country": TN, "state": None, "city": None, "custom_state_s": None, "custom_villes_s": None,
            "custom_state_d": None, "custom_villes_d": None, "custom_secteur": None}
    base.update(k)
    return base


class TestTablePure(unittest.TestCase):
    def test_noms_sans_collision(self):
        """La reconnaissance par graphie ne confond jamais deux gouvernorats, ni deux villes d'un gouvernorat."""
        self.assertEqual(len({normaliser_nom(g) for g in VILLES_PAR_GOUVERNORAT}), len(VILLES_PAR_GOUVERNORAT))
        for g, villes in VILLES_PAR_GOUVERNORAT.items():
            self.assertEqual(len({normaliser_nom(v) for v in villes}), len(villes), g)

    def test_graphies(self):
        self.assertEqual(S.gouvernorat_officiel("ARIANA"), "Ariana")
        self.assertEqual(S.gouvernorat_officiel("gabes"), "Gabès")
        self.assertIsNone(S.gouvernorat_officiel("Constantine"))
        self.assertEqual(S.ville_officielle("Ariana", "Borj\xa0touil"), "Borj touil")       # espace insécable
        self.assertIsNone(S.ville_officielle("Ariana", "La Soukra"))                         # pas de ressemblance devinée
        self.assertIsNone(S.ville_officielle("Tunis", "Soukra"))                             # ville d'un autre gouvernorat
        self.assertEqual(S.secteur_attendu("ARIANA", "cité ennasr 1"), "Secteur 2")
        self.assertIsNone(S.secteur_attendu("Ariana", "inconnu"))
        self.assertIsNone(S.secteur_attendu(None, None))


class TestAlignerPure(unittest.TestCase):
    def test_creation_par_la_saisie_rapide(self):
        """ERPNext n'écrit que state / city : les champs de la fiche se remplissent."""
        a = adr(state="Ariana", city="Cité Ennasr 1")
        self.assertEqual(S.aligner(a), {"custom_state_s": "Ariana", "custom_villes_s": "Cité Ennasr 1"})

    def test_creation_par_la_fiche(self):
        a = adr(custom_state_s="Tunis", custom_villes_s="La Marsa")
        self.assertEqual(S.aligner(a), {"state": "Tunis", "city": "La Marsa"})

    def test_creation_les_deux_remplis_la_fiche_fait_foi(self):
        a = adr(state="Nabeul", city="Hammamet", custom_state_s="Tunis", custom_villes_s="La Marsa")
        self.assertEqual(S.aligner(a), {"state": "Tunis", "city": "La Marsa"})

    def test_ancienne_adresse_modifiee_pour_autre_chose(self):
        """Adresse créée avant ce module (champs de la fiche vides), on change le téléphone : rien ne se perd."""
        avant = adr(state="Ariana", city="Cité Ennasr 1", custom_secteur="Secteur 2")
        self.assertEqual(S.aligner(dict(avant), avant), {"custom_state_s": "Ariana", "custom_villes_s": "Cité Ennasr 1"})

    def test_la_fiche_change_la_ville(self):
        avant = adr(state="Ariana", city="Soukra", custom_state_s="Ariana", custom_villes_s="Soukra")
        apres = dict(avant, custom_villes_s="Raoued")
        self.assertEqual(S.aligner(apres, avant), {"city": "Raoued"})

    def test_la_fiche_vide_la_ville(self):
        """Gouvernorat changé, ville pas rechoisie : la ville standard est vidée — Frappe réclamera la ville, on ne
        garde pas en silence celle de l'ancien gouvernorat."""
        avant = adr(state="Ariana", city="Soukra", custom_state_s="Ariana", custom_villes_s="Soukra")
        apres = dict(avant, custom_state_s="Tunis", custom_villes_s="")
        self.assertEqual(S.aligner(apres, avant), {"state": "Tunis", "city": ""})

    def test_un_programme_change_le_standard(self):
        avant = adr(state="Ariana", city="Soukra", custom_state_s="Ariana", custom_villes_s="Soukra")
        apres = dict(avant, state="Tunis", city="La Marsa")
        self.assertEqual(S.aligner(apres, avant), {"custom_state_s": "Tunis", "custom_villes_s": "La Marsa"})

    def test_contradiction_ancienne_la_fiche_client_fait_foi(self):
        """Personne n'a touché ni l'un ni l'autre : le standard, celui que la fiche client affiche (20/11/2026)."""
        avant = adr(state="Nabeul", city="Hammamet", custom_state_s="Tunis", custom_villes_s="Tunis")
        self.assertEqual(S.aligner(dict(avant), avant), {"custom_state_s": "Nabeul", "custom_villes_s": "Hammamet"})
        # ...mais si la fiche vient de choisir autre chose, c'est ce choix qui compte
        self.assertEqual(S.aligner(dict(avant, custom_villes_s="La Marsa"), avant), {"custom_state_s": "Nabeul", "city": "La Marsa"})

    def test_rien_a_faire(self):
        avant = adr(state="Ariana", city="Soukra", custom_state_s="Ariana", custom_villes_s="Soukra")
        self.assertEqual(S.aligner(dict(avant), avant), {})

    def test_graphie_officielle(self):
        a = adr(state="ARIANA", city="Borj\xa0touil")
        self.assertEqual(S.aligner(a), {"state": "Ariana", "custom_state_s": "Ariana", "city": "Borj touil", "custom_villes_s": "Borj touil"})
        a = adr(state="SFAX", city="SFAX")                       # la ville n'est pas dans la table : gardée telle quelle
        self.assertEqual(S.aligner(a), {"state": "Sfax", "custom_state_s": "Sfax", "custom_villes_s": "SFAX"})

    def test_hors_tunisie(self):
        a = adr(country="China", city="Zhejiang")
        self.assertEqual(S.aligner(a), {"custom_villes_d": "Zhejiang"})
        avant = adr(country="Algeria", state="Constantine", city="canstantine", custom_state_d="Constantine", custom_villes_d="canstantine")
        self.assertEqual(S.aligner(dict(avant), avant), {})
        self.assertEqual(S.aligner(dict(avant, custom_state_d=""), avant), {"state": ""})      # gouvernorat effacé sur la fiche
        # jamais de graphie tunisienne imposée à l'étranger
        self.assertEqual(S.aligner(adr(country="Algeria", state="tunis", city="x")), {"custom_state_d": "tunis", "custom_villes_d": "x"})

    def test_changement_de_pays_par_la_fiche(self):
        """Tunisie -> Algérie : la ville saisie dans « Villes Hors Tunisie » fait foi, le gouvernorat tunisien ne
        survit pas, les champs « Tunisie » sont vidés."""
        avant = adr(state="Ariana", city="Soukra", custom_state_s="Ariana", custom_villes_s="Soukra")
        apres = dict(avant, country="Algeria", custom_villes_d="Oran")
        self.assertEqual(S.aligner(apres, avant), {"state": "", "city": "Oran", "custom_state_s": "", "custom_villes_s": ""})

    def test_changement_de_pays_par_un_programme(self):
        """Seul le pays change, aucun champ de la fiche renseigné pour ce pays : on garde la ville connue."""
        avant = adr(state="Ariana", city="Soukra", custom_state_s="Ariana", custom_villes_s="Soukra")
        apres = dict(avant, country="Algeria")
        self.assertEqual(S.aligner(apres, avant), {"custom_state_d": "Ariana", "custom_villes_d": "Soukra",
                                                   "custom_state_s": "", "custom_villes_s": ""})


class TestSecteurPure(unittest.TestCase):
    def test_secteur_manquant(self):
        self.assertEqual(S.secteur_a_poser(adr(state="Ariana", city="Soukra")), "Secteur 1")
        self.assertIsNone(S.secteur_a_poser(adr(state="Ariana", city="inconnu")))            # l'ancien script plantait
        self.assertIsNone(S.secteur_a_poser(adr()))
        self.assertEqual(S.secteur_a_poser(adr(country="China", city="Zhejiang")), "Hors Secteur")

    def test_secteur_choisi_a_la_main_reste(self):
        avant = adr(state="Tunis", city="Le Bardo", custom_secteur="Secteur 4")              # la table dit Secteur 3
        self.assertIsNone(S.secteur_a_poser(dict(avant), avant))
        self.assertIsNone(S.secteur_a_poser(adr(state="Tunis", city="Le Bardo", custom_secteur="Secteur 4")))   # création

    def test_ville_changee(self):
        avant = adr(state="Ariana", city="Soukra", custom_secteur="Secteur 1")
        self.assertEqual(S.secteur_a_poser(dict(avant, city="Ettadhamen"), avant), "Secteur 3")
        # ...sauf si le secteur a été choisi dans le même enregistrement
        self.assertIsNone(S.secteur_a_poser(dict(avant, city="Ettadhamen", custom_secteur="Secteur 7"), avant))
        # ville inconnue de la table : le secteur reste
        self.assertIsNone(S.secteur_a_poser(dict(avant, city="Ailleurs"), avant))

    def test_pays_change(self):
        avant = adr(state="Ariana", city="Soukra", custom_secteur="Secteur 1")
        self.assertEqual(S.secteur_a_poser(dict(avant, country="Algeria", state="", city="Oran"), avant), "Hors Secteur")
        avant = adr(country="Algeria", city="Oran", custom_secteur="Hors Secteur")
        self.assertEqual(S.secteur_a_poser(dict(avant, country=TN, state="Tunis", city="La Marsa"), avant), "Secteur 1")


class TestRattrapagePure(unittest.TestCase):
    def test_champs_de_la_fiche_remplis(self):
        a = adr(state="Ariana", city="Cité Ennasr 1", custom_secteur="Secteur 2")
        self.assertEqual(P.corrections(a), {"custom_state_s": "Ariana", "custom_villes_s": "Cité Ennasr 1"})

    def test_deja_bon(self):
        a = adr(state="Ariana", city="Soukra", custom_state_s="Ariana", custom_villes_s="Soukra", custom_secteur="Secteur 1")
        self.assertEqual(P.corrections(a), {})

    def test_contradiction_la_fiche_client_fait_foi(self):
        """« Pour ces cas prends les valeurs de la fiche client » : le standard, et le secteur qui en découle."""
        a = adr(state="Nabeul", city="Hammamet", custom_state_s="Tunis", custom_villes_s="Tunis", custom_secteur="Secteur 4")
        self.assertEqual(P.corrections(a), {"custom_state_s": "Nabeul", "custom_villes_s": "Hammamet", "custom_secteur": "Secteur 9"})
        a = adr(state="Medenine", city="Zarzis", custom_state_s="Tunis", custom_villes_s="Tunis", custom_secteur="Secteur 4")
        self.assertEqual(P.corrections(a), {"custom_state_s": "Medenine", "custom_villes_s": "Zarzis", "custom_secteur": "Hors Secteur"})
        # la ville seule se contredit, même secteur : seul le champ de la fiche change
        a = adr(state="Sousse", city="Kalaa Kebira", custom_state_s="Sousse", custom_villes_s="Sousse Medina", custom_secteur="Hors Secteur")
        self.assertEqual(P.corrections(a), {"custom_villes_s": "Kalaa Kebira"})
        # un secteur choisi à la main, qui ne vient ni de l'un ni de l'autre, reste
        a = adr(state="Nabeul", city="Hammamet", custom_state_s="Tunis", custom_villes_s="Tunis", custom_secteur="Secteur 7")
        self.assertEqual(P.corrections(a), {"custom_state_s": "Nabeul", "custom_villes_s": "Hammamet"})

    def test_secteur_a_la_main_sans_contradiction_reste(self):
        a = adr(state="Tunis", city="Le Bardo", custom_state_s="Tunis", custom_villes_s="Le Bardo", custom_secteur="Secteur 4")   # table : Secteur 3
        self.assertEqual(P.corrections(a), {})

    def test_graphie_et_secteur_manquant(self):
        a = adr(state="ARIANA", city="La Soukra")
        self.assertEqual(P.corrections(a), {"state": "Ariana", "custom_state_s": "Ariana", "custom_villes_s": "La Soukra"})
        a = adr(state="Ariana", city="Borj\xa0touil", custom_secteur="Secteur 2")
        self.assertEqual(P.corrections(a), {"custom_state_s": "Ariana", "city": "Borj touil", "custom_villes_s": "Borj touil"})
        a = adr(state="Tunis", city="La Marsa", custom_state_s="Tunis", custom_villes_s="La Marsa")
        self.assertEqual(P.corrections(a), {"custom_secteur": "Secteur 1"})

    def test_sans_gouvernorat(self):
        a = adr(city="inconnu")
        self.assertEqual(P.corrections(a), {"custom_villes_s": "inconnu"})

    def test_hors_tunisie(self):
        a = adr(country="China", city="Zhejiang")
        self.assertEqual(P.corrections(a), {"custom_villes_d": "Zhejiang", "custom_secteur": "Hors Secteur"})
        a = adr(country="Algeria", state="Rouached", city="Rouached", custom_villes_d="Rouached", custom_secteur="Hors Secteur")
        self.assertEqual(P.corrections(a), {"custom_state_d": "Rouached"})

    def test_apres_le_patch_plus_rien_a_faire(self):
        """Le patch ne crée pas lui-même de contradiction : rejoué sur son propre résultat, il ne change rien."""
        for a in (adr(state="ARIANA", city="La Soukra"), adr(state="Ariana", city="Borj\xa0touil"), adr(city="Tunisi"),
                  adr(country="Senegal", city="Dakar", custom_villes_d="Dakar")):
            a.update(P.corrections(a))
            self.assertEqual(P.corrections(a), {}, a)
            self.assertFalse(a.get("custom_state_s") and a.get("state") and a["custom_state_s"] != a["state"], a)


class TestDocument(unittest.TestCase):
    """Vraies adresses enregistrées sur le site, annulées ensuite (lanceur sans commit)."""

    @classmethod
    def setUpClass(cls):
        import frappe
        if not getattr(frappe.local, "site", None):
            raise unittest.SkipTest("sans site")
        cls.frappe = frappe

    def setUp(self):
        f = self.frappe
        f.set_user("Administrator")
        f.db.savepoint("adresse_sectorisation_test")
        P._desactiver_scripts()                      # les anciens scripts en base, comme après le patch

    def tearDown(self):
        self.frappe.db.rollback(save_point="adresse_sectorisation_test")
        self.frappe.cache.delete_value("server_script_map")

    def _creer(self, **k):
        f = self.frappe
        doc = f.get_doc(dict({"doctype": "Address", "address_title": "ESSAI-SECT-" + f.generate_hash(length=8),
                              "address_type": "Billing", "address_line1": "12 rue d’essai", "country": TN}, **k))
        doc.insert(ignore_permissions=True)
        return f.get_doc("Address", doc.name)

    def test_saisie_rapide_du_client(self):
        """Le chemin d'ERPNext (make_address) : state / city seulement."""
        a = self._creer(state="Ariana", city="Cité Ennasr 1")
        self.assertEqual((a.custom_state_s, a.custom_villes_s, a.custom_secteur), ("Ariana", "Cité Ennasr 1", "Secteur 2"))

    def test_ancienne_adresse_puis_telephone(self):
        """Le cas de la capture : champs de la fiche vides en base, on ajoute un téléphone — rien ne se perd."""
        f = self.frappe
        a = self._creer(state="Ariana", city="Cité Ennasr 1")
        f.db.set_value("Address", a.name, {"custom_state_s": None, "custom_villes_s": None}, update_modified=False)
        a = f.get_doc("Address", a.name)
        a.phone = "71000000"
        a.save(ignore_permissions=True)
        a = f.get_doc("Address", a.name)
        self.assertEqual((a.state, a.city, a.custom_state_s, a.custom_villes_s, a.custom_secteur),
                         ("Ariana", "Cité Ennasr 1", "Ariana", "Cité Ennasr 1", "Secteur 2"))

    def test_la_fiche_change_la_ville(self):
        a = self._creer(custom_state_s="Ariana", custom_villes_s="Soukra")
        self.assertEqual((a.state, a.city, a.custom_secteur), ("Ariana", "Soukra", "Secteur 1"))
        a.custom_villes_s = "Ettadhamen"
        a.save(ignore_permissions=True)
        self.assertEqual((a.city, a.custom_secteur), ("Ettadhamen", "Secteur 3"))

    def test_couple_inconnu_sans_erreur(self):
        """L'ancien Server Script levait KeyError ; l'adresse s'enregistre, secteur laissé vide."""
        a = self._creer(state="Ariana", city="Quartier inconnu")
        self.assertEqual((a.custom_state_s, a.custom_villes_s), ("Ariana", "Quartier inconnu"))
        self.assertFalse(a.custom_secteur)

    def test_adresse_etrangere_reste_etrangere(self):
        a = self._creer(country="Algeria", city="Oran")
        self.assertEqual((a.country, a.custom_villes_d, a.custom_secteur), ("Algeria", "Oran", "Hors Secteur"))
        self.assertFalse(a.custom_villes_s)
        a.phone = "71000000"
        a.save(ignore_permissions=True)
        self.assertEqual((a.country, a.city, a.custom_villes_d), ("Algeria", "Oran", "Oran"))

    def test_patch_sur_la_base(self):
        """Le patch, rejoué sur la copie : plus aucune adresse tunisienne avec un champ de la fiche vide alors que
        le standard est rempli, plus aucune contradiction, et les champs standard n'ont pas bougé."""
        f = self.frappe
        f.db.set_value("Address", self._creer(state="Nabeul", city="Hammamet").name,
                       {"custom_state_s": "Tunis", "custom_villes_s": "Tunis", "custom_secteur": "Secteur 4"}, update_modified=False)
        avant = {r.name: (r.state, r.city) for r in P.contradictoires()}
        self.assertTrue(avant)
        P.execute()
        reste = f.db.sql("""select count(*) from tabAddress where country = %s and
            ((ifnull(custom_state_s, '') = '' and ifnull(state, '') <> '') or (ifnull(custom_villes_s, '') = '' and ifnull(city, '') <> ''))""", TN)[0][0]
        self.assertEqual(reste, 0)
        self.assertEqual(P.contradictoires(), [])
        for nom, (gouv, ville) in avant.items():
            a = f.db.get_value("Address", nom, ["state", "city", "custom_state_s", "custom_villes_s", "custom_secteur"], as_dict=True)
            self.assertEqual((a.state, a.city, a.custom_state_s, a.custom_villes_s), (gouv, ville, gouv, ville), nom)
            self.assertEqual(a.custom_secteur, S.secteur_attendu(gouv, ville), nom)
        self.assertFalse(f.db.get_value("Client Script", P.CLIENT_SCRIPT, "enabled"))
        self.assertTrue(f.db.get_value("Server Script", P.SERVER_SCRIPT, "disabled"))


if __name__ == "__main__":
    unittest.main()
