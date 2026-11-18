/**
 * Tâche de travail — consultation seule quand la tâche est celle d'un employé hors de ma liste (18/11/2026).
 *
 * Tout employé peut désormais OUVRIR toute tâche (l'historique d'un client ne dépend pas de qui a fait le travail,
 * même parti) ; mais il ne peut modifier que celles des employés de sa liste. Le serveur refuse déjà
 * l'enregistrement (customization_app.tache_lecture) : ici on le dit d'emblée, fiche figée, plutôt que de laisser
 * saisir puis refuser.
 */
(function () {
    frappe.ui.form.on("Tache de travail", {
        refresh(frm) {
            if (frm.is_new()) return;
            const nom = frm.doc.name;
            frappe.call({ method: "customization_app.tache_lecture.droits", args: { tache: nom } }).then((r) => {
                const d = r.message || {};
                if (frm.doc.name !== nom || d.modifiable !== false) return;
                frm.disable_form();
                frm.set_intro(__("Consultation seule : cette tâche est celle de {0}.", [frappe.utils.escape_html(d.employe || "—")]), "blue");
            });
        },
    });
})();
