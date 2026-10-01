import frappe
import json
from frappe import scrub
from frappe.desk.reportview import get_filters_cond, get_match_cond
from frappe.utils import nowdate


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def buying_item_query(doctype, txt, searchfield, start, page_len, filters, as_dict=False):
    """
    Query personnalisée pour les items dans les documents d'achat (Purchase Order, Purchase Invoice, etc.)
    Permet de sélectionner des templates (has_variants = 1) contrairement à la query standard ERPNext.
    
    Basée sur erpnext.controllers.queries.item_query mais SANS le filtre has_variants=0
    """
    return _item_query_avec_modeles(
        txt, searchfield, start, page_len, filters, as_dict,
        base_cond="and tabItem.is_purchase_item=1",
    )


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def stock_item_query(doctype, txt, searchfield, start, page_len, filters, as_dict=False):
    """Articles proposés dans l'Écriture de stock et le Rapprochement de stock.

    ERPNext écarte tout article modèle (has_variants = 1) de sa recherche, même
    quand « Conserver le stock » est coché. Or certains modèles (AP-M, AP-C, SP-M…)
    sont bien des unités physiques en magasin : leurs variantes ne sont que des
    bundles de vente. Ici le seul critère est donc : le stock est suivi.
    """
    return _item_query_avec_modeles(
        txt, searchfield, start, page_len, filters, as_dict,
        base_cond="and tabItem.is_stock_item=1",
    )


def _item_query_avec_modeles(txt, searchfield, start, page_len, filters, as_dict, base_cond):
    """Recherche d'article sans le filtre has_variants=0 d'ERPNext.

    ``base_cond`` : condition SQL propre au contexte (achat, stock…).
    """
    doctype = "Item"
    conditions = []

    if isinstance(filters, str):
        filters = json.loads(filters)

    # Get searchfields from meta
    meta = frappe.get_meta(doctype, cached=True)
    searchfields = meta.get_search_fields()

    columns = ""
    extra_searchfields = [field for field in searchfields if field not in ["name", "description"]]

    if extra_searchfields:
        columns += ", " + ", ".join(extra_searchfields)

    if "description" in searchfields:
        columns += """, if(length(tabItem.description) > 40, 
            concat(substr(tabItem.description, 1, 40), "..."), description) as description"""

    searchfields = searchfields + [
        field
        for field in [searchfield or "name", "item_code", "item_group", "item_name"]
        if field not in searchfields
    ]
    searchfields = " or ".join([field + " like %(txt)s" for field in searchfields])

    # Handle customer/supplier specific items
    if filters and isinstance(filters, dict):
        if filters.get("customer") or filters.get("supplier"):
            party = filters.get("customer") or filters.get("supplier")
            item_rules_list = frappe.get_all(
                "Party Specific Item",
                filters={"party": party},
                fields=["restrict_based_on", "based_on_value"],
            )

            filters_dict = {}
            for rule in item_rules_list:
                if rule["restrict_based_on"] == "Item":
                    rule["restrict_based_on"] = "name"
                filters_dict[rule.restrict_based_on] = []

            for rule in item_rules_list:
                filters_dict[rule.restrict_based_on].append(rule.based_on_value)

            for filter in filters_dict:
                filters[scrub(filter)] = ["in", filters_dict[filter]]

            if filters.get("customer"):
                del filters["customer"]
            else:
                del filters["supplier"]
        else:
            filters.pop("customer", None)
            filters.pop("supplier", None)

    description_cond = ""
    if frappe.db.count(doctype, cache=True) < 50000:
        description_cond = "or tabItem.description LIKE %(txt)s"

    # MODIFICATION PRINCIPALE : Ne pas considérer has_variants ici.
    # Seul ``base_cond`` (achat ou stock) restreint les articles retournés.
    return frappe.db.sql(
        """select
            tabItem.name {columns}
        from tabItem
        where tabItem.docstatus < 2
            and tabItem.disabled=0
            {base_cond}
            and (tabItem.end_of_life > %(today)s or ifnull(tabItem.end_of_life, '0000-00-00')='0000-00-00')
            and ({scond} or tabItem.item_code IN (select parent from `tabItem Barcode` where barcode LIKE %(txt)s)
                {description_cond})
            {fcond} {mcond}
        order by
            -- exact item_code match first
            (item_code = %(_txt)s) desc,
            -- then items where txt appears in item_code
            (locate(%(_txt)s, item_code) > 0) desc,
            -- prefer shorter item_code when txt is present
            length(item_code) asc,
            -- then position of the search text inside item_code
            if(locate(%(_txt)s, item_code), locate(%(_txt)s, item_code), 99999),
            -- keep the original name-based tie-breakers after
            if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999),
            idx desc,
            item_code, name, item_name
        limit %(start)s, %(page_len)s """.format(
            columns=columns,
            base_cond=base_cond,
            scond=searchfields,
            fcond=get_filters_cond(doctype, filters, conditions).replace("%", "%%"),
            mcond=get_match_cond(doctype).replace("%", "%%"),
            description_cond=description_cond,
        ),
        {
            "today": nowdate(),
            "txt": "%%%s%%" % txt,
            "_txt": txt.replace("%", ""),
            "_prefix": txt.replace("%", "") + '%',
            "start": start,
            "page_len": page_len,
        },
        as_dict=as_dict,
    )