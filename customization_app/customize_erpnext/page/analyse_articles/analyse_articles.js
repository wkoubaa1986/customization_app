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
    this.marge_min = 0;            // marge minimale garantie ENREGISTRÉE (0 = aucune)
    // Colonnes repliées (« groupe », « valo », « pl:<liste> ») : retenues sur ce navigateur.
    this.cols_off = new Set((() => {
      try { return JSON.parse(localStorage.getItem("aa_cols_off") || "[]"); } catch (e) { return []; }
    })());
    // Largeurs réglées à la main ({colonne: pixels}) : retenues sur ce navigateur, comme les colonnes repliées.
    this.cols_w = (() => {
      try { return JSON.parse(localStorage.getItem("aa_cols_w") || "{}") || {}; } catch (e) { return {}; }
    })();
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
    // ── Tri par famille puis prix (19/11/2026) ──────────────────────────────────────────────────────────
    // Les variantes d'un même modèle restent ensemble ; le prix est celui de la liste choisie à côté.
    // Posé ici et non dans le gabarit .html : une apostrophe droite y casse toute la page.
    $("#aa-marge").closest(".aa-field").after(`
      <div class="aa-field"><label>Tri</label>
        <select id="aa-tri" class="form-control" style="width:230px">
          <option value="">Code article</option>
          <option value="asc">Famille, puis prix croissant</option>
          <option value="desc">Famille, puis prix décroissant</option>
        </select></div>
      <div class="aa-field" id="aa-tri-liste-zone" style="display:none"><label>Prix de la liste</label>
        <select id="aa-tri-liste" class="form-control" style="width:170px"></select></div>`);
    $("#aa-marge option[value='lt:0']").after('<option value="sous_min">⚠️ Sous la marge minimale garantie (promos comprises)</option>');
    $("#aa-tri").on("change", () => {
      const sens = $("#aa-tri").val();
      $("#aa-tri-liste-zone").toggle(!!sens);
      if (sens) { this.order_by = "famille_prix"; this.order_dir = sens; }
      else { this.order_by = "item_code"; this.order_dir = "asc"; }
      this.start = 0; this._load();
    });
    $("#aa-tri-liste").on("change", () => { this.start = 0; this._load(); });
    $(this.wrapper).on("click", "th.sortable", (e) => {
      if (Date.now() - (this._rz_fin || 0) < 350) return;       // on vient de lâcher une poignée de largeur : pas un tri
      $("#aa-tri").val(""); $("#aa-tri-liste-zone").hide();      // un clic d'en-tête reprend le tri simple
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
    this._poser_zone_prix();
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
    // ── Largeur des colonnes : on tire le bord droit d'un en-tête ; double-clic = largeur par défaut ──
    const noter_largeurs = () => {
      try { localStorage.setItem("aa_cols_w", JSON.stringify(this.cols_w)); } catch (err) { /* stockage refusé : sans effet */ }
      $("#aa-cols-w-raz").toggle(Object.keys(this.cols_w).length > 0);
    };
    $(this.wrapper).on("mousedown", ".aa-rz", (e) => {
      e.preventDefault(); e.stopPropagation();
      const cle = $(e.currentTarget).closest("th").attr("data-ck");
      this._figer_largeurs();                                   // chaque colonne prend sa largeur du moment, en pixels
      const table = $("#aa-table table.aa-tbl")[0];
      const col = Array.from(table.querySelectorAll("colgroup col")).find((c) => c.getAttribute("data-ck") === cle);
      if (!col) return;
      const depart = parseFloat(col.style.width), total = parseFloat(table.style.width), x0 = e.pageX;
      $("body").css({ cursor: "col-resize", "user-select": "none" });
      $(document).on("mousemove.aarz", (ev) => {
        const w = Math.max(40, depart + ev.pageX - x0);
        col.style.width = w + "px";
        table.style.width = (total + w - depart) + "px";
      }).on("mouseup.aarz", () => {
        $(document).off(".aarz");
        $("body").css({ cursor: "", "user-select": "" });
        this.cols_w[cle] = Math.round(parseFloat(col.style.width));
        this._rz_fin = Date.now();
        noter_largeurs();
      });
    });
    $(this.wrapper).on("click", ".aa-rz", (e) => e.stopPropagation());
    $(this.wrapper).on("dblclick", ".aa-rz", (e) => {
      e.stopPropagation();
      delete this.cols_w[$(e.currentTarget).closest("th").attr("data-ck")];
      noter_largeurs();
      if (this.data) this._render();
    });
    $(this.wrapper).on("click", "#aa-cols-w-raz", (e) => {
      e.preventDefault();
      this.cols_w = {};
      noter_largeurs();
      if (this.data) this._render();
    });
    // Colonnes repliables : la pastille au-dessus du tableau, ou le ⊟ de l'en-tête.
    $(this.wrapper).on("click", ".aa-col-chip, .aa-col-x", (e) => {
      e.stopPropagation();                                      // pas de tri quand on replie « Groupe »
      e.preventDefault();
      const col = $(e.currentTarget).attr("data-col");
      if (col === "*") this.cols_off.clear();
      else if (this.cols_off.has(col)) this.cols_off.delete(col);
      else this.cols_off.add(col);
      try { localStorage.setItem("aa_cols_off", JSON.stringify(Array.from(this.cols_off))); } catch (err) { /* stockage refusé : sans effet */ }
      if (this.data) this._render();
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
    (f.price_lists || []).forEach((pl) => {
      $("#aa-tri-liste").append(`<option value="${frappe.utils.escape_html(pl.name)}">${frappe.utils.escape_html(pl.name)}</option>`);
    });
    if ((f.price_lists || []).some((pl) => pl.name === "Vente standard")) $("#aa-tri-liste").val("Vente standard");
    $("#aa-zone-listes").html((f.price_lists || []).map((pl, i) => `<label style="margin:0 14px 0 0;font-weight:400">
        <input type="checkbox" class="aa-zone-liste" value="${frappe.utils.escape_html(pl.name)}" ${i === 0 ? "checked" : ""}> ${frappe.utils.escape_html(pl.name)}</label>`).join("")
      + `<a href="#" id="aa-zone-toutes" style="font-size:12px">toutes</a>`);
    (f.item_groups || []).forEach((g) => {
      // L'arborescence : un retrait par niveau (espaces insécables — les espaces ordinaires sont avalés dans une
      // liste déroulante) et un dossier devant les familles qui en contiennent d'autres.
      $("#aa-regle-famille").append(`<option value="${frappe.utils.escape_html(g.name)}">${"\u00a0\u00a0\u00a0\u00a0".repeat(g.depth || 0)}${g.is_group ? "📁 " : ""}${frappe.utils.escape_html(g.name)}</option>`);
    });
    this._charger_regle();
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
          prix_liste: $("#aa-tri-liste").val() || "",
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

  // `sous_min` : la marge est positive mais sous la marge minimale garantie (orange ; le rouge reste la perte).
  _marge(v, sous_min) {
    if (v == null) return '<span class="aa-muted">—</span>';
    const cls = v < 0 ? "aa-marge-bad" : sous_min ? "aa-marge-min" : "aa-marge-pos";
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
    // Colonnes repliables : Groupe, Valorisation (HT / TVA / TTC) et chaque liste de prix.
    const off = this.cols_off;
    const voir_groupe = !off.has("groupe"), voir_valo = !off.has("valo");
    const pls_vis = pls.filter((pl) => !off.has("pl:" + pl));
    this.vue = { voir_groupe, voir_valo, pls_vis,
                 colonnes: 3 + (voir_groupe ? 1 : 0) + (voir_valo ? 3 : 0) + pls_vis.length * 2 };
    const replier = (col) => `<span class="aa-col-x" data-col="${this._esc(col)}" title="Replier cette colonne">⊟</span>`;
    const pastilles = [["groupe", "Groupe"], ["valo", "Valorisation"]].concat(pls.map((pl) => ["pl:" + pl, pl]))
      .map(([col, nom]) => `<span class="aa-col-chip${off.has(col) ? " off" : ""}" data-col="${this._esc(col)}"
          title="${off.has(col) ? "Afficher" : "Replier"} cette colonne">${off.has(col) ? "＋" : "✓"} ${this._esc(nom)}</span>`).join("");
    const barre = `<div class="aa-cols"><span>Colonnes :</span>${pastilles}${
      off.size ? '<a href="#" class="aa-col-x" data-col="*" style="opacity:1;font-size:12px">tout afficher</a>' : ""}
      <a href="#" id="aa-cols-w-raz" style="margin-left:7px;${Object.keys(this.cols_w).length ? "" : "display:none"}">largeurs par défaut</a></div>`;
    // Chaque colonne réelle a une clé (sa largeur se règle et se retient) et une poignée sur son bord droit.
    const poignee = '<span class="aa-rz" title="Tirer pour élargir ou rétrécir — double-clic : largeur par défaut"></span>';
    const cles = ["art", "des"].concat(voir_groupe ? ["groupe"] : [], voir_valo ? ["valo_ht", "tva", "valo_ttc"] : []);
    pls_vis.forEach((pl) => cles.push("pv:" + pl, "mg:" + pl));
    const colgroup = `<colgroup><col data-ck="chk">${cles.map((k) => `<col data-ck="${this._esc(k)}">`).join("")}</colgroup>`;

    let head = `<tr>
      <th rowspan="2" style="width:26px"><input type="checkbox" id="aa-chk-all" title="Tout cocher (page)"></th>
      <th rowspan="2" class="sortable" data-field="item_code" data-ck="art">Code article${this._sort_icon("item_code")}${poignee}</th>
      <th rowspan="2" class="sortable" data-field="item_name" data-ck="des">Désignation${this._sort_icon("item_name")}${poignee}</th>`;
    if (voir_groupe) head += `<th rowspan="2" class="sortable" data-field="item_group" data-ck="groupe">Groupe${this._sort_icon("item_group")}${replier("groupe")}${poignee}</th>`;
    if (voir_valo) head += `<th class="num pl-head" colspan="3">Valorisation${replier("valo")}</th>`;
    pls_vis.forEach((pl) => {
      head += `<th class="num pl-head" colspan="2">${this._esc(pl)}${replier("pl:" + pl)}</th>`;
    });
    head += "</tr><tr>";
    if (voir_valo) head += `<th class="num pl-head" data-ck="valo_ht">Valo. HT${poignee}</th><th class="num" data-ck="tva">TVA %${poignee}</th><th class="num" data-ck="valo_ttc">Valo. TTC${poignee}</th>`;
    pls_vis.forEach((pl) => {
      head += `<th class="num pl-head" data-ck="${this._esc("pv:" + pl)}">PV TTC${poignee}</th><th class="num" data-ck="${this._esc("mg:" + pl)}">Marge${poignee}</th>`;
    });
    head += "</tr>";

    // Tri par famille : un titre à chaque changement de famille, et un sous-titre par modèle à variantes.
    const par_famille = this.order_by === "famille_prix";
    const colonnes = this.vue.colonnes;
    let famille = null, modele = null;
    const body = d.rows.map((r) => {
      let titres = "";
      if (par_famille) {
        if (r.item_group !== famille) {
          famille = r.item_group; modele = null;
          titres += `<tr class="aa-famille"><td colspan="${colonnes}" style="background:var(--subtle-fg);font-weight:700;padding:7px 10px">📁 ${this._esc(famille || "Sans famille")}</td></tr>`;
        }
        const m = r.variant_of || (r.has_variants ? r.item_code : null);
        if (m !== modele) {
          modele = m;
          if (m) titres += `<tr class="aa-modele"><td colspan="${colonnes}" style="padding:5px 10px 5px 26px;font-weight:600;color:var(--text-muted)">↳ Modèle ${this._esc(m)} et ses variantes</td></tr>`;
        }
      }
      return titres + this._render_row(r, pls);
    }).join("");
    $("#aa-table").html(`${barre}<table class="aa-tbl">${colgroup}<thead>${head}</thead><tbody>${body}</tbody></table>`);
    if (Object.keys(this.cols_w).length) this._figer_largeurs();
    this._maj_libelle_bloc();
  }

  // Passe le tableau en largeurs fixes : chaque colonne garde sa largeur naturelle du moment, sauf celles réglées
  // à la main, qui prennent la largeur retenue. Sans aucun réglage, le tableau reste en largeurs automatiques.
  _figer_largeurs() {
    const table = $("#aa-table table.aa-tbl")[0];
    if (!table || table.classList.contains("aa-fixe")) return;
    const entetes = {};
    table.querySelectorAll("thead th[data-ck]").forEach((th) => { entetes[th.getAttribute("data-ck")] = th; });
    const cols = Array.from(table.querySelectorAll("colgroup col"));
    const largeurs = cols.map((c) => {
      const cle = c.getAttribute("data-ck");
      const th = cle === "chk" ? table.querySelector("thead th") : entetes[cle];
      return this.cols_w[cle] || (th ? th.getBoundingClientRect().width : 60);
    });
    cols.forEach((c, i) => { c.style.width = largeurs[i] + "px"; });
    table.style.width = largeurs.reduce((a, b) => a + b, 0) + "px";
    table.classList.add("aa-fixe");
  }

  _maj_libelle_bloc() {
    const n = this.selection.size, total = this.data ? this.data.total : 0;
    $("#aa-zone-portee").html(n ? `— s’applique aux <b>${n}</b> article(s) <b>cochés</b>`
                                : `— aucun article coché : s’applique aux <b>${total}</b> article(s) du filtre actuel`);
  }

  _render_row(r, pls) {
    const img = r.image
      ? `<img class="aa-img" src="${this._esc(r.image)}" loading="lazy">`
      : `<span class="aa-img-empty">🖼</span>`;
    const b = r.bundle;
    const toggle = b ? `<span class="aa-toggle">▸</span>` : "";
    const badge = b ? `<span class="aa-badge">BUNDLE</span>` : "";
    const desc = r.description
      ? `<div class="aa-desc">${this._esc(strip_html(r.description))}</div>` : "";

    // L'article : la photo, et son code juste dessous (une seule colonne) ; la désignation vient ensuite.
    const v = this.vue;
    let cells = `
      <td><input type="checkbox" class="aa-sel" data-item="${this._esc(r.item_code)}"
           ${this.selection.has(r.item_code) ? "checked" : ""}></td>
      <td class="aa-art"><div class="aa-art-in">${img}
        <div>${toggle}<span class="aa-code">${this._esc(r.item_code)}</span></div>${badge}</div></td>
      <td class="aa-des">${this._esc(r.item_name)}${desc}</td>`;
    if (v.voir_groupe) cells += `<td>${this._esc(r.item_group)}</td>`;
    if (v.voir_valo) cells += `
      <td class="num pl-first">${this._money(b ? b.cout_ht : r.val_ht)}</td>
      <td class="num">${r.tva == null ? '<span class="aa-muted">—</span>' : format_number(r.tva, null, 1)}</td>
      <td class="num">${this._money(b ? b.cout_ttc : r.val_ttc)}</td>`;
    v.pls_vis.forEach((pl) => {
      const p = r.prices[pl] || {};
      // Sous le prix : l'écart avec la Vente standard (ce que cette liste « remise » par rapport à la vente).
      const ecart = p.ecart_ref == null ? "" : `<div class="aa-ecart" title="Écart avec le prix Vente standard"
          style="font-size:11px;color:${p.ecart_ref > 0 ? "#b45309" : "var(--text-muted)"}">${p.ecart_ref > 0 ? "+" : p.ecart_ref < 0 ? "−" : ""}${format_number(Math.abs(p.ecart_ref), null, 1)} % / vente</div>`;
      // Les réductions applicables (règles de prix sur l'article ou sa famille) : prix remisé, et SA marge en face.
      const remises = p.remises || [];
      const prixRemises = remises.map((x) => `<div class="aa-remise" title="${this._esc(x.titre)}"
          style="font-size:11px;white-space:nowrap;margin-top:2px"><span style="background:#fef3c7;color:#92400e;border-radius:8px;padding:0 6px;font-weight:600">🏷 ${this._esc(x.libelle)}${x.palier ? " " + this._esc(x.palier) : ""}</span> ${this._money(x.prix)}</div>`).join("");
      const margesRemises = remises.map((x) => `<div style="font-size:11px;margin-top:2px" title="${x.sous_min ? "Sous la marge minimale garantie" : "Marge au prix remisé"}">${this._marge(x.marge, x.sous_min)}</div>`).join("");
      // Sous la marge minimale garantie (le prix lui-même ou une de ses promos) : le prix qui la rétablirait.
      const mini = p.sous_min ? `<div class="aa-mini" title="Le plus petit prix de liste qui garde ${format_number(this.data.marge_min, null, 1)} % de marge, même à la remise la plus forte">⚠ mini ${format_number(p.plancher, null, 3)}</div>` : "";
      cells += `<td class="num pl-first aa-pv" style="cursor:pointer" title="Cliquer pour modifier ce prix"
                    data-item="${this._esc(r.item_code)}" data-pl="${this._esc(pl)}"
                    data-pv="${p.pv_ttc == null ? "" : p.pv_ttc}">${this._money(p.pv_ttc)} <span class="aa-muted">✏️</span>${ecart}${prixRemises}${mini}</td>
                <td class="num">${this._marge(p.marge, p.sous_min_prix)}${ecart ? '<div style="font-size:11px">&nbsp;</div>' : ""}${margesRemises}</td>`;
    });
    let html = `<tr>${cells}</tr>`;
    if (b) html += this._render_bundle_detail(r, pls);
    return html;
  }

  _render_bundle_detail(r, pls) {
    const b = r.bundle;
    const colspan = this.vue.colonnes;

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
        <td class="num"><b>${p.regle_ttc == null ? '<span class="aa-incomplet">incomplet</span>' : this._money(p.regle_ttc)}</b>${
          p.regle_calcule == null ? "" : `<div class="aa-mini" title="La somme selon la règle donnerait ${format_number(p.regle_calcule, null, 3)} : relevé pour garder la marge minimale garantie">relevé de ${format_number(p.regle_calcule, null, 3)}</div>`}</td>
        <td class="num">${this._marge(p.regle_marge)}</td>
      </tr>`;
    }).join("");

    let compRows = b.components.map((c) => {
      const prices = pls.map((pl) =>
        `<td class="num">${this._money(c.prices[pl])}</td>`).join("");
      return `<tr class="${c.exclu ? "aa-exclu" : ""}">
        <td>${this._esc(c.item_code)}${c.exclu ? '<span class="aa-nc" title="Non compté dans le prix de l’ensemble (règle)">non compté</span>' : ""}</td>
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
        <th class="num">Somme de tous les composants</th><th class="num">Marge</th>
        <th class="num">Prix selon la règle</th><th class="num">Marge</th></tr></thead>
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
        { fieldtype: "Check", fieldname: "garantir", default: 1, hidden: this.marge_min > 0 ? 0 : 1,
          label: __("Garantir la marge minimale de {0} % (promos comprises)", [format_number(this.marge_min, null, 1)]) },
      ],
      primary_action_label: __("Aperçu"),
      primary_action: (v) => {
        d.hide();
        this._apercu_et_appliquer(
          { mode: "liste", changements: [{ item_code, price_list, nouveau: v.nouveau }], garantir: v.garantir ? 1 : 0 },
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

  // ── ✏️ Modifier les prix : une ZONE REPLIABLE au-dessus du tableau (19/11/2026) ──────────────────────────
  // Elle reste ouverte pendant qu'on filtre et qu'on coche, au lieu d'un dialogue à rouvrir à chaque fois.
  // Dedans : l'action sur les prix, et — repliée — la règle de prix des ensembles (ce qu'on ne compte pas dans
  // leur prix : par famille, par début de code, par mot contenu) avec son recalcul automatique.
  _poser_zone_prix() {
    const ouvert = (() => { try { return localStorage.getItem("aa_zone_prix") === "1"; } catch (e) { return false; } })();
    $("#aa-main .aa-card").first().after(`
      <div class="aa-card" id="aa-zone-prix" style="padding:0;margin-top:12px">
        <div id="aa-zone-tete" style="cursor:pointer;padding:11px 16px;font-weight:700;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span id="aa-zone-fleche">${ouvert ? "▾" : "▸"}</span> ✏️ Modifier les prix
          <span id="aa-zone-portee" style="font-weight:400;font-size:12.5px;color:var(--text-muted)"></span></div>
        <div id="aa-zone-corps" style="display:${ouvert ? "block" : "none"};padding:0 16px 14px">
          <div style="margin-bottom:10px"><div class="aa-zone-lbl">Listes de prix</div><div id="aa-zone-listes"></div></div>
          <div style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap">
            <div><div class="aa-zone-lbl">Action</div>
              <select id="aa-zone-action" class="form-control" style="width:420px">
                <option value="pct">Variation en % (ex. 5 ou -10)</option>
                <option value="montant">Variation en montant (ex. 2.5 ou -1)</option>
                <option value="fixe">Prix fixe pour tous</option>
                <option value="ensemble">Ensembles : somme des composants (selon la règle)</option>
                <option value="plancher">Relever les prix sous la marge minimale garantie (promos comprises)</option>
              </select></div>
            <div id="aa-zone-valeur-zone"><div class="aa-zone-lbl">Valeur</div>
              <input type="number" step="any" id="aa-zone-valeur" class="form-control" style="width:120px"></div>
            <label id="aa-zone-cascade-zone" style="margin:0 0 7px;font-weight:400">
              <input type="checkbox" id="aa-zone-cascade" checked> Réaligner les ensembles qui contiennent ces articles</label>
            <label id="aa-zone-garantir-zone" style="margin:0 0 7px;font-weight:400;display:none">
              <input type="checkbox" id="aa-zone-garantir" checked> Garantir la marge minimale (<span id="aa-zone-garantir-taux"></span> %)</label>
            <button class="btn btn-primary btn-sm" id="aa-zone-apercu" style="margin-bottom:2px">Aperçu avant d’appliquer</button>
          </div>
          <div id="aa-zone-aide" style="font-size:12px;color:var(--text-muted);margin-top:8px"></div>
          <details id="aa-regle" style="margin-top:12px;border-top:1px solid var(--border-color);padding-top:10px">
            <summary style="cursor:pointer;font-weight:600">⚙️ Règles de prix <span id="aa-regle-resume" style="font-weight:400;font-size:12.5px;color:var(--text-muted)"></span></summary>
            <div style="font-weight:600;margin-top:12px">🛡️ Marge minimale garantie</div>
            <div style="font-size:12.5px;color:var(--text-muted);margin:6px 0 8px">Aucun prix posé depuis cette page ne laisse moins que cette marge, <b>même à la remise la plus forte</b> (tous les paliers de quantité comptent) : variation, prix fixe, règle des ensembles et recalcul automatique sont relevés au plus petit prix qui la garde. La marge se mesure sur le <b>prix de vente</b>. Rien ne baisse à cause d’elle.</div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
              <input type="number" id="aa-regle-marge" class="form-control" min="0" max="99" step="any" placeholder="aucune" style="width:100px">
              <span>% du prix de vente</span>
              <span id="aa-regle-commande" style="font-size:12px;color:var(--text-muted)"></span>
            </div>
            <div id="aa-regle-impact" style="font-size:12.5px;margin-top:8px"></div>
            <div style="font-weight:600;margin-top:16px;border-top:1px solid var(--border-color);padding-top:10px">📦 Prix des ensembles</div>
            <div style="font-size:12.5px;color:var(--text-muted);margin:8px 0">Le prix d’un ensemble = la somme de ses composants, <b>sans compter</b> ceux que vous désignez ici (accessoires offerts, par exemple). Trois façons de les désigner, qui s’additionnent.<br>La <b>valorisation</b> de l’ensemble (son coût de revient) compte toujours <b>tous</b> ses composants : la marge affichée reste la vraie.</div>
            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px">
              <div><div class="aa-zone-lbl">Par famille (sous-familles comprises)</div>
                <select id="aa-regle-famille" class="form-control"><option value="">＋ ajouter une famille…</option></select>
                <div id="aa-regle-familles" style="display:flex;flex-wrap:wrap;gap:5px;margin-top:6px"></div></div>
              <div><div class="aa-zone-lbl">Le code commence par</div>
                <input type="text" id="aa-regle-debuts" class="form-control" placeholder="ex. : Acc0, Rel-">
                <div style="font-size:11.5px;color:var(--text-muted);margin-top:3px">plusieurs : séparés par une virgule</div></div>
              <div><div class="aa-zone-lbl">Le code ou le nom contient</div>
                <input type="text" id="aa-regle-contient" class="form-control" placeholder="ex. : clip, coude">
                <div style="font-size:11.5px;color:var(--text-muted);margin-top:3px">sans tenir compte des majuscules ni des accents</div></div>
            </div>
            <label style="margin:12px 0 0;font-weight:400;display:block">
              <input type="checkbox" id="aa-regle-auto"> <b>Calcul automatique</b> : quand un composant change de prix (ici ou ailleurs), le prix des ensembles qui le contiennent suit tout seul</label>
            <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
              <button class="btn btn-default btn-sm" id="aa-regle-enregistrer">Enregistrer les règles</button>
              <button class="btn btn-default btn-sm" id="aa-regle-tous">Aperçu : appliquer la règle à tous les ensembles</button>
            </div>
          </details>
        </div>
      </div>
      <style>.aa-zone-lbl{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.03em;color:var(--text-muted);margin-bottom:3px}
        .aa-chip-famille{background:var(--subtle-fg);border:1px solid var(--border-color);border-radius:14px;padding:2px 9px;font-size:12px;cursor:pointer}
        .aa-marge-min{color:#c2410c;font-weight:700} .aa-mini{font-size:11px;font-weight:600;color:#c2410c;white-space:nowrap;margin-top:2px}
        .aa-exclu td{color:var(--text-muted)} .aa-exclu .aa-nc{background:#fef3c7;color:#92400e;border-radius:9px;padding:0 7px;font-size:10.5px;font-weight:700;margin-left:6px}</style>`);

    $("#aa-zone-tete").on("click", () => {
      const montre = $("#aa-zone-corps").toggle().is(":visible");
      $("#aa-zone-fleche").text(montre ? "▾" : "▸");
      try { localStorage.setItem("aa_zone_prix", montre ? "1" : "0"); } catch (e) { /* stockage refusé : sans effet */ }
    });
    $(this.wrapper).on("click", "#aa-zone-toutes", (e) => {
      e.preventDefault();
      const toutes = $(".aa-zone-liste").length === $(".aa-zone-liste:checked").length;
      $(".aa-zone-liste").prop("checked", !toutes);
    });
    const maj_action = () => {
      const action = $("#aa-zone-action").val();
      const ensemble = action === "ensemble", plancher = action === "plancher";
      $("#aa-zone-valeur-zone").toggle(!ensemble && !plancher);
      $("#aa-zone-cascade-zone").toggle(!ensemble);
      // La case de la garantie n'a de sens que si une marge minimale est enregistrée ; « Relever » la porte d'office.
      $("#aa-zone-garantir-zone").toggle(this.marge_min > 0 && !plancher);
      $("#aa-zone-garantir-taux").text(format_number(this.marge_min, null, 1));
      $("#aa-zone-aide").text(ensemble
        ? "Seuls les ensembles parmi les articles visés sont touchés : leur prix devient la somme de leurs composants selon la règle ci-dessous. Un ensemble dont un composant compté n’a pas de prix est laissé tel quel."
        : plancher
        ? "Seuls les prix qui sont sous leur plancher bougent, et ils y montent : le plus petit prix qui garde la marge minimale même à la remise la plus forte. Les articles sans coût connu sont laissés tels quels."
        : "Les articles sans prix sur une liste y sont ignorés (sauf « Prix fixe »). L’ancien prix est clos à hier, le nouveau prend effet aujourd’hui : les commandes déjà passées gardent leur tarif.");
    };
    this._maj_action = maj_action;
    $("#aa-zone-action").on("change", maj_action);
    maj_action();
    // L'effet d'un taux se voit pendant qu'on le tape, avant d'enregistrer.
    let minuterie = null;
    $("#aa-regle-marge").on("input", () => { clearTimeout(minuterie); minuterie = setTimeout(() => this._impact_marge(), 450); });
    $(this.wrapper).on("click", "#aa-regle-voir", async (e) => {
      e.preventDefault();
      await this._enregistrer_regle(true);
      $("#aa-marge").val("sous_min"); this.start = 0; this._load();
    });
    $("#aa-zone-apercu").on("click", () => this._apercu_zone(false));

    // ── la règle des ensembles ──
    this.regle_familles = [];
    $("#aa-regle-famille").on("change", (e) => {
      const f = $(e.currentTarget).val();
      if (f && !this.regle_familles.includes(f)) { this.regle_familles.push(f); this._rendre_familles(); }
      $(e.currentTarget).val("");
    });
    $(this.wrapper).on("click", ".aa-chip-famille", (e) => {
      this.regle_familles = this.regle_familles.filter((x) => x !== $(e.currentTarget).attr("data-famille"));
      this._rendre_familles();
    });
    $("#aa-regle-enregistrer").on("click", () => this._enregistrer_regle());
    $("#aa-regle-tous").on("click", async () => { await this._enregistrer_regle(true); this._apercu_zone(true); });
  }

  _rendre_familles() {
    $("#aa-regle-familles").html(this.regle_familles.map((f) =>
      `<span class="aa-chip-famille" data-famille="${this._esc(f)}" title="Retirer">${this._esc(f)} ✕</span>`).join(""));
  }

  _resumer_regle(r) {
    const n = (r.familles || []).length + (r.debuts || []).length + (r.contient || []).length;
    $("#aa-regle-resume").text(`— marge minimale ${r.marge_min > 0 ? format_number(r.marge_min, null, 1) + " %" : "non garantie"} · ensembles : ${n ? n + " exclusion(s)" : "somme de tous les composants"} · calcul automatique ${r.auto ? "ACTIVÉ" : "désactivé"}`);
    this.marge_min = r.marge_min > 0 ? r.marge_min : 0;
    if (this._maj_action) this._maj_action();
  }

  // Combien de prix passeraient sous le plancher au taux TAPÉ (pas encore enregistré) : la règle montre son effet.
  async _impact_marge() {
    const taux = parseFloat($("#aa-regle-marge").val());
    const $z = $("#aa-regle-impact");
    if (!(taux > 0 && taux < 100)) { $z.html('<span style="color:var(--text-muted)">Aucune marge minimale garantie : les prix ne sont pas surveillés.</span>'); return; }
    const jeton = (this._jeton_impact = (this._jeton_impact || 0) + 1);
    const m = (await frappe.call({ method: "customization_app.analyse_articles.impact_marge_min", args: { taux } })).message || {};
    if (jeton !== this._jeton_impact) return;            // une frappe plus récente a déjà répondu
    const t = format_number(taux, null, 1);
    if (!m.sous) { $z.html(`<span class="aa-marge-pos">✓ À ${t} % : les ${m.prix} prix dont on connaît le coût tiennent tous, promos comprises.</span>`); return; }
    const listes = Object.keys(m.par_liste || {}).map((l) => `${this._esc(l)} ${m.par_liste[l]}`).join(" · ");
    $z.html(`<span class="aa-marge-min">⚠ À ${t} % : ${m.sous} prix sur ${m.prix} sont sous le minimum</span>
      (${m.articles} article(s)${m.dont_promo ? `, dont ${m.dont_promo} seulement à cause d’une promo` : ""}) —
      <span style="color:var(--text-muted)">${listes}</span> · <a href="#" id="aa-regle-voir">enregistrer et voir ces articles</a>`);
  }

  async _charger_regle() {
    const r = (await frappe.call({ method: "customization_app.analyse_articles.get_regle_ensembles" })).message || {};
    this.regle_familles = r.familles || [];
    this._rendre_familles();
    $("#aa-regle-debuts").val((r.debuts || []).join(", "));
    $("#aa-regle-contient").val((r.contient || []).join(", "));
    $("#aa-regle-auto").prop("checked", !!r.auto);
    $("#aa-regle-marge").val(r.marge_min > 0 ? r.marge_min : "");
    $("#aa-regle-commande").text(r.marge_commande > 0
      ? `(à la vente, la commande client relève déjà ses lignes sous ${format_number(r.marge_commande, null, 1)} %)` : "");
    this._resumer_regle(r);
    this._impact_marge();
  }

  async _enregistrer_regle(silencieux) {
    const r = (await frappe.call({
      method: "customization_app.analyse_articles.enregistrer_regle_ensembles", type: "POST",
      args: { familles: JSON.stringify(this.regle_familles), debuts: $("#aa-regle-debuts").val() || "",
              contient: $("#aa-regle-contient").val() || "", auto: $("#aa-regle-auto").is(":checked") ? 1 : 0,
              marge_min: parseFloat($("#aa-regle-marge").val()) || 0 },
    })).message || {};
    this._resumer_regle(r);
    if (!silencieux) {
      frappe.show_alert({ message: __("Règles de prix enregistrées"), indicator: "green" }, 5);
      this._load();           // le détail des ensembles montre aussitôt le prix selon la nouvelle règle
    }
  }

  // Construit la demande depuis la zone, puis ouvre l'aperçu (rien n'est appliqué sans lui).
  _apercu_zone(tous_les_ensembles) {
    const listes = $(".aa-zone-liste:checked").map((_, el) => el.value).get();
    if (!listes.length) { frappe.msgprint(__("Cochez au moins une liste de prix.")); return; }
    const operation = tous_les_ensembles ? "ensemble" : $("#aa-zone-action").val();
    const valeur = parseFloat($("#aa-zone-valeur").val());
    const sans_valeur = operation === "ensemble" || operation === "plancher";
    if (!sans_valeur && isNaN(valeur)) { frappe.msgprint(__("Indiquez la valeur.")); return; }
    if (operation === "plancher" && !(this.marge_min > 0)) {
      frappe.msgprint(__("Indiquez d’abord la marge minimale garantie dans « Règles de prix », puis enregistrez.")); return;
    }
    const cible = { mode: "bloc", price_lists: listes, operation, valeur: isNaN(valeur) ? 0 : valeur,
                    garantir: $("#aa-zone-garantir").is(":checked") ? 1 : 0 };
    if (tous_les_ensembles) cible.tous_ensembles = 1;
    else if (this.selection.size) cible.item_codes = Array.from(this.selection);
    else {
      cible.search = $("#aa-search").val() || "";
      cible.item_group = $("#aa-group").val() || "";
      cible.marge = $("#aa-marge").val() || "";
    }
    this._apercu_et_appliquer(cible, operation !== "ensemble" && $("#aa-zone-cascade").is(":checked") ? 1 : 0);
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
    // Marge minimale garantie : un prix relevé au plancher le dit (avec le prix qu'il aurait eu) ; garantie
    // coupée, le prix qui passe dessous est signalé.
    const note = (c) => c.calcule != null ? `<div class="aa-mini">relevé de ${fmt(c.calcule)} (marge mini)</div>`
      : c.sous_min ? `<div class="aa-mini">⚠ sous la marge mini — plancher ${fmt(c.plancher)}</div>` : "";
    const releves = m.changements.filter((c) => c.calcule != null).length + m.bundles.filter((b) => b.calcule != null).length;
    const signales = m.changements.filter((c) => c.sous_min).length + m.bundles.filter((b) => b.sous_min).length;
    const lignes = m.changements.slice(0, 400).map((c) =>
      `<tr><td>${esc(c.item_code)}</td><td>${esc(c.item_name)}</td>
       <td>${esc(c.price_list)}</td><td class="num">${fmt(c.ancien)}</td>
       <td class="num"><b>${fmt(c.nouveau)}</b>${note(c)}</td></tr>`).join("");
    const bundles = m.bundles.map((b, i) =>
      `<tr><td><input type="checkbox" class="aa-b-chk" data-b="${esc(b.bundle)}"
            ${b.incomplet ? "disabled" : "checked"}></td>
       <td>${esc(b.bundle)}</td><td>${esc(b.item_name)}</td>
       <td>${esc(b.price_list)}</td><td class="num">${fmt(b.ancien)}</td>
       <td class="num">${b.incomplet ? '<span style="color:#b45309">incomplet — non touché</span>' : "<b>" + fmt(b.nouveau) + "</b>" + note(b)}</td></tr>`).join("");

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
    const bandeau = releves ? `<div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:8px 12px;margin-bottom:10px;font-size:12.5px">🛡️ <b>${releves}</b> prix relevé(s) à leur plancher pour garder <b>${format_number(m.marge_min, null, 1)} %</b> de marge, promos comprises.</div>`
      : signales ? `<div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:8px;padding:8px 12px;margin-bottom:10px;font-size:12.5px">⚠ <b>${signales}</b> prix passeraient sous la marge minimale de <b>${format_number(m.marge_min, null, 1)} %</b> (garantie décochée).</div>` : "";
    d.fields_dict.zone.$wrapper.html(`${bandeau}
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
