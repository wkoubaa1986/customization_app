"""Position du téléphone à chaque action (20/11/2026) : ce qu'on garde, ce que ça désigne. Sans la base."""
import unittest
from datetime import datetime, timedelta

from customization_app import flotte_gps as fg
from customization_app import flotte_positions as fp

H = lambda hh, mm: datetime(2026, 11, 20, hh, mm)          # noqa: E731
MAGASIN = (36.86684, 10.25487)
CHEZ_A = (36.76408, 10.28494)
CHEZ_B = (36.79570, 10.14557)


def _arret(point, h1, h2, dm=0.0):
    return {"arr": h1, "dep": h2, "lat": point[0] + dm / 111_000, "lng": point[1], "minutes": int((h2 - h1).total_seconds() // 60)}


class TestAcceptable(unittest.TestCase):
    def test_precision_et_territoire(self):
        self.assertEqual(fp.acceptable(36.8, 10.2, 25), (True, ""))
        self.assertEqual(fp.acceptable(36.8, 10.2, 200), (True, ""))
        ok, raison = fp.acceptable(36.8, 10.2, 1800)                   # position réseau : 1-3 km
        self.assertFalse(ok); self.assertIn("trop imprécis", raison)
        self.assertFalse(fp.acceptable(36.8, 10.2, 0)[0])                # précision inconnue
        self.assertFalse(fp.acceptable(48.85, 2.35, 10)[0])              # Paris

    def test_position_du_client_depuis_le_telephone(self):
        releves = [{"reference_name": "T1", "lat": 36.80, "lng": 10.20, "precision": 90},
                   {"reference_name": "T1", "lat": 36.81, "lng": 10.21, "precision": 15},
                   {"reference_name": "T2", "lat": 36.82, "lng": 10.22, "precision": 12}]
        self.assertEqual(fp.position_telephone(releves, "T1"), (36.81, 10.21))     # le plus précis
        self.assertIsNone(fp.position_telephone(releves, "T1", precision_max=10))  # pas assez précis pour placer un client
        self.assertIsNone(fp.position_telephone(releves, "T9"))
        self.assertIsNone(fp.position_telephone([], "T1"))


class TestIndicesVehicule(unittest.TestCase):
    """Deux véhicules : la 232 chez A de 9h à 9h40, la 3554 chez B de 9h à 10h ; les deux au Magasin de 12h à 13h."""

    def journaux(self):
        return {"232": {"erreur": None, "arrets": [_arret(CHEZ_A, H(9, 0), H(9, 40)), _arret(MAGASIN, H(12, 0), H(13, 0))]},
                "3554": {"erreur": None, "arrets": [_arret(CHEZ_B, H(9, 0), H(10, 0)), _arret(MAGASIN, H(12, 0), H(13, 0))]}}

    def test_photo_sans_position_un_seul_vehicule_a_l_arret(self):
        # 9h50 : la 232 est repartie (arrêt fini il y a 10 min : « juste avant », encore possible), la 3554 est chez B
        # → deux possibles, rien ; 9h20 : les deux arrêtées → rien ; 9h55 avec un seul arrêt « juste avant » → 232
        s = fg.indices_vehicule([{"employe": "E", "heure": H(9, 20)}], self.journaux(), 150, [MAGASIN])
        self.assertEqual(s, {})
        j = {"232": self.journaux()["232"]}
        self.assertEqual(fg.indices_vehicule([{"employe": "E", "heure": H(9, 20)}], j, 150, [MAGASIN]), {("E", "232"): 1})

    def test_photo_au_magasin_ne_compte_pas(self):
        s = fg.indices_vehicule([{"employe": "E", "heure": H(12, 30)}], {"232": self.journaux()["232"]}, 150, [MAGASIN])
        self.assertEqual(s, {})

    def test_position_du_telephone_departage(self):
        # 9h20, les deux véhicules arrêtés : le téléphone est chez B → c'est la 3554 (poids 2)
        s = fg.indices_vehicule([{"employe": "E", "heure": H(9, 20), "lat": CHEZ_B[0], "lng": CHEZ_B[1]}], self.journaux(), 150, [MAGASIN])
        self.assertEqual(s, {("E", "3554"): 2})
        # au Magasin, les deux à côté du téléphone : rien
        s = fg.indices_vehicule([{"employe": "E", "heure": H(12, 30), "lat": MAGASIN[0], "lng": MAGASIN[1]}], self.journaux(), 150, [MAGASIN])
        self.assertEqual(s, {})
        # téléphone loin de tout véhicule (à pied, autre voiture) : rien
        s = fg.indices_vehicule([{"employe": "E", "heure": H(9, 20), "lat": 36.9, "lng": 10.4}], self.journaux(), 150, [MAGASIN])
        self.assertEqual(s, {})

    def test_un_arret_court_compte_avec_la_position_pas_avec_la_photo_seule(self):
        j = {"3554": {"erreur": None, "arrets": [_arret(CHEZ_B, H(12, 13), H(12, 16))]}}       # 3 minutes
        self.assertEqual(fg.indices_vehicule([{"employe": "E", "heure": H(12, 14)}], j, 150, [MAGASIN]), {})
        self.assertEqual(fg.indices_vehicule([{"employe": "E", "heure": H(12, 14), "lat": CHEZ_B[0], "lng": CHEZ_B[1]}], j, 150, [MAGASIN]),
                         {("E", "3554"): 2})

    def test_les_indices_entrent_dans_l_attribution(self):
        # aucune tâche positionnée (client inconnu), aucun défaut : sans indice E n'a pas de véhicule ; avec, la 3554
        taches = [{"name": "T", "employe": "E", "lat": None, "lng": None, "debut": H(9, 0), "statut": "Open"}]
        sans = fg.vehicules_du_jour(taches, self.journaux(), {}, 150, [MAGASIN])
        self.assertEqual(sans, {})
        avec = fg.vehicules_du_jour(taches, self.journaux(), {}, 150, [MAGASIN],
                                    indices=[{"employe": "E", "heure": H(9, 20), "lat": CHEZ_B[0], "lng": CHEZ_B[1]}])
        self.assertEqual(avec, {"E": "3554"})
        # deux techniciens dans la même voiture, chacun reconnu par son téléphone : le second reçoit le même véhicule
        # seulement par l'équipage — un véhicule ne va qu'à un employé par les scores
        deux = fg.vehicules_du_jour(taches + [{"name": "T2", "employe": "F", "lat": None, "lng": None, "debut": H(9, 0), "statut": "Open"}],
                                    self.journaux(), {}, 150, [MAGASIN],
                                    indices=[{"employe": "E", "heure": H(9, 20), "lat": CHEZ_B[0], "lng": CHEZ_B[1]},
                                             {"employe": "F", "heure": H(9, 25), "lat": CHEZ_B[0], "lng": CHEZ_B[1]}])
        self.assertEqual(deux["E"], "3554")
        self.assertNotIn("F", deux)

    def test_indices_depuis_les_taches_et_les_releves(self):
        taches = [{"name": "T1", "employe": "E"}, {"name": "T2", "employe": None}]
        out = fg._indices(taches, {"T1": H(9, 5), "T2": H(9, 6)}, [{"employe": "F", "heure": H(10, 0), "lat": 1.0, "lng": 2.0}])
        self.assertEqual(out, [{"employe": "E", "heure": H(9, 5)}, {"employe": "F", "heure": H(10, 0), "lat": 1.0, "lng": 2.0}])


if __name__ == "__main__":
    unittest.main()
