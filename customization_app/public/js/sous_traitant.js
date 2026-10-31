/**
 * Sous-traitant connecté : il n'a besoin que de SA journée (décision utilisateur 30/10/2026), et il ne
 * doit voir que SES choses (31/10/2026) : ni barre latérale des espaces de travail, ni listes des autres.
 *
 * `frappe.boot.sous_traitant` est posé par le serveur (sous_traitant.boot_session). Dès que le Desk
 * s'ouvre sur l'accueil ou un espace de travail, on l'amène sur la liste de ses tâches (filtrée par le
 * serveur) et on ouvre « Ma journée ». Une barre fixe en bas de l'écran le fait circuler entre ses quatre
 * écrans : Ma journée, Agenda (jour / semaine / mois, agenda_sous_traitant.js), Mon stock, Ma caisse.
 */
(function () {
    const st = frappe.boot && frappe.boot.sous_traitant;
    if (!st) return;

    const ACCUEIL = new Set(["", "home", "workspaces", "workspace", "app"]);
    // Sa page de fond : « Mon agenda » (page agenda-sous-traitant, jour / semaine / mois). « Ma journée »
    // s'ouvre par-dessus. La liste et le calendrier des tâches du Desk ne lui sont jamais montrés (31/10/2026).
    const FOND = ["agenda-sous-traitant"];
    let deja = false;

    // ── Son écran : pas de barre latérale (espaces de travail, filtres de liste), la page prend toute la largeur.
    const style = document.createElement("style");
    style.textContent = `
        .layout-side-section, .sidebar-toggle-btn, .page-head .sidebar-toggle-btn { display: none !important; }
        .layout-main-section-wrapper { flex: 0 0 100% !important; max-width: 100% !important; }
        body { padding-bottom: 64px; }
        .st-nav { position: fixed; left: 0; right: 0; bottom: 0; z-index: 1030; display: flex;
                  background: #fff; border-top: 1px solid var(--border-color, #e4e8ee);
                  box-shadow: 0 -2px 8px rgba(0,0,0,.06); padding-bottom: env(safe-area-inset-bottom); }
        .st-nav button { flex: 1 1 25%; min-height: 56px; border: none; background: none; font-size: 11.5px;
                         font-weight: 600; color: var(--text-muted, #6b7280); display: flex; flex-direction: column;
                         align-items: center; justify-content: center; gap: 2px; cursor: pointer; }
        .st-nav button .ic { font-size: 20px; line-height: 1; }
        .st-nav button.on { color: #2490ef; }
        .modal.mj-modal .modal-content { padding-bottom: 64px; }`;
    document.head.appendChild(style);

    // ── La barre de navigation.
    const ECRANS = [
        { cle: "journee",    ic: "☀️", libelle: __("Ma journée"),  aller: () => window.maJournee_ouvrir && window.maJournee_ouvrir() },
        { cle: "calendrier", ic: "📅", libelle: __("Agenda"),      aller: () => frappe.set_route(...FOND) },
        { cle: "stock",      ic: "📦", libelle: __("Mon stock"),   aller: () => frappe.set_route("stock-entrepots") },
        { cle: "caisse",     ic: "💰", libelle: __("Ma caisse"),   aller: () => frappe.set_route("caisse-journaliere") },
    ];

    function ecran_courant() {
        const r = frappe.get_route() || [];
        if ($(".modal.show.mj-modal").length) return "journee";
        if (r[0] === "agenda-sous-traitant") return "calendrier";
        if (r[0] === "stock-entrepots") return "stock";
        if (r[0] === "caisse-journaliere") return "caisse";
        return "";
    }

    function peindre() {
        let $nav = $(".st-nav");
        if (!$nav.length) {
            $nav = $(`<div class="st-nav">${ECRANS.map((e) =>
                `<button type="button" data-ecran="${e.cle}"><span class="ic">${e.ic}</span>${e.libelle}</button>`).join("")}</div>`)
                .appendTo(document.body);
            $nav.on("click", "button", (ev) => {
                const e = ECRANS.find((x) => x.cle === $(ev.currentTarget).attr("data-ecran"));
                // Les fenêtres ouvertes (Ma journée, agenda, clôture) se ferment : on change d'écran.
                $(".modal.show").modal("hide");
                e.aller();
            });
        }
        const courant = ecran_courant();
        $nav.find("button").each((_, b) => $(b).toggleClass("on", $(b).attr("data-ecran") === courant));
    }

    function accueillir() {
        peindre();
        const r = frappe.get_route() || [];
        // Liste, calendrier ou rapport des tâches : pas pour lui — l'app lui montre ses tâches dans l'agenda.
        if (r[0] === "tache-de-travail" && r[1] !== "Form") { frappe.set_route(...FOND); return; }
        if (deja) return;
        if (!ACCUEIL.has((r[0] || "").toLowerCase()) && !(r[0] === "Workspaces")) return;
        deja = true;
        frappe.set_route(...FOND).then(() => {
            setTimeout(() => { if (window.maJournee_ouvrir) window.maJournee_ouvrir(); }, 900);
        });
    }

    window._st_nav_peindre = peindre;
    $(document).on("shown.bs.modal hidden.bs.modal", () => setTimeout(peindre, 50));
    $(document).on("app_ready", () => setTimeout(accueillir, 300));
    frappe.router.on("change", accueillir);
})();
