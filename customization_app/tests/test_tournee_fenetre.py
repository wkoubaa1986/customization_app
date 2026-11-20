"""Limite de déplacement stricte : solveur, trafic et garde avant application."""
import copy
import unittest
from unittest.mock import patch
import frappe
from customization_app import tournee_optimisation as T


class TestLimiteHoraire(unittest.TestCase):
    def test_bornes_application_et_depassement_d_une_seconde(self):
        doc = frappe._dict(name='TEST', status='Open', starts_on='2026-11-25 16:00:00')
        for heure in ('14:00:00', '16:00:00', '18:00:00'):
            T.verifier_limite_application(doc, {'starts_on':'2026-11-25 '+heure}, '2026-11-25', 120)
        with patch.object(frappe, 'throw', side_effect=frappe.ValidationError):
            for heure in ('12:00:00', '13:59:59', '18:00:01', '19:00:00'):
                with self.assertRaises(frappe.ValidationError):
                    T.verifier_limite_application(doc, {'starts_on':'2026-11-25 '+heure}, '2026-11-25', 120)
            with self.assertRaises(frappe.ValidationError):
                T.verifier_limite_application(doc, {'starts_on':'2026-11-26 00:00:00'}, '2026-11-25', 0)

    def test_fenetre_vide_est_non_placee_sans_elargissement(self):
        sol = T.resoudre([[0,10],[10,0]], [{'service':30,'fenetre':(540,480),'vehicule':None}],
                        1, 480, 1080, limite_s=1)
        self.assertEqual(sol['non_places'],[1])

    def test_retour_a_l_employe_initial_et_recontrole_en_cascade(self):
        a = dict(tache='A', client='A', de='EMP1', employe='EMP2', original_min=960,
                 debut='12:00', debut_min=840, mobile=True, deplace=True, decale=True)
        b = dict(tache='B', client='B', de='EMP2', employe='EMP2', original_min=900,
                 debut='16:00', debut_min=960, mobile=True, deplace=False, decale=True)
        employes=[{'employe':'EMP1','nom':'Employé 1','apres':{'arrets':[]}},
                  {'employe':'EMP2','nom':'Employé 2','apres':{'arrets':[a,b]}}]
        passages=[]
        def recaler(es):
            passages.append(1)
            for e in es:
                for arret in e['apres']['arrets']:
                    arret['debut']=T._hm(arret['debut_min'] if not arret['mobile'] else 1100)
            return {'source':'google'}
        retablis, _=T.retablir_limite_horaire(employes,120,recaler)
        self.assertEqual(len(passages),2)
        self.assertEqual({r['tache'] for r in retablis},{'A','B'})
        self.assertEqual((a['employe'],a['debut'],a['deplace'],a['decale']),('EMP1','16:00',False,False))
        self.assertEqual((b['employe'],b['debut']),('EMP2','15:00'))
        self.assertTrue(a['non_place'] and b['non_place'])
        self.assertEqual([x['tache'] for x in employes[0]['apres']['arrets']],['A'])

    def test_aucun_repli_pour_deplacement_dans_limite_ou_heures_libres(self):
        e=[{'employe':'E','apres':{'arrets':[{'debut':'14:00','original_min':960}]}}]
        with patch('builtins.print'):
            self.assertEqual(T.retablir_limite_horaire(e,120,lambda _:self.fail('Recalcul inutile')),([],None))
        e[0]['apres']['arrets'][0]['debut']='12:00'
        self.assertEqual(T.retablir_limite_horaire(e,0,lambda _:self.fail('Heures explicitement libres')),([],None))
