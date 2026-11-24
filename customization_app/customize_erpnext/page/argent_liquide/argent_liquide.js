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
        <li><span class="al-pastille" style="background:var(--p-collecte)"></span><b>${__("Chez le collecteur")}</b> : ${__("ce qu’il a reçu aux collectes, moins ce qu’il a déjà sorti du compte Espèces (vers Sabah, la banque, des dépenses personnelles).")}</li>
        <li><span class="al-pastille" style="background:var(--p-sabah)"></span><b>${__("Sabah Espèces")}</b> : ${__("le solde du compte comptable où l’argent collecté est mis de côté.")}</li>
        <li>${__("Cliquez sur une personne pour lire son explication en une phrase, sur une case du chemin pour aller au détail, sur un jour du graphique pour voir qui tenait l’argent ce soir-là. Survolez une couleur pour son montant.")}</li>
      </ul></details>`;

    // ── Le chemin de l'argent ──
    const etape = (cible, c, titre, v, detail) => `<div class="al-etape" tabindex="0" data-cible="${cible}" style="--al-c:${c}">
        <div class="t">${titre}</div><div class="v">${dt(v, false)}</div><div class="d">${detail}</div></div>`;
    const chez_employes = poches.fond + poches.a_remettre + poches.non_compte;
    const chemin = `<div class="al-chemin">
      ${etape("al-personnes", "var(--p-non_compte)", "👥 " + __("Chez les employés"), chez_employes,
              __("fond gardé {0} · pas remis {1} · pas compté {2}", [txt(poches.fond), txt(poches.a_remettre), txt(poches.non_compte)]))}
      <div class="al-fleche">→</div>
      ${etape("al-collecteurs", "var(--p-collecte)", "📥 " + __("Chez les collecteurs"), poches.collecte, __("reçu aux collectes, pas encore versé"))}
      <div class="al-fleche">→</div>
      ${etape("al-jours", "var(--p-sabah)", "🔒 " + __("Sabah Espèces"), poches.sabah, __("argent collecté mis de côté"))}
      <div class="al-fleche">→</div>
      ${etape("al-banque", "var(--text-muted)", "🏦 " + __("Versé en banque"), verse_banque, __("sur les 30 derniers jours (déjà sorti du liquide)"))}
    </div>`;

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
        l.collecteur && l.collecteur.depuis ? " " + __("— le plus ancien depuis le {0}", [jour(l.collecteur.depuis)]) : ""]));
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
      ${aide}${chemin}${alertes}${repartition}${personnes}
      <h4 id="al-collecteurs">📥 ${__("Chez les collecteurs")}</h4>
      <div class="al-sous">${__("Le calcul : ce qu’il a reçu des employés, moins ce qu’il a déjà sorti. Le résultat est l’argent encore entre ses mains.")}</div>
      ${collecteurs || `<div class="muet">${__("Rien de collecté depuis la date de départ.")}</div>`}
      <h4 id="al-jours">📅 ${__("Les 14 derniers jours")}</h4>
      <div class="al-sous">${__("Chaque jour : ce que les employés ont encaissé en espèces, ce que le collecteur a reçu, ce qui a été mis sur Sabah ou versé en banque. Survolez un jour pour les montants ; cliquez pour voir qui tenait l’argent ce soir-là.")}</div>
      <div class="al-legende" id="al-legende-jours"></div>
      <div class="al-graph" id="al-graph"></div>
      <details style="margin-top:6px"><summary class="muet" style="cursor:pointer">${__("Voir les chiffres en tableau")}</summary><div class="al-defile" id="al-table-jours"></div></details>
      <h4 id="al-banque">🏦 ${__("Versements en banque (30 derniers jours)")}</h4>${versements}
      <div class="al-note">${__("Méthode : toutes les caisses partagent un seul compte comptable « Espèces », qui ne dit pas qui tient les billets (son solde le {0} : {1}). La page reconstruit donc qui détient quoi à partir des comptages, des collectes et des écritures depuis la date de départ des caisses ({2}). Une écriture de sortie saisie par quelqu’un qui n’est pas collecteur est comptée chez le responsable titulaire. Alerte au-delà de {3} ou après {4} jours sans versement (Config Caisse).",
        [jour(s.date), txt(t.comptable_especes), jour(s.date_depart), txt(s.plafond), s.jours_max])}</div>
    </div>`);

    const $al = this.$c.find(".al");
    $al.find(".al-aide").on("toggle", function () {
      try { localStorage.setItem("al-aide-replie", this.open ? "0" : "1"); } catch (e) { /* stockage indisponible */ }
    });
    $al.find(".al-etape").on("click keydown", function (e) {
      if (e.type === "keydown" && e.key !== "Enter") return;
      const cible = document.getElementById($(this).attr("data-cible"));
      if (cible) cible.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    $al.find("tr.al-ligne").on("click", function () { $al.find(`tr.al-phrase[data-pour="${$(this).attr("data-i")}"]`).toggle(); });
    $al.find(".al-vides").on("click", () => { this.voir_vides = !this.voir_vides; this.rendre(); });
    this.brancher_bulles($al);
    this.dessiner_jours();
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
