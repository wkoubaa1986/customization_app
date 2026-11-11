// Liste « Liste prix documents » : le bouton « Ajouter » ouvre l'assistant (page liste-prix-assistant),
// le formulaire d'origine reste accessible par « Formulaire classique » (11/11/2026).
// En app_include_js : le doctype est personnalisé (en base), doctype_list_js ne le charge pas.
// `primary_action` et non `page.set_primary_action` dans onload : la liste repose son propre bouton après.
frappe.listview_settings["Liste prix documents"] = Object.assign(frappe.listview_settings["Liste prix documents"] || {}, {
  primary_action() {
    frappe.set_route("liste-prix-assistant");
  },
  onload(listview) {
    if (!frappe.model.can_create("Liste prix documents")) return;
    listview.page.add_inner_button(__("Formulaire classique"), () => frappe.new_doc("Liste prix documents"));
  },
});
