/**
 * Le graphe « Situation Mensuelle » de l'onglet Comptabilité ouvre la page de détail au clic.
 *
 * Un graphe d'espace de travail Frappe n'a pas d'action au clic : on la lui donne en repérant le
 * widget par son titre, après chaque rendu de l'onglet (les widgets arrivent en différé).
 */
(function () {
  const TITRE = "situation mensuelle";
  const brancher = function () {
    $(".widget.dashboard-widget-box").each(function () {
      const $w = $(this);
      const titre = ($w.find(".widget-title").first().text() || "").trim().toLowerCase();
      if (!titre.startsWith(TITRE) || $w.attr("data-sm-clic")) return;
      $w.attr("data-sm-clic", "1");
      $w.find(".widget-body").css("cursor", "pointer").attr("title", "Ouvrir le détail")
        .on("click", function (e) {
          if ($(e.target).closest(".dropdown, .filter-chart, .chart-actions").length) return;
          // Ouvrir la page sur la période choisie dans l'entonnoir du graphe (mois, année, total).
          frappe.call({ method: "customization_app.situation_mensuelle.periode_du_graphe" }).then((r) => {
            frappe.route_options = { sm_periode: r.message || {} };
            frappe.set_route("situation-mensuelle");
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
