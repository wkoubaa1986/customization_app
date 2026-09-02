"""Tests de l'écran « Ma journée ».

Convention : `unittest.TestCase` pur. On teste ce qui se teste sans base — la
lecture des photos, la liste des résultats d'appel, et surtout LE PÉRIMÈTRE DE
SUPERVISION, qui décide qui voit la journée de qui.
"""
from __future__ import annotations

import unittest

import frappe

from customization_app import planning_employe as P


class _Doc:
    """Le minimum que `_photos_de` interroge : deux champs texte."""

    def __init__(self, avant="", apres=""):
        self._v = {"liste_photos_avant": avant, "liste_photos_apres": apres}

    def get(self, champ):
        return self._v.get(champ)


class TestPhotosDeLaTache(unittest.TestCase):
    """Les photos servent à vérifier le bordereau Aramex : les rater, c'est
    refuser un numéro parfaitement valide."""

    def test_le_format_pose_par_l_application(self):
        d = _Doc(avant='📁 "/private/files/a.png"\n📁 "/private/files/b.png"')
        self.assertEqual(P._photos_de(d),
                         ["/private/files/a.png", "/private/files/b.png"])

    def test_les_deux_champs_sont_lus(self):
        d = _Doc(avant='📁 "/private/files/a.png"', apres='📁 "/private/files/b.png"')
        self.assertEqual(len(P._photos_de(d)), 2)

    def test_une_url_nue_privee_est_reconnue(self):
        """Repli : une URL sans guillemets, et PRIVÉE — les photos de clôture
        le sont toutes. Le repli d'origine ne voyait que `/files/`."""
        self.assertEqual(P._photos_de(_Doc(avant="/private/files/c.jpg")),
                         ["/private/files/c.jpg"])

    def test_un_champ_vide_ne_leve_pas(self):
        self.assertEqual(P._photos_de(_Doc()), [])

    def test_une_ligne_parasite_est_ignoree(self):
        d = _Doc(avant='note du technicien\n📁 "/private/files/a.png"')
        self.assertEqual(P._photos_de(d), ["/private/files/a.png"])


class TestSupervision(unittest.TestCase):
    """QUI voit la journée d'un autre — le point le plus sensible de l'écran."""

    def test_seul_system_manager_supervise(self):
        """⚠️ MESURÉ SUR LA BASE RÉELLE (02/09/2026) : « Sales Manager »,
        « Maintenance Manager » et « Gestionaire Activité » sont portés par les
        TECHNICIENS eux-mêmes. Les inclure ouvrirait la journée de chacun à
        tout le monde — l'inverse de ce qui est demandé."""
        self.assertEqual(set(P.ROLES_SUPERVISION), {"System Manager"})

    def test_les_roles_de_terrain_ne_supervisent_pas(self):
        for role in ("Sales Manager", "Maintenance Manager", "Gestionaire Activité",
                     "Sales User", "Employee", "Stock User"):
            self.assertNotIn(role, P.ROLES_SUPERVISION, role)


class TestResultatsAppel(unittest.TestCase):
    """L'écran demande le résultat APRÈS l'appel : « on a composé le numéro »
    ne dit pas si le client a décroché, et c'est cela qui décide de la suite."""

    def test_les_trois_suites_possibles(self):
        self.assertEqual(list(P.RESULTATS_APPEL),
                         ["Répondu", "Sans réponse", "À rappeler"])

    def test_le_prefixe_permet_de_retrouver_les_appels(self):
        """La trace vit dans un commentaire : sans préfixe stable, l'historique
        des appels devient irretrouvable."""
        self.assertTrue(P.PREFIXE_APPEL.strip())
        self.assertIn("Appel", P.PREFIXE_APPEL)


class TestReglement(unittest.TestCase):
    """« Payé » ne veut pas dire « encaissé ».

    Une commande peut porter un paiement de mode « Dette non payée » : la pièce
    existe, l'argent non. La carte annonçait « soldée » en vert sur 651 DT que
    personne n'avait touchés (02/09/2026).
    """

    def _infos(self, paiements, total=651.0):
        return {"total": total, "total_paye": sum(p["montant"] for p in paiements),
                "paiements": paiements}

    def _p(self, montant, mode, compte):
        return {"montant": montant, "mode": mode, "compte": compte,
                "date": "2026-08-29", "paiement": "ACC-PAY-1"}

    def test_un_vrai_encaissement_ne_leve_aucune_alerte(self):
        r = P._reglement(self._infos([self._p(651.0, "Espèces", "Espèces - A&S")]))
        self.assertEqual((r["reste"], r["dette"], r["dette_aramex"]), (0.0, 0.0, 0.0))

    def test_une_dette_non_payee_est_signalee(self):
        """C'est le cas de la capture : soldée en vert, argent jamais touché."""
        r = P._reglement(self._infos([self._p(651.0, P.MODE_DETTE, "Dettes - A&S")]))
        self.assertEqual(r["reste"], 0.0)
        self.assertEqual(r["dette"], 651.0)

    def test_la_dette_aramex_n_est_pas_une_alerte_SI_le_colis_est_parti(self):
        """Exception voulue : le transporteur encaisse à la remise, il n'y a
        rien à réclamer sur place — encore faut-il qu'il ait le colis."""
        r = P._reglement(self._infos(
            [self._p(406.0, P.MODE_DETTE, "Livraison Aramex - A&S")], total=406.0),
            bordereau="51330112912")
        self.assertEqual(r["dette"], 0.0)
        self.assertEqual(r["dette_aramex"], 406.0)

    def test_une_dette_aramex_SANS_bordereau_reste_a_encaisser(self):
        """Le colis n'a pas voyagé : personne n'a rien pris, et c'est souvent
        NOTRE technicien qui se déplace (Tache-08235, Installation sur
        WEB1-008367, 406 DT). L'écran annonçait « encaissé par Aramex »."""
        r = P._reglement(self._infos(
            [self._p(406.0, P.MODE_DETTE, "Livraison Aramex - A&S")], total=406.0),
            bordereau="")
        self.assertEqual(r["dette"], 406.0)
        self.assertEqual(r["dette_aramex"], 0.0)

    def test_le_compte_ET_le_bordereau_tranchent(self):
        """Les deux dettes portent le MÊME mode : le compte les sépare, et le
        bordereau décide si celle d'Aramex est vraiment partie."""
        r = P._reglement(self._infos([
            self._p(100.0, P.MODE_DETTE, "Dettes - A&S"),
            self._p(306.0, P.MODE_DETTE, "Livraison Aramex - A&S")], total=406.0),
            bordereau="51330112912")
        self.assertEqual((r["dette"], r["dette_aramex"]), (100.0, 306.0))

    def test_un_reste_impaye_prime_sur_tout(self):
        r = P._reglement(self._infos([self._p(200.0, "Espèces", "Espèces - A&S")]))
        self.assertEqual(r["reste"], 451.0)

    def test_sans_commande_il_n_y_a_rien_a_dire(self):
        self.assertIsNone(P._reglement(None))


class TestPerimetreAramex(unittest.TestCase):
    """La règle Aramex ne vaut que pour une LIVRAISON.

    L'échéancier « Livraison Aramex » est posé AUTOMATIQUEMENT à la création
    d'une commande web. Quand celle-ci devient finalement une Installation, il
    ne décrit plus rien : notre technicien se déplace, aucun colis ne part, et
    l'argent est à encaisser sur place (décision utilisateur 02/09/2026, cas
    Tache-08235 / WEB1-008367, 406 DT).
    """

    def test_le_type_livraison_est_celui_du_moteur(self):
        """Le libellé doit rester celui des tâches, sinon la règle ne
        s'appliquerait jamais — ou s'appliquerait partout."""
        self.assertEqual(P.TYPE_LIVRAISON, "Livraison")

    def test_une_dette_aramex_sans_bordereau_est_a_encaisser(self):
        """C'est ce que voit le technicien d'une Installation : le serveur a
        neutralisé le bordereau, la dette redevient ordinaire."""
        infos = {"total": 406.0, "total_paye": 406.0,
                 "paiements": [{"montant": 406.0, "mode": P.MODE_DETTE,
                                "compte": "Livraison Aramex - A&S",
                                "date": "2026-08-29"}]}
        r = P._reglement(infos, bordereau="")
        self.assertEqual(r["dette"], 406.0)
        self.assertEqual(r["dette_aramex"], 0.0)


class TestBordereauSurEcheancier(unittest.TestCase):
    """Le bordereau doit atterrir DANS L'ÉCHÉANCIER, pas seulement sur la
    commande.

    Constaté le 02/09/2026 : après « Enregistrer » dans Ma journée, la colonne
    « N: Chèque » de la commande affichait toujours « xxxxx ». C'est ce champ-là
    (`Payment Schedule.custom__n_chèque__transaction`) que l'utilisateur
    regarde, et que l'ancien rappel du soir lisait pour annoncer le numéro de
    suivi au client.
    """

    def test_les_bouchons_de_la_commande_web_sont_remplacables(self):
        """Ce que la création d'une commande web laisse dans la case."""
        for bouchon in ("xxxxx", "XXXXX", "0000", "000", "", "  ", None):
            self.assertTrue(P._bouchon(bouchon), repr(bouchon))

    def test_un_vrai_numero_n_est_jamais_un_bouchon(self):
        """⚠️ Un numéro déjà saisi — par le magasin ou par un encaissement
        Aramex — est une donnée. L'écraser en silence ferait perdre le suivi
        d'un colis déjà parti."""
        for vrai in ("51330112891", "48812239851",
                     "Aramex N: 47070786151 Virement recu N: FT24096QB9SS"):
            self.assertFalse(P._bouchon(vrai), vrai)

    def test_un_zero_isole_reste_un_bouchon(self):
        """« 0 » n'est pas un numéro de bordereau : c'est une case pas remplie."""
        self.assertTrue(P._bouchon("0"))
