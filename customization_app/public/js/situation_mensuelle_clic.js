/**
 * Les graphes « Situation Mensuelle » et « Pertes et Profits » de l'onglet Comptabilité ouvrent leur page de détail
 * au clic, sur la période choisie dans l'entonnoir du graphe (mois, année, total).
 *
 * Un graphe d'espace de travail Frappe n'a pas d'action au clic : on la lui donne en repérant le
 * widget par son titre, après chaque rendu de l'onglet (les widgets arrivent en différé).
 */
(function () {
  const GRAPHES = [
    { titre: "situation mensuelle", page: "situation-mensuelle", option: "sm_periode",
      methode: "customization_app.situation_mensuelle.periode_du_graphe" },
    // Pertes et Profits (05/11/2026) : la page /app/pertes-et-profits, où l'on choisit ce qui compte.
    { titre: "pertes et profits", page: "pertes-et-profits", option: "pp_periode",
      methode: "customization_app.pertes_profits.periode_du_graphe" },
  ];
  const brancher = function () {
    $(".widget.dashboard-widget-box").each(function () {
      const $w = $(this);
      const titre = ($w.find(".widget-title").first().text() || "").trim().toLowerCase();
      const g = GRAPHES.find((x) => titre.startsWith(x.titre));
      if (!g || $w.attr("data-clic-detail")) return;
      $w.attr("data-clic-detail", g.page);
      $w.find(".widget-body").css("cursor", "pointer").attr("title", "Ouvrir le détail")
        .on("click", function (e) {
          if ($(e.target).closest(".dropdown, .filter-chart, .chart-actions").length) return;
          frappe.call({ method: g.methode }).then((r) => {
            frappe.route_options = { [g.option]: r.message || {} };
            frappe.set_route(g.page);
          });
        });
    });
  };
  const guetter = function () {
    const r = (frappe.get_route && frappe.get_route()) || [];
    if (r[0] !== "Workspaces") return;
    let essais = 0;
    const t = setInterval(function () { brancher(); if (++essais > 20) clearInterval(t); }, 300);
  };
  if (frappe.router && frappe.router.on) frappe.router.on("change", guetter);
  $(document).on("app_ready", guetter);
})();
