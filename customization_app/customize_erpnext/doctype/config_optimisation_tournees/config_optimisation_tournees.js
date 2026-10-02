// Réglage de l'optimisation des tournées : état du géocodage des adresses et bouton pour le lancer.
frappe.ui.form.on("Config Optimisation Tournees", {
  refresh(frm) {
    frm.add_custom_button("📍 Géocoder les adresses", () => {
      frappe.confirm(
        "Lire la position de toutes les adresses sans position (lien Google Maps, sinon texte de l’adresse via OpenStreetMap) ? " +
        "Tâche de fond, quelques minutes pour des centaines d’adresses.",
        async () => {
          const r = (await frappe.call({ method: "customization_app.tournee_optimisation.lancer_geocodage", args: { limite: 1000 }, freeze: true })).message;
          frappe.show_alert({ message: `Géocodage lancé en tâche de fond — ${r.geocodees}/${r.total} adresses positionnées pour l’instant`, indicator: "blue" }, 6);
        }
      );
    });
    frappe.call({ method: "customization_app.tournee_optimisation.etat_geocodage" }).then((r) => {
      const e = r.message || {};
      frm.dashboard.set_headline(
        `📍 Adresses positionnées : <b>${e.geocodees}</b> / ${e.total} (avec lien Google Maps : ${e.avec_lien} · ` +
        `liens morts : ${e.liens_morts} · position approchée par le texte : ${e.approchees}). ` +
        `Un lien mort se corrige sur l’adresse (coller un nouveau lien) ; le géocodage repasse chaque nuit.`
      );
    });
  },
});
