"""Tests du report du lien Google Map de la tâche vers l'adresse (ticket #17).

Le dialogue de clôture exige un lien Google Map et l'écrit sur la Tache de
travail ; il n'atterrissait jamais sur l'Address. À l'intervention suivante à la
même adresse, la tâche naissait sans lien — le technicien devait se relocaliser.

Convention : `unittest.TestCase` pur, aucune base — `frappe.db` est remplacé par
un faux qui retient les écritures. Ce qui se teste est la DÉCISION (écrire ou
non, quel lien), pas le SQL.
"""
from __future__ import annotations

import types
import unittest

import frappe

from customization_app.cloture_tache import (
    CHAMP_GMAP_ADRESSE,
    lien_a_propager,
    propager_google_map_vers_adresse,
)
from customization_app.patches.propager_google_map_taches_vers_adresses import (
    liens_par_adresse,
)

LIEN = "https://maps.google.com/?q=36.8065,10.1815"
AUTRE_LIEN = "https://maps.google.com/?q=35.8256,10.6084"


class TestLienAPropager(unittest.TestCase):
    """La règle nue : on ne remplit que les adresses vides."""

    def test_l_adresse_vide_recoit_le_lien_de_la_tache(self):
        self.assertEqual(lien_a_propager(LIEN, None), LIEN)
        self.assertEqual(lien_a_propager(LIEN, ""), LIEN)
        self.assertEqual(lien_a_propager(LIEN, "   "), LIEN)

    def test_une_adresse_deja_localisee_garde_son_lien(self):
        """Un lien corrigé à la main sur la fiche Address ne doit jamais être
        écrasé par la position du technicien, qui peut être approximative."""
        self.assertIsNone(lien_a_propager(LIEN, AUTRE_LIEN))

    def test_une_tache_sans_lien_ne_propage_rien(self):
        self.assertIsNone(lien_a_propager(None, None))
        self.assertIsNone(lien_a_propager("", None))
        self.assertIsNone(lien_a_propager("  \n ", None))

    def test_deux_liens_identiques_ne_declenchent_pas_d_ecriture(self):
        self.assertIsNone(lien_a_propager(LIEN, LIEN))

    def test_le_lien_est_nettoye_avant_ecriture(self):
        self.assertEqual(lien_a_propager("  %s \n" % LIEN, None), LIEN)


class _FausseBase:
    """`frappe.db` réduit à ce dont la propagation a besoin, plus un journal."""

    def setUp(self):
        self.adresses = {"ADR-001": None}      # lien actuel par adresse
        self.colonne = True                    # custom_lien_google_map existe ?
        self.ecritures = []                    # (doctype, nom, champ, valeur)
        self.erreurs = []                      # appels à frappe.log_error
        self.ecriture_leve = None              # exception à lever à l'écriture

        self._vrais = (frappe.db, frappe.log_error, frappe.get_traceback)
        frappe.db = types.SimpleNamespace(
            has_column=lambda doctype, colonne: self.colonne,
            get_value=self._get_value,
            set_value=self._set_value)
        frappe.log_error = lambda message, titre=None: self.erreurs.append(titre)
        frappe.get_traceback = lambda *a, **k: "traceback"

    def tearDown(self):
        frappe.db, frappe.log_error, frappe.get_traceback = self._vrais

    def _get_value(self, doctype, nom, champ):
        return self.adresses.get(nom)

    def _set_value(self, doctype, nom, champ, valeur, update_modified=True):
        if self.ecriture_leve:
            raise self.ecriture_leve
        self.ecritures.append((doctype, nom, champ, valeur))
        self.adresses[nom] = valeur

    def _tache(self, **champs):
        tache = frappe._dict({"name": "TACHE-0001", "select_address": "ADR-001",
                              "google_map": LIEN, "status": "Completed"})
        tache.update(champs)
        return tache


class TestPropagationDepuisLaTache(_FausseBase, unittest.TestCase):
    def test_la_cloture_localise_l_adresse(self):
        self.assertEqual(propager_google_map_vers_adresse(self._tache()), LIEN)
        self.assertEqual(self.ecritures,
                         [("Address", "ADR-001", CHAMP_GMAP_ADRESSE, LIEN)])

    def test_l_ecriture_ne_touche_pas_a_modified(self):
        """L'Address ne doit pas remonter en tête des « modifiés récemment »
        pour un champ d'appoint — et ses hooks n'ont pas à être rejoués."""
        appels = []
        frappe.db.set_value = lambda *a, **k: appels.append((a, k))
        propager_google_map_vers_adresse(self._tache())
        self.assertEqual(appels[0][1], {"update_modified": False})

    def test_une_adresse_deja_localisee_est_laissee_telle_quelle(self):
        self.adresses["ADR-001"] = AUTRE_LIEN
        self.assertIsNone(propager_google_map_vers_adresse(self._tache()))
        self.assertEqual(self.ecritures, [])

    def test_une_tache_sans_adresse_ne_fait_rien(self):
        self.assertIsNone(
            propager_google_map_vers_adresse(self._tache(select_address=None)))
        self.assertEqual(self.ecritures, [])

    def test_une_tache_sans_lien_ne_fait_rien(self):
        self.assertIsNone(propager_google_map_vers_adresse(self._tache(google_map="")))
        self.assertEqual(self.ecritures, [])

    def test_sans_le_champ_custom_rien_n_est_ecrit(self):
        """Site neuf : les fixtures se synchronisent après les patches."""
        self.colonne = False
        self.assertIsNone(propager_google_map_vers_adresse(self._tache()))
        self.assertEqual(self.ecritures, [])

    def test_une_erreur_d_ecriture_ne_bloque_pas_la_cloture(self):
        """LE POINT SENSIBLE : ce report vit dans before_save. Une adresse
        verrouillée ou disparue ne doit pas faire échouer l'enregistrement de
        l'intervention — elle se journalise, c'est tout."""
        self.ecriture_leve = frappe.DoesNotExistError("adresse disparue")
        self.assertIsNone(propager_google_map_vers_adresse(self._tache()))
        self.assertEqual(self.erreurs, ["propager_google_map_vers_adresse"])


class TestChoixDeLaTacheSourceDuPatch(unittest.TestCase):
    """Le rattrapage : une adresse, plusieurs tâches — laquelle fait foi ?"""

    def _tache(self, name, adresse="ADR-001", lien=LIEN, status="Open",
               modified="2026-01-01 10:00:00"):
        return frappe._dict({"name": name, "select_address": adresse,
                             "google_map": lien, "status": status,
                             "modified": modified})

    def test_la_plus_recente_l_emporte(self):
        liens = liens_par_adresse([
            self._tache("T1", lien=AUTRE_LIEN, modified="2026-01-01 10:00:00"),
            self._tache("T2", lien=LIEN, modified="2026-06-15 08:30:00"),
        ])
        self.assertEqual(liens, {"ADR-001": LIEN})

    def test_une_tache_terminee_prime_sur_une_tache_ouverte_plus_recente(self):
        """Une intervention Completed a vu le technicien sur place ; un rendez-vous
        encore ouvert peut porter un lien provisoire."""
        liens = liens_par_adresse([
            self._tache("T1", lien=LIEN, status="Completed",
                        modified="2026-01-01 10:00:00"),
            self._tache("T2", lien=AUTRE_LIEN, status="Open",
                        modified="2026-06-15 08:30:00"),
        ])
        self.assertEqual(liens, {"ADR-001": LIEN})

    def test_entre_deux_taches_terminees_la_plus_recente_gagne(self):
        liens = liens_par_adresse([
            self._tache("T1", lien=AUTRE_LIEN, status="Completed",
                        modified="2026-01-01 10:00:00"),
            self._tache("T2", lien=LIEN, status="Completed",
                        modified="2026-06-15 08:30:00"),
        ])
        self.assertEqual(liens, {"ADR-001": LIEN})

    def test_chaque_adresse_a_son_lien(self):
        liens = liens_par_adresse([
            self._tache("T1", adresse="ADR-001", lien=LIEN),
            self._tache("T2", adresse="ADR-002", lien=AUTRE_LIEN),
        ])
        self.assertEqual(liens, {"ADR-001": LIEN, "ADR-002": AUTRE_LIEN})

    def test_les_taches_sans_adresse_ou_sans_lien_sont_ecartees(self):
        liens = liens_par_adresse([
            self._tache("T1", adresse=None),
            self._tache("T2", lien=""),
            self._tache("T3", lien="   "),
        ])
        self.assertEqual(liens, {})

    def test_le_lien_retenu_est_nettoye(self):
        liens = liens_par_adresse([self._tache("T1", lien="  %s\n" % LIEN)])
        self.assertEqual(liens, {"ADR-001": LIEN})

    def test_aucune_tache_aucune_adresse(self):
        self.assertEqual(liens_par_adresse([]), {})


if __name__ == "__main__":
    unittest.main()
