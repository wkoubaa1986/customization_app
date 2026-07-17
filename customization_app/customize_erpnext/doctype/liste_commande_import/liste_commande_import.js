frappe.ui.form.on("Liste Commande Import", {
  refresh(frm) {
    // le grid natif est remplacé par la table custom (données inchangées)
    frm.get_field("articles").$wrapper.hide();
    lci_render_table(frm);

    frm.add_custom_button(__("➕ Article du catalogue"), () => lci_add_catalogue(frm));
    frm.add_custom_button(__("➕ Article libre"), () => lci_add_libre(frm));
    frm.add_custom_button(__("🗂 Organiser par groupe puis code"), () => lci_organize(frm));

    // chemins de groupes (pour en-têtes) — chargés une fois puis re-render
    if (!frm.__lci_paths) {
      frappe.call({ method: "customization_app.liste_commande_import.get_group_paths" })
        .then((r) => { frm.__lci_paths = r.message || {}; lci_render_table(frm); })
        .catch(() => { frm.__lci_paths = {}; });
    }

    if (frm.is_new()) return;

    frm.add_custom_button(__("✨ Améliorer tout"), () => lci_ai(frm, "ai_improve_descriptions", null), __("IA"));
    frm.add_custom_button(__("🌐 Traduire tout vers ") + (frm.doc.langue_cible || "English"),
      () => lci_ai(frm, "ai_translate", null), __("IA"));

    frm.add_custom_button(__("📄 PDF cotation"), () => {
      window.open(`/api/method/customization_app.liste_commande_import.download_pdf?docname=${encodeURIComponent(frm.doc.name)}`);
    });
    frm.add_custom_button(__("📥 Excel"), () => {
      window.open(`/api/method/customization_app.liste_commande_import.download_excel?docname=${encodeURIComponent(frm.doc.name)}`);
    });

    if (frm.doc.volume_total_m3) {
      frm.dashboard.add_indicator(
        __("Volume total : {0} m³", [format_number(frm.doc.volume_total_m3, null, 3)]),
        frm.doc.volume_total_m3 > 60 ? "red" : "blue"
      );
    }
  },
  langue_cible(frm) { frm.refresh(); },
});

// ------------------------------------------------------------------ helpers

function lci_rows(frm) { return frm.doc.articles || []; }

function lci_recalc(frm, render = true) {
  let total = 0;
  lci_rows(frm).forEach((r) => {
    r.volume_ligne_m3 = flt(r.qty) * flt(r.volume_unitaire_m3);
    total += r.volume_ligne_m3;
  });
  frm.doc.volume_total_m3 = total;
  frm.doc.nb_articles = lci_rows(frm).length;
  if (render) lci_render_table(frm);
}

async function lci_add_catalogue(frm) {
  const d = new frappe.ui.Dialog({
    title: __("Ajouter un article du catalogue"),
    fields: [
      { fieldname: "item", label: __("Article"), fieldtype: "Link", options: "Item", reqd: 1,
        get_query: () => ({ filters: { disabled: 0 } }) },
      { fieldname: "qty", label: __("Quantité"), fieldtype: "Float", default: 1 },
    ],
    primary_action_label: __("Ajouter"),
    async primary_action(v) {
      d.hide();
      const r = await frappe.db.get_value("Item", v.item,
        ["item_name", "description", "image", "stock_uom", "custom_volume_m3", "item_group"]);
      const it = r.message || {};
      const row = frm.add_child("articles", {
        item_code: v.item,
        item_name: it.item_name || v.item,
        item_group: it.item_group || "",
        qty: flt(v.qty) || 1,
        uom: it.stock_uom || "Pièce",
        description: it.description || "",
        image: it.image || "",
        volume_unitaire_m3: flt(it.custom_volume_m3),
      });
      frm.dirty();
      lci_recalc(frm);
    },
  });
  d.show();
}

// --- Article libre : popup de création complète (image + nom + description) ---
function lci_add_libre(frm) {
  const d = new frappe.ui.Dialog({
    title: __("Nouvel article libre (hors catalogue)"),
    size: "large",
    fields: [
      { fieldname: "item_name", label: __("Désignation"), fieldtype: "Data", reqd: 1,
        description: __("Nom de l'article tel qu'il apparaîtra dans la cotation") },
      { fieldname: "c1", fieldtype: "Column Break" },
      { fieldname: "image", label: __("Image"), fieldtype: "Attach Image" },
      { fieldname: "s1", fieldtype: "Section Break" },
      { fieldname: "qty", label: __("Quantité"), fieldtype: "Float", default: 1 },
      { fieldname: "c2", fieldtype: "Column Break" },
      { fieldname: "uom", label: __("UDM"), fieldtype: "Data", default: "Pièce" },
      { fieldname: "c3", fieldtype: "Column Break" },
      { fieldname: "volume_unitaire_m3", label: __("Volume unitaire (m³)"), fieldtype: "Float" },
      { fieldname: "s2", fieldtype: "Section Break" },
      { fieldname: "description", label: __("Description technique"), fieldtype: "Text Editor" },
    ],
    primary_action_label: __("Ajouter à la liste"),
    primary_action(v) {
      frm.add_child("articles", {
        item_name: v.item_name,
        qty: flt(v.qty) || 1,
        uom: v.uom || "Pièce",
        volume_unitaire_m3: flt(v.volume_unitaire_m3),
        description: v.description || "",
        image: v.image || "",
      });
      d.hide();
      frm.dirty();
      lci_recalc(frm);
      frappe.show_alert({ message: __("Article libre ajouté — enregistrez le document."), indicator: "green" });
    },
  });
  d.show();
}

// --- Organisation : groupe (chemin hiérarchique complet) puis code article ---
async function lci_organize(frm) {
  if (!frm.__lci_paths) {
    const r = await frappe.call({ method: "customization_app.liste_commande_import.get_group_paths" });
    frm.__lci_paths = r.message || {};
  }
  const paths = frm.__lci_paths;
  // compléter item_group manquant depuis la fiche Article
  const missing = lci_rows(frm).filter((x) => x.item_code && !x.item_group).map((x) => x.item_code);
  if (missing.length) {
    const res = await frappe.call({
      method: "frappe.client.get_list",
      args: { doctype: "Item", filters: { name: ["in", missing] },
              fields: ["name", "item_group"], limit_page_length: 0 },
    });
    const m = {};
    (res.message || []).forEach((i) => (m[i.name] = i.item_group));
    lci_rows(frm).forEach((x) => { if (!x.item_group && m[x.item_code]) x.item_group = m[x.item_code]; });
  }
  frm.doc.articles.sort((a, b) => {
    const pa = (a.item_group && paths[a.item_group]) || "￿"; // articles libres à la fin
    const pb = (b.item_group && paths[b.item_group]) || "￿";
    if (pa !== pb) return pa < pb ? -1 : 1;
    return (a.item_code || a.item_name || "").localeCompare(b.item_code || b.item_name || "");
  });
  frm.doc.articles.forEach((x, i) => (x.idx = i + 1));
  frm.dirty();
  lci_render_table(frm);
  frappe.show_alert({ message: __("Articles organisés par groupe puis code — enregistrez pour figer l'ordre (PDF/Excel suivront)."), indicator: "blue" });
}

async function lci_ai(frm, method, row_name) {
  if (frm.is_dirty()) {
    frappe.msgprint(__("Enregistrez d'abord le document (Ctrl+S)."));
    return;
  }
  try {
    const r = await frappe.call({
      method: `customization_app.liste_commande_import.${method}`,
      args: { docname: frm.doc.name, row_names: row_name ? JSON.stringify([row_name]) : "" },
      freeze: true,
      freeze_message: __("IA en cours…"),
    });
    const n = (r.message?.updated || []).length;
    frappe.show_alert({ message: __("{0} ligne(s) mise(s) à jour", [n]), indicator: "green" });
    frm.reload_doc();
  } catch (err) {
    console.error(err);
  }
}

function lci_upload_image(frm, row) {
  if (frm.is_new()) {
    frappe.msgprint(__("Enregistrez d'abord le document pour joindre des images."));
    return;
  }
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/*";
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file, file.name);
    fd.append("is_private", "0");
    fd.append("doctype", frm.doc.doctype);
    fd.append("docname", frm.doc.name);
    try {
      const res = await fetch("/api/method/upload_file", {
        method: "POST",
        headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
        body: fd,
      });
      const out = await res.json();
      const url = out.message?.file_url;
      if (url) {
        frappe.model.set_value(row.doctype, row.name, "image", url);
        lci_render_table(frm);
      }
    } catch (e) {
      frappe.msgprint(__("Échec de l'upload."));
    }
  };
  input.click();
}

function lci_desc_dialog(frm, row) {
  const lang = frm.doc.langue_cible || "…";
  const d = new frappe.ui.Dialog({
    title: __("Textes — {0}", [row.item_name || row.item_code || ""]),
    size: "extra-large",
    fields: [
      { fieldname: "col_orig", fieldtype: "Column Break", label: __("Original (français)") },
      { fieldname: "item_name", label: __("Désignation"), fieldtype: "Data",
        default: row.item_name || "" },
      { fieldname: "description", label: __("Description technique"), fieldtype: "Text Editor",
        default: row.description || "" },
      { fieldname: "col_trad", fieldtype: "Column Break", label: __("Traduction ({0})", [lang]) },
      { fieldname: "item_name_traduit", label: __("Désignation traduite"), fieldtype: "Data",
        default: row.item_name_traduit || "" },
      { fieldname: "description_traduite", label: __("Description traduite"), fieldtype: "Text",
        default: row.description_traduite || "" },
    ],
    primary_action_label: __("Appliquer"),
    primary_action(v) {
      frappe.model.set_value(row.doctype, row.name, "item_name", v.item_name || "");
      frappe.model.set_value(row.doctype, row.name, "description", v.description || "");
      frappe.model.set_value(row.doctype, row.name, "item_name_traduit", v.item_name_traduit || "");
      frappe.model.set_value(row.doctype, row.name, "description_traduite", v.description_traduite || "");
      d.hide();
      lci_render_table(frm);
    },
  });
  d.show();
}

// ------------------------------------------------------------------ table

function lci_render_table(frm) {
  const field = frm.get_field("articles");
  if (!field || !field.$wrapper) return;
  let $t = field.$wrapper.parent().find(".lci-table-wrap");
  if (!$t.length) {
    $t = $('<div class="lci-table-wrap"></div>').insertBefore(field.$wrapper);
  }
  const rows = lci_rows(frm);
  const esc = frappe.utils.escape_html;
  const paths = frm.__lci_paths || {};
  const strip = (h) => $("<div>").html(h || "").text();

  let body = "";
  let prev_group = null;
  rows.forEach((r, i) => {
    // en-tête de groupe (chemin hiérarchique complet) quand le groupe change
    const g = r.item_group || "";
    if (g !== prev_group) {
      const label = g ? (paths[g] || g) : __("Articles libres / sans groupe");
      const members = rows.filter((x) => (x.item_group || "") === g);
      const vol = members.reduce((s, x) => s + flt(x.volume_ligne_m3), 0);
      body += `<tr class="lci-ghead"><td colspan="9">📁 ${esc(label)}
        <span class="lci-gmeta">· ${members.length} article(s) · ${format_number(vol, null, 3)} m³</span></td></tr>`;
      prev_group = g;
    }
    const tr_name = (r.item_name_traduit || "").trim();
    const tr_desc = strip(r.description_traduite);
    const tr_txt = [tr_name, tr_desc].filter(Boolean).join(" — ");
    body += `
    <tr data-name="${esc(r.name)}" data-idx="${i}">
      <td class="lci-c">${i + 1}</td>
      <td class="lci-c">
        <div class="lci-imgbox" data-act="image" title="${__("Changer l'image")}">
          ${r.image ? `<img src="${esc(r.image)}">` : `<span class="lci-noimg">📦</span>`}
        </div>
      </td>
      <td>
        ${r.item_code
          ? `<a href="/app/item/${encodeURIComponent(r.item_code)}" target="_blank" class="lci-code">${esc(r.item_code)}</a>`
          : `<span class="lci-libre">${__("libre")}</span>`}
        <input class="lci-inp lci-name" data-f="item_name" value="${esc(r.item_name || "")}"
               placeholder="${__("Désignation…")}">
        ${(() => {
          const d0 = strip(r.description);
          return d0 ? `<div class="lci-desc0" data-act="desc" title="${esc(d0)}">${esc(d0.slice(0, 100))}${d0.length > 100 ? "…" : ""}</div>` : "";
        })()}
        ${tr_txt ? `<div class="lci-tr" data-act="desc" title="${esc(tr_txt)}">🌐 ${esc(tr_txt.slice(0, 90))}${tr_txt.length > 90 ? "…" : ""}</div>` : ""}
      </td>
      <td><input class="lci-inp lci-num" data-f="qty" type="number" step="any" value="${r.qty ?? ""}"></td>
      <td><input class="lci-inp lci-uom" data-f="uom" value="${esc(r.uom || "")}"></td>
      <td><input class="lci-inp lci-num" data-f="volume_unitaire_m3" type="number" step="any"
                 value="${r.volume_unitaire_m3 ?? ""}"></td>
      <td class="lci-c lci-volligne">${format_number(flt(r.volume_ligne_m3), null, 3)}</td>
      <td class="lci-c lci-desc-state" data-act="desc" title="${__("Voir / éditer la description")}">
        ${r.description ? "📝" : '<span style="opacity:.25;">📝</span>'}${r.description_traduite ? " 🌐" : ""}
      </td>
      <td class="lci-actions">
        <button class="btn btn-xs btn-default" data-act="ai" title="${__("Améliorer cette ligne (IA)")}">✨</button>
        <button class="btn btn-xs btn-default" data-act="tr" title="${__("Traduire cette ligne (IA)")}">🌐</button>
        <button class="btn btn-xs btn-default" data-act="del" title="${__("Supprimer")}">🗑</button>
      </td>
    </tr>`;
  });

  $t.html(`
    <style>
      .lci-table-wrap { overflow-x: auto; margin-bottom: 8px; }
      table.lci-tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; }
      table.lci-tbl th { background: var(--bg-light-gray,#f6f8fa); color: #6b7280; font-size: 10.5px;
        text-transform: uppercase; padding: 6px 8px; text-align: left; white-space: nowrap;
        border-bottom: 1px solid var(--border-color,#e4e8ee); }
      table.lci-tbl td { padding: 5px 8px; border-bottom: 1px solid var(--border-color,#eef1f5); vertical-align: middle; }
      .lci-c { text-align: center; }
      .lci-imgbox { width: 54px; height: 54px; border-radius: 8px; background: var(--bg-light-gray,#f6f8fa);
        display: inline-flex; align-items: center; justify-content: center; cursor: pointer;
        border: 1px solid var(--border-color,#e4e8ee); overflow: hidden; }
      .lci-imgbox img { max-width: 100%; max-height: 100%; object-fit: contain; }
      .lci-imgbox:hover { box-shadow: 0 0 0 2px #91caff; }
      .lci-noimg { font-size: 22px; color: #c3cad4; }
      .lci-code { font-weight: 700; font-size: 11px; }
      .lci-libre { font-size: 10px; color: #ad6800; background: #fff7e6; border: 1px solid #ffd591;
        border-radius: 6px; padding: 1px 6px; }
      .lci-inp { border: 1px solid transparent; background: transparent; border-radius: 6px;
        padding: 3px 6px; width: 100%; font-size: 12.5px; }
      .lci-inp:hover { border-color: var(--border-color,#d5dae1); }
      .lci-inp:focus { border-color: #91caff; background: var(--card-bg,#fff); outline: none; }
      .lci-name { font-weight: 600; min-width: 180px; }
      .lci-desc0 { font-size: 11px; color: var(--text-muted,#8a93a0); margin-top: 2px; cursor: pointer;
                   max-width: 420px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .lci-desc0:hover { color: #0958d9; }
      .lci-tr { font-size: 11px; color: #135200; background: #f6ffed; border-radius: 6px; cursor: pointer;
                padding: 2px 6px; margin-top: 2px; max-width: 420px; overflow: hidden;
                text-overflow: ellipsis; white-space: nowrap; }
      tr.lci-ghead > td { background: #e6f4ff; font-weight: 800; font-size: 11.5px;
                          color: #0958d9; padding: 5px 10px; border-top: 2px solid #91caff; }
      .lci-gmeta { font-weight: 600; color: #6b7280; font-size: 10.5px; }
      .lci-num { width: 84px; text-align: right; }
      .lci-uom { width: 64px; }
      .lci-volligne { font-weight: 700; white-space: nowrap; }
      .lci-desc-state { cursor: pointer; font-size: 14px; white-space: nowrap; }
      /* colonne Actions toujours visible (collante à droite) */
      table.lci-tbl th:last-child, table.lci-tbl td.lci-actions {
        position: sticky; right: 0; background: var(--card-bg,#fff);
        box-shadow: -4px 0 6px -4px rgba(0,0,0,.15); }
      table.lci-tbl th:last-child { background: var(--bg-light-gray,#f6f8fa); z-index: 1; }
      .lci-actions { white-space: nowrap; }
      .lci-actions .btn { padding: 2px 7px; }
    </style>
    <table class="lci-tbl">
      <thead><tr>
        <th style="width:26px;">#</th><th style="width:60px;">${__("Image")}</th>
        <th>${__("Article / Désignation")}</th>
        <th style="width:90px;">${__("Qté")}</th><th style="width:70px;">UDM</th>
        <th style="width:90px;">${__("Vol. unit (m³)")}</th>
        <th style="width:80px;">${__("Vol. ligne")}</th>
        <th style="width:56px;">${__("Desc.")}</th>
        <th style="width:110px;">${__("Actions")}</th>
      </tr></thead>
      <tbody>${body}</tbody>
    </table>`);

  const row_of = (e) => {
    const name = $(e.currentTarget).closest("tr").data("name");
    return lci_rows(frm).find((r) => r.name === name);
  };

  // édition inline -> modèle EN TEMPS RÉEL (input), sans re-render pour garder
  // le focus. Ainsi Ctrl+S capture toujours la saisie en cours (articles libres).
  $t.find(".lci-inp").on("input change", (e) => {
    const row = row_of(e);
    if (!row) return;
    const f = $(e.currentTarget).data("f");
    let v = $(e.currentTarget).val();
    if (["qty", "volume_unitaire_m3"].includes(f)) v = flt(v);
    if (row[f] === v) return;
    frappe.model.set_value(row.doctype, row.name, f, v);
    if (["qty", "volume_unitaire_m3"].includes(f)) {
      lci_recalc(frm, false);
      const $tr = $(e.currentTarget).closest("tr");
      $tr.find(".lci-volligne").text(format_number(flt(row.volume_ligne_m3), null, 3));
    }
  });

  $t.find('[data-act="image"]').on("click", (e) => { const r = row_of(e); r && lci_upload_image(frm, r); });
  $t.find('[data-act="desc"]').on("click", (e) => { const r = row_of(e); r && lci_desc_dialog(frm, r); });
  $t.find('[data-act="ai"]').on("click", (e) => { const r = row_of(e); r && lci_ai(frm, "ai_improve_descriptions", r.name); });
  $t.find('[data-act="tr"]').on("click", (e) => { const r = row_of(e); r && lci_ai(frm, "ai_translate", r.name); });
  $t.find('[data-act="del"]').on("click", (e) => {
    const row = row_of(e);
    if (!row) return;
    frm.doc.articles = lci_rows(frm).filter((r) => r.name !== row.name);
    frm.doc.articles.forEach((r, i) => (r.idx = i + 1));
    frm.dirty();
    lci_recalc(frm);
    frm.refresh_field("articles");
  });
}
