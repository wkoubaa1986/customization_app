// Écriture de stock / Rapprochement de stock : proposer tout article dont le
// stock est suivi, y compris les modèles à variantes (AP-M, AP-C, SP-M…) que la
// recherche standard d'ERPNext écarte (has_variants = 0 forcé).
//
// frappe.ui.form.on n'accepte qu'un DocType à la fois : un tableau devient la
// clé « Stock Entry,Stock Reconciliation » et le gestionnaire ne tourne jamais.
['Stock Entry', 'Stock Reconciliation'].forEach((doctype) => {
  frappe.ui.form.on(doctype, {
    onload(frm) {
      override_stock_item_query(frm);
    },
    refresh(frm) {
      override_stock_item_query(frm);
    },
  });
});

function override_stock_item_query(frm) {
  if (!frm.fields_dict.items || !frm.fields_dict.items.grid) return;

  const query = function () {
    return {
      query: 'customization_app.query.stock_item_query',
      filters: { is_stock_item: 1 },
    };
  };

  // stock_entry.js pose sa requête dans setup() (avant nous), mais
  // stock_reconciliation.js la pose dans onload, que Frappe déclenche sans
  // l'attendre : son affectation retombe APRÈS notre refresh. On fige donc
  // la propriété : toute réaffectation ultérieure (set_query, affectation
  // directe) est ignorée, l'ordre d'exécution n'a plus d'importance.
  const champ = frm.fields_dict.items.grid.get_field('item_code');
  Object.defineProperty(champ, 'get_query', {
    get: () => query,
    set: () => {},
    configurable: true,
  });
}
