"""Sorties du Magasin : approbation des BL réservée au magasinier principal, sortie exceptionnelle tracée et
vérifiée (27/10/2026). Vrais BL en attente de la base, dans un savepoint : tout est annulé."""
from __future__ import annotations

import unittest

from customization_app import sorties_magasin as M

HEDI = "hedi.ibidhi@gmail.com"
AKRAM = "morchediakram0@gmail.com"     # Stock User, pas magasinier principal
SALMA = "salmaaquaworldservicing@gmail.com"
WASSIM = "koubaawassim@gmail.com"
S_CONFIG = "Config Stock Entrepot"


class TestSortiesMagasin(unittest.TestCase):
    def setUp(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.savepoint("sorties_magasin")
        if not M._champs() or not frappe.db.exists("Role", M.ROLE_PRINCIPAL):
            self.skipTest("patch ensure_sorties_magasin non joué")
        frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)
        self.attente = frappe.get_all("Delivery Note", filters={"docstatus": 0, "workflow_state": M.EN_ATTENTE,
                                                                M.CHAMP_FLAG: 1, "is_return": 0},
                                      pluck="name", order_by="creation desc")
        for u in (HEDI, AKRAM):
            if not frappe.db.exists("User", u) or not frappe.db.get_value("User", u, "enabled"):
                self.skipTest(f"utilisateur {u} absent")

    def tearDown(self):
        import frappe
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="sorties_magasin")

    def _bl(self, name):
        import frappe
        return frappe.db.get_value("Delivery Note", name, ["docstatus", "workflow_state", M.CHAMP_VERIF, M.CHAMP_SORTI_PAR,
                                                            M.CHAMP_VERIFIE_PAR, M.CHAMP_NOTE], as_dict=True)

    def _notifs(self, user, name):
        import frappe
        return frappe.get_all("Notification Log", filters={"for_user": user, "document_name": name}, pluck="subject")

    def test_roles(self):
        import frappe
        self.assertTrue(M.est_principal(HEDI))
        self.assertFalse(M.est_principal(AKRAM))
        self.assertIn(HEDI, M.principaux())
        self.assertNotIn(AKRAM, M.principaux())
        # Alertes : le magasinier en titre seulement (réglage responsable magasin) ; les autres principaux approuvent sans bandeau.
        frappe.db.set_single_value(S_CONFIG, "responsable_verification", frappe.db.get_value("Employee", {"user_id": HEDI}, "name"))
        self.assertEqual(M.alertes_pour(), [HEDI])
        if frappe.db.exists("User", WASSIM):
            frappe.set_user(WASSIM)
            self.assertTrue(M.est_principal())
            self.assertIsNone(M.compteur())
        frappe.set_user(HEDI)
        self.assertIsNotNone(M.compteur())

    def test_transitions_selon_le_role(self):
        """Hedi voit Approve / Reject, jamais la sortie exceptionnelle ; Akram ne voit QUE la sortie exceptionnelle."""
        import frappe
        if not self.attente:
            self.skipTest("aucun BL en attente")
        name = self.attente[0]
        frappe.set_user(HEDI)
        a = set(M.actions_possibles(name))
        self.assertIn(M.ACTION_APPROUVER, a)
        self.assertIn(M.ACTION_REJETER, a)
        self.assertNotIn(M.ACTION_EXCEPTIONNELLE, a)
        frappe.set_user(AKRAM)
        self.assertEqual(set(M.actions_possibles(name)), {M.ACTION_EXCEPTIONNELLE})
        with self.assertRaises(frappe.PermissionError):
            M.approuver(name)
        with self.assertRaises(frappe.PermissionError):
            M.verifier(name, M.CONFORME)

    def test_sortie_exceptionnelle_puis_verification(self):
        import frappe
        if not self.attente:
            self.skipTest("aucun BL en attente")
        name = self.attente[0]
        frappe.set_user(AKRAM)
        r = M.sortie_exceptionnelle(name, motif="Hedi en congé")
        self.assertEqual((r["docstatus"], r["verification"], r["sorti_par_user"]), (1, M.A_VERIFIER, AKRAM))
        bl = self._bl(name)
        self.assertEqual((bl.docstatus, bl.workflow_state, bl[M.CHAMP_VERIF], bl[M.CHAMP_SORTI_PAR]),
                         (1, M.APPROUVE, M.A_VERIFIER, AKRAM))
        self.assertIn("Motif : Hedi en congé", bl[M.CHAMP_NOTE])
        self.assertTrue(any("Sortie exceptionnelle" in s for s in self._notifs(HEDI, name)))   # Hedi prévenu
        e = M.etat()                                                                           # Akram : ses sorties
        self.assertFalse(e["principal"])
        self.assertIn(name, [b["name"] for b in e["miennes"]])
        self.assertNotIn(name, [b["name"] for b in e["a_approuver"]])
        frappe.set_user(HEDI)
        e = M.etat()
        self.assertTrue(e["principal"])
        self.assertIn(name, [b["name"] for b in e["a_verifier"]])
        with self.assertRaises(frappe.ValidationError):                   # un écart sans note est refusé
            M.verifier(name, M.ECART)
        r = M.verifier(name, M.ECART, note="2 cartouches en moins")
        self.assertEqual((r["verification"], r["verifie_par"]), (M.ECART, frappe.utils.get_fullname(HEDI)))
        self.assertTrue(any("Écart" in s for s in self._notifs(AKRAM, name)))                  # Akram prévenu
        e = M.etat()
        self.assertIn(name, [b["name"] for b in e["ecarts"]])
        self.assertNotIn(name, [b["name"] for b in e["a_verifier"]])
        r = M.verifier(name, M.CONFORME, note="régularisé par transfert")
        self.assertEqual(r["verification"], M.CONFORME)
        self.assertIn("2 cartouches en moins", r["note"])
        self.assertIn("régularisé", r["note"])
        self.assertIn(name, [b["name"] for b in M.etat()["historique"]])
        self.assertEqual(M.compteur()["ecarts"], 0)

    def test_approbation_normale_et_rejet(self):
        import frappe
        if len(self.attente) < 2:
            self.skipTest("moins de deux BL en attente")
        a, b = self.attente[:2]
        frappe.set_user(HEDI)
        self.assertIsNotNone(M.compteur())
        r = M.approuver(a)
        self.assertEqual((r["docstatus"], r["verification"] or None, r["sorti_par_user"]), (1, None, HEDI))
        self.assertNotIn(a, [x["name"] for x in M.etat()["a_verifier"]])          # rien à vérifier
        self.assertIn(a, [x["name"] for x in M.etat()["historique"]])
        M.rejeter(b, motif="quantité fausse")
        self.assertEqual(self._bl(b).workflow_state, M.REJETE)
        frappe.set_user(AKRAM)
        self.assertIsNone(M.compteur())

    def test_entree_en_attente_previent_le_magasinier(self):
        """Un BL qui passe en attente d'approbation crée une notification pour Hedi (hook on_update)."""
        import frappe
        if not self.attente:
            self.skipTest("aucun BL en attente")
        name = self.attente[0]
        frappe.db.delete("Notification Log", {"document_name": name})
        doc = frappe.get_doc("Delivery Note", name)
        doc.db_set("workflow_state", "Brouillon", update_modified=False)
        doc = frappe.get_doc("Delivery Note", name)
        doc.workflow_state = M.EN_ATTENTE
        doc.flags.ignore_validate_update_after_submit = True
        doc.save(ignore_permissions=True)
        self.assertTrue(any("à approuver" in s for s in self._notifs(HEDI, name)))

    def test_ecritures_en_post_seulement(self):
        import frappe
        for fn in (M.approuver, M.rejeter, M.sortie_exceptionnelle, M.verifier):
            self.assertEqual(frappe.allowed_http_methods_for_whitelisted_func.get(fn), ["POST"], fn.__name__)


class TestEchangeAConfirmer(unittest.TestCase):
    """Un BL d'échange (pièce reprise) ne s'approuve qu'après confirmation du magasinier (popup de la
    page Stock : pièce sortie ET pièce reprise). Pur, sans base."""

    def test_un_echange_sans_confirmation_est_refuse(self):
        self.assertTrue(M.echange_a_confirmer([{"item_code": "Rob-LC", "qte": 1}], 0))
        self.assertTrue(M.echange_a_confirmer([{"item_code": "Rob-LC", "qte": 1}], None))
        self.assertTrue(M.echange_a_confirmer([{"item_code": "Rob-LC", "qte": 1}], "0"))

    def test_un_echange_confirme_passe(self):
        self.assertFalse(M.echange_a_confirmer([{"item_code": "Rob-LC", "qte": 1}], 1))
        self.assertFalse(M.echange_a_confirmer([{"item_code": "Rob-LC", "qte": 1}], "1"))

    def test_un_bl_sans_reprise_ne_demande_rien(self):
        self.assertFalse(M.echange_a_confirmer([], 0))
        self.assertFalse(M.echange_a_confirmer(None, 0))


class TestChoixEntrepots(unittest.TestCase):
    """Entrepôts choisis dans le popup d'échange : vides = ceux du BL ; un nom inconnu ou inactif est refusé."""
    ACTIFS = {"Magasins - A&S", "Articles defectueux - A&S", "Hall - A&S"}

    def test_vides_gardent_les_entrepots_du_bl(self):
        self.assertEqual(M.choix_entrepots(None, "", self.ACTIFS), (None, None))

    def test_des_entrepots_actifs_sont_acceptes(self):
        self.assertEqual(M.choix_entrepots("Hall - A&S", " Articles defectueux - A&S ", self.ACTIFS),
                         ("Hall - A&S", "Articles defectueux - A&S"))

    def test_un_entrepot_inconnu_est_refuse(self):
        with self.assertRaises(ValueError):
            M.choix_entrepots("Magasins - A&S", "Stock Fantome - A&S", self.ACTIFS)
