import json

import frappe
import json
import frappe
from frappe import _


@frappe.whitelist()
def get_custom_tache_events(start, end, filters=None):
    # Define the field mapping for your custom "Tache de travail" doctype
    field_map = frappe._dict({
        "start": "starts_on",     # Field for the start date of the event
        "end": "ends_on",         # Field for the end date of the event
        "title": "titre",         # Title of the event
        "color": "color"          # Color field for the event
    })

    # List of fields to be retrieved from the "Tache de travail" doctype
    fields = [
        field_map.start, field_map.end, field_map.title, "name", field_map.color,
        "custom_choix_du_staff", "custom_employé", "custom_client", "nom_client",
        "status", "toute_la_journée", "custom_reservation_app"
    ]

    # If filters are passed, use them; otherwise, default to an empty list
    filters = json.loads(filters) if filters else []

    # Add conditions to filter events within the date range
    start_date = "ifnull(%s, '0001-01-01 00:00:00')" % field_map.start
    end_date = "ifnull(%s, '2199-12-31 00:00:00')" % field_map.end

    filters += [
        ['Tache de travail', start_date, '<=', end],  # Events should start before or on the end date
        ['Tache de travail', end_date, '>=', start]   # Events should end after or on the start date
    ]

    # Ensure that all necessary fields are unique and avoid any duplicates
    fields = list({field for field in fields if field})
    # print(fields)
    
    # Retrieve events from the "Tache de travail" doctype using the filters and fields
    events = frappe.get_list('Tache de travail', fields=fields, filters=filters)

    # Add the custom logic for checking all-day events
    for event in events:
        # Calculate the duration of the event in hours
        if event.get('starts_on') and event.get('ends_on'):
            start_time = frappe.utils.get_datetime(event['starts_on'])
            end_time = frappe.utils.get_datetime(event['ends_on'])
            duration = (end_time - start_time).total_seconds() / 3600  # Convert duration to hours
            
            # Check if the event duration is greater than 7 hours or if 'toute_la_journée' is checked
            if duration > 7 or event.get('toute_la_journée'):
                event['all_day'] = 1
            else:
                event['all_day'] = 0

    # print(events)
    return events

@frappe.whitelist()
def get_data(data=None):
    print("get_data")
    return {
		"heatmap": True,
		"heatmap_message": _(
			"Ceci est basé sur les commandes client. Voir la chronologie ci-dessous pour plus de détails"
		),
		"fieldname": "customer",
		"non_standard_fieldnames": {
			"Payment Entry": "party",
			"Quotation": "party_name",
			"Opportunity": "party_name",
			"Bank Account": "party",
			"Subscription": "party",
		},
		"dynamic_links": {"party_name": ["Customer", "quotation_to"]},
		"transactions": [
			{"label": _("Pre Sales"), "items": ["Opportunity", "Quotation"]},
			{"label": _("Orders"), "items": ["Sales Order", "Delivery Note", "Sales Invoice"]},
			{"label": _("Payments"), "items": ["Payment Entry", "Bank Account", "Dunning"]},
			{
				"label": _("Support"),
				"items": ["Issue", "Maintenance Visit", "Installation Note", "Warranty Claim"],
			},
			{"label": _("Projects"), "items": ["Project"]},
			{"label": _("Pricing"), "items": ["Pricing Rule"]},
			{"label": _("Subscriptions"), "items": ["Subscription"]},
		],
	}
