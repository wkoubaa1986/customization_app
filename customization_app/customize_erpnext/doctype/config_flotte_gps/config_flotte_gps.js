frappe.ui.form.on("Config Flotte GPS", {
  refresh(frm) {
    frm.add_custom_button(__("Ouvrir le suivi terrain"), () => frappe.set_route("suivi-terrain"));
  },
  lire_vehicules(frm) {
    if (!frm.doc.utilisateur || !frm.doc.mot_de_passe) {
      frappe.msgprint(__("Renseigner l’utilisateur et le mot de passe, puis enregistrer."));
      return;
    }
    frappe.call({
      method: "customization_app.flotte_gps.lister_vehicules",
      freeze: true, freeze_message: __("Connexion à la plateforme…"),
    }).then((r) => {
      const vehs = r.message || [];
      const deja = new Set((frm.doc.vehicules || []).map((v) => String(v.cbox)));
      let ajoutes = 0;
      vehs.forEach((v) => {
        if (deja.has(String(v.CBOX))) return;
        const row = frm.add_child("vehicules");
        row.vehicule = v.LVEH; row.cbox = String(v.CBOX); row.actif = 1; ajoutes++;
      });
      frm.refresh_field("vehicules");
      frappe.show_alert({ message: __("{0} véhicule(s) lus, {1} ajouté(s) — affecter les employés puis enregistrer.", [vehs.length, ajoutes]), indicator: "green" });
    });
  },
});
