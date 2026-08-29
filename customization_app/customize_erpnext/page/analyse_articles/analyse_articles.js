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
    this.selection = new Set();
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
    $("#aa-marge").on("change", () => { this.start = 0; this._load(); });
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
        marge: $("#aa-marge").val() || "",
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
    // ✏️ prix : clic sur une cellule PV TTC → modification de CE prix
    $(this.wrapper).on("click", ".aa-pv", (e) => {
      const $c = $(e.currentTarget);
      this._dialog_prix_article($c.data("item"), String($c.data("pl")), $c.data("pv"));
    });
    $("#aa-excel").after('<button class="btn btn-sm btn-primary" id="aa-bulk-price" style="margin-left:6px">✏️ Modifier les prix</button>');
    $("#aa-bulk-price").on("click", () => this._dialog_prix_bloc());
    // ☑️ sélection d'articles (persiste entre pages et filtres)
    $(this.wrapper).on("change", ".aa-sel", (e) => {
      const code = $(e.currentTarget).data("item");
      if (e.currentTarget.checked) this.selection.add(code);
      else this.selection.delete(code);
      this._maj_libelle_bloc();
    });
    $(this.wrapper).on("change", "#aa-chk-all", (e) => {
      const on = e.currentTarget.checked;
      $(this.wrapper).find(".aa-sel").each((_, el) => {
        el.checked = on;
        const code = $(el).data("item");
        if (on) this.selection.add(code);
        else this.selection.delete(code);
      });
      this._maj_libelle_bloc();
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
          marge: $("#aa-marge").val() || "",
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
      <th style="width:26px"><input type="checkbox" id="aa-chk-all" title="Tout cocher (page)"></th>
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
    head += "</tr><tr><th colspan='8'></th>";
    pls.forEach(() => {
      head += `<th class="num pl-head">PV TTC</th><th class="num">Marge</th>`;
    });
    head += "</tr>";

    const body = d.rows.map((r) => this._render_row(r, pls)).join("");
    $("#aa-table").html(`<table class="aa-tbl"><thead>${head}</thead><tbody>${body}</tbody></table>`);
    this._maj_libelle_bloc();
  }

  _maj_libelle_bloc() {
    const n = this.selection.size;
    $("#aa-bulk-price").text(n ? `✏️ Modifier les prix (${n} ☑)` : "✏️ Modifier les prix");
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
      <td><input type="checkbox" class="aa-sel" data-item="${this._esc(r.item_code)}"
           ${this.selection.has(r.item_code) ? "checked" : ""}></td>
      <td>${img}</td>
      <td>${toggle}<span class="aa-code">${this._esc(r.item_code)}</span>${badge}</td>
      <td>${this._esc(r.item_name)}${desc}</td>
      <td>${this._esc(r.item_group)}</td>
      <td class="num">${this._money(b ? b.cout_ht : r.val_ht)}</td>
      <td class="num">${r.tva == null ? '<span class="aa-muted">—</span>' : format_number(r.tva, null, 1)}</td>
      <td class="num">${this._money(b ? b.cout_ttc : r.val_ttc)}</td>`;
    pls.forEach((pl) => {
      const p = r.prices[pl] || {};
      cells += `<td class="num pl-first aa-pv" style="cursor:pointer" title="Cliquer pour modifier ce prix"
                    data-item="${this._esc(r.item_code)}" data-pl="${this._esc(pl)}"
                    data-pv="${p.pv_ttc == null ? "" : p.pv_ttc}">${this._money(p.pv_ttc)} <span class="aa-muted">✏️</span></td>
                <td class="num">${this._marge(p.marge)}</td>`;
    });
    let html = `<tr>${cells}</tr>`;
    if (b) html += this._render_bundle_detail(r, pls);
    return html;
  }

  _render_bundle_detail(r, pls) {
    const b = r.bundle;
    const colspan = 8 + pls.length * 2;

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

  // ---------------------------------------------------------------- prix

  _dialog_prix_article(item_code, price_list, pv_actuel) {
    const d = new frappe.ui.Dialog({
      title: __("Modifier le prix — {0}", [item_code]),
      fields: [
        { fieldtype: "HTML", fieldname: "info" },
        { fieldtype: "Currency", fieldname: "nouveau", reqd: 1,
          label: __("Nouveau prix TTC ({0})", [price_list]),
          default: pv_actuel || 0 },
        { fieldtype: "Check", fieldname: "maj_bundles", default: 1,
          label: __("Mettre à jour les bundles contenant cet article (prix recalculé)") },
      ],
      primary_action_label: __("Aperçu"),
      primary_action: (v) => {
        d.hide();
        this._apercu_et_appliquer(
          { mode: "liste", changements: [{ item_code, price_list, nouveau: v.nouveau }] },
          v.maj_bundles ? 1 : 0);
      },
    });
    d.fields_dict.info.$wrapper.html(
      `<div style="font-size:12.5px;color:var(--text-muted);margin-bottom:6px">
        Prix actuel : <b>${pv_actuel == null || pv_actuel === "" ? "—" : format_number(pv_actuel, null, 3)}</b>.
        L'ancien prix est clos à hier et un nouveau prend effet aujourd'hui :
        les commandes antérieures, brouillons compris, gardent leur tarif.</div>`);
    d.show();
  }

  _dialog_prix_bloc() {
    const total = this.data ? this.data.total : 0;
    const pls = (this.data ? this.data.price_lists : []).map((p) => p.name);
    const nsel = this.selection.size;
    const d = new frappe.ui.Dialog({
      title: __("Modifier les prix en bloc"),
      fields: [
        { fieldtype: "HTML", fieldname: "info" },
        { fieldtype: "MultiCheck", fieldname: "price_lists", columns: 2,
          label: __("Listes de prix"), select_all: pls.length > 1,
          options: pls.map((p, i) => ({ label: p, value: p, checked: i === 0 })) },
        { fieldtype: "Select", fieldname: "operation", reqd: 1, label: __("Opération"),
          options: [
            { value: "pct", label: __("Variation en % (ex. 5 ou -10)") },
            { value: "montant", label: __("Variation en montant (ex. 2.5 ou -1)") },
            { value: "fixe", label: __("Prix fixe pour tous") },
          ], default: "pct" },
        { fieldtype: "Float", fieldname: "valeur", reqd: 1, label: __("Valeur") },
        { fieldtype: "Check", fieldname: "maj_bundles", default: 1,
          label: __("Mettre à jour les bundles contenant ces articles") },
      ],
      primary_action_label: __("Aperçu"),
      primary_action: (v) => {
        const listes = v.price_lists || [];
        if (!listes.length) {
          frappe.msgprint(__("Cochez au moins une liste de prix."));
          return;
        }
        d.hide();
        const cible = {
          mode: "bloc",
          price_lists: listes, operation: v.operation, valeur: v.valeur,
        };
        if (nsel) {
          cible.item_codes = Array.from(this.selection);
        } else {
          cible.search = $("#aa-search").val() || "";
          cible.item_group = $("#aa-group").val() || "";
          cible.marge = $("#aa-marge").val() || "";
        }
        this._apercu_et_appliquer(cible, v.maj_bundles ? 1 : 0);
      },
    });
    d.fields_dict.info.$wrapper.html(
      `<div style="font-size:12.5px;color:var(--text-muted);margin-bottom:6px">
        ${nsel
          ? `S'applique aux <b>${nsel}</b> article(s) <b>cochés</b> dans le tableau.`
          : `Aucun article coché : s'applique aux <b>${total}</b> article(s) du
             filtre actuel (recherche + groupe + marge).`}
        Les articles sans prix sur une liste y sont ignorés (sauf « Prix fixe »).</div>`);
    d.show();
  }

  async _apercu_et_appliquer(cible, maj_bundles) {
    const r = await frappe.call({
      method: "customization_app.analyse_articles.apercu_prix",
      args: { cible: JSON.stringify(cible), maj_bundles },
      freeze: true, freeze_message: __("Calcul de l'aperçu…"),
    });
    const m = r.message || { changements: [], bundles: [] };
    if (!m.changements.length) {
      frappe.msgprint(__("Aucun changement de prix (mêmes valeurs ou aucun prix existant)."));
      return;
    }
    const esc = frappe.utils.escape_html;
    const fmt = (v) => (v == null ? "—" : format_number(v, null, 3));
    const lignes = m.changements.slice(0, 400).map((c) =>
      `<tr><td>${esc(c.item_code)}</td><td>${esc(c.item_name)}</td>
       <td>${esc(c.price_list)}</td><td class="num">${fmt(c.ancien)}</td>
       <td class="num"><b>${fmt(c.nouveau)}</b></td></tr>`).join("");
    const bundles = m.bundles.map((b, i) =>
      `<tr><td><input type="checkbox" class="aa-b-chk" data-b="${esc(b.bundle)}"
            ${b.incomplet ? "disabled" : "checked"}></td>
       <td>${esc(b.bundle)}</td><td>${esc(b.item_name)}</td>
       <td>${esc(b.price_list)}</td><td class="num">${fmt(b.ancien)}</td>
       <td class="num">${b.incomplet ? '<span style="color:#b45309">incomplet — non touché</span>' : "<b>" + fmt(b.nouveau) + "</b>"}</td></tr>`).join("");

    const d = new frappe.ui.Dialog({
      title: __("Aperçu — {0} article(s), {1} bundle(s)", [m.changements.length, m.bundles.length]),
      size: "extra-large",
      fields: [{ fieldtype: "HTML", fieldname: "zone" }],
      primary_action_label: __("✅ Appliquer les prix"),
      primary_action: async () => {
        const exclus = [];
        d.$wrapper.find(".aa-b-chk").each(function () {
          if (!this.checked) exclus.push($(this).data("b"));
        });
        d.hide();
        const res = await frappe.call({
          method: "customization_app.analyse_articles.appliquer_prix",
          args: { cible: JSON.stringify(cible), maj_bundles,
                  bundles_exclus: JSON.stringify(exclus) },
          freeze: true, freeze_message: __("Application des prix…"),
        });
        const out = res.message || {};
        frappe.show_alert({
          message: __("✅ {0} prix d'article(s) et {1} bundle(s) mis à jour", [out.articles, out.bundles]),
          indicator: "green",
        }, 8);
        this.selection.clear();
        this._load();
      },
    });
    d.fields_dict.zone.$wrapper.html(`
      <div class="aa-sub-title">Articles (${m.changements.length}${m.changements.length > 400 ? ", 400 affichés" : ""})</div>
      <div style="max-height:260px;overflow:auto"><table class="aa-sub">
        <thead><tr><th>Code</th><th>Désignation</th><th>Liste</th>
        <th class="num">Ancien TTC</th><th class="num">Nouveau TTC</th></tr></thead>
        <tbody>${lignes}</tbody></table></div>
      ${m.bundles.length ? `
      <div class="aa-sub-title" style="margin-top:10px">Bundles impactés (réalignés sur le prix calculé — décochez pour ne pas toucher)</div>
      <div style="max-height:220px;overflow:auto"><table class="aa-sub">
        <thead><tr><th></th><th>Bundle</th><th>Désignation</th><th>Liste</th>
        <th class="num">Prix propre actuel</th><th class="num">Nouveau prix</th></tr></thead>
        <tbody>${bundles}</tbody></table></div>` : ""}
      <div style="margin-top:8px;font-size:12px;color:var(--text-muted)">
        🛡️ Mécanisme : l'ancien prix est clos à hier, le nouveau prend effet aujourd'hui —
        les commandes déjà passées (même en brouillon) conservent leur tarif.</div>`);
    d.show();
  }
}
