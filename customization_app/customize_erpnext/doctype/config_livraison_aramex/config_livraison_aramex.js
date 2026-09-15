// Config Livraison Aramex : un bouton pour prouver que les identifiants passent
// (un TrackShipments — lecture seule — rend « inconnu » si le login est bon, « ERR75 » sinon).
frappe.ui.form.on("Config Livraison Aramex", {
    refresh(frm) {
        if (!frm.doc.api_active) return;
        frm.add_custom_button(__("Tester la connexion Aramex"), () => {
            if (frm.is_dirty()) {
                frappe.msgprint(__("Enregistrez la configuration avant de tester."));
                return;
            }
            frappe.call({
                method: "customization_app.aramex_api.tester_connexion",
                freeze: true,
                freeze_message: __("Appel de l'API Aramex…"),
                callback: (r) => {
                    const m = r.message || {};
                    frappe.msgprint({
                        title: m.ok ? __("Connexion réussie") : __("Connexion refusée"),
                        indicator: m.ok ? "green" : "red",
                        message: frappe.utils.escape_html(m.ok ? __("Aramex répond. Détail : {0}", [m.detail || ""]) : m.erreur || ""),
                    });
                },
            });
        });
    },
});
