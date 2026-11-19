// Rendez-vous pris : combien entrent dans le calendrier, et par où — à la main, avec l’app par un utilisateur,
// ou par le client lui-même sur le portail. Tout le calcul est côté serveur (customization_app.tableau_rdv) ;
// l’écran ne fait qu’afficher.
// Pas de gabarit .html : une apostrophe droite y casse la page (cf. pages Desk), tout est construit ici.

frappe.pages["rendez-vous-pris"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({ parent: wrapper, title: "Rendez-vous pris", single_column: true });
  // AJOUTER, jamais .html() sur .layout-main-section : Frappe y range la barre des filtres (page.add_field).
  const $main = $("<div>").appendTo($(wrapper).find(".layout-main-section"));
  // Trois couleurs, une par canal, toujours dans le même ordre (contrôlées pour le daltonisme, clair et sombre).
  // Les textes restent en encre normale : la pastille porte la couleur, jamais le chiffre.
  $main.html(`<style>
      .rv-wrap{max-width:1300px;--rv-manuel:#2a78d6;--rv-app_magasin:#eb6834;--rv-app_client:#1baf7a}
      [data-theme="dark"] .rv-wrap{--rv-manuel:#3987e5;--rv-app_magasin:#d95926;--rv-app_client:#199e70}
      .rv-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:0 0 14px}
      .rv-kpi{background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:12px 14px;cursor:pointer}
      .rv-kpi:hover{background:var(--subtle-fg)}
      .rv-kpi .l{font-size:12px;color:var(--text-muted);display:flex;align-items:center;gap:6px}
      .rv-kpi .v{font-size:28px;font-weight:600;margin-top:2px;line-height:1.15}
      .rv-kpi .v small{font-size:14px;font-weight:500;color:var(--text-muted);margin-left:6px}
      .rv-kpi .s{font-size:11.5px;color:var(--text-muted);margin-top:3px}
      .rv-pastille{display:inline-block;width:10px;height:10px;border-radius:3px;flex:none}
      .rv-part{display:flex;gap:2px;height:14px;margin:0 0 14px}
      .rv-part div{border-radius:3px;min-width:2px}
      .rv-card{background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:12px 14px;margin-bottom:14px}
      .rv-card h5{margin:0 0 8px;font-weight:600}
      .rv-card h5 span{font-weight:400;font-size:12px;color:var(--text-muted)}
      .rv-legende{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--text-color);margin-bottom:8px}
      .rv-legende span{display:flex;align-items:center;gap:6px}
      .rv-graph{position:relative;display:flex;gap:6px}
      .rv-axe{display:flex;flex-direction:column-reverse;justify-content:space-between;height:220px;font-size:11px;
        color:var(--text-muted);text-align:right;min-width:26px;margin-top:18px}
      .rv-axe span{line-height:0}
      .rv-plot{position:relative;flex:1;min-width:0}
      .rv-grille{position:absolute;left:0;right:0;top:18px;height:220px;display:flex;flex-direction:column-reverse;
        justify-content:space-between;pointer-events:none}
      .rv-grille i{display:block;border-top:1px solid var(--border-color);opacity:.7}
      .rv-cols{position:relative;display:flex;align-items:stretch;gap:2px}
      .rv-col{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;cursor:pointer}
      .rv-col:hover .rv-zone{background:var(--subtle-fg)}
      .rv-zone{height:238px;width:100%;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;border-radius:4px}
      .rv-nb{font-size:11px;color:var(--text-muted);line-height:16px;height:16px;margin-bottom:2px}
      .rv-pile{width:62%;max-width:34px;display:flex;flex-direction:column-reverse;gap:2px}
      .rv-pile div{min-height:2px} .rv-pile div.haut{border-radius:4px 4px 0 0}
      .rv-x{font-size:11px;color:var(--text-muted);margin-top:5px;white-space:nowrap;height:16px}
      .rv-bulle{position:absolute;z-index:5;pointer-events:none;background:var(--card-bg);border:1px solid var(--border-color);
        border-radius:8px;box-shadow:var(--shadow-md);padding:8px 10px;font-size:12px;min-width:190px;display:none}
      .rv-bulle b.t{display:block;margin-bottom:4px}
      .rv-bulle div{display:flex;align-items:center;gap:6px;justify-content:space-between}
      .rv-bulle div span{display:flex;align-items:center;gap:6px}
      .rv-bulle div.tot{border-top:1px solid var(--border-color);margin-top:4px;padding-top:4px;font-weight:600}
      .rv-scroll{overflow-x:auto}
      .rv-table{width:100%;border-collapse:collapse;font-size:12.5px}
      .rv-table th,.rv-table td{padding:6px 8px;border-bottom:1px solid var(--border-color);text-align:right;white-space:nowrap}
      .rv-table th:first-child,.rv-table td:first-child{text-align:left}
      .rv-table th{font-size:11px;color:var(--text-muted);font-weight:600;background:var(--subtle-fg)}
      .rv-table th .rv-pastille{margin-right:5px;vertical-align:-1px}
      .rv-table td.t{font-weight:600}
      .rv-table td.c{cursor:pointer} .rv-table td.c:hover{background:var(--subtle-fg);text-decoration:underline}
      .rv-table tr.tot td{font-weight:600;border-top:2px solid var(--border-color)}
      .rv-table tr.it td{color:var(--text-muted);font-style:italic}
      .rv-z{color:var(--gray-400)}
      .rv-deux{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:14px;align-items:start}
      .rv-deux .rv-card{margin-bottom:0} .rv-deux{margin-bottom:14px}
      .rv-def{font-size:12.5px;line-height:1.6} .rv-def ul{margin:0 0 6px;padding-left:18px}
      .rv-vide{padding:30px;text-align:center;color:var(--text-muted)}
    </style>
    <div class="rv-wrap">
      <div class="rv-kpis" id="rv-kpis"></div>
      <div class="rv-part" id="rv-part"></div>
      <div class="rv-card"><h5 id="rv-titre-graph">Rendez-vous par période</h5>
        <div class="rv-legende" id="rv-legende"></div><div id="rv-graph"></div></div>
      <div class="rv-deux">
        <div class="rv-card"><h5>Par utilisateur <span>— qui prend les rendez-vous, et comment</span></h5>
          <div class="rv-scroll" id="rv-users"></div></div>
        <div class="rv-card"><h5>Par type d’intervention</h5><div class="rv-scroll" id="rv-types"></div></div>
      </div>
      <div class="rv-card"><h5>Détail par période <span>— les mêmes chiffres que le graphique</span></h5>
        <div class="rv-scroll" id="rv-periodes"></div></div>
      <div class="rv-card rv-def" id="rv-def"></div>
    </div>`);

  const esc = frappe.utils.escape_html;
  const auj = frappe.datetime.get_today();
  const BASES = { "Date de prise du rendez-vous": "prise", "Date de l’intervention": "prevue" };
  const PAS = { "Automatique": "", "Par jour": "jour", "Par semaine": "semaine", "Par mois": "mois" };
  const f_du = page.add_field({ fieldname: "du", label: "Du", fieldtype: "Date", default: frappe.datetime.add_days(auj, -29),
                                change: () => charger() });
  const f_au = page.add_field({ fieldname: "au", label: "Au", fieldtype: "Date", default: auj, change: () => charger() });
  const f_base = page.add_field({ fieldname: "base", label: "Compter selon", fieldtype: "Select", options: Object.keys(BASES),
                                  default: Object.keys(BASES)[0], change: () => charger() });
  const f_pas = page.add_field({ fieldname: "pas", label: "Regrouper", fieldtype: "Select", options: Object.keys(PAS),
                                 default: "Automatique", change: () => charger() });
  let gel = false;      // deux champs changés d’un coup (boutons de période) = UN seul chargement
  const periode = async (du, au) => { gel = true; await f_du.set_value(du); await f_au.set_value(au); gel = false; charger(); };
  page.add_inner_button("30 jours", () => periode(frappe.datetime.add_days(auj, -29), auj));
  page.add_inner_button("Ce mois", () => periode(frappe.datetime.month_start(), auj));
  page.add_inner_button("Mois dernier", () => {
    const fin = frappe.datetime.add_days(frappe.datetime.month_start(), -1);
    periode(fin.slice(0, 8) + "01", fin);
  });
  page.add_inner_button("3 mois", () => periode(frappe.datetime.add_days(auj, -90), auj));
  page.set_secondary_action("Actualiser", () => charger(), "refresh");

  const nb = (v) => (v ? String(v) : `<span class="rv-z">·</span>`);
  const pct = (v, total) => (total ? Math.round((100 * v) / total) + " %" : "—");
  const jour = (d) => frappe.datetime.str_to_user(d);
  const pastille = (cle) => `<i class="rv-pastille" style="background:var(--rv-${cle})"></i>`;
  let d = null;        // la dernière réponse du serveur
  let en_cours = 0;

  // Ouvre la liste des tâches qui composent un chiffre : c’est la preuve du chiffre.
  function ouvrir(canal, du, au, user, type) {
    const o = { custom_client: ["is", "set"], [d.base === "prevue" ? "starts_on" : "creation"]: ["between", [du, au]] };
    o.custom_type_dintervention = type === "(sans type)" ? ["is", "not set"] : type || ["not in", d.hors];
    o.owner = user || ["!=", "Administrator"];
    if (canal === "manuel") o.custom_reservation_app = 0;
    if (canal === "app_magasin") { o.custom_reservation_app = 1; if (!user) o.owner = ["not in", ["Guest", "Administrator"]]; }
    if (canal === "app_client") { o.custom_reservation_app = 1; o.owner = "Guest"; }
    frappe.route_options = o;
    frappe.set_route("List", "Tache de travail", "List");
  }

  function cartes() {
    const t = d.totaux, a = d.avant;
    const ecart = (v, av) => {
      if (!av && !v) return "";
      const diff = v - av;
      return `${diff > 0 ? "▲ +" + diff : diff < 0 ? "▼ −" + Math.abs(diff) : "="} par rapport aux ${a.jours} jours précédents (${av})`;
    };
    const carte = (cle, libelle, v, part, annules, av) => `
      <div class="rv-kpi" data-canal="${cle}">
        <div class="l">${cle ? pastille(cle) : ""}${esc(libelle)}</div>
        <div class="v">${v}${part ? `<small>${part}</small>` : ""}</div>
        <div class="s">${ecart(v, av)}</div>
        <div class="s">${annules ? `dont ${annules} annulé${annules > 1 ? "s" : ""} ensuite` : "&nbsp;"}</div>
      </div>`;
    $("#rv-kpis", wrapper).html(
      carte("", "Tous les rendez-vous", t.total, "", t.annules, a.total) +
      d.canaux.map((c) => carte(c.cle, c.libelle, t[c.cle], pct(t[c.cle], t.total), d.annules[c.cle], a[c.cle])).join(""));
    // La répartition d’un coup d’œil : une seule barre, trois segments.
    $("#rv-part", wrapper).html(t.total ? d.canaux.filter((c) => t[c.cle]).map((c) =>
      `<div style="flex:${t[c.cle]} 0 0;background:var(--rv-${c.cle})" title="${esc(c.libelle)} : ${t[c.cle]} (${pct(t[c.cle], t.total)})"></div>`
    ).join("") : "");
  }

  // Un sommet d’axe « rond », divisible en 4 graduations entières.
  function sommet(max) {
    const brut = Math.max(max, 4) / 4;
    const p = Math.pow(10, Math.floor(Math.log10(brut)));
    return Math.ceil([1, 2, 2.5, 5, 10].find((m) => m * p >= brut) * p) * 4;
  }

  function graphique() {
    const P = d.periodes, $g = $("#rv-graph", wrapper);
    const libelle_pas = { jour: "par jour", semaine: "par semaine", mois: "par mois" }[d.pas];
    $("#rv-titre-graph", wrapper).html(`Rendez-vous ${libelle_pas} <span>— ${d.base === "prevue"
      ? "comptés à la date de l’intervention" : "comptés le jour où ils sont pris"}</span>`);
    $("#rv-legende", wrapper).html(d.canaux.map((c) => `<span>${pastille(c.cle)}${esc(c.libelle)}</span>`).join(""));
    if (!d.totaux.total) { $g.html(`<div class="rv-vide">Aucun rendez-vous sur cette période.</div>`); return; }
    const H = 220, max = sommet(Math.max(...P.map((p) => p.total)));
    const tous = Math.ceil(P.length / 14);      // une étiquette d’axe sur N quand les barres sont nombreuses
    const court = (p) => p.libelle.replace("sem. du ", "");
    $g.html(`<div class="rv-graph">
        <div class="rv-axe">${[0, 1, 2, 3, 4].map((i) => `<span>${(max / 4) * i}</span>`).join("")}</div>
        <div class="rv-plot">
          <div class="rv-grille"><i></i><i></i><i></i><i></i><i></i></div>
          <div class="rv-cols">${P.map((p, i) => {
            const pleins = d.canaux.filter((c) => p[c.cle]);
            return `<div class="rv-col" data-i="${i}">
              <div class="rv-zone">
                ${P.length <= 16 && p.total ? `<div class="rv-nb">${p.total}</div>` : ""}
                <div class="rv-pile" style="height:${Math.round((H * p.total) / max)}px">${pleins.map((c, k) =>
                  `<div class="${k === pleins.length - 1 ? "haut" : ""}" style="flex:${p[c.cle]} 0 0;background:var(--rv-${c.cle})"></div>`).join("")}</div>
              </div>
              <div class="rv-x">${i % tous === 0 ? esc(court(p)) : ""}</div>
            </div>`;
          }).join("")}</div>
          <div class="rv-bulle"></div>
        </div></div>`);
    const $bulle = $g.find(".rv-bulle"), $plot = $g.find(".rv-plot");
    $g.find(".rv-col").on("mouseenter", function () {
      const p = P[+this.dataset.i];
      $bulle.html(`<b class="t">${esc(p.libelle)}${p.du !== p.au ? ` <span class="text-muted" style="font-weight:400">(${jour(p.du)} → ${jour(p.au)})</span>` : ""}</b>` +
        d.canaux.map((c) => `<div><span>${pastille(c.cle)}${esc(c.libelle)}</span><b>${p[c.cle]}</b></div>`).join("") +
        `<div class="tot"><span>Total</span><b>${p.total}</b></div>`).show();
      const x = this.offsetLeft + this.offsetWidth / 2, l = $plot.width();
      $bulle.css({ top: 0, left: x > l / 2 ? x - $bulle.outerWidth() - 14 : x + 14 });
    }).on("mouseleave", () => $bulle.hide())
      .on("click", function () { const p = P[+this.dataset.i]; ouvrir("", p.du, p.au); });
  }

  // Une cellule cliquable : elle ouvre la liste des tâches derrière le chiffre.
  const cell = (v, attrs, classe) => `<td class="${v ? "c " : ""}${classe || ""}" ${v ? attrs : ""}>${nb(v)}</td>`;
  // Dans les deux tableaux côte à côte, des intitulés courts : sinon la dernière colonne sort de la carte.
  const COURT = { manuel: "À la main", app_magasin: "App · utilisateur", app_client: "App · client" };
  const entetes = (premier, canaux, court) => `<thead><tr><th>${premier}</th>${canaux.map((c) =>
    `<th title="${esc(c.libelle)}">${pastille(c.cle)}${esc(court ? COURT[c.cle] : c.libelle)}</th>`).join("")}<th>Total</th>`;

  function tableaux() {
    const t = d.totaux, mag = d.canaux.filter((c) => c.cle !== "app_client");
    // Par utilisateur : le client qui réserve seul n’est pas un utilisateur — il a sa ligne à part, en bas.
    $("#rv-users", wrapper).html(d.utilisateurs.length || t.app_client ? `<table class="rv-table">
      ${entetes("Utilisateur", mag, true)}<th>Part app</th></tr></thead><tbody>
      ${d.utilisateurs.map((u) => `<tr><td>${esc(u.nom)}</td>
        ${mag.map((c) => cell(u[c.cle], `data-canal="${c.cle}" data-user="${esc(u.user)}"`)).join("")}
        ${cell(u.total, `data-canal="" data-user="${esc(u.user)}"`, "t")}<td>${pct(u.app_magasin, u.total)}</td></tr>`).join("")}
      <tr class="it"><td>${pastille("app_client")} Clients, seuls sur le portail</td><td></td><td></td>
        ${cell(t.app_client, `data-canal="app_client"`, "t")}<td></td></tr>
      <tr class="tot"><td>Total</td><td>${nb(t.manuel)}</td><td>${nb(t.app_magasin)}</td><td>${nb(t.total)}</td>
        <td>${pct(t.app_magasin + t.app_client, t.total)}</td></tr></tbody></table>`
      : `<div class="rv-vide">Aucun rendez-vous sur cette période.</div>`);

    $("#rv-types", wrapper).html(d.types.length ? `<table class="rv-table">
      ${entetes("Type", d.canaux, true)}<th>Part app</th></tr></thead><tbody>
      ${d.types.map((y) => `<tr><td>${esc(y.type)}</td>
        ${d.canaux.map((c) => cell(y[c.cle], `data-canal="${c.cle}" data-type="${esc(y.type)}"`)).join("")}
        ${cell(y.total, `data-canal="" data-type="${esc(y.type)}"`, "t")}<td>${pct(y.app_magasin + y.app_client, y.total)}</td></tr>`).join("")}
      <tr class="tot"><td>Total</td>${d.canaux.map((c) => `<td>${nb(t[c.cle])}</td>`).join("")}<td>${nb(t.total)}</td>
        <td>${pct(t.app_magasin + t.app_client, t.total)}</td></tr></tbody></table>`
      : `<div class="rv-vide">Aucun rendez-vous sur cette période.</div>`);

    $("#rv-periodes", wrapper).html(`<table class="rv-table">
      ${entetes("Période", d.canaux)}<th>Annulés ensuite</th></tr></thead><tbody>
      ${d.periodes.slice().reverse().map((p) => `<tr><td>${esc(p.libelle)}${d.pas === "semaine" ? ` <span class="text-muted">→ ${jour(p.au)}</span>` : ""}</td>
        ${d.canaux.map((c) => cell(p[c.cle], `data-canal="${c.cle}" data-du="${p.du}" data-au="${p.au}"`)).join("")}
        ${cell(p.total, `data-canal="" data-du="${p.du}" data-au="${p.au}"`, "t")}<td>${nb(p.annules)}</td></tr>`).join("")}
      <tr class="tot"><td>Total</td>${d.canaux.map((c) => `<td>${nb(t[c.cle])}</td>`).join("")}<td>${nb(t.total)}</td>
        <td>${nb(t.annules)}</td></tr></tbody></table>`);
  }

  function definitions() {
    $("#rv-def", wrapper).html(`<h5>📖 Comment lire ce tableau</h5>
      <ul>
        <li><b>Un rendez-vous</b> = une tâche du calendrier avec un client (${d.hors.map(esc).join(", ")} ne comptent pas).
          C’est la même définition que le Tableau des appels.</li>
        <li>${pastille("manuel")} <b>Calendrier, à la main</b> : la tâche a été posée par un utilisateur, à l’heure et chez
          le technicien qu’il a choisis (« 📅 Prendre RDV », fiche de tâche, liste d’appels). Les règles de l’app ne s’appliquent pas.</li>
        <li>${pastille("app_magasin")} <b>App, par un utilisateur</b> : pris avec « 📱 RDV avec l’app » à la place du client —
          le créneau et le technicien sont choisis par l’app selon son réglage.</li>
        <li>${pastille("app_client")} <b>App, par le client</b> : le client a réservé seul sur le portail, avec son code SMS.</li>
        <li><b>Annulés ensuite</b> : rendez-vous pris puis annulés par le magasin ; ils restent comptés comme pris.
          Un rendez-vous que le client annule lui-même sur le portail est supprimé du calendrier : il n’apparaît plus ici.</li>
        <li>Cliquez sur un chiffre, une carte ou une barre pour ouvrir la liste des tâches correspondantes.</li>
      </ul>`);
  }

  // Un clic sur un chiffre ouvre la liste : cartes (toute la période), cellules des tableaux (leur ligne).
  $main.on("click", ".rv-kpi", function () { ouvrir(this.dataset.canal, d.du, d.au); });
  $main.on("click", "td.c", function () {
    const s = this.dataset;
    ouvrir(s.canal, s.du || d.du, s.au || d.au, s.user, s.type);
  });

  async function charger() {
    if (gel) return;
    const moi = ++en_cours;
    const r = (await frappe.call({ method: "customization_app.tableau_rdv.get_tableau",
      args: { du: f_du.get_value(), au: f_au.get_value(), base: BASES[f_base.get_value()] || "prise",
              pas: PAS[f_pas.get_value()] || "" } })).message;
    if (moi !== en_cours || !r) return;       // une réponse plus récente est déjà affichée
    d = r;
    d.avant.jours = frappe.datetime.get_day_diff(d.au, d.du) + 1;
    cartes(); graphique(); tableaux(); definitions();
  }
  wrapper.rv_charger = charger;
  charger();
};

frappe.pages["rendez-vous-pris"].on_page_show = function (wrapper) {
  if (wrapper.rv_charger) wrapper.rv_charger();
};
