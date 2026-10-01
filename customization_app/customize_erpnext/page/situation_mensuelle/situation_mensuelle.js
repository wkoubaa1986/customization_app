/**
 * « Situation mensuelle » — le graphe interactif et son détail.
 *
 * Même calcul que l'ancien rapport « Test » (customization_app.situation_mensuelle). Ici :
 * 12 mois en barres (touchez un mois pour l'afficher), 5 cartes (touchez-en une pour voir ce qui
 * a été compté, compte par compte puis pièce par pièce), et sur chaque pièce de dépense
 * « Omettre » (en tout ou en partie, avec motif) ou « Rétablir ». Pensé pour le téléphone.
 */

frappe.pages["situation-mensuelle"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Situation mensuelle", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("situation_mensuelle", {}));
  wrapper.sm = new SituationMensuelle(wrapper);
};
frappe.pages["situation-mensuelle"].on_page_show = function (wrapper) {
  // Fil d'Ariane : on vient de l'onglet Comptabilité (sinon Frappe affiche le dernier onglet visité).
  frappe.breadcrumbs.add({ type: "Custom", label: __("Accounting"), route: "/app/accounting" });
  if (wrapper.sm && wrapper.sm.data) {
    wrapper.sm.appliquerRoute();
    wrapper.sm.charger();
  }
};

const SM_API = "customization_app.situation_mensuelle";
const sm_esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
const sm_dt = (v, dec = 0) => format_currency(v || 0, "TND", dec);
const SM_IND = [
  { cle: "ventes", titre: "Ventes" }, { cle: "cout", titre: "Coût marchandise" }, { cle: "charges", titre: "Charges" },
  { cle: "tva", titre: "TVA achat" }, { cle: "benefice", titre: "Bénéfice" },
  { cle: "marge", titre: "Marge %", pct: true }, { cle: "marge_brute", titre: "Marge brute %", pct: true },
];
const SM_COULEURS_ANNEES = ["#94a3b8", "#f59e0b", "#2563eb", "#16a34a", "#9333ea", "#dc2626"];
const sm_pct = (v) => (v == null ? "—" : `${(Math.round(v * 10) / 10).toLocaleString("fr-FR")} %`);
const SM_CARTES = [
  { cle: "ventes", rubrique: "Ventes", titre: "Ventes livrées" },
  { cle: "cout", rubrique: "Coût de la marchandise", titre: "Coût marchandise" },
  { cle: "charges", rubrique: "Charges", titre: "Charges" },
  { cle: "tva", rubrique: "TVA Achat", titre: "TVA achat" },
];

class SituationMensuelle {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".sm-page");
    this.mode = "mois";
    this.mois = null;
    this.annee = null;
    this.appliquerRoute();
    this.$root.find("#sm-mode span").on("click", (e) => {
      this.mode = $(e.currentTarget).attr("data-mode");
      if (this.mode === "total") { this.mode = "annee"; this.annee = "Total"; }
      else if (this.mode === "annee" && (!this.annee || this.annee === "Total")) this.annee = String(new Date().getFullYear());
      this.charger();
    });
    this.indicateur = "ventes";
    this.anneesMasquees = new Set();
    this.chargerComparaison();
    this.$root.find("#sm-choix").on("change", (e) => {
      if (this.mode === "mois") this.mois = $(e.target).val(); else this.annee = $(e.target).val();
      this.charger();
    });
    this.charger();
  }

  /** Période transmise par le graphe de l'onglet (clic) : mois, année ou total. */
  appliquerRoute() {
    const p = (frappe.route_options || {}).sm_periode;
    if (!p) return;
    frappe.route_options = null;
    this.mode = p.mode === "annee" ? "annee" : "mois";
    this.mois = p.mois || null;
    this.annee = p.annee || null;
  }

  args() {
    return { mode: this.mode, mois: this.mode === "mois" ? this.mois : null, annee: this.mode === "annee" ? this.annee : null };
  }

  async charger() {
    const r = await frappe.call({ method: SM_API + ".get_situation", args: this.args(), freeze: !this.data, freeze_message: "Calcul…" });
    this.data = r.message;
    if (this.mode === "mois") this.mois = this.data.periode.libelle;
    if (this.comparaisonPerimee) { this.comparaisonPerimee = false; this.chargerComparaison(); }
    this.rendre();
    this.chargerOmissions();
  }

  rendre() {
    const d = this.data, c = d.calcul, r = this.$root;
    const total = this.mode === "annee" && this.annee === "Total";
    r.find("#sm-mode span").each((_, el) => {
      const m = $(el).attr("data-mode");
      $(el).toggleClass("on", total ? m === "total" : m === this.mode);
    });
    const opts = this.mode === "mois" ? d.options.mois : d.options.annees.filter((a) => a !== "Total");
    r.find("#sm-choix").toggle(!total).html(opts.map((o) => `<option ${o === d.periode.libelle ? "selected" : ""}>${sm_esc(o)}</option>`).join(""));
    r.find("#sm-titre").text(`${frappe.datetime.str_to_user(d.periode.debut)} → ${frappe.datetime.str_to_user(d.periode.fin)}`);

    const carte = (k) => {
      const v = k.cle === "ventes" ? c.ventes : c[k.cle].net;
      const omis = k.cle === "ventes" ? 0 : c[k.cle].omis;
      return `<div class="sm-kpi ${k.cle}" data-rubrique="${sm_esc(k.rubrique)}">
        <div class="l">${sm_esc(k.titre)}</div><div class="v">${sm_dt(v)}</div>
        <div class="s ${omis ? "omis" : ""}">${omis ? `dont ${sm_dt(omis)} omis` : "voir le détail ›"}</div></div>`;
    };
    const extra = [c.initial ? `bénéfice initial ${sm_dt(c.initial)}` : "", c.ajustement ? `ajustements ${sm_dt(c.ajustement)}` : ""].filter(Boolean).join(" · ");
    r.find("#sm-kpis").html(SM_CARTES.map(carte).join("") + `
      <div class="sm-kpi benefice ${c.benefice < 0 ? "perte" : ""}" data-rubrique="Bénéfice">
        <div class="l">${c.benefice < 0 ? "Perte" : "Bénéfice"}</div><div class="v">${sm_dt(c.benefice)}</div>
        <div class="s">${sm_esc(extra || "ventes − coût − charges − TVA achat")}</div></div>
      <div class="sm-kpi ratio marge ${c.marge != null && c.marge < 0 ? "perte" : ""}">
        <div class="l">Marge</div><div class="v">${sm_pct(c.marge)}</div>
        <div class="s">bénéfice / ventes TTC</div></div>
      <div class="sm-kpi ratio marge_brute">
        <div class="l">Marge brute</div><div class="v">${sm_pct(c.marge_brute)}</div>
        <div class="s">(ventes − coût) / ventes TTC · coût / ventes ${sm_pct(c.cout_ventes)}</div></div>`);
    r.find(".sm-kpi[data-rubrique]").on("click", (e) => {
      const rub = $(e.currentTarget).attr("data-rubrique");
      if (rub === "Bénéfice") return this.expliquerBenefice();
      this.detail(rub);
    });
    this.graphe();
  }

  graphe() {
    const s = this.data.serie || [];
    // Un conteneur NEUF à chaque dessin : l'écouteur « data-select » posé sur l'ancien gardait sa
    // série et, au redessin suivant, envoyait vers le mauvais mois (constaté : septembre → août).
    const $g = $("<div>").appendTo(this.$root.find("#sm-graphe").empty());
    if (!s.length) return;
    const jeu = (nom, cle, signe = 1) => ({ name: nom, values: s.map((m) => Math.round(signe * m[cle])) });
    // Une seule couleur par série dans frappe-charts : le bénéfice et la perte sont deux séries, pour
    // qu'un mois en perte sorte en ROUGE (comme la carte) et pas en vert vers le bas.
    const benef = { name: "Bénéfice", values: s.map((m) => Math.round(Math.max(0, m.benefice))) };
    const perte = { name: "Perte", values: s.map((m) => Math.round(Math.min(0, m.benefice))) };
    this.chart = new frappe.Chart($g[0], {
      type: "bar", height: 280,
      data: { labels: s.map((m) => m.court), datasets: [jeu("Ventes", "ventes"), jeu("Coût marchandise", "cout", -1),
        jeu("Charges", "charges", -1), jeu("TVA achat", "tva", -1), benef, perte] },
      colors: ["#2563eb", "#f59e0b", "#ef4444", "#ec4899", "#22c55e", "#7f1d1d"],
      barOptions: { spaceRatio: 0.3 },
      axisOptions: { xIsSeries: true },
      tooltipOptions: { formatTooltipY: (v) => sm_dt(v) },
    });
    // Le mode « isNavigable » de frappe-charts est fragile (repères en erreur avec des barres
    // négatives, clic parfois ignoré) : on lit nous-mêmes l'index du mois sur la barre touchée.
    $g.on("click", "[data-point-index]", (e) => {
      const m = s[Number($(e.currentTarget).attr("data-point-index"))];
      if (!m || (this.mode === "mois" && m.mois === this.mois)) return;
      this.mode = "mois"; this.mois = m.mois;
      this.charger();
    });
    $g.css("cursor", "pointer");
  }

  expliquerBenefice() {
    const c = this.data.calcul;
    const ligne = (l, v, signe) => `<div class="sm-ligne"><div class="lib">${sm_esc(l)}</div><div class="mt">${signe}${sm_dt(Math.abs(v), 3)}</div></div>`;
    frappe.msgprint({ title: `${c.benefice < 0 ? "Perte" : "Bénéfice"} — ${sm_esc(this.data.periode.libelle)}`, message:
      ligne("Ventes livrées (BL, TTC)", c.ventes, "+ ") + ligne("Coût de la marchandise", c.cout.net, "− ")
      + ligne("Charges", c.charges.net, "− ") + ligne("TVA achat", c.tva.net, "− ")
      + (c.initial ? ligne("Bénéfice initial", c.initial, "+ ") : "") + (c.ajustement ? ligne("Ajustements d’ouverture", c.ajustement, "+ ") : "")
      + `<div class="sm-ligne"><div class="lib"><b>${c.benefice < 0 ? "Perte" : "Bénéfice"}</b></div><div class="mt">${sm_dt(c.benefice, 3)}</div></div>`
      + ((c.cout.omis + c.charges.omis + c.tva.omis) ? `<div class="sm-aide" style="margin-top:6px">Dépenses omises du calcul : ${sm_dt(c.cout.omis + c.charges.omis + c.tva.omis, 3)}</div>` : "") });
  }

  // ── Détail d'une rubrique ──────────────────────────────────────────────────
  async detail(rubrique) {
    const d = new frappe.ui.Dialog({ title: `${rubrique} — ${this.data.periode.libelle}`, size: "extra-large",
                                     fields: [{ fieldtype: "HTML", fieldname: "zone" }] });
    const $w = d.fields_dict.zone.$wrapper.html(`<div class="sm-vide">Chargement…</div>`);
    d.show();
    let filtre = "", ouverts = new Set();
    // La recherche se fait côté serveur : la liste affichée est limitée aux plus grosses pièces de
    // chaque compte (vue « Total » : plus de 10 000 pièces), la recherche retrouve toutes les autres.
    const charger = async () => {
      this.det = (await frappe.call({ method: SM_API + ".get_detail",
        args: Object.assign({ rubrique, recherche: filtre || null }, this.args()) })).message;
      peindre();
    };
    const peindre = () => {
      const det = this.det, q = filtre.toLowerCase();
      const garde = () => true;
      $w.html(`
        <div class="sm-entete-detail">
          <span>Compté : <b>${sm_dt(det.total, 3)}</b></span>
          ${det.omis ? `<span style="color:#c2410c">Omis : <b>${sm_dt(det.omis, 3)}</b></span><span>Retenu : <b>${sm_dt(det.net, 3)}</b></span>` : ""}
        </div>
        <input type="search" class="form-control" id="sm-filtre" placeholder="🔎 Chercher une pièce (n°, nom, date AAAA-MM-JJ)…" value="${sm_esc(filtre)}" style="margin-bottom:8px">
        ${(det.comptes || []).map((cpt, ci) => {
          const lignes = cpt.pieces.filter(garde);
          if (!lignes.length) return "";
          return `<details class="sm-compte" data-ci="${ci}" ${ouverts.has(ci) || q || det.comptes.length === 1 ? "open" : ""}>
            <summary><span class="nom">${sm_esc(cpt.compte)}</span><span class="sub" style="font-size:11.5px;color:#64748b">${cpt.nb} pièce(s)</span>
              <span class="tot">${sm_dt(cpt.total)}</span></summary>
            <div class="corps">${lignes.map((p) => this.lignePiece(p, rubrique, det.peut_omettre)).join("")}
              ${cpt.reste && cpt.reste.n ? `<div class="sm-aide" style="padding:8px 0">… et ${cpt.reste.n} autre(s) pièce(s) plus petite(s), ${sm_dt(cpt.reste.montant)} au total —
                tapez un n° de pièce, un nom ou une date dans la recherche pour les retrouver.</div>` : ""}</div></details>`;
        }).join("") || `<div class="sm-vide">${q ? "Aucune pièce ne correspond à la recherche." : "Rien de compté sur la période."}</div>`}`);
      $w.find("details.sm-compte").on("toggle", (e) => {
        const ci = Number($(e.currentTarget).attr("data-ci"));
        e.currentTarget.open ? ouverts.add(ci) : ouverts.delete(ci);
      });
      let t = null;
      $w.find("#sm-filtre").on("input", (e) => {
        clearTimeout(t);
        t = setTimeout(async () => {
          filtre = $(e.target).val();
          await charger();
          const $f = $w.find("#sm-filtre").focus();
          $f[0] && $f[0].setSelectionRange(filtre.length, filtre.length);
        }, 400);
      });
      $w.find("[data-omettre]").on("click", (e) => {
        const $b = $(e.currentTarget);
        this.omettre(rubrique, $b.attr("data-vt"), $b.attr("data-vn"), Number($b.attr("data-contrib")), async () => { await charger(); this.charger(); });
      });
      $w.find("[data-retablir]").on("click", (e) => {
        const nom = $(e.currentTarget).attr("data-retablir");
        frappe.confirm("Rétablir cette pièce dans le calcul ?", async () => {
          await frappe.call({ method: SM_API + ".retablir", args: { name: nom } });
          this.comparaisonPerimee = true;
          await charger(); this.charger();
        });
      });
    };
    await charger();
  }

  lignePiece(p, rubrique, peut) {
    const url = `/app/${frappe.router.slug(p.voucher_type)}/${encodeURIComponent(p.voucher_no)}`;
    const o = p.omission;
    const classe = o ? (o.mode === "Totale" ? "omise" : "partielle") : "";
    const badge = o ? `<span class="sm-badge omis" title="${sm_esc(o.motif)}">${o.mode === "Totale" ? "omise" : "omis " + sm_dt(o.montant)}</span>` : "";
    const actions = rubrique === "Ventes" || !peut ? "" : `<div class="act">${o
      ? `<button class="btn btn-xs btn-default" data-retablir="${sm_esc(o.name)}">↩️ Rétablir</button><span class="sub" style="font-size:11.5px;color:#64748b">${sm_esc(o.motif)}</span>`
      : `<button class="btn btn-xs btn-default" data-omettre data-vt="${sm_esc(p.voucher_type)}" data-vn="${sm_esc(p.voucher_no)}" data-contrib="${p.contribution}">🚫 Omettre</button>`}</div>`;
    return `<div class="sm-ligne ${classe}">
      <div class="lib"><a href="${url}" target="_blank">${sm_esc(p.voucher_no)}</a> ${badge}
        <div class="sub">${sm_esc(frappe.datetime.str_to_user(p.date))}${p.libelle ? " · " + sm_esc(p.libelle) : ""}</div></div>
      <div class="mt">${sm_dt(p.montant, 3)}</div>${actions}</div>`;
  }

  omettre(rubrique, voucher_type, voucher_no, contribution, apres) {
    const d = new frappe.ui.Dialog({
      title: `Omettre ${voucher_no}`,
      fields: [
        { fieldtype: "HTML", fieldname: "info", options: `<div class="sm-aide" style="margin-bottom:6px">Compté dans « ${sm_esc(rubrique)} » : <b>${sm_dt(Math.abs(contribution), 3)}</b></div>` },
        { fieldtype: "Select", fieldname: "mode", label: "Omettre", options: [{ value: "Totale", label: "Toute la dépense" }, { value: "Partielle", label: "Une partie seulement" }], default: "Totale" },
        { fieldtype: "Currency", fieldname: "montant", label: "Montant à omettre", depends_on: "eval:doc.mode=='Partielle'",
          description: `Entre 0 et ${sm_dt(Math.abs(contribution), 3)}` },
        { fieldtype: "Small Text", fieldname: "motif", label: "Motif", reqd: 1, description: "Pourquoi cette dépense ne doit pas compter (visible de tous)." },
      ],
      primary_action_label: "Omettre du calcul",
      primary_action: async (v) => {
        await frappe.call({ method: SM_API + ".omettre", args: { rubrique, voucher_type, voucher_no, mode: v.mode, montant: v.montant, motif: v.motif } });
        this.comparaisonPerimee = true;
        d.hide();
        frappe.show_alert({ message: `${voucher_no} omise du calcul`, indicator: "orange" }, 4);
        apres && apres();
      },
    });
    d.show();
  }

  // ── Comparer les années ────────────────────────────────────────────────────
  async chargerComparaison() {
    if (!this._redim) {
      let t = null;
      this._redim = () => { clearTimeout(t); t = setTimeout(() => this.peindreComparaison(), 250); };
      $(window).on("resize", this._redim);
    }
    const r = await frappe.call({ method: SM_API + ".get_comparaison", freeze: false });
    this.comp = r.message;
    this.peindreComparaison();
  }

  peindreComparaison() {
    const r = this.$root, comp = this.comp;
    if (!comp) return;
    r.find("#sm-ind").html(SM_IND.map((i) => `<span class="${i.cle === this.indicateur ? "on" : ""}" data-ind="${i.cle}">${sm_esc(i.titre)}</span>`).join(""));
    r.find("#sm-ind span").on("click", (e) => { this.indicateur = $(e.currentTarget).attr("data-ind"); this.moisBulle = null; this.peindreComparaison(); });
    const ind = SM_IND.find((i) => i.cle === this.indicateur);
    const fmt = (v) => (v == null ? "—" : ind.pct ? sm_pct(v) : sm_dt(v));
    const annees = comp.annees.map((a, k) => ({ annee: a.annee, couleur: SM_COULEURS_ANNEES[k % SM_COULEURS_ANNEES.length],
      valeurs: a.mois.map((m) => (m ? m[this.indicateur] : null)) }));
    // Dessin à la taille réelle du cadre (texte à 11 px partout) ; sur téléphone, 600 px au moins et
    // défilement horizontal plutôt qu'un graphe réduit illisible.
    const largeur = Math.max(Math.round(r.find("#sm-courbes").width() || 760), 600);
    r.find("#sm-courbes").html(this.svgCourbes(annees.filter((a) => !this.anneesMasquees.has(a.annee)), comp.mois, ind.pct, largeur));
    r.find("#sm-courbes .colonne").on("click", (e) => { this.moisBulle = Number($(e.currentTarget).attr("data-m")); this.peindreComparaison(); });
    r.find("#sm-legende").html(annees.map((a) => `<span class="${this.anneesMasquees.has(a.annee) ? "off" : ""}" data-an="${a.annee}">
      <i style="background:${a.couleur}"></i>${a.annee}</span>`).join(""));
    r.find("#sm-legende span").on("click", (e) => {
      const an = $(e.currentTarget).attr("data-an");
      this.anneesMasquees.has(an) ? this.anneesMasquees.delete(an) : this.anneesMasquees.add(an);
      this.peindreComparaison();
    });
    const m = this.moisBulle;
    r.find("#sm-bulle").html(m == null ? `<div class="sm-aide" style="margin-top:6px">Touchez un mois pour comparer ses valeurs d’une année à l’autre.</div>`
      : `<div class="sm-bulle"><b>${sm_esc(comp.mois[m])}</b> ${annees.map((a) => `<span style="margin-right:12px;white-space:nowrap">
          <i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${a.couleur}"></i> ${a.annee} : <b>${fmt(a.valeurs[m])}</b></span>`).join("")}</div>`);
    // Totaux par année (même logique que la vue « Année »).
    const cols = SM_IND;
    r.find("#sm-table").html(`<table class="sm-annees"><thead><tr><th>Année</th>${cols.map((c) =>
        `<th class="${c.cle === this.indicateur ? "on" : ""}">${sm_esc(c.titre)}</th>`).join("")}</tr></thead><tbody>
      ${comp.annees.map((a, k) => {
        const prec = k ? comp.annees[k - 1].total : null;
        return `<tr><td><b>${a.annee}</b>${a.partielle ? `<div class="sm-aide">${sm_esc(frappe.datetime.str_to_user(a.debut))} → ${sm_esc(frappe.datetime.str_to_user(a.fin))}</div>` : ""}
          ${a.initial ? `<div class="sm-aide">dont bénéfice initial ${sm_dt(a.initial)}</div>` : ""}</td>
          ${cols.map((c) => {
            const v = a.total[c.cle];
            let evol = "";
            if (prec && prec[c.cle] && v != null && !c.pct) {
              const e = (100 * (v - prec[c.cle])) / Math.abs(prec[c.cle]);
              evol = `<span class="sm-evol ${e >= 0 ? "up" : "down"}">${e >= 0 ? "▲" : "▼"} ${Math.abs(Math.round(e))} %</span>`;
            } else if (prec && prec[c.cle] != null && v != null && c.pct) {
              const e = v - prec[c.cle];
              evol = `<span class="sm-evol ${e >= 0 ? "up" : "down"}">${e >= 0 ? "+" : "−"}${Math.abs(Math.round(e * 10) / 10)} pt</span>`;
            }
            // <bdi> : le symbole « د.ت » s'écrit de droite à gauche et entraînait l'évolution dans son sens.
            return `<td class="${c.cle === this.indicateur ? "on" : ""} ${v != null && v < 0 ? "neg" : ""}"><bdi>${c.pct ? sm_pct(v) : sm_dt(v)}</bdi>${evol}</td>`;
          }).join("")}</tr>`;
      }).join("")}</tbody></table>
      <div class="sm-aide" style="margin-top:6px">Évolution par rapport à l’année précédente. L’année en cours s’arrête à aujourd’hui ; 2023 commence avec le bénéfice initial, comme dans la vue « Année ».</div>`);
  }

  /** Courbes d'une année par série, mois en abscisse. Dessinées à la main (SVG) : frappe-charts
   *  trace un mois sans donnée à ZÉRO, ce qui ferait plonger l'année en cours en novembre-décembre ;
   *  ici la courbe s'arrête simplement. */
  svgCourbes(annees, mois, pct, L = 760) {
    const H = 260, g = 58, d = 12, h = 14, b = 30;
    const tous = annees.flatMap((a) => a.valeurs).filter((v) => v != null);
    if (!tous.length) return `<div class="sm-vide">Aucune donnée.</div>`;
    let min = Math.min(0, ...tous), max = Math.max(0, ...tous);
    if (min === max) max = min + 1;
    const pas = Math.pow(10, Math.floor(Math.log10((max - min) / 4 || 1)));
    const cran = [1, 2, 2.5, 5, 10].map((k) => k * pas).find((k) => (max - min) / k <= 5) || 10 * pas;
    min = Math.floor(min / cran) * cran; max = Math.ceil(max / cran) * cran;
    const X = (i) => g + (i + 0.5) * ((L - g - d) / 12);
    const Y = (v) => h + (H - h - b) * (1 - (v - min) / (max - min));
    const lbl = (v) => (pct ? `${Math.round(v)} %` : Math.abs(v) >= 1000 ? `${Math.round(v / 1000)} k` : String(Math.round(v)));
    let svg = `<svg width="${L}" height="${H}" viewBox="0 0 ${L} ${H}" role="img">`;
    for (let v = min; v <= max + 1e-9; v += cran) {
      svg += `<line class="${Math.abs(v) < 1e-9 ? "zero" : "grille"}" x1="${g}" x2="${L - d}" y1="${Y(v)}" y2="${Y(v)}"/>
              <text class="axe" x="${g - 6}" y="${Y(v) + 4}" text-anchor="end">${lbl(v)}</text>`;
    }
    mois.forEach((m, i) => {
      svg += `<rect class="colonne ${this.moisBulle === i ? "on" : ""}" data-m="${i}" x="${X(i) - (L - g - d) / 24}" y="${h}" width="${(L - g - d) / 12}" height="${H - h - b}"/>
              <text class="axe" x="${X(i)}" y="${H - 10}" text-anchor="middle">${sm_esc(m)}</text>`;
    });
    annees.forEach((a) => {
      let chemin = "", ouvert = false;
      a.valeurs.forEach((v, i) => {
        if (v == null) { ouvert = false; return; }
        chemin += `${ouvert ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`;
        ouvert = true;
      });
      svg += `<path d="${chemin}" fill="none" stroke="${a.couleur}" stroke-width="2.5" stroke-linejoin="round" pointer-events="none"/>`;
      a.valeurs.forEach((v, i) => { if (v != null) svg += `<circle cx="${X(i)}" cy="${Y(v)}" r="3.5" fill="${a.couleur}" pointer-events="none"/>`; });
    });
    return svg + "</svg>";
  }

  // ── Omissions de la période ────────────────────────────────────────────────
  async chargerOmissions() {
    const r = (await frappe.call({ method: SM_API + ".get_omissions", args: this.args() })).message;
    const liste = r.omissions || [];
    const total = liste.reduce((s, o) => s + (o.mode === "Totale" ? Math.abs(o.montant_piece || o.montant_exclu) : o.montant_exclu), 0);
    const $o = this.$root.find("#sm-omissions");
    $o.html(`<div class="sm-omis-tete">🚫 Dépenses omises du calcul <span class="n">${liste.length ? `${liste.length} · ${sm_dt(total)}` : ""}</span></div>
      ${liste.map((o) => `<div class="sm-ligne">
        <div class="lib"><a href="/app/${frappe.router.slug(o.voucher_type)}/${encodeURIComponent(o.voucher_no)}" target="_blank">${sm_esc(o.voucher_no)}</a>
          <span class="sm-badge omis">${sm_esc(o.rubrique)}${o.mode === "Partielle" ? " · partielle" : ""}</span>
          <div class="sub">${sm_esc(frappe.datetime.str_to_user(o.date_piece))} · ${sm_esc(o.motif)} — ${sm_esc(o.par)}</div></div>
        <div class="mt">${sm_dt(o.mode === "Totale" ? Math.abs(o.montant_piece || o.montant_exclu) : o.montant_exclu, 3)}</div>
        ${r.peut_omettre ? `<div class="act"><button class="btn btn-xs btn-default" data-retablir="${sm_esc(o.name)}">↩️ Rétablir</button></div>` : ""}
      </div>`).join("") || `<div class="sm-aide" style="margin-top:6px">Aucune dépense omise sur la période. Touchez une carte, puis « Omettre » sur une pièce.</div>`}`);
    $o.find("[data-retablir]").on("click", (e) => {
      const nom = $(e.currentTarget).attr("data-retablir");
      frappe.confirm("Rétablir cette pièce dans le calcul ?", async () => {
        await frappe.call({ method: SM_API + ".retablir", args: { name: nom } });
        this.comparaisonPerimee = true;
        this.charger();
      });
    });
  }
}
