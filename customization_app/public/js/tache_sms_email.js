/**
 * 📨 SMS / E-mail depuis la fiche Tache de travail — avec modèles prédéfinis.
 *
 * Même esprit que sales_order_sms_groupe.js : le rendu des balises, les
 * numéros et l'envoi vivent CÔTÉ SERVEUR (customization_app.sms_taches).
 * En plus : un choix de modèle remplit le message, qui reste modifiable.
 * Le technicien de la tâche et son téléphone sont injectés automatiquement
 * ({technicien}, {tel_technicien}), la commande liée aussi si elle existe.
 */

(function () {
    const LIBELLE = "📨 SMS / E-mail";

    const BALISES = [
        ["{nom_client}", "Nom du client"],
        ["{technicien}", "Employé affecté à la tâche"],
        ["{tel_technicien}", "Téléphone de l'employé"],
        ["{commande}", "N° de la commande liée"],
        ["{total_ttc}", "Total TTC de la commande liée"],
        ["{devise}", "Devise de la commande"],
        ["{date}", "Date de la tâche"],
        ["{heure}", "Heure de la tâche"],
        ["{type}", "Type d'intervention"],
        ["{lien_rdv}", "Lien de prise de rendez-vous en ligne"],
    ];

    function _dialogue(taches) {
        let modeles = [];

        const d = new frappe.ui.Dialog({
            title: __("Message au client — {0}", [taches[0]]),
            size: "large",
            fields: [
                { fieldtype: "HTML", fieldname: "totaux" },
                {
                    fieldtype: "Select", fieldname: "choix_modele",
                    label: __("Modèle prédéfini"),
                    description: __("Choisir un modèle remplit le message — il reste modifiable."),
                },
                {
                    fieldtype: "Small Text", fieldname: "message", reqd: 1,
                    label: __("Message (SMS et corps de l'e-mail)"),
                    description: __("Cliquez une balise pour l'insérer."),
                },
                { fieldtype: "HTML", fieldname: "balises" },
                { fieldtype: "Column Break" },
                {
                    fieldtype: "Check", fieldname: "sms", default: 1,
                    label: __("Envoyer par SMS (Liste Telephone du client)"),
                },
                {
                    fieldtype: "Check", fieldname: "email", default: 1,
                    label: __("Envoyer par e-mail (contacts du client)"),
                },
                {
                    fieldtype: "Data", fieldname: "sujet",
                    label: __("Objet de l'e-mail"),
                    depends_on: "email",
                },
                { fieldtype: "Section Break", label: __("Aperçu") },
                { fieldtype: "HTML", fieldname: "apercu" },
            ],
            primary_action_label: __("Envoyer"),
            primary_action: (v) => _envoyer(d, taches, v),
        });

        d.fields_dict.balises.$wrapper.html(
            `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:4px">${
                BALISES.map(([b, aide]) =>
                    `<button type="button" class="btn btn-xs btn-default" data-balise="${b}"
                        title="${frappe.utils.escape_html(aide)}">${b}</button>`).join("")
            }</div>`
        );
        d.fields_dict.balises.$wrapper.find("[data-balise]").on("click", function () {
            const champ = d.fields_dict.message.$input.get(0);
            const balise = $(this).attr("data-balise");
            const debut = champ.selectionStart || 0;
            const valeur = champ.value || "";
            champ.value = valeur.slice(0, debut) + balise + valeur.slice(champ.selectionEnd || debut);
            champ.focus();
            champ.selectionStart = champ.selectionEnd = debut + balise.length;
            d.fields_dict.message.set_value(champ.value);
            _rafraichir(d, taches);
        });

        // Choisir un modèle remplit le message et rafraîchit l'aperçu.
        // ⚠️ set_value est ASYNCHRONE : rafraîchir avant sa résolution rendrait
        // l'aperçu avec l'ancien message (constaté au premier test navigateur).
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
            method: "customization_app.sms_taches.apercu",
            args: { taches: JSON.stringify(taches), modele: d.get_value("message") || "" },
            callback: (r) => {
                const m = r.message || {};
                const t = m.totaux || {};
                d.fields_dict.totaux.$wrapper.html(
                    `<div style="padding:8px 10px;border-radius:8px;background:var(--bg-light-gray,#f6f7f9);
                          font-size:12.5px">
                       📱 <b>${t.numeros || 0}</b> numéro(s) · ✉️ <b>${t.emails || 0}</b> e-mail(s)
                       ${t.sans_numero ? ` · <span style="color:#b45309">${__("client sans numéro !")}</span>` : ""}
                       ${t.sans_email ? ` · <span style="color:#b45309">${__("client sans e-mail")}</span>` : ""}
                     </div>`);

                const esc = frappe.utils.escape_html;
                d.fields_dict.apercu.$wrapper.html(
                    `<div style="max-height:240px;overflow:auto;font-size:12px">${
                        (m.lignes || []).map((l) => `<div style="padding:6px 0;
                             border-bottom:1px solid var(--border-color,#eee)">
                           <b>${esc(l.nom_client)}</b> · ${esc(l.tache)}
                           ${l.technicien ? ` · 👷 ${esc(l.technicien)} (${esc(l.tel_technicien || "—")})` : ""}
                           ${l.commande ? ` · 🧾 ${esc(l.commande)} (${l.total_ttc} ${esc(l.devise)})` : ""}<br>
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

    function _envoyer(d, taches, v) {
        if (!(v.message || "").trim()) {
            frappe.msgprint(__("Écrivez le message à envoyer."));
            return;
        }
        if (!v.sms && !v.email) {
            frappe.msgprint(__("Choisissez au moins un canal : SMS ou e-mail."));
            return;
        }
        frappe.confirm(
            __("Envoyer ce message au client ?<br>Le SMS part vers un VRAI numéro."),
            () => frappe.call({
                method: "customization_app.sms_taches.envoyer",
                args: {
                    taches: JSON.stringify(taches), modele: v.message,
                    sujet: v.sujet, sms: v.sms ? 1 : 0, email: v.email ? 1 : 0,
                },
                freeze: true,
                freeze_message: __("Envoi en cours…"),
                callback: (r) => {
                    const m = r.message || {};
                    d.hide();
                    frappe.msgprint({
                        title: __("Envoi terminé"),
                        indicator: m.echecs ? "orange" : "green",
                        message: __("📱 {0} SMS · ✉️ {1} e-mail(s) · {2} échec(s)",
                                    [m.sms_envoyes || 0, m.emails_envoyes || 0, m.echecs || 0])
                            + (m.simulation
                                ? `<div style="margin-top:6px;color:#b45309">${__("Mode développement : envois SIMULÉS, rien n'est parti.")}</div>`
                                : "")
                            + `<div style="margin-top:8px;max-height:220px;overflow:auto;font-size:12px">${
                                (m.detail || []).map((x) => `<div>
                                  <b>${frappe.utils.escape_html(x.client)}</b> (${x.tache}) —
                                  📱 ${frappe.utils.escape_html(x.sms || "—")} ·
                                  ✉️ ${frappe.utils.escape_html(x.email || "—")}</div>`).join("")}</div>`,
                    });
                },
            })
        );
    }

    frappe.ui.form.on("Tache de travail", {
        refresh(frm) {
            if (frm.is_new()) return;
            frm.add_custom_button(__(LIBELLE), () => _dialogue([frm.doc.name]));
        },
    });
})();
