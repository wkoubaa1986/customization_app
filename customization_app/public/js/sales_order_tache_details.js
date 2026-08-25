// Détails des tâches de travail liées, en bandeau sur la fiche commande :
// type d'intervention, employé, statut, durée, date planifiée — avec lien vers
// chaque tâche. Serveur : customization_app.tache_infos.taches_de_commande.
//
// Injection DOM directe au-dessus du formulaire (comme la LCI) plutôt que
// frm.dashboard.add_section : la zone dashboard est repliable et vidée par le
// cycle de refresh — le bandeau y restait invisible.
frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		const $ancien = frm.layout.wrapper.find(".so-taches-bandeau");
		if (frm.is_new()) {
			$ancien.remove();
			return;
		}
		const commande = frm.doc.name;
		frappe.call({
			method: "customization_app.tache_infos.taches_de_commande",
			args: { commande },
			callback: (r) => {
				// le doc peut avoir changé pendant l'appel (navigation SPA)
				if (frm.doc.name !== commande) return;
				frm.layout.wrapper.find(".so-taches-bandeau").remove();
				const taches = r.message || [];
				if (!taches.length) return;
				const esc = frappe.utils.escape_html;
				const couleur = { Open: "#0958d9", Completed: "#237804", Cancelled: "#a8071a" };
				const fond = { Open: "#e6f4ff", Completed: "#f6ffed", Cancelled: "#fff1f0" };
				const statut_fr = { Open: __("Ouverte"), Completed: __("Terminée"), Cancelled: __("Annulée") };
				const lignes = taches.map((t) => {
					const c = couleur[t.status] || "#6b7280";
					const f = fond[t.status] || "#f6f8fa";
					const quand = t.starts_on
						? frappe.datetime.str_to_user(t.starts_on).slice(0, 16)
						: __("non planifiée");
					return `
					<div style="padding:6px 12px; border:1px solid var(--border-color,#e4e8ee);
					            border-left:4px solid ${c}; border-radius:8px; margin-top:6px;
					            font-size:12.5px; background:var(--card-bg,#fff);">
						<div style="margin-bottom:3px;">
							🔗 ${__("Lié à la tâche de travail")}
							<a href="/app/tache-de-travail/${encodeURIComponent(t.name)}"
							   style="font-weight:700;">${esc(t.name)}</a>
						</div>
						<div style="display:flex; flex-wrap:wrap; align-items:center; gap:10px;">
							<span style="font-weight:700;">🛠 ${esc(t.custom_type_dintervention || __("Intervention"))}</span>
							<span>👤 ${esc(t.employe || "—")}</span>
							<span style="background:${f}; color:${c}; border-radius:6px;
							             padding:1px 8px; font-weight:700;">${esc(statut_fr[t.status] || t.status || "")}</span>
							<span>⏱ ${esc(t.temps || "—")}</span>
							<span>📅 ${esc(quand)}</span>
						</div>
					</div>`;
				});
				const $bandeau = $(`<div class="so-taches-bandeau" style="margin:8px 0 4px;">
					${lignes.join("")}
				</div>`);
				// SOUS le message bleu (« Valider ce document… »), AU-DESSUS de
				// la barre d'onglets Détails / Adresse & Contact.
				const $tabs = frm.layout.wrapper.find(".form-tabs-list").first();
				if ($tabs.length) {
					$bandeau.insertBefore($tabs);
				} else {
					$bandeau.prependTo(frm.layout.wrapper);
				}
			},
		});
	},
});
