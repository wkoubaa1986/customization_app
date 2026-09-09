"""Tests de l'encaissement des dettes depuis la caisse journalière (ticket #8).

Convention : `unittest.TestCase` pur, aucune base — comme `test_annulation_facture`.
Trois choses se testent ici, et ce sont les endroits où le bug vivait :

  - la RÈGLE qui écarte une dette dont la commande (ou la facture) est annulée,
    et l'ORDRE des refus opposés à la sélection ;
  - le Server Script « Traitement des encaissement » lu dans la fixture : il doit
    compiler, et ne plus recopier un lien BL/commande sans regarder le docstatus ;
  - le CONTRÔLE PRÉALABLE de ce même script, extrait de la fixture et EXÉCUTÉ sur
    un `frappe` factice — c'est le seul moyen de vérifier son comportement (une
    facture soumise que le traitement annulerait doit être refusée) sans site.

Le reste (échéanciers, paiements, recréation de facture) touche la comptabilité et
se vérifie en recette sur des cas réels.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import types
import unittest

import frappe

from customization_app import caisse_encaissement_dettes as CED

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "fixtures", "server_script.json")
DIALOGUE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "customize_erpnext", "page", "caisse_journaliere",
                        "caisse_journaliere.js")
NOM_SCRIPT = "Traitement des encaissement"


def _script():
    """Le code du Server Script tel qu'il partira en production (fixture)."""
    with open(FIXTURE, encoding="utf-8") as f:
        for s in json.load(f):
            if s.get("name") == NOM_SCRIPT:
                return s["script"]
    raise AssertionError("Server Script « %s » absent de la fixture." % NOM_SCRIPT)


def _lignes_de_facture(arbre):
    """Le dict qui recopie une ligne dans la facture recréée, repéré par `dn_detail`."""
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Dict) and any(
                isinstance(c, ast.Constant) and c.value == "dn_detail" for c in noeud.keys):
            return {c.value: v for c, v in zip(noeud.keys, noeud.values)}
    raise AssertionError("Le bloc de recopie des lignes de facture est introuvable.")


def _bloc_controle(arbre):
    """Le contrôle préalable du script : le `if name:` qui refuse la dette dont le
    document est annulé — ou le sera par le traitement lui-même."""
    for noeud in ast.walk(arbre):
        if (isinstance(noeud, ast.If) and isinstance(noeud.test, ast.Name)
                and noeud.test.id == "name" and "statut_cible" in ast.unparse(noeud)):
            return ast.unparse(noeud)
    raise AssertionError("Le contrôle préalable est introuvable dans le script.")


class Refus(Exception):
    """Ce que `frappe.throw` lève dans le script."""


class _FrappeFactice:
    """Le strict nécessaire au contrôle préalable : `frappe.db.get_value` et
    `frappe.throw`. Les documents sont donnés en dur — aucune base n'est ouverte."""

    def __init__(self, documents):
        self.documents = documents          # {(doctype, nom): {champ: valeur}}
        self.db = self                      # `frappe.db.get_value` retombe ici

    def get_value(self, doctype, nom, champ):
        return (self.documents.get((doctype, nom)) or {}).get(champ)

    def throw(self, message):
        raise Refus(message)


def _boucle_des_avances(arbre):
    """La purge des avances mortes : le `for avance in …` de la recréation de facture."""
    for noeud in ast.walk(arbre):
        if (isinstance(noeud, ast.For) and isinstance(noeud.target, ast.Name)
                and noeud.target.id == "avance"):
            return ast.unparse(noeud)
    raise AssertionError("La purge des avances est introuvable dans le script.")


def _purger_avances(avances, documents):
    """Joue la purge de la fixture sur une table d'avances donnée → celles gardées."""
    boucle = _boucle_des_avances(ast.parse(_script(), NOM_SCRIPT))
    espace = {"frappe": _FrappeFactice(documents), "avances_vivantes": [],
              "new_invoice": types.SimpleNamespace(get=lambda champ: avances)}
    exec(compile(boucle, "purge des avances", "exec"), espace)  # noqa: S102
    return espace["avances_vivantes"]


def _avance(paiement, doctype="Payment Entry"):
    return types.SimpleNamespace(reference_type=doctype, reference_name=paiement)


def _executer_controle(documents, cible, dette="ACC-PAY-2026-00123",
                       enc="ENC-2026-00042"):
    """Joue le contrôle préalable du script de la fixture sur une dette donnée."""
    bloc = _bloc_controle(ast.parse(_script(), NOM_SCRIPT))
    espace = {"frappe": _FrappeFactice(documents), "name": cible,
              "ipay": types.SimpleNamespace(ref_paiement=dette),
              "doc": types.SimpleNamespace(name=enc)}
    exec(compile(bloc, "controle prealable", "exec"), espace)  # noqa: S102


class TestMotifNonEncaissable(unittest.TestCase):
    """La règle : une dette dont le document est ANNULÉ ne s'encaisse pas — son
    paiement porterait un lien vers lui et Frappe le refuse."""

    def test_une_commande_soumise_est_encaissable(self):
        self.assertEqual(
            CED._motif_non_encaissable([("Sales Order", "SAL-ORD-2026-03325", 1)]), ("", ""))

    def test_une_commande_annulee_donne_un_motif_et_son_nom(self):
        self.assertEqual(
            CED._motif_non_encaissable([("Sales Order", "SAL-ORD-2026-03325", 2)]),
            (CED.MOTIF_COMMANDE_ANNULEE, "SAL-ORD-2026-03325"))

    def test_une_facture_annulee_donne_son_propre_motif(self):
        """Une dette d'ouverture pointe une facture, pas une commande : le motif
        affiché doit parler de la facture."""
        self.assertEqual(
            CED._motif_non_encaissable([("Sales Invoice", "ACC-SINV-2026-01068", 2)]),
            (CED.MOTIF_FACTURE_ANNULEE, "ACC-SINV-2026-01068"))

    def test_le_docstatus_en_chaine_est_compris(self):
        """`frappe.db.get_value` peut rendre le docstatus en chaîne."""
        self.assertEqual(CED._motif_non_encaissable([("Sales Order", "WEB1-007819", "2")]),
                         (CED.MOTIF_COMMANDE_ANNULEE, "WEB1-007819"))

    def test_une_commande_brouillon_n_est_pas_bloquee(self):
        """Un brouillon n'est pas annulé : il ne sert pas de lien `bl` (c'est
        `encaisser` qui s'en charge), mais il n'interdit pas l'encaissement."""
        self.assertEqual(CED._motif_non_encaissable([("Sales Order", "WEB1-007819", 0)]),
                         ("", ""))

    def test_une_dette_sans_document_reste_encaissable(self):
        """Le script sait la traiter par la référence de son paiement."""
        self.assertEqual(CED._motif_non_encaissable([]), ("", ""))
        self.assertEqual(CED._motif_non_encaissable([("", "", None), (None, None, None)]),
                         ("", ""))

    def test_la_facture_soumise_n_empeche_pas_de_voir_la_commande_annulee(self):
        """Le cas « Facturation Auto » : la dette porte la facture (soumise) ET la
        commande d'origine (annulée). C'est la commande qu'il faut nommer."""
        self.assertEqual(CED._motif_non_encaissable([
            ("Sales Invoice", "ACC-SINV-2026-01068", 1),
            ("Sales Order", "SAL-ORD-2026-03325", 2)]),
            (CED.MOTIF_COMMANDE_ANNULEE, "SAL-ORD-2026-03325"))

    def test_le_premier_document_annule_est_celui_qui_est_nomme(self):
        self.assertEqual(CED._motif_non_encaissable([
            ("Sales Invoice", "ACC-SINV-2026-01068", 2),
            ("Sales Order", "SAL-ORD-2026-03325", 2)]),
            (CED.MOTIF_FACTURE_ANNULEE, "ACC-SINV-2026-01068"))


def _dette(nom, montant=100.0, motif="", commande="SAL-ORD-2026-03325"):
    return {"name": nom, "montant": montant, "motif": motif, "commande": commande}


class TestSelectionPerimee(unittest.TestCase):
    """LE GARDE-FOU DU DOUBLE ENCAISSEMENT. La validation étant immédiate, rejouer
    une requête dont la réponse s'est perdue (réseau, double clic, retour arrière)
    ne doit RIEN pouvoir encaisser une seconde fois."""

    def test_rejouer_une_requete_deja_encaissee_est_refuse(self):
        """100 versés sur une dette de 200 : la dette d'origine a disparu, un
        reliquat de 100 l'a remplacée. La même requête rejouée retombait sur ce
        reliquat et encaissait 100 de plus, sans confirmation."""
        reliquat = _dette("PE-RELIQUAT", montant=100.0)
        choisies, refus = CED._trier_selection([reliquat], ["PE-1"])
        self.assertEqual(choisies, [])
        self.assertEqual(refus, (CED.REFUS_DISPARUES, ["PE-1"]))

    def test_une_seule_dette_disparue_refuse_toute_l_operation(self):
        """La liste a bougé : on ne devine pas ce que l'employé voulait vraiment."""
        restante = _dette("PE-2")
        choisies, refus = CED._trier_selection([restante], ["PE-1", "PE-2"])
        self.assertEqual(choisies, [])
        self.assertEqual(refus, (CED.REFUS_DISPARUES, ["PE-1"]))

    def test_le_refus_de_liste_perimee_passe_avant_les_autres(self):
        """Même si ce qui reste est par ailleurs bloqué, c'est le rechargement
        qu'il faut demander en premier."""
        bloquee = _dette("PE-2", motif=CED.MOTIF_COMMANDE_ANNULEE)
        _, refus = CED._trier_selection([bloquee], ["PE-1", "PE-2"])
        self.assertEqual(refus[0], CED.REFUS_DISPARUES)

    def test_sans_selection_le_repli_reste_permis(self):
        """L'appelant qui ne coche rien demande explicitement tout l'encaissable."""
        a = _dette("PE-1")
        choisies, refus = CED._trier_selection([a], [])
        self.assertEqual(choisies, [a])
        self.assertIsNone(refus)

    def test_un_client_vide_avec_selection_demande_un_rechargement(self):
        choisies, refus = CED._trier_selection([], ["PE-1"])
        self.assertEqual(choisies, [])
        self.assertEqual(refus, (CED.REFUS_DISPARUES, ["PE-1"]))


class TestTriDeLaSelection(unittest.TestCase):
    """L'ORDRE des refus : le motif détaillé passe avant « aucune dette encaissable »."""

    def test_la_selection_de_l_employe_est_respectee(self):
        a, b = _dette("PE-1"), _dette("PE-2")
        choisies, refus = CED._trier_selection([a, b], ["PE-2"])
        self.assertEqual(choisies, [b])
        self.assertIsNone(refus)

    def test_sans_selection_tout_l_encaissable_est_pris(self):
        a, b = _dette("PE-1"), _dette("PE-2", motif=CED.MOTIF_COMMANDE_ANNULEE)
        choisies, refus = CED._trier_selection([a, b], [])
        self.assertEqual(choisies, [a])
        self.assertIsNone(refus)

    def test_une_dette_annulee_cochee_est_refusee_avec_son_motif(self):
        bloquee = _dette("PE-2", motif=CED.MOTIF_COMMANDE_ANNULEE)
        choisies, refus = CED._trier_selection([_dette("PE-1"), bloquee], ["PE-1", "PE-2"])
        self.assertEqual(choisies, [])
        self.assertEqual(refus, (CED.REFUS_BLOQUEES, [bloquee]))

    def test_l_unique_dette_annulee_d_un_client_est_nommee(self):
        """Le défaut d'origine : le refus générique « aucune dette encaissable »
        partait le premier et l'employé n'apprenait ni laquelle ni pourquoi."""
        bloquee = _dette("PE-1", motif=CED.MOTIF_FACTURE_ANNULEE,
                         commande="ACC-SINV-2026-01068")
        choisies, refus = CED._trier_selection([bloquee], ["PE-1"])
        self.assertEqual(choisies, [])
        self.assertEqual(refus[0], CED.REFUS_BLOQUEES)
        self.assertEqual(refus[1], [bloquee])

    def test_un_client_sans_dette_recoit_le_refus_generique(self):
        self.assertEqual(CED._trier_selection([], []), ([], (CED.REFUS_AUCUNE, [])))

    def test_toutes_bloquees_sans_selection_donne_le_refus_generique(self):
        """Rien n'a été coché : aucune dette à nommer, le message générique suffit."""
        _, refus = CED._trier_selection(
            [_dette("PE-1", motif=CED.MOTIF_COMMANDE_ANNULEE)], [])
        self.assertEqual(refus, (CED.REFUS_AUCUNE, []))


class TestDetteFactureeAvecCommandeAnnulee(unittest.TestCase):
    """« Facturation Auto » recopie le paiement de dette en gardant `reference_no`
    (la COMMANDE) et en remplaçant ses références par la FACTURE. Commande annulée
    depuis, facture toujours soumise : la dette passait pour encaissable, était
    cochée, et la validation échouait plus tard sur la commande annulée.

    Les documents sont donnés en dur — `_document` est remplacé, aucune base n'est
    ouverte (même patron que `test_annulation_facture` avec `_capacite`).
    """

    COMMANDE = "SAL-ORD-2026-03325"
    FACTURE = "ACC-SINV-2026-01068"

    def setUp(self):
        self.documents = {
            ("Sales Invoice", self.FACTURE): frappe._dict(
                grand_total=161.0, posting_date="2026-06-30", docstatus=1),
            ("Sales Order", self.COMMANDE): frappe._dict(
                grand_total=161.0, transaction_date="2026-05-18", docstatus=2),
        }
        self._vrai = CED._document
        CED._document = lambda doctype, nom, champ: self.documents.get((doctype, nom))

    def tearDown(self):
        CED._document = self._vrai

    def _dette_facturee(self):
        """La dette telle que la rend la requête : la facture dans les références,
        la commande dans `reference_no`."""
        return CED._qualifier(frappe._dict(
            name="ACC-PAY-2026-00123", paid_amount=161.0, posting_date="2026-06-30",
            reference_no=self.COMMANDE, paid_to=CED.COMPTE_DETTES,
            party_name="AQUA SERVICE", commande=self.FACTURE))

    def test_la_commande_annulee_bloque_la_dette_et_est_nommee(self):
        dette = self._dette_facturee()
        # La dette reste rattachée à sa facture pour l'affichage…
        self.assertEqual(dette.commande, self.FACTURE)
        self.assertEqual(dette.commande_doctype, "Sales Invoice")
        # … mais c'est la commande annulée qui la bloque.
        self.assertEqual(dette.motif, CED.MOTIF_COMMANDE_ANNULEE)
        self.assertEqual(dette.document_bloquant, self.COMMANDE)

    def test_une_commande_soumise_laisse_la_dette_encaissable(self):
        self.documents[("Sales Order", self.COMMANDE)].docstatus = 1
        dette = self._dette_facturee()
        self.assertEqual(dette.motif, "")
        self.assertEqual(dette.document_bloquant, "")

    def test_le_dialogue_recoit_la_dette_comme_non_encaissable(self):
        """La réponse de `dettes_client` : la case sera grisée, motif et document
        à l'appui."""
        dette = self._dette_facturee()
        vrais = (frappe.only_for, frappe.get_meta, CED._dettes)
        frappe.only_for = lambda *a, **k: None
        frappe.get_meta = lambda doctype: types.SimpleNamespace(
            get_field=lambda champ: types.SimpleNamespace(options="Zitouna\nBIAT"))
        CED._dettes = lambda client: [dette]
        # `@frappe.whitelist()` enveloppe la méthode dans un contrôle de typage qui
        # lit `frappe.local.flags` : hors site, ce drapeau n'existe pas.
        sans_flags = not hasattr(frappe.local, "flags")
        if sans_flags:
            frappe.local.flags = frappe._dict(in_test=False)
        try:
            reponse = CED.dettes_client("AQUA SERVICE")
        finally:
            frappe.only_for, frappe.get_meta, CED._dettes = vrais
            if sans_flags:
                del frappe.local.flags
        ligne = reponse["dettes"][0]
        self.assertFalse(ligne["encaissable"])
        self.assertEqual(ligne["motif"], CED.MOTIF_COMMANDE_ANNULEE)
        self.assertEqual(ligne["document_bloquant"], self.COMMANDE)
        self.assertEqual(ligne["commande"], self.FACTURE)

    def test_l_encaissement_la_refuse(self):
        """Cochée malgré tout (ancien dialogue en cache, appel direct), elle est
        refusée avec son motif — pas encaissée puis mise en échec."""
        dette = self._dette_facturee()
        choisies, refus = CED._trier_selection([dette], [dette.name])
        self.assertEqual(choisies, [])
        self.assertEqual(refus, (CED.REFUS_BLOQUEES, [dette]))


class TestControlePrealableDuScript(unittest.TestCase):
    """Le contrôle préalable de la fixture, EXÉCUTÉ : ce qu'il laisse passer et ce
    qu'il refuse, avec le message rendu à l'employé."""

    def test_une_commande_soumise_passe(self):
        _executer_controle({("Sales Order", "SAL-ORD-2026-03325"): {"docstatus": 1}},
                           "SAL-ORD-2026-03325")

    def test_une_commande_annulee_est_refusee(self):
        with self.assertRaises(Refus) as levee:
            _executer_controle({("Sales Order", "SAL-ORD-2026-03325"): {"docstatus": 2}},
                               "SAL-ORD-2026-03325")
        self.assertIn("SAL-ORD-2026-03325", str(levee.exception))
        self.assertIn("ACC-PAY-2026-00123", str(levee.exception))
        self.assertIn("annule", str(levee.exception))

    def test_une_facture_d_ouverture_soumise_passe(self):
        """Le cas nominal d'une dette sans commande : le traitement n'y touche pas."""
        _executer_controle({("Sales Invoice", "ACC-SINV-2026-00007"):
                            {"docstatus": 1, "is_opening": "Yes"}},
                           "ACC-SINV-2026-00007")

    def test_une_facture_soumise_que_le_traitement_annulerait_est_refusee(self):
        """LE cas resté ouvert : la facture est encore soumise à l'entrée, mais le
        bloc « Cancel invoices » l'annule et la recrée avant la création des
        paiements — qui la référenceraient annulée. On refuse avant toute écriture,
        en nommant la dette, la facture et l'encaissement."""
        with self.assertRaises(Refus) as levee:
            _executer_controle({("Sales Invoice", "ACC-SINV-2026-01068"):
                                {"docstatus": 1, "is_opening": "No"}},
                               "ACC-SINV-2026-01068")
        message = str(levee.exception)
        self.assertIn("ACC-PAY-2026-00123", message)      # la dette
        self.assertIn("ACC-SINV-2026-01068", message)     # la facture
        self.assertIn("ENC-2026-00042", message)          # l'encaissement

    def test_une_facture_deja_annulee_est_refusee(self):
        with self.assertRaises(Refus) as levee:
            _executer_controle({("Sales Invoice", "ACC-SINV-2026-01068"):
                                {"docstatus": 2, "is_opening": "No"}},
                               "ACC-SINV-2026-01068")
        self.assertIn("ACC-SINV-2026-01068", str(levee.exception))

    def test_une_cible_inconnue_ne_bloque_pas(self):
        """Ni commande ni facture retrouvée : rien ne prouve qu'il y ait un problème."""
        _executer_controle({}, "VIEUX-REF-42")


class TestContratDeValidation(unittest.TestCase):
    """Les DEUX contrats d'`encaisser` : l'ancien (brouillon puis `valider`) reste
    le défaut, le nouveau dialogue demande explicitement la validation immédiate.

    Sans cela, un dialogue resté ouvert au moment du déploiement recevrait un
    document DÉJÀ soumis et proposerait quand même de l'annuler : l'employé
    croirait avoir renoncé à une opération pourtant enregistrée.
    """

    def test_le_defaut_laisse_un_brouillon(self):
        self.assertEqual(
            inspect.signature(CED.encaisser).parameters["soumettre"].default, 0)

    def test_le_dialogue_demande_la_validation_immediate(self):
        with open(DIALOGUE, encoding="utf-8") as f:
            js = f.read()
        appel = js[js.index('method: API + ".encaisser"'):][:600]
        self.assertIn("soumettre: 1", appel)


class TestLectureDePhotoTolerante(unittest.TestCase):
    """`_comparer_photo` tourne APRÈS le commit : elle ne doit jamais lever.

    Le modèle est censé rendre un objet JSON ; « null », « [] » ou un nombre
    passent pourtant `json.loads`, et un `.get` dessus casserait alors que
    l'encaissement est définitivement enregistré.
    """

    def setUp(self):
        # `_()` et `flt(x, 3)` ont besoin d'un site : on les neutralise, seule la
        # LOGIQUE se teste ici (même parti pris que les autres tests du dépôt).
        self._vrais = (CED._, CED.flt)
        CED._ = lambda message: message
        CED.flt = lambda valeur, precision=None: float(valeur)
        self.piece = {"mode": "Chèque", "montant": 161.0, "numero": "1234567"}

    def tearDown(self):
        CED._, CED.flt = self._vrais

    def _comparer(self, lu):
        return CED._comparer_photo(lu, self.piece, "chèque n°1234567", "chèque")

    def test_une_reponse_qui_n_est_pas_un_objet_donne_un_avertissement(self):
        for lu in (None, [], "illisible", 12, True):
            avert = self._comparer(lu)
            self.assertEqual(len(avert), 1, "réponse %r" % (lu,))
            self.assertIn("exploitable", avert[0])

    def test_un_objet_vide_ne_reproche_rien(self):
        self.assertEqual(self._comparer({}), [])

    def test_une_photo_declaree_illisible_est_signalee(self):
        self.assertEqual(len(self._comparer({"lisible": False})), 1)

    def test_un_numero_different_est_signale(self):
        avert = self._comparer({"numero": "9999999"})
        self.assertEqual(len(avert), 1)
        self.assertIn("9999999", avert[0])

    def test_un_montant_different_est_signale(self):
        avert = self._comparer({"montant": 200})
        self.assertEqual(len(avert), 1)
        self.assertIn("200", avert[0])

    def test_un_montant_illisible_ne_reproche_rien(self):
        self.assertEqual(self._comparer({"montant": None, "numero": None}), [])


class TestAvertissementsApresSoumission(unittest.TestCase):
    """Une panne de la lecture des photos ne doit PAS ressembler à un échec
    d'encaissement : le paiement est déjà enregistré, l'employé recommencerait."""

    def setUp(self):
        self._vrais = (CED._, CED._verifier_photo, frappe.log_error)
        CED._ = lambda message: message
        frappe.log_error = lambda **kwargs: None
        self.pieces = [{"mode": "Chèque", "montant": 161.0, "numero": "1234567",
                        "photo": "data:image/jpeg;base64,xxx"}]

    def tearDown(self):
        CED._, CED._verifier_photo, frappe.log_error = self._vrais

    def test_une_exception_devient_un_avertissement(self):
        def casse(piece):
            raise AttributeError("'NoneType' object has no attribute 'get'")

        CED._verifier_photo = casse
        avert = CED._avertissements_photos(self.pieces)
        self.assertEqual(len(avert), 1)
        self.assertIn("l'encaissement est bien enregistré", avert[0])

    def test_une_panne_du_journal_ne_casse_pas_non_plus(self):
        """Journaliser touche la base : après le commit, elle peut aussi refuser."""
        def casse(piece):
            raise RuntimeError("modèle indisponible")

        CED._verifier_photo = casse
        frappe.log_error = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("db"))
        self.assertEqual(len(CED._avertissements_photos(self.pieces)), 1)

    def test_les_especes_et_les_pieces_sans_photo_sont_ignorees(self):
        CED._verifier_photo = lambda piece: ["jamais"]
        self.assertEqual(CED._avertissements_photos([
            {"mode": "Espèces", "montant": 50.0, "numero": "", "photo": None},
            {"mode": "Chèque", "montant": 50.0, "numero": "1234567", "photo": None},
        ]), [])

    def test_les_avertissements_normaux_remontent(self):
        CED._verifier_photo = lambda piece: ["chèque n°1234567 : photo illisible."]
        self.assertEqual(CED._avertissements_photos(self.pieces),
                         ["chèque n°1234567 : photo illisible."])


class TestPurgeDesAvancesMortes(unittest.TestCase):
    """LA cause du refus vu sur ENC-09-09-2026-00001 : « Impossible de lier le
    document annulé – Ligne #2 : Nom de référence : ACC-PAY-2026-03539 ».

    `copy_doc` recopie la table des paiements anticipés de la facture ; ses lignes
    pointent des Payment Entry, et un paiement annulé (ou supprimé) y reste inscrit
    — l'annulation avec `ignore_links` ne nettoie pas la facture. La facture
    recréée ne doit garder que les avances dont le paiement est ENCORE soumis.
    """

    VIVANT = "ACC-PAY-2026-04000"
    ANNULE = "ACC-PAY-2026-03539"
    SUPPRIME = "ACC-PAY-2026-03540"

    def setUp(self):
        self.documents = {
            ("Payment Entry", self.VIVANT): {"docstatus": 1},
            ("Payment Entry", self.ANNULE): {"docstatus": 2},
        }

    def test_une_avance_dont_le_paiement_est_soumis_est_gardee(self):
        gardees = _purger_avances([_avance(self.VIVANT)], self.documents)
        self.assertEqual([a.reference_name for a in gardees], [self.VIVANT])

    def test_l_avance_du_paiement_annule_est_ecartee(self):
        gardees = _purger_avances(
            [_avance(self.VIVANT), _avance(self.ANNULE)], self.documents)
        self.assertEqual([a.reference_name for a in gardees], [self.VIVANT])

    def test_l_avance_d_un_paiement_supprime_est_ecartee(self):
        """Le script supprime les anciennes dettes : le lien ne pointe plus rien."""
        gardees = _purger_avances([_avance(self.SUPPRIME)], self.documents)
        self.assertEqual(gardees, [])

    def test_une_ligne_sans_reference_est_ecartee(self):
        gardees = _purger_avances([_avance("", doctype="")], self.documents)
        self.assertEqual(gardees, [])

    def test_une_table_vide_ne_casse_pas(self):
        self.assertEqual(_purger_avances([], self.documents), [])


class TestFixtureTraitementDesEncaissements(unittest.TestCase):
    """Le Server Script de la fixture — le code qui a levé « Impossible de lier le
    document annulé » en production."""

    def setUp(self):
        self.script = _script()
        self.arbre = ast.parse(self.script, NOM_SCRIPT)

    def test_le_script_compile(self):
        compile(self.script, NOM_SCRIPT, "exec")

    def test_les_liens_bl_et_commande_passent_par_une_variable(self):
        """Recopier `item.delivery_note` tel quel est ce qui reliait un BL annulé."""
        lignes = _lignes_de_facture(self.arbre)
        for champ in ("sales_order", "delivery_note"):
            self.assertIsInstance(
                lignes[champ], ast.Name,
                "Le lien %s doit passer par une variable filtrée sur le docstatus." % champ)

    def test_le_lien_n_est_recopie_que_si_le_document_est_soumis(self):
        affectations = {n.targets[0].id: ast.unparse(n.value)
                        for n in ast.walk(self.arbre)
                        if isinstance(n, ast.Assign) and len(n.targets) == 1
                        and isinstance(n.targets[0], ast.Name)}
        for champ, doctype in (("sales_order", "Sales Order"),
                               ("delivery_note", "Delivery Note")):
            source = affectations[_lignes_de_facture(self.arbre)[champ].id]
            self.assertIn(doctype, source)
            self.assertIn("docstatus", source)
            self.assertIn("== 1", source)

    def test_les_details_de_ligne_suivent_leur_parent(self):
        """`so_detail`/`dn_detail` sont des Data : gardés seuls, ils désigneraient
        une ligne d'un document que la facture ne référence plus."""
        lignes = _lignes_de_facture(self.arbre)
        for champ in ("so_detail", "dn_detail"):
            self.assertIsInstance(lignes[champ], ast.IfExp,
                                  "%s doit être conditionné au lien de son parent." % champ)

    def test_le_script_refuse_une_dette_dont_le_document_est_annule(self):
        """Le contrôle vient AVANT toute écriture, et nomme la dette et le document."""
        self.assertIn("est annule, il ne peut plus recevoir de paiement", self.script)
        controle = self.script.index("statut_cible")
        self.assertLess(controle, self.script.index("Payement_left={}"),
                        "Le contrôle doit précéder la réécriture des échéanciers.")

    def test_les_avances_sont_purgees_avant_de_recreer_la_facture(self):
        """Purger après l'insert ne servirait à rien : c'est l'insert qui refuse le
        lien vers le paiement annulé. (Sur les lignes, pas sur le texte : les
        commentaires du script citent `new_invoice.insert()`.)"""
        purge = min(n.lineno for n in ast.walk(self.arbre)
                    if isinstance(n, ast.Assign)
                    and ast.unparse(n.targets[0]) == "new_invoice.advances")
        insert = min(n.lineno for n in ast.walk(self.arbre)
                     if isinstance(n, ast.Call)
                     and ast.unparse(n.func) == "new_invoice.insert")
        self.assertLess(purge, insert)


if __name__ == "__main__":
    unittest.main()
