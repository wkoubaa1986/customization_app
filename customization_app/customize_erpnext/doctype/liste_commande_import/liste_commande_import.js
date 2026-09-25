// Menus de la barre d'outils : la liste comptait neuf boutons côte à côte,
// regroupés ici par intention (ajouter, organiser, produire la cotation…).
const LCI_GRP_AJOUT = "➕ Ajouter";
const LCI_GRP_ORGANISER = "🗂 Organiser";
const LCI_GRP_COTATION = "📄 Cotation";

// Colonnes facultatives de la table : la liste en compte trop pour tenir sur un
// écran, chacun affiche celles qui servent à son étape (préparation, puis
// négociation). Le choix est gardé dans le navigateur, par utilisateur.
const LCI_COLONNES = [
  { cle: "image", label: "Image" },
  { cle: "uom", label: "UDM" },
  { cle: "vol_unit", label: "Vol. unitaire (m³)" },
  { cle: "vol_ligne", label: "Vol. ligne (m³)" },
  { cle: "prix_cible", label: "Prix cible" },
  { cle: "prix_frn", label: "Prix fournisseur" },
  { cle: "qty_frn", label: "Qté fournisseur" },
  { cle: "qty_cible", label: "Qté cible (retenue)" },
  { cle: "prix_neg", label: "Prix cible négocié" },
  { cle: "decision", label: "Décision" },
  { cle: "remarque", label: "Remarque du fournisseur" },
  { cle: "observation", label: "Observation (envoyée)" },
  { cle: "conteneur", label: "Conteneur" },
  { cle: "desc", label: "Description (icônes)" },
];
const LCI_COLS_KEY = "lci_colonnes_visibles";

function lci_cols() {
  let v = {};
  try {
    v = JSON.parse(window.localStorage.getItem(LCI_COLS_KEY) || "{}") || {};
  } catch (e) {
    v = {};   // navigation privée ou stockage bloqué : tout reste visible
  }
  const out = {};
  LCI_COLONNES.forEach((c) => (out[c.cle] = v[c.cle] !== false));
  return out;
}

function lci_cols_dialog(frm) {
  const v = lci_cols();
  const d = new frappe.ui.Dialog({
    title: __("Colonnes affichées"),
    fields: LCI_COLONNES.map((c, i) => [
      ...(i === Math.ceil(LCI_COLONNES.length / 2)
        ? [{ fieldtype: "Column Break" }] : []),
      { fieldtype: "Check", fieldname: c.cle, label: __(c.label), default: v[c.cle] ? 1 : 0 },
    ]).flat(),
    primary_action_label: __("Appliquer"),
    primary_action: (val) => {
      const garde = {};
      LCI_COLONNES.forEach((c) => (garde[c.cle] = !!val[c.cle]));
      try {
        window.localStorage.setItem(LCI_COLS_KEY, JSON.stringify(garde));
      } catch (e) { /* stockage indisponible : l'affichage vaut pour cette session */ }
      d.hide();
      lci_render_table(frm);
    },
    secondary_action_label: __("Tout afficher"),
    secondary_action: () => {
      try {
        window.localStorage.removeItem(LCI_COLS_KEY);
      } catch (e) { /* rien à nettoyer */ }
      d.hide();
      lci_render_table(frm);
    },
  });
  d.show();
}

frappe.ui.form.on("Liste Commande Import", {
  refresh(frm) {
    // le grid natif est remplacé par la table custom (données inchangées)
    frm.get_field("articles").$wrapper.hide();
    lci_render_table(frm);
    lci_render_conteneurs(frm);

    frm.add_custom_button(__("Depuis le catalogue"),
      () => lci_add_catalogue(frm), __(LCI_GRP_AJOUT));
    frm.add_custom_button(__("Article libre (hors catalogue)"),
      () => lci_add_libre(frm), __(LCI_GRP_AJOUT));
    frm.add_custom_button(__("Trier par groupe puis code"),
      () => lci_organize(frm), __(LCI_GRP_ORGANISER));
    lci_doublons_bouton(frm);   // « Enlever les doublons », dans le même menu
    frm.add_custom_button(__("Colonnes affichées…"),
      () => lci_cols_dialog(frm), __(LCI_GRP_ORGANISER));

    // la table est plus large que le gabarit centré du formulaire : on rend la
    // page pleine largeur (même mécanique que la vue Kanban).
    if (frm.page && frm.page.container) frm.page.container.addClass("full-width");

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

    frm.add_custom_button(__("PDF à envoyer"), () => {
      window.open(`/api/method/customization_app.liste_commande_import.download_pdf?docname=${encodeURIComponent(frm.doc.name)}`);
    }, __(LCI_GRP_COTATION));
    frm.add_custom_button(__("Excel à envoyer"), () => {
      window.open(`/api/method/customization_app.liste_commande_import.download_excel?docname=${encodeURIComponent(frm.doc.name)}`);
    }, __(LCI_GRP_COTATION));

    if (frm.doc.fichier_fournisseur) {
      frm.add_custom_button(__("📤 Renvoyer SON fichier annoté"),
        () => lci_export_annote(frm), __(LCI_GRP_COTATION));
    }

    frm.add_custom_button(__("🏭 Produits assemblables"), () => lci_estimation_dialog(frm));
    frm.add_custom_button(__("🚢 Répartir en conteneurs"), () => lci_conteneurs_dialog(frm));

    frm.add_custom_button(__("📩 Importer la réponse du fournisseur"),
      () => lci_reponse_import(frm), __("💰 Prix"));
    frm.add_custom_button(__("🎯 Proposer des prix cibles"),
      () => lci_prix_cibles(frm), __("💰 Prix"));
    frm.add_custom_button(__("💬 Rédiger les observations"),
      () => lci_observations_ia(frm), __("IA"));
    frm.add_custom_button(__("📐 Estimer les volumes manquants"),
      () => lci_volumes_dialog(frm), __("IA"));

    if (frm.doc.volume_total_m3) {
      frm.dashboard.add_indicator(
        __("Volume total : {0} m³", [format_number(frm.doc.volume_total_m3, null, 3)]),
        frm.doc.volume_total_m3 > 60 ? "red" : "blue"
      );
    }
    if (flt(frm.doc.montant_propose)) {
      frm.dashboard.add_indicator(
        __("Proposé : {0}", [format_currency(frm.doc.montant_propose, frm.doc.devise)]), "blue");
    }
    if (flt(frm.doc.montant_retenu)
        && Math.abs(flt(frm.doc.montant_retenu) - flt(frm.doc.montant_propose)) > 0.01) {
      frm.dashboard.add_indicator(
        __("Retenu : {0}", [format_currency(frm.doc.montant_retenu, frm.doc.devise)]), "green");
    }
    if (cint(frm.doc.nb_conteneurs)) {
      frm.dashboard.add_indicator(
        __("{0} conteneur(s)", [cint(frm.doc.nb_conteneurs)]), "purple");
    }
    if (flt(frm.doc.montant_cible) && flt(frm.doc.montant_propose)) {
      const e = flt(frm.doc.ecart_global_pct);
      frm.dashboard.add_indicator(
        __("Écart / cible : {0} %", [format_number(e, null, 1)]),
        e > 10 ? "red" : e > 0 ? "orange" : "green");
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
  // miroir du controller serveur : volumes, montants et écarts, pour que la
  // saisie inline se voie immédiatement sans aller-retour.
  let total = 0, cible = 0, propose = 0, cotees = 0, base_cmp = 0, cote_cmp = 0;
  let fichier = 0, retenu = 0, divergentes = 0;
  lci_rows(frm).forEach((r) => {
    if (flt(r.qty_par_carton) > 0 && flt(r.volume_carton_m3) > 0) {
      r.volume_unitaire_m3 = flt(r.volume_carton_m3) / flt(r.qty_par_carton);
    }
    r.total_fournisseur = flt(r.qty) * flt(r.prix_fournisseur);
    r.ecart_pct = (flt(r.prix_cible) > 0 && flt(r.prix_fournisseur) > 0)
      ? (flt(r.prix_fournisseur) - flt(r.prix_cible)) / flt(r.prix_cible) * 100 : 0;
    cible += flt(r.qty) * flt(r.prix_cible);
    propose += flt(r.total_fournisseur);
    if (flt(r.prix_fournisseur) > 0) cotees += 1;
    if (flt(r.prix_cible) > 0 && flt(r.prix_fournisseur) > 0) {
      base_cmp += flt(r.qty) * flt(r.prix_cible);
      cote_cmp += flt(r.total_fournisseur);
    }

    if (lci_qty_divergente(r)) divergentes += 1;
    fichier += lci_total_fichier(r);

    // la quantité retenue est celle qui part réellement : elle compte le
    // volume et remplit les conteneurs.
    const q_ret = lci_qty_ret(r);
    r.total_retenu = (r.decision === "Abandonné") ? 0 : q_ret * lci_prix_ret(r);
    retenu += flt(r.total_retenu);

    let vol = q_ret * flt(r.volume_unitaire_m3);
    lci_adds(r).forEach((a) => {  // volume des additionnels embarqués
      vol += q_ret * flt(a.qty_par_pack) * flt(a.volume_unitaire_m3);
    });
    r.volume_ligne_m3 = vol;
    total += vol;
  });
  frm.doc.volume_total_m3 = total;
  frm.doc.nb_articles = lci_rows(frm).length;
  frm.doc.montant_cible = cible;
  frm.doc.montant_propose = propose;
  frm.doc.montant_retenu = retenu;
  frm.doc.nb_lignes_cotees = cotees;
  frm.doc.nb_lignes_divergentes = divergentes;
  if (fichier) frm.doc.montant_fichier = fichier;
  frm.doc.ecart_global_pct = base_cmp ? (cote_cmp - base_cmp) / base_cmp * 100 : 0;
  if (render) lci_render_table(frm);
}

// écart d'une ligne -> couleur : au-dessus de la cible c'est rouge, en dessous
// c'est gagné. Entre les deux (0 à +10 %) la négociation reste ouverte.
function lci_ecart_couleur(e) {
  return e > 10 ? "#a8071a" : e > 0 ? "#ad6800" : "#135200";
}

// valeurs retenues — miroir exact de lci_observation.py côté serveur
function lci_qty_ret(r) { return flt(r.qty_cible) || flt(r.qty); }
function lci_prix_ret(r) { return flt(r.prix_cible_negocie) || flt(r.prix_fournisseur); }

// montant de la ligne TEL QUE LE FOURNISSEUR L'ÉCRIT : sa colonne « Amount »
// quand il en a une, sinon SA quantité × son prix. Jamais la nôtre.
function lci_total_fichier(r) {
  if (flt(r.total_fichier) > 0) return flt(r.total_fichier);
  if (flt(r.prix_fournisseur) <= 0) return 0;
  return (flt(r.qty_fournisseur) || flt(r.qty)) * flt(r.prix_fournisseur);
}

function lci_qty_divergente(r) {
  return flt(r.qty_fournisseur) > 0 && flt(r.qty) > 0
    && Math.abs(flt(r.qty_fournisseur) - flt(r.qty)) > 0.001;
}

function lci_cont_parts(r) {
  try { return JSON.parse(r.repartition_conteneurs || "[]") || []; } catch (e) { return []; }
}

function lci_cont_label(r) {
  const parts = lci_cont_parts(r);
  if (!parts.length) return "";
  if (parts.length === 1) return `C${parts[0].no}`;
  return parts.map((p) => `C${p.no} (${format_number(flt(p.qty), null, 0)})`).join(" + ");
}

// ------------------------------------------------------------------ filtre
// Purement visuel : il ne touche JAMAIS frm.doc.articles. Une ligne masquée
// reste dans le document, sinon un filtre supprimerait des lignes à
// l'enregistrement. Le glisser-déposer est neutralisé tant qu'il est actif :
// réordonner une liste partielle déplacerait les lignes cachées.
function lci_filtre(frm) {
  if (!frm.__lci_filtre) frm.__lci_filtre = { decision: "", etat: "", texte: "" };
  return frm.__lci_filtre;
}

function lci_filtre_actif(frm) {
  const f = lci_filtre(frm);
  return !!(f.decision || f.etat || (f.texte || "").trim());
}

function lci_rows_visibles(frm) {
  const f = lci_filtre(frm);
  const q = (f.texte || "").trim().toLowerCase();
  return lci_rows(frm).filter((r) => {
    if (f.decision === "__vide") { if (r.decision) return false; }
    else if (f.decision && (r.decision || "") !== f.decision) return false;
    if (f.etat === "cotee" && !(flt(r.prix_fournisseur) > 0)) return false;
    if (f.etat === "non_cotee" && flt(r.prix_fournisseur) > 0) return false;
    if (f.etat === "qty_ko" && !lci_qty_divergente(r)) return false;
    if (f.etat === "au_dessus"
        && !(flt(r.prix_cible) > 0 && flt(r.prix_fournisseur) > flt(r.prix_cible))) return false;
    if (f.etat === "sans_conteneur" && lci_cont_parts(r).length) return false;
    if (q) {
      const txt = `${r.item_code || ""} ${r.item_name || ""} ${r.item_name_traduit || ""}`.toLowerCase();
      if (!txt.includes(q)) return false;
    }
    return true;
  });
}

function lci_frn_meta(frm, r) {
  const dev = frm.doc.devise || "USD";
  if (!flt(r.prix_fournisseur)) return "";
  const tot = `<span class="lci-frn-tot">${format_currency(flt(r.total_fournisseur), dev)}</span>`;
  if (!flt(r.prix_cible)) return `<div class="lci-frn-meta">${tot}</div>`;
  const e = flt(r.ecart_pct);
  const badge = `<span class="lci-ecart" style="color:${lci_ecart_couleur(e)};">`
    + `${e > 0 ? "+" : ""}${format_number(e, null, 1)} %</span>`;
  return `<div class="lci-frn-meta">${badge} · ${tot}</div>`;
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

// --- Doublons : même article sur plusieurs lignes → une seule ligne, quantités ADDITIONNÉES ---
// Bouton NU dans la barre (pas de groupe : un bouton dans un menu déroulant est un bouton
// introuvable), visible sur tout brouillon y compris jamais enregistré. La règle de fusion est
// au serveur (customization_app.lci_doublons) ; ici on ne fait que compter les codes répétés
// pour colorer le bouton et poser un bandeau — indice d'affichage, pas règle métier.
const LCI_DOUBLONS = "🧹 Enlever les doublons";

// identité d'une ligne : code article, sinon désignation normalisée (miroir de lci_doublons._identite)
function lci_identite(r) {
  const code = (r.item_code || "").trim();
  if (code) return code;
  const nom = (r.item_name || "").trim().split(/\s+/).join(" ").toLowerCase();
  return nom ? `libre:${nom}` : null;
}

function lci_nb_repetes(frm) {
  const vus = new Map();   // Map : un code « constructor » ou « __proto__ » ne heurte aucun prototype
  lci_rows(frm).forEach((r) => {
    const k = lci_identite(r);
    if (k !== null) vus.set(k, (vus.get(k) || 0) + 1);
  });
  let n = 0;
  vus.forEach((c) => { if (c > 1) n += 1; });
  return n;
}

function lci_doublons_bouton(frm) {
  if (frm.doc.docstatus !== 0) return;
  const $b = frm.add_custom_button(__(LCI_DOUBLONS), () => lci_enlever_doublons(frm),
                                   __(LCI_GRP_ORGANISER));
  if ($b) {
    $b.attr("title", __("Regroupe les lignes du même article en une seule : les quantités sont ADDITIONNÉES sur la première ligne. Rien n'est écrit avant que vous enregistriez."));
  }
  lci_doublons_etat(frm);
}

// couleur du bouton + bandeau orange, recalculés à chaque rendu de la table
function lci_doublons_etat(frm) {
  const n = lci_nb_repetes(frm);
  const $b = frm.custom_buttons && frm.custom_buttons[__(LCI_DOUBLONS)];
  if ($b) $b.toggleClass("btn-warning", n > 0);
  // le bouton vit désormais dans un menu fermé la plupart du temps : c'est le
  // bouton du GROUPE qui doit porter l'alerte, sinon le signal reste caché.
  const $grp = frm.page && frm.page.get_inner_group_button
    ? frm.page.get_inner_group_button(__(LCI_GRP_ORGANISER)) : null;
  if ($grp && $grp.length) $grp.find("button").toggleClass("btn-warning", n > 0);

  // show_message EMPILE les blocs et clear_headline viderait aussi ceux de Frappe :
  // on ne retire que le nôtre.
  const $box = frm.layout && frm.layout.message;
  if (!$box) return;
  $box.find(".lci-doublons").closest(".form-message").remove();
  if ($box.children().length === 0) $box.addClass("hidden");
  if (n > 0 && frm.doc.docstatus === 0) {
    const html = `<span class="lci-doublons">${__("{0} article(s) apparaissent sur plusieurs lignes —", [n])}
      <a class="lci-doublons-go" href="#" title="${__("Les quantités sont additionnées sur la première ligne ; rien n'est écrit avant l'enregistrement.")}">${__("enlever les doublons")}</a></span>`;
    frm.layout.show_message(html, "orange", true);
    $box.find(".lci-doublons-go").on("click", (e) => { e.preventDefault(); lci_enlever_doublons(frm); });
  }
}

async function lci_enlever_doublons(frm) {
  const rows = lci_rows(frm).map((r) => ({
    name: r.name, item_code: r.item_code, item_name: r.item_name, uom: r.uom, qty: r.qty,
    articles_additionnels: r.articles_additionnels,
  }));
  if (!rows.length) {
    frappe.msgprint(__("Aucune ligne dans la liste."));
    return;
  }
  const r = await frappe.call({
    method: "customization_app.liste_commande_import.fusionner_lignes",
    args: { articles: JSON.stringify(rows) },
    freeze: true,
    freeze_message: __("Recherche des doublons…"),
  });
  const res = r.message || {};
  const non = res.non_fusionnees || [];
  const non_txt = non.length
    ? "<br><br>" + __("Non fusionnées (à trancher à la main) :") + "<ul>" + non.map((x) =>
        `<li><b>${frappe.utils.escape_html(x.article)}</b> — ${x.lignes} ${__("lignes")}, ${__("motif")} : ${frappe.utils.escape_html(x.motif)}</li>`).join("") + "</ul>"
    : "";

  if (!res.doublons) {
    frappe.msgprint({ title: __("Aucune ligne en double"), indicator: non.length ? "orange" : "blue",
      message: __("Chaque article n'apparaît qu'une fois.") + non_txt });
    return;
  }

  // appliquer À L'ÉCRAN seulement : quantités sommées sur la première ligne, lignes en trop retirées
  const a_supprimer = new Set(res.supprimer || []);
  (res.conserver || []).forEach((c) => {
    if (c.fusionnees > 1) {
      const row = lci_rows(frm).find((x) => x.name === c.name);
      if (row) frappe.model.set_value(row.doctype, row.name, "qty", c.qty);
    }
  });
  frm.doc.articles = lci_rows(frm).filter((x) => !a_supprimer.has(x.name));
  lci_reindex(frm);
  frm.dirty();
  lci_recalc(frm);
  frm.refresh_field("articles");
  frappe.msgprint({ title: __("Doublons enlevés"), indicator: "green",
    message: __("{0} ligne(s) en double retirée(s), quantités additionnées sur la première ligne. Enregistrez pour figer (un rechargement annule).", [res.doublons]) + non_txt });
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
          — ${format_number(flt(a.qty_par_pack), null, 0)}/pack × ${format_number(lci_qty_ret(row), null, 0)}
          = <b>${format_number(flt(a.qty_par_pack) * lci_qty_ret(row), null, 0)} ${esc(a.uom || "")}</b>
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

// Rapprochement des montants : quatre chiffres qui ne veulent pas dire la même
// chose. Tant qu'ils cohabitent sans être nommés, « la somme ne correspond pas
// à l'Excel » revient à chaque cotation.
function lci_rapprochement(frm) {
  const dev = frm.doc.devise || "USD";
  const fichier = flt(frm.doc.montant_fichier);
  if (!fichier) return "";
  const propose = flt(frm.doc.montant_propose);
  const retenu = flt(frm.doc.montant_retenu);
  const div = cint(frm.doc.nb_lignes_divergentes);
  const ecart = propose - fichier;
  const colle = Math.abs(ecart) < Math.max(0.01, fichier * 0.001);
  const sep = `<span class="lci-rapp-sep">·</span>`;
  return `<div class="lci-rapp${colle ? " ok" : ""}">
    ${colle ? "✅" : "📊"} ${__("Son fichier")} : <b>${format_currency(fichier, dev)}</b>${sep}
    ${__("à nos quantités")} : <b>${format_currency(propose, dev)}</b>${sep}
    ${__("retenu")} : <b>${format_currency(retenu, dev)}</b>
    ${colle ? "" : `<br>${__("Écart de {0} — {1} ligne(s) cotée(s) sur une autre quantité que la nôtre.",
      [`<b>${format_currency(Math.abs(ecart), dev)}</b>`, `<b>${div}</b>`])}
      ${div ? `<a href="#" class="lci-fl-voir">${__("voir ces lignes")}</a>` : ""}`}
  </div>`;
}

function lci_barre_filtre(frm, rows, vis) {
  const F = lci_filtre(frm);
  const opt = (v, lbl, sel) => `<option value="${v}" ${sel === v ? "selected" : ""}>${lbl}</option>`;
  const actif = lci_filtre_actif(frm);
  return `<div class="lci-filtres">
    <span>🔎</span>
    <select data-fl="decision">
      ${opt("", __("Toutes les décisions"), F.decision)}
      ${opt("__vide", __("— sans décision —"), F.decision)}
      ${["À négocier", "Accepté", "Abandonné"].map((o) => opt(o, __(o), F.decision)).join("")}
    </select>
    <select data-fl="etat">
      ${opt("", __("Toutes les lignes"), F.etat)}
      ${opt("cotee", __("Cotées"), F.etat)}
      ${opt("non_cotee", __("Non cotées"), F.etat)}
      ${opt("qty_ko", __("Quantité divergente"), F.etat)}
      ${opt("au_dessus", __("Au-dessus de la cible"), F.etat)}
      ${opt("sans_conteneur", __("Sans conteneur"), F.etat)}
    </select>
    <input type="search" data-fl="texte" value="${frappe.utils.escape_html(F.texte || "")}"
           placeholder="${__("code ou désignation…")}" style="width:180px;">
    ${actif ? `<button class="btn btn-xs btn-default" data-fl="reset">✕ ${__("Filtre")}</button>` : ""}
    <span class="lci-fl-count${actif ? " lci-fl-on" : ""}">
      ${actif ? __("{0} ligne(s) sur {1}", [vis.length, rows.length])
              : __("{0} ligne(s)", [rows.length])}</span>
    ${actif ? `<span class="lci-fl-count">${__("· réorganisation désactivée sous filtre")}</span>` : ""}
    <span class="lci-sel-bar-slot"></span>
  </div>`;
}

// ─── sélection de lignes : dupliquer / copier vers une autre liste (demande utilisateur 25/09/2026)
function lci_selection(frm) {
  if (!frm._lci_sel) frm._lci_sel = new Set();
  const noms = new Set(lci_rows(frm).map((r) => r.name));
  frm._lci_sel.forEach((n) => { if (!noms.has(n)) frm._lci_sel.delete(n); });   // lignes disparues
  return frm._lci_sel;
}

function lci_sel_barre(frm) {
  // la table vit dans .lci-table-wrap, inséré AVANT le champ HTML (voir lci_render_table)
  const $t = $(frm.wrapper).find(".lci-table-wrap"), sel = lci_selection(frm), n = sel.size;
  const $slot = $t.find(".lci-sel-bar-slot");
  if (!n) { $slot.html(""); return; }
  $slot.html(`<span class="lci-sel-bar"><b>${__("{0} cochée(s)", [n])}</b>
    <button class="btn btn-xs btn-default" data-sel="dup" title="${__("Une copie de chaque ligne cochée, juste en dessous")}">⧉ ${__("Dupliquer")}</button>
    <button class="btn btn-xs btn-default" data-sel="copier" title="${__("Copier ces lignes vers une autre liste (brouillon existant ou nouvelle)")}">📋 ${__("Copier vers une liste…")}</button>
    <button class="btn btn-xs btn-default" data-sel="aucune" title="${__("Tout décocher")}">✕</button></span>`);
  $slot.find('[data-sel="dup"]').on("click", () => lci_dupliquer(frm, lci_rows(frm).filter((r) => sel.has(r.name))));
  $slot.find('[data-sel="copier"]').on("click", () => lci_copier_vers(frm, lci_rows(frm).filter((r) => sel.has(r.name))));
  $slot.find('[data-sel="aucune"]').on("click", () => { sel.clear(); lci_render_table(frm); });
}

// miroir de lci_copie.CHAMPS_COPIES : ce que nous avons saisi, jamais la réponse du fournisseur
const LCI_CHAMPS_COPIES = ["item_code", "item_name", "item_group", "qty", "uom", "volume_unitaire_m3", "image", "description",
  "item_name_traduit", "description_traduite", "articles_additionnels", "prix_cible", "moq", "qty_par_carton", "volume_carton_m3", "volume_estime"];

function lci_copie_de(row) {
  const c = {};
  LCI_CHAMPS_COPIES.forEach((k) => { if (row[k] !== undefined && row[k] !== null && row[k] !== "") c[k] = row[k]; });
  return c;
}

// Duplique les lignes données, chaque copie juste sous son original ; à enregistrer ensuite.
function lci_dupliquer(frm, rows) {
  if (!rows.length) return;
  const noms = new Set(rows.map((r) => r.name));
  const arr = frm.doc.articles, nouveau = [];
  arr.forEach((r) => {
    nouveau.push(r);
    if (noms.has(r.name)) {
      const copie = frm.add_child("articles", lci_copie_de(r));
      nouveau.push(copie);
    }
  });
  // add_child a ajouté les copies en fin de tableau : on remet l'ordre voulu
  frm.doc.articles = nouveau;
  lci_selection(frm).clear();
  lci_reindex(frm);
  frm.dirty();
  lci_recalc(frm);
  frm.refresh_field("articles");
  frappe.show_alert({ message: __("{0} ligne(s) dupliquée(s). Enregistrez pour figer.", [rows.length]), indicator: "green" });
}

// Copie les lignes vers une autre liste (brouillon existant ou nouvelle) — côté serveur, sur les
// lignes enregistrées : la liste doit être sauvegardée d'abord.
function lci_copier_vers(frm, rows) {
  if (!rows.length) return;
  if (frm.is_dirty() || frm.is_new()) {
    frappe.msgprint(__("Enregistrez d'abord la liste : la copie part des lignes enregistrées."));
    return;
  }
  const d = new frappe.ui.Dialog({
    title: __("Copier {0} ligne(s) vers une liste", [rows.length]),
    fields: [
      { fieldtype: "Link", fieldname: "target", label: __("Liste existante (brouillon)"), options: "Liste Commande Import",
        get_query: () => ({ filters: { statut: "Brouillon", name: ["!=", frm.doc.name] } }),
        description: __("Laissez vide pour créer une nouvelle liste.") },
      { fieldtype: "Data", fieldname: "titre", label: __("Titre de la nouvelle liste"), default: __("Copie de {0}", [frm.doc.titre || frm.doc.name]) },
      { fieldtype: "HTML", options: `<p class="text-muted small">${__("Sont copiés : article, désignation, quantité, unité, volume, image, descriptions et traductions, additionnels, prix cible, MOQ, carton. Pas la réponse du fournisseur (prix, quantités cotées, décisions, remarques, conteneurs).")}</p>` },
    ],
    primary_action_label: __("Copier"),
    primary_action: async (v) => {
      d.hide();
      let r;
      try {
        r = await frappe.call({ method: "customization_app.liste_commande_import.copier_lignes",
          args: { source: frm.doc.name, row_names: rows.map((x) => x.name), target: v.target || null, titre: v.titre || null },
          freeze: true, freeze_message: __("Copie des lignes…") });
      } catch (e) { return; }
      const res = r.message;
      lci_selection(frm).clear(); lci_render_table(frm);
      frappe.msgprint({ title: __("Lignes copiées"), indicator: "green",
        message: `${__("{0} ligne(s) copiée(s) vers", [res.nb])} <a href="/app/liste-commande-import/${encodeURIComponent(res.name)}">${frappe.utils.escape_html(res.titre || res.name)}</a>` +
          `<div style="margin-top:8px"><button class="btn btn-xs btn-primary" onclick="frappe.set_route('Form','Liste Commande Import','${frappe.utils.escape_html(res.name)}')">${__("Ouvrir la liste")}</button></div>` });
    },
  });
  d.show();
}

function lci_render_table(frm) {
  const field = frm.get_field("articles");
  if (!field || !field.$wrapper) return;
  let $t = field.$wrapper.parent().find(".lci-table-wrap");
  if (!$t.length) {
    $t = $('<div class="lci-table-wrap"></div>').insertBefore(field.$wrapper);
  }
  const rows = lci_rows(frm);
  const vis = lci_rows_visibles(frm);      // filtre : affichage seulement
  const filtre_on = lci_filtre_actif(frm);
  const esc = frappe.utils.escape_html;
  const paths = frm.__lci_paths || {};
  const strip = (h) => $("<div>").html((h || "").replace(/<br\s*\/?>/gi, " ")).text();
  const V = lci_cols();          // colonnes retenues par l'utilisateur
  const dev = frm.doc.devise || "USD";
  // #, désignation, qté et actions sont toujours là ; le reste est facultatif
  const nb_cols = 4 + LCI_COLONNES.filter((c) => V[c.cle]).length;

  let body = "";
  let prev_group = null;
  vis.forEach((r) => {
    const i = rows.indexOf(r);   // la position affichée reste la vraie position
    // en-tête de groupe (chemin hiérarchique complet) quand le groupe change
    const g = r.item_group || "";
    if (g !== prev_group) {
      const label = g ? (paths[g] || g) : __("Articles libres / sans groupe");
      const members = vis.filter((x) => (x.item_group || "") === g);
      const vol = members.reduce((s, x) => s + flt(x.volume_ligne_m3), 0);
      const mnt = members.reduce((s2, x) => s2 + flt(x.total_fournisseur), 0);
      body += `<tr class="lci-ghead"><td colspan="${nb_cols}">📁 ${esc(label)}
        <span class="lci-gmeta">· ${members.length} article(s) · ${format_number(vol, null, 3)} m³${
          mnt ? " · " + format_currency(mnt, frm.doc.devise || "USD") : ""}</span></td></tr>`;
      prev_group = g;
    }
    const tr_name = (r.item_name_traduit || "").trim();
    const tr_desc = strip(r.description_traduite);
    const tr_txt = [tr_name, tr_desc].filter(Boolean).join(" — ");
    const dec_cls = r.decision === "Accepté" ? " lci-dec-ok"
      : r.decision === "Abandonné" ? " lci-dec-ko" : "";
    const cells = [];
    cells.push(`<td class="lci-c lci-drag" title="${__("Glisser pour déplacer · double-clic : envoyer à la ligne…")}">
        <div class="lci-dragnum">⠿ ${i + 1}</div>
        <div class="lci-updown"><span data-act="up" title="${__("Monter")}">▲</span><span data-act="down" title="${__("Descendre")}">▼</span></div>
      </td>`);

    if (V.image) {
      const adds_img = lci_adds(r).filter((a) => a.image);
      cells.push(`<td class="lci-c">
        <div class="lci-imgbox" data-act="image" title="${__("Changer l'image")}">
          ${r.image ? `<img src="${esc(r.image)}">` : `<span class="lci-noimg">📦</span>`}
        </div>
        ${adds_img.length ? `<div class="lci-addimgs">${adds_img.map((a) =>
          `<img class="lci-addimg" src="${esc(a.image)}" title="${esc(a.item_name || a.item_code)} (ADDITIONNEL)">`).join("")}</div>` : ""}
      </td>`);
    }

    const d0 = strip(r.description);
    cells.push(`<td>
      ${r.item_code
        ? `<a href="/app/item/${encodeURIComponent(r.item_code)}" target="_blank" class="lci-code">${esc(r.item_code)}</a>`
        : `<span class="lci-libre">${__("libre")}</span>`}
      <input class="lci-inp lci-name" data-f="item_name" value="${esc(r.item_name || "")}"
             placeholder="${__("Désignation…")}">
      ${d0 ? `<div class="lci-desc0" data-act="desc" title="${esc(d0)}">${esc(d0.slice(0, 100))}${d0.length > 100 ? "…" : ""}</div>` : ""}
      ${tr_txt ? `<div class="lci-tr" data-act="desc" title="${esc(tr_txt)}">🌐 ${esc(tr_txt.slice(0, 90))}${tr_txt.length > 90 ? "…" : ""}</div>` : ""}
      ${lci_adds(r).map((a, ai) => `
        <div class="lci-add">➕ <b>ADDITIONNEL</b> : ${esc(lci_add_label(a))}
          — ${format_number(flt(a.qty_par_pack), null, 0)}/pack × ${format_number(lci_qty_ret(r), null, 0)}
          = <b>${format_number(flt(a.qty_par_pack) * lci_qty_ret(r), null, 0)} ${esc(a.uom || "")}</b>
          <span class="lci-add-x" data-addx="${ai}" title="${__("Retirer cet additionnel")}">✕</span></div>`).join("")}
    </td>`);

    cells.push(`<td><input class="lci-inp lci-num" data-f="qty" type="number" step="any" value="${r.qty ?? ""}"></td>`);
    if (V.uom) cells.push(`<td><input class="lci-inp lci-uom" data-f="uom" value="${esc(r.uom || "")}"></td>`);
    if (V.vol_unit) {
      cells.push(`<td><input class="lci-inp lci-num${r.volume_estime ? " lci-vol-est" : ""}"
                 data-f="volume_unitaire_m3" type="number" step="any"
                 value="${r.volume_unitaire_m3 ?? ""}"
                 title="${r.volume_estime ? __("Volume estimé par l'IA — non recopié sur la fiche Article")
                                          : __("Volume unitaire (m³)")}">
        ${r.volume_estime ? `<div class="lci-vol-est-l">≈ ${__("estimé")}</div>` : ""}</td>`);
    }
    if (V.vol_ligne) {
      cells.push(`<td class="lci-c lci-volligne">${format_number(flt(r.volume_ligne_m3), null, 3)}</td>`);
    }
    if (V.prix_cible) {
      cells.push(`<td><input class="lci-inp lci-num lci-cible" data-f="prix_cible" type="number" step="any"
                 value="${r.prix_cible || ""}" placeholder="${__("cible")}"></td>`);
    }
    if (V.prix_frn) {
      cells.push(`<td>
        <input class="lci-inp lci-num lci-frn" data-f="prix_fournisseur" type="number" step="any"
               value="${r.prix_fournisseur || ""}" placeholder="${__("fourn.")}">
        ${lci_frn_meta(frm, r)}
        ${r.reponse_source ? `<div class="lci-src" title="${esc(r.reponse_source)}">↩ ${esc(r.reponse_source.slice(0, 26))}</div>` : ""}
      </td>`);
    }
    if (V.qty_frn) {
      // une quantité cotée différente de la nôtre change le prix négocié :
      // elle doit se voir, pas se deviner.
      const ecart_q = flt(r.qty_fournisseur) > 0 && Math.abs(flt(r.qty_fournisseur) - flt(r.qty)) > 0.001;
      cells.push(`<td>
        <input class="lci-inp lci-num${ecart_q ? " lci-qty-ko" : ""}" data-f="qty_fournisseur"
               type="number" step="any" value="${r.qty_fournisseur || ""}" placeholder="${__("qté")}"
               title="${__("Quantité cotée ou facturée par le fournisseur")}">
        ${ecart_q ? `<div class="lci-qty-note">${__("nous : {0}", [format_number(flt(r.qty), null, 0)])}</div>` : ""}
      </td>`);
    }
    if (V.qty_cible) {
      // ce qu'on retient après arbitrage : vide = on garde notre quantité.
      const q_ret = lci_qty_ret(r);
      const base = flt(r.qty_fournisseur) || flt(r.qty);
      const bouge = flt(r.qty_cible) > 0 && Math.abs(q_ret - base) > 0.001;
      cells.push(`<td>
        <input class="lci-inp lci-num${bouge ? " lci-ret-ko" : ""}" data-f="qty_cible"
               type="number" step="any" value="${r.qty_cible || ""}"
               placeholder="${format_number(flt(r.qty), null, 0)}"
               title="${__("Quantité retenue pour la contre-proposition (vide = {0})", [format_number(flt(r.qty), null, 0)])}">
      </td>`);
    }
    if (V.prix_neg) {
      const t = flt(r.total_retenu);
      cells.push(`<td>
        <input class="lci-inp lci-num lci-neg" data-f="prix_cible_negocie" type="number" step="any"
               value="${r.prix_cible_negocie || ""}"
               placeholder="${r.prix_fournisseur ? format_number(flt(r.prix_fournisseur), null, 4) : __("négocié")}"
               title="${__("Prix que nous demandons au second tour (vide = son prix accepté)")}">
        ${t ? `<div class="lci-frn-meta lci-frn-tot">${format_currency(t, dev)}</div>` : ""}
      </td>`);
    }
    if (V.decision) {
      cells.push(`<td>
        <select class="lci-inp lci-dec" data-f="decision">
          ${["", "À négocier", "Accepté", "Abandonné"].map((o) =>
            `<option value="${esc(o)}" ${(r.decision || "") === o ? "selected" : ""}>${esc(o || "—")}</option>`).join("")}
        </select>
      </td>`);
    }
    if (V.remarque) {
      const rq = r.remarque_fournisseur || "";
      cells.push(`<td><input class="lci-inp lci-rq" data-f="remarque_fournisseur"
                 value="${esc(rq)}" title="${esc(rq)}" placeholder="${__("observation…")}"></td>`);
    }
    if (V.observation) {
      const ob = r.observation || "";
      const auto = ob && ob === (r.observation_auto || "");
      cells.push(`<td>
        <textarea class="lci-inp lci-obs${auto ? " lci-obs-auto" : ""}" data-f="observation" rows="2"
          placeholder="${__("écrite seule à l'enregistrement…")}"
          title="${auto ? __("Texte généré : il suit les écarts tant que vous n'y touchez pas.")
                        : __("Texte à vous : l'automatisme ne l'écrasera plus.")}">${esc(ob)}</textarea>
      </td>`);
    }
    if (V.conteneur) {
      const cl = lci_cont_label(r);
      cells.push(`<td class="lci-c">${cl
        ? `<span class="lci-cont">${esc(cl)}</span>`
        : `<span style="opacity:.3;">—</span>`}</td>`);
    }
    if (V.desc) {
      cells.push(`<td class="lci-c lci-desc-state" data-act="desc" title="${__("Voir / éditer la description")}">
        ${r.description ? "📝" : '<span style="opacity:.25;">📝</span>'}${r.description_traduite ? " 🌐" : ""}
      </td>`);
    }
    cells.push(`<td class="lci-actions">
      <button class="btn btn-xs btn-default" data-act="dup" title="${__("Dupliquer cette ligne (copie juste en dessous, sans la réponse du fournisseur)")}">⧉</button>
      ${r.item_code ? `<button class="btn btn-xs btn-default" data-act="fam" title="${__("Variantes / articles apparentés")}">🧬</button>` : ""}
      <button class="btn btn-xs btn-default" data-act="ai" title="${__("Améliorer cette ligne (IA)")}">✨</button>
      <button class="btn btn-xs btn-default" data-act="tr" title="${__("Traduire cette ligne (IA)")}">🌐</button>
      <button class="btn btn-xs btn-default" data-act="del" title="${__("Supprimer")}">🗑</button>
    </td>`);

    const sel = lci_selection(frm);
    cells.unshift(`<td class="lci-c lci-selcell"><input type="checkbox" class="lci-sel" ${sel.has(r.name) ? "checked" : ""} title="${__("Cocher pour dupliquer ou copier plusieurs lignes")}"></td>`);
    body += `<tr data-name="${esc(r.name)}" data-idx="${i}" class="${dec_cls.trim()}${sel.has(r.name) ? " lci-selected" : ""}">${cells.join("")}</tr>`;
  });

  const th = [];
  th.push(`<th style="width:30px;" class="lci-c"><input type="checkbox" class="lci-sel-all" title="${__("Tout cocher / décocher (lignes affichées)")}"></th>`);
  th.push(`<th style="width:46px;">#</th>`);
  if (V.image) th.push(`<th style="width:60px;">${__("Image")}</th>`);
  th.push(`<th>${__("Article / Désignation")}</th>`);
  th.push(`<th style="width:90px;">${__("Qté")}</th>`);
  if (V.uom) th.push(`<th style="width:70px;">${__("UDM")}</th>`);
  if (V.vol_unit) th.push(`<th style="width:90px;">${__("Vol. unit (m³)")}</th>`);
  if (V.vol_ligne) th.push(`<th style="width:80px;">${__("Vol. ligne")}</th>`);
  if (V.prix_cible) th.push(`<th style="width:92px;">${__("Prix cible")} (${esc(dev)})</th>`);
  if (V.prix_frn) th.push(`<th style="width:120px;">${__("Prix fourn.")} (${esc(dev)})</th>`);
  if (V.qty_frn) th.push(`<th style="width:92px;">${__("Qté fourn.")}</th>`);
  if (V.qty_cible) th.push(`<th style="width:92px;">${__("Qté retenue")}</th>`);
  if (V.prix_neg) th.push(`<th style="width:110px;">${__("Prix négocié")} (${esc(dev)})</th>`);
  if (V.decision) th.push(`<th style="width:104px;">${__("Décision")}</th>`);
  if (V.remarque) th.push(`<th style="width:160px;">${__("Remarque fourn.")}</th>`);
  if (V.observation) th.push(`<th style="width:220px;">${__("Observation")}</th>`);
  if (V.conteneur) th.push(`<th style="width:90px;">${__("Conteneur")}</th>`);
  if (V.desc) th.push(`<th style="width:56px;">${__("Desc.")}</th>`);
  th.push(`<th style="width:140px;">${__("Actions")}</th>`);

  $t.html(`
    <style>
      /* La table défile DANS son cadre : c'est ce qui permet de figer la barre de filtre et
         l'en-tête des colonnes. Le débordement horizontal faisait déjà de ce bloc un conteneur
         de défilement — sans hauteur maximale, une position collante n'a rien à quoi se
         raccrocher et l'en-tête part avec la page.
         (Pas d'accent grave dans ce commentaire : il vit dans un gabarit JS.) */
      .lci-table-wrap { overflow: auto; margin-bottom: 8px; max-height: calc(100vh - 210px); }
      table.lci-tbl { width: 100%; border-collapse: collapse; font-size: 12.5px; }
      table.lci-tbl th { background: var(--bg-light-gray,#f6f8fa); color: #6b7280; font-size: 10.5px;
        text-transform: uppercase; padding: 6px 8px; text-align: left; white-space: nowrap;
        border-bottom: 1px solid var(--border-color,#e4e8ee);
        position: sticky; top: 38px; z-index: 5; }
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
                          color: #0958d9; padding: 5px 10px; border-top: 2px solid #91caff;
                          position: sticky; top: 64px; z-index: 3; }
      .lci-gmeta { font-weight: 600; color: #6b7280; font-size: 10.5px; }
      .lci-num { width: 84px; text-align: right; }
      .lci-cible { background: #fffbe6; }
      .lci-frn-meta { font-size: 10.5px; margin-top: 2px; text-align: right; white-space: nowrap; }
      .lci-ecart { font-weight: 800; }
      .lci-frn-tot { color: var(--text-muted,#8a93a0); }
      .lci-src { font-size: 9.5px; color: #8a93a0; text-align: right; overflow: hidden;
                 text-overflow: ellipsis; white-space: nowrap; }
      tr.lci-selected > td { background: #fffbe6; }
      .lci-selcell input { width: 14px; height: 14px; margin: 0; cursor: pointer; }
      .lci-sel-bar { display: inline-flex; align-items: center; gap: 6px; margin-left: 8px; padding: 2px 8px;
                     border-radius: 999px; background: #fff7e6; border: 1px solid #ffd591; font-size: 11.5px; }
      .lci-dec { width: 96px; font-size: 11px; padding: 2px 4px;
                 border: 1px solid var(--border-color,#e4e8ee); }
      .lci-qty-ko { color: #ad6800; font-weight: 700; background: #fffbe6; }
      .lci-qty-note { font-size: 9.5px; color: #ad6800; text-align: right; white-space: nowrap; }
      .lci-rq { font-size: 11px; min-width: 120px; }
      tr.lci-dec-ok > td { background: #f6ffed; }
      tr.lci-dec-ko > td { background: #fff1f0; opacity: .6; }
      .lci-uom { width: 64px; }
      .lci-volligne { font-weight: 700; white-space: nowrap; }
      .lci-desc-state { cursor: pointer; font-size: 14px; white-space: nowrap; }
      /* colonne Actions toujours visible (collante à droite) */
      table.lci-tbl th:last-child, table.lci-tbl td.lci-actions {
        position: sticky; right: 0; background: var(--card-bg,#fff);
        box-shadow: -4px 0 6px -4px rgba(0,0,0,.15); }
      /* l'angle haut-droit est collant DANS LES DEUX SENS : il doit passer devant l'en-tête
         (z 5) et devant la colonne Actions des lignes (z 2), sinon il disparaît sous elles */
      table.lci-tbl th:last-child { background: var(--bg-light-gray,#f6f8fa); z-index: 8; }
      table.lci-tbl td.lci-actions { z-index: 2; }
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
      .lci-ret-ko { color: #0958d9; font-weight: 700; background: #e6f4ff; }
      .lci-vol-est { background: #fff7e6; color: #ad6800; }
      .lci-vol-est-l { font-size: 9.5px; color: #ad6800; text-align: right; }
      .lci-neg { background: #f9f0ff; }
      .lci-obs { min-width: 200px; font-size: 11px; resize: vertical; line-height: 1.35; }
      .lci-obs-auto { color: #0958d9; font-style: italic; }
      .lci-cont { font-size: 11px; font-weight: 700; color: #391085; background: #f9f0ff;
                  border: 1px solid #d3adf7; border-radius: 6px; padding: 1px 6px; white-space: nowrap; }
      .lci-filtres { display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
                     margin-bottom: 6px; font-size: 12px;
                     position: sticky; top: 0; z-index: 7; padding: 4px 0 6px;
                     background: var(--card-bg,#fff);
                     border-bottom: 1px solid var(--border-color,#e4e8ee); }
      .lci-filtres select, .lci-filtres input { font-size: 11.5px; padding: 3px 6px; height: 26px;
        border: 1px solid var(--border-color,#e4e8ee); border-radius: 6px; background: var(--card-bg,#fff); }
      .lci-fl-count { font-size: 11px; color: var(--text-muted,#8a93a0); }
      .lci-fl-on { color: #ad6800; font-weight: 700; }
      .lci-rapp { font-size: 11.5px; border-radius: 8px; padding: 6px 10px; margin-bottom: 6px;
                  border: 1px solid #ffe58f; background: #fffbe6; color: #614700; }
      .lci-rapp b { color: #874d00; }
      .lci-rapp.ok { border-color: #b7eb8f; background: #f6ffed; color: #135200; }
      .lci-rapp.ok b { color: #135200; }
      .lci-rapp-sep { color: #d9d9d9; padding: 0 4px; }
    </style>
    ${lci_rapprochement(frm)}
    ${lci_barre_filtre(frm, rows, vis)}
    <table class="lci-tbl">
      <thead><tr>${th.join("")}</tr></thead>
      <tbody>${body}</tbody>
    </table>`);

  // ------------------------------------------------------------ filtre
  const F = lci_filtre(frm);
  $t.find("[data-fl]").on("change", function () {
    const cle = $(this).data("fl");
    if (cle === "reset") return;
    F[cle] = this.value;
    lci_render_table(frm);
  });
  $t.find('[data-fl="texte"]').on("input", function () {
    F.texte = this.value;
    frm.__lci_focus_filtre = true;
    lci_render_table(frm);
  });
  $t.find(".lci-fl-voir").on("click", (e) => {
    e.preventDefault();
    F.etat = "qty_ko";
    lci_render_table(frm);
  });
  $t.find('[data-fl="reset"]').on("click", () => {
    frm.__lci_filtre = { decision: "", etat: "", texte: "" };
    lci_render_table(frm);
  });
  if (frm.__lci_focus_filtre) {
    frm.__lci_focus_filtre = false;
    const el = $t.find('[data-fl="texte"]').get(0);
    if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
  }

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
    const NUM = ["qty", "volume_unitaire_m3", "prix_cible", "prix_fournisseur",
                 "qty_fournisseur", "qty_cible", "prix_cible_negocie"];
    let v = $(e.currentTarget).val();
    if (NUM.includes(f)) v = flt(v);
    if (row[f] === v) return;
    frappe.model.set_value(row.doctype, row.name, f, v);
    if (f === "volume_unitaire_m3" && row.volume_estime) {
      // corrigé à la main : ce n'est plus une estimation, il peut repartir
      // vers la fiche Article.
      frappe.model.set_value(row.doctype, row.name, "volume_estime", 0);
    }
    if (NUM.includes(f)) {
      lci_recalc(frm, false);
      const $tr = $(e.currentTarget).closest("tr");
      $tr.find(".lci-volligne").text(format_number(flt(row.volume_ligne_m3), null, 3));
      $tr.find(".lci-frn-meta").remove();
      $tr.find(".lci-frn").after(lci_frn_meta(frm, row));
    }
    if (f === "decision") {
      const $tr = $(e.currentTarget).closest("tr");
      $tr.removeClass("lci-dec-ok lci-dec-ko")
        .addClass(v === "Accepté" ? "lci-dec-ok" : v === "Abandonné" ? "lci-dec-ko" : "");
    }
  });

  $t.find('[data-act="image"]').on("click", (e) => { const r = row_of(e); r && lci_upload_image(frm, r); });
  $t.find('[data-act="desc"]').on("click", (e) => { const r = row_of(e); r && lci_desc_dialog(frm, r); });
  $t.find('[data-act="ai"]').on("click", (e) => { const r = row_of(e); r && lci_ai(frm, "ai_improve_descriptions", r.name); });
  $t.find('[data-act="tr"]').on("click", (e) => { const r = row_of(e); r && lci_ai(frm, "ai_translate", r.name); });
  $t.find('[data-act="dup"]').on("click", (e) => { const r = row_of(e); r && lci_dupliquer(frm, [r]); });
  $t.find(".lci-sel").on("change", (e) => {
    const row = row_of(e), sel = lci_selection(frm);
    if (!row) return;
    if (e.target.checked) sel.add(row.name); else sel.delete(row.name);
    $(e.target).closest("tr").toggleClass("lci-selected", e.target.checked);
    lci_sel_barre(frm);
  });
  $t.find(".lci-sel-all").on("change", (e) => {
    // sans redessiner la table (le défilement resterait en place et les cases changent sous les yeux)
    const sel = lci_selection(frm), coche = e.target.checked;
    lci_rows_visibles(frm).forEach((r) => { if (coche) sel.add(r.name); else sel.delete(r.name); });
    $t.find("tr[data-name]").each((_i, tr) => {
      const on = sel.has($(tr).data("name"));
      $(tr).toggleClass("lci-selected", on).find(".lci-sel").prop("checked", on);
    });
    lci_sel_barre(frm);
  });
  lci_sel_barre(frm);
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

  if (!filtre_on) lci_bind_sortable(frm, $t);   // voir lci_rows_visibles
  lci_doublons_etat(frm);   // couleur du bouton + bandeau suivent chaque rendu (ajout, retrait, fusion)
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

// ==================================================================
// Réponse du fournisseur : import du classeur chiffré et appariement
// ==================================================================

// Valeurs qu'une ligne du fichier fournisseur propose d'écrire dans la LCI.
// Le volume au carton prime : c'est la mesure que le fournisseur maîtrise.
function lci_valeurs_source(src) {
  const v = {};
  if (!src) return v;
  if (src.prix_unitaire != null) v.prix_fournisseur = src.prix_unitaire;
  if (src.total != null) v.total_fichier = src.total;   // SON addition, pour le rapprochement
  if (src.qty != null) v.qty_fournisseur = src.qty;
  if (src.moq != null) v.moq = src.moq;
  if (src.pcs_carton != null) v.qty_par_carton = src.pcs_carton;
  if (src.volume_carton_m3 != null) v.volume_carton_m3 = src.volume_carton_m3;
  else if (src.volume_unitaire_m3 != null) v.volume_unitaire_m3 = src.volume_unitaire_m3;
  if (src.remarque) v.remarque_fournisseur = src.remarque;
  return v;
}

function lci_src_label(src) {
  if (!src) return "";
  return `L${src.ligne} · ${src.code || ""} ${src.designation || ""}`.trim();
}

async function lci_reponse_import(frm) {
  if (frm.is_new()) {
    frappe.msgprint(__("Enregistrez d'abord le document."));
    return;
  }
  if (frm.is_dirty()) {
    frappe.msgprint(__("Enregistrez vos modifications en cours (Ctrl+S) avant d'importer la réponse."));
    return;
  }
  const input = document.createElement("input");
  input.type = "file";
  // .pdf : liste de prix PDF, tableau lu dans le texte, sinon transcrit par l'IA (25/09/2026)
  input.accept = ".xlsx,.xlsm,.xls,.pdf,application/pdf,application/vnd.ms-excel,"
    + "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file, file.name);
    fd.append("is_private", "1");   // une grille de prix fournisseur reste privée
    fd.append("doctype", frm.doc.doctype);
    fd.append("docname", frm.doc.name);
    frappe.dom.freeze(/\.pdf$/i.test(file.name) ? __("Lecture de la liste de prix PDF (texte, sinon IA)…") : __("Lecture du fichier fournisseur…"));
    let url;
    try {
      const res = await fetch("/api/method/upload_file", {
        method: "POST",
        headers: { "X-Frappe-CSRF-Token": frappe.csrf_token },
        body: fd,
      });
      url = (await res.json()).message?.file_url;
    } catch (e) {
      url = null;
    }
    if (!url) {
      frappe.dom.unfreeze();
      frappe.msgprint(__("Échec de l'envoi du fichier."));
      return;
    }
    try {
      const r = await frappe.call({
        method: "customization_app.lci_reponse.analyser_reponse",
        args: { docname: frm.doc.name, file_url: url },
      });
      frappe.dom.unfreeze();
      if (r.message) lci_reponse_dialog(frm, r.message, file.name);
    } catch (e) {
      frappe.dom.unfreeze();   // le message d'erreur serveur s'affiche seul
    }
  };
  input.click();
}

function lci_reponse_dialog(frm, data, filename) {
  const esc = frappe.utils.escape_html;
  const dev = data.devise || "USD";
  const src_by_id = {};
  (data.sources || []).forEach((s) => (src_by_id[s.id] = s));

  // état modifiable de l'aperçu : source retenue et prix retenu par ligne
  const st = {};
  data.lignes.forEach((l) => {
    const src = l.source ? src_by_id[l.source] : null;
    st[l.row] = {
      source: l.source || "",
      prix: src && src.prix_unitaire != null ? src.prix_unitaire : null,
      apply: !!l.source,
    };
  });

  let tout = false;   // afficher aussi les lignes sans correspondance

  const d = new frappe.ui.Dialog({
    title: __("Réponse du fournisseur"),
    size: "extra-large",
    fields: [{ fieldtype: "HTML", fieldname: "zone" }],
    primary_action_label: __("Appliquer à la liste"),
    primary_action: async () => {
      const lignes = data.lignes
        .filter((l) => st[l.row].apply)
        .map((l) => {
          const src = src_by_id[st[l.row].source];
          const valeurs = lci_valeurs_source(src);
          if (st[l.row].prix != null && st[l.row].prix !== "") {
            valeurs.prix_fournisseur = flt(st[l.row].prix);
            // son montant de ligne ne vaut plus rien si on corrige son prix
            if (src && src.total != null) valeurs.total_fichier = 0;
          }
          return { row: l.row, valeurs, source_label: lci_src_label(src),
                   source_id: st[l.row].source || "" };
        })
        .filter((x) => Object.keys(x.valeurs).length);
      if (!lignes.length) {
        frappe.msgprint(__("Aucune ligne sélectionnée."));
        return;
      }
      const r = await frappe.call({
        method: "customization_app.lci_reponse.appliquer_reponse",
        args: { docname: frm.doc.name, lignes: JSON.stringify(lignes),
                plan: JSON.stringify(data.plan || null) },
        freeze: true,
        freeze_message: __("Écriture des prix…"),
      });
      d.hide();
      await frm.reload_doc();
      lci_render_table(frm);
      const m = r.message || {};
      frappe.msgprint({
        title: __("Réponse importée"),
        indicator: "green",
        message: __("{0} ligne(s) mise(s) à jour.", [m.maj])
          + (m.montant_fichier
            ? `<br>${__("Son fichier additionne")} : <b>${format_currency(m.montant_fichier, dev)}</b>`
              + (m.nb_lignes_divergentes
                ? ` <span style="color:#ad6800;">(${__("{0} ligne(s) cotée(s) sur une autre quantité",
                    [m.nb_lignes_divergentes])})</span>` : "")
            : "")
          + `<br>${__("Montant à nos quantités")} : <b>${format_currency(m.montant_propose, dev)}</b>`
          + (m.montant_cible
            ? ` &nbsp;·&nbsp; ${__("cible")} : ${format_currency(m.montant_cible, dev)}`
              + ` &nbsp;·&nbsp; ${__("écart")} : <b style="color:${lci_ecart_couleur(m.ecart_global_pct)};">`
              + `${m.ecart_global_pct > 0 ? "+" : ""}${format_number(m.ecart_global_pct, null, 1)} %</b>`
            : "")
          + `<br>${__("Volume total")} : <b>${format_number(m.volume_total_m3, null, 3)} m³</b>`,
      });
    },
  });

  const $z = d.fields_dict.zone.$wrapper;

  function render() {
    const visibles = data.lignes.filter((l) => tout || st[l.row].source);
    const options = [`<option value="">${__("— aucune —")}</option>`].concat(
      (data.sources || []).map((s) =>
        `<option value="${esc(s.id)}">${esc(lci_src_label(s))}${
          s.prix_unitaire != null ? ` — ${s.prix_unitaire}` : ""}</option>`));

    // une même ligne fournisseur servie deux fois est presque toujours une erreur
    const usage = {};
    data.lignes.forEach((l) => {
      if (st[l.row].apply && st[l.row].source) {
        usage[st[l.row].source] = (usage[st[l.row].source] || 0) + 1;
      }
    });

    const body = visibles.map((l) => {
      const s = st[l.row];
      const src = s.source ? src_by_id[s.source] : null;
      const prix = s.prix != null ? s.prix : "";
      const ecart = (l.prix_cible > 0 && flt(prix) > 0)
        ? (flt(prix) - l.prix_cible) / l.prix_cible * 100 : null;
      const qty_ko = src && src.qty != null && Math.abs(flt(src.qty) - flt(l.qty)) > 0.001;
      const v = lci_valeurs_source(src);
      const extras = [
        v.moq != null ? `MOQ ${format_number(v.moq, null, 0)}` : "",
        v.qty_par_carton != null ? `${format_number(v.qty_par_carton, null, 0)}/ctn` : "",
        v.volume_carton_m3 != null ? `${format_number(v.volume_carton_m3, null, 4)} m³/ctn` : "",
        v.volume_unitaire_m3 != null ? `${format_number(v.volume_unitaire_m3, null, 4)} m³/u` : "",
      ].filter(Boolean).join(" · ");

      return `<tr data-row="${esc(l.row)}" class="${s.apply ? "" : "lci-rep-off"}">
        <td class="lci-c"><input type="checkbox" data-k="apply" ${s.apply ? "checked" : ""}></td>
        <td class="lci-c">${l.idx}</td>
        <td>
          <div><b>${esc(l.item_code || __("libre"))}</b></div>
          <div class="lci-rep-nom">${esc(l.item_name || "")}</div>
        </td>
        <td class="lci-c">${format_number(l.qty, null, 0)}</td>
        <td class="lci-c${qty_ko ? " lci-rep-qko" : ""}"
            title="${qty_ko ? __("Le fournisseur a coté une quantité différente de la vôtre") : ""}">
          ${src && src.qty != null ? format_number(src.qty, null, 0) : "—"}
        </td>
        <td>
          <select class="form-control input-xs" data-k="source">${options.join("")}</select>
          <div class="lci-rep-meta">
            ${l.methode ? `<span class="lci-rep-badge">${esc(l.methode)}${
              l.confiance && l.confiance < 1 ? ` ${Math.round(l.confiance * 100)} %` : ""}</span>` : ""}
            ${usage[s.source] > 1 ? `<span class="lci-rep-warn">${__("utilisée {0}×", [usage[s.source]])}</span>` : ""}
          </div>
          ${extras ? `<div class="lci-rep-extra">${esc(extras)}</div>` : ""}
        </td>
        <td class="lci-c">${l.prix_cible ? format_number(l.prix_cible, null, 4) : "—"}</td>
        <td><input type="number" step="any" class="form-control input-xs lci-rep-prix"
                   data-k="prix" value="${prix}"></td>
        <td class="lci-c" style="color:${ecart == null ? "#8a93a0" : lci_ecart_couleur(ecart)};font-weight:700;">
          ${ecart == null ? "—" : `${ecart > 0 ? "+" : ""}${format_number(ecart, null, 1)} %`}
        </td>
      </tr>`;
    }).join("");

    const orphelines = (data.orphelines || []).map((id) => src_by_id[id]).filter(Boolean);
    const nb_sel = data.lignes.filter((l) => st[l.row].apply).length;
    const total_sel = data.lignes.reduce((acc, l) =>
      acc + (st[l.row].apply ? flt(st[l.row].prix) * flt(l.qty) : 0), 0);
    // même prix, quantités différentes : deux totaux, et l'écart se voit.
    let nb_qty_ko = 0;
    const total_frn = data.lignes.reduce((acc, l) => {
      if (!st[l.row].apply) return acc;
      const src = src_by_id[st[l.row].source];
      const q = src && src.qty != null ? flt(src.qty) : flt(l.qty);
      if (src && src.qty != null && Math.abs(q - flt(l.qty)) > 0.001) nb_qty_ko += 1;
      return acc + flt(st[l.row].prix) * q;
    }, 0);

    $z.html(`
      <style>
        .lci-rep-info { font-size: 11.5px; color: var(--text-muted,#6b7280); margin-bottom: 8px; }
        .lci-rep-info b { color: var(--text-color,#1f272e); }
        table.lci-rep { width: 100%; border-collapse: collapse; font-size: 12px; }
        table.lci-rep th { background: var(--bg-light-gray,#f6f8fa); font-size: 10px;
          text-transform: uppercase; color: #6b7280; padding: 5px 6px; text-align: left;
          position: sticky; top: 0; z-index: 2; }
        table.lci-rep td { padding: 4px 6px; border-bottom: 1px solid var(--border-color,#eef1f5);
          vertical-align: middle; }
        .lci-rep-wrap { max-height: 54vh; overflow: auto; border: 1px solid var(--border-color,#e4e8ee);
          border-radius: 8px; }
        tr.lci-rep-off { opacity: .45; }
        .lci-rep-nom { font-size: 11px; color: var(--text-muted,#8a93a0); max-width: 300px;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .lci-rep-meta { margin-top: 2px; }
        .lci-rep-badge { font-size: 9.5px; background: #e6f4ff; color: #0958d9; border-radius: 5px;
          padding: 1px 5px; margin-right: 3px; }
        .lci-rep-warn { font-size: 9.5px; background: #fff1f0; color: #a8071a; border-radius: 5px;
          padding: 1px 5px; margin-right: 3px; }
        .lci-rep-extra { font-size: 10px; color: #135200; background: #f6ffed; border-radius: 5px;
          padding: 1px 5px; margin-top: 2px; display: inline-block; }
        .lci-rep-prix { width: 92px; text-align: right; }
        .lci-rep-qko { color: #ad6800; font-weight: 800; background: #fffbe6; }
        .lci-rep-qdiff { font-size: 11.5px; color: #ad6800; margin-top: 3px; }
        .lci-rep-orph { margin-top: 10px; font-size: 11.5px; }
        .lci-rep-orph li { color: #a8071a; }
        .lci-rep-foot { margin-top: 8px; font-size: 12.5px; }
      </style>
      <div class="lci-rep-info">
        📄 <b>${esc(filename || "")}</b> · ${__("feuille")} <b>${esc(data.feuille)}</b>,
        ${__("en-tête ligne")} ${data.ligne_entete} ·
        <b>${(data.sources || []).length}</b> ${__("ligne(s) de cotation lue(s)")}<br>
        ${__("Colonnes reconnues")} : ${Object.entries(data.colonnes || {})
          .map(([k, v]) => `<b>${esc(k)}</b>${v ? ` (${esc(v)})` : ""}`).join(" · ")}
      </div>
      <label style="font-size:12px;">
        <input type="checkbox" id="lci-rep-tout" ${tout ? "checked" : ""}>
        ${__("Afficher aussi les {0} ligne(s) sans correspondance",
             [data.lignes.length - data.lignes.filter((l) => l.source).length])}
      </label>
      <div class="lci-rep-wrap">
        <table class="lci-rep">
          <thead><tr>
            <th style="width:30px;"></th><th style="width:34px;">#</th>
            <th>${__("Ligne de la liste")}</th>
            <th style="width:54px;">${__("Qté")}</th>
            <th style="width:66px;">${__("Qté fourn.")}</th>
            <th style="width:34%;">${__("Ligne du fournisseur")}</th>
            <th style="width:78px;">${__("Cible")}</th>
            <th style="width:100px;">${__("Prix")} (${esc(dev)})</th>
            <th style="width:72px;">${__("Écart")}</th>
          </tr></thead>
          <tbody>${body || `<tr><td colspan="9" style="padding:14px;text-align:center;color:#8a93a0;">${
            __("Aucune correspondance trouvée — cochez « afficher toutes les lignes » pour apparier à la main.")}</td></tr>`}</tbody>
        </table>
      </div>
      <div class="lci-rep-foot">
        <b>${nb_sel}</b> ${__("ligne(s) à écrire")} ·
        ${__("total à vos quantités")} <b>${format_currency(total_sel, dev)}</b>
        ${nb_qty_ko ? `<div class="lci-rep-qdiff">⚠️ ${
          __("{0} ligne(s) cotées sur une quantité différente de la vôtre — total aux quantités du fournisseur : {1}",
             [nb_qty_ko, format_currency(total_frn, dev)])}</div>` : ""}
      </div>
      ${orphelines.length ? `<div class="lci-rep-orph">
        ⚠️ <b>${orphelines.length}</b> ${__("ligne(s) du fournisseur ne correspondent à aucun article de la liste")} :
        <ul>${orphelines.slice(0, 12).map((o) =>
          `<li>L${o.ligne} — ${esc(o.code || "")} ${esc(o.designation || "")}${
            o.prix_unitaire != null ? ` — ${o.prix_unitaire}` : ""}</li>`).join("")}
        ${orphelines.length > 12 ? `<li>… ${orphelines.length - 12} ${__("autres")}</li>` : ""}</ul>
      </div>` : ""}
    `);

    // les <select> sont posés après coup : une valeur avec « : » casse le sélecteur CSS
    $z.find("tr[data-row]").each(function () {
      const row = $(this).data("row");
      $(this).find('[data-k="source"]').val(st[row].source || "");
    });

    $z.find("#lci-rep-tout").on("change", function () {
      tout = this.checked;
      render();
    });
    $z.find('[data-k="apply"]').on("change", function () {
      st[$(this).closest("tr").data("row")].apply = this.checked;
      render();
    });
    $z.find('[data-k="source"]').on("change", function () {
      const row = $(this).closest("tr").data("row");
      const src = src_by_id[this.value];
      st[row].source = this.value;
      st[row].prix = src && src.prix_unitaire != null ? src.prix_unitaire : null;
      st[row].apply = !!this.value;
      render();
    });
    $z.find('[data-k="prix"]').on("change", function () {
      const row = $(this).closest("tr").data("row");
      st[row].prix = this.value === "" ? null : flt(this.value);
      render();
    });
  }

  render();
  d.show();
}

// ------------------------------------------------- prix cibles suggérés

async function lci_prix_cibles(frm) {
  if (frm.is_new()) {
    frappe.msgprint(__("Enregistrez d'abord le document."));
    return;
  }
  const r = await frappe.call({
    method: "customization_app.lci_reponse.prix_cible_suggere",
    args: { docname: frm.doc.name },
    freeze: true,
    freeze_message: __("Recherche des références de prix…"),
  });
  const lignes = (r.message || {}).lignes || [];
  const dev = (r.message || {}).devise || frm.doc.devise || "USD";
  if (!lignes.length) {
    frappe.msgprint({
      title: __("Aucune référence"),
      indicator: "orange",
      message: __("Aucun prix historique en {0} pour les articles de cette liste. "
        + "Les prix cibles sont à saisir à la main dans la colonne « Prix cible ».", [dev]),
    });
    return;
  }
  const esc = frappe.utils.escape_html;
  const par_nom = {};
  lci_rows(frm).forEach((x) => (par_nom[x.name] = x));
  const choix = {};
  lignes.forEach((l) => (choix[l.row] = true));

  const d = new frappe.ui.Dialog({
    title: __("Prix cibles suggérés ({0})", [dev]),
    size: "large",
    fields: [{ fieldtype: "HTML", fieldname: "zone" }],
    primary_action_label: __("Appliquer aux lignes cochées"),
    primary_action: () => {
      let n = 0;
      lignes.forEach((l) => {
        if (!choix[l.row] || !par_nom[l.row]) return;
        frappe.model.set_value(par_nom[l.row].doctype, l.row, "prix_cible", flt(l.prix));
        n += 1;
      });
      d.hide();
      lci_recalc(frm);
      frappe.show_alert({
        message: __("{0} prix cible(s) proposé(s) — enregistrez pour figer.", [n]),
        indicator: "blue",
      });
    },
  });

  const $z = d.fields_dict.zone.$wrapper;
  $z.html(`
    <div style="font-size:11.5px;color:#6b7280;margin-bottom:8px;">
      ${__("Reprise du dernier prix connu <b>dans la même devise</b> : cotation précédente du même "
        + "fournisseur, sinon d'un autre, sinon dernier achat facturé. Rien n'est écrit tant que "
        + "vous n'avez pas enregistré.")}
    </div>
    <div style="max-height:52vh;overflow:auto;border:1px solid var(--border-color,#e4e8ee);border-radius:8px;">
    <table class="lci-rep" style="width:100%;border-collapse:collapse;font-size:12px;">
      <thead><tr>
        <th style="width:30px;"></th><th>${__("Article")}</th>
        <th style="width:100px;">${__("Cible actuelle")}</th>
        <th style="width:100px;">${__("Proposé")}</th>
        <th style="width:38%;">${__("Source")}</th>
      </tr></thead>
      <tbody>${lignes.map((l) => `
        <tr data-row="${esc(l.row)}">
          <td style="text-align:center;"><input type="checkbox" checked></td>
          <td><b>${esc(l.item_code)}</b><div style="font-size:11px;color:#8a93a0;">${
            esc((par_nom[l.row] || {}).item_name || "")}</div></td>
          <td style="text-align:right;">${
            flt((par_nom[l.row] || {}).prix_cible) ? format_number((par_nom[l.row] || {}).prix_cible, null, 4) : "—"}</td>
          <td style="text-align:right;font-weight:700;">${format_number(l.prix, null, 4)}</td>
          <td style="font-size:11px;color:#6b7280;">${esc(l.source || "")}</td>
        </tr>`).join("")}</tbody>
    </table></div>`);
  $z.find("input[type=checkbox]").on("change", function () {
    choix[$(this).closest("tr").data("row")] = this.checked;
  });
  d.show();
}

// --------------------------------------- renvoi du fichier du fournisseur

function lci_export_annote(frm) {
  if (!frm.doc.fichier_fournisseur) {
    frappe.msgprint({
      title: __("Aucun fichier du fournisseur"),
      indicator: "orange",
      message: __("Importez d'abord sa réponse (💰 Prix → Importer la réponse du fournisseur) : "
        + "c'est le fichier reçu qui sert de base au renvoi."),
    });
    return;
  }
  if (frm.is_dirty()) {
    frappe.msgprint(__("Enregistrez d'abord : le fichier est rempli avec la version enregistrée."));
    return;
  }
  const url = "/api/method/customization_app.lci_export_fournisseur.download_reponse_annotee"
    + `?docname=${encodeURIComponent(frm.doc.name)}`;
  // un .xls (format d'avant 2007) ne se réécrit pas : il est reconstruit en
  // .xlsx, donc ses photos et sa mise en forme ne suivent pas. Autant le dire
  // avant, pas une fois le fichier envoyé au fournisseur.
  if ((frm.doc.fichier_fournisseur || "").toLowerCase().endsWith(".xls")) {
    frappe.confirm(
      __("Son fichier est au vieux format .xls : il sera renvoyé en .xlsx. "
        + "Les valeurs, l'ordre des lignes et ses colonnes sont conservés ; "
        + "ses photos et sa mise en forme, non.<br><br>Continuer ?"),
      () => window.open(url));
    return;
  }
  window.open(url);
}

// ------------------------------------------------ observations rédigées

async function lci_observations_ia(frm) {
  if (frm.is_dirty()) {
    frappe.msgprint(__("Enregistrez d'abord : l'IA part des écarts enregistrés."));
    return;
  }
  const r = await frappe.call({
    method: "customization_app.lci_observation.ai_observations",
    args: { docname: frm.doc.name },
    freeze: true,
    freeze_message: __("Rédaction des observations…"),
  });
  const m = r.message || {};
  await frm.reload_doc();
  lci_render_table(frm);
  frappe.show_alert({
    message: m.maj ? __("{0} observation(s) rédigée(s).", [m.maj])
                   : (m.message || __("Rien à commenter.")),
    indicator: m.maj ? "green" : "blue",
  });
}

// ------------------------------------------------------- conteneurs

// « 2 × 40' HC + 1 × 20' » — la flotte en une ligne (miroir de resume_gabarits).
function lci_ct_flotte(conteneurs) {
  const ordre = [];
  const compte = {};
  (conteneurs || []).forEach((c) => {
    const t = c.type || "40' HC";
    if (!(t in compte)) ordre.push(t);
    compte[t] = (compte[t] || 0) + 1;
  });
  return ordre.map((t) => `${compte[t]} × ${t}`).join(" + ");
}

// Déplace EN BLOC des lignes (repérées par conteneur/position) vers le conteneur `dest`
// (index). On retire du plus grand index au plus petit pour ne rien décaler, et deux
// morceaux d'une même ligne se réunissent à l'arrivée. Rend le nombre de lignes déplacées.
// Pure (pas de DOM) : c'est elle que le menu par ligne ET la sélection multiple appellent.
function lci_ct_deplacer_groupe(conteneurs, items, dest) {
  const cible = conteneurs[dest];
  if (!cible) return 0;
  const vus = new Set();
  const a_bouger = (items || []).filter((it) => {
    const cle = it.ci + ":" + it.li;
    if (vus.has(cle) || it.ci === dest) return false;
    vus.add(cle);
    return conteneurs[it.ci] && conteneurs[it.ci].lignes[it.li];
  }).sort((a, b) => (b.ci - a.ci) || (b.li - a.li));
  const parts = a_bouger.map((it) => conteneurs[it.ci].lignes.splice(it.li, 1)[0]).reverse();
  parts.forEach((part) => {
    const meme = cible.lignes.find((l) => l.row === part.row);
    if (meme) meme.qty = flt(meme.qty) + flt(part.qty);
    else cible.lignes.push(part);
  });
  return parts.length;
}

async function lci_conteneurs_dialog(frm) {
  if (frm.is_new()) {
    frappe.msgprint(__("Enregistrez d'abord le document."));
    return;
  }
  if (frm.is_dirty()) {
    frappe.msgprint(__("Enregistrez d'abord : la répartition se calcule sur les volumes enregistrés."));
    return;
  }
  const esc = frappe.utils.escape_html;
  const dev = frm.doc.devise || "USD";
  // `type` est le gabarit PAR DÉFAUT (nouveaux conteneurs) ; `types` porte
  // celui de chaque conteneur, ce qui permet de mélanger 20' et 40' HC.
  const etat = { type: "40' HC", taux: 0.9, types: [], dates: [], plan: null, unites: {},
                 coches: new Set() };   // "ci:row" des lignes cochées pour un déplacement groupé
  let dernier_ajout = null;   // conteneur à mettre en évidence après un ajout
  const LCI_GABARITS = ["20'", "40'", "40' HC"];
  const lci_cap = (c) => flt(c && c.capacite) || flt((etat.plan || {}).capacite);

  const d = new frappe.ui.Dialog({
    title: __("Répartition en conteneurs"),
    size: "extra-large",
    fields: [{ fieldtype: "HTML", fieldname: "zone" }],
    primary_action_label: __("Appliquer la répartition"),
    primary_action: async () => {
      if (!etat.plan) return;
      // conteneurs vidés à la main : on les retire et on renumérote, sinon la
      // liste porterait un « C3 » qui ne contient rien.
      const pleins = etat.plan.conteneurs.filter((c) => (c.lignes || []).length);
      pleins.forEach((c, i) => (c.no = i + 1));
      etat.plan.conteneurs = pleins;
      const r = await frappe.call({
        method: "customization_app.lci_conteneurs.appliquer_plan",
        args: { docname: frm.doc.name, plan: JSON.stringify(etat.plan) },
        freeze: true,
        freeze_message: __("Écriture du plan de chargement…"),
      });
      d.hide();
      await frm.reload_doc();
      lci_render_table(frm);
      lci_render_conteneurs(frm);
      const m = r.message || {};
      frappe.show_alert({
        message: __("{0} conteneur(s) enregistré(s) — {1}.",
                    [m.nb_conteneurs, m.gabarits || ""]),
        indicator: "green",
      });
    },
  });
  const $z = d.fields_dict.zone.$wrapper;

  function recalculer_totaux() {
    etat.plan.conteneurs.forEach((c) => {
      c.volume = 0;
      c.montant = 0;
      (c.lignes || []).forEach((l) => {
        const u = etat.unites[l.row] || { vu: 0, pu: 0 };
        l.volume = flt(l.qty) * u.vu;
        l.montant = flt(l.qty) * u.pu;
        c.volume += l.volume;
        c.montant += l.montant;
      });
    });
  }

  async function calculer() {
    const r = await frappe.call({
      method: "customization_app.lci_conteneurs.plan_auto",
      args: { docname: frm.doc.name, type_conteneur: etat.type, taux: etat.taux,
              types: JSON.stringify(etat.types), dates: JSON.stringify(etat.dates) },
      freeze: true,
      freeze_message: __("Calcul du chargement…"),
    });
    etat.plan = r.message || { conteneurs: [], sans_volume: [] };
    etat.coches.clear();
    etat.types = (etat.plan.conteneurs || []).map((c) => c.type || etat.type);
    etat.dates = (etat.plan.conteneurs || []).map((c) => c.date || "");
    // volume et montant à l'unité : tout déplacement manuel s'y réfère
    etat.unites = {};
    (etat.plan.conteneurs || []).forEach((c) => (c.lignes || []).forEach((l) => {
      etat.unites[l.row] = {
        vu: flt(l.qty) ? flt(l.volume) / flt(l.qty) : 0,
        pu: flt(l.qty) ? flt(l.montant) / flt(l.qty) : 0,
        libelle: l.libelle,
      };
    }));
    render();
  }

  function lci_ct_ajouter() {
    const c = { no: etat.plan.conteneurs.length + 1, type: etat.type, date: "",
                capacite: flt(etat.plan.capacite), volume: 0, montant: 0, lignes: [] };
    etat.plan.conteneurs.push(c);
    etat.types.push(etat.type);
    etat.dates.push("");
    dernier_ajout = c.no;   // sinon il naît tout en bas, hors de l'écran
    return c;
  }

  function lci_ct_supprimer(ci) {
    if (etat.plan.conteneurs.length <= 1) {
      frappe.show_alert({ message: __("Il faut au moins un conteneur."), indicator: "orange" });
      return;
    }
    etat.types.splice(ci, 1);
    etat.dates.splice(ci, 1);
    calculer();   // ses lignes doivent retrouver une place : on recalcule
  }

  function deplacer_groupe(items, dest) {
    if (!items.length) return;
    if (dest === "__new") {
      lci_ct_ajouter();
      dest = etat.plan.conteneurs.length - 1;
    }
    const n = lci_ct_deplacer_groupe(etat.plan.conteneurs, items, cint(dest));
    etat.coches.clear();
    recalculer_totaux();
    render();
    if (n) {
      frappe.show_alert({ message: __("{0} ligne(s) déplacée(s) vers C{1}.", [n, etat.plan.conteneurs[cint(dest)].no]),
                          indicator: "green" });
    }
  }

  function deplacer(ci, li, dest) {
    deplacer_groupe([{ ci, li }], dest);
  }

  // la barre « N lignes cochées » se met à jour sans tout redessiner
  function maj_bulk() {
    const n = etat.coches.size;
    $z.find("#lci-ct-bulk-n").text(n);
    $z.find("#lci-ct-bulk").css("display", n ? "inline-flex" : "none");
  }

  function scinder(ci, li) {
    const part = etat.plan.conteneurs[ci].lignes[li];
    const options = etat.plan.conteneurs.map((c, i) => ({ label: `C${c.no}`, value: String(i) }))
      .filter((o) => cint(o.value) !== ci);
    options.push({ label: __("nouveau conteneur"), value: "__new" });
    frappe.prompt([
      { fieldname: "qty", label: __("Quantité à déplacer"), fieldtype: "Float",
        default: Math.floor(flt(part.qty) / 2), reqd: 1,
        description: __("sur {0}", [format_number(flt(part.qty), null, 0)]) },
      { fieldname: "dest", label: __("Vers"), fieldtype: "Select",
        options: options.map((o) => o.label).join("\n"), default: options[0].label },
    ], (v) => {
      const q = flt(v.qty);
      if (q <= 0 || q >= flt(part.qty)) {
        frappe.show_alert({ message: __("Quantité à scinder invalide."), indicator: "orange" });
        return;
      }
      part.qty = flt(part.qty) - q;
      part.scinde = true;
      const dest = (options.find((o) => o.label === v.dest) || options[0]).value;
      const copie = { row: part.row, libelle: part.libelle, qty: q, scinde: true };
      if (dest === "__new") {
        lci_ct_ajouter().lignes.push(copie);
      } else {
        const cible = etat.plan.conteneurs[cint(dest)];
        const meme = cible.lignes.find((l) => l.row === copie.row);
        if (meme) meme.qty = flt(meme.qty) + q;
        else cible.lignes.push(copie);
      }
      recalculer_totaux();
      render();
    }, __("Scinder la ligne"), __("Scinder"));
  }

  function render() {
    const plan = etat.plan || { conteneurs: [], sans_volume: [], capacite: 0 };
    const vol_total = plan.conteneurs.reduce((a, c) => a + flt(c.volume), 0);
    const mnt_total = plan.conteneurs.reduce((a, c) => a + flt(c.montant), 0);
    const opts_conteneurs = (ci) => plan.conteneurs
      .map((c, i) => `<option value="${i}" ${i === ci ? "selected" : ""}>C${c.no}</option>`)
      .join("") + `<option value="__new">${__("+ nouveau")}</option>`;

    const blocs = plan.conteneurs.map((c, ci) => {
      const cc = lci_cap(c);
      const pct = cc ? Math.min(100, flt(c.volume) / cc * 100) : 0;
      const plein = flt(c.volume) > cc + 0.0001;
      return `<div class="lci-ct${dernier_ajout === c.no ? " neuf" : ""}" data-no="${c.no}">
        <div class="lci-ct-head">
          <label class="lci-ct-all-l" title="${__("Cocher toutes les lignes de ce conteneur")}">
            <input type="checkbox" class="lci-ct-all" data-ci="${ci}"
                   ${(c.lignes || []).length && (c.lignes || []).every((l) => etat.coches.has(ci + ":" + l.row)) ? "checked" : ""}></label>
          <b>C${c.no}</b>
          <span class="lci-ct-gab-l">${esc(c.type || etat.type)}</span>
          ${c.date ? `<span class="lci-ct-date-l">🗓 ${esc(frappe.datetime.str_to_user(c.date))}</span>` : ""}
          <span class="lci-ct-cap">${format_number(cc, null, 2)} m³ ${__("utiles")}</span>
          · ${(c.lignes || []).length} ${__("ligne(s)")}
          <span class="lci-ct-vol${plein ? " ko" : ""}">${format_number(flt(c.volume), null, 3)} /
            ${format_number(cc, null, 2)} m³ (${format_number(pct, null, 0)} %)</span>
          <span class="lci-ct-mnt">${format_currency(flt(c.montant), dev)}</span>
        </div>
        <div class="lci-ct-bar"><div class="lci-ct-fill${plein ? " ko" : ""}" style="width:${pct}%;"></div></div>
        <table class="lci-ct-tbl"><tbody>
          ${(c.lignes || []).length ? "" : `<tr><td class="lci-ct-vide">${
            __("Conteneur vide — envoyez-y des lignes avec le menu « C{0} » à droite de chaque ligne.",
               [c.no])}</td></tr>`}
          ${(c.lignes || []).map((l, li) => `<tr>
            <td style="width:24px;text-align:center;">
              <input type="checkbox" class="lci-ct-sel" data-ci="${ci}" data-li="${li}" data-row="${esc(l.row)}"
                     ${etat.coches.has(ci + ":" + l.row) ? "checked" : ""}></td>
            <td>${esc(l.libelle || "")}${l.scinde ? ` <span class="lci-ct-split">${__("scindée")}</span>` : ""}</td>
            <td style="width:80px;text-align:right;">${format_number(flt(l.qty), null, 0)}</td>
            <td style="width:92px;text-align:right;">${format_number(flt(l.volume), null, 3)} m³</td>
            <td style="width:74px;text-align:center;">
              <select class="lci-ct-mv" data-ci="${ci}" data-li="${li}">${opts_conteneurs(ci)}</select></td>
            <td style="width:34px;text-align:center;">
              <button class="btn btn-xs btn-default lci-ct-cut" data-ci="${ci}" data-li="${li}"
                      title="${__("Scinder cette ligne")}">✂</button></td>
          </tr>`).join("")}
        </tbody></table>
      </div>`;
    }).join("");

    $z.html(`
      <style>
        .lci-ct { border: 1px solid var(--border-color,#e4e8ee); border-radius: 8px; margin-bottom: 8px; }
        .lci-ct-head { padding: 6px 10px; font-size: 12px; background: var(--bg-light-gray,#f6f8fa);
          display: flex; gap: 10px; align-items: center; border-radius: 8px 8px 0 0; }
        .lci-ct-vol { margin-left: auto; font-weight: 700; color: #135200; }
        .lci-ct-vol.ko { color: #a8071a; }
        .lci-ct-mnt { color: #6b7280; }
        .lci-ct-cap { font-size: 10.5px; color: #8a93a0; }
        .lci-ct-gab { font-size: 11px; padding: 1px 3px; }
        .lci-ct-gab-l { font-size: 11px; background: #f9f0ff; border: 1px solid #d3adf7;
          border-radius: 6px; padding: 1px 6px; color: #391085; font-weight: 700; }
        .lci-ct-date-l { font-size: 11px; color: #0958d9; }
        .lci-ct-vide { color: #8a93a0; font-style: italic; padding: 8px 10px; }
        .lci-ct-flotte { display: flex; flex-wrap: wrap; gap: 6px; align-items: center;
          margin-bottom: 8px; padding-bottom: 8px; border-bottom: 1px dashed var(--border-color,#e4e8ee); }
        .lci-ct-chip { display: flex; align-items: center; gap: 4px; font-size: 11.5px;
          border: 1px solid var(--border-color,#e4e8ee); border-radius: 8px; padding: 3px 6px;
          background: var(--card-bg,#fff); }
        .lci-ct-chip.neuf, .lci-ct.neuf { box-shadow: 0 0 0 2px #b37feb; }
        .lci-ct-chip .lci-ct-date { font-size: 11px; padding: 1px 4px; border: 1px solid
          var(--border-color,#e4e8ee); border-radius: 6px; }
        .lci-ct-chipvol { font-size: 10.5px; color: #8a93a0; }
        .lci-ct-del { cursor: pointer; color: #a8071a; font-weight: 700; padding: 0 2px; }
        .lci-ct-del:hover { color: #5c0011; }
        .lci-ct-bar { height: 5px; background: #eef1f5; }
        .lci-ct-fill { height: 5px; background: #52c41a; }
        .lci-ct-fill.ko { background: #f5222d; }
        .lci-ct-tbl { width: 100%; border-collapse: collapse; font-size: 11.5px; }
        .lci-ct-tbl td { padding: 3px 8px; border-bottom: 1px solid var(--border-color,#f2f4f7); }
        .lci-ct-split { font-size: 9.5px; color: #391085; background: #f9f0ff; border-radius: 5px;
          padding: 1px 5px; }
        .lci-ct-mv { font-size: 11px; padding: 1px 3px; }
        .lci-ct-bulk { gap: 6px; align-items: center; background: #fff7e6; border: 1px solid #ffd591;
          border-radius: 6px; padding: 2px 8px; font-size: 12px; }
        .lci-ct-all-l { margin: 0; font-weight: 400; display: inline-flex; }
        .lci-ct-top { display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
          font-size: 12px; margin-bottom: 8px; }
        .lci-ct-warn { font-size: 11.5px; color: #ad6800; background: #fffbe6; border: 1px solid #ffe58f;
          border-radius: 8px; padding: 6px 10px; margin-bottom: 8px; }
        .lci-ct-wrap { max-height: 56vh; overflow: auto; }
      </style>
      <div class="lci-ct-top">
        <label>${__("Gabarit par défaut")}
          <select id="lci-ct-type">${LCI_GABARITS.map((t) =>
            `<option value="${t}" ${etat.type === t ? "selected" : ""}>${t}</option>`).join("")}</select>
        </label>
        <label>${__("Remplissage")}
          <input type="number" id="lci-ct-taux" step="5" min="50" max="100"
                 value="${Math.round(etat.taux * 100)}" style="width:66px;"> %
        </label>
        <button class="btn btn-xs btn-default" id="lci-ct-calc">${__("Recalculer")}</button>
        <span id="lci-ct-bulk" class="lci-ct-bulk" style="display:${etat.coches.size ? "inline-flex" : "none"};">
          ✔ <b id="lci-ct-bulk-n">${etat.coches.size}</b> ${__("ligne(s) cochée(s)")} →
          <select id="lci-ct-bulk-dest">${plan.conteneurs.map((c, i) =>
            `<option value="${i}">C${c.no}</option>`).join("")}<option value="__new">${__("+ nouveau")}</option></select>
          <button class="btn btn-xs btn-primary" id="lci-ct-bulk-go">${__("Déplacer")}</button>
          <button class="btn btn-xs btn-default" id="lci-ct-bulk-clear" title="${__("Tout décocher")}">✕</button>
        </span>
        <span style="margin-left:auto;">
          <b>${esc(lci_ct_flotte(plan.conteneurs) || __("aucun conteneur"))}</b> ·
          <b>${format_number(vol_total, null, 3)} m³</b> · ${format_currency(mnt_total, dev)}
        </span>
      </div>
      <div class="lci-ct-flotte">
        ${plan.conteneurs.map((c, ci) => `<div class="lci-ct-chip${
          dernier_ajout === c.no ? " neuf" : ""}" data-ci="${ci}" data-no="${c.no}">
          <b>C${c.no}</b>
          <select class="lci-ct-gab" data-ci="${ci}" title="${__("Gabarit")}">
            ${LCI_GABARITS.map((t) =>
              `<option value="${t}" ${(c.type || etat.type) === t ? "selected" : ""}>${t}</option>`).join("")}
          </select>
          <input type="date" class="lci-ct-date" data-ci="${ci}" value="${esc(c.date || "")}"
                 title="${__("Date de départ de ce conteneur")}">
          <span class="lci-ct-chipvol">${format_number(flt(c.volume), null, 1)} m³</span>
          <span class="lci-ct-del" data-ci="${ci}" title="${__("Retirer ce conteneur")}">✕</span>
        </div>`).join("")}
        <button class="btn btn-xs btn-default" id="lci-ct-add">➕ ${__("Conteneur")}</button>
      </div>
      ${(plan.sans_volume || []).length ? `<div class="lci-ct-warn">
        ⚠️ ${__("{0} ligne(s) sans volume unitaire ne sont pas réparties — renseignez le volume "
                + "ou le couple qté/carton + CBM/carton :", [plan.sans_volume.length])}
        ${plan.sans_volume.slice(0, 8).map((l) => esc(l.libelle)).join(", ")}
        ${plan.sans_volume.length > 8 ? " …" : ""}
        <button class="btn btn-xs btn-default" id="lci-ct-vol" style="margin-left:6px;">
          📐 ${__("Estimer par IA")}</button></div>` : ""}
      <div class="lci-ct-wrap">${blocs || `<div style="padding:20px;text-align:center;color:#8a93a0;">${
        __("Aucune ligne à charger (lignes abandonnées ou sans quantité).")}</div>`}</div>`);

    $z.find("#lci-ct-type").on("change", function () { etat.type = this.value; calculer(); });
    $z.find("#lci-ct-taux").on("change", function () {
      etat.taux = Math.min(1, Math.max(0.5, flt(this.value) / 100));
      calculer();
    });
    $z.find("#lci-ct-calc").on("click", calculer);
    $z.find("#lci-ct-add").on("click", () => { lci_ct_ajouter(); render(); });
    $z.find(".lci-ct-gab").on("change", function () {
      // changer un gabarit rebat les cartes en aval : on relance le calcul,
      // sinon les lignes resteraient dans un conteneur devenu trop petit.
      etat.types[cint($(this).data("ci"))] = this.value;
      calculer();
    });
    $z.find(".lci-ct-date").on("change", function () {
      // la date ne change pas le chargement : on l'inscrit sans tout recalculer
      const ci = cint($(this).data("ci"));
      etat.dates[ci] = this.value || "";
      if (etat.plan.conteneurs[ci]) etat.plan.conteneurs[ci].date = this.value || "";
      render();
    });
    $z.find(".lci-ct-del").on("click", function () { lci_ct_supprimer(cint($(this).data("ci"))); });
    $z.find("#lci-ct-vol").on("click", () => lci_volumes_dialog(frm, calculer));

    // un conteneur qui vient de naître se voit : on l'amène à l'écran
    if (dernier_ajout) {
      const el = $z.find(`.lci-ct[data-no="${dernier_ajout}"]`).get(0);
      if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
      dernier_ajout = null;
    }
    $z.find(".lci-ct-mv").on("change", function () {
      deplacer(cint($(this).data("ci")), cint($(this).data("li")), this.value);
    });
    $z.find(".lci-ct-sel").on("change", function () {
      const cle = cint($(this).data("ci")) + ":" + $(this).data("row");
      if (this.checked) etat.coches.add(cle); else etat.coches.delete(cle);
      maj_bulk();
    });
    $z.find(".lci-ct-all").on("change", function () {
      const ci = cint($(this).data("ci"));
      const on = this.checked;
      $z.find(`.lci-ct-sel[data-ci="${ci}"]`).each(function () {
        this.checked = on;
        const cle = ci + ":" + $(this).data("row");
        if (on) etat.coches.add(cle); else etat.coches.delete(cle);
      });
      maj_bulk();
    });
    $z.find("#lci-ct-bulk-go").on("click", () => {
      const items = [];
      $z.find(".lci-ct-sel:checked").each(function () {
        items.push({ ci: cint($(this).data("ci")), li: cint($(this).data("li")) });
      });
      deplacer_groupe(items, $z.find("#lci-ct-bulk-dest").val());
    });
    $z.find("#lci-ct-bulk-clear").on("click", () => { etat.coches.clear(); render(); });
    $z.find(".lci-ct-cut").on("click", function () {
      scinder(cint($(this).data("ci")), cint($(this).data("li")));
    });
  }

  d.show();
  await calculer();
}

// ------------------------------------- onglet « Conteneurs » du formulaire

// Le plan appliqué, relu depuis le document (miroir de plan_enregistre côté
// serveur). Rien n'est recalculé : un plan retouché à la main doit rester tel
// qu'il a été appliqué, à l'écran comme dans l'Excel.
function lci_plan_local(frm) {
  let entete = {};
  try { entete = JSON.parse(frm.doc.plan_conteneurs || "{}") || {}; } catch (e) { entete = {}; }
  const conteneurs = (entete.conteneurs || []).map((c) => ({
    no: cint(c.no),
    type: c.type || entete.type || "40' HC",
    date: c.date || "",
    capacite: flt(c.capacite) || flt(entete.capacite),
    volume: 0, montant: 0, lignes: [],
  }));
  const par_no = {};
  conteneurs.forEach((c) => (par_no[c.no] = c));

  const hors_plan = [];
  lci_rows(frm).forEach((r) => {
    const parts = lci_cont_parts(r);
    const q = lci_qty_ret(r);
    if (!parts.length) {
      if (q > 0 && r.decision !== "Abandonné") hors_plan.push(r);
      return;
    }
    const vu = q ? flt(r.volume_ligne_m3) / q : 0;
    const pu = lci_prix_ret(r);
    parts.forEach((p) => {
      const c = par_no[cint(p.no)];
      if (!c) return;
      const qq = flt(p.qty);
      c.lignes.push({
        item_code: r.item_code || "", libelle: r.item_name || r.item_code || "",
        qty: qq, uom: r.uom || "", volume: qq * vu, montant: qq * pu,
        scinde: parts.length > 1,
      });
      c.volume += qq * vu;
      c.montant += qq * pu;
    });
  });
  return { conteneurs, hors_plan, entete };
}

function lci_render_conteneurs(frm) {
  const field = frm.get_field("conteneurs_html");
  if (!field || !field.$wrapper) return;
  const esc = frappe.utils.escape_html;
  const dev = frm.doc.devise || "USD";
  const { conteneurs, hors_plan, entete } = lci_plan_local(frm);
  const $w = field.$wrapper;

  const style = `<style>
    .lci-pl { font-size: 12.5px; }
    .lci-pl-top { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 10px; }
    .lci-pl-flotte { font-weight: 800; color: #391085; }
    .lci-pl-note { font-size: 11px; color: var(--text-muted,#8a93a0); }
    .lci-pl-ct { border: 1px solid var(--border-color,#e4e8ee); border-radius: 8px; margin-bottom: 10px; }
    .lci-pl-head { display: flex; gap: 10px; align-items: center; padding: 6px 10px;
      background: #f9f0ff; border-radius: 8px 8px 0 0; font-size: 12px; }
    .lci-pl-no { font-weight: 800; color: #391085; }
    .lci-pl-gab { font-size: 11px; background: #fff; border: 1px solid #d3adf7; border-radius: 6px;
      padding: 1px 6px; color: #391085; }
    .lci-pl-date { font-size: 11px; color: #0958d9; font-weight: 700; }
    .lci-pl-vol { margin-left: auto; font-weight: 700; color: #135200; }
    .lci-pl-vol.ko { color: #a8071a; }
    .lci-pl-bar { height: 5px; background: #eef1f5; }
    .lci-pl-fill { height: 5px; background: #722ed1; }
    .lci-pl-fill.ko { background: #f5222d; }
    .lci-pl-tbl { width: 100%; border-collapse: collapse; font-size: 11.5px; }
    .lci-pl-tbl td { padding: 3px 10px; border-bottom: 1px solid var(--border-color,#f2f4f7); }
    .lci-pl-split { font-size: 9.5px; color: #391085; background: #f9f0ff; border-radius: 5px; padding: 1px 5px; }
    .lci-pl-vide { padding: 18px; text-align: center; color: #8a93a0; }
    .lci-pl-hors { font-size: 11.5px; color: #ad6800; background: #fffbe6; border: 1px solid #ffe58f;
      border-radius: 8px; padding: 6px 10px; }
  </style>`;

  if (!conteneurs.length) {
    $w.html(`${style}<div class="lci-pl"><div class="lci-pl-vide">
      ${__("Aucun plan de chargement. Le bouton « 🚢 Répartir en conteneurs » calcule la répartition "
        + "à partir des volumes, gabarit par gabarit.")}
      </div><div style="text-align:center;">
      <button class="btn btn-sm btn-primary lci-pl-open">🚢 ${__("Répartir en conteneurs")}</button>
      </div></div>`);
    $w.find(".lci-pl-open").on("click", () => lci_conteneurs_dialog(frm));
    return;
  }

  const vol = conteneurs.reduce((a, c) => a + flt(c.volume), 0);
  const mnt = conteneurs.reduce((a, c) => a + flt(c.montant), 0);

  const blocs = conteneurs.map((c) => {
    const pct = flt(c.capacite) ? Math.min(100, flt(c.volume) / flt(c.capacite) * 100) : 0;
    const ko = flt(c.volume) > flt(c.capacite) + 0.0001;
    return `<div class="lci-pl-ct">
      <div class="lci-pl-head">
        <span class="lci-pl-no">C${c.no}</span>
        <span class="lci-pl-gab">${esc(c.type)}</span>
        ${c.date ? `<span class="lci-pl-date">🗓 ${esc(frappe.datetime.str_to_user(c.date))}</span>` : ""}
        <span class="lci-pl-note">${__("capacité utile")} ${format_number(flt(c.capacite), null, 2)} m³</span>
        <span>· ${c.lignes.length} ${__("ligne(s)")}</span>
        <span class="lci-pl-vol${ko ? " ko" : ""}">${format_number(flt(c.volume), null, 3)} m³
          (${format_number(pct, null, 0)} %)</span>
        <span class="lci-pl-note">${format_currency(flt(c.montant), dev)}</span>
      </div>
      <div class="lci-pl-bar"><div class="lci-pl-fill${ko ? " ko" : ""}" style="width:${pct}%;"></div></div>
      <table class="lci-pl-tbl"><tbody>
        ${c.lignes.map((l) => `<tr>
          <td style="width:130px;"><b>${esc(l.item_code)}</b></td>
          <td>${esc(l.libelle)}${l.scinde ? ` <span class="lci-pl-split">${__("scindée")}</span>` : ""}</td>
          <td style="width:90px;text-align:right;">${format_number(flt(l.qty), null, 0)} ${esc(l.uom)}</td>
          <td style="width:96px;text-align:right;">${format_number(flt(l.volume), null, 3)} m³</td>
          <td style="width:104px;text-align:right;">${format_currency(flt(l.montant), dev)}</td>
        </tr>`).join("")}
      </tbody></table>
    </div>`;
  }).join("");

  $w.html(`${style}<div class="lci-pl">
    <div class="lci-pl-top">
      <span class="lci-pl-flotte">${esc(lci_ct_flotte(conteneurs))}</span>
      <span>· <b>${format_number(vol, null, 3)} m³</b> · <b>${format_currency(mnt, dev)}</b></span>
      <span class="lci-pl-note">· ${__("remplissage")} ${format_number(flt(entete.taux) * 100, null, 0)} %</span>
      ${conteneurs.some((c) => c.date) ? `<span class="lci-pl-note">· ${
        conteneurs.filter((c) => c.date).map((c) =>
          `C${c.no} ${esc(frappe.datetime.str_to_user(c.date))}`).join(" · ")}</span>` : ""}
      <button class="btn btn-xs btn-default lci-pl-open">🚢 ${__("Modifier la répartition")}</button>
      <button class="btn btn-xs btn-default lci-pl-xls">📄 ${__("Excel (onglet Conteneurs)")}</button>
    </div>
    ${hors_plan.length ? `<div class="lci-pl-hors">⚠️ ${
      __("{0} ligne(s) hors plan (volume unitaire manquant ou ajoutées depuis) : {1}",
         [hors_plan.length, esc(hors_plan.slice(0, 10).map((r) => r.item_code || r.item_name).join(", "))])}
      ${hors_plan.length > 10 ? " …" : ""}</div>` : ""}
    <div style="margin-top:10px;">${blocs}</div>
    <div class="lci-pl-note">${__("Ce plan part dans un onglet « Conteneurs » des deux exports Excel "
      + "— notre cotation comme le fichier du fournisseur renvoyé annoté.")}</div>
  </div>`);

  $w.find(".lci-pl-open").on("click", () => lci_conteneurs_dialog(frm));
  $w.find(".lci-pl-xls").on("click", () => {
    window.open("/api/method/customization_app.liste_commande_import.download_excel"
      + `?docname=${encodeURIComponent(frm.doc.name)}`);
  });
}

// ------------------------------------ volumes manquants estimés par l'IA

async function lci_volumes_dialog(frm, apres) {
  if (frm.is_new() || frm.is_dirty()) {
    frappe.msgprint(__("Enregistrez d'abord : l'estimation part des lignes enregistrées."));
    return;
  }
  const r = await frappe.call({
    method: "customization_app.lci_conteneurs.ai_estimer_volumes",
    args: { docname: frm.doc.name },
    freeze: true,
    freeze_message: __("Estimation des volumes…"),
  });
  const data = r.message || {};
  const lignes = data.lignes || [];
  if (!lignes.length) {
    frappe.msgprint({ title: __("Rien à estimer"), indicator: "blue",
      message: data.message || __("Aucune ligne sans volume.") });
    return;
  }

  const esc = frappe.utils.escape_html;
  // état modifiable : un volume proposé se corrige avant d'entrer dans la liste
  const st = {};
  lignes.forEach((l) => (st[l.row] = {
    vol: flt(l.volume_m3), pcs: flt(l.pcs_carton), vct: flt(l.volume_carton_m3),
    apply: flt(l.volume_m3) > 0,
  }));

  const d = new frappe.ui.Dialog({
    title: __("Volumes manquants — proposition"),
    size: "extra-large",
    fields: [{ fieldtype: "HTML", fieldname: "zone" }],
    primary_action_label: __("Appliquer aux lignes cochées"),
    primary_action: async () => {
      const a_ecrire = lignes.filter((l) => st[l.row].apply && st[l.row].vol > 0)
        .map((l) => ({ row: l.row, volume_m3: st[l.row].vol,
                       pcs_carton: st[l.row].pcs, volume_carton_m3: st[l.row].vct,
                       estime: l.estime }));
      if (!a_ecrire.length) {
        frappe.msgprint(__("Aucune ligne sélectionnée."));
        return;
      }
      const res = await frappe.call({
        method: "customization_app.lci_conteneurs.appliquer_volumes",
        args: { docname: frm.doc.name, lignes: JSON.stringify(a_ecrire) },
        freeze: true,
        freeze_message: __("Écriture des volumes…"),
      });
      d.hide();
      await frm.reload_doc();
      lci_render_table(frm);
      lci_render_conteneurs(frm);
      const m = res.message || {};
      frappe.show_alert({
        message: __("{0} volume(s) écrit(s) — total {1} m³.",
                    [m.maj, format_number(m.volume_total_m3, null, 3)]),
        indicator: "green",
      });
      if (apres) apres();      // rouvre le calcul du chargement, le cas échéant
    },
  });

  const $z = d.fields_dict.zone.$wrapper;
  function render() {
    const retenus = lignes.filter((l) => st[l.row].apply && st[l.row].vol > 0);
    const ajout = retenus.reduce((a, l) => a + st[l.row].vol * flt(l.qty), 0);
    const couleur = (c) => (c >= 0.75 ? "#135200" : c >= 0.5 ? "#ad6800" : "#a8071a");

    $z.html(`
      <style>
        table.lci-vol { width: 100%; border-collapse: collapse; font-size: 12px; }
        table.lci-vol th { background: var(--bg-light-gray,#f6f8fa); font-size: 10px;
          text-transform: uppercase; color: #6b7280; padding: 5px 6px; text-align: left;
          position: sticky; top: 0; z-index: 2; }
        table.lci-vol td { padding: 4px 6px; border-bottom: 1px solid var(--border-color,#eef1f5); }
        .lci-vol-wrap { max-height: 54vh; overflow: auto; border: 1px solid var(--border-color,#e4e8ee);
          border-radius: 8px; }
        .lci-vol-inp { width: 96px; text-align: right; }
        .lci-vol-nom { font-size: 11px; color: var(--text-muted,#8a93a0); max-width: 320px;
          overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .lci-vol-base { font-size: 10.5px; color: #6b7280; }
        .lci-vol-foot { margin-top: 8px; font-size: 12.5px; }
        tr.lci-vol-off { opacity: .45; }
      </style>
      <div style="font-size:11.5px;color:#6b7280;margin-bottom:8px;">
        ${__("Volume d'expédition estimé, emballage compris, calé sur les articles voisins déjà "
          + "mesurés. <b>Rien n'est écrit tant que vous n'avez pas appliqué</b> ; un volume estimé "
          + "sert au chargement mais n'est pas recopié sur la fiche Article.")}
      </div>
      <div class="lci-vol-wrap">
        <table class="lci-vol">
          <thead><tr>
            <th style="width:30px;"></th><th style="width:34px;">#</th>
            <th>${__("Article")}</th>
            <th style="width:80px;">${__("Qté")}</th>
            <th style="width:110px;">${__("Vol. unitaire (m³)")}</th>
            <th style="width:80px;">${__("Pcs/ctn")}</th>
            <th style="width:100px;">${__("CBM/ctn")}</th>
            <th style="width:96px;">${__("Vol. ligne")}</th>
            <th style="width:30%;">${__("Base")}</th>
          </tr></thead>
          <tbody>${lignes.map((l) => {
            const s = st[l.row];
            return `<tr data-row="${esc(l.row)}" class="${s.apply ? "" : "lci-vol-off"}">
              <td style="text-align:center;"><input type="checkbox" data-k="apply" ${
                s.apply ? "checked" : ""} ${s.vol > 0 ? "" : "disabled"}></td>
              <td style="text-align:center;">${l.idx}</td>
              <td><b>${esc(l.item_code || __("libre"))}</b>
                <div class="lci-vol-nom">${esc(l.item_name)}</div></td>
              <td style="text-align:right;">${format_number(flt(l.qty), null, 0)} ${esc(l.uom)}</td>
              <td><input type="number" step="any" class="form-control input-xs lci-vol-inp"
                         data-k="vol" value="${s.vol || ""}"></td>
              <td style="text-align:right;">${s.pcs ? format_number(s.pcs, null, 0) : "—"}</td>
              <td style="text-align:right;">${s.vct ? format_number(s.vct, null, 4) : "—"}</td>
              <td style="text-align:right;font-weight:700;">${
                format_number(s.vol * flt(l.qty), null, 3)} m³</td>
              <td class="lci-vol-base">${esc(l.base || "")}
                ${l.estime ? `<span style="color:${couleur(flt(l.confiance))};font-weight:700;">
                  · ${Math.round(flt(l.confiance) * 100)} %</span>` : ""}</td>
            </tr>`;
          }).join("")}</tbody>
        </table>
      </div>
      <div class="lci-vol-foot">
        <b>${retenus.length}</b> ${__("ligne(s) sur {0}", [lignes.length])} ·
        ${__("volume ajouté")} <b>${format_number(ajout, null, 3)} m³</b> ·
        ${__("total liste")} <b>${format_number(flt(frm.doc.volume_total_m3) + ajout, null, 3)} m³</b>
      </div>`);

    $z.find('[data-k="apply"]').on("change", function () {
      st[$(this).closest("tr").data("row")].apply = this.checked;
      render();
    });
    $z.find('[data-k="vol"]').on("change", function () {
      const row = $(this).closest("tr").data("row");
      st[row].vol = flt(this.value);
      st[row].pcs = 0;        // volume corrigé à la main : le carton ne colle plus
      st[row].vct = 0;
      if (st[row].vol > 0) st[row].apply = true;
      render();
    });
  }
  render();
  d.show();
}
