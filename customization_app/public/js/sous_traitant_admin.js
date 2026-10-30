/**
 * « ➕ Nouveau sous-traitant » sur la liste des employés (System Manager) — décision 30/10/2026.
 *
 * Un seul formulaire, et le serveur (sous_traitant.creer) fabrique tout : l'utilisateur avec le
 * rôle Sous-traitant, la fiche employé, son entrepôt « Stock <Nom> », son compte de caisse
 * « <Nom> », et sa vérification de stock planifiée (jour, heure, chaque semaine ou toutes les
 * deux semaines) comme pour les techniciens.
 */
(function () {
    const JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];

    function dialogue() {
        const d = new frappe.ui.Dialog({
            title: __("Nouveau sous-traitant"),
            fields: [
                { fieldtype: "Data", fieldname: "nom", label: __("Nom complet"), reqd: 1 },
                { fieldtype: "Data", fieldname: "email", label: __("E-mail (identifiant)"), reqd: 1, options: "Email" },
                { fieldtype: "Password", fieldname: "mot_de_passe", label: __("Mot de passe"), reqd: 1 },
                { fieldtype: "Data", fieldname: "telephone", label: __("Téléphone"), options: "Phone" },
                { fieldtype: "Column Break" },
                { fieldtype: "Select", fieldname: "genre", label: __("Genre"), options: "Male\nFemale", default: "Male" },
                { fieldtype: "Date", fieldname: "date_naissance", label: __("Date de naissance") },
                { fieldtype: "Color", fieldname: "couleur", label: __("Couleur du calendrier"),
                  description: __("Vide : une couleur de la palette lui est attribuée.") },
                { fieldtype: "Section Break", label: __("Vérification de stock planifiée") },
                { fieldtype: "Select", fieldname: "jour_verification", label: __("Jour"), options: JOURS.join("\n"), default: "Lundi" },
                { fieldtype: "Time", fieldname: "heure_verification", label: __("Heure"), default: "09:00:00" },
                { fieldtype: "Select", fieldname: "periodicite", label: __("Périodicité"),
                  options: "Chaque semaine\nToutes les deux semaines", default: "Chaque semaine" },
            ],
            primary_action_label: __("Créer"),
            primary_action: (v) => {
                frappe.call({
                    method: "customization_app.sous_traitant.creer",
                    args: { nom: v.nom, email: v.email, mot_de_passe: v.mot_de_passe, telephone: v.telephone,
                            genre: v.genre, date_naissance: v.date_naissance, couleur: v.couleur,
                            jour_verification: v.jour_verification,
                            heure_verification: (v.heure_verification || "09:00").slice(0, 5),
                            periodicite: v.periodicite },
                    freeze: true,
                    callback: (r) => {
                        const m = r.message || {};
                        d.hide();
                        frappe.msgprint({ title: __("Sous-traitant créé"), indicator: "green", message: [
                            `${__("Utilisateur")} : <b>${frappe.utils.escape_html(m.utilisateur)}</b> (${__("rôle Sous-traitant")})`,
                            `${__("Employé")} : <a href="/app/employee/${encodeURIComponent(m.employe)}">${frappe.utils.escape_html(m.employe)}</a>`,
                            `${__("Entrepôt")} : <b>${frappe.utils.escape_html(m.entrepot)}</b>`,
                            `${__("Compte de caisse")} : <b>${frappe.utils.escape_html(m.compte)}</b>`,
                            `${__("Couleur")} : <span style="display:inline-block;width:14px;height:14px;background:${m.couleur};border-radius:3px;vertical-align:middle"></span>`,
                            __("Pensez à lui transférer son stock depuis la page Stock (double validation)."),
                        ].join("<br>") });
                        if (cur_list && cur_list.doctype === "Employee") cur_list.refresh();
                    },
                });
            },
        });
        d.show();
    }

    const _after_render = frappe.views.ListView.prototype.after_render;
    frappe.views.ListView.prototype.after_render = function () {
        _after_render.apply(this, arguments);
        if (this.doctype !== "Employee" || this._st_bouton || !frappe.user.has_role("System Manager")) return;
        try {
            this._st_bouton = true;
            this.page.add_inner_button(__("➕ Nouveau sous-traitant"), dialogue);
        } catch (e) { this._st_bouton = false; }
    };
})();
