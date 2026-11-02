"""Plugin ChatGPT (serveur MCP) : dialogue JSON-RPC, règles pures."""
import json
import unittest

from customization_app.assistant_chatgpt import OUTILS, outils_mcp
from customization_app.mcp_chatgpt import VERSION_PROTOCOLE, traiter


def appel_factice(nom, arguments):
    if nom == "chercher_client":
        return [{"name": "Lamia Zaabi", "texte": arguments.get("texte")}]
    raise Exception("Le montant TTC doit être positif.")


class TestMCP(unittest.TestCase):
    def test_initialize_et_ping(self):
        r = traiter({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, appel_factice)
        self.assertEqual((r["id"], r["result"]["protocolVersion"], r["result"]["serverInfo"]["name"]), (1, VERSION_PROTOCOLE, "aqua-world-erp"))
        self.assertEqual(traiter({"jsonrpc": "2.0", "id": 2, "method": "ping"}, appel_factice)["result"], {})
        self.assertIsNone(traiter({"jsonrpc": "2.0", "method": "notifications/initialized"}, appel_factice))

    def test_liste_des_outils(self):
        outils = traiter({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, appel_factice)["result"]["tools"]
        self.assertEqual({o["name"] for o in outils}, set(OUTILS))
        dep = next(o for o in outils if o["name"] == "saisir_depense")
        self.assertEqual(dep["inputSchema"]["required"], ["date", "libelle", "montant_ttc", "compte_charge"])
        self.assertEqual(next(o for o in outils if o["name"] == "moi")["inputSchema"]["required"], [])
        self.assertEqual(len(outils_mcp()), len(OUTILS))

    def test_appel_outil_ok_et_erreur_metier(self):
        r = traiter({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "chercher_client", "arguments": {"texte": "zaabi"}}}, appel_factice)
        self.assertFalse(r["result"]["isError"])
        self.assertEqual(json.loads(r["result"]["content"][0]["text"])[0]["name"], "Lamia Zaabi")
        r = traiter({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "saisir_depense", "arguments": {}}}, appel_factice)
        self.assertTrue(r["result"]["isError"])
        self.assertIn("positif", r["result"]["content"][0]["text"])

    def test_inconnus(self):
        self.assertEqual(traiter({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "supprimer_tout"}}, appel_factice)["error"]["code"], -32602)
        self.assertEqual(traiter({"jsonrpc": "2.0", "id": 7, "method": "resources/list"}, appel_factice)["error"]["code"], -32601)
