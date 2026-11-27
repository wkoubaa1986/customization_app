"""Un BL ne sort pas moins cher que sa commande parce que la règle de prix s'est réappliquée (27/11/2026), et le
verrou « total des paiements = total de la commande » d'une commande validée. Fonctions pures."""
import unittest

from customization_app import bl_prix_commande as B
from customization_app import echeancier as E

REGLE = '[\n "PRLE-0214"\n]'


def ligne(rate, plr, remise=0.0, regles=REGLE, **plus):
    return {"rate": rate, "price_list_rate": plr, "discount_percentage": remise, "pricing_rules": regles, **plus}


class TestPrixARependre(unittest.TestCase):
    def test_le_cas_amri_sadok(self):
        # Commande : PP relevée à 3,000 et UDF à 4,500 par la marge minimale ; BL : la règle (−5 %) est revenue.
        self.assertEqual(B.prix_a_reprendre(ligne(2.66, 2.8, 5.0), 3.0), 3.0)
        self.assertEqual(B.prix_a_reprendre(ligne(4.275, 4.5, 5.0), 4.5), 4.5)

    def test_meme_prix_que_la_commande_rien_a_faire(self):
        self.assertIsNone(B.prix_a_reprendre(ligne(4.513, 4.75, 5.0), 4.513))          # CTO : remise voulue des deux côtés
        self.assertIsNone(B.prix_a_reprendre(ligne(7.41, 7.8, 5.0), 7.41))

    def test_un_prix_tape_sur_le_bl_reste(self):
        # 2,500 n'est pas le prix de la règle (2,660) : quelqu'un l'a tapé, « c'est le BL qui fait foi ».
        self.assertIsNone(B.prix_a_reprendre(ligne(2.5, 2.8, 5.0), 3.0))

    def test_bl_plus_cher_que_la_commande_reste(self):
        self.assertIsNone(B.prix_a_reprendre(ligne(250.0, 250.0), 220.0))

    def test_sans_regle_de_prix_ou_gratuit_reste(self):
        self.assertIsNone(B.prix_a_reprendre(ligne(25.21, 30.0, 0.0, regles=None), 30.0))    # M-E-OD : prix HT, pas une règle
        self.assertIsNone(B.prix_a_reprendre(ligne(25.21, 30.0, 0.0, regles=""), 30.0))
        self.assertIsNone(B.prix_a_reprendre(ligne(25.21, 30.0, 0.0, regles="[]"), 30.0))
        self.assertIsNone(B.prix_a_reprendre(ligne(2.66, 2.8, 5.0, is_free_item=1), 3.0))
        self.assertIsNone(B.prix_a_reprendre(ligne(2.66, 0.0, 5.0), 3.0))                     # pas de prix de liste
        self.assertIsNone(B.prix_a_reprendre(ligne(2.66, 2.8, 5.0), 0))                       # commande à 0 DT

    def test_majoration_carte_perdue_sur_le_bl(self):
        # Commande : 4,275 majoré de 2 % = 4,361 ; BL : la règle redonne 4,275.
        self.assertEqual(B.prix_a_reprendre(ligne(4.275, 4.5, 5.0), 4.361), 4.361)

    def test_prix_de_la_regle(self):
        self.assertAlmostEqual(B.prix_de_la_regle(ligne(0, 2.8, 5.0)), 2.66, 6)
        self.assertAlmostEqual(B.prix_de_la_regle(ligne(0, 4.5, 0.0)), 4.5, 6)
        self.assertIsNone(B.prix_de_la_regle(ligne(0, 4.5, 5.0, regles=None)))


class TestVerrouEcheancier(unittest.TestCase):
    AMRI = [{"payment_amount": 183.415}, {"payment_amount": 53.2}]

    def test_echeancier_egal_au_total(self):
        self.assertIsNone(E.ecart_echeancier(self.AMRI, 236.615, 236.615, sans_arrondi=True))
        self.assertIsNone(E.ecart_echeancier(self.AMRI, 0, 236.615))                    # pas de total arrondi : le TTC

    def test_arrondi_au_dinar_admis(self):
        # Citrus hygiene : TTC 3 895,400, arrondi 3 895,000, échéancier 3 895,000.
        self.assertIsNone(E.ecart_echeancier([{"payment_amount": 3895.0}], 3895.0, 3895.4))
        self.assertIsNone(E.ecart_echeancier([{"payment_amount": 238.05}], 238.0, 238.5))    # 0,050 : sous la tolérance

    def test_echeancier_different_refuse(self):
        self.assertEqual(E.ecart_echeancier([{"payment_amount": 150.0}, {"payment_amount": 53.2}], 236.615, 236.615, True), -33.415)
        self.assertEqual(E.ecart_echeancier([{"payment_amount": 3895.4}], 3895.0, 3895.4), 0.4)     # compare à l'arrondi
        self.assertEqual(E.ecart_echeancier([], 100.0, 100.0), -100.0)                              # échéancier vidé
        self.assertEqual(E.ecart_echeancier(self.AMRI + [{"payment_amount": 10.0}], 236.615, 236.615, True), 10.0)


class TestVerrouExemptions(unittest.TestCase):
    """Le verrou se tait pour un TRAITEMENT en cours, plus pour une commande marquée à vie par l'outil."""

    def test_exemptions(self):
        self.assertFalse(E.verrou_exempte(False, False, False))            # commande ordinaire… ou marquée par l'outil
        self.assertTrue(E.verrou_exempte(False, True, False))              # un encaissement se valide en ce moment
        self.assertTrue(E.verrou_exempte(False, False, True))              # régularisation d'une livraison partielle
        self.assertTrue(E.verrou_exempte(True, False, False))              # commande née d'un entretien

    def test_un_enregistrement_qui_ne_touche_a_rien(self):
        class D(dict):
            def get(self, k, d=None): return dict.get(self, k, d)

        class L:
            def __init__(self, m): self.payment_amount = m
        avant = D(grand_total=236.615, rounded_total=237.0, payment_schedule=[L(183.415), L(20.0)])
        meme = D(grand_total=236.615, rounded_total=237.0, payment_schedule=[L(203.415)])            # même somme
        echeancier_change = D(grand_total=236.615, rounded_total=237.0, payment_schedule=[L(183.415), L(30.0)])
        total_change = D(grand_total=300.0, rounded_total=300.0, payment_schedule=[L(183.415), L(20.0)])
        self.assertTrue(E.rien_n_a_bouge(avant, meme))
        self.assertFalse(E.rien_n_a_bouge(avant, echeancier_change))
        self.assertFalse(E.rien_n_a_bouge(avant, total_change))
        self.assertFalse(E.rien_n_a_bouge(None, meme))                     # pas d'état d'avant : on contrôle


class TestBranchements(unittest.TestCase):
    """Ce que hooks.py et les classes surchargées appellent existe (une fonction retirée par mégarde faisait échouer
    tout enregistrement de BL — 27/11/2026)."""

    def test_les_fonctions_appelees_existent(self):
        for module, noms in ((B, ("delivery_note_validate", "apres_valeurs_manquantes", "corriger_bl_valides", "aligner")),
                             (E, ("verifier_total_echeancier", "marquer_outil_encaissement", "exiger_mode_de_paiement"))):
            for nom in noms:
                self.assertTrue(callable(getattr(module, nom, None)), nom)

    def test_hooks_et_classes(self):
        import inspect

        from customization_app import hooks, retour_echange

        texte = inspect.getsource(hooks)
        for chemin in ("customization_app.bl_prix_commande.delivery_note_validate",
                       "customization_app.echeancier.verifier_total_echeancier",
                       "customization_app.echeancier.marquer_outil_encaissement"):
            self.assertIn(chemin, texte)
        for classe in (retour_echange.SalesInvoiceEchange, retour_echange.DeliveryNoteEchange):
            self.assertIn("set_missing_values", classe.__dict__, classe.__name__)


class TestScriptsVersionnes(unittest.TestCase):
    """Deux défauts des Server Scripts corrigés le 27/11/2026 : ils ne doivent pas revenir."""

    @classmethod
    def setUpClass(cls):
        import json
        import os

        chemin = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "server_script.json")
        cls.scripts = {s["name"]: s for s in json.load(open(chemin, encoding="utf-8"))}

    def test_un_script_d_evenement_n_annule_ni_ne_valide_la_transaction(self):
        # `frappe.db.rollback`, `commit` et `set_single_value` n'existent pas dans un script d'événement : le
        # gestionnaire d'erreur de l'encaissement échouait lui-même et masquait la vraie cause.
        for nom, s in self.scripts.items():
            if s.get("script_type") != "DocType Event" or s.get("disabled"):
                continue
            code = "\n".join(l for l in s["script"].split("\n") if not l.strip().startswith("#"))
            for interdit in ("frappe.db.rollback(", "frappe.db.commit(", "frappe.db.set_single_value("):
                self.assertNotIn(interdit, code, "%s : %s" % (nom, interdit))

    def test_la_marque_outil_de_paiement_est_lue_comme_un_nombre_et_remise_a_zero(self):
        # Champ texte : relue de la base elle vaut « 1 », pas l'entier 1 (« Régulariser » échouait).
        script = self.scripts["re-generate payment after sales order"]["script"]
        self.assertIn("frappe.utils.cint(doc.custom_outil_de_paiement)==1", script)
        self.assertNotIn("elif doc.custom_outil_de_paiement==1", script)
        self.assertIn('frappe.db.set_value("Sales Order", doc.name, "custom_outil_de_paiement", 0', script)

    def test_l_erreur_de_l_encaissement_remonte(self):
        fin = self.scripts["Traitement des encaissement"]["script"].rstrip()
        self.assertTrue(fin.endswith("raise"))
        self.assertIn("except Exception as e_enc:", fin)


class TestMietteSurCommandeFacturee(unittest.TestCase):
    """Chèque 9746703 de BEN Sassi BIOAQUA (27/11/2026) : 0,001 du chèque posé sur une commande facturée à 100 %
    faisait échouer tout l'encaissement. Les deux boucles (chèques, traites) laissent ce millime non affecté."""

    def test_les_deux_boucles_laissent_la_miette(self):
        import json
        import os

        chemin = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "server_script.json")
        script = next(s for s in json.load(open(chemin, encoding="utf-8")) if s["name"] == "Traitement des encaissement")["script"]
        self.assertEqual(script.count("for ipay in unique_ref:"), 2)
        self.assertEqual(script.count('frappe.utils.flt(unique_ref[ipay][3])<0.01'), 2)
        self.assertEqual(script.count('"per_billed"))>=99.99'), 2)
        # La garde précède la création de la référence, dans chaque boucle.
        for bloc in script.split("for ipay in unique_ref:")[1:]:
            self.assertLess(bloc.index("continue"), bloc.index('frappe.new_doc("Payment Entry Reference")'))
