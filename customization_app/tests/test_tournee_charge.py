"""Redistribution réservée aux techniciens et équilibrage du travail actif."""
import unittest
from unittest.mock import patch
import frappe
from customization_app import tournee_optimisation as T


class TestCharge(unittest.TestCase):
    def test_detail_charge_hors_attente_et_pause(self):
        arrets=[{'service':30,'position':'adresse','attente':180},
                {'service':60,'position':'Google Maps estimé'}, {'service':45,'position':'dépôt'}]
        self.assertEqual(T.charge_travail(arrets,40,10,5),
                         {'interventions':135,'trajets':40,'stationnement':20,'rangement':10,'total':205})

    def test_garde_redistribution_et_epingle(self):
        doc=frappe._dict(name='TEST',status='Open',custom_choix_du_staff='E1')
        for techs in (set(),{'E1'},{'E2'}):
            with patch.object(frappe,'throw',side_effect=frappe.ValidationError):
                with self.assertRaises(frappe.ValidationError):
                    T.verifier_redistribution(doc,{'employe':'E2'},techs)
        T.verifier_redistribution(doc,{'employe':'E2'},{'E1','E2'})
        T.verifier_redistribution(doc,{'employe':'E1'},set())
        doc[T.EMPLOYE_FIXE]=1
        T.verifier_redistribution(doc,{'employe':'E2'},set())  # gardée chez E1 par appliquer

    def test_duree_equilibree_plutot_que_nombre_de_taches(self):
        services=[180,30,30,30,30,30]
        minutes=[[0]*7 for _ in range(7)]
        arrets=[{'service':s,'fenetre':None,'vehicule':None,'vehicules':[0,1]} for s in services]
        sol=T.resoudre(minutes,arrets,2,480,1080,equilibre=5,vehicules_equilibres=[0,1],limite_s=1)
        self.assertEqual(sol['non_places'],[])
        self.assertEqual(sorted(sum(services[n-1] for n,_ in route) for route in sol['routes']),[150,180])
        self.assertEqual(sorted(map(len,sol['routes'])),[1,5])

    def test_non_technicien_exclu_de_l_equilibrage_et_des_transferts(self):
        # Une charge de 10 h chez le troisième employé ne doit pas masquer l'écart
        # entre les deux techniciens. Ses tâches lui restent réservées.
        services=[30]*6+[600]
        minutes=[[0]*8 for _ in range(8)]
        arrets=[{'service':s,'fenetre':None,'vehicule':None,'vehicules':[0,1]} for s in services[:-1]]
        arrets.append({'service':600,'fenetre':None,'vehicule':2})
        sol=T.resoudre(minutes,arrets,3,480,1440,equilibre=5,vehicules_equilibres=[0,1],limite_s=1)
        self.assertEqual(sol['non_places'],[])
        self.assertEqual([len(r) for r in sol['routes']],[3,3,1])
        self.assertEqual([n for n,_ in sol['routes'][2]],[7])

    def test_charge_fixe_comptee_meme_si_sortie_du_modele_de_trajets(self):
        minutes=[[0]*7 for _ in range(7)]
        arrets=[{'service':30,'fenetre':None,'vehicule':None,'vehicules':[0,1]} for _ in range(6)]
        sol=T.resoudre(minutes,arrets,2,480,1080,equilibre=5,vehicules_equilibres=[0,1],charges_fixes=[120,0],limite_s=1)
        self.assertEqual([len(r) for r in sol['routes']],[1,5])

    def test_trajets_et_marges_comptent_dans_la_charge(self):
        # Même dépôt : une visite isolée à 60 min de route ne vaut pas une visite
        # voisine. Les petites visites doivent aller principalement à l'autre.
        minutes=[[0,60,5,5,5],[60,0,60,60,60],[5,60,0,2,2],[5,60,2,0,2],[5,60,2,2,0]]
        arrets=[{'service':30,'fenetre':None,'vehicule':None,'vehicules':[0,1]} for _ in range(4)]
        sol=T.resoudre(minutes,arrets,2,480,1080,equilibre=5,vehicules_equilibres=[0,1],marge=10,rangement=5,limite_s=1)
        isole=next(r for r in sol['routes'] if any(n==1 for n,_ in r))
        self.assertEqual([n for n,_ in isole],[1])
        self.assertEqual(sorted(map(len,sol['routes'])),[1,3])
