// frappe.ui.form.on n'accepte qu'un DocType à la fois : passé en tableau, la
// clé devenait « Purchase Order,Purchase Invoice,… » et rien ne s'exécutait
// (la recherche gardait le has_variants=0 d'ERPNext). Corrigé avec v5.114.0.
['Purchase Order', 'Purchase Invoice', 'Purchase Receipt', 'Supplier Quotation', 'Request for Quotation'].forEach(
  (doctype) => {
    frappe.ui.form.on(doctype, {
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
      },
    });
  }
);

function override_item_query(frm) {
  if (!frm.fields_dict.items || !frm.fields_dict.items.grid) return;

  const champ = frm.fields_dict.items.grid.get_field('item_code');
  if (champ.__customization_app_fige) return;

  // ERPNext pose sa requête dans onload (buying.js setup_queries), que Frappe
  // déclenche sans l'attendre : une simple affectation retombait après notre
  // refresh. On fige la propriété ; la fonction d'ERPNext est conservée pour
  // le seul cas où elle diffère vraiment, la sous-traitance.
  let requete_erpnext = champ.get_query;
  const query = function (doc, cdt, cdn) {
    if (doc && doc.is_subcontracted && requete_erpnext) {
      return requete_erpnext(doc, cdt, cdn);
    }
    const filters = { is_purchase_item: 1 };
    if (doc && doc.supplier) {
      filters.supplier = doc.supplier;
    }
    return {
      query: 'customization_app.query.buying_item_query',
      filters: filters,
    };
  };
  Object.defineProperty(champ, 'get_query', {
    get: () => query,
    set: (fn) => {
      requete_erpnext = fn;
    },
    configurable: true,
  });
  champ.__customization_app_fige = true;
}
