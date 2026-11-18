/**
 * La commande simplifiée du technicien en écran simplifié (demande du 18/11/2026) — ouverte par
 * « ➕ Créer une nouvelle commande » de la clôture d'une intervention, à la place du formulaire complet.
 *
 * Le client et la date sont déjà posés. Trois gestes : 1) ses articles — SON stock d'abord, avec image, prix et
 * quantité ; « hors de mon stock » est un choix explicite, la ligne est alors marquée ; 2) le total exact, calculé
 * par le serveur comme le ferait le formulaire ; 3) le règlement (termes de paiement), avec la majoration carte.
 * Tout se décide côté serveur (customization_app.commande_technicien) ; l'écran ne fait que saisir et montrer.
 */
(function () {
    const API = "customization_app.commande_technicien.";
    const esc = (t) => frappe.utils.escape_html(String(t == null ? "" : t));
    const n3 = (v) => Number(v || 0).toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
    const r3 = (v) => Math.round((Number(v) || 0) * 1000) / 1000;
    const COURT = { "Espèces": "💵 Espèces", "Chèque": "🧾 Chèque", "Carte de crédit": "💳 Carte", "Virement": "🏦 Virement",
                    "Traite bancaire LC": "📄 Traite", "Dette non payée": "⏳ À payer plus tard" };

    function poser_css() {
        if (document.getElementById("ct-css")) return;
        const s = document.createElement("style");
        s.id = "ct-css";
        s.textContent = `
        .ct-tete { background:var(--control-bg,#f4f5f7); border-radius:12px; padding:10px 12px; margin-bottom:10px; font-size:14px; }
        .ct-tete b { font-size:15px; }
        .ct-sec { border:1px solid var(--border-color,#e4e8ee); border-radius:12px; padding:10px 12px; margin-bottom:10px; background:var(--card-bg,#fff); }
        .ct-sec h5 { margin:0 0 8px; font-size:12.5px; text-transform:uppercase; letter-spacing:.3px; color:#6b7280; }
        .ct-chips { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px; }
        .ct-chip { border:1px solid var(--border-color,#d1d8dd); background:#fff; border-radius:999px; padding:7px 13px; font-size:13.5px; cursor:pointer; min-height:38px; }
        .ct-chip.on { background:#171717; border-color:#171717; color:#fff; font-weight:600; }
        .ct-chip.hors.on { background:#b45309; border-color:#b45309; }
        .ct-rech { width:100%; height:46px; font-size:16px; border-radius:10px; border:1px solid var(--border-color,#d1d8dd); padding:0 12px; }
        .ct-alerte { background:#fff7ed; color:#9a3412; border-radius:8px; padding:7px 10px; font-size:12.5px; margin:8px 0 0; }
        .ct-res { display:flex; align-items:center; gap:10px; padding:8px 2px; border-bottom:1px solid #eef1f5; cursor:pointer; }
        .ct-res:active { background:#f0f9ff; }
        .ct-img { width:54px; height:54px; object-fit:contain; border-radius:8px; background:#f2f4f7; border:1px solid #e4e8ee; flex:none; display:flex; align-items:center; justify-content:center; font-size:22px; }
        .ct-nom { font-weight:600; font-size:13.5px; line-height:1.25; }
        .ct-sub { color:#6b7280; font-size:12px; }
        .ct-prix { margin-left:auto; text-align:right; white-space:nowrap; font-weight:700; }
        .ct-ligne { display:flex; align-items:center; gap:8px; padding:9px 0; border-bottom:1px solid #eef1f5; flex-wrap:wrap; }
        .ct-ligne .txt { flex:1 1 140px; min-width:0; }
        .ct-qte { display:flex; align-items:center; gap:4px; }
        .ct-qte button { width:40px; height:40px; font-size:18px; border-radius:8px; border:1px solid var(--border-color,#d1d8dd); background:#fff; }
        .ct-qte input { width:56px; height:40px; text-align:center; font-size:15px; border-radius:8px; border:1px solid var(--border-color,#d1d8dd); }
        .ct-x { width:36px; height:36px; border:none; background:none; color:#b02a37; font-size:18px; }
        .ct-badge { display:inline-block; font-size:11px; font-weight:700; padding:1px 8px; border-radius:999px; background:#ffedd5; color:#9a3412; }
        .ct-badge.mo { background:#e0e7ff; color:#3730a3; }
        .ct-remise { display:grid; grid-template-columns:1fr 130px; gap:6px; align-items:center; padding:9px 0 4px; }
        .ct-remise label { margin:0; font-weight:600; font-size:13.5px; }
        .ct-remise input, .ct-remise select { height:42px; font-size:15px; border-radius:8px; border:1px solid var(--border-color,#d1d8dd); padding:0 10px; width:100%; }
        .ct-remise .plein { grid-column:1 / -1; }
        .ct-sous { display:flex; justify-content:space-between; font-size:13.5px; color:#4b5563; padding-top:4px; }
        .ct-total { display:flex; justify-content:space-between; align-items:baseline; font-size:17px; font-weight:800; padding-top:8px; }
        .ct-pay { border-top:1px solid #eef1f5; padding:9px 0; display:grid; grid-template-columns:1fr 1fr; gap:6px; align-items:center; }
        .ct-pay .mode { font-weight:700; } .ct-pay .plein { grid-column:1 / -1; }
        .ct-pay input, .ct-pay select { height:44px; font-size:15px; border-radius:8px; border:1px solid var(--border-color,#d1d8dd); padding:0 10px; width:100%; }
        .ct-reste { font-weight:700; margin-top:8px; } .ct-reste.ko { color:#b02a37; } .ct-reste.ok { color:#166534; }
        .ct-muted { color:#6b7280; font-size:12px; }
        .ct-vide { color:#6b7280; text-align:center; padding:14px 0; font-size:13px; }`;
        document.head.appendChild(s);
    }

    class Commande {
        constructor(tache, opts) {
            poser_css();
            this.tache = tache; this.opts = opts || {};
            this.panier = [];        // {item_code, item_name, image, qty, prix, hors_stock, service, stock}
            this.paiements = [];     // {mode, montant, numero, banque}
            this.resume = null;      // le calcul du serveur pour le panier courant
            this.remise = { montant: 0, motif: "", commentaire: "" };
            this.hors = false; this.tour = 0; this.calcul = 0;
            this.d = new frappe.ui.Dialog({
                title: __("Nouvelle commande"), size: "large",
                fields: [{ fieldtype: "HTML", fieldname: "corps" }],
                primary_action_label: __("✅ Valider la commande"),
                primary_action: () => this.valider(),
            });
            this.$c = this.d.fields_dict.corps.$wrapper;
            this.$c.html(`<div class="ct-vide">${__("Chargement…")}</div>`);
            this.d.show();
            frappe.call({ method: API + "preparer", args: { tache } }).then((r) => {
                this.ctx = r.message || {};
                if (this.ctx.main_oeuvre) this.ajouter(Object.assign({}, this.ctx.main_oeuvre), true);
                this.squelette(); this.chercher(); this.calculer();
            });
        }

        squelette() {
            const c = this.ctx;
            this.$c.html(`
              <div class="ct-tete">👤 <b>${esc(c.nom_client)}</b><br>
                <span class="ct-muted">📅 ${esc(frappe.datetime.str_to_user(c.date))} · ${esc(c.type || "")} · ${__("prix")} ${esc(c.liste_prix)}</span></div>
              <div class="ct-sec"><h5>🧺 ${__("Articles de la commande")}</h5><div class="ct-panier"></div>
                <div class="ct-totaux"></div></div>
              <div class="ct-sec"><h5>➕ ${__("Ajouter un article")}</h5>
                <div class="ct-chips">
                  <span class="ct-chip on" data-hors="0">📦 ${__("Mon stock")}</span>
                  <span class="ct-chip hors" data-hors="1">🏬 ${__("Hors de mon stock")}</span>
                </div>
                <input type="search" class="ct-rech" placeholder="${esc(__("Chercher par nom ou par code…"))}" autocomplete="off">
                <div class="ct-alerte ct-alerte-hors" style="display:none">⚠️ ${__("Article hors de votre stock : il sortira du magasin et sera signalé sur la commande.")}</div>
                <div class="ct-resultats"></div></div>
              <div class="ct-sec"><h5>💰 ${__("Règlement")}</h5><div class="ct-reglement"></div></div>
              <div style="text-align:center;margin:4px 0 2px"><a href="#" class="ct-complet ct-muted">${__("Client avec ristourne ou avoir ? Ouvrir le formulaire complet")}</a></div>`);
            const $c = this.$c;
            $c.on("click", ".ct-chip[data-hors]", (e) => {
                this.hors = $(e.currentTarget).attr("data-hors") === "1";
                $c.find(".ct-chip[data-hors]").each((_, x) => $(x).toggleClass("on", ($(x).attr("data-hors") === "1") === this.hors));
                $c.find(".ct-alerte-hors").toggle(this.hors);
                this.chercher();
            });
            $c.on("input", ".ct-rech", frappe.utils.debounce(() => this.chercher(), 300));
            $c.on("click", ".ct-res", (e) => this.ajouter(this.resultats[Number($(e.currentTarget).attr("data-i"))]));
            $c.on("click", ".ct-plus-res", () => this.chercher(true));
            $c.on("click", "[data-q]", (e) => this.quantite(Number($(e.currentTarget).closest("[data-l]").attr("data-l")), Number($(e.currentTarget).attr("data-q"))));
            $c.on("change", ".ct-qte input", (e) => this.quantite(Number($(e.currentTarget).closest("[data-l]").attr("data-l")), null, Number($(e.currentTarget).val())));
            $c.on("click", ".ct-x[data-retirer]", (e) => { this.panier.splice(Number($(e.currentTarget).attr("data-retirer")), 1); this.change(); });
            $c.on("click", ".ct-chip[data-mode]", (e) => this.ajouter_paiement($(e.currentTarget).attr("data-mode")));
            $c.on("click", ".ct-x[data-oter]", (e) => { this.paiements.splice(Number($(e.currentTarget).attr("data-oter")), 1); this.reglement_change(); });
            $c.on("change", "[data-p]", (e) => {
                const p = this.paiements[Number($(e.currentTarget).closest("[data-pi]").attr("data-pi"))];
                const cle = $(e.currentTarget).attr("data-p");
                if (cle === "montant") {
                    // Le montant tapé est la PART de la commande réglée par ce moyen ; il est « saisi » : les autres
                    // lignes, elles, s'ajustent toutes seules.
                    p.montant = Math.max(0, r3(String($(e.currentTarget).val()).replace(",", ".")));
                    p.saisi = true;
                    this.reglement_change();
                } else {
                    p[cle] = $(e.currentTarget).val();
                }
            });
            // La carte affiche part + majoration ; quand il y entre, il retrouve la part qu'il a tapée (comme la commande).
            $c.on("focus", "[data-p='montant'][data-part]", (e) => { $(e.currentTarget).val($(e.currentTarget).attr("data-part")); });
            $c.on("change", "[data-r]", (e) => {
                const cle = $(e.currentTarget).attr("data-r");
                this.remise[cle] = cle === "montant" ? Math.max(0, r3(String($(e.currentTarget).val()).replace(",", "."))) : $(e.currentTarget).val();
                this.change();
            });
            $c.on("click", ".ct-complet", (e) => { e.preventDefault(); this.d.hide(); if (this.opts.formulaire) this.opts.formulaire(); });
            this.peindre_panier(); this.peindre_reglement();
        }

        // ── articles
        chercher(suite) {
            const tour = ++this.tour;
            const texte = String(this.$c.find(".ct-rech").val() || "").trim();
            const mini = this.ctx.recherche_min || 2;
            if (texte.length < mini) {
                // Rien tant qu'il n'a pas tapé 2 lettres : sur le téléphone, une liste entière ne sert à rien.
                this.resultats = [];
                this.$c.find(".ct-resultats").html(`<div class="ct-vide">${__("Tapez au moins {0} lettres du nom ou du code de l’article.", [mini])}</div>`);
                return;
            }
            const debut = suite ? (this.resultats || []).length : 0;
            frappe.call({ method: API + "articles", args: { tache: this.tache, txt: this.$c.find(".ct-rech").val() || "", hors_stock: this.hors ? 1 : 0, debut } }).then((r) => {
                if (tour !== this.tour) return;
                const m = r.message || {};
                this.resultats = (suite ? this.resultats : []).concat(m.articles || []);
                const $r = this.$c.find(".ct-resultats");
                if (!this.resultats.length) {
                    $r.html(`<div class="ct-vide">${this.hors ? __("Aucun article ne correspond.")
                        : __("Rien dans votre stock pour cette recherche. Essayez « Hors de mon stock ».")}</div>`);
                    return;
                }
                $r.html(this.resultats.map((a, i) => `<div class="ct-res" data-i="${i}">
                    ${a.image ? `<img class="ct-img" loading="lazy" src="${esc(a.image)}" alt="">` : `<span class="ct-img">${a.service ? "🛠" : "📦"}</span>`}
                    <div style="min-width:0"><div class="ct-nom">${esc(a.item_name)}</div>
                      <div class="ct-sub">${esc(a.item_code)}${a.service ? " · " + __("main d’œuvre")
                        : " · " + (a.hors_stock ? __("au magasin") : __("mon stock")) + " : " + n3(a.stock).replace(/,000$/, "")}</div></div>
                    <div class="ct-prix">${a.prix == null ? `<span class="ct-muted">${__("sans prix")}</span>` : n3(a.prix)}</div></div>`).join("")
                    + (m.suite ? `<button type="button" class="btn btn-default btn-block ct-plus-res" style="margin-top:8px;min-height:44px">${__("Afficher plus")}</button>` : ""));
            });
        }

        ajouter(a, silencieux) {
            if (!a) return;
            const deja = this.panier.find((l) => l.item_code === a.item_code && !!l.hors_stock === !!a.hors_stock);
            if (deja) deja.qty += 1;
            else this.panier.push({ item_code: a.item_code, item_name: a.item_name, image: a.image, qty: 1, prix: a.prix,
                                    hors_stock: !!a.hors_stock && !a.service, service: !!a.service, stock: a.stock });
            if (!silencieux) { frappe.show_alert({ message: __("{0} ajouté", [a.item_name]), indicator: "green" }, 2); this.change(); }
        }

        quantite(i, delta, valeur) {
            const l = this.panier[i];
            if (!l) return;
            l.qty = Math.max(0, valeur != null ? valeur : l.qty + delta);
            if (!l.qty) this.panier.splice(i, 1);
            this.change();
        }

        // Le panier (ou la remise) a changé : le total affiché est périmé jusqu'à la réponse du serveur.
        change() { this.perime = true; this.peindre_panier(); this.peindre_reglement(); this.calculer(); }

        peindre_panier() {
            const exact = {};
            ((this.resume || {}).lignes || []).forEach((x) => { exact[x.item_code + "|" + (x.hors_stock ? 1 : 0)] = x; });
            this.$c.find(".ct-panier").html(this.panier.length ? this.panier.map((l, i) => {
                const x = exact[l.item_code + "|" + (l.hors_stock ? 1 : 0)];
                const montant = x ? x.montant : (l.prix || 0) * l.qty;
                return `<div class="ct-ligne" data-l="${i}">
                  ${l.image ? `<img class="ct-img" src="${esc(l.image)}" alt="">` : `<span class="ct-img">${l.service ? "🛠" : "📦"}</span>`}
                  <div class="txt"><div class="ct-nom">${esc(l.item_name)}</div>
                    <div class="ct-sub">${esc(l.item_code)} · ${n3(x ? x.prix : l.prix)}
                      ${l.hors_stock ? `<span class="ct-badge">${__("hors de mon stock")}</span>` : ""}${l.service ? `<span class="ct-badge mo">${__("main d’œuvre")}</span>` : ""}</div></div>
                  <div class="ct-qte"><button type="button" data-q="-1">−</button><input type="number" inputmode="decimal" value="${l.qty}"><button type="button" data-q="1">+</button></div>
                  <div class="ct-prix">${n3(montant)}</div>
                  <button type="button" class="ct-x" data-retirer="${i}" title="${esc(__("Retirer"))}">✕</button></div>`;
            }).join("") : `<div class="ct-vide">${__("Aucun article : ajoutez-en ci-dessous.")}</div>`);
            const r = this.perime ? null : this.resume, c = this.ctx, rm = this.remise;
            const maj = this.majoration_affichee();
            const case_remise = `<div class="ct-remise">
                <label>🏷️ ${__("Remise")} <span class="ct-muted">(DT)</span></label>
                <input type="number" inputmode="decimal" step="0.001" min="0" data-r="montant" value="${rm.montant || ""}" placeholder="0">
                ${rm.montant > 0 ? `<select class="plein" data-r="motif"><option value="">${esc(__("Pourquoi cette remise ? *"))}</option>${
                    (c.motifs_remise || []).map((m) => `<option ${m === rm.motif ? "selected" : ""}>${esc(m)}</option>`).join("")}</select>
                  <input class="plein" data-r="commentaire" placeholder="${esc(__("Précisez ({0} caractères minimum) *", [c.commentaire_min || 10]))}" value="${esc(rm.commentaire || "")}">
                  ${this.remise_complete() ? "" : `<div class="plein ct-alerte" style="margin:0">${__("La remise s’appliquera une fois le motif choisi et précisé.")}</div>`}` : ""}
              </div>`;
            this.$c.find(".ct-totaux").html(!this.panier.length ? "" : case_remise + (r
                ? `${r.remise > 0 ? `<div class="ct-sous"><span>${__("Remise")}</span><span>− ${n3(r.remise)}</span></div>` : ""}
                   ${maj > 0 ? `<div class="ct-sous"><span>${__("Majoration carte ({0} %)", [String((c.carte || {}).taux).replace(".", ",")])}</span><span>+ ${n3(maj)}</span></div>` : ""}
                   <div class="ct-total"><span>${__("Total à payer")}</span><span>${n3(r.base + maj)} DT</span></div>
                   ${(r.notes || []).map((t) => `<div class="ct-alerte">${esc(t)}</div>`).join("")}`
                : `<div class="ct-total ct-muted"><span>${__("Total")}</span><span>${this.erreur ? "—" : __("calcul…")}</span></div>
                   ${this.erreur ? `<div class="ct-alerte">${this.erreur}</div>` : ""}`));
        }

        // ── le total exact, par le serveur
        calculer() {
            clearTimeout(this.minuterie);
            this.erreur = "";
            if (!this.panier.length) return;
            this.minuterie = setTimeout(() => {
                const n = ++this.calcul;
                const envoyes = this.cle_reglement();
                frappe.call({ method: API + "apercu",
                    args: { tache: this.tache, articles: JSON.stringify(this.envoi()), remise: JSON.stringify(this.remise_envoyee()),
                            paiements: JSON.stringify(this.paiements.map((p) => ({ mode: p.mode, montant: p.montant }))) },
                    error: (xhr) => {
                        if (n !== this.calcul) return;
                        let t = __("Calcul impossible.");
                        try { t = JSON.parse(JSON.parse(xhr.responseText)._server_messages)[0]; t = JSON.parse(t).message || t; } catch (e) { /* message brut */ }
                        this.erreur = t; this.resume = null; this.perime = false; this.peindre_panier(); this.peindre_reglement();
                        $(".modal.show .msgprint").closest(".modal").modal("hide");       // dit dans l'écran, pas en fenêtre
                    } }).then((r) => {
                    if (n !== this.calcul || !r || !r.message) return;
                    const base_avant = (this.resume || {}).base;
                    this.resume = r.message;
                    this.perime = false;
                    this.resume.pour = envoyes;           // le règlement pour lequel la majoration a été calculée
                    if (base_avant !== this.resume.base) this.equilibrer();
                    this.peindre_panier(); this.peindre_reglement();
                    // le total a changé le règlement (une seule ligne, ou des lignes libres) : la majoration est à refaire
                    if (this.a_une_carte() && this.cle_reglement() !== envoyes) this.calculer();
                });
            }, 500);
        }

        // Une remise ne compte que justifiée (motif + précision) : tant qu'elle ne l'est pas, le total se calcule sans.
        remise_complete() {
            const rm = this.remise, mini = this.ctx.commentaire_min || 10;
            return rm.montant > 0 && !!rm.motif && String(rm.commentaire || "").trim().length >= mini;
        }
        remise_envoyee() { return this.remise_complete() ? this.remise : {}; }

        envoi() { return this.panier.map((l) => ({ item_code: l.item_code, qty: l.qty, hors_stock: l.hors_stock ? 1 : 0 })); }

        // ── règlement
        cle_reglement() { return JSON.stringify(this.paiements.map((p) => [p.mode, r3(p.montant)])); }
        a_une_carte() { return this.paiements.some((p) => p.mode === this.ctx.mode_carte && p.montant > 0); }
        part_carte() { return r3(this.paiements.filter((p) => p.mode === this.ctx.mode_carte).reduce((s, p) => s + (p.montant || 0), 0)); }

        // La majoration telle que la commande la portera : celle du serveur si elle a été calculée pour CE règlement,
        // sinon la même formule ici (taux et arrondi du réglage), le temps que le serveur réponde.
        majoration_estimee(part) {
            const c = this.ctx.carte || {};
            if (!(c.taux > 0) || !(part > 0)) return 0;
            const brut = part * c.taux / 100, pas = c.pas > 0 ? c.pas : 0.001;
            return r3((c.superieur ? Math.ceil(brut / pas - 1e-9) : Math.round(brut / pas)) * pas);
        }
        majoration_affichee() {
            const r = this.resume;
            if (!r || !this.a_une_carte()) return 0;
            return r.pour === this.cle_reglement() && r.majoration > 0 ? r.majoration : this.majoration_estimee(this.part_carte());
        }

        /** Le calcul automatique : ce qu'il a TAPÉ ne bouge pas ; le reste de la commande va sur les lignes qu'il n'a
         *  pas touchées (la première prend tout). Une seule ligne suit toujours le total. */
        equilibrer() {
            const base = (this.resume || {}).base;
            if (base == null || !this.paiements.length) return;
            if (this.paiements.length === 1) { this.paiements[0].montant = r3(base); this.paiements[0].saisi = false; return; }
            const libres = this.paiements.filter((p) => !p.saisi);
            if (!libres.length) return;
            const fixe = this.paiements.filter((p) => p.saisi).reduce((s, p) => s + (p.montant || 0), 0);
            libres.forEach((p, i) => { p.montant = i === 0 ? Math.max(0, r3(base - fixe)) : 0; });
        }

        reglement_change() {
            this.equilibrer();
            this.peindre_panier(); this.peindre_reglement();
            // La majoration carte s'applique à la COMMANDE (prix relevés) : le serveur la recalcule pour ce règlement.
            if (this.a_une_carte() || ((this.resume || {}).majoration > 0)) this.calculer();
        }

        ajouter_paiement(mode) {
            // La nouvelle ligne est « libre » : elle prend ce qui reste, ou attend qu'il tape son montant.
            this.paiements.push({ mode, montant: 0, numero: "", banque: "", saisi: false });
            const base = (this.resume || {}).base || 0;
            const pris = this.paiements.filter((p) => p.saisi).reduce((s, p) => s + (p.montant || 0), 0);
            if (this.paiements.length > 1 && this.paiements.filter((p) => !p.saisi).length > 1) {
                // deux lignes libres : la plus ancienne garde le reste, la nouvelle part de zéro jusqu'à ce qu'il tape
                this.paiements[this.paiements.length - 1].montant = 0;
            } else if (this.paiements.length > 1) {
                this.paiements[this.paiements.length - 1].montant = Math.max(0, r3(base - pris));
            }
            this.reglement_change();
        }

        peindre_reglement() {
            const $z = this.$c.find(".ct-reglement"), r = this.resume;
            if (!this.panier.length || !r) { $z.html(`<div class="ct-vide">${__("Le règlement se saisit une fois le total calculé.")}</div>`); return; }
            const c = this.ctx, regles = c.regles || {};
            const maj = this.majoration_affichee(), carte = this.part_carte();
            const pris = r3(this.paiements.reduce((s, p) => s + (p.montant || 0), 0)), reste = r3(r.base - pris);
            $z.html(`<div class="ct-chips">${(c.modes || []).map((m) => `<span class="ct-chip" data-mode="${esc(m)}">＋ ${esc(COURT[m] || m)}</span>`).join("")}</div>
              ${this.paiements.map((p, i) => {
                const g = regles[p.mode] || {};
                const est_carte = p.mode === c.mode_carte && p.montant > 0;
                // la majoration de CETTE ligne : sa part de la majoration totale (une seule carte : tout)
                const sa_maj = est_carte ? (carte > 0 ? r3(maj * p.montant / carte) : 0) : 0;
                return `<div class="ct-pay" data-pi="${i}">
                  <div class="mode">${esc(COURT[p.mode] || p.mode)}${!p.saisi && this.paiements.length > 1 ? ` <span class="ct-muted">· ${__("auto")}</span>` : ""}</div>
                  <div style="display:flex;gap:4px;align-items:center"><input type="number" inputmode="decimal" step="0.001" data-p="montant"
                      ${est_carte ? `data-part="${p.montant}"` : ""} value="${p.montant ? r3(p.montant + sa_maj) : ""}">
                    <button type="button" class="ct-x" data-oter="${i}">✕</button></div>
                  ${g.piece ? `<input class="plein" data-p="numero" placeholder="${esc(g.piece)}${g.requise ? " *" : ""}" value="${esc(p.numero || "")}">` : ""}
                  ${g.banque ? `<select class="plein" data-p="banque"><option value="">${esc(__("Banque"))}</option>${(c.banques || []).map((b) =>
                      `<option ${b === p.banque ? "selected" : ""}>${esc(b)}</option>`).join("")}</select>` : ""}
                  ${est_carte ? `<div class="plein ct-alerte" style="margin:0">💳 ${__("Par carte : {0} de la commande + {1} de majoration ({2} %) = {3} sur le terminal",
                      [n3(p.montant), n3(sa_maj), String(c.carte.taux).replace(".", ","), "<b>" + n3(p.montant + sa_maj) + "</b>"])}</div>` : ""}
                </div>`;
              }).join("")}
              ${this.paiements.length ? `<div class="ct-reste ${Math.abs(reste) < 0.005 ? "ok" : "ko"}">${Math.abs(reste) < 0.005
                  ? "✓ " + __("Le règlement couvre la commande : {0} DT", [n3(r.base + maj)])
                  : (reste > 0 ? __("Reste à répartir : {0}", [n3(reste)]) : __("Trop saisi : {0}", [n3(-reste)]))}</div>`
                : `<div class="ct-vide">${__("Choisissez comment le client règle.")}</div>`}`);
        }

        valider() {
            if (!this.panier.length) return frappe.msgprint(__("Ajoutez au moins un article."));
            if (!this.resume || this.perime) return frappe.msgprint(this.erreur ? this.erreur : __("Le total est en cours de calcul, réessayez dans un instant."));
            if (!this.paiements.length) return frappe.msgprint(__("Choisissez comment le client règle."));
            if (this.remise.montant > 0 && !this.remise_complete()) return frappe.msgprint(__("Remise : choisissez le motif et précisez-le, ou remettez la remise à 0."));
            frappe.call({ method: API + "creer", type: "POST", freeze: true, freeze_message: __("Enregistrement de la commande…"),
                args: { tache: this.tache, articles: JSON.stringify(this.envoi()), paiements: JSON.stringify(this.paiements),
                        remise: JSON.stringify(this.remise_envoyee()) } }).then((r) => {
                const m = r.message;
                if (!m || !m.name) return;
                this.d.hide();
                frappe.msgprint({ title: __("Commande {0} validée", [m.name]), indicator: "green",
                    message: `<div style="font-size:15px;margin-bottom:6px">${__("À encaisser")} : <b>${n3(m.total)} DT</b>${
                        m.majoration ? ` <span class="text-muted">(${__("dont {0} de majoration carte", [n3(m.majoration)])})</span>` : ""}</div>
                      ${(m.echeances || []).map((e) => `<div>${esc(COURT[e.mode] || e.mode)} : <b>${n3(e.montant)}</b></div>`).join("")}` });
                if (this.opts.cree) this.opts.cree(m.name, m);
            });
        }
    }

    window.commande_technicien = (tache, opts) => new Commande(tache, opts);
})();
