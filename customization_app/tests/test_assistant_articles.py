"""Assistant Articles : règles pures (contrôles, prix, marge, groupes, changements) et dialogue MCP du second plugin."""
import json
import unittest

from customization_app import assistant_articles as R
from customization_app.assistant_chatgpt import OUTILS as OUTILS_ASSISTANT
from customization_app.mcp_articles import CATALOGUE, NOM_SERVEUR
from customization_app.mcp_chatgpt import traiter

FEUILLES = {"Bouteilles FRP", "Coudes"}
UNITES = {"Pièce", "Meter"}
MARQUES = {"ecoPurium"}
LISTES = {"Vente standard": {"selling": 1, "buying": 0}, "Compte Pro": {"selling": 1, "buying": 0}, "Achat standard": {"selling": 0, "buying": 1}}


class TestRegles(unittest.TestCase):
    def test_article_accepte_et_refus(self):
        self.assertEqual(R.verifier_article("FRP-1465", "Réservoir FRP", "Bouteilles FRP", "Pièce", 19, "ecoPurium", FEUILLES, UNITES, MARQUES), [])
        self.assertEqual(R.verifier_article("X", "Y", "Coudes", "Meter", "7", None, FEUILLES, UNITES, MARQUES), [])
        e = R.verifier_article(" FRP ", "", "Adoucisseurs", "Tonne", 12, "Inconnue", FEUILLES, UNITES, MARQUES)
        self.assertEqual(len(e), 6)                      # code, nom, groupe (parent), unité, tva, marque
        self.assertTrue(R.verifier_article("A<b>", "x" * 141, "Coudes", "Pièce", 19, None, FEUILLES, UNITES, MARQUES))

    def test_prix(self):
        self.assertEqual(R.lire_prix('{"Vente standard": "750,5", "Achat standard": 126.568}'), {"Vente standard": 750.5, "Achat standard": 126.568})
        self.assertEqual(R.lire_prix(None), {})
        with self.assertRaises(ValueError):
            R.lire_prix({"Vente standard": "cher"})
        with self.assertRaises(ValueError):
            R.lire_prix([750])
        self.assertEqual(R.verifier_prix({"Vente standard": 750, "Achat standard": 100}, LISTES), [])
        e = R.verifier_prix({"Vente standard": -1, "Gros": 10}, LISTES)
        self.assertEqual(len(e), 2)
        self.assertIn("Achat standard", e[1])

    def test_composants_et_codes(self):
        self.assertEqual(R.lire_composants('{"F-T33-A": 2, "M-80-V": "1"}'), {"F-T33-A": 2.0, "M-80-V": 1.0})
        self.assertEqual(R.lire_composants(None), {})
        with self.assertRaises(ValueError):
            R.lire_composants({"F-T33-A": 0})
        with self.assertRaises(ValueError):
            R.lire_composants(["F-T33-A"])
        self.assertEqual(R.lire_codes("A, B ,A\nC"), ["A", "B", "C"])
        self.assertEqual(R.lire_codes('["X", " Y "]'), ["X", "Y"])
        self.assertEqual(R.lire_codes(["X", "", None]), ["X"])
        self.assertEqual(R.lire_codes(None), [])

    def test_marge(self):
        self.assertEqual(R.marge_pct(119, 19, 60), 40.0)   # 100 HT, coût 60
        self.assertEqual(R.marge_pct("107", "7", 100), 0.0)
        self.assertIsNone(R.marge_pct(119, 19, 0))
        self.assertIsNone(R.marge_pct(0, 19, 60))
        self.assertLess(R.marge_pct(50, 19, 60), 0)

    def test_chemins_groupes(self):
        g = [{"name": R.GROUPE_RACINE, "parent_item_group": "", "is_group": 1},
             {"name": "Adoucisseurs", "parent_item_group": R.GROUPE_RACINE, "is_group": 1},
             {"name": "Vannes de commande", "parent_item_group": "Adoucisseurs", "is_group": 1},
             {"name": "Vannes adoucisseurs automatiques", "parent_item_group": "Vannes de commande", "is_group": 0},
             {"name": "Bacs à sels", "parent_item_group": "Adoucisseurs", "is_group": 0}]
        self.assertEqual(R.chemins_groupes(g), {"Vannes adoucisseurs automatiques": "Adoucisseurs > Vannes de commande > Vannes adoucisseurs automatiques",
                                                "Bacs à sels": "Adoucisseurs > Bacs à sels"})

    def test_changements(self):
        actuel = {"item_name": "Ancien", "description": "d", "brand": None, "disabled": 0, "custom_volume_m3": 0.0, "custom_sync_avec_woocommerce": 1}
        c = R.changements({"nom": " Nouveau ", "description": "d", "marque": "", "desactive": "1", "volume_m3": "0,25", "sync_site_web": "oui"}, actuel)
        self.assertEqual(c, {"item_name": "Nouveau", "disabled": 1, "custom_volume_m3": 0.25})
        self.assertEqual(R.changements({"desactive": 0, "rupture_site_web": None}, actuel), {})

    def test_coercer_et_modifiable(self):
        self.assertEqual(R.coercer({"fieldtype": "Check", "label": "x"}, "oui"), 1)
        self.assertEqual(R.coercer({"fieldtype": "Float", "label": "Poids"}, "1,5"), 1.5)
        self.assertEqual(R.coercer({"fieldtype": "Int", "label": "Délai"}, "3"), 3)
        self.assertEqual(R.coercer({"fieldtype": "Select", "label": "Méthode", "options": "\nFIFO\nMoving Average"}, "FIFO"), "FIFO")
        self.assertEqual(R.coercer({"fieldtype": "Date", "label": "Fin"}, "2026-12-31"), "2026-12-31")
        self.assertEqual(R.coercer({"fieldtype": "Data", "label": "x"}, " abc "), "abc")
        for champ, v in (({"fieldtype": "Int", "label": "x"}, "1.5"), ({"fieldtype": "Float", "label": "x"}, "lourd"),
                         ({"fieldtype": "Select", "label": "x", "options": "A\nB"}, "C"), ({"fieldtype": "Date", "label": "x"}, "31/12/2026")):
            with self.assertRaises(ValueError):
                R.coercer(champ, v)
        self.assertTrue(R.modifiable({"fieldname": "weight_per_unit", "fieldtype": "Float"}))
        for c in ({"fieldname": "item_code", "fieldtype": "Data"}, {"fieldname": "taxes", "fieldtype": "Table"},
                  {"fieldname": "image", "fieldtype": "Attach Image"}, {"fieldname": "x", "fieldtype": "Data", "read_only": 1},
                  {"fieldname": "has_variants", "fieldtype": "Check"}, {"fieldname": "disabled", "fieldtype": "Check"}):
            self.assertFalse(R.modifiable(c), c)

    def test_url(self):
        self.assertTrue(R.url_acceptable("https://exemple.com/img.jpg"))
        for u in ("ftp://x/y.jpg", "http://localhost/a.png", "http://erp.local/a.png", "/files/a.png", "", None):
            self.assertFalse(R.url_acceptable(u), u)


class TestPlugin(unittest.TestCase):
    """Le second serveur : son nom, ses outils, et rien de l'autre plugin."""

    def _appel(self, nom, arguments):
        if nom == "chercher_article":
            return [{"code": "FRP-1465", "texte": arguments.get("texte")}]
        raise Exception("Le code « X » existe déjà")

    def test_initialize_et_outils(self):
        r = traiter({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, self._appel, CATALOGUE)
        self.assertEqual(r["result"]["serverInfo"]["name"], NOM_SERVEUR)
        self.assertEqual(r["result"]["serverInfo"]["name"], "aqua-world-articles")
        self.assertIn("ARTICLES", r["result"]["instructions"])
        outils = traiter({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, self._appel, CATALOGUE)["result"]["tools"]
        self.assertEqual({o["name"] for o in outils}, set(R.OUTILS))
        self.assertFalse(set(R.OUTILS) & (set(OUTILS_ASSISTANT) - {"moi"}))      # jeux d'outils disjoints
        creer = next(o for o in outils if o["name"] == "creer_article")
        self.assertEqual(creer["inputSchema"]["required"], ["code", "nom", "groupe"])
        self.assertEqual(creer["inputSchema"]["properties"]["prix"], {"type": "object", "description": creer["inputSchema"]["properties"]["prix"]["description"],
                                                                      "additionalProperties": {"type": "number"}})
        self.assertEqual(next(o for o in outils if o["name"] == "image_article")["inputSchema"]["required"], ["code", "url"])
        self.assertEqual(next(o for o in outils if o["name"] == "chercher_article")["inputSchema"]["required"], [])
        dep = next(o for o in outils if o["name"] == "deplacer_articles")["inputSchema"]
        self.assertEqual((dep["required"], dep["properties"]["codes"]["items"]), (["codes", "groupe"], {"type": "string"}))
        self.assertEqual(next(o for o in outils if o["name"] == "creer_ensemble")["inputSchema"]["required"], ["code", "composants"])
        var = next(o for o in outils if o["name"] == "creer_variante")["inputSchema"]
        self.assertEqual(var["required"], ["modele", "valeurs"])
        self.assertEqual(var["properties"]["composants"]["additionalProperties"], {"type": "number"})
        self.assertEqual(next(o for o in outils if o["name"] == "creer_modele")["inputSchema"]["required"], ["code", "nom", "groupe", "attributs"])
        self.assertEqual(next(o for o in outils if o["name"] == "modifier_champs")["inputSchema"]["required"], ["code", "valeurs"])
        self.assertEqual(len(outils), 23)

    def test_appel_et_cloisonnement(self):
        r = traiter({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "chercher_article", "arguments": {"texte": "frp"}}}, self._appel, CATALOGUE)
        self.assertFalse(r["result"]["isError"])
        self.assertEqual(json.loads(r["result"]["content"][0]["text"])[0]["code"], "FRP-1465")
        r = traiter({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "creer_article", "arguments": {}}}, self._appel, CATALOGUE)
        self.assertTrue(r["result"]["isError"])
        # un outil de l'autre plugin n'existe pas ici
        r = traiter({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "saisir_depense", "arguments": {}}}, self._appel, CATALOGUE)
        self.assertEqual(r["error"]["code"], -32602)
        # et réciproquement, le plugin assistant ignore les outils articles
        r = traiter({"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "creer_article", "arguments": {}}}, self._appel)
        self.assertEqual(r["error"]["code"], -32602)
        self.assertEqual(traiter({"jsonrpc": "2.0", "id": 7, "method": "initialize"}, self._appel)["result"]["serverInfo"]["name"], "aqua-world-erp")
