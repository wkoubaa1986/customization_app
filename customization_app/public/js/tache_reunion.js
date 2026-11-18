/**
 * « 👥 Réunion » — bouton de la liste / du calendrier des tâches.
 *
 * Depuis le 18/11/2026 il crée une VRAIE réunion du Suivi d'activité (« comme à partir de l'onglet Réunion, avec
 * lien ») : date, heure, lieu, participants, lien visio, ordre du jour et compte-rendu — elle s'affiche dans le
 * calendrier comme les autres. Même service que l'onglet (customization_app.reunion_activite.creer), mêmes valeurs
 * par défaut (reunion_activite.preparer_creation). Avant, le bouton créait une tâche « Autre » par participant
 * (04/10/2026, taches_groupe.creer_reunion) : sans lien, sans ordre du jour.
 *
 * Les activités suivies par la réunion se choisissent ensuite dans sa page (Suivi d'activité) : rien de coché =
 * toutes les activités des participants, comme dans l'onglet.
 */
frappe.provide("customization_app");

customization_app.reunion = (function () {
    const API = "customization_app.reunion_activite";
    const esc = frappe.utils.escape_html;
    const REPETE = ["Non", "Chaque semaine", "Toutes les 2 semaines", "Toutes les 3 semaines", "Toutes les 4 semaines"];

    function ouvrir(apres) {
        frappe.call({ method: API + ".preparer_creation" }).then((r) => {
            const c = r.message || {};
            if (!c.peut_gerer) {
                frappe.msgprint({ title: __("Réunion"), indicator: "orange",
                    message: __("Seul un responsable du suivi d’activité peut créer une réunion.") });
                return;
            }
            dialogue(c, apres);
        });
    }

    function dialogue(c, apres) {
        const equipe = c.employes || [];
        const d = new frappe.ui.Dialog({
            title: __("👥 Nouvelle réunion"),
            size: "large",
            fields: [
                { fieldtype: "Date", fieldname: "date", label: __("Date"), reqd: 1, default: c.prochaine_date || frappe.datetime.get_today() },
                { fieldtype: "Time", fieldname: "heure", label: __("Heure"), reqd: 1, default: c.heure || "09:00:00" },
                { fieldtype: "Column Break" },
                { fieldtype: "Data", fieldname: "titre", label: __("Titre"),
                  description: __("Vide = « Réunion d’équipe · point du … », ou « Point avec &lt;employé&gt; » s’il n’y en a qu’un") },
                { fieldtype: "Data", fieldname: "lieu", label: __("Lieu"), default: c.lieu || "" },
                { fieldtype: "Section Break", label: __("Répétition") },
                { fieldtype: "Select", fieldname: "repete", label: __("Répéter"), options: REPETE.join("\n"), default: "Non",
                  description: __("Une réunion par occurrence, chacune avec son lien visio, de la date ci-dessus jusqu’à la date de fin.") },
                { fieldtype: "Column Break" },
                { fieldtype: "Date", fieldname: "jusquau", label: __("Jusqu’au"), depends_on: "eval:doc.repete && doc.repete !== 'Non'" },
                { fieldtype: "Section Break", label: __("Participants") },
                { fieldtype: "HTML", fieldname: "participants" },
            ],
            primary_action_label: __("Créer la réunion"),
            primary_action(v) {
                const ids = d.$wrapper.find(".rn-emp:checked").map(function () { return $(this).val(); }).get();
                if (!ids.length) { frappe.msgprint(__("Choisissez au moins un participant.")); return; }
                const n = Math.max(0, REPETE.indexOf(v.repete));
                if (n && !v.jusquau) { frappe.msgprint(__("Indiquez la date de fin de la répétition.")); return; }
                frappe.call({
                    method: API + ".creer", freeze: true, freeze_message: __("Création de la réunion…"),
                    args: { date: v.date, heure: v.heure, titre: v.titre || "", lieu: v.lieu || "", employes: JSON.stringify(ids),
                            toutes_les: n, jusquau: n ? v.jusquau : null },
                }).then((rr) => {
                    d.hide();
                    confirmer(rr.message || {}, v);
                    if (apres) apres();
                });
            },
        });
        const cases = equipe.map((e) => `
            <label style="display:flex;align-items:center;gap:6px;padding:4px 10px;border:1px solid #e4e8ee;
                          border-radius:999px;margin:0 6px 6px 0;cursor:pointer">
                <input type="checkbox" class="rn-emp" value="${esc(e.name)}" checked> ${esc(e.employee_name)}
            </label>`).join("");
        d.fields_dict.participants.$wrapper.html(`
            <div style="margin-bottom:6px">
                <a href="#" class="rn-tous">${__("Tout cocher")}</a> · <a href="#" class="rn-aucun">${__("Tout décocher")}</a>
                <span class="text-muted small"> — ${__("un seul employé = point individuel")}</span>
            </div>
            <div style="display:flex;flex-wrap:wrap">${cases}</div>
            <div class="text-muted small" style="margin-top:6px">${c.visio
                ? __("Un lien visio est créé avec la réunion.")
                : __("Le lien visio est désactivé dans le réglage du suivi d’activité : la réunion sera créée sans lien.")}</div>`);
        d.$wrapper.on("click", ".rn-tous", (ev) => { ev.preventDefault(); d.$wrapper.find(".rn-emp").prop("checked", true); });
        d.$wrapper.on("click", ".rn-aucun", (ev) => { ev.preventDefault(); d.$wrapper.find(".rn-emp").prop("checked", false); });
        d.show();
    }

    /** La réunion est créée : son lien (à copier ou à ouvrir) et l'accès à sa page. */
    function confirmer(m, v) {
        const noms = m.noms || [];
        const lien = m.lien_visio || "";
        const f = new frappe.ui.Dialog({
            title: noms.length > 1 ? __("{0} réunions créées (série)", [noms.length]) : __("Réunion créée"),
            fields: [{ fieldtype: "HTML", fieldname: "corps" }],
            primary_action_label: __("Ouvrir la réunion"),
            primary_action() {
                f.hide();
                frappe.route_options = { reunion: m.name };
                frappe.set_route("suivi-activite");
            },
            secondary_action_label: __("Fermer"),
            secondary_action() { f.hide(); },
        });
        f.fields_dict.corps.$wrapper.html(`
            <div style="font-size:14px;margin-bottom:10px">${esc(frappe.datetime.str_to_user(v.date))} · ${esc((v.heure || "").slice(0, 5))}
                ${v.lieu ? " · " + esc(v.lieu) : ""}</div>
            ${lien ? `<div class="text-muted small">${__("Lien visio")}</div>
                <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:2px">
                    <a href="${esc(lien)}" target="_blank" rel="noopener" style="word-break:break-all">${esc(lien)}</a>
                    <button type="button" class="btn btn-default btn-xs rn-copier">${__("Copier le lien")}</button>
                </div>` : `<div class="text-muted">${__("Réunion créée sans lien visio (désactivé dans le réglage).")}</div>`}
            ${noms.length > 1 ? `<div class="text-muted small" style="margin-top:8px">${__("Chaque réunion de la série a son propre lien, dans sa page.")}</div>` : ""}`);
        f.$wrapper.on("click", ".rn-copier", () => frappe.utils.copy_to_clipboard(lien));
        f.show();
    }

    return { ouvrir };
})();

(function () {
    const _after_render = frappe.views.ListView.prototype.after_render;
    frappe.views.ListView.prototype.after_render = function () {
        _after_render.apply(this, arguments);
        if (this.doctype !== "Tache de travail") return;
        try {
            if (this.__tache_bouton_reunion) return;
            this.__tache_bouton_reunion = true;
            this.page.add_inner_button(__("👥 Réunion"), () => customization_app.reunion.ouvrir(() => this.refresh()));
        } catch (e) {
            console.error("Bouton réunion :", e);
        }
    };

    // Espace « Calendrier » : même bouton flottant que « Gérer les interventions », juste au-dessus.
    const ESPACE = "Calendrier", ID = "rn-fab";
    function verifier() {
        const route = (frappe.get_route && frappe.get_route()) || [];
        const present = document.getElementById(ID);
        if (route[0] === "Workspaces" && route[1] === ESPACE) {
            if (present) return;
            const b = document.createElement("button");
            b.id = ID; b.innerHTML = "👥 Réunion";
            b.title = "Créer une réunion du suivi d’activité, avec son lien visio";
            Object.assign(b.style, { position: "fixed", bottom: "132px", right: "28px", zIndex: "9999",
                padding: "10px 16px", borderRadius: "999px", border: "none", background: "#7c3aed", color: "#fff",
                fontWeight: "600", cursor: "pointer", boxShadow: "0 6px 18px rgba(124,58,237,.38)" });
            b.addEventListener("click", () => customization_app.reunion.ouvrir());
            document.body.appendChild(b);
        } else if (present) {
            present.remove();
        }
    }
    $(document).on("page-change", verifier);
    $(window).on("hashchange", verifier);
    $(document).ready(() => setTimeout(verifier, 800));
})();
