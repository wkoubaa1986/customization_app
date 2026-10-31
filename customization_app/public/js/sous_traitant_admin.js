/**
 * « ➕ Nouveau sous-traitant » sur la liste des employés (System Manager) — décision 30/10/2026 — et, depuis le
 * 31/10/2026, le panneau d'administration de l'onglet « Sous-traitant » (window.sousTraitant_admin, appelé par le
 * bloc HTML posé par patches/ensure_onglet_sous_traitant) : situation de chaque sous-traitant, raccourcis, marche à suivre.
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
                        if (window._st_admin_rafraichir) window._st_admin_rafraichir();
                    },
                });
            },
        });
        d.show();
    }

    window.sousTraitant_nouveau = dialogue;

    // ── Panneau d'administration de l'onglet « Sous-traitant » (31/10/2026) ─────────────────────
    // Appelé par le bloc HTML posé par patches/ensure_onglet_sous_traitant (root = shadowRoot du bloc).
    const esc = (t) => frappe.utils.escape_html(String(t == null ? "" : t));
    const dt = (d) => (d ? frappe.datetime.str_to_user(d) : "—");
    const money = (v) => (v == null ? "—" : format_currency(v, "TND", 3));

    const ETAPES = [
        ["Créer le sous-traitant", "Bouton « ➕ Nouveau sous-traitant » (ou liste des employés). En un geste : son utilisateur " +
            "(rôle Sous-traitant), sa fiche employé cochée « Sous-traitant », son entrepôt <b>Stock &lt;Nom&gt;</b>, son compte " +
            "de caisse <b>&lt;Nom&gt;</b> (sous Comptes bancaires) et sa vérification de stock planifiée."],
        ["Lui donner son stock", "Page <b>Stock et transferts</b> → onglet Transfert : Magasin → « Stock &lt;Nom&gt; ». Double validation : " +
            "il confirme la réception depuis son téléphone (Mon stock). Il vend uniquement ce qui est dans son stock, " +
            "au prix de vente standard."],
        ["Planifier ses interventions", "<b>Calendrier de travail</b> : une tâche avec lui comme « choix du staff » (Installation, Entretien, " +
            "Réparation…). Pour une commande préparée au bureau : créer la commande en <b>brouillon</b> et la lier à la tâche " +
            "(champ Commande client) ; il la retrouve à la clôture et peut y retirer/ajouter des articles de son stock."],
        ["Il travaille sur son téléphone", "Adresse <code>" + esc(window.location.origin) + "/app</code> avec son e-mail et son mot de passe. " +
            "Barre du bas : Ma journée (adresse, lien Google Maps, Clôturer, Annuler avec motif), Calendrier, Mon stock, Ma caisse. " +
            "À la clôture : articles de son stock, main d'œuvre proposée selon le type (M-I-OD / M-E-OD / M-R-OD), remise justifiée, " +
            "règlement (espèces, chèque ou traite avec photo, dette justifiée) → commande + bon de livraison créés sur son stock."],
        ["Vérifier son stock", "Le jour planifié, il compte depuis Mon stock ; vous validez dans <b>Stock et transferts</b> → Vérification " +
            "(réglage du jour / de la périodicité : Config Stock Entrepot Verification)."],
        ["Collecter sa caisse chaque semaine", "Il clôture sa période depuis <b>Ma caisse</b> (du lendemain de sa dernière remise à aujourd'hui) ; " +
            "vous collectez dans <b>Caisse journalière</b> → Clôtures de caisse. Jusque-là, espèces, chèques et traites sont sur son compte " +
            "<b>&lt;Nom&gt;</b> ; à la collecte validée, ils en sont effacés et recréés à la date de réception dans la caisse du " +
            "collecteur, sur Espèces, Chèques et Traite Bancaire. Les dettes naissent directement dans Dettes - A&S. Sa rémunération se règle hors système."],
        ["Le jour où il arrête", "Bouton « Désactiver » : fiche employé en statut Left et utilisateur désactivé (réactivable). " +
            "Pensez à rapatrier son stock (transfert vers le Magasin) et à collecter sa dernière caisse."],
    ];

    function ligneHtml(e) {
        const cl = e.derniere_cloture, vf = e.derniere_verification;
        const actif = e.status === "Active" && e.utilisateur_actif;
        const lien = (libelle, route, options) =>
            `<a href="#" data-route='${esc(JSON.stringify(route))}' data-options='${esc(JSON.stringify(options || {}))}'>${libelle}</a>`;
        return `<tr class="${actif ? "" : "text-muted"}">
            <td><span data-couleur="${esc(e.name)}" data-valeur="${esc(e.couleur)}" title="${__("Changer la couleur du calendrier")}"
                      style="display:inline-block;width:16px;height:16px;border-radius:4px;background:${esc(e.couleur)};vertical-align:middle;margin-right:6px;cursor:pointer;border:1px solid rgba(0,0,0,.15)"></span>
                <b>${esc(e.employee_name)}</b><br><small>${esc(e.user_id || "")}${e.cell_number ? " · " + esc(e.cell_number) : ""}</small>
                ${actif ? "" : `<br><span class="indicator-pill red">${__("Désactivé")}</span>`}</td>
            <td>${e.taches_jour} ${__("aujourd'hui")}<br><small>${e.taches_ouvertes} ${__("ouvertes")}</small></td>
            <td>${e.stock_articles} ${__("articles")}<br><small>${e.stock_unites} ${__("unités")}</small></td>
            <td>${money(e.solde_caisse)}<br><small>${cl ? __("dernière remise") + " " + dt(cl.date_cloture) + " (" + esc(cl.statut) + ")" : __("jamais remis")}</small></td>
            <td>${vf ? dt(vf.date) + "<br><small>" + esc(vf.statut) + "</small>" : "—"}</td>
            <td class="st-actions">
                ${lien(__("Fiche"), ["Form", "Employee", e.name])}
                ${lien(__("Tâches"), ["List", "Tache de travail"], { custom_choix_du_staff: e.name })}
                ${lien(__("Stock"), ["stock-entrepots"], { entrepot: e.custom_warehouse })}
                ${lien(__("Commandes"), ["List", "Sales Order"], { owner: e.user_id })}
                ${lien(__("Vérifications"), ["List", "Verification Stock"], { entrepot: e.custom_warehouse })}
                ${lien(__("Clôtures"), ["List", "Cloture Caisse"], { caisse: e.employee_name })}
                ${lien(__("Compte"), ["query-report", "General Ledger"], { account: e.custom_compte_caisse, company: frappe.defaults.get_default("company") })}
                <a href="#" data-activer="${esc(e.name)}" data-actif="${actif ? 0 : 1}" class="${actif ? "text-danger" : ""}">${actif ? __("Désactiver") : __("Réactiver")}</a>
            </td></tr>`;
    }

    window.sousTraitant_admin = function (root) {
        const boite = root.querySelector(".st-admin");
        if (!boite) return;
        const rendre = async () => {
            let liste = [];
            try { liste = (await frappe.call({ method: "customization_app.sous_traitant.liste" })).message || []; }
            catch (e) { boite.innerHTML = `<p class="text-danger">${__("Impossible de lire les sous-traitants.")}</p>`; return; }
            boite.innerHTML = `
                <style>
                  .st-admin table { width:100%; font-size:var(--text-sm); } .st-admin th { font-weight:600; color:var(--text-muted); }
                  .st-admin td { vertical-align:top; padding:8px 6px; border-top:1px solid var(--border-color); }
                  .st-actions a { display:inline-block; margin:0 10px 4px 0; white-space:nowrap; }
                  .st-etapes { margin:18px 0 0; padding-left:0; list-style:none; counter-reset:e; }
                  .st-etapes li { position:relative; padding:6px 0 6px 34px; counter-increment:e; }
                  .st-etapes li::before { content:counter(e); position:absolute; left:0; top:6px; width:24px; height:24px; border-radius:50%;
                    background:var(--primary); color:#fff; font-weight:600; text-align:center; line-height:24px; font-size:12px; }
                </style>
                <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px">
                  <div>${liste.length} ${__("sous-traitant(s)")}</div>
                  <div><button class="btn btn-primary btn-sm" data-nouveau>➕ ${__("Nouveau sous-traitant")}</button>
                       <button class="btn btn-default btn-sm" data-rafraichir>↻</button></div>
                </div>
                ${liste.length ? `<table><thead><tr><th>${__("Sous-traitant")}</th><th>${__("Interventions")}</th><th>${__("Son stock")}</th>
                    <th>${__("Sa caisse")}</th><th>${__("Vérification")}</th><th></th></tr></thead>
                    <tbody>${liste.map(ligneHtml).join("")}</tbody></table>`
                    : `<p class="text-muted">${__("Aucun sous-traitant pour l'instant.")}</p>`}
                <details ${liste.length ? "" : "open"}><summary style="cursor:pointer;font-weight:600;margin-top:14px">📋 ${__("Marche à suivre")}</summary>
                  <ol class="st-etapes">${ETAPES.map(([t, d]) => `<li><b>${t}</b><br><span class="text-muted">${d}</span></li>`).join("")}</ol>
                </details>`;
            boite.querySelector("[data-nouveau]").onclick = () => dialogue();
            boite.querySelector("[data-rafraichir]").onclick = rendre;
            boite.querySelectorAll("[data-route]").forEach((a) => a.onclick = (ev) => {
                ev.preventDefault();
                const options = JSON.parse(a.getAttribute("data-options") || "{}");
                Object.keys(options).forEach((k) => { if (options[k] == null) delete options[k]; });
                frappe.route_options = options;
                frappe.set_route(JSON.parse(a.getAttribute("data-route")));
            });
            // Pastille → sélecteur de couleur ; enregistrée sur la fiche et appliquée à toutes ses tâches ouvertes.
            boite.querySelectorAll("[data-couleur]").forEach((sw) => sw.onclick = () => {
                const d = new frappe.ui.Dialog({
                    title: __("Couleur du calendrier"),
                    fields: [{ fieldtype: "Color", fieldname: "couleur", label: __("Couleur"), reqd: 1, default: sw.getAttribute("data-valeur") },
                             { fieldtype: "HTML", options: `<div class="text-muted">${__("Elle s'applique tout de suite à toutes ses tâches ouvertes du calendrier.")}</div>` }],
                    primary_action_label: __("Appliquer"),
                    primary_action: (v) => frappe.call({ method: "customization_app.sous_traitant.changer_couleur",
                        args: { employe: sw.getAttribute("data-couleur"), couleur: v.couleur }, freeze: true,
                        callback: (r) => { d.hide(); const n = (r.message || {}).taches_recolorees || 0;
                            frappe.show_alert({ message: __("Couleur enregistrée : {0} tâche(s) ouverte(s) recolorée(s).", [n]), indicator: "green" }, 5);
                            rendre(); } }),
                });
                d.show();
            });
            boite.querySelectorAll("[data-activer]").forEach((a) => a.onclick = (ev) => {
                ev.preventDefault();
                const actif = Number(a.getAttribute("data-actif"));
                frappe.confirm(actif ? __("Réactiver ce sous-traitant (employé actif, utilisateur rétabli) ?")
                                     : __("Désactiver ce sous-traitant ? Sa fiche passe en « Left » et son utilisateur ne peut plus se connecter."),
                    () => frappe.call({ method: "customization_app.sous_traitant.activer",
                                        args: { employe: a.getAttribute("data-activer"), actif }, freeze: true, callback: rendre }));
            });
        };
        window._st_admin_rafraichir = rendre;
        rendre();
    };

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
