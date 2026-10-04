/**
 * « 👥 Réunion » : crée en une fois une tâche « Autre » pour chaque participant choisi, même créneau,
 * avec le titre saisi (demande utilisateur 04/10/2026). Toute la création vit CÔTÉ SERVEUR
 * (customization_app.taches_groupe.creer_reunion). Bouton sur la liste / le calendrier des tâches.
 */
frappe.provide("customization_app");

customization_app.reunion = (function () {
    const API = "customization_app.taches_groupe";
    const esc = frappe.utils.escape_html;

    function ouvrir(apres) {
        frappe.call({
            method: API + ".employes_reunion",
            callback: (r) => {
                const employes = r.message || [];
                const d = new frappe.ui.Dialog({
                    title: __("👥 Nouvelle réunion"),
                    size: "large",
                    fields: [
                        { fieldtype: "Data", fieldname: "titre", label: __("Titre de la réunion"), reqd: 1,
                          description: __("C'est le titre qui s'affiche sur la tâche de chaque participant.") },
                        { fieldtype: "Date", fieldname: "jour", label: __("Jour"), reqd: 1, default: frappe.datetime.get_today() },
                        { fieldtype: "Column Break" },
                        { fieldtype: "Time", fieldname: "heure", label: __("Heure de début"), reqd: 1, default: "09:00:00" },
                        { fieldtype: "Select", fieldname: "duree", label: __("Durée"), reqd: 1, default: "60",
                          options: ["15", "30", "45", "60", "90", "120", "180", "240"].join("\n") },
                        { fieldtype: "Section Break", label: __("Participants") },
                        { fieldtype: "HTML", fieldname: "participants" },
                        { fieldtype: "Section Break" },
                        { fieldtype: "Small Text", fieldname: "notes", label: __("Notes (facultatif)"),
                          description: __("Ajoutées au sujet de chaque tâche.") },
                    ],
                    primary_action_label: __("Créer les tâches"),
                    primary_action(v) {
                        const ids = d.$wrapper.find(".rn-emp:checked").map(function () { return $(this).val(); }).get();
                        if (!ids.length) { frappe.msgprint(__("Choisissez au moins un participant.")); return; }
                        frappe.call({
                            method: API + ".creer_reunion", freeze: true, freeze_message: __("Création des tâches…"),
                            args: { titre: v.titre, jour: v.jour, heure: (v.heure || "").slice(0, 5),
                                    duree_minutes: parseInt(v.duree, 10), employes: JSON.stringify(ids), notes: v.notes || "" },
                            callback: (rr) => {
                                d.hide();
                                const m = rr.message || {};
                                frappe.show_alert({ message: __("Réunion créée : {0} tâche(s).", [(m.creees || []).length]), indicator: "green" });
                                if (apres) apres();
                            },
                        });
                    },
                });
                const cases = employes.map((e) => `
                    <label style="display:flex;align-items:center;gap:6px;padding:4px 10px;border:1px solid #e4e8ee;
                                  border-radius:999px;margin:0 6px 6px 0;cursor:pointer">
                        <input type="checkbox" class="rn-emp" value="${esc(e.valeur)}"> ${esc(e.libelle)}
                    </label>`).join("");
                d.fields_dict.participants.$wrapper.html(`
                    <div style="margin-bottom:6px">
                        <a href="#" class="rn-tous">${__("Tout cocher")}</a> · <a href="#" class="rn-aucun">${__("Tout décocher")}</a>
                    </div>
                    <div style="display:flex;flex-wrap:wrap">${cases}</div>`);
                d.$wrapper.on("click", ".rn-tous", (ev) => { ev.preventDefault(); d.$wrapper.find(".rn-emp").prop("checked", true); });
                d.$wrapper.on("click", ".rn-aucun", (ev) => { ev.preventDefault(); d.$wrapper.find(".rn-emp").prop("checked", false); });
                d.show();
            },
        });
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
            b.title = "Créer une tâche pour chaque participant, avec le titre choisi";
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
