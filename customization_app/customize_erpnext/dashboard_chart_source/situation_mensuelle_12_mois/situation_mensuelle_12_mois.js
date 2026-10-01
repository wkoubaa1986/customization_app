frappe.provide("frappe.dashboards.chart_sources");

// ⚠️ Ce fichier est réévalué à chaque ouverture du graphe : rien de déclaré au niveau global
// (un « const » au premier niveau lève « has already been declared » au deuxième chargement).
(function () {
	// Mois et années calculés au chargement, comme dans l'ancien rapport « Test » (dès octobre 2023).
	const noms = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"];
	const mois = [];
	const d = new Date(2023, 9, 1), auj = new Date();
	while (d <= auj) { mois.push(noms[d.getMonth()] + " " + d.getFullYear()); d.setMonth(d.getMonth() + 1); }
	mois.reverse();
	const annees = [];
	for (let a = auj.getFullYear(); a >= 2023; a--) annees.push(String(a));

	frappe.dashboards.chart_sources["Situation Mensuelle 12 mois"] = {
		method: "customization_app.customize_erpnext.dashboard_chart_source.situation_mensuelle_12_mois.situation_mensuelle_12_mois.get",
		filters: [
			{ fieldname: "vue", label: "Affichage", fieldtype: "Select",
			  options: ["12 derniers mois", "Un mois", "Une année (mois par mois)", "Par année", "Total"], default: "12 derniers mois" },
			{ fieldname: "mois", label: "Mois", fieldtype: "Select", options: mois, default: mois[0],
			  depends_on: "eval:doc.vue == 'Un mois'" },
			{ fieldname: "annee", label: "Année", fieldtype: "Select", options: annees, default: annees[0],
			  depends_on: "eval:doc.vue == 'Une année (mois par mois)'" },
		],
	};
})();
