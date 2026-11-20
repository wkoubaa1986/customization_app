// La fiche Adresse et le calendrier utilisent les mêmes dialogues de localisation.
frappe.ui.form.on("Address", {
  refresh(frm) {
    if (frm.is_new() || !["System Manager", "Responsable magasin", "Appels"].some(r => (frappe.user_roles || []).includes(r))) return;
    const ouvrir = async action => {
      if (frm.is_dirty()) { frappe.msgprint("Enregistrez l’adresse avant de vérifier sa position."); return; }
      try { await window.aquaworld_geocodage[action](frm.doc.name, () => frm.reload_doc()); }
      catch (_) { /* Frappe affiche l'erreur de chargement. */ }
    };
    frm.add_custom_button("Vérifier la position", () => ouvrir("verifier"), "Localisation");
    frm.add_custom_button("Renseigner un point GPS", () => ouvrir("renseigner"), "Localisation");
  }
});
