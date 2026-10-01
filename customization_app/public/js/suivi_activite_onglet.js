/**
 * L'onglet « Suivi d'activité » ouvre DIRECTEMENT l'outil (page suivi-activite).
 *
 * Frappe v15 ne sait pas faire pointer une entrée de la barre latérale vers une page : un onglet
 * est toujours un espace de travail, et l'outil se trouvait derrière le raccourci « Mes
 * activités » — les boutons (Nouvelle activité, Liste pour l'équipe) restaient invisibles
 * (constaté le 01/10/2026). On remplace l'entrée d'historique (replace_route) : le bouton
 * « retour » du navigateur ne ramène pas sur l'onglet vide, donc pas de boucle.
 */
(function () {
  const ouvrir_l_outil = function () {
    const r = (frappe.get_route && frappe.get_route()) || [];
    if (r[0] === "Workspaces" && r.length === 2 && r[1] === "Suivi d'activité") {
      frappe.route_flags.replace_route = true;
      frappe.set_route("suivi-activite");
    }
  };
  // Branché dès le chargement (arrivée directe par l'URL de l'onglet) ET à chaque navigation.
  if (frappe.router && frappe.router.on) frappe.router.on("change", ouvrir_l_outil);
  $(document).on("app_ready", ouvrir_l_outil);
})();
