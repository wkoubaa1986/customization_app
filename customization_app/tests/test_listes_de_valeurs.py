"""Listes de valeurs (champs Select) : un réglage des fixtures ne doit pas retirer une valeur que le type de document
déclare. Cas du 23/11/2026 : un Property Setter de juin 2026 remplaçait la liste de « recontacte » par l'ancienne ;
« 1er appel sans réponse », posé par la liste d'appels, faisait ensuite refuser tout enregistrement de la tâche."""
import json
import unittest
from pathlib import Path

APP = Path(__file__).parents[1]


def _types_de_l_app() -> dict:
    """{nom du type: {champ: [valeurs]}} pour les types de document décrits dans l'app."""
    out = {}
    for chemin in APP.glob("*/doctype/*/*.json"):
        try:
            d = json.loads(chemin.read_text())
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("doctype") != "DocType":
            continue
        out[d["name"]] = {f["fieldname"]: (f.get("options") or "").split("\n")
                          for f in d.get("fields", []) if f.get("fieldtype") == "Select"}
    return out


class TestListesDeValeurs(unittest.TestCase):
    def test_aucun_reglage_ne_retire_une_valeur_du_type(self):
        types = _types_de_l_app()
        self.assertIn("Tache de travail", types)
        fautes = []
        for r in json.loads((APP / "fixtures" / "property_setter.json").read_text()):
            if r.get("property") != "options" or r.get("doc_type") not in types:
                continue
            declarees = types[r["doc_type"]].get(r.get("field_name"))
            if declarees is None:
                continue
            manque = [v for v in declarees if v not in (r.get("value") or "").split("\n")]
            if manque:
                fautes.append("%s retire %s" % (r["name"], manque))
        self.assertEqual(fautes, [])

    def test_les_appels_sans_reponse_sont_des_valeurs_du_champ(self):
        from customization_app import api
        valeurs = _types_de_l_app()["Tache de travail"]["recontacte"]
        self.assertIn(api.RECONTACTE_1ER_APPEL, valeurs)
        self.assertIn(api.RECONTACTE_2E_APPEL, valeurs)

    def test_le_patch_supprime_le_bon_reglage(self):
        from customization_app.patches import supprimer_reglage_options_recontacte as P
        self.assertEqual(P.REGLAGE, "Tache de travail-recontacte-options")
        self.assertIn("customization_app.patches.supprimer_reglage_options_recontacte", (APP / "patches.txt").read_text())


if __name__ == "__main__":
    unittest.main()
