frappe.ui.form.on("Config Flotte GPS", {
  refresh(frm) {
    frm.add_custom_button(__("Ouvrir le suivi terrain"), () => frappe.set_route("suivi-terrain"));
    frm.add_custom_button(__("Réglage des tournées (domiciles)"), () => frappe.set_route("Form", "Config Optimisation Tournees"));
    // Domiciles : lus dans Config Optimisation Tournees (« Points de départ particuliers ») — une seule source pour les deux.
    frappe.call({ method: "customization_app.flotte_presence.domiciles_regles" }).then((r) => {
      const esc = frappe.utils.escape_html, d = r.message || { domiciles: [], rayon_nuit_m: 500 };
      let html = `<div class="text-muted small" style="margin-bottom:6px">Lus dans <a href="/app/config-optimisation-tournees">Config Optimisation Tournées → Points de départ particuliers</a>. Le suivi GPS s'en sert pour dire « au domicile » la nuit, ignorer ces arrêts en sortie de circuit, et attribuer à un technicien le véhicule qui dort à moins de ${d.rayon_nuit_m} m de chez lui. La même valeur est sur chaque fiche Employé (section « Domicile ») : modifier l'un met l'autre à jour. Pour en régler un : onglet <a href="/app/suivi-terrain">Présence Aquaworld</a> (« Où dorment les voitures la nuit ») ou la table du réglage des tournées.</div>`;
      if (!d.domiciles.length) html += `<div class="text-muted">Aucun domicile réglé.</div>`;
      else {
        html += `<table class="table table-bordered table-sm" style="max-width:720px"><thead><tr><th>Technicien</th><th>Position</th><th>Carte</th></tr></thead><tbody>`;
        d.domiciles.forEach((x) => { html += `<tr><td><a href="/app/employee/${encodeURIComponent(x.employe)}">${esc(x.employe_nom)}</a></td><td>${x.lat.toFixed(5)}, ${x.lng.toFixed(5)}</td><td><a href="https://www.google.com/maps?q=${x.lat},${x.lng}" target="_blank" rel="noopener">🗺 ouvrir</a></td></tr>`; });
        html += `</tbody></table>`;
      }
      frm.get_field("domiciles_html").$wrapper.html(html);
    });
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
