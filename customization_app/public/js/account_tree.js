/**
 * Plan comptable (arbre) : des sections en couleur (demande 04/11/2026).
 *
 *     Comptes fermés     rouge
 *     Comptes bancaires  bleu
 *     Liquidités         vert
 *     Liste créance      orange
 *
 * La couleur est posée sur le groupe et descend à tous ses comptes : la ligne entière, nom ET
 * solde (précisé par l'utilisateur le 04/11/2026). Si une section en contient une autre, la plus
 * proche l'emporte.
 *
 * ⚠️ LE REPÈRE EST LE NOM DU GROUPE, SANS L'ABRÉVIATION DE LA SOCIÉTÉ. Le nœud ne porte que
 * `value` (« Liste créance - A&S ») : ni `account_type`, ni `disabled` ne descendent jusqu'à
 * l'arbre (`erpnext.accounts.utils.get_children`). Renommer un de ces groupes lui fait perdre sa
 * couleur : il suffit d'ajuster SECTIONS. Un compte désactivé laissé hors de « Comptes fermés »
 * n'est donc pas rouge.
 *
 * Chargé après account_tree.js d'ERPNext (hook `doctype_tree_js`) : on complète ses réglages,
 * on ne les remplace pas.
 */
(function () {
    const SECTIONS = [
        { groupe: "Comptes fermés", couleur: "rouge" },
        { groupe: "Comptes bancaires", couleur: "bleu" },
        { groupe: "Liquidités", couleur: "vert" },
        { groupe: "Liste créance", couleur: "orange" },
    ];

    // ⚠️ LE LIBELLÉ EST UN LIEN (`<a class="tree-label">`) : il n'hérite pas de la couleur de la
    // ligne, le `a { color }` global de Frappe l'en empêche. On le vise donc directement.
    // Le solde (`.balance-area`) a sa couleur en !important dans Frappe : il faut l'être aussi.
    // La variable est redéclarée sur chaque section, donc la section la plus proche gagne.
    const CSS = `
        .tree-node.coa-rouge  { --coa-couleur: var(--red-600); }
        .tree-node.coa-bleu   { --coa-couleur: var(--blue-600); }
        .tree-node.coa-vert   { --coa-couleur: var(--green-600); }
        .tree-node.coa-orange { --coa-couleur: var(--orange-600); }
        [data-theme="dark"] .tree-node.coa-rouge  { --coa-couleur: var(--red-500); }
        [data-theme="dark"] .tree-node.coa-bleu   { --coa-couleur: var(--blue-500); }
        [data-theme="dark"] .tree-node.coa-vert   { --coa-couleur: var(--green-500); }
        [data-theme="dark"] .tree-node.coa-orange { --coa-couleur: var(--orange-500); }
        .tree-node.coa-section .tree-link .tree-label { color: var(--coa-couleur); }
        .tree-node.coa-section .balance-area { color: var(--coa-couleur) !important; }
    `;

    const norm = (s) => (s || "").normalize("NFC").trim().toLowerCase();

    function section(node) {
        const libelle = norm(node.label);
        return SECTIONS.find(({ groupe }) => {
            const g = norm(groupe);
            return libelle === g || libelle.startsWith(g + " - ");
        });
    }

    function colorer(node) {
        if (node.is_root || !node.expandable || !node.parent || !node.parent.addClass) return;
        const s = section(node);
        if (s) node.parent.addClass(`coa-section coa-${s.couleur}`);
    }

    if (!document.getElementById("coa-sections-css")) {
        $("<style>").attr("id", "coa-sections-css").text(CSS).appendTo(document.head);
    }

    const reglages = (frappe.treeview_settings = frappe.treeview_settings || {});
    reglages.Account = reglages.Account || {};
    // Le script est réévalué à chaque rechargement de la méta : ne pas empiler les enveloppes.
    if (reglages.Account.onrender && reglages.Account.onrender.__coa_sections) return;

    const avant = reglages.Account.onrender;
    const onrender = function (node) {
        if (avant) avant.apply(this, arguments);
        colorer(node);
    };
    onrender.__coa_sections = true;
    reglages.Account.onrender = onrender;
})();
