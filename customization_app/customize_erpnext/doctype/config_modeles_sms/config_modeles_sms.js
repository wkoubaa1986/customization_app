// Modèles des SMS automatiques : aperçu sur de vraies données (coût en segments) et retour aux textes d’origine.
frappe.ui.form.on("Config Modeles SMS", {
  refresh(frm) {
    frm.add_custom_button("👁️ Aperçu des messages", async () => {
      const modeles = {};
      Object.keys(frm.doc).forEach((k) => { if (typeof frm.doc[k] === "string") modeles[k] = frm.doc[k]; });
      const r = (await frappe.call({ method: "customization_app.modeles_sms.apercu", args: { modeles }, freeze: true })).message || [];
      const esc = frappe.utils.escape_html;
      const html = r.map((m) => {
        const a = m.analyse || {};
        const cout = a.unicode
          ? `<span style="color:#b91c1c">⚠️ unicode (${esc((a.hors_gsm || []).join(" "))}) · ${a.longueur} car. · <b>${a.segments} segment(s)</b></span>`
          : `<span style="color:#15803d">GSM · ${a.longueur} car. · <b>${a.segments} segment(s)</b></span>`;
        return `<div style="margin-bottom:12px"><div><b>${esc(m.titre)}</b> <span class="text-muted" style="font-size:11.5px">· ${cout}</span></div>
          <pre style="white-space:pre-wrap;font-family:inherit;font-size:12.5px;background:#f8fafc;border-radius:6px;padding:8px 10px;margin:4px 0 0">${esc(m.texte)}</pre></div>`;
      }).join("");
      const d = new frappe.ui.Dialog({ title: "Aperçu — textes du formulaire (même non enregistrés)", size: "large" });
      d.$body.html(html || "<div class='text-muted'>Rien à afficher.</div>");
      d.show();
    });
    frm.add_custom_button("↩️ Textes d’origine", async () => {
      const d = (await frappe.call({ method: "customization_app.modeles_sms.defauts" })).message || {};
      frappe.confirm("Remplacer tous les champs par les textes d’origine ? (à enregistrer ensuite)", () => {
        Object.keys(d).forEach((k) => frm.set_value(k, d[k]));
      });
    });
    frappe.call({ method: "customization_app.modeles_sms.defauts" }).then((r) => {
      const d = r.message || {};
      const vides = Object.keys(d).filter((k) => !(frm.doc[k] || "").trim()).length;
      frm.dashboard.set_headline(vides
        ? `${vides} champ(s) vide(s) → texte d’origine utilisé. Cliquez « Textes d’origine » pour les voir et les adapter.`
        : "Tous les textes sont personnalisés. « Aperçu des messages » montre ce que recevra le client et le coût en segments.");
    });
  },
});
