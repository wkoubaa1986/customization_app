/**
 * « 💵 Où est l'argent liquide » (demande du 24/11/2026) — pour les responsables de collecte et la direction.
 * À une date : ce que tient chaque employé (fond gardé, compté pas encore remis, pas encore compté), ce que chaque
 * collecteur a reçu et pas encore versé, le compte Sabah Espèces, les versements en banque et les flux jour par jour.
 * Tout vient de customization_app.argent_liquide.situation (la méthode est expliquée dans « Comment lire cette page »).
 *
 * Version graphique (24/11/2026, « rends-la plus graphique ou interactive ») : le chemin de l'argent en 4 étapes
 * cliquables, la répartition du total (barre segmentée), une mini-barre par personne avec sa phrase d'explication au
 * clic, le calcul du collecteur posé en opération, et les 14 jours en graphique (un clic sur un jour = la situation
 * ce soir-là). Couleurs : les 5 premières teintes de la palette de référence (validées clair et sombre) ; chaque
 * montant reste écrit en toutes lettres (légende, tableau), la couleur n'est jamais seule à porter l'information.
 */
frappe.pages["argent-liquide"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: __("Où est l’argent liquide"), single_column: true });
  wrapper.al = new ArgentLiquide(wrapper);
};
frappe.pages["argent-liquide"].on_page_show = function (wrapper) {
  if (wrapper.al) wrapper.al.charger();
};

// Les cinq endroits où peut se trouver l'argent, dans l'ordre du chemin (ordre fixe = couleur fixe).
const AL_POCHES = [
  { cle: "fond", lib: "Fond gardé par l’employé", court: "Fond gardé" },
  { cle: "a_remettre", lib: "Compté, pas encore remis", court: "Compté, pas remis" },
  { cle: "non_compte", lib: "Encaissé, pas encore compté", court: "Pas encore compté" },
  { cle: "collecte", lib: "Chez le collecteur, pas encore versé", court: "Chez le collecteur" },
  { cle: "sabah", lib: "Compte Sabah Espèces", court: "Sabah Espèces" },
];

class ArgentLiquide {
  constructor(wrapper) {
    this.page = wrapper.page;
    this.$c = $(wrapper).find(".layout-main-section");
    this.voir_vides = false;
    this.poser_css();
    this.champ_date = this.page.add_field({
      fieldtype: "Date", fieldname: "date", label: __("Situation au"), default: frappe.datetime.get_today(),
      change: () => this.charger(),
    });
    this.page.set_primary_action(__("Actualiser"), () => this.charger(), "refresh");
    this.$tip = $(`<div class="al-tip" role="tooltip"></div>`).appendTo("body");
    $(window).on("resize.al", frappe.utils.debounce(() => { if (this.s) { this.dessiner_jours(); this.dessiner_flux(); } }, 200));
  }

  poser_css() {
    if (document.getElementById("al-css")) return;
    $("head").append(`<style id="al-css">
      .al { --p-fond:#2a78d6; --p-a_remettre:#eb6834; --p-non_compte:#1baf7a; --p-collecte:#eda100; --p-sabah:#e87ba4;
            --al-grille:#e1e0d9; --al-axe:#c3c2b7; max-width: 1100px; margin: 0 auto; padding-bottom: 30px; transition: opacity .2s; }
      [data-theme="dark"] .al { --p-fond:#3987e5; --p-a_remettre:#d95926; --p-non_compte:#199e70; --p-collecte:#c98500; --p-sabah:#d55181;
            --al-grille:#2c2c2a; --al-axe:#383835; }
      .al.al-charge { opacity: .45; pointer-events: none; }
      .al h4 { margin: 22px 0 4px; font-size: 15px; font-weight: 800; }
      .al .al-sous { font-size: 12.5px; color: var(--text-muted); margin-bottom: 8px; }
      .al .muet { color: var(--text-muted); font-size: 12px; }
      .al .zero { color: var(--text-light); }
      .al .n { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
      .al-aide { border: 1px solid #bfdbfe; background: #eff6ff; border-radius: 12px; padding: 10px 14px; margin: 4px 0 14px; font-size: 13px; color: #1e3a8a; }
      [data-theme="dark"] .al-aide { background: #172033; border-color: #1e3a8a; color: #c7d7f5; }
      .al-aide summary { cursor: pointer; font-weight: 700; }
      .al-aide ul { margin: 8px 0 2px; padding-left: 18px; } .al-aide li { margin: 3px 0; }
      .al-pastille { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; vertical-align: -1px; }
      /* Le chemin de l'argent */
      .al-chemin { display: flex; align-items: stretch; gap: 6px; flex-wrap: wrap; }
      .al-etape { flex: 1 1 200px; border: 1px solid var(--border-color); border-radius: 12px; padding: 10px 12px; background: var(--card-bg);
                  cursor: pointer; border-top: 4px solid var(--al-c); transition: box-shadow .15s; }
      .al-etape:hover, .al-etape:focus { box-shadow: 0 2px 10px rgba(0,0,0,.12); outline: none; }
      .al-etape .t { font-size: 12.5px; color: var(--text-muted); font-weight: 600; }
      .al-etape .v { font-size: 21px; font-weight: 800; white-space: nowrap; margin: 2px 0; }
      .al-etape .d { font-size: 12px; color: var(--text-muted); line-height: 1.35; }
      .al-fleche { display: flex; align-items: center; font-size: 20px; color: var(--text-muted); }
      .al-etape .periode { font-size: 11.5px; margin-top: 6px; font-weight: 600; color: var(--text-color); }
      /* Rapprochement avec la comptabilité */
      .al-rap { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
      .al-rap .bloc { border: 1px solid var(--border-color); border-radius: 12px; padding: 10px 14px; background: var(--card-bg); }
      .al-rap .bloc table td { border-bottom: none; padding: 3px 0; }
      .al-rap .bloc tr.res td { border-top: 1px solid var(--border-color); font-weight: 800; padding-top: 6px; }
      .al-causes td { vertical-align: top; } .al-causes .indice { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
      .al-causes tr.fort td { background: #fff7ed; } [data-theme="dark"] .al-causes tr.fort td { background: #2b1d0e; }
      .al-liens { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
      @media (max-width: 768px) { .al-rap { grid-template-columns: 1fr; } }
      /* Répartition : la barre segmentée */
      .al-barre { display: flex; gap: 2px; height: 30px; margin: 6px 0 8px; }
      .al-seg { height: 100%; min-width: 3px; cursor: default; transition: filter .1s; }
      .al-seg:first-child { border-radius: 4px 0 0 4px; } .al-seg:last-child { border-radius: 0 4px 4px 0; }
      .al-seg:only-child { border-radius: 4px; }
      .al-seg:hover, .al-seg:focus { filter: brightness(1.12); outline: none; }
      .al-legende { display: flex; flex-wrap: wrap; gap: 6px 18px; font-size: 12.5px; }
      .al-legende b { font-variant-numeric: tabular-nums; }
      .al-total { font-size: 13px; margin-top: 4px; }
      .al-alerte { background: #fef2f2; border: 1px solid #fca5a5; color: #991b1b; border-radius: 10px; padding: 9px 12px; margin: 10px 0 0; font-weight: 600; }
      [data-theme="dark"] .al-alerte { background: #3a1616; border-color: #7f1d1d; color: #fecaca; }
      /* Tableaux */
      .al table { width: 100%; border-collapse: collapse; font-size: 13px; background: var(--card-bg); }
      .al th { text-align: left; font-size: 11.5px; color: var(--text-muted); font-weight: 600; padding: 6px 8px; border-bottom: 1px solid var(--border-color); }
      .al th[title] { cursor: help; text-decoration: underline dotted; text-underline-offset: 3px; }
      .al td { padding: 6px 8px; border-bottom: 1px solid var(--border-color); vertical-align: top; }
      .al tr.al-ligne { cursor: pointer; } .al tr.al-ligne:hover td { background: var(--subtle-fg); }
      .al tr.al-phrase td { background: var(--subtle-accent); font-size: 13px; line-height: 1.45; }
      .al tr.pied td { font-weight: 800; border-top: 2px solid var(--border-color); }
      .al-mini { display: flex; gap: 2px; height: 12px; margin-top: 5px; }
      .al-mini .al-seg:first-child { border-radius: 3px 0 0 3px; } .al-mini .al-seg:last-child { border-radius: 0 3px 3px 0; }
      .al-mini .al-seg:only-child { border-radius: 3px; }
      .al-defile { overflow-x: auto; }
      .al-bouton-lien { background: none; border: none; padding: 0; color: var(--text-color); text-decoration: underline; font-size: 12.5px; cursor: pointer; }
      /* Collecteur */
      .al-coll { border: 1px solid var(--border-color); border-radius: 12px; padding: 10px 14px; margin-bottom: 10px; background: var(--card-bg); }
      .al-calcul { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin: 8px 0; }
      .al-calcul .op { font-size: 18px; font-weight: 800; color: var(--text-muted); }
      .al-calcul .bloc { border: 1px solid var(--border-color); border-radius: 10px; padding: 5px 10px; }
      .al-calcul .bloc .t { font-size: 11.5px; color: var(--text-muted); } .al-calcul .bloc .v { font-weight: 800; white-space: nowrap; }
      .al-calcul .bloc.res { border-color: var(--p-collecte); border-width: 2px; }
      .al-coll details summary { cursor: pointer; font-size: 12.5px; color: var(--text-muted); margin-top: 4px; }
      /* Ce que le collecteur principal doit avoir entre les mains (25/11/2026) */
      .al-principal { border: 2px solid var(--p-collecte); border-radius: 14px; padding: 12px 16px; margin: 14px 0 4px; background: var(--card-bg); }
      .al-principal .tete { display: flex; justify-content: space-between; gap: 12px; flex-wrap: wrap; align-items: flex-start; }
      .al-principal .tete .t { font-weight: 800; font-size: 15px; }
      .al-principal .tete .v { font-size: 24px; font-weight: 800; white-space: nowrap; font-variant-numeric: tabular-nums; }
      .al-principal-lignes { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 10px; }
      .al-principal-lignes .l { border: 1px solid var(--border-color); border-radius: 10px; padding: 8px 10px; }
      .al-principal-lignes .l .e { font-size: 12.5px; color: var(--text-muted); font-weight: 600; }
      .al-principal-lignes .l b { display: block; font-size: 17px; white-space: nowrap; font-variant-numeric: tabular-nums; margin: 2px 0; }
      @media (max-width: 768px) { .al-principal-lignes { grid-template-columns: 1fr; } }
      .al tr.vieille td { background: #fff7ed; } [data-theme="dark"] .al tr.vieille td { background: #2b1d0e; }
      .al-ok { color: var(--text-muted); font-size: 12.5px; margin-top: 6px; }
      .al-verdict { border-radius: 14px; padding: 12px 16px; margin: 6px 0 4px; border: 2px solid #86efac; background: #f0fdf4; color: #14532d; }
      .al-verdict.alerte { border-color: #fca5a5; background: #fef2f2; color: #7f1d1d; }
      .al-verdict.a_faire { border-color: #fcd34d; background: #fffbeb; color: #78350f; }
      [data-theme="dark"] .al-verdict { background: #12261a; border-color: #166534; color: #bbf7d0; }
      [data-theme="dark"] .al-verdict.alerte { background: #3a1616; border-color: #7f1d1d; color: #fecaca; }
      [data-theme="dark"] .al-verdict.a_faire { background: #33270d; border-color: #92400e; color: #fde68a; }
      .al-verdict .t { font-weight: 800; font-size: 17px; }
      .al-verdict ul { margin: 8px 0 0; padding-left: 0; list-style: none; }
      .al-verdict li { margin: 5px 0; padding-left: 22px; position: relative; font-size: 13.5px; line-height: 1.45; }
      .al-verdict li::before { position: absolute; left: 0; content: "📝"; font-size: 12px; top: 1px; }
      .al-verdict li.alerte::before { content: "⚠️"; }
      .al-preuves { margin-top: 12px; border-top: 1px dashed var(--border-color); padding-top: 10px; }
      .al-preuves .t { font-weight: 700; font-size: 13.5px; margin-bottom: 4px; }
      .al-preuves .p { display: flex; gap: 8px; align-items: flex-start; flex-wrap: wrap; font-size: 13px; line-height: 1.45; padding: 5px 0; }
      .al-preuves .p .i { flex: 0 0 20px; } .al-preuves .p > div { flex: 1 1 260px; }
      .al-partie { border: 1px solid var(--border-color); border-radius: 12px; padding: 10px 12px; margin: 12px 0 0; background: var(--card-bg); }
      .al-partie .tete { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; align-items: baseline; margin-bottom: 6px; }
      .al-partie .tete .t { font-weight: 800; font-size: 15px; } .al-partie .tete .r { font-size: 13px; color: var(--text-muted); }
      .al-partie .tete .r b { color: var(--text-color); }
      .al tr.al-groupe td { background: var(--subtle-fg, #f3f4f6); font-weight: 700; font-size: 12.5px; padding-top: 5px; padding-bottom: 5px; }
      .al tr.al-groupe.vieille td { background: #fef3c7; color: #78350f; }
      .al tr.al-groupe.banque td { background: #e0f2fe; color: #075985; }
      [data-theme="dark"] .al tr.al-groupe.vieille td { background: #3a2a0b; color: #fde68a; }
      [data-theme="dark"] .al tr.al-groupe.banque td { background: #0c2a3a; color: #bae6fd; }
      .al-total-pieces { display: flex; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin-top: 10px; padding: 8px 12px; border-top: 2px solid var(--border-color); font-size: 14px; }
      .al-total-pieces b { font-variant-numeric: tabular-nums; }
      .al-detail { margin-top: 18px; } .al-detail > summary { cursor: pointer; font-weight: 700; font-size: 14px; padding: 8px 0; color: var(--text-muted); }
      /* Graphique jour par jour */
      .al-graph { position: relative; }
      .al-graph svg { display: block; width: 100%; }
      /* « al-jour » et pas « col » : Bootstrap impose width:100% à la classe .col (les colonnes se recouvraient). */
      .al-graph .al-jour { cursor: pointer; fill: transparent; }
      .al-graph .al-jour:hover, .al-graph .al-jour:focus { fill: var(--subtle-fg); outline: none; }
      .al-tip { position: fixed; z-index: 1100; pointer-events: none; display: none; background: var(--card-bg, #fff); color: var(--text-color, #111);
                border: 1px solid var(--border-color, #ddd); border-radius: 8px; padding: 7px 10px; font-size: 12.5px; box-shadow: 0 4px 14px rgba(0,0,0,.15); max-width: 320px; }
      .al-tip .l { display: flex; align-items: center; gap: 6px; white-space: nowrap; }
      .al-tip .l i { display: inline-block; width: 12px; height: 2px; }
      .al-tip .l b { font-variant-numeric: tabular-nums; }
      .al-note { font-size: 12px; color: var(--text-muted); margin-top: 18px; line-height: 1.5; }
      @media (max-width: 768px) { .al-fleche { display: none; } .al-etape .v { font-size: 18px; }
        /* Téléphone : le tableau défile de côté plutôt que d'écraser la colonne des noms. */
        .al-defile table { min-width: 660px; } .al-defile td:first-child { min-width: 160px; } }
      /* À régulariser, inventaire (25/11/2026) */
      .al-reg { border: 1px solid var(--border-color); border-left: 4px solid #d97706; border-radius: 10px; padding: 10px 14px; margin-bottom: 8px; background: var(--card-bg); }
      .al-reg .t { font-weight: 700; } .al-reg .x { font-size: 12.5px; color: var(--text-muted); margin: 3px 0 8px; line-height: 1.45; }
      .al-reg .b { display: flex; gap: 6px; flex-wrap: wrap; } .al-reg .b .btn { white-space: normal; text-align: left; }
      .al-propre { border: 1px solid #86efac; background: #f0fdf4; color: #166534; border-radius: 10px; padding: 9px 12px; font-weight: 600; }
      [data-theme="dark"] .al-propre { background: #12261a; border-color: #166534; color: #bbf7d0; }
      .al-inv { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 8px; font-size: 12.5px; }
      /* Graphique de flux : une couleur par endroit (ordre validé clair et sombre), l'extérieur en gris */
      .al { --f-especes:#2a78d6; --f-dettes:#eb6834; --f-partenaire:#1baf7a; --f-pieces:#6250d6; --f-sabah:#e87ba4; --f-banque:#008300; --f-neutre:#8b8a83; }
      [data-theme="dark"] .al { --f-especes:#3987e5; --f-dettes:#d95926; --f-partenaire:#199e70; --f-pieces:#9085e9; --f-sabah:#d55181; --f-banque:#0ca30c; --f-neutre:#8b8a83; }
      .al-flux { overflow-x: auto; } .al-flux svg { display: block; }
      .al-flux .lien { opacity: .32; transition: opacity .1s; } .al-flux .lien:hover, .al-flux .lien:focus { opacity: .62; outline: none; }
      .al-flux .noeud:focus { outline: none; }
      .al-flux text { font-size: 11.5px; fill: var(--text-color); } .al-flux text.m { fill: var(--text-muted); }
    </style>`);
  }

  // ── Bulle d'information (survol et clavier) : texte posé par textContent, jamais en HTML ──
  bulle(e, lignes) {
    const $t = this.$tip.empty();
    lignes.forEach((l) => {
      const $l = $(`<div class="l"></div>`);
      if (l.c) $l.append($("<i></i>").css("background", l.c));
      if (l.v) $l.append($("<b></b>").text(l.v));
      $l.append($("<span></span>").text(l.t || ""));
      $t.append($l);
    });
    const x = (e.clientX || (e.target.getBoundingClientRect().left + 10)) + 14;
    const y = (e.clientY || e.target.getBoundingClientRect().top) + 14;
    $t.css({ display: "block", left: Math.min(x, window.innerWidth - 330), top: Math.min(y, window.innerHeight - 120) });
  }
  cacher_bulle() { this.$tip.hide(); }
  brancher_bulles($zone) {
    const self = this;
    $zone.find("[data-bulle]").each(function () {
      const lignes = JSON.parse(this.getAttribute("data-bulle"));
      $(this).attr("tabindex", "0")
        .on("pointermove focus", (e) => self.bulle(e, lignes))
        .on("pointerleave blur", () => self.cacher_bulle());
    });
  }

  charger() {
    const date = this.champ_date.get_value() || frappe.datetime.get_today();
    const $al = this.$c.find(".al");
    if ($al.length) $al.addClass("al-charge");         // on garde l'image précédente pendant le calcul
    else this.$c.html(`<div class="al muet" style="padding:20px">${__("Calcul…")}</div>`);
    frappe.call({ method: "customization_app.argent_liquide.situation", args: { date },
                  callback: (r) => { this.s = r.message || {}; this.rendre(); } });
  }

  // La couleur d'une poche (variable CSS, claire ou sombre selon le thème), lue sur un témoin invisible : la page
  // n'existe pas encore quand ses bulles se préparent.
  couleur(cle) {
    if (!this.$temoin || !document.body.contains(this.$temoin[0])) this.$temoin = $(`<div class="al" style="display:none"></div>`).appendTo("body");
    return getComputedStyle(this.$temoin[0]).getPropertyValue("--p-" + cle).trim() || "#888";
  }

  rendre() {
    const s = this.s, esc = frappe.utils.escape_html;
    const dt = (v, zero) => {
      const n = Number(v) || 0;
      if (!n && zero !== false) return `<span class="zero">—</span>`;
      return n.toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 }) + " DT";
    };
    const txt = (v) => (Number(v) || 0).toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 }) + " DT";
    const jour = (iso) => (iso ? frappe.datetime.str_to_user(iso) : "");
    const t = s.totaux || {};
    const bulle = (lignes) => esc(JSON.stringify(lignes));
    const signe_txt = (v) => (v > 0.0005 ? "+ " + txt(v) : v < -0.0005 ? "− " + txt(-v) : txt(0));

    // Une ligne par personne : sa caisse + ce qu'elle a collecté et pas versé.
    const lignes = (s.employes || []).map((e) => Object.assign({ collecte: 0, nom: e.caisse }, e));
    (s.collecteurs || []).forEach((x) => {
      const l = lignes.find((e) => e.caisse === x.caisse);
      if (l) { l.collecte = x.detient; l.total += x.detient; l.collecteur = x; }
      else lignes.push({ caisse: x.nom, nom: x.nom, fond: 0, a_remettre: 0, non_compte: 0, collecte: x.detient, total: x.detient, collecteur: x });
    });
    // TIROIR UNIQUE (27/11/2026) : le collecteur principal n'a qu'UNE poche — sa caisse et l'argent collecté. Sa ligne
    // montre ce que son tiroir doit contenir (son dernier comptage + ce qui a bougé depuis).
    const pr0 = s.principal;
    if (pr0) {
      let l = lignes.find((e) => e.caisse === pr0.caisse);
      if (!l) { l = { caisse: pr0.caisse, nom: pr0.nom, fond: 0, a_remettre: 0, non_compte: 0, collecte: 0, total: 0 }; lignes.push(l); }
      Object.assign(l, { fond: 0, a_remettre: 0, non_compte: 0, collecte: pr0.especes, total: pr0.especes, unique: true });
    }
    lignes.sort((a, b) => b.total - a.total);
    const pleines = lignes.filter((l) => Math.abs(l.total) > 0.0005);
    const vides = lignes.filter((l) => Math.abs(l.total) <= 0.0005);
    const somme = (k) => lignes.reduce((acc, l) => acc + (Number(l[k]) || 0), 0);
    const poches = { fond: somme("fond"), a_remettre: somme("a_remettre"), non_compte: somme("non_compte"),
                     collecte: somme("collecte"), sabah: Number(t.sabah) || 0 };
    const total = Object.values(poches).reduce((a, b) => a + b, 0);
    const pct = (v) => (total ? Math.round((v / total) * 1000) / 10 : 0).toLocaleString("fr-FR") + " %";
    const verse_banque = (s.versements || []).reduce((a, v) => a + (Number(v.montant) || 0), 0);
    const partenaires = (s.partenaires || {}).comptes || [];
    const partenaires_total = partenaires.reduce((a, x) => a + (Number(x.total) || 0), 0);

    // ── Mode d'emploi ──
    let replie = false;
    try { replie = localStorage.getItem("al-aide-replie") === "1"; } catch (e) { /* stockage indisponible */ }
    const aide = `<details class="al-aide"${replie ? "" : " open"}>
      <summary>ℹ️ ${__("Comment lire cette page")}</summary>
      <ul>
        <li><b>${__("Le chemin de l’argent")}</b> : ${__("l’employé encaisse en espèces → le soir il compte sa caisse et remet l’argent au collecteur (Nejib) → le collecteur le met sur « Sabah Espèces » ou le verse à la banque. Chaque case du chemin dit combien d’argent se trouve à cette étape au soir choisi.")}</li>
        <li><span class="al-pastille" style="background:var(--p-fond)"></span><b>${__("Fond gardé")}</b> : ${__("ce que l’employé a gardé à son dernier comptage (compté − remis) ; il repart avec le lendemain.")}</li>
        <li><span class="al-pastille" style="background:var(--p-a_remettre)"></span><b>${__("Compté, pas encore remis")}</b> : ${__("l’employé a compté, le collecteur n’a pas encore confirmé avoir reçu l’argent.")}</li>
        <li><span class="al-pastille" style="background:var(--p-non_compte)"></span><b>${__("Encaissé, pas encore compté")}</b> : ${__("espèces encaissées moins espèces dépensées depuis le dernier comptage — la journée en cours, ou des jours oubliés (alors « sur N jours »).")}</li>
        <li><span class="al-pastille" style="background:var(--p-collecte)"></span><b>${__("Chez le collecteur")}</b> : ${__("tout l’argent liquide qui n’est ni au coffre ni chez un autre employé : le solde du compte « Espèces » moins ce que tiennent les autres caisses. Le calcul par les collectes (reçu des employés − déjà sorti) sert de contrôle ; quand il ne tombe pas sur le même montant, la différence est dans « À régulariser ».")}</li>
        <li><b>${__("Chèques et traites")}</b> : ${__("une pièce reçue attend sur le compte « Chèques » ou « Traite Bancaire » jusqu’à ce que la banque la crédite. Tant qu’elle n’est pas déposée, elle est chez le collecteur principal, ou chez l’employé qui l’a reçue tant que sa caisse n’est pas collectée. Elle ne compte pas dans l’argent liquide.")}</li>
        <li><span class="al-pastille" style="background:var(--p-sabah)"></span><b>${__("Sabah Espèces")}</b> : ${__("le solde du compte comptable où l’argent collecté est mis de côté.")}</li>
        <li><b>${__("Périodes")}</b> : ${__("« chez les employés » est une photo au soir choisi (depuis leur dernier comptage) ; « chez le collecteur » et Sabah sont des soldes comptables au soir choisi ; la banque, les 30 derniers jours. Chaque case l’écrit en bas.")}</li>
        <li><b>${__("Le partenaire (Economic Aqua Solution)")}</b> : ${__("il encaisse sur son propre compte ; sa caisse ne se compte pas. La section « Parti chez le partenaire » dit seulement ce qui est parti de son côté.")}</li>
        <li><b>${__("Et le compte comptable « Espèces » ?")}</b> : ${__("il ne dit pas qui tient l’argent. La section « Rapprochement avec la comptabilité », en bas, montre son solde, le compare à l’argent reconstitué ici et chiffre chaque cause d’écart, avec des liens vers le grand livre.")}</li>
        <li>${__("Cliquez sur une personne pour lire son explication en une phrase, sur une case du chemin pour aller au détail, sur un jour du graphique pour voir qui tenait l’argent ce soir-là. Survolez une couleur pour son montant.")}</li>
      </ul></details>`;

    // ── Le chemin de l'argent ──
    const etape = (cible, c, titre, v, detail, periode) => `<div class="al-etape" tabindex="0" data-cible="${cible}" style="--al-c:${c}">
        <div class="t">${titre}</div><div class="v">${dt(v, false)}</div><div class="d">${detail}</div>
        <div class="periode">🕒 ${periode}</div></div>`;
    const il_y_a_30 = frappe.datetime.add_days(s.date, -30);
    const chez_employes = poches.fond + poches.a_remettre + poches.non_compte;
    const chemin = `<div class="al-chemin">
      ${etape("al-personnes", "var(--p-non_compte)", "👥 " + __("Chez les employés"), chez_employes,
              __("fond gardé {0} · pas remis {1} · pas compté {2}", [txt(poches.fond), txt(poches.a_remettre), txt(poches.non_compte)]),
              __("au soir du {0}, depuis leur dernier comptage", [jour(s.date)]))}
      <div class="al-fleche">→</div>
      ${etape("al-collecteurs", "var(--p-collecte)", "📥 " + __("Chez les collecteurs"), poches.collecte,
              s.principal ? __("chez {0} : tout l’argent liquide qui n’est ni au coffre ni chez un autre employé", [esc(s.principal.nom)]) : __("reçu aux collectes, pas encore versé"),
              s.principal ? __("d’après le compte « Espèces » au {0}", [jour(s.date)]) : __("cumul du {0} (départ des caisses) au {1}", [jour(s.date_depart), jour(s.date)]))}
      <div class="al-fleche">→</div>
      ${etape("al-rapprochement", "var(--p-sabah)", "🔒 " + __("Sabah Espèces"), poches.sabah, __("argent collecté mis de côté"),
              __("solde comptable au {0}", [jour(s.date)]))}
      <div class="al-fleche">→</div>
      ${etape("al-banque", "var(--text-muted)", "🏦 " + __("Versé en banque"), verse_banque, __("déjà sorti du liquide"),
              __("du {0} au {1}", [jour(il_y_a_30), jour(s.date)]))}
    </div>
    ${partenaires_total ? `<div class="al-sous" style="margin-top:8px">🤝 ${__("Hors de nos caisses : {0} encaissés par le partenaire sur son propre compte depuis le {1}.", [txt(partenaires_total), jour((s.partenaires || {}).depart)])}
      <button class="al-bouton-lien al-aller" data-cible="al-partenaires">${__("Voir ce qui est parti de son côté")}</button></div>` : ""}`;

    // ── Répartition du total ──
    const segments = AL_POCHES.filter((p) => poches[p.cle] > 0.0005).map((p) =>
      `<div class="al-seg" style="flex:${poches[p.cle]};background:var(--p-${p.cle})"
            data-bulle="${bulle([{ c: this.couleur(p.cle), v: txt(poches[p.cle]), t: " " + __(p.lib) + " · " + pct(poches[p.cle]) }])}"></div>`).join("");
    const repartition = `<h4>💵 ${__("Où se trouve l’argent liquide le {0}", [jour(s.date)])}</h4>
      <div class="al-sous">${__("Une couleur par endroit ; la largeur est proportionnelle au montant.")}</div>
      <div class="al-barre" role="img" aria-label="${__("Répartition de l’argent liquide")}">${segments}</div>
      <div class="al-legende">${AL_POCHES.map((p) => `<span><span class="al-pastille" style="background:var(--p-${p.cle})"></span>${__(p.court)} <b>${dt(poches[p.cle], false)}</b> <span class="muet">${pct(poches[p.cle])}</span></span>`).join("")}</div>
      <div class="al-total">${__("Total de l’argent liquide")} : <b>${dt(total, false)}</b></div>`;

    const alertes = (s.collecteurs || []).filter((x) => !(x.principal && s.verdict)).flatMap((x) => (x.alertes || []).map((a) => `<div class="al-alerte">⚠️ ${esc(x.nom)} : ${esc(a)}</div>`)).join("");

    // ── Ce que le collecteur principal doit avoir entre les mains (25/11/2026 : « Nejib est le collecteur principal,
    //    il doit avoir tout l'argent espèces, chèques et traites qui n'est pas versé ») ──
    const pr = s.principal, pf = s.portefeuille || { pieces: [], par_type: {} };
    const remis_par = (x) => (x.remis_par || []).map((m) => __("{0} collectés par {1}", [txt(m.montant), esc(m.nom)])).join(", ");
    const heure = (quand) => (quand ? __("{0} à {1}", [jour(String(quand).slice(0, 10)), String(quand).slice(11, 16)]) : "");
    const v = s.verdict;
    const verdict = v ? `<div class="al-verdict ${v.etat}">
        <div class="t">${v.etat === "ok" ? "✔" : v.etat === "alerte" ? "⚠️" : "📝"} ${esc(__(v.titre))}</div>
        ${(v.points || []).length ? `<ul>${v.points.map((p) => `<li class="${p.niveau}"><span>${esc(p.texte)}</span>${p.cible
          ? ` <button class="al-bouton-lien al-aller" data-cible="${p.cible}">${__("Voir")}</button>` : ""}</li>`).join("")}</ul>` : ""}
      </div>` : "";
    const principal = pr ? (() => {
      const ch = pr.pieces["Chèque"] || { nb: 0, total: 0 }, tr = pr.pieces["Traite"] || { nb: 0, total: 0 };
      const dc = pr.dernier_comptage, vf = pr.verification, anc = pr.ancien_comptage;
      const lui = dc && dc.par === pr.user;
      const anciennes = (pf.anciennes || []).length;
      const preuve = (icone, corps, bouton) => `<div class="p"><span class="i">${icone}</span><div>${corps}</div>${bouton || ""}</div>`;
      return `<div class="al-principal" id="al-principal">
        <div class="tete"><div><div class="t">🧾 ${__("Ce que {0} doit avoir en main le {1}", [esc(pr.nom), jour(s.date)])}</div>
          <div class="muet">${__("C’est le collecteur principal. Il n’a qu’un tiroir : sa caisse et l’argent collecté pas encore versé au coffre ou en banque.")}</div></div>
          <div class="v">${dt(pr.total, false)}</div></div>
        <div class="al-principal-lignes">
          <div class="l"><span class="e">💵 ${__("Espèces dans son tiroir")}</span><b>${dt(pr.especes, false)}</b>
            <div class="muet">${esc(pr.explication || __("le compte « Espèces » ({0}) moins ce que tiennent les autres employés ({1})", [txt(pr.compte_especes), txt(pr.autres_caisses)]))}</div></div>
          <div class="l"><span class="e">📄 ${__("Chèques")}</span><b>${dt(ch.total, false)}</b><div class="muet">${__("{0} pièce(s) pas encore déposée(s)", [ch.nb])}</div></div>
          <div class="l"><span class="e">📄 ${__("Traites")}</span><b>${dt(tr.total, false)}</b><div class="muet">${__("{0} pièce(s) pas encore déposée(s)", [tr.nb])}</div></div>
        </div>
        <div class="al-preuves">
          <div class="t">${__("Qu’est-ce qui prouve que cet argent est bien là ?")}</div>
          ${preuve("🧮", dc
            ? __("<b>Dernier comptage de son tiroir</b> : le {0}, {1} comptés pour {2} attendus (écart {3}), par {4}.", [heure(dc.quand), txt(dc.compte), txt(dc.attendu), signe_txt(dc.ecart), esc(dc.par_nom || "?")])
              + (lui ? " " + __("Compté par lui-même : c’est sa déclaration, pas une preuve.") : "")
            : __("<b>Son tiroir n’a pas encore été compté en entier.</b> Son prochain comptage sera comparé au compte « Espèces » et servira de point de départ.")
              + (anc ? " " + __("Dernier comptage avec l’ancienne méthode : {0} le {1} ({2}).", [txt(anc.compte), heure(anc.quand), esc(anc.name)]) : ""))}
          ${preuve("👁", vf
            ? __("<b>Vérifié par une autre personne</b> : le {0} par {1}, {2} comptés pour {3} attendus (écart {4}).", [heure(vf.quand), esc(vf.par_nom || "?"), txt(vf.compte), txt(vf.attendu), signe_txt(vf.ecart)])
            : __("<b>Vérifié par une autre personne : jamais.</b> Seul un comptage fait par quelqu’un d’autre que lui prouve que les espèces sont là."),
            s.peut_inventorier ? `<button class="btn btn-xs btn-primary al-inventaire" data-coffre="0">${__("Vérifier son tiroir maintenant")}</button>` : "")}
          ${preuve("🔒", s.inventaire_coffre
            ? __("<b>Coffre « Sabah Espèces »</b> : compté le {0}, {1} pour {2} au compte (écart {3}).", [jour(s.inventaire_coffre.date), txt(s.inventaire_coffre.compte), txt(s.inventaire_coffre.theorique), signe_txt(s.inventaire_coffre.ecart)])
            : __("<b>Coffre « Sabah Espèces »</b> : {0} au compte, jamais compté. Un versement au coffre est une écriture saisie par lui seul : personne ne confirme la réception.", [txt(t.sabah)]),
            s.peut_inventorier ? `<button class="btn btn-xs btn-default al-inventaire" data-coffre="1">${__("Compter le coffre")}</button>` : "")}
          ${preuve("📄", __("<b>Chèques et traites</b> : chaque pièce reste dans la liste tant que la banque ne l’a pas créditée (le robot bancaire la sort à ce moment-là). Aucun pointage physique des pièces n’est enregistré.")
              + (anciennes ? " " + __("{0} pièce(s) attendent depuis plus de 30 jours.", [anciennes]) : ""),
            `<button class="btn btn-xs btn-default al-aller" data-cible="al-pieces">${__("Voir les pièces")}</button>`)}
          ${Math.abs(pr.ecart_compte || 0) > 0.5 ? preuve("📒", __("<b>Comparé au compte « Espèces »</b> : d’après le compte seul, il aurait {0}, soit {1}. Cette différence est la somme des écarts de comptage jamais passés en comptabilité.",
              [txt(pr.selon_compte), pr.ecart_compte > 0 ? __("{0} de moins que ses comptages", [txt(pr.ecart_compte)]) : __("{0} de plus que ses comptages", [txt(-pr.ecart_compte)])]),
            `<button class="btn btn-xs btn-default al-aller" data-cible="al-rapprochement">${__("Voir")}</button>`) : ""}
        </div>
      </div>`;
    })() : "";

    // ── À régulariser (25/11/2026 : « les erreurs identifiées, ne pas les montrer, les corriger ») : chaque anomalie de
    //    la carte attend UNE décision de la direction ; décidée (ou absorbée par un inventaire), elle part au journal. ──
    const areg = s.a_regulariser || [], journal_reg = s.regularisations || [], traitees = s.anomalies_traitees || [];
    const cles_ouvertes = new Set(areg.map((a) => a.cle));
    // Les anomalies sans effet et à décision unique (paiements antidatés) : une seule carte pour toutes.
    const lots = {};
    areg.filter((a) => a.options.length === 1 && !a.options[0].effet).forEach((a) => { (lots[a.nature] = lots[a.nature] || []).push(a); });
    Object.keys(lots).forEach((k) => { if (lots[k].length < 2) delete lots[k]; });
    const lien_doc = (type, nom) => (type && nom ? `<a href="/app/${frappe.router.slug(type)}/${encodeURIComponent(nom)}">${esc(nom)}</a>` : "");
    const regulariser = `<h4 id="al-regulariser">🧹 ${__("À régulariser")}${areg.length ? ` (${areg.length})` : ""}</h4>
      <div class="al-sous">${__("Une anomalie n’est pas une alerte permanente : la direction décide une fois, écrit le motif, et l’anomalie quitte la carte pour le journal. Un inventaire du collecteur absorbe d’un coup toutes ses anomalies passées.")}</div>
      ${areg.length ? areg.map((a, i) => (lots[a.nature] ? "" : `<div class="al-reg"><div class="t">${esc(a.titre)}</div>
          <div class="x">${esc(a.explication)} ${lien_doc(a.document_type, a.document)}</div>
          <div class="b">${s.peut_regulariser
            ? a.options.map((o) => `<button class="btn btn-xs ${o.effet ? "btn-primary" : "btn-default"} al-regulariser" data-i="${i}" data-code="${esc(o.code)}">${esc(o.libelle)}</button>`).join("")
            : `<span class="muet">${__("La décision revient à la direction.")}</span>`}</div></div>`)).join("")
          + Object.keys(lots).map((nature) => `<div class="al-reg"><div class="t">${__("{0} × {1} — {2}", [lots[nature].length, esc(__(nature)), txt(lots[nature].reduce((x, a) => x + (Number(a.montant) || 0), 0))])}</div>
          <div class="x">${lots[nature].map((a) => `${esc(a.titre)} · ${lien_doc(a.document_type, a.document)}`).join("<br>")}
            <div style="margin-top:4px">${esc(lots[nature][0].explication.replace(/^[^)]*\) /, ""))}</div></div>
          <div class="b">${s.peut_regulariser
            ? `<button class="btn btn-xs btn-default al-regulariser-lot" data-nature="${esc(nature)}">${__("{0} — pour les {1}", [esc(lots[nature][0].options[0].libelle), lots[nature].length])}</button>`
            : `<span class="muet">${__("La décision revient à la direction.")}</span>`}</div></div>`).join("")
        : `<div class="al-propre">✔ ${__("Rien à régulariser : la carte est propre.")}</div>`}
      ${journal_reg.length || traitees.length ? `<details style="margin-top:6px"><summary class="muet" style="cursor:pointer">${__("Journal des régularisations et des inventaires ({0})", [journal_reg.length])}</summary>
        <div class="al-defile"><table><tr><th>${__("Décidé le")}</th><th>${__("Nature")}</th><th>${__("Décision et motif")}</th><th>${__("Par")}</th><th class="n">${__("Effet sur la carte")}</th><th></th></tr>
        ${journal_reg.map((r) => `<tr><td>${esc(r.date || "")}</td><td>${esc(__(r.nature || ""))}${r.caisse ? `<div class="muet">${esc(r.caisse)}</div>` : ""}</td>
          <td>${esc(r.decision || "")} ${lien_doc(r.document_type, r.document)}<div class="muet">${esc(r.motif || "")}</div></td><td>${esc(r.decide_par_nom || "")}</td>
          <td class="n">${Number(r.effet) ? signe_txt(Number(r.effet)) : `<span class="zero">—</span>`}</td>
          <td>${s.peut_regulariser ? `<button class="al-bouton-lien al-annuler-reg" data-nom="${esc(r.name)}">${__("Annuler")}</button>` : ""}</td></tr>`).join("")}
        </table></div>
        ${traitees.filter((a) => !a.traitee_par).length ? `<div class="muet" style="margin-top:4px">${__("Absorbées par un inventaire : {0}.", [traitees.filter((a) => !a.traitee_par).map((a) => esc(a.titre)).join(" ; ")])}</div>` : ""}
      </details>` : ""}`;

    // ── Les chèques et traites pas encore versés ──
    const pieces = (() => {
      const ps = pf.pieces || [];
      const titre = `<h4 id="al-pieces">📄 ${__("Chèques et traites pas encore versés")}</h4>`;
      if (!ps.length) return titre + `<div class="muet">${__("Aucune pièce en attente.")}</div>`;
      // DEUX PARTIES (27/11/2026 : « organise bien la partie traite et la partie chèque ») : les traites, puis les
      // chèques ; dans chacune, d'abord ce qui attend depuis plus de 30 jours, puis ce qui est en main, puis ce qui est
      // déjà à la banque — avec le sous-total de la partie.
      const ligne_p = (p) => {
        const vieille = !p.depose_le && p.age_jours > 30;
        const traite = p.type === "Traite";
        const echue = traite && !p.depose_le && p.date_piece && p.date_piece <= s.date;
        const chez = p.depose_le
          ? `🏦 ${__("à la banque depuis le {0}", [jour(p.depose_le)])}${p.bordereau ? `<div class="muet">${esc(p.bordereau)}</div>` : ""}`
          : (p.chez_principal ? esc(p.chez) : `👤 ${esc(p.chez)}<div class="muet">${__("sa caisse n’est pas encore collectée")}</div>`);
        return `<tr${vieille ? ' class="vieille"' : ""}><td><b>${esc(p.numero || "—")}</b></td>
          <td>${esc(p.client)}</td>
          <td style="white-space:nowrap">${jour(p.date_piece)}${echue ? `<div class="muet">${__("échue")}</div>` : (traite && !p.depose_le && p.date_piece ? `<div class="muet">${__("pas encore échue")}</div>` : "")}</td>
          <td><span style="white-space:nowrap">${jour(p.entre_le)}</span><div class="muet">${esc(p.saisi_par)}</div></td>
          <td>${chez}</td>
          <td class="n">${__("{0} j", [p.age_jours])}${vieille ? `<div class="muet">⚠️ ${__("à vérifier")}</div>` : ""}</td>
          <td class="n"><a href="/app/payment-entry/${encodeURIComponent(p.name)}">${txt(p.montant)}</a></td></tr>`;
      };
      const somme_p = (liste) => liste.reduce((a, p) => a + (Number(p.montant) || 0), 0);
      const partie = (type, icone, titre_partie, col_numero, col_date, aide_date) => {
        const du_type = ps.filter((p) => p.type === type);
        if (!du_type.length) return "";
        const vieilles = du_type.filter((p) => !p.depose_le && p.age_jours > 30).sort((a, b) => b.age_jours - a.age_jours);
        const en_main = du_type.filter((p) => !p.depose_le && p.age_jours <= 30).sort((a, b) => b.age_jours - a.age_jours);
        const banque = du_type.filter((p) => p.depose_le).sort((a, b) => (a.depose_le < b.depose_le ? -1 : 1));
        const groupe = (libelle, liste, cls) => (liste.length ? `<tr class="al-groupe ${cls || ""}"><td colspan="6">${libelle} · ${liste.length}</td><td class="n">${txt(somme_p(liste))}</td></tr>${liste.map(ligne_p).join("")}` : "");
        const pas_deposees = vieilles.concat(en_main);
        return `<div class="al-partie">
          <div class="tete"><span class="t">${icone} ${titre_partie}</span>
            <span class="r">${__("{0} pas encore déposée(s) : {1}", [pas_deposees.length, `<b>${txt(somme_p(pas_deposees))}</b>`])}${banque.length ? " · " + __("{0} à la banque : {1}", [banque.length, txt(somme_p(banque))]) : ""}</span></div>
          <div class="al-defile"><table>
            <tr><th>${col_numero}</th><th>${__("Client")}</th><th title="${aide_date}">${col_date}</th>
              <th>${__("Reçue le")}</th><th>${__("Chez")}</th><th class="n" title="${__("Jours depuis la saisie de la pièce")}">${__("Depuis")}</th><th class="n">${__("Montant")}</th></tr>
            ${groupe("⚠️ " + __("Plus de 30 jours, à vérifier une par une"), vieilles, "vieille")}
            ${groupe(__("En main, à déposer"), en_main)}
            ${groupe("🏦 " + __("Déjà à la banque, en attente du crédit"), banque, "banque")}
            <tr class="pied"><td colspan="6">${__("Total {0}", [titre_partie.toLowerCase()])} · ${du_type.length}</td><td class="n">${txt(somme_p(du_type))}</td></tr>
          </table></div></div>`;
      };
      const parties = partie("Traite", "📜", __("Traites"), __("N° de la traite"), __("Échéance"), __("L’échéance écrite sur la traite"))
        + partie("Chèque", "🧾", __("Chèques"), __("N° du chèque et banque"), __("Date du chèque"), __("La date écrite sur le chèque"));
      const anciennes = (pf.anciennes || []).length;
      const controle = pf.solde_comptable === null || pf.solde_comptable === undefined ? ""
        : (Math.abs(pf.ecart_comptable) < 0.0005
          ? `<div class="al-ok">✔ ${__("Ces pièces refont exactement le solde des comptes « Chèques » et « Traite Bancaire » ({0}).", [txt(pf.solde_comptable)])}</div>`
          : `<div class="al-alerte">⚠️ ${__("Le solde des comptes « Chèques » et « Traite Bancaire » ({0}) ne correspond pas aux pièces : écart {1}. Une pièce est sortie (ou entrée) par une écriture et pas par un paiement.", [txt(pf.solde_comptable), txt(pf.ecart_comptable)])}</div>`);
      return `${titre}
        <div class="al-sous">${__("Une pièce reçue attend sur le compte « Chèques » ou « Traite Bancaire » jusqu’à ce que la banque la crédite : le robot bancaire l’en sort alors. Tant qu’elle n’est pas déposée, elle est chez {0} — ou chez l’employé qui l’a reçue, tant que sa caisse n’est pas collectée.", [esc(pf.principal || "")])}</div>
        ${pf.date_passee ? `<div class="al-sous">🕒 ${__("Date passée : ce sont les pièces encore en attente aujourd’hui qui étaient déjà reçues ce jour-là ; celles versées depuis n’apparaissent plus.")}</div>` : ""}
        ${anciennes ? `<div class="al-alerte">⚠️ ${__("{0} pièce(s) attendent depuis plus de 30 jours : déjà versées sans que la banque les ait reconnues, égarées, ou gardées exprès (chèque daté plus tard) ? À vérifier une par une.", [anciennes])}</div>` : ""}
        ${parties}
        <div class="al-total-pieces"><span>${__("En tout, pas encore déposé")} : ${(pf.par_type["Traite"] || { nb: 0 }).nb} ${__("traite(s)")} + ${(pf.par_type["Chèque"] || { nb: 0 }).nb} ${__("chèque(s)")}</span><b>${dt(pf.total, false)}</b></div>
        ${controle}
        <div class="al-liens">${Object.keys(pf.par_type || {}).length ? ["Chèques - A&S", "Traite Bancaire - A&S"].map((c) =>
          `<button class="btn btn-xs btn-default" data-grand-livre="${esc(c)}" data-du="${ps.reduce((m, p) => (p.entre_le < m ? p.entre_le : m), s.date)}" data-au="${s.date}">📒 ${__("Grand livre « {0} »", [esc(c.replace(" - A&S", ""))])}</button>`).join("") : ""}</div>`;
    })();

    // ── Qui tient l'argent ──
    const max_total = Math.max(...pleines.map((l) => Math.abs(l.total)), 1);
    const phrase = (l) => {
      if (l.unique) {
        return `<b>${esc(l.nom)}</b> ${__("doit avoir")} <b>${txt(l.total)}</b> : ${__("c’est le collecteur principal, sa caisse et l’argent collecté sont un seul tiroir.")} ${esc(pr0.explication || "")}`;
      }
      const morceaux = [];
      if (l.fond) morceaux.push(__("{0} gardés comme fond à son comptage du {1}", [txt(l.fond), jour(l.derniere && l.derniere.date)]));
      if (l.a_remettre) morceaux.push(__("{0} comptés le {1} et pas encore reçus par le collecteur ({2})", [txt(l.a_remettre), jour(l.derniere && l.derniere.date), esc(l.derniere ? l.derniere.name : "")]));
      if (l.non_compte) morceaux.push(l.jours_non_comptes > 1
        ? __("{0} encaissés sur {1} jours sans comptage — sa caisse est à compter", [txt(l.non_compte), l.jours_non_comptes])
        : __("{0} encaissés aujourd’hui, pas encore comptés", [txt(l.non_compte)]));
      if (l.collecte) morceaux.push(__("{0} collectés chez les employés et pas encore versés (Sabah ou banque){1}", [txt(l.collecte),
        (l.collecteur && l.collecteur.depuis ? " " + __("— le plus ancien depuis le {0}", [jour(l.collecteur.depuis)]) : "")
        + (l.collecteur && remis_par(l.collecteur) ? " " + __("(dont {0}, remis à lui)", [remis_par(l.collecteur)]) : "")]));
      if (l.regularise) morceaux.push(__("le fond tient compte de {0} de régularisation décidée par la direction", [signe_txt(l.regularise)]));
      return `<b>${esc(l.nom)}</b> ${__("tient")} <b>${txt(l.total)}</b>${morceaux.length ? " : " + morceaux.join(" ; ") : ""}.`;
    };
    const ligne = (l, i) => {
      const segs = ["fond", "a_remettre", "non_compte", "collecte"].filter((k) => Math.abs(l[k]) > 0.0005).map((k) => {
        const p = AL_POCHES.find((x) => x.cle === k);
        return `<div class="al-seg" style="flex:${Math.abs(l[k])};background:var(--p-${k})" data-bulle="${bulle([{ c: this.couleur(k), v: txt(l[k]), t: " " + __(p.lib) }])}"></div>`;
      }).join("");
      const largeur = Math.max(2, Math.round((Math.abs(l.total) / max_total) * 100));
      return `<tr class="al-ligne" data-i="${i}">
          <td><b>${esc(l.nom)}</b>${l.jours_non_comptes > 1 ? ` <span title="${__("Caisse pas comptée depuis plusieurs jours")}">⚠️</span>` : ""}
            ${l.derniere ? `<div class="muet">${__("dernier comptage")} <a href="/app/cloture-caisse/${encodeURIComponent(l.derniere.name)}" onclick="event.stopPropagation()">${jour(l.derniere.date)}</a> · ${esc(l.derniere.statut)}</div>` : ""}
            ${segs ? `<div class="al-mini" style="width:${largeur}%">${segs}</div>` : ""}</td>
          <td class="n">${dt(l.fond)}</td><td class="n">${dt(l.a_remettre)}</td>
          <td class="n">${dt(l.non_compte)}${l.jours_non_comptes > 1 && !l.unique ? `<div class="muet">${__("sur {0} jours", [l.jours_non_comptes])}</div>` : ""}</td>
          <td class="n">${dt(l.collecte)}</td><td class="n"><b>${dt(l.total)}</b></td></tr>
        <tr class="al-phrase" data-pour="${i}" style="display:none"><td colspan="6">${phrase(l)}</td></tr>`;
    };
    const entete = (p) => `<span class="al-pastille" style="background:var(--p-${p.cle})"></span>${__(p.court)}`;
    const personnes = `<h4 id="al-personnes">👥 ${__("Qui tient l’argent le {0}", [jour(s.date)])}</h4>
      <div class="al-sous">${__("Cliquez sur une ligne pour son explication. La petite barre montre de quoi est fait ce que la personne tient.")}</div>
      <div class="al-defile"><table>
        <tr><th>${__("Personne")}</th>
          <th class="n" title="${__("Gardé à son dernier comptage (compté − remis)")}">${entete(AL_POCHES[0])}</th>
          <th class="n" title="${__("Compté, le collecteur n’a pas encore confirmé la réception")}">${entete(AL_POCHES[1])}</th>
          <th class="n" title="${__("Encaissé − dépensé en espèces depuis le dernier comptage")}">${entete(AL_POCHES[2])}</th>
          <th class="n" title="${__("Tout l’argent liquide qui n’est ni au coffre ni chez un autre employé : le compte « Espèces » moins les autres caisses")}">${entete(AL_POCHES[3])}</th>
          <th class="n">${__("Total tenu")}</th></tr>
        ${pleines.map((l) => ligne(l, lignes.indexOf(l))).join("")}
        ${this.voir_vides ? vides.map((l) => ligne(l, lignes.indexOf(l))).join("") : ""}
        <tr class="pied"><td>${__("Total")}</td><td class="n">${dt(poches.fond)}</td><td class="n">${dt(poches.a_remettre)}</td>
          <td class="n">${dt(poches.non_compte)}</td><td class="n">${dt(poches.collecte)}</td><td class="n">${dt(somme("total"))}</td></tr>
      </table></div>
      ${vides.length ? `<button class="al-bouton-lien al-vides">${this.voir_vides ? __("Masquer les caisses vides") : __("Afficher aussi les {0} caisses vides", [vides.length])}</button>` : ""}`;

    // ── Collecteurs : le calcul posé ──
    const collecteurs = (s.collecteurs || []).map((x) => {
      const bloc = (titre, v, cls) => `<div class="bloc ${cls || ""}"><div class="t">${titre}</div><div class="v">${dt(v, false)}</div></div>`;
      const sorties = Object.keys(x.sorties || {}).map((k) => `${esc(k)} ${txt(x.sorties[k])}`).join(" · ");
      return `<div class="al-coll">
        <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap">
          <b style="font-size:15px">${esc(x.nom)}</b>
          <span class="muet">${x.depuis ? __("le plus ancien argent encore chez lui date du {0} ({1} jour(s))", [jour(x.depuis), x.age_jours]) : __("tout est versé")}</span></div>
        ${x.tiroir && pr0 ? (pr0.mode === "chaine" && pr0.base && pr0.depuis ? `<div class="al-calcul">
          ${bloc(__("Compté le {0}", [heure(pr0.base.quand)]), pr0.base.compte)}
          <span class="op">+</span>${bloc(__("Collecté depuis"), pr0.depuis.collecte)}
          <span class="op">−</span>${bloc(__("Versé au coffre ou en banque"), pr0.depuis.sorties)}
          <span class="op">${pr0.depuis.autres < 0 ? "−" : "+"}</span>${bloc(__("Ses encaissements, dépenses, autres"), Math.abs(pr0.depuis.autres))}
          <span class="op">=</span>${bloc(__("Doit être dans son tiroir"), x.detient, "res")}
        </div>` : `<div class="al-calcul">
          ${bloc(__("Compte « Espèces »"), pr0.compte_especes)}
          <span class="op">−</span>${bloc(__("Chez les autres employés"), pr0.autres_caisses)}
          <span class="op">=</span>${bloc(__("Doit être dans son tiroir"), x.detient, "res")}
        </div>
        <div class="muet">${__("Son tiroir n’a pas encore été compté en entier : le chiffre vient du compte. Dès son prochain comptage, il partira de ce qu’il a compté.")}</div>`) : `<div class="al-calcul">
          ${bloc(__("Reçu aux collectes"), x.recu)}
          ${x.passations ? `<span class="op">+</span>${bloc(__("Passations reçues"), x.passations)}` : ""}
          ${x.entrees_autres ? `<span class="op">+</span>${bloc(__("Revenu au compte Espèces"), x.entrees_autres)}` : ""}
          <span class="op">−</span>${bloc(__("Sorti (Sabah, banque, perso…)"), x.sorties_total)}
          ${x.regularise ? `<span class="op">${x.regularise > 0 ? "+" : "−"}</span>${bloc(__("Inventaire et régularisations"), Math.abs(x.regularise))}` : ""}
          <span class="op">=</span>${bloc(__("Encore entre ses mains"), x.detient, "res")}
        </div>`}
        ${remis_par(x) ? `<div class="muet">↪ ${__("Dans « reçu aux collectes » : {0}, remis ensuite à {1} (il est le collecteur principal).", [remis_par(x), esc(x.nom)])}</div>` : ""}
        ${(x.entrees_manuelles || []).filter((m) => cles_ouvertes.has("jv:" + m.name)).length ? `<div class="al-alerte">⚠️ ${__("{0} écriture(s) font ENTRER de l’argent dans Espèces sans venir du coffre ni de la banque : à décider dans « À régulariser ».", [x.entrees_manuelles.filter((m) => cles_ouvertes.has("jv:" + m.name)).length])}
          <button class="btn btn-xs btn-default al-aller" data-cible="al-regulariser">${__("Voir")}</button></div>` : ""}
        ${sorties ? `<div class="muet">${__("Détail des sorties depuis la date de départ")} : ${sorties}</div>` : ""}
        ${(x.detail_sorties || []).length ? `<details><summary>${__("Voir les 15 derniers mouvements")}</summary><div class="al-defile"><table>
          <tr><th>${__("Date")}</th><th>${__("Vers / depuis")}</th><th>${__("Référence")}</th><th>${__("Saisie par")}</th><th class="n">${__("Montant")}</th></tr>
          ${x.detail_sorties.map((m) => `<tr><td>${jour(m.date)}</td><td>${esc((m.vers || "").replace(/ - A&S/g, ""))}</td>
            <td><a href="/app/journal-entry/${encodeURIComponent(m.name)}">${esc(m.reference || m.name)}</a></td><td>${esc(m.saisie_par || "")}</td>
            <td class="n">${m.montant < 0 ? "+ " + txt(-m.montant) + " " + __("(revenu)") : txt(m.montant)}</td></tr>`).join("")}
        </table></div></details>` : ""}
      </div>`;
    }).join("");

    const versements = (s.versements || []).length ? `<div class="al-defile"><table>
        <tr><th>${__("Date")}</th><th>${__("Depuis")}</th><th>${__("Banque")}</th><th>${__("Référence du bordereau")}</th><th>${__("Saisi par")}</th><th class="n">${__("Montant")}</th></tr>
        ${s.versements.map((v) => `<tr><td>${jour(v.date)}</td><td>${esc(v.depuis)}</td><td>${esc(v.banque)}</td>
          <td><a href="/app/journal-entry/${encodeURIComponent(v.name)}">${esc(v.reference || v.name)}</a></td><td>${esc(v.saisie_par || "")}</td><td class="n">${txt(v.montant)}</td></tr>`).join("")}
      </table></div>` : `<div class="muet">${__("Aucun versement en banque sur les 30 derniers jours.")}</div>`;

    this.$c.html(`<div class="al">
      ${verdict}${principal}${alertes}
      <h4 id="al-flux-titre">🔀 ${__("Le chemin de l’argent du {0} au {1}", [jour((s.flux || {}).depart), jour(s.date)])}</h4>
      <div class="al-sous">${__("Tout ce que les clients ont payé, par où c’est passé, où c’est maintenant. Lu dans la comptabilité, donc ça s’équilibre : ce qui était là + ce qui est entré − ce qui est sorti = ce qui reste. L’épaisseur d’un trait est son montant ; survolez-le pour le lire.")}</div>
      <div class="al-flux" id="al-flux"></div>
      <details style="margin-top:6px"><summary class="muet" style="cursor:pointer">${__("Voir les chiffres en tableau")}</summary><div class="al-defile" id="al-table-flux"></div></details>
      ${regulariser}${pieces}
      <details class="al-detail"${this.detail_ouvert ? " open" : ""}><summary>${__("Voir tout le détail : qui tient quoi, les 14 derniers jours, la comptabilité")}</summary>
      ${aide}${chemin}${repartition}
      ${personnes}
      <h4 id="al-collecteurs">📥 ${__("Chez les collecteurs")}</h4>
      <div class="al-sous">${__("Le tiroir du collecteur principal : ce qu’il a compté la dernière fois, plus ce qui est entré, moins ce qui est sorti depuis.")}</div>
      ${collecteurs || `<div class="muet">${__("Rien de collecté depuis la date de départ.")}</div>`}
      ${partenaires.length ? `<h4 id="al-partenaires">🤝 ${__("Parti chez le partenaire")}</h4>
        <div class="al-sous">${__("Le partenaire encaisse certains de nos clients sur son propre compte : sa caisse ne se compte pas et cet argent n’est pas dans nos tiroirs. Voici ce qui est parti de son côté depuis le {0}.", [jour(s.partenaires.depart)])}</div>
        ${partenaires.map((x) => `<div class="al-coll">
          <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap">
            <b style="font-size:15px">${esc(x.nom)}</b>
            <span><b>${txt(x.total)}</b> ${__("encaissés pour nous")} · ${__("{0} paiement(s), dont {1} en espèces", [x.nb, txt(x.especes)])}</span></div>
          <div class="al-liens"><button class="btn btn-xs btn-default" data-grand-livre="${esc(x.compte)}" data-du="${s.partenaires.depart}" data-au="${s.date}">📒 ${__("Grand livre du partenaire")}</button></div>
          <details><summary>${__("Voir les 10 derniers paiements")}</summary><div class="al-defile"><table>
            <tr><th>${__("Date")}</th><th>${__("Paiement")}</th><th>${__("Client")}</th><th>${__("Mode")}</th><th class="n">${__("Montant")}</th></tr>
            ${x.derniers.map((m) => `<tr><td>${jour(m.date)}</td><td><a href="/app/payment-entry/${encodeURIComponent(m.name)}">${esc(m.name)}</a></td>
              <td>${esc(m.client)}</td><td>${esc(m.mode)}</td><td class="n">${txt(m.montant)}</td></tr>`).join("")}
          </table></div></details></div>`).join("")}` : ""}
      <h4 id="al-jours">📅 ${__("Les 14 derniers jours")}</h4>
      <div class="al-sous">${__("Chaque jour : ce que les employés ont encaissé en espèces, ce que le collecteur a reçu, ce qui a été mis sur Sabah ou versé en banque. Survolez un jour pour les montants ; cliquez pour voir qui tenait l’argent ce soir-là.")}</div>
      <div class="al-legende" id="al-legende-jours"></div>
      <div class="al-graph" id="al-graph"></div>
      <details style="margin-top:6px"><summary class="muet" style="cursor:pointer">${__("Voir les chiffres en tableau")}</summary><div class="al-defile" id="al-table-jours"></div></details>
      <h4 id="al-banque">🏦 ${__("Versements en banque (30 derniers jours)")}</h4>${versements}
      ${this.html_rapprochement(dt, txt, jour, esc)}
      <div class="al-note">${__("Méthode : toutes les caisses partagent un seul compte comptable « Espèces », qui ne dit pas qui tient les billets (son solde le {0} : {1}). Ce que tient chaque employé vient de ses comptages. Le collecteur principal n’a qu’un tiroir : ce qu’il doit avoir part de son dernier comptage, puis suit le compte « Espèces » moins les autres caisses (collectes, ses encaissements et dépenses, versements au coffre ou en banque). Départ des caisses : {2}. Alerte au-delà de {3} ou après {4} jours sans versement (Config Caisse).",
        [jour(s.date), txt(t.comptable_especes), jour(s.date_depart), txt(s.plafond), s.jours_max])}</div>
      </details>
    </div>`);

    const $al = this.$c.find(".al");
    $al.find(".al-aide").on("toggle", function () {
      try { localStorage.setItem("al-aide-replie", this.open ? "0" : "1"); } catch (e) { /* stockage indisponible */ }
    });
    const moi = this;
    $al.find(".al-detail").on("toggle", function () {
      moi.detail_ouvert = this.open;
      if (this.open) moi.dessiner_jours();
    });
    const montrer = (id) => {
      const cible = document.getElementById(id);
      if (!cible) return;
      const replie = cible.closest("details.al-detail");
      if (replie && !replie.open) { replie.open = true; moi.detail_ouvert = true; moi.dessiner_jours(); }
      cible.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    $al.find(".al-aller").on("click", function (e) {
      e.preventDefault();
      montrer($(this).attr("data-cible"));
    });
    $al.find(".al-etape").on("click keydown", function (e) {
      if (e.type === "keydown" && e.key !== "Enter") return;
      montrer($(this).attr("data-cible"));
    });
    $al.find("tr.al-ligne").on("click", function () { $al.find(`tr.al-phrase[data-pour="${$(this).attr("data-i")}"]`).toggle(); });
    $al.find(".al-vides").on("click", () => { this.voir_vides = !this.voir_vides; this.rendre(); });
    $al.find("[data-grand-livre]").on("click", function () {
      frappe.route_options = { company: frappe.defaults.get_user_default("Company"), account: [$(this).attr("data-grand-livre")],
                               from_date: $(this).attr("data-du"), to_date: $(this).attr("data-au"),
                               group_by: "Group by Voucher (Consolidated)" };
      frappe.set_route("query-report", "General Ledger");
    });
    $al.find(".al-regulariser").on("click", (e) => {
      const a = areg[+$(e.currentTarget).attr("data-i")];
      const o = a && a.options.find((x) => x.code === $(e.currentTarget).attr("data-code"));
      if (a && o) this.decider(a, o);
    });
    $al.find(".al-regulariser-lot").on("click", (e) => {
      const lot = lots[$(e.currentTarget).attr("data-nature")] || [];
      if (lot.length) this.decider(lot[0], lot[0].options[0], lot);
    });
    $al.find(".al-inventaire").on("click", (e) => this.inventaire($(e.currentTarget).attr("data-coffre") === "1"));
    $al.find(".al-annuler-reg").on("click", (e) => {
      const nom = $(e.currentTarget).attr("data-nom");
      frappe.confirm(__("Annuler la décision {0} ? L’anomalie (ou l’écart d’inventaire) reviendra sur la carte.", [esc(nom)]), () =>
        frappe.call({ method: "customization_app.caisse_regularisations.annuler", args: { name: nom }, freeze: true })
          .then(() => { frappe.show_alert({ message: __("Décision annulée."), indicator: "orange" }); this.charger(); }));
    });
    this.brancher_bulles($al);
    this.dessiner_flux();                                 // toujours visible (« je l'aimais », 27/11/2026)
    if (this.detail_ouvert) this.dessiner_jours();
  }

  // ── Décider d'une anomalie : la décision choisie, son effet, le motif (obligatoire) ──
  decider(a, o, lot) {
    const esc = frappe.utils.escape_html;
    const txt = (v) => (Number(v) || 0).toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 }) + " DT";
    const effet = Number(o.effet) || 0;
    const d = new frappe.ui.Dialog({
      title: __("Régulariser"),
      fields: [
        { fieldtype: "HTML", fieldname: "rappel" },
        { fieldtype: "Small Text", fieldname: "motif", label: __("Motif (reste dans le journal)"), reqd: 1,
          description: __("Qui a confirmé, ce qui s’est passé — 10 caractères au moins.") },
      ],
      primary_action_label: __("Enregistrer la décision"),
      primary_action: (v) => {
        frappe.call({ method: "customization_app.caisse_regularisations.regulariser", args: { cle: lot ? JSON.stringify(lot.map((x) => x.cle)) : a.cle, code: o.code, motif: v.motif },
                      freeze: true, freeze_message: __("Enregistrement…") })
          .then(() => { d.hide(); frappe.show_alert({ message: __("Régularisé : l’anomalie est passée au journal."), indicator: "green" }); this.charger(); });
      },
    });
    d.fields_dict.rappel.$wrapper.html(`<p><b>${lot ? lot.map((x) => esc(x.titre)).join("<br>") : esc(a.titre)}</b></p>
      ${lot ? "" : `<p class="text-muted" style="font-size:12.5px">${esc(a.explication)}</p>`}
      <p>${__("Votre décision")} : <b>${esc(o.libelle)}</b></p>
      <p>${effet ? __("Effet sur la carte : {0} chez {1}.", [(effet > 0 ? "+ " : "− ") + txt(Math.abs(effet)), esc(a.caisse || "")])
                 : __("Aucun montant ne change sur la carte : l’anomalie est simplement classée.")}</p>`);
    d.show();
  }

  // ── L'inventaire : ce qui est réellement en main (espèces du collecteur), ou dans le coffre ──
  inventaire(coffre) {
    const txt = (v) => (Number(v) || 0).toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 }) + " DT";
    const d = new frappe.ui.Dialog({
      title: coffre ? __("Compter le coffre « Sabah Espèces »") : __("Vérifier le tiroir du collecteur"),
      fields: [
        { fieldtype: "HTML", fieldname: "aide" },
        { fieldtype: "Float", fieldname: "especes", label: __("Espèces comptées (DT)"), reqd: 1, precision: 3 },
        { fieldtype: "Small Text", fieldname: "note", label: __("Note"), description: __("Obligatoire s’il y a un écart : ce qui l’explique, qui était présent.") },
      ],
      primary_action_label: __("Enregistrer le comptage"),
      primary_action: (v) => {
        frappe.call({ method: "customization_app.caisse_regularisations.inventorier",
                      args: { especes: v.especes, note: v.note || "", coffre: coffre ? 1 : 0 }, freeze: true, freeze_message: __("Enregistrement…") })
          .then((r) => {
            const m = r.message || {};
            d.hide();
            frappe.msgprint({ title: coffre ? __("Comptage du coffre enregistré") : __("Vérification enregistrée"), indicator: Math.abs(m.ecart || 0) < 0.0005 ? "green" : "orange",
              message: __("Compté {0} pour {1} attendus : écart {2}.", [txt(m.compte), txt(m.theorique), (m.ecart > 0 ? "+ " : m.ecart < 0 ? "− " : "") + txt(Math.abs(m.ecart || 0))])
                + (coffre ? " " + __("Le solde du coffre est comptable : un écart se corrige par une écriture.")
                          : " " + __("Son prochain comptage repartira de ce montant. Aucune écriture comptable n’est passée : un écart se règle par une écriture d’écart de caisse, si vous le décidez.")) });
            this.charger();
          });
      },
    });
    d.fields_dict.aide.$wrapper.html(`<p class="text-muted" style="font-size:12.5px">${coffre
      ? __("Comptez les billets du coffre. Le montant sera comparé au solde du compte « Sabah Espèces ».")
      : __("Comptez vous-même TOUTES les espèces de son tiroir : sa caisse et l’argent collecté pas encore versé. Faites-lui d’abord saisir un versement au coffre qui ne serait pas encore enregistré. Le montant attendu s’affiche après l’enregistrement, pas avant. Aucune écriture comptable n’est passée.")}</p>`);
    d.show();
  }

  // ── Rapprochement avec le compte comptable Espèces (24/11/2026) : son solde, l'argent reconstitué, et l'écart
  //    décomposé cause par cause (la décomposition est exacte : le « reste » vaut 0 quand rien n'a bougé après coup).
  html_rapprochement(dt, txt, jour, esc) {
    const r = this.s.rapprochement;
    if (!r) return "";
    const veille = frappe.datetime.add_days(r.depart, -1);
    const signe = (v) => (v > 0.0005 ? "+ " + txt(v) : v < -0.0005 ? "− " + txt(-v) : txt(0));
    const lien_gl = (compte, libelle) => `<button class="btn btn-xs btn-default" data-grand-livre="${esc(compte)}" data-du="${r.depart}" data-au="${this.s.date}">📒 ${libelle}</button>`;
    const pr = this.s.principal;
    const difference = (Number(r.reconstitue) || 0) - (Number(r.solde_jour) || 0);
    return `<h4 id="al-rapprochement">🧾 ${__("Rapprochement avec la comptabilité")}</h4>
      <div class="al-sous">${__("Le compte comptable « Espèces » regroupe toutes les caisses : il ne dit pas qui tient l’argent. Voici son solde, l’argent reconstitué par cette page sur la même période, et ce qui explique la différence.")}</div>
      <div class="al-rap">
        <div class="bloc"><b>${__("Compte « Espèces » (comptabilité)")}</b><table>
          <tr><td>${__("Solde au {0} (avant le départ des caisses)", [jour(veille)])}</td><td class="n">${txt(r.solde_avant)}</td></tr>
          <tr><td>+ ${__("Entré par paiements de clients")}</td><td class="n">${txt(r.entre_paiements)}</td></tr>
          <tr><td>− ${__("Sorti par paiements")}</td><td class="n">${txt(r.sorti_paiements)}</td></tr>
          <tr><td>− ${__("Dépenses de caisse")}</td><td class="n">${txt(r.depenses_caisse)}</td></tr>
          <tr><td>± ${__("Transferts (Sabah, banque, perso, autres)")}</td><td class="n">${signe(r.transferts)}</td></tr>
          <tr class="res"><td>= ${__("Solde au {0}", [jour(this.s.date)])}</td><td class="n">${txt(r.solde_jour)}</td></tr>
        </table>
        <div class="al-liens">${lien_gl("Espèces - A&S", __("Grand livre Espèces"))}${lien_gl("Sabah Espèces - A&S", __("Grand livre Sabah Espèces"))}</div></div>
        <div class="bloc"><b>${__("Qui tient cet argent")}</b><table>
          <tr><td>${__("Chez les employés (leurs comptages)")}</td><td class="n">${txt(this.s.totaux.employes)}</td></tr>
          <tr><td>${pr ? __("Dans le tiroir de {0} (son dernier comptage + les mouvements depuis)", [esc(pr.nom)]) : __("Chez les collecteurs")}</td><td class="n">${txt(r.reconstitue - this.s.totaux.employes)}</td></tr>
          <tr class="res"><td>${__("Total d’après les comptages")}</td><td class="n">${txt(r.reconstitue)}</td></tr>
          <tr><td>${__("Solde du compte « Espèces »")}</td><td class="n">${txt(r.solde_jour)}</td></tr>
          <tr class="res"><td>${__("Différence")}</td><td class="n">${signe(difference)}</td></tr>
        </table>
        <div class="muet" style="margin-top:6px">${Math.abs(difference) < 0.5
          ? __("Les comptages et le compte disent la même chose.")
          : __("Cette différence est la somme des écarts de comptage (ceux du collecteur et ceux des employés) qui n’ont jamais été passés en comptabilité. Elle ne se corrige pas toute seule : c’est une écriture d’écart de caisse, à votre décision.")}</div></div>
      </div>
      ${(r.verses_banque || []).length ? `<details style="margin-top:6px"><summary class="muet" style="cursor:pointer">${__("Espèces versées directement sur un compte bancaire ({0}, {1}) — pas une erreur : cet argent est en banque, aucune caisse ne le tient", [r.verses_banque.length, txt(r.verses_banque.reduce((a, m) => a + (Number(m.montant) || 0), 0))])}</summary>
        <div class="al-defile"><table><tr><th>${__("Date")}</th><th>${__("Paiement")}</th><th>${__("Client")}</th><th>${__("Compte")}</th><th>${__("Référence")}</th><th class="n">${__("Montant")}</th></tr>
        ${r.verses_banque.map((m) => `<tr><td>${jour(m.date)}</td><td><a href="/app/payment-entry/${encodeURIComponent(m.name)}">${esc(m.name)}</a></td><td>${esc(m.client)}</td><td>${esc(m.compte)}</td><td>${esc(m.reference)}</td><td class="n">${txt(m.montant)}</td></tr>`).join("")}
        </table></div></details>` : ""}`;
  }

  // ── Le graphique de flux (25/11/2026, « est-ce que tu peux créer un graphique de flux ») : d'où vient l'argent, par
  //    où il passe, où il est. Quatre colonnes : les sources · les endroits suivis · le coffre · les arrivées. Chaque
  //    endroit suivi s'équilibre (déjà là + entré = sorti + encore là) ; l'épaisseur d'un trait est son montant.
  dessiner_flux() {
    const $g = this.$c.find("#al-flux"), f = this.s.flux;
    if (!$g.length || !f) return;
    const txt = (v) => (Number(v) || 0).toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 }) + " DT";
    const court = (v) => Math.round(Number(v) || 0).toLocaleString("fr-FR") + " DT";
    const jour = (iso) => (iso ? frappe.datetime.str_to_user(iso) : "");
    const veille = jour(frappe.datetime.add_days(f.depart, -1));
    const SUIVIS = { especes: __("Espèces (caisses, collecteur)"), dettes: __("Dettes clients"), partenaire: __("Chez le partenaire"),
                     pieces: __("Chèques et traites"), sabah: __("Coffre Sabah Espèces") };
    // Colonnes et ordre fixes : une couleur suit son endroit, jamais son rang.
    const NOEUDS = [
      { id: "deja", col: 0, lib: __("Déjà là le {0}", [veille]), c: "neutre" },
      { id: "clients", col: 0, lib: __("Payé par les clients"), c: "neutre" },
      { id: "banque_src", col: 0, lib: __("Retiré de la banque"), c: "banque" },
      { id: "autres_entrees", col: 0, lib: __("Autres entrées (écritures)"), c: "neutre" },
      { id: "especes", col: 1, lib: SUIVIS.especes, c: "especes" },
      { id: "dettes", col: 1, lib: SUIVIS.dettes, c: "dettes" },
      { id: "partenaire", col: 1, lib: SUIVIS.partenaire, c: "partenaire" },
      { id: "pieces", col: 1, lib: SUIVIS.pieces, c: "pieces" },
      { id: "sabah", col: 2, lib: SUIVIS.sabah, c: "sabah" },
      { id: "banque", col: 3, lib: __("Arrivé en banque"), c: "banque" },
      { id: "depenses", col: 3, lib: __("Dépenses de caisse"), c: "neutre" },
      { id: "perso", col: 3, lib: __("Dépenses personnelles"), c: "neutre" },
      { id: "fournisseurs", col: 3, lib: __("Payé à des fournisseurs"), c: "neutre" },
      { id: "autres_sorties", col: 3, lib: __("Autres sorties (écritures)"), c: "neutre" },
      { id: "r_especes", col: 3, lib: __("Encore en espèces"), c: "especes" },
      { id: "r_sabah", col: 3, lib: __("Encore au coffre"), c: "sabah" },
      { id: "r_pieces", col: 3, lib: __("Chèques et traites en attente"), c: "pieces" },
      { id: "r_dettes", col: 3, lib: __("Dettes pas encore payées"), c: "dettes" },
      { id: "r_partenaire", col: 3, lib: __("Encore chez le partenaire"), c: "partenaire" },
    ];
    // Les liens : ceux du grand livre (allers-retours entre deux endroits suivis ramenés à leur net), ce qui était
    // déjà là au départ, et ce qui reste à la fin.
    const brut = {};
    (f.liens || []).forEach((l) => { brut[l.de + ">" + l.vers] = (brut[l.de + ">" + l.vers] || 0) + (Number(l.montant) || 0); });
    const liens = [];
    Object.keys(brut).forEach((k) => {
      let [de, vers] = k.split(">");
      let m = brut[k], note = "";
      const retour = brut[vers + ">" + de] || 0;
      if (SUIVIS[de] && SUIVIS[vers] && retour > 0) {
        if (m < retour || (m === retour && de > vers)) return;        // le sens net est porté par l'autre lien
        note = __("{0} dans ce sens, {1} en retour", [txt(m), txt(retour)]);
        m -= retour;
      }
      if (de === "banque") de = "banque_src";
      if (m > 0.0005) liens.push({ de, vers, m, note });
    });
    (f.stocks || []).forEach((st) => {
      if (st.avant > 0.0005) liens.push({ de: "deja", vers: st.cle, m: st.avant, note: "" });
      if (st.apres > 0.0005) liens.push({ de: st.cle, vers: "r_" + st.cle, m: st.apres, note: "" });
    });
    const noeuds = NOEUDS.map((n) => Object.assign({ entre: 0, sorti: 0 }, n));
    const par_id = {}; noeuds.forEach((n) => { par_id[n.id] = n; });
    const utiles = liens.filter((l) => par_id[l.de] && par_id[l.vers]);
    utiles.forEach((l) => { par_id[l.de].sorti += l.m; par_id[l.vers].entre += l.m; });
    const actifs = noeuds.filter((n) => Math.max(n.entre, n.sorti) > 0.0005);
    actifs.forEach((n) => { n.v = Math.max(n.entre, n.sorti); });
    if (!actifs.length) { $g.html(`<div class="muet">${__("Aucun mouvement sur la période.")}</div>`); return; }

    // Mise en page : l'échelle la plus grande qui fait tenir la colonne la plus chargée ; un nœud garde 26 px au moins
    // (la place de son étiquette sur deux lignes).
    const W = Math.max(760, $g.width() || 900), H = 470, haut = 8, bas = 8, larg = 12, entre = 8, MINI = 26;
    const cols = [0, 1, 2, 3].map((c) => actifs.filter((n) => n.col === c));
    const hauteur_col = (col, k) => col.reduce((a, n) => a + Math.max(MINI, n.v * k), 0) + Math.max(0, col.length - 1) * entre;
    let k_bas = 0, k_haut = (H - haut - bas) / Math.max(...actifs.map((n) => n.v));
    for (let i = 0; i < 40; i++) {
      const k = (k_bas + k_haut) / 2;
      if (cols.every((col) => hauteur_col(col, k) <= H - haut - bas)) k_bas = k; else k_haut = k;
    }
    const k = k_bas;
    const xs = [0, 0.33, 0.62, 1].map((r) => 4 + r * (W - larg - 8));
    cols.forEach((col, c) => {
      let y = haut + (H - haut - bas - hauteur_col(col, k)) / 2;
      col.forEach((n) => { n.x = xs[c]; n.h = Math.max(2, n.v * k); n.case = Math.max(MINI, n.v * k); n.y = y + (n.case - n.h) / 2; y += n.case + entre; n.pris_s = 0; n.pris_e = 0; });
    });
    const ordre = {}; actifs.forEach((n, i) => { ordre[n.id] = i; });
    const couleur = (l) => `var(--f-${par_id[l.de].c !== "neutre" ? par_id[l.de].c : par_id[l.vers].c})`;
    let traits = "";
    // Les liens d'un nœud sont empilés dans l'ordre de leurs arrivées (côté départ) et de leurs sources (côté arrivée).
    const cote_depart = utiles.slice().sort((a, b) => ordre[a.de] - ordre[b.de] || ordre[a.vers] - ordre[b.vers]);
    cote_depart.forEach((l) => { const n = par_id[l.de]; l.y0 = n.y + n.pris_s; n.pris_s += l.m * k; });
    utiles.slice().sort((a, b) => ordre[a.vers] - ordre[b.vers] || ordre[a.de] - ordre[b.de])
      .forEach((l) => { const n = par_id[l.vers]; l.y1 = n.y + n.pris_e; n.pris_e += l.m * k; });
    cote_depart.forEach((l, i) => {
      const a = par_id[l.de], b = par_id[l.vers], e = Math.max(1, l.m * k);
      const x0 = a.x + larg + 1, x1 = b.x - 1, xm = (x0 + x1) / 2;
      traits += `<path class="lien" data-i="${i}" fill="${couleur(l)}" d="M${x0},${l.y0} C${xm},${l.y0} ${xm},${l.y1} ${x1},${l.y1} V${l.y1 + e} C${xm},${l.y1 + e} ${xm},${l.y0 + e} ${x0},${l.y0 + e} Z"/>`;
    });
    let corps = "";
    actifs.forEach((n, i) => {
      // L'étiquette à droite du nœud, sauf la dernière colonne (à gauche) : nom à l'encre du texte, montant en retrait.
      const a_gauche = n.col === 3, tx = a_gauche ? n.x - 6 : n.x + larg + 6, ancre = a_gauche ? "end" : "start", ty = n.y + n.h / 2;
      corps += `<rect class="noeud" data-n="${i}" x="${n.x}" y="${n.y}" width="${larg}" height="${n.h}" rx="2" fill="var(--f-${n.c})"/>
        <text x="${tx}" y="${ty - 2}" text-anchor="${ancre}" paint-order="stroke" stroke="var(--bg-color, #fff)" stroke-width="3">${frappe.utils.escape_html(n.lib)}</text>
        <text class="m" x="${tx}" y="${ty + 12}" text-anchor="${ancre}" paint-order="stroke" stroke="var(--bg-color, #fff)" stroke-width="3">${court(n.v)}</text>`;
    });
    $g.html(`<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${__("Graphique de flux de l’argent")}">${traits}${corps}</svg>`);
    const self = this, vraie = (nom) => getComputedStyle(self.$c.find(".al")[0]).getPropertyValue("--f-" + nom).trim() || "#888";
    $g.find("path.lien").attr("tabindex", "0").each(function () {
      const l = cote_depart[+this.getAttribute("data-i")];
      const c = par_id[l.de].c !== "neutre" ? par_id[l.de].c : par_id[l.vers].c;
      const lignes = [{ c: vraie(c), v: txt(l.m), t: " " + par_id[l.de].lib + " → " + par_id[l.vers].lib }];
      if (l.note) lignes.push({ t: l.note });
      $(this).on("pointermove focus", (e) => self.bulle(e, lignes)).on("pointerleave blur", () => self.cacher_bulle());
    });
    $g.find("rect.noeud").attr("tabindex", "0").each(function () {
      const n = actifs[+this.getAttribute("data-n")];
      const lignes = [{ c: vraie(n.c), v: txt(n.v), t: " " + n.lib }];
      if (n.entre > 0.0005 && n.sorti > 0.0005) lignes.push({ t: __("entré {0} · sorti ou resté {1}", [txt(n.entre), txt(n.sorti)]) });
      $(this).on("pointermove focus", (e) => self.bulle(e, lignes)).on("pointerleave blur", () => self.cacher_bulle());
    });
    // La même chose en tableau : chaque endroit suivi, et son équilibre.
    const esc = frappe.utils.escape_html;
    this.$c.find("#al-table-flux").html(`<table>
      <tr><th>${__("Endroit")}</th><th class="n">${__("Déjà là le {0}", [veille])}</th><th class="n">${__("+ Entré")}</th><th class="n">${__("− Sorti")}</th>
          <th class="n">${__("= Encore là le {0}", [jour(f.jour)])}</th></tr>
      ${(f.stocks || []).map((st) => `<tr><td><span class="al-pastille" style="background:var(--f-${st.cle})"></span>${esc(SUIVIS[st.cle] || st.libelle)}${
          Math.abs(st.reste) > 0.0005 ? `<div class="muet">⚠️ ${__("ne s’équilibre pas : {0}", [txt(st.reste)])}</div>` : ""}</td>
        <td class="n">${txt(st.avant)}</td><td class="n">${txt(st.entre)}</td><td class="n">${txt(st.sorti)}</td><td class="n"><b>${txt(st.apres)}</b></td></tr>`).join("")}
      </table>
      <table style="margin-top:8px"><tr><th>${__("De")}</th><th>${__("Vers")}</th><th class="n">${__("Montant")}</th></tr>
      ${cote_depart.slice().sort((a, b) => b.m - a.m).map((l) => `<tr><td>${esc(par_id[l.de].lib)}</td><td>${esc(par_id[l.vers].lib)}${l.note ? `<div class="muet">${esc(l.note)}</div>` : ""}</td><td class="n">${txt(l.m)}</td></tr>`).join("")}
      </table>
      <div class="muet" style="margin-top:6px">${__("« Encore en espèces » est le solde du compte comptable. La carte, elle, reconstitue {0} tenus par les personnes : la différence est expliquée dans « Rapprochement avec la comptabilité ».",
        [txt((this.s.rapprochement || {}).reconstitue)])}</div>`);
  }

  // ── Les 14 derniers jours : barres groupées (SVG), une colonne cliquable par jour ──
  dessiner_jours() {
    const $g = this.$c.find("#al-graph");
    if (!$g.length) return;
    const jours = (this.s.jours || []).slice().reverse();          // du plus ancien au plus récent
    const series = [
      { cle: "fond", lib: __("Encaissé en espèces par les employés"), val: (j) => j.encaisse },
      { cle: "collecte", lib: __("Reçu par le collecteur"), val: (j) => j.collecte },
      { cle: "sabah", lib: __("Mis sur Sabah ou versé en banque"), val: (j) => (Number(j.sabah) || 0) + (Number(j.banque) || 0) },
    ];
    const txt = (v) => (Number(v) || 0).toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 }) + " DT";
    const court = (v) => (v >= 1000 ? (Math.round(v / 100) / 10).toLocaleString("fr-FR") + " k" : Math.round(v).toLocaleString("fr-FR"));
    this.$c.find("#al-legende-jours").html(series.map((s) =>
      `<span><span class="al-pastille" style="background:var(--p-${s.cle})"></span>${s.lib}</span>`).join(""));
    const W = Math.max(320, $g.width() || 800), H = 240, g = 44, d = 8, haut = 12, bas = 26;
    const max = Math.max(1, ...jours.flatMap((j) => series.map((s) => Number(s.val(j)) || 0)));
    // Graduations rondes : 4 lignes au plus.
    const pas = (() => { const brut = max / 4, p = Math.pow(10, Math.floor(Math.log10(brut))); return [1, 2, 2.5, 5, 10].map((m) => m * p).find((m) => m >= brut); })();
    const ymax = Math.ceil(max / pas) * pas;
    const y = (v) => haut + (H - haut - bas) * (1 - v / ymax);
    const larg_col = (W - g - d) / jours.length;
    const larg_barre = Math.max(3, Math.min(18, (larg_col - 10) / 3));
    let svg = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="${__("Flux d’espèces des 14 derniers jours")}">`;
    for (let v = 0; v <= ymax + 0.001; v += pas) {
      svg += `<line x1="${g}" x2="${W - d}" y1="${y(v)}" y2="${y(v)}" stroke="var(--al-grille)" stroke-width="1"/>`;
      svg += `<text x="${g - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--text-muted)">${court(v)}</text>`;
    }
    svg += `<line x1="${g}" x2="${W - d}" y1="${y(0)}" y2="${y(0)}" stroke="var(--al-axe)" stroke-width="1"/>`;
    jours.forEach((j, i) => {
      const x0 = g + i * larg_col + (larg_col - (3 * larg_barre + 4)) / 2;
      series.forEach((s, k) => {
        const v = Number(s.val(j)) || 0;
        if (v <= 0) return;
        const x = x0 + k * (larg_barre + 2), h = Math.max(2, y(0) - y(v)), r = Math.min(4, larg_barre / 2, h);
        // Barre ancrée à la ligne de base, extrémité arrondie (4px) en haut seulement.
        svg += `<path d="M${x},${y(0)} V${y(0) - h + r} Q${x},${y(0) - h} ${x + r},${y(0) - h} H${x + larg_barre - r} Q${x + larg_barre},${y(0) - h} ${x + larg_barre},${y(0) - h + r} V${y(0)} Z" fill="var(--p-${s.cle})"/>`;
      });
      const [a, m, jj] = j.date.split("-");
      const etiq = jours.length > 10 && W < 600 ? (i % 2 ? "" : `${jj}/${m}`) : `${jj}/${m}`;
      svg += `<text x="${g + i * larg_col + larg_col / 2}" y="${H - 8}" text-anchor="middle" font-size="11" fill="var(--text-muted)">${etiq}</text>`;
      svg += `<rect class="al-jour" data-i="${i}" x="${g + i * larg_col}" y="${haut}" width="${larg_col}" height="${H - haut - bas + 4}" rx="4"/>`;
    });
    svg += "</svg>";
    $g.html(svg);
    const self = this;
    $g.find("rect.al-jour").attr("tabindex", "0").each(function () {
      const j = jours[+this.getAttribute("data-i")];
      const lignes = [{ t: frappe.datetime.str_to_user(j.date) }]
        .concat(series.map((s) => ({ c: self.couleur(s.cle), v: txt(s.val(j)), t: " " + s.lib })))
        .concat([{ t: __("Cliquez : qui tenait l’argent ce soir-là") }]);
      $(this).on("pointermove focus", (e) => self.bulle(e, lignes)).on("pointerleave blur", () => self.cacher_bulle())
        .on("click keydown", (e) => {
          if (e.type === "keydown" && e.key !== "Enter") return;
          self.cacher_bulle();
          self.champ_date.set_value(j.date);          // le champ déclenche le recalcul
          window.scrollTo({ top: 0, behavior: "smooth" });
        });
    });
    // La même chose en tableau (lisible sans survol, et sans couleur).
    const dt = (v) => ((Number(v) || 0) ? txt(v) : `<span class="zero">—</span>`);
    this.$c.find("#al-table-jours").html(`<table>
      <tr><th>${__("Jour")}</th><th class="n">${__("Encaissé en espèces")}</th><th class="n">${__("Dépensé en espèces")}</th>
          <th class="n">${__("Reçu par le collecteur")}</th><th class="n">${__("→ Sabah Espèces")}</th><th class="n">${__("→ Banque")}</th><th class="n">${__("Autres sorties")}</th></tr>
      ${(this.s.jours || []).map((j) => `<tr><td>${frappe.datetime.str_to_user(j.date)}</td><td class="n">${dt(j.encaisse)}</td><td class="n">${dt(j.depense)}</td>
        <td class="n">${dt(j.collecte)}</td><td class="n">${dt(j.sabah)}</td><td class="n">${dt(j.banque)}</td><td class="n">${dt(j.autres)}</td></tr>`).join("")}
    </table>`);
  }
}
