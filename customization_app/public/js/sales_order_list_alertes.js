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
        bleu: "so-alerte-bleu",
    };
    // « Retour colis » (Aramex) : un fait constaté, pastille bleue distincte —
    // il coexiste avec une éventuelle anomalie sans la remplacer.
    const LIBELLE_RETOUR = "📦 Retour colis";
    const LIBELLE_ARAMEX_SB = "🚚 Livraison Aramex faite sans bordereau";
    const cache = {};        // nom de commande -> {couleur, libelle} ou null
    // Pastilles enrichies (demande 27/08) : Aramex (bordereau + statut du colis)
    // et tâches liées (statut, employé, lien direct) — chargées en lot comme
    // les alertes, affichées sur la même 2e ligne.
    const cache_infos = {};  // nom de commande -> {aramex, taches} ou null
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
            .list-row-container.so-alerte-bleu   { background-color: #e8f1fd; }
            .list-row-container.so-alerte-rouge:hover,
            .list-row-container.so-alerte-orange:hover,
            .list-row-container.so-alerte-violet:hover,
            .list-row-container.so-alerte-jaune:hover { filter: brightness(0.97); }
            /* La pastille d'anomalie s'affiche SOUS le nom du client (décision 19/08) :
               la cellule sujet est trop étroite pour porter nom + libellé sur une ligne —
               en sœur des colonnes elle décalait la grille, inline elle se faisait
               écraser en « T… ». Deuxième ligne : la cellule passe en flex-wrap, la
               pastille occupe toute la largeur (flex-basis 100 %), et SEULES les lignes
               à anomalie prennent une hauteur auto — les autres gardent la leur. */
            /* La ligne 2 est positionnée par rapport à la LIGNE, pas à la cellule sujet :
               confinée à la cellule, la pastille se tronquait (« dette non pa… ») alors
               que tout l'espace sous les autres colonnes est vide à cette hauteur. La
               hauteur supplémentaire vient du padding-bottom de la ligne. */
            .list-row-container.so-avec-alerte .list-row { height: auto; min-height: 40px;
                position: relative; padding-top: 4px; padding-bottom: 24px; }
            .list-subject .so-alerte-ligne2 { position: absolute; left: 41px; right: 15px;
                bottom: 4px; line-height: 1; min-width: 0; }
            .so-alerte-pastille {
                display: inline-block; padding: 1px 7px;
                border-radius: 10px; font-size: 10.5px; white-space: nowrap;
                max-width: 100%; overflow: hidden; text-overflow: ellipsis;
                vertical-align: middle;
            }
            .so-alerte-pastille.rouge  { background: #fbd5d0; color: #922b21; }
            .so-alerte-pastille.orange { background: #ffe3bf; color: #9a5b09; }
            .so-alerte-pastille.violet { background: #e9d5ff; color: #6b21a8; }
            .so-alerte-pastille.jaune  { background: #f5ec7a; color: #6b5900; }
            .so-alerte-pastille.bleu   { background: #cfe3fb; color: #1e5aa8; }
            .so-alerte-pastille.vert   { background: #d1f2d9; color: #1a7a3a; }
            .so-alerte-pastille.gris   { background: #e2e8f0; color: #334155; }
            .so-alerte-pastille + .so-alerte-pastille { margin-left: 6px; }
            a.so-alerte-pastille, a.so-alerte-pastille:hover { text-decoration: none; }
            /* Compteur d'appels : volontairement neutre, il coexiste avec une
               pastille d'anomalie sur la même ligne sans lui disputer l'œil. */
            .so-appels-pastille {
                display: inline-block; margin-left: 8px; padding: 1px 7px;
                border-radius: 10px; font-size: 11px; white-space: nowrap;
                background: #e2e8f0; color: #334155; vertical-align: middle;
                flex: 0 0 auto;
            }
            .so-appels-pastille.deux { background: #cbd5e1; color: #1e293b; font-weight: 600; }
            /* Dans la ligne des pastilles, le 📞 ouvre la rangée : pas de marge gauche. */
            .so-alerte-ligne2 .so-appels-pastille { margin-left: 0; margin-right: 6px; }
            /* Fiche : le bandeau de titre prend la couleur de l'anomalie, la pastille
               s'affiche à côté du statut (Brouillon, À facturer…). */
            .page-head.so-alerte-rouge  { background-color: #fdecea; }
            .page-head.so-alerte-orange { background-color: #fff4e5; }
            .page-head.so-alerte-violet { background-color: #f3e8fd; }
            .page-head.so-alerte-jaune  { background-color: #fbf8c4; }
            .page-head.so-alerte-bleu   { background-color: #e8f1fd; }
            .so-alerte-pastille-fiche { margin-left: 8px; font-size: 11px; }
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
        $ligne.removeClass(TOUTES_CLASSES + " so-avec-alerte");
        $ligne.find(".so-alerte-ligne2, .so-alerte-pastille, .so-appels-pastille").remove();
    }

    function _peindre(lignes) {
        lignes.forEach(({ $ligne, nom }) => {
            _nettoyer($ligne);
            // ⚠️ une commande SAINE peut quand même porter des pastilles d'info
            // (Aramex, tâche) : on ne sort plus sur l'absence d'alerte.
            const info = cache[nom] || {};
            // La cellule sujet (« Nom du client ») et non .level-left : une pastille en
            // sœur des colonnes décalait Total TTC / % / ID sur les lignes à anomalie.
            const $cible = ($ligne.find(".list-subject").first().length
                ? $ligne.find(".list-subject").first()
                : $ligne.find(".level-left").first());

            const pastilles = [];
            // 📞 en TÊTE de la ligne des pastilles (demande 27/08) : appendu
            // dans la cellule du nom, il se faisait rogner dès que le nom était
            // long — la 2e ligne, elle, court sur toute la largeur de la ligne.
            if (info.appels) {
                const texte_appels = info.appels > 1
                    ? __("{0} appels sans réponse", [info.appels])
                    : __("1 appel sans réponse");
                pastilles.push(`<span class="so-appels-pastille ${info.appels > 1 ? "deux" : ""}"
                       title="${frappe.utils.escape_html(texte_appels)}">📞 ${info.appels}</span>`);
            }
            if (info.libelle) {
                pastilles.push(`<span class="so-alerte-pastille ${info.couleur}"
                       title="${frappe.utils.escape_html(info.libelle)}">${
                    frappe.utils.escape_html(info.libelle)}</span>`);
            }
            if (info.retour) {
                pastilles.push(`<span class="so-alerte-pastille bleu"
                       title="${LIBELLE_RETOUR}">${LIBELLE_RETOUR}</span>`);
            }
            // « Livraison Aramex sans bordereau » : un FAIT indépendant qui
            // COEXISTE avec l'anomalie classique — deux pastilles sur la même
            // ligne (décision 27/08), même modèle que « Retour colis ».
            if (info.aramex_sb) {
                pastilles.push(`<span class="so-alerte-pastille orange"
                       title="${LIBELLE_ARAMEX_SB}">${LIBELLE_ARAMEX_SB}</span>`);
            }
            pastilles.push(..._pastilles_infos(nom));
            if (pastilles.length) {
                // La couleur de fond : l'anomalie d'abord, le retour ou le fait
                // Aramex sinon — les pastilles d'info seules ne teintent pas.
                if (info.libelle || info.retour) {
                    $ligne.addClass(CLASSES[info.libelle ? info.couleur : "bleu"]);
                } else if (info.aramex_sb) {
                    $ligne.addClass(CLASSES.orange);
                }
                $ligne.addClass("so-avec-alerte");
                $cible.append(`<div class="so-alerte-ligne2">${pastilles.join("")}</div>`);
            }

        });
    }

    // Une couleur par état du colis (demande 27/08) : vert = livré, bleu = en
    // route, gris = créé (encore chez nous), ROUGE = colis revenu (Returned —
    // demande 27/08 aussi) ou bordereau introuvable, orange = échec/refus de
    // livraison en cours.
    function _couleur_aramex(a) {
        if (a.livre) return "vert";
        const s = (a.statut || "").toLowerCase();
        if (a.erreur || s.includes("introuvable") || s.includes("indisponible")) return "rouge";
        if (["return", "retour", "renvoy"].some((m) => s.includes(m))) return "rouge";
        if (["refus", "echec", "échec"].some((m) => s.includes(m))) return "orange";
        if (s.includes("créé") || s.includes("cree")) return "gris";
        return "bleu";
    }

    // Les pastilles d'info (Aramex + tâches) de la 2e ligne. Rendu pur depuis
    // le cache — le chargement se fait en lot dans appliquer_alertes.
    function _pastilles_infos(nom) {
        const esc = frappe.utils.escape_html;
        const infos = cache_infos[nom];
        if (!infos) return [];
        const out = [];
        const a = infos.aramex;
        if (a) {
            // Toujours nommer le transporteur : « Aramex — 51330112875 · statut ».
            const libelle = "🚚 " + (a.bordereau
                ? __("Aramex") + " — " + a.bordereau
                : __("Aramex — sans bordereau"))
                + (a.statut ? " · " + a.statut
                    : a.bordereau ? " · " + __("statut ?") : "");
            const titre = a.erreur || a.maj
                || (a.bordereau && !a.statut ? __("Suivi jamais demandé — cliquer pour interroger Aramex") : libelle);
            const couleur = _couleur_aramex(a);
            if (a.url) {
                // Le suivi est connu : la pastille ouvre la page Aramex du colis.
                out.push(`<a class="so-alerte-pastille ${couleur}" href="${esc(a.url)}" target="_blank"
                       title="${esc(titre)}" onclick="event.stopPropagation()">${esc(libelle)} ↗</a>`);
            } else if (a.bordereau) {
                // Bordereau sans suivi (ou en erreur) : un clic interroge Aramex
                // à la demande — ~2 s, puis la pastille se repeint avec le statut.
                out.push(`<span class="so-alerte-pastille ${couleur}" title="${esc(titre)}"
                       style="cursor:pointer" data-suivi-ref="${esc(a.bordereau)}"
                       data-suivi-so="${esc(nom)}">${esc(libelle)} 🔄</span>`);
            } else {
                out.push(`<span class="so-alerte-pastille ${couleur}" title="${esc(titre)}">${esc(libelle)}</span>`);
            }
        }
        (infos.taches || []).forEach((t) => {
            const couleur = t.statut === "Completed" ? "vert"
                : t.statut === "Cancelled" ? "gris" : "jaune";
            // La date ET l'heure dans la pastille elle-même (demande 27/08) :
            // « 01-09-2026 09:00 » — l'heure tronquée aux minutes.
            let quand = "";
            if (t.date) {
                const [jour, heure] = t.date.split(" ");
                quand = " · " + frappe.datetime.str_to_user(jour)
                    + (heure ? " " + heure.slice(0, 5) : "");
            }
            const libelle = "🛠️ " + (t.type || __("Tâche")) + " · " + __(t.statut || "")
                + (t.employe ? " · " + t.employe : "") + quand;
            out.push(`<a class="so-alerte-pastille ${couleur}"
                href="/app/tache-de-travail/${encodeURIComponent(t.tache)}"
                title="${esc(t.tache)}${t.temps ? " · " + esc(t.temps) : ""}"
                onclick="event.stopPropagation()">${esc(libelle)}</a>`);
        });
        return out;
    }

    function appliquer_alertes(listview) {
        _poser_css();

        const lignes = _lignes(listview);
        if (!lignes.length) return;

        // 1. L'état connu, TOUT DE SUITE — pas de clignotement au tri/scroll.
        _peindre(lignes);

        // 2. Puis TOUJOURS re-demander pour les lignes visibles. L'ancien cache
        //    « une fois pour toutes » montrait des pastilles périmées : le motif
        //    bouge côté serveur (hooks de sauvegarde, recalculs, cron nocturne)
        //    et la liste ne s'en apercevait qu'après un F5. Un appel groupé par
        //    rendu (≤ 100 noms) est bon marché ; le jeton évite qu'une réponse
        //    lente d'un rendu précédent écrase celle du rendu courant.
        // ⚠️ PAR PAQUETS DE 100. get_alertes TRONQUE en silence à MAX_NOMS (100) :
        // le défilement infini accumule les lignes chargées, et envoyer 200 noms
        // d'un coup laissait les 100 derniers SANS réponse — leur cache passait à
        // null et l'anomalie + le 📞 disparaissaient de l'écran alors que la base
        // les portait (constaté 27/08 sur une ligne au-delà de la 100e).
        const noms = lignes.map(l => l.nom);
        const MORCEAU = 100;
        const paquets = [];
        for (let i = 0; i < noms.length; i += MORCEAU) paquets.push(noms.slice(i, i + MORCEAU));
        const jeton = (listview.__so_jeton = (listview.__so_jeton || 0) + 1);
        Promise.all(paquets.flatMap((paquet) => [
            frappe.call({
                method: "customization_app.commande_alertes.get_alertes",
                args: { noms: JSON.stringify(paquet) },
            }).then((r) => {
                const alertes = r.message || {};
                paquet.forEach(nom => { cache[nom] = alertes[nom] || null; });
            }),
            frappe.call({
                method: "customization_app.traitement_commandes.infos_liste",
                args: { noms: JSON.stringify(paquet) },
            }).then((r) => {
                const infos = r.message || {};
                paquet.forEach(nom => { cache_infos[nom] = infos[nom] || null; });
            }),
        ])).then(() => {
            if (listview.__so_jeton === jeton) _peindre(_lignes(listview));
        });
    }

    function _poser_bouton(listview) {
        // « Mettre à jour les anomalies » : rejoue TOUTE la logique côté serveur, actions
        // comprises (clôture des tâches des commandes soldées), puis requalifie tout.
        if (listview.__so_bouton_anomalies) return;
        listview.__so_bouton_anomalies = true;
        listview.page.add_inner_button(__("Mettre à jour les anomalies"), () => {
            frappe.call({
                method: "customization_app.commande_alertes.resynchroniser",
                freeze: true,
                freeze_message: __("Recalcul des anomalies…"),
                callback: (r) => {
                    const m = r.message || {};
                    frappe.show_alert({
                        message: __("{0} tâche(s) clôturée(s), {1} commande(s) requalifiée(s)",
                            [m.fermees || 0, m.modifiees || 0]),
                        indicator: "green",
                    });
                    // Le cache par commande est périmé : tout re-demander au re-rendu.
                    Object.keys(cache).forEach((k) => delete cache[k]);
                    Object.keys(cache_infos).forEach((k) => delete cache_infos[k]);
                    listview.refresh();
                },
            });
        });
    }

    function _poser_filtre_anomalie_multi(listview) {
        // Le filtre standard « Anomalie » (Select) ne prend qu'une valeur : on
        // le remplace par une multisélection qui filtre en « in ». Le Select
        // natif est masqué, pas supprimé — frappe le régénère à chaque vue.
        if (listview.__so_filtre_anomalie_multi) return;
        listview.__so_filtre_anomalie_multi = true;

        const natif = listview.page.fields_dict && listview.page.fields_dict.custom_anomalie;
        if (natif && natif.$wrapper) natif.$wrapper.hide();

        const OPTIONS = Object.keys(LIBELLE_COULEUR);
        // Le champ porte le NOM RÉEL de la colonne (custom_anomalie) avec
        // condition "in" : get_standard_filters() de frappe construit alors
        // lui-même [doctype, custom_anomalie, in, [valeurs]] — un fieldname
        // inventé partait tel quel dans la requête (« Champ non autorisé »).
        // ⚠️ le 2e argument (parent) est indispensable : sans lui le contrôle
        // atterrit dans la zone d'actions en haut à droite, pas dans la barre
        // des filtres standard (c'est ainsi que frappe pose les siens).
        const champ = listview.page.add_field({
            fieldname: "custom_anomalie",
            label: __("Anomalie"),
            fieldtype: "MultiSelectList",
            condition: "in",
            get_data(txt) {
                return OPTIONS
                    .filter((o) => !txt || o.toLowerCase().includes(txt.toLowerCase()))
                    .map((o) => ({ value: o, description: "" }));
            },
            change: () => listview.filter_area.debounced_refresh_list_view(),
        }, listview.filter_area.standard_filters_wrapper);

        // Un tableau VIDE est truthy : sans ce garde, « in [] » partirait dans
        // la requête dès que tout est décoché.
        const _get_value = champ.get_value.bind(champ);
        champ.get_value = () => {
            const v = _get_value();
            return v && v.length ? v : null;
        };
        // filter_area.remove() rappelle set_value("") : une MultiSelectList
        // attend un tableau — on normalise toute valeur falsy ou scalaire.
        const _set_value = champ.set_value.bind(champ);
        champ.set_value = (v) =>
            _set_value(Array.isArray(v) ? v : v ? [v] : []);

        // « Effacer les anomalies » : bouton À CÔTÉ du filtre (demande 26/08),
        // visible dès qu'au moins une anomalie est cochée — un clic vide tout.
        // Positionné en fin de fonction, APRÈS le réordonnancement des filtres.
        const $effacer = $(
            `<button type="button" class="btn btn-xs btn-default so-anomalie-effacer"
                title="${__("Effacer les anomalies sélectionnées")}"
                style="display:none;align-self:center;white-space:nowrap;
                       margin-left:2px;">✕ ${__("Effacer")}</button>`
        );
        const _maj_effacer = () => {
            const v = champ.get_value();
            $effacer.toggle(!!(v && v.length));
        };
        $effacer.on("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            champ.set_value([]);
            // Retire AUSSI le filtre matérialisé « custom_anomalie in [...] » :
            // en condition "in" il vit dans la liste de filtres (badge
            // « Filters »), pas dans le champ standard — vider le champ seul
            // laisserait le badge et la requête en place.
            listview.filter_area.remove("custom_anomalie");
            _maj_effacer();
            listview.filter_area.debounced_refresh_list_view();
        });
        // df.change est relu à chaque sélection : on y greffe la visibilité du ✕.
        const _change = champ.df.change;
        champ.df.change = () => {
            if (_change) _change();
            _maj_effacer();
        };

        // Ordre voulu : … Statut de la Livraison, Statut de la Facturation,
        // Anomalie (multi), Retour colis.
        const dict = listview.page.fields_dict || {};
        const facturation = dict.billing_status || dict.delivery_status;
        if (facturation && facturation.$wrapper && champ && champ.$wrapper) {
            champ.$wrapper.insertAfter(facturation.$wrapper);
            const retour = dict.custom_retour_colis;
            if (retour && retour.$wrapper) {
                retour.$wrapper.insertAfter(champ.$wrapper);
            }
        }

        // Une valeur déjà posée dans l'URL (ancien Select, lien partagé) est
        // reprise dans la multisélection pour rester cohérente à l'écran.
        const existant = (listview.filter_area.get() || []).find(
            (f) => f[1] === "custom_anomalie");
        if (existant && champ && champ.set_value) {
            const vals = Array.isArray(existant[3]) ? existant[3] : [existant[3]];
            champ.set_value(vals.filter(Boolean));
        }
        // Insertion en dernier : le bouton suit le filtre Anomalie, même après
        // le réordonnancement ci-dessus (… Anomalie | ✕ Effacer | Retour colis).
        $effacer.insertAfter(champ.$wrapper);
        _maj_effacer();
    }

    function _poser_bouton_aramex(listview) {
        // « 🚚 Actualiser Aramex » : interroge d'un coup tous les colis des
        // commandes depuis le 01/07/2026 — livrés et retours sautés — puis
        // matérialise le statut dans custom_statut_aramex (le filtre de la barre).
        if (listview.__so_bouton_aramex) return;
        listview.__so_bouton_aramex = true;
        listview.page.add_inner_button(__("🚚 Actualiser Aramex"), () => {
            frappe.call({
                method: "customization_app.traitement_commandes.actualiser_statuts_aramex",
                freeze: true,
                freeze_message: __("Interrogation d'Aramex pour tous les colis en cours…"),
                callback: (r) => {
                    const m = r.message || {};
                    const detail = Object.entries(m.statuts || {})
                        .map(([s, n]) => `${s} : ${n}`).join(" · ");
                    frappe.show_alert({
                        message: __("{0} colis interrogé(s), {1} déjà livrés/revenus sautés, {2} erreur(s)",
                            [m.interroges || 0, m.sautes || 0, m.erreurs || 0])
                            + (detail ? "<br>" + frappe.utils.escape_html(detail) : ""),
                        indicator: m.erreurs ? "orange" : "green",
                    }, 10);
                    Object.keys(cache_infos).forEach((k) => delete cache_infos[k]);
                    listview.refresh();
                },
            });
        });
    }

    function _poser_filtre_aramex(listview) {
        // Case « 🚚 Aramex » dans la barre des filtres : ne montre que les
        // commandes en Livraison Aramex (échéancier de paiement dédié).
        if (listview.__so_filtre_aramex) return;
        listview.__so_filtre_aramex = true;
        const champ = listview.page.add_field({
            fieldname: "payment_terms_template",
            label: __("🚚 Aramex"),
            fieldtype: "Check",
            condition: "=",
            change: () => listview.filter_area.debounced_refresh_list_view(),
        }, listview.filter_area.standard_filters_wrapper);
        // Cochée → filtre « échéancier = Livraison Aramex » ; décochée → aucun filtre.
        champ.get_value = () => (champ.get_input_value() ? "Livraison Aramex" : null);
    }

    function _poser_filtre_statut_aramex_multi(listview) {
        // Le filtre standard « Statut Aramex » (Data) ne fait qu'un LIKE sur du
        // texte tapé : on le remplace par une multisélection alimentée par les
        // statuts réellement en base — même mécanique que le filtre Anomalie
        // (champ au NOM RÉEL de la colonne + condition "in", natif masqué).
        if (listview.__so_filtre_statut_aramex) return;
        listview.__so_filtre_statut_aramex = true;

        const natif = listview.page.fields_dict && listview.page.fields_dict.custom_statut_aramex;
        if (natif && natif.$wrapper) natif.$wrapper.hide();

        // Liste de secours immédiate ; remplacée par les statuts réellement
        // présents dès que le serveur répond.
        let OPTIONS = ["Livré", "Créé", "Statut en transit", "Returned",
            "Introuvable chez Aramex", "Suivi indisponible"];
        frappe.call({
            method: "customization_app.traitement_commandes.statuts_aramex",
        }).then((r) => {
            if ((r.message || []).length) OPTIONS = r.message;
        });

        const champ = listview.page.add_field({
            fieldname: "custom_statut_aramex",
            label: __("Statut Aramex"),
            fieldtype: "MultiSelectList",
            condition: "in",
            get_data(txt) {
                return OPTIONS
                    .filter((o) => !txt || o.toLowerCase().includes(txt.toLowerCase()))
                    .map((o) => ({ value: o, description: "" }));
            },
            change: () => listview.filter_area.debounced_refresh_list_view(),
        }, listview.filter_area.standard_filters_wrapper);

        // Un tableau VIDE est truthy : sans ce garde, « in [] » partirait dans
        // la requête dès que tout est décoché.
        const _get_value = champ.get_value.bind(champ);
        champ.get_value = () => {
            const v = _get_value();
            return v && v.length ? v : null;
        };
        const _set_value = champ.set_value.bind(champ);
        champ.set_value = (v) =>
            _set_value(Array.isArray(v) ? v : v ? [v] : []);

        // Ordre voulu (demande 27/08) : 🚚 Aramex | Livraison faite sans
        // bordereau | Statut Aramex | ✕. La case du fait est le filtre standard
        // généré par frappe : on la DÉPLACE à côté de la case Aramex.
        const aramex = listview.page.fields_dict
            && listview.page.fields_dict.payment_terms_template;
        if (aramex && aramex.$wrapper && champ && champ.$wrapper) {
            champ.$wrapper.insertAfter(aramex.$wrapper);
            const fait = listview.page.fields_dict.custom_aramex_sans_bordereau;
            if (fait && fait.$wrapper) fait.$wrapper.insertAfter(aramex.$wrapper);
        }

        // « ✕ Effacer » à côté du filtre, visible dès qu'un statut est coché —
        // même mécanique que le bouton du filtre Anomalie : vider le champ ET
        // retirer le filtre matérialisé « custom_statut_aramex in [...] ».
        const $effacer = $(
            `<button type="button" class="btn btn-xs btn-default so-statut-aramex-effacer"
                title="${__("Effacer les statuts Aramex sélectionnés")}"
                style="display:none;align-self:center;white-space:nowrap;
                       margin-left:2px;">✕ ${__("Effacer")}</button>`
        );
        const _maj_effacer = () => {
            const v = champ.get_value();
            $effacer.toggle(!!(v && v.length));
        };
        $effacer.on("click", (e) => {
            e.preventDefault();
            e.stopPropagation();
            champ.set_value([]);
            listview.filter_area.remove("custom_statut_aramex");
            _maj_effacer();
            listview.filter_area.debounced_refresh_list_view();
        });
        const _change = champ.df.change;
        champ.df.change = () => {
            if (_change) _change();
            _maj_effacer();
        };
        $effacer.insertAfter(champ.$wrapper);
        _maj_effacer();
    }

    function _poser_interrogation_aramex(listview) {
        // Délégué : les pastilles se recréent à chaque re-rendu, le handler
        // vit donc sur le conteneur de la liste, posé une seule fois.
        if (listview.__so_suivi_aramex) return;
        listview.__so_suivi_aramex = true;
        listview.$result.on("click", "[data-suivi-ref]", function (e) {
            e.preventDefault();
            e.stopPropagation();
            const ref = $(this).attr("data-suivi-ref");
            const so = $(this).attr("data-suivi-so");
            frappe.call({
                method: "customization_app.livraison_aramex.rafraichir",
                args: { references: JSON.stringify([ref]), tout: 1 },
                freeze: true,
                freeze_message: __("Interrogation d'Aramex…"),
                callback: () => {
                    delete cache_infos[so];
                    appliquer_alertes(listview);
                },
            });
        });
    }

    const _after_render = frappe.views.ListView.prototype.after_render;
    frappe.views.ListView.prototype.after_render = function () {
        _after_render.apply(this, arguments);
        if (this.doctype !== "Sales Order") return;
        try {
            _poser_bouton(this);
            _poser_bouton_aramex(this);
            _poser_filtre_anomalie_multi(this);
            _poser_filtre_aramex(this);
            _poser_filtre_statut_aramex_multi(this);
            _poser_interrogation_aramex(this);
            appliquer_alertes(this);
        } catch (e) {
            console.error("Alertes commandes client :", e);
        }
    };

    // ── Fiche : bandeau coloré + pastille à côté du statut ──────────────────
    // ⚠️ Miroir de commande_alertes.COULEURS côté serveur — garder les deux en phase.
    const LIBELLE_COULEUR = {
        "Tâche ouverte en retard": "jaune",
        "Tâche annulée, dette non payée": "violet",
        "Main d'œuvre sans tâche": "rouge",
        "Livraison sans tâche": "rouge",
        "Tâche terminée, commande non soldée": "orange",
    };
    // « Commande annulée avec tâche Tache-XXXXX » porte le nom de la tâche : la couleur
    // se résout par PRÉFIXE (miroir de commande_alertes.couleur_du_motif).
    const couleur_du_motif = (motif) =>
        LIBELLE_COULEUR[motif] ||
        (motif && motif.startsWith("Commande annulée avec tâche") ? "violet" : "orange");

    frappe.ui.form.on("Sales Order", {
        refresh(frm) {
            _poser_css();
            const $wrapper = $(frm.page.wrapper);
            const $head = $wrapper.find(".page-head").first();
            $head.removeClass(TOUTES_CLASSES);
            $wrapper.find(".so-alerte-pastille-fiche").remove();

            const motif = frm.doc.custom_anomalie;
            const retour = !!frm.doc.custom_retour_colis;
            if (!motif && !retour) return;
            // Le bandeau prend la couleur de l'anomalie ; à défaut, le bleu du
            // retour de colis (« en haut : retour de colis », décision 19/08).
            const couleur = motif ? couleur_du_motif(motif) : "bleu";
            $head.addClass(CLASSES[couleur]);

            let pastilles = "";
            if (motif) {
                pastilles += `<span class="so-alerte-pastille so-alerte-pastille-fiche ${
                    couleur_du_motif(motif)}"
                    title="${frappe.utils.escape_html(motif)}">${frappe.utils.escape_html(motif)}</span>`;
            }
            if (retour) {
                pastilles += `<span class="so-alerte-pastille so-alerte-pastille-fiche bleu"
                    title="${LIBELLE_RETOUR}">${LIBELLE_RETOUR}</span>`;
            }
            const $statut = $wrapper.find(".title-area .indicator-pill").first();
            if ($statut.length) {
                $statut.after(pastilles);
            } else {
                $wrapper.find(".title-area").first().append(pastilles);
            }
        },
    });
})();
