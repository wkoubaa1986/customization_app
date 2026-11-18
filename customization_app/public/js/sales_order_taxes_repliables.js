// Commande client — la partie « Taxes » est repliable (demande du 18/11/2026).
//
// La section « Taxes » est rendue repliable par un réglage (patch taxes_repliables_commande) : Frappe l'ouvre
// fermée et son titre la déplie. Mais le TABLEAU des taxes et le TOTAL des taxes sont dans deux sections sans
// titre, juste en dessous : Frappe ne sait pas les replier. Ici, elles suivent la section « Taxes » — fermées
// avec elle, ouvertes avec elle. Aucun champ n'est supprimé ni modifié : c'est seulement de l'affichage.
(() => {
    const TETE = "taxes_section", SUITE = ["section_break_40", "section_break_43"];

    function suivre(frm) {
        const sections = frm.layout && frm.layout.sections_dict;
        const tete = sections && sections[TETE];
        if (!tete || !tete.df || !tete.df.collapsible || !tete.head) return;
        if (!tete.__suite_branchee) {
            tete.__suite_branchee = true;
            // Après le clic de Frappe sur le titre (qui vient de replier ou déplier la section).
            tete.head.on("click", () => setTimeout(() => suivre(frm), 0));
        }
        const fermee = tete.is_collapsed();
        SUITE.forEach((nom) => { const s = sections[nom]; if (s && s.wrapper) $(s.wrapper).toggle(!fermee); });
    }

    frappe.ui.form.on("Sales Order", {
        refresh(frm) { suivre(frm); },
        onload_post_render(frm) { suivre(frm); },
    });
})();
