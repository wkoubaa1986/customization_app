app_name = "customization_app"
app_title = "Customize erpnext"
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
    "Tache de travail": {
        "before_save": "customization_app.api.before_save_tache_de_travail",
        "after_save":  "customization_app.api.after_save_tache_de_travail",
    },
    "Delivery Note": {
        "on_cancel": "customization_app.api.on_delivery_note_cancel",
        "after_cancel": "customization_app.api.on_delivery_note_cancel",
    },
    "Sales Order": {
        "on_cancel": "customization_app.api.on_sales_order_cancel",
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
app_include_js = ["/assets/customization_app/js/customer_quick_entry.js",
                  "/assets/customization_app/js/custom_calendar.js",
                  "/assets/customization_app/js/mes_interventions_employe.js",
                "/assets/customization_app/js/pos_auto_customer.js",
                  "/assets/customization_app/js/buying_item_query_override.js"]
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
            ["dt", "in", ["Compagne SMS", "Customer"]],
        ],
    },
    # ✅ Server Script
    {
        "doctype": "Server Script",
        "filters": [
            # si tes server scripts sont attachés à un DocType
            ["reference_doctype", "in", ["Compagne SMS"]],
        ],
    },
    {"doctype": "Responsable Relance", "filters": [["name", "=", "Default"]]},
    # Custom DocTypes — Mes Interventions Employe and its child table
    {
        "doctype": "DocType",
        "filters": [
            ["name", "in", ["Mes Interventions Employe", "Intervention"]],
        ],
    },
    {
        "doctype": "Number Card",
        "filters": [
            ["name", "in", ["Solde WINSMS", "Expiration WINSMS (jours)", "Solde Caisse"]],
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
    },
}

# after_migrate = ["customization_app.patches.override_get_item_details.execute"]
# doctype_js = {
#     "Tache de travail": "assets/customization_app/js/custom_calendar.js"
# }
# doctype_js = {
#     "Customer": "public/js/customer_quick_entry.js"
# }