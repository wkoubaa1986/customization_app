/**
 * « 📦 Articles » — la liste d’articles du technicien en écran simplifié (demande du 18/11/2026) :
 * une barre de recherche (nom ou code), puis pour chaque article son image, son code, son nom, sa description
 * et son prix dans la liste « Vente standard ». Lecture seule : aucune fiche à ouvrir, aucun bouton d’action.
 * Tout vient du serveur (customization_app.accueil_technicien.articles), par pages de 30.
 * Filtre par groupe d'articles et tri par nom, par groupe ou par prix (18/11/2026) : décidés par le serveur, qui
 * connaît le prix du jour de chaque article.
 */
(function () {
    const METHODE = "customization_app.accueil_technicien.articles";
    const esc = (t) => frappe.utils.escape_html(String(t == null ? "" : t));
    const prix = (v) => Number(v).toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 });

    function poser_css() {
        if (document.getElementById("art-css")) return;
        const s = document.createElement("style");
        s.id = "art-css";
        s.textContent = `
        .art-barre { position: sticky; top: var(--navbar-height, 48px); z-index: 5; background: var(--bg-color, #f8f9fa); padding: 6px 0 10px; }
        .art-barre input { width: 100%; min-height: 48px; font-size: 16px; padding: 10px 14px; border-radius: 12px;
                           border: 1px solid var(--border-color, #d1d8dd); background: #fff; }
        .art-choix { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; margin-top: 6px; }
        .art-choix select { width: 100%; min-height: 44px; font-size: 14px; padding: 0 10px; border-radius: 10px;
                            border: 1px solid var(--border-color, #d1d8dd); background: #fff; }
        .art-intertitre { grid-column: 1 / -1; font-weight: 800; font-size: 13px; text-transform: uppercase; letter-spacing: .3px;
                          color: #374151; padding: 10px 2px 2px; border-bottom: 2px solid var(--border-color, #e4e8ee); }
        .art-etq { font-family: var(--font-stack, sans-serif); font-size: 11px; background: #eef2f7; color: #4b5563;
                   border-radius: 999px; padding: 1px 7px; margin-left: 4px; white-space: nowrap; }
        .art-compte { color: var(--text-muted, #6b7280); font-size: 12.5px; padding: 6px 2px 0; }
        .art-liste { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 10px; }
        .art { display: flex; gap: 12px; background: #fff; border: 1px solid var(--border-color, #e4e8ee);
               border-radius: 14px; padding: 10px; }
        .art-img { flex: 0 0 92px; width: 92px; height: 92px; border-radius: 10px; background: #f3f5f8; overflow: hidden;
                   display: flex; align-items: center; justify-content: center; color: #b6bdc7; font-size: 28px; cursor: zoom-in; }
        .art-img img { width: 100%; height: 100%; object-fit: contain; }
        .art-img.vide { cursor: default; }
        .art-txt { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; }
        .art-nom { font-weight: 700; font-size: 14.5px; line-height: 1.25; }
        .art-code { font-family: var(--font-stack-monospace, monospace); font-size: 12px; color: var(--text-muted, #6b7280); margin-top: 2px; }
        .art-desc { font-size: 12.5px; color: #4b5563; margin-top: 4px; display: -webkit-box; -webkit-line-clamp: 3;
                    -webkit-box-orient: vertical; overflow: hidden; }
        .art-prix { margin-top: auto; padding-top: 6px; font-weight: 800; font-size: 16px; }
        .art-prix small { font-weight: 500; font-size: 11.5px; color: var(--text-muted, #6b7280); }
        .art-prix.sans { font-weight: 500; font-size: 12.5px; color: var(--text-muted, #6b7280); }
        .art-plus { width: 100%; min-height: 48px; margin: 12px 0 4px; font-weight: 700; }
        .art-vide { color: var(--text-muted, #6b7280); text-align: center; padding: 28px 0; }`;
        document.head.appendChild(s);
    }

    class ArticlesTechnicien {
        constructor($parent) {
            poser_css();
            this.$el = $(`<div>
                <div class="art-barre">
                    <input type="search" inputmode="search" autocomplete="off" placeholder="${esc(__("Chercher par nom ou par code…"))}">
                    <div class="art-choix">
                        <select class="art-groupe" title="${esc(__("Groupe d’articles"))}"><option value="">${esc(__("Tous les groupes"))}</option></select>
                        <select class="art-tri" title="${esc(__("Trier"))}">
                            <option value="nom">${esc(__("Tri : nom"))}</option>
                            <option value="groupe">${esc(__("Tri : groupe"))}</option>
                            <option value="prix_croissant">${esc(__("Tri : prix croissant"))}</option>
                            <option value="prix_decroissant">${esc(__("Tri : prix décroissant"))}</option>
                        </select>
                    </div>
                    <div class="art-compte"></div>
                </div>
                <div class="art-liste"></div>
                <button type="button" class="btn btn-default art-plus" style="display:none">${esc(__("Afficher plus"))}</button>
            </div>`).appendTo($parent);
            this.$champ = this.$el.find("input");
            this.$liste = this.$el.find(".art-liste");
            this.$plus = this.$el.find(".art-plus");
            this.$compte = this.$el.find(".art-compte");
            this.tour = 0;
            this.$groupe = this.$el.find(".art-groupe");
            this.$tri = this.$el.find(".art-tri");
            this.$champ.on("input", frappe.utils.debounce(() => this.charger(true), 300));
            this.$groupe.add(this.$tri).on("change", () => this.charger(true));
            frappe.call({ method: "customization_app.accueil_technicien.groupes" }).then((r) => {
                (r.message || []).forEach((g) => this.$groupe.append(
                    `<option value="${esc(g.groupe)}">${esc(g.groupe)} (${g.nombre})</option>`));
            });
            this.$plus.on("click", () => this.charger(false));
            this.$liste.on("click", ".art-img:not(.vide)", (ev) => this.agrandir($(ev.currentTarget)));
            this.charger(true);
        }

        charger(depuis_le_debut) {
            if (depuis_le_debut) this.charges = 0;
            const tour = ++this.tour;   // une réponse en retard (frappe rapide) ne doit pas écraser la suivante
            if (depuis_le_debut) this.dernier_groupe = null;
            frappe.call({ method: METHODE, args: { recherche: this.$champ.val(), debut: this.charges,
                                                   groupe: this.$groupe.val() || "", tri: this.$tri.val() || "nom" } }).then((r) => {
                if (tour !== this.tour) return;
                const m = r.message || {};
                if (depuis_le_debut) this.$liste.empty();
                (m.articles || []).forEach((a) => {
                    // Trié par groupe : un intertitre à chaque changement de groupe.
                    if (m.tri === "groupe" && a.groupe !== this.dernier_groupe) {
                        this.dernier_groupe = a.groupe;
                        this.$liste.append(`<div class="art-intertitre">${esc(a.groupe || __("Sans groupe"))}</div>`);
                    }
                    this.$liste.append(this.carte(a, m.liste_prix, m.tri));
                });
                this.charges += (m.articles || []).length;
                this.$plus.toggle(!!m.suite);
                this.$compte.text(m.total ? __("{0} article(s) — prix {1}", [m.total, m.liste_prix]) : "");
                if (!m.total) this.$liste.html(`<div class="art-vide">${esc(__("Aucun article ne correspond."))}</div>`);
            });
        }

        carte(a, liste, tri) {
            const image = a.image
                ? `<div class="art-img" data-src="${esc(a.image)}" data-nom="${esc(a.nom)}"><img loading="lazy" src="${esc(a.image)}" alt=""></div>`
                : `<div class="art-img vide">📦</div>`;
            const p = a.prix == null
                ? `<div class="art-prix sans">${esc(__("Pas de prix en {0}", [liste]))}</div>`
                : `<div class="art-prix">${prix(a.prix)} <small>DT${a.unite ? " / " + esc(a.unite) : ""}</small></div>`;
            return `<div class="art">${image}<div class="art-txt">
                <div class="art-nom">${esc(a.nom)}</div><div class="art-code">${esc(a.code)}${
                    a.groupe && tri !== "groupe" ? ` <span class="art-etq">${esc(a.groupe)}</span>` : ""}</div>
                ${a.description ? `<div class="art-desc">${esc(a.description)}</div>` : ""}${p}</div></div>`;
        }

        agrandir($img) {
            const d = new frappe.ui.Dialog({ title: $img.attr("data-nom") || __("Article") });
            d.$body.html(`<img src="${esc($img.attr("data-src"))}" alt="" style="width:100%;height:auto;border-radius:8px">`);
            d.show();
        }
    }

    frappe.pages["articles-technicien"].on_page_load = function (wrapper) {
        frappe.ui.make_app_page({ parent: wrapper, title: __("Articles"), single_column: true });
        wrapper.articles = new ArticlesTechnicien($(wrapper).find(".layout-main-section"));
    };
})();
