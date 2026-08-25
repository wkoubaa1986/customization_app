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
    "Mes Interventions Employe": {
        "before_submit": "customization_app.api.before_submit_mes_interventions",
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
        ],
        "after_delete": "customization_app.commande_alertes.on_tache_change",
    },
    "Delivery Note": {
        # Après update_prevdoc_status d'ERPNext : la commande passe à 100 %
        # livré si ses BL validés couvrent son TTC, ce que le calcul standard
        # sur les quantités rate en cas d'échange d'article.
        "on_submit": [
            "customization_app.per_delivered_montant.on_delivery_note_change",
            "customization_app.commande_alertes.on_delivery_note_change",
        ],
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
    },
    # Les motifs « sans tâche » regardent OÙ sont parqués les paiements liés (19/08/2026) :
    # un encaissement de dette doit requalifier la commande tout de suite, pas à 04h00.
    "Payment Entry": {
        "on_submit": "customization_app.commande_alertes.on_payment_entry_change",
        "on_cancel": "customization_app.commande_alertes.on_payment_entry_change",
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
                  _js("custom_calendar.js"),
                  _js("mes_interventions_employe.js"),
                  _js("pos_auto_customer.js"),
                  _js("buying_item_query_override.js"),
                  _js("calendrier_rdv_button.js"),
                  _js("sales_order_avoir.js"),
                  # Coloration des anomalies dans la liste des commandes.
                  # Volontairement en app_include_js et non en doctype_list_js :
                  # woocommerce_fusion réassigne listview_settings["Sales Order"]
                  # et son fichier est concaténé après le nôtre.
                  _js("sales_order_list_alertes.js"),
                  # Boutons de suivi des appels sur les commandes WEB.
                  _js("sales_order_appels.js"),
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
                  _js("sales_order_tache_details.js")]
# Hide filter message shown in the awesomplete dropdown
app_include_css = ["/assets/customization_app/css/hide_filter_message.css"]
# doctype_calendar_js = {
#     "Tache de travail": "/assets/customization_app/js/custom_calendar.js"
# }


override_doctype_class = {
	"Customer": "customization_app.customization.SynchroCustomer",
    "Item": "customization_app.customization.CustomItem",
    "Stock Ledger Entry": "customization_app.customization.CustomStockLedgerEntry",
    "Item Price": "customization_app.customization.ItemPrice"
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
            ["name", "in", ["Selling", "Accounting", "Partenaire", "Analyse des Articles"]],
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

# after_migrate = ["customization_app.patches.override_get_item_details.execute"]

# Facture d'achat : bouton « 📦 Rattacher des BL » (bons de livraison capturés
# en caisse, en attente de leur facture — voir caisse_depenses.bls_en_attente).
doctype_js = {
    "Purchase Invoice": "public/js/purchase_invoice_caisse.js",
}

# doctype_js = {
#     "Tache de travail": "assets/customization_app/js/custom_calendar.js"
# }
# doctype_js = {
#     "Customer": "public/js/customer_quick_entry.js"
# }

# Item : verrou sync WooCommerce sans image + popup saisie groupée des prix de vente
doctype_js = {
    "Item": "public/js/item.js",
}

# Item (vue liste) : bouton "Vérification base article"
doctype_list_js = {
    "Item": "public/js/item_list.js",
}