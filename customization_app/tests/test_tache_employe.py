"""Le nom affiché sur la tâche suit toujours le choix du staff (04/10/2026)."""
import unittest
from unittest import mock

from customization_app import tache_employe as T


class TestNomAttendu(unittest.TestCase):

    def test_nom_de_la_fiche_employe(self):
        self.assertEqual(T.nom_attendu("HR-EMP-00010", "Akram"), "Akram")

    def test_sans_staff_le_nom_est_vide(self):
        self.assertEqual(T.nom_attendu(None, "Akram"), "")
        self.assertEqual(T.nom_attendu("", None), "")

    def test_fiche_sans_nom_retombe_sur_l_identifiant(self):
        self.assertEqual(T.nom_attendu("HR-EMP-00099", None), "HR-EMP-00099")


class TestAlignement(unittest.TestCase):

    def _doc(self, staff, nom):
        return _Doc(staff, nom)

    def test_corrige_un_nom_perime_et_un_nom_vide(self):
        with mock.patch.object(T, "frappe") as fr:
            fr.db.get_value.return_value = "Akram"
            d = self._doc("HR-EMP-00010", "Mohamed Hedi Chouchane")
            T.aligner_nom_employe(d)
            self.assertEqual(d.get("custom_employé"), "Akram")
            d = self._doc("HR-EMP-00010", None)
            T.aligner_nom_employe(d)
            self.assertEqual(d.get("custom_employé"), "Akram")

    def test_vide_le_nom_quand_le_staff_est_retire(self):
        d = self._doc(None, "Akram")
        T.aligner_nom_employe(d)
        self.assertEqual(d.get("custom_employé"), "")


class _Doc(dict):
    def __init__(self, staff, nom):
        super().__init__(custom_choix_du_staff=staff)
        self["custom_employé"] = nom

    def set(self, k, v):
        self[k] = v


if __name__ == "__main__":
    unittest.main()
