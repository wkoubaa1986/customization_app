/**
 * Tâche de travail — un lien « Maps » cliquable sous « Lien Google Map » (demande du 18/11/2026).
 *
 * Le champ est du texte : même rempli, il ne s'ouvrait pas d'un clic ; et vide, il ne disait rien alors que
 * l'adresse a souvent une latitude et une longitude. Sous le champ, on montre maintenant :
 *   - le lien de la tâche, cliquable ;
 *   - sinon le lien de l'adresse (repris sur la tâche à l'enregistrement) ;
 *   - sinon la position de l'adresse ouverte dans Maps — annoncée « approximative » quand elle a été calculée
 *     d'après l'adresse écrite (source « texte ≈ »).
 * Quand le champ est vide (donc masqué), le lien s'affiche sous « Detail Adresse ».
 * Le champ lui-même n'est JAMAIS rempli avec une position approximative (voir customization_app.tache_position).
 */
(function () {
    const CHAMP = "google_map";
    const ADRESSE = "details_adresse";
    const esc = (t) => frappe.utils.escape_html(String(t == null ? "" : t));

    function montrer(frm) {
        if (!frm.fields_dict[CHAMP]) return;
        const tour = (frm.__position_tour = (frm.__position_tour || 0) + 1);
        frappe.call({
            method: "customization_app.tache_position.position",
            args: { adresse: frm.doc.select_address || "", lien_tache: frm.doc[CHAMP] || "" },
        }).then((r) => {
            if (tour !== frm.__position_tour) return;       // une réponse en retard ne remplace pas la suivante
            const p = r.message;
            let html = "";
            if (p && /^https?:\/\//i.test(p.url)) {
                const lien = (texte) => `<a href="${esc(p.url)}" target="_blank" rel="noopener" style="font-weight:600">${texte}</a>`;
                if (p.origine === "tache") html = lien("🗺️ " + __("Ouvrir dans Maps"));
                else if (p.origine === "adresse") html = lien("🗺️ " + __("Ouvrir le lien de l’adresse dans Maps"))
                    + ` <span class="text-muted">— ${__("il sera repris sur la tâche à l’enregistrement")}</span>`;
                else html = lien("📍 " + __("Ouvrir la position de l’adresse dans Maps"))
                    + (p.approximatif
                        ? ` <span style="color:#b45309">— ${__("position approximative, calculée d’après l’adresse écrite : ce n’est pas le point exact du client")}</span>`
                        : "");
            }
            // Où le montrer : sous « Lien Google Map » s'il est rempli ; sinon sous « Detail Adresse » — un champ
            // vide en lecture seule est masqué par Frappe, et son aide avec lui.
            const sous_le_lien = !!(frm.doc[CHAMP] || "").trim();
            frm.set_df_property(CHAMP, "description", sous_le_lien ? html : "");
            if (frm.fields_dict[ADRESSE]) frm.set_df_property(ADRESSE, "description", sous_le_lien ? "" : html);
        });
    }

    frappe.ui.form.on("Tache de travail", {
        refresh: montrer,
        select_address: montrer,
        google_map: frappe.utils.debounce(montrer, 400),
    });
})();
