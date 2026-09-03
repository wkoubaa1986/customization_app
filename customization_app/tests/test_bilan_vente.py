"""Tests des deux options de comptage du Bilan Vente.

Convention : `unittest.TestCase` pur, aucune base — on éprouve ici les RÈGLES qui
décident quelles commandes entrent dans le mois, pas les chiffres eux-mêmes.

Contexte (03/09/2026) : le bilan comptait chaque commande sur sa DATE DE LIVRAISON.
Sur août 2026, quatre commandes livrées fin août portaient une tâche datée de
septembre et toujours ouverte — 3 001 DT de vente annoncés sur du travail qui
n'avait pas eu lieu, et un solde Economiq qui changeait de camp selon la règle
retenue. D'où deux réglages, offerts à l'écran.
"""
from __future__ import annotations

import unittest

from customization_app import bilan_vente as BV


class TestBaseDeComptage(unittest.TestCase):
    """Sur quelle date une commande est rattachée à un mois."""

    def test_la_livraison_reste_le_defaut(self):
        """⚠️ Le bilan d'août 2026 a déjà été lu et discuté avec cette base. Basculer
        le défaut changerait sous les pieds de l'utilisateur des chiffres qu'il a
        commentés — le nouveau comportement doit être un CHOIX, jamais une surprise."""
        self.assertEqual(BV._base_valide(None), BV.BASE_LIVRAISON)
        self.assertEqual(BV._base_valide(""), BV.BASE_LIVRAISON)

    def test_les_deux_bases_sont_acceptees(self):
        self.assertEqual(BV._base_valide("tache"), BV.BASE_TACHE)
        self.assertEqual(BV._base_valide("livraison"), BV.BASE_LIVRAISON)

    def test_la_casse_et_les_espaces_ne_genent_pas(self):
        self.assertEqual(BV._base_valide("  TACHE "), BV.BASE_TACHE)

    def test_une_base_inconnue_retombe_sur_la_livraison(self):
        """Un paramètre venu de l'URL ou d'un export ne doit jamais produire un
        périmètre indéterminé — ni une erreur SQL par interpolation."""
        for bidon in ("autre", "delivery_date", "1; DROP TABLE", "tâche"):
            self.assertEqual(BV._base_valide(bidon), BV.BASE_LIVRAISON, bidon)


class TestFragmentSql(unittest.TestCase):
    """Le fragment de date est INTERPOLÉ dans la requête : il ne peut venir que
    d'ici, jamais de l'appelant."""

    def test_la_base_livraison_ne_joint_aucune_tache(self):
        date, jointure = BV._repere(BV.BASE_LIVRAISON)
        self.assertEqual(date, "so.delivery_date")
        self.assertEqual(jointure, "")

    def test_la_base_tache_joint_et_retombe_sur_la_livraison(self):
        """La commande SANS tâche garde sa date de livraison : c'est le seul
        événement daté qu'elle porte. Une jointure stricte l'aurait fait
        disparaître du bilan (SAL-ORD-2026-02930, 60 DT en août 2026)."""
        date, jointure = BV._repere(BV.BASE_TACHE)
        self.assertIn("COALESCE", date)
        self.assertIn("so.delivery_date", date)
        self.assertIn("Tache de travail", jointure)

    def test_seules_les_taches_vivantes_datent_la_commande(self):
        """Une tâche annulée ne dit rien de la date du travail."""
        self.assertIn("docstatus < 2", BV._repere(BV.BASE_TACHE)[1])

    def test_la_premiere_tache_fait_foi(self):
        """MIN : une commande qui traîne sur deux mois appartient au mois où le
        travail a COMMENCÉ, pas au dernier passage."""
        self.assertIn("MIN(starts_on)", BV._repere(BV.BASE_TACHE)[1])

    def test_un_fragment_inconnu_ne_peut_pas_etre_injecte(self):
        date, jointure = BV._repere("'; DROP TABLE `tabSales Order`; --")
        self.assertEqual(date, "so.delivery_date")
        self.assertEqual(jointure, "")


class TestTacheOuverte(unittest.TestCase):
    """Ce qui fait dire qu'une commande n'est pas finie."""

    def test_une_tache_ouverte_suffit(self):
        self.assertTrue(BV._a_une_tache_ouverte([{"status": "Open"}]))

    def test_une_seule_ouverte_parmi_plusieurs_suffit(self):
        """Une installation faite mais un entretien encore à venir : la commande
        n'est pas soldée."""
        self.assertTrue(BV._a_une_tache_ouverte(
            [{"status": "Completed"}, {"status": "Open"}]))

    def test_tout_termine_ne_declenche_rien(self):
        self.assertFalse(BV._a_une_tache_ouverte(
            [{"status": "Completed"}, {"status": "Completed"}]))

    def test_une_tache_annulee_n_est_pas_ouverte(self):
        """Annulée = le travail n'aura pas lieu, pas « en attente ». L'exclure
        retirerait du bilan une vente pourtant acquise."""
        self.assertFalse(BV._a_une_tache_ouverte([{"status": "Cancelled"}]))

    def test_une_commande_sans_tache_n_est_jamais_ecartee(self):
        """Rien à attendre : une vente au comptoir n'a pas de tâche, et elle est
        bien du chiffre d'affaires du mois."""
        self.assertFalse(BV._a_une_tache_ouverte([]))
        self.assertFalse(BV._a_une_tache_ouverte(None))

    def test_une_ligne_vide_ne_leve_pas(self):
        self.assertFalse(BV._a_une_tache_ouverte([None, {}]))


class TestRegleEnregistree(unittest.TestCase):
    """La règle est PARTAGÉE avec l'onglet « Partenaire Economiq ».

    Cet onglet appelle `get_data(month=...)` sans paramètre et bâtit sur le résultat l'écriture
    qui règle les comptes avec le partenaire. Un réglage seulement visuel aurait fait afficher
    654,86 de bénéfice d'un côté et 1 321,01 de l'autre sur le même mois d'août 2026 — et
    l'ajustement se serait calculé sur le mauvais chiffre.
    """

    def test_les_libelles_font_l_aller_retour(self):
        """Le DocType stocke un libellé lisible ; le moteur travaille sur un jeton. Si la
        correspondance se casse, la règle retombe silencieusement sur la livraison."""
        for jeton, libelle in BV.LIBELLE_DE_BASE.items():
            self.assertEqual(BV._LIBELLES_BASE[libelle], jeton)

    def test_les_deux_bases_ont_un_libelle(self):
        self.assertEqual(set(BV.LIBELLE_DE_BASE), set(BV.BASES))

    def test_un_libelle_inconnu_en_base_retombe_sur_la_livraison(self):
        """Quelqu'un renomme l'option dans le DocType : on veut le comportement
        historique, pas une erreur ni un périmètre au hasard."""
        self.assertEqual(BV._LIBELLES_BASE.get("Date du paiement", BV.BASE_LIVRAISON),
                         BV.BASE_LIVRAISON)

    def test_non_renseigne_n_est_pas_zero(self):
        """⚠️ Le cœur du mécanisme : `exclure_ouvertes=None` veut dire « applique la règle »,
        `0` veut dire « n'exclus rien ». Un défaut à 0 dans la signature aurait fait diverger
        les deux écrans en silence, puisque l'onglet Economiq ne passe rien."""
        import inspect

        params = inspect.signature(BV.get_data).parameters
        self.assertIsNone(params["base"].default)
        self.assertIsNone(params["exclure_ouvertes"].default)
        self.assertIsNone(params["month"].default)

    def test_l_export_excel_suit_la_meme_convention(self):
        """Sinon un export lancé par un script sortirait sur un autre périmètre que l'écran."""
        import inspect

        params = inspect.signature(BV.download_excel).parameters
        self.assertIsNone(params["base"].default)
        self.assertIsNone(params["exclure_ouvertes"].default)


class TestEtatDesBonsDeLivraison(unittest.TestCase):
    """L'état de chaque bon de livraison, affiché au dépliage de la commande.

    L'éligibilité au bilan repose sur la livraison — « Fully Delivered », ou un bon validé avec
    réconciliation de stock — sans jamais montrer QUELLE pièce la porte. On lisait donc une
    commande au bilan sans pouvoir vérifier ce qui l'y avait fait entrer (demande utilisateur
    03/09/2026).
    """

    def test_un_bon_valide_garde_son_statut(self):
        self.assertEqual(BV.etat_bon("To Bill", 1), "To Bill")
        self.assertEqual(BV.etat_bon("Completed", 1), "Completed")

    def test_un_bon_annule_le_dit(self):
        """⚠️ Une pièce annulée CONSERVE le statut qu'elle avait avant. Lire `status` seul
        affichait « Completed » sur un bon annulé — or c'est précisément le cas qui explique
        qu'une commande paraisse livrée sans l'être."""
        self.assertEqual(BV.etat_bon("Completed", 2), "Cancelled")
        self.assertEqual(BV.etat_bon("To Bill", 2), "Cancelled")

    def test_un_brouillon_le_dit_aussi(self):
        self.assertEqual(BV.etat_bon("", 0), "Draft")
        self.assertEqual(BV.etat_bon("To Bill", 0), "Draft")

    def test_un_statut_absent_ne_leve_pas(self):
        self.assertEqual(BV.etat_bon(None, 1), "")

    def test_le_docstatus_en_chaine_est_accepte(self):
        """Il arrive du SQL, et une comparaison stricte à 2 raterait « 2 »."""
        self.assertEqual(BV.etat_bon("Completed", "2"), "Cancelled")

    def test_la_commande_porte_toujours_la_cle_bons(self):
        """Le rendu itère dessus : une clé absente casserait le dépliage."""
        import inspect

        source = inspect.getsource(BV._build_order)
        self.assertIn('"bons"', source)
