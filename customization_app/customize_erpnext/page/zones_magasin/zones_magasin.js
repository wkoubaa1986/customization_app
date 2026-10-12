/**
 * « Zones & sorties d'articles » — où ranger chaque article, et d'où il sort.
 *
 * Deux niveaux : des espaces (Magasin, Hall…) qui contiennent des zones (« Magasin - A1 »).
 * Un article peut être rangé dans plusieurs zones ; on affecte dans les deux sens :
 *  - « Par zone » : on ouvre une zone, on voit ses articles, on en ajoute ou en retire ;
 *  - « Par article » : un TABLEAU (demande du 12/10/2026) — zones (✕ pour retirer, ＋ pour en
 *    ajouter) et sortie principale (Magasin ou Hall seulement, prise automatiquement par les ventes) ;
 *    cases à cocher + barre d'actions de groupe, jusqu'à « tout le filtre ».
 * Filtres en arbre : groupes d'articles (un parent prend sa branche), espaces (« tout le Hall »
 * = l'espace et toutes ses zones). Les commandes Aramex sortent toujours du Magasin
 * (customization_app.aramex_stock), quelle que soit la sortie principale.
 * Toute la règle est côté serveur (customization_app.zones_magasin).
 */

frappe.pages["zones-magasin"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Zones & sorties", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("zones_magasin", {}));
  wrapper.zm = new ZonesMagasin(wrapper);
};
frappe.pages["zones-magasin"].on_page_show = function (wrapper) {
  if (wrapper.zm && wrapper.zm.ctx) wrapper.zm.majBarre();
};

const ZM_API = "customization_app.zones_magasin";
const ZM_SANS = "__sans__";
const ZM_TOUT = "tout/";                       // « tout l'espace » : l'espace et toutes ses zones
const ZM_RETRAIT = "\u00a0\u00a0\u00a0\u00a0";        // retrait d'un niveau dans les listes en arbre
const zm_esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
const zm_court = (wh) => String(wh || "").replace(/ - [^-]+$/, "");    // « Magasins - A&S » → « Magasins »
const zm_img = (url, cls = "zm-img") => (url ? `<img class="${cls}" src="${zm_esc(url)}" loading="lazy">` : `<div class="${cls}">📦</div>`);

class ZonesMagasin {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".zm-page");
    this.vue = "zones";
    this.articles = [];
    this.selection = new Set();
    this.init();
  }

  async init() {
    this.ctx = (await frappe.call({ method: ZM_API + ".get_context" })).message;
    const r = this.$root, ctx = this.ctx;
    // Groupes en arbre : un parent (📂) prend toute sa branche.
    r.find("#zm-groupe").html(`<option value="">📂 Tous les groupes</option>` + ctx.groupes.map((g) =>
      `<option value="${zm_esc(g.name)}">${ZM_RETRAIT.repeat(g.profondeur)}${g.is_group ? "📂 " : ""}${zm_esc(g.name)} (${g.articles})</option>`).join(""));
    r.find("#zm-sortie").html(`<option value="">🚚 Toutes les sorties principales</option><option value="${ZM_SANS}">⚠️ Sans sortie principale</option>`
      + ctx.entrepots.map((e) => `<option value="${zm_esc(e.name)}">Sortie : ${zm_esc(e.warehouse_name)}</option>`).join(""));
    r.find("#zm-lot-sortie").html(`<option value="__">🚚 Sortie principale…</option>`
      + ctx.entrepots.map((e) => `<option value="${zm_esc(e.name)}">${zm_esc(e.warehouse_name)}</option>`).join(""));
    if (ctx.entrepot_aramex) {
      r.find("#zm-aramex").html(`🚚 <b>Commandes Aramex</b> : la marchandise sort et revient toujours au <b>${zm_esc(zm_court(ctx.entrepot_aramex))}</b>,
        quelle que soit la sortie principale de l’article.`).show();
    }
    r.find("#zm-nouvel-espace").toggle(!!ctx.peut_modifier);
    r.find(".zm-vue").on("click", (e) => this.montrerVue($(e.currentTarget).attr("data-vue")));
    r.find("#zm-espace-saisie").on("keydown", async (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      const texte = ($(e.target).val() || "").trim();
      if (!texte) return;
      await this.creer(texte, null);
      $(e.target).val("");
    });
    r.find("#zm-groupe, #zm-sortie, #zm-stockes, #zm-filtre-zone").on("change", () => this.charger());
    let t = null;
    r.find("#zm-recherche").on("input", () => { clearTimeout(t); t = setTimeout(() => this.charger(), 300); });
    r.find("#zm-plus").on("click", () => this.charger(true));
    r.find("#zm-page-tout").on("change", (e) => {
      this.articles.forEach((a) => (e.target.checked ? this.selection.add(a.item_code) : this.selection.delete(a.item_code)));
      this.peindreArticles();
    });
    r.find("#zm-tout-filtre").on("click", () => this.cocherToutLeFiltre());
    r.find("#zm-aucun").on("click", () => { this.selection.clear(); this.peindreArticles(); });
    r.find("#zm-lot-ajout").on("click", () => this.choisirZones(Array.from(this.selection), "ajout"));
    r.find("#zm-lot-retrait").on("click", () => this.choisirZones(Array.from(this.selection), "retrait"));
    r.find("#zm-lot-sortie").on("change", async (e) => {
      const v = $(e.target).val(); $(e.target).val("__");
      if (v === "__" || !this.selection.size) return;
      const items = Array.from(this.selection);
      frappe.confirm(`Sortie principale <b>${zm_esc(zm_court(v))}</b> pour ${items.length} article(s) ?`, async () => {
        await this.appliquerSortie(items, v);
        this.charger(false, true);
      });
    });
    this.lierTableau();
    this.peindreZones();
    this.charger();
  }

  montrerVue(vue) {
    this.vue = vue;
    this.$root.find(".zm-vue").each((_, el) => $(el).toggleClass("on", $(el).attr("data-vue") === vue));
    this.$root.find("#zm-v-zones").toggle(vue === "zones");
    this.$root.find("#zm-v-articles").toggle(vue === "articles");
    this.majBarre();
  }

  /** Depuis « Par zone » : la zone (ou tout l'espace) en tableau, dans « Par article ». */
  voirEnTableau(zone) {
    this.$root.find("#zm-filtre-zone").val(zone);
    this.$root.find("#zm-recherche").val("");
    this.$root.find("#zm-stockes").prop("checked", false);
    this.montrerVue("articles");
    this.charger();
  }

  espaces() {
    const zones = this.ctx.zones || [];
    return zones.filter((z) => z.est_espace).map((e) => Object.assign({}, e, { zones: zones.filter((z) => z.espace === e.name) }));
  }

  // ── Vue « Par zone » ───────────────────────────────────────────────────────
  peindreZones() {
    const r = this.$root, ctx = this.ctx, modif = ctx.peut_modifier;
    const espaces = this.espaces();
    r.find("#zm-espaces").html(espaces.map((e) => `<div class="zm-card zm-espace">
        <div class="zm-espace-tete">
          <span class="nom" data-ouvrir="${zm_esc(e.name)}">🏬 ${zm_esc(e.name)}</span>
          <span class="n" data-ouvrir="${zm_esc(e.name)}">${e.zones.length} zone(s)${e.articles ? ` · ${e.articles} art. directement` : ""}</span>
          <span class="zm-act" data-tableau="${zm_esc(ZM_TOUT + e.name)}" title="Tous les articles de l’espace et de ses zones, en tableau">📋 ${e.total || 0} art.</span>
          ${modif ? `<span class="act"><span class="zm-act" data-renommer="${zm_esc(e.name)}" title="Renommer">✏️</span>
            <span class="zm-act" data-supprimer="${zm_esc(e.name)}" title="Supprimer">🗑️</span></span>` : ""}
        </div>
        <div class="zm-tuiles">${e.zones.map((z) => `<div class="zm-tuile ${z.articles ? "" : "vide"}" data-ouvrir="${zm_esc(z.name)}">
            <div class="c">${zm_esc(z.code)}</div><div class="n">${z.articles} art.</div></div>`).join("")
          || `<div class="zm-aide">Aucune zone dans cet espace.</div>`}</div>
        ${modif ? `<input type="text" class="form-control zm-saisie" style="margin-top:10px" data-espace="${zm_esc(e.name)}"
            placeholder="➕ Zones de ${zm_esc(e.name)} : A1, A2, A3 puis Entrée">` : ""}
      </div>`).join("") || `<div class="zm-vide">Aucun espace. Créez « Magasin », « Hall »… ci-dessus.</div>`);
    r.find("#zm-espaces [data-ouvrir]").on("click", (ev) => this.ouvrirZone($(ev.currentTarget).attr("data-ouvrir")));
    r.find("#zm-espaces [data-tableau]").on("click", (ev) => this.voirEnTableau($(ev.currentTarget).attr("data-tableau")));
    r.find("#zm-espaces [data-renommer]").on("click", (ev) => this.renommer($(ev.currentTarget).attr("data-renommer")));
    r.find("#zm-espaces [data-supprimer]").on("click", (ev) => this.supprimer($(ev.currentTarget).attr("data-supprimer")));
    r.find("#zm-espaces input[data-espace]").on("keydown", async (ev) => {
      if (ev.key !== "Enter") return;
      ev.preventDefault();
      const texte = ($(ev.target).val() || "").trim();
      if (texte) await this.creer(texte, $(ev.target).attr("data-espace"));
    });
    // Filtre de zone de la vue « Par article », en arbre : l'espace entier (avec toutes ses zones),
    // l'espace seul (« sans zone précise »), puis chacune de ses zones.
    const courant = r.find("#zm-filtre-zone").val();
    r.find("#zm-filtre-zone").html(`<option value="">📍 Toutes les zones</option><option value="${ZM_SANS}">⚠️ Sans zone (${ctx.sans_zone})</option>`
      + espaces.map((e) => `<option value="${zm_esc(ZM_TOUT + e.name)}">🏬 ${zm_esc(e.name)} — tout, avec ses ${e.zones.length} zone(s) (${e.total || 0})</option>`
        + `<option value="${zm_esc(e.name)}">${ZM_RETRAIT}${zm_esc(e.name)} sans zone précise (${e.articles})</option>`
        + e.zones.map((z) => `<option value="${zm_esc(z.name)}">${ZM_RETRAIT}└ ${zm_esc(z.code)} (${z.articles})</option>`).join("")).join(""));
    if (courant) r.find("#zm-filtre-zone").val(courant);
  }

  async majContexte() {
    this.ctx = (await frappe.call({ method: ZM_API + ".get_context" })).message;
    this.peindreZones();
  }

  async creer(texte, espace) {
    const r = (await frappe.call({ method: ZM_API + ".creer_zones", args: { texte, espace } })).message;
    const msg = [r.creees.length ? `${r.creees.length} ajoutée(s) : ${r.creees.join(", ")}` : "",
                 r.deja.length ? `déjà là : ${r.deja.join(", ")}` : ""].filter(Boolean).join(" — ");
    frappe.show_alert({ message: zm_esc(msg), indicator: r.creees.length ? "green" : "orange" }, 5);
    await this.majContexte();
  }

  renommer(zone) {
    const z = this.ctx.zones.find((x) => x.name === zone) || {};
    frappe.prompt({ fieldtype: "Data", fieldname: "code", reqd: 1, default: z.code,
                    label: z.est_espace ? "Nouveau nom de l’espace" : `Nouveau code dans ${z.espace}`,
                    description: z.est_espace ? "Toutes ses zones suivent." : "Si le code existe déjà dans l’espace, les deux zones fusionnent." },
      async (v) => {
        const r = (await frappe.call({ method: ZM_API + ".renommer_zone", args: { ancien: zone, nouveau: v.code } })).message;
        frappe.show_alert({ message: r.fusion ? `Fusionnée dans « ${zm_esc(r.zone)} »` : `Renommée « ${zm_esc(r.zone)} »`, indicator: "green" });
        await this.majContexte();
        if (this.vue === "articles") this.charger();
      }, z.est_espace ? `Renommer l’espace ${zone}` : `Renommer ${zone}`, "Valider");
  }

  supprimer(zone) {
    const z = this.ctx.zones.find((x) => x.name === zone) || {};
    const enfants = this.ctx.zones.filter((x) => x.espace === zone);
    const n = z.articles + enfants.reduce((s, x) => s + x.articles, 0);
    frappe.confirm(`Supprimer ${z.est_espace ? "l’espace" : "la zone"} « ${zm_esc(zone)} »`
      + (enfants.length ? ` et ses ${enfants.length} zone(s)` : "") + " ?"
      + (n ? `<br>Les articles concernés n’y seront plus rangés.` : ""), async () => {
      await frappe.call({ method: ZM_API + ".supprimer_zone", args: { zone } });
      await this.majContexte();
      if (this.vue === "articles") this.charger();
    });
  }

  /** Feuille d'une zone : ses articles (✕ pour retirer) et une recherche pour en ajouter (＋). */
  async ouvrirZone(zone) {
    const modif = this.ctx.peut_modifier;
    const z = this.ctx.zones.find((x) => x.name === zone) || { name: zone };
    let membres = [], trouves = [];
    const d = new frappe.ui.Dialog({ title: `📍 ${zone}`, size: "large", fields: [{ fieldtype: "HTML", fieldname: "zone" }] });
    const $w = d.fields_dict.zone.$wrapper;
    const ligne = (a, bouton) => `<div class="zm-ligne">${zm_img(a.image)}
        <div class="txt"><div class="zm-nom">${zm_esc(a.item_name || a.item_code)}</div><div class="zm-code">${zm_esc(a.item_code)}</div></div>${bouton}</div>`;
    const peindre = () => {
      const ids = new Set(membres.map((m) => m.item_code));
      $w.html(`
        <div style="display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap">
          <span class="zm-act" data-z-tableau title="Ouvrir dans le tableau « Par article »">📋 Voir en tableau</span>
          ${modif ? `<span class="zm-act" data-z-renommer title="Renommer">✏️ Renommer</span>
            <span class="zm-act" data-z-supprimer title="Supprimer">🗑️ Supprimer</span>` : ""}</div>
        ${modif ? `
          <input type="search" class="form-control zm-saisie" id="zm-z-recherche" placeholder="➕ Ajouter un article : tapez son nom ou son code…">
          <div id="zm-z-trouves">${trouves.map((a) => ligne(a, ids.has(a.item_code)
              ? `<span class="bt fait" title="Déjà dans la zone">✓</span>`
              : `<span class="bt ajout" data-ajouter="${zm_esc(a.item_code)}" title="Ajouter">＋</span>`)).join("")}</div>` : ""}
        <div class="zm-lbl" style="margin-top:12px">${membres.length} article(s) rangé(s) ici</div>
        <div>${membres.map((a) => ligne(a, modif ? `<span class="bt retrait" data-retirer="${zm_esc(a.item_code)}" title="Retirer">✕</span>` : "")).join("")
          || `<div class="zm-aide">Aucun article pour l’instant.</div>`}</div>`);
    };
    const chargerMembres = async () => {
      membres = (await frappe.call({ method: ZM_API + ".get_articles", args: { zone, stockes: 0, limite: 200 } })).message.articles;
      peindre();
    };
    let t = null, saisie = "";
    $w.on("input", "#zm-z-recherche", (e) => {
      saisie = $(e.target).val();
      clearTimeout(t);
      t = setTimeout(async () => {
        trouves = saisie.trim().length < 2 ? [] : (await frappe.call({ method: ZM_API + ".get_articles",
          args: { recherche: saisie.trim(), stockes: 0, limite: 15 } })).message.articles;
        peindre();
        $w.find("#zm-z-recherche").val(saisie).focus();
      }, 250);
    });
    $w.on("click", "[data-ajouter]", async (e) => {
      await frappe.call({ method: ZM_API + ".ajouter_a_zone", args: { zone, items: [$(e.currentTarget).attr("data-ajouter")] } });
      await chargerMembres();
      $w.find("#zm-z-recherche").val(saisie);
    });
    $w.on("click", "[data-retirer]", async (e) => {
      await frappe.call({ method: ZM_API + ".retirer_de_zone", args: { zone, items: [$(e.currentTarget).attr("data-retirer")] } });
      await chargerMembres();
    });
    $w.on("click", "[data-z-tableau]", () => { d.hide(); this.voirEnTableau(z.est_espace ? ZM_TOUT + zone : zone); });
    $w.on("click", "[data-z-renommer]", () => { d.hide(); this.renommer(zone); });
    $w.on("click", "[data-z-supprimer]", () => { d.hide(); this.supprimer(zone); });
    d.onhide = () => { this.majContexte(); if (this.vue === "articles") this.charger(); };
    d.show();
    await chargerMembres();
  }

  /** Choix de zones groupées par espace — pour un article (ses zones exactes) ou un lot (ajout / retrait). */
  choisirZones(items, mode, actuelles = []) {
    if (!items.length) return;
    const choisies = new Set(actuelles);
    const titres = { article: "Zones de l’article", ajout: `Ranger ${items.length} article(s) dans…`, retrait: `Retirer ${items.length} article(s) de…` };
    const d = new frappe.ui.Dialog({
      title: titres[mode], size: "large", fields: [{ fieldtype: "HTML", fieldname: "zone" }],
      primary_action_label: mode === "retrait" ? "Retirer" : "Enregistrer",
      primary_action: async () => {
        const zones = Array.from(choisies);
        if (mode === "article") {
          await frappe.call({ method: ZM_API + ".definir_zones", args: { item: items[0], zones } });
        } else {
          if (!zones.length) return frappe.show_alert({ message: "Choisissez au moins une zone", indicator: "orange" });
          for (const zone of zones) {
            await frappe.call({ method: ZM_API + (mode === "ajout" ? ".ajouter_a_zone" : ".retirer_de_zone"), args: { zone, items } });
          }
        }
        d.hide();
        frappe.show_alert({ message: "Zones mises à jour", indicator: "green" }, 3);
        await this.majContexte();
        mode === "article" ? this.majLocale(items[0]) : this.charger(false, true);
      },
    });
    const $w = d.fields_dict.zone.$wrapper;
    // « Tout le Hall » : toutes ses zones — et, pour un retrait, l'espace lui-même (rangement « sans zone précise »).
    const duTout = (e) => (mode === "retrait" ? [e.name] : []).concat(e.zones.map((z) => z.name));
    const espaces = this.espaces();
    const peindre = () => $w.html(espaces.map((e) => {
      const tous = duTout(e), plein = tous.length && tous.every((z) => choisies.has(z));
      return `<div class="zm-choix-espace">🏬 ${zm_esc(e.name)}
          ${e.zones.length ? `<span class="tout" data-tout="${zm_esc(e.name)}">${plein ? "☑" : "☐"} Tout ${zm_esc(e.name)} (${e.zones.length} zones)</span>` : ""}</div>
        <div class="zm-choix">
        <span class="ch ${choisies.has(e.name) ? "on" : ""}" data-z="${zm_esc(e.name)}">${zm_esc(e.name)} (sans zone précise)</span>
        ${e.zones.map((z) => `<span class="ch ${choisies.has(z.name) ? "on" : ""}" data-z="${zm_esc(z.name)}">${zm_esc(z.code)}</span>`).join("")}</div>`;
    }).join(""));
    $w.on("click", ".ch", (ev) => {
      const z = $(ev.currentTarget).attr("data-z");
      choisies.has(z) ? choisies.delete(z) : choisies.add(z);
      peindre();
    });
    $w.on("click", "[data-tout]", (ev) => {
      const e = espaces.find((x) => x.name === $(ev.currentTarget).attr("data-tout"));
      const tous = duTout(e), plein = tous.every((z) => choisies.has(z));
      tous.forEach((z) => (plein ? choisies.delete(z) : choisies.add(z)));
      peindre();
    });
    peindre();
    d.show();
  }

  // ── Vue « Par article » : tableau ───────────────────────────────────────
  filtres() {
    const r = this.$root;
    return { recherche: r.find("#zm-recherche").val() || null, groupe: r.find("#zm-groupe").val() || null,
             zone: r.find("#zm-filtre-zone").val() || null, sortie: r.find("#zm-sortie").val() || null,
             stockes: r.find("#zm-stockes").is(":checked") ? 1 : 0 };
  }

  /** suite : page suivante ; garder : la sélection survit (après une action de groupe). */
  async charger(suite = false, garder = false) {
    const n = (this.requete = (this.requete || 0) + 1);
    const res = (await frappe.call({ method: ZM_API + ".get_articles", freeze: false,
      args: Object.assign(this.filtres(), { start: suite ? this.suivant || 0 : 0 }) })).message;
    if (n !== this.requete) return;                 // une réponse arrivée après un filtre plus récent est périmée
    this.articles = suite ? this.articles.concat(res.articles) : res.articles;
    if (!suite && !garder) this.selection.clear();
    this.suivant = res.suivant;
    this.total = res.total;
    this.peindreArticles();
  }

  async cocherToutLeFiltre() {
    const res = (await frappe.call({ method: ZM_API + ".get_articles", args: Object.assign(this.filtres(), { codes_seulement: 1 }) })).message;
    res.codes.forEach((c) => this.selection.add(c));
    this.peindreArticles();
  }

  ligne(a) {
    const ctx = this.ctx, modif = ctx.peut_modifier, sel = this.selection.has(a.item_code);
    const stock = (a.stocks || []).map((s) => `${zm_esc(zm_court(s.entrepot))} <b>${s.qte}</b>`).join("<br>") || `<span style="color:#cbd5e1">—</span>`;
    const connue = !a.sortie || ctx.entrepots.some((e) => e.name === a.sortie);
    const zones = (a.zones || []).map((z) => `<span class="zm-zchip">${zm_esc(z)}${modif ? `<span class="x" data-retirer-zone="${zm_esc(z)}" title="Retirer">✕</span>` : ""}</span>`).join("")
      || `<span class="zm-zchip aucune">aucune zone</span>`;
    return `<tr class="${sel ? "sel" : ""}" data-item="${zm_esc(a.item_code)}">
      <td class="c-sel">${modif ? `<input type="checkbox" data-coche ${sel ? "checked" : ""}>` : ""}</td>
      <td class="c-art"><div class="zm-art-cell">${zm_img(a.image)}<div>
        <div class="zm-nom"><a href="/app/item/${encodeURIComponent(a.item_code)}" target="_blank">${zm_esc(a.item_name || a.item_code)}</a></div>
        <div class="zm-code">${zm_esc(a.item_code)}</div></div></div></td>
      <td class="c-grp">${zm_esc(a.item_group || "")}</td>
      <td class="c-stock">${stock}</td>
      <td class="c-zones"><div class="zm-zchips">${zones}${modif ? `<span class="zm-zchip plus" data-zones>＋ zone</span>` : ""}</div></td>
      <td class="c-sortie"><select class="form-control" data-sortie ${modif ? "" : "disabled"}>
        ${connue ? "" : `<option value="${zm_esc(a.sortie)}" selected>⚠️ ${zm_esc(zm_court(a.sortie))} (non autorisée)</option>`}
        ${a.sortie ? "" : `<option value="" selected>— non définie —</option>`}
        ${ctx.entrepots.map((e) => `<option value="${zm_esc(e.name)}" ${e.name === a.sortie ? "selected" : ""}>${zm_esc(e.warehouse_name)}</option>`).join("")}
      </select></td></tr>`;
  }

  /** Événements du tableau, posés une fois (délégués) : les lignes se repeignent sans les perdre. */
  lierTableau() {
    const r = this.$root, $t = r.find("#zm-lignes");
    const code = (el) => $(el).closest("tr").attr("data-item");
    $t.on("change", "[data-coche]", (e) => {
      const c = code(e.currentTarget);
      e.currentTarget.checked ? this.selection.add(c) : this.selection.delete(c);
      $(e.currentTarget).closest("tr").toggleClass("sel", e.currentTarget.checked);
      this.majBarre();
    });
    $t.on("click", "[data-zones]", (e) => {
      const a = this.articles.find((x) => x.item_code === code(e.currentTarget));
      this.choisirZones([a.item_code], "article", a.zones || []);
    });
    $t.on("click", "[data-retirer-zone]", async (e) => {
      const c = code(e.currentTarget), zone = $(e.currentTarget).attr("data-retirer-zone");
      await frappe.call({ method: ZM_API + ".retirer_de_zone", args: { zone, items: [c] } });
      await this.majContexte();
      this.majLocale(c);
    });
    $t.on("change", "select[data-sortie]", (e) => {
      const v = $(e.currentTarget).val();
      if (v) this.appliquerSortie([code(e.currentTarget)], v);
    });
  }

  peindreArticles() {
    const r = this.$root;
    r.find("#zm-info").text(`${this.total || 0} article(s)` + (this.articles.length < (this.total || 0) ? ` — ${this.articles.length} affichés` : ""));
    r.find("#zm-lignes").html(this.articles.length ? this.articles.map((a) => this.ligne(a)).join("")
      : `<tr><td colspan="6" class="zm-vide">Aucun article pour ces filtres.</td></tr>`);
    r.find("#zm-plus").toggle(!!this.suivant);
    this.majBarre();
  }

  majBarre() {
    const r = this.$root, n = this.selection.size, modif = this.ctx && this.ctx.peut_modifier;
    r.find("#zm-actions").toggle(!!modif).toggleClass("vide", !n);
    r.find("#zm-compte").text(n ? `${n} article(s) coché(s)` : "Cochez des articles pour agir en groupe");
    r.find("#zm-aucun").toggle(!!n);
    const tous = (this.total || 0) > 0 && n >= this.total;
    r.find("#zm-tout-filtre").toggle(!tous && (this.total || 0) > 0).text(`☑ Cocher tout le filtre (${this.total || 0})`);
    const page = this.articles.length && this.articles.every((a) => this.selection.has(a.item_code));
    r.find("#zm-page-tout").prop("checked", !!page);
    // La barre colle sous la barre de navigation (et sous le titre de page s'il colle lui aussi).
    const $tete = r.closest(".page-container, .page-wrapper").find(".page-head").first();
    const haut = ($(".navbar").outerHeight() || 0) + ($tete.css("position") === "sticky" ? $tete.outerHeight() || 0 : 0);
    if (haut) r[0].style.setProperty("--zm-haut", `${haut}px`);
  }

  async appliquerSortie(items, entrepot) {
    if (!items.length) return;
    await frappe.call({ method: ZM_API + ".definir_sortie", args: { items, entrepot }, freeze: items.length > 20 });
    frappe.show_alert({ message: `🚚 Sortie « ${zm_esc(zm_court(entrepot))} » pour ${items.length} article(s)`, indicator: "green" }, 3);
    this.articles.forEach((a) => { if (items.includes(a.item_code)) a.sortie = entrepot; });
  }

  /** Rafraîchit une seule ligne (ses zones) sans recharger la liste : la ligne reste à sa place. */
  async majLocale(item) {
    const res = (await frappe.call({ method: ZM_API + ".get_articles", args: { recherche: item, stockes: 0, limite: 50 } })).message;
    const frais = res.articles.find((x) => x.item_code === item);
    const a = this.articles.find((x) => x.item_code === item);
    if (a && frais) Object.assign(a, { zones: frais.zones, sortie: frais.sortie });
    this.peindreArticles();
  }
}
