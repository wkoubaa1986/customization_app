/**
 * « 📅 Mon agenda » — la PAGE de fond du sous-traitant sur son téléphone (demande 31/10/2026) : l'app
 * lui montre ses tâches, jamais la liste ni le calendrier du Desk (sous_traitant.js y redirige).
 * Trois vues (Jour / Semaine / Mois), ‹ › et « Aujourd'hui », ses tâches seulement
 * (customization_app.sous_traitant.agenda). Toucher un jour ouvre sa liste ; toucher une tâche ouvre
 * « Ma journée » de ce jour (adresse, Maps, appel, clôture, annulation) par-dessus la page.
 */
frappe.pages["agenda-sous-traitant"].on_page_load = function (wrapper) {
    frappe.ui.make_app_page({ parent: wrapper, title: __("Mon agenda"), single_column: true });
    wrapper.agenda = new AgendaSousTraitant($(wrapper).find(".layout-main-section"));
};
frappe.pages["agenda-sous-traitant"].on_page_show = function (wrapper) {
    // Retour sur la page (ou fermeture de « Ma journée ») : une tâche a pu être clôturée ou annulée.
    if (wrapper.agenda) wrapper.agenda.rafraichir();
};

(function () {
    const ICONES = { "Entretien": "🔧", "Réparation": "🧰", "Installation": "🔨", "Livraison": "🚚", "Visite": "👋", "Autre": "☕" };
    const JOURS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"];
    const MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"];
    const esc = (t) => frappe.utils.escape_html(String(t == null ? "" : t));
    const m = (d) => moment(d, "YYYY-MM-DD");
    const iso = (mo) => mo.format("YYYY-MM-DD");

    function poser_css() {
        if (document.getElementById("ag-css")) return;
        const s = document.createElement("style");
        s.id = "ag-css";
        s.textContent = `
        .ag-barre { display:flex; align-items:center; gap:6px; margin-bottom:8px; flex-wrap:wrap; }
        .ag-barre .btn { min-height:40px; }
        .ag-titre { flex:1; text-align:center; font-weight:700; font-size:15px; text-transform:capitalize; }
        .ag-vues { display:flex; border:1px solid var(--border-color,#e4e8ee); border-radius:10px; overflow:hidden; margin-bottom:10px; }
        .ag-vues button { flex:1; min-height:40px; border:none; background:#fff; font-weight:600; color:#6b7280; }
        .ag-vues button.on { background:#2490ef; color:#fff; }
        .ag-tache { display:flex; gap:10px; align-items:flex-start; padding:10px 12px; border:1px solid var(--border-color,#e4e8ee);
                    border-radius:12px; margin-bottom:8px; background:#fff; cursor:pointer; }
        .ag-tache.faite { border-color:#86efac; background:#f0fdf4; }
        .ag-tache.annulee { opacity:.55; text-decoration:line-through; }
        .ag-heure { font-weight:700; min-width:48px; font-size:15px; }
        .ag-client { font-weight:600; }
        .ag-muted { color:#6b7280; font-size:12.5px; }
        .ag-vide { color:#6b7280; text-align:center; padding:24px 0; }
        .ag-sem-jour { display:flex; gap:10px; padding:8px 0; border-bottom:1px solid #eef1f5; cursor:pointer; }
        .ag-sem-jour.auj .ag-sem-date { background:#2490ef; color:#fff; }
        .ag-sem-date { width:52px; min-width:52px; text-align:center; border-radius:10px; padding:4px 0; font-weight:700; line-height:1.15; }
        .ag-sem-date small { display:block; font-weight:600; font-size:11px; }
        .ag-sem-liste { flex:1; display:grid; gap:4px; font-size:13px; }
        .ag-sem-ligne.faite { color:#166534; } .ag-sem-ligne.annulee { text-decoration:line-through; opacity:.55; }
        .ag-grille { display:grid; grid-template-columns:repeat(7,1fr); gap:4px; }
        .ag-grille .ag-entete { text-align:center; font-size:11px; font-weight:700; color:#6b7280; padding:2px 0; }
        .ag-case { min-height:58px; border:1px solid #eef1f5; border-radius:8px; padding:4px; cursor:pointer; background:#fff; }
        .ag-case.hors { opacity:.35; } .ag-case.auj { border-color:#2490ef; box-shadow:inset 0 0 0 1px #2490ef; }
        .ag-case .n { font-weight:700; font-size:12px; }
        .ag-pastille { display:inline-block; border-radius:999px; font-size:10.5px; font-weight:700; padding:0 6px; margin:2px 2px 0 0; }
        .ag-pastille.ouv { background:#fef3c7; color:#92400e; } .ag-pastille.ok { background:#dcfce7; color:#166534; }
        .ag-jour-ouvrir { width:100%; min-height:44px; margin-top:6px; }
        .ag-page { max-width:720px; margin:0 auto; }`;
        document.head.appendChild(s);
    }

    class AgendaSousTraitant {
        constructor($conteneur) {
            poser_css();
            this.vue = "jour";
            this.jour = frappe.datetime.get_today();
            this.cache = {};            // "YYYY-MM-DD" → [tâches]
            this.charges = new Set();   // plages déjà lues ("debut|fin")
            this.$c = $('<div class="ag-page"></div>').appendTo($conteneur);
            this._bind();
            this.rendre();
            // « Ma journée » refermée par-dessus : l'agenda se relit.
            $(document).on("hidden.bs.modal", () => { if (this.$c.is(":visible")) this.rafraichir(); });
        }

        rafraichir() { this.cache = {}; this.charges.clear(); this.rendre(); }

        _bind() {
            const w = this.$c;
            w.on("click", "[data-vue]", (e) => { this.vue = $(e.currentTarget).data("vue"); this.rendre(); });
            w.on("click", "[data-nav]", (e) => { this.jour = iso(this._debut().add(Number($(e.currentTarget).data("nav")), this._pas())); this.rendre(); });
            w.on("click", "[data-aujourdhui]", () => { this.jour = frappe.datetime.get_today(); this.rendre(); });
            w.on("click", "[data-jour]", (e) => { this.jour = $(e.currentTarget).data("jour"); this.vue = "jour"; this.rendre(); });
            w.on("click", "[data-journee]", (e) => {
                e.stopPropagation();
                if (window.maJournee_ouvrir) window.maJournee_ouvrir($(e.currentTarget).data("journee"));
            });
        }

        // Début de la vue courante (moment) et unité de déplacement.
        _debut() {
            const d = m(this.jour);
            if (this.vue === "semaine") return d.startOf("isoWeek");
            if (this.vue === "mois") return d.startOf("month");
            return d;
        }
        _pas() { return this.vue === "semaine" ? "week" : this.vue === "mois" ? "month" : "day"; }
        _plage() {
            const d = this._debut();
            if (this.vue === "jour") return [iso(d), iso(d)];
            if (this.vue === "semaine") return [iso(d), iso(d.clone().add(6, "day"))];
            // Mois : la grille complète (lundi avant le 1er → dimanche après le dernier jour).
            return [iso(d.clone().startOf("isoWeek")), iso(d.clone().endOf("month").endOf("isoWeek"))];
        }

        async _charger(debut, fin) {
            const cle = debut + "|" + fin;
            if (this.charges.has(cle)) return;
            const r = await frappe.call({ method: "customization_app.sous_traitant.agenda", args: { debut, fin } });
            let d = m(debut);
            while (iso(d) <= fin) { this.cache[iso(d)] = []; d.add(1, "day"); }
            (r.message || []).forEach((t) => { (this.cache[t.jour] = this.cache[t.jour] || []).push(t); });
            this.charges.add(cle);
        }

        async rendre() {
            const [debut, fin] = this._plage();
            this.$c.html(`<div class="ag-vide">${__("Chargement…")}</div>`);
            try { await this._charger(debut, fin); }
            catch (e) { this.$c.html(`<div class="ag-vide">${__("Impossible de lire l'agenda.")}</div>`); return; }
            const d = this._debut();
            const titre = this.vue === "jour" ? d.format("dddd D MMMM") : this.vue === "semaine"
                ? `${d.format("D MMM")} → ${d.clone().add(6, "day").format("D MMM")}` : `${MOIS[d.month()]} ${d.year()}`;
            const corps = this.vue === "jour" ? this._jour() : this.vue === "semaine" ? this._semaine() : this._mois();
            this.$c.html(`
                <div class="ag-vues">${["jour", "semaine", "mois"].map((v) =>
                    `<button type="button" data-vue="${v}" class="${v === this.vue ? "on" : ""}">${__(v === "jour" ? "Jour" : v === "semaine" ? "Semaine" : "Mois")}</button>`).join("")}</div>
                <div class="ag-barre">
                  <button class="btn btn-default" data-nav="-1">‹</button>
                  <div class="ag-titre">${esc(titre)}</div>
                  <button class="btn btn-default" data-nav="1">›</button>
                  <button class="btn btn-default btn-sm" data-aujourdhui>${__("Aujourd'hui")}</button>
                </div>${corps}`);
            if (window._st_nav_peindre) window._st_nav_peindre();
        }

        _classe(t) { return t.status === "Completed" ? "faite" : t.status === "Cancelled" ? "annulee" : ""; }

        _jour() {
            const taches = this.cache[this.jour] || [];
            const liste = taches.length ? taches.map((t) => `
                <div class="ag-tache ${this._classe(t)}" data-journee="${esc(this.jour)}">
                  <div class="ag-heure">${esc(t.debut)}</div>
                  <div style="flex:1">
                    <div><span>${ICONES[t.type] || "📌"}</span> <b>${esc(t.type)}</b> · <span class="ag-client">${esc(t.client)}</span></div>
                    ${t.adresse ? `<div class="ag-muted">${esc(t.adresse)}</div>` : ""}
                    <div class="ag-muted">${t.duree ? esc(t.duree) + " · " : ""}${t.status === "Completed" ? "✅ " + __("Terminée")
                        : t.status === "Cancelled" ? "❌ " + __("Annulée") + (t.raison_annulation ? " (" + esc(t.raison_annulation) + ")" : "")
                        : "🕒 " + __("À faire")}${t.commande ? " · 🧾 " + __("commande préparée") : ""}</div>
                  </div>
                </div>`).join("") : `<div class="ag-vide">${__("Aucune intervention ce jour.")}</div>`;
            return liste + `<button class="btn btn-primary ag-jour-ouvrir" data-journee="${esc(this.jour)}">☀️ ${__("Ouvrir Ma journée du")} ${frappe.datetime.str_to_user(this.jour)}</button>`;
        }

        _semaine() {
            const auj = frappe.datetime.get_today();
            let d = this._debut();
            const lignes = [];
            for (let i = 0; i < 7; i++) {
                const j = iso(d), taches = this.cache[j] || [];
                lignes.push(`
                  <div class="ag-sem-jour ${j === auj ? "auj" : ""}" data-jour="${j}">
                    <div class="ag-sem-date">${JOURS[i]}<small>${d.format("D/M")}</small></div>
                    <div class="ag-sem-liste">${taches.length ? taches.map((t) =>
                        `<div class="ag-sem-ligne ${this._classe(t)}"><b>${esc(t.debut)}</b> ${ICONES[t.type] || "📌"} ${esc(t.type)} · ${esc(t.client)}</div>`).join("")
                        : `<span class="ag-muted">—</span>`}</div>
                  </div>`);
                d = d.add(1, "day");
            }
            return lignes.join("");
        }

        _mois() {
            const auj = frappe.datetime.get_today(), mois = this._debut().month();
            const [debut, fin] = this._plage();
            let d = m(debut);
            const cases = JOURS.map((j) => `<div class="ag-entete">${j}</div>`);
            while (iso(d) <= fin) {
                const j = iso(d), taches = this.cache[j] || [];
                const ouv = taches.filter((t) => t.status === "Open").length, ok = taches.filter((t) => t.status === "Completed").length;
                cases.push(`<div class="ag-case ${d.month() !== mois ? "hors" : ""} ${j === auj ? "auj" : ""}" data-jour="${j}">
                    <div class="n">${d.date()}</div>
                    ${ouv ? `<span class="ag-pastille ouv">${ouv}</span>` : ""}${ok ? `<span class="ag-pastille ok">${ok}</span>` : ""}</div>`);
                d = d.add(1, "day");
            }
            return `<div class="ag-grille">${cases.join("")}</div>
                    <div class="ag-muted" style="margin-top:8px">${__("Pastilles : à faire (orange), terminées (vert). Touchez un jour pour le détail.")}</div>`;
        }
    }

    window.AgendaSousTraitant = AgendaSousTraitant;
})();
