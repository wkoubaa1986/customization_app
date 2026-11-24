"""Le PDF de clôture refondu (24/11/2026) : en-tête, verdict, montants en « DT », dates jj/mm/aaaa, pied paginé.

Lancer dans le site (jamais `bench run-tests`) : `unittest customization_app.tests.test_caisse_pdf` depuis
frappe-bench/sites, site initialisé (frappe.utils.get_fullname, logo d'AquaWorld Settings)."""
import unittest

import frappe

from customization_app import caisse_cloture as CL


def _cloture(**autres):
    doc = frappe._dict(
        name="CLO-TEST", caisse="Caisse Essai", date_cloture="2026-11-23", valide_par="Essai Caissier",
        compte_le="2026-11-23 17:15:00", collecte_par="Essai Collecteur", collecte_le="2026-11-23 17:17:00",
        solde_ouverture=0, encaissements_especes=144.25, depenses_especes=0, solde_theorique=144.25,
        especes_comptees=144.25, ecart=0, especes_remises=144.25, especes_recues=144.25, ecart_remise=0,
        fond_conserve=0, validation_seule=0, note="", modified="2026-11-23 17:17:00",
        controles="49,800 DT en dette — SAL-ORD-1 · CLIENT\n  → Client professionnel — accord Jamel",
        echanges="[2026-11-23 09:22] ↩ Essai rouvre son comptage")
    doc.update(autres)
    return doc


DONNEES = {
    "recap": {"par_mode": {"Espèces": 144.25, "Carte de crédit": 91.8}, "grand_total": 236.05},
    "employees": [{"employe": "Caisse Essai", "nb_commandes": 1, "total": 20, "orders": [{
        "sales_order": "SAL-ORD-2026-04778", "customer": "Client", "date": "2026-11-23", "grand_total": 20,
        "total_paid": 20, "status": "À facturer", "tache_status": "Completed", "warnings": [],
        "payments": [{"name": "ACC-PAY-1", "date": "2026-11-23", "mode": "Espèces", "amount": 20,
                      "reference_no": "SAL-ORD-2026-04778"}]}]}],
}


class TestPdfCloture(unittest.TestCase):
    def test_lisible(self):
        html = CL._html_instantane(_cloture(), DONNEES)
        self.assertIn("CLÔTURE DE CAISSE", html)
        self.assertIn("Caisse juste", html)
        self.assertIn("144,250&nbsp;DT", html)
        self.assertNotIn("د.ت", html)
        self.assertIn("Journée du lundi 23/11/2026", html)
        self.assertIn("[23/11/2026 09:22]", html)           # échanges : date lisible
        self.assertIn("tâche terminée", html)                # statut de tâche en français
        self.assertIn("id='footer-html'", html)              # pied paginé
        self.assertIn("class='topage'", html)
        self.assertIn("accord Jamel", html)                  # point de contrôle : commentaire repris

    def test_ecart_et_remise(self):
        html = CL._html_instantane(_cloture(especes_comptees=140, ecart=-4.25, ecart_remise=-1), DONNEES)
        self.assertIn("Manque de caisse", html)
        self.assertIn("écart de remise", html)
        self.assertIn("verdict ko", html)

    def test_validee_seule(self):
        html = CL._html_instantane(_cloture(validation_seule=1, collecte_par=None), DONNEES)
        self.assertIn("Validée seule par Essai Caissier", html)
        self.assertNotIn("Remise à la collecte", html)
