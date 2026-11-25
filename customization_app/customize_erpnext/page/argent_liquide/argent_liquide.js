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
    $(window).on("resize.al", frappe.utils.debounce(() => { if (this.s) this.dessiner_jours(); }, 200));
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

    // Une ligne par personne : sa caisse + ce qu'elle a collecté et pas versé.
    const lignes = (s.employes || []).map((e) => Object.assign({ collecte: 0, nom: e.caisse }, e));
    (s.collecteurs || []).forEach((x) => {
      const l = lignes.find((e) => e.caisse === x.caisse);
      if (l) { l.collecte = x.detient; l.total += x.detient; l.collecteur = x; }
      else lignes.push({ caisse: x.nom, nom: x.nom, fond: 0, a_remettre: 0, non_compte: 0, collecte: x.detient, total: x.detient, collecteur: x });
    });
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
        <li><span class="al-pastille" style="background:var(--p-collecte)"></span><b>${__("Chez le collecteur")}</b> : ${__("ce qu’il a reçu aux collectes, moins ce qu’il a déjà sorti du compte Espèces (vers Sabah, la banque, des dépenses personnelles). Tout est compté chez le collecteur principal : ce qu’un autre responsable collecte, il le lui remet.")}</li>
        <li><b>${__("Chèques et traites")}</b> : ${__("une pièce reçue attend sur le compte « Chèques » ou « Traite Bancaire » jusqu’à ce que la banque la crédite. Tant qu’elle n’est pas déposée, elle est chez le collecteur principal, ou chez l’employé qui l’a reçue tant que sa caisse n’est pas collectée. Elle ne compte pas dans l’argent liquide.")}</li>
        <li><span class="al-pastille" style="background:var(--p-sabah)"></span><b>${__("Sabah Espèces")}</b> : ${__("le solde du compte comptable où l’argent collecté est mis de côté.")}</li>
        <li><b>${__("Périodes")}</b> : ${__("« chez les employés » est une photo au soir choisi (depuis leur dernier comptage) ; « chez les collecteurs » est un cumul depuis la date de départ des caisses ; Sabah est un solde comptable ; la banque, les 30 derniers jours. Chaque case l’écrit en bas.")}</li>
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
              s.principal ? __("chez {0} : tout ce qui est collecté lui revient, pas encore versé", [esc(s.principal.nom)]) : __("reçu aux collectes, pas encore versé"),
              __("cumul du {0} (départ des caisses) au {1}", [jour(s.date_depart), jour(s.date)]))}
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

    const alertes = (s.collecteurs || []).flatMap((x) => (x.alertes || []).map((a) => `<div class="al-alerte">⚠️ ${esc(x.nom)} : ${esc(a)}</div>`)).join("");

    // ── Ce que le collecteur principal doit avoir entre les mains (25/11/2026 : « Nejib est le collecteur principal,
    //    il doit avoir tout l'argent espèces, chèques et traites qui n'est pas versé ») ──
    const pr = s.principal, pf = s.portefeuille || { pieces: [], par_type: {} };
    const remis_par = (x) => (x.remis_par || []).map((m) => __("{0} collectés par {1}", [txt(m.montant), esc(m.nom)])).join(", ");
    const principal = pr ? (() => {
      const ch = pr.pieces["Chèque"] || { nb: 0, total: 0 }, tr = pr.pieces["Traite"] || { nb: 0, total: 0 };
      const remis = remis_par(pr);
      const saut = (pr.sauts || []).reduce((a, x) => a + (Number(x.saut) || 0), 0);
      const plusieurs = (pr.sauts || []).length > 1;
      const sauts = (pr.sauts || []).map((x) => (plusieurs ? txt(x.saut) + " : " : "") + __("sa clôture du {0} ({1}) a été faite avant celle du {2} ({3}) et repart d’un fond qui ignore cette journée",
        [jour(x.date), esc(x.cloture), jour(x.date_precedente), esc(x.precedente || "")])).join(" ; ");
      return `<div class="al-principal" id="al-principal">
        <div class="tete"><div><div class="t">🧾 ${__("Ce que {0} doit avoir entre les mains le {1}", [esc(pr.nom), jour(s.date)])}</div>
          <div class="muet">${__("C’est le collecteur principal : tout ce qui n’est pas encore versé (à Sabah ou à la banque) doit être chez lui.")}</div></div>
          <div class="v">${dt(pr.total, false)}</div></div>
        <div class="al-principal-lignes">
          <div class="l"><span class="e">💵 ${__("Espèces")}</span><b>${dt(pr.especes, false)}</b>
            <div class="muet">${__("sa propre caisse {0} + collecté chez les employés, pas encore versé {1}", [txt(pr.caisse_propre), txt(pr.collecte)])}${remis ? " — " + __("dont {0}", [remis]) : ""}</div></div>
          <div class="l"><span class="e">📄 ${__("Chèques")}</span><b>${dt(ch.total, false)}</b><div class="muet">${__("{0} pièce(s) pas encore déposée(s)", [ch.nb])}</div></div>
          <div class="l"><span class="e">📄 ${__("Traites")}</span><b>${dt(tr.total, false)}</b><div class="muet">${__("{0} pièce(s) pas encore déposée(s)", [tr.nb])}</div></div>
        </div>
        ${saut ? `<div class="al-alerte">⚠️ ${__("Dans ces espèces, {0} viennent probablement d’une erreur de chaîne de sa caisse ({1}). Si c’est bien une erreur, il devrait avoir {2} en espèces.",
          [txt(saut), sauts, txt(pr.especes - saut)])}</div>` : ""}
        <div class="al-ok">${__("Pour vérifier : compter avec lui les billets, les chèques et les traites, et comparer à ces chiffres.")}</div>
        <div class="al-liens"><button class="btn btn-xs btn-default al-aller" data-cible="al-pieces">📄 ${__("Voir les chèques et traites")}</button>
          <button class="btn btn-xs btn-default al-aller" data-cible="al-collecteurs">📥 ${__("Voir le calcul des espèces")}</button>
          ${saut ? `<button class="btn btn-xs btn-default al-aller" data-cible="al-rapprochement">🧾 ${__("Voir l’erreur de chaîne")}</button>` : ""}</div>
      </div>`;
    })() : "";

    // ── Les chèques et traites pas encore versés ──
    const pieces = (() => {
      const ps = pf.pieces || [];
      const titre = `<h4 id="al-pieces">📄 ${__("Chèques et traites pas encore versés")}</h4>`;
      if (!ps.length) return titre + `<div class="muet">${__("Aucune pièce en attente.")}</div>`;
      const ligne_p = (p) => {
        const vieille = !p.depose_le && p.age_jours > 30;
        const chez = p.depose_le
          ? `🏦 ${__("à la banque depuis le {0}, en attente du crédit", [jour(p.depose_le)])}${p.bordereau ? `<div class="muet">${esc(p.bordereau)}</div>` : ""}`
          : (p.chez_principal ? esc(p.chez) : `👤 ${esc(p.chez)}<div class="muet">${__("sa caisse n’est pas encore collectée")}</div>`);
        return `<tr${vieille ? ' class="vieille"' : ""}><td><b>${esc(__(p.type))}</b><div class="muet">${esc(p.numero)}</div></td>
          <td>${esc(p.client)}</td><td>${jour(p.date_piece)}</td>
          <td>${jour(p.entre_le)}<div class="muet">${esc(p.saisi_par)}</div></td>
          <td>${chez}</td>
          <td class="n">${__("{0} j", [p.age_jours])}${vieille ? `<div class="muet">⚠️ ${__("à vérifier")}</div>` : ""}</td>
          <td class="n"><a href="/app/payment-entry/${encodeURIComponent(p.name)}">${txt(p.montant)}</a></td></tr>`;
      };
      const types = Object.keys(pf.par_type || {}).map((k) => __("{0} {1} ({2})", [pf.par_type[k].nb, esc(__(k === "Chèque" ? "chèque(s)" : "traite(s)")), txt(pf.par_type[k].total)])).join(" · ");
      const anciennes = (pf.anciennes || []).length;
      const controle = pf.solde_comptable === null || pf.solde_comptable === undefined ? ""
        : (Math.abs(pf.ecart_comptable) < 0.0005
          ? `<div class="al-ok">✔ ${__("Ces pièces refont exactement le solde des comptes « Chèques » et « Traite Bancaire » ({0}).", [txt(pf.solde_comptable)])}</div>`
          : `<div class="al-alerte">⚠️ ${__("Le solde des comptes « Chèques » et « Traite Bancaire » ({0}) ne correspond pas aux pièces : écart {1}. Une pièce est sortie (ou entrée) par une écriture et pas par un paiement.", [txt(pf.solde_comptable), txt(pf.ecart_comptable)])}</div>`);
      return `${titre}
        <div class="al-sous">${__("Une pièce reçue attend sur le compte « Chèques » ou « Traite Bancaire » jusqu’à ce que la banque la crédite : le robot bancaire l’en sort alors. Tant qu’elle n’est pas déposée, elle est chez {0} — ou chez l’employé qui l’a reçue, tant que sa caisse n’est pas collectée.", [esc(pf.principal || "")])}</div>
        ${pf.date_passee ? `<div class="al-sous">🕒 ${__("Date passée : ce sont les pièces encore en attente aujourd’hui qui étaient déjà reçues ce jour-là ; celles versées depuis n’apparaissent plus.")}</div>` : ""}
        ${anciennes ? `<div class="al-alerte">⚠️ ${__("{0} pièce(s) attendent depuis plus de 30 jours : déjà versées sans que la banque les ait reconnues, égarées, ou gardées exprès (chèque daté plus tard) ? À vérifier une par une.", [anciennes])}</div>` : ""}
        <div class="al-defile" style="margin-top:8px"><table>
          <tr><th>${__("Pièce")}</th><th>${__("Client")}</th><th title="${__("La date écrite sur le chèque, l’échéance de la traite")}">${__("Date sur la pièce")}</th>
            <th>${__("Reçue le")}</th><th>${__("Chez")}</th><th class="n" title="${__("Jours depuis la saisie de la pièce")}">${__("Depuis")}</th><th class="n">${__("Montant")}</th></tr>
          ${ps.map(ligne_p).join("")}
          <tr class="pied"><td colspan="6">${__("Pas encore déposé")} : ${types || "—"}</td><td class="n">${dt(pf.total, false)}</td></tr>
        </table></div>
        ${pf.deposes && pf.deposes.nb ? `<div class="al-ok">🏦 ${__("Déjà à la banque, en attente du crédit : {0} pièce(s), {1}.", [pf.deposes.nb, txt(pf.deposes.total)])}</div>` : ""}
        ${controle}
        <div class="al-liens">${Object.keys(pf.par_type || {}).length ? ["Chèques - A&S", "Traite Bancaire - A&S"].map((c) =>
          `<button class="btn btn-xs btn-default" data-grand-livre="${esc(c)}" data-du="${ps.reduce((m, p) => (p.entre_le < m ? p.entre_le : m), s.date)}" data-au="${s.date}">📒 ${__("Grand livre « {0} »", [esc(c.replace(" - A&S", ""))])}</button>`).join("") : ""}</div>`;
    })();

    // ── Qui tient l'argent ──
    const max_total = Math.max(...pleines.map((l) => Math.abs(l.total)), 1);
    const phrase = (l) => {
      const morceaux = [];
      if (l.fond) morceaux.push(__("{0} gardés comme fond à son comptage du {1}", [txt(l.fond), jour(l.derniere && l.derniere.date)]));
      if (l.a_remettre) morceaux.push(__("{0} comptés le {1} et pas encore reçus par le collecteur ({2})", [txt(l.a_remettre), jour(l.derniere && l.derniere.date), esc(l.derniere ? l.derniere.name : "")]));
      if (l.non_compte) morceaux.push(l.jours_non_comptes > 1
        ? __("{0} encaissés sur {1} jours sans comptage — sa caisse est à compter", [txt(l.non_compte), l.jours_non_comptes])
        : __("{0} encaissés aujourd’hui, pas encore comptés", [txt(l.non_compte)]));
      if (l.collecte) morceaux.push(__("{0} collectés chez les employés et pas encore versés (Sabah ou banque){1}", [txt(l.collecte),
        (l.collecteur && l.collecteur.depuis ? " " + __("— le plus ancien depuis le {0}", [jour(l.collecteur.depuis)]) : "")
        + (l.collecteur && remis_par(l.collecteur) ? " " + __("(dont {0}, remis à lui)", [remis_par(l.collecteur)]) : "")]));
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
          <td class="n">${dt(l.non_compte)}${l.jours_non_comptes > 1 ? `<div class="muet">${__("sur {0} jours", [l.jours_non_comptes])}</div>` : ""}</td>
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
          <th class="n" title="${__("Reçu aux collectes, pas encore sorti vers Sabah ou la banque")}">${entete(AL_POCHES[3])}</th>
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
          <span class="muet">${x.depuis ? __("le plus ancien argent collecté et pas versé date du {0} ({1} jour(s))", [jour(x.depuis), x.age_jours]) : __("tout est versé")}</span></div>
        <div class="al-calcul">
          ${bloc(__("Reçu aux collectes"), x.recu)}
          ${x.passations ? `<span class="op">+</span>${bloc(__("Passations reçues"), x.passations)}` : ""}
          ${x.entrees_autres ? `<span class="op">+</span>${bloc(__("Revenu au compte Espèces"), x.entrees_autres)}` : ""}
          <span class="op">−</span>${bloc(__("Sorti (Sabah, banque, perso…)"), x.sorties_total)}
          <span class="op">=</span>${bloc(__("Encore entre ses mains"), x.detient, "res")}
        </div>
        ${remis_par(x) ? `<div class="muet">↪ ${__("Dans « reçu aux collectes » : {0}, remis ensuite à {1} (il est le collecteur principal).", [remis_par(x), esc(x.nom)])}</div>` : ""}
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
      ${aide}${chemin}${principal}${alertes}${repartition}${personnes}
      <h4 id="al-collecteurs">📥 ${__("Chez les collecteurs")}</h4>
      <div class="al-sous">${__("Le calcul : ce qu’il a reçu des employés, moins ce qu’il a déjà sorti. Le résultat est l’argent encore entre ses mains.")}</div>
      ${collecteurs || `<div class="muet">${__("Rien de collecté depuis la date de départ.")}</div>`}
      ${pieces}
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
      <div class="al-note">${__("Méthode : toutes les caisses partagent un seul compte comptable « Espèces », qui ne dit pas qui tient les billets (son solde le {0} : {1}). La page reconstruit donc qui détient quoi à partir des comptages, des collectes et des écritures depuis la date de départ des caisses ({2}). Tout ce qui est collecté (par un autre responsable aussi) et toute sortie du compte Espèces sont comptés chez le collecteur principal : il doit avoir tout ce qui n’est pas versé. Alerte au-delà de {3} ou après {4} jours sans versement (Config Caisse).",
        [jour(s.date), txt(t.comptable_especes), jour(s.date_depart), txt(s.plafond), s.jours_max])}</div>
    </div>`);

    const $al = this.$c.find(".al");
    $al.find(".al-aide").on("toggle", function () {
      try { localStorage.setItem("al-aide-replie", this.open ? "0" : "1"); } catch (e) { /* stockage indisponible */ }
    });
    $al.find(".al-aller").on("click", function (e) {
      e.preventDefault();
      const cible = document.getElementById($(this).attr("data-cible"));
      if (cible) cible.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    $al.find(".al-etape").on("click keydown", function (e) {
      if (e.type === "keydown" && e.key !== "Enter") return;
      const cible = document.getElementById($(this).attr("data-cible"));
      if (cible) cible.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    $al.find("tr.al-ligne").on("click", function () { $al.find(`tr.al-phrase[data-pour="${$(this).attr("data-i")}"]`).toggle(); });
    $al.find(".al-vides").on("click", () => { this.voir_vides = !this.voir_vides; this.rendre(); });
    $al.find("[data-grand-livre]").on("click", function () {
      frappe.route_options = { company: frappe.defaults.get_user_default("Company"), account: [$(this).attr("data-grand-livre")],
                               from_date: $(this).attr("data-du"), to_date: $(this).attr("data-au"),
                               group_by: "Group by Voucher (Consolidated)" };
      frappe.set_route("query-report", "General Ledger");
    });
    this.brancher_bulles($al);
    this.dessiner_jours();
  }

  // ── Rapprochement avec le compte comptable Espèces (24/11/2026) : son solde, l'argent reconstitué, et l'écart
  //    décomposé cause par cause (la décomposition est exacte : le « reste » vaut 0 quand rien n'a bougé après coup).
  html_rapprochement(dt, txt, jour, esc) {
    const r = this.s.rapprochement;
    if (!r) return "";
    const veille = frappe.datetime.add_days(r.depart, -1);
    const t = r.termes || {};
    const flux = (Number(t.encaisse) || 0) + (Number(t.depense) || 0) + (Number(t.non_compte) || 0);
    const signe = (v) => (v > 0.0005 ? "+ " + txt(v) : v < -0.0005 ? "− " + txt(-v) : txt(0));
    const mal = r.mal_comptabilises || [];
    const total_mal = mal.reduce((a, m) => a + (Number(m.montant) || 0), 0);
    const ad = r.antidates_avant_depart || {};
    const lien_gl = (compte, libelle) => `<button class="btn btn-xs btn-default" data-grand-livre="${esc(compte)}" data-du="${r.depart}" data-au="${this.s.date}">📒 ${libelle}</button>`;
    const causes = [
      { lib: __("Encaissé et dépensé : les caisses ne comptent pas comme le compte"), v: flux, fort: Math.abs(flux) > 100,
        indice: [__("encaissé selon les {0} clôtures {1} contre {2} entrés au compte par paiements ; dépensé {3} contre {4} sortis en dépenses ; plus le pas-encore-compté ({5}), déjà au compte mais pas encore dans une clôture.",
                    [r.nb_clotures, txt(r.encaisse_clotures), txt(r.entre_paiements), txt(r.depense_clotures), txt((Number(r.sorti_paiements) || 0) + (Number(r.depenses_caisse) || 0)), txt(t.non_compte)]),
                 ad.nb ? __("piste : {0} paiements en espèces saisis après le {1} mais datés d’avant ({2}) — comptés par les caisses, rangés avant la période par la comptabilité.", [ad.nb, jour(r.depart), txt(ad.total)]) : "",
                 mal.length ? __("piste : {0} paiements saisis « Espèces » mais enregistrés sur un autre compte ({1}) — voir la liste ci-dessous.", [mal.length, txt(total_mal)]) : ""] },
      { lib: __("Écarts de comptage (compté − attendu)"), v: t.ecarts_comptage, indice: [__("ce que les employés ont compté en plus (ou en moins) du montant attendu.")] },
      { lib: __("Écarts de remise (reçu − remis)"), v: t.ecarts_remise, indice: [__("différence entre ce que l’employé a remis et ce que le collecteur a reçu.")] },
      { lib: __("Sauts dans la chaîne des clôtures"), v: t.sauts, fort: Math.abs(Number(t.sauts) || 0) > 0.0005,
        indice: (r.sauts || []).map((x) => __("{0}, le {1} : la clôture {2} s’ouvre à {3} au lieu de {4} (fond de {5} du {6}) — un jour compté avant la veille.",
          [esc(x.caisse), jour(x.date), `<a href="/app/cloture-caisse/${encodeURIComponent(x.cloture)}">${esc(x.cloture)}</a>`, txt(x.ouverture), txt(x.attendu),
           x.precedente ? `<a href="/app/cloture-caisse/${encodeURIComponent(x.precedente)}">${esc(x.precedente)}</a>` : "—", jour(x.date_precedente)])) },
      { lib: __("Reste : pièces modifiées après le comptage"), v: r.reste, indice: [__("paiements ou dépenses saisis, datés ou corrigés après que la caisse du jour a été comptée.")] },
    ];
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
        <div class="bloc"><b>${__("Argent reconstitué par cette page")}</b><table>
          <tr><td>${__("Chez les employés")}</td><td class="n">${txt(this.s.totaux.employes)}</td></tr>
          <tr><td>${__("Chez les collecteurs (y compris un éventuel négatif)")}</td><td class="n">${txt(r.reconstitue - this.s.totaux.employes)}</td></tr>
          <tr class="res"><td>${__("Total reconstitué depuis le {0}", [jour(r.depart)])}</td><td class="n">${txt(r.reconstitue)}</td></tr>
          <tr><td>${__("Mouvements du compte sur la même période")}</td><td class="n">${signe(r.mouvements)}</td></tr>
          <tr class="res"><td>${__("Écart à expliquer")}</td><td class="n">${signe(r.ecart)}</td></tr>
        </table>
        <div class="muet" style="margin-top:6px">${__("Les caisses sont reparties de zéro le {0} : les {1} déjà sur le compte à cette date n’y entrent pas.", [jour(r.depart), txt(r.solde_avant)])}</div></div>
      </div>
      <div class="al-defile" style="margin-top:10px"><table class="al-causes">
        <tr><th>${__("D’où vient l’écart")}</th><th class="n">${__("Montant")}</th></tr>
        ${causes.map((c) => `<tr class="${c.fort ? "fort" : ""}"><td>${c.fort ? "⚠️ " : ""}<b>${c.lib}</b>${(c.indice || []).filter(Boolean).map((i) => `<div class="indice">${i}</div>`).join("")}</td>
          <td class="n">${signe(Number(c.v) || 0)}</td></tr>`).join("")}
        <tr class="pied"><td>${__("Total = écart à expliquer")}</td><td class="n">${signe(r.ecart)}</td></tr>
      </table></div>
      ${mal.length ? `<details style="margin-top:6px"><summary class="muet" style="cursor:pointer">${__("Paiements saisis « Espèces » enregistrés sur un autre compte ({0})", [mal.length])}</summary>
        <div class="al-defile"><table><tr><th>${__("Date")}</th><th>${__("Paiement")}</th><th>${__("Compte utilisé")}</th><th>${__("Saisi par")}</th><th class="n">${__("Montant")}</th></tr>
        ${mal.map((m) => `<tr><td>${jour(m.date)}</td><td><a href="/app/payment-entry/${encodeURIComponent(m.name)}">${esc(m.name)}</a></td><td>${esc(m.compte)}</td><td>${esc(m.saisi_par)}</td><td class="n">${txt(m.montant)}</td></tr>`).join("")}
        </table></div></details>` : ""}`;
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
