/**
 * « 🧾 Contrôle de la paie » — le mois en un coup d'œil, pour le responsable de la paie (demande du 24/11/2026).
 * Une ligne par employé : brouillon / validée, jours, brut, retenues, net à payer ; le PDF ; sur un brouillon,
 * absences, prime exceptionnelle et avance. Ce qui manque pour qu'un employé ait ses fiches. Tout vient de
 * customization_app.paie (rôle « Responsable paie »).
 */
frappe.pages["controle-paie"].on_page_load = function (wrapper) {
    frappe.ui.make_app_page({ parent: wrapper, title: __("Contrôle de la paie"), single_column: true });
    wrapper.cp = new ControlePaie($(wrapper).find(".layout-main-section"));
};
frappe.pages["controle-paie"].on_page_show = function (wrapper) {
    if (wrapper.cp) wrapper.cp.charger();
};

(function () {
    const API = "customization_app.paie";
    const FORMAT = "Aqua World Fiche de paie";
    const esc = (t) => frappe.utils.escape_html(String(t == null ? "" : t));
    const dt = (v) => (v || v === 0 ? (Math.round(v * 1000) / 1000).toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 }) : "—");
    const jr = (v) => String(Math.round((v || 0) * 100) / 100).replace(".", ",");
    const MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"];
    const lib_mois = (iso) => { const [a, m] = iso.split("-"); return MOIS[+m - 1] + " " + a; };
    const pdf = (fiche) => "/api/method/frappe.utils.print_format.download_pdf?doctype=Salary%20Slip&name="
        + encodeURIComponent(fiche) + "&format=" + encodeURIComponent(FORMAT);

    function poser_css() {
        if (document.getElementById("cp-css")) return;
        const s = document.createElement("style");
        s.id = "cp-css";
        s.textContent = `
        .cp-bloc { background:#fff; border:1px solid var(--border-color,#e4e8ee); border-radius:14px; padding:12px 14px; margin-bottom:12px; }
        .cp-titre { font-weight:700; font-size:15px; margin-bottom:8px; }
        .cp-nav { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin-bottom:12px; }
        .cp-nav select { max-width:220px; }
        .cp-bandeau { border-radius:12px; padding:10px 12px; margin-bottom:12px; font-size:13.5px; display:flex; gap:10px; align-items:center; justify-content:space-between; flex-wrap:wrap; }
        .cp-bandeau.att { background:#fffbeb; border:1px solid #f59e0b; color:#78350f; }
        .cp-bandeau.ok { background:#f0fdf4; border:1px solid #22c55e; color:#14532d; }
        .cp-totaux { display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:8px; }
        .cp-totaux div { background:#f8fafc; border-radius:10px; padding:8px 10px; }
        .cp-totaux small { display:block; color:#6b7280; font-size:11.5px; }
        .cp-totaux b { font-size:15px; }
        .cp-table { width:100%; border-collapse:collapse; font-size:13px; }
        .cp-table th { text-align:left; color:#6b7280; font-weight:600; font-size:11.5px; padding:6px 6px; border-bottom:1px solid #e5e7eb; }
        .cp-table td { padding:8px 6px; border-bottom:1px solid #f1f3f6; vertical-align:middle; }
        .cp-table .n { text-align:right; white-space:nowrap; }
        .cp-pastille { font-size:11px; font-weight:700; padding:2px 8px; border-radius:999px; color:#fff; white-space:nowrap; }
        .cp-actions { white-space:nowrap; text-align:right; }
        .cp-actions .btn { margin-left:4px; }
        .cp-petit { color:#6b7280; font-size:12px; }
        .cp-manque { padding:6px 0; border-top:1px solid #f1f3f6; }
        .cp-manque:first-of-type { border-top:none; }
        @media (max-width:768px) {
            .cp-table thead { display:none; }
            .cp-table tr { display:block; border-bottom:1px solid #e5e7eb; padding:6px 0; }
            .cp-table td { display:flex; justify-content:space-between; border:none; padding:3px 2px; }
            .cp-table td[data-l]::before { content:attr(data-l); color:#6b7280; font-size:12px; }
            .cp-actions { text-align:left; }
        }`;
        document.head.appendChild(s);
    }

    class ControlePaie {
        constructor($parent) {
            poser_css();
            this.mois = null;
            this.$el = $('<div class="cp"></div>').appendTo($parent);
            this.charger();
        }

        charger() {
            frappe.call({ method: API + ".controle", args: { mois: this.mois } }).then((r) => this.peindre(r.message || {}));
        }

        peindre(c) {
            this.c = c;
            this.mois = c.mois;
            const i = (c.mois_possibles || []).indexOf(c.mois);
            const options = (c.mois_possibles || []).slice().reverse().map((m) =>
                `<option value="${m}" ${m === c.mois ? "selected" : ""}>${esc(lib_mois(m))}</option>`).join("");
            const reste = frappe.datetime.get_day_diff(c.validation_le, frappe.datetime.get_today());
            const bandeau = c.brouillons
                ? `<div class="cp-bandeau att"><span>📝 <b>${c.brouillons}</b> ${__("fiche(s) en brouillon")} — ${__("validation automatique le")}
                     <b>${esc(frappe.datetime.str_to_user(c.validation_le))}</b>${reste > 0 ? ` (${__("dans")} ${reste} ${__("jour(s)")})` : ""}.
                     ${__("Modifiables jusque-là")} (✏️).</span>
                   <button class="btn btn-sm btn-primary cp-valider">✅ ${__("Valider maintenant")}</button></div>`
                : c.validees
                    ? `<div class="cp-bandeau ok"><span>✅ ${c.validees} ${__("fiche(s) validée(s)")} — ${__("chaque employé voit la sienne dans « Mes congés et ma paie »")}.</span></div>`
                    : "";
            const t = c.totaux || {};
            const lignes = (c.lignes || []).map((l) => {
                const statut = !l.fiche ? [__("Pas encore de fiche"), "#9ca3af"] : l.docstatus === 1 ? [__("Validée"), "#15803d"] : [__("Brouillon"), "#b45309"];
                const retenues = (l.cnss || 0) + (l.irpp || 0) + (l.css || 0) + (l.avance || 0);
                const details = l.fiche ? [l.absences ? `${jr(l.absences)} ${__("j. d’absence")}` : "", l.prime ? `${__("prime")} ${dt(l.prime)}` : "",
                    l.avance ? `${__("avance")} ${dt(l.avance)}` : ""].filter(Boolean).join(" · ") : "";
                return `<tr>
                    <td><b>${esc(l.nom)}</b> <span class="cp-petit">${esc(l.profil)}${l.parti ? " · " + __("parti") : ""}</span>
                        ${details ? `<div class="cp-petit">${esc(details)}</div>` : ""}</td>
                    <td class="n" data-l="${__("Jours")}">${l.fiche ? jr(l.jours) : "—"}</td>
                    <td class="n" data-l="${__("Brut")}">${l.fiche ? dt(l.brut) : "—"}</td>
                    <td class="n" data-l="${__("Retenues")}" title="CNSS ${dt(l.cnss)} · IRPP ${dt(l.irpp)} · CSS ${dt(l.css)}${l.avance ? " · avance " + dt(l.avance) : ""}">${l.fiche ? dt(retenues) : "—"}</td>
                    <td class="n" data-l="${__("Net à payer")}"><b>${l.fiche ? dt(l.a_payer) : "—"}</b></td>
                    <td data-l="${__("Statut")}"><span class="cp-pastille" style="background:${statut[1]}">${statut[0]}</span></td>
                    <td class="cp-actions">${l.fiche ? `
                        <a class="btn btn-xs btn-default" href="${pdf(l.fiche)}" target="_blank" rel="noopener" title="${__("PDF")}">📄</a>
                        ${l.docstatus === 0 ? `<button class="btn btn-xs btn-default cp-modifier" data-fiche="${esc(l.fiche)}" title="${__("Absences, prime, avance")}">✏️</button>` : ""}
                        <a class="btn btn-xs btn-default" href="/app/salary-slip/${encodeURIComponent(l.fiche)}" title="${__("Ouvrir la fiche")}">↗</a>` : ""}</td>
                </tr>`;
            }).join("");
            const a_param = (c.a_parametrer || []).map((p) => `<div class="cp-manque">
                    <a href="/app/employee/${encodeURIComponent(p.employe)}"><b>${esc(p.nom)}</b></a> <span class="cp-petit">${esc(p.profil)}</span>
                    <div class="cp-petit">${__("À remplir dans l’onglet « Paie »")} : ${esc(p.manque.join(", "))}</div></div>`).join("");
            const sans = (c.sans_contrat || []).map((s) => `<a href="/app/employee/${encodeURIComponent(s.employe)}">${esc(s.nom)}</a>`).join(", ");
            this.$el.html(`
                <div class="cp-nav">
                    <button class="btn btn-sm btn-default cp-prec" ${i <= 0 ? "disabled" : ""}>◀</button>
                    <select class="form-control input-sm cp-mois">${options}</select>
                    <button class="btn btn-sm btn-default cp-suiv" ${i < 0 || i >= (c.mois_possibles || []).length - 1 ? "disabled" : ""}>▶</button>
                    <button class="btn btn-sm btn-default cp-recalculer" style="margin-left:auto">🔄 ${__("Recalculer maintenant")}</button>
                </div>
                ${bandeau}
                <div class="cp-bloc"><div class="cp-titre">${esc(c.libelle)}</div>
                    <div class="cp-totaux">
                        <div><small>${__("Brut")}</small><b>${dt(t.brut)}</b></div>
                        <div><small>CNSS</small><b>${dt(t.cnss)}</b></div>
                        <div><small>IRPP</small><b>${dt(t.irpp)}</b></div>
                        <div><small>CSS</small><b>${dt(t.css)}</b></div>
                        <div><small>${__("Net à payer (virements)")}</small><b>${dt(t.a_payer)}</b></div>
                    </div></div>
                <div class="cp-bloc"><div class="cp-titre">👥 ${__("Fiches du mois")}</div>
                    ${lignes ? `<table class="cp-table"><thead><tr><th>${__("Employé")}</th><th class="n">${__("Jours")}</th><th class="n">${__("Brut")}</th>
                        <th class="n">${__("Retenues")}</th><th class="n">${__("Net à payer")}</th><th>${__("Statut")}</th><th></th></tr></thead><tbody>${lignes}</tbody></table>`
                        : `<div class="cp-petit">${__("Aucun employé paramétré pour ce mois.")}</div>`}</div>
                ${a_param ? `<div class="cp-bloc"><div class="cp-titre">⚙️ ${__("À compléter pour avoir des fiches")}</div>${a_param}</div>` : ""}
                ${sans ? `<div class="cp-bloc"><div class="cp-titre" style="font-size:13px">${__("Sans contrat de paie (aucune fiche)")}</div><div class="cp-petit">${sans}</div></div>` : ""}
                <div class="cp-petit" style="margin-bottom:12px">${__("Contrat, date de début, salaire, matricule, N° CNSS : fiche Employé, onglet « Paie ». Une fiche validée ne se modifie plus ; elle s’annule depuis la fiche (↗).")}</div>`);
            const aller = (m) => { if (m) { this.mois = m; this.charger(); } };
            const liste = c.mois_possibles || [];
            this.$el.find(".cp-mois").on("change", (e) => aller(e.currentTarget.value));
            this.$el.find(".cp-prec").on("click", () => aller(liste[i - 1]));
            this.$el.find(".cp-suiv").on("click", () => aller(liste[i + 1]));
            this.$el.find(".cp-recalculer").on("click", () => this.recalculer());
            this.$el.find(".cp-valider").on("click", () => this.valider());
            this.$el.find(".cp-modifier").on("click", (e) => this.modifier($(e.currentTarget).attr("data-fiche")));
        }

        recalculer() {
            frappe.call({ method: API + ".recalculer", freeze: true, freeze_message: __("Calcul des fiches…") }).then((r) => {
                const v = Object.keys(r.message || {}).length;
                frappe.show_alert({ message: v ? __("Fiches à jour ; {0} employé(s) ont eu des fiches validées.", [v]) : __("Fiches à jour."), indicator: "green" });
                this.charger();
            });
        }

        valider() {
            frappe.confirm(__("Valider tout de suite les {0} fiche(s) en brouillon de {1} ? Chaque employé est prévenu et la voit dans son espace ; elles ne se modifient plus.",
                [this.c.brouillons, esc(this.c.libelle)]), () => {
                frappe.call({ method: API + ".valider_mois", args: { mois: this.c.mois }, freeze: true }).then((r) => {
                    frappe.show_alert({ message: __("Validées : {0}", [(r.message || []).map(esc).join(", ")]), indicator: "green" });
                    this.charger();
                });
            });
        }

        modifier(fiche) {
            const l = (this.c.lignes || []).find((x) => x.fiche === fiche) || {};
            const d = new frappe.ui.Dialog({
                title: __("{0} — {1}", [l.nom, this.c.libelle]),
                fields: [
                    { fieldtype: "Float", fieldname: "absences", label: __("Jours d’absence non payés (sur 26)"), default: l.absences || 0 },
                    { fieldtype: "Currency", fieldname: "prime", label: __("Prime exceptionnelle (soumise à CNSS et IRPP)"), default: l.prime || 0 },
                    { fieldtype: "Currency", fieldname: "avance", label: __("Avance sur salaire (retirée du net)"), default: l.avance || 0 },
                ],
                primary_action_label: __("Recalculer la fiche"),
                primary_action: (v) => {
                    frappe.call({ method: API + ".modifier_fiche", args: { fiche, absences: v.absences || 0, prime: v.prime || 0, avance: v.avance || 0 }, freeze: true })
                        .then((r) => {
                            d.hide();
                            frappe.show_alert({ message: __("Net à payer : {0} DT", [dt((r.message || {}).a_payer)]), indicator: "green" });
                            this.charger();
                        });
                },
            });
            d.show();
        }
    }
    window.ControlePaie = ControlePaie;
})();
