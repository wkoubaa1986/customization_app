/**
 * Sous-traitant connecté : il n'a besoin que de SA journée (décision utilisateur 30/10/2026).
 *
 * `frappe.boot.sous_traitant` est posé par le serveur (sous_traitant.boot_session). Dès que
 * le Desk s'ouvre sur l'accueil ou un espace de travail, on l'amène sur le calendrier des
 * tâches (filtré à ses tâches par le serveur) et on ouvre « Ma journée ». Les pages qu'il
 * utilise par ailleurs — Stock de son entrepôt, Caisse journalière — restent accessibles.
 */
(function () {
    const st = frappe.boot && frappe.boot.sous_traitant;
    if (!st) return;

    const ACCUEIL = new Set(["", "home", "workspaces", "workspace", "app"]);
    let deja = false;

    function accueillir() {
        if (deja) return;
        const r = frappe.get_route() || [];
        if (!ACCUEIL.has((r[0] || "").toLowerCase()) && !(r[0] === "Workspaces")) return;
        deja = true;
        frappe.set_route("tache-de-travail", "view", "calendar", "Calendrier de travail").then(() => {
            setTimeout(() => { if (window.maJournee_ouvrir) window.maJournee_ouvrir(); }, 900);
        });
    }

    $(document).on("app_ready", () => setTimeout(accueillir, 300));
    frappe.router.on("change", accueillir);
})();
