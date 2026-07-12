frappe.pages["facturation-auto"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({
    parent: wrapper,
    title: "Facturation Auto",
    single_column: true,
  });
  $(wrapper).find(".layout-main-section").html(
    frappe.render_template("facturation_auto", {})
  );
  new FacturationAuto(wrapper);
};

const FAC_ALLOWED_USER = "koubaawassim@gmail.com";

class FacturationAuto {
  constructor(wrapper) {
    this.wrapper = wrapper;
    if (frappe.session.user !== FAC_ALLOWED_USER) {
      $("#fac-main").hide();
      $("#fac-denied").show();
      return;
    }
    this._bind();
    this._loadState();
  }

  async _loadState() {
    const useGaps = $("#fac-use-gaps").is(":checked") ? 1 : 0;
    $("#fac-state").html(
      '<div class="fac-state-card"><div class="fac-state-loading">⏳ Lecture de l’état de la base…</div></div>'
    );
    try {
      const r = await frappe.call({
        method: "customization_app.facturation_auto.preview",
        args: { use_gaps: useGaps },
      });
      this._render_state(r.message || {});
    } catch (e) {
      $("#fac-state").html(
        `<div class="fac-state-card"><div class="fac-state-loading">État indisponible : ${frappe.utils.escape_html(String(e))}</div></div>`
      );
    }
  }

  _render_state(s) {
    if (!s || !Object.keys(s).length) {
      $("#fac-state").empty();
      return;
    }
    const gaps = s.initial_gaps_count || 0;
    const gapRange = gaps
      ? `de ${this._v(s.first_available_gap)} à ${this._v(s.initial_last_gap)}`
      : "aucun trou";
    $("#fac-state").html(`
      <div class="fac-state-card">
        <div class="fac-state-head">📊 État actuel de la base — avant lancement</div>
        <div class="fac-state-grid">
          <div class="fac-state-box">
            <div class="lbl">Dernier n° facture (M-1)</div>
            <div class="val">${this._v(s.last_previous_fac_num)}</div>
            <div class="sub">mois précédent la période</div>
          </div>
          <div class="fac-state-box">
            <div class="lbl">1er n° facture (M+1)</div>
            <div class="val">${this._v(s.first_fac_num_next_month)}</div>
            <div class="sub">déjà en base${s.next_month_start ? ` · dès ${s.next_month_start}` : ""}</div>
          </div>
          <div class="fac-state-box accent">
            <div class="lbl">Trous de numérotation</div>
            <div class="val">${gaps}</div>
            <div class="sub">${gapRange}</div>
          </div>
          <div class="fac-state-box">
            <div class="lbl">Prochain n° (sans trous)</div>
            <div class="val">${this._v(s.next_invoice_number_without_gaps)}</div>
            <div class="sub">1er n° du mois : ${this._v(s.current_month_first_fac_num)} · dernier : ${this._v(s.current_month_last_fac_num)}</div>
          </div>
          <div class="fac-state-box">
            <div class="lbl">Paiements éligibles</div>
            <div class="val">${s.payments_found == null ? "—" : s.payments_found}</div>
            <div class="sub">du mois à facturer</div>
          </div>
        </div>
        <div class="fac-state-period">Période facturée : ${s.date_start || ""} → ${s.date_end || ""} — lecture seule, rien n’est enregistré.</div>
      </div>`);
  }

  _bind() {
    $("#fac-dry").on("click", () => this._launch(true));
    $("#fac-commit").on("click", () => this._confirm_commit());
    $(this.wrapper).on("click", ".fac-doc-link", (e) => {
      e.preventDefault();
      const $a = $(e.currentTarget);
      this._open_modal($a.attr("data-dt"), $a.attr("data-dn"));
    });
  }

  _params() {
    const num = $("#fac-last-num").val();
    return {
      use_gaps: $("#fac-use-gaps").is(":checked") ? 1 : 0,
      create_leftover: $("#fac-leftover").is(":checked") ? 1 : 0,
      verbose: $("#fac-verbose").is(":checked") ? 1 : 0,
      last_fac_num: (num === "" || num == null) ? "" : num,
      passager_factor: $("#fac-passager").val() || "0.5",
    };
  }

  _confirm_commit() {
    frappe.confirm(
      "⚠️ Génération RÉELLE : les factures du mois précédent vont être créées et soumises. Continuer ?",
      () => this._launch(false)
    );
  }

  async _launch(dry_run) {
    const p = this._params();
    this._loading(true);
    $("#fac-result").empty();
    try {
      const r = await frappe.call({
        method: "customization_app.facturation_auto.run",
        args: { dry_run: dry_run ? 1 : 0, ...p },
        freeze: true,
        freeze_message: dry_run ? "Simulation en cours…" : "Génération en cours…",
      });
      this._render(r.message || {});
    } catch (e) {
      frappe.msgprint({ title: "Erreur", message: String(e), indicator: "red" });
    } finally {
      this._loading(false);
    }
  }

  _render(res) {
    if (!res || !Object.keys(res).length) {
      $("#fac-result").html('<div class="fac-muted">Aucun résultat.</div>');
      return;
    }
    if (res.error) {
      $("#fac-result").html(`<div class="fac-errors"><b>${frappe.utils.escape_html(res.message || "Erreur")}</b><br>${frappe.utils.escape_html(res.error)}</div>`);
      return;
    }
    const dry = !!res.dry_run;
    const cls = dry ? "dry" : "commit";
    const badge = dry ? "DRY-RUN" : "COMMIT";

    const banner = `<div class="fac-banner ${cls}">
      <span class="fac-badge ${cls}">${badge}</span>
      <span>${frappe.utils.escape_html(res.message || "")}</span>
      <span style="margin-left:auto; font-weight:600;">Période : ${res.date_start || ""} → ${res.date_end || ""}</span>
    </div>`;

    const kpis = `<div class="fac-kpis">
      ${this._kpi("Factures créées", res.created_invoices_count, "#2b4c7e",
        `${res.payment_invoices_created_count || 0} paiement · ${res.leftover_invoices_created_count || 0} PASSAGER`)}
      ${this._kpi("Total factures", this._fmt(res.created_invoices_total), "#2e9e5b", "DT")}
      ${this._kpi("Total paiements/cibles", this._fmt(res.created_payments_or_targets_total), "#12908a", "DT")}
      ${this._kpi("Écart", this._fmt(res.created_difference_total), Math.abs(res.created_difference_total) > 0.01 ? "#c0392b" : "#6b7280", "DT")}
      ${this._kpi("Paiements trouvés", res.payments_found, "#7a4bb0", this._fmt(res.payments_amount_total_found) + " DT")}
      ${this._kpi("Trous restants", res.final_gaps_count, "#b8901f", `initiaux : ${res.initial_gaps_count}`)}
    </div>`;

    const numbering = `<div class="fac-sub">🔢 Numérotation & trous</div>
      <div class="fac-card"><div class="fac-kv">
        <div><b>Dernière facture (mois préc.)</b> : ${this._v(res.last_previous_fac_num)}</div>
        <div><b>Cible dernière facture</b> : ${this._v(res.target_last_fac_num)} ${res.manual_last_fac_num ? "(manuel)" : ""}</div>
        <div><b>1re facture du mois</b> : ${this._v(res.current_month_first_fac_num)}</div>
        <div><b>Dernière facture du mois</b> : ${this._v(res.current_month_last_fac_num)}</div>
        <div><b>1er trou disponible</b> : ${this._v(res.first_available_gap)}</div>
        <div><b>Prochain n° sans trous</b> : ${this._v(res.next_invoice_number_without_gaps)}</div>
        <div><b>1er n° facture du mois suivant (en base)</b> : ${this._v(res.first_fac_num_next_month)}${res.next_month_start ? ` <span class="fac-muted">(dès ${res.next_month_start})</span>` : ""}</div>
      </div></div>`;

    const payments = `<div class="fac-sub">💳 Paiements</div>
      <div class="fac-card"><div class="fac-kv">
        <div><b>Trouvés (éligibles)</b> : ${res.payments_found} — ${this._fmt(res.payments_amount_total_found)} DT</div>
        <div><b>À facturer (&gt; 1 DT)</b> : ${res.payments_count_gt_1} — ${this._fmt(res.payments_amount_gt_1)} DT</div>
        <div><b>Non facturés (≤ 1 DT)</b> : ${res.payments_count_lte_1} — ${this._fmt(res.payments_amount_lte_1)} DT</div>
        <div><b>Facteur PASSAGER</b> : ${res.passager_factor} → cible ${this._fmt(res.passager_target_total)} DT</div>
      </div></div>`;

    const paymentsTable = this._payments_table(res.payments_detail || []);
    const totals = this._totals(res);
    const invoices = this._invoices_table(res.created_invoices || []);
    const coherence = this._coherence(res);
    const errors = (res.errors && res.errors.length)
      ? `<div class="fac-sub" style="color:#b02a37;">❌ Erreurs (${res.errors_count})</div>
         <div class="fac-errors"><ul>${res.errors.map(e => `<li>${frappe.utils.escape_html(typeof e === "string" ? e : JSON.stringify(e))}</li>`).join("")}</ul></div>`
      : "";

    $("#fac-result").html(banner + kpis + numbering + totals + payments + paymentsTable + invoices + coherence + errors);
  }

  _payments_table(list) {
    const nonFac = list.filter(p => !p.to_invoice).length;
    const title = `<div class="fac-sub">💳 Paiements éligibles du mois ${list.length ? "(" + list.length + ", dont " + nonFac + " non facturés)" : ""}</div>`;
    if (!list.length) return title + '<div class="fac-card fac-muted">Aucun paiement éligible.</div>';
    const rows = list.map(p => {
      const badge = p.to_invoice
        ? '<span class="fac-badge commit">À FACTURER</span>'
        : '<span class="fac-badge" style="background:#6b7280;color:#fff;">IGNORÉ ≤1</span>';
      return `<tr${p.to_invoice ? "" : ' style="background:#f4f5f7;"'}>
        <td>${this._link("Payment Entry", p.payment_entry)}</td>
        <td class="fac-muted">${p.date || ""}</td>
        <td>${frappe.utils.escape_html(p.customer || "")}</td>
        <td class="num">${this._fmt(p.paid_amount)}</td>
        <td>${p.sales_order ? this._link("Sales Order", p.sales_order) : '<span class="fac-muted">—</span>'}</td>
        <td style="font-size:11px;">${frappe.utils.escape_html(p.reference || "")}</td>
        <td>${badge}</td>
      </tr>`;
    }).join("");
    return title + `<div class="fac-card" style="padding:0; overflow-x:auto;">
      <table class="fac-tbl">
        <thead><tr>
          <th>Paiement</th><th>Date</th><th>Client</th><th class="num">Montant</th>
          <th>Commande</th><th>Info</th><th>Statut</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table></div>`;
  }

  _invoices_table(list) {
    const title = `<div class="fac-sub">🧾 Factures ${list.length ? "(" + list.length + ")" : ""}</div>`;
    if (!list.length) return title + '<div class="fac-card fac-muted">Aucune facture générée.</div>';
    list = list.slice().sort((a, b) =>
      (parseInt(a.custom_numero_facture, 10) || 0) - (parseInt(b.custom_numero_facture, 10) || 0)
    );
    const rows = list.map(o => {
      const diff = parseFloat(o.amount_difference) || 0;
      const diffTxt = Math.abs(diff) > 0.01 ? `<span class="fac-diff-bad">${this._fmt(diff)}</span>` : this._fmt(diff);
      return `<tr>
        <td>${this._v(o.custom_numero_facture)}</td>
        <td class="fac-muted" style="white-space:nowrap;">${o.posting_date || ""}</td>
        <td>${this._link("Sales Invoice", o.invoice)}</td>
        <td>${frappe.utils.escape_html(o.customer || "")}</td>
        <td class="num">${this._fmt(o.net_total)}</td>
        <td class="num">${this._fmt(o.total_taxes)}</td>
        <td class="num"><b>${this._fmt(o.grand_total)}</b></td>
        <td class="num">${this._fmt(o.payment_amount)}</td>
        <td class="num">${diffTxt}</td>
        <td>${o.invoice_type || ""}</td>
        <td>${o.sales_order ? this._link("Sales Order", o.sales_order) : '<span class="fac-muted">—</span>'}</td>
        <td>${o.payment_entry ? this._link("Payment Entry", o.payment_entry) : '<span class="fac-muted">—</span>'}</td>
      </tr>`;
    }).join("");
    return title + `<div class="fac-card" style="padding:0; overflow-x:auto;">
      <table class="fac-tbl">
        <thead><tr>
          <th>N°</th><th>Date</th><th>Facture</th><th>Client</th>
          <th class="num">HT</th><th class="num">TVA</th><th class="num">TTC</th>
          <th class="num">Paiement</th><th class="num">Écart</th><th>Type</th><th>Commande</th><th>Paiement</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table></div>`;
  }

  _totals(res) {
    const dbTtc = parseFloat(res.db_period_ttc) || 0;
    const dbHt = parseFloat(res.db_period_ht) || 0;
    const gTtc = parseFloat(res.generated_ttc) || 0;
    const gHt = parseFloat(res.generated_ht) || 0;
    const gTva = parseFloat(res.generated_tva) || 0;
    return `<div class="fac-sub">💰 Totaux TTC / HT / TVA</div>
      <div class="fac-card"><div class="fac-kv" style="grid-template-columns:repeat(auto-fit,minmax(260px,1fr));">
        <div><b>En base (mois facturé, hors génération)</b><br>
          TTC : <b>${this._fmt(dbTtc)}</b> DT &nbsp;·&nbsp; HT : ${this._fmt(dbHt)} DT</div>
        <div><b>Généré ce lancement</b><br>
          TTC : <b>${this._fmt(gTtc)}</b> DT &nbsp;·&nbsp; TVA : ${this._fmt(gTva)} DT &nbsp;·&nbsp; HT : ${this._fmt(gHt)} DT</div>
        <div><b>Total après génération</b><br>
          TTC : <b>${this._fmt(dbTtc + gTtc)}</b> DT &nbsp;·&nbsp; HT : ${this._fmt(dbHt + gHt)} DT</div>
      </div></div>`;
  }

  _coherence(res) {
    const gaps = res.remaining_gaps || [];
    const dupes = res.duplicate_numbers || [];
    if (!gaps.length && !dupes.length) {
      return `<div class="fac-card" style="background:#d4edda;border-color:#b6ddc0;color:#155724;font-weight:600;">
        ✅ Numérotation cohérente : aucun trou non comblé, aucun numéro en double.</div>`;
    }
    let html = "";
    if (gaps.length) {
      html += `<div class="fac-errors" style="margin-bottom:10px;">
        ⚠️ <b>${gaps.length} trou(s) non comblé(s)</b> (numéros manquants dans la série) :
        ${gaps.join(", ")}</div>`;
    }
    if (dupes.length) {
      html += `<div class="fac-errors">
        ⛔ <b>${dupes.length} numéro(s) en double</b> (collision) : ${dupes.join(", ")}</div>`;
    }
    return `<div class="fac-sub" style="color:#b02a37;">🔎 Contrôle de cohérence</div>${html}`;
  }

  _open_modal(doctype, name) {
    if (!doctype || !name) return;
    const slug = frappe.router.slug ? frappe.router.slug(doctype) : doctype.toLowerCase().replace(/ /g, "-");
    const d = new frappe.ui.Dialog({ title: `${doctype} — ${name}`, size: "extra-large" });
    d.$body.html(`<iframe src="/app/${slug}/${encodeURIComponent(name)}" style="width:100%;height:78vh;border:0;border-radius:6px;"></iframe>`);
    d.show();
    d.$wrapper.find(".modal-dialog").css("max-width", "95vw");
  }

  _kpi(label, value, bg, sub) {
    return `<div class="fac-kpi" style="background:${bg};">
      <div class="lbl">${frappe.utils.escape_html(label)}</div>
      <div class="val">${value == null ? "—" : value}</div>
      <div class="sub">${sub ? frappe.utils.escape_html(sub) : ""}</div>
    </div>`;
  }
  _link(doctype, name) {
    if (!name) return "";
    return `<a href="#" class="fac-doc-link" data-dt="${frappe.utils.escape_html(doctype)}" data-dn="${frappe.utils.escape_html(name)}" style="font-weight:600;">${frappe.utils.escape_html(name)} 🔍</a>`;
  }
  _v(x) { return (x == null || x === "") ? '<span class="fac-muted">—</span>' : x; }
  _fmt(v) {
    const n = parseFloat(v) || 0;
    return n.toLocaleString("fr-TN", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  }
  _loading(show) { $("#fac-loading").css("display", show ? "flex" : "none"); }
}
