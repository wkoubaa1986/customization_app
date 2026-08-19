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
    $("#rcj-employe").on("change", () => this._fetch());
    // Encaissement des anciennes dettes : le dialogue fabrique un « Encaissement
    // Paiement » et laisse les scripts maison faire (allocation FIFO, échéanciers,
    // reliquat de dette, création du paiement).
    $("#rcj-btn-dettes").on("click", () => rcj_encaissement_dettes(this));
    $("#rcj-btn-depense").on("click", () => rcj_depense(this));
    $("#rcj-btn-cloture").on("click", () => rcj_cloture(this));
    // Exclure / réintégrer un paiement d'ancienne commande (correction de saisie).
    $(this.wrapper).on("click", ".rcj-exclure", (e) => {
      const $b = $(e.currentTarget);
      frappe.call({
        method: "customization_app.rapport_caisse_journaliere.exclure_ancien_paiement",
        args: { pe: $b.attr("data-pe"), exclure: $b.attr("data-exclure") },
        freeze: true,
        callback: () => this._fetch(),
      });
    });
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
      const args = { d1, d2 };
      if (this._charge_une_fois) {
        args.employe = $("#rcj-employe").val() || "";
      }
      const r = await frappe.call({
        method: "customization_app.rapport_caisse_journaliere.get_data",
        args,
      });
      this._charge_une_fois = true;
      this._data = r.message || {};
      this._peupler_filtre_employe();
      this._render();
    } catch (e) {
      frappe.msgprint({ title: "Erreur", message: String(e), indicator: "red" });
    } finally {
      this._loading(false);
    }
  }

  _peupler_filtre_employe() {
    // La liste vient du serveur (tous les noms, même quand on filtre) ; la sélection
    // courante est conservée d'un rafraîchissement à l'autre.
    const $sel = $("#rcj-employe");
    const courant = this._data.employe || $sel.val() || "";
    const noms = this._data.employes || [];
    if ($sel.data("peuple") !== noms.join("|")) {
      $sel.data("peuple", noms.join("|"));
      $sel.empty().append(`<option value="">${__("Tous les employés")}</option>`);
      noms.forEach((n) => $sel.append(
        `<option value="${frappe.utils.escape_html(n)}">${frappe.utils.escape_html(n)}</option>`));
    }
    $sel.val(courant);
    $("#rcj-caisse-nom").text(courant || __("Tous les employés"));
  }

  _render() {
    this._render_warn_banner();
    this._render_kpis();
    this._render_employees();
    this._render_recap();
    this._render_anciens();
    this._render_depenses();
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
    // La caisse dépense aussi : total des dépenses saisies, et solde espèces NET
    // (espèces encaissées − dépenses en espèces) — le liquide réellement en caisse.
    const dep = this._data.depenses || { total: 0, par_mode: {}, lignes: [] };
    if (dep.lignes.length) {
      $k.append(this._kpi_card("Dépenses caisse", dep.total, "#7b241c",
        dep.lignes.length + " dépense(s)"));
    }
    const esp_in = (recap.par_mode || {})["Espèces"] || 0;
    const esp_out = (dep.par_mode || {})["Espèces"] || 0;
    if (esp_in || esp_out) {
      $k.append(this._kpi_card("Solde espèces (net)", esp_in - esp_out, "#1d6f42",
        this._fmt(esp_in) + " reçus − " + this._fmt(esp_out) + " dépensés"));
    }
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
    const du_jour = (o.payments || []).filter(p => !p.hors_periode);
    const modes = [...new Set(du_jour.map(p => p.mode).filter(Boolean))];
    let modeChips = modes.map(m => this._chip(m, RCJ_MODE[m])).join(" ") || '<span class="rcj-empty">—</span>';
    const av = o.avance_anterieure || {};
    if (av.total) {
      modeChips += ` <span class="rcj-chip" style="background:#fff3cd;color:#8a6d1f"
        title="${__("Avance reçue avant la période — n'entre pas dans la caisse du jour")}">⏪ ${
        __("Avance")} ${this._fmt(av.total)} (${frappe.utils.escape_html((av.modes || []).join(", "))})</span>`;
    }
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
    // Une AVANCE reçue hors période reste visible (elle explique le solde de la
    // commande) mais grisée et marquée : elle ne compte pas dans la caisse du jour.
    const rows = pays.map(p => `<tr${p.hors_periode ? ' style="opacity:.55"' : ""}>
      <td>${this._link("Payment Entry", p.name, true)}</td>
      <td class="rcj-muted">${p.date || ""}${p.hors_periode
        ? ' <span style="color:#b9770e;font-size:10px;font-weight:700">hors période</span>' : ""}${p.antidate
        ? ` <span style="color:#c0392b;font-size:10px;font-weight:700">⚠ saisi le ${
            frappe.datetime.str_to_user(p.creation_date)}</span>` : ""}</td>
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
      const exclu = !!p.exclu;
      const pieces = (p.pieces || []).length
        ? p.pieces.map(x =>
            `<a href="/app/${frappe.router.slug(x.doctype)}/${encodeURIComponent(x.name)}" target="_blank">${esc(x.name)}</a>` +
            (x.date ? ` <span class="rcj-muted">(${frappe.datetime.str_to_user(x.date)})</span>` : "")
          ).join("<br>")
        : '<span class="rcj-empty">aucune pièce liée</span>';
      return `<tr${exclu ? ' style="opacity:.5"' : ""}>
        <td style="white-space:nowrap">${p.date ? frappe.datetime.str_to_user(p.date) : "—"}
          ${p.antidate ? `<br><span style="color:#c0392b;font-size:11px">⚠ saisi le ${frappe.datetime.str_to_user(p.creation_date)}</span>` : ""}</td>
        <td>${esc(p.saisi_par || "")}</td>
        <td>${esc(p.customer_name || "")}</td>
        <td><span class="rcj-badge" style="background:${mode_style.bg};color:${mode_style.fg}">${esc(p.mode)}</span></td>
        <td>${esc(p.compte || "")}</td>
        <td style="text-align:right;font-weight:700">${this._fmt(p.amount)}</td>
        <td>${esc(p.reference_no || "")}</td>
        <td><a href="/app/payment-entry/${encodeURIComponent(p.name)}" target="_blank">${esc(p.name)}</a>${
          exclu ? '<br><span class="rcj-chip" style="background:#e2e3e5;color:#41464b">exclu de la caisse</span>' : ""}</td>
        <td>${pieces}</td>
        <td><button class="btn btn-xs ${exclu ? "btn-default" : "btn-warning"} rcj-exclure"
              data-pe="${esc(p.name)}" data-exclure="${exclu ? 0 : 1}"
              title="${exclu ? __("Réintégrer dans les totaux de la caisse")
                             : __("Écarter des totaux (correction de saisie)")}">${
              exclu ? __("Réintégrer") : __("Exclure")}</button></td>
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
            <th style="text-align:left">Ancienne(s) pièce(s)</th><th></th>
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
// Le serveur (customization_app.caisse_encaissement_dettes) construit l'allocation
// sur la SÉLECTION de l'employé (FIFO par date), plafonne le montant à la somme
// sélectionnée, exige la photo et un numéro à 7 chiffres pour un chèque, puis passe
// par l'Outil d'encaissement (échéanciers, reliquat, paiement).
function rcj_encaissement_dettes(rapport) {
  const API = "customization_app.caisse_encaissement_dettes";
  let etat = { total: 0, banques: [], dettes: [], photo: null, photo_nom: null };

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
        fieldtype: "Data", fieldname: "n_cheque", label: __("N° de chèque (7 chiffres)"),
        depends_on: 'eval:doc.mode=="Chèque"',
        mandatory_depends_on: 'eval:doc.mode=="Chèque"',
      },
      {
        fieldtype: "Select", fieldname: "banque", label: __("Banque"),
        depends_on: 'eval:doc.mode=="Chèque"',
        mandatory_depends_on: 'eval:doc.mode=="Chèque"',
      },
      { fieldtype: "HTML", fieldname: "photo_zone" },
    ],
    primary_action_label: __("Encaisser"),
    primary_action(v) {
      const choisies = selection();
      const total_sel = choisies.reduce((s, x) => s + x.montant, 0);
      if (!choisies.length) {
        frappe.msgprint(__("Sélectionnez au moins une dette."));
        return;
      }
      if (v.montant > total_sel + 0.001) {
        frappe.msgprint(__("Le montant dépasse la somme des dettes sélectionnées ({0}).",
          [format_currency(total_sel, "TND")]));
        return;
      }
      if (v.mode === "Chèque") {
        if (!/^\d{7}$/.test((v.n_cheque || "").trim())) {
          frappe.msgprint(__("Le numéro de chèque doit comporter exactement 7 chiffres."));
          return;
        }
        if (!etat.photo) {
          frappe.msgprint(__("Prenez la photo du chèque avant d'encaisser."));
          return;
        }
      }
      frappe.call({
        method: API + ".encaisser",
        args: { client: v.client, montant: v.montant, mode: v.mode,
                n_cheque: v.n_cheque, banque: v.banque,
                dettes: JSON.stringify(choisies.map((x) => x.paiement)),
                photo: v.mode === "Chèque" ? etat.photo : null,
                photo_nom: etat.photo_nom },
        freeze: true, freeze_message: __("Calcul de l'allocation…"),
        callback: (r) => confirmer(r.message, v),
      });
    },
  });

  function selection() {
    const cochees = [];
    d.fields_dict.liste.$wrapper.find("input.rcj-dette:checked").each(function () {
      const nom = $(this).attr("data-pe");
      const x = etat.dettes.find((dd) => dd.paiement === nom);
      if (x) cochees.push(x);
    });
    return cochees;
  }

  function maj_total_selection() {
    const t = selection().reduce((s, x) => s + x.montant, 0);
    d.fields_dict.liste.$wrapper.find(".rcj-total-sel")
      .text(format_currency(t, "TND"));
  }

  function lien_commande(x) {
    if (!x.commande) return "—";
    if (!x.commande_doctype) return frappe.utils.escape_html(x.commande);
    const slug = frappe.router.slug(x.commande_doctype);
    return `<a href="/app/${slug}/${encodeURIComponent(x.commande)}" target="_blank">${
      frappe.utils.escape_html(x.commande)}</a>`;
  }

  function charger() {
    const client = d.get_value("client");
    d.fields_dict.liste.$wrapper.empty();
    etat.dettes = []; etat.total = 0;
    if (!client) return;
    frappe.call({
      method: API + ".dettes_client", args: { client },
      callback: (r) => {
        const m = r.message || { dettes: [], total: 0, banques: [] };
        etat.dettes = m.dettes || []; etat.total = m.total || 0;
        if (m.banques && m.banques.length) {
          d.set_df_property("banque", "options", [""].concat(m.banques).join("\n"));
        }
        const lignes = etat.dettes.map((x) => `
          <tr><td style="text-align:center"><input type="checkbox" class="rcj-dette" checked
                     data-pe="${frappe.utils.escape_html(x.paiement)}"></td>
              <td>${frappe.utils.escape_html(x.paiement)}</td>
              <td>${lien_commande(x)}</td>
              <td style="text-align:right">${x.commande_ttc
                ? format_currency(x.commande_ttc, "TND") : "—"}</td>
              <td>${x.commande_date ? frappe.datetime.str_to_user(x.commande_date) : "—"}</td>
              <td>${frappe.datetime.str_to_user(x.date)}</td>
              <td style="text-align:right">${format_currency(x.montant, "TND")}</td></tr>`).join("");
        d.fields_dict.liste.$wrapper.html(etat.dettes.length ? `
          <div style="overflow-x:auto">
          <table class="table table-bordered" style="margin-top:8px;font-size:12px;min-width:640px">
            <thead><tr><th></th><th>${__("Dette")}</th><th>${__("Commande")}</th>
                       <th style="text-align:right">${__("TTC commande")}</th>
                       <th>${__("Date commande")}</th>
                       <th>${__("Date dette")}</th>
                       <th style="text-align:right">${__("Montant dû")}</th></tr></thead>
            <tbody>${lignes}</tbody>
            <tfoot>
              <tr><th colspan="6">${__("Total des dettes")}</th>
                  <th style="text-align:right">${format_currency(m.total, "TND")}</th></tr>
              <tr><th colspan="6">${__("Total sélectionné")}</th>
                  <th style="text-align:right" class="rcj-total-sel">${
                    format_currency(m.total, "TND")}</th></tr>
            </tfoot>
          </table></div>
          <div class="text-muted" style="font-size:11px">${
            __("Décochez une dette pour l'écarter — l'allocation suit la sélection (FIFO par date de commande).")}</div>`
          : `<div class="text-muted" style="margin-top:8px">${
            __("Aucune dette encaissable pour ce client.")}</div>`);
        d.fields_dict.liste.$wrapper.find("input.rcj-dette")
          .on("change", () => maj_total_selection());
        d.set_value("montant", m.total);
      },
    });
  }

  // Photo du chèque : caméra du téléphone (capture) ou fichier. Obligatoire pour un chèque.
  function poser_zone_photo() {
    const $z = d.fields_dict.photo_zone.$wrapper;
    $z.html(`
      <div class="rcj-photo" style="display:none;margin-top:4px">
        <button type="button" class="btn btn-default btn-sm rcj-photo-btn">📷 ${
          __("Photo du chèque")}</button>
        <span class="rcj-photo-nom text-muted" style="margin-left:8px"></span>
        <input type="file" accept="image/*" capture="environment" style="display:none">
      </div>`);
    const $bloc = $z.find(".rcj-photo");
    const $input = $z.find("input[type=file]");
    $z.find(".rcj-photo-btn").on("click", () => $input.trigger("click"));
    $input.on("change", function () {
      const f = this.files && this.files[0];
      if (!f) return;
      const lecteur = new FileReader();
      lecteur.onload = () => {
        etat.photo = lecteur.result;
        etat.photo_nom = f.name;
        $z.find(".rcj-photo-nom").text("✓ " + f.name);
      };
      lecteur.readAsDataURL(f);
    });
    const basculer = () => $bloc.toggle(d.get_value("mode") === "Chèque");
    d.fields_dict.mode.$input.on("change", basculer);
    basculer();
  }

  frappe.call({
    method: API + ".banques",
    callback: (r) => {
      etat.banques = r.message || [];
      d.set_df_property("banque", "options", [""].concat(etat.banques).join("\n"));
    },
  });

  function confirmer(res, v) {
    d.hide();
    const lignes = (res.allocation || []).map((a) => `
      <tr><td>${frappe.utils.escape_html(a.paiement)}</td>
          <td>${a.commande ? frappe.utils.escape_html(a.commande) : "—"}</td>
          <td style="text-align:right">${format_currency(a.montant, "TND")}</td>
          <td style="text-align:right">${format_currency(a.dette_totale, "TND")}</td></tr>`).join("");
    const corps = `
      <p>${__("Paiement {0} de {1} — répartition sur les dettes sélectionnées :",
        [v.mode, format_currency(v.montant, "TND")])}</p>
      <div style="overflow-x:auto"><table class="table table-bordered" style="font-size:12px">
        <thead><tr><th>${__("Dette consommée")}</th><th>${__("Commande")}</th>
                   <th style="text-align:right">${__("Encaissé")}</th>
                   <th style="text-align:right">${__("Dette totale")}</th></tr></thead>
        <tbody>${lignes}</tbody></table></div>
      ${res.restant > 0.001 ? `<p class="text-muted">${
        __("Reliquat non couvert : {0} — une dette sera recréée sur la commande concernée.",
          [format_currency(res.restant, "TND")])}</p>` : ""}`;
    frappe.confirm(corps, () => {
      frappe.call({
        method: API + ".valider", args: { name: res.name },
        freeze: true, freeze_message: __("Encaissement…"),
        callback: () => {
          frappe.show_alert(
            { message: __("Dettes encaissées ({0}).", [res.name]), indicator: "green" });
          // La caisse reflète l'encaissement sans geste supplémentaire.
          if (rapport && rapport._fetch) rapport._fetch();
        },
      });
    }, () => {
      // Refus : le brouillon ne doit pas rester, il fausserait le prochain calcul.
      frappe.call({ method: API + ".abandonner", args: { name: res.name } });
    });
  }

  d.show();
  poser_zone_photo();
}


// ── Dépenses de caisse ───────────────────────────────────────────────────────
// Trois types (non facturée / avec facture / facture d'achat), photo de la
// facture obligatoire dès qu'il y a facture, analyse OpenAI pour préremplir,
// mode Espèces / Chèque (7 chiffres + photo) / Carte. Le serveur
// (customization_app.caisse_depenses) fait foi sur toutes les règles.
function rcj_depense(rapport) {
  const API = "customization_app.caisse_depenses";
  let etat = { facture: null, facture_nom: null, cheque: null, cheque_nom: null,
               numero: null, date_facture: null, analyse_faite: false };

  const d = new frappe.ui.Dialog({
    title: __("Dépense de caisse"),
    size: "large",
    fields: [
      {
        fieldtype: "Select", fieldname: "type_depense", label: __("Type de dépense"),
        options: "Dépense non facturée\nDépense avec facture\nFacture d'achat",
        default: "Dépense non facturée", reqd: 1,
      },
      { fieldtype: "Data", fieldname: "description", label: __("Description"), reqd: 1 },
      { fieldtype: "Column Break" },
      { fieldtype: "Currency", fieldname: "montant", label: __("Montant"), reqd: 1 },
      {
        fieldtype: "Link", fieldname: "compte", options: "Account",
        label: __("Compte de charge"), default: "Dépenses non déclarées - A&S",
        depends_on: 'eval:doc.type_depense!="Facture d\'achat"',
        mandatory_depends_on: 'eval:doc.type_depense!="Facture d\'achat"',
        get_query: () => ({ query: API + ".comptes_depense" }),
      },
      {
        fieldtype: "Currency", fieldname: "tva", label: __("dont TVA"),
        depends_on: 'eval:doc.type_depense=="Dépense avec facture"',
        description: __("Renseignée par l'analyse — l'écriture fera Cr paiement / Dr TVA / Dr compte classé."),
      },
      { fieldtype: "Float", fieldname: "taux_tva", hidden: 1 },
      {
        fieldtype: "Data", fieldname: "fournisseur", label: __("Fournisseur"),
        depends_on: 'eval:doc.type_depense!="Dépense non facturée"',
        mandatory_depends_on: 'eval:doc.type_depense=="Facture d\'achat"',
      },
      { fieldtype: "Section Break", label: __("Facture") },
      { fieldtype: "HTML", fieldname: "zone_facture" },
      { fieldtype: "Section Break", label: __("Paiement") },
      {
        fieldtype: "Select", fieldname: "mode", label: __("Mode de paiement"),
        options: "Espèces\nChèque\nCarte de crédit", default: "Espèces", reqd: 1,
      },
      { fieldtype: "Column Break" },
      {
        fieldtype: "Data", fieldname: "n_cheque", label: __("N° de chèque (7 chiffres)"),
        depends_on: 'eval:doc.mode=="Chèque"',
        mandatory_depends_on: 'eval:doc.mode=="Chèque"',
      },
      {
        fieldtype: "Select", fieldname: "banque", label: __("Banque"),
        depends_on: 'eval:doc.mode=="Chèque"',
        mandatory_depends_on: 'eval:doc.mode=="Chèque"',
      },
      { fieldtype: "HTML", fieldname: "zone_cheque" },
    ],
    primary_action_label: __("Enregistrer la dépense"),
    primary_action(v) {
      if (v.type_depense !== "Dépense non facturée") {
        if (!etat.facture) {
          frappe.msgprint(__("Prenez la photo de la facture avant d'enregistrer."));
          return;
        }
        // L'analyse est OBLIGATOIRE (décision 19/08) : c'est elle qui garantit
        // montant, TVA, fournisseur et compte cohérents avec la pièce.
        if (!etat.analyse_faite) {
          frappe.msgprint(__("Analysez la facture (bouton 🤖) avant d'enregistrer."));
          return;
        }
      }
      if (v.mode === "Chèque") {
        if (!/^\d{7}$/.test((v.n_cheque || "").trim())) {
          frappe.msgprint(__("Le numéro de chèque doit comporter exactement 7 chiffres."));
          return;
        }
        if (!etat.cheque) {
          frappe.msgprint(__("Prenez la photo du chèque avant d'enregistrer."));
          return;
        }
      }
      frappe.call({
        method: API + ".creer",
        args: {
          type_depense: v.type_depense, montant: v.montant, mode: v.mode,
          compte: v.compte, description: v.description, fournisseur: v.fournisseur,
          tva: v.tva || 0, taux_tva: v.taux_tva || 0,
          numero_facture: etat.numero, date_facture: etat.date_facture,
          n_cheque: v.n_cheque, banque: v.banque,
          photo_facture: etat.facture, photo_facture_nom: etat.facture_nom,
          photo_cheque: v.mode === "Chèque" ? etat.cheque : null,
          photo_cheque_nom: etat.cheque_nom,
        },
        freeze: true, freeze_message: __("Enregistrement de la dépense…"),
        callback: (r) => {
          d.hide();
          frappe.show_alert({ message: __("Dépense enregistrée ({0}).", [r.message.name]),
                              indicator: "green" });
          if (rapport && rapport._fetch) rapport._fetch();
        },
      });
    },
  });

  function zone_photo(champ, cle, cle_nom, libelle, apres) {
    const $z = d.fields_dict[champ].$wrapper;
    $z.html(`
      <div style="margin:4px 0">
        <button type="button" class="btn btn-default btn-sm rcj-ph-btn">📷 ${libelle}</button>
        <span class="rcj-ph-nom text-muted" style="margin-left:8px"></span>
        <input type="file" accept="image/*" capture="environment" style="display:none">
      </div>`);
    const $input = $z.find("input[type=file]");
    $z.find(".rcj-ph-btn").on("click", () => $input.trigger("click"));
    $input.on("change", function () {
      const f = this.files && this.files[0];
      if (!f) return;
      const lecteur = new FileReader();
      lecteur.onload = () => {
        etat[cle] = lecteur.result;
        etat[cle_nom] = f.name;
        $z.find(".rcj-ph-nom").text("✓ " + f.name);
        if (apres) apres();
      };
      lecteur.readAsDataURL(f);
    });
    return $z;
  }

  // Facture : photo + bouton d'analyse OpenAI (préremplit, l'employé confirme).
  const $zf = zone_photo("zone_facture", "facture", "facture_nom", __("Photo de la facture"),
    () => {
      etat.analyse_faite = false;   // nouvelle photo -> nouvelle analyse exigée
      $zf.find(".rcj-analyser").prop("disabled", false);
    });
  $zf.children().first().append(
    `<button type="button" class="btn btn-default btn-sm rcj-analyser"
             style="margin-left:8px" disabled>🤖 ${__("Analyser la facture")}</button>`);
  $zf.find(".rcj-analyser").on("click", () => {
    frappe.call({
      method: API + ".analyser",
      args: { photo: etat.facture, type_depense: d.get_value("type_depense") },
      freeze: true, freeze_message: __("Lecture de la facture…"),
      callback: (r) => {
        const m = r.message || {};
        if (m.montant) d.set_value("montant", m.montant);
        if (m.tva) d.set_value("tva", m.tva);
        if (m.taux_tva) d.set_value("taux_tva", m.taux_tva);
        if (m.compte_suggere) d.set_value("compte", m.compte_suggere);
        if (m.fournisseur) d.set_value("fournisseur", m.fournisseur);
        etat.numero = m.numero || null;
        etat.date_facture = m.date || null;
        etat.analyse_faite = true;
        if (m.numero && !d.get_value("description")) {
          d.set_value("description", __("Facture {0} — {1}", [m.numero, m.fournisseur || ""]));
        }
        frappe.show_alert({
          message: m.coherent
            ? __("Lecture cohérente (HT + TVA + timbre = TTC).")
            : __("Lecture à VÉRIFIER : les totaux ne tombent pas juste."),
          indicator: m.coherent ? "green" : "orange",
        });
      },
    });
  });
  const basculer_facture = () => {
    const type = d.get_value("type_depense");
    $zf.toggle(type !== "Dépense non facturée");
    // « Pas payé » n'existe que pour une facture d'achat (aucune écriture :
    // la dette naîtra avec la facture saisie).
    const modes = type === "Facture d'achat"
      ? "Espèces\nChèque\nCarte de crédit\nPas payé"
      : "Espèces\nChèque\nCarte de crédit";
    const mode_courant = d.get_value("mode");
    d.set_df_property("mode", "options", modes);
    // set_df_property vide la valeur : on la repose (repli Espèces).
    d.set_value("mode",
      modes.split("\n").includes(mode_courant) ? mode_courant : "Espèces");
    // Le compte par défaut (Dépenses non déclarées) n'a de sens que pour une
    // dépense NON facturée ; avec facture, c'est la CLASSIFICATION (ou un choix
    // manuel) qui le donne — un pré-remplissage tromperait l'employé.
    if (type === "Dépense avec facture"
        && d.get_value("compte") === "Dépenses non déclarées - A&S") {
      d.set_value("compte", "");
    }
    if (type === "Dépense non facturée" && !d.get_value("compte")) {
      d.set_value("compte", "Dépenses non déclarées - A&S");
    }
  };
  d.fields_dict.type_depense.$input.on("change", basculer_facture);

  // Chèque : photo obligatoire, même patron que l'encaissement.
  const $zc = zone_photo("zone_cheque", "cheque", "cheque_nom", __("Photo du chèque"));
  const basculer_cheque = () => $zc.toggle(d.get_value("mode") === "Chèque");
  d.fields_dict.mode.$input.on("change", basculer_cheque);

  frappe.call({
    method: "customization_app.caisse_encaissement_dettes.banques",
    callback: (r) => d.set_df_property("banque", "options",
      [""].concat(r.message || []).join("\n")),
  });

  d.show();
  basculer_facture();
  basculer_cheque();
}


RapportCaisseJournaliere.prototype._render_depenses = function () {
  const esc = frappe.utils.escape_html;
  const dep = this._data.depenses || {};
  const rows = dep.lignes || [];
  const $c = $("#rcj-depenses").empty();
  if (!rows.length) return;

  const par_mode = Object.entries(dep.par_mode || {})
    .map(([m, v]) => `${esc(m)} : <b>${this._fmt(v)}</b>`).join(" · ");
  const body = rows.map((l) => {
    const style = RCJ_MODE[l.mode] || { bg: "#eee", fg: "#333" };
    return `<tr>
      <td style="white-space:nowrap">${frappe.datetime.str_to_user(l.date)}</td>
      <td>${esc(l.saisi_par || "")}</td>
      <td>${esc(l.type || "")}</td>
      <td>${esc(l.description || "")}</td>
      <td><span class="rcj-badge" style="background:${style.bg};color:${style.fg}">${esc(l.mode)}</span></td>
      <td style="text-align:right;font-weight:700">${this._fmt(l.montant)}</td>
      <td><a href="/app/journal-entry/${encodeURIComponent(l.name)}" target="_blank">${esc(l.name)}</a></td>
    </tr>`;
  }).join("");

  $c.html(`
    <div class="rcj-recap">
      <div class="rcj-recap-head" style="background:linear-gradient(90deg,#7b241c,#a93226)">
        🧾 Dépenses de la caisse — ${this._fmt(dep.total)} DT
        <span style="font-weight:400;font-size:12px;margin-left:10px">${par_mode}</span>
      </div>
      <div style="overflow-x:auto"><table class="rcj-recap-tbl" style="min-width:760px">
        <thead><tr><th style="text-align:left">Date</th><th style="text-align:left">Saisie par</th>
          <th style="text-align:left">Type</th><th style="text-align:left">Description</th>
          <th style="text-align:left">Mode</th><th>Montant</th><th style="text-align:left">Écriture</th></tr></thead>
        <tbody>${body}</tbody>
      </table></div>
    </div>`);
};


// ── Validation (clôture) de la caisse ────────────────────────────────────────
// État AVANT (report de la clôture précédente) -> mouvements du jour -> APRÈS
// (théorique), espèces comptées et écart — figé dans un document soumis avec
// son PDF instantané. Chaque employé valide SA caisse ; la direction valide
// n'importe laquelle et la globale « Tous les employés ».
function rcj_cloture(rapport) {
  const API = "customization_app.caisse_cloture";
  const d1 = $("#rcj-d1").val(), d2 = $("#rcj-d2").val();
  if (d1 !== d2) {
    frappe.msgprint(__("La clôture porte sur UNE journée : mettez « De » = « À »."));
    return;
  }
  const caisse = $("#rcj-employe").val() || "Tous les employés";

  frappe.call({
    method: API + ".etat", args: { caisse, date: d1 },
    freeze: true,
    callback: (r) => {
      const e = r.message || {};
      if (e.deja_validee) {
        frappe.msgprint(__("La caisse « {0} » du {1} est déjà validée : {2}",
          [caisse, frappe.datetime.str_to_user(d1),
           `<a href="/app/cloture-caisse/${encodeURIComponent(e.deja_validee)}">${e.deja_validee}</a>`]));
        return;
      }
      const fmt = (v) => format_currency(v || 0, "TND");
      const d = new frappe.ui.Dialog({
        title: __("Valider la caisse — {0} ({1})", [caisse, frappe.datetime.str_to_user(d1)]),
        fields: [
          { fieldtype: "HTML", fieldname: "controles" },
          { fieldtype: "HTML", fieldname: "etat" },
          {
            fieldtype: "Currency", fieldname: "comptees",
            label: __("Espèces comptées (physique)"), default: e.solde_theorique,
            onchange: () => {
              const ecart = (d.get_value("comptees") || 0) - e.solde_theorique;
              d.fields_dict.etat.$wrapper.find(".rcj-clo-ecart")
                .text(fmt(ecart))
                .css("color", Math.abs(ecart) < 0.005 ? "#1d6f42" : "#c0392b");
            },
          },
          { fieldtype: "Small Text", fieldname: "note", label: __("Note") },
        ],
        primary_action_label: __("Valider et figer"),
        primary_action(v) {
          // Un BL en brouillon bloque ; chaque autre point exige sa justification.
          if ((e.controles || []).some((c) => c.bloquant)) {
            frappe.msgprint(__("Validez d'abord le(s) bon(s) de livraison signalés en rouge."));
            return;
          }
          const justifications = {};
          let manque = false;
          d.fields_dict.controles.$wrapper.find("input.rcj-just").each(function () {
            const val = ($(this).val() || "").trim();
            if (!val) manque = true;
            justifications[$(this).attr("data-cle")] = val;
          });
          if (manque) {
            frappe.msgprint(__("Chaque point de contrôle doit être justifié."));
            return;
          }
          frappe.call({
            method: API + ".valider",
            args: { caisse, date: d1, especes_comptees: v.comptees, note: v.note,
                    justifications: JSON.stringify(justifications) },
            freeze: true, freeze_message: __("Clôture et génération du PDF…"),
            callback: (rr) => {
              d.hide();
              frappe.msgprint(__("Caisse validée : {0} — le PDF instantané est attaché.",
                [`<a href="/app/cloture-caisse/${encodeURIComponent(rr.message.name)}">${rr.message.name}</a>`]));
              if (rapport && rapport._fetch) rapport._fetch();
            },
          });
        },
      });
      const pts = e.controles || [];
      if (pts.length) {
        const lignes = pts.map((c) => c.bloquant
          ? `<div style="border:1px solid #e0b4b4;background:#fdecea;border-radius:6px;
                padding:8px 10px;margin-bottom:6px">
               <b style="color:#a93226">⛔ ${frappe.utils.escape_html(c.libelle)}</b>
               <button class="btn btn-xs btn-danger rcj-valider-bl" style="margin-left:8px"
                       data-bl="${frappe.utils.escape_html(c.bl)}">${__("Valider le BL")}</button>
             </div>`
          : `<div style="border:1px solid #e6d9a8;background:#fffbe9;border-radius:6px;
                padding:8px 10px;margin-bottom:6px">
               <div style="color:#7a5d10;font-weight:600">⚠️ ${frappe.utils.escape_html(c.libelle)}</div>
               <input class="form-control input-sm rcj-just" style="margin-top:4px"
                      data-cle="${frappe.utils.escape_html(c.cle)}"
                      placeholder="${__("Justification (obligatoire)")}">
             </div>`).join("");
        d.fields_dict.controles.$wrapper.html(
          `<div style="margin-bottom:6px;font-weight:700">${__("Points de contrôle")}</div>${lignes}`);
        d.fields_dict.controles.$wrapper.find(".rcj-valider-bl").on("click", function () {
          const bl = $(this).attr("data-bl");
          frappe.call({
            method: API + ".valider_bl", args: { bl },
            freeze: true, freeze_message: __("Validation du BL…"),
            callback: () => {
              frappe.show_alert({ message: __("BL {0} validé.", [bl]), indicator: "green" });
              d.hide();
              rcj_cloture(rapport);   // recharger l'état et les contrôles
            },
          });
        });
      }
      d.fields_dict.etat.$wrapper.html(`
        <table class="table table-bordered" style="font-size:12.5px">
          <tr><td>${__("Solde d'ouverture (avant)")}</td>
              <td style="text-align:right;font-weight:700">${fmt(e.solde_ouverture)}</td></tr>
          <tr><td>${__("Encaissements espèces du jour")}</td>
              <td style="text-align:right">+ ${fmt(e.encaissements_especes)}</td></tr>
          <tr><td>${__("Dépenses espèces du jour")}</td>
              <td style="text-align:right">− ${fmt(e.depenses_especes)}</td></tr>
          <tr><td><b>${__("Solde théorique (après)")}</b></td>
              <td style="text-align:right;font-weight:800">${fmt(e.solde_theorique)}</td></tr>
          <tr><td>${__("Écart (compté − théorique)")}</td>
              <td style="text-align:right;font-weight:700" ><span class="rcj-clo-ecart"
                  style="color:#1d6f42">${fmt(0)}</span></td></tr>
          <tr><td class="text-muted">${__("Chèques du jour")} · ${__("Autres modes")}</td>
              <td style="text-align:right" class="text-muted">${fmt(e.total_cheques)} · ${
                fmt(e.total_autres_modes)}</td></tr>
        </table>`);
      d.show();
    },
  });
}
