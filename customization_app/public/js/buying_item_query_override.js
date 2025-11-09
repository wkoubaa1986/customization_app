frappe.ui.form.on(
  ['Purchase Order', 'Purchase Invoice', 'Purchase Receipt', 'Supplier Quotation', 'Request for Quotation'],
  {
    setup(frm) {
      override_item_query(frm);
    },
    
    onload(frm) {
      override_item_query(frm);
    },
    
    refresh(frm) {
      override_item_query(frm);
    },
    
    supplier(frm) {
      override_item_query(frm);
    }
  }
);

function override_item_query(frm) {
  if (!frm.fields_dict.items) return;

  console.log("🔧 Custom buying item query applied - Templates allowed");

  // Force the grid to use your query, even if ERPNext tries to override it
  frm.fields_dict.items.grid.get_field('item_code').get_query = function(doc, cdt, cdn) {
    let filters = {
      is_purchase_item: 1
    };
    if (doc.supplier) {
      filters.supplier = doc.supplier;
    }
    // Add other filters as needed
    return {
      query: "customization_app.query.buying_item_query",
      filters: filters
    };
  };

  // Also set_query for safety
  frm.set_query('item_code', 'items', function(doc, cdt, cdn) {
    let filters = {
      is_purchase_item: 1
    };
    if (doc.supplier) {
      filters.supplier = doc.supplier;
    }
    return {
      query: "customization_app.query.buying_item_query",
      filters: filters
    };
  });

  if (frm.fields_dict.items.grid) {
    frm.fields_dict.items.grid.refresh();
  }
}