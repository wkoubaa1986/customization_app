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
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
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

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

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
                  "/assets/customization_app/js/custom_calendar.js"]
# doctype_calendar_js = {
#     "Tache de travail": "/assets/customization_app/js/custom_calendar.js"
# }

override_doctype_class = {
	"Customer": "customization_app.customization.SynchroCustomer",
    "Item": "customization_app.customization.CustomItem",
    "Stock Ledger Entry": "customization_app.customization.CustomStockLedgerEntry"
}

override_doctype_dashboards = {
    "Customer": "customization_app.api.get_data"
}
# override_whitelisted_methods = {
#     "frappe.desk.calendar.get_events": "customization_app.api.get_custom_tache_events"
# }
# doctype_js = {
#     "Tache de travail": "assets/customization_app/js/custom_calendar.js"
# }
# doctype_js = {
#     "Customer": "public/js/customer_quick_entry.js"
# }