frappe.pages["prevision-import"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({
    parent: wrapper,
    title: "Prévision Import",
    single_column: true,
  });
  $(wrapper).find(".layout-main-section").html(
    frappe.render_template("prevision_import", {})
  );
  new PrevisionImport(wrapper);
};

const PI_ALLOWED_USER = "koubaawassim@gmail.com";
const PI_PAGE_LENGTH = 50;

class PrevisionImport {
  constructor(wrapper) {
    this.wrapper = wrapper;
    if (frappe.session.user !== PI_ALLOWED_USER) {
      $("#pi-main").hide();
      $("#pi-denied").show();
      return;
    }
    this.start = 0;
    this.order_by = "a_importer";
    this.order_dir = "desc";
    this.data = null;
    // panier de sélection : item_code -> row (survit à la pagination/filtres)
    this.selection = new Map();
    this._bind();
    this._load_filters().then(() => this._load());
  }

  _update_sel_ui() {
    const n = this.selection.size;
    $("#pi-sel-count").text(n);
    $("#pi-commande").toggle(n > 0);
  }

  _params() {
    return {
      search: $("#pi-search").val() || "",
      item_group: $("#pi-group").val() || "",
      warehouse: $("#pi-warehouse").val() || "",
      periode: $("#pi-periode").val() || 3,
      fenetre_moy: $("#pi-moyenne").val() || 3,
      fenetre_hist: $("#pi-historique").val() || 12,
      croissance: $("#pi-croissance").val(),
      risque: JSON.stringify(
        $(".pi-chip[data-state].active").map((_, el) => $(el).data("state")).get()
      ),
      only_a_importer: $("#pi-only-imp").prop("checked") ? 1 : 0,
    };
  }

  _bind() {
    let timer = null;
    $("#pi-search").on("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => { this.start = 0; this._load(); }, 400);
    });
    $("#pi-group, #pi-warehouse").on("change", () => { this.start = 0; this._load(); });
    $("#pi-only-imp").on("change", () => { this.start = 0; this._load(); });
    $(this.wrapper).on("click", ".pi-chip[data-state]", (e) => {
      $(e.currentTarget).toggleClass("active");
      this.start = 0; this._load();
    });
    // cartes KPI = raccourcis vers les chips (toggle)
    $(this.wrapper).on("click", ".pi-kpi[data-risque]", (e) => {
      const v = $(e.currentTarget).data("risque");
      if (v === "a_importer") {
        $("#pi-only-imp").prop("checked", !$("#pi-only-imp").prop("checked"));
      } else {
        $(`.pi-chip[data-state="${v}"]`).toggleClass("active");
      }
      this.start = 0; this._load();
    });
    $("#pi-refresh").on("click", () => { this.start = 0; this._load(); });
    $("#pi-periode, #pi-moyenne, #pi-historique, #pi-croissance").on("keydown", (e) => {
      if (e.key === "Enter") { this.start = 0; this._load(); }
    });
    $("#pi-prev").on("click", () => {
      if (this.start > 0) { this.start = Math.max(0, this.start - PI_PAGE_LENGTH); this._load(); }
    });
    $("#pi-next").on("click", () => {
      if (this.data && this.start + PI_PAGE_LENGTH < this.data.total) {
        this.start += PI_PAGE_LENGTH; this._load();
      }
    });
    $("#pi-excel").on("click", () => {
      const p = this._params();
      const params = new URLSearchParams({
        ...p, croissance: p.croissance || "",
        order_by: this.order_by, order_dir: this.order_dir,
      });
      window.open(`/api/method/customization_app.prevision_import.download_excel?${params}`);
    });
    $(this.wrapper).on("click", "th.sortable", (e) => {
      const f = $(e.currentTarget).data("field");
      if (this.order_by === f) {
        this.order_dir = this.order_dir === "asc" ? "desc" : "asc";
      } else {
        this.order_by = f; this.order_dir = "desc";
      }
      this.start = 0; this._load();
    });
    $(this.wrapper).on("click", "tr.pi-row", (e) => this._toggle_chart(e));

    // --- sélection pour liste de commande ---
    $(this.wrapper).on("click", ".pi-check", (e) => {
      e.stopPropagation();
      const $cb = $(e.currentTarget);
      const idx = $cb.data("idx");
      const r = this.data.rows[idx];
      if ($cb.prop("checked")) this.selection.set(r.item_code, r);
      else this.selection.delete(r.item_code);
      this._update_sel_ui();
    });
    $(this.wrapper).on("click", "#pi-check-all", (e) => {
      e.stopPropagation();
      const checked = $(e.currentTarget).prop("checked");
      (this.data?.rows || []).forEach((r, i) => {
        if (checked) this.selection.set(r.item_code, r);
        else this.selection.delete(r.item_code);
      });
      $(".pi-check").prop("checked", checked);
      this._update_sel_ui();
    });
    $("#pi-commande").on("click", () => this._open_commande_dialog());
  }

  async _open_commande_dialog() {
    if (!this.selection.size) return;
    const items = [...this.selection.values()];
    const d = new frappe.ui.Dialog({
      title: __("Créer / compléter une liste de commande"),
      fields: [
        {
          fieldname: "info", fieldtype: "HTML",
          options: `<div style="margin-bottom:8px;color:#6b7280;font-size:12px;">
            <b>${items.length}</b> article(s) sélectionné(s) :
            ${items.slice(0, 8).map(r => frappe.utils.escape_html(r.item_code)).join(", ")}${items.length > 8 ? "…" : ""}</div>`,
        },
        {
          fieldname: "qty_mode", label: __("Quantité à reprendre"), fieldtype: "Select",
          options: "À importer (2 cycles)\nÀ importer (1 cycle)",
          default: "À importer (2 cycles)", reqd: 1,
        },
        {
          fieldname: "target", label: __("Compléter une liste Brouillon existante"),
          fieldtype: "Link", options: "Liste Commande Import",
          get_query: () => ({ filters: { statut: "Brouillon" } }),
          description: __("Laisser vide pour créer une nouvelle liste"),
        },
        {
          fieldname: "titre", label: __("Titre (nouvelle liste)"), fieldtype: "Data",
          default: "Commande import " + frappe.datetime.get_today(),
          depends_on: "eval:!doc.target",
        },
      ],
      primary_action_label: __("Créer"),
      primary_action: async (values) => {
        const two = values.qty_mode !== "À importer (1 cycle)";
        const payload = items.map(r => ({
          item_code: r.item_code,
          qty: Math.ceil(two ? (r.a_importer_2 ?? 0) : (r.a_importer ?? 0)),
        }));
        d.hide();
        try {
          const res = await frappe.call({
            method: "customization_app.liste_commande_import.create_from_selection",
            args: { items: JSON.stringify(payload), target: values.target || "", titre: values.titre || "" },
            freeze: true, freeze_message: __("Création de la liste…"),
          });
          this.selection.clear();
          this._update_sel_ui();
          frappe.set_route("Form", "Liste Commande Import", res.message.name);
        } catch (err) {
          frappe.msgprint(__("Erreur lors de la création de la liste."));
          console.error(err);
        }
      },
    });
    d.show();
  }

  async _load_filters() {
    const r = await frappe.call({ method: "customization_app.prevision_import.get_filters" });
    const f = r.message || {};
    const $g = $("#pi-group");
    (f.item_groups || []).forEach((g) => {
      const indent = "  ".repeat(g.depth || 0);
      const label = indent + (g.is_group ? "📁 " : "") + g.name;
      $g.append(`<option value="${frappe.utils.escape_html(g.name)}">${label}</option>`);
    });
    const $w = $("#pi-warehouse");
    (f.warehouses || []).forEach((w) => {
      $w.append(`<option value="${frappe.utils.escape_html(w)}">${frappe.utils.escape_html(w)}</option>`);
    });
    const d = f.defaults || {};
    if (d.periode) $("#pi-periode").val(d.periode);
    if (d.fenetre_moy) $("#pi-moyenne").val(d.fenetre_moy);
    if (d.fenetre_hist) $("#pi-historique").val(d.fenetre_hist);
  }

  async _load() {
    $("#pi-table").html('<div class="pi-loading">⏳ Calcul en cours…</div>');
    try {
      const r = await frappe.call({
        method: "customization_app.prevision_import.get_prevision",
        args: {
          ...this._params(),
          start: this.start,
          page_length: PI_PAGE_LENGTH,
          order_by: this.order_by,
          order_dir: this.order_dir,
        },
      });
      this.data = r.message;
      this._render();
    } catch (err) {
      $("#pi-table").html('<div class="pi-empty">❌ Erreur de chargement.</div>');
      console.error(err);
    }
  }

  _fmt(v, dec = 2) {
    if (v === null || v === undefined) return "—";
    return format_number(v, null, dec);
  }

  _render() {
    const d = this.data;
    if (!d || !d.rows || !d.rows.length) {
      $("#pi-table").html('<div class="pi-empty">Aucun article.</div>');
      $("#pi-kpis").hide();
      $("#pi-page-info").text("—");
      return;
    }

    // KPIs
    $("#pi-kpis").css("display", "flex");
    $("#pi-kpi-imp").text(d.totaux.a_importer_articles);
    $("#pi-kpi-rupture").text(d.totaux.rupture);
    $("#pi-kpi-attention").text(d.totaux.attention);

    // Pager
    const from = d.start + 1;
    const to = Math.min(d.start + PI_PAGE_LENGTH, d.total);
    $("#pi-page-info").text(`${from}–${to} / ${d.total}`);

    const p = d.params || {};
    const arrow = (f) => (this.order_by === f ? (this.order_dir === "asc" ? " ▲" : " ▼") : "");
    const th = (f, label, num) =>
      `<th class="sortable${num ? " num" : ""}" data-field="${f}">${label}${arrow(f)}</th>`;

    let html = `<table class="pi-tbl"><thead><tr>
      <th style="width:26px;"><input type="checkbox" id="pi-check-all" title="Tout cocher (page)"></th>
      ${th("item_code", "Article")}
      ${th("item_group", "Groupe")}
      ${th("mg", `MG ${p.fenetre_moy || ""} mois`, 1)}
      ${th("tendance", "Tendance", 1)}
      ${th("croissance", "Croissance", 1)}
      ${th("prevision", `Prévision<br>${p.periode || ""} mois`, 1)}
      ${th("stock", "Stock", 1)}
      ${th("a_importer", "À importer<br>1 cycle", 1)}
      ${th("a_importer_2", `À importer<br>2 cycles (${(p.periode || 0) * 2} mois)`, 1)}
      ${th("couverture", "Couverture", 1)}
      <th>Risque</th>
    </tr></thead><tbody>`;

    const badge = { rupture: "RUPTURE", attention: "Attention", ok: "OK", aucun: "—" };
    d.rows.forEach((r, i) => {
      const tcls = r.tendance > 0 ? "pi-up" : r.tendance < 0 ? "pi-down" : "pi-muted";
      const ccls = r.croissance > 0 ? "pi-up" : r.croissance < 0 ? "pi-down" : "pi-muted";
      html += `<tr class="pi-row" data-idx="${i}" data-item="${frappe.utils.escape_html(r.item_code)}">
        <td><input type="checkbox" class="pi-check" data-idx="${i}" ${this.selection.has(r.item_code) ? "checked" : ""}></td>
        <td><span class="pi-code">${frappe.utils.escape_html(r.item_code)}</span>
            <div class="pi-name">${frappe.utils.escape_html(r.item_name)}</div></td>
        <td>${frappe.utils.escape_html(r.item_group)}</td>
        <td class="num">${this._fmt(r.mg)} <span class="pi-muted">${frappe.utils.escape_html(r.stock_uom)}/mois</span></td>
        <td class="num ${tcls}">${r.tendance > 0 ? "+" : ""}${this._fmt(r.tendance, 1)}%</td>
        <td class="num ${ccls}">${r.croissance > 0 ? "+" : ""}${this._fmt(r.croissance, 1)}%</td>
        <td class="num">${this._fmt(r.prevision)}</td>
        <td class="num">${this._fmt(r.stock)}</td>
        <td class="num pi-imp">${r.a_importer > 0 ? this._fmt(r.a_importer) : '<span class="pi-muted">0</span>'}</td>
        <td class="num pi-imp2">${r.a_importer_2 > 0 ? this._fmt(r.a_importer_2) : '<span class="pi-muted">0</span>'}</td>
        <td class="num">${r.couverture !== null ? this._fmt(r.couverture, 1) + " mois" : "—"}</td>
        <td><span class="pi-badge ${r.risque}">${badge[r.risque] || r.risque}</span></td>
      </tr>`;
    });
    html += "</tbody></table>";
    $("#pi-table").html(html);
  }

  async _toggle_chart(e) {
    const $row = $(e.currentTarget);
    const $next = $row.next("tr.pi-chart-row");
    if ($next.length) { $next.remove(); return; }
    $("tr.pi-chart-row").remove();

    const item = $row.data("item");
    const idx = $row.data("idx");
    const r = this.data.rows[idx];
    const chartId = "pi-chart-" + idx;
    $row.after(`<tr class="pi-chart-row"><td colspan="12">
      <div class="pi-chart-title">📈 ${frappe.utils.escape_html(item)} — ventes mensuelles (BL) · MG ${this._fmt(r.mg)} · prévision ${this._fmt(r.prevision)}</div>
      <div id="${chartId}"><div class="pi-loading">⏳</div></div>
    </td></tr>`);

    try {
      const res = await frappe.call({
        method: "customization_app.prevision_import.get_item_history",
        args: { item_code: item, months: 24, warehouse: $("#pi-warehouse").val() || "" },
      });
      const h = res.message || { months: [], qty: [] };
      const mgLine = h.months.map(() => r.mg);
      new frappe.Chart("#" + chartId, {
        data: {
          labels: h.months,
          datasets: [
            { name: "Ventes (BL)", values: h.qty, chartType: "bar" },
            { name: "Moyenne glissante", values: mgLine, chartType: "line" },
          ],
        },
        type: "axis-mixed",
        height: 220,
        colors: ["#0958d9", "#fa8c16"],
        axisOptions: { shortenYAxisNumbers: 1, xIsSeries: 1 },
        tooltipOptions: { formatTooltipY: (v) => this._fmt(v) },
      });
    } catch (err) {
      $("#" + chartId).html('<div class="pi-empty">❌ Erreur graphique.</div>');
    }
  }
}
