"""Relance Paiements Clients — ce qui s'est dit sur une dette et sur le client, visible dans l'écran (08/10/2026) :
commentaires des pièces liées à la dette (paiement, commande, facture, tâche), dernière relance et commentaire pris au
téléphone. Sur une vraie dette de la base, dans un savepoint : tout est annulé."""
from __future__ import annotations

import unittest

from customization_app import api

UTILISATEUR = "sadokaquaworld@gmail.com"


class TestCommentairesDette(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("relance_cmt")
        row = frappe.db.sql("""select distinct gle.voucher_no pe, pe.party customer, r.reference_name so
                               from `tabGL Entry` gle
                               join `tabPayment Entry` pe on pe.name = gle.voucher_no and pe.party_type = 'Customer'
                               join `tabPayment Entry Reference` r on r.parent = pe.name and r.reference_doctype = 'Sales Order'
                               where gle.voucher_type = 'Payment Entry' and gle.is_cancelled = 0 and gle.debit > 0
                                 and gle.account = 'Dettes - A&S'
                               order by gle.posting_date desc limit 1""", as_dict=True)
        if not row or not frappe.db.exists("User", UTILISATEUR):
            self.skipTest("pas de dette liée à une commande")
        self.pe, self.client, self.so = row[0].pe, row[0].customer, row[0].so

    def tearDown(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="relance_cmt")

    def _commenter(self, doctype, nom, texte, auteur=UTILISATEUR):
        import frappe
        c = frappe.get_doc({"doctype": "Comment", "comment_type": "Comment", "reference_doctype": doctype,
                            "reference_name": nom, "content": texte}).insert(ignore_permissions=True)
        frappe.db.set_value("Comment", c.name, {"owner": auteur, "comment_by": None}, update_modified=False)
        return c.name

    def test_le_commentaire_d_une_dette_apparait_une_fois(self):
        explication = "💵 Caisse du 07/10/2026 — Dette : <b>Le client paiera plus tard</b> — essai relance 7f3a"
        self._commenter("Sales Order", self.so, explication)
        self._commenter("Payment Entry", self.pe, explication)                  # même texte ailleurs : une fois
        self._commenter("Sales Order", self.so, "🗺️ Optimisation de la tournée du 09-10-2026 : A → B")   # trace du code
        self._commenter("Sales Order", self.so, "Correction de numérotation : 1 -&gt; 2", auteur="Administrator")
        liste = api._commentaires_des_paiements(api._pieces_des_paiements([self.pe]))[self.pe]
        textes = [c["texte"] for c in liste]
        self.assertEqual(textes.count("💵 Caisse du 07/10/2026 — Dette : Le client paiera plus tard — essai relance 7f3a"), 1)
        self.assertFalse([t for t in textes if t.startswith("🗺️") or "numérotation" in t])
        ligne = next(x for x in api.get_relance_detail(self.client) if x["voucher_no"] == self.pe)
        self.assertIn("essai relance 7f3a", ligne["commentaires"][0]["texte"])
        self.assertTrue(ligne["commentaires"][0]["par"])

    def test_la_liste_montre_la_derniere_relance_et_le_commentaire(self):
        api._log_relance_comment(self.client, "Téléphone", "promet de payer vendredi — essai 7f3a")
        self._commenter("Sales Order", self.so, "client absent, repasser — essai 7f3a")
        client = next((c for c in api.get_relance_clients()["clients"] if c["customer"] == self.client), None)
        if not client:
            self.skipTest("client sans reste à relancer")
        self.assertEqual((client["derniere_relance"]["type"], client["derniere_relance"]["note"]),
                         ("Téléphone", "promet de payer vendredi — essai 7f3a"))
        self.assertEqual(client["commentaires"]["dernier"]["texte"], "client absent, repasser — essai 7f3a")
        histo = api.get_historique_relances(self.client)
        self.assertTrue(histo[0]["par"])
