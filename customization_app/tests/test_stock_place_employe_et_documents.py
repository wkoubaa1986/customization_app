"""Page Stock (27/11/2026) : valider un transfert à la place de l'employé du véhicule, et les entrées / sorties
regroupées par document. Fonctions pures."""
import unittest

from customization_app import stock_entrepots as S

HEDI, WASSIM, RADHOUAN = "hedi@x", "wassim@x", "radhouan@x"


class TestValiderALaPlaceDeLEmploye(unittest.TestCase):
    def test_un_autre_responsable_que_celui_qui_a_demande(self):
        # MAT-STE-2026-00338 : Hedi demande (côté Magasin), c'est au tour de Radhouan, qui a quitté.
        self.assertTrue(S.peut_valider_pour_employe(S.COTE_EMPLOYE, True, False, HEDI, WASSIM))
        self.assertFalse(S.peut_valider_pour_employe(S.COTE_EMPLOYE, True, False, HEDI, HEDI))      # sa propre demande

    def test_jamais_hors_de_ces_conditions(self):
        self.assertFalse(S.peut_valider_pour_employe(S.COTE_MAGASIN, True, False, RADHOUAN, WASSIM))   # pas le tour de l'employé
        self.assertFalse(S.peut_valider_pour_employe(S.COTE_EMPLOYE, False, False, HEDI, "autre@x"))   # pas responsable magasin
        self.assertFalse(S.peut_valider_pour_employe(S.COTE_EMPLOYE, True, True, HEDI, RADHOUAN))      # son propre véhicule
        self.assertFalse(S.peut_valider_pour_employe(None, True, False, HEDI, WASSIM))                 # transfert sans double validation


def mvt(type_, piece, item, qte, date="2026-11-23", heure="16:42", entrepot="Magasins - A&S", **plus):
    base = {"type": type_, "piece": piece, "libelle": {"Purchase Invoice": "Facture d’achat", "Stock Entry": "Transfert",
                                                        "Delivery Note": "BL"}.get(type_, type_),
            "item_code": item, "item_name": "Nom " + item, "image": None, "uom": "Pièce", "qte": qte, "date": date,
            "heure": heure, "entrepot": entrepot, "de": None, "vers": None, "taches": []}
    base.update(plus)
    return base


class TestParDocument(unittest.TestCase):
    def test_une_piece_ses_articles_en_dessous(self):
        mouvements = [
            mvt("Purchase Invoice", "ACC-PINV-2026-00132", "C-10'-PP-5m", 300, fournisseur="AQUA SERVICE", facture_fournisseur="2605588"),
            mvt("Purchase Invoice", "ACC-PINV-2026-00132", "C-10'-UDF", 200, fournisseur="AQUA SERVICE", facture_fournisseur="2605588"),
            mvt("Stock Entry", "MAT-STE-2026-00228", "C-10'-PP-5m", 100, date="2026-10-29", heure="16:48", de="Hall - A&S",
                vers="Magasins - A&S", demande_par="Hedi ibidhii"),
        ]
        achat, transfert = S.grouper_par_piece(mouvements)
        self.assertEqual((achat["piece"], achat["libelle"], achat["fournisseur"], achat["facture_fournisseur"]),
                         ("ACC-PINV-2026-00132", "Facture d’achat", "AQUA SERVICE", "2605588"))
        self.assertEqual((achat["qte"], achat["nb_articles"], achat["date"]), (500.0, 2, "2026-11-23"))
        self.assertEqual([(a["item_code"], a["qte"]) for a in achat["articles"]], [("C-10'-PP-5m", 300.0), ("C-10'-UDF", 200.0)])
        self.assertEqual((transfert["piece"], transfert["de"], transfert["demande_par"], transfert["qte"]),
                         ("MAT-STE-2026-00228", "Hall - A&S", "Hedi ibidhii", 100.0))
        self.assertNotIn("_index", achat)

    def test_un_article_deux_fois_dans_la_meme_piece_est_additionne(self):
        (bl,) = S.grouper_par_piece([mvt("Delivery Note", "MAT-DN-1", "S-S25", 2), mvt("Delivery Note", "MAT-DN-1", "S-S25", 3)])
        self.assertEqual((bl["qte"], bl["nb_articles"], [(a["item_code"], a["qte"]) for a in bl["articles"]]), (5.0, 1, [("S-S25", 5.0)]))

    def test_tous_entrepots_le_meme_article_reste_separe_par_entrepot(self):
        (bl,) = S.grouper_par_piece([mvt("Delivery Note", "MAT-DN-1", "S-S25", 2, entrepot="A"), mvt("Delivery Note", "MAT-DN-1", "S-S25", 3, entrepot="B")])
        self.assertEqual((bl["nb_articles"], [(a["entrepot"], a["qte"]) for a in bl["articles"]]), (1, [("A", 2.0), ("B", 3.0)]))

    def test_origines_differentes_non_affichees(self):
        (t,) = S.grouper_par_piece([mvt("Stock Entry", "STE-1", "A", 1, de="Hall", vers="Magasin"), mvt("Stock Entry", "STE-1", "B", 1, de="Stock X", vers="Magasin")])
        self.assertEqual((t["de"], t["vers"]), (None, "Magasin"))

    def test_ordre_des_pieces_et_liste_vide(self):
        pieces = S.grouper_par_piece([mvt("Delivery Note", "B", "x", 1), mvt("Delivery Note", "A", "x", 1), mvt("Delivery Note", "B", "y", 1)])
        self.assertEqual([p["piece"] for p in pieces], ["B", "A"])
        self.assertEqual(S.grouper_par_piece([]), [])
        self.assertEqual(S.grouper_par_piece(None), [])
