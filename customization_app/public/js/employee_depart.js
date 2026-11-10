// Fiche Employé : « Départ : transférer son travail » (depart_employe.py, 10/11/2026).
// Un seul geste : inventaire de ce qui reste à son nom, choix du remplaçant pour les rendez-vous à venir,
// puis retrait partout, calendrier nettoyé, vérification finale du stock, fiche « Left », compte désactivé.
frappe.ui.form.on("Employee", {
  refresh(frm) {
    if (frm.is_new()) return;
    const droit = frappe.user.has_role("System Manager") || frappe.user.has_role("HR Manager") || frappe.user.has_role("Congés RH");
    if (!droit) return;
    if (frm.doc.custom_depart_le) {
      if (frm.doc.custom_depart_verification && !frm.doc.custom_depart_stock_clos) {
        frm.dashboard.set_headline(__("🚪 Départ traité le {0} — vérification finale du stock en attente : {1}",
          [frappe.datetime.str_to_user(frm.doc.custom_depart_le),
           `<a href="/app/verification-stock/${encodeURIComponent(frm.doc.custom_depart_verification)}">${frm.doc.custom_depart_verification}</a>`]));
      }
      return;
    }
    frm.add_custom_button(__("🚪 Départ : transférer son travail"), () => rcj_depart_employe(frm));
  },
});

function rcj_depart_employe(frm) {
  const API = "customization_app.depart_employe";
  frappe.call({
    method: API + ".inventaire", args: { employe: frm.doc.name }, freeze: true,
    callback: (r) => {
      const inv = r.message || {};
      const esc = frappe.utils.escape_html;
      const fmt = (v) => format_currency(v || 0, "TND");
      const lignes = [
        [__("Rendez-vous clients à venir"), (inv.taches_a_venir || []).length],
        [__("Tâches ouvertes passées, jamais clôturées"), inv.taches_passees || 0],
        [__("Jours d'absence à venir dans le calendrier"), inv.absences_a_venir || 0],
        [__("Congés / récupérations à venir"), (inv.conges_a_venir || []).length + (inv.recup_planifiees || 0)],
        [__("Réglages qui le citent"), (inv.reglages || []).length],
        [__("Vérifications de stock ouvertes"), (inv.verifications_ouvertes || []).length],
        [__("Machines en réparation"), (inv.reparations || []).length],
        [__("Activités / réunions"), (inv.activites || 0) + (inv.reunions || 0)],
      ];
      const stock = inv.stock && inv.stock.quantite > 0
        ? __("{0} article(s), {1} dans {2}", [inv.stock.articles, fmt(inv.stock.valeur), esc(inv.stock.entrepot)])
        : (inv.stock ? __("entrepôt vide ({0})", [esc(inv.stock.entrepot)]) : __("pas d'entrepôt"));
      const html = `
        <table class="table table-bordered" style="font-size:12.5px;margin-bottom:6px">
          ${lignes.map(([l, n]) => `<tr><td>${l}</td><td style="text-align:right;font-weight:700">${n}</td></tr>`).join("")}
          <tr><td>${__("Stock")}</td><td style="text-align:right;font-weight:700">${stock}</td></tr>
        </table>
        ${(inv.reglages || []).length ? `<div class="text-muted" style="font-size:11.5px;margin-bottom:6px">${__("Retiré de :")} ${
          [...new Set(inv.reglages.map((x) => esc(x.libelle)))].join(" · ")}</div>` : ""}
        <div class="text-muted" style="font-size:11.5px">${__("Les réglages ne sont pas remplacés : son nom en est retiré. Congés et récupérations à venir sortent du calendrier. Le stock passe par une vérification finale du responsable principal du magasin ; à la fin, le reste part au magasin et l'entrepôt est fermé. La fiche passe en « Parti » et son compte est désactivé.")}</div>`;
      const aTaches = (inv.taches_a_venir || []).length > 0;
      const d = new frappe.ui.Dialog({
        title: __("Départ de {0}", [inv.nom || frm.doc.employee_name]),
        fields: [
          { fieldtype: "HTML", fieldname: "inv", options: html },
          { fieldtype: "Date", fieldname: "date_depart", label: __("Date du départ"), default: frappe.datetime.get_today(), reqd: 1 },
          { fieldtype: "Column Break" },
          { fieldtype: "Link", fieldname: "remplacant", label: __("Reprend ses rendez-vous à venir"), options: "Employee",
            reqd: aTaches ? 1 : 0, get_query: () => ({ filters: { status: "Active", name: ["!=", frm.doc.name] } }),
            description: aTaches ? __("{0} rendez-vous à transférer", [inv.taches_a_venir.length]) : __("aucun rendez-vous à venir") },
          ...(inv.portail_defaut ? [{ fieldtype: "Link", fieldname: "remplacant_portail", options: "Employee", reqd: 1,
            label: __("Nouveau technicien par défaut du portail de rendez-vous"),
            get_query: () => ({ filters: { status: "Active", name: ["!=", frm.doc.name] } }) }] : []),
          { fieldtype: "Section Break" },
          { fieldtype: "Check", fieldname: "annuler_passees", label: __("Annuler ses tâches passées jamais clôturées ({0})", [inv.taches_passees || 0]),
            default: 1 },
        ],
        primary_action_label: __("Traiter le départ"),
        primary_action(v) {
          frappe.confirm(__("Traiter le départ de {0} ? Tout est fait en une fois.", [esc(inv.nom || "")]), () => {
            frappe.call({
              method: API + ".executer",
              args: { employe: frm.doc.name, remplacant: v.remplacant || null, remplacant_portail: v.remplacant_portail || null,
                      date_depart: v.date_depart, annuler_passees: v.annuler_passees ? 1 : 0 },
              freeze: true, freeze_message: __("Transfert en cours…"),
              callback: (rr) => {
                d.hide();
                const j = (rr.message && rr.message.journal) || [];
                frappe.msgprint({ title: __("Départ traité"), indicator: "green",
                  message: "<ul>" + j.map((x) => `<li>${esc(x)}</li>`).join("") + "</ul>" });
                frm.reload_doc();
              },
            });
          });
        },
      });
      d.show();
    },
  });
}
