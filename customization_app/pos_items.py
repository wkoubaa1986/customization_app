import frappe
from frappe.query_builder import DocType, Order
from frappe.utils import cint
from frappe.utils.nestedset import get_root_of
from erpnext.stock.get_item_details import get_conversion_factor
from erpnext.accounts.doctype.pos_invoice.pos_invoice import get_stock_availability
from erpnext.selling.page.point_of_sale.point_of_sale import (
    get_conditions,
    get_item_group_condition,
    filter_result_items,
    search_by_term,
)


@frappe.whitelist()
def get_items(start, page_length, price_list, item_group, pos_profile, search_term=""):
    """
    Override of erpnext get_items.
    - Stock items with actual_qty > 0 in the POS warehouse → shown
    - Non-stock items (services, bundles) → hidden by default
    - EXCEPT items with show_in_pos_intervention = 1 → always shown
    """
    warehouse, hide_unavailable_items = frappe.db.get_value(
        "POS Profile", pos_profile, ["warehouse", "hide_unavailable_items"]
    )

    result = []

    if search_term:
        result = search_by_term(search_term, warehouse, price_list) or []
        filter_result_items(result, pos_profile)
        if result:
            return {"items": result}

    if not frappe.db.exists("Item Group", item_group):
        item_group = get_root_of("Item Group")

    search_condition = get_conditions(search_term)
    item_group_condition = get_item_group_condition(pos_profile)  # starts with "and "

    lft, rgt = frappe.db.get_value("Item Group", item_group, ["lft", "rgt"])

    bin_join_selection = "LEFT JOIN `tabBin` bin ON bin.item_code = item.name AND bin.warehouse = %(warehouse)s"

    # Whitelisted items bypass item_group and stock restrictions.
    # Non-whitelisted items: must be in item_group tree + item_group_condition + stock > 0.
    items_data = frappe.db.sql(
        """
        SELECT
            item.name AS item_code,
            item.item_name,
            item.description,
            item.stock_uom,
            item.image AS item_image,
            item.is_stock_item,
            item.sales_uom
        FROM
            `tabItem` item {bin_join_selection}
        WHERE
            item.disabled = 0
            AND item.has_variants = 0
            AND item.is_sales_item = 1
            AND item.is_fixed_asset = 0
            AND {search_condition}
            AND (
                item.name IN (
                    SELECT item_code FROM `tabPOS Profile Item Whitelist`
                    WHERE parent = %(pos_profile)s AND parenttype = 'POS Profile'
                )
                OR (
                    item.item_group in (SELECT name FROM `tabItem Group` WHERE lft >= {lft} AND rgt <= {rgt})
                    {item_group_condition}
                    AND item.is_stock_item = 1
                    AND COALESCE(bin.actual_qty, 0) > 0
                )
            )
        ORDER BY
            item.name asc
        LIMIT
            {page_length} offset {start}""".format(
            start=cint(start),
            page_length=cint(page_length),
            lft=cint(lft),
            rgt=cint(rgt),
            search_condition=search_condition,
            item_group_condition=item_group_condition,
            bin_join_selection=bin_join_selection,
        ),
        {"warehouse": warehouse, "pos_profile": pos_profile},
        as_dict=1,
    )

    if not items_data:
        return {"items": result}

    current_date = frappe.utils.today()

    for item in items_data:
        item.actual_qty, _, is_negative_stock_allowed = get_stock_availability(item.item_code, warehouse)

        ItemPrice = DocType("Item Price")
        item_prices = (
            frappe.qb.from_(ItemPrice)
            .select(
                ItemPrice.price_list_rate,
                ItemPrice.currency,
                ItemPrice.uom,
                ItemPrice.batch_no,
                ItemPrice.valid_from,
                ItemPrice.valid_upto,
            )
            .where(ItemPrice.price_list == price_list)
            .where(ItemPrice.item_code == item.item_code)
            .where(ItemPrice.selling == 1)
            .where((ItemPrice.valid_from <= current_date) | (ItemPrice.valid_from.isnull()))
            .where((ItemPrice.valid_upto >= current_date) | (ItemPrice.valid_upto.isnull()))
            .orderby(ItemPrice.valid_from, order=Order.desc)
        ).run(as_dict=True)

        stock_uom_price = next((d for d in item_prices if d.get("uom") == item.stock_uom), {})
        item_uom = item.stock_uom
        item_uom_price = stock_uom_price

        if item.sales_uom and item.sales_uom != item.stock_uom:
            item_uom = item.sales_uom
            sales_uom_price = next((d for d in item_prices if d.get("uom") == item.sales_uom), {})
            if sales_uom_price:
                item_uom_price = sales_uom_price

        if item_prices and not item_uom_price:
            item_uom = item_prices[0].get("uom")
            item_uom_price = item_prices[0]

        item_conversion_factor = get_conversion_factor(item.item_code, item_uom).get("conversion_factor")

        if item.stock_uom != item_uom:
            item.actual_qty = item.actual_qty // item_conversion_factor

        if item_uom_price and item_uom != item_uom_price.get("uom"):
            item_uom_price.price_list_rate = item_uom_price.price_list_rate * item_conversion_factor

        result.append(
            {
                **item,
                "price_list_rate": item_uom_price.get("price_list_rate"),
                "currency": item_uom_price.get("currency"),
                "uom": item_uom,
                "batch_no": item_uom_price.get("batch_no"),
            }
        )

    return {"items": result}
