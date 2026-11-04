/**
 * L'onglet « Clients Pro » de la barre latérale ouvre DIRECTEMENT le tableau de bord (page clients-b2b).
 *
 * Même mécanique que suivi_activite_onglet.js : Frappe v15 ne sait pas faire pointer un onglet vers une
 * page, un onglet est toujours un espace de travail. On remplace l'entrée d'historique (replace_route) pour
 * que le bouton « retour » ne ramène pas sur l'espace vide.
 *
 * ⚠️ L'ONGLET NE PEUT PAS S'APPELER « Clients B2B » : Frappe refuse un espace de travail dont le nom, en
 * minuscules avec des tirets, est celui d'une page (validate_route_conflict) — et la page est clients-b2b.
 */
(function () {
  const ouvrir_l_outil = function () {
    const r = (frappe.get_route && frappe.get_route()) || [];
    if (r[0] === "Workspaces" && r.length === 2 && r[1] === "Clients Pro") {
      frappe.route_flags.replace_route = true;
      frappe.set_route("clients-b2b");
    }
  };
  if (frappe.router && frappe.router.on) frappe.router.on("change", ouvrir_l_outil);
  $(document).on("app_ready", ouvrir_l_outil);
})();
