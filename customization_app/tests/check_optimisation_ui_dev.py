"""Régression Chromium du dialogue d’optimisation sur DEV.

Depuis frappe-bench :
  env/bin/python apps/customization_app/customization_app/tests/check_optimisation_ui_dev.py

Crée puis détruit une session temporaire. Les endpoints d’optimisation et de
localisation sont simulés ; aucun planning ni aucune adresse client n’est écrit.
Les connexions externes du navigateur sont bloquées.
"""
import json, copy
from urllib.parse import urlsplit,parse_qs
import frappe
from frappe.sessions import Session,delete_session
from playwright.sync_api import sync_playwright,expect

frappe.init(site='mysite.localhost',sites_path='/workspace/development/frappe-bench/sites');frappe.connect()
assert frappe.conf.developer_mode, 'Essai réservé au site DEV'
frappe.set_user('Administrator')
sid=frappe.generate_hash(length=56); now=frappe.utils.now()
sess=Session.__new__(Session);sess.sid=sid
sess.data=frappe._dict(sid=sid,user='Administrator',data=frappe._dict(user='Administrator',last_updated=now,creation=now,session_expiry='01:00:00',session_ip='127.0.0.1',user_type='System User',full_name='Administrator',csrf_token=frappe.generate_hash()))
sess.insert_session_record();frappe.db.commit()
state={'phase':'success'}; calls=[]; pending=[]; errors=[]
stop={'tache':'T-UI-1','client':'Client essai','type':'Entretien','debut':'10:00','fin':'10:30','decale':True,'deplace':False,'ancien_debut':'09:00','ecart_min':60,'employe':'EMP-UI','starts_on':'2026-11-25 10:00:00','ends_on':'2026-11-25 10:30:00','lat':36.81,'lng':10.18,'position':'Google Maps estimé'}
success={'date':'2026-11-25','employes':[{'employe':'EMP-UI','nom':'Employé essai','depart':'Magasin','depart_point':[36.87,10.19],'journee':['09:00','18:00'],'avant':{'arrets':[stop],'km':10,'min':30},'apres':{'arrets':[stop],'km':8,'min':20,'fin':'11:00'}}],'deplacees':0,'decalees':1,'total':{'avant_min':30,'apres_min':20,'avant_km':10,'apres_km':8},'depot':[36.87,10.19],'journee':['09:00','18:00'],'premiere':'09:00','marge':5,'rangement':5,'fenetre':120,'depassement':60,'source':'osrm','coef':1,'non_places':[],'avertissements':[],'recalage':{'source':'osrm','raison':'Essai interface'},'notifications':[]}

e=success['employes'][0]
e.update(technicien=False,equilibrage_charge=False)
e['avant'].update(minutes=30,charge={'interventions':30,'trajets':30,'stationnement':10,'rangement':5,'total':75})
e['apres'].update(minutes=20,charge={'interventions':30,'trajets':20,'stationnement':10,'rangement':5,'total':65})

success['positions_estimees']=[{'tache':'T-UI-1','client':'Client essai <script>window.test_injection=true</script>','source':'Google Maps estimé'}]

def fulfill(route,data,status=200):route.fulfill(status=status,content_type='application/json',body=json.dumps({'message':data}))
try:
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
        ctx=browser.new_context(viewport={'width':1440,'height':900})
        ctx.add_cookies([{'name':'sid','value':sid,'url':'http://mysite.localhost:8000','httpOnly':True}])
        def intercept(route):
            req=route.request;url=req.url
            if urlsplit(url).hostname not in ('mysite.localhost','localhost','127.0.0.1'):route.abort();return
            if '/api/method/customization_app.tournee_optimisation.' in url or '/api/method/customization_app.geocodage_adresses.' in url:
                action=url.split('.')[-1].split('?')[0]; args=parse_qs(req.post_data or urlsplit(url).query);calls.append((action,args))
                if action=='reglages_calcul':fulfill(route,{'fenetre':120,'depassement':60})
                elif action=='employes_du_jour':fulfill(route,[{'employe':'EMP-UI','nom':'Employé essai','taches':7,'mobiles':7,'debut':'09:00','fin':'18:00'}])
                elif action=='proposer':
                    if state['phase']=='late':pending.append(route);return
                    if state['phase']=='error':route.fulfill(status=500,content_type='application/json',body='{}');return
                    data=copy.deepcopy(success);data['date']=args['date'][0];data['fenetre']=int(args['fenetre'][0]);fulfill(route,data)
                elif action=='appliquer':
                    assert args['fenetre']==['60'],args
                    assert args['prevenir']==['0'],args
                    fulfill(route,{'modifiees':[],'prevenus':0})  # Aucune écriture réelle.
                else:raise AssertionError('Appel non autorisé dans cet essai : '+action)
                return
            route.continue_()
        ctx.route('**/*',intercept)
        page=ctx.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto('http://mysite.localhost:8000/app/tache-de-travail/view/calendar/Calendrier%20de%20travail',wait_until='domcontentloaded')
        page.locator('#btn-optimiser-tournees').wait_for(timeout=60000);page.locator('#btn-optimiser-tournees').click()
        opt=page.locator('.opt-dialogue:visible');expect(opt.locator('[data-emp]')).to_have_count(1)
        page.wait_for_function("frappe.ui.open_dialogs.some(d=>d.$wrapper.hasClass('opt-dialogue'))")
        page.evaluate("()=>{window.optTest=frappe.ui.open_dialogs.find(d=>d.$wrapper.hasClass('opt-dialogue'));}")
        def date(d):
            page.evaluate("async d=>{await optTest.set_value('date',d);optTest.fields_dict.date.$input.trigger('change');}",d)
            expect(opt.locator('[data-emp]')).to_have_count(1)
            expect(opt.locator('.modal-footer .btn-primary')).to_be_enabled()
        date('2026-11-25')
        opt.locator('.modal-footer .btn-primary').click()
        expect(opt.locator('[data-opt-estimations]')).to_contain_text('Calcul automatique terminé')
        expect(opt.locator('[data-opt-estimations]')).to_contain_text('1 position(s) estimée(s)')
        assert opt.locator('[data-opt-localisation],.opt-position,[data-verifier-position]').count()==0
        assert not page.evaluate('window.test_injection || false')
        expect(opt.locator('[data-opt-mode]')).to_contain_text('Parcours individuel')
        expect(opt.locator('[data-opt-charge]')).to_contain_text('Charge de travail : 1 h 15 → 1 h 05')
        expect(opt.locator('[data-opt-charge]')).to_contain_text('stationnement')
        bounds=opt.locator('.modal-content').bounding_box();assert bounds['y']>=0 and bounds['y']+bounds['height']<=902,bounds
        opt.locator('.modal-content').screenshot(path='/tmp/optimisation-ui-auto.png')
        expect(opt.get_by_role('button',name='✅ Appliquer (1 tâche(s))')).to_be_visible()
        opt.locator('input[data-fieldname="fenetre"]').fill('60');opt.locator('input[data-fieldname="fenetre"]').press('Tab')
        expect(opt.locator('[data-opt-attente]')).to_be_visible();assert opt.locator('.modal-footer .btn-modal-secondary:visible').count()==0
        state['phase']='late';opt.locator('.modal-footer .btn-primary').click()
        page.wait_for_timeout(200);assert len(pending)==1
        page.evaluate("async()=>{await optTest.set_value('date','2026-11-26');optTest.fields_dict.date.$input.trigger('change');}")
        page.wait_for_timeout(200);fulfill(pending.pop(),success)
        expect(opt.locator('.modal-footer .btn-primary')).to_be_enabled();expect(opt.locator('[data-opt-attente]')).to_contain_text('Nouvelle date')
        assert opt.locator('.modal-footer .btn-modal-secondary:visible').count()==0
        state['phase']='error';opt.locator('.modal-footer .btn-primary').click();expect(opt.locator('.alert-danger')).to_contain_text('Le calcul n’a pas abouti')
        expect(opt.locator('.modal-footer .btn-primary')).to_be_enabled()
        # Fermer une éventuelle notification Frappe, puis vérifier l'étroitesse de l'écran.
        page.evaluate("frappe.ui.open_dialogs.filter(d=>d!==optTest).forEach(d=>d.hide())")
        state['phase']='success';opt.locator('.modal-footer .btn-primary').click();expect(opt.locator('[data-opt-estimations]')).to_be_visible()
        page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(200)
        body=opt.locator('.modal-body');assert body.evaluate('(el)=>el.scrollWidth<=el.clientWidth+2')
        bounds=opt.locator('.modal-content').bounding_box();assert bounds['y']>=0 and bounds['y']+bounds['height']<=846,bounds
        opt.locator('.modal-content').screenshot(path='/tmp/optimisation-ui-mobile.png')
        # Une date effacée ne permet plus de lancer un calcul.
        page.evaluate("async()=>{await optTest.set_value('date','');optTest.fields_dict.date.$input.trigger('change');}")
        expect(opt.locator('.modal-footer .btn-primary')).to_be_disabled()
        expect(opt.locator('.modal-footer .btn-primary')).to_contain_text('Choisir une date')
        assert not errors,errors
        date('2026-11-25')
        opt.locator('.modal-footer .btn-primary').click()
        expect(opt.locator('.modal-body')).to_contain_text('déplacement maximal ± 60 min (strict)')
        opt.get_by_role('button',name='✅ Appliquer (1 tâche(s))').click()
        confirmation=page.locator('.modal:visible').filter(has_text='Appliquer la proposition :')
        confirmation.locator('.modal-footer .btn-primary').click()
        expect(page.locator('.opt-dialogue:visible')).to_have_count(0)
        assert sum(a=='appliquer' for a,_ in calls)==1
        assert not errors,errors
        print(json.dumps({'interface':'OK','calcul_direct_avec_estimations':True,'aucune_confirmation_position':True,'proposition_invalidee':True,'reponse_perimee_ignoree':True,'mobile_sans_debordement':True,'erreurs_js':len(errors),'charge_et_mode_affiches':True,'limite_transmise_a_application':True,'aucun_planning_applique':True},ensure_ascii=False))
        ctx.close();browser.close()
finally:
    delete_session(sid=sid,reason='Fin essai UI optimisation');frappe.destroy()
