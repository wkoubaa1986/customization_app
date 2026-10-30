/**
 * Clôture mobile d'un SOUS-TRAITANT (décision utilisateur 30/10/2026).
 *
 * Depuis « Ma journée », sur son téléphone : les photos exigées, sa position, les articles
 * posés (cherchés dans SON stock, prix de vente affiché), le règlement (espèces, chèque,
 * traite, dette, fractionné), le rapport. « Valider » fait tout d'un coup côté serveur
 * (customization_app.sous_traitant.cloturer_intervention) : commande depuis son stock, bon de
 * livraison, paiements sur son compte, tâche terminée.
 *
 * Si le bureau a déjà lié une commande à la tâche, on ne refait rien : les articles et le
 * règlement sont ceux de la commande, l'écran se limite aux photos et au rapport.
 */
(function () {
    const CSS = `
    .cst-sec { border:1px solid var(--border-color,#e4e8ee); border-radius:12px; padding:10px 12px; margin-bottom:10px; background:var(--card-bg,#fff); }
    .cst-sec h5 { margin:0 0 8px; font-size:13px; text-transform:uppercase; letter-spacing:.3px; color:#6b7280; display:flex; justify-content:space-between; }
    .cst-sec h5 .ok { color:#166534; } .cst-sec h5 .ko { color:#b02a37; }
    .cst-slot { display:flex; align-items:center; gap:8px; padding:7px 0; border-top:1px solid #eef1f5; }
    .cst-slot:first-of-type { border-top:none; }
    .cst-slot .btn { min-height:44px; }
    .cst-recherche { width:100%; height:44px; font-size:15px; }
    .cst-res { display:flex; align-items:center; gap:10px; padding:8px 4px; border-bottom:1px solid #eef1f5; cursor:pointer; }
    .cst-res:active { background:#f0f9ff; }
    .cst-img { width:48px; height:48px; object-fit:cover; border-radius:8px; background:#f2f4f7; border:1px solid #e4e8ee; flex:none; }
    .cst-ligne { display:grid; grid-template-columns:1fr auto auto auto; gap:6px; align-items:center; padding:8px 0; border-bottom:1px solid #eef1f5; }
    .cst-ligne .qte { display:flex; align-items:center; gap:4px; }
    .cst-ligne .qte button { width:40px; height:40px; font-size:18px; }
    .cst-ligne .qte input { width:54px; height:40px; text-align:center; font-size:15px; }
    .cst-tot { display:flex; justify-content:space-between; font-weight:700; font-size:15px; padding-top:8px; }
    .cst-pay { display:grid; grid-template-columns:1fr 1fr; gap:6px; align-items:center; padding:8px 0; border-bottom:1px solid #eef1f5; }
    .cst-pay select, .cst-pay input { height:44px; font-size:15px; }
    .cst-pay .plein { grid-column:1 / -1; }
    .cst-modes { display:flex; flex-wrap:wrap; gap:6px; margin:6px 0; }
    .cst-modes .btn { min-height:44px; flex:1 1 40%; font-weight:600; }
    .cst-reste { font-weight:700; } .cst-reste.ko { color:#b02a37; } .cst-reste.ok { color:#166534; }
    .cst-muted { color:#6b7280; font-size:12px; }
    @media (max-width: 768px) { .cst-modal .modal-dialog { margin:0; max-width:100%; height:100%; }
      .cst-modal .modal-content { min-height:100vh; border-radius:0; } }
    `;

    function poser_css() {
        if (document.getElementById("cst-css")) return;
        const s = document.createElement("style"); s.id = "cst-css"; s.textContent = CSS;
        document.head.appendChild(s);
    }

    const fmt = (n) => format_currency(n || 0, "TND", 3);
    const esc = frappe.utils.escape_html;
    const MODES = [
        { v: "Espèces", l: "💵 Espèces" }, { v: "Chèque", l: "🧾 Chèque" },
        { v: "Traite bancaire LC", l: "📜 Traite" }, { v: "Dette non payée", l: "⏳ Dette" },
    ];
    const BANQUES = ["", "ABC", "Al Baraka Bank", "Al Wifak International Bank", "Amen Bank", "ATB", "Attijari Bank",
        "Banque Zitouna", "BCT", "BFPME", "BFT", "BH", "BIAT", "BNA", "BT", "BTE", "BTK", "BTL", "BTS", "Citi Bank",
        "La Poste", "QNB-Tunis", "STB", "TSB", "UBCI", "UIB"];

    class Cloture {
        constructor(tache, apres) {
            poser_css();
            this.tache = tache;
            this.apres = apres || (() => {});
            this.ex = {};
            this.articles = [];          // {item_code, item_name, prix, stock, qty}
            this.paiements = [];         // {mode, montant, numero, banque}
            this.d = new frappe.ui.Dialog({
                title: __("Terminer l'intervention"),
                size: "large",
                fields: [{ fieldtype: "HTML", fieldname: "corps" }],
                primary_action_label: __("✅ Valider et terminer"),
                primary_action: () => this.valider(),
            });
            this.d.$wrapper.addClass("cst-modal");
            this.$c = this.d.fields_dict.corps.$wrapper;
            this._bind();
            this.d.show();
            this.charger();
        }

        charger() {
            this.$c.html(`<div class="cst-muted">${__("Chargement…")}</div>`);
            frappe.call({ method: "customization_app.cloture_tache.exigences", args: { tache: this.tache } })
                .then((r) => { this.ex = r.message || {}; this.rendre(); });
        }

        // ------------------------------------------------------------ rendu
        rendre() {
            const ex = this.ex;
            const commande_liee = !!ex.commande;
            this.$c.html(`
              ${this._photos(ex)}
              ${this._position(ex)}
              ${commande_liee ? this._commande_liee(ex) : this._articles() + this._reglement()}
              ${this._rapport(ex)}`);
            this.$c.find("#cst-recherche").on("input", frappe.utils.debounce(() => this._chercher(), 250));
            const recherche = this.$c.find("#cst-recherche");
            if (recherche.length && this.dernier_motif) { recherche.val(this.dernier_motif); this._chercher(); }
        }

        _photos(ex) {
            const slots = ex.slots || [];
            const ok = ex.dispense || !ex.concerne
                || ((ex.photos || {}).avant >= (ex.minima || {}).avant && (ex.photos || {}).apres >= (ex.minima || {}).apres);
            if (!slots.length) return "";
            return `<div class="cst-sec">
              <h5>📷 ${__("Photos")} <span class="${ok ? "ok" : "ko"}">${ok ? __("complet") : __("obligatoires")}</span></h5>
              ${slots.map((s, i) => `<div class="cst-slot">
                 <div style="flex:1">${esc(s.label || "")}<div class="cst-muted">${esc(s.champ === "avant" ? __("avant") : __("après"))} · ${(ex.photos || {})[s.champ] || 0}/${(ex.minima || {})[s.champ] || 0}</div></div>
                 <button class="btn btn-default btn-sm" data-slot="${i}">📷 ${__("Prendre")}</button>
               </div>`).join("")}
            </div>`;
        }

        _position(ex) {
            if (!ex.gmap_requis) return "";
            return `<div class="cst-sec"><h5>📍 ${__("Position")} <span class="${ex.gmap ? "ok" : "ko"}">${ex.gmap ? __("enregistrée") : __("obligatoire")}</span></h5>
              <button class="btn btn-default btn-sm" data-gps style="min-height:44px">📍 ${__("Ma position actuelle")}</button></div>`;
        }

        _commande_liee(ex) {
            const ci = ex.commande_infos || {};
            return `<div class="cst-sec"><h5>🧾 ${__("Commande")} <span class="ok">${esc(ex.commande)}</span></h5>
              <div>${__("Total")} <b>${fmt(ci.total)}</b> · ${__("réglé")} <b>${fmt(ci.total_paye)}</b></div>
              <div class="cst-muted">${__("La commande a été préparée par le bureau : articles et règlement y sont déjà. Valider termine l'intervention et sort les articles de votre stock.")}</div>
            </div>`;
        }

        _articles() {
            const total = this.total();
            return `<div class="cst-sec"><h5>📦 ${__("Articles posés")} <span class="${this.articles.length ? "ok" : "ko"}">${this.articles.length} ${__("ligne(s)")}</span></h5>
              <input id="cst-recherche" class="form-control cst-recherche" placeholder="${__("Chercher dans mon stock (nom ou code)…")}" autocomplete="off">
              <div id="cst-resultats"></div>
              <div id="cst-lignes">${this.articles.map((a, i) => `<div class="cst-ligne">
                  <div><b>${esc(a.item_name)}</b><div class="cst-muted">${esc(a.item_code)} · ${fmt(a.prix)} · ${__("stock")} ${a.stock}</div></div>
                  <div class="qte"><button class="btn btn-default" data-moins="${i}">−</button>
                    <input type="number" min="1" step="1" value="${a.qty}" data-qte="${i}">
                    <button class="btn btn-default" data-plus="${i}">+</button></div>
                  <div><b>${fmt(a.qty * a.prix)}</b></div>
                  <button class="btn btn-default btn-sm" data-retirer="${i}">✕</button>
                </div>`).join("")}</div>
              <div class="cst-tot"><span>${__("Total")}</span><span>${fmt(total)}</span></div>
            </div>`;
        }

        _reglement() {
            const total = this.total(), regle = this.regle(), reste = Math.round((total - regle) * 1000) / 1000;
            return `<div class="cst-sec"><h5>💰 ${__("Règlement")} <span class="cst-reste ${Math.abs(reste) < 0.005 && total > 0 ? "ok" : "ko"}">${__("reste")} ${fmt(reste)}</span></h5>
              <div class="cst-modes">${MODES.map((m) => `<button class="btn btn-default" data-mode="${esc(m.v)}">${m.l}</button>`).join("")}</div>
              ${this.paiements.map((p, i) => `<div class="cst-pay">
                 <div><b>${esc((MODES.find((m) => m.v === p.mode) || {}).l || p.mode)}</b></div>
                 <input type="number" step="0.001" min="0" class="form-control" value="${p.montant}" data-montant="${i}" placeholder="${__("Montant")}">
                 ${["Chèque", "Traite bancaire LC"].includes(p.mode) ? `
                 <input type="text" class="form-control" value="${esc(p.numero || "")}" data-numero="${i}" placeholder="${__("N°")}">
                 <select class="form-control" data-banque="${i}">${BANQUES.map((b) => `<option value="${esc(b)}"${b === (p.banque || "") ? " selected" : ""}>${b || __("Banque…")}</option>`).join("")}</select>` : ""}
                 <button class="btn btn-default btn-sm plein" data-retirer-pay="${i}">✕ ${__("Retirer")}</button>
               </div>`).join("")}
              <div class="cst-muted">${__("Plusieurs lignes = paiement fractionné. Un règlement dû plus tard = « Dette ».")}</div>
            </div>`;
        }

        _rapport(ex) {
            if (!ex.rapport_requis) return "";
            return `<div class="cst-sec"><h5>📝 ${__("Rapport d'intervention")} <span class="${ex.rapport ? "ok" : "ko"}">${ex.rapport ? __("renseigné") : __("obligatoire")}</span></h5>
              <textarea id="cst-rapport" class="form-control" rows="3" style="font-size:15px">${esc(ex.rapport_texte || this.rapport || "")}</textarea></div>`;
        }

        total() { return Math.round(this.articles.reduce((s, a) => s + a.qty * a.prix, 0) * 1000) / 1000; }
        regle() { return Math.round(this.paiements.reduce((s, p) => s + (parseFloat(p.montant) || 0), 0) * 1000) / 1000; }

        // ------------------------------------------------------------ actions
        _bind() {
            const w = this.$c;
            w.on("click", "[data-slot]", (e) => this._photo(Number($(e.currentTarget).data("slot"))));
            w.on("click", "[data-gps]", (e) => this._gps($(e.currentTarget)));
            w.on("click", "[data-ajouter]", (e) => this._ajouter($(e.currentTarget).data("ajouter")));
            w.on("click", "[data-plus]", (e) => this._qte($(e.currentTarget).data("plus"), +1));
            w.on("click", "[data-moins]", (e) => this._qte($(e.currentTarget).data("moins"), -1));
            w.on("change", "[data-qte]", (e) => { const i = $(e.currentTarget).data("qte"); this.articles[i].qty = Math.max(1, parseInt(e.currentTarget.value, 10) || 1); this._memoriser(); this.rendre(); });
            w.on("click", "[data-retirer]", (e) => { this.articles.splice($(e.currentTarget).data("retirer"), 1); this._memoriser(); this.rendre(); });
            w.on("click", "[data-mode]", (e) => {
                const reste = Math.max(0, Math.round((this.total() - this.regle()) * 1000) / 1000);
                this.paiements.push({ mode: $(e.currentTarget).data("mode"), montant: reste, numero: "", banque: "" });
                this._memoriser(); this.rendre();
            });
            w.on("change", "[data-montant]", (e) => { this.paiements[$(e.currentTarget).data("montant")].montant = parseFloat(e.currentTarget.value) || 0; this._memoriser(); this.rendre(); });
            w.on("change", "[data-numero]", (e) => { this.paiements[$(e.currentTarget).data("numero")].numero = e.currentTarget.value; });
            w.on("change", "[data-banque]", (e) => { this.paiements[$(e.currentTarget).data("banque")].banque = e.currentTarget.value; });
            w.on("click", "[data-retirer-pay]", (e) => { this.paiements.splice($(e.currentTarget).data("retirer-pay"), 1); this._memoriser(); this.rendre(); });
            w.on("input", "#cst-rapport", (e) => { this.rapport = e.currentTarget.value; });
        }

        _memoriser() {
            // Le rendu refait les champs : on garde la recherche en cours pour ne pas perdre le fil.
            this.dernier_motif = this.$c.find("#cst-recherche").val() || "";
        }

        _chercher() {
            const txt = this.$c.find("#cst-recherche").val() || "";
            this.dernier_motif = txt;
            const $r = this.$c.find("#cst-resultats");
            if (txt.trim().length < 2) { $r.html(""); return; }
            frappe.call({ method: "customization_app.sous_traitant.rechercher_articles", args: { txt } }).then((r) => {
                const res = r.message || [];
                this._res = res;
                $r.html(res.length ? res.map((a, i) => `<div class="cst-res" data-ajouter="${i}">
                    ${a.image ? `<img class="cst-img" src="${a.image}">` : '<span class="cst-img"></span>'}
                    <div style="flex:1;min-width:0"><b>${esc(a.item_name)}</b><div class="cst-muted">${esc(a.item_code)} · ${__("stock")} ${a.stock}</div></div>
                    <div><b>${fmt(a.prix)}</b></div></div>`).join("")
                    : `<div class="cst-muted" style="padding:8px">${__("Rien dans votre stock pour cette recherche.")}</div>`);
            });
        }

        _ajouter(i) {
            const a = (this._res || [])[i];
            if (!a) return;
            const existe = this.articles.find((x) => x.item_code === a.item_code);
            if (existe) existe.qty += 1;
            else this.articles.push({ item_code: a.item_code, item_name: a.item_name, prix: a.prix, stock: a.stock, qty: 1 });
            this.dernier_motif = "";
            this.rendre();
        }

        _qte(i, delta) {
            this.articles[i].qty = Math.max(1, this.articles[i].qty + delta);
            this._memoriser(); this.rendre();
        }

        _photo(i) {
            const slot = (this.ex.slots || [])[i];
            if (!slot) return;
            new frappe.ui.FileUploader({
                doctype: "Tache de travail", docname: this.tache, folder: "Home/Attachments",
                allow_multiple: !!slot.multiple, restrictions: { allowed_file_types: ["image/*"] },
                on_success: (file) => {
                    const fichiers = Array.isArray(file) ? file : [file];
                    Promise.all(fichiers.map((f) => frappe.call({
                        method: "customization_app.cloture_tache.enregistrer_photo",
                        args: { tache: this.tache, champ: slot.champ, file_url: f.file_url },
                    }).then((r) => {
                        if ((r.message || {}).deja) frappe.show_alert({ message: __("Cette photo est déjà enregistrée — chaque prise doit être une photo différente."), indicator: "orange" }, 6);
                    }))).then(() => this.charger());
                },
            });
        }

        _gps($b) {
            if (!navigator.geolocation) { frappe.msgprint(__("La géolocalisation n'est pas disponible sur cet appareil.")); return; }
            $b.prop("disabled", true).text(__("Localisation…"));
            navigator.geolocation.getCurrentPosition(
                (pos) => {
                    const lien = `https://maps.google.com/?q=${pos.coords.latitude},${pos.coords.longitude}`;
                    frappe.db.set_value("Tache de travail", this.tache, "google_map", lien).then(() => this.charger());
                },
                (err) => { $b.prop("disabled", false).text(__("📍 Ma position actuelle")); frappe.msgprint(__("Position introuvable : {0}", [err.message || ""])); },
                { enableHighAccuracy: true, timeout: 15000 });
        }

        valider() {
            const ex = this.ex;
            const rapport = (this.$c.find("#cst-rapport").val() || this.rapport || "").trim();
            if (ex.rapport_requis && !ex.rapport && !rapport) { frappe.msgprint(__("Le rapport d'intervention est obligatoire.")); return; }
            if (!ex.commande) {
                if (!this.articles.length) { frappe.msgprint(__("Ajoutez au moins un article de votre stock.")); return; }
                const reste = Math.round((this.total() - this.regle()) * 1000) / 1000;
                if (Math.abs(reste) >= 0.005) { frappe.msgprint(__("Le règlement doit couvrir le total : reste {0}.", [fmt(reste)])); return; }
            }
            frappe.call({
                method: "customization_app.sous_traitant.cloturer_intervention",
                args: { tache: this.tache, articles: this.articles.map((a) => ({ item_code: a.item_code, qty: a.qty })),
                        paiements: this.paiements, rapport_visite: rapport || null },
                freeze: true, freeze_message: __("Commande, bon de livraison, clôture…"),
                callback: (r) => {
                    const m = r.message || {};
                    this.d.hide();
                    frappe.msgprint({ title: __("Intervention terminée"), indicator: "green",
                        message: (m.etapes || []).map((e) => `${esc(e.quoi)} : <b>${esc(e.doc)}</b> — ${esc(e.etat)}`).join("<br>") });
                    this.apres();
                },
            });
        }
    }

    window.cloture_sous_traitant = (tache, apres) => new Cloture(tache, apres);
})();
