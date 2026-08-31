/**
 * Prendre rendez-vous depuis une commande client.
 *
 * DEUX ENTRÉES, UNE SEULE MÉCANIQUE
 * ----------------------------------
 * - Sur la FICHE : un bouton rouge au bout de la barre d'onglets, après « Connexions ». Il ouvre
 *   le calendrier en vue semaine, et le créneau choisi ouvre la tâche de travail avec le client
 *   ET le type d'intervention déjà posés.
 * - Sur la LISTE : le même calendrier, sans client — pour caler un rendez-vous sans partir d'une
 *   commande précise.
 *
 * ⚠️ RIEN N'EST RÉÉCRIT ICI. Le calendrier plein écran, le forçage de la vue semaine, la capture
 * du créneau et le dialogue de tâche existent déjà dans `calendrier_rdv_button.js` — éprouvés.
 * Ce fichier ne fait que les appeler avec le bon préremplissage : `rdvLibre_openOverlay` lit
 * `rdv_prefill` dans le localStorage au moment de s'ouvrir, et l'efface aussitôt.
 *
 * ⚠️ LE TYPE D'INTERVENTION VIENT DU SERVEUR, PAS DE L'ÉCRAN. Livraison ou Installation se
 * décide sur le GROUPE D'ARTICLE des lignes, et cette règle est déjà celle qui produit les
 * anomalies « Livraison sans tâche » et « Main d'œuvre sans tâche » de la liste. La relire ici en
 * JavaScript, c'est se garantir qu'un jour l'écran proposera une livraison pendant que la liste
 * réclamera une pose. Voir `customization_app.api.rdv_depuis_commande`.
 */

frappe.provide("frappe.views");

(function () {
    const CLASSE_ITEM = "so-rdv-nav-item";

    function poser_css() {
        if (document.getElementById("so-rdv-css")) return;
        const style = document.createElement("style");
        style.id = "so-rdv-css";
        // `margin-left:auto` colle le bouton au bout de la barre : la liste des onglets est un
        // flex, il se place donc APRÈS « Connexions » quel que soit le nombre d'onglets — et il
        // suit si un onglet est ajouté demain.
        style.textContent = `
            .${CLASSE_ITEM} { margin-left: auto; display: flex; align-items: center;
                              gap: 6px; padding-right: 8px; }
            .so-rdv-app { background: #0ea5e9; }
            .so-rdv-app:hover { background: #0284c7; }
            .so-rdv-btn { background: #dc2626; color: #fff; border: none; border-radius: 6px;
                          padding: 4px 12px; font-size: 12px; font-weight: 600; cursor: pointer;
                          white-space: nowrap; }
            .so-rdv-btn:hover { background: #b91c1c; }
            .so-rdv-btn[disabled] { opacity: .6; cursor: progress; }
        `;
        document.head.appendChild(style);
    }

    /** Ouvre le calendrier, préremplissage compris. `commande` peut être nul : on cale alors un
     *  rendez-vous sans partir d'une commande, et le dialogue demandera le client. */
    function ouvrir_calendrier(commande, $btn) {
        if (typeof window.rdvLibre_openOverlay !== "function") {
            frappe.msgprint(__("Le calendrier de prise de rendez-vous n'est pas chargé. Rechargez la page."));
            return;
        }
        if (!commande) {
            localStorage.removeItem("rdv_prefill");
            window.rdvLibre_openOverlay();
            return;
        }
        if ($btn) $btn.prop("disabled", true);
        frappe.call({
            method: "customization_app.api.rdv_depuis_commande",
            args: { sales_order: commande },
            callback: (r) => {
                const m = r.message || {};
                // Les clés sont celles que `openRdvDialog` attend : `_addresses` et `secteur`
                // alimentent la liste d'adresses sans deuxième aller-retour.
                localStorage.setItem(
                    "rdv_prefill",
                    JSON.stringify({
                        custom_client: m.customer,
                        custom_type_dintervention: m.type_intervention || "",
                        // ⚠️ LA COMMANDE VOYAGE AVEC LE RENDEZ-VOUS. Sans elle, la tâche naît
                        // orpheline : la commande garderait son anomalie « sans tâche » alors que
                        // l'intervention est planifiée, et personne ne saurait, devant la tâche,
                        // ce qu'il faut livrer ou poser.
                        sales_order: m.sales_order,
                        secteur: m.secteur || "",
                        _addresses: m.addresses || [],
                    })
                );
                window.rdvLibre_openOverlay();
                // Dire ce qui a été déduit, et sur quoi. Un type posé en silence se découvre le
                // jour où le technicien se présente pour une livraison au lieu d'une pose.
                if (m.type_intervention) {
                    frappe.show_alert(
                        {
                            message: __("Intervention proposée : {0} (ligne « {1} » sur la commande)", [
                                m.type_intervention,
                                m.motif,
                            ]),
                            indicator: "blue",
                        },
                        7
                    );
                } else {
                    frappe.show_alert(
                        {
                            message: __("Ni livraison ni main d'œuvre sur cette commande : choisissez le type."),
                            indicator: "orange",
                        },
                        7
                    );
                }
            },
            always: () => $btn && $btn.prop("disabled", false),
        });
    }

    /* ── Ouvrir l'app de prise de rendez-vous À LA PLACE du client ───────────── */
    //
    // POURQUOI UN SECOND BOUTON, ET NON UN REMPLACEMENT. Les deux ne font pas le même
    // travail. Le calendrier (« 📅 Prendre RDV ») pose une tâche où l'on veut, à l'heure que
    // l'on veut : c'est l'outil du magasin quand il DÉCIDE. L'app, elle, applique les règles
    // d'organisation du portail — secteur de l'adresse, capacité de la demi-journée,
    // battements, quota lointain, date d'ouverture du type. Prendre le rendez-vous au
    // téléphone « comme le ferait le client » est le seul moyen qu'un créneau promis à
    // l'oral vaille exactement un créneau pris en ligne.
    //
    // ⚠️ L'ONGLET S'OUVRE AU CLIC, PAS DANS LA RÉPONSE. Un `window.open` déclenché depuis un
    // rappel asynchrone n'est plus rattaché au geste de l'utilisateur : les navigateurs le
    // bloquent comme une pop-up. On ouvre donc l'onglet tout de suite, et on l'aiguille
    // quand le serveur a rendu le lien.
    // Le serveur refuse de toute façon, mais un bouton qui ne peut qu'échouer n'a rien à faire
    // sous les yeux de qui n'a pas le droit : le magasin pose des rendez-vous, la caisse non.
    const peut_poser_rdv = () =>
        frappe.model.can_create("Tache de travail") && frappe.model.can_read("Customer");

    function ouvrir_app(commande, $btn) {
        const onglet = window.open("about:blank", "_blank");
        if ($btn) $btn.prop("disabled", true);
        frappe.call({
            method: "customization_app.portail_rdv_agent.ouvrir",
            args: { commande: commande || null },
            callback: (r) => {
                const url = (r.message || {}).url;
                if (!url) return;
                if (onglet && !onglet.closed) onglet.location = url;
                else window.location.href = url;
            },
            error: () => onglet && onglet.close(),
            always: () => $btn && $btn.prop("disabled", false),
        });
    }

    /* ── Bouton de la fiche, au bout de la barre d'onglets ───────────────────── */
    function poser_bouton(frm) {
        // Une commande jamais enregistrée n'a pas de nom : il n'y a rien à interroger côté
        // serveur, et le client peut encore changer.
        if (frm.is_new()) return;
        poser_css();
        const $tabs = frm.$wrapper.find("ul.form-tabs").first();
        if (!$tabs.length) {
            // ⚠️ REPLI, PAS ABANDON. Sans barre d'onglets — mise en page sans onglets, ou classe
            // renommée par une version de Frappe — le bouton disparaîtrait sans que rien ne le
            // dise, et la fonction serait réputée cassée alors qu'elle marche. Il reprend alors
            // sa place ordinaire dans la barre d'actions.
            frm.add_custom_button(__("📅 Prendre RDV"), () => ouvrir_calendrier(frm.doc.name));
            if (peut_poser_rdv()) {
                frm.add_custom_button(__("📱 RDV avec l'app"), () => ouvrir_app(frm.doc.name));
            }
            return;
        }
        $tabs.find("." + CLASSE_ITEM).remove(); // un seul, même après plusieurs rendus
        const $btn = $(
            `<button type="button" class="so-rdv-btn">📅 ${__("Prendre RDV")}</button>`
        ).on("click", () => ouvrir_calendrier(frm.doc.name, $btn));
        // Le client est déjà connu : l'app s'ouvre directement sur SA fiche, sans code SMS,
        // et présélectionne cette commande dès qu'une installation ou une livraison est
        // choisie.
        const $item = $(`<li class="nav-item ${CLASSE_ITEM}"></li>`).append($btn);
        if (peut_poser_rdv()) {
            const $app = $(
                `<button type="button" class="so-rdv-btn so-rdv-app"
                         title="${__("Ouvrir l'application de rendez-vous pour ce client, sans code SMS")}"
                 >📱 ${__("RDV avec l'app")}</button>`
            ).on("click", () => ouvrir_app(frm.doc.name, $app));
            $item.append($app);
        }
        $item.appendTo($tabs);
    }

    frappe.ui.form.on("Sales Order", {
        // `refresh` seul ne suffit pas : au premier affichage, les onglets sont dessinés APRÈS
        // lui et le bouton se posait dans le vide.
        refresh: poser_bouton,
        onload_post_render: poser_bouton,
    });

    /* ── Bouton de la vue liste ──────────────────────────────────────────────── */
    // ⚠️ PAR LE PROTOTYPE, PAS PAR `frappe.listview_settings`. woocommerce_fusion réassigne cet
    // objet en entier et son fichier est concaténé après le nôtre : la déclaration serait
    // silencieusement écrasée. Même raison que `sales_order_list_alertes.js`.
    const _after_render = frappe.views.ListView.prototype.after_render;
    frappe.views.ListView.prototype.after_render = function () {
        _after_render.apply(this, arguments);
        if (this.doctype !== "Sales Order" || this._so_rdv_bouton) return;
        try {
            this._so_rdv_bouton = true;
            this.page.add_inner_button(__("📅 Calendrier des tâches"), () =>
                ouvrir_calendrier(null)
            );
            // Sans commande de départ : l'app s'ouvre sur une recherche de client (nom ou
            // téléphone), puis déroule le parcours normal.
            if (peut_poser_rdv()) {
                this.page.add_inner_button(__("📱 RDV avec l'app"), () => ouvrir_app(null));
            }
        } catch (e) {
            this._so_rdv_bouton = false;
            console.error("Bouton calendrier des commandes :", e);
        }
    };
})();
