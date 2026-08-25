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

    frm.add_custom_button(__("🏭 Produits assemblables"), () => lci_estimation_dialog(frm));

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

function lci_reindex(frm) {
  lci_rows(frm).forEach((r, i) => (r.idx = i + 1));
}

// articles additionnels embarqués dans une ligne pack (JSON sur la ligne)
function lci_adds(row) {
  try { return JSON.parse(row.articles_additionnels || "[]") || []; }
  catch (e) { return []; }
}

function lci_add_label(a) {
  const name = a.item_name || a.item_code || "";
  return a.brand ? `${name} (${a.brand})` : name;
}

function lci_set_adds(frm, row, list) {
  frappe.model.set_value(row.doctype, row.name, "articles_additionnels",
    list.length ? JSON.stringify(list) : "");
  frm.dirty();
  lci_recalc(frm);
}

function lci_add_additionnel(frm, row, it, qty_par_pack) {
  const list = lci_adds(row);
  const found = list.find((a) => a.item_code === it.item_code);
  if (found) {
    found.qty_par_pack = flt(qty_par_pack);
  } else {
    list.push({
      item_code: it.item_code,
      item_name: it.item_name || it.item_code,
      brand: it.brand || "",
      qty_par_pack: flt(qty_par_pack),
      uom: it.uom || "Pièce",
      image: it.image || "",
      volume_unitaire_m3: flt(it.volume_unitaire_m3),
    });
  }
  lci_set_adds(frm, row, list);
}

function lci_recalc(frm, render = true) {
  let total = 0;
  lci_rows(frm).forEach((r) => {
    let vol = flt(r.qty) * flt(r.volume_unitaire_m3);
    lci_adds(r).forEach((a) => {  // volume des additionnels embarqués
      vol += flt(r.qty) * flt(a.qty_par_pack) * flt(a.volume_unitaire_m3);
    });
    r.volume_ligne_m3 = vol;
    total += vol;
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
  const adds = lci_adds(row);
  const esc = frappe.utils.escape_html;
  const adds_html = !adds.length ? "" : `
    <div style="margin-top:6px;">
      ${adds.map((a) => `
        <div style="color:#a8071a;background:#fff1f0;border:1px solid #ffa39e;border-radius:6px;
                    padding:4px 8px;margin-bottom:4px;font-size:12px;">
          ➕ <b>ADDITIONNEL</b> : ${esc(lci_add_label(a))}
          — ${format_number(flt(a.qty_par_pack), null, 0)}/pack × ${format_number(flt(row.qty), null, 0)}
          = <b>${format_number(flt(a.qty_par_pack) * flt(row.qty), null, 0)} ${esc(a.uom || "")}</b>
        </div>`).join("")}
      <div class="lci-gmeta">${__("Ajoutés automatiquement EN ROUGE dans le PDF de cotation (description + quantité + image). Gestion via le bouton 🧬 de la ligne (✕ pour retirer).")}</div>
    </div>`;
  const d = new frappe.ui.Dialog({
    title: __("Textes — {0}", [row.item_name || row.item_code || ""]),
    size: "extra-large",
    fields: [
      ...(adds.length ? [
        { fieldname: "adds_info", fieldtype: "HTML",
          options: `<div><b style="font-size:12px;">${__("Articles ADDITIONNELS de cette ligne (en plus du pack)")}</b>${adds_html}</div>` },
        { fieldname: "s_texts", fieldtype: "Section Break" },
      ] : []),
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
      { fieldname: "s_preview", fieldtype: "Section Break",
        label: __("👁 Aperçu export (tel que dans l'Excel / PDF)") },
      { fieldname: "preview", fieldtype: "HTML" },
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

  // aperçu export : rendu par le MÊME code que l'Excel/PDF (dernière sauvegarde)
  const $p = d.fields_dict.preview.$wrapper;
  if (frm.is_new() || !row.name || row.name.startsWith("new-")) {
    $p.html(`<div class="text-muted">${__("Enregistrez le document pour voir l'aperçu export.")}</div>`);
  } else {
    $p.html(`<div class="text-muted">${__("Chargement…")}</div>`);
    frappe.call({
      method: "customization_app.liste_commande_import.preview_row_export",
      args: { docname: frm.doc.name, row_name: row.name },
    }).then((res) => {
      const p = res.message || {};
      $p.html(`
        <div style="border:1px solid var(--border-color,#d5dae1);border-radius:8px;padding:10px 12px;
                    background:var(--bg-light-gray,#fafbfc);font-size:12px;">
          <div style="font-weight:700;">${esc(p.name || "")}
            <span class="lci-gmeta" style="float:right;">${esc(p.lang || "")} · ${esc(p.qty || "")}</span></div>
          <div style="white-space:pre-wrap;margin-top:6px;">${esc(p.desc || "")}</div>
          ${(p.adds || []).map((l) => `<div style="color:#b00020;font-weight:700;">${esc(l)}</div>`).join("")}
        </div>
        ${frm.is_dirty() ? `<div class="lci-gmeta" style="margin-top:4px;">⚠️ ${__("Modifications non enregistrées : l'aperçu reflète la dernière sauvegarde (Ctrl+S puis rouvrir).")}</div>` : ""}`);
    }).catch(() => $p.html(`<div class="text-danger">${__("Aperçu indisponible.")}</div>`));
  }
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
  const strip = (h) => $("<div>").html((h || "").replace(/<br\s*\/?>/gi, " ")).text();

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
      <td class="lci-c lci-drag" title="${__("Glisser pour déplacer · double-clic : envoyer à la ligne…")}">
        <div class="lci-dragnum">⠿ ${i + 1}</div>
        <div class="lci-updown"><span data-act="up" title="${__("Monter")}">▲</span><span data-act="down" title="${__("Descendre")}">▼</span></div>
      </td>
      <td class="lci-c">
        <div class="lci-imgbox" data-act="image" title="${__("Changer l'image")}">
          ${r.image ? `<img src="${esc(r.image)}">` : `<span class="lci-noimg">📦</span>`}
        </div>
        ${(() => {
          const adds = lci_adds(r).filter((a) => a.image);
          return adds.length ? `<div class="lci-addimgs">${adds.map((a) =>
            `<img class="lci-addimg" src="${esc(a.image)}" title="${esc(a.item_name || a.item_code)} (ADDITIONNEL)">`).join("")}</div>` : "";
        })()}
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
        ${lci_adds(r).map((a, ai) => `
          <div class="lci-add">➕ <b>ADDITIONNEL</b> : ${esc(lci_add_label(a))}
            — ${format_number(flt(a.qty_par_pack), null, 0)}/pack × ${format_number(flt(r.qty), null, 0)}
            = <b>${format_number(flt(a.qty_par_pack) * flt(r.qty), null, 0)} ${esc(a.uom || "")}</b>
            <span class="lci-add-x" data-addx="${ai}" title="${__("Retirer cet additionnel")}">✕</span></div>`).join("")}
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
        ${r.item_code ? `<button class="btn btn-xs btn-default" data-act="fam" title="${__("Variantes / articles apparentés")}">🧬</button>` : ""}
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
      .lci-drag { cursor: grab; user-select: none; white-space: nowrap; }
      .lci-drag:active { cursor: grabbing; }
      .lci-dragnum { font-weight: 700; color: #8a93a0; font-size: 11px; }
      .lci-posinp { width: 46px; font-size: 11px; font-weight: 700; text-align: center;
                    border: 1px solid #91caff; border-radius: 6px; padding: 1px 2px;
                    background: var(--card-bg,#fff); outline: none; }
      .lci-updown { line-height: 1; margin-top: 2px; }
      .lci-updown span { cursor: pointer; font-size: 9px; color: #b6bec9; padding: 0 2px; }
      .lci-updown span:hover { color: #0958d9; }
      tr.sortable-ghost { opacity: .4; background: #e6f4ff; }
      .lci-add { font-size: 11px; color: #a8071a; background: #fff1f0; border: 1px solid #ffa39e;
                 border-radius: 6px; padding: 2px 6px; margin-top: 3px; max-width: 460px; }
      .lci-add-x { cursor: pointer; font-weight: 700; color: #a8071a; padding: 0 3px; float: right; }
      .lci-add-x:hover { color: #5c0011; }
      .lci-addimgs { margin-top: 3px; display: flex; gap: 2px; justify-content: center; flex-wrap: wrap; }
      .lci-addimg { width: 22px; height: 22px; object-fit: contain; border: 1px solid #ffa39e;
                    border-radius: 4px; background: #fff; }
    </style>
    <table class="lci-tbl">
      <thead><tr>
        <th style="width:46px;">#</th><th style="width:60px;">${__("Image")}</th>
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
    lci_reindex(frm);
    frm.dirty();
    lci_recalc(frm);
    frm.refresh_field("articles");
  });
  $t.find('[data-act="fam"]').on("click", (e) => { const r = row_of(e); r && lci_family_dialog(frm, r); });
  $t.find("[data-addx]").on("click", (e) => {
    e.stopPropagation();
    const row = row_of(e);
    if (!row) return;
    const list = lci_adds(row);
    const removed = list.splice(cint($(e.currentTarget).data("addx")), 1);
    lci_set_adds(frm, row, list);
    if (removed.length) frappe.show_alert({ message: __("Additionnel {0} retiré.", [removed[0].item_code]), indicator: "orange" });
  });
  $t.find('[data-act="up"]').on("click", (e) => { e.stopPropagation(); const r = row_of(e); r && lci_move(frm, r, -1); });
  $t.find('[data-act="down"]').on("click", (e) => { e.stopPropagation(); const r = row_of(e); r && lci_move(frm, r, +1); });

  // double-clic sur le n° de ligne → saisie directe de la position cible
  $t.find(".lci-drag").on("dblclick", (e) => {
    e.preventDefault();
    e.stopPropagation();
    const row = row_of(e);
    if (!row) return;
    const $cell = $(e.currentTarget);
    if ($cell.find(".lci-posinp").length) return; // déjà en édition
    const total = lci_rows(frm).length;
    const cur = lci_rows(frm).findIndex((r) => r.name === row.name) + 1;
    const $num = $cell.find(".lci-dragnum");
    const $inp = $(`<input type="number" class="lci-posinp" min="1" max="${total}"
                    value="${cur}" title="${__("Ligne cible (1–{0})", [total])}">`);
    $num.hide();
    $inp.insertAfter($num).focus().select();
    let closed = false;
    const done = (apply) => {
      if (closed) return;
      closed = true;
      const v = cint($inp.val());
      $inp.remove();
      $num.show();
      if (apply && v >= 1 && v !== cur) lci_move_to(frm, row, v - 1);
    };
    $inp.on("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); done(true); }
      else if (ev.key === "Escape") done(false);
      ev.stopPropagation();
    });
    $inp.on("blur", () => done(true));
    $inp.on("mousedown dblclick", (ev) => ev.stopPropagation());
  });

  lci_bind_sortable(frm, $t);
}

// ------------------------------------------------------------ réorganisation

function lci_move(frm, row, delta) {
  const arr = frm.doc.articles;
  const pos = arr.findIndex((r) => r.name === row.name);
  const target = pos + delta;
  if (pos < 0 || target < 0 || target >= arr.length) return;
  arr.splice(target, 0, arr.splice(pos, 1)[0]);
  lci_reindex(frm);
  frm.dirty();
  lci_render_table(frm);
}

// déplace la ligne à une position absolue (index 0-based), bornée aux limites
function lci_move_to(frm, row, target) {
  const arr = frm.doc.articles;
  const pos = arr.findIndex((r) => r.name === row.name);
  if (pos < 0) return;
  target = Math.max(0, Math.min(arr.length - 1, cint(target)));
  if (target === pos) return;
  arr.splice(target, 0, arr.splice(pos, 1)[0]);
  lci_reindex(frm);
  frm.dirty();
  lci_render_table(frm);
  frappe.show_alert({ message: __("Ligne déplacée en position {0}.", [target + 1]), indicator: "green" });
}

function lci_bind_sortable(frm, $t) {
  const tbody = $t.find("table.lci-tbl tbody").get(0);
  if (!tbody || typeof Sortable === "undefined") return;
  new Sortable(tbody, {
    handle: ".lci-drag",
    draggable: "tr[data-name]",
    filter: ".lci-ghead",
    animation: 150,
    onStart: () => $t.find("tr.lci-ghead").hide(),
    onEnd: () => {
      const order = $t.find("tbody tr[data-name]").map((_, el) => $(el).data("name")).get();
      frm.doc.articles.sort((a, b) => order.indexOf(a.name) - order.indexOf(b.name));
      lci_reindex(frm);
      frm.dirty();
      lci_render_table(frm); // re-render → en-têtes de groupe recalculés
    },
  });
}

// --------------------------------------------- variantes / apparentés (LCI)

function lci_replace_item(frm, row, it, keep_texts) {
  const sets = {
    item_code: it.item_code,
    item_group: it.item_group || "",
    uom: it.uom || row.uom || "Pièce",
    volume_unitaire_m3: flt(it.volume_unitaire_m3),
    image: it.image || "",
    item_name_traduit: "",       // traduction invalidée par le changement d'article
    description_traduite: "",
  };
  if (!keep_texts) {
    sets.item_name = it.item_name || it.item_code;
    sets.description = it.description || "";
  }
  Object.entries(sets).forEach(([f, v]) => frappe.model.set_value(row.doctype, row.name, f, v));
  frm.dirty();
  lci_recalc(frm);
}

function lci_insert_after(frm, after_row, values) {
  const row = frm.add_child("articles", values);
  const arr = frm.doc.articles;
  arr.pop(); // add_child a mis la ligne en fin — on la repositionne
  const pos = after_row ? arr.findIndex((r) => r.name === after_row.name) : arr.length - 1;
  arr.splice(pos + 1, 0, row);
  lci_reindex(frm);
  frm.dirty();
  lci_recalc(frm);
  return row;
}

async function lci_item_details(codes) {
  if (!codes.length) return {};
  const res = await frappe.call({
    method: "frappe.client.get_list",
    args: { doctype: "Item", filters: { name: ["in", codes] },
            fields: ["name", "item_name", "item_group", "image", "stock_uom",
                     "custom_volume_m3", "description", "brand"],
            limit_page_length: 0 },
  });
  const out = {};
  (res.message || []).forEach((i) => (out[i.name] = i));
  return out;
}

async function lci_family_dialog(frm, row) {
  let data;
  try {
    const r = await frappe.call({
      method: "customization_app.liste_commande_import.get_related_items",
      args: { item_code: row.item_code },
      freeze: true, freeze_message: __("Recherche des articles apparentés…"),
    });
    data = r.message || {};
  } catch (err) { console.error(err); return; }

  const esc = frappe.utils.escape_html;
  const by_code = {};
  [...(data.variants || []), ...(data.siblings || [])].forEach((it) => (by_code[it.item_code] = it));

  const item_rows = (items) => items.map((it) => `
    <tr>
      <td class="lci-c">${it.image ? `<div class="lci-imgbox" style="width:36px;height:36px;"><img src="${esc(it.image)}"></div>` : ""}</td>
      <td><a href="/app/item/${encodeURIComponent(it.item_code)}" target="_blank" class="lci-code">${esc(it.item_code)}</a>
          ${it.is_template ? `<span class="lci-libre">${__("modèle")}</span>` : ""}
          <div class="lci-fam-name">${esc(it.attributes || it.item_name || "")}${it.brand ? ` · <b>${esc(it.brand)}</b>` : ""}</div></td>
      <td class="lci-c">${format_number(flt(it.stock), null, 0)}</td>
      <td class="lci-c">${it.volume_unitaire_m3 ? format_number(it.volume_unitaire_m3, null, 4) : "—"}</td>
      <td class="lci-c" style="white-space:nowrap;">
        <button class="btn btn-xs btn-primary" data-rep="${esc(it.item_code)}">${__("Remplacer")}</button>
        <button class="btn btn-xs btn-default" data-add="${esc(it.item_code)}">${__("+ Ajouter")}</button>
      </td>
    </tr>`).join("");

  const section = (title, items) => !items.length ? "" : `
    <div class="lci-fam-sec">
      <div class="lci-fam-title">${title} <span class="lci-gmeta">· ${items.length}</span></div>
      <div class="lci-fam-scroll"><table class="lci-tbl">
        <thead><tr><th style="width:44px;"></th><th>${__("Article")}</th>
        <th style="width:70px;">${__("Stock")}</th><th style="width:80px;">${__("Vol. m³")}</th>
        <th style="width:150px;"></th></tr></thead>
        <tbody>${item_rows(items)}</tbody></table></div>
    </div>`;

  const present = () => new Set(lci_rows(frm).filter((r) => r.item_code).map((r) => r.item_code));
  const bundle_rows = (data.bundles || []).map((b, i) => {
    const p = present();
    const comps = b.components.map((c) => {
      const ok = p.has(c.item_code);
      return `<span class="lci-bcomp ${ok ? "lci-bok" : "lci-bmiss"}" title="${esc(c.item_name)}">
        ${ok ? "✅" : "⚠️"} ${esc(c.item_code)} ×${c.qty}</span>`;
    }).join(" ");
    return `
    <div class="lci-bundle">
      <div><a href="/app/item/${encodeURIComponent(b.item_code)}" target="_blank" class="lci-code">${esc(b.item_code)}</a>
        <span class="lci-fam-name">${esc(b.attributes || b.item_name || "")}</span></div>
      <div class="lci-bcomps">${comps}</div>
      <div class="lci-bact">
        <input type="number" class="lci-inp lci-num" style="width:64px;border-color:var(--border-color,#d5dae1);" data-bqty="${i}" value="1" min="1" step="1">
        <button class="btn btn-xs btn-default" data-bundle-add="${i}">${__("➕ Ajouter les composants manquants")}</button>
      </div>
    </div>`;
  }).join("");

  const compo = data.composition || null;
  const compo_rows = !compo ? "" : compo.map((c, i) => `
    <tr>
      <td class="lci-c">${c.image ? `<div class="lci-imgbox" style="width:36px;height:36px;"><img src="${esc(c.image)}"></div>` : ""}</td>
      <td><a href="/app/item/${encodeURIComponent(c.item_code)}" target="_blank" class="lci-code">${esc(c.item_code)}</a>
          <div class="lci-fam-name">${esc(c.attributes || c.item_name || "")}${c.brand ? ` · <b>${esc(c.brand)}</b>` : ""}</div></td>
      <td class="lci-c">${format_number(c.qty_per_pack, null, 0)}</td>
      <td class="lci-c">${format_number(flt(c.stock), null, 0)}</td>
      <td class="lci-c" style="white-space:nowrap;">
        <input type="number" class="lci-inp lci-num" style="width:56px;border-color:var(--border-color,#d5dae1);"
               data-cqty="${i}" value="1" min="0" step="any" title="${__("Quantité additionnelle PAR PACK")}">
        <span class="lci-gmeta">/pack</span>
        <button class="btn btn-xs btn-warning" data-compo-add="${i}">${__("➕ Additionnel")}</button>
      </td>
    </tr>`).join("");

  const d = new frappe.ui.Dialog({
    title: __("🧬 {0} — articles apparentés", [row.item_code]),
    size: "extra-large",
    fields: [
      { fieldname: "keep_texts", fieldtype: "Check", default: 0,
        label: __("Conserver la désignation et la description actuelles lors d'un remplacement") },
      { fieldname: "html", fieldtype: "HTML" },
      { fieldname: "s_add", fieldtype: "Section Break",
        label: __("➕ Ajouter un autre article du catalogue (même hors pack)") },
      { fieldname: "add_item", fieldtype: "Link", options: "Item", label: __("Article"),
        get_query: () => ({ filters: { disabled: 0 } }) },
      { fieldname: "c_add1", fieldtype: "Column Break" },
      { fieldname: "add_qty", fieldtype: "Float", default: 1,
        label: __("Quantité (par pack si ADDITIONNEL, sinon totale)") },
      { fieldname: "c_add2", fieldtype: "Column Break" },
      { fieldname: "add_mark", fieldtype: "Check", default: compo ? 1 : 0,
        label: __("ADDITIONNEL : intégré à la ligne du pack (total = qté × packs)") },
      { fieldname: "c_add3", fieldtype: "Column Break" },
      { fieldname: "add_btn", fieldtype: "Button", label: __("➕ Ajouter"),
        click: async () => {
          const code = d.get_value("add_item");
          if (!code) {
            frappe.show_alert({ message: __("Choisissez d'abord un article."), indicator: "orange" });
            return;
          }
          const q = flt(d.get_value("add_qty")) || 1;
          const mark = d.get_value("add_mark");
          const det = await lci_item_details([code]);
          const it = det[code] || {};
          if (mark) {
            lci_add_additionnel(frm, row, {
              item_code: code, item_name: it.item_name || code,
              brand: it.brand || "",
              uom: it.stock_uom || "Pièce", image: it.image || "",
              volume_unitaire_m3: flt(it.custom_volume_m3),
            }, q);
            d.hide();
            frappe.show_alert({
              message: __("ADDITIONNEL : +{0}/pack × {1} = {2} intégré à la ligne du pack.",
                [q, code, format_number(q * flt(row.qty), null, 0)]),
              indicator: "green",
            });
            return;
          }
          lci_insert_after(frm, row, {
            item_code: code, item_name: it.item_name || code,
            item_group: it.item_group || "", qty: q, uom: it.stock_uom || "Pièce",
            description: it.description || "", image: it.image || "",
            volume_unitaire_m3: flt(it.custom_volume_m3),
          });
          d.hide();
          frappe.show_alert({ message: __("{0} × {1} ajouté sous la ligne.", [q, code]), indicator: "green" });
        } },
    ],
  });
  d.fields_dict.html.$wrapper.html(`
    <style>
      .lci-fam-sec { margin-bottom: 14px; }
      .lci-fam-title { font-weight: 800; font-size: 12px; color: #0958d9; margin-bottom: 4px; }
      .lci-fam-scroll { max-height: 260px; overflow-y: auto; border: 1px solid var(--border-color,#e4e8ee); border-radius: 8px; }
      .lci-fam-name { font-size: 11px; color: var(--text-muted,#8a93a0); }
      .lci-bundle { border: 1px solid var(--border-color,#e4e8ee); border-radius: 8px; padding: 8px 10px; margin-bottom: 8px; }
      .lci-bcomps { margin: 6px 0; line-height: 2; }
      .lci-bcomp { font-size: 11px; border-radius: 6px; padding: 2px 6px; margin-right: 4px; white-space: nowrap; }
      .lci-bok { background: #f6ffed; border: 1px solid #b7eb8f; }
      .lci-bmiss { background: #fff7e6; border: 1px solid #ffd591; }
      .lci-bact { display: flex; gap: 6px; align-items: center; }
      .lci-bundles-scroll { max-height: 340px; overflow-y: auto; }
    </style>
    ${compo ? `
      <div class="lci-fam-sec">
        <div class="lci-fam-title">📦 ${__("Composition de ce pack")} <span class="lci-gmeta">· ${compo.length} ${__("composant(s)")} · ${__("qté préremplie = {0} pack(s)", [flt(row.qty) || 1])}</span></div>
        <div class="lci-fam-scroll"><table class="lci-tbl">
          <thead><tr><th style="width:44px;"></th><th>${__("Composant")}</th>
          <th style="width:70px;">${__("Qté / pack")}</th><th style="width:70px;">${__("Stock")}</th>
          <th style="width:190px;">${__("Ajouter en plus du pack")}</th></tr></thead>
          <tbody>${compo_rows}</tbody></table></div>
        <div class="lci-gmeta">${__("La ligne ajoutée est marquée « ADDITIONNEL » dans sa désignation et sa description (repris tel quel dans la cotation et la traduction).")}</div>
      </div>` : ""}
    ${section(__("Variantes du même modèle"), data.variants || [])}
    ${section(__("Frères par code ({0}-…)", [esc((row.item_code || "").split("-").slice(0, -1).join("-"))]), data.siblings || [])}
    ${(data.bundles || []).length ? `
      <div class="lci-fam-sec">
        <div class="lci-fam-title">${__("Utilisé dans (produits finis)")} <span class="lci-gmeta">· ${data.bundles.length}</span></div>
        <div class="lci-bundles-scroll">${bundle_rows}</div>
        <div class="lci-gmeta">${__("Les composants déjà présents dans la liste ne sont pas modifiés.")}</div>
      </div>` : ""}
    ${!(data.variants || []).length && !(data.siblings || []).length && !(data.bundles || []).length && !compo
      ? `<div class="text-muted">${__("Aucun article apparenté trouvé.")}</div>` : ""}
  `);

  const $w = d.fields_dict.html.$wrapper;
  $w.find("[data-rep]").on("click", (e) => {
    const it = by_code[$(e.currentTarget).data("rep")];
    if (!it) return;
    lci_replace_item(frm, row, it, d.get_value("keep_texts"));
    d.hide();
    frappe.show_alert({ message: __("Ligne remplacée par {0} (quantité conservée).", [it.item_code]), indicator: "green" });
  });
  $w.find("[data-add]").on("click", (e) => {
    const it = by_code[$(e.currentTarget).data("add")];
    if (!it) return;
    lci_insert_after(frm, row, {
      item_code: it.item_code, item_name: it.item_name || it.item_code,
      item_group: it.item_group || "", qty: 1, uom: it.uom || "Pièce",
      description: it.description || "", image: it.image || "",
      volume_unitaire_m3: flt(it.volume_unitaire_m3),
    });
    d.hide();
    frappe.show_alert({ message: __("{0} ajouté sous la ligne.", [it.item_code]), indicator: "green" });
  });
  $w.find("[data-compo-add]").on("click", (e) => {
    const i = cint($(e.currentTarget).data("compo-add"));
    const c = (compo || [])[i];
    if (!c) return;
    const q = flt($w.find(`[data-cqty="${i}"]`).val());
    if (!q) { frappe.show_alert({ message: __("Quantité vide."), indicator: "orange" }); return; }
    lci_add_additionnel(frm, row, c, q);
    d.hide();
    frappe.show_alert({
      message: __("ADDITIONNEL : +{0}/pack × {1} = {2} {3} intégré à la ligne du pack.",
        [q, c.item_code, format_number(q * flt(row.qty), null, 0), c.uom || ""]),
      indicator: "green",
    });
  });
  $w.find("[data-bundle-add]").on("click", async (e) => {
    const i = cint($(e.currentTarget).data("bundle-add"));
    const b = (data.bundles || [])[i];
    if (!b) return;
    const n = flt($w.find(`[data-bqty="${i}"]`).val()) || 1;
    const p = present();
    const missing = b.components.filter((c) => !p.has(c.item_code));
    if (!missing.length) {
      frappe.show_alert({ message: __("Tous les composants sont déjà dans la liste."), indicator: "blue" });
      return;
    }
    const det = await lci_item_details(missing.map((c) => c.item_code));
    let after = row;
    missing.forEach((c) => {
      const it = det[c.item_code] || {};
      after = lci_insert_after(frm, after, {
        item_code: c.item_code, item_name: it.item_name || c.item_name,
        item_group: it.item_group || "", qty: flt(c.qty) * n,
        uom: it.stock_uom || "Pièce", description: it.description || "",
        image: it.image || "", volume_unitaire_m3: flt(it.custom_volume_m3),
      });
    });
    d.hide();
    frappe.show_alert({ message: __("{0} composant(s) de {1} ajouté(s) ×{2}.", [missing.length, b.item_code, n]), indicator: "green" });
  });
  d.show();
}

// ------------------------------------------------- estimation produits finis

function lci_estimation_dialog(frm) {
  if (frm.is_dirty() || frm.is_new()) {
    frappe.msgprint(__("Enregistrez d'abord le document (Ctrl+S) — l'estimation lit les quantités sauvegardées."));
    return;
  }
  const esc = frappe.utils.escape_html;
  const state = { manual: [], data: null };

  const d = new frappe.ui.Dialog({
    title: __("🏭 Produits finis assemblables"),
    size: "extra-large",
    fields: [
      { fieldname: "include_stock", fieldtype: "Check", default: 1,
        label: __("Inclure le stock actuel"), onchange: () => load() },
      { fieldname: "c1", fieldtype: "Column Break" },
      { fieldname: "hide_zero", fieldtype: "Check", default: 1,
        label: __("Masquer les non assemblables (0)"), onchange: () => render() },
      { fieldname: "c2", fieldtype: "Column Break" },
      { fieldname: "add_bundle", fieldtype: "Link", options: "Item",
        label: __("Ajouter un produit fini précis"),
        get_query: () => ({ filters: { disabled: 0 } }),
        onchange: function () {
          const v = this.get_value();
          if (v && !state.manual.includes(v)) {
            state.manual.push(v);
            this.set_value("");
            load();
          }
        } },
      { fieldname: "s1", fieldtype: "Section Break" },
      { fieldname: "html", fieldtype: "HTML" },
    ],
  });

  const $w = () => d.fields_dict.html.$wrapper;

  async function load() {
    $w().html(`<div class="text-muted">${__("Calcul…")}</div>`);
    try {
      const r = await frappe.call({
        method: "customization_app.liste_commande_import.estimate_finished_products",
        args: { docname: frm.doc.name,
                include_stock: d.get_value("include_stock") ? 1 : 0,
                bundle_codes: JSON.stringify(state.manual) },
      });
      state.data = r.message || { bundles: [] };
      render();
    } catch (err) {
      console.error(err);
      $w().html(`<div class="text-danger">${__("Erreur de calcul.")}</div>`);
    }
  }

  function render() {
    const data = state.data;
    if (!data) return;
    const hide_zero = d.get_value("hide_zero");
    const bundles = (data.bundles || []).filter((b) => !hide_zero || b.buildable > 0 || b.manual);
    const with_stock = !!data.include_stock;

    const rows = bundles.map((b, i) => {
      const lim = new Set(b.limiting || []);
      const detail = b.components.map((c) => `
        <tr class="${lim.has(c.item_code) ? "lci-est-lim" : ""}">
          <td>${esc(c.item_code)} <span class="lci-fam-name">${esc(c.item_name || "")}</span></td>
          <td class="lci-c">${format_number(c.per_unit, null, 0)}</td>
          <td class="lci-c">${format_number(c.ordered, null, 0)}</td>
          ${with_stock ? `<td class="lci-c">${format_number(c.stock, null, 0)}</td>` : ""}
          <td class="lci-c"><b>${format_number(c.available, null, 0)}</b></td>
          <td class="lci-c">${c.buildable === null ? "—" : format_number(c.buildable, null, 0)}</td>
          <td class="lci-c">${flt(c.leftover) > 0 ? `<b style="color:#135200;">+${format_number(c.leftover, null, 0)}</b>` : "—"}</td>
        </tr>`).join("");
      return `
      <tr class="lci-est-row" data-est="${i}">
        <td class="lci-c" style="width:24px;">▸</td>
        <td><a href="/app/item/${encodeURIComponent(b.item_code)}" target="_blank" class="lci-code">${esc(b.item_code)}</a>
          ${b.manual ? `<span class="lci-libre">${__("manuel")}</span>` : ""}
          <div class="lci-fam-name">${esc(b.attributes || b.item_name || "")}</div></td>
        <td class="lci-c"><span class="lci-est-n ${b.buildable > 0 ? "lci-est-pos" : "lci-est-zero"}">${format_number(b.buildable, null, 0)}</span>
          ${flt(b.direct_qty) > 0 ? `<div class="lci-fam-name">${__("dont {0} pack(s) complet(s) commandé(s)", [format_number(b.direct_qty, null, 0)])}</div>` : ""}</td>
        <td>${(b.limiting || []).map((l) => `<span class="lci-bcomp lci-blim">${esc(l)}</span>`).join(" ")}</td>
      </tr>
      <tr class="lci-est-detail" data-detail="${i}" style="display:none;">
        <td></td>
        <td colspan="3"><table class="lci-tbl" style="margin:4px 0;">
          <thead><tr><th>${__("Composant")}</th><th class="lci-c">${__("Requis / unité")}</th>
          <th class="lci-c">${__("Commandé")}</th>${with_stock ? `<th class="lci-c">${__("Stock")}</th>` : ""}
          <th class="lci-c">${__("Disponible")}</th><th class="lci-c">${__("→ Assemblables")}</th>
          <th class="lci-c">${__("Reste (rechange)")}</th></tr></thead>
          <tbody>${detail}</tbody></table></td>
      </tr>`;
    }).join("");

    $w().html(`
      <style>
        .lci-est-row { cursor: pointer; }
        .lci-est-row:hover td { background: var(--bg-light-gray,#f6f8fa); }
        .lci-est-n { font-weight: 800; font-size: 15px; border-radius: 8px; padding: 2px 10px; }
        .lci-est-pos { background: #f6ffed; color: #135200; border: 1px solid #b7eb8f; }
        .lci-est-zero { background: #fff1f0; color: #a8071a; border: 1px solid #ffa39e; }
        .lci-blim { background: #fff1f0; border: 1px solid #ffa39e; }
        .lci-est-lim td { background: #fff1f0; }
        .lci-est-wrap { max-height: 420px; overflow-y: auto; }
      </style>
      <div class="lci-est-wrap"><table class="lci-tbl">
        <thead><tr><th style="width:24px;"></th><th>${__("Produit fini")}</th>
        <th class="lci-c" style="width:120px;">${__("Assemblables")}</th>
        <th>${__("Composant(s) limitant(s)")}</th></tr></thead>
        <tbody>${rows || `<tr><td colspan="4" class="text-muted">${__("Aucun produit fini assemblable avec ces articles.")}</td></tr>`}</tbody>
      </table></div>
      <div class="lci-gmeta" style="margin-top:8px;">
        ${esc(data.note || "")} · ${__("Les lignes qui sont elles-mêmes des packs sont décomposées en composants dans « Commandé ».")}
        ${data.total > data.shown ? " · " + __("{0} produits candidats, {1} affichés (les mieux couverts).", [data.total, data.shown]) : ""}
        ${hide_zero ? " · " + __("{0} produit(s) à zéro masqué(s).", [(data.bundles || []).length - bundles.length]) : ""}
      </div>`);

    $w().find(".lci-est-row").on("click", (e) => {
      const i = $(e.currentTarget).data("est");
      const $det = $w().find(`[data-detail="${i}"]`);
      $det.toggle();
      $(e.currentTarget).find("td:first").text($det.is(":visible") ? "▾" : "▸");
    });
  }

  d.show();
  load();
}
