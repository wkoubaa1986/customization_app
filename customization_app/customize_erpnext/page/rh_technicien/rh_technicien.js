/**
 * « 🏖️ Mes congés et ma paie » — l'écran RH du technicien sur son téléphone (décision 10/11/2026).
 * Deux choses seulement : demander un congé (et suivre ses demandes), ouvrir ses fiches de paie.
 * Tout vient du serveur (customization_app.rh_technicien) ; la barre de navigation de l'écran simplifié reste là.
 */
frappe.pages["rh-technicien"].on_page_load = function (wrapper) {
    frappe.ui.make_app_page({ parent: wrapper, title: __("Mes congés et ma paie"), single_column: true });
    wrapper.rh = new RhTechnicien($(wrapper).find(".layout-main-section"));
};
frappe.pages["rh-technicien"].on_page_show = function (wrapper) {
    if (wrapper.rh) wrapper.rh.rafraichir();
};

(function () {
    const API = "customization_app.rh_technicien";
    const esc = (t) => frappe.utils.escape_html(String(t == null ? "" : t));
    const dt = (d) => (d ? frappe.datetime.str_to_user(d) : "");
    const nb = (v) => (v == null ? "—" : (Math.round(v * 10) / 10).toString().replace(".", ","));
    const STATUTS = { Open: ["En attente", "#b45309"], Approved: ["Approuvé", "#15803d"], Rejected: ["Refusé", "#b91c1c"], Cancelled: ["Annulé", "#6b7280"] };

    function poser_css() {
        if (document.getElementById("rh-css")) return;
        const s = document.createElement("style");
        s.id = "rh-css";
        s.textContent = `
        .rh-bloc { background:#fff; border:1px solid var(--border-color,#e4e8ee); border-radius:14px; padding:12px 14px; margin-bottom:12px; }
        .rh-titre { font-weight:700; font-size:15px; margin-bottom:8px; display:flex; align-items:center; justify-content:space-between; }
        .rh-solde { display:flex; justify-content:space-between; align-items:center; padding:8px 0; border-top:1px solid #f1f3f6; }
        .rh-solde:first-of-type { border-top:none; }
        .rh-solde b { font-size:18px; }
        .rh-ligne { display:flex; justify-content:space-between; align-items:center; gap:8px; padding:10px 0; border-top:1px solid #f1f3f6; }
        .rh-ligne:first-of-type { border-top:none; }
        .rh-pastille { font-size:11.5px; font-weight:700; padding:2px 8px; border-radius:999px; color:#fff; white-space:nowrap; }
        .rh-gros { min-height:48px; font-size:15px; font-weight:700; width:100%; }
        .rh-vide { color:#6b7280; font-size:13px; padding:6px 0; }
        @media (max-width:768px) { .rh-bloc { border-radius:12px; } }`;
        document.head.appendChild(s);
    }

    class RhTechnicien {
        constructor($parent) {
            poser_css();
            this.$el = $('<div class="rh"></div>').appendTo($parent);
            this.rafraichir();
        }

        rafraichir() {
            frappe.call({ method: API + ".etat" }).then((r) => this.peindre(r.message || {}));
        }

        peindre(e) {
            this.etat = e;
            const soldes = (e.soldes || []).map((s) => `
                <div class="rh-solde"><span>${esc(s.type)}${s.sans_solde ? ` <span class="text-muted">(${__("sans solde")})</span>` : ""}</span>
                    ${s.sans_solde ? "" : `<span><b>${nb(s.restants)}</b> <span class="text-muted">/ ${nb(s.alloues)} ${__("j.")}${
                        s.en_attente ? ` · ${nb(s.en_attente)} ${__("en attente")}` : ""}</span></span>`}
                </div>`).join("") || `<div class="rh-vide">${__("Aucun droit à congé attribué pour le moment.")}</div>`;
            const demandes = (e.demandes || []).map((d) => {
                const [lib, couleur] = STATUTS[d.status] || [d.status, "#6b7280"];
                return `<div class="rh-ligne">
                    <div><div><b>${esc(d.leave_type)}</b> · ${nb(d.total_leave_days)} ${__("j.")}</div>
                         <div class="text-muted" style="font-size:12px">${dt(d.from_date)}${d.to_date !== d.from_date ? " → " + dt(d.to_date) : ""}${d.half_day ? " · " + __("demi-journée") : ""}</div></div>
                    <div style="display:flex;gap:6px;align-items:center"><span class="rh-pastille" style="background:${couleur}">${__(lib)}</span>
                        ${d.annulable ? `<button class="btn btn-xs btn-default rh-annuler" data-name="${esc(d.name)}">✕</button>` : ""}</div>
                </div>`;
            }).join("") || `<div class="rh-vide">${__("Aucune demande.")}</div>`;
            const fiches = (e.fiches || []).map((f) => `
                <a class="rh-ligne" href="${f.pdf}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">
                    <div><b>${esc(moment(f.start_date).format("MMMM YYYY"))}</b><div class="text-muted" style="font-size:12px">${dt(f.start_date)} → ${dt(f.end_date)}</div></div>
                    <div><b>${format_currency(f.net_pay || 0, "TND")}</b> <span class="text-muted">📄</span></div>
                </a>`).join("") || `<div class="rh-vide">${__("Aucune fiche de paie disponible.")}</div>`;
            const feries = (e.feries || []).map((f) => `<span class="text-muted" style="font-size:12px">${dt(f.date)} ${esc(f.libelle)}</span>`).join(" · ");
            this.$el.html(`
                <div class="rh-bloc">
                    <div class="rh-titre"><span>🏖️ ${__("Mes congés")}</span>${e.approbateur ? `<span class="text-muted" style="font-size:12px;font-weight:400">${__("approuvés par")} ${esc(e.approbateur)}</span>` : ""}</div>
                    ${soldes}
                    <button class="btn btn-primary rh-gros rh-demander" style="margin-top:10px">➕ ${__("Demander un congé")}</button>
                </div>
                <div class="rh-bloc"><div class="rh-titre"><span>🗂️ ${__("Mes demandes")}</span></div>${demandes}</div>
                <div class="rh-bloc"><div class="rh-titre"><span>💵 ${__("Mes fiches de paie")}</span></div>${fiches}</div>
                ${feries ? `<div class="rh-bloc"><div class="rh-titre"><span>📅 ${__("Prochains jours fériés")}</span></div>${feries}</div>` : ""}`);
            this.$el.find(".rh-demander").on("click", () => this.demander());
            this.$el.find(".rh-annuler").on("click", (ev) => this.annuler($(ev.currentTarget).attr("data-name")));
        }

        demander() {
            const types = (this.etat.soldes || []).map((s) => s.type);
            if (!types.length) { frappe.msgprint(__("Aucun droit à congé ne vous est attribué : voyez avec votre responsable.")); return; }
            const d = new frappe.ui.Dialog({
                title: __("Demander un congé"),
                fields: [
                    { fieldtype: "Select", fieldname: "leave_type", label: __("Type de congé"), options: types.join("\n"), default: types[0], reqd: 1 },
                    { fieldtype: "Date", fieldname: "from_date", label: __("Du"), reqd: 1, default: frappe.datetime.get_today() },
                    { fieldtype: "Date", fieldname: "to_date", label: __("Jusqu'au"), reqd: 1, default: frappe.datetime.get_today() },
                    { fieldtype: "Check", fieldname: "half_day", label: __("Demi-journée") },
                    { fieldtype: "Date", fieldname: "half_day_date", label: __("Date de la demi-journée"), depends_on: "half_day" },
                    { fieldtype: "Small Text", fieldname: "motif", label: __("Motif (facultatif)") },
                ],
                primary_action_label: __("Envoyer la demande"),
                primary_action: (v) => {
                    frappe.call({
                        method: API + ".demander_conge",
                        args: { leave_type: v.leave_type, from_date: v.from_date, to_date: v.to_date, half_day: v.half_day ? 1 : 0,
                                half_day_date: v.half_day ? (v.half_day_date || v.from_date) : null, motif: v.motif },
                        freeze: true,
                        callback: (r) => {
                            d.hide();
                            frappe.show_alert({ message: __("Demande envoyée ({0} jour(s)) : en attente d'approbation.", [nb(r.message.jours)]), indicator: "green" });
                            this.rafraichir();
                        },
                    });
                },
            });
            d.show();
        }

        annuler(name) {
            frappe.confirm(__("Retirer cette demande ?"), () => {
                frappe.call({ method: API + ".annuler_demande", args: { name }, freeze: true, callback: () => this.rafraichir() });
            });
        }
    }
    window.RhTechnicien = RhTechnicien;
})();
