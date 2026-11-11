/**
 * « Nouvelle liste de prix » — l'assistant qui remplace la saisie du formulaire « Liste prix documents » :
 * 1) pour qui (client facultatif, liste de prix), 2) quoi (recherche ou famille entière), 3) créer.
 * Les prix, remises et paliers viennent du serveur (customization_app.liste_prix_assistant) ; l'écran ne
 * garde que les codes choisis et leurs quantités (repris après un rechargement de la page).
 */
frappe.pages["liste-prix-assistant"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Nouvelle liste de prix", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("liste_prix_assistant", {}));
  wrapper.lpa = new ListePrixAssistant(wrapper);
};
frappe.pages["liste-prix-assistant"].on_page_show = function (wrapper) {
  if (wrapper.lpa) wrapper.lpa.depuis_route();
};

const LPA_API = "customization_app.liste_prix_assistant";
const LPA_BROUILLON = "lpa_brouillon";
const LPA_AFFICHES_MAX = 150;      // au-delà, la famille s'ajoute en bloc
const lpa_esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
const lpa_dt = (v) => (v == null ? "—"
  : Number(v).toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 }) + " DT");
const lpa_img = (src) => (src ? `<img src="${lpa_esc(src)}" loading="lazy" alt="">` : `<div class="sansimg"></div>`);

const LPA_TRIS = [
  ["prix_asc", "Prix croissant"], ["prix_desc", "Prix décroissant"],
  ["variantes", "Par variante"], ["variantes_prix", "Par variante et par prix"],
];
const lpa_code = (a, b) => String(a.item_code).localeCompare(String(b.item_code));

const LPA_PRIX = "__prix";      // le critère « prix » dans la liste des critères d'un tri par variante

/** Les noms d'attributs des variantes de ces lignes, dans l'ordre où on les rencontre. Fonction pure. */
function lpa_attributs(lignes) {
  const out = [];
  lignes.forEach((l) => (l.attributs || []).forEach((a) => { if (!out.includes(a.nom)) out.push(a.nom); }));
  return out;
}

/**
 * Critères par défaut d'un tri par variante. « variantes » : les attributs dans l'ordre de la fiche.
 * « variantes_prix » : un seul attribut -> le prix d'abord (les variantes du moins cher au plus cher) ;
 * plusieurs -> le 1er attribut, puis le prix, puis les autres (regroupé par 1er attribut, classé par prix).
 */
function lpa_criteres_defaut(lignes, mode) {
  const noms = lpa_attributs(lignes);
  if (mode !== "variantes_prix") return noms;
  return noms.length > 1 ? [noms[0], LPA_PRIX].concat(noms.slice(1)) : [LPA_PRIX].concat(noms);
}

/** Compare deux variantes sur un critère : le prix, ou la valeur d'un attribut (ordre de la liste de
 * l'attribut, valeur numérique, sinon alphabétique ; une variante sans cet attribut passe après). */
function lpa_comparer(x, y, critere) {
  if (critere === LPA_PRIX) return x.prix_ttc - y.prix_ttc;
  const a = (x.attributs || []).find((t) => t.nom === critere), b = (y.attributs || []).find((t) => t.nom === critere);
  if (!a || !b) return (a ? 0 : 1) - (b ? 0 : 1);
  if (a.pos != null && b.pos != null && a.pos !== b.pos) return a.pos - b.pos;
  if ((a.pos == null) !== (b.pos == null)) return a.pos == null ? 1 : -1;
  return String(a.valeur).localeCompare(String(b.valeur), "fr", { numeric: true });
}

/**
 * Codes de la liste dans le nouvel ordre : les familles gardent leur place, les articles de `groupe`
 * (de toutes les familles si null) sont réordonnés selon `mode`. Fonction pure.
 *  - prix_asc / prix_desc : par prix TTC, à prix égal par code ;
 *  - variantes / variantes_prix : les variantes d'un même modèle se suivent, classées selon `criteres`
 *    (noms d'attributs et, éventuellement, LPA_PRIX, dans l'ordre choisi ; défaut : lpa_criteres_defaut).
 *    « variantes » : modèles et articles simples gardent leur ordre d'arrivée. « variantes_prix » : les
 *    blocs (article simple, ou modèle) vont du moins cher au plus cher — un modèle vaut sa variante la
 *    moins chère.
 */
function lpa_ordre(lignes, groupe, mode, criteres) {
  const familles = [];
  lignes.forEach((l) => { if (!familles.includes(l.item_group)) familles.push(l.item_group); });
  const out = [];
  familles.forEach((f) => {
    let bloc = lignes.filter((l) => l.item_group === f);
    if (groupe == null || f === groupe) {
      if (mode === "prix_asc" || mode === "prix_desc") {
        const s = mode === "prix_asc" ? 1 : -1;
        bloc = bloc.slice().sort((a, b) => (a.prix_ttc - b.prix_ttc) * s || lpa_code(a, b));
      } else {
        const crit = criteres || lpa_criteres_defaut(bloc, mode);
        const blocs = [];                       // [{cle, lignes}] : un article simple, ou un modèle
        bloc.forEach((l) => {
          const cle = l.modele ? "m:" + l.modele : "s:" + l.item_code;
          let b = blocs.find((x) => x.cle === cle);
          if (!b) { b = { cle, lignes: [] }; blocs.push(b); }
          b.lignes.push(l);
        });
        blocs.forEach((b) => b.lignes.sort((x, y) => {
          for (const c of crit) { const d = lpa_comparer(x, y, c); if (d) return d; }
          return (x.rang - y.rang) || lpa_code(x, y);
        }));
        if (mode === "variantes_prix") {
          const mini = (b) => Math.min(...b.lignes.map((l) => l.prix_ttc));
          blocs.sort((x, y) => mini(x) - mini(y) || x.cle.localeCompare(y.cle));
        }
        bloc = [].concat(...blocs.map((b) => b.lignes));
      }
    }
    bloc.forEach((l) => out.push(l.item_code));
  });
  return out;
}

class ListePrixAssistant {
  constructor(wrapper) {
    this.page = wrapper.page;
    this.$root = $(wrapper).find(".lpa-page");
    this.ctx = null;
    this.client = null;
    this.liste = null;
    this.sel = new Map();            // code -> quantité, dans l'ordre d'ajout
    this.lignes = [];                // dernier aperçu du serveur
    this.vue = { type: "familles" }; // familles | recherche | famille
    this.resultats = [];
    this.jeton = 0;                  // ignore les réponses périmées
    this.jeton_apercu = 0;

    this.ctrl_client = frappe.ui.form.make_control({
      df: { fieldtype: "Link", options: "Customer", fieldname: "client", label: "Client",
            placeholder: "Aucun client (liste générale)", change: () => this.client_change() },
      parent: this.$root.find("#lpa-client"), render_input: true,
    });
    // Le contact imprimé sur le PDF catalogue (champ « Référant » du document) : proposé d'office,
    // modifiable (demande utilisateur 11/11/2026).
    this.contact = null;
    this.ctrl_contact = frappe.ui.form.make_control({
      df: { fieldtype: "Link", options: "Employee", fieldname: "contact", label: "Contact sur la liste",
            placeholder: "Employé à contacter", get_query: () => ({ filters: { status: "Active" } }),
            change: () => this.contact_change() },
      parent: this.$root.find("#lpa-contact"), render_input: true,
    });
    this.page.add_inner_button("Toutes les listes", () => frappe.set_route("List", "Liste prix documents"));

    const $q = this.$root.find("#lpa-q");
    $q.on("input", frappe.utils.debounce(() => this.chercher(), 350));
    $q.on("keydown", (e) => { if (e.key === "Enter") this.chercher(); });
    this.$root.find("#lpa-q-x").on("click", () => { $q.val(""); this.chercher(); $q.trigger("focus"); });
    this.$root.on("click", ".lpa-chip", (e) => this.choisir_liste($(e.currentTarget).data("liste")));
    this.$root.on("click", ".lpa-fam .nom", (e) => this.ouvrir_famille($(e.currentTarget).closest(".lpa-fam").data("famille")));
    this.$root.on("click", ".lpa-fam .lpa-tout", (e) => this.ajouter_famille($(e.currentTarget).closest(".lpa-fam").data("famille")));
    this.$root.on("click", "#lpa-retour", () => { $q.val(""); this.chercher(); });
    this.$root.on("click", "#lpa-tout-vue", () => this.ajouter_codes(this.codes_vue()));
    this.$root.on("click", ".lpa-plus[data-code]", (e) => this.basculer(String($(e.currentTarget).data("code"))));
    this.$root.on("click", ".lpa-plus[data-modele]", (e) => {
      const m = this.resultats.find((r) => r.item_code === String($(e.currentTarget).data("modele")));
      if (m) this.ajouter_codes((m.variantes || []).filter((v) => v.prix_ttc != null).map((v) => v.item_code));
    });
    this.$root.on("click", ".lpa-suppr[data-code]", (e) => this.retirer([String($(e.currentTarget).data("code"))]));
    this.$root.on("click", ".lpa-suppr[data-groupe]", (e) => {
      const g = String($(e.currentTarget).data("groupe"));
      this.retirer(this.lignes.filter((l) => l.item_group === g).map((l) => l.item_code));
    });
    // Tri par prix, famille par famille ou pour toute la liste (demande utilisateur 11/11/2026).
    // L'ordre affiché est celui du document et du PDF : on réordonne la sélection elle-même.
    this.tris = {};                  // famille (ou "*" pour toute la liste) -> dernier tri appliqué
    this.$root.on("change", ".lpa-tri", (e) => {
      const $s = $(e.currentTarget), g = $s.data("groupe");
      if ($s.val()) this.trier(g === undefined ? null : String(g), $s.val());
    });
    this.$root.on("change", ".lpa-qte", (e) => {
      const code = String($(e.currentTarget).data("code"));
      const q = Math.max(1, parseInt($(e.currentTarget).val(), 10) || 1);
      if (this.sel.has(code)) { this.sel.set(code, q); this.maj_selection(); }
    });
    this.$root.find("#lpa-vider").on("click", () => {
      if (!this.sel.size) return;
      frappe.confirm("Vider la liste en cours ?", () => { this.sel.clear(); this.maj_selection(); this.marquer_ajoutes(); });
    });
    this.$root.find("#lpa-creer").on("click", () => this.creer());

    frappe.call({ method: LPA_API + ".contexte" }).then((r) => {
      this.ctx = r.message;
      this.reprendre_brouillon();
      if (!this.ctrl_contact.get_value() && this.ctx.contact) this.ctrl_contact.set_value(this.ctx.contact);
      if (!this.liste) this.liste = this.ctx.listes.includes(this.ctx.defaut) ? this.ctx.defaut : this.ctx.listes[0];
      this.rendre_listes();
      this.rendre_gauche();
      this.depuis_route();
      this.maj_selection();
    });
  }

  // ── 1. Pour qui ───────────────────────────────────────────────────────────
  /** Lien depuis un autre écran : frappe.set_route("liste-prix-assistant", {client: "…"}). */
  depuis_route() {
    const o = frappe.route_options;
    if (!this.ctx || !o || !o.client) return;
    frappe.route_options = null;
    this.ctrl_client.set_value(o.client);
  }

  client_change() {
    const c = this.ctrl_client.get_value() || null;
    if (c === this.client) return;
    this.client = c;
    const $info = this.$root.find("#lpa-info-client");
    if (!c) {
      $info.text("Facultatif. Avec un client, sa liste de prix est choisie toute seule.");
      this.sauver();
      return;
    }
    frappe.call({ method: LPA_API + ".liste_du_client", args: { client: c } }).then((r) => {
      if (this.client !== c) return;
      const m = r.message;
      $info.html(`${lpa_esc(m.nom)} — sa liste de prix : <b>${lpa_esc(m.liste)}</b>`);
      this.choisir_liste(m.liste);
    });
  }

  contact_change() {
    const c = this.ctrl_contact.get_value() || null;
    if (c === this.contact) return;
    this.contact = c;
    this.sauver();
    const $info = this.$root.find("#lpa-info-contact");
    if (!c) { $info.text("Sans choix, le contact habituel est repris."); return; }
    frappe.call({ method: LPA_API + ".contact", args: { employe: c } }).then((r) => {
      if (this.contact !== c) return;
      const m = r.message;
      $info.html(m ? `${lpa_esc(m.employee_name)}${m.designation ? " — " + lpa_esc(m.designation) : ""}${
        m.cell_number ? " · " + lpa_esc(m.cell_number) : ""}<br>Imprimé sur le PDF catalogue.`
        : `<span class="text-danger">Cet employé n’est pas actif.</span>`);
    });
  }

  choisir_liste(liste) {
    if (!liste || liste === this.liste) { this.sauver(); return; }
    this.liste = liste;
    this.rendre_listes();
    this.sauver();
    // Les prix affichés sont ceux de la liste : on relit la vue en cours et la sélection.
    if (this.vue.type === "recherche") this.chercher();
    else if (this.vue.type === "famille") this.ouvrir_famille(this.vue.famille);
    this.maj_selection();
  }

  rendre_listes() {
    this.$root.find("#lpa-listes").html((this.ctx.listes || []).map((l) =>
      `<span class="lpa-chip ${l === this.liste ? "actif" : ""}" data-liste="${lpa_esc(l)}">${lpa_esc(l)}</span>`).join(""));
  }

  // ── 2. Ajouter ────────────────────────────────────────────────────────────
  chercher() {
    const q = (this.$root.find("#lpa-q").val() || "").trim();
    this.$root.find("#lpa-q-x").toggle(!!q);
    const jeton = ++this.jeton;
    if (q.length < 2) { this.vue = { type: "familles" }; this.resultats = []; this.rendre_gauche(); return; }
    this.vue = { type: "recherche", q };
    this.$root.find("#lpa-gauche").html(`<div class="lpa-vide">Recherche de « ${lpa_esc(q)} »…</div>`);
    frappe.call({ method: LPA_API + ".rechercher", args: { query: q, price_list: this.liste } }).then((r) => {
      if (jeton !== this.jeton) return;
      this.resultats = (r.message || {}).results || [];
      this.rendre_gauche();
    });
  }

  ouvrir_famille(famille) {
    const jeton = ++this.jeton;
    this.vue = { type: "famille", famille };
    this.$root.find("#lpa-gauche").html(`<div class="lpa-vide">Lecture de la famille « ${lpa_esc(famille)} »…</div>`);
    return frappe.call({ method: LPA_API + ".famille", args: { item_group: famille, price_list: this.liste } }).then((r) => {
      if (jeton !== this.jeton) return null;
      this.resultats = r.message.results || [];
      this.vue.sans_prix = r.message.sans_prix || 0;
      this.rendre_gauche();
      return r.message;
    });
  }

  /** « ＋ Tout » depuis l'arbre : ajoute la famille entière sans l'ouvrir. */
  ajouter_famille(famille) {
    frappe.call({ method: LPA_API + ".famille", args: { item_group: famille, price_list: this.liste },
                  freeze: true, freeze_message: "Lecture de la famille…" }).then((r) => {
      const codes = (r.message.results || []).map((f) => f.item_code);
      if (!codes.length) {
        frappe.show_alert({ message: `Aucun article de « ${famille} » n’a de prix dans « ${this.liste} ».`, indicator: "orange" });
        return;
      }
      this.ajouter_codes(codes);
    });
  }

  /** Les codes ajoutables de la vue (les variantes d'un modèle, jamais le modèle lui-même). */
  codes_vue() {
    const out = [];
    this.resultats.forEach((r) => {
      if (r.est_modele) (r.variantes || []).forEach((v) => { if (v.prix_ttc != null) out.push(v.item_code); });
      else if (r.prix_ttc != null) out.push(r.item_code);
    });
    return out;
  }

  rendre_gauche() {
    const $g = this.$root.find("#lpa-gauche");
    if (this.vue.type === "familles") {
      const fams = (this.ctx.familles || []).map((f) => `
        <div class="lpa-fam n${f.niveau}" data-famille="${lpa_esc(f.name)}" style="padding-left:${6 + f.niveau * 18}px">
          <span class="nom" title="Voir les articles">${lpa_esc(f.name)}</span>
          <span class="nb">${f.nb}</span>
          <button class="btn btn-xs btn-default lpa-tout" title="Ajouter toute la famille">＋ Tout</button>
        </div>`).join("");
      $g.html(`<div class="lpa-entete-vue"><b>Familles</b>
          <span class="lpa-muted">— cliquez un nom pour choisir article par article, ou « ＋ Tout » pour la famille entière</span></div>
        <div class="lpa-defile">${fams}</div>`);
      return;
    }
    const codes = this.codes_vue();
    const titre = this.vue.type === "famille"
      ? `<b>${lpa_esc(this.vue.famille)}</b> <span class="lpa-muted">${codes.length} article(s)${
          this.vue.sans_prix ? ` · ${this.vue.sans_prix} sans prix dans « ${lpa_esc(this.liste)} », non proposé(s)` : ""}</span>`
      : `<b>${this.resultats.length} résultat(s)</b> <span class="lpa-muted">pour « ${lpa_esc(this.vue.q)} »</span>`;
    const entete = `<div class="lpa-entete-vue">
        <button class="btn btn-xs btn-default" id="lpa-retour">← Familles</button> ${titre}
        ${codes.length > 1 ? `<button class="btn btn-xs btn-primary" id="lpa-tout-vue" style="margin-left:auto">＋ Tout ajouter (${codes.length})</button>` : ""}
      </div>`;
    if (!this.resultats.length) { $g.html(entete + `<div class="lpa-vide">Aucun article trouvé.</div>`); return; }
    const visibles = this.resultats.slice(0, LPA_AFFICHES_MAX);
    const reste = this.resultats.length - visibles.length;
    $g.html(entete + `<div class="lpa-defile">${visibles.map((r) => this.html_resultat(r)).join("")}
      ${reste > 0 ? `<div class="lpa-vide">… et ${reste} autre(s) : utilisez « ＋ Tout ajouter », ou la recherche pour en trouver un.</div>` : ""}</div>`);
    this.marquer_ajoutes();
  }

  html_prix(f) {
    if (f.prix_ttc == null) return `<span class="lpa-muted">pas de prix</span>`;
    const rem = f.remise_pct > 0 && f.prix_standard_ttc
      ? `<br><s>${lpa_dt(f.prix_standard_ttc)}</s> <span class="rem">-${f.remise_pct} %</span>` : "";
    return `${lpa_dt(f.prix_ttc)}${rem}`;
  }

  html_article(f, sous) {
    const libelle = sous && f.attributs ? Object.values(f.attributs).join(" · ") || f.item_name : f.item_name;
    return `<div class="lpa-art ${sous ? "sous" : ""}">
        ${sous ? "" : lpa_img(f.image)}
        <div class="txt"><div class="nom">${lpa_esc(libelle)}</div>
          <div class="code">${lpa_esc(f.item_code)}${f.brand ? " · " + lpa_esc(f.brand) : ""}${
            !sous && f.item_group && this.vue.type !== "famille" ? " · " + lpa_esc(f.item_group) : ""}</div></div>
        <div class="prix">${this.html_prix(f)}</div>
        ${f.prix_ttc == null ? `<span class="lpa-plus"></span>`
          : `<button class="btn btn-sm btn-default lpa-plus" data-code="${lpa_esc(f.item_code)}">＋ Ajouter</button>`}
      </div>`;
  }

  html_resultat(r) {
    if (!r.est_modele) return this.html_article(r, false);
    const prix = r.prix_min == null ? `<span class="lpa-muted">pas de prix</span>`
      : (r.prix_min === r.prix_max ? lpa_dt(r.prix_min) : `${lpa_dt(r.prix_min)} – ${lpa_dt(r.prix_max)}`);
    return `<div class="lpa-modele">
        <div class="lpa-art">${lpa_img(r.image)}
          <div class="txt"><div class="nom">${lpa_esc(r.item_name)}</div>
            <div class="code">${lpa_esc(r.item_code)} · ${r.nb_variantes} variante(s)</div></div>
          <div class="prix">${prix}</div>
          <button class="btn btn-sm btn-default lpa-plus" data-modele="${lpa_esc(r.item_code)}">＋ Toutes</button>
        </div>
        ${(r.variantes || []).map((v) => this.html_article(v, true)).join("")}
      </div>`;
  }

  marquer_ajoutes() {
    this.$root.find(".lpa-plus[data-code]").each((_, el) => {
      const dedans = this.sel.has(String($(el).data("code")));
      $(el).toggleClass("ajoute", dedans).text(dedans ? "✓ Ajouté" : "＋ Ajouter");
    });
  }

  // ── 3. La liste ───────────────────────────────────────────────────────────
  basculer(code) {
    if (this.sel.has(code)) this.sel.delete(code); else if (!this.plein(1)) this.sel.set(code, 1);
    this.marquer_ajoutes();
    this.maj_selection();
  }

  ajouter_codes(codes) {
    const nouveaux = codes.filter((c) => !this.sel.has(c));
    if (!nouveaux.length) { frappe.show_alert({ message: "Ces articles sont déjà dans la liste.", indicator: "blue" }); return; }
    if (this.plein(nouveaux.length)) return;
    nouveaux.forEach((c) => this.sel.set(c, 1));
    frappe.show_alert({ message: `${nouveaux.length} article(s) ajouté(s).`, indicator: "green" });
    this.marquer_ajoutes();
    this.maj_selection();
  }

  plein(n) {
    const max = (this.ctx || {}).maximum || 1500;
    if (this.sel.size + n <= max) return false;
    frappe.msgprint(`Une liste ne peut pas dépasser ${max} articles.`);
    return true;
  }

  retirer(codes) {
    codes.forEach((c) => this.sel.delete(c));
    this.marquer_ajoutes();
    this.maj_selection();
  }

  /**
   * Réordonne une famille (ou toute la liste si `groupe` est null) selon `mode` (cf. lpa_ordre).
   * Tri par variante avec plusieurs attributs : l'utilisateur choisit d'abord lequel passe en premier,
   * en deuxième… (demande utilisateur 11/11/2026) ; son choix est repris la fois suivante.
   */
  trier(groupe, mode) {
    const cle = groupe == null ? "*" : groupe;
    const lignes = groupe == null ? this.lignes : this.lignes.filter((l) => l.item_group === groupe);
    const appliquer = (criteres) => {
      if (groupe == null) this.tris = { "*": mode }; else this.tris[groupe] = mode;
      const ordre = lpa_ordre(this.lignes, groupe, mode, criteres);
      const sel = new Map();
      ordre.forEach((c) => { if (this.sel.has(c)) sel.set(c, this.sel.get(c)); });
      this.sel.forEach((q, c) => { if (!sel.has(c)) sel.set(c, q); });   // sans prix / pas encore relus : à la fin
      this.sel = sel;
      this.maj_selection();
    };
    if (!mode.startsWith("variantes") || lpa_attributs(lignes).length < 2) { appliquer(null); return; }
    this.choisir_criteres(lignes, mode, cle, appliquer);
  }

  /** La fenêtre « dans quel ordre ? » : une ligne par attribut (et le prix), à cocher et à monter / descendre. */
  choisir_criteres(lignes, mode, cle, appliquer) {
    const noms = lpa_attributs(lignes);
    const possibles = noms.concat(mode === "variantes_prix" ? [LPA_PRIX] : []);
    const memo = ((this.criteres || {})[cle + "|" + mode] || []).filter((c) => possibles.includes(c.nom));
    let liste = memo.length ? memo.slice() : lpa_criteres_defaut(lignes, mode).map((nom) => ({ nom, actif: true }));
    possibles.forEach((nom) => { if (!liste.some((c) => c.nom === nom)) liste.push({ nom, actif: true }); });
    const libelle = (nom) => (nom === LPA_PRIX ? "Prix (croissant)" : nom);
    const exemple = (nom) => {
      if (nom === LPA_PRIX) return "";
      const vus = [];
      lignes.forEach((l) => (l.attributs || []).forEach((a) => { if (a.nom === nom && !vus.includes(a.valeur)) vus.push(a.valeur); }));
      return vus.slice(0, 4).join(", ") + (vus.length > 4 ? "…" : "");
    };
    const d = new frappe.ui.Dialog({
      title: mode === "variantes_prix" ? "Trier par variante et par prix" : "Trier par variante",
      fields: [{ fieldtype: "HTML", fieldname: "corps" }],
      primary_action_label: "Trier",
      primary_action: () => {
        this.criteres = this.criteres || {};
        this.criteres[cle + "|" + mode] = liste.map((c) => ({ ...c }));
        const actifs = liste.filter((c) => c.actif).map((c) => c.nom);
        d.hide();
        appliquer(actifs.length ? actifs : null);
      },
    });
    const $c = d.fields_dict.corps.$wrapper;
    const rendre = () => {
      let rang = 0;
      $c.html(`<p class="text-muted" style="font-size:12.5px">Les variantes d’un même modèle sont classées d’abord selon la 1<sup>re</sup> ligne,
          puis la 2<sup>e</sup>, etc. Montez ou descendez une ligne pour changer l’ordre ; décochez-la pour l’ignorer.</p>
        ${liste.map((c, i) => `<div class="lpa-crit" data-i="${i}" style="display:flex;align-items:center;gap:10px;padding:7px 8px;border:1px solid var(--border-color,#e2e8f0);border-radius:8px;margin-bottom:6px;${c.actif ? "" : "opacity:.55"}">
            <span style="width:22px;height:22px;border-radius:50%;background:${c.actif ? "#2563eb" : "#cbd5e1"};color:#fff;font-size:12px;display:inline-flex;align-items:center;justify-content:center">${c.actif ? ++rang : "–"}</span>
            <input type="checkbox" class="lpa-crit-actif" ${c.actif ? "checked" : ""} style="margin:0">
            <span style="flex:1"><b>${lpa_esc(libelle(c.nom))}</b>${exemple(c.nom) ? ` <span class="text-muted" style="font-size:11.5px">— ${lpa_esc(exemple(c.nom))}</span>` : ""}</span>
            <button type="button" class="btn btn-xs btn-default lpa-crit-haut" ${i === 0 ? "disabled" : ""}>↑</button>
            <button type="button" class="btn btn-xs btn-default lpa-crit-bas" ${i === liste.length - 1 ? "disabled" : ""}>↓</button>
          </div>`).join("")}`);
    };
    $c.on("click", ".lpa-crit-haut, .lpa-crit-bas", (e) => {
      const i = Number($(e.currentTarget).closest(".lpa-crit").data("i"));
      const j = i + ($(e.currentTarget).hasClass("lpa-crit-haut") ? -1 : 1);
      if (j < 0 || j >= liste.length) return;
      [liste[i], liste[j]] = [liste[j], liste[i]];
      rendre();
    });
    $c.on("change", ".lpa-crit-actif", (e) => {
      liste[Number($(e.currentTarget).closest(".lpa-crit").data("i"))].actif = e.currentTarget.checked;
      rendre();
    });
    rendre();
    // Fenêtre fermée sans trier : le menu revient à l'ordre en place.
    d.onhide = () => this.rendre_selection(this.dernier_apercu || { lignes: this.lignes, total: 0 });
    d.show();
  }

  /** Le menu de tri d'une famille (ou global) ; les tris « par variante » n'apparaissent que s'il y en a. */
  html_tri(groupe, lignes) {
    const variantes = lignes.some((l) => l.modele);
    const actuel = this.tris[groupe == null ? "*" : groupe] || (groupe == null ? "" : this.tris["*"]) || "";
    return `<select class="lpa-tri" ${groupe == null ? "" : `data-groupe="${lpa_esc(groupe)}"`} title="Ordre des articles${groupe == null ? " de toute la liste" : " de cette famille"}">
        <option value="">↕ Trier…</option>
        ${LPA_TRIS.filter(([m]) => variantes || !m.startsWith("variantes"))
          .map(([m, t]) => `<option value="${m}" ${m === actuel ? "selected" : ""}>${t}</option>`).join("")}
      </select>`;
  }

  articles() {
    return Array.from(this.sel, ([item_code, qte]) => ({ item_code, qte }));
  }

  sauver() {
    try {
      localStorage.setItem(LPA_BROUILLON, JSON.stringify({ client: this.client, liste: this.liste, contact: this.contact, articles: this.articles() }));
    } catch (e) { /* stockage indisponible : la liste vit le temps de la page */ }
  }

  reprendre_brouillon() {
    let b = null;
    try { b = JSON.parse(localStorage.getItem(LPA_BROUILLON) || "null"); } catch (e) { b = null; }
    if (!b) return;
    if (b.liste && this.ctx.listes.includes(b.liste)) this.liste = b.liste;
    (b.articles || []).forEach((a) => { if (a && a.item_code) this.sel.set(String(a.item_code), Math.max(1, parseInt(a.qte, 10) || 1)); });
    if (b.contact) this.ctrl_contact.set_value(b.contact);
    if (b.client) this.ctrl_client.set_value(b.client);   // relit aussi la liste de prix du client
  }

  maj_selection() {
    this.sauver();
    const jeton = ++this.jeton_apercu;
    if (!this.sel.size) { this.lignes = []; this.rendre_selection({ lignes: [], total: 0, sans_prix: [], introuvables: [] }); return; }
    frappe.call({ method: LPA_API + ".apercu", args: { articles: JSON.stringify(this.articles()), price_list: this.liste } })
      .then((r) => { if (jeton === this.jeton_apercu) { this.lignes = r.message.lignes; this.rendre_selection(r.message); } });
  }

  rendre_selection(a) {
    this.dernier_apercu = a;
    const n = a.lignes.length;
    this.$root.find("#lpa-titre-liste").html(`Votre liste <span class="lpa-muted">— ${n} article(s) · ${lpa_esc(this.liste || "")}</span>`);
    this.$root.find("#lpa-creer").prop("disabled", !n);
    this.$root.find("#lpa-vider").toggle(!!this.sel.size);
    this.$root.find("#lpa-trier").html(n > 1 ? this.html_tri(null, a.lignes) : "");
    this.$root.find("#lpa-total").html(n ? `Total TTC pour ces quantités : <b>${lpa_dt(a.total)}</b>` : "");
    const ecartes = (a.sans_prix || []).concat(a.introuvables || []);
    this.$root.find("#lpa-alertes").html(ecartes.length
      ? `<div class="lpa-alerte">⚠️ ${ecartes.length} article(s) sans prix dans « ${lpa_esc(this.liste)} » (ou plus en vente) :
           ils ne seront pas dans la liste — ${ecartes.slice(0, 8).map(lpa_esc).join(", ")}${ecartes.length > 8 ? "…" : ""}
           <a href="#" id="lpa-ecarter" style="margin-left:6px">Les retirer</a></div>` : "");
    this.$root.find("#lpa-ecarter").on("click", (e) => { e.preventDefault(); this.retirer(ecartes); });
    const $s = this.$root.find("#lpa-selection");
    if (!n) {
      $s.html(`<div class="lpa-vide">La liste est vide.<br>Cherchez un article ou choisissez une famille à gauche, puis « ＋ Ajouter ».</div>`);
      return;
    }
    let html = `<div class="lpa-colonnes"><span></span><span>Article</span><span class="num">Prix TTC</span><span>Qté</span><span class="num">Total</span><span></span></div>
      <div class="lpa-defile">`;
    let groupe = null;
    a.lignes.forEach((l) => {
      if (l.item_group !== groupe) {
        groupe = l.item_group;
        const nb = a.lignes.filter((x) => x.item_group === groupe).length;
        html += `<div class="lpa-groupe"><span style="flex:1">${lpa_esc(groupe || "Sans famille")} <span class="nb">· ${nb}</span></span>
          ${nb > 1 ? this.html_tri(groupe, a.lignes.filter((x) => x.item_group === groupe)) : ""}
          <span class="lpa-suppr" data-groupe="${lpa_esc(groupe)}" title="Retirer toute la famille">✕</span></div>`;
      }
      const palier = l.palier ? `<div class="pal ok">${lpa_esc(l.palier)}</div>`
        : (l.prochain ? `<div class="pal">prochain palier : ${lpa_esc(l.prochain)}</div>` : "");
      html += `<div class="lpa-sel-ligne">
          ${lpa_img(l.image)}
          <div><div class="nom">${lpa_esc(l.item_name)}</div>${
            (l.attributs || []).length ? `<div class="attr">${l.attributs.map((t) => `${lpa_esc(t.nom)} : ${lpa_esc(t.valeur)}`).join(" · ")}</div>` : ""}
            <div class="code">${lpa_esc(l.item_code)}</div>${palier}</div>
          <div class="num"><b>${lpa_dt(l.prix_ttc)}</b>${l.remise_pct > 0 && l.prix_standard_ttc
            ? `<br><s>${lpa_dt(l.prix_standard_ttc)}</s> <span class="rem">-${l.remise_pct} %</span>` : ""}${
            l.prix_promo != null ? `<br><span class="promo" title="Prix TTC minimum avec promotion">promo ${lpa_dt(l.prix_promo)}</span>` : ""}</div>
          <input type="number" min="1" step="1" class="form-control lpa-qte" data-code="${lpa_esc(l.item_code)}" value="${l.qte}">
          <div class="num">${lpa_dt(l.total)}</div>
          <span class="lpa-suppr" data-code="${lpa_esc(l.item_code)}" title="Retirer">✕</span>
        </div>`;
    });
    $s.html(html + "</div>");
  }

  creer() {
    if (!this.sel.size) return;
    frappe.call({
      method: LPA_API + ".creer", type: "POST",
      args: { articles: JSON.stringify(this.articles()), price_list: this.liste, client: this.client, referant: this.contact },
      freeze: true, freeze_message: "Création de la liste de prix…",
    }).then((r) => {
      const m = r.message;
      this.sel.clear();
      this.maj_selection();
      this.marquer_ajoutes();
      const libelles = { "Liste prix client": "📄 PDF simple (prix, remise, quantités)", "Liste des prix": "📘 PDF catalogue (avec descriptions)" };
      const d = new frappe.ui.Dialog({
        title: "Liste de prix créée",
        fields: [{ fieldtype: "HTML", fieldname: "corps" }],
        primary_action_label: "Nouvelle liste",
        primary_action: () => d.hide(),
      });
      d.fields_dict.corps.$wrapper.html(`
        <p>✅ <b>${lpa_esc(m.name)}</b> — ${m.nb_articles} article(s), liste « ${lpa_esc(this.liste)} »${
          this.client ? `, client ${lpa_esc(this.client)}` : ""}.</p>
        ${(m.sans_prix || []).length ? `<p class="text-muted">${m.sans_prix.length} article(s) sans prix écarté(s).</p>` : ""}
        <div style="display:flex;flex-direction:column;gap:8px;margin-top:10px">
          ${(m.pdf || []).map((p) => `<a class="btn btn-default" target="_blank" href="${lpa_esc(p.url)}">${libelles[p.format] || lpa_esc(p.format)}</a>`).join("")}
          <a class="btn btn-default" href="/app/liste-prix-documents/${encodeURIComponent(m.name)}">Ouvrir le document</a>
        </div>`);
      d.show();
    });
  }
}
