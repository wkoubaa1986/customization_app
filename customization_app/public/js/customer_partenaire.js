// Fiche client : « géré par le partenaire » (exclu de nos relances) — bandeau + bouton. Règles : partenaire_clients.py.
frappe.ui.form.on("Customer", {
  refresh(frm) {
    if (frm.is_new()) return;
    frappe.call({ method: "customization_app.partenaire_clients.etat", args: { customer: frm.doc.name } }).then((r) => {
      const e = r.message || {};
      if (!e.champs) return;
      const esc = frappe.utils.escape_html;
      const lien = ` · <a href="/app/clients-partenaire">Clients partenaire</a>`;
      if (e.gere) {
        frm.dashboard.set_headline(`🤝 Client <b>géré par le partenaire</b>${e.partenaire ? " " + esc(e.partenaire) : ""} : exclu de nos relances d’entretien (SMS, e-mail, liste d’appels).${lien}`, "red");
        if (e.peut_modifier) frm.add_custom_button("↩️ Repasser chez nous", () => cp_basculer(frm, 0));
      } else if (e.cree_par_partenaire) {
        frm.dashboard.set_headline(`⚠️ Fiche créée par le compte partenaire mais <b>non marquée</b> : nos relances lui partent. À trancher.${lien}`, "orange");
        if (e.peut_modifier) frm.add_custom_button("🤝 Géré par le partenaire", () => cp_basculer(frm, 1));
      } else if (e.zone) {
        frm.dashboard.set_headline(`📍 Notre client en zone partenaire (${esc(e.zone)}) : relancé par nous, rendez-vous exécutés par le partenaire.${lien}`, "blue");
        if (e.peut_modifier) frm.add_custom_button("🤝 Géré par le partenaire", () => cp_basculer(frm, 1));
      } else if (e.peut_modifier) {
        frm.add_custom_button("🤝 Géré par le partenaire", () => cp_basculer(frm, 1));
      }
    });
  },
});

function cp_basculer(frm, valeur) {
  const q = valeur ? "Marquer ce client « géré par le partenaire » ? Il sortira de nos relances SMS, e-mail et appels."
                   : "Reprendre ce client chez nous ? Il sera de nouveau relancé par notre équipe.";
  frappe.confirm(q, async () => {
    await frappe.call({ method: "customization_app.partenaire_clients.basculer", args: { clients: [frm.doc.name], valeur }, freeze: true });
    frm.reload_doc();
  });
}
