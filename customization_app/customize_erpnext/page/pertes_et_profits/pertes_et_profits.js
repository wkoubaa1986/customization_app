/**
 * Pertes et Profits — le détail du graphique « Pertes et Profits » de l'onglet Comptabilité (05/11/2026).
 *
 * Quatre totaux sur la période (ventes H.T, stock livré H.T, dépenses, bénéfice). Toucher un total ouvre ses
 * pièces, compte par compte : « Omettre » (en tout ou en partie, avec motif) ou « Rétablir ». Ces choix ne valent
 * QUE pour Pertes et Profits (le graphique de l'onglet les suit) ; la situation mensuelle garde les siens.
 * Serveur : customization_app.pertes_profits. Pensé pour le téléphone.
 */
frappe.pages["pertes-et-profits"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Pertes et Profits", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("pertes_et_profits", {}));
  wrapper.pp = new PertesEtProfits(wrapper);
};

frappe.pages["pertes-et-profits"].on_page_show = function (wrapper) {
  // Fil d'Ariane : on vient de l'onglet Comptabilité (sinon Frappe affiche le dernier onglet visité).
  frappe.breadcrumbs.add({ type: "Custom", label: __("Accounting"), route: "/app/accounting" });
  if (wrapper.pp && wrapper.pp.data) {
    wrapper.pp.appliquerRoute();      // clic sur le graphe de l'onglet : sa période
    wrapper.pp.charger();
  }
};

const PP_API = "customization_app.pertes_profits";
const pp_esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
const pp_dt = (v, dec = 0) => format_currency(v || 0, "TND", dec);
const PP_BLOCS = [
  { bloc: "ventes", titre: "Ventes H.T" },
  { bloc: "stock", titre: "Stock délivré H.T" },
  { bloc: "depenses", titre: "Dépenses comptabilisées" },
];

class PertesEtProfits {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".pp-page");
    this.mode = "mois";
    this.mois = null;
    this.annee = null;
    this.$root.find("#pp-mode span").on("click", (e) => {
      const m = $(e.currentTarget).attr("data-mode");
      if (m === "total") { this.mode = "annee"; this.annee = "Total"; }
      else { this.mode = m; if (m === "annee" && (!this.annee || this.annee === "Total")) this.annee = String(new Date().getFullYear()); }
      this.charger();
    });
    this.$root.find("#pp-choix").on("change", (e) => {
      if (this.mode === "mois") this.mois = $(e.target).val(); else this.annee = $(e.target).val();
      this.charger();
    });
    this.demarrer();
  }

  /** Période transmise par le clic sur le graphe de l'onglet. -> vrai si une période a été appliquée. */
  appliquerRoute() {
    const p = (frappe.route_options || {}).pp_periode;
    if (!p) return false;
    frappe.route_options = null;
    this.mode = p.mode === "annee" ? "annee" : "mois";
    this.mois = p.mois || null;
    this.annee = p.annee || null;
    return true;
  }

  /** On ouvre sur la période que montre le graphique de l'onglet (filtre de l'entonnoir). */
  async demarrer() {
    if (this.appliquerRoute()) return this.charger();
    try {
      const p = (await frappe.call({ method: PP_API + ".periode_du_graphe" })).message || {};
      this.mode = p.mode === "annee" ? "annee" : "mois";
      this.mois = p.mois || null;
      this.annee = p.annee || null;
    } catch (e) { /* période par défaut : le mois en cours */ }
    this.charger();
  }

  args() {
    return { mode: this.mode, mois: this.mode === "mois" ? this.mois : null, annee: this.mode === "annee" ? this.annee : null };
  }

  async charger() {
    const r = await frappe.call({ method: PP_API + ".get_pnl", args: this.args(), freeze: !this.data, freeze_message: "Calcul…" });
    this.data = r.message;
    if (this.mode === "mois") this.mois = this.data.periode.libelle;
    this.rendre();
    this.chargerOmissions();
  }

  rendre() {
    const d = this.data, c = d.calcul, r = this.$root;
    const total = this.mode === "annee" && this.annee === "Total";
    r.find("#pp-mode span").each((_, el) => {
      const m = $(el).attr("data-mode");
      $(el).toggleClass("on", total ? m === "total" : m === this.mode);
    });
    const opts = this.mode === "mois" ? d.options.mois : d.options.annees.filter((a) => a !== "Total");
    r.find("#pp-choix").toggle(!total).html(opts.map((o) => `<option ${o === d.periode.libelle ? "selected" : ""}>${pp_esc(o)}</option>`).join(""));
    r.find("#pp-titre").text(`${frappe.datetime.str_to_user(d.periode.debut)} → ${frappe.datetime.str_to_user(d.periode.fin)}`);
    const carte = (b) => {
      const omis = c[b.bloc + "_omis"];
      return `<div class="pp-kpi ${b.bloc}" data-bloc="${b.bloc}">
        <div class="l">${pp_esc(b.titre)}</div><div class="v">${pp_dt(c[b.bloc])}</div>
        <div class="s ${omis ? "omis" : ""}">${omis ? `dont ${pp_dt(omis)} omis · compté ${pp_dt(c[b.bloc + "_brut"])}` : "voir les pièces ›"}</div></div>`;
    };
    r.find("#pp-kpis").html(PP_BLOCS.map(carte).join("") + `
      <div class="pp-kpi benefice ${c.benefice < 0 ? "perte" : ""}">
        <div class="l">${c.benefice < 0 ? "Perte" : "Bénéfice"}</div><div class="v">${pp_dt(c.benefice)}</div>
        <div class="s">ventes − stock − dépenses${c.nb_omis ? ` · ${c.nb_omis} pièce(s) omise(s)` : ""}</div></div>`);
    r.find(".pp-kpi[data-bloc]").on("click", (e) => this.detail($(e.currentTarget).attr("data-bloc")));
  }

  // ── Détail d'un total ──────────────────────────────────────────────────────
  async detail(bloc) {
    const titre = PP_BLOCS.find((b) => b.bloc === bloc).titre;
    const d = new frappe.ui.Dialog({ title: `${titre} — ${this.data.periode.libelle}`, size: "extra-large",
                                     fields: [{ fieldtype: "HTML", fieldname: "zone" }] });
    const $w = d.fields_dict.zone.$wrapper.html(`<div class="pp-vide">Chargement…</div>`);
    d.show();
    let filtre = "";
    const ouverts = new Set();
    const charger = async () => {
      this.det = (await frappe.call({ method: PP_API + ".get_detail",
        args: Object.assign({ bloc, recherche: filtre || null }, this.args()) })).message;
      peindre();
    };
    const peindre = () => {
      const det = this.det;
      $w.html(`
        <div class="pp-entete-detail">
          <span>Compté : <b>${pp_dt(det.total, 3)}</b></span>
          ${det.omis ? `<span style="color:#c2410c">Omis : <b>${pp_dt(det.omis, 3)}</b></span><span>Retenu : <b>${pp_dt(det.net, 3)}</b></span>` : ""}
        </div>
        <input type="search" class="form-control" id="pp-filtre" placeholder="🔎 Chercher une pièce (n°, nom, date AAAA-MM-JJ)…" value="${pp_esc(filtre)}" style="margin-bottom:8px">
        ${(det.comptes || []).map((cpt, ci) => `
          <details class="pp-compte" data-ci="${ci}" ${ouverts.has(cpt.compte) || filtre || det.comptes.length === 1 ? "open" : ""}>
            <summary><span class="nom">${pp_esc(cpt.compte)} <span class="sub" style="font-weight:400;color:#94a3b8">· ${cpt.nb} pièce(s)</span></span>
              ${cpt.omis ? `<span class="pp-badge">−${pp_dt(cpt.omis)}</span>` : ""}<span class="tot">${pp_dt(cpt.total)}</span></summary>
            <div class="corps">${cpt.pieces.map((p) => this.lignePiece(p, det.peut_omettre)).join("")}
              ${cpt.reste && cpt.reste.n ? `<div class="pp-aide">… et ${cpt.reste.n} autre(s) pièce(s), ${pp_dt(cpt.reste.montant)} — cherchez-les ci-dessus.</div>` : ""}</div>
          </details>`).join("") || `<div class="pp-vide">Aucune pièce sur la période.</div>`}`);
      $w.find("details.pp-compte").on("toggle", (e) => {
        const nom = det.comptes[Number($(e.currentTarget).attr("data-ci"))].compte;
        if (e.currentTarget.open) ouverts.add(nom); else ouverts.delete(nom);
      });
      let minuteur;
      $w.find("#pp-filtre").on("input", (e) => {
        clearTimeout(minuteur);
        minuteur = setTimeout(() => { filtre = $(e.target).val().trim(); charger(); }, 350);
      });
      const apres = async () => { await charger(); this.charger(); };
      $w.find("[data-omettre]").on("click", (e) => {
        const $b = $(e.currentTarget);
        this.omettre(bloc, $b.attr("data-vt"), $b.attr("data-vn"), Number($b.attr("data-contrib")), apres);
      });
      $w.find("[data-retablir]").on("click", (e) => this.retablir($(e.currentTarget).attr("data-retablir"), apres));
    };
    await charger();
  }

  lignePiece(p, peut) {
    const url = `/app/${frappe.router.slug(p.voucher_type)}/${encodeURIComponent(p.voucher_no)}`;
    const o = p.omission;
    const classe = o ? (o.mode === "Totale" ? "omise" : "partielle") : "";
    const badge = o ? `<span class="pp-badge" title="${pp_esc(o.motif)}">${o.mode === "Totale" ? "omise" : "omis " + pp_dt(o.montant)}</span>` : "";
    const actions = !peut ? "" : `<div class="act">${o
      ? `<button class="btn btn-xs btn-default" data-retablir="${pp_esc(o.name)}">↩️ Rétablir</button><span class="sub">${pp_esc(o.motif)}</span>`
      : `<button class="btn btn-xs btn-default" data-omettre data-vt="${pp_esc(p.voucher_type)}" data-vn="${pp_esc(p.voucher_no)}" data-contrib="${p.contribution}">🚫 Omettre</button>`}</div>`;
    return `<div class="pp-ligne ${classe}">
      <div class="lib"><a href="${url}" target="_blank">${pp_esc(p.voucher_no)}</a> ${badge}
        <div class="sub">${pp_esc(frappe.datetime.str_to_user(p.date))}${p.libelle ? " · " + pp_esc(p.libelle) : ""}</div></div>
      <div class="mt">${pp_dt(p.montant, 3)}</div>${actions}</div>`;
  }

  omettre(bloc, voucher_type, voucher_no, contribution, apres) {
    const titre = PP_BLOCS.find((b) => b.bloc === bloc).titre;
    const d = new frappe.ui.Dialog({
      title: `Omettre ${voucher_no} de Pertes et Profits`,
      fields: [
        { fieldtype: "HTML", fieldname: "info", options: `<div class="pp-aide" style="margin-bottom:6px">Compté dans « ${pp_esc(titre)} » : <b>${pp_dt(Math.abs(contribution), 3)}</b>. La situation mensuelle n'est pas touchée.</div>` },
        { fieldtype: "Select", fieldname: "mode", label: "Omettre", options: [{ value: "Totale", label: "Toute la pièce" }, { value: "Partielle", label: "Une partie seulement" }], default: "Totale" },
        { fieldtype: "Currency", fieldname: "montant", label: "Montant à omettre", depends_on: "eval:doc.mode=='Partielle'",
          description: `Entre 0 et ${pp_dt(Math.abs(contribution), 3)}` },
        { fieldtype: "Small Text", fieldname: "motif", label: "Motif", reqd: 1, description: "Pourquoi cette pièce ne doit pas compter (visible de tous)." },
      ],
      primary_action_label: "Omettre de Pertes et Profits",
      primary_action: async (v) => {
        await frappe.call({ method: PP_API + ".omettre", args: { bloc, voucher_type, voucher_no, mode: v.mode, montant: v.montant, motif: v.motif } });
        d.hide();
        frappe.show_alert({ message: `${voucher_no} omise de Pertes et Profits`, indicator: "orange" }, 4);
        apres && apres();
      },
    });
    d.show();
  }

  retablir(nom, apres) {
    frappe.confirm("Compter de nouveau cette pièce dans Pertes et Profits ?", async () => {
      await frappe.call({ method: PP_API + ".retablir", args: { name: nom } });
      apres ? apres() : this.charger();
    });
  }

  // ── Omissions de la période ────────────────────────────────────────────────
  async chargerOmissions() {
    const r = (await frappe.call({ method: PP_API + ".get_omissions", args: this.args() })).message;
    const liste = r.omissions || [];
    const total = liste.reduce((s, o) => s + o.montant, 0);
    const $o = this.$root.find("#pp-omissions");
    $o.html(`<div class="pp-omis-tete">🚫 Pièces omises de Pertes et Profits <span class="n">${liste.length ? `${liste.length} · ${pp_dt(total)}` : ""}</span></div>
      ${liste.map((o) => `<div class="pp-ligne">
        <div class="lib"><a href="/app/${frappe.router.slug(o.voucher_type)}/${encodeURIComponent(o.voucher_no)}" target="_blank">${pp_esc(o.voucher_no)}</a>
          <span class="pp-badge">${pp_esc(o.titre)}${o.mode === "Partielle" ? " · partielle" : ""}</span>
          <div class="sub">${pp_esc(frappe.datetime.str_to_user(o.date_piece))} · ${pp_esc(o.motif)} — ${pp_esc(o.par)}</div></div>
        <div class="mt">${pp_dt(o.montant, 3)}</div>
        ${r.peut_omettre ? `<div class="act"><button class="btn btn-xs btn-default" data-retablir="${pp_esc(o.name)}">↩️ Rétablir</button></div>` : ""}
      </div>`).join("") || `<div class="pp-aide">Rien d'omis sur la période : tout compte. Touchez un total, puis « Omettre » sur une pièce.</div>`}`);
    $o.find("[data-retablir]").on("click", (e) => this.retablir($(e.currentTarget).attr("data-retablir")));
  }
}
