/**
 * Envoi groupé SMS + e-mail depuis la liste des commandes client.
 *
 * On coche des commandes, on écrit un message avec des balises, on voit qui
 * recevra quoi, puis on envoie. Le rendu des balises, les numéros et l'envoi
 * vivent CÔTÉ SERVEUR (customization_app.sms_commandes) — l'écran ne compose
 * rien lui-même.
 *
 * ⚠️ Comme sales_order_list_alertes.js : on étend le prototype de la vue liste
 * plutôt que frappe.listview_settings["Sales Order"], que woocommerce_fusion
 * réassigne en entier après nous.
 */

frappe.provide("frappe.views");

(function () {
    const LIBELLE = "📨 SMS / E-mail";

    const BALISES = [
        ["{nom_client}", "Nom du client"],
        ["{commande}", "N° de la commande"],
        ["{total_ttc}", "Total TTC"],
        ["{devise}", "Devise"],
        ["{date}", "Date de la commande"],
        ["{statut}", "Statut"],
        ["{article}", "Nom du 1er article"],
        ["{code}", "Code du 1er article"],
        ["{articles}", "Tous les noms d'articles"],
        ["{codes}", "Tous les codes articles"],
        ["{lien_rdv}", "Lien de prise de rendez-vous en ligne"],
    ];

    function _ouvrir(listview) {
        const coches = (listview.get_checked_items() || []).map((d) => d.name);
        if (!coches.length) {
            frappe.msgprint(__("Cochez d'abord une ou plusieurs commandes dans la liste."));
            return;
        }
        _dialogue(coches, {
            apres: () => listview.clear_checked_items && listview.clear_checked_items(),
        });
    }

    // Le dialogue, partagé par la LISTE (plusieurs commandes cochées) et par la
    // FICHE (la commande ouverte) — un seul endroit à corriger.
    function _dialogue(coches, options) {
        const opts = options || {};

        const d = new frappe.ui.Dialog({
            title: coches.length > 1
                ? __("Envoi groupé — {0} commandes", [coches.length])
                : __("Message au client — {0}", [coches[0]]),
            size: "large",
            fields: [
                { fieldtype: "HTML", fieldname: "totaux" },
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
            primary_action: (v) => _envoyer(d, opts, coches, v),
        });

        // Les balises : un clic les insère à la position du curseur.
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
            _rafraichir(d, coches);
        });

        d.fields_dict.message.$input.on("input", frappe.utils.debounce(
            () => _rafraichir(d, coches), 400));

        d.show();
        _rafraichir(d, coches);
    }

    // Qui recevra quoi, message rendu — AVANT d'envoyer.
    function _rafraichir(d, coches) {
        frappe.call({
            method: "customization_app.sms_commandes.apercu",
            args: { noms: JSON.stringify(coches), modele: d.get_value("message") || "" },
            callback: (r) => {
                const m = r.message || {};
                const t = m.totaux || {};
                d.fields_dict.totaux.$wrapper.html(
                    `<div style="padding:8px 10px;border-radius:8px;background:var(--bg-light-gray,#f6f7f9);
                          font-size:12.5px">
                       <b>${t.commandes || 0}</b> commande(s) · <b>${t.clients || 0}</b> client(s) ·
                       📱 <b>${t.numeros || 0}</b> numéro(s) · ✉️ <b>${t.emails || 0}</b> e-mail(s)
                       ${t.sans_numero ? ` · <span style="color:#b45309">${t.sans_numero} sans numéro</span>` : ""}
                       ${t.sans_email ? ` · <span style="color:#b45309">${t.sans_email} sans e-mail</span>` : ""}
                     </div>`);

                const esc = frappe.utils.escape_html;
                d.fields_dict.apercu.$wrapper.html(
                    `<div style="max-height:240px;overflow:auto;font-size:12px">${
                        (m.lignes || []).map((l) => `<div style="padding:6px 0;
                             border-bottom:1px solid var(--border-color,#eee)">
                           <b>${esc(l.nom_client)}</b> · ${esc(l.commande)}<br>
                           <span style="color:var(--text-muted)">
                             📱 ${l.numeros.length ? esc(l.numeros.join(", ")) : "—"} ·
                             ✉️ ${l.emails.length ? esc(l.emails.join(", ")) : "—"}</span>
                           ${l.message ? `<div style="margin-top:3px;white-space:pre-wrap">${esc(l.message)}</div>` : ""}
                         </div>`).join("")
                    }</div>`);
            },
        });
    }

    function _envoyer(d, opts, coches, v) {
        if (!(v.message || "").trim()) {
            frappe.msgprint(__("Écrivez le message à envoyer."));
            return;
        }
        if (!v.sms && !v.email) {
            frappe.msgprint(__("Choisissez au moins un canal : SMS ou e-mail."));
            return;
        }
        frappe.confirm(
            coches.length > 1
                ? __("Envoyer ce message pour {0} commandes ?<br>Les SMS partent vers de VRAIS clients.",
                     [coches.length])
                : __("Envoyer ce message au client ?<br>Le SMS part vers un VRAI numéro."),
            () => frappe.call({
                method: "customization_app.sms_commandes.envoyer",
                args: {
                    noms: JSON.stringify(coches), modele: v.message,
                    sujet: v.sujet, sms: v.sms ? 1 : 0, email: v.email ? 1 : 0,
                },
                freeze: true,
                freeze_message: __("Envoi en cours…"),
                callback: (r) => {
                    const m = r.message || {};
                    d.hide();
                    // Gros lot : la tournée part en file d'attente, l'écran la
                    // suit (progression + résumé) au lieu d'expirer.
                    if (m.differe) {
                        frappe.show_alert({
                            message: __("Envoi lancé pour {0} commande(s) — la progression s'affiche ici, vous pouvez continuer à travailler.",
                                        [m.commandes]),
                            indicator: "blue",
                        }, 10);
                        opts.apres && opts.apres();
                        return;
                    }
                    frappe.msgprint({
                        title: __("Envoi terminé"),
                        indicator: m.echecs ? "orange" : "green",
                        message: __("📱 {0} SMS · ✉️ {1} e-mail(s) · {2} échec(s)",
                                    [m.sms_envoyes || 0, m.emails_envoyes || 0, m.echecs || 0])
                            + `<div style="margin-top:8px;max-height:220px;overflow:auto;font-size:12px">${
                                (m.detail || []).map((x) => `<div>
                                  <b>${frappe.utils.escape_html(x.client)}</b> (${x.commande}) —
                                  📱 ${frappe.utils.escape_html(x.sms || "—")} ·
                                  ✉️ ${frappe.utils.escape_html(x.email || "—")}</div>`).join("")}</div>`,
                    });
                    opts.apres && opts.apres();
                },
            })
        );
    }

    // Suivi des envois différés : la file d'attente parle, l'écran écoute.
    // Posé UNE fois pour la session, pas à chaque rendu de liste.
    if (!window.__so_sms_realtime) {
        window.__so_sms_realtime = true;
        frappe.realtime.on("envoi_groupe_progres", (d) =>
            frappe.show_progress(__("Envoi groupé"), d.fait, d.total,
                __("{0} / {1} — {2}", [d.fait, d.total, d.client || d.commande])));
        frappe.realtime.on("envoi_groupe_termine", (m) => {
            frappe.hide_progress();
            frappe.msgprint({
                title: __("Envoi groupé terminé"),
                indicator: m.echecs ? "orange" : "green",
                message: __("📱 {0} SMS · ✉️ {1} e-mail(s) · {2} échec(s)",
                            [m.sms_envoyes || 0, m.emails_envoyes || 0, m.echecs || 0])
                    + `<div style="margin-top:8px;max-height:260px;overflow:auto;font-size:12px">${
                        (m.detail || []).map((x) => `<div>
                          <b>${frappe.utils.escape_html(x.client)}</b> (${x.commande}) —
                          📱 ${frappe.utils.escape_html(x.sms || "—")} ·
                          ✉️ ${frappe.utils.escape_html(x.email || "—")}</div>`).join("")}</div>`,
            });
        });
    }

    // Sur la FICHE : le même envoi, pour cette commande seule.
    frappe.ui.form.on("Sales Order", {
        refresh(frm) {
            if (frm.is_new()) return;
            frm.add_custom_button(__(LIBELLE), () => _dialogue([frm.doc.name], {}));
        },
    });

    const _after_render = frappe.views.ListView.prototype.after_render;
    frappe.views.ListView.prototype.after_render = function () {
        _after_render.apply(this, arguments);
        if (this.doctype !== "Sales Order") return;
        try {
            if (this.__so_bouton_sms) return;
            this.__so_bouton_sms = true;
            this.page.add_inner_button(LIBELLE, () => _ouvrir(this));
        } catch (e) {
            console.error("Envoi groupé commandes :", e);
        }
    };
})();
