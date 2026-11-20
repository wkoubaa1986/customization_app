/**
 * « 🗓️ Gérer les interventions » — écran d'actions groupées, ouvert depuis le
 * CALENDRIER (et depuis la liste des tâches).
 *
 * Refonte du 20/11/2026 (« fais-le par jour, montre les calendriers l'un à côté de l'autre quand je transfère,
 * une tâche à employé fixe reste fixe, améliore la position des actions ») :
 *   1. EN HAUT, l'action — Décaler / Transférer / Annuler — et ses réglages : on dit d'abord ce qu'on veut faire.
 *   2. La période (raccourcis jour / semaine, flèches), puis les filtres (types et employés à choix multiple).
 *   3. Les tâches RANGÉES PAR JOUR. En transfert : deux agendas côte à côte, celui de qui on prend et celui de
 *      qui reçoit ; ce qu'on coche à gauche apparaît à droite « à recevoir », avec les chevauchements d'horaire.
 *      Ce qui ne se transfère pas est verrouillé 🔒 : employé fixe, congé / récupération, tâche terminée.
 *   4. Le message au client, replié : vide = aucun envoi.
 *
 * Toute la décision vit CÔTÉ SERVEUR (customization_app.taches_groupe) : la
 * recherche, le décalage, le transfert, l'annulation et l'envoi. L'écran ne compose rien — le verrou 🔒 n'est
 * qu'un reflet de `refus_transfert`, qui refuse de toute façon.
 */

frappe.provide("customization_app");

customization_app.gestion_taches = (function () {
    const API = "customization_app.taches_groupe";
    const esc = (t) => frappe.utils.escape_html(String(t == null ? "" : t));
    const STATUTS = [["Open", "Ouvertes"], ["Completed", "Terminées"], ["Cancelled", "Annulées"], ["", "Toutes"]];
    const PASTILLES = { Open: ["Ouverte", "#1d4ed8", "#dbeafe"], Completed: ["Terminée", "#15803d", "#dcfce7"],
                        Cancelled: ["Annulée", "#6b7280", "#f1f3f5"] };
    const ACTIONS = { "Décaler": "decaler", "Transférer": "transferer", "Annuler": "annuler" };
    const ICONES_ACTION = { decaler: "🗓️", transferer: "🔁", annuler: "❌" };
    const ABSENCES = ["Congé", "Jour de récupération"];
    const ICONES = { "Entretien": "🔧", "Réparation": "🧰", "Installation": "🔨", "Livraison": "🚚", "Visite": "👋",
                     "Autre": "📌", "Tournée commerciale": "🚗", "Congé": "🌴", "Jour de récupération": "🛌" };
    const JOURS = ["dimanche", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi"];
    const MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"];

    let F = {};                   // ce que le serveur propose : types, employés actifs, anciens avec des tâches ouvertes
    let selection = new Set();
    let lignes = [];
    let types = new Set();        // filtres à choix multiple : vide = tous
    let employes = new Set();
    let applique = false;         // une action a tourné : le calendrier derrière est à recharger
    let e = {};                   // l'état de l'écran : action, période, statut, de → vers, réglages de l'action
    let tour = 0;                 // numéro de la dernière recherche : une réponse plus ancienne est ignorée

    function poser_css() {
        if (document.getElementById("gt-css")) return;
        const st = document.createElement("style");
        st.id = "gt-css";
        st.textContent = `
        .gt-onglets { display:flex; gap:8px; margin-bottom:10px; }
        .gt-onglet { flex:1; border:1px solid var(--border-color,#d1d8dd); background:#fff; border-radius:10px; padding:9px 12px;
                     cursor:pointer; text-align:left; line-height:1.3; }
        .gt-onglet b { display:block; font-size:14px; }
        .gt-onglet span { font-size:11.5px; color:var(--text-muted,#6b7280); }
        .gt-onglet:hover { border-color:#94a3b8; }
        .gt-onglet.on { background:#171717; border-color:#171717; color:#fff; }
        .gt-onglet.on span { color:#d4d4d8; }
        .gt-onglet.on[data-action="annuler"] { background:#b91c1c; border-color:#b91c1c; }
        .gt-reglage { display:flex; flex-wrap:wrap; align-items:center; gap:8px; background:var(--bg-light-gray,#f6f8fa);
                      border-radius:10px; padding:9px 12px; margin-bottom:10px; font-size:13px; }
        .gt-reglage .form-control { height:30px; padding:3px 8px; font-size:13px; width:auto; display:inline-block;
                                    background:#fff; border:1px solid var(--border-color,#d1d8dd); }
        .gt-reglage select.form-control { min-width:210px; }
        .gt-reglage .gt-fleche { font-size:18px; font-weight:700; color:#0ea5e9; }
        .gt-aide { font-size:11.5px; color:var(--text-muted,#6b7280); flex-basis:100%; }
        .gt-periode { display:flex; flex-wrap:wrap; align-items:center; gap:6px; margin-bottom:8px; font-size:12.5px; }
        .gt-periode .form-control { height:28px; padding:2px 6px; font-size:12.5px; width:auto; display:inline-block; }
        .gt-periode > b, .gt-filtre > b { flex:0 0 62px; font-size:12px; color:var(--text-muted,#6b7280); font-weight:600; }
        .gt-nav { border:1px solid var(--border-color,#d1d8dd); background:#fff; border-radius:8px; width:28px; height:28px;
                  cursor:pointer; line-height:1; }
        .gt-nav:hover, .gt-rac:hover { border-color:#94a3b8; }
        .gt-rac { border:1px solid var(--border-color,#d1d8dd); background:#fff; border-radius:999px; padding:3px 10px;
                  cursor:pointer; font-size:12px; }
        .gt-pousse { margin-left:auto; display:flex; align-items:center; gap:6px; }
        .gt-filtre { display:flex; align-items:flex-start; gap:6px; margin-bottom:6px; }
        .gt-filtre > b { padding-top:5px; }
        .gt-chips { display:flex; flex-wrap:wrap; gap:6px; }
        .gt-chip { border:1px solid var(--border-color,#d1d8dd); background:#fff; border-radius:999px; padding:3px 11px;
                   font-size:12.5px; cursor:pointer; user-select:none; line-height:1.5; }
        .gt-chip:hover { border-color:#94a3b8; }
        .gt-chip.on { background:#171717; border-color:#171717; color:#fff; font-weight:600; }
        .gt-barre { display:flex; flex-wrap:wrap; gap:12px; align-items:center; margin:8px 0 6px; font-size:12.5px; }
        .gt-barre a { cursor:pointer; }
        .gt-coche { background:#171717; color:#fff; border-radius:999px; padding:1px 9px; font-weight:700; }
        .gt-coche.zero { background:#e5e7eb; color:#6b7280; }
        .gt-alerte { color:#b45309; font-weight:600; }
        .gt-cadre { max-height:46vh; min-height:150px; overflow:auto; border:1px solid var(--border-color,#e4e8ee); border-radius:10px; }
        .gt-jour-tete { position:sticky; top:0; z-index:2; display:flex; align-items:baseline; gap:10px; padding:6px 10px;
                        background:var(--bg-light-gray,#f1f4f7); border-bottom:1px solid var(--border-color,#e4e8ee); font-size:12.5px; }
        .gt-jour-tete b { font-size:13px; }
        /* Les boutons flottants du calendrier passent sous l'écran tant qu'il est ouvert (ils couvraient son bouton). */
        body.gt-ouvert #gt-fab, body.gt-ouvert #rn-fab { display:none !important; }
        .btn.gt-rouge, .btn.gt-rouge:hover, .btn.gt-rouge:focus { background:#b91c1c !important; border-color:#b91c1c !important; color:#fff !important; }
        .gt-jour-tete .gt-tag { background:#0ea5e9; color:#fff; border-radius:999px; padding:0 8px; font-size:11px; font-weight:600; }
        .gt-jour-tete a { margin-left:auto; cursor:pointer; font-size:12px; }
        .gt-n { color:var(--text-muted,#6b7280); font-size:11.5px; }
        .gt-ligne { display:grid; grid-template-columns:26px 104px 150px minmax(120px,1fr) 150px 120px 78px; gap:8px; align-items:center;
                    padding:6px 10px; border-bottom:1px solid var(--border-color,#eef1f5); font-size:12.5px; cursor:pointer; }
        .gt-ligne:hover { background:#f8fafc; }
        .gt-ligne.on { background:#eef4ff; }
        .gt-ligne input, .gt-carte input { margin:0; }
        .gt-heure { font-variant-numeric:tabular-nums; white-space:nowrap; font-weight:600; }
        .gt-heure small { font-weight:400; color:var(--text-muted,#6b7280); }
        .gt-coupe { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .gt-pastille { font-size:11px; font-weight:600; padding:1px 8px; border-radius:999px; white-space:nowrap; }
        .gt-vide { padding:26px; text-align:center; color:#6b7280; font-size:13px; }
        .gt-deux { display:grid; grid-template-columns:1fr 1fr; }
        .gt-deux > div { padding:8px 10px; min-width:0; }
        .gt-deux > div + div { border-left:1px dashed var(--border-color,#d1d8dd); }
        .gt-colonnes { position:sticky; top:0; z-index:3; background:#fff; border-bottom:1px solid var(--border-color,#e4e8ee); }
        .gt-colonnes > div { padding:8px 10px; font-size:12.5px; }
        .gt-colonnes b { font-size:13.5px; }
        .gt-deux-jours .gt-jour-tete { top:37px; }
        .gt-carte { display:flex; gap:8px; align-items:flex-start; border:1px solid var(--border-color,#e4e8ee); border-radius:8px;
                    padding:6px 8px; margin-bottom:6px; font-size:12.5px; background:#fff; }
        .gt-carte .gt-corps { flex:1; min-width:0; }
        .gt-carte .gt-sous { font-size:11.5px; color:var(--text-muted,#6b7280); }
        .gt-carte.libre { cursor:pointer; }
        .gt-carte.libre:hover { border-color:#94a3b8; }
        .gt-carte.on { background:#eef4ff; border-color:#3b82f6; }
        .gt-carte.verrou { background:#f8fafc; color:#6b7280; }
        .gt-carte.arrive { border:1.5px dashed #3b82f6; background:#eef4ff; }
        .gt-carte.arrive.conflit, .gt-carte.gene { border-color:#f59e0b; background:#fffbeb; }
        .gt-carte .gt-verrou { font-size:11.5px; font-weight:600; color:#6b7280; white-space:nowrap; }
        .gt-rien { font-size:12px; color:#9ca3af; padding:4px 2px 8px; }
        @media (max-width: 800px) {
          .gt-onglet span { display:none; }
          .gt-ligne { grid-template-columns:26px 96px 1fr 70px; }
          .gt-ligne .gt-c-emp, .gt-ligne .gt-c-sect, .gt-ligne .gt-c-type { display:none; }
        }`;
        document.head.appendChild(st);
    }

    // ── Dates ───────────────────────────────────────────────────────────────
    const aujourdhui = () => frappe.datetime.get_today();
    const plus = (jour, n) => frappe.datetime.add_days(jour, n);
    const lundi = (jour) => plus(jour, -((moment(jour).day() + 6) % 7));
    const ecart = (a, b) => moment(b).diff(moment(a), "days");
    function nom_du_jour(jour) {
        const m = moment(jour, "YYYY-MM-DD");
        if (!m.isValid()) return jour || __("sans date");
        const nom = JOURS[m.day()];
        return `${nom.charAt(0).toUpperCase()}${nom.slice(1)} ${m.date()} ${MOIS[m.month()]}`;
    }
    const hm = (q) => (q || "").slice(11, 16);
    // Le créneau d'une tâche, en minutes depuis minuit (fin inconnue : une heure).
    function creneau(l) {
        const mn = (q) => Number(q.slice(11, 13)) * 60 + Number(q.slice(14, 16));
        const debut = mn(l.quand);
        const fin = l.fin && l.fin.slice(0, 10) === l.quand.slice(0, 10) ? mn(l.fin) : (l.fin && l.fin > l.quand ? 24 * 60 : debut + 60);
        return [debut, Math.max(fin, debut + 1)];
    }
    const se_chevauchent = (a, b) => { const x = creneau(a), y = creneau(b); return x[0] < y[1] && y[0] < x[1]; };
    const heures = (l) => `${esc(hm(l.quand))}${l.fin && hm(l.fin) !== hm(l.quand) ? `<small>–${esc(hm(l.fin))}</small>` : ""}`;
    const type_html = (l) => `${ICONES[l.type] || "•"} ${esc(l.type || __("sans type"))}`;
    const nom_employe = (matricule) => {
        const x = (F.employes || []).concat(F.anciens || []).find((y) => y.valeur === matricule);
        return x ? x.libelle : matricule || "";
    };

    // Pourquoi une tâche ne se transfère pas — le reflet de taches_groupe.refus_transfert (qui décide).
    function verrou(l) {
        if (l.statut === "Completed") return __("terminée");
        if (ABSENCES.includes(l.type)) return __("absence");
        if (l.fixe) return __("employé fixe");
        return "";
    }

    function ouvrir(preselection, periode) {
        poser_css();
        selection = new Set(preselection || []);
        types = new Set();
        employes = new Set();
        applique = false;
        lignes = [];
        e = { action: "decaler", du: (periode && periode.from) || aujourdhui(),
              au: (periode && periode.to) || plus(aujourdhui(), 14),
              statut: (preselection && preselection.length) ? "" : "Open",
              de: "", vers: "", jours: 7, nouvelle_date: "", motif: "" };
        const d = new frappe.ui.Dialog({
            title: __("Gérer les interventions"),
            size: "extra-large",
            fields: [
                // L'action d'abord, ses réglages, la période et les filtres : tout ce qui se décide est en haut.
                { fieldtype: "HTML", fieldname: "tete" },
                { fieldtype: "HTML", fieldname: "resultats" },
                { fieldtype: "Section Break", fieldname: "sec_message", collapsible: 1,
                  label: __("✉️ Prévenir les clients (facultatif — message vide = aucun envoi)") },
                { fieldtype: "Select", fieldname: "choix_modele", label: __("Message prédéfini") },
                { fieldtype: "Small Text", fieldname: "message", label: __("Message") },
                { fieldtype: "Column Break" },
                { fieldtype: "Check", fieldname: "sms", default: 1, label: __("SMS") },
                { fieldtype: "Check", fieldname: "email", default: 1, label: __("E-mail") },
                { fieldtype: "Data", fieldname: "sujet", label: __("Objet de l'e-mail"),
                  depends_on: "email" },
                { fieldtype: "HTML", fieldname: "apercu" },
            ],
            primary_action_label: __("Appliquer aux tâches cochées"),
            primary_action: () => _appliquer(d),
        });

        d.fields_dict.choix_modele.$input.on("change", function () {
            const m = (d.__modeles || []).find((x) => x.libelle === $(this).val());
            if (!m) return;
            Promise.resolve(d.fields_dict.message.set_value(m.texte))
                .then(() => _apercu(d));
        });
        d.fields_dict.message.$input.on("input",
            frappe.utils.debounce(() => _apercu(d), 400));

        // L'action : on change de vue, pas seulement de bouton.
        d.$wrapper.on("click", ".gt-onglet", function () {
            const action = $(this).attr("data-action");
            if (action === e.action) return;
            if (action === "transferer" || e.action === "transferer") selection.clear();   // on ne coche pas la même chose
            e.action = action;
            _peindre_tete(d);
            _chercher(d);
        });
        // Les réglages et la période : l'état suit la saisie ; ce qui change la liste la recharge.
        d.$wrapper.on("change input", "[data-champ]", function (ev) {
            const champ = $(this).attr("data-champ");
            const recharge = ["du", "au", "statut", "de", "vers"].includes(champ);
            if (recharge && ev.type !== "change") return;
            e[champ] = $(this).val() || "";
            if (champ === "de") {
                selection.clear();
                if (e.vers === e.de) e.vers = "";
                // Un employé parti : ses tâches ouvertes sont souvent hors de la période affichée — on la pose pour lui.
                const ancien = (F.anciens || []).find((a) => a.valeur === e.de);
                if (ancien) { e.du = ancien.du; e.au = ancien.au; }
                _peindre_tete(d);
            }
            if (champ === "du" && e.au < e.du) { e.au = e.du; _peindre_tete(d); }
            if (champ === "au" && e.au < e.du) { e.du = e.au; _peindre_tete(d); }
            if (recharge) _chercher(d); else _bouton(d);
        });
        d.$wrapper.on("click", ".gt-rac", function () {
            const r = $(this).attr("data-rac"), j = aujourdhui();
            if (r === "jour") { e.du = j; e.au = j; }
            if (r === "demain") { e.du = plus(j, 1); e.au = e.du; }
            if (r === "semaine") { e.du = lundi(j); e.au = plus(e.du, 6); }
            if (r === "suivante") { e.du = plus(lundi(j), 7); e.au = plus(e.du, 6); }
            _peindre_tete(d);
            _chercher(d);
        });
        d.$wrapper.on("click", ".gt-nav", function () {
            const pas = (ecart(e.du, e.au) + 1) * Number($(this).attr("data-sens"));
            e.du = plus(e.du, pas); e.au = plus(e.au, pas);
            _peindre_tete(d);
            _chercher(d);
        });

        // Filtres à choix multiple.
        d.$wrapper.on("click", ".gt-chip", function () {
            const ens = $(this).attr("data-filtre") === "type" ? types : employes;
            const v = $(this).attr("data-valeur");
            if (v === "") ens.clear();                       // « Tous »
            else if (ens.has(v)) ens.delete(v); else ens.add(v);
            _peindre_tete(d);
            _chercher(d);
        });

        // Cocher : la case, ou n'importe où sur la ligne / la carte.
        const cocher = (n, oui) => {
            if (oui) selection.add(n); else selection.delete(n);
            _rendre(d);
            _apercu(d);
        };
        d.$wrapper.on("click", "[data-cocher]", function (ev) {
            if ($(ev.target).is("a")) return;
            const n = $(this).attr("data-cocher");
            cocher(n, !selection.has(n));
        });
        d.$wrapper.on("click", ".gt-tout", () => {
            _cochables().forEach((l) => selection.add(l.tache));
            _rendre(d);
            _apercu(d);
        });
        d.$wrapper.on("click", ".gt-aucun", () => {
            selection.clear();
            _rendre(d);
            _apercu(d);
        });
        d.$wrapper.on("click", ".gt-cocher-jour", function () {
            const jour = $(this).attr("data-jour");
            const du_jour = _cochables().filter((l) => l.quand.slice(0, 10) === jour);
            const tout = du_jour.every((l) => selection.has(l.tache));
            du_jour.forEach((l) => { if (tout) selection.delete(l.tache); else selection.add(l.tache); });
            _rendre(d);
            _apercu(d);
        });

        // À la fermeture : si une action a tourné, le calendrier (ou la liste) derrière se recharge.
        d.onhide = () => {
            document.body.classList.remove("gt-ouvert");
            if (applique && window.cur_list && cur_list.doctype === "Tache de travail" && cur_list.refresh) cur_list.refresh();
        };

        d.show();
        document.body.classList.add("gt-ouvert");
        try { (d.layout.sections || []).filter((s) => s.df && s.df.fieldname === "sec_message").forEach((s) => s.collapse(true)); } catch (err) { /* reste ouvert */ }
        _peindre_tete(d);
        _charger_filtres(d).then(() => _chercher(d));
        _charger_modeles(d);
        _apercu(d);
    }

    function _charger_filtres(d) {
        // Relus à chaque ouverture : un employé vient d'arriver ou de partir.
        return frappe.call({ method: API + ".get_filtres" })
            .then((r) => { F = r.message || {}; _peindre_tete(d); });
    }

    // Le haut de l'écran : l'action, ses réglages, la période, les filtres.
    function _peindre_tete(d) {
        const sous = { decaler: __("changer la date, même heure"), transferer: __("d’un employé à un autre"),
                       annuler: __("les tâches n’auront pas lieu") };
        const onglets = Object.keys(ACTIONS).map((nom) => {
            const a = ACTIONS[nom];
            return `<button type="button" class="gt-onglet ${e.action === a ? "on" : ""}" data-action="${a}">
                      <b>${ICONES_ACTION[a]} ${__(nom)}</b><span>${sous[a]}</span></button>`;
        }).join("");

        const options = (liste, choisi, vide) => `<option value="">${vide}</option>` + liste.map((x) =>
            `<option value="${esc(x.valeur)}" ${x.valeur === choisi ? "selected" : ""}>${esc(x.libelle)}</option>`).join("");
        const actifs = F.employes || [];
        let reglage;
        if (e.action === "transferer") {
            // « De » : les actifs, puis les partis qui gardent des tâches ouvertes. « À » : un actif, jamais le même.
            const origines = actifs.concat((F.anciens || []).map((a) => ({ valeur: a.valeur,
                libelle: __("{0} (parti) — {1} tâche(s) ouverte(s)", [a.libelle, a.ouvertes]) })));
            reglage = `<b>${__("De")}</b>
                <select class="form-control" data-champ="de">${options(origines, e.de, __("— qui laisse ses tâches ? —"))}</select>
                <span class="gt-fleche">→</span><b>${__("à")}</b>
                <select class="form-control" data-champ="vers">${options(actifs.filter((x) => x.valeur !== e.de), e.vers, __("— qui les reprend ? —"))}</select>
                <span class="gt-aide">${__("La date et l’heure ne changent pas. Verrouillées 🔒 : les tâches « employé fixe », les congés et récupérations, les tâches terminées.")}</span>`;
        } else if (e.action === "annuler") {
            reglage = `<b>${__("Motif")}</b>
                <input type="text" class="form-control" data-champ="motif" value="${esc(e.motif)}" style="flex:1;min-width:240px"
                       placeholder="${__("Pourquoi ces tâches sont annulées (gardé sur chaque tâche)")}">
                <span class="gt-aide">${__("La tâche reste au calendrier comme « annulée », avec son historique.")}</span>`;
        } else {
            reglage = `<b>${__("Décaler de")}</b>
                <input type="number" class="form-control" data-champ="jours" value="${esc(e.jours)}" style="width:76px"> ${__("jour(s)")}
                <span style="color:var(--text-muted)">${__("ou vers le")}</span>
                <input type="date" class="form-control" data-champ="nouvelle_date" value="${esc(e.nouvelle_date)}">
                <span class="gt-aide">${__("Nombre négatif pour avancer. L’heure et la durée sont conservées.")}</span>`;
        }

        const transfert = e.action === "transferer";
        const chips = (filtre, ens, valeurs) =>
            `<span class="gt-chip ${ens.size ? "" : "on"}" data-filtre="${filtre}" data-valeur="">${__("Tous")}</span>`
            + valeurs.map((x) => `<span class="gt-chip ${ens.has(x.valeur) ? "on" : ""}" data-filtre="${filtre}"
                 data-valeur="${esc(x.valeur)}">${esc(x.libelle)}</span>`).join("");
        const un_jour = e.du === e.au;
        d.fields_dict.tete.$wrapper.html(`
            <div class="gt-onglets">${onglets}</div>
            <div class="gt-reglage">${reglage}</div>
            <div class="gt-periode"><b>${__("Période")}</b>
              <button type="button" class="gt-nav" data-sens="-1" title="${un_jour ? __("Jour précédent") : __("Période précédente")}">‹</button>
              <input type="date" class="form-control" data-champ="du" value="${esc(e.du)}">
              <span>→</span>
              <input type="date" class="form-control" data-champ="au" value="${esc(e.au)}">
              <button type="button" class="gt-nav" data-sens="1" title="${un_jour ? __("Jour suivant") : __("Période suivante")}">›</button>
              <button type="button" class="gt-rac" data-rac="jour">${__("Aujourd’hui")}</button>
              <button type="button" class="gt-rac" data-rac="demain">${__("Demain")}</button>
              <button type="button" class="gt-rac" data-rac="semaine">${__("Cette semaine")}</button>
              <button type="button" class="gt-rac" data-rac="suivante">${__("Semaine prochaine")}</button>
              ${transfert ? "" : `<span class="gt-pousse"><b style="flex:none">${__("Statut")}</b>
                <select class="form-control" data-champ="statut">${STATUTS.map((x) =>
                    `<option value="${x[0]}" ${x[0] === e.statut ? "selected" : ""}>${__(x[1])}</option>`).join("")}</select></span>`}
            </div>
            ${transfert ? "" : `
            <div class="gt-filtre"><b>${__("Type")}</b><div class="gt-chips">${
                chips("type", types, (F.types || []).map((t) => ({ valeur: t, libelle: t })))}</div></div>
            <div class="gt-filtre"><b>${__("Employé")}</b><div class="gt-chips">${
                chips("employe", employes, actifs)}</div></div>`}`);
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
        const moi = ++tour;
        let args;
        if (e.action === "transferer") {
            // Les deux agendas entiers (tous statuts : une tâche terminée occupe aussi la journée de celui qui reçoit).
            if (!e.de) { lignes = []; _rendre(d); return; }
            args = { date_from: e.du, date_to: e.au, employe: JSON.stringify([e.de, e.vers].filter(Boolean)),
                     type_intervention: "[]", statut: "", limite: 600 };
        } else {
            args = { date_from: e.du, date_to: e.au,
                     employe: JSON.stringify(Array.from(employes)),
                     type_intervention: JSON.stringify(Array.from(types)),
                     statut: e.statut || "", limite: 400 };
        }
        frappe.call({
            method: API + ".rechercher",
            args,
            callback: (r) => {
                if (moi !== tour) return;                    // une recherche plus récente est partie entre-temps
                lignes = ((r.message || {}).lignes || []).filter((l) => l.quand);
                if (e.action === "transferer") {
                    // On ne garde cochées que des tâches encore transférables de l'employé d'origine.
                    const ok = new Set(_cochables().map((l) => l.tache));
                    selection.forEach((n) => { if (!ok.has(n)) selection.delete(n); });
                }
                _rendre(d);
            },
        });
    }

    // Ce qu'on peut cocher dans la vue en cours.
    function _cochables() {
        if (e.action !== "transferer") return lignes;
        return lignes.filter((l) => l.matricule === e.de && l.statut === "Open" && !verrou(l));
    }

    function _par_jour(liste) {
        const jours = new Map();
        liste.forEach((l) => { const j = l.quand.slice(0, 10); if (!jours.has(j)) jours.set(j, []); jours.get(j).push(l); });
        return jours;
    }

    function _tete_jour(jour, detail, cochables) {
        const j = aujourdhui();
        const tag = jour === j ? __("aujourd’hui") : jour === plus(j, 1) ? __("demain") : "";
        const tout = cochables.length && cochables.every((l) => selection.has(l.tache));
        return `<div class="gt-jour-tete"><b>${esc(nom_du_jour(jour))}</b>${tag ? `<span class="gt-tag">${tag}</span>` : ""}
                  <span class="gt-n">${detail}</span>
                  ${cochables.length ? `<a class="gt-cocher-jour" data-jour="${jour}">${tout ? __("décocher la journée") : __("cocher la journée")}</a>` : ""}</div>`;
    }

    function _rendre(d) {
        const zone = d.fields_dict.resultats.$wrapper;
        const haut = zone.find(".gt-cadre").scrollTop() || 0;        // on recoche sans perdre sa place
        zone.html(e.action === "transferer" ? _html_transfert() : _html_liste());
        zone.find(".gt-cadre").scrollTop(haut);
        _compter(d);
    }

    // ── Décaler / annuler : une liste, rangée par jour ──────────────────────
    function _html_liste() {
        const pastille = (st) => {
            const p = PASTILLES[st];
            return p ? `<span class="gt-pastille" style="color:${p[1]};background:${p[2]}">${__(p[0])}</span>` : esc(st || "");
        };
        const jours = _par_jour(lignes);
        const corps = lignes.length ? Array.from(jours.keys()).sort().map((jour) => {
            const ls = jours.get(jour);
            return _tete_jour(jour, __("{0} tâche(s)", [ls.length]), ls) + ls.map((l) => `
                <div class="gt-ligne ${selection.has(l.tache) ? "on" : ""}" data-cocher="${esc(l.tache)}" title="${esc(l.tache)}">
                  <input type="checkbox" ${selection.has(l.tache) ? "checked" : ""}>
                  <span class="gt-heure">${heures(l)}</span>
                  <span class="gt-coupe gt-c-type">${type_html(l)}${l.fixe ? ` <span title="${__("Employé fixe")}">🔒</span>` : ""}</span>
                  <span class="gt-coupe">${esc(l.client) || `<span class="gt-n">—</span>`}</span>
                  <span class="gt-coupe gt-c-emp">${esc(l.employe)}</span>
                  <span class="gt-coupe gt-c-sect gt-n">${esc(l.secteur)}</span>
                  <span>${pastille(l.statut)}</span>
                </div>`).join("");
        }).join("") : `<div class="gt-vide">${__("Aucune tâche pour cette période et ces filtres.")}</div>`;
        return _barre() + `<div class="gt-cadre">${corps}</div>`;
    }

    // ── Transférer : les deux agendas côte à côte, jour par jour ────────────
    function _html_transfert() {
        if (!e.de) return `<div class="gt-cadre"><div class="gt-vide">${__("Choisissez d’abord l’employé dont vous transférez les tâches (« De »).")}</div></div>`;
        const vivantes = lignes.filter((l) => l.statut !== "Cancelled");
        const src = _par_jour(vivantes.filter((l) => l.matricule === e.de));
        const dst = _par_jour(e.vers ? vivantes.filter((l) => l.matricule === e.vers) : []);
        const jours = Array.from(new Set([...src.keys(), ...dst.keys()])).sort();
        const nom_de = nom_employe(e.de).replace(/ \(parti\).*$/, ""), nom_vers = e.vers ? nom_employe(e.vers) : "";

        const carte_source = (l) => {
            const v = verrou(l), on = selection.has(l.tache);
            return `<div class="gt-carte ${v ? "verrou" : "libre"} ${on ? "on" : ""}" ${v ? "" : `data-cocher="${esc(l.tache)}"`} title="${esc(l.tache)}">
                ${v ? `<span>🔒</span>` : `<input type="checkbox" ${on ? "checked" : ""}>`}
                <div class="gt-corps"><div><span class="gt-heure">${heures(l)}</span> · ${type_html(l)}</div>
                  <div class="gt-sous gt-coupe">${esc(l.client)}${l.client && l.secteur ? " · " : ""}${esc(l.secteur)}</div></div>
                ${v ? `<span class="gt-verrou">${v}</span>` : on ? `<span class="gt-fleche" style="color:#3b82f6;font-weight:700">→</span>` : ""}</div>`;
        };
        const carte_dest = (l, arrive, genes) => `
            <div class="gt-carte ${arrive ? "arrive" : ""} ${arrive && genes.length ? "conflit" : ""} ${!arrive && genes.length ? "gene" : ""}" title="${esc(l.tache)}">
              <div class="gt-corps"><div><span class="gt-heure">${heures(l)}</span> · ${type_html(l)}${arrive ? ` <span class="gt-n">${__("← à recevoir")}</span>` : ""}</div>
                <div class="gt-sous gt-coupe">${esc(l.client)}${l.client && l.secteur ? " · " : ""}${esc(l.secteur)}</div>
                ${arrive && genes.length ? `<div class="gt-alerte" style="font-size:11.5px">⚠ ${ABSENCES.includes(genes[0].type)
                    ? __("{0} est en {1} à cette heure", [esc(nom_vers), esc(genes[0].type.toLowerCase())])
                    : __("chevauche {0} {1}", [esc(hm(genes[0].quand)), esc(genes[0].type || "")])}</div>` : ""}</div></div>`;

        const corps = jours.map((jour) => {
            const s = src.get(jour) || [], t = dst.get(jour) || [];
            const cochables = s.filter((l) => l.statut === "Open" && !verrou(l));
            const arrivees = cochables.filter((l) => selection.has(l.tache));
            const droite = t.map((l) => [l, false, arrivees.filter((a) => se_chevauchent(a, l))])
                .concat(arrivees.map((l) => [l, true, t.filter((x) => se_chevauchent(l, x))]))
                .sort((a, b) => (a[0].quand < b[0].quand ? -1 : a[0].quand > b[0].quand ? 1 : 0));
            const absent = t.find((l) => ABSENCES.includes(l.type));
            return _tete_jour(jour, __("{0} transférable(s)", [cochables.length])
                              + (absent ? ` · <span class="gt-alerte">${ICONES[absent.type] || ""} ${__("{0} est absent ce jour", [esc(nom_vers)])}</span>` : ""), cochables)
                + `<div class="gt-deux">
                     <div>${s.length ? s.map(carte_source).join("") : `<div class="gt-rien">${__("rien ce jour")}</div>`}</div>
                     <div>${!e.vers ? "" : droite.length ? droite.map((x) => carte_dest(x[0], x[1], x[2])).join("") : `<div class="gt-rien">${__("journée libre")}</div>`}</div>
                   </div>`;
        }).join("") || `<div class="gt-vide">${__("Aucune tâche pour {0} sur cette période.", [esc(nom_de)])}</div>`;

        return _barre() + `<div class="gt-cadre gt-deux-jours">
            <div class="gt-deux gt-colonnes">
              <div>${__("De")} <b>${esc(nom_de)}</b></div>
              <div>${e.vers ? `${__("à")} <b>${esc(nom_vers)}</b> <span class="gt-n">${__("— son agenda, avec ce qu’il recevra")}</span>`
                            : `<span class="gt-alerte">${__("Choisissez qui reprend les tâches (« à »), son agenda s’affichera ici.")}</span>`}</div>
            </div>${corps}</div>`;
    }

    function _barre() {
        return `<div class="gt-barre">
                  <span class="gt-coche"></span><span class="gt-compte"></span>
                  <a class="gt-tout">${__("tout cocher")}</a>
                  <a class="gt-aucun">${__("tout décocher")}</a>
                  <span class="gt-conflits gt-alerte"></span>
                </div>`;
    }

    // Les tâches cochées qui tombent sur une tâche (ou une absence) de celui qui reçoit.
    function _conflits() {
        if (e.action !== "transferer" || !e.vers) return 0;
        const chez_lui = lignes.filter((l) => l.matricule === e.vers && l.statut !== "Cancelled");
        return _cochables().filter((l) => selection.has(l.tache)
            && chez_lui.some((x) => x.quand.slice(0, 10) === l.quand.slice(0, 10) && se_chevauchent(l, x))).length;
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
        const transfert = e.action === "transferer";
        d.$wrapper.find(".gt-coche").text(__("{0} cochée(s)", [selection.size])).toggleClass("zero", !selection.size);
        d.$wrapper.find(".gt-compte").text(transfert ? __("sur {0} tâche(s) transférable(s)", [_cochables().length])
                                                     : __("sur {0} tâche(s) affichée(s)", [lignes.length]));
        const n = _conflits();
        d.$wrapper.find(".gt-conflits").text(n ? __("⚠ {0} chevauchement(s) d’horaire chez {1}", [n, nom_employe(e.vers)]) : "");
        _bouton(d);
    }

    // Le bouton dit ce qu'il va faire : « Transférer 3 tâche(s) à Jamel Aloui ».
    function _bouton(d) {
        const nom = Object.keys(ACTIONS).find((k) => ACTIONS[k] === e.action) || "Décaler";
        let libelle = selection.size ? __("{0} {1} tâche(s)", [__(nom), selection.size]) : __("Appliquer aux tâches cochées");
        if (selection.size && e.action === "transferer" && e.vers) libelle += " " + __("à {0}", [nom_employe(e.vers)]);
        d.set_primary_action(libelle, () => _appliquer(d));
        // Rouge pour annuler — par une classe à nous : sans « btn-primary », Frappe ne retrouve plus son bouton.
        d.get_primary_btn().toggleClass("gt-rouge", e.action === "annuler").prop("disabled", !selection.size);
    }

    function _appliquer(d) {
        const v = d.get_values(true) || {};
        const taches = Array.from(selection);
        if (!taches.length) {
            frappe.msgprint(__("Cochez au moins une tâche."));
            return;
        }
        const action = e.action;
        const vers = e.vers || "";
        const nom_vers = nom_employe(vers);
        const jours = parseInt(e.jours, 10) || 0;
        if (action === "decaler" && !jours && !e.nouvelle_date) {
            frappe.msgprint(__("Indiquez un nombre de jours ou une date."));
            return;
        }
        if (action === "transferer" && !vers) {
            frappe.msgprint(__("Choisissez l’employé à qui transférer les tâches."));
            return;
        }
        const conflits = _conflits();
        const question = { decaler: __("Décaler {0} tâche(s) ?", [taches.length]),
                           transferer: __("Transférer {0} tâche(s) à {1} ?", [taches.length, esc(nom_vers)])
                               + (conflits ? `<br><span style="color:#b45309">⚠ ${__("{0} tombe(nt) sur une tâche ou une absence de {1}.", [conflits, esc(nom_vers)])}</span>` : ""),
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
                    action === "annuler" ? { motif: e.motif }
                    : action === "transferer" ? { vers }
                    : { jours, nouvelle_date: e.nouvelle_date || null }),
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
                    _charger_filtres(d).then(() => _chercher(d));      // un employé parti peut ne plus rien avoir à reprendre
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
        b.title = "Décaler, transférer ou annuler plusieurs interventions, en prévenant les clients";
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
