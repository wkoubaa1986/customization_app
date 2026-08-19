frappe.pages["caisse-journaliere"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({
    parent: wrapper,
    title: "Rapport Caisse Journalière",
    single_column: true,
  });
  $(wrapper).find(".layout-main-section").html(
    frappe.render_template("caisse_journaliere", {})
  );
  new RapportCaisseJournaliere(wrapper);
};

const RCJ_MODE = {
  "Espèces":                   { bg: "#d4edda", fg: "#155724" },
  "Chèque":                    { bg: "#cce5ff", fg: "#004085" },
  "Virement":                  { bg: "#d1f2f0", fg: "#0c5460" },
  "Carte de crédit":           { bg: "#dfe2ff", fg: "#2b2f77" },
  "Traite bancaire LC":        { bg: "#e2d4f0", fg: "#4a235a" },
  "Dette non payée":           { bg: "#f8d7da", fg: "#721c24" },
  "Retenue a la source vente": { bg: "#fff3cd", fg: "#856404" },
  "Perte de paiement":         { bg: "#f5c6cb", fg: "#491217" },
};
const RCJ_KPI_BG = {
  "Espèces": "#2e9e5b", "Chèque": "#2b6cb0", "Virement": "#12908a", "Carte de crédit": "#4b52b0",
  "Traite bancaire LC": "#7a4bb0", "Dette non payée": "#c0392b",
  "Retenue a la source vente": "#b8901f", "Perte de paiement": "#7b241c",
};
const RCJ_STATUS = {
  "Brouillon":            { bg: "#fff3cd", fg: "#856404" },
  "À facturer":           { bg: "#e1ecff", fg: "#2b4c7e" },
  "À livrer":             { bg: "#e1ecff", fg: "#2b4c7e" },
  "À livrer et facturer": { bg: "#e1ecff", fg: "#2b4c7e" },
  "Terminé":              { bg: "#d4edda", fg: "#155724" },
  "Fermé":                { bg: "#e2e3e5", fg: "#41464b" },
  "Annulé":               { bg: "#f8d7da", fg: "#721c24" },
  "En attente":           { bg: "#ffe5d0", fg: "#8a4b1f" },
};
const RCJ_TASK = {
  "Completed": { bg: "#d4edda", fg: "#155724" },
  "Open":      { bg: "#f8d7da", fg: "#a01722" },
  "Cancelled": { bg: "#e2e3e5", fg: "#41464b" },
};
const RCJ_MODE_ORDER = [
  "Espèces", "Chèque", "Virement", "Carte de crédit",
  "Traite bancaire LC", "Dette non payée", "Retenue a la source vente", "Perte de paiement",
];

class RapportCaisseJournaliere {
  constructor(wrapper) {
    this.wrapper = wrapper;
    // Encaissement des anciennes dettes : le dialogue fabrique un « Encaissement
    // Paiement » et laisse les scripts maison faire (allocation FIFO, échéanciers,
    // reliquat de dette, création du paiement).
    wrapper.page.add_inner_button(__("Encaissement dettes"), () => rcj_encaissement_dettes());
    this._data = null;
    this._all_collapsed = false;
    this._init_defaults();
    this._bind();
    this._fetch();
  }

  _init_defaults() {
    // Au premier chargement : aujourd'hui. L'utilisateur peut ensuite changer et actualiser.
    const today = frappe.datetime.get_today();
    $("#rcj-d1").val(today);
    $("#rcj-d2").val(today);
  }

  _bind() {
    $("#rcj-refresh").on("click", () => this._fetch());
    $("#rcj-toggle-all").on("click", () => this._toggle_all());
    $("#rcj-chart-btn").on("click", () => this._toggle_chart());
    $("#rcj-chart-close").on("click", () => $("#rcj-chart-section").hide());

    const $emp = $("#rcj-employees");
    // collapse employee
    $emp.on("click", ".rcj-emp-head", (e) => {
      $(e.currentTarget).closest(".rcj-emp").toggleClass("collapsed");
    });
    // toggle order detail
    $emp.on("click", ".rcj-more", (e) => {
      e.stopPropagation();
      const $tr = $(e.currentTarget).closest("tr.rcj-order");
      const $det = $tr.nextAll("tr.rcj-detail").first();
      const shown = $det.is(":visible");
      $det.toggle(!shown);
      $(e.currentTarget).text(shown ? "▸ détail" : "▾ masquer");
    });

    // open document (Sales Order / Tache) in a modal; refresh report on close/save
    $(this.wrapper).on("click", ".rcj-doc-link", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const $a = $(e.currentTarget);
      this._open_doc_modal($a.attr("data-dt"), $a.attr("data-dn"));
    });
  }

  // ── Modale document (iframe du vrai formulaire) ─────────────────────
  _open_doc_modal(doctype, name) {
    if (!doctype || !name) return;
    const slug = frappe.router.slug ? frappe.router.slug(doctype) : doctype.toLowerCase().replace(/ /g, "-");
    const url = `/app/${slug}/${encodeURIComponent(name)}`;

    const d = new frappe.ui.Dialog({
      title: `${doctype} — ${name}`,
      size: "extra-large",
      primary_action_label: "Fermer & actualiser",
      primary_action: () => d.hide(),
    });
    d.$body.html(
      `<iframe src="${url}" style="width:100%; height:78vh; border:0; border-radius:6px;"></iframe>`
    );

    let refreshed = false;
    let poll = null;
    const cleanup = () => { if (poll) { clearInterval(poll); poll = null; } };

    d.onhide = () => {
      cleanup();
      if (!refreshed) { refreshed = true; this._fetch(); }
    };
    d.show();
    d.$wrapper.find(".modal-dialog").css("max-width", "95vw");

    // Bouton "Fermer & actualiser" : actif seulement quand le document est enregistré (pas "Non Sauvegardé").
    const $btn = d.get_primary_btn();
    const set_btn = (enabled) => {
      $btn.prop("disabled", !enabled)
          .css({ opacity: enabled ? 1 : 0.45, cursor: enabled ? "pointer" : "not-allowed" })
          .attr("title", enabled ? "" : "Enregistrez (ou annulez) les modifications avant de fermer");
    };
    set_btn(false);

    // Surveille l'iframe (même origine) : hook after_save + état "dirty" en continu.
    const iframe = d.$body.find("iframe")[0];
    let hooked = false;
    poll = setInterval(() => {
      try {
        const w = iframe.contentWindow;
        if (w && w.frappe && w.cur_frm) {
          if (!hooked && w.frappe.ui && w.frappe.ui.form) {
            w.frappe.ui.form.on(doctype, "after_save", () => setTimeout(() => d.hide(), 500));
            hooked = true;
          }
          const dirty = w.cur_frm.is_dirty
            ? w.cur_frm.is_dirty()
            : !!(w.cur_frm.doc && w.cur_frm.doc.__unsaved);
          set_btn(!dirty);
        } else {
          set_btn(false);
        }
      } catch (err) { /* iframe pas prête */ }
    }, 400);
  }

  async _fetch() {
    const d1 = $("#rcj-d1").val(), d2 = $("#rcj-d2").val();
    if (!d1 || !d2) { frappe.msgprint("Veuillez saisir les dates."); return; }
    this._loading(true);
    try {
      const r = await frappe.call({
        method: "customization_app.rapport_caisse_journaliere.get_data",
        args: { d1, d2 },
      });
      this._data = r.message || {};
      this._render();
    } catch (e) {
      frappe.msgprint({ title: "Erreur", message: String(e), indicator: "red" });
    } finally {
      this._loading(false);
    }
  }

  _render() {
    this._render_warn_banner();
    this._render_kpis();
    this._render_employees();
    this._render_recap();
    this._render_anciens();
    if ($("#rcj-chart-section").is(":visible")) this._render_chart();
  }

  _render_warn_banner() {
    let orders = 0, count = 0;
    (this._data.employees || []).forEach(emp => {
      (emp.orders || []).forEach(o => {
        if (o.warnings && o.warnings.length) { orders += 1; count += o.warnings.length; }
      });
    });
    const $b = $("#rcj-warn-banner").empty();
    if (orders > 0) {
      $b.html(`<div class="rcj-warn-banner">⚠️ ${orders} commande(s) validée(s) avec incohérence (${count} avertissement(s)) : total des paiements ≠ Total TTC, ou total des BL ≠ total des factures. Repérez le ⚠️ dans le tableau et ouvrez « ▸ détail ».</div>`);
    }
  }

  // ── KPIs ────────────────────────────────────────────────────────────
  _render_kpis() {
    const recap = this._data.recap || {};
    const modes = this._ordered_modes(recap.modes || []);
    const $k = $("#rcj-kpis").empty();
    $k.append(this._kpi_card("Total encaissé", recap.grand_total || 0, "#2b4c7e",
      (recap.par_employe || []).length + " employé(s)"));
    modes.forEach(m => {
      $k.append(this._kpi_card(m, (recap.par_mode || {})[m] || 0, RCJ_KPI_BG[m] || "#5b6472"));
    });
  }

  _kpi_card(label, value, bg, sub) {
    return `<div class="rcj-kpi" style="background:${bg};">
      <div class="lbl">${frappe.utils.escape_html(label)}</div>
      <div class="val">${this._fmt(value)}</div>
      <div class="sub">${sub ? frappe.utils.escape_html(sub) : "DT"}</div>
    </div>`;
  }

  // ── Employees ───────────────────────────────────────────────────────
  _render_employees() {
    const $c = $("#rcj-employees").empty();
    const emps = this._data.employees || [];
    if (!emps.length) {
      $c.html('<div class="rcj-muted" style="padding:20px; text-align:center;">Aucune donnée pour cette période.</div>');
      return;
    }
    emps.forEach(emp => $c.append(this._emp_block(emp)));
  }

  _emp_block(emp) {
    const modes = this._ordered_modes(Object.keys(emp.totaux_par_mode || {}));
    const mtags = modes
      .filter(m => Math.abs(emp.totaux_par_mode[m]) > 0.001)
      .map(m => `<span class="mtag">${frappe.utils.escape_html(m)}: ${this._fmt(emp.totaux_par_mode[m])}</span>`)
      .join("");

    const rows = (emp.orders || []).map(o => this._order_rows(o)).join("");

    return `<div class="rcj-emp">
      <div class="rcj-emp-head">
        <span class="caret">▾</span>
        <span class="name">${frappe.utils.escape_html(emp.employe || "—")}</span>
        <span class="count">${emp.nb_commandes} cmd · ${this._fmt(emp.total)} DT</span>
        <span class="modes">${mtags}</span>
      </div>
      <div class="rcj-emp-body">
        <table class="rcj-tbl">
          <thead><tr>
            <th>Commande</th><th>Client</th><th>Date</th><th class="num">Total TTC</th>
            <th>Statut</th><th>Mode paiement</th><th>Info paiement</th>
            <th>Intervention</th><th>Statut tâche</th><th></th>
          </tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>`;
  }

  _order_rows(o) {
    const modes = [...new Set((o.payments || []).map(p => p.mode).filter(Boolean))];
    const modeChips = modes.map(m => this._chip(m, RCJ_MODE[m])).join(" ") || '<span class="rcj-empty">—</span>';
    const infos = [...new Set((o.payments || []).map(p => p.reference_no).filter(Boolean))];
    const infoTxt = infos.length ? frappe.utils.escape_html(infos.join(" · ")) : '<span class="rcj-empty">—</span>';

    const statut = o.status ? this._badge(o.status, RCJ_STATUS[o.status]) : "";
    const tstat = o.tache_status ? this._badge(o.tache_status, RCJ_TASK[o.tache_status]) : '<span class="rcj-empty">—</span>';

    const warns = o.warnings || [];
    const warnBadge = warns.length
      ? `<span class="rcj-warn" title="${frappe.utils.escape_html(warns.map(w => w.message).join(" | "))}">⚠️</span>`
      : "";

    const cls = ["rcj-order"];
    if (o.task_open) cls.push("open-task");
    if (o.alert) cls.push("alert-" + o.alert);
    if (warns.length) cls.push("has-warning");

    const main = `<tr class="${cls.join(" ")}">
      <td>${warnBadge}${this._link("Sales Order", o.sales_order, true)}</td>
      <td>${o.customer ? this._link("Customer", o.customer) : '<span class="rcj-empty">—</span>'}</td>
      <td class="rcj-muted">${o.date || ""}</td>
      <td class="num">${this._fmt(o.grand_total)}</td>
      <td>${statut}</td>
      <td>${modeChips}</td>
      <td style="font-size:11px;">${infoTxt}</td>
      <td>${o.intervention ? frappe.utils.escape_html(o.intervention) : '<span class="rcj-empty">—</span>'}</td>
      <td>${tstat}</td>
      <td><span class="rcj-more">▸ détail</span></td>
    </tr>`;

    const warnRow = warns.length
      ? `<tr class="rcj-order-warn"><td colspan="10"><div class="rcj-warn-line">${warns.map(w => `⚠️ ${frappe.utils.escape_html(w.message)}`).join("<br>")}</div></td></tr>`
      : "";

    const detail = `<tr class="rcj-detail" style="display:none;"><td colspan="10">
      <div class="rcj-detail-inner">
        <div>
          <h6>💳 Paiements — payé ${this._fmt(o.total_paid)} / ${this._fmt(o.grand_total)} DT</h6>
          ${this._payments_table(o.payments)}
        </div>
        <div>
          <h6>🚚 Bons de livraison</h6>
          ${this._doc_list(o.delivery_notes, "Delivery Note")}
          <h6 style="margin-top:10px;">🧾 Factures</h6>
          ${this._doc_list(o.sales_invoices, "Sales Invoice")}
        </div>
        <div>
          <h6>🛠️ Tâche</h6>
          ${this._task_detail(o)}
        </div>
      </div>
    </td></tr>`;

    return main + warnRow + detail;
  }

  _payments_table(pays) {
    if (!pays || !pays.length) return '<div class="rcj-empty">Aucun paiement</div>';
    const rows = pays.map(p => `<tr>
      <td>${this._link("Payment Entry", p.name, true)}</td>
      <td class="rcj-muted">${p.date || ""}</td>
      <td>${this._chip(p.mode || "?", RCJ_MODE[p.mode])}</td>
      <td class="num">${this._fmt(p.amount)}</td>
      <td style="font-size:10.5px;">${frappe.utils.escape_html(p.reference_no || "")}</td>
    </tr>`).join("");
    return `<table class="rcj-sub">
      <thead><tr><th>Réf.</th><th>Date</th><th>Mode</th><th class="num">Montant</th><th>Info</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
  }

  _doc_list(list, doctype) {
    if (!list || !list.length) return '<div class="rcj-empty">Aucun</div>';
    return list.map(d =>
      `<div style="display:flex; justify-content:space-between; gap:8px; padding:2px 0;">
        <span>${this._link(doctype, d.name, true)}</span>
        <span class="num" style="color:#555;">${this._fmt(d.grand_total)}</span>
      </div>`).join("");
  }

  _task_detail(o) {
    if (!o.tache_reference && !o.tache_employee) return '<div class="rcj-empty">Aucune tâche liée</div>';
    let html = "";
    if (o.tache_reference) html += `<div style="padding:2px 0;">Réf : ${this._link("Tache de travail", o.tache_reference, true)}</div>`;
    if (o.tache_employee) html += `<div style="padding:2px 0;">Employé : ${frappe.utils.escape_html(o.tache_employee)}</div>`;
    if (o.intervention) html += `<div style="padding:2px 0;">Intervention : ${frappe.utils.escape_html(o.intervention)}</div>`;
    return html;
  }

  // ── Recap ───────────────────────────────────────────────────────────
  _render_recap() {
    const recap = this._data.recap || {};
    const modes = this._ordered_modes(recap.modes || []);
    const $c = $("#rcj-recap").empty();
    const list = recap.par_employe || [];
    if (!list.length) {
      $c.html('<div class="rcj-muted" style="padding:16px; text-align:center;">Aucun encaissement.</div>');
      return;
    }

    const has_anciens = ((this._data.anciens || {}).paiements || []).length > 0;
    const head = `<tr><th>Employé</th>${modes.map(m => `<th>${frappe.utils.escape_html(m)}</th>`).join("")}<th>Total</th></tr>`;
    const body = list.map(e => `<tr>
      <td>${frappe.utils.escape_html(e.employe)}
        ${e.anciens ? `<div style="font-size:10.5px;color:#8a4b1f;">dont anciennes cmd : ${this._fmt(e.anciens)}</div>` : ""}</td>
      ${modes.map(m => `<td>${this._recap_cell(e.par_mode[m], m)}</td>`).join("")}
      <td style="font-weight:700;">${this._fmt(e.total)}</td>
    </tr>`).join("");
    const totals = `<tr class="total-row">
      <td>TOTAL</td>
      ${modes.map(m => `<td>${this._fmt((recap.par_mode || {})[m] || 0)}</td>`).join("")}
      <td>${this._fmt(recap.grand_total || 0)}</td>
    </tr>`;

    $c.html(`<div class="rcj-recap">
      <div class="rcj-recap-head">🧾 Encaissements par employé et par mode${has_anciens ? " — paiements sur anciennes commandes inclus" : ""}</div>
      <div style="overflow-x:auto;">
        <table class="rcj-recap-tbl">
          <thead>${head}</thead>
          <tbody>${body}${totals}</tbody>
        </table>
      </div>
    </div>`);
  }

  // ── Paiements encaissés sur la période mais hors commandes du rapport
  //    (= règlements d'anciennes commandes) — données de get_data().anciens
  _render_anciens() {
    const esc = frappe.utils.escape_html;
    const anciens = this._data.anciens || {};
    const rows = anciens.paiements || [];
    const $c = $("#rcj-anciens").empty();
    if (!rows.length) return;

    const par_mode = Object.entries(anciens.par_mode || {})
      .map(([m, v]) => `${esc(m)} : <b>${this._fmt(v)}</b>`).join(" · ");

    const body = rows.map(p => {
      const mode_style = RCJ_MODE[p.mode] || { bg: "#eee", fg: "#333" };
      const pieces = (p.pieces || []).length
        ? p.pieces.map(x =>
            `<a href="/app/${frappe.router.slug(x.doctype)}/${encodeURIComponent(x.name)}" target="_blank">${esc(x.name)}</a>` +
            (x.date ? ` <span class="rcj-muted">(${frappe.datetime.str_to_user(x.date)})</span>` : "")
          ).join("<br>")
        : '<span class="rcj-empty">aucune pièce liée</span>';
      return `<tr>
        <td style="white-space:nowrap">${p.date ? frappe.datetime.str_to_user(p.date) : "—"}
          ${p.antidate ? `<br><span style="color:#c0392b;font-size:11px">⚠ saisi le ${frappe.datetime.str_to_user(p.creation_date)}</span>` : ""}</td>
        <td>${esc(p.saisi_par || "")}</td>
        <td>${esc(p.customer_name || "")}</td>
        <td><span class="rcj-badge" style="background:${mode_style.bg};color:${mode_style.fg}">${esc(p.mode)}</span></td>
        <td>${esc(p.compte || "")}</td>
        <td style="text-align:right;font-weight:700">${this._fmt(p.amount)}</td>
        <td>${esc(p.reference_no || "")}</td>
        <td><a href="/app/payment-entry/${encodeURIComponent(p.name)}" target="_blank">${esc(p.name)}</a></td>
        <td>${pieces}</td>
      </tr>`;
    }).join("");

    $c.html(`<div class="rcj-recap" style="border-color:#c98a2b;">
      <div class="rcj-recap-head" style="background:linear-gradient(90deg,#8a4b1f,#c96f2b);">
        💰 Paiements sur anciennes commandes — encaissés sur la période (${rows.length})
        <span style="float:right">Total : ${this._fmt(anciens.total || 0)}</span>
      </div>
      <div style="padding:8px 16px;font-size:12.5px;background:#fff6ec;color:#7a4b10;">
        Paiements entrés en caisse (Espèces / Chèques / Traite bancaire) pendant la période,
        mais liés à aucune commande du rapport — à compter dans la vérification. ${par_mode}
      </div>
      <div style="overflow-x:auto;">
        <table class="rcj-recap-tbl">
          <thead><tr>
            <th style="text-align:left">Date</th><th style="text-align:left">Saisi par</th>
            <th style="text-align:left">Client</th><th style="text-align:left">Mode</th>
            <th style="text-align:left">Compte</th><th>Montant</th>
            <th style="text-align:left">Référence</th><th style="text-align:left">Paiement</th>
            <th style="text-align:left">Ancienne(s) pièce(s)</th>
          </tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </div>`);
  }

  _recap_cell(v, mode) {
    v = parseFloat(v) || 0;
    if (!v) return '<span class="rcj-empty">—</span>';
    const c = RCJ_MODE[mode] || { fg: "#333" };
    return `<span style="color:${c.fg}; font-weight:600;">${this._fmt(v)}</span>`;
  }

  // ── Chart ───────────────────────────────────────────────────────────
  _toggle_chart() {
    const $s = $("#rcj-chart-section");
    if ($s.is(":visible")) { $s.hide(); return; }
    $s.show();
    this._render_chart();
  }

  _render_chart() {
    const recap = this._data.recap || {};
    const modes = this._ordered_modes(recap.modes || []);
    const list = recap.par_employe || [];
    if (!list.length) { $("#rcj-chart").html('<div class="rcj-muted">Aucune donnée.</div>'); return; }
    const labels = list.map(e => e.employe);
    const datasets = modes.map(m => ({
      name: m,
      values: list.map(e => parseFloat(e.par_mode[m]) || 0),
      chartType: "bar",
    }));
    $("#rcj-chart").empty();
    new frappe.Chart("#rcj-chart", {
      data: { labels, datasets },
      type: "bar",
      height: 300,
      barOptions: { stacked: 1 },
      colors: modes.map(m => RCJ_KPI_BG[m] || "#888"),
      tooltipOptions: { formatTooltipY: v => this._fmt(v) + " DT" },
      axisOptions: { shortenYAxisNumbers: 1 },
    });
  }

  // ── Interactions ────────────────────────────────────────────────────
  _toggle_all() {
    this._all_collapsed = !this._all_collapsed;
    $(".rcj-emp").toggleClass("collapsed", this._all_collapsed);
    $("#rcj-toggle-all").text(this._all_collapsed ? "Tout déplier" : "Tout replier");
  }

  // ── Helpers ─────────────────────────────────────────────────────────
  _ordered_modes(modes) {
    const set = new Set(modes);
    const ordered = RCJ_MODE_ORDER.filter(m => set.has(m));
    modes.forEach(m => { if (ordered.indexOf(m) === -1) ordered.push(m); });
    return ordered;
  }

  _chip(text, c) {
    c = c || { bg: "#eef1f5", fg: "#555" };
    return `<span class="rcj-chip" style="background:${c.bg}; color:${c.fg};">${frappe.utils.escape_html(text)}</span>`;
  }
  _badge(text, c) {
    c = c || { bg: "#eef1f5", fg: "#555" };
    return `<span class="rcj-badge" style="background:${c.bg}; color:${c.fg};">${frappe.utils.escape_html(text)}</span>`;
  }
  _link(doctype, name, modal) {
    if (!name) return "";
    if (modal) {
      return `<a href="#" class="rcj-doc-link" data-dt="${frappe.utils.escape_html(doctype)}" data-dn="${frappe.utils.escape_html(name)}" style="font-weight:600;">${frappe.utils.escape_html(name)} 🔍</a>`;
    }
    const slug = frappe.router.slug ? frappe.router.slug(doctype) : doctype.toLowerCase().replace(/ /g, "-");
    return `<a href="/app/${slug}/${encodeURIComponent(name)}" target="_blank" style="font-weight:600;">${frappe.utils.escape_html(name)}</a>`;
  }
  _fmt(val) {
    const n = parseFloat(val) || 0;
    return n.toLocaleString("fr-TN", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  }
  _loading(show) {
    $("#rcj-loading").css("display", show ? "flex" : "none");
  }
}

// ── Encaissement des anciennes dettes ────────────────────────────────────────
// Le serveur (customization_app.caisse_encaissement_dettes) plafonne le montant à
// la somme des dettes et passe par l'Outil d'encaissement : on ne montre ici que
// la recherche client (nom ou téléphone), les dettes avec leur commande, et
// l'allocation à confirmer avant soumission.
function rcj_encaissement_dettes() {
  const API = "customization_app.caisse_encaissement_dettes";
  let etat = { total: 0, banques: [] };

  const d = new frappe.ui.Dialog({
    title: __("Encaissement de dettes"),
    size: "large",
    fields: [
      {
        fieldtype: "Link", fieldname: "client", options: "Customer",
        label: __("Client (nom ou n° de téléphone)"), reqd: 1,
        get_query: () => ({ query: API + ".recherche_client" }),
        onchange: () => charger(),
      },
      { fieldtype: "HTML", fieldname: "liste" },
      { fieldtype: "Section Break" },
      { fieldtype: "Currency", fieldname: "montant", label: __("Montant reçu"), reqd: 1 },
      {
        fieldtype: "Select", fieldname: "mode", label: __("Mode d'encaissement"),
        options: "Espèces\nChèque", default: "Espèces", reqd: 1,
      },
      { fieldtype: "Column Break" },
      {
        fieldtype: "Data", fieldname: "n_cheque", label: __("N° de chèque"),
        depends_on: 'eval:doc.mode=="Chèque"',
        mandatory_depends_on: 'eval:doc.mode=="Chèque"',
      },
      {
        fieldtype: "Select", fieldname: "banque", label: __("Banque"),
        depends_on: 'eval:doc.mode=="Chèque"',
        mandatory_depends_on: 'eval:doc.mode=="Chèque"',
      },
    ],
    primary_action_label: __("Encaisser"),
    primary_action(v) {
      if (!etat.total) {
        frappe.msgprint(__("Ce client n'a aucune dette encaissable."));
        return;
      }
      if (v.montant > etat.total + 0.001) {
        frappe.msgprint(__("Le montant dépasse la somme des dettes ({0}).",
          [format_currency(etat.total, "TND")]));
        return;
      }
      frappe.call({
        method: API + ".encaisser",
        args: { client: v.client, montant: v.montant, mode: v.mode,
                n_cheque: v.n_cheque, banque: v.banque },
        freeze: true, freeze_message: __("Calcul de l'allocation…"),
        callback: (r) => confirmer(r.message, v),
      });
    },
  });

  function charger() {
    const client = d.get_value("client");
    d.fields_dict.liste.$wrapper.empty();
    etat = { total: 0, banques: etat.banques };
    if (!client) return;
    frappe.call({
      method: API + ".dettes_client", args: { client },
      callback: (r) => {
        const m = r.message || { dettes: [], total: 0, banques: [] };
        etat = m;
        d.set_df_property("banque", "options", [""].concat(m.banques).join("\n"));
        const lignes = (m.dettes || []).map((x) => `
          <tr><td>${frappe.utils.escape_html(x.paiement)}</td>
              <td>${x.commande ? frappe.utils.escape_html(x.commande) : "—"}</td>
              <td>${frappe.datetime.str_to_user(x.date)}</td>
              <td style="text-align:right">${format_currency(x.montant, "TND")}</td></tr>`).join("");
        d.fields_dict.liste.$wrapper.html(m.dettes.length ? `
          <table class="table table-bordered" style="margin-top:8px;font-size:12px">
            <thead><tr><th>${__("Dette")}</th><th>${__("Commande")}</th>
                       <th>${__("Date")}</th><th style="text-align:right">${__("Montant")}</th></tr></thead>
            <tbody>${lignes}</tbody>
            <tfoot><tr><th colspan="3">${__("Total des dettes")}</th>
                       <th style="text-align:right">${format_currency(m.total, "TND")}</th></tr></tfoot>
          </table>` : `<div class="text-muted" style="margin-top:8px">${
            __("Aucune dette encaissable pour ce client.")}</div>`);
        d.set_value("montant", m.total);
      },
    });
  }

  function confirmer(res, v) {
    d.hide();
    const lignes = (res.allocation || []).map((a) => `
      <tr><td>${frappe.utils.escape_html(a.paiement)}</td>
          <td>${a.commande ? frappe.utils.escape_html(a.commande) : "—"}</td>
          <td style="text-align:right">${format_currency(a.montant, "TND")}</td></tr>`).join("");
    const corps = `
      <p>${__("Paiement {0} de {1} — répartition sur les dettes :",
        [v.mode, format_currency(v.montant, "TND")])}</p>
      <table class="table table-bordered" style="font-size:12px">
        <thead><tr><th>${__("Dette consommée")}</th><th>${__("Commande")}</th>
                   <th style="text-align:right">${__("Montant")}</th></tr></thead>
        <tbody>${lignes}</tbody></table>
      ${res.restant > 0.001 ? `<p class="text-muted">${
        __("Reliquat non couvert : {0} — une dette sera recréée sur la commande concernée.",
          [format_currency(res.restant, "TND")])}</p>` : ""}`;
    frappe.confirm(corps, () => {
      frappe.call({
        method: API + ".valider", args: { name: res.name },
        freeze: true, freeze_message: __("Encaissement…"),
        callback: () => frappe.show_alert(
          { message: __("Dettes encaissées ({0}).", [res.name]), indicator: "green" }),
      });
    }, () => {
      // Refus : le brouillon ne doit pas rester, il fausserait le prochain calcul.
      frappe.call({ method: API + ".abandonner", args: { name: res.name } });
    });
  }

  d.show();
}
