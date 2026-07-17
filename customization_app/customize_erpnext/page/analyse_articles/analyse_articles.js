frappe.pages["analyse-articles"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({
    parent: wrapper,
    title: "Analyse Articles",
    single_column: true,
  });
  $(wrapper).find(".layout-main-section").html(
    frappe.render_template("analyse_articles", {})
  );
  new AnalyseArticles(wrapper);
};

const AA_ALLOWED_USER = "koubaawassim@gmail.com";
const AA_PAGE_LENGTH = 50;

class AnalyseArticles {
  constructor(wrapper) {
    this.wrapper = wrapper;
    if (frappe.session.user !== AA_ALLOWED_USER) {
      $("#aa-main").hide();
      $("#aa-denied").show();
      return;
    }
    this.start = 0;
    this.order_by = "item_code";
    this.order_dir = "asc";
    this.data = null;
    this._bind();
    this._load_filters().then(() => this._load());
  }

  _bind() {
    let timer = null;
    $("#aa-search").on("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => { this.start = 0; this._load(); }, 400);
    });
    $("#aa-group").on("change", () => { this.start = 0; this._load(); });
    $("#aa-prev").on("click", () => {
      if (this.start > 0) { this.start = Math.max(0, this.start - AA_PAGE_LENGTH); this._load(); }
    });
    $("#aa-next").on("click", () => {
      if (this.data && this.start + AA_PAGE_LENGTH < this.data.total) {
        this.start += AA_PAGE_LENGTH; this._load();
      }
    });
    $("#aa-excel").on("click", () => {
      const params = new URLSearchParams({
        search: $("#aa-search").val() || "",
        item_group: $("#aa-group").val() || "",
      });
      window.open(`/api/method/customization_app.analyse_articles.download_excel?${params}`);
    });
    $(this.wrapper).on("click", "th.sortable", (e) => {
      const f = $(e.currentTarget).data("field");
      if (this.order_by === f) {
        this.order_dir = this.order_dir === "asc" ? "desc" : "asc";
      } else {
        this.order_by = f; this.order_dir = "asc";
      }
      this.start = 0; this._load();
    });
    $(this.wrapper).on("click", ".aa-toggle", (e) => {
      e.stopPropagation();
      const $row = $(e.currentTarget).closest("tr");
      const $detail = $row.next("tr.aa-bundle-detail");
      $detail.toggle();
      $(e.currentTarget).text($detail.is(":visible") ? "▾" : "▸");
    });
  }

  async _load_filters() {
    const r = await frappe.call({ method: "customization_app.analyse_articles.get_filters" });
    const f = r.message || {};
    const $g = $("#aa-group");
    (f.item_groups || []).forEach((g) => {
      const indent = "  ".repeat(g.depth || 0);
      const label = indent + (g.is_group ? "📁 " : "") + g.name;
      $g.append(`<option value="${frappe.utils.escape_html(g.name)}">${frappe.utils.escape_html(label)}</option>`);
    });
  }

  async _load() {
    $("#aa-table").html('<div class="aa-loading">⏳ Chargement…</div>');
    try {
      const r = await frappe.call({
        method: "customization_app.analyse_articles.get_analysis",
        args: {
          search: $("#aa-search").val() || "",
          item_group: $("#aa-group").val() || "",
          start: this.start,
          page_length: AA_PAGE_LENGTH,
          order_by: this.order_by,
          order_dir: this.order_dir,
        },
      });
      this.data = r.message;
      this._render();
    } catch (e) {
      $("#aa-table").html(
        `<div class="aa-empty">Erreur de chargement : ${frappe.utils.escape_html(String(e))}</div>`
      );
    }
  }

  // ---------------------------------------------------------------- rendu

  _esc(v) { return frappe.utils.escape_html(String(v == null ? "" : v)); }

  _money(v) {
    if (v == null) return '<span class="aa-muted">—</span>';
    return format_number(v, null, 3);
  }

  _marge(v) {
    if (v == null) return '<span class="aa-muted">—</span>';
    const cls = v < 0 ? "aa-marge-bad" : "aa-marge-pos";
    return `<span class="${cls}">${format_number(v, null, 1)}%</span>`;
  }

  _sort_icon(field) {
    if (this.order_by !== field) return "";
    return this.order_dir === "asc" ? " ▲" : " ▼";
  }

  _render() {
    const d = this.data;
    const from = d.total ? d.start + 1 : 0;
    const to = Math.min(d.start + AA_PAGE_LENGTH, d.total);
    $("#aa-page-info").text(`${from}–${to} sur ${d.total}`);
    $("#aa-prev").prop("disabled", d.start <= 0);
    $("#aa-next").prop("disabled", to >= d.total);

    if (!d.rows.length) {
      $("#aa-table").html('<div class="aa-empty">Aucun article trouvé.</div>');
      return;
    }

    const pls = d.price_lists.map((p) => p.name);
    let head = `<tr>
      <th></th>
      <th class="sortable" data-field="item_code">Code article${this._sort_icon("item_code")}</th>
      <th class="sortable" data-field="item_name">Désignation${this._sort_icon("item_name")}</th>
      <th class="sortable" data-field="item_group">Groupe${this._sort_icon("item_group")}</th>
      <th class="num">Valo. HT</th>
      <th class="num">TVA %</th>
      <th class="num">Valo. TTC</th>`;
    pls.forEach((pl) => {
      head += `<th class="num pl-head" colspan="2">${this._esc(pl)}</th>`;
    });
    head += "</tr><tr><th colspan='7'></th>";
    pls.forEach(() => {
      head += `<th class="num pl-head">PV TTC</th><th class="num">Marge</th>`;
    });
    head += "</tr>";

    const body = d.rows.map((r) => this._render_row(r, pls)).join("");
    $("#aa-table").html(`<table class="aa-tbl"><thead>${head}</thead><tbody>${body}</tbody></table>`);
  }

  _render_row(r, pls) {
    const img = r.image
      ? `<img class="aa-img" src="${this._esc(r.image)}" loading="lazy">`
      : `<span class="aa-img-empty">🖼</span>`;
    const b = r.bundle;
    const toggle = b ? `<span class="aa-toggle">▸</span>` : "";
    const badge = b ? ` <span class="aa-badge">BUNDLE</span>` : "";
    const desc = r.description
      ? `<div class="aa-desc">${this._esc(strip_html(r.description))}</div>` : "";

    let cells = `
      <td>${img}</td>
      <td>${toggle}<span class="aa-code">${this._esc(r.item_code)}</span>${badge}</td>
      <td>${this._esc(r.item_name)}${desc}</td>
      <td>${this._esc(r.item_group)}</td>
      <td class="num">${this._money(b ? b.cout_ht : r.val_ht)}</td>
      <td class="num">${r.tva == null ? '<span class="aa-muted">—</span>' : format_number(r.tva, null, 1)}</td>
      <td class="num">${this._money(b ? b.cout_ttc : r.val_ttc)}</td>`;
    pls.forEach((pl) => {
      const p = r.prices[pl] || {};
      cells += `<td class="num pl-first">${this._money(p.pv_ttc)}</td>
                <td class="num">${this._marge(p.marge)}</td>`;
    });
    let html = `<tr>${cells}</tr>`;
    if (b) html += this._render_bundle_detail(r, pls);
    return html;
  }

  _render_bundle_detail(r, pls) {
    const b = r.bundle;
    const colspan = 7 + pls.length * 2;

    // prix propre vs prix calculé par liste
    let plRows = pls.map((pl) => {
      const p = b.prices[pl] || {};
      const calc = p.calc_ttc == null
        ? '<span class="aa-incomplet">incomplet</span>' : this._money(p.calc_ttc);
      return `<tr>
        <td>${this._esc(pl)}</td>
        <td class="num">${this._money(p.own_ttc)}</td>
        <td class="num">${this._marge(p.own_marge)}</td>
        <td class="num">${calc}</td>
        <td class="num">${this._marge(p.calc_marge)}</td>
      </tr>`;
    }).join("");

    let compRows = b.components.map((c) => {
      const prices = pls.map((pl) =>
        `<td class="num">${this._money(c.prices[pl])}</td>`).join("");
      return `<tr>
        <td>${this._esc(c.item_code)}</td>
        <td>${this._esc(c.item_name)}</td>
        <td class="num">${format_number(c.qty, null, 2)}</td>
        <td class="num">${this._money(c.val_ht)}</td>
        <td class="num">${c.tva == null ? "—" : format_number(c.tva, null, 1)}</td>
        <td class="num">${this._money(c.val_ttc)}</td>
        ${prices}
      </tr>`;
    }).join("");
    const compPlHeads = pls.map((pl) => `<th class="num">${this._esc(pl)}</th>`).join("");

    return `<tr class="aa-bundle-detail" style="display:none"><td colspan="${colspan}">
      <div class="aa-sub-title">Prix du bundle par liste — coût de revient : ${this._money(b.cout_ht)} HT / ${this._money(b.cout_ttc)} TTC</div>
      <table class="aa-sub">
        <thead><tr><th>Liste de prix</th><th class="num">PV propre TTC</th><th class="num">Marge propre</th>
        <th class="num">PV calculé TTC</th><th class="num">Marge calculée</th></tr></thead>
        <tbody>${plRows}</tbody>
      </table>
      <div class="aa-sub-title" style="margin-top:12px">Composants</div>
      <table class="aa-sub">
        <thead><tr><th>Code</th><th>Désignation</th><th class="num">Qté</th>
        <th class="num">Valo. HT</th><th class="num">TVA %</th><th class="num">Valo. TTC</th>${compPlHeads}</tr></thead>
        <tbody>${compRows}</tbody>
      </table>
    </td></tr>`;
  }
}
