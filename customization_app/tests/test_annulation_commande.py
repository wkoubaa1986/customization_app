"""Annulation d'une commande (26/11/2026) — règles PURES : textes des commentaires, et la décision du patch qui éteint le
Server Script « cancel sales order payment » (il n'est éteint que si son code est toujours celui qui a été repris)."""
import datetime
import unittest
from unittest import mock

from customization_app import annulation_commande as A
from customization_app.patches import script_annulation_commande_dans_l_app as P

QUAND = datetime.datetime(2026, 11, 26, 9, 35)


class TestTextes(unittest.TestCase):
    def test_echeancier_annule_dit_commande_date_et_qui(self):
        t = A.texte_echeancier_annule("SAL-ORD-2026-04284", QUAND, "Salma Ben Ali (salma@x.tn)")
        self.assertIn("SAL-ORD-2026-04284", t)
        self.assertIn("26/11/2026 à 09:35", t)
        self.assertIn("Salma Ben Ali (salma@x.tn)", t)
        self.assertIn("annulé", t)
        self.assertNotIn("supprim", t)

    def test_brouillon_laisse(self):
        t = A.texte_brouillon_laisse("SAL-ORD-1", QUAND, "Administrator")
        self.assertIn("brouillon", t)
        self.assertIn("laissé tel quel", t)

    def test_reference_retiree(self):
        self.assertIn("1 ligne(s) machine, 2 visite(s)", A.texte_reference_retiree("SAL-ORD-1", 1, 2))
        self.assertIn("(1 visite(s))", A.texte_reference_retiree("SAL-ORD-1", 0, 1))
        self.assertIn("aucune ligne", A.texte_reference_retiree("SAL-ORD-1", 0, 0))

    def test_horodatage_tolere_une_chaine(self):
        self.assertEqual(A._horodatage("2026-11-26"), "2026-11-26")
        self.assertEqual(A._horodatage(None), "")

    def test_qui_agit(self):
        with mock.patch.object(A, "get_fullname", lambda u: "Nejib Koubaa"):
            self.assertEqual(A.qui_agit("nejib@aquaworld.tn"), "Nejib Koubaa (nejib@aquaworld.tn)")
        with mock.patch.object(A, "get_fullname", lambda u: u):
            self.assertEqual(A.qui_agit("Administrator"), "Administrator")
        with mock.patch.object(A, "get_fullname", lambda u: ""):
            self.assertEqual(A.qui_agit("x@y"), "x@y")


class TestFicheClient(unittest.TestCase):
    def test_commentaire_client_nomme_les_echeanciers(self):
        # Relecture du 26/11/2026 : l'historique gardé sur l'échéancier annulé n'apparaissait nulle part pour l'équipe.
        t = A.texte_client_commande_annulee("SAL-ORD-2026-04284", ["MAT-MSH-2026-01332", "MAT-MSH-X"], QUAND,
                                            "Salma (s@x)")
        self.assertTrue(t.startswith("❌"))
        self.assertIn("SAL-ORD-2026-04284", t)
        self.assertIn("MAT-MSH-2026-01332, MAT-MSH-X annulé(s)", t)
        self.assertIn("26/11/2026 à 09:35 par Salma (s@x)", t)


class TestListesBrouillon(unittest.TestCase):
    """Relecture du 26/11/2026 : une liste d'appels brouillon qui cite un échéancier annulé ne s'enregistre plus."""

    def test_articles_de_la_ligne(self):
        self.assertEqual(A.articles_de_la_ligne('{"F-UV-6w": "2026-11-01", "AP-M-AJ-5-SM": "2026-05-01", "_x": 1}'),
                         ["AP-M-AJ-5-SM", "F-UV-6w"])
        self.assertEqual(A.articles_de_la_ligne("pas du json"), [])
        self.assertEqual(A.articles_de_la_ligne(None), [])
        self.assertEqual(A.articles_de_la_ligne("[1, 2]"), [])

    def test_autre_echeancier_du_client_repointe(self):
        self.assertEqual(A.sort_ligne_liste({"appele": 1, "resume_appel": "Autres reponses"}, "MS-2"),
                         ("repointer", "MS-2"))
        self.assertEqual(A.sort_ligne_liste({}, "MS-2"), ("repointer", "MS-2"))

    def test_ligne_pas_encore_appelee_retiree(self):
        self.assertEqual(A.sort_ligne_liste({"appele": 0, "resume_appel": "", "reponse_client": None}), ("retirer", None))
        self.assertEqual(A.sort_ligne_liste({"appele": 0, "resume_appel": "  "}), ("retirer", None))

    def test_appel_deja_saisi_retire_et_recopie(self):
        # Le champ échéancier de la ligne est OBLIGATOIRE : le vider bloquait la liste autant (MandatoryError, mesurée
        # le 26/11/2026). La ligne part, son appel est recopié sur la fiche client.
        for ligne in ({"appele": 1}, {"resume_appel": "Ne répond pas 1er appel"}, {"reponse_client": "rappeler"},
                      {"tache_de_travail": "TACHE-1"}):
            self.assertEqual(A.sort_ligne_liste(ligne), ("retirer_appel", None), ligne)

    def test_texte_liste(self):
        t = A.texte_liste("MS-1", "↩️ Machine rendue (BL de retour B1, X)",
                          [("retirer", "Karray", None), ("repointer", "Adnane", "MS-2"), ("retirer_appel", "Zey", None)])
        self.assertTrue(t.startswith("↩️ Machine rendue (BL de retour B1, X) : l'échéancier MS-1 est annulé"))
        self.assertIn("ligne de Karray retirée (pas encore appelée)", t)
        self.assertIn("ligne de Adnane rattachée à MS-2", t)
        self.assertIn("ligne de Zey retirée, son appel est recopié sur la fiche client", t)

    def test_appel_recopie_sur_la_fiche_client(self):
        t = A.texte_appel_recopie("LIST-1", {"appele": 1, "resume_appel": "Rendez-vous pris",
                                             "reponse_client": "<b>mardi</b>", "tache_de_travail": "TACHE-9"},
                                  "MS-1", "↩️ Machine rendue (BL de retour B1, X)")
        self.assertTrue(t.startswith("📞 Appel noté dans la liste d'appels LIST-1 : « Rendez-vous pris »"))
        self.assertIn("réponse « &lt;b&gt;mardi&lt;/b&gt; »", t)
        self.assertIn("tâche TACHE-9 à revoir", t)
        self.assertIn("(a été appelé)", t)
        self.assertIn("l'échéancier MS-1 est annulé — ↩️ Machine rendue", t)
        self.assertIn("pas encore coché", A.texte_appel_recopie("LIST-1", {"resume_appel": "x"}, "MS-1", "p"))


class TestDroitsEtCascade(unittest.TestCase):
    def test_droits_de_l_utilisateur_pour_paiements_et_ecritures(self):
        # Relecture du 26/11/2026 : le « frappe.flags.ignore_permissions = True » du script visait le bac à sable et ne
        # levait rien. La reprise ne doit lever aucun droit : ni le drapeau global, ni celui des pièces annulées.
        vus = []

        class Piece:
            def __init__(self, nom, docstatus=1):
                self.name, self.docstatus, self.flags = nom, docstatus, {}

            def cancel(self):
                vus.append((self.name, A.frappe.flags.get("ignore_permissions"), self.flags.get("ignore_permissions")))

        supprimes = []
        with mock.patch.object(A, "_paiements_de", lambda c: ["PE-1"]), \
                mock.patch.object(A, "_ecritures_de", lambda c: ["JE-1", "JE-2"]), \
                mock.patch.object(A.frappe, "get_doc", lambda dt, n: Piece(n, 2 if n == "JE-2" else 1)), \
                mock.patch.object(A.frappe, "delete_doc", lambda dt, n, **k: supprimes.append((dt, n, k))), \
                mock.patch.object(A.frappe, "flags", A.frappe._dict()):
            A._supprimer_paiements_et_ecritures(A.frappe._dict(name="SAL-ORD-1"))
            self.assertNotIn("ignore_permissions", A.frappe.flags)
        self.assertEqual(vus, [("PE-1", None, None), ("JE-1", None, None)])     # JE-2 déjà annulée : supprimée seulement
        self.assertEqual([(dt, n) for dt, n, _k in supprimes],
                         [("Payment Entry", "PE-1"), ("Journal Entry", "JE-1"), ("Journal Entry", "JE-2")])
        self.assertTrue(all(k == {"ignore_permissions": True} for _dt, _n, k in supprimes))

    def test_drapeau_de_cascade_pose_puis_rendu(self):
        vu = []
        with mock.patch.object(A.frappe, "flags", A.frappe._dict()), \
                mock.patch.object(A, "_annuler_et_supprimer_bls", lambda d: vu.append(A.annulation_commande_en_cours())), \
                mock.patch.object(A, "_nettoyer_echeanciers", lambda d: {}), \
                mock.patch.object(A, "_supprimer_paiements_et_ecritures", lambda d: None), \
                mock.patch.object(A, "_noter", lambda b: None):
            A.before_cancel_sales_order(A.frappe._dict(name="SAL-ORD-9"))
            self.assertEqual(vu, ["SAL-ORD-9"])
            self.assertIsNone(A.annulation_commande_en_cours())
            # … rendu aussi quand la cascade échoue (l'annulation est refusée, la requête continue)
            with mock.patch.object(A, "_nettoyer_echeanciers", side_effect=RuntimeError("refus")):
                with self.assertRaises(RuntimeError):
                    A.before_cancel_sales_order(A.frappe._dict(name="SAL-ORD-9"))
            self.assertIsNone(A.annulation_commande_en_cours())


class TestPatchServerScript(unittest.TestCase):
    def ss(self, **k):
        d = {"disabled": 0, "script": P.REPRIS, "reference_doctype": "Sales Order", "doctype_event": "Before Cancel"}
        d.update(k)
        return d

    def test_code_identique_eteint(self):
        self.assertEqual(P.decision(self.ss()), "eteindre")

    def test_espaces_et_commentaires_ne_comptent_pas(self):
        code = "# une note ajoutée\n" + P.REPRIS.replace("    ", "\t").replace("\n\n", "\n") + "\n\n   \n"
        self.assertEqual(P.decision(self.ss(script=code)), "eteindre")
        self.assertEqual(P.decision(self.ss(script=P.REPRIS.replace("\n", "\r\n"))), "eteindre")

    def test_une_instruction_changee_laisse_actif(self):
        code = P.REPRIS.replace('frappe.delete_doc("Payment Entry"', 'frappe.msgprint("Payment Entry"')
        self.assertEqual(P.decision(self.ss(script=code)), "modifie")
        self.assertEqual(P.decision(self.ss(script=P.REPRIS + "\nfrappe.throw('non')")), "modifie")

    def test_autre_evenement_laisse_actif(self):
        self.assertEqual(P.decision(self.ss(doctype_event="After Cancel")), "modifie")
        self.assertEqual(P.decision(self.ss(reference_doctype="Sales Invoice")), "modifie")

    def test_absent_ou_deja_eteint(self):
        self.assertEqual(P.decision(None), "absent")
        self.assertEqual(P.decision(self.ss(disabled=1)), "deja_eteint")
        self.assertEqual(P.decision(self.ss(disabled=1, script="autre chose")), "deja_eteint")

    def test_la_copie_reprise_est_bien_celle_du_script(self):
        # Ses quatre boucles, dans l'ordre : paiements, écritures, BL, échéanciers.
        positions = [P.REPRIS.index(x) for x in ('"Payment Entry"', '"Journal Entry Account"', "'Delivery Note Item'",
                                                  "'Maintenance Schedule Item'")]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(P.REPRIS), 2147)


if __name__ == "__main__":
    unittest.main()
