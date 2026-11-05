"""Verrou par code du plugin dépenses : règles pures et dialogue MCP (jeton exigé pour les écritures seulement)."""
import unittest

from customization_app import verrou_chatgpt as V
from customization_app.assistant_chatgpt import OUTILS, outils_mcp
from customization_app.mcp_chatgpt import traiter

EMPREINTE = V.hacher("4821")


class TestRegles(unittest.TestCase):
    def test_code(self):
        self.assertTrue(V.code_valide("4821", EMPREINTE))
        self.assertTrue(V.code_valide(" 4821 ", EMPREINTE))
        for c, e in (("0000", EMPREINTE), ("", EMPREINTE), (None, EMPREINTE), ("4821", None), ("4821", "pas-une-empreinte")):
            self.assertFalse(V.code_valide(c, e), (c, e))

    def test_outils_verrouilles(self):
        self.assertTrue(V.exige_jeton("saisir_depense") and V.exige_jeton("creer_rendez_vous"))
        self.assertFalse(V.exige_jeton("stock_article") or V.exige_jeton("mes_saisies") or V.exige_jeton("deverrouiller"))
        self.assertTrue(V.ECRITURES <= set(OUTILS))
        self.assertEqual({n for n, (_r, _p, post, _q) in OUTILS.items() if post} - V.ECRITURES, {"deverrouiller"})   # toute écriture est verrouillée
        liste = V.avec_jeton(outils_mcp())
        self.assertEqual({o["name"] for o in liste if "jeton" in o["inputSchema"]["properties"]}, set(V.ECRITURES))
        self.assertNotIn("jeton", next(o for o in liste if o["name"] == "saisir_depense")["inputSchema"]["required"])
        self.assertNotIn("jeton", next(o for o in outils_mcp() if o["name"] == "saisir_depense")["inputSchema"]["properties"])  # l'original intact

    def test_decision(self):
        vivant = lambda j: j == "JETON-OK"
        # sans code posé : tout passe, le jeton est quand même retiré des arguments
        self.assertEqual(V.decision("saisir_depense", {"date": "d", "jeton": "x"}, None, vivant), (True, {"date": "d"}))
        # lecture : jamais de jeton
        self.assertEqual(V.decision("stock_article", {"texte": "frp"}, EMPREINTE, vivant), (True, {"texte": "frp"}))
        # écriture : jeton vivant, ou le code lui-même, sinon refus
        self.assertEqual(V.decision("saisir_depense", {"date": "d", "jeton": "JETON-OK"}, EMPREINTE, vivant), (True, {"date": "d"}))
        self.assertEqual(V.decision("saisir_depense", {"date": "d", "jeton": "4821"}, EMPREINTE, vivant), (True, {"date": "d"}))
        self.assertEqual(V.decision("saisir_depense", {"date": "d"}, EMPREINTE, vivant)[0], False)
        self.assertEqual(V.decision("saisir_depense", {"date": "d", "jeton": "perime"}, EMPREINTE, vivant)[0], False)
        self.assertEqual(V.decision("saisir_depense", {"date": "d", "jeton": "0000"}, EMPREINTE, vivant)[0], False)


class TestDialogue(unittest.TestCase):
    """Le serveur avec un verrou injecté : l'écriture sans jeton revient en erreur claire, avec jeton l'outil s'exécute sans lui."""

    def setUp(self):
        self.recus = []
        def appel(nom, arguments):
            self.recus.append((nom, arguments))
            return {"ok": nom}
        def verrou(nom, arguments):
            ok, args = V.decision(nom, arguments, EMPREINTE, lambda j: j == "JETON-OK")
            if not ok:
                raise Exception(V.MESSAGE_VERROUILLE)
            return args
        self.cat = {"nom": "test", "version": "0", "instructions": "", "outils": OUTILS, "liste": lambda: V.avec_jeton(outils_mcp()), "verrou": verrou}
        self.appel = appel

    def _call(self, nom, arguments):
        return traiter({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": nom, "arguments": arguments}}, self.appel, self.cat)["result"]

    def test_ecriture_verrouillee_puis_deverrouillee(self):
        r = self._call("saisir_depense", {"date": "2026-11-05", "libelle": "x", "montant_ttc": 10, "compte_charge": "c"})
        self.assertTrue(r["isError"])
        self.assertIn("Verrouillé", r["content"][0]["text"])
        self.assertEqual(self.recus, [])                                  # rien n'a été appelé
        r = self._call("saisir_depense", {"date": "2026-11-05", "libelle": "x", "montant_ttc": 10, "compte_charge": "c", "jeton": "JETON-OK"})
        self.assertFalse(r["isError"])
        self.assertEqual(self.recus[-1], ("saisir_depense", {"date": "2026-11-05", "libelle": "x", "montant_ttc": 10, "compte_charge": "c"}))

    def test_lecture_libre_et_liste(self):
        self.assertFalse(self._call("stock_article", {"texte": "frp"})["isError"])
        outils = traiter({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, self.appel, self.cat)["result"]["tools"]
        self.assertIn("jeton", next(o for o in outils if o["name"] == "verser_en_banque")["inputSchema"]["properties"])
        self.assertIn("deverrouiller", {o["name"] for o in outils})
