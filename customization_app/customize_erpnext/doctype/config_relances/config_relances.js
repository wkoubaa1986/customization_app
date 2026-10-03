// Réglage des relances d’entretien : recopier les familles historiques pour les adapter.
frappe.ui.form.on("Config Relances", {
  refresh(frm) {
    frm.add_custom_button("↩️ Familles par défaut", async () => {
      const r = (await frappe.call({ method: "customization_app.relances_config.familles_par_defaut" })).message || [];
      frappe.confirm(`Remplacer la table des familles par les ${r.length} familles historiques ? (à enregistrer ensuite)`, () => {
        frm.clear_table("familles");
        r.forEach((f) => { const row = frm.add_child("familles"); Object.assign(row, f); });
        frm.refresh_field("familles");
      });
    });
    frm.dashboard.set_headline("Un champ vide = la valeur historique entre parenthèses. Les trois automatismes relisent ce réglage à chaque passage.");
  },
});
