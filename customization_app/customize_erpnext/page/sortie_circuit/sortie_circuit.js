frappe.pages["sortie-circuit"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Sorties de circuit", single_column: true });
  // Le bloc vit dans public/js/sortie_circuit.js : le même sert d'onglet au suivi terrain.
  frappe.require("/assets/customization_app/js/sortie_circuit.js", () => {
    const $main = $(wrapper).find(".layout-main-section");
    $main.html(SortieCircuit.html());
    wrapper.sortie_circuit = new SortieCircuit($main[0]);
  });
};
