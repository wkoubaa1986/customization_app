/**
 * « 🗓️ Gérer les interventions » — écran d'actions groupées, ouvert depuis le
 * CALENDRIER (et depuis la liste des tâches).
 *
 * On filtre par période, employé et type, on coche, puis on décale ou on annule
 * en prévenant les clients avec un modèle prédéfini.
 *
 * Toute la décision vit CÔTÉ SERVEUR (customization_app.taches_groupe) : la
 * recherche, le décalage, l'annulation et l'envoi. L'écran ne compose rien.
 */

frappe.provide("customization_app");

customization_app.gestion_taches = (function () {
    let filtres_charges = null;
    let selection = new Set();
    let lignes = [];

    function ouvrir(preselection, periode) {
        selection = new Set(preselection || []);
        const d = new frappe.ui.Dialog({
            title: __("Gérer les interventions"),
            size: "extra-large",
            fields: [
                { fieldtype: "Date", fieldname: "date_from", label: __("Du"),
                  default: (periode && periode.from) || frappe.datetime.get_today() },
                { fieldtype: "Column Break" },
                { fieldtype: "Date", fieldname: "date_to", label: __("Au"),
                  default: (periode && periode.to)
                           || frappe.datetime.add_days(frappe.datetime.get_today(), 14) },
                { fieldtype: "Column Break" },
                { fieldtype: "Select", fieldname: "type_intervention", label: __("Type") },
                { fieldtype: "Column Break" },
                { fieldtype: "Select", fieldname: "employe", label: __("Employé") },
                { fieldtype: "Column Break" },
                { fieldtype: "Select", fieldname: "statut", label: __("Statut"),
                  options: ["Open", "Completed", "Cancelled", ""].join("\n"),
                  default: (preselection && preselection.length) ? "" : "Open" },
                { fieldtype: "Section Break" },
                { fieldtype: "HTML", fieldname: "resultats" },
                { fieldtype: "Section Break", label: __("Action") },
                { fieldtype: "Select", fieldname: "action", reqd: 1, default: "Décaler",
                  label: __("Que faire des tâches cochées ?"),
                  options: ["Décaler", "Annuler"].join("\n") },
                { fieldtype: "Int", fieldname: "jours", default: 7,
                  label: __("Décaler de (jours)"),
                  depends_on: "eval:doc.action=='Décaler'",
                  description: __("Négatif pour avancer. Heure et durée conservées.") },
                { fieldtype: "Date", fieldname: "nouvelle_date",
                  label: __("…ou vers cette date"),
                  depends_on: "eval:doc.action=='Décaler'" },
                { fieldtype: "Small Text", fieldname: "motif", label: __("Motif"),
                  depends_on: "eval:doc.action=='Annuler'" },
                { fieldtype: "Column Break" },
                { fieldtype: "Select", fieldname: "choix_modele",
                  label: __("Message prédéfini"),
                  description: __("Message vide = aucun envoi.") },
                { fieldtype: "Small Text", fieldname: "message", label: __("Message") },
                { fieldtype: "Check", fieldname: "sms", default: 1, label: __("SMS") },
                { fieldtype: "Check", fieldname: "email", default: 1, label: __("E-mail") },
                { fieldtype: "Data", fieldname: "sujet", label: __("Objet de l'e-mail"),
                  depends_on: "email" },
                { fieldtype: "Section Break",
                  label: __("Aperçu du message, client par client") },
                { fieldtype: "HTML", fieldname: "apercu" },
            ],
            primary_action_label: __("Appliquer aux tâches cochées"),
            primary_action: (v) => _appliquer(d, v),
        });

        ["date_from", "date_to", "type_intervention", "employe", "statut"]
            .forEach((f) => d.fields_dict[f].$input.on("change", () => _chercher(d)));

        d.fields_dict.choix_modele.$input.on("change", function () {
            const m = (d.__modeles || []).find((x) => x.libelle === $(this).val());
            if (!m) return;
            Promise.resolve(d.fields_dict.message.set_value(m.texte))
                .then(() => _apercu(d));
        });
        d.fields_dict.message.$input.on("input",
            frappe.utils.debounce(() => _apercu(d), 400));

        d.$wrapper.on("change", ".gt-sel", function () {
            const n = $(this).data("tache");
            if (this.checked) selection.add(n); else selection.delete(n);
            _compter(d);
            _apercu(d);
        });
        d.$wrapper.on("click", ".gt-tout", () => {
            lignes.forEach((l) => selection.add(l.tache));
            d.$wrapper.find(".gt-sel").prop("checked", true);
            _compter(d);
            _apercu(d);
        });
        d.$wrapper.on("click", ".gt-aucun", () => {
            selection.clear();
            d.$wrapper.find(".gt-sel").prop("checked", false);
            _compter(d);
            _apercu(d);
        });

        d.show();
        _charger_filtres(d).then(() => _chercher(d));
        _charger_modeles(d);
        _apercu(d);
    }

    function _charger_filtres(d) {
        if (filtres_charges) { _poser_filtres(d); return Promise.resolve(); }
        return frappe.call({ method: "customization_app.taches_groupe.get_filtres" })
            .then((r) => { filtres_charges = r.message || {}; _poser_filtres(d); });
    }

    function _poser_filtres(d) {
        d.fields_dict.type_intervention.df.options =
            [""].concat(filtres_charges.types || []).join("\n");
        d.fields_dict.type_intervention.refresh();
        // L'employé s'affiche par son NOM, la valeur reste le matricule.
        const sel = d.fields_dict.employe.$input;
        sel.html(`<option value=""></option>` + (filtres_charges.employes || []).map((e) =>
            `<option value="${frappe.utils.escape_html(e.valeur)}">${
                frappe.utils.escape_html(e.libelle)}</option>`).join(""));
    }

    function _charger_modeles(d) {
        frappe.call({
            method: "customization_app.taches_groupe.apercu",
            args: { taches: JSON.stringify([]) },
            callback: (r) => {
                d.__modeles = (r.message || {}).modeles || [];
                d.fields_dict.choix_modele.df.options =
                    [""].concat(d.__modeles.map((x) => x.libelle)).join("\n");
                d.fields_dict.choix_modele.refresh();
            },
        });
    }

    function _chercher(d) {
        const v = d.get_values(true) || {};
        frappe.call({
            method: "customization_app.taches_groupe.rechercher",
            args: {
                date_from: v.date_from, date_to: v.date_to,
                employe: d.fields_dict.employe.$input.val() || "",
                type_intervention: v.type_intervention || "",
                statut: v.statut || "",
            },
            callback: (r) => { lignes = (r.message || {}).lignes || []; _rendre(d); },
        });
    }

    function _rendre(d) {
        const esc = frappe.utils.escape_html;
        d.fields_dict.resultats.$wrapper.html(
            `<div style="display:flex;gap:8px;align-items:center;margin-bottom:5px">
               <b class="gt-compte"></b>
               <a class="gt-tout" style="cursor:pointer">tout cocher</a>
               <a class="gt-aucun" style="cursor:pointer">tout décocher</a>
             </div>
             <div style="max-height:300px;overflow:auto;border:1px solid
                  var(--border-color,#e4e8ee);border-radius:8px">
               <table style="width:100%;font-size:12px">
                 <thead><tr style="background:var(--bg-light-gray,#f6f8fa);
                       font-size:10.5px;text-transform:uppercase;color:#6b7280">
                   <th style="width:30px"></th><th style="text-align:left;padding:5px 8px">Quand</th>
                   <th style="text-align:left;padding:5px 8px">Client</th>
                   <th style="text-align:left;padding:5px 8px">Type</th>
                   <th style="text-align:left;padding:5px 8px">Employé</th>
                   <th style="text-align:left;padding:5px 8px">Secteur</th></tr></thead>
                 ${lignes.length ? lignes.map((l) => `<tr style="border-bottom:1px solid
                       var(--border-color,#eef1f5)">
                     <td style="padding:4px 8px"><input type="checkbox" class="gt-sel"
                        data-tache="${esc(l.tache)}" ${selection.has(l.tache) ? "checked" : ""}></td>
                     <td style="padding:4px 8px;white-space:nowrap">${esc(l.quand)}</td>
                     <td style="padding:4px 8px">${esc(l.client)}</td>
                     <td style="padding:4px 8px">${esc(l.type)}</td>
                     <td style="padding:4px 8px">${esc(l.employe)}</td>
                     <td style="padding:4px 8px">${esc(l.secteur)}</td></tr>`).join("")
                   : `<tr><td colspan="6" style="padding:18px;text-align:center;color:#6b7280">
                        Aucune tâche pour ces filtres.</td></tr>`}
               </table></div>`);
        _compter(d);
    }

    // L'aperçu porte sur les tâches COCHÉES : c'est à elles que le message
    // partira, et les balises {date}/{heure} valent celles de chaque tâche.
    function _apercu(d) {
        const zone = d.fields_dict.apercu.$wrapper;
        const taches = Array.from(selection);
        const modele = d.get_value("message") || "";
        if (!taches.length) {
            zone.html(`<div style="color:var(--text-muted,#6b7280);font-size:12.5px">
                ${__("Cochez des tâches pour voir le message qu'elles recevront.")}</div>`);
            return;
        }
        if (!modele.trim()) {
            zone.html(`<div style="color:#b45309;font-size:12.5px">
                ${__("Message vide : aucun envoi ne sera fait.")}</div>`);
            return;
        }
        frappe.call({
            method: "customization_app.taches_groupe.apercu",
            args: { taches: JSON.stringify(taches), modele: modele },
            callback: (r) => {
                const m = r.message || {};
                const t = m.totaux || {};
                const esc = frappe.utils.escape_html;
                zone.html(
                    `<div style="padding:7px 10px;border-radius:8px;margin-bottom:6px;
                          background:var(--bg-light-gray,#f6f7f9);font-size:12.5px">
                       📱 <b>${t.numeros || 0}</b> numéro(s) · ✉️ <b>${t.emails || 0}</b> e-mail(s)
                       ${t.sans_numero ? ` · <span style="color:#b45309">${t.sans_numero} sans numéro</span>` : ""}
                       ${t.sans_email ? ` · <span style="color:#b45309">${t.sans_email} sans e-mail</span>` : ""}
                     </div>
                     <div style="max-height:240px;overflow:auto;font-size:12px">${
                       (m.lignes || []).map((l) => `<div style="padding:6px 0;
                            border-bottom:1px solid var(--border-color,#eee)">
                          <b>${esc(l.nom_client || "")}</b> · ${esc(l.tache)} ·
                          ${esc(l.type || "")} · ${esc(l.quand || "sans date")}<br>
                          <span style="color:var(--text-muted)">
                            📱 ${l.numeros.length ? esc(l.numeros.join(", ")) : "—"} ·
                            ✉️ ${l.emails.length ? esc(l.emails.join(", ")) : "—"}</span>
                          ${l.message ? `<div style="margin-top:3px;white-space:pre-wrap">${
                              esc(l.message)}</div>` : ""}
                        </div>`).join("")}</div>`);
            },
        });
    }

    function _compter(d) {
        d.$wrapper.find(".gt-compte").text(
            __("{0} tâche(s) affichée(s) · {1} cochée(s)", [lignes.length, selection.size]));
    }

    function _appliquer(d, v) {
        const taches = Array.from(selection);
        if (!taches.length) {
            frappe.msgprint(__("Cochez au moins une tâche."));
            return;
        }
        const annule = v.action === "Annuler";
        if (!annule && !v.jours && !v.nouvelle_date) {
            frappe.msgprint(__("Indiquez un nombre de jours ou une date."));
            return;
        }
        const avec_message = !!(v.message || "").trim() && (v.sms || v.email);
        frappe.confirm(
            (annule ? __("ANNULER {0} tâche(s) ?", [taches.length])
                    : __("Décaler {0} tâche(s) ?", [taches.length]))
            + (avec_message
                ? __("<br>Les clients seront prévenus — les SMS partent vers de VRAIS numéros.")
                : __("<br><b>Aucun message ne sera envoyé.</b>")),
            () => frappe.call({
                method: annule ? "customization_app.taches_groupe.annuler"
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
                    frappe.msgprint({
                        title: annule ? __("Annulation") : __("Décalage"),
                        message: `<div style="max-height:260px;overflow:auto;font-size:12px">${
                            (m.resultats || []).map((x) =>
                              `<div><b>${esc(x.tache)}</b> — ${esc(x.etat)}</div>`).join("")}</div>`
                            + `<div style="margin-top:8px">✉️ ${(m.prevenus || []).length} client(s) prévenu(s).</div>`,
                    });
                    selection.clear();
                    _chercher(d);
                },
            })
        );
    }

    return { ouvrir };
})();

/* ── Bouton flottant sur le CALENDRIER ─────────────────────────────────────
   Même mécanique que « 📅 Nouveau RDV » : on suit les changements de route,
   la barre d'outils d'un espace de travail n'accueillant pas de bouton. */
(function () {
    const ESPACE = "Calendrier";
    const ID = "gt-fab";

    function poser() {
        if (document.getElementById(ID)) return;
        const b = document.createElement("button");
        b.id = ID;
        b.innerHTML = "🗓️ Gérer les interventions";
        b.title = "Décaler ou annuler plusieurs interventions, en prévenant les clients";
        Object.assign(b.style, {
            position: "fixed", bottom: "84px", right: "28px", zIndex: "9999",
            padding: "10px 16px", borderRadius: "999px", border: "none",
            background: "#0ea5e9", color: "#fff", fontWeight: "600",
            cursor: "pointer", boxShadow: "0 6px 18px rgba(2,132,199,.38)",
        });
        b.addEventListener("click", () => customization_app.gestion_taches.ouvrir());
        document.body.appendChild(b);
    }

    function retirer() {
        const b = document.getElementById(ID);
        if (b) b.remove();
    }

    function verifier() {
        const route = (frappe.get_route && frappe.get_route()) || [];
        const sur_calendrier = route[0] === "Workspaces" && route[1] === ESPACE;
        if (sur_calendrier) poser(); else retirer();
    }

    $(document).on("page-change", verifier);
    $(window).on("hashchange", verifier);
    $(document).ready(() => setTimeout(verifier, 800));
})();
