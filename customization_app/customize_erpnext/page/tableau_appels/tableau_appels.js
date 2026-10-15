// Tableau des appels : par utilisateur et par jour, les gestes faits au téléphone et les rendez-vous pris, rangés
// par PRIORITÉ d'appel (Urgence → Commandes à traiter → Normal → Rattrapage → 2e appel → Requalification →
// Zone partenaire → Relance paiements 1×/semaine), puis les autres suivis. Chaque rendez-vous est rangé dans sa file.
// Tout le calcul est côté serveur (customization_app.tableau_appels) ; l’écran ne fait qu’afficher.
// Pas de gabarit .html : une apostrophe droite y casse la page (cf. pages Desk), tout est construit ici.

frappe.pages["tableau-appels"].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({ parent: wrapper, title: "Tableau des appels", single_column: true });
  // AJOUTER, jamais .html() sur .layout-main-section : Frappe y range la barre des filtres (page.add_field).
  const $main = $("<div>").appendTo($(wrapper).find(".layout-main-section"));
  $main.html(`<style>
      .ta-wrap{max-width:1300px}
      .ta-sec{font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.04em;margin:4px 0 6px}
      .ta-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:0 0 14px}
      .ta-kpi{background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:10px 12px}
      .ta-kpi .l{font-size:11px;color:var(--text-muted)} .ta-kpi .v{font-size:22px;font-weight:600;margin-top:2px}
      .ta-kpi .s{font-size:11px;color:var(--text-muted)}
      .ta-kpi.main{border-color:var(--primary)} .ta-kpi.rdv{border-color:var(--green-500)}
      .ta-prios{display:grid;grid-template-columns:repeat(var(--n,8),minmax(125px,1fr));gap:8px;margin:0 0 14px;overflow-x:auto}
      .ta-prio{background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:9px 10px;position:relative}
      .ta-prio .rg{position:absolute;top:8px;right:9px;width:20px;height:20px;border-radius:50%;background:var(--subtle-fg);
        font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center}
      .ta-prio .l{font-size:12px;font-weight:600;padding-right:22px;min-height:30px}
      .ta-prio .v{font-size:20px;font-weight:600} .ta-prio .s{font-size:11px;color:var(--text-muted)}
      .ta-prio .r{margin-top:6px;padding-top:6px;border-top:1px dashed var(--border-color);font-size:11.5px}
      .ta-prio .r b{font-size:13px}
      .ta-prio .d{font-size:11.5px;color:var(--blue-600);margin-top:2px} .ta-prio .d.zero{color:var(--text-muted)}
      .ta-table .rp{display:block;font-size:10.5px;color:var(--blue-600);font-weight:600}
      .ta-prio.suivante{border:2px solid var(--primary)} .ta-prio.suivante .rg{background:var(--primary);color:#fff}
      .ta-card{background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:12px;margin-bottom:14px}
      .ta-card h5{margin:0 0 8px;font-weight:600}
      .ta-scroll{overflow-x:auto}
      .ta-table{width:100%;border-collapse:collapse;font-size:12.5px}
      .ta-table th,.ta-table td{padding:6px 8px;border-bottom:1px solid var(--border-color);text-align:right;white-space:nowrap}
      .ta-table th:first-child,.ta-table td:first-child,.ta-table td.g,.ta-table th.g{text-align:left}
      .ta-table th{font-size:11px;color:var(--text-muted);font-weight:600;background:var(--subtle-fg)}
      .ta-table th.grp{text-align:center;background:var(--card-bg);border-bottom:2px solid var(--border-color);color:var(--text-color)}
      .ta-table th.grp.p{border-bottom-color:var(--primary)}
      .ta-table td.t{font-weight:600} .ta-table td.r{color:var(--green-600);font-weight:600}
      .ta-table td.sep,.ta-table th.sep{border-left:2px solid var(--border-color)}
      .ta-table tr.cl{cursor:pointer} .ta-table tr.cl:hover td{background:var(--subtle-fg)}
      .ta-table tr.tot td{font-weight:600;border-top:2px solid var(--border-color)}
      .ta-z{color:var(--gray-400)}
      .ta-def{font-size:12.5px;line-height:1.6}
      .ta-def h6{margin:14px 0 4px;font-weight:600;font-size:13px} .ta-def h6:first-of-type{margin-top:4px}
      .ta-def p,.ta-def ul,.ta-def ol{margin:0 0 6px} .ta-def ul,.ta-def ol{padding-left:18px}
      .ta-def .ta-table td,.ta-def .ta-table th{white-space:normal;text-align:left;vertical-align:top}
      .ta-def .ta-table td:first-child{font-weight:600;width:230px}
      .ta-def .ex{background:var(--subtle-fg);border-radius:8px;padding:8px 10px}
      [data-def]{cursor:help}
    </style>
    <div class="ta-wrap">
      <div class="ta-kpis" id="ta-kpis"></div>
      <div class="ta-sec">Par priorité d’appel</div><div class="ta-prios" id="ta-prios"></div>
      <div id="ta-autres-bloc"><div class="ta-sec">Autres suivis</div><div class="ta-kpis" id="ta-autres"></div></div>
      <div class="ta-card"><h5>Appels et suivis par jour</h5><div id="ta-chart"></div></div>
      <div class="ta-card"><h5>Rendez-vous pris par jour <span class="text-muted" style="font-weight:400;font-size:12px">— comptés le jour où la tâche est créée</span></h5>
        <div id="ta-chart-rdv"></div></div>
      <div class="ta-card"><h5>Par utilisateur <span class="text-muted" style="font-weight:400;font-size:12px" id="ta-hint"></span></h5>
        <div class="ta-scroll" id="ta-users"></div></div>
      <div class="ta-card"><h5>Détail par jour</h5><div class="ta-scroll" id="ta-jours"></div></div>
      <div class="ta-card ta-def" id="ta-def"></div>
    </div>`);

  const esc = frappe.utils.escape_html;
  const auj = frappe.datetime.get_today();
  const f_du = page.add_field({ fieldname: "du", label: "Du", fieldtype: "Date", default: frappe.datetime.add_days(auj, -6),
                                change: () => charger() });
  const f_au = page.add_field({ fieldname: "au", label: "Au", fieldtype: "Date", default: auj, change: () => charger() });
  const f_user = page.add_field({ fieldname: "utilisateur", label: "Utilisateur", fieldtype: "Select", options: [""],
                                  change: () => charger() });
  const periode = (du, au) => { f_du.set_value(du); f_au.set_value(au); };
  page.add_inner_button("Aujourd’hui", () => periode(auj, auj));
  page.add_inner_button("7 jours", () => periode(frappe.datetime.add_days(auj, -6), auj));
  page.add_inner_button("Ce mois", () => periode(frappe.datetime.month_start(), auj));
  page.add_inner_button("📖 Définitions", () => {
    const el = $(wrapper).find("#ta-def")[0];
    el && el.scrollIntoView({ behavior: "smooth", block: "start" });
  });
  page.set_secondary_action("Actualiser", () => charger(), "refresh");

  let en_cours = 0;
  const nb = (v) => (v ? String(v) : `<span class="ta-z">·</span>`);
  const jour = (d) => frappe.datetime.str_to_user(d);

  // Les définitions : [phrase courte (infobulles, sous les cartes), geste qui compte (section du bas), reste à faire].
  const DEF = {
    urgence: ["Clients traités dans les listes Urgence",
              "remplir la ligne d’un client dans une liste d’appels de type Urgence (cocher « A été appelé », choisir un résumé, écrire la réponse du client ou l’intérêt entretien / relance)",
              "clients pas encore appelés dans les listes Urgence en cours"],
    commandes: ["Commandes traitées (commentaire ou appel WEB)",
                "un commentaire écrit à la main sur une commande, ou les boutons « 1er / 2e appel sans réponse » d’une commande WEB (auteur non enregistré : ligne « Non attribué »)",
                "commandes de la page Commandes à traiter (depuis le 01/07) qui restent à livrer"],
    normal: ["Clients traités dans les listes Normal",
             "remplir la ligne d’un client dans une liste d’appels de type Normal",
             "clients pas encore appelés dans les listes Normal en cours"],
    relance_liste: ["Clients traités dans les listes 2e appel",
                    "remplir la ligne d’un client dans une liste d’appels de type Relance (2e appel)",
                    "clients pas encore appelés dans les listes 2e appel en cours"],
    rattrapage: ["Clients rappelés depuis la Liste Appels Rattrapage",
                 "dans la Liste Appels Rattrapage : marquer « sans réponse », ou le client comme recontacté ou replanifié",
                 "clients encore présents dans la Liste Appels Rattrapage"],
    zone: ["Clients traités dans les listes Zone partenaire",
           "remplir la ligne d’un client dans une liste d’appels de type Zone partenaire",
           "clients pas encore appelés dans les listes Zone partenaire en cours"],
    requalif: ["Clients traités dans les listes Requalification",
               "remplir la ligne d’un client dans une liste d’appels de type Requalification",
               "clients pas encore appelés dans les listes Requalification en cours"],
    relances: ["Clients relancés pour un paiement (téléphone, SMS ou e-mail)",
               "une relance enregistrée depuis la page Relance Paiements Clients : appel noté (Téléphone), SMS ou e-mail envoyé",
               "clients qui doivent encore de l’argent (dette, chèque ou traite impayés) et n’ont pas été relancés depuis 7 jours — file du lundi, chacun est relancé une fois par semaine"],
    ras: ["Clients appelés pour un certificat de retenue à la source manquant",
          "tracer un appel (ou un SMS / e-mail) depuis la page Retenue à la source — onglet « À réclamer » — file du mardi",
          "clients dont un certificat manque depuis plus de 45 jours et qui n’ont pas été relancés depuis (le mardi)"],
    hors_secteur: ["Clients traités dans les listes Hors secteur",
                   "remplir la ligne d’un client dans une liste d’appels de type Hors secteur (Gafsa, Sfax, Sousse sans partenaire…) — à faire quand toutes les autres files sont vides",
                   "clients pas encore appelés dans les listes Hors secteur en cours"],
    terrain: ["Appels passés par les techniciens", "un technicien qui note « 📞 Appel » depuis Ma journée"],
    client: ["Commentaires écrits sur des fiches clients",
             "un commentaire écrit à la main sur une fiche client (les relances sont comptées à part)"],
    activite: ["Notes ajoutées au suivi d’activité", "une note ajoutée dans le suivi d’activité"],
  };
  const INDICATEURS = {
    total: "Somme des actions de toutes les sources sur la période. Un même client peut y compter deux fois le même jour s’il a donné lieu à deux gestes différents (un appel de liste puis un commentaire).",
    rdv: "Tâches créées avec un client (ni congé, ni récupération, ni tournée), comptées à leur auteur et au jour de leur création. Elles ne sont pas incluses dans le total des actions.",
    rdv_liste: "Parmi les rendez-vous pris, ceux qui viennent d’une liste d’appels : créés depuis la ligne (bouton « Prendre RDV »), ou pour un client que la même personne a appelé le même jour dans une liste.",
    rdv_file: "Chaque rendez-vous est rangé dans la file d’où il vient : 1) créé depuis une ligne de liste d’appels → la priorité de cette liste ; 2) sinon, le même client traité le même jour par la même personne dans une file (liste, rattrapage, relance paiement) → cette file ; 3) sinon, tâche rattachée à une commande → Commandes à traiter ; 4) sinon « hors files » (le client a appelé lui-même, rendez-vous repris à la main…).",
    taux: "Rendez-vous créés depuis une liste ÷ clients traités dans les listes d’appels (toutes priorités de liste) × 100 : combien de rendez-vous pour 100 clients appelés.",
    reste: "Ce qui attend encore, maintenant (quelle que soit la période choisie).",
    jours: "Nombre de jours avec au moins une action.",
    moyenne: "Total des actions ÷ jours actifs : le rythme moyen d’un jour travaillé.",
  };
  const def = (cle, i) => (DEF[cle] || ["", "", ""])[i] || "";

  function definitions(r) {
    const ligne = (s) => `<tr><td>${esc(s.libelle)}</td><td>${esc(def(s.cle, 1))}</td></tr>`;
    return `<h5>📖 Comment lire ce tableau</h5>
      <h6>Les priorités d’appel</h6>
      <p>Les appels à faire sont rangés dans cet ordre — la première file qui n’est pas vide est « à faire maintenant » :</p>
      <ol>${r.priorites.map((p) => `<li><b>${esc(p.libelle)}</b> — reste à faire : ${esc(def(p.cle, 2))}.</li>`).join("")}</ol>
      <p>Chaque personne qui appelle retrouve ces files, avec son propre « fait aujourd’hui », en tête de l’onglet Appels
      (bloc « Mes appels par priorité »).</p>
      <h6>Une action, c’est quoi ?</h6>
      <p>Une <b>action</b>, c’est un client (ou une commande) traité par une personne, un jour donné, dans une source
      donnée. Chacune de ces combinaisons ne compte qu’<b>une seule fois</b> : rappeler le même client le même jour
      dans la même source ne fait pas deux actions. Le même client revu un autre jour compte à nouveau.</p>
      <h6>Ce qui crée une action</h6>
      <table class="ta-table"><thead><tr><th>Source</th><th>Le geste qui compte</th></tr></thead>
        <tbody>${r.priorites.map(ligne).join("")}${r.autres.map(ligne).join("")}</tbody></table>
      <h6>Ce qui ne compte pas</h6>
      <ul><li>Ouvrir une liste ou une fiche sans rien remplir.</li>
        <li>Décocher ou vider un champ, ou modifier un autre champ (adresse, téléphone…).</li>
        <li>Tout ce que le système fait seul : SMS automatiques, tâches planifiées, traces posées par le code.</li></ul>
      <h6>Exemple</h6>
      <p class="ex">Salma appelle le client X d’une liste Normal et met « Ne répond pas » : <b>1 action</b> en Normal. Elle
      le rappelle l’après-midi et passe à « Rendez-vous pris » : <b>toujours 1 action</b> (même client, même jour, même
      source). Elle crée la tâche du rendez-vous : <b>+1 RDV pris</b>, rangé en Normal (compté à part, hors total des actions). Elle
      écrit un commentaire sur la fiche de X : <b>+1 action</b> dans « Commentaires clients ». Bilan pour X ce jour-là :
      2 actions et 1 rendez-vous.</p>
      <h6>Les indicateurs</h6>
      <ul><li><b>Appels et suivis</b> : ${esc(INDICATEURS.total)}</li>
        <li><b>Rendez-vous pris</b> : ${esc(INDICATEURS.rdv)} « Portail client » = rendez-vous réservé par le client lui-même sur /rdv.</li>
        <li><b>dont liste</b> : ${esc(INDICATEURS.rdv_liste)}</li>
        <li><b>RDV pris par file</b> (cartes « Par priorité », petits chiffres bleus des tableaux) : ${esc(INDICATEURS.rdv_file)}</li>
        <li><b>RDV / 100 appels de liste</b> : ${esc(INDICATEURS.taux)}</li>
        <li><b>Reste maintenant</b> (cartes « Par priorité ») : ${esc(INDICATEURS.reste)}</li>
        <li><b>Jours actifs</b> : ${esc(INDICATEURS.jours)} <b>Moy./jour</b> : ${esc(INDICATEURS.moyenne)}</li>
        <li><b>Graphiques</b> : une barre par jour, hauteur = nombre d’actions (premier graphique) ou de rendez-vous pris (second) ; à plusieurs utilisateurs, une couleur par personne, la même dans les deux.</li>
        <li>Une source à zéro sur toute la période n’a pas de colonne ; les « Autres suivis » à zéro n’ont pas de carte.</li></ul>
      <h6>À garder en tête</h6>
      <ul><li>Un appel est daté du moment où la ligne a été remplie dans ERPNext, pas de l’heure réelle du coup de fil.</li>
        <li>Un rendez-vous est compté le jour où la tâche a été <b>créée</b>, pas le jour de l’intervention.</li></ul>`;
  }

  function tableau(lignes, r, opts) {
    // Les colonnes toujours vides sur la période sont masquées : elles noient l’essentiel.
    const actif = (s) => lignes.some((l) => l[s.cle] || l["rdv_" + s.cle]);
    const prios = r.priorites.filter(actif), autres = r.autres.filter(actif);
    const th = (s, sep) => `<th class="${sep ? "sep" : ""}" data-def title="${esc(def(s.cle, 0))}">${esc(s.libelle)}</th>`;
    const fixes_g = (opts.jour ? 3 : 2);
    const grp = `<tr><th class="grp" colspan="${fixes_g}"></th>` +
      (prios.length ? `<th class="grp p sep" colspan="${prios.length}">Priorités d’appel</th>` : "") +
      (autres.length ? `<th class="grp sep" colspan="${autres.length}">Autres suivis</th>` : "") +
      `<th class="grp sep" colspan="${opts.jour ? 2 : 4}"></th></tr>`;
    const tete = `<tr>${opts.jour ? `<th class="g">Jour</th>` : ""}<th class="g">Utilisateur</th>` +
      `<th data-def title="${esc(INDICATEURS.total)}">Total</th>` +
      prios.map((s, i) => th(s, i === 0)).join("") + autres.map((s, i) => th(s, i === 0)).join("") +
      `<th class="sep" data-def title="${esc(INDICATEURS.rdv)}">RDV pris</th><th data-def title="${esc(INDICATEURS.rdv_liste)}">dont liste</th>` +
      (opts.jour ? "" : `<th data-def title="${esc(INDICATEURS.jours)}">Jours actifs</th><th data-def title="${esc(INDICATEURS.moyenne)}">Moy./jour</th>`) + `</tr>`;
    // Sous le nombre d’actions d’une priorité, les rendez-vous qui en sont sortis.
    const rp = (l, s) => (l["rdv_" + s.cle] ? `<span class="rp" title="rendez-vous pris depuis cette file">${l["rdv_" + s.cle]} RDV</span>` : "");
    const td = (l, s, sep) => `<td class="${sep ? "sep" : ""}">${nb(l[s.cle])}${rp(l, s)}</td>`;
    const ligne = (l, cls) => `<tr class="${cls || ""}" data-user="${esc(l.user || "")}">` +
      (opts.jour ? `<td class="g">${l.jour ? jour(l.jour) : ""}</td>` : "") +
      `<td class="g">${esc(l.nom || "")}</td><td class="t">${nb(l.total)}</td>` +
      prios.map((s, i) => td(l, s, i === 0)).join("") + autres.map((s, i) => td(l, s, i === 0)).join("") +
      `<td class="r sep">${nb(l.rdv)}</td><td>${nb(l.rdv_liste)}</td>` +
      (opts.jour ? "" : `<td>${nb(l.jours)}</td><td>${l.jours ? (l.total / l.jours).toFixed(1) : nb(0)}</td>`) + `</tr>`;
    const corps = lignes.map((l) => ligne(l, opts.cliquable ? "cl" : "")).join("");
    const total = opts.totaux ? ligne(Object.assign({ nom: "Total", jours: 0 }, opts.totaux), "tot") : "";
    return lignes.length ? `<table class="ta-table"><thead>${grp}${tete}</thead><tbody>${corps}${total}</tbody></table>`
      : `<div class="text-muted">Aucune action sur la période.</div>`;
  }

  async function charger() {
    const appel = ++en_cours;
    const r = (await frappe.call({ method: "customization_app.tableau_appels.get_tableau",
      args: { du: f_du.get_value(), au: f_au.get_value(), utilisateur: f_user.get_value() || "" } })).message;
    if (!r || appel !== en_cours) return;

    if (r.supervise) {
      const opts = [{ label: "Tous les utilisateurs", value: "" }].concat(r.choix.map((c) => ({ label: c.nom, value: c.user })));
      const avant = f_user.get_value();
      f_user.df.options = opts;
      f_user.refresh();
      f_user.$input && f_user.$input.val(avant || "");
    } else {
      f_user.$wrapper.hide();
    }

    // Chaque carte porte sa définition : une ligne visible, la phrase complète en infobulle.
    const t = r.totaux, k = (l, v, s, cls, aide) => `<div class="ta-kpi ${cls || ""}" data-def title="${esc(aide || "")}">
      <div class="l">${l}</div><div class="v">${v}</div>${s ? `<div class="s">${s}</div>` : ""}</div>`;
    const jours_actifs = new Set(r.par_jour.filter((d) => d.total).map((d) => d.jour)).size;
    $(wrapper).find("#ta-kpis").html(
      k("Appels et suivis", t.total, `actions sur ${jours_actifs} jour(s) d’activité`, "main", INDICATEURS.total) +
      k("Rendez-vous pris", t.rdv, `dont ${t.rdv_liste} depuis une liste` + (t.rdv_hors ? ` · ${t.rdv_hors} hors files` : ""),
        "rdv", INDICATEURS.rdv + " " + INDICATEURS.rdv_file) +
      k("RDV / 100 appels de liste", t.listes ? Math.round((100 * t.rdv_liste) / t.listes) : "—",
        `rendez-vous pour 100 clients appelés (${t.listes} appels de liste)`, "", INDICATEURS.taux));

    // Les 7 priorités, dans l’ordre : actions de la période et ce qui reste maintenant.
    const suivante = r.priorites.find((p) => (r.restes[p.cle] || 0) > 0);
    $(wrapper).find("#ta-prios").css("--n", r.priorites.length).html(r.priorites.map((p) => {
      const reste = r.restes[p.cle];
      return `<div class="ta-prio ${suivante && suivante.cle === p.cle ? "suivante" : ""}" data-def
          title="${esc(def(p.cle, 0) + " — reste à faire : " + def(p.cle, 2))}">
        <div class="rg">${p.rang}</div><div class="l">${esc(p.libelle)}</div>
        <div class="v">${t[p.cle] || 0}</div><div class="s">action(s) sur la période</div>
        <div class="d ${t["rdv_" + p.cle] ? "" : "zero"}">📅 <b>${t["rdv_" + p.cle] || 0}</b> RDV pris</div>
        <div class="r">reste maintenant : <b>${reste == null ? "—" : reste}</b>${suivante && suivante.cle === p.cle ? " 👉" : ""}</div>
      </div>`;
    }).join(""));
    const autres = r.autres.filter((s) => t[s.cle]);
    $(wrapper).find("#ta-autres-bloc").toggle(!!autres.length);
    $(wrapper).find("#ta-autres").html(autres.map((s) => k(esc(s.libelle), t[s.cle], esc(def(s.cle, 0)), "", def(s.cle, 1))).join(""));

    // Graphiques : une barre par jour, empilée par utilisateur — les actions, puis les rendez-vous pris.
    const graphique = (sel, cle, unite, vide) => {
      const $c = $(wrapper).find(sel).empty();
      if (!r.series.some((s) => (s[cle] || []).some((v) => v))) return $c.html(`<div class="text-muted">${vide}</div>`);
      new frappe.Chart($c[0], {
        // Empilé seulement à plusieurs : avec une seule série, frappe-charts dessine des barres vides.
        type: "bar", height: 240, barOptions: { stacked: r.series.length > 1 ? 1 : 0, spaceRatio: 0.35 },
        data: { labels: r.jours.map((d) => d.slice(8, 10) + "/" + d.slice(5, 7)),
                datasets: r.series.map((s) => ({ name: s.nom, values: s[cle] || [] })) },
        axisOptions: { xIsSeries: 1 }, tooltipOptions: { formatTooltipY: (v) => `${v} ${unite}` },
      });
    };
    graphique("#ta-chart", "valeurs", "action(s)", "Aucune action sur la période.");
    graphique("#ta-chart-rdv", "rdv", "rendez-vous", "Aucun rendez-vous pris sur la période.");

    $(wrapper).find("#ta-hint").text(r.supervise && !r.utilisateur ? "— cliquez une ligne pour ne voir qu’elle" : "");
    $(wrapper).find("#ta-users").html(tableau(r.par_utilisateur, r,
      { cliquable: r.supervise && !r.utilisateur, totaux: r.par_utilisateur.length > 1 ? t : null }));
    $(wrapper).find("#ta-jours").html(tableau(r.par_jour, r, { jour: true }));
    $(wrapper).find("#ta-def").html(definitions(r));
  }

  $main.on("click", "#ta-users tr.cl", function () {
    const u = $(this).attr("data-user");
    if (u && !u.startsWith("(") && u !== "Guest") f_user.set_value(u);
  });
  wrapper.ta_charger = charger;
  charger();
};

frappe.pages["tableau-appels"].on_page_show = function (wrapper) {
  if (wrapper.ta_charger) wrapper.ta_charger();
};
