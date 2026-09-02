/**
 * « 🗓️ Ma journée » — une FENÊTRE, pas une page (demande 02/09/2026).
 *
 * Le technicien s'en sert sur son TÉLÉPHONE, entre deux interventions : ouvrir
 * un onglet, perdre le calendrier, revenir — c'est trois gestes de trop. La
 * fenêtre s'ouvre par-dessus, se ferme, et le calendrier est toujours là.
 *
 * MOBILE D'ABORD. Sous 768 px, la fenêtre prend tout l'écran, les libellés
 * passent au-dessus des valeurs et les boutons deviennent des cibles de doigt
 * (44 px). Au-dessus, c'est une fenêtre ordinaire.
 *
 * Toute la décision est CÔTÉ SERVEUR (customization_app.planning_employe) :
 * l'écran n'invente ni les droits, ni les règles de clôture, ni la validité
 * d'un bordereau. Il collecte, il affiche, il demande.
 */

frappe.provide("frappe.views");

(function () {
    const LIBELLE = "🗓️ Ma journée";
    const ICONES = {
        "Entretien": "🔧", "Réparation": "🧰", "Installation": "🔨",
        "Livraison": "🚚", "Visite": "👋", "Autre": "☕",
    };

    function poser_css() {
        if (document.getElementById("mj-css")) return;
        const style = document.createElement("style");
        style.id = "mj-css";
        style.textContent = `
        .mj-bar { display:flex; flex-wrap:wrap; gap:6px; align-items:center;
                  margin-bottom:10px; }
        .mj-bar .form-control { height:34px; font-size:13px; width:auto; min-width:130px; }
        .mj-bar .btn { height:34px; }
        .mj-compte { width:100%; font-size:12.5px; color:var(--text-muted,#6b7280); }
        .mj-carte { border:1px solid var(--border-color,#e4e8ee); border-radius:12px;
                    background:var(--card-bg,#fff); margin-bottom:10px; overflow:hidden; }
        /* Terminée = VERT (demande 02/09/2026). Le gris pâle d'avant se lisait
           comme « désactivée » : on ne voyait pas ce qui était FAIT. */
        .mj-carte.faite { border-color:#86efac; background:#f0fdf4; }
        .mj-carte.faite .mj-tete { border-bottom-color:#bbf7d0; }
        .mj-carte.faite .mj-heure { color:#166534; }
        .mj-tete { display:flex; align-items:center; gap:7px; padding:9px 11px;
                   border-bottom:1px solid var(--border-color,#eef1f5); flex-wrap:wrap; }
        .mj-heure { font-weight:700; font-size:15px; }
        .mj-client { font-weight:600; }
        .mj-corps { padding:9px 11px; display:grid; gap:8px; }
        .mj-l { display:flex; gap:8px; align-items:flex-start; font-size:13px; }
        .mj-l .k { color:var(--text-muted,#6b7280); min-width:78px; font-weight:600;
                   font-size:10.5px; text-transform:uppercase; letter-spacing:.3px;
                   padding-top:3px; }
        .mj-badge { display:inline-block; padding:1px 7px; border-radius:999px;
                    font-size:10.5px; font-weight:700; white-space:nowrap; }
        .mj-badge.ok { background:#dcfce7; color:#166534; }
        .mj-badge.ko { background:#fee2e2; color:#b02a37; }
        .mj-badge.att { background:#fef3c7; color:#92400e; }
        .mj-badge.inf { background:#e0f2fe; color:#075985; }
        .mj-badge.gris { background:#eef2f7; color:#64748b; }
        .mj-tel { display:inline-block; margin:0 8px 4px 0; font-size:14px;
                  font-weight:600; }
        .mj-hist { font-size:11px; color:var(--text-muted,#6b7280); margin-top:3px; }
        .mj-art { display:flex; gap:8px; align-items:flex-start; padding:3px 0; }
        .mj-art img { width:44px; height:44px; object-fit:cover; border-radius:8px;
                      border:1px solid var(--border-color,#e4e8ee); background:#fff; }
        .mj-art .mj-art-vide { width:44px; height:44px; display:inline-flex;
                      align-items:center; justify-content:center; border-radius:8px;
                      background:var(--bg-light-gray,#f1f5f9); font-size:19px; }
        .mj-conf { font-size:11.5px; color:var(--text-muted,#6b7280); margin-top:1px; }
        .mj-liens { display:flex; gap:12px; flex-wrap:wrap; margin-top:5px;
                    font-size:12px; }
        .mj-ref { font-size:11px; font-family:monospace; opacity:.75; }
        .mj-photos { display:flex; gap:6px; flex-wrap:wrap; }
        .mj-photos img { width:66px; height:66px; object-fit:cover; border-radius:8px;
                         border:1px solid var(--border-color,#e4e8ee); }
        .mj-manque { color:#b02a37; }
        .mj-actions { display:flex; flex-wrap:wrap; gap:6px; padding:9px 11px;
                      border-top:1px solid var(--border-color,#eef1f5);
                      background:var(--bg-light-gray,#f8fafc); }
        .mj-vide { padding:26px; text-align:center; color:var(--text-muted,#6b7280); }
        /* Le doigt, pas la souris. */
        @media (max-width: 767px) {
          .modal.mj-modal .modal-dialog { margin:0; max-width:100%; width:100%; }
          .modal.mj-modal .modal-content { min-height:100vh; border-radius:0; }
          .mj-l { flex-direction:column; gap:2px; }
          .mj-l .k { min-width:0; }
          .mj-actions .btn, .mj-bar .btn { min-height:44px; flex:1 1 46%; }
          .mj-bar .form-control { min-height:44px; flex:1 1 100%; }
          .mj-tel { display:block; font-size:16px; padding:6px 0; }
        }`;
        document.head.appendChild(style);
    }

    class Journee {
        constructor() {
            poser_css();
            this.data = null;
            this.jour = frappe.datetime.get_today();
            this.employe = null;
            this.dialog = new frappe.ui.Dialog({
                title: __("Ma journée"),
                size: "large",
                fields: [{ fieldtype: "HTML", fieldname: "corps" }],
            });
            this.dialog.$wrapper.addClass("mj-modal");
            this.$corps = this.dialog.fields_dict.corps.$wrapper;
            this._bind();
            this.dialog.show();
            this.charger();
        }

        _bind() {
            const w = this.dialog.$wrapper;
            w.on("change", "[data-jour]", (e) => { this.jour = e.currentTarget.value; this.charger(); });
            w.on("change", "[data-employe]", (e) => { this.employe = e.currentTarget.value; this.charger(); });
            w.on("click", "[data-decaler]", (e) => {
                this.jour = frappe.datetime.add_days(this.jour,
                    parseInt($(e.currentTarget).data("decaler"), 10));
                this.charger();
            });
            w.on("click", "[data-recharger]", () => this.charger());
            w.on("click", "[data-caisse]", () => this._caisse());
            w.on("click", "[data-appel]", (e) => this._appeler(e.currentTarget));
            w.on("click", "[data-aramex]", (e) => this._aramex(e.currentTarget));
            w.on("click", "[data-bordereau-photo]", (e) =>
                this._photo_bordereau($(e.currentTarget).data("bordereau-photo")));
            w.on("click", "[data-terminer]", (e) => this._terminer(e.currentTarget));
            w.on("click", "[data-fiche]", (e) => {
                e.preventDefault();
                this._fiche($(e.currentTarget).data("fiche"));
            });
        }

        charger() {
            this.$corps.html(`<div class="mj-vide">${__("Chargement…")}</div>`);
            frappe.call({
                method: "customization_app.planning_employe.ma_journee",
                args: { date: this.jour, employe: this.employe },
                callback: (r) => { this.data = r.message || {}; this._rendre(); },
                // ⚠️ ON MONTRE LE MESSAGE DU SERVEUR. Un « indisponible » générique
                // cachait la vraie cause — « aucune fiche employé rattachée » —
                // et laissait chercher un bug qui n'existait pas.
                error: (r) => this.$corps.html(
                    `<div class="mj-vide">${frappe.utils.escape_html(
                        (r && r.message) || __("Journée indisponible."))}</div>`),
            });
        }

        // -------------------------------------------------------------- rendu

        _rendre() {
            const esc = frappe.utils.escape_html;
            const m = this.data;
            const lignes = m.lignes || [];
            const restant = lignes.filter((l) => l.statut === "Open").length;

            const selecteur = (m.supervise && (m.employes || []).length)
                ? `<select class="form-control" data-employe>
                     <option value="">${__("— choisir un employé")}</option>
                     ${(m.employes || []).map((e) =>
                        `<option value="${esc(e.nom)}"${e.nom === m.employe ? " selected" : ""}
                          >${esc(e.libelle || e.nom)}</option>`).join("")}
                   </select>` : "";

            const corps = m.sans_employe
                ? `<div class="mj-vide">${__("Choisissez un employé pour voir sa journée.")}</div>`
                : (lignes.length
                    ? lignes.map((l) => this._carte(l, esc)).join("")
                    : `<div class="mj-vide">${__("Aucune intervention ce jour-là.")}</div>`);

            this.$corps.html(`
              <div class="mj-bar">
                <input type="date" class="form-control" data-jour value="${esc(this.jour)}">
                ${selecteur}
                <button class="btn btn-sm btn-default" data-decaler="-1">◀</button>
                <button class="btn btn-sm btn-default" data-decaler="1">▶</button>
                <button class="btn btn-sm btn-primary" data-recharger>🔄 ${__("Actualiser")}</button>
                <button class="btn btn-sm btn-default" data-caisse>💰 ${__("Ma caisse")}</button>
                <span class="mj-compte">${m.sans_employe ? ""
                    : `<b>${esc(m.employe_nom || "")}</b> · ${lignes.length} ${
                        __("intervention(s)")}, ${restant} ${__("à faire")}`}</span>
              </div>
              ${corps}`);
        }

        _carte(l, esc) {
            const faite = l.statut !== "Open";
            const st = { "Open": ["att", __("à faire")],
                         "Completed": ["ok", __("terminée")] }[l.statut] || ["gris", l.statut];
            return `<div class="mj-carte ${faite ? "faite" : ""}">
              <div class="mj-tete">
                <span class="mj-heure">${esc(l.debut)}</span>
                <span class="mj-badge inf">${ICONES[l.type] || "📌"} ${esc(l.type || "?")}</span>
                <span class="mj-client">${esc(l.client || "")}</span>
                ${l.secteur ? `<span class="mj-badge gris">📍 ${esc(l.secteur)}</span>` : ""}
                <a href="#" class="mj-ref" data-fiche="${esc(l.tache)}"
                   title="${__("Ouvrir la fiche sans quitter cet écran")}">${esc(l.tache)}</a>
                <span class="mj-badge ${st[0]}" style="margin-left:auto">${esc(st[1])}</span>
              </div>
              <div class="mj-corps">
                ${this._tel(l, esc)}${this._adresse(l, esc)}${this._articles(l, esc)}
                ${l.note ? `<div class="mj-l"><span class="k">${__("Note")}</span>
                    <span>${esc(l.note)}</span></div>` : ""}
                ${this._aramex_bloc(l, esc)}${this._cloture(l, esc)}${this._reglement(l, esc)}
                ${this._photos(l, esc)}
              </div>
              ${faite ? "" : this._actions(l, esc)}
            </div>`;
        }

        _tel(l, esc) {
            if (!(l.telephones || []).length) {
                return `<div class="mj-l"><span class="k">${__("Client")}</span>
                    <span class="mj-manque">${__("aucun numéro au dossier")}</span></div>`;
            }
            // Combien de fois CE numéro a déjà été composé, affiché devant lui :
            // c'est ce qui distingue « on n'a pas encore essayé » de « on a
            // essayé trois fois », et donc décide d'annuler ou de rappeler.
            const compte = {};
            (l.appels || []).forEach((a) => {
                if (a.numero) compte[a.numero] = (compte[a.numero] || 0) + 1;
            });
            const liens = (l.telephones || []).map((t) => {
                const n = compte[t] || 0;
                return `<a class="mj-tel" href="tel:${esc(t)}" data-appel="${esc(t)}"
                   data-tache="${esc(l.tache)}">${n ? `<span class="mj-badge ${
                     n >= 2 ? "att" : "gris"}">${n}×</span> ` : ""}📞 ${esc(t)}</a>`;
            }).join("");
            const hist = (l.appels || []).length
                ? `<div class="mj-hist">${(l.appels || []).slice(0, 3).map((a) =>
                     `${esc(a.quand)} — ${esc(a.texte.replace("📞 Appel ", ""))}`).join("<br>")}</div>`
                : "";
            return `<div class="mj-l"><span class="k">${__("Appeler")}</span>
                <span>${liens}${hist}</span></div>`;
        }

        _adresse(l, esc) {
            if (!l.adresse && !l.google_map) return "";
            // Sans lien enregistré, on en fabrique un : mieux vaut une recherche
            // Maps que rien du tout devant une porte cochère.
            const url = l.google_map || (l.adresse
                ? "https://www.google.com/maps/search/?api=1&query="
                  + encodeURIComponent(l.adresse) : "");
            return `<div class="mj-l"><span class="k">${__("Adresse")}</span><span>
                ${esc(l.adresse || "")}
                ${url ? `<div><a href="${esc(url)}" target="_blank" rel="noopener"
                    >🗺️ ${l.google_map ? __("Ouvrir dans Maps") : __("Chercher dans Maps")}</a></div>`
                  : ""}</span></div>`;
        }

        _articles(l, esc) {
            if (!(l.articles || []).length) return "";
            // La PHOTO d'abord : un osmoseur se reconnaît à son image, pas à son
            // code. Et sous une VARIANTE, sa configuration — sans elle, deux
            // variantes du même modèle sont indiscernables sur l'écran.
            const arts = (l.articles || []).map((a) => `
              <div class="mj-art">
                ${a.image ? `<img src="${esc(a.image)}" alt="" loading="lazy">`
                          : `<span class="mj-art-vide">📦</span>`}
                <div>
                  <div><b>${a.qte}×</b> ${esc(a.article)}</div>
                  ${(a.configuration || []).length
                    ? `<div class="mj-conf">${(a.configuration || []).map((c) =>
                        `${esc(c.attribut)} : <b>${esc(c.valeur)}</b>`).join("<br>")}</div>`
                    : ""}
                </div>
              </div>`).join("");
            const liens = l.commande ? `<div class="mj-liens">
                <a href="/app/sales-order/${encodeURIComponent(l.commande)}"
                   target="_blank">${esc(l.commande)} ↗</a>
                <a href="/api/method/frappe.utils.print_format.download_pdf?doctype=Sales%20Order&name=${
                   encodeURIComponent(l.commande)}" target="_blank" rel="noopener"
                  >📄 ${__("Commande en PDF")}</a></div>` : "";
            return `<div class="mj-l"><span class="k">${__("À poser")}</span>
                <span>${arts}${liens}</span></div>`;
        }

        /** Le bordereau d une LIVRAISON Aramex — et son absence, en ROUGE.
         *
         *  Un colis parti sans bordereau ne se suit plus : ni nous, ni le
         *  client, ni la réclamation. C est la seule chose de cette carte qui
         *  DOIT être faite avant de partir, d où le rouge et non l orange.
         *
         *  Le bloc n apparaît que sur une Livraison : sur une Installation,
         *  l échéancier Aramex est un reste de la création web et ne décrit
         *  rien — le serveur l a déjà neutralisé.
         */
        _aramex_bloc(l, esc) {
            if (!l.aramex) return "";
            const manque = !l.bordereau;
            return `<div class="mj-l"><span class="k">Aramex</span><span>
                ${manque ? `<span class="mj-badge ko">⚠️ ${__("bordereau manquant")}</span>`
                         : `<span class="mj-badge ok">📦 ${esc(l.bordereau)}</span>`}
                ${l.statut === "Open" ? `<div style="display:flex;gap:5px;margin-top:5px;
                      flex-wrap:wrap">
                    <input type="text" class="form-control" inputmode="numeric"
                      placeholder="${__("N° de bordereau")}" data-champ="${esc(l.tache)}"
                      value="${esc(l.bordereau || "")}">
                    <button class="btn btn-sm btn-default"
                      data-bordereau-photo="${esc(l.tache)}"
                      >📷 ${__("Photo")}</button>
                    <button class="btn btn-sm ${manque ? "btn-danger" : "btn-default"}"
                      data-aramex="${esc(l.tache)}"
                      >${manque ? __("Enregistrer") : __("Vérifier")}</button></div>
                    <div class="mj-conf">${
                      __("Photographiez le bordereau, puis enregistrez : le numéro est lu sur la photo avant d être inscrit sur la commande.")
                    }</div>` : ""}
              </span></div>`;
        }

        /** Ce qu'il reste à faire — dit AVANT que « Terminer » n'échoue. */
        _cloture(l, esc) {
            if (l.statut !== "Open") return "";
            const e = l.exigences || {};
            // Le VERDICT vient de la fiche (`tache_exigences_completes`) : deux
            // juges pour une même clôture finiraient par se contredire.
            if (typeof tache_exigences_completes === "function"
                    && tache_exigences_completes(e)) {
                return `<div class="mj-l"><span class="k">${__("Clôture")}</span>
                    <span class="mj-badge ok">${__("prête")}</span></div>`;
            }
            const manque = [];
            if (!e.dispense && e.concerne) {
                const min = e.minima || {}, ph = e.photos || {};
                if ((min.avant || 0) > (ph.avant || 0))
                    manque.push(__("{0} photo(s) avant", [(min.avant || 0) - (ph.avant || 0)]));
                if ((min.apres || 0) > (ph.apres || 0))
                    manque.push(__("{0} photo(s) après", [(min.apres || 0) - (ph.apres || 0)]));
                if (e.gmap_requis && !e.gmap) manque.push(__("position GPS"));
            }
            if (e.commande_requise && !e.commande) manque.push(__("commande liée"));
            if (e.rapport_requis && !e.rapport) manque.push(__("compte rendu"));
            return `<div class="mj-l"><span class="k">${__("Clôture")}</span><span>${
                manque.length
                    ? `<span class="mj-manque">${__("Il manque")} : ${manque.map(esc).join(" · ")}</span>`
                    : `<span class="mj-badge ok">${__("prête")}</span>`}</span></div>`;
        }

        /** ⚠️ UN SEUL CHEMIN DE CLÔTURE, celui de la fiche.
         *
         *  J'avais posé ici des boutons « Avant / Après » de mon cru : c'est
         *  faux. Les photos ne se rangent pas en deux tas — chaque type
         *  d'intervention a ses CRÉNEAUX (le compteur d'eau, l'appareil posé,
         *  le bordereau…), et le dialogue de `tache_photos_cloture` les guide
         *  un par un, avec la position GPS, le compte rendu, le code
         *  superviseur et l'état de la commande et du BL.
         *
         *  Refaire ce dialogue en plus petit, c'était garantir qu'un jour
         *  « Ma journée » accepterait une clôture que la fiche refuse.
         */
        /** Le total de la commande et ce qui a DÉJÀ été encaissé, sous la ligne
         *  de clôture (demande 02/09/2026).
         *
         *  C'est la question qui vient juste après « c'est fini » : reste-t-il
         *  quelque chose à encaisser chez ce client ? La poser à l'écran évite
         *  de repartir sans avoir demandé, et de devoir revenir.
         *
         *  Les chiffres viennent de `cloture_tache.exigences` — la même lecture
         *  que le dialogue de la fiche, paiements alloués à la commande OU à
         *  ses factures.
         */
        _reglement(l, esc) {
            const r = l.reglement;
            if (!r) return "";
            const detail = (r.paiements || []).map((p) =>
                `<div class="mj-conf">${esc(p.date)} · ${esc(p.mode || "—")} · <b>${
                    format_currency(p.montant, "TND")}</b></div>`).join("");
            // ⚠️ « PAYÉ » N'EST PAS « ENCAISSÉ ». Une commande peut porter un
            // paiement de mode « Dette non payée » : la pièce existe, l'argent
            // non. Annoncer « soldée » en vert sur 651 DT que personne n'a
            // touchés envoyait le technicien repartir les mains vides.
            // La dette ARAMEX est l'exception : le transporteur encaisse à la
            // remise, il n'y a rien à réclamer sur place.
            let verdict;
            if (r.reste > 0.005) {
                verdict = `<span class="mj-badge ko">${__("reste")} ${
                    format_currency(r.reste, "TND")}</span>`;
            } else if (r.dette > 0.005) {
                verdict = `<span class="mj-badge att">⚠️ ${__("dette non payée")} — ${
                    __("à encaisser")} ${format_currency(r.dette, "TND")}</span>`;
            } else if (r.dette_aramex > 0.005) {
                verdict = `<span class="mj-badge inf">📦 ${
                    __("encaissé par Aramex à la remise")}</span>`;
            } else {
                verdict = `<span class="mj-badge ok">${__("soldée")}</span>`;
            }
            return `<div class="mj-l"><span class="k">${__("Règlement")}</span><span>
                ${__("Total")} <b>${format_currency(r.total, "TND")}</b>
                ${verdict}${detail}</span></div>`;
        }

        /** Les photos prises pendant l'intervention, une fois celle-ci terminée.
         *
         *  C'est la seule preuve visible de ce qui a été fait : les montrer sur
         *  la carte évite d'ouvrir la fiche pour vérifier qu'on a bien
         *  photographié — et de découvrir le lendemain qu'on ne l'avait pas
         *  fait. Un clic agrandit.
         */
        _photos(l, esc) {
            if (l.statut !== "Completed" || !(l.photos || []).length) return "";
            return `<div class="mj-l"><span class="k">${__("Photos")}</span><span>
                <div class="mj-photos">${(l.photos || []).map((u) =>
                  `<a href="${esc(u)}" target="_blank" rel="noopener">
                     <img src="${esc(u)}" alt="" loading="lazy"></a>`).join("")}</div>
              </span></div>`;
        }

        _actions(l, esc) {
            return `<div class="mj-actions">
              <a class="btn btn-sm btn-default" target="_blank"
                 href="/app/tache-de-travail/${encodeURIComponent(l.tache)}"
                >${__("Ouvrir la fiche")}</a>
              <button class="btn btn-sm btn-primary" data-terminer="${esc(l.tache)}"
                >✅ ${__("Clôturer")}</button>
            </div>`;
        }

        // ------------------------------------------------------------ actions

        /** La caisse journalière de l'employé affiché, en fenêtre.
         *
         *  Le technicien finit sa tournée et veut voir ce qu'il a encaissé :
         *  l'envoyer sur une autre page lui ferait perdre sa journée. Le
         *  rapport gère déjà ses propres droits — il s'ouvre sur la caisse de
         *  l'utilisateur CONNECTÉ, et seuls ceux qui supervisent peuvent en
         *  choisir une autre. On ne lui ajoute donc aucun pouvoir.
         *
         *  On présélectionne quand même l'employé regardé, pour le superviseur
         *  qui consulte la journée d'un autre : sans cela il ouvrirait la
         *  caisse de tout le monde et devrait re-choisir. La liste du filtre
         *  arrive après un aller-retour serveur, d'où la petite attente — et si
         *  elle n'arrive pas, la page reste simplement sur son défaut.
         */
        _caisse() {
            const nom = (this.data || {}).employe_nom || "";
            frappe.require("/assets/customization_app/js/ouvrir_document.js", () => {
                customization_app.ouvrir_document("Page", "caisse-journaliere", {
                    titre: __("Caisse journalière — {0}", [nom || __("moi")]),
                    url: "/app/caisse-journaliere",
                    au_chargement: (fenetre) => this._preselectionner_caisse(fenetre, nom),
                });
            });
        }

        _preselectionner_caisse(fenetre, nom) {
            if (!nom) return;
            let restant = 20;
            const essayer = () => {
                try {
                    const sel = fenetre.document.getElementById("rcj-employe");
                    if (sel && Array.from(sel.options).some((o) => o.value === nom)) {
                        sel.value = nom;
                        sel.dispatchEvent(new fenetre.Event("change", { bubbles: true }));
                        return;
                    }
                } catch (e) { return; }        // page pas encore prête, ou refusée
                if (--restant > 0) setTimeout(essayer, 300);
            };
            setTimeout(essayer, 500);
        }

        /** La fiche de la tâche, en fenêtre par-dessus la journée.
         *
         *  On réutilise `customization_app.ouvrir_document` — déjà partagé par
         *  les autres tableaux de bord, et déjà corrigé deux fois (la barre
         *  ENREGISTRER, le bandeau Raven). Une copie ici serait une occasion de
         *  n'en corriger qu'une la prochaine fois.
         */
        _fiche(tache) {
            frappe.require("/assets/customization_app/js/ouvrir_document.js", () => {
                customization_app.ouvrir_document("Tache de travail", tache, {
                    titre: __("Intervention {0}", [tache]),
                    a_la_fermeture: () => this.charger(),
                });
            });
        }

        _ligne(tache) {
            return (this.data.lignes || []).find((x) => x.tache === tache) || {};
        }

        /** Le clic APPELLE, et rien d'autre.
         *
         *  Le lien `tel:` part vers le composeur du téléphone ; on ne
         *  l'intercepte pas. La trace est posée en arrière-plan — demander
         *  « alors, ça a répondu ? » au retour d'appel s'interposait entre le
         *  technicien et son geste (constaté à l'usage 02/09/2026).
         *
         *  Le compteur devant le numéro compte donc les APPELS PASSÉS, pas les
         *  résultats : c'est déjà ce qui distingue « pas encore essayé » de
         *  « essayé trois fois ».
         */
        _appeler(el) {
            const tache = $(el).data("tache");
            const numero = String($(el).data("appel"));
            const ligne = this._ligne(tache);
            (ligne.appels = ligne.appels || []).unshift(
                { numero, quand: frappe.datetime.now_datetime().slice(0, 16),
                  texte: __("Appel au {0}", [numero]) });

            // ⚠️ ON NE REDESSINE PAS LA LISTE ICI. Redessiner pendant le clic
            // remplace le <a> qu'on vient de toucher ; un lien retiré du DOM au
            // milieu de son propre événement empêche le navigateur de suivre le
            // `tel:` — le composeur ne s'ouvrirait pas. On incrémente donc la
            // pastille SUR PLACE, et l'action par défaut suit son cours intacte.
            const n = (ligne.appels || []).filter((a) => a.numero === numero).length;
            const $lien = $(el);
            let $p = $lien.find(".mj-badge").first();
            if (!$p.length) {
                $p = $(`<span class="mj-badge gris"></span> `).prependTo($lien);
            }
            $p.text(n + "×").attr("class", "mj-badge " + (n >= 2 ? "att" : "gris"));

            frappe.call({
                method: "customization_app.planning_employe.tracer_appel",
                args: { tache, numero },
            });
        }

        /** La photo du bordereau, prise DEPUIS le bloc Aramex.
         *
         *  Le numéro ne peut être enregistré qu'une fois la photo prise — c'est
         *  la règle voulue. Mais obliger à passer par le guide de clôture pour
         *  la prendre, puis revenir ici, coupait le geste en deux : le
         *  technicien tient le colis, il photographie et il saisit, dans le
         *  même mouvement.
         *
         *  Elle va dans « photos après », le créneau que la clôture réserve au
         *  bordereau — la même photo sert donc aux deux, et la clôture avance.
         *  Et si un numéro est déjà tapé, la vérification s'enchaîne toute
         *  seule : c'est la suite évidente.
         */
        _photo_bordereau(tache) {
            new frappe.ui.FileUploader({
                doctype: "Tache de travail", docname: tache, folder: "Home/Attachments",
                allow_multiple: false,
                restrictions: { allowed_file_types: ["image/*"] },
                on_success: (file) => frappe.call({
                    method: "customization_app.cloture_tache.enregistrer_photo",
                    args: { tache, champ: "liste_photos_apres", file_url: file.file_url },
                    callback: () => {
                        const $champ = this.dialog.$wrapper.find(`[data-champ="${tache}"]`);
                        const numero = ($champ.val() || "").trim();
                        frappe.show_alert({ message: __("Photo du bordereau enregistrée"),
                                            indicator: "green" }, 3);
                        if (numero) {
                            this._verifier(tache, numero);
                        } else {
                            this.charger();
                        }
                    },
                }),
            });
        }

        _aramex(el) {
            const tache = $(el).data("aramex");
            this._verifier(tache, this.dialog.$wrapper.find(`[data-champ="${tache}"]`).val());
        }

        _verifier(tache, numero) {
            frappe.call({
                method: "customization_app.planning_employe.verifier_bordereau",
                args: { tache, numero },
                freeze: true,
                freeze_message: __("Lecture de la photo du bordereau…"),
                callback: (r) => {
                    const m = r.message || {};
                    if (m.avertissement) {
                        frappe.msgprint({ title: __("Bordereau enregistré"),
                                          indicator: "orange", message: m.avertissement });
                    } else {
                        frappe.show_alert({ indicator: "green",
                            message: __("Bordereau {0} vérifié sur la photo", [m.bordereau]) }, 5);
                    }
                    this.charger();
                },
            });
        }

        _terminer(el) {
            const tache = $(el).data("terminer");
            if (typeof tache_dialogue_cloture !== "function") {
                frappe.msgprint(__("Le guide de clôture n est pas chargé — rechargez la page."));
                return;
            }
            frappe.call({
                method: "customization_app.cloture_tache.exigences",
                args: { tache },
                callback: (r) => tache_dialogue_cloture(this._facade(tache), r.message || {}),
            });
        }

        /** Ce que le dialogue de la fiche attend d un formulaire — rien de plus.
         *
         *  Il n utilise que `docname`, `doctype`, `doc.name`, `reload_doc`, et
         *  le couple `set_value("status","Completed")` + `save()`. La façade
         *  redirige ce couple vers `planning_employe.cloturer`, qui fait le
         *  MÊME enregistrement : les contrôles serveur s appliquent donc à
         *  l identique, sans que la fenêtre ait à charger le formulaire. */
        _facade(tache) {
            const moi = this;
            const attente = {};
            return {
                doctype: "Tache de travail",
                docname: tache,
                doc: { name: tache },
                reload_doc: () => moi.charger(),
                set_value: (champ, valeur) => { attente[champ] = valeur; },
                save: () => frappe.call({
                    method: "customization_app.planning_employe.cloturer",
                    args: { tache, rapport_visite: attente.rapport_visite || null },
                    freeze: true,
                    callback: () => {
                        frappe.show_alert({ message: __("Intervention terminée"),
                                            indicator: "green" }, 4);
                        moi.charger();
                    },
                }),
            };
        }
    }

    // Ouvrable depuis n'importe où (bouton de liste, calendrier, fiche).
    window.maJournee_ouvrir = () => new Journee();

    /* ── Le bouton, sur la liste ET le calendrier des tâches ─────────────────
     * ⚠️ PAR LE PROTOTYPE, PAS PAR `frappe.listview_settings` : d'autres
     * fichiers réassignent cet objet en entier et sont concaténés après le
     * nôtre — la déclaration serait silencieusement écrasée. Même raison que
     * `sales_order_rdv.js`. */
    const _after_render = frappe.views.ListView.prototype.after_render;
    frappe.views.ListView.prototype.after_render = function () {
        _after_render.apply(this, arguments);
        if (this.doctype !== "Tache de travail" || this._mj_bouton) return;
        try {
            this._mj_bouton = true;
            this.page.add_inner_button(__(LIBELLE), window.maJournee_ouvrir);
        } catch (e) {
            this._mj_bouton = false;
            console.error("Bouton Ma journée :", e);
        }
    };

    const _cal = frappe.views.CalendarView && frappe.views.CalendarView.prototype;
    if (_cal && _cal.render) {
        const _render = _cal.render;
        _cal.render = function () {
            const out = _render.apply(this, arguments);
            try {
                if (this.doctype === "Tache de travail" && !this._mj_bouton) {
                    this._mj_bouton = true;
                    this.page.add_inner_button(__(LIBELLE), window.maJournee_ouvrir);
                }
            } catch (e) { this._mj_bouton = false; }
            return out;
        };
    }
})();
