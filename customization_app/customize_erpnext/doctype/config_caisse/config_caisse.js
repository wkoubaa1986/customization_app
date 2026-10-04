// Config Caisse : la colonne « Caisse » de « Date de départ par caisse » propose les noms exacts
// du sélecteur du rapport (Autocomplete), pour ne pas taper un nom qui ne correspond à personne.
frappe.ui.form.on("Config Caisse", {
    refresh(frm) {
        frappe.call({
            method: "customization_app.caisse_collecte.noms_caisses",
            callback: (r) => {
                const noms = r.message || [];
                const grid = frm.fields_dict.departs && frm.fields_dict.departs.grid;
                if (grid) grid.update_docfield_property("caisse", "options", noms);
            },
        });
    },
});
