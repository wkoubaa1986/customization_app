/**
 * « 🗓️ Gérer les interventions » — écran d'actions groupées, ouvert depuis le
 * CALENDRIER (et depuis la liste des tâches).
 *
 * On filtre par période, statut, types et employés (plusieurs à la fois, 18/11/2026), on coche, puis on
 * décale, on TRANSFÈRE à un autre employé (18/11/2026) ou on annule, en prévenant les clients avec un
 * modèle prédéfini. Seuls les employés actifs sont proposés, comme filtre et comme destinataire.
 *
 * Toute la décision vit CÔTÉ SERVEUR (customization_app.taches_groupe) : la
 * recherche, le décalage, le transfert, l'annulation et l'envoi. L'écran ne compose rien.
 */

frappe.provide("customization_app");

customization_app.gestion_taches = (function () {
    const API = "customization_app.taches_groupe";
    const esc = (t) => frappe.utils.escape_html(String(t == null ? "" : t));
    const STATUTS = [["Open", "Ouvertes"], ["Completed", "Terminées"], ["Cancelled", "Annulées"], ["", "Toutes"]];
    const PASTILLES = { Open: ["Ouverte", "#1d4ed8", "#dbeafe"], Completed: ["Terminée", "#15803d", "#dcfce7"],
                        Cancelled: ["Annulée", "#6b7280", "#f1f3f5"] };
    const ACTIONS = { "Décaler": "decaler", "Transférer": "transferer", "Annuler": "annuler" };

    let filtres_charges = null;
    let selection = new Set();
    let lignes = [];
    let types = new Set();        // filtres à choix multiple : vide = tous
    let employes = new Set();
    let applique = false;         // une action a tourné : le calendrier derrière est à recharger

    function poser_css() {
        if (document.getElementById("gt-css")) return;
        const st = document.createElement("style");
        st.id = "gt-css";
        st.textContent = `
        .gt-filtre { display:flex; align-items:flex-start; gap:10px; margin-bottom:8px; }
        .gt-filtre > b { flex:0 0 74px; font-size:12px; color:var(--text-muted,#6b7280); padding-top:5px; font-weight:600; }
        .gt-chips { display:flex; flex-wrap:wrap; gap:6px; }
        .gt-chip { border:1px solid var(--border-color,#d1d8dd); background:#fff; border-radius:999px; padding:3px 11px;
                   font-size:12.5px; cursor:pointer; user-select:none; line-height:1.5; }
        .gt-chip:hover { border-color:#94a3b8; }
        .gt-chip.on { background:#171717; border-color:#171717; color:#fff; font-weight:600; }
        .gt-barre { display:flex; gap:12px; align-items:center; margin-bottom:6px; font-size:12.5px; }
        .gt-barre a { cursor:pointer; }
        .gt-coche { background:#171717; color:#fff; border-radius:999px; padding:1px 9px; font-weight:700; }
        .gt-coche.zero { background:#e5e7eb; color:#6b7280; }
        .gt-cadre { max-height:320px; overflow:auto; border:1px solid var(--border-color,#e4e8ee); border-radius:10px; }
        .gt-table { width:100%; font-size:12.5px; border-collapse:separate; border-spacing:0; }
        .gt-table th { position:sticky; top:0; z-index:1; background:var(--bg-light-gray,#f6f8fa); text-align:left;
                       padding:6px 8px; font-size:10.5px; text-transform:uppercase; color:#6b7280; }
        .gt-table td { padding:6px 8px; border-top:1px solid var(--border-color,#eef1f5); }
        .gt-table tbody tr { cursor:pointer; }
        .gt-table tbody tr:hover { background:#f8fafc; }
        .gt-table tbody tr.on { background:#eef4ff; }
        .gt-pastille { font-size:11px; font-weight:600; padding:1px 8px; border-radius:999px; white-space:nowrap; }
        .gt-vide { padding:22px; text-align:center; color:#6b7280; }`;
        document.head.appendChild(st);
    }

    function ouvrir(preselection, periode) {
        poser_css();
        selection = new Set(preselection || []);
        types = new Set();
        employes = new Set();
        applique = false;
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
                { fieldtype: "Select", fieldname: "statut", label: __("Statut"),
                  options: STATUTS.map((x) => x[0]).join("\n"),
                  default: (preselection && preselection.length) ? "" : "Open" },
                { fieldtype: "Section Break" },
                // Types et employés : plusieurs à la fois, rien d'allumé = tous.
                { fieldtype: "HTML", fieldname: "filtres" },
                { fieldtype: "HTML", fieldname: "resultats" },
                { fieldtype: "Section Break", label: __("Action") },
                { fieldtype: "Select", fieldname: "action", reqd: 1, default: "Décaler",
                  label: __("Que faire des tâches cochées ?"),
                  options: Object.keys(ACTIONS).join("\n") },
                { fieldtype: "Int", fieldname: "jours", default: 7,
                  label: __("Décaler de (jours)"),
                  depends_on: "eval:doc.action=='Décaler'",
                  description: __("Négatif pour avancer. Heure et durée conservées.") },
                { fieldtype: "Date", fieldname: "nouvelle_date",
                  label: __("…ou vers cette date"),
                  depends_on: "eval:doc.action=='Décaler'" },
                { fieldtype: "Select", fieldname: "vers", label: __("Transférer à"),
                  depends_on: "eval:doc.action=='Transférer'",
                  description: __("Les tâches cochées passent à cet employé ; leur date et leur heure ne changent pas.") },
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

        // Le statut se lit en français ; la valeur envoyée reste celle de la tâche.
        d.fields_dict.statut.$input.find("option").each(function () {
            const x = STATUTS.find((y) => y[0] === (this.value || ""));
            if (x) $(this).text(__(x[1]));
        });

        ["date_from", "date_to", "statut"]
            .forEach((f) => d.fields_dict[f].$input.on("change", () => _chercher(d)));
        d.fields_dict.action.$input.on("change", () => _bouton(d));

        d.fields_dict.choix_modele.$input.on("change", function () {
            const m = (d.__modeles || []).find((x) => x.libelle === $(this).val());
            if (!m) return;
            Promise.resolve(d.fields_dict.message.set_value(m.texte))
                .then(() => _apercu(d));
        });
        d.fields_dict.message.$input.on("input",
            frappe.utils.debounce(() => _apercu(d), 400));

        // Filtres à choix multiple.
        d.$wrapper.on("click", ".gt-chip", function () {
            const ens = $(this).attr("data-filtre") === "type" ? types : employes;
            const v = $(this).attr("data-valeur");
            if (v === "") ens.clear();                       // « Tous »
            else if (ens.has(v)) ens.delete(v); else ens.add(v);
            _peindre_filtres(d);
            _chercher(d);
        });

        // Cocher : la case, ou n'importe où sur la ligne.
        const cocher = (n, oui) => {
            if (oui) selection.add(n); else selection.delete(n);
            d.$wrapper.find(`.gt-sel[data-tache="${n}"]`).prop("checked", oui).closest("tr").toggleClass("on", oui);
            _compter(d);
            _apercu(d);
        };
        d.$wrapper.on("change", ".gt-sel", function () { cocher($(this).attr("data-tache"), this.checked); });
        d.$wrapper.on("click", ".gt-table tbody tr[data-tache]", function (ev) {
            if ($(ev.target).is("input, a")) return;
            const n = $(this).attr("data-tache");
            cocher(n, !selection.has(n));
        });
        d.$wrapper.on("click", ".gt-tout", () => {
            lignes.forEach((l) => selection.add(l.tache));
            _rendre(d);
            _apercu(d);
        });
        d.$wrapper.on("click", ".gt-aucun", () => {
            selection.clear();
            _rendre(d);
            _apercu(d);
        });

        // À la fermeture : si une action a tourné, le calendrier (ou la liste) derrière se recharge.
        d.onhide = () => {
            if (applique && window.cur_list && cur_list.doctype === "Tache de travail" && cur_list.refresh) cur_list.refresh();
        };

        d.show();
        _charger_filtres(d).then(() => _chercher(d));
        _charger_modeles(d);
        _apercu(d);
    }

    function _charger_filtres(d) {
        // Relus à chaque ouverture : un employé vient d'arriver ou de partir.
        return frappe.call({ method: API + ".get_filtres" })
            .then((r) => { filtres_charges = r.message || {}; _peindre_filtres(d); _poser_destinataires(d); });
    }

    function _peindre_filtres(d) {
        const chips = (filtre, ens, valeurs) =>
            `<span class="gt-chip ${ens.size ? "" : "on"}" data-filtre="${filtre}" data-valeur="">${__("Tous")}</span>`
            + valeurs.map((x) => `<span class="gt-chip ${ens.has(x.valeur) ? "on" : ""}" data-filtre="${filtre}"
                 data-valeur="${esc(x.valeur)}">${esc(x.libelle)}</span>`).join("");
        d.fields_dict.filtres.$wrapper.html(`
            <div class="gt-filtre"><b>${__("Type")}</b><div class="gt-chips">${
                chips("type", types, (filtres_charges.types || []).map((t) => ({ valeur: t, libelle: t })))}</div></div>
            <div class="gt-filtre"><b>${__("Employé")}</b><div class="gt-chips">${
                chips("employe", employes, filtres_charges.employes || [])}</div></div>`);
    }

    // « Transférer à » : les employés actifs, par leur NOM ; la valeur reste le matricule.
    function _poser_destinataires(d) {
        d.fields_dict.vers.$input.html(`<option value=""></option>` + (filtres_charges.employes || []).map((e) =>
            `<option value="${esc(e.valeur)}">${esc(e.libelle)}</option>`).join(""));
    }

    function _charger_modeles(d) {
        frappe.call({
            method: API + ".apercu",
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
            method: API + ".rechercher",
            args: {
                date_from: v.date_from, date_to: v.date_to,
                employe: JSON.stringify(Array.from(employes)),
                type_intervention: JSON.stringify(Array.from(types)),
                statut: d.fields_dict.statut.$input.val() || "",
            },
            callback: (r) => { lignes = (r.message || {}).lignes || []; _rendre(d); },
        });
    }

    function _rendre(d) {
        const pastille = (st) => {
            const p = PASTILLES[st];
            return p ? `<span class="gt-pastille" style="color:${p[1]};background:${p[2]}">${__(p[0])}</span>` : esc(st || "");
        };
        d.fields_dict.resultats.$wrapper.html(
            `<div class="gt-barre">
               <span class="gt-coche"></span><span class="gt-compte"></span>
               <a class="gt-tout">${__("tout cocher")}</a>
               <a class="gt-aucun">${__("tout décocher")}</a>
             </div>
             <div class="gt-cadre">
               <table class="gt-table">
                 <thead><tr><th style="width:30px"></th><th>${__("Quand")}</th><th>${__("Client")}</th>
                   <th>${__("Type")}</th><th>${__("Employé")}</th><th>${__("Secteur")}</th><th>${__("Statut")}</th></tr></thead>
                 <tbody>${lignes.length ? lignes.map((l) => `<tr data-tache="${esc(l.tache)}" class="${selection.has(l.tache) ? "on" : ""}">
                     <td><input type="checkbox" class="gt-sel"
                        data-tache="${esc(l.tache)}" ${selection.has(l.tache) ? "checked" : ""}></td>
                     <td style="white-space:nowrap">${esc(_quand(l.quand))}</td>
                     <td>${esc(l.client)}</td>
                     <td>${esc(l.type)}</td>
                     <td>${esc(l.employe)}</td>
                     <td>${esc(l.secteur)}</td>
                     <td>${pastille(l.statut)}</td></tr>`).join("")
                   : `<tr><td colspan="7" class="gt-vide">${__("Aucune tâche pour ces filtres.")}</td></tr>`}</tbody>
               </table></div>`);
        _compter(d);
    }

    // « 2026-11-19 10:00 » → « jeu. 19/11 · 10:00 »
    function _quand(q) {
        const m = moment(q, "YYYY-MM-DD HH:mm");
        if (!m.isValid()) return q || "";
        return ["dim.", "lun.", "mar.", "mer.", "jeu.", "ven.", "sam."][m.day()] + " " + m.format("DD/MM · HH:mm");
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
            method: API + ".apercu",
            args: { taches: JSON.stringify(taches), modele: modele },
            callback: (r) => {
                const m = r.message || {};
                const t = m.totaux || {};
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
        d.$wrapper.find(".gt-coche").text(__("{0} cochée(s)", [selection.size])).toggleClass("zero", !selection.size);
        d.$wrapper.find(".gt-compte").text(__("sur {0} tâche(s) affichée(s)", [lignes.length]));
        _bouton(d);
    }

    // Le bouton dit ce qu'il va faire : « Transférer 3 tâche(s) ».
    function _bouton(d) {
        const action = d.fields_dict.action.$input.val() || "Décaler";
        d.set_primary_action(selection.size ? __("{0} {1} tâche(s)", [__(action), selection.size])
                                            : __("Appliquer aux tâches cochées"), () => _appliquer(d, d.get_values(true) || {}));
    }

    function _appliquer(d, v) {
        const taches = Array.from(selection);
        if (!taches.length) {
            frappe.msgprint(__("Cochez au moins une tâche."));
            return;
        }
        const action = ACTIONS[v.action] || "decaler";
        const vers = d.fields_dict.vers.$input.val() || "";
        const nom_vers = d.fields_dict.vers.$input.find("option:selected").text();
        if (action === "decaler" && !v.jours && !v.nouvelle_date) {
            frappe.msgprint(__("Indiquez un nombre de jours ou une date."));
            return;
        }
        if (action === "transferer" && !vers) {
            frappe.msgprint(__("Choisissez l’employé à qui transférer les tâches."));
            return;
        }
        const question = { decaler: __("Décaler {0} tâche(s) ?", [taches.length]),
                           transferer: __("Transférer {0} tâche(s) à {1} ?", [taches.length, esc(nom_vers)]),
                           annuler: __("ANNULER {0} tâche(s) ?", [taches.length]) }[action];
        const titre = { decaler: __("Décalage"), transferer: __("Transfert"), annuler: __("Annulation") }[action];
        const avec_message = !!(v.message || "").trim() && (v.sms || v.email);
        frappe.confirm(
            question
            + (avec_message
                ? __("<br>Les clients seront prévenus — les SMS partent vers de VRAIS numéros.")
                : __("<br><b>Aucun message ne sera envoyé.</b>")),
            () => frappe.call({
                method: API + "." + action,
                args: Object.assign(
                    { taches: JSON.stringify(taches), modele: v.message || "",
                      sujet: v.sujet, sms: v.sms ? 1 : 0, email: v.email ? 1 : 0 },
                    action === "annuler" ? { motif: v.motif }
                    : action === "transferer" ? { vers }
                    : { jours: v.jours || 0, nouvelle_date: v.nouvelle_date || null }),
                freeze: true,
                freeze_message: titre + "…",
                callback: (r) => {
                    const m = r.message || {};
                    applique = true;
                    frappe.msgprint({
                        title: titre,
                        message: `<div style="max-height:260px;overflow:auto;font-size:12px">${
                            (m.resultats || []).map((x) =>
                              `<div><b>${esc(x.tache)}</b> — ${esc(x.etat)}</div>`).join("")}</div>`
                            + (avec_message
                                ? `<div style="margin-top:8px">✉️ ${__("{0} client(s) prévenu(s).", [(m.prevenus || []).length])}</div>`
                                : `<div style="margin-top:8px" class="text-muted">${__("Aucun message envoyé.")}</div>`),
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
