/**
 * Coloration des anomalies dans la liste des commandes client.
 *
 * On étend le prototype de la vue liste plutôt que de passer par
 * frappe.listview_settings["Sales Order"] : woocommerce_fusion réassigne cet
 * objet en entier, et son fichier est concaténé APRÈS celui de
 * customization_app (app 7 contre app 5). Toute déclaration via
 * doctype_list_js serait donc silencieusement écrasée, pastille de statut et
 * formatters WooCommerce compris.
 */

frappe.provide("frappe.views");

(function () {
    const CLASSES = {
        rouge: "so-alerte-rouge",
        orange: "so-alerte-orange",
        violet: "so-alerte-violet",
        jaune: "so-alerte-jaune",
    };
    const cache = {};        // nom de commande -> {couleur, libelle} ou null
    let css_pose = false;

    function _poser_css() {
        if (css_pose) return;
        css_pose = true;
        const style = document.createElement("style");
        style.textContent = `
            .list-row-container.so-alerte-rouge  { background-color: #fdecea; }
            .list-row-container.so-alerte-orange { background-color: #fff4e5; }
            .list-row-container.so-alerte-violet { background-color: #f3e8fd; }
            .list-row-container.so-alerte-jaune  { background-color: #fbf8c4; }
            .list-row-container.so-alerte-rouge:hover,
            .list-row-container.so-alerte-orange:hover,
            .list-row-container.so-alerte-violet:hover,
            .list-row-container.so-alerte-jaune:hover { filter: brightness(0.97); }
            /* Les pastilles vivent DANS la cellule « Nom du client » : une cellule de la
               grille absorbe son contenu (ellipsis) sans toucher aux largeurs des autres
               colonnes. En sœur des colonnes (.level-left), elles devenaient un item flex
               de plus et décalaient toute la ligne. */
            .so-alerte-pastille {
                display: inline-block; margin-left: 8px; padding: 1px 7px;
                border-radius: 10px; font-size: 11px; white-space: nowrap;
                max-width: 45%; overflow: hidden; text-overflow: ellipsis;
                vertical-align: middle;
            }
            .so-alerte-pastille.rouge  { background: #fbd5d0; color: #922b21; }
            .so-alerte-pastille.orange { background: #ffe3bf; color: #9a5b09; }
            .so-alerte-pastille.violet { background: #e9d5ff; color: #6b21a8; }
            .so-alerte-pastille.jaune  { background: #f5ec7a; color: #6b5900; }
            /* Compteur d'appels : volontairement neutre, il coexiste avec une
               pastille d'anomalie sur la même ligne sans lui disputer l'œil. */
            .so-appels-pastille {
                display: inline-block; margin-left: 8px; padding: 1px 7px;
                border-radius: 10px; font-size: 11px; white-space: nowrap;
                background: #e2e8f0; color: #334155; vertical-align: middle;
            }
            .so-appels-pastille.deux { background: #cbd5e1; color: #1e293b; font-weight: 600; }
        `;
        document.head.appendChild(style);
    }

    function _lignes(listview) {
        // Chaque ligne porte un [data-name] (case à cocher posée par Frappe).
        return listview.$result.find(".list-row-container").map(function () {
            const $ligne = $(this);
            const nom = $ligne.find("[data-name]").first().attr("data-name");
            return nom ? { $ligne, nom: decodeURIComponent(nom) } : null;
        }).get().filter(Boolean);
    }

    // Toutes les classes de couleur, pour un nettoyage exhaustif : lister les
    // couleurs à la main laisserait une ligne teintée après un tri dès qu'une
    // nouvelle couleur est ajoutée.
    const TOUTES_CLASSES = Object.values(CLASSES).join(" ");

    function _nettoyer($ligne) {
        // Le rendu est rejoué au tri, au filtrage et au changement de page :
        // sans nettoyage, les pastilles s'empileraient et la couleur
        // précédente resterait.
        $ligne.removeClass(TOUTES_CLASSES);
        $ligne.find(".so-alerte-pastille, .so-appels-pastille").remove();
    }

    function _peindre(lignes) {
        lignes.forEach(({ $ligne, nom }) => {
            _nettoyer($ligne);
            const info = cache[nom];
            if (!info) return;
            // La cellule sujet (« Nom du client ») et non .level-left : une pastille en
            // sœur des colonnes décalait Total TTC / % / ID sur les lignes à anomalie.
            const $cible = ($ligne.find(".list-subject").first().length
                ? $ligne.find(".list-subject").first()
                : $ligne.find(".level-left").first());

            if (info.libelle) {
                $ligne.addClass(CLASSES[info.couleur]);
                $cible.append(
                    `<span class="so-alerte-pastille ${info.couleur}"
                           title="${frappe.utils.escape_html(info.libelle)}">${
                        frappe.utils.escape_html(info.libelle)}</span>`
                );
            }

            if (info.appels) {
                const texte = info.appels > 1
                    ? __("{0} appels sans réponse", [info.appels])
                    : __("1 appel sans réponse");
                $cible.append(
                    `<span class="so-appels-pastille ${info.appels > 1 ? "deux" : ""}"
                           title="${frappe.utils.escape_html(texte)}">📞 ${info.appels}</span>`
                );
            }
        });
    }

    function appliquer_alertes(listview) {
        _poser_css();

        const lignes = _lignes(listview);
        if (!lignes.length) return;

        const inconnus = lignes.map(l => l.nom).filter(nom => !(nom in cache));

        if (!inconnus.length) {
            _peindre(lignes);
            return;
        }

        frappe.call({
            method: "customization_app.commande_alertes.get_alertes",
            args: { noms: JSON.stringify(inconnus) },
            callback: function (r) {
                const alertes = r.message || {};
                // Mémoriser aussi les commandes saines, pour ne pas les
                // redemander à chaque re-rendu.
                inconnus.forEach(nom => { cache[nom] = alertes[nom] || null; });
                _peindre(_lignes(listview));
            },
        });
    }

    const _after_render = frappe.views.ListView.prototype.after_render;
    frappe.views.ListView.prototype.after_render = function () {
        _after_render.apply(this, arguments);
        if (this.doctype !== "Sales Order") return;
        try {
            appliquer_alertes(this);
        } catch (e) {
            console.error("Alertes commandes client :", e);
        }
    };
})();
