/**
 * Actions groupées sur la liste des tâches de travail : décaler ou annuler
 * plusieurs interventions d'un coup, en prévenant les clients.
 *
 * ⚠️ Comme sales_order_sms_groupe.js : on étend le prototype de la vue liste
 * plutôt que frappe.listview_settings, qu'un autre script peut réassigner
 * en entier après nous.
 *
 * Toute la décision vit CÔTÉ SERVEUR (customization_app.taches_groupe) : le
 * décalage, l'annulation et l'envoi. L'écran ne compose rien lui-même.
 */

frappe.provide("frappe.views");

(function () {
    const LIBELLE = "🗓️ Décaler / annuler";

    function _ouvrir(listview) {
        const coches = (listview.get_checked_items() || []).map((d) => d.name);
        if (!coches.length) {
            frappe.msgprint(__("Cochez d'abord une ou plusieurs tâches dans la liste."));
            return;
        }
        // Le même écran que depuis le calendrier, avec la sélection déjà faite :
        // deux dialogues concurrents pour la même action, c'est deux bugs.
        if (window.customization_app && customization_app.gestion_taches) {
            customization_app.gestion_taches.ouvrir(coches);
            return;
        }
        _dialogue(coches, listview);
    }

    function _dialogue(taches, listview) {
        let modeles = [];

        const d = new frappe.ui.Dialog({
            title: __("Décaler ou annuler — {0} tâche(s)", [taches.length]),
            size: "large",
            fields: [
                { fieldtype: "HTML", fieldname: "resume" },
                {
                    fieldtype: "Select", fieldname: "action", reqd: 1, default: "Décaler",
                    label: __("Que faire de ces tâches ?"),
                    options: ["Décaler", "Annuler"].join("\n"),
                },
                {
                    fieldtype: "Int", fieldname: "jours", default: 7,
                    label: __("Décaler de (jours)"),
                    depends_on: "eval:doc.action=='Décaler'",
                    description: __("Négatif pour avancer. L'heure est conservée."),
                },
                {
                    fieldtype: "Date", fieldname: "nouvelle_date",
                    label: __("…ou vers cette date précise"),
                    depends_on: "eval:doc.action=='Décaler'",
                },
                {
                    fieldtype: "Small Text", fieldname: "motif",
                    label: __("Motif (tracé sur chaque tâche)"),
                    depends_on: "eval:doc.action=='Annuler'",
                },
                { fieldtype: "Section Break", label: __("Message aux clients") },
                {
                    fieldtype: "Select", fieldname: "choix_modele",
                    label: __("Modèle prédéfini"),
                    description: __("Laisser le message vide n'envoie rien."),
                },
                {
                    fieldtype: "Small Text", fieldname: "message",
                    label: __("Message (SMS et corps de l'e-mail)"),
                    description: __("Balises : {nom_client} {technicien} {date} {heure} {type} {lien_rdv} {signature}"),
                },
                { fieldtype: "Column Break" },
                {
                    fieldtype: "Check", fieldname: "sms", default: 1,
                    label: __("Envoyer par SMS"),
                },
                {
                    fieldtype: "Check", fieldname: "email", default: 1,
                    label: __("Envoyer par e-mail"),
                },
                {
                    fieldtype: "Data", fieldname: "sujet", label: __("Objet de l'e-mail"),
                    depends_on: "email",
                },
                { fieldtype: "Section Break", label: __("Aperçu") },
                { fieldtype: "HTML", fieldname: "apercu" },
            ],
            primary_action_label: __("Appliquer"),
            primary_action: (v) => _appliquer(d, taches, v, listview),
        });

        d.fields_dict.choix_modele.$input.on("change", function () {
            const m = modeles.find((x) => x.libelle === $(this).val());
            if (!m) return;
            Promise.resolve(d.fields_dict.message.set_value(m.texte))
                .then(() => _rafraichir(d, taches));
        });
        d.fields_dict.message.$input.on("input", frappe.utils.debounce(
            () => _rafraichir(d, taches), 400));

        d.show();
        _rafraichir(d, taches, (m) => {
            modeles = m.modeles || [];
            d.fields_dict.choix_modele.df.options =
                [""].concat(modeles.map((x) => x.libelle)).join("\n");
            d.fields_dict.choix_modele.refresh();
        });
    }

    function _rafraichir(d, taches, apres) {
        frappe.call({
            method: "customization_app.taches_groupe.apercu",
            args: { taches: JSON.stringify(taches), modele: d.get_value("message") || "" },
            callback: (r) => {
                const m = r.message || {};
                const t = m.totaux || {};
                const esc = frappe.utils.escape_html;
                const types = Object.entries(m.par_type || {})
                    .map(([k, n]) => `${esc(k)} : <b>${n}</b>`).join(" · ");
                d.fields_dict.resume.$wrapper.html(
                    `<div style="padding:8px 10px;border-radius:8px;background:var(--bg-light-gray,#f6f7f9);
                          font-size:12.5px">
                       ${types || "—"}<br>
                       📱 <b>${t.numeros || 0}</b> numéro(s) · ✉️ <b>${t.emails || 0}</b> e-mail(s)
                       ${t.sans_numero ? ` · <span style="color:#b45309">${t.sans_numero} sans numéro</span>` : ""}
                     </div>`);
                d.fields_dict.apercu.$wrapper.html(
                    `<div style="max-height:240px;overflow:auto;font-size:12px">${
                        (m.lignes || []).map((l) => `<div style="padding:6px 0;
                             border-bottom:1px solid var(--border-color,#eee)">
                           <b>${esc(l.nom_client || "")}</b> · ${esc(l.tache)}
                           · ${esc(l.type || "")} · ${esc(l.quand || "sans date")}
                           <span class="text-muted">(${esc(l.statut || "")})</span><br>
                           <span style="color:var(--text-muted)">
                             📱 ${l.numeros.length ? esc(l.numeros.join(", ")) : "—"} ·
                             ✉️ ${l.emails.length ? esc(l.emails.join(", ")) : "—"}</span>
                           ${l.message ? `<div style="margin-top:3px;white-space:pre-wrap">${esc(l.message)}</div>` : ""}
                         </div>`).join("")
                    }</div>`);
                apres && apres(m);
            },
        });
    }

    function _appliquer(d, taches, v, listview) {
        const annule = v.action === "Annuler";
        if (!annule && !v.jours && !v.nouvelle_date) {
            frappe.msgprint(__("Indiquez un nombre de jours ou une date."));
            return;
        }
        const quoi = annule
            ? __("ANNULER {0} tâche(s)", [taches.length])
            : (v.nouvelle_date
                ? __("Décaler {0} tâche(s) au {1}", [taches.length, v.nouvelle_date])
                : __("Décaler {0} tâche(s) de {1} jour(s)", [taches.length, v.jours]));
        const avec_message = !!(v.message || "").trim() && (v.sms || v.email);

        frappe.confirm(
            quoi + (avec_message
                ? __("<br>Les clients seront prévenus — les SMS partent vers de VRAIS numéros.")
                : __("<br><b>Aucun message ne sera envoyé</b> (message vide).")),
            () => frappe.call({
                method: annule
                    ? "customization_app.taches_groupe.annuler"
                    : "customization_app.taches_groupe.decaler",
                args: Object.assign(
                    { taches: JSON.stringify(taches), modele: v.message || "",
                      sujet: v.sujet, sms: v.sms ? 1 : 0, email: v.email ? 1 : 0 },
                    annule ? { motif: v.motif }
                           : { jours: v.jours || 0, nouvelle_date: v.nouvelle_date || null }),
                freeze: true,
                freeze_message: annule ? __("Annulation…") : __("Décalage…"),
                callback: (r) => {
                    const m = r.message || {};
                    const esc = frappe.utils.escape_html;
                    d.hide();
                    frappe.msgprint({
                        title: annule ? __("Annulation") : __("Décalage"),
                        message: `<div style="max-height:260px;overflow:auto;font-size:12px">${
                            (m.resultats || []).map((x) =>
                              `<div><b>${esc(x.tache)}</b> — ${esc(x.etat)}</div>`).join("")}</div>`
                            + `<div style="margin-top:8px">✉️ ${(m.prevenus || []).length} client(s) prévenu(s).</div>`,
                    });
                    listview && listview.clear_checked_items && listview.clear_checked_items();
                    listview && listview.refresh();
                },
            })
        );
    }

    const _after_render = frappe.views.ListView.prototype.after_render;
    frappe.views.ListView.prototype.after_render = function () {
        _after_render.apply(this, arguments);
        if (this.doctype !== "Tache de travail") return;
        try {
            if (this.__tache_bouton_groupe) return;
            this.__tache_bouton_groupe = true;
            this.page.add_inner_button(LIBELLE, () => _ouvrir(this));
        } catch (e) {
            console.error("Actions groupées tâches :", e);
        }
    };
})();
