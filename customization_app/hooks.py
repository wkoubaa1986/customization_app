import os as _os

app_name = "customization_app"
app_title = "Customize erpnext"


def _js(nom):
    """
    Chemin d'un JS du Desk suffixé de la date de modification du fichier.

    Frappe ne versionne que les fichiers `.bundle.js` (via assets.json) ; les
    chemins simples déclarés dans app_include_js sont servis tels quels, avec
    un Cache-Control de 12 h. Sans ce suffixe, toute correction JS reste
    invisible pour les navigateurs jusqu'à expiration du cache — y compris
    après un déploiement en production.
    """
    chemin = f"/assets/customization_app/js/{nom}"
    try:
        mtime = int(_os.path.getmtime(_os.path.join(_os.path.dirname(__file__), "public", "js", nom)))
    except OSError:
        return chemin
    return f"{chemin}?v={mtime}"
app_publisher = "Wassim"
app_description = "This app aloow to change same functionalities in erpnext"
app_email = "koubaawassim@gmail.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "customization_app",
# 		"logo": "/assets/customization_app/logo.png",
# 		"title": "Customize erpnext",
# 		"route": "/customization_app",
# 		"has_permission": "customization_app.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/customization_app/css/customization_app.css"
# app_include_js = "/assets/customization_app/js/customization_app.js"

# include js, css files in header of web template
# web_include_css = "/assets/customization_app/css/customization_app.css"
# web_include_js = "/assets/customization_app/js/customization_app.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "customization_app/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# pos_auto_customer is now loaded via app_include_js above
# page_js = {"point-of-sale": "public/js/pos_auto_customer.js"}

# include js in doctype views
# doctype_js does not work for Custom DocTypes stored in DB — use app_include_js instead
# app_include_js = [

# ]
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "customization_app/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "customization_app.utils.jinja_methods",
# 	"filters": "customization_app.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "customization_app.install.before_install"
# after_install = "customization_app.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "customization_app.uninstall.before_uninstall"
# after_uninstall = "customization_app.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "customization_app.utils.before_app_install"
# after_app_install = "customization_app.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "customization_app.utils.before_app_uninstall"
# after_app_uninstall = "customization_app.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "customization_app.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Suivi d'activité : chaque employé ne voit QUE ses activités (liste, rapports, API, pièces
# jointes), les responsables tout. ⚠️ Une seule affectation de chaque dict dans ce fichier.
permission_query_conditions = {
    "Activite Employe": "customization_app.suivi_activite.permission_query_conditions",
}
has_permission = {
    "Activite Employe": "customization_app.suivi_activite.has_permission",
}

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
    # Congés & récupérations : chaque jour d'absence approuvé = une Tâche de travail « toute la
    # journée » dans le calendrier (remplace le Server Script « Generer » sur Attendance), retirée
    # à l'annulation. Voir conges_recuperations.py.
    "Leave Application": {
        "on_submit": "customization_app.conges_recuperations.leave_application_on_submit",
        "on_cancel": "customization_app.conges_recuperations.leave_application_on_cancel",
    },
    "Mes Interventions Employe": {
        "before_submit": "customization_app.api.before_submit_mes_interventions",
    },
    # Stock par entrepôt : un transfert en attente de la confirmation d'un employé ne se soumet que par
    # la page (valider_transfert) — jamais depuis le formulaire Stock Entry (MAT-STE-2026-00104, 02/10/2026).
    "Stock Entry": {
        "before_submit": "customization_app.stock_entrepots.stock_entry_before_submit",
    },
    # Le stock d'un employé ne se rapproche que par une vérification (page) : jamais depuis le formulaire.
    "Stock Reconciliation": {
        "before_submit": "customization_app.stock_entrepots.stock_reconciliation_before_submit",
    },
    # Caisse journalière, double validation : une clôture (comptée par l'employé) ne se soumet que par la
    # collecte du responsable (caisse_collecte), une passation que par sa réception — jamais depuis le formulaire.
    "Cloture Caisse": {
        "before_submit": "customization_app.caisse_collecte.cloture_caisse_before_submit",
    },
    "Passation Caisse": {
        "before_submit": "customization_app.caisse_collecte.passation_caisse_before_submit",
    },
    # La file « Facture Achat a Saisir » (captures de la caisse) se rattache toute
    # seule aux vraies factures d'achat : appariement (fournisseur, n°), copie du
    # justificatif scanné, statut « Saisie » à la soumission.
    "Purchase Invoice": {
        # on_update, pas validate : au validate la facture n'existe pas encore et
        # une insertion qui échoue ensuite laisserait un lien vers un fantôme.
        "on_update": "customization_app.caisse_depenses.pi_lier_fiche_caisse",
        "on_submit": "customization_app.caisse_depenses.pi_marquer_fiche_saisie",
        "on_cancel": "customization_app.caisse_depenses.pi_rouvrir_fiche",
    },
    # BL de caisse -> reçu d'achat : le reçu créé depuis une fiche BL
    # (custom_fiche_caisse) se lie à sa fiche et reçoit le justificatif.
    "Purchase Receipt": {
        "on_update": "customization_app.caisse_depenses.pr_lier_fiche_caisse",
        "on_submit": "customization_app.caisse_depenses.pr_lier_fiche_caisse",
        "on_cancel": "customization_app.caisse_depenses.pr_detacher_fiche_caisse",
    },
    # BL de caisse -> COMMANDE d'achat : à la soumission, l'avance de caisse
    # devient un paiement lié à la commande (avance fournisseur native) ; la
    # facture se crée ensuite depuis une ou plusieurs commandes.
    "Purchase Order": {
        "on_update": "customization_app.caisse_depenses.po_lier_fiche_caisse",
        "on_submit": "customization_app.caisse_depenses.po_convertir_avances",
        "on_cancel": "customization_app.caisse_depenses.po_detacher_fiche_caisse",
    },
    # Zones magasin : la table « Zones magasin » de la fiche réécrit « Emplacement Magasin ».
    "Item": {
        "validate": "customization_app.zones_magasin.item_validate",
    },
    "Tache de travail": {
        "before_save": "customization_app.api.before_save_tache_de_travail",
        # ATTENTION : « after_save » n'est pas un événement Frappe — le
        # framework ne l'appelle jamais. Ce hook est donc inactif depuis sa
        # mise en place. Laissé en l'état : l'activer ferait tourner pour la
        # première fois un code qui écrit dans tabIntervention.
        "after_save": "customization_app.api.after_save_tache_de_travail",
        # on_update couvre la création et la modification. after_delete, et non
        # on_trash, car on_trash se déclenche AVANT la suppression de la ligne :
        # le recalcul verrait encore la tâche.
        "on_update": [
            # L'alignement d'abord : il peut faire passer la commande à 100 %
            # livré, ce que le calcul d'anomalie doit voir.
            "customization_app.per_delivered_montant.on_tache_change",
            "customization_app.commande_alertes.on_tache_change",
            # Atelier osmoseurs : la clôture de la dernière tâche liée valide le dossier.
            "customization_app.reparation_osmoseur.on_tache_change",
        ],
        "after_delete": [
            "customization_app.commande_alertes.on_tache_change",
            "customization_app.reparation_osmoseur.on_tache_change",
        ],
    },
    "Delivery Note": {
        # Échange « E-… » : la pièce remise sort par sa propre ligne à 0 DT,
        # posée avant la validation ERPNext (qui la complète) …
        "before_validate": "customization_app.retour_echange.poser_composants",
        # … et remise à 0 DT après ; les packed items du bundle d'échange
        # (pièce reprise à −1, pièce remise) sont retirés : le stock ne doit
        # bouger qu'une fois, par la ligne.
        "validate": "customization_app.retour_echange.retirer_pieces_reprises",
        # Après update_prevdoc_status d'ERPNext : la commande passe à 100 %
        # livré si ses BL validés couvrent son TTC, ce que le calcul standard
        # sur les quantités rate en cas d'échange d'article.
        "on_submit": [
            "customization_app.per_delivered_montant.on_delivery_note_change",
            "customization_app.commande_alertes.on_delivery_note_change",
            # Un BL d'échange (bundle « E-… » à ligne négative) fait rentrer la
            # pièce reprise par un BL retour créé et validé ici même.
            "customization_app.retour_echange.on_submit_bl",
        ],
        # Annuler l'échange annule sa reprise ; la reprise ne s'annule pas seule.
        "before_cancel": "customization_app.retour_echange.before_cancel_bl",
        "on_cancel": [
            "customization_app.api.on_delivery_note_cancel",
            "customization_app.per_delivered_montant.on_delivery_note_change",
            "customization_app.commande_alertes.on_delivery_note_change",
        ],
        "after_cancel": "customization_app.api.on_delivery_note_cancel",
    },
    "Sales Order": {
        # on_update se déclenche à l'enregistrement d'un brouillon ET à la
        # validation : ajouter une ligne de main d'œuvre à un devis non validé
        # met donc aussitôt l'anomalie à jour. on_submit seul l'aurait raté.
        "on_update": "customization_app.commande_alertes.on_sales_order_change",
        "on_update_after_submit": "customization_app.commande_alertes.on_sales_order_change",
        # Cascade AVANT le Server Script « cancel sales order payment » (les hooks
        # Python passent d'abord) : BL annulés PUIS SUPPRIMÉS (magasin désactivé
        # réactivé le temps du reposting, stock repris transféré au magasin par
        # défaut), échéanciers supprimés, lignes de calendrier remises en attente.
        "before_cancel": "customization_app.annulation_commande.before_cancel_sales_order",
        # Une commande ANNULÉE se supprime même encore liée ailleurs : les
        # références bloquantes sont retirées avant le contrôle des liens.
        "on_trash": "customization_app.annulation_commande.on_trash_sales_order",
        "on_cancel": [
            "customization_app.api.on_sales_order_cancel",
            "customization_app.commande_alertes.on_sales_order_change",
            # SMS d'annulation aux numéros du client, commandes WEB uniquement.
            # Mis en file d'attente : la passerelle attend jusqu'à 15 s par
            # numéro, l'annulation ne doit pas patienter.
            "customization_app.sms_annulation.on_sales_order_cancel",
        ],
    },
    # Numérotation auto de la facture (remplace le Server Script « Generation N Facture »).
    "Sales Invoice": {
        "before_insert": "customization_app.facturation_numbering.set_numero_facture",
        # Lignes composants d'un échange : marque reprise du BL avant le
        # contrôle « Commande client requise » (SalesInvoiceEchange).
        "before_validate": "customization_app.retour_echange.marquer_composants_facture",
        "validate": [
            "customization_app.ristourne_facture.apply_order_discounts",
            # La facture reprend les lignes du BL d'échange : packed items du
            # bundle retirés, lignes composants remises à 0 DT comme sur le BL.
            "customization_app.retour_echange.retirer_pieces_reprises",
        ],
        # Annuler une facture rend le paiement à la ou aux commandes qui l'ont
        # générée, au prorata de leurs lignes. Le plan se calcule AVANT
        # l'annulation (les affectations existent encore) et s'applique APRÈS
        # (ERPNext a détaché la facture). Remplace les Server Scripts « cancel
        # Invoice order Payment » et « traitement paiement après annulation
        # facture », qui ignoraient les commandes WEB1 et supprimaient le
        # paiement au lieu de l'amender.
        "before_cancel": "customization_app.annulation_facture.before_cancel_sales_invoice",
        "on_cancel": "customization_app.annulation_facture.on_cancel_sales_invoice",
    },
    # Les motifs « sans tâche » regardent OÙ sont parqués les paiements liés (19/08/2026) :
    # un encaissement de dette doit requalifier la commande tout de suite, pas à 04h00.
    "Payment Entry": {
        # Lignes de référence en double (une par commande, converties vers la même facture par ERPNext) :
        # fusionnées sur un brouillon/amendement, sinon « Duplicate entry in References » (01/10/2026).
        "before_validate": "customization_app.paiement_references.before_validate",
        "on_submit": "customization_app.commande_alertes.on_payment_entry_change",
        "on_cancel": "customization_app.commande_alertes.on_payment_entry_change",
    },
    # Partage avec le partenaire à la création, retrait à la validation — avec
    # ignore_share_permission, ce que les deux Server Scripts remplacés (éteints
    # par patch) ne pouvaient pas faire : un Maintenance Manager ne validait plus.
    "Liste Appelle Entretien": {
        # Le partage automatique avec le compte partenaire (after_insert → partage_partenaire.partager) est RETIRÉ le
        # 03/10/2026 : le partenaire n'appelle pas nos clients, il exécute les tâches qu'on lui affecte. Le retrait à la
        # validation reste, pour nettoyer les partages des listes encore ouvertes.
        "on_submit": "customization_app.partage_partenaire.retirer_partages",
        # Lignes « a été appelé » → cases du client + visites marquées appelées (remplace le Server Script « update donnee appelle »).
        "on_update": "customization_app.liste_appels.synchroniser_appels",
    },
    # Une fiche client créée par le compte partenaire est « gérée par le partenaire » : exclue de nos relances.
    "Customer": {
        "before_insert": "customization_app.partenaire_clients.customer_before_insert",
    },
    # Le dernier commentaire d'une commande est recopié sur la fiche (pastille + filtre de la liste, écran Commandes à traiter).
    "Comment": {
        "after_insert": "customization_app.commentaires_commande.comment_change",
        "on_update": "customization_app.commentaires_commande.comment_change",
        "after_delete": "customization_app.commentaires_commande.comment_change",   # après la suppression (on_trash voit encore la ligne)
    },
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"customization_app.tasks.all"
# 	],
# 	"daily": [
# 		"customization_app.tasks.daily"
# 	],
# 	"hourly": [
# 		"customization_app.tasks.hourly"
# 	],
# 	"weekly": [
# 		"customization_app.tasks.weekly"
# 	],
# 	"monthly": [
# 		"customization_app.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "customization_app.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "customization_app.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "customization_app.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["customization_app.utils.before_request"]
# after_request = ["customization_app.utils.after_request"]

# Job Events
# ----------
# before_job = ["customization_app.utils.before_job"]
# after_job = ["customization_app.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"customization_app.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Load my JS globally in the Desk (ERPNext admin interface)
app_include_js = [_js("customer_quick_entry.js"),
                  _js("delivery_note_echange.js"),
                  _js("caisse_impayes.js"),
                  _js("caisse_bascule_pas_paye.js"),
                  _js("custom_calendar.js"),
                  _js("mes_interventions_employe.js"),
                  _js("pos_auto_customer.js"),
                  _js("buying_item_query_override.js"),
                  # Écriture / Rapprochement de stock : les modèles à variantes
                  # suivis en stock (AP-M…) sont proposés (query.stock_item_query).
                  _js("stock_item_query_override.js"),
                  _js("calendrier_rdv_button.js"),
                  _js("sales_order_avoir.js"),
                  # Livraison partielle : bandeau livré / réglé / dette réelle et bouton
                  # « Régulariser sur le livré » (livraison_partielle.py).
                  _js("sales_order_livraison_partielle.js"),
                  # Liste des commandes : bouton « Livraisons partielles » — toutes
                  # les commandes livrées en partie, dette surévaluée ou non, avec
                  # le même bouton « Régulariser » que la fiche.
                  _js("sales_order_list_livraisons_partielles.js"),
                  _js("tache_liste_groupe.js"),
                  # « 👥 Réunion » : une tâche « Autre » par participant, titre choisi.
                  _js("tache_reunion.js"),
                  # « Ma journée » : la fenêtre où chacun termine ses
                  # interventions du jour, depuis la liste ou le calendrier des
                  # tâches. Une FENÊTRE et non une page — elle sert sur le
                  # téléphone, entre deux interventions.
                  _js("ma_journee.js"),
                  # Stock cible (réglage + fiche véhicule) : articles suivis seulement,
                  # et « Coller une liste » pour saisir des dizaines de lignes en un bloc.
                  _js("stock_cible_liste.js"),
                  # Calendrier des tâches : « 🗺️ Optimiser la journée » (tournee_optimisation.py).
                  _js("optimiser_tournees.js"),
                  # Le dialogue de création d'un bordereau Aramex, partagé par la fiche
                  # commande et « Ma journée » (aramex_expedition / planning_employe).
                  _js("aramex_dialogue.js"),
                  _js("taches_gestion_groupe.js"),
                  _js("tache_cloture_partenaire.js"),
                  # Coloration des anomalies dans la liste des commandes.
                  # Volontairement en app_include_js et non en doctype_list_js :
                  # woocommerce_fusion réassigne listview_settings["Sales Order"]
                  # et son fichier est concaténé après le nôtre.
                  _js("sales_order_list_alertes.js"),
                  # Boutons de suivi des appels sur les commandes WEB.
                  _js("sales_order_appels.js"),
                  # Envoi groupé SMS / e-mail depuis la liste des commandes.
                  _js("sales_order_sms_groupe.js"),
                  # Clôture guidée des tâches : photos obligatoires + code superviseur.
                  _js("tache_photos_cloture.js"),
                  _js("suivi_activite_onglet.js"),  # l'onglet « Suivi d'activité » ouvre l'outil
                  _js("situation_mensuelle_clic.js"),  # graphe Situation Mensuelle → page de détail
                  # 📨 SMS / e-mail au client depuis la fiche tâche, avec modèles prédéfinis
                  # (technicien + téléphone injectés automatiquement, commande liée si présente).
                  _js("tache_sms_email.js"),
                  # Prise de rendez-vous depuis une commande : bouton rouge au bout de la barre
                  # d'onglets de la fiche, et calendrier des tâches depuis la liste.
                  # ⚠️ APRÈS calendrier_rdv_button.js, dont il appelle `rdvLibre_openOverlay`.
                  _js("sales_order_rdv.js"),
                  # Annuler une commande sans le dialogue « Annuler tous les
                  # documents » : la cascade serveur (annulation_commande.py)
                  # gère déjà BL, échéancier, calendrier, paiements.
                  _js("sales_order_annulation.js"),
                  # Bandeau des tâches de travail liées sur la fiche commande :
                  # type d'intervention, employé, statut, durée, date planifiée.
                  _js("sales_order_tache_details.js"),
                  # 📜 Historique client + « 🔧 À proposer » : listes d'appels, rattrapage, commandes à traiter.
                  _js("historique_client.js")]
# Hide filter message shown in the awesomplete dropdown
app_include_css = ["/assets/customization_app/css/hide_filter_message.css"]
# doctype_calendar_js = {
#     "Tache de travail": "/assets/customization_app/js/custom_calendar.js"
# }


override_doctype_class = {
	"Customer": "customization_app.customization.SynchroCustomer",
    "Item": "customization_app.customization.CustomItem",
    "Stock Ledger Entry": "customization_app.customization.CustomStockLedgerEntry",
    "Item Price": "customization_app.customization.ItemPrice",
    # « Commande client requise » levé pour les BL retour d'échange et les
    # lignes composants d'un échange (BL et facture).
    "Delivery Note": "customization_app.retour_echange.DeliveryNoteEchange",
    "Sales Invoice": "customization_app.retour_echange.SalesInvoiceEchange",
}

override_doctype_dashboards = {
    "Customer": "customization_app.api.get_data"
}
app_ready = "customization_app.patches.override_get_item_details.apply"

# Méthodes appelables depuis le Jinja des print formats.
# bl_sous_garantie : utilisée par « Aqua World BL » pour décider d'imprimer la
# mention de garantie. Un helper plutôt que du Jinja inline, pour résoudre la
# descendance des groupes d'articles en une seule requête.
jinja = {"methods": ["customization_app.jinja_methods.bl_sous_garantie"]}

override_whitelisted_methods = {
    "erpnext.stock.get_item_details.get_item_details": "customization_app.get_item_details.get_item_details",
    "erpnext.selling.page.point_of_sale.point_of_sale.get_items": "customization_app.pos_items.get_items",
    # Le Client Script de la Tache de travail appelle « get_customer_booking_info »
    # au chargement de la fiche. Ce nom nu désignait un Server Script API en base,
    # qui plantait sur un rendez-vous incomplet (« can only concatenate str (not
    # "NoneType") »). Frappe consulte ce hook AVANT les Server Scripts, l'appel
    # arrive donc sur la version versionnée et protégée, sans toucher aux fixtures.
    "get_customer_booking_info": "customization_app.api.get_customer_booking_info",
}
fixtures = [
    # Custom Field de ton module
    {
        "doctype": "Custom Field",
        "filters": [
            ["module", "=", "Customize erpnext"],
        ],
    },
    # Property Setter de ton module
    {
        "doctype": "Property Setter",
        "filters": [
            ["module", "=", "Customize erpnext"],
        ],
    },
    # Client Script pour tes doctypes
    {
        "doctype": "Client Script",
        "filters": [
            ["dt", "in", ["Compagne SMS", "Customer", "Liste Appelle Entretien", "Tache de travail"]],
        ],
    },
    # ✅ Server Script
    {
        "doctype": "Server Script",
        "filters": [
            [
                "name",
                "in",
                [
                    "ajuster rendez vous pris par partenaire",
                    "Autorisation Sales order partenaire",
                    "Generation payement",
                    "re-generate payment after sales order",
                    "fill payment schedule row uid",
                    "cancel sales order payment",
                    "Traitement des encaissement",
                    # Régénère `dettes_a_encaisser` en FIFO à l'enregistrement. Absent de cette
                    # liste jusqu'ici : toute correction restait locale et le prochain migrate
                    # la réécrasait. Il décide seul des lignes de dette encaissées — il doit
                    # suivre le même chemin que « Traitement des encaissement », qui les exécute.
                    "generartion_list dette",
                    "generer un echeancier de maintenace",
                    "Facturation Auto",
                    "Generation N Facture",
                    "get customer information",
                ],
            ],
        ],
    },
    {"doctype": "Responsable Relance", "filters": [["name", "=", "Default"]]},
    # NOTE : plus AUCUN DocType en fixtures. L'import de fixtures fait
    # delete+insert avec validation → interdit en prod sans developer_mode
    # (CannotCreateStandardDoctypeError). Les DocTypes standards vivent en
    # fichiers de module (customize_erpnext/doctype/…), synchronisés par
    # migrate sans contrainte de developer_mode.
    {
        "doctype": "Number Card",
        "filters": [
            ["name", "in", ["Solde WINSMS", "Expiration WINSMS (jours)", "Solde Caisse", "Espèce à verser"]],
        ],
    },
    # Workspaces personnalisés — UNE seule entrée : deux entrées Workspace
    # distinctes écriraient toutes deux workspace.json et la seconde écraserait
    # la première (seule la dernière survivait). Le filtre "in" les exporte ensemble.
    {
        "doctype": "Workspace",
        "filters": [
            ["name", "in", ["Selling", "Accounting", "Partenaire", "Analyse des Articles", "Buying", "Relances et partenaire", "Appels"]],
        ],
    },
    {
        "doctype": "Report",
        "filters": [
            ["name", "in", ["Liste Appels Rattrapage", "Rapport Espece"]],
        ],
    },
]
scheduler_events = {
    # Tâche lourde exécutée une fois par jour (heure gérée par Frappe)
    "daily_long": [
        "customization_app.Maintenance.update_schedule.run_cron",
        # Optimisation des tournées : positions des adresses nouvelles (lien Google Maps, sinon texte).
        "customization_app.tournee_optimisation.geocodage_quotidien",
        # Portail /rdv : secteurs voisins recalculés APRÈS le géocodage de la nuit (centres des secteurs à jour).
        "customization_app.portail_rdv_planning.rafraichir_voisins_nuit",
    ],
    "daily": [
        # Jours de récupération par quinzaine : crédite les quinzaines écoulées (Regle Recuperation).
        "customization_app.conges_recuperations.tache_quotidienne",
        # Vérification hebdomadaire des stocks d'employés : fiche + tâches le jour fixé (Config Stock Entrepot).
        "customization_app.stock_entrepots.planifier_verifications",
    ],

    "cron": {
        # Lundi–samedi à 07:00 : création liste d'appels
        "0 7 * * 1-6": [
            "customization_app.Maintenance.creation_liste_appelle.run_cron",
        ],

        # Lundi–samedi à 10:00 : relance SMS maintenance
        "0 10 * * 1-6": [
            "customization_app.Maintenance.relance_maintenance_sms.run_cron",
        ],

        # Tous les soirs à 20:00 : rappel des rendez-vous du lendemain et avis
        # de remise Aramex du jour. Remplace le Server Script « Rappelle Rendez
        # vous », éteint par patch — il tombait 7 soirs sur 30 et, envoyant au
        # fil de la boucle, privait de rappel tous les clients qui suivaient le
        # rendez-vous fautif.
        "0 20 * * *": [
            "customization_app.rappel_rdv.cron_du_soir",
        ],

        # Tous les jours à 07:30 : création/MAJ liste interventions Nizar
        "30 7 * * *": [
            "customization_app.api.tache_journalier_nizar",
        ],

        # Tous les jours à 03:00 : contrôle/réparation des images (articles & groupes)
        "0 3 * * *": [
            "customization_app.Maintenance.image_monitor.run_cron",
        ],

        # Tous les jours à 04:00 : resynchronisation du champ Anomalie des
        # commandes. Filet de sécurité si un événement a été manqué — import en
        # masse, correction directe en base, suppression non hookée.
        "0 4 * * *": [
            "customization_app.commande_alertes.recalculer_tout",
        ],

        # Tous les jours à 16:00 : actualisation du suivi des colis Aramex.
        # En fin de journée, quand les tournées du transporteur sont faites — et jamais sur les
        # colis déjà livrés, dont l'état ne bougera plus.
        "0 16 * * *": [
            "customization_app.livraison_aramex.run_cron",
        ],
    },
}

# ⚠️ APRÈS CHAQUE MIGRATION, et pas seulement une fois par patch.
# `bench migrate` REIMPORTE les espaces de travail depuis les fichiers JSON des
# apps : tout raccourci ajouté en base à un espace d'ERPNext (ici « Selling »)
# est effacé au déploiement suivant. Constaté le 31/08/2026 — le raccourci
# « Commandes à traiter », posé par un patch, avait disparu et sa date de
# modification était retombée à celle du fichier. Un patch ne se rejoue pas :
# il faut donc le reposer à chaque migration. La fonction est idempotente.
after_migrate = [
    # Onglet « Suivi d'activité » + rôles Suivi Activité / Responsable Activité.
    "customization_app.patches.ensure_onglet_suivi_activite.execute",
    # Graphe « Situation Mensuelle » sur 12 mois + raccourci vers la page de détail (onglet Comptabilité).
    "customization_app.patches.ensure_situation_mensuelle.execute",
    "customization_app.patches.ensure_raccourci_commandes_a_traiter.execute",
    "customization_app.patches.ensure_raccourci_reparation_osmoseurs.execute",  # "Selling", après "Commandes à traiter"
    # « Rapport Prime » dans l'onglet Banque (workspace importé par bank_retenue_sync).
    "customization_app.patches.ensure_raccourci_rapport_prime.execute",
    # « Transformation d’articles » dans l'espace Stock, après « Dashboard ».
    "customization_app.patches.ensure_raccourci_transformation_articles.execute",
    # « Zones & sorties d’articles » dans l'espace Stock, après « Transformation d’articles ».
    "customization_app.patches.ensure_raccourci_zones_magasin.execute",
    # « Stock par entrepôt » dans l'espace Stock, après « Zones & sorties d’articles » + premier réglage.
    "customization_app.patches.ensure_stock_entrepots.execute",
    # « Ensembles de produits » dans l'espace Stock, après « Stock par entrepôt ».
    "customization_app.patches.ensure_ensembles_produits.execute",
    # « Congés & récupérations » dans l'onglet HR + type de congé « Récupération (quinzaine) ».
    "customization_app.patches.ensure_conges_recuperations.execute",
    # Onglet « Relances & partenaire » : réservé au rôle « Relances » (attribué par patch restreindre_onglet_relances).
    "customization_app.patches.ensure_onglet_relances.execute",
    # Onglet « Appels » (poste de Salma) : réservé au rôle « Appels » (attribué par patch attribuer_onglet_appels).
    "customization_app.patches.ensure_onglet_appels.execute",
]

# after_migrate = ["customization_app.patches.override_get_item_details.execute"]

# ⚠️ UNE SEULE AFFECTATION DE `doctype_js` DANS TOUT LE FICHIER. Il y en avait deux — l'une
# pour la facture d'achat, l'autre pour l'article — et Python garde la DERNIÈRE : le bouton
# « 📦 Rattacher des BL » n'a jamais été chargé depuis que la seconde existe. Vérifié le
# 04/09/2026 sur le script réellement injecté dans le formulaire : 51 237 caractères, et pas une
# ligne de `purchase_invoice_caisse`. Tout tient donc ici, et les ajouts se font DANS ce
# dictionnaire, jamais dans un second.
doctype_js = {
    # Fiche client : bandeau « géré par le partenaire » + bouton pour (dé)marquer (partenaire_clients).
    "Customer": "public/js/customer_partenaire.js",
    # Facture d'achat : bouton « 📦 Rattacher des BL » (bons de livraison capturés en caisse,
    # en attente de leur facture — voir caisse_depenses.bls_en_attente) ; et le panneau qui
    # montre le scan pendant la saisie.
    "Purchase Invoice": ["public/js/purchase_invoice_caisse.js",
                         "public/js/document_a_saisir.js"],
    # Le scan a été pris en caisse et attaché à la fiche de la file, jamais à la pièce qu'on
    # saisit (demande utilisateur 04/09/2026).
    "Purchase Order": "public/js/document_a_saisir.js",
    "Purchase Receipt": "public/js/document_a_saisir.js",
    # Et sur la fiche de la file elle-meme : c'est la qu'on relit la facture avant de creer la
    # piece, et l'apercu natif de Frappe y est MODAL — il recouvre le formulaire.
    "Facture Achat a Saisir": "public/js/document_a_saisir.js",
    # Item : verrou sync WooCommerce sans image + popup saisie groupée des prix de vente.
    "Item": "public/js/item.js",
    # Commande : boutons « Créer le bordereau Aramex » / étiquette / mise en attente
    # (API Aramex — aramex_expedition.py). woocommerce_fusion a aussi un doctype_js sur
    # Sales Order : Frappe concatène les deux, aucun n'écrase l'autre.
    "Sales Order": "public/js/sales_order_aramex.js",
}

# doctype_js = {
#     "Tache de travail": "assets/customization_app/js/custom_calendar.js"
# }
# doctype_js = {
#     "Customer": "public/js/customer_quick_entry.js"
# }

# Item (vue liste) : bouton "Vérification base article"
doctype_list_js = {
    "Item": "public/js/item_list.js",
}