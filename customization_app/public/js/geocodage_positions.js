// Dialogues communs au calendrier et à la fiche Adresse. Aucune recherche à leur chargement.
(() => {
  const API = "customization_app.geocodage_adresses.";
  const esc = s => frappe.utils.escape_html(s == null ? "" : String(s));
  const carte = p => `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(p.join(","))}`;
  const appel = async (methode, args) => (await frappe.call({method: API + methode, args})).message;
  const bouton = (d, attente, label) => d.get_primary_btn().prop("disabled", attente).text(attente ? "Enregistrement…" : label);

  async function renseigner(adresse, apres) {
    const etat = await appel("etat_adresse", {adresse});
    const d = new frappe.ui.Dialog({title: "Renseigner la position", fields: [
      {fieldtype: "HTML", options: "<p>Collez le lien Google Maps du lieu d’intervention ou ses coordonnées GPS.</p>"},
      {fieldname: "lien", fieldtype: "Data", label: "Lien Google Maps"},
      {fieldtype: "Section Break", label: "Ou les coordonnées GPS"},
      {fieldname: "latitude", fieldtype: "Float", precision: 7, label: "Latitude"},
      {fieldname: "longitude", fieldtype: "Float", precision: 7, label: "Longitude"}
    ], primary_action_label: "Enregistrer la position", primary_action: async v => {
      if (!v.lien && (!Number.isFinite(v.latitude) || !Number.isFinite(v.longitude) || !v.latitude || !v.longitude)) {
        frappe.msgprint("Renseignez un lien Google Maps ou les deux coordonnées GPS."); return;
      }
      bouton(d, true);
      try {
        await appel("enregistrer_gps", {...v, adresse, empreinte: etat.signature, version: etat.version});
        d.hide();
        frappe.show_alert({message: "Position enregistrée", indicator: "green"});
        if (apres) await apres();
      } catch (_) { /* Frappe affiche l'erreur ; garder les champs saisis. */ }
      finally { bouton(d, false, "Enregistrer la position"); }
    }});
    d.$wrapper.attr("data-geocodage-gps", "1");
    d.show();
    return d;
  }

  async function verifier(adresse, apres) {
    const r = await appel("diagnostiquer", {adresse});
    const connu = !!r.point;
    const d = new frappe.ui.Dialog({title: "Vérifier la position", size: "large",
      fields: [{fieldtype: "HTML", fieldname: "resultat"}],
      primary_action_label: connu ? "Terminer la vérification" : "Saisir un lien ou un GPS",
      primary_action: async () => {
        if (connu) { d.hide(); if (apres) await apres(); return; }
        d.get_primary_btn().prop("disabled", true);
        try { await renseigner(adresse, async () => { d.hide(); if (apres) await apres(); }); }
        catch (_) { /* erreur affichée par Frappe */ }
        finally { d.get_primary_btn().prop("disabled", false); }
      }});
    d.$wrapper.attr("data-geocodage-verification", "1");
    const precisions = {ROOFTOP: "Bâtiment", RANGE_INTERPOLATED: "Numéro estimé sur la rue", GEOMETRIC_CENTER: "Centre du lieu ou de la zone",
      APPROXIMATE: "Ville ou quartier — approximatif", house: "Bâtiment", building: "Bâtiment"};
    d.fields_dict.resultat.$wrapper.html(`
      ${r.adresse_saisie ? `<p><b>Adresse saisie</b><br>${esc(r.adresse_saisie)}</p>` : ""}
      <div class="alert ${connu ? "alert-success" : "alert-warning"}"><b>${connu ? "Position disponible" : "Position à préciser"}</b><br>${esc(r.motif)}</div>
      ${connu ? `<p><a target="_blank" rel="noopener" href="${carte(r.point)}">Voir la position retenue sur Google Maps</a></p>` :
        `<p>${r.lien_present ? "Le lien enregistré ne donne pas encore une position exploitable." : "Aucun lien Google Maps n’est enregistré pour cette adresse."}
        Vous pouvez saisir un lien ou un GPS, ou contrôler les propositions ci-dessous.</p>`}
      ${r.distance_services_m == null ? "" : `<p>Écart entre les services : ${esc(r.distance_services_m)} m.</p>`}
      ${(r.candidats || []).map((c, i) => `<div class="well" style="margin-bottom:12px;padding:14px">
        <b>${c.service === "google" ? "Google Maps" : "Nominatim"}</b> · ${esc(precisions[c.precision] || c.precision)}
        ${c.partiel ? '<span class="text-muted"> · correspondance partielle</span>' : ""}
        <p style="margin-top:8px">${esc(c.libelle)}<br><span class="text-muted">${esc(c.motif)}</span></p>
        <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:center">
          <a target="_blank" rel="noopener" href="${carte([c.lat, c.lng])}">Voir sur Google Maps</a>
          <button class="btn btn-default btn-sm" data-confirmer="${i}">Confirmer ce point après contrôle</button>
        </div></div>`).join("")}
      ${!connu ? '<p class="text-muted">Une proposition de ville ou de quartier ne désigne pas forcément le lieu d’intervention. Confirmez uniquement un point que vous avez vérifié.</p>' : ""}`);
    d.fields_dict.resultat.$wrapper.on("click", "[data-confirmer]", ev => {
      const b = $(ev.currentTarget), index = Number(b.attr("data-confirmer"));
      frappe.confirm("Confirmez-vous que ce point est bien le lieu d’intervention, après vérification sur la carte ?", async () => {
        b.prop("disabled", true);
        try {
          await appel("confirmer", {adresse, jeton: r.jeton, candidat: index});
          d.hide(); frappe.show_alert({message: "Position confirmée", indicator: "green"});
          if (apres) await apres();
        } catch (_) { /* proposition expirée ou adresse modifiée : ne pas fermer */ }
        finally { b.prop("disabled", false); }
      });
    });
    d.show();
    return d;
  }
  window.aquaworld_geocodage = {verifier, renseigner};
})();
