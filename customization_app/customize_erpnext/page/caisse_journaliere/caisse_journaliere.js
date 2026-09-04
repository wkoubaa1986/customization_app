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
    $("#rcj-btn-a-payer").on("click", () => rcj_depenses_a_payer(this));
    $("#rcj-btn-bl").on("click", () => rcj_depenses_bl(this));
    $("#rcj-btn-fa-payer").on("click", () => rcj_factures_a_payer(this));
    $("#rcj-btn-fa-bl").on("click", () => rcj_factures_bl(this));
    $("#rcj-btn-fa-sans-justif").on("click", () => rcj_factures_sans_justif());
    $("#rcj-btn-fa-saisir").on("click", () =>
      frappe.set_route("List", "Facture Achat a Saisir", { statut: "À saisir" }));
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
      this._afficher_cloture();
    } catch (e) {
      frappe.msgprint({ title: "Erreur", message: String(e), indicator: "red" });
    } finally {
      this._loading(false);
    }
  }

  _afficher_cloture() {
    // Bannière « caisse validée » : visible dès l'ouverture, sans passer par le bouton.
    const $b = $("#rcj-cloture-banner").hide().empty();
    const d1 = $("#rcj-d1").val(), d2 = $("#rcj-d2").val();
    if (!d1 || d1 !== d2) return;   // la clôture porte sur une journée
    const caisse = $("#rcj-employe").val() || "Tous les employés";
    frappe.call({
      method: "customization_app.caisse_cloture.cloture_info",
      args: { caisse, date: d1 },
      callback: (r) => {
        const c = r.message;
        if (!c) return;
        const ecart = c.ecart || 0;
        $b.html(
          `✅ ${__("Caisse validée")} — <a href="/app/cloture-caisse/${
            encodeURIComponent(c.name)}">${c.name}</a> · ${__("par")} ${
            frappe.utils.escape_html(c.valide_par)} · ${__("écart")} ${
            format_currency(ecart, "TND")}${c.pdf_url
              ? ` · <a href="${c.pdf_url}" target="_blank"><b>📄 ${__("Ouvrir le PDF")}</b></a>`
              : ""}`).show();
      },
    });
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
// sur la SÉLECTION de l'employé (dettes en FIFO par date, paiements dans l'ordre
// de saisie), plafonne le total à la somme sélectionnée, exige numéro + banque +
// photo pour chaque chèque (7 chiffres) et chaque traite bancaire, puis passe par
// l'Outil d'encaissement (échéanciers, reliquat, paiements). PLUSIEURS pièces
// peuvent couvrir la même sélection : chaque ligne devient son propre paiement.
function rcj_encaissement_dettes(rapport) {
  const API = "customization_app.caisse_encaissement_dettes";
  const MODES = ["Espèces", "Chèque", "Traite bancaire"];
  let etat = { total: 0, banques: [], dettes: [], paiements: [] };

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
      { fieldtype: "Section Break", label: __("Paiements reçus") },
      { fieldtype: "HTML", fieldname: "paiements_zone" },
    ],
    primary_action_label: __("Encaisser"),
    primary_action(v) {
      const choisies = selection();
      const total_sel = choisies.reduce((s, x) => s + x.montant, 0);
      if (!choisies.length) {
        frappe.msgprint(__("Sélectionnez au moins une dette."));
        return;
      }
      if (!etat.paiements.length) {
        frappe.msgprint(__("Ajoutez au moins un paiement."));
        return;
      }
      const doublons = new Set();
      for (let i = 0; i < etat.paiements.length; i++) {
        const p = etat.paiements[i];
        const no = __("Paiement {0}", [i + 1]);
        if (!(p.montant > 0)) {
          frappe.msgprint(__("{0} : le montant doit être positif.", [no]));
          return;
        }
        if (p.mode !== "Espèces") {
          const num = (p.n_piece || "").trim();
          if (p.mode === "Chèque" && !/^\d{7}$/.test(num)) {
            frappe.msgprint(__("{0} : le numéro de chèque doit comporter exactement 7 chiffres.", [no]));
            return;
          }
          if (p.mode === "Traite bancaire" && !/^\d{4,20}$/.test(num)) {
            frappe.msgprint(__("{0} : le numéro de traite doit comporter de 4 à 20 chiffres.", [no]));
            return;
          }
          // Banque obligatoire pour un chèque seulement (décision utilisateur 2026-08-20).
          if (p.mode === "Chèque" && !(p.banque || "").trim()) {
            frappe.msgprint(__("{0} : pour un chèque, la banque est obligatoire.", [no]));
            return;
          }
          if (!p.photo) {
            frappe.msgprint(__("{0} : prenez la photo de la pièce avant d'encaisser.", [no]));
            return;
          }
          const cle = p.mode + "|" + num + "|" + p.banque;
          if (doublons.has(cle)) {
            frappe.msgprint(__("{0} : le numéro {1} ({2}) est saisi deux fois.", [no, num, p.banque]));
            return;
          }
          doublons.add(cle);
        }
      }
      const total = etat.paiements.reduce((s, p) => s + (p.montant || 0), 0);
      if (total > total_sel + 0.001) {
        frappe.msgprint(__("Le total des paiements dépasse la somme des dettes sélectionnées ({0}).",
          [format_currency(total_sel, "TND")]));
        return;
      }
      frappe.call({
        method: API + ".encaisser",
        args: { client: v.client,
                paiements: JSON.stringify(etat.paiements.map((p) => ({
                  mode: p.mode, montant: p.montant, n_piece: p.n_piece,
                  banque: p.banque, photo: p.photo, photo_nom: p.photo_nom }))),
                dettes: JSON.stringify(choisies.map((x) => x.paiement)) },
        freeze: true, freeze_message: __("Calcul de l'allocation…"),
        callback: (r) => confirmer(r.message),
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
        // Les banques alimentent les selects des lignes de paiement (plus de champ
        // « banque » au niveau du dialogue depuis le multi-pièces).
        if (m.banques && m.banques.length) etat.banques = m.banques;
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
          .on("change", () => { maj_total_selection(); maj_total_paiements(); });
        // Une seule ligne Espèces préremplie au total : le cas le plus fréquent
        // reste à un clic, les pièces multiples s'ajoutent par le bouton.
        etat.paiements = [{ mode: "Espèces", montant: m.total, n_piece: "",
                            banque: "", photo: null, photo_nom: null }];
        render_paiements();
      },
    });
  }

  // Les lignes de paiement : mode, montant, n° de pièce, banque, photo PAR PIÈCE
  // (caméra du téléphone ou fichier — obligatoire pour chèque et traite).
  function render_paiements() {
    const $z = d.fields_dict.paiements_zone.$wrapper;
    const opts_banque = (b) => [""].concat(etat.banques).map((x) =>
      `<option ${x === b ? "selected" : ""}>${frappe.utils.escape_html(x)}</option>`).join("");
    const lignes = etat.paiements.map((p, i) => {
      const piece = p.mode !== "Espèces";
      return `
      <tr data-i="${i}">
        <td><select class="form-control input-sm rcj-p-mode">${MODES.map((m) =>
          `<option ${m === p.mode ? "selected" : ""}>${m}</option>`).join("")}</select></td>
        <td><input type="number" step="0.001" min="0" class="form-control input-sm rcj-p-montant"
                   value="${p.montant || ""}"></td>
        <td>${piece ? `<input type="text" class="form-control input-sm rcj-p-numero"
                   placeholder="${p.mode === "Chèque" ? __("7 chiffres") : __("N° traite")}"
                   value="${frappe.utils.escape_html(p.n_piece || "")}">` : "—"}</td>
        <td>${piece ? `<select class="form-control input-sm rcj-p-banque">${
                   opts_banque(p.banque)}</select>` : "—"}</td>
        <td style="white-space:nowrap">${piece ? `
          <button type="button" class="btn btn-default btn-xs rcj-p-photo">📷</button>
          <span class="text-muted" style="font-size:11px">${p.photo
            ? `<a href="#" class="rcj-p-voir">✓ ${
                frappe.utils.escape_html(p.photo_nom || "photo")}</a>`
            : __("requise")}</span>
          <input type="file" accept="image/*,application/pdf" style="display:none">` : "—"}</td>
        <td><button type="button" class="btn btn-default btn-xs rcj-p-suppr">✕</button></td>
      </tr>`;
    }).join("");
    $z.html(`
      <div style="overflow-x:auto">
      <table class="table table-bordered" style="font-size:12px;margin:4px 0 6px">
        <thead><tr><th style="min-width:130px">${__("Mode")}</th>
                   <th style="min-width:110px;text-align:right">${__("Montant")}</th>
                   <th style="min-width:120px">${__("N° pièce")}</th>
                   <th style="min-width:130px">${__("Banque")}</th>
                   <th>${__("Photo")}</th><th></th></tr></thead>
        <tbody>${lignes}</tbody>
      </table></div>
      <button type="button" class="btn btn-default btn-sm rcj-p-ajouter">＋ ${
        __("Ajouter un paiement")}</button>
      <span class="rcj-p-total text-muted" style="margin-left:12px"></span>`);

    const ligne_de = (el) => etat.paiements[parseInt($(el).closest("tr").attr("data-i"), 10)];
    $z.find(".rcj-p-mode").on("change", function () {
      ligne_de(this).mode = $(this).val();
      render_paiements();          // les colonnes n°/banque/photo suivent le mode
    });
    $z.find(".rcj-p-montant").on("input", function () {
      ligne_de(this).montant = parseFloat($(this).val()) || 0;
      maj_total_paiements();
    });
    $z.find(".rcj-p-numero").on("input", function () { ligne_de(this).n_piece = $(this).val(); });
    $z.find(".rcj-p-banque").on("change", function () { ligne_de(this).banque = $(this).val(); });
    $z.find(".rcj-p-photo").on("click", function () {
      $(this).closest("td").find("input[type=file]").trigger("click");
    });
    $z.find(".rcj-p-voir").on("click", function (e) {
      e.preventDefault();
      const p = ligne_de(this);
      rcj_apercu_fichier(p.photo, p.photo_nom);
    });
    $z.find("input[type=file]").on("change", function () {
      const f = this.files && this.files[0];
      if (!f) return;
      const p = ligne_de(this);
      const lecteur = new FileReader();
      lecteur.onload = () => { p.photo = lecteur.result; p.photo_nom = f.name; render_paiements(); };
      lecteur.readAsDataURL(f);
    });
    $z.find(".rcj-p-suppr").on("click", function () {
      etat.paiements.splice(parseInt($(this).closest("tr").attr("data-i"), 10), 1);
      render_paiements();
    });
    $z.find(".rcj-p-ajouter").on("click", () => {
      // Une pièce supplémentaire est presque toujours un chèque ou une traite.
      etat.paiements.push({ mode: "Chèque", montant: 0, n_piece: "", banque: "",
                            photo: null, photo_nom: null });
      render_paiements();
    });
    maj_total_paiements();
  }

  function maj_total_paiements() {
    const total = etat.paiements.reduce((s, p) => s + (p.montant || 0), 0);
    const total_sel = selection().reduce((s, x) => s + x.montant, 0);
    const $t = d.fields_dict.paiements_zone.$wrapper.find(".rcj-p-total");
    $t.html(__("Total paiements : {0} / sélectionné : {1}",
      [format_currency(total, "TND"), format_currency(total_sel, "TND")]));
    $t.css("color", total > total_sel + 0.001 ? "#c0392b" : "");
  }

  frappe.call({
    method: API + ".banques",
    callback: (r) => {
      etat.banques = r.message || [];
      render_paiements();   // les selects Banque des lignes reçoivent les options
    },
  });

  function confirmer(res) {
    d.hide();
    const lignes = (res.allocation || []).map((a) => `
      <tr><td>${frappe.utils.escape_html(a.paiement)}</td>
          <td>${a.commande ? frappe.utils.escape_html(a.commande) : "—"}</td>
          <td>${frappe.utils.escape_html(a.mode || "")}${
            a.piece ? " " + frappe.utils.escape_html(a.piece) : ""}</td>
          <td style="text-align:right">${format_currency(a.montant, "TND")}</td>
          <td style="text-align:right">${format_currency(a.dette_totale, "TND")}</td></tr>`).join("");
    // Vérification OpenAI des photos : de simples AVERTISSEMENTS — l'employé tranche.
    const avert = (res.avertissements || []).length ? `
      <div style="background:#fff8e1;border:1px solid #f0c36d;border-radius:6px;
                  padding:8px 12px;margin-bottom:10px">
        <strong>⚠️ ${__("Vérification des photos")}</strong>
        <ul style="margin:6px 0 0 18px">${res.avertissements.map((a) =>
          `<li>${frappe.utils.escape_html(a)}</li>`).join("")}</ul>
        <div class="text-muted" style="font-size:11px">${
          __("Simple avertissement : vérifie la pièce, puis confirme ou annule.")}</div>
      </div>` : "";
    const corps = `
      ${avert}
      <p>${__("Total reçu {0} — répartition sur les dettes sélectionnées :",
        [format_currency(res.total_paiements, "TND")])}</p>
      <div style="overflow-x:auto"><table class="table table-bordered" style="font-size:12px">
        <thead><tr><th>${__("Dette consommée")}</th><th>${__("Commande")}</th>
                   <th>${__("Pièce")}</th>
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
  render_paiements();
}


// ── Aperçu d'un justificatif encore en mémoire (avant enregistrement) ────────
// La pièce n'est pas encore sur le serveur : on la relit depuis son dataURL.
// ⚠️ PAR UN BLOB, PAS PAR LE dataURL DIRECT : Chrome refuse d'ouvrir un
// « data:application/pdf » dans un onglet ou une iframe (protection anti-
// phishing) — le justificatif resterait invisible au moment où on veut le
// vérifier.
function rcj_apercu_fichier(dataUrl, nom) {
  const virgule = (dataUrl || "").indexOf(",");
  if (virgule < 0) return;
  const entete = dataUrl.slice(0, virgule);
  const b64 = dataUrl.slice(virgule + 1);
  const mt = (entete.match(/data:([^;]+)/) || [])[1] || "application/octet-stream";
  let url;
  try {
    const bin = atob(b64);
    const buf = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
    url = URL.createObjectURL(new Blob([buf], { type: mt }));
  } catch (e) {
    frappe.msgprint(__("Ce fichier ne peut pas être affiché."));
    return;
  }
  const d = new frappe.ui.Dialog({
    title: nom || __("Justificatif"),
    size: "large",
    fields: [{ fieldtype: "HTML", fieldname: "zone" }],
    primary_action_label: __("Ouvrir dans un onglet"),
    primary_action: () => window.open(url, "_blank"),
    onhide: () => setTimeout(() => URL.revokeObjectURL(url), 60000),
  });
  d.fields_dict.zone.$wrapper.html(mt === "application/pdf"
    ? `<iframe src="${url}" style="width:100%;height:70vh;border:1px solid #ddd"></iframe>`
    : `<img src="${url}" style="max-width:100%;max-height:70vh;display:block;margin:0 auto">`);
  d.show();
}


// ── Cadrage du scan : les 4 coins détectés, ajustables à la main ─────────────
// Le serveur (detecter_contour) propose le quadrilatère ; l'employé déplace les
// poignées sur la photo (souris ou doigt) puis valide — ces coins font foi pour
// le redressement (warpPerspective côté serveur). « Photo entière » = pas de
// recadrage imposé, le serveur garde son comportement automatique.
function rcj_cadrage(photo, onDone) {
  frappe.call({
    method: "customization_app.caisse_depenses.detecter_contour",
    args: { photo },
    freeze: true, freeze_message: __("Détection du document…"),
    callback: (r) => {
      const m = r.message || {};
      // Un PDF est déjà un document cadré : pas d'étape de cadrage (et un
      // <img> ne saurait pas l'afficher — il resterait bloqué sur onload).
      if (m.pdf) { onDone(null); return; }
      const img = new Image();
      img.onload = () => ouvrir(img, m);
      img.onerror = () => onDone(null);   // fichier illisible : on n'impose rien
      img.src = photo;
    },
  });

  function ouvrir(img, m) {
    const maxW = Math.min(700, window.innerWidth - 80);
    const maxH = 480;
    const k = Math.min(maxW / img.width, maxH / img.height, 1);
    const W = Math.round(img.width * k), H = Math.round(img.height * k);
    let coins = (m.coins || []).map((c) => [c[0] * k, c[1] * k]);
    if (coins.length !== 4) {
      coins = [[10, 10], [W - 10, 10], [W - 10, H - 10], [10, H - 10]];
    }
    const d = new frappe.ui.Dialog({
      title: __("Cadrage du document — ajuste les 4 coins"),
      size: "large",
      fields: [{ fieldtype: "HTML", fieldname: "zone" }],
      primary_action_label: __("Valider ce cadrage"),
      primary_action() {
        d.hide();
        onDone(coins.map((c) => [c[0] / k, c[1] / k]));
      },
      secondary_action_label: __("Photo entière"),
      secondary_action() { d.hide(); onDone(null); },
    });
    d.fields_dict.zone.$wrapper.html(`
      <div class="text-muted" style="font-size:11px;margin-bottom:4px">${m.detecte
        ? __("Contour détecté automatiquement — déplace les coins si besoin.")
        : __("Aucun contour net détecté — place les coins toi-même.")}</div>
      <canvas class="rcj-cadre" width="${W}" height="${H}"
              style="max-width:100%;touch-action:none;cursor:crosshair;border:1px solid #ddd"></canvas>`);
    const canvas = d.fields_dict.zone.$wrapper.find("canvas.rcj-cadre")[0];
    const ctx = canvas.getContext("2d");
    function dessiner() {
      ctx.drawImage(img, 0, 0, W, H);
      // Voile sombre HORS de la zone retenue : on voit ce qui sera coupé.
      ctx.save();
      ctx.fillStyle = "rgba(0,0,0,0.45)";
      ctx.beginPath();
      ctx.rect(0, 0, W, H);
      ctx.moveTo(coins[0][0], coins[0][1]);
      for (let i = 3; i >= 0; i--) ctx.lineTo(coins[i][0], coins[i][1]);
      ctx.closePath();
      ctx.fill("evenodd");
      ctx.restore();
      ctx.strokeStyle = "#2e7d32"; ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(coins[0][0], coins[0][1]);
      for (let i = 1; i < 4; i++) ctx.lineTo(coins[i][0], coins[i][1]);
      ctx.closePath(); ctx.stroke();
      coins.forEach((c) => {
        ctx.beginPath(); ctx.arc(c[0], c[1], 7, 0, 2 * Math.PI);
        ctx.fillStyle = "#fff"; ctx.fill(); ctx.stroke();
      });
    }
    let actif = -1;
    const pos = (ev) => {
      const r = canvas.getBoundingClientRect();
      const p = ev.touches ? ev.touches[0] : ev;
      // max-width:100% peut réduire l'affichage : reconvertir en pixels canvas.
      return [(p.clientX - r.left) * (W / r.width), (p.clientY - r.top) * (H / r.height)];
    };
    const debut = (ev) => {
      const [x, y] = pos(ev);
      let best = -1, bd = 1e9;
      coins.forEach((c, i) => {
        const dd = Math.hypot(c[0] - x, c[1] - y);
        if (dd < bd) { bd = dd; best = i; }
      });
      if (bd <= 28) { actif = best; ev.preventDefault(); }
    };
    const bouge = (ev) => {
      if (actif < 0) return;
      const [x, y] = pos(ev);
      coins[actif] = [Math.max(0, Math.min(W, x)), Math.max(0, Math.min(H, y))];
      dessiner();
      ev.preventDefault();
    };
    const fin = () => { actif = -1; };
    canvas.addEventListener("mousedown", debut);
    canvas.addEventListener("mousemove", bouge);
    window.addEventListener("mouseup", fin);
    canvas.addEventListener("touchstart", debut, { passive: false });
    canvas.addEventListener("touchmove", bouge, { passive: false });
    canvas.addEventListener("touchend", fin);
    d.show();
    dessiner();
  }
}


// ── Fournisseur douteux : on demande, on ne crée jamais un doublon en silence ─
// Le serveur rapproche par MATRICULE FISCAL d'abord (il identifie le
// contribuable), puis par nom normalisé. Quand rien n'est certain mais que des
// fiches ressemblent, c'est l'utilisateur qui tranche.
function rcj_choisir_fournisseur(m, onChoix) {
  const esc = frappe.utils.escape_html;
  const lu = m.fournisseur || "";
  const options = (m.fournisseur_candidats || []).map((c, i) => `
    <label style="display:block;padding:6px 8px;border:1px solid #eee;border-radius:6px;margin-bottom:4px">
      <input type="radio" name="rcj-four" value="${esc(c.name)}" ${i === 0 ? "checked" : ""}>
      <strong>${esc(c.supplier_name)}</strong>
      ${c.tax_id ? `<span class="text-muted"> — ${__("MF")} ${esc(c.tax_id)}</span>` : ""}
      <span class="text-muted" style="font-size:11px"> (${Math.round(c.score * 100)} %)</span>
    </label>`).join("");
  const d = new frappe.ui.Dialog({
    title: __("Quel fournisseur ?"),
    fields: [{ fieldtype: "HTML", fieldname: "zone" }],
    primary_action_label: __("Utiliser cette fiche"),
    primary_action() {
      const choix = d.$wrapper.find("input[name=rcj-four]:checked").val();
      d.hide();
      onChoix(choix || null);
    },
    secondary_action_label: __("➕ Nouveau fournisseur"),
    secondary_action() { d.hide(); onChoix(null); },
  });
  d.fields_dict.zone.$wrapper.html(`
    <p>${__("Lu sur la facture : <strong>{0}</strong>{1}.", [esc(lu),
      m.matricule ? __(" (MF {0})", [esc(m.matricule)]) : ""])}</p>
    <p class="text-muted" style="font-size:12px">${
      __("Des fiches existantes ressemblent — choisis la bonne pour éviter un doublon, ou crée un nouveau fournisseur.")}</p>
    ${options}`);
  d.show();
}


// ── Dépenses de caisse ───────────────────────────────────────────────────────
// Trois types (non facturée / avec facture / facture d'achat), photo de la
// facture obligatoire dès qu'il y a facture, analyse OpenAI pour préremplir,
// mode Espèces / Chèque (7 chiffres + photo) / Carte. Le serveur
// (customization_app.caisse_depenses) fait foi sur toutes les règles.
function rcj_depense(rapport) {
  const API = "customization_app.caisse_depenses";
  let etat = { facture: null, facture_nom: null, facture_coins: null,
               supplier: null, matricule: null,
               cheque: null, cheque_nom: null,
               numero: null, date_facture: null, analyse_faite: false,
               est_bl: false, numero_bl: null,
               paiements: [], banques: [] };

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
      // ⚠️ `depends_on` ET NON un toggle jQuery : Frappe réaffiche les champs à
      // chaque rafraîchissement du dialogue (set_df_property, set_value…), et la
      // section « Facture » revenait sur une dépense NON facturée, qui n'a pas
      // de justificatif à joindre.
      {
        fieldtype: "Section Break", label: __("Facture"),
        depends_on: 'eval:doc.type_depense!="Dépense non facturée"',
      },
      {
        fieldtype: "HTML", fieldname: "zone_facture",
        depends_on: 'eval:doc.type_depense!="Dépense non facturée"',
      },
      { fieldtype: "Section Break", label: __("Paiement") },
      {
        // La retenue n-est PAS un moyen de paiement : c-est la part qu-on ne verse pas au
        // fournisseur et qui reste due au Tresor. Elle se pose donc AVANT les reglements, et
        // ceux-ci ne couvrent que le net.
        fieldtype: "Currency", fieldname: "retenue",
        label: __("Retenue à la source achat"),
        depends_on: 'eval:doc.type_depense!="Dépense non facturée"',
        description: __("Proposée dès le seuil. Corrigez-la si la facture porte un timbre, ou mettez 0 si le fournisseur n-est pas assujetti."),
      },
      { fieldtype: "HTML", fieldname: "zone_retenue" },
      {
        fieldtype: "Check", fieldname: "fractionne",
        label: __("Paiement fractionné (plusieurs chèques, ou espèces + chèque…)"),
        depends_on: 'eval:doc.type_depense!="Dépense non facturée" && doc.mode!="Pas payé"',
      },
      {
        fieldtype: "Select", fieldname: "mode", label: __("Mode de paiement"),
        options: "Espèces\nChèque\nCarte de crédit", default: "Espèces", reqd: 1,
        depends_on: "eval:!doc.fractionne",
      },
      { fieldtype: "Column Break" },
      {
        fieldtype: "Data", fieldname: "n_cheque", label: __("N° de chèque (7 chiffres)"),
        depends_on: 'eval:doc.mode=="Chèque" && !doc.fractionne',
        mandatory_depends_on: 'eval:doc.mode=="Chèque" && !doc.fractionne',
      },
      {
        fieldtype: "Select", fieldname: "banque", label: __("Banque"),
        depends_on: 'eval:doc.mode=="Chèque" && !doc.fractionne',
        mandatory_depends_on: 'eval:doc.mode=="Chèque" && !doc.fractionne',
      },
      { fieldtype: "HTML", fieldname: "zone_cheque" },
      { fieldtype: "HTML", fieldname: "zone_paiements" },
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
      // Les reglements couvrent le TTC MOINS la retenue — c-est ce que le serveur validera.
      const a_regler = flt(v.montant) - flt(v.retenue || 0);
      let paiements = null;
      if (v.fractionne && v.mode !== "Pas payé") {
        paiements = rcj_collecter_paiements(etat, a_regler);
        if (!paiements) return;   // message déjà affiché
      } else if (v.mode === "Chèque") {
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
          coins_facture: etat.facture_coins ? JSON.stringify(etat.facture_coins) : null,
          supplier: etat.supplier || null, matricule: etat.matricule || null,
          photo_cheque: !paiements && v.mode === "Chèque" ? etat.cheque : null,
          photo_cheque_nom: etat.cheque_nom,
          paiements: paiements ? JSON.stringify(paiements) : null,
          retenue: flt(v.retenue || 0),
          est_bl: v.type_depense !== "Dépense non facturée" && etat.est_bl ? 1 : 0,
          numero_bl: etat.numero_bl || null,
        },
        freeze: true, freeze_message: __("Enregistrement de la dépense…"),
        callback: (r) => {
          d.hide();
          if (r.message.a_payer) {
            frappe.show_alert({
              message: __("Dépense enregistrée EN DETTE ({0}) — à régler via « 💸 Dépenses à payer ».",
                [r.message.fiche]),
              indicator: "orange",
            }, 8);
          } else {
            frappe.show_alert({ message: __("Dépense enregistrée ({0}).", [r.message.name || r.message.fiche]),
                                indicator: "green" });
          }
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
        <input type="file" accept="image/*,application/pdf" style="display:none">
      </div>`);
    const $input = $z.find("input[type=file]");
    $z.find(".rcj-ph-btn").on("click", () => $input.trigger("click"));
    // Le nom du fichier ouvre son aperçu : on vérifie ce qu'on joint avant
    // d'enregistrer, sans quitter la saisie.
    $z.on("click", ".rcj-ph-voir", (e) => {
      e.preventDefault();
      rcj_apercu_fichier(etat[cle], etat[cle_nom]);
    });
    $input.on("change", function () {
      const f = this.files && this.files[0];
      if (!f) return;
      const lecteur = new FileReader();
      lecteur.onload = () => {
        etat[cle] = lecteur.result;
        etat[cle_nom] = f.name;
        $z.find(".rcj-ph-nom").html(
          `<a href="#" class="rcj-ph-voir">✓ ${frappe.utils.escape_html(f.name)}</a>`);
        if (apres) apres();
      };
      lecteur.readAsDataURL(f);
    });
    return $z;
  }

  // ⚠️ À l'ouverture du dialogue, la valeur par défaut du Select n'est posée
  // qu'APRÈS le premier basculer_facture() : get_value renvoie undefined et le
  // verrou croyait être sur un type facturé — bouton mort sur une dépense non
  // facturée. Le repli sur le défaut du champ corrige la course.
  const type_depense_courant = () =>
    d.get_value("type_depense") || "Dépense non facturée";

  // La retenue à la source achat : le SERVEUR décide, l-écran ne fait qu-afficher. Elle se
  // recalcule quand le montant, le type ou le fournisseur bougent — et elle DIT ce qui
  // manquerait pour émettre le certificat, sans jamais bloquer la saisie (décision
  // utilisateur 04/09/2026 : on pose la retenue et on signale).
  let retenue_touchee = false;
  const maj_retenue = frappe.utils.debounce(async () => {
    const $z = d.fields_dict.zone_retenue.$wrapper;
    const type = type_depense_courant();
    const montant = flt(d.get_value("montant"));
    if (type === "Dépense non facturée" || !montant) {
      $z.empty();
      return;
    }
    let r;
    try {
      r = (await frappe.call({
        method: `${API}.proposition_retenue`,
        args: { type_depense: type, montant,
                fournisseur: d.get_value("fournisseur") || "",
                supplier: etat.supplier || null },
      })).message;
    } catch (e) {
      return;
    }
    // On ne réécrit JAMAIS une valeur que l-opérateur a corrigée à la main : la proposition
    // sert de point de départ, pas de verdict.
    if (!retenue_touchee) d.set_value("retenue", r.retenue || 0);
    if (!r.retenue) {
      $z.empty();
      return;
    }
    const avert = (r.avertissements || []).map(
      (a) => `<div class="rcj-warn-banner" style="margin-top:6px">⚠️ ${frappe.utils.escape_html(a)}</div>`
    ).join("");
    $z.html(`<div style="font-size:12.5px;color:var(--text-muted)">
        Net à régler : <b>${format_currency(r.net, "TND")}</b>
        — la retenue reste due au Trésor et fera l-objet dun certificat.</div>${avert}`);
  }, 350);

  // ⚠️ ENREGISTRER VERROUILLÉ TANT QUE L'ANALYSE N'EST PAS FAITE pour les
  // types facturés (décision utilisateur 24/08) — le msgprint ne suffisait pas.
  const maj_bouton = () => {
    const bloque = type_depense_courant() !== "Dépense non facturée"
      && !etat.analyse_faite;
    d.get_primary_btn().prop("disabled", bloque)
      .attr("title", bloque ? __("Analysez d'abord la facture (bouton 🤖).") : "");
  };

  // Facture : photo + bouton d'analyse OpenAI (préremplit, l'employé confirme).
  const $zf = zone_photo("zone_facture", "facture", "facture_nom",
    __("Photo ou PDF de la facture"),
    () => {
      etat.analyse_faite = false;   // nouvelle photo -> nouvelle analyse exigée
      etat.facture_coins = null;
      $zf.find(".rcj-analyser").prop("disabled", false);
      maj_bouton();
      // Cadrage à valider AVANT tout : les coins retenus font foi pour le scan.
      rcj_cadrage(etat.facture, (coins) => { etat.facture_coins = coins; });
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
        // Détection BL : la fiche sera marquée « Bon de livraison » et la
        // facture qui la couvre sera rattachée plus tard (bouton sur la
        // facture d'achat), éventuellement avec d'autres BL du fournisseur.
        etat.est_bl = !!m.est_bl;
        etat.numero_bl = m.numero_bl || null;
        if (m.est_bl && d.get_value("type_depense") !== "Dépense non facturée") {
          frappe.show_alert({
            message: __("📦 BON DE LIVRAISON détecté{0} — la facture sera rattachée plus tard.",
              [m.numero_bl ? " (n° " + m.numero_bl + ")" : ""]),
            indicator: "orange",
          }, 8);
        }
        maj_bouton();
        // La description LUE sur la photo d'abord ; à défaut, le gabarit
        // « Facture n° — fournisseur ». On ne touche jamais une saisie déjà faite.
        if (!d.get_value("description")) {
          if (m.description) {
            d.set_value("description", m.description);
          } else if (m.numero) {
            d.set_value("description", __("Facture {0} — {1}", [m.numero, m.fournisseur || ""]));
          }
        }
        frappe.show_alert({
          message: m.coherent
            ? __("Lecture cohérente (HT + TVA + timbre = TTC).")
            : __("Lecture à VÉRIFIER : les totaux ne tombent pas juste."),
          indicator: m.coherent ? "green" : "orange",
        });
        // Rapprochement fournisseur : uniquement pour la FACTURE D'ACHAT — c'est
        // le seul type qui rattache ou crée une fiche fournisseur.
        etat.matricule = m.matricule || null;
        etat.supplier = m.fournisseur_certain || null;
        if (d.get_value("type_depense") === "Facture d'achat") {
          if (m.fournisseur_certain) {
            frappe.show_alert({
              message: __("Fournisseur reconnu par le {0} : {1}",
                [m.fournisseur_motif === "matricule" ? __("matricule fiscal") : __("nom"),
                 m.fournisseur_certain]),
              indicator: "green",
            });
          } else if ((m.fournisseur_candidats || []).length) {
            rcj_choisir_fournisseur(m, (choix) => { etat.supplier = choix; });
          }
        }
      },
    });
  });
  const basculer_facture = () => {
    const type = type_depense_courant();
    // Le masquage est porté par `depends_on` (cf. la définition des champs) ;
    // ce toggle ne fait que suivre immédiatement, sans attendre un refresh.
    $zf.toggle(type !== "Dépense non facturée");
    // « Pas payé » : facture d'achat (aucune écriture, dette avec la facture)
    // ET dépense avec facture (charge immédiate contre le découvert, réglée
    // plus tard via « 💸 Dépenses à payer » — décision utilisateur 24/08).
    const modes = type !== "Dépense non facturée"
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
    maj_bouton();
    maj_retenue();
  };
  d.fields_dict.type_depense.$input.on("change", basculer_facture);
  // Le montant et le fournisseur decident aussi de la retenue : l-analyse de la facture les
  // remplit toute seule, d-ou l-ecoute sur `change` en plus de la frappe.
  d.fields_dict.montant.$input.on("change input", () => maj_retenue());
  d.fields_dict.fournisseur.$input.on("change input", () => maj_retenue());
  d.fields_dict.retenue.$input.on("input", () => { retenue_touchee = true; });

  // Chèque : photo obligatoire, même patron que l'encaissement.
  const $zc = zone_photo("zone_cheque", "cheque", "cheque_nom", __("Photo du chèque"));
  const basculer_cheque = () =>
    $zc.toggle(d.get_value("mode") === "Chèque" && !d.get_value("fractionne"));
  d.fields_dict.mode.$input.on("change", basculer_cheque);

  // Paiement fractionné : lignes de règlement rendues dans zone_paiements.
  // « Pas payé » possible par ligne pour les DEUX types facturés (paiement
  // partiel — la part non payée part en dette).
  const rendre_paiements = rcj_zone_paiements(d, etat, () => flt(d.get_value("montant")),
    ["Espèces", "Chèque", "Carte de crédit", "Pas payé"]);
  d.fields_dict.fractionne.$input.on("change", () => { basculer_cheque(); rendre_paiements(); });
  d.fields_dict.montant.$input.on("change", rendre_paiements);

  frappe.call({
    method: "customization_app.caisse_encaissement_dettes.banques",
    callback: (r) => {
      etat.banques = r.message || [];
      d.set_df_property("banque", "options",
        [""].concat(etat.banques).join("\n"));
    },
  });

  d.show();
  basculer_facture();
  basculer_cheque();
}


RapportCaisseJournaliere.prototype._render_depenses = function () {
  const esc = frappe.utils.escape_html;
  const dep = this._data.depenses || {};
  let rows = dep.lignes || [];
  const $c = $("#rcj-depenses").empty();
  if (!rows.length) return;

  // Filtre « BL seulement » : la description porte « BL n°X » (posé par
  // l'analyse) pour toute dépense dont le justificatif est un bon de livraison.
  const est_bl = (l) => /BL n°/i.test(l.description || "");
  const nb_bl = rows.filter(est_bl).length;
  if (this._depenses_bl_seulement) rows = rows.filter(est_bl);

  const par_mode = Object.entries(dep.par_mode || {})
    .map(([m, v]) => `${esc(m)} : <b>${this._fmt(v)}</b>`).join(" · ");
  const body = rows.map((l) => {
    const style = RCJ_MODE[l.mode] || { bg: "#eee", fg: "#333" };
    return `<tr>
      <td style="white-space:nowrap">${frappe.datetime.str_to_user(l.date)}</td>
      <td>${esc(l.saisi_par || "")}</td>
      <td>${esc(l.type || "")}</td>
      <td>${est_bl(l) ? '<span class="rcj-badge" style="background:#fff7e6;color:#ad6800;border:1px solid #ffd591">📦 BL</span> ' : ""}${esc(l.description || "")}</td>
      <td><span class="rcj-badge" style="background:${style.bg};color:${style.fg}">${esc(l.mode)}</span></td>
      <td style="text-align:right;font-weight:700">${this._fmt(l.montant)}</td>
      <td style="text-align:center">${l.piece
        ? `<a href="#" class="rcj-piece" data-url="${esc(l.piece)}" title="${__("Voir le justificatif")}">📎</a>`
        : '<span style="opacity:.25">📎</span>'}</td>
      <td><a href="/app/${frappe.router.slug(l.doctype || "Journal Entry")}/${
        encodeURIComponent(l.name)}" target="_blank">${esc(l.name)}</a></td>
    </tr>`;
  }).join("");

  $c.html(`
    <div class="rcj-recap">
      <div class="rcj-recap-head" style="background:linear-gradient(90deg,#7b241c,#a93226)">
        🧾 Dépenses de la caisse — ${this._fmt(dep.total)} DT
        <span style="font-weight:400;font-size:12px;margin-left:10px">${par_mode}</span>
        ${nb_bl ? `<button type="button" class="btn btn-xs rcj-filtre-bl"
          style="float:right;background:${this._depenses_bl_seulement ? "#ffd591" : "#fff"};font-weight:700">
          📦 BL seulement (${nb_bl})</button>` : ""}
      </div>
      <div style="overflow-x:auto"><table class="rcj-recap-tbl" style="min-width:760px">
        <thead><tr><th style="text-align:left">Date</th><th style="text-align:left">Saisie par</th>
          <th style="text-align:left">Type</th><th style="text-align:left">Description</th>
          <th style="text-align:left">Mode</th><th>Montant</th><th>Pièce</th>
          <th style="text-align:left">Écriture</th></tr></thead>
        <tbody>${body}</tbody>
      </table></div>
    </div>`);
  $c.find(".rcj-filtre-bl").on("click", () => {
    this._depenses_bl_seulement = !this._depenses_bl_seulement;
    this._render_depenses();
  });
  $c.find(".rcj-piece").on("click", (e) => {
    e.preventDefault();
    rcj_apercu_url($(e.currentTarget).data("url"));
  });
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
        frappe.msgprint({
          title: __("Caisse déjà validée"),
          indicator: "green",
          message: __("La caisse « {0} » du {1} est validée : {2}{3}",
            [caisse, frappe.datetime.str_to_user(d1),
             `<a href="/app/cloture-caisse/${encodeURIComponent(e.deja_validee)}">${e.deja_validee}</a>`,
             e.pdf_url
               ? ` — <a href="${e.pdf_url}" target="_blank"><b>📄 ${__("Ouvrir le PDF")}</b></a>`
               : ""]),
        });
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
        </table>${rcj_html_rapprochement(e.rapprochement, fmt)}`);
      d.show();
    },
  });
}


// ── Rapprochement chèques / traites / dettes (caisse globale) ────────────────
// Rendu dans le dialogue de clôture : portefeuille avant + reçus − remis =
// après, et l'encours des dettes (comptable, rien à compter).
function rcj_html_rapprochement(rap, fmt) {
  if (!rap) return "";
  const l = (nom, r) => `
    <tr><td>${nom}</td>
      <td style="text-align:right">${fmt(r.avant)}</td>
      <td style="text-align:right">+ ${fmt(r.recus)}</td>
      <td style="text-align:right">− ${fmt(r.remis)}</td>
      <td style="text-align:right;font-weight:700">${fmt(r.apres)}</td></tr>`;
  return `
    <div style="margin:6px 0 4px;font-weight:700">${__("Rapprochement — chèques, traites, dettes")}</div>
    <table class="table table-bordered" style="font-size:12px">
      <tr style="background:#f6f6f6;font-weight:600"><td></td>
        <td style="text-align:right">${__("Avant")}</td>
        <td style="text-align:right">${__("Entrées")}</td>
        <td style="text-align:right">${__("Sorties")}</td>
        <td style="text-align:right">${__("Après")}</td></tr>
      ${l(__("Chèques en portefeuille"), rap.cheques)}
      ${l(__("Traites en portefeuille"), rap.traites)}
      <tr><td>${__("Encours dettes (comptable)")}</td>
        <td style="text-align:right">${fmt(rap.dettes.avant)}</td>
        <td style="text-align:right" colspan="2">${__("variation")} ${fmt(rap.dettes.variation)}</td>
        <td style="text-align:right;font-weight:700">${fmt(rap.dettes.apres)}</td></tr>
    </table>
    <div class="text-muted" style="font-size:11px;margin-bottom:6px">
      ${__("Les pièces encore en portefeuille sont listées nominativement dans le PDF — à compter physiquement.")}
    </div>`;
}


// ------------------------------------------------- paiement fractionné (UI)

// Rend les lignes de règlement dans `zone_paiements` du dialogue. `etat` porte
// `paiements` (les lignes) et `banques` ; `get_cible` rend le montant TTC visé.
// Retourne la fonction de rendu (à appeler après show / au changement du Check).
function rcj_zone_paiements(d, etat, get_cible, modes_lignes) {
  const esc = frappe.utils.escape_html;
  const $w = () => d.fields_dict.zone_paiements.$wrapper;
  // « Pas payé » comme LIGNE = paiement partiel (la part payée sort de la
  // caisse, le reste part en dette) — offert à la saisie, pas au règlement.
  const MODES_L = modes_lignes || ["Espèces", "Chèque", "Carte de crédit"];

  function rendre() {
    const actif = !!d.get_value("fractionne");
    $w().toggle(actif);
    if (!actif) return;
    if (!etat.paiements.length) {
      etat.paiements = [{ mode: "Espèces", montant: get_cible() || 0 },
                        { mode: "Chèque", montant: 0 }];
    }
    const cible = flt(get_cible());
    const somme = etat.paiements.reduce((s, p) => s + flt(p.montant), 0);
    const ok = Math.abs(somme - cible) <= 0.001;
    const banques = etat.banques || [];
    const lignes = etat.paiements.map((p, i) => `
      <div class="rcj-pay-l" data-i="${i}"
           style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:6px;
                  border:1px solid var(--border-color,#e4e8ee);border-radius:8px;padding:6px 8px;">
        <select class="form-control input-sm rcj-pay-mode" style="width:130px">
          ${MODES_L.map((m) =>
            `<option ${p.mode === m ? "selected" : ""}>${m}</option>`).join("")}
        </select>
        <input type="number" step="any" min="0" class="form-control input-sm rcj-pay-montant"
               style="width:110px;text-align:right" value="${p.montant || ""}" placeholder="${__("Montant")}">
        ${p.mode === "Chèque" ? `
          <input type="text" maxlength="7" class="form-control input-sm rcj-pay-cheque"
                 style="width:110px" value="${esc(p.n_cheque || "")}" placeholder="${__("N° chèque")}">
          <select class="form-control input-sm rcj-pay-banque" style="width:130px">
            <option value=""></option>
            ${banques.map((b) => `<option ${p.banque === b ? "selected" : ""}>${esc(b)}</option>`).join("")}
          </select>
          <button type="button" class="btn btn-default btn-xs rcj-pay-photo">📷</button>
          <span class="text-muted" style="font-size:11px">${p.photo ? "✓ " + esc(p.photo_nom || __("photo")) : __("photo du chèque")}</span>
        ` : ""}
        <button type="button" class="btn btn-default btn-xs rcj-pay-suppr" title="${__("Retirer")}">🗑</button>
      </div>`).join("");
    $w().html(`
      <div style="margin:4px 0">
        ${lignes}
        <button type="button" class="btn btn-default btn-xs rcj-pay-ajout">➕ ${__("Ajouter un règlement")}</button>
        <span style="margin-left:10px;font-weight:700;color:${ok ? "#135200" : "#a8071a"}">
          ${__("Somme")} : ${format_currency(somme, "TND")} / ${format_currency(cible, "TND")}
        </span>
      </div>`);

    const ligne_de = (e) => cint($(e.currentTarget).closest(".rcj-pay-l").data("i"));
    $w().find(".rcj-pay-mode").on("change", (e) => {
      etat.paiements[ligne_de(e)].mode = $(e.currentTarget).val(); rendre();
    });
    $w().find(".rcj-pay-montant").on("change", (e) => {
      etat.paiements[ligne_de(e)].montant = flt($(e.currentTarget).val()); rendre();
    });
    $w().find(".rcj-pay-cheque").on("change", (e) => {
      etat.paiements[ligne_de(e)].n_cheque = $(e.currentTarget).val().trim();
    });
    $w().find(".rcj-pay-banque").on("change", (e) => {
      etat.paiements[ligne_de(e)].banque = $(e.currentTarget).val();
    });
    $w().find(".rcj-pay-photo").on("click", (e) => {
      const i = ligne_de(e);
      const input = document.createElement("input");
      input.type = "file"; input.accept = "image/*";
      input.onchange = () => {
        const f = input.files && input.files[0];
        if (!f) return;
        const lecteur = new FileReader();
        lecteur.onload = () => {
          etat.paiements[i].photo = lecteur.result;
          etat.paiements[i].photo_nom = f.name;
          rendre();
        };
        lecteur.readAsDataURL(f);
      };
      input.click();
    });
    $w().find(".rcj-pay-suppr").on("click", (e) => {
      etat.paiements.splice(ligne_de(e), 1); rendre();
    });
    $w().find(".rcj-pay-ajout").on("click", () => {
      etat.paiements.push({ mode: "Chèque", montant: Math.max(0, cible - somme) });
      rendre();
    });
  }
  return rendre;
}

// Valide et retourne les lignes prêtes pour le serveur, ou null (message montré).
function rcj_collecter_paiements(etat, montant) {
  const lignes = (etat.paiements || []).filter((p) => flt(p.montant) > 0);
  if (!lignes.length) {
    frappe.msgprint(__("Ajoutez au moins un règlement avec un montant."));
    return null;
  }
  const somme = lignes.reduce((s, p) => s + flt(p.montant), 0);
  if (Math.abs(somme - flt(montant)) > 0.001) {
    frappe.msgprint(__("La somme des règlements ({0}) doit égaler le montant ({1}).",
      [format_currency(somme, "TND"), format_currency(flt(montant), "TND")]));
    return null;
  }
  for (const p of lignes) {
    if (p.mode === "Chèque") {
      if (!/^\d{7}$/.test((p.n_cheque || "").trim())) {
        frappe.msgprint(__("Chaque chèque doit avoir un numéro de 7 chiffres."));
        return null;
      }
      if (!p.banque) {
        frappe.msgprint(__("Choisissez la banque de chaque chèque."));
        return null;
      }
      if (!p.photo) {
        frappe.msgprint(__("Prenez la photo de chaque chèque."));
        return null;
      }
    }
  }
  return lignes.map((p) => ({
    mode: p.mode, montant: flt(p.montant), n_cheque: p.n_cheque || null,
    banque: p.banque || null, photo_cheque: p.photo || null,
    photo_cheque_nom: p.photo_nom || null,
  }));
}

// --------------------------------------------- dépenses à payer (découvert)

function rcj_depenses_a_payer(rapport) {
  const API = "customization_app.caisse_depenses";
  const esc = frappe.utils.escape_html;
  frappe.call({ method: API + ".depenses_a_payer" }).then((r) => {
    const rows = r.message || [];
    if (!rows.length) {
      frappe.msgprint(__("Aucune dépense en attente de paiement — tout est réglé. ✅"));
      return;
    }
    const d = new frappe.ui.Dialog({
      title: __("💸 Dépenses à payer ({0})", [rows.length]),
      size: "extra-large",
      fields: [{ fieldtype: "HTML", fieldname: "liste" }],
    });
    d.fields_dict.liste.$wrapper.html(`
      <table class="table table-bordered" style="font-size:12.5px">
        <thead><tr>
          <th>${__("Date facture")}</th><th>${__("Description")}</th>
          <th>${__("Fournisseur")}</th><th>${__("N° facture")}</th>
          <th style="text-align:right">${__("Montant")}</th>
          <th>${__("Pièce")}</th><th></th>
        </tr></thead>
        <tbody>
          ${rows.map((f) => `
            <tr>
              <td>${esc(f.date_facture || "")}</td>
              <td>${f.est_bl ? '<span class="rcj-badge" style="background:#fff7e6;color:#ad6800;border:1px solid #ffd591">📦 BL' + (f.numero_bl ? " n°" + esc(f.numero_bl) : "") + "</span> " : ""}${esc(f.description || "")}
                <div class="text-muted" style="font-size:11px">
                  <a href="/app/depense-a-payer/${encodeURIComponent(f.name)}" target="_blank">${esc(f.name)}</a>
                  ${f.journal_entry ? ` · <a href="/app/journal-entry/${encodeURIComponent(f.journal_entry)}" target="_blank">${esc(f.journal_entry)}</a>` : ""}
                </div></td>
              <td>${esc(f.fournisseur || "")}</td>
              <td>${esc(f.numero_facture || "")}</td>
              <td style="text-align:right;font-weight:700">${format_currency(f.montant, "TND")}</td>
              <td style="text-align:center">${f.piece
                ? `<a href="#" class="rcj-piece" data-url="${esc(f.piece)}" title="${__("Voir le justificatif")}">📎</a>`
                : '<span style="opacity:.25">📎</span>'}</td>
              <td><button class="btn btn-danger btn-xs rcj-payer" data-fiche="${esc(f.name)}">💸 ${__("Payer")}</button></td>
            </tr>`).join("")}
        </tbody>
      </table>
      <div class="text-muted" style="font-size:11px">
        ${__("La charge est déjà comptabilisée contre le compte de découvert — le paiement génère l'écriture de règlement au jour choisi, et la dépense entre au rapport de caisse ce jour-là.")}
      </div>`);
    d.fields_dict.liste.$wrapper.find(".rcj-payer").on("click", (e) => {
      const fiche = rows.find((f) => f.name === $(e.currentTarget).data("fiche"));
      if (!fiche) return;
      d.hide();
      rcj_payer_depense(fiche, rapport);
    });
    d.fields_dict.liste.$wrapper.find(".rcj-piece").on("click", (e) => {
      e.preventDefault();
      rcj_apercu_url($(e.currentTarget).data("url"));
    });
    d.show();
  });
}

function rcj_payer_depense(fiche, rapport) {
  const API = "customization_app.caisse_depenses";
  const etat = { cheque: null, cheque_nom: null, paiements: [], banques: [] };
  const d = new frappe.ui.Dialog({
    title: __("💸 Payer — {0}", [fiche.description || fiche.name]),
    size: "large",
    fields: [
      { fieldtype: "Date", fieldname: "date_reglement", label: __("Date du paiement"),
        default: frappe.datetime.get_today(), reqd: 1 },
      { fieldtype: "Column Break" },
      { fieldtype: "Currency", fieldname: "montant", label: __("Montant"),
        default: fiche.montant, read_only: 1 },
      { fieldtype: "Section Break", label: __("Règlement") },
      { fieldtype: "Check", fieldname: "fractionne",
        label: __("Paiement fractionné (plusieurs chèques, ou espèces + chèque…)") },
      { fieldtype: "Select", fieldname: "mode", label: __("Mode de paiement"),
        options: "Espèces\nChèque\nCarte de crédit", default: "Espèces", reqd: 1,
        depends_on: "eval:!doc.fractionne" },
      { fieldtype: "Column Break" },
      { fieldtype: "Data", fieldname: "n_cheque", label: __("N° de chèque (7 chiffres)"),
        depends_on: 'eval:doc.mode=="Chèque" && !doc.fractionne' },
      { fieldtype: "Select", fieldname: "banque", label: __("Banque"),
        depends_on: 'eval:doc.mode=="Chèque" && !doc.fractionne' },
      { fieldtype: "HTML", fieldname: "zone_cheque" },
      { fieldtype: "HTML", fieldname: "zone_paiements" },
    ],
    primary_action_label: __("💸 Enregistrer le paiement"),
    primary_action(v) {
      let paiements = null;
      if (v.fractionne) {
        paiements = rcj_collecter_paiements(etat, fiche.montant);
        if (!paiements) return;
      } else if (v.mode === "Chèque") {
        if (!/^\d{7}$/.test((v.n_cheque || "").trim())) {
          frappe.msgprint(__("Le numéro de chèque doit comporter exactement 7 chiffres."));
          return;
        }
        if (!v.banque) {
          frappe.msgprint(__("Pour un chèque, la banque est obligatoire."));
          return;
        }
        if (!etat.cheque) {
          frappe.msgprint(__("Prenez la photo du chèque avant d'enregistrer."));
          return;
        }
      }
      frappe.call({
        method: API + ".solder_depense",
        args: {
          fiche: fiche.name, date_reglement: v.date_reglement,
          mode: v.mode, n_cheque: v.n_cheque, banque: v.banque,
          photo_cheque: !paiements && v.mode === "Chèque" ? etat.cheque : null,
          photo_cheque_nom: etat.cheque_nom,
          paiements: paiements ? JSON.stringify(paiements) : null,
        },
        freeze: true, freeze_message: __("Enregistrement du paiement…"),
        callback: (r) => {
          d.hide();
          frappe.show_alert({
            message: __("Dépense payée — écriture {0}.", [r.message.name]),
            indicator: "green",
          });
          if (rapport && rapport._fetch) rapport._fetch();
        },
      });
    },
  });

  // photo du chèque (mode simple)
  const $zc = d.fields_dict.zone_cheque.$wrapper;
  $zc.html(`
    <div style="margin:4px 0">
      <button type="button" class="btn btn-default btn-sm rcj-ph-btn">📷 ${__("Photo du chèque")}</button>
      <span class="rcj-ph-nom text-muted" style="margin-left:8px"></span>
      <input type="file" accept="image/*" style="display:none">
    </div>`);
  const $input = $zc.find("input[type=file]");
  $zc.find(".rcj-ph-btn").on("click", () => $input.trigger("click"));
  $input.on("change", function () {
    const f = this.files && this.files[0];
    if (!f) return;
    const lecteur = new FileReader();
    lecteur.onload = () => {
      etat.cheque = lecteur.result;
      etat.cheque_nom = f.name;
      $zc.find(".rcj-ph-nom").text("✓ " + f.name);
    };
    lecteur.readAsDataURL(f);
  });
  const basculer_cheque = () =>
    $zc.toggle(d.get_value("mode") === "Chèque" && !d.get_value("fractionne"));

  const rendre_paiements = rcj_zone_paiements(d, etat, () => flt(fiche.montant));
  d.fields_dict.mode.$input.on("change", basculer_cheque);
  d.fields_dict.fractionne.$input.on("change", () => { basculer_cheque(); rendre_paiements(); });

  frappe.call({
    method: "customization_app.caisse_encaissement_dettes.banques",
    callback: (r) => {
      etat.banques = r.message || [];
      d.set_df_property("banque", "options", [""].concat(etat.banques).join("\n"));
    },
  });

  d.show();
  basculer_cheque();
  rendre_paiements();
}


// ------------------------------------------------ aperçu pièce jointe (popup)

function rcj_apercu_url(url, titre) {
  if (!url) return;
  const esc = frappe.utils.escape_html;
  const d = new frappe.ui.Dialog({
    title: titre || __("📎 Justificatif"),
    size: "extra-large",
    fields: [{ fieldtype: "HTML", fieldname: "zone" }],
  });
  const est_pdf = /\.pdf($|\?)/i.test(url);
  d.fields_dict.zone.$wrapper.html(`
    ${est_pdf
      ? `<iframe src="${esc(url)}" style="width:100%;height:72vh;border:1px solid var(--border-color,#e4e8ee);border-radius:8px"></iframe>`
      : `<div style="text-align:center"><img src="${esc(url)}"
           style="max-width:100%;max-height:72vh;border:1px solid var(--border-color,#e4e8ee);border-radius:8px"></div>`}
    <div style="margin-top:6px;text-align:right">
      <a href="${esc(url)}" target="_blank">${__("Ouvrir dans un onglet")} ↗</a>
    </div>`);
  d.show();
}

// ------------------------------------ dépenses BL (toutes périodes, facture)

function rcj_depenses_bl(rapport) {
  const API = "customization_app.caisse_depenses";
  const esc = frappe.utils.escape_html;
  frappe.call({ method: API + ".depenses_bl" }).then((r) => {
    const rows = r.message || [];
    if (!rows.length) {
      frappe.msgprint(__("Aucune dépense sur bon de livraison."));
      return;
    }
    const etat = { facture: null, facture_nom: null };
    const sans_facture = rows.filter((f) => !f.numero_facture).length;
    const d = new frappe.ui.Dialog({
      title: __("📦 Dépenses sur BL ({0} — dont {1} sans facture)", [rows.length, sans_facture]),
      size: "extra-large",
      fields: [
        { fieldtype: "HTML", fieldname: "liste" },
        { fieldtype: "Section Break", label: __("🧾 Facture reçue (couvre les BL cochés)") },
        { fieldtype: "Data", fieldname: "numero_facture", label: __("N° de la facture"), reqd: 1 },
        { fieldtype: "Column Break" },
        { fieldtype: "Date", fieldname: "date_facture", label: __("Date de la facture") },
        { fieldtype: "Column Break" },
        { fieldtype: "HTML", fieldname: "zone_photo" },
      ],
      primary_action_label: __("🧾 Attacher la facture aux BL cochés"),
      primary_action(v) {
        const choisis = d.fields_dict.liste.$wrapper
          .find(".rcj-bl-choix:checked").map((_, el) => $(el).val()).get();
        if (!choisis.length) {
          frappe.msgprint(__("Cochez au moins un BL."));
          return;
        }
        if (!(v.numero_facture || "").trim()) {
          frappe.msgprint(__("Saisissez le numéro de la facture reçue."));
          return;
        }
        frappe.call({
          method: API + ".attacher_facture_bl",
          args: {
            fiches: JSON.stringify(choisis),
            numero_facture: v.numero_facture,
            date_facture: v.date_facture || null,
            photo_facture: etat.facture,
            photo_facture_nom: etat.facture_nom,
          },
          freeze: true, freeze_message: __("Rattachement de la facture…"),
          callback: (res) => {
            d.hide();
            frappe.show_alert({
              message: __("Facture n° {0} attachée à {1} BL. ✅",
                [v.numero_facture, (res.message.factures || []).length]),
              indicator: "green",
            }, 7);
            rcj_depenses_bl(rapport);   // rouvrir la liste mise à jour
          },
        });
      },
    });

    const maj_total = () => {
      let total = 0;
      d.fields_dict.liste.$wrapper.find(".rcj-bl-choix:checked").each((_, el) => {
        total += flt($(el).data("montant"));
      });
      d.fields_dict.liste.$wrapper.find(".rcj-bl-total").text(format_currency(total, "TND"));
    };

    d.fields_dict.liste.$wrapper.html(`
      <div style="max-height:46vh;overflow-y:auto">
      <table class="table table-bordered" style="font-size:12.5px">
        <thead><tr>
          <th style="width:30px"><input type="checkbox" class="rcj-bl-tout" title="${__("Cocher les BL sans facture")}"></th>
          <th>${__("Date")}</th><th>${__("Description")}</th>
          <th>${__("Statut")}</th><th>${__("N° BL")}</th>
          <th>${__("Facture")}</th>
          <th style="text-align:right">${__("Montant")}</th><th>${__("Pièce")}</th>
        </tr></thead>
        <tbody>
          ${rows.map((f) => `
            <tr>
              <td><input type="checkbox" class="rcj-bl-choix" value="${esc(f.name)}"
                         data-montant="${f.montant}" data-sf="${f.numero_facture ? 0 : 1}"></td>
              <td style="white-space:nowrap">${esc(f.date || "")}</td>
              <td>${esc(f.description || "")}
                <div class="text-muted" style="font-size:11px">
                  <a href="/app/depense-a-payer/${encodeURIComponent(f.name)}" target="_blank">${esc(f.name)}</a>
                  ${f.journal_entry ? ` · <a href="/app/journal-entry/${encodeURIComponent(f.journal_entry)}" target="_blank">${esc(f.journal_entry)}</a>` : ""}
                </div></td>
              <td><span class="rcj-badge" style="${f.statut === "Payée"
                    ? "background:#f6ffed;color:#135200;border:1px solid #b7eb8f"
                    : "background:#fff1f0;color:#a8071a;border:1px solid #ffa39e"}">${esc(f.statut)}</span></td>
              <td>${esc(f.numero_bl || "")}</td>
              <td>${f.numero_facture
                    ? `<span class="rcj-badge" style="background:#e6f4ff;color:#0958d9;border:1px solid #91caff">🧾 ${esc(f.numero_facture)}</span>`
                    : `<span class="rcj-badge" style="background:#fff7e6;color:#ad6800;border:1px solid #ffd591">${__("sans facture")}</span>`}</td>
              <td style="text-align:right;font-weight:700">${format_currency(f.montant, "TND")}</td>
              <td style="text-align:center">${f.piece
                    ? `<a href="#" class="rcj-piece" data-url="${esc(f.piece)}" title="${__("Voir le justificatif")}">📎</a>`
                    : '<span style="opacity:.25">📎</span>'}</td>
            </tr>`).join("")}
        </tbody>
      </table></div>
      <div style="font-weight:700;margin-top:4px">${__("Total sélectionné")} :
        <span class="rcj-bl-total">0</span></div>
      <div class="text-muted" style="font-size:11px">
        ${__("Cochez le ou les BL couverts par la facture reçue : un seul = le BL devient la facture ; plusieurs = facture globale. Rien n'est modifié en comptabilité — la facture s'attache aux fiches et aux écritures comme preuve.")}
      </div>`);
    const $w = d.fields_dict.liste.$wrapper;
    $w.find(".rcj-bl-choix").on("change", maj_total);
    $w.find(".rcj-bl-tout").on("change", function () {
      $w.find('.rcj-bl-choix[data-sf="1"]').prop("checked", this.checked);
      maj_total();
    });
    $w.find(".rcj-piece").on("click", (e) => {
      e.preventDefault();
      rcj_apercu_url($(e.currentTarget).data("url"));
    });

    // photo / PDF de la facture reçue
    const $zp = d.fields_dict.zone_photo.$wrapper;
    $zp.html(`
      <div style="margin:4px 0">
        <button type="button" class="btn btn-default btn-sm rcj-ph-btn">📷 ${__("Photo ou PDF de la facture")}</button>
        <span class="rcj-ph-nom text-muted" style="margin-left:8px"></span>
        <input type="file" accept="image/*,application/pdf" style="display:none">
      </div>`);
    const $input = $zp.find("input[type=file]");
    $zp.find(".rcj-ph-btn").on("click", () => $input.trigger("click"));
    $input.on("change", function () {
      const f = this.files && this.files[0];
      if (!f) return;
      const lecteur = new FileReader();
      lecteur.onload = () => {
        etat.facture = lecteur.result;
        etat.facture_nom = f.name;
        $zp.find(".rcj-ph-nom").text("✓ " + f.name);
      };
      lecteur.readAsDataURL(f);
    });

    d.show();
  });
}


// ---------------------------------------- achats fournisseurs (fiches FAS)

function rcj_factures_a_payer() {
  const API = "customization_app.caisse_depenses";
  const esc = frappe.utils.escape_html;
  frappe.call({ method: API + ".factures_a_payer" }).then((r) => {
    const m = r.message || {};
    const factures = m.factures || [];
    const fiches = m.fiches || [];
    if (!factures.length && !fiches.length) {
      frappe.msgprint(__("Rien à payer côté fournisseurs. ✅"));
      return;
    }
    const d = new frappe.ui.Dialog({
      title: __("💸 Factures à payer ({0} factures · {1} captures)", [factures.length, fiches.length]),
      size: "extra-large",
      fields: [{ fieldtype: "HTML", fieldname: "liste" }],
    });
    const total_encours = factures.reduce((s2, f) => s2 + flt(f.outstanding_amount), 0);
    d.fields_dict.liste.$wrapper.html(`
      <div style="max-height:60vh;overflow-y:auto">
      ${factures.length ? `
      <div style="font-weight:700;margin-bottom:4px;color:#a8071a">
        🧾 ${__("Factures d’achat non soldées")} — ${__("encours")} : ${format_currency(total_encours, "TND")}</div>
      <table class="table table-bordered" style="font-size:12.5px">
        <thead><tr>
          <th>${__("Date")}</th><th>${__("Fournisseur")}</th><th>${__("N° facture")}</th>
          <th>${__("Échéance")}</th>
          <th style="text-align:right">${__("Total")}</th>
          <th style="text-align:right">${__("Reste à payer")}</th>
          <th>${__("Pièce")}</th>
        </tr></thead>
        <tbody>
          ${factures.map((f) => `
            <tr>
              <td style="white-space:nowrap">${esc(f.posting_date || "")}
                <div class="text-muted" style="font-size:11px">
                  <a href="/app/purchase-invoice/${encodeURIComponent(f.name)}" target="_blank">${esc(f.name)}</a></div></td>
              <td>${esc(f.supplier || "")}</td>
              <td>${esc(f.bill_no || "")}</td>
              <td>${esc(f.due_date || "")}</td>
              <td style="text-align:right">${format_currency(f.montant, "TND")}</td>
              <td style="text-align:right;font-weight:700;color:#a8071a">${format_currency(f.outstanding_amount, "TND")}</td>
              <td style="text-align:center">${f.piece
                ? `<a href="#" class="rcj-piece" data-url="${esc(f.piece)}" title="${__("Voir le justificatif")}">📎</a>`
                : '<span style="opacity:.25">📎</span>'}</td>
            </tr>`).join("")}
        </tbody>
      </table>` : ""}
      ${fiches.length ? `
      <div style="font-weight:700;margin:8px 0 4px;color:#ad6800">
        📷 ${__("Captures caisse non payées (à comptabiliser)")}</div>
      <table class="table table-bordered" style="font-size:12.5px">
        <thead><tr>
          <th>${__("Date")}</th><th>${__("Fournisseur")}</th>
          <th>${__("N° facture / BL")}</th><th>${__("Mode")}</th>
          <th style="text-align:right">${__("Montant")}</th><th>${__("Pièce")}</th>
        </tr></thead>
        <tbody>
          ${fiches.map((f) => `
            <tr>
              <td style="white-space:nowrap">${esc(f.date || "")}
                <div class="text-muted" style="font-size:11px">
                  <a href="/app/facture-achat-a-saisir/${encodeURIComponent(f.name)}" target="_blank">${esc(f.name)}</a>
                  ${f.est_bl ? ' <span class="rcj-badge" style="background:#fff7e6;color:#ad6800;border:1px solid #ffd591">📦 BL</span>' : ""}</div></td>
              <td>${esc(f.fournisseur || f.supplier || "")}</td>
              <td>${esc(f.numero_facture || f.numero_bl || "")}</td>
              <td><span class="rcj-badge" style="background:#fff1f0;color:#a8071a;border:1px solid #ffa39e">${esc(f.mode_paiement || "")}</span></td>
              <td style="text-align:right;font-weight:700">${format_currency(f.montant, "TND")}</td>
              <td style="text-align:center">${f.piece
                ? `<a href="#" class="rcj-piece" data-url="${esc(f.piece)}">📎</a>`
                : '<span style="opacity:.25">📎</span>'}</td>
            </tr>`).join("")}
        </tbody>
      </table>` : ""}
      </div>`);
    d.fields_dict.liste.$wrapper.find(".rcj-piece").on("click", (e) => {
      e.preventDefault();
      rcj_apercu_url($(e.currentTarget).data("url"));
    });
    d.show();
  });
}

function rcj_factures_sans_justif() {
  const API = "customization_app.caisse_depenses";
  const esc = frappe.utils.escape_html;
  frappe.call({ method: API + ".factures_sans_justificatif" }).then((r) => {
    const rows = r.message || [];
    if (!rows.length) {
      frappe.msgprint(__("Toutes les factures d’achat depuis le 01-01-2026 ont un justificatif. ✅"));
      return;
    }
    const d = new frappe.ui.Dialog({
      title: __("🗂 Factures sans justificatifs ({0})", [rows.length]),
      size: "extra-large",
      fields: [{ fieldtype: "HTML", fieldname: "liste" }],
    });
    d.fields_dict.liste.$wrapper.html(`
      <div style="max-height:60vh;overflow-y:auto">
      <table class="table table-bordered" style="font-size:12.5px">
        <thead><tr>
          <th>${__("Date")}</th><th>${__("Facture")}</th><th>${__("Fournisseur")}</th>
          <th>${__("N° fournisseur")}</th>
          <th style="text-align:right">${__("Total")}</th>
          <th style="text-align:right">${__("Reste à payer")}</th>
        </tr></thead>
        <tbody>
          ${rows.map((f) => `
            <tr>
              <td style="white-space:nowrap">${esc(f.posting_date || "")}</td>
              <td><a href="/app/purchase-invoice/${encodeURIComponent(f.name)}" target="_blank">${esc(f.name)}</a></td>
              <td>${esc(f.supplier || "")}</td>
              <td>${esc(f.bill_no || "")}</td>
              <td style="text-align:right">${format_currency(f.rounded_total || f.grand_total, "TND")}</td>
              <td style="text-align:right">${format_currency(f.outstanding_amount, "TND")}</td>
            </tr>`).join("")}
        </tbody>
      </table></div>
      <div class="text-muted" style="font-size:11px">
        ${__("Ouvre la facture et joins le scan (ou capture-la en caisse : l’appariement par n° copiera le justificatif automatiquement).")}
      </div>`);
    d.show();
  });
}

function rcj_factures_bl(rapport) {
  const API = "customization_app.caisse_depenses";
  const esc = frappe.utils.escape_html;
  frappe.call({ method: API + ".factures_bl" }).then((r) => {
    const rows = r.message || [];
    if (!rows.length) {
      frappe.msgprint(__("Aucun achat sur bon de livraison."));
      return;
    }
    const sans_cmd = rows.filter((f) => !f.purchase_order && f.statut === "À saisir").length;
    const d = new frappe.ui.Dialog({
      title: __("📦 Achats sur BL ({0} — dont {1} sans commande)", [rows.length, sans_cmd]),
      size: "extra-large",
      fields: [{ fieldtype: "HTML", fieldname: "liste" }],
      primary_action_label: __("🧾 Créer la facture depuis les commandes cochées"),
      primary_action() {
        const choisis = d.fields_dict.liste.$wrapper
          .find(".rcj-fbl-choix:checked").map((_, el) => $(el).val()).get();
        if (!choisis.length) {
          frappe.msgprint(__("Cochez au moins un BL dont la commande est soumise."));
          return;
        }
        frappe.call({
          method: API + ".creer_facture_depuis_commandes",
          args: { fiches: JSON.stringify(choisis) },
          freeze: true, freeze_message: __("Création de la facture depuis les commandes…"),
          callback: (res) => {
            d.hide();
            frappe.show_alert({
              message: __("Facture {0} créée depuis {1} commande(s) — complétez le n° fournisseur puis soumettez.",
                [res.message.purchase_invoice, (res.message.commandes || []).length]),
              indicator: "green",
            }, 8);
            frappe.set_route("Form", "Purchase Invoice", res.message.purchase_invoice);
          },
        });
      },
    });
    const maj_total = () => {
      let total = 0;
      d.fields_dict.liste.$wrapper.find(".rcj-fbl-choix:checked").each((_, el) => {
        total += flt($(el).data("montant"));
      });
      d.fields_dict.liste.$wrapper.find(".rcj-fbl-total").text(format_currency(total, "TND"));
    };
    d.fields_dict.liste.$wrapper.html(`
      <div style="max-height:52vh;overflow-y:auto">
      <table class="table table-bordered" style="font-size:12.5px">
        <thead><tr>
          <th style="width:30px" title="${__("Cochables : BL avec commande soumise, sans facture")}"></th>
          <th>${__("Date")}</th><th>${__("Fournisseur")}</th><th>${__("N° BL")}</th>
          <th>${__("Statut")}</th><th>${__("Payé")}</th>
          <th style="text-align:right">${__("Montant")}</th>
          <th>${__("Commande")}</th><th>${__("Facture")}</th><th>${__("Pièce")}</th>
        </tr></thead>
        <tbody>
          ${rows.map((f) => `
            <tr>
              <td>${f.purchase_order && f.po_docstatus === 1 && !f.purchase_invoice && f.statut === "À saisir"
                    ? `<input type="checkbox" class="rcj-fbl-choix" value="${esc(f.name)}" data-montant="${f.montant}">`
                    : ""}</td>
              <td style="white-space:nowrap">${esc(f.date || "")}
                <div class="text-muted" style="font-size:11px">
                  <a href="/app/facture-achat-a-saisir/${encodeURIComponent(f.name)}" target="_blank">${esc(f.name)}</a></div></td>
              <td>${esc(f.fournisseur || f.supplier || "")}</td>
              <td>${esc(f.numero_bl || "")}</td>
              <td><span class="rcj-badge" style="${f.statut === "Saisie"
                    ? "background:#f6ffed;color:#135200;border:1px solid #b7eb8f"
                    : "background:#fff7e6;color:#ad6800;border:1px solid #ffd591"}">${esc(f.statut)}</span></td>
              <td>${f.mode_paiement === "Pas payé" ? "—" : esc(f.mode_paiement || "")}</td>
              <td style="text-align:right;font-weight:700">${format_currency(f.montant, "TND")}</td>
              <td>${f.purchase_order
                    ? `<a href="/app/purchase-order/${encodeURIComponent(f.purchase_order)}" target="_blank">${esc(f.purchase_order)}</a>
                       ${f.po_docstatus !== 1 ? ` <span class="rcj-badge" style="background:#fff7e6;color:#ad6800;border:1px solid #ffd591">${__("brouillon — à soumettre")}</span>` : ""}`
                    : (f.statut === "À saisir"
                        ? `<button class="btn btn-warning btn-xs rcj-creer-cmd"
                                   data-fiche="${esc(f.name)}" data-supplier="${esc(f.supplier || "")}">➕ ${__("Commande")}</button>`
                        : "—")}</td>
              <td>${f.purchase_invoice
                    ? `<a href="/app/purchase-invoice/${encodeURIComponent(f.purchase_invoice)}" target="_blank">${esc(f.purchase_invoice)}</a>`
                    : `<span class="rcj-badge" style="background:#fff7e6;color:#ad6800;border:1px solid #ffd591">${__("sans facture")}</span>`}</td>
              <td style="text-align:center">${f.piece
                    ? `<a href="#" class="rcj-piece" data-url="${esc(f.piece)}">📎</a>`
                    : '<span style="opacity:.25">📎</span>'}</td>
            </tr>`).join("")}
        </tbody>
      </table></div>
      <div style="font-weight:700;margin-top:4px">${__("Total sélectionné")} :
        <span class="rcj-fbl-total">0</span></div>
      <div class="text-muted" style="font-size:11px">
        ${__("Chaîne : BL capturé → « ➕ Commande » (le comptable saisit les articles ; à la SOUMISSION de la commande, l’avance de caisse devient un paiement lié) → cochez une ou plusieurs commandes du même fournisseur → « Créer la facture » : les avances suivent et s’allouent à la facture.")}
      </div>`);
    const $w = d.fields_dict.liste.$wrapper;
    $w.find(".rcj-fbl-choix").on("change", maj_total);
    $w.find(".rcj-piece").on("click", (e) => {
      e.preventDefault();
      rcj_apercu_url($(e.currentTarget).data("url"));
    });
    $w.find(".rcj-creer-cmd").on("click", (e) => {
      const fiche = $(e.currentTarget).data("fiche");
      const supplier = $(e.currentTarget).data("supplier");
      d.hide();
      // la commande s ouvre pré-remplie ; à l enregistrement le hook serveur lie
      // la fiche, et à la SOUMISSION l avance devient un paiement de la commande.
      frappe.new_doc("Purchase Order", {
        supplier: supplier,
        custom_fiche_caisse: fiche,
      });
    });
    d.show();
  });
}
