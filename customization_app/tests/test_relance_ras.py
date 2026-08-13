"""Tests de la relance des certificats de retenue (retenue_source.py).

Convention : `unittest.TestCase` pur, aucune base, aucun reseau — seules les regles se testent ici.
"""
from __future__ import annotations

import unittest

from customization_app import retenue_source as RS


class TestSeparationDesDestinataires(unittest.TestCase):
    """⚠️ LA LISTE N'EST PAS UN DESTINATAIRE. `custom_liste_telephone` porte souvent plusieurs
    numeros separes par un retour a la ligne : envoyee en bloc, elle visait « 98366053\\n71854009 »,
    un numero qui n'existe pas — et le SMS partait dans le vide sans que rien ne le signale."""

    def test_les_numeros_separes_par_un_retour_a_la_ligne(self):
        self.assertEqual(RS.separer("98366053\n71854009"), ["98366053", "71854009"])

    def test_les_adresses_separees_par_une_virgule(self):
        self.assertEqual(RS.separer("com-itkan@gnet.tn, khaledsg@itkan.com.tn"),
                         ["com-itkan@gnet.tn", "khaledsg@itkan.com.tn"])

    def test_le_point_virgule_et_les_espaces_multiples(self):
        self.assertEqual(RS.separer(" a@b.tn ;  c@d.tn "), ["a@b.tn", "c@d.tn"])

    def test_une_valeur_vide_ne_donne_aucun_destinataire(self):
        """C'est ce qui fait dire « email manquant » plutot que d'envoyer a une chaine vide."""
        for vide in (None, "", "   ", "\n"):
            self.assertEqual(RS.separer(vide), [])

    def test_un_seul_destinataire_reste_un_destinataire(self):
        self.assertEqual(RS.separer("khaledsg@itkan.com.tn"), ["khaledsg@itkan.com.tn"])


if __name__ == "__main__":
    unittest.main()
