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
        "total_paid": 20, "status": "À facturer", "tache_reference": "Tache-ESSAI", "tache_employee": "Essai",
        "tache_status": "Completed", "warnings": [],
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
        self.assertIn("tâche Tache-ESSAI (Essai) terminée", html)   # statut de tâche en français
        self.assertIn("id='footer-html'", html)              # pied paginé
        self.assertIn("class='topage'", html)
        self.assertIn("accord Jamel", html)                  # point de contrôle : commentaire repris

    def test_ecart_et_remise(self):
        html = CL._html_instantane(_cloture(especes_comptees=140, ecart=-4.25, ecart_remise=-1), DONNEES)
        self.assertIn("Manque de caisse", html)
        self.assertIn("écart de remise", html)
        self.assertIn("verdict ko", html)

    def test_justification_sous_sa_commande(self):
        ctl = ("49,800 DT en dette — SAL-ORD-2026-04778 · CLIENT\n  → Client professionnel — accord Jamel\n"
               "Paiement ACC-PAY-9 exclu de la caisse\n  → Déjà encaissé — chèque")
        html = CL._html_instantane(_cloture(controles=ctl), DONNEES)
        self.assertIn("class='ijustif'", html)
        self.assertIn("⚑ 49,800 DT en dette</div>", html)         # libellé raccourci, sous la commande
        self.assertIn("Autres points de contrôle justifiés", html)  # le point sans commande reste en bas
        self.assertIn("Paiement ACC-PAY-9 exclu", html)

    def test_points_du_texte(self):
        self.assertEqual(CL.points_du_texte("A — SAL-ORD-2026-1\n  → Motif — mon commentaire\nB\n  → Seul"),
                         [("A — SAL-ORD-2026-1", [("Motif", "mon commentaire")]), ("B", [("Seul", "")])])
        self.assertEqual(CL.points_du_texte(None), [])

    def test_texte_trace(self):
        noms = {"x@y.com": "Jamel Aloui"}.get
        t = CL._texte_trace("<p>🔁 Transférée par x@y.com : A&amp;S → B ✅ (3,800 د.ت)</p>", noms)
        self.assertEqual(t, "Transférée par Jamel Aloui : A&S → B (3,800 DT)")
        self.assertTrue(CL._texte_trace("a" * 500, noms).endswith("…"))

    def test_compte_par_commande_et_par_mode(self):
        o = {"payments": [{"mode": "Espèces", "amount": 826, "hors_periode": True},
                          {"mode": "Espèces", "amount": 394}, {"mode": "Carte de crédit", "amount": 10.5},
                          {"mode": "Espèces", "amount": 0.25}]}
        self.assertEqual(CL.compte_commande(o), {"Espèces": 394.25, "Carte de crédit": 10.5})   # avance exclue
        self.assertEqual(CL.compte_commande({}), {})
        self.assertEqual(CL.ordre_modes({"Dette non payée", "Zzz", "Espèces"}), ["Espèces", "Dette non payée", "Zzz"])

    def test_calcul_dans_le_pdf(self):
        donnees = dict(DONNEES, anciens={"paiements": [
            {"saisi_par": "Caisse Essai", "mode": "Espèces", "amount": 112, "name": "ACC-PAY-2", "date": "2026-11-21"},
            {"saisi_par": "Caisse Essai", "mode": "Espèces", "amount": 50, "exclu": True, "name": "ACC-PAY-3"}],
            "total": 112})
        donnees["employees"] = [dict(DONNEES["employees"][0], orders=[dict(
            DONNEES["employees"][0]["orders"][0], payments=[
                {"name": "ACC-PAY-0", "mode": "Espèces", "amount": 826, "hors_periode": True, "date": "2026-11-01"},
                {"name": "ACC-PAY-1", "mode": "Espèces", "amount": 20, "date": "2026-11-23"}])])]
        html = CL._html_instantane(_cloture(), donnees)
        self.assertIn("Calcul de la caisse — Caisse Essai", html)
        self.assertIn("non compté<br>avance d'avant la période", html)
        self.assertIn("Compté dans la caisse pour cette commande (Espèces)", html)
        self.assertIn("Paiements sur anciennes commandes</td>", html)
        self.assertIn("<b>132,000</b>", html)      # total Espèces = 20 + 112 (l'avance et l'exclu ne comptent pas)

    def test_validee_seule(self):
        html = CL._html_instantane(_cloture(validation_seule=1, collecte_par=None), DONNEES)
        self.assertIn("Validée seule par Essai Caissier", html)
        self.assertNotIn("Remise à la collecte", html)
