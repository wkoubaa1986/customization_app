"""Tests du suivi des livraisons Aramex (livraison_aramex.py).

Convention : `unittest.TestCase` pur — seules les regles se testent ici, pas le reseau.
"""
from __future__ import annotations

import unittest

from customization_app import livraison_aramex as A


class TestReferenceAramex(unittest.TestCase):
    """Le bordereau vit dans le libelle du paiement, pas dans un champ a lui."""

    def test_le_libelle_reel_donne_le_bordereau(self):
        self.assertEqual(A.reference_aramex("Aramex N: 51330112234"), "51330112234")

    def test_le_placeholder_ne_donne_rien(self):
        """« Aramex N: 000 » = colis parti sans bordereau saisi. Le service repond 400 : autant ne
        pas l'appeler, et le signaler a l'ecran."""
        self.assertIsNone(A.reference_aramex("Aramex N: 000"))

    def test_les_libelles_vides(self):
        for vide in (None, "", "Aramex", "Aramex N:"):
            self.assertIsNone(A.reference_aramex(vide), vide)

    def test_un_numero_nu_reste_lisible(self):
        self.assertEqual(A.reference_aramex("51330112153"), "51330112153")


class TestAlerte(unittest.TestCase):
    """⚠️ TOUT CE QUI N'EST PAS LIVRE N'EST PAS UNE ALERTE. Un colis en transit se laisse attendre ;
    un client absent ou un refus ne se resoudra pas tout seul. Seul le second appelle quelqu'un."""

    def suivi(self, description, livre=False):
        return {"livre": livre, "derniere_maj": {"description": description}}

    def test_un_transit_normal_n_alerte_pas(self):
        self.assertIsNone(A.alerte(self.suivi("Statut en transit")))

    def test_une_tentative_echouee_alerte(self):
        texte = "Tentative de livraison - client non disponible - livraison reportée"
        self.assertEqual(A.alerte(self.suivi(texte)), texte)

    def test_un_refus_ou_un_retour_alerte(self):
        for texte in ("Colis refusé par le client", "Retour à l'expéditeur",
                      "Destinataire injoignable"):
            self.assertIsNotNone(A.alerte(self.suivi(texte)), texte)

    def test_un_colis_livre_n_alerte_jamais(self):
        """Meme si la derniere etape mentionne une tentative : elle est derriere lui."""
        self.assertIsNone(A.alerte(self.suivi("Tentative puis livré", livre=True)))

    def test_sans_suivi_aucune_alerte(self):
        self.assertIsNone(A.alerte(None))
        self.assertIsNone(A.alerte({}))


class TestKpis(unittest.TestCase):
    def colis(self, montant, suivi=None, alerte=None, reference="513301"):
        return {"montant": montant, "suivi": suivi, "alerte": alerte, "reference": reference}

    def test_chez_aramex_ne_compte_que_ce_qui_n_est_pas_livre(self):
        """C'est le seul chiffre qui parle d'argent : ce que le transporteur detient encore."""
        k = A.kpis([self.colis(100.0, {"livre": True}),
                    self.colis(50.0, {"livre": False}),
                    self.colis(25.0, None)])
        self.assertEqual(k["chez_aramex"], 75.0)
        self.assertEqual((k["livres"], k["en_route"]), (1, 1))

    def test_un_colis_sans_bordereau_est_compte_a_part(self):
        k = A.kpis([self.colis(30.0, None, reference=None)])
        self.assertEqual(k["sans_reference"], 1)
        self.assertEqual(k["suivi_inconnu"], 0)

    def test_un_suivi_jamais_demande_se_distingue_d_un_suivi_absent(self):
        k = A.kpis([self.colis(30.0, None)])
        self.assertEqual((k["sans_reference"], k["suivi_inconnu"]), (0, 1))

    def test_un_suivi_en_erreur_n_est_ni_livre_ni_en_route(self):
        """« Je ne sais pas » n'est pas « rien a signaler »."""
        k = A.kpis([self.colis(40.0, {"erreur": "502 — service indisponible"})])
        self.assertEqual((k["livres"], k["en_route"]), (0, 0))


if __name__ == "__main__":
    unittest.main()


class TestInformationDuClient(unittest.TestCase):
    """Un SMS, un seul, et seulement quand il apprend quelque chose au client."""

    def suivi(self, statut="Statut en transit", livre=False, franchies=3, erreur=None):
        d = {"statut": statut, "livre": livre, "etapes_franchies": franchies, "etapes_total": 7}
        if erreur:
            d["erreur"] = erreur
        return d

    def test_un_colis_en_route_avec_un_numero_declenche_le_sms(self):
        self.assertTrue(A.doit_prevenir(self.suivi(), False, "98366053"))

    def test_jamais_deux_fois_le_meme_bordereau(self):
        """Le colis peut rester quinze jours au tableau et la synchro passe chaque soir : sans
        cette regle, quinze SMS pour un seul colis."""
        self.assertFalse(A.doit_prevenir(self.suivi(), True, "98366053"))

    def test_jamais_pour_un_colis_livre(self):
        """Le client l'a en main : le prevenir serait le deranger pour rien."""
        self.assertFalse(A.doit_prevenir(self.suivi(statut="Livré", livre=True, franchies=7),
                                         False, "98366053"))

    def test_jamais_avant_la_deuxieme_etape(self):
        """« Créé » = le bordereau existe, le colis est encore chez nous. Annoncer un départ qui
        n'a pas eu lieu fait rappeler le client le lendemain."""
        self.assertFalse(A.doit_prevenir(self.suivi(statut="Créé", franchies=1), False, "98366053"))

    def test_jamais_quand_aramex_ignore_le_bordereau(self):
        """Notre probleme de saisie ne se transmet pas au client."""
        self.assertFalse(A.doit_prevenir(self.suivi(erreur="404 — aucune expedition"), False,
                                         "98366053"))

    def test_sans_numero_rien_a_envoyer(self):
        self.assertFalse(A.doit_prevenir(self.suivi(), False, ""))

    def test_le_message_porte_la_reference_le_statut_et_le_total(self):
        colis = {"customer_name": "Haifa Zaghouani", "reference": "51330108583", "montant": 106.0,
                 "suivi": self.suivi()}
        texte = A.message_sms(colis)
        for attendu in ("Haifa Zaghouani", "51330108583", "106.0", "Statut en transit"):
            self.assertIn(attendu, texte)


class TestAlerteBilingue(unittest.TestCase):
    """⚠️ ARAMEX MÉLANGE LES DEUX LANGUES. La première liste de mots-clés laissait passer les
    deux cas les plus graves, constatés en réel."""

    def test_le_statut_anglais_returned_est_une_alerte(self):
        """« Returned » + « Colis renvoyé à l'expéditeur » : ni « retour » ni « refus » n'y
        figurent, et ces colis passaient pour être en route."""
        s = {"statut": "Returned", "livre": False,
             "derniere_maj": {"description": "Colis renvoyé à l'expéditeur"}}
        self.assertIsNotNone(A.alerte(s))

    def test_un_echec_sous_un_statut_rassurant_est_une_alerte(self):
        """« échec de livraison » se lisait sous « Statut en transit » : ne regarder que le statut
        laisse passer la moitié des cas."""
        s = {"statut": "Statut en transit", "livre": False,
             "derniere_maj": {"description": "échec de livraison"}}
        self.assertIsNotNone(A.alerte(s))

    def test_les_accents_ne_font_pas_rater_un_mot(self):
        self.assertIsNotNone(A.alerte({"statut": "En cours", "livre": False,
                                       "derniere_maj": {"description": "ÉCHEC DE LIVRAISON"}}))

    def test_un_transit_sain_reste_sain(self):
        s = {"statut": "Statut en transit", "livre": False,
             "derniere_maj": {"description": "Colis arrivé au centre Aramex de destination"}}
        self.assertIsNone(A.alerte(s))

    def test_aucun_sms_automatique_sur_un_colis_en_difficulte(self):
        """Renvoi, échec, client absent : il faut appeler, pas réciter un statut."""
        s = {"statut": "Returned", "livre": False, "etapes_franchies": 4,
             "derniere_maj": {"description": "Colis renvoyé à l'expéditeur"}}
        self.assertFalse(A.doit_prevenir(s, False, "98366053"))


class TestReponseOuEchec(unittest.TestCase):
    """⚠️ CE QUI SE REESSAYE ET CE QUI NE SE REESSAYE PAS. Les appels partent desormais par quatre,
    et le service en laisse tomber un de temps a autre quand ils arrivent ensemble. Sans second
    essai, un colis en parfait etat ressortirait « suivi indisponible » et gagnerait une tentative
    au compteur — trois de ces faux echecs et la tache quotidienne abandonne un bordereau qui allait
    tres bien. Mais tout reessayer serait aussi mauvais : un 404 coute 21 secondes pour reobtenir le
    meme 404."""

    def test_une_reponse_sans_erreur_repond(self):
        self.assertTrue(A.repond_vraiment({"statut": "Livré", "livre": True}))

    def test_un_404_est_une_reponse_pas_un_echec(self):
        """« Aucune expedition Aramex pour cette reference » est un constat definitif : le
        bordereau est faux ou l'expedition n'a jamais ete creee."""
        self.assertTrue(A.repond_vraiment(
            {"erreur": "404 — Aucune expedition Aramex pour la reference 51330108583."}))

    def test_un_500_est_un_echec_a_reprendre(self):
        """Observe en parallele : revient en 30 millisecondes, corps vide. Le service se marche
        dessus, il n'a rien appris sur le colis."""
        self.assertFalse(A.repond_vraiment({"erreur": "500 — Internal Server Error"}))

    def test_une_panne_de_transport_est_un_echec_a_reprendre(self):
        self.assertFalse(A.repond_vraiment({"erreur": "HTTPConnectionPool : timed out"}))


class TestPiecesDuColis(unittest.TestCase):
    """Le paiement ne pointe qu'une piece — une commande dans 30 cas sur 35, une facture dans 5.
    Le BON DE LIVRAISON n'est jamais pointe, et c'est pourtant lui qui dit ce qui est REELLEMENT
    parti chez le client : la seule piece a confronter au suivi du transporteur. Les 35 colis en ont
    un."""

    FICHES = {
        ("Sales Order", "SAL-ORD-2026-02584"): {
            "doctype": "Sales Order", "name": "SAL-ORD-2026-02584", "statut": "Completed",
            "date": "2026-07-27", "montant": 687.885, "docstatus": 1},
        ("Sales Invoice", "ACC-SINV-2026-01016"): {
            "doctype": "Sales Invoice", "name": "ACC-SINV-2026-01016", "statut": "Partly Paid",
            "date": "2026-07-27", "montant": 693.885, "docstatus": 1},
        ("Delivery Note", "MAT-DN-2026-02952"): {
            "doctype": "Delivery Note", "name": "MAT-DN-2026-02952", "statut": "Completed",
            "date": "2026-07-28", "montant": 693.885, "docstatus": 1},
    }
    RESEAU = {("Sales Invoice", "ACC-SINV-2026-01016"): set(FICHES)}

    def _pieces(self, fiches=None):
        return A._pieces_du_colis(self.RESEAU, fiches if fiches is not None else self.FICHES,
                                  "Sales Invoice", "ACC-SINV-2026-01016")

    def test_les_trois_pieces_sortent_dans_l_ordre_du_cycle_de_vente(self):
        """Commande, facture, bon de livraison : l'ordre du cycle, pas celui de l'alphabet ni du
        hasard d'un ensemble Python."""
        self.assertEqual([p["doctype"] for p in self._pieces()],
                         ["Sales Order", "Sales Invoice", "Delivery Note"])

    def test_la_piece_du_paiement_est_marquee(self):
        """C'est par elle que le colis est entre dans ce tableau : l'ecran doit pouvoir le dire."""
        marquees = [p["name"] for p in self._pieces() if p["principale"]]
        self.assertEqual(marquees, ["ACC-SINV-2026-01016"])

    def test_une_piece_interdite_de_lecture_disparait_sans_faire_de_trou(self):
        """⚠️ PAS DE PORTE FERMEE A L'ECRAN. Un charge de vente peut ne pas avoir les bons de
        livraison : mieux vaut ne rien proposer que proposer un lien qui repondra « acces refuse »."""
        sans_bl = {k: v for k, v in self.FICHES.items() if k[0] != "Delivery Note"}
        self.assertEqual([p["doctype"] for p in self._pieces(sans_bl)],
                         ["Sales Order", "Sales Invoice"])

    def test_un_colis_sans_piece_rend_une_liste_vide(self):
        self.assertEqual(A._pieces_du_colis({}, {}, "Sales Order", "SAL-ORD-0000"), [])


class TestGabaritSansApostropheDroite(unittest.TestCase):
    """⚠️ LE PIEGE QUI A DEJA TUE CETTE PAGE DEUX FOIS, ET QUI NE PREVIENT PAS.

    Frappe expedie le gabarit d'une Page Desk au navigateur dans une chaine JS delimitee par des
    guillemets SIMPLES (`frappe.templates["..."] = '...'`, cf. `frappe.build.html_to_js_template`),
    et son echappement vaut `content.replace("'", "'")` : un no-op. Une seule apostrophe droite,
    fut-elle enfouie dans un commentaire CSS, ferme la chaine et emporte TOUT le script de la page —
    le gabarit et le JS qui le suit. La page se charge vide, sans erreur parlante, et rien dans le
    fichier fautif ne ressemble a un bug.

    Les commentaires CSS ne protegent de rien : seuls les commentaires HTML sont retires avant
    l'envoi. Ce test lit le gabarit sur le disque, la ou le probleme se cree.
    """

    def test_le_gabarit_ne_contient_aucune_apostrophe_droite(self):
        import os

        chemin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(A.__file__))),
                              "customization_app", "customize_erpnext", "page", "livraison_aramex",
                              "livraison_aramex.html")
        with open(chemin, encoding="utf-8") as f:
            contenu = f.read()
        fautives = [(n, l.strip()) for n, l in enumerate(contenu.splitlines(), 1) if "'" in l]
        self.assertEqual(fautives, [],
                         "apostrophe droite dans le gabarit — ecrire l'apostrophe typographique")
