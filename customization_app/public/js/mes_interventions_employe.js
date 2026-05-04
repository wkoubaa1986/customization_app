// Guard: only register handlers for this specific doctype
if (typeof frappe !== "undefined") {

frappe.ui.form.on('Mes Interventions Employe', {
    refresh(frm) {
        if (!frm._pos_iframe_open) {
            sync_et_afficher(frm);
        }

        if (frm._sync_interventions_interval) {
            clearInterval(frm._sync_interventions_interval);
        }

        frm._sync_interventions_interval = setInterval(() => {
            if (frm._pos_iframe_open) return;
            if (frm._sync_in_progress) return;  // bloquer si traduction en cours
            if (!frm.is_dirty()) {
                sync_et_afficher(frm);
            }
        }, 30000);
    }
});

frappe.ui.form.on('Intervention', {
    photo1(frm)      { if (!cur_frm._resetting) autosave(cur_frm); },
    photo2(frm)      { if (!cur_frm._resetting) autosave(cur_frm); },
    photo3(frm)      { if (!cur_frm._resetting) autosave(cur_frm); },
    google_maps(frm) { if (!cur_frm._resetting) autosave(cur_frm); },
    remarque(frm)    { if (!cur_frm._resetting) autosave(cur_frm); },
    vente(frm)       { if (!cur_frm._resetting) autosave(cur_frm); },
    commande(frm)    { if (!cur_frm._resetting) autosave(cur_frm); },
});

function sync_et_afficher(frm) {
    if (frm._sync_in_progress) return;  // éviter les appels parallèles
    frm._sync_in_progress = true;

    // Afficher un bandeau arabe discret pendant la traduction
    if (!frm._translation_banner) {
        frm._translation_banner = $(`
            <div id="translation-banner" style="
                position:fixed;bottom:16px;left:50%;transform:translateX(-50%);
                background:#1e293b;color:#f8fafc;
                padding:8px 18px;border-radius:20px;
                font-size:13px;z-index:9999;
                display:flex;align-items:center;gap:8px;
                box-shadow:0 4px 12px rgba(0,0,0,0.25);
            ">
                <span style="animation:spin 1s linear infinite;display:inline-block;">⏳</span>
                جاري تحديث المعلومات...
            </div>
        `).appendTo('body');
    }

    frappe.call({
        method: "customization_app.api.sync_interventions",
        args: { doc_name: frm.doc.name },
        freeze: false,
        error: function() {
            // Débloquer même si le serveur renvoie une erreur
            frm._sync_in_progress = false;
            if (frm._translation_banner) {
                frm._translation_banner.remove();
                frm._translation_banner = null;
            }
        },
        callback: function(r) {
            frm._sync_in_progress = false;
            if (frm._translation_banner) {
                frm._translation_banner.remove();
                frm._translation_banner = null;
            }

            const added   = (r.message && r.message.added)   || 0;
            const removed = (r.message && r.message.removed) || 0;
            // ar_* translations retournées directement par sync_interventions
            const ar_translations = (r.message && r.message.ar_translations) || {};

            if (added > 0) {
                frappe.show_alert({
                    message: added + " nouvelle(s) tâche(s) ajoutée(s)",
                    indicator: "orange"
                });
            }
            if (removed > 0) {
                frappe.show_alert({
                    message: removed + " tâche(s) supprimée(s)",
                    indicator: "red"
                });
            }

            // Recharger le document depuis le serveur pour avoir l'état exact
            frappe.call({
                method: "frappe.client.get",
                args: { doctype: "Mes Interventions Employe", name: frm.doc.name },
                freeze: false,
                callback: function(res) {
                    if (res.message) {
                        const server_tache = res.message.tache || [];

                        // Map des lignes serveur par name (row id)
                        const server_map = {};
                        server_tache.forEach(r => { server_map[r.name] = r; });

                        // Map des lignes serveur par tache_de_travail (clé métier)
                        const server_sources = new Set(
                            server_tache.map(r => r.tache_de_travail || r.source_task).filter(Boolean)
                        );

                        // ── 1. Supprimer localement les lignes absentes du serveur ──
                        frm.doc.tache = (frm.doc.tache || []).filter(row => {
                            const source = row.tache_de_travail || row.source_task;
                            // Garder si la ligne est sur le serveur
                            if (server_map[row.name]) return true;
                            // Garder si c'est une ligne sans source (saisie manuelle)
                            if (!source) return true;
                            // Supprimer si la source n'est plus sur le serveur
                            return server_sources.has(source);
                        });

                        // ── 2. Mettre à jour les champs externes sur les lignes existantes ──
                        const existing_sources = new Set();
                        (frm.doc.tache || []).forEach(row => {
                            const srv = server_map[row.name];
                            if (srv) {
                                const client_changed = srv.client && srv.client !== row.client;
                                if (client_changed) {
                                    // Reset complet : le serveur a remplacé le client
                                    if (frm._row_snapshots) delete frm._row_snapshots[row.name];
                                    if (frm._totals_cache) {
                                        delete frm._totals_cache["v:" + row.vente];
                                        delete frm._totals_cache["c:" + row.commande];
                                    }
                                    Object.assign(row, srv);
                                } else {
                                    // Champs éditables utilisateur
                                    if (srv.commande)          row.commande          = srv.commande;
                                    if (srv.vente)             row.vente             = srv.vente;
                                    if (srv.google_maps)       row.google_maps       = srv.google_maps;
                                    if (srv.photo1)            row.photo1            = srv.photo1;
                                    if (srv.photo2)            row.photo2            = srv.photo2;
                                    if (srv.photo3)            row.photo3            = srv.photo3;
                                    if (srv.nb_appels)         row.nb_appels         = srv.nb_appels;
                                    if (srv.nb_appels_detail)  row.nb_appels_detail  = srv.nb_appels_detail;
                                    if (srv.annule != null)    row.annule            = srv.annule;
                                    // Données source
                                    row.client  = srv.client  || row.client  || "";
                                    row.adresse = srv.adresse || row.adresse || "";
                                    row.tel     = srv.tel     || row.tel     || "";
                                    row.heure   = srv.heure   || row.heure   || "";
                                    // Badge جديد
                                    row.nouvelle_tache = srv.nouvelle_tache || 0;
                                }
                            }
                            // Traductions arabes : priorité ar_translations du sync (source directe DB)
                            const ar = ar_translations[row.name] || {};
                            row.ar_client   = ar.ar_client   || (srv && srv.ar_client)   || row.ar_client   || "";
                            row.ar_adresse  = ar.ar_adresse  || (srv && srv.ar_adresse)  || row.ar_adresse  || "";
                            row.ar_remarque = ar.ar_remarque || (srv && srv.ar_remarque) || row.ar_remarque || "";
                            row.ar_sujet    = ar.ar_sujet    || (srv && srv.ar_sujet)    || row.ar_sujet    || "";
                            existing_sources.add(row.tache_de_travail || row.source_task);
                        });

                        // ── 3. Ajouter les nouvelles lignes du serveur ──
                        if (!frm.doc.tache) frm.doc.tache = [];
                        server_tache.filter(row =>
                            !existing_sources.has(row.tache_de_travail) &&
                            !existing_sources.has(row.source_task)
                        ).forEach(row => {
                            // Injecter ar_* depuis sync si disponible
                            const ar = ar_translations[row.name] || {};
                            row.ar_client   = ar.ar_client   || row.ar_client   || "";
                            row.ar_adresse  = ar.ar_adresse  || row.ar_adresse  || "";
                            row.ar_remarque = ar.ar_remarque || row.ar_remarque || "";
                            row.ar_sujet    = ar.ar_sujet    || row.ar_sujet    || "";
                            frm.doc.tache.push(row);
                            if (frm._row_snapshots) delete frm._row_snapshots[row.name];
                        });
                    }
                    afficher_cartes_taches(frm);
                }
            });
        }
    });
}

function afficher_cartes_taches(frm) {
    if (!frm.fields_dict.tache) return;

    const wrapper = frm.fields_dict.tache.$wrapper;

    wrapper.find(".grid-heading-row").hide();
    wrapper.find(".grid-body").hide();
    wrapper.find(".grid-footer").hide();
    wrapper.find(".grid-empty").hide();
    wrapper.find(".grid-add-row").hide();
    wrapper.find(".grid-remove-rows").hide();

    wrapper.find(".custom-tache-cards").remove();

    const rows = frm.doc.tache || [];

    // Initialiser le store de snapshots (état tel que construit par le script serveur)
    if (!frm._row_snapshots) frm._row_snapshots = {};

    // Lignes dont le snapshot n'est pas encore initialisé et qui ont une source task
    const rows_sans_snap = rows.filter(r => r.tache_de_travail && !frm._row_snapshots[r.name]);
    if (rows_sans_snap.length > 0) {
        const names = rows_sans_snap.map(r => r.tache_de_travail);
        frappe.call({
            method: "frappe.client.get_list",
            args: {
                doctype: "Tache de travail",
                filters: [["name", "in", names]],
                fields: ["name", "google_map", "rapport_visite", "subject", "commande_client"],
                limit: names.length,
            },
            freeze: false,
            callback: function(r) {
                const task_map = {};
                (r.message || []).forEach(t => { task_map[t.name] = t; });
                rows_sans_snap.forEach(row => {
                    const task = task_map[row.tache_de_travail] || {};
                    // Snapshot = valeurs telles que le script serveur les aurait mises
                    // google_maps et remarque viennent de Tache de travail, le reste est vide
                    frm._row_snapshots[row.name] = {
                        photo1:           "",
                        photo2:           "",
                        photo3:           "",
                        google_maps:      task.google_map       || "",
                        remarque:         task.rapport_visite   || "",
                        sujet:            task.subject          || "",
                        commande_client:  task.commande_client  || "",
                        vente:            "",
                        commande:         "",
                    };
                });
                // Re-render maintenant que les snapshots sont prêts
                afficher_cartes_taches(frm);
            }
        });
        return; // on attend le callback avant de rendre les cartes
    }

    // ── Batch-fetch totals for vente (POS Invoice) and commande (Sales Order) ──
    if (!frm._totals_cache) frm._totals_cache = {};

    const ventes_to_fetch   = rows.filter(r => r.vente       && !frm._totals_cache["v:" + r.vente]).map(r => r.vente);
    const commandes_to_fetch = rows.filter(r => {
        const cmd = r.commande || (frm._row_snapshots[r.name] && frm._row_snapshots[r.name].commande_client) || "";
        return cmd && !frm._totals_cache["c:" + cmd];
    }).map(r => r.commande || (frm._row_snapshots[r.name] && frm._row_snapshots[r.name].commande_client));

    const needs_fetch = ventes_to_fetch.length > 0 || commandes_to_fetch.length > 0;
    if (needs_fetch) {
        let pending = 0;
        const done = () => { pending--; if (pending === 0) afficher_cartes_taches(frm); };

        if (ventes_to_fetch.length > 0) {
            pending++;
            frappe.call({
                method: "frappe.client.get_list",
                args: { doctype: "POS Invoice", filters: [["name","in", ventes_to_fetch]], fields: ["name","grand_total"], limit: ventes_to_fetch.length },
                freeze: false,
                callback(r) {
                    (r.message || []).forEach(d => { frm._totals_cache["v:" + d.name] = d.grand_total; });
                    done();
                }
            });
        }
        if (commandes_to_fetch.length > 0) {
            pending++;
            frappe.call({
                method: "frappe.client.get_list",
                args: { doctype: "Sales Order", filters: [["name","in", commandes_to_fetch]], fields: ["name","grand_total"], limit: commandes_to_fetch.length },
                freeze: false,
                callback(r) {
                    (r.message || []).forEach(d => { frm._totals_cache["c:" + d.name] = d.grand_total; });
                    done();
                }
            });
        }
        return; // wait for totals before rendering
    }

    let container = $(`
        <div class="custom-tache-cards" style="
            display:flex;
            flex-direction:column;
            gap:12px;
            margin-top:12px;
        "></div>
    `);

    rows.forEach((row, index) => {
        // Snap déjà initialisé avant le rendu via Tache de travail.
        // Pour les lignes sans source task (cas rare), snap vide par défaut.
        if (!frm._row_snapshots[row.name]) {
            frm._row_snapshots[row.name] = {
                photo1: "", photo2: "", photo3: "",
                google_maps: "", remarque: "", sujet: "", commande_client: "", vente: "", commande: "",
            };
        }

        const type = (row.intervention || "").toLowerCase();

        const has_maps  = row.google_maps && row.google_maps.trim() !== "";
        const has_vente = row.vente && row.vente.trim() !== "";
        const commande_client = row.commande || (frm._row_snapshots[row.name] && frm._row_snapshots[row.name].commande_client) || "";
        const has_commande = !!(commande_client);
        const has_all_photos = !!(row.photo1 && row.photo2 && row.photo3);
        const is_finished = has_all_photos && has_maps && (has_vente || has_commande);

        const vente_total    = has_vente    ? (frm._totals_cache["v:" + row.vente]      || null) : null;
        const commande_total = has_commande ? (frm._totals_cache["c:" + commande_client] || null) : null;
        const fmt_amount = (v) => v != null ? format_currency(v, frappe.boot.sysdefaults.currency) : "";

        const client  = row.ar_client  || row.client  || "العميل غير محدد";
        const adresse = row.ar_adresse || row.adresse || "العنوان غير محدد";
        const remarque = row.ar_remarque || row.remarque || "";
        const sujet_raw = (frm._row_snapshots[row.name] && frm._row_snapshots[row.name].sujet) || "";
        const sujet = row.ar_sujet || sujet_raw;
        const heure_raw = row.heure || "";
        // Extract HH:MM from datetime string "2026-05-03 09:30:00" → "09:30"
        const heure = heure_raw.includes(" ") ? heure_raw.split(" ")[1].slice(0, 5) : heure_raw.slice(0, 5);
        const intervention_translations = {
            "entretien":    "صيانة",
            "installation": "تركيب",
            "réparation":   "تصليح",
            "reparation":   "تصليح",
            "livraison":    "توصيل",
            "visite":       "زيارة",
            "autre":        "حاجة أخرى",
        };
        const intervention_label = intervention_translations[(row.intervention || "").toLowerCase()] || row.intervention || "";

        // Vérifier si tous les numéros ont été appelés au moins une fois
        let appels_map_check = {};
        try { appels_map_check = JSON.parse(row.nb_appels_detail || "{}"); } catch(e) {}
        const phones_list = row.tel ? row.tel.split(/\r?\n/).map(p => p.trim().replace(/\s+/g,'')).filter(p => p !== '') : [];
        const all_called = phones_list.length > 0 && phones_list.every(p => (appels_map_check[p] || 0) >= 1);
        const is_annule = !!(row.annule);

        const bg_style = is_annule
            ? "background:#e5e7eb;border:1px solid #9ca3af;opacity:0.75;"
            : is_finished
                ? "background:#dcfce7;border:1px solid #86efac;"
                : row.nouvelle_tache
                    ? "background:#fff7ed;border:1px solid #fed7aa;"
                    : "background:#fff;border:1px solid #e5e7eb;";

        const annule_badge = is_annule ? `
            <span style="
                background:#6b7280;
                color:white;
                padding:2px 6px;
                border-radius:6px;
                font-size:10px;
                margin-left:6px;
            ">ملغي</span>` : "";

        const new_badge = row.nouvelle_tache ? `
            <span style="
                background:#fb923c;
                color:white;
                padding:2px 6px;
                border-radius:6px;
                font-size:10px;
                margin-left:6px;
            ">جديد</span>
        ` : "";

        let phone_buttons = "";
        // Compteur par numéro : {"0612345678": 2, "0698765432": 1}
        let appels_map = {};
        try { appels_map = JSON.parse(row.nb_appels_detail || "{}"); } catch(e) {}

        if (row.tel) {
            const phones = row.tel
                .split(/\r?\n/)
                .map(p => p.trim())
                .filter(p => p !== "");

            phones.forEach((tel) => {
                const clean_tel = tel.replace(/\s+/g, "");
                const count = appels_map[clean_tel] || 0;
                const btn_color = count > 0
                    ? "background:#22c55e;color:#fff;border-color:#16a34a;"
                    : "background:#0ea5e9;color:#fff;border-color:#0284c7;";
                const badge = count > 0
                    ? `<span style="background:rgba(0,0,0,0.2);border-radius:10px;padding:0 5px;margin-left:4px;font-size:10px;">${count}</span>`
                    : "";

                phone_buttons += `
                    <button class="btn btn-xs btn-tel" data-tel="${clean_tel}"
                        style="${btn_color}font-weight:600;">
                        📞 ${tel}${badge}
                    </button>
                `;
            });
        }

        const card = $(`
            <div class="tache-card" style="
                display:flex;
                flex-direction:column;
                gap:10px;
                padding:14px;
                ${bg_style}
                border-radius:12px;
                box-shadow:0 1px 3px rgba(0,0,0,0.06);
            ">
                <div>
                    <div style="margin-bottom:6px;">
                        <strong>
                            ${is_finished ? "👏 " : ""}${intervention_label ? intervention_label + " - " : ""}${heure ? "🕐 " + heure + " - " : ""}${client}
                            ${new_badge}
                            ${annule_badge}
                        </strong>
                    </div>

                    <div style="color:#555;font-size:13px;">
                        📍 ${adresse}
                    </div>

                    ${sujet ? `
                        <div style="margin-top:4px;font-size:13px;background:#fee2e2;color:#991b1b;padding:4px 8px;border-radius:6px;">
                            ⚠️ ${sujet}
                        </div>
                    ` : ``}

                    ${remarque ? `
                        <div style="margin-top:6px;font-size:13px;color:#92400e;">
                            📝 ${remarque}
                        </div>
                    ` : ``}

                    ${(vente_total != null || commande_total != null) ? `
                        <div style="margin-top:6px;display:flex;gap:10px;flex-wrap:wrap;">
                            ${vente_total   != null ? `<span style="background:#dcfce7;color:#166534;padding:2px 8px;border-radius:6px;font-size:13px;font-weight:600;">🧾 بيعة: ${fmt_amount(vente_total)}</span>` : ""}
                            ${commande_total != null ? `<span style="background:#e0e7ff;color:#3730a3;padding:2px 8px;border-radius:6px;font-size:13px;font-weight:600;">📦 كوموند: ${fmt_amount(commande_total)}</span>` : ""}
                        </div>
                    ` : ``}
                </div>

                <div style="display:flex;flex-direction:column;gap:6px;">
                    <div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;">
                        ${phone_buttons}

                        ${has_maps ? `<button class="btn btn-xs btn-primary btn-nav">📍 ڨوڨل مابس</button>` : ``}
                        ${!has_maps ? `<button class="btn btn-xs btn-gps" style="background:#7c3aed;color:#fff;border-color:#7c3aed;">🛰️ جي بي إس</button>` : ``}

                        ${!has_vente ? `
                            <button class="btn btn-xs btn-warning btn-cmd">🛒 بيعة</button>
                        ` : `
                            <button class="btn btn-xs btn-success btn-open-sale">✅ مباع</button>
                        `}

                        ${has_commande ? `
                            <a href="/app/sales-order/${commande_client}" target="_blank"
                               class="btn btn-xs"
                               style="background:#9ca3af;color:#fff;border-color:#9ca3af;">
                                📦 ${commande_client}
                            </a>
                        ` : ``}
                    </div>

                    <div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;">
                        <button class="btn btn-xs ${row.photo1 ? 'btn-success' : 'btn-default'} btn-photo1">📷 1</button>
                        <button class="btn btn-xs ${row.photo2 ? 'btn-success' : 'btn-default'} btn-photo2">📷 2</button>
                        <button class="btn btn-xs ${row.photo3 ? 'btn-success' : 'btn-default'} btn-photo3">📷 3</button>

                        ${all_called && !is_annule && vente_total == null ? `<button class="btn btn-xs btn-battel" style="background:#9ca3af;color:#fff;border-color:#6b7280;font-weight:600;">بطّل</button>` : ''}
                        ${is_annule ? `<button class="btn btn-xs btn-ma-battel" style="background:#22c55e;color:#fff;border-color:#16a34a;font-weight:600;">ما بطلش</button>` : ''}

                        <button class="btn btn-xs btn-danger btn-reset" title="Réinitialiser cette carte">🗑️ ريسات</button>
                    </div>
                </div>
            </div>
        `);

        container.append(card);

        // ── Boutons téléphone : compteur d'appels par numéro ──────────────────
        card.find(".btn-tel").on("click", function(e) {
            e.preventDefault();
            const tel = $(this).data("tel");

            // Lire le map actuel et incrémenter ce numéro
            let map = {};
            try { map = JSON.parse(row.nb_appels_detail || "{}"); } catch(e) {}
            map[tel] = (map[tel] || 0) + 1;
            const detail_json = JSON.stringify(map);

            // Sauvegarder nb_appels_detail et nb_appels (total)
            const total = Object.values(map).reduce((a, b) => a + b, 0);
            frappe.model.set_value(row.doctype, row.name, "nb_appels_detail", detail_json);
            frappe.model.set_value(row.doctype, row.name, "nb_appels", total);
            frm.dirty();
            autosave(frm);

            // Ouvrir l'appel
            window.location.href = "tel:" + tel;

            // Re-rendre pour mettre à jour couleur et badge
            setTimeout(() => afficher_cartes_taches(frm), 600);
        });

        card.find(".btn-battel").on("click", function(e) {
            e.preventDefault();
            frappe.model.set_value(row.doctype, row.name, "annule", 1);
            frm.dirty();
            autosave(frm);
            setTimeout(() => afficher_cartes_taches(frm), 400);
        });

        card.find(".btn-ma-battel").on("click", function(e) {
            e.preventDefault();
            frappe.model.set_value(row.doctype, row.name, "annule", 0);
            frm.dirty();
            autosave(frm);
            setTimeout(() => afficher_cartes_taches(frm), 400);
        });

        card.find(".btn-nav").on("click", function(e) {
            e.preventDefault();
            if (row.google_maps) window.open(row.google_maps, "_blank");
            else frappe.msgprint("Aucun lien Google Maps.");
        });

        card.find(".btn-gps").on("click", function(e) {
            e.preventDefault();

            if (!navigator.geolocation) {
                frappe.msgprint("جي بي إس غير مدعوم.");
                return;
            }

            navigator.geolocation.getCurrentPosition(function(pos) {
                const link = `https://www.google.com/maps?q=${pos.coords.latitude},${pos.coords.longitude}`;

                frappe.model.set_value(row.doctype, row.name, "google_maps", link);

                frm.dirty();
                frm.refresh_field("tache");

                autosave(frm);

                frappe.msgprint("تم حفظ جي بي إس.");
                setTimeout(() => afficher_cartes_taches(frm), 400);
            });
        });

        card.find(".btn-cmd").on("click", function(e) {
            e.preventDefault();

            if (!row.client) {
                frappe.msgprint("Aucun client défini.");
                return;
            }

            let item_code = "";

            if (type.includes("entretien")) item_code = "M-E-OD";
            else if (type.includes("installation")) item_code = "M-I-OD";
            else if (type.includes("réparation") || type.includes("reparation")) item_code = "M-R-OD";

            localStorage.setItem("pos_customer", row.client);
            localStorage.setItem("pos_source_parent", frm.doc.name);
            localStorage.setItem("pos_source_child", row.name);
            localStorage.removeItem("pos_item_code");
            localStorage.setItem("pos_from_intervention", "1");

            autosave(frm);

            // Toggle inline POS below this card
            let container_id = "pos-inline-" + row.name.replace(/[^a-zA-Z0-9]/g, "-");
            let existing = $("#" + container_id);
            if (existing.length) {
                existing.remove();
                // Réafficher toutes les cartes et reprendre le refresh
                $(".tache-card").show();
                frm._pos_iframe_open = false;
                return;
            }

            // Cacher toutes les autres cartes
            $(".tache-card").not(card[0]).hide();
            frm._pos_iframe_open = true;

            let iframe_el = $(`<iframe src="/app/point-of-sale" style="width:100%; height:680px; border:none; display:block;"></iframe>`);

            let pos_container = $(`
                <div id="${container_id}" style="margin-top:10px; border:2px solid #cbd5e0; border-radius:8px; overflow:hidden;">
                    <div style="display:flex; align-items:center; background:#2d3748; padding:8px 16px;">
                        <span style="color:white; font-weight:bold; flex:1;">Point de vente — ${row.client}</span>
                        <button class="pos-inline-close" style="background:#e74c3c; color:white; border:none; padding:4px 14px; border-radius:4px; cursor:pointer; font-size:13px;">✕ Fermer</button>
                    </div>
                </div>
            `);

            pos_container.append(iframe_el);
            card.after(pos_container);

            pos_container.find(".pos-inline-close").on("click", function () {
                pos_container.remove();
                // Réafficher toutes les cartes et reprendre le refresh
                $(".tache-card").show();
                frm._pos_iframe_open = false;
                // Recharger pour récupérer le n° de vente créé
                setTimeout(() => sync_et_afficher(frm), 500);
            });

            pos_container[0].scrollIntoView({ behavior: "smooth", block: "start" });
        });

        card.find(".btn-open-sale").on("click", function(e) {
            e.preventDefault();
            if (row.vente) window.open(`/app/pos-invoice/${row.vente}`, "_blank");
        });

        card.find(".btn-photo1").on("click", function(e) {
            e.preventDefault();
            upload_image(frm, row, "photo1", $(this));
        });

        card.find(".btn-photo2").on("click", function(e) {
            e.preventDefault();
            upload_image(frm, row, "photo2", $(this));
        });

        card.find(".btn-photo3").on("click", function(e) {
            e.preventDefault();
            upload_image(frm, row, "photo3", $(this));
        });

        card.find(".btn-reset").on("click", function(e) {
            e.preventDefault();

            frappe.confirm(
                `Réinitialiser complètement la carte <b>${row.client}</b> ?<br>
                <small>GPS, photos et vente associée seront supprimés.</small>`,
                function () {
                    reset_card(frm, row);
                }
            );
        });
    });

    wrapper.append(container);
}

function upload_image(frm, row, fieldname, btn) {
    let input = document.createElement("input");

    input.type = "file";
    input.accept = "image/*";
    input.capture = "environment";
    input.style.display = "none";

    document.body.appendChild(input);

    input.addEventListener("change", function () {
        let file = input.files && input.files[0];

        if (!file) {
            document.body.removeChild(input);
            return;
        }

        let form_data = new FormData();
        form_data.append("file", file);
        form_data.append("doctype", row.doctype);
        form_data.append("docname", row.name);
        form_data.append("fieldname", fieldname);
        form_data.append("is_private", "0");

        frappe.show_alert({
            message: "Upload image...",
            indicator: "blue"
        });

        $.ajax({
            url: "/api/method/upload_file",
            type: "POST",
            data: form_data,
            processData: false,
            contentType: false,
            headers: {
                "X-Frappe-CSRF-Token": frappe.csrf_token
            },
            success: function (r) {
                if (r.message && r.message.file_url) {
                    frappe.model.set_value(row.doctype, row.name, fieldname, r.message.file_url);

                    if (btn) {
                        btn.removeClass("btn-default").addClass("btn-success");
                    }

                    frm.dirty();
                    frm.refresh_field("tache");

                    autosave(frm);

                    frappe.show_alert({
                        message: "Image ajoutée",
                        indicator: "green"
                    });

                    setTimeout(() => afficher_cartes_taches(frm), 400);
                }
            },
            error: function () {
                frappe.msgprint("Erreur upload image.");
            },
            complete: function () {
                document.body.removeChild(input);
            }
        });
    });

    input.click();
}

function reset_card(frm, row) {
    // Toujours utiliser la ligne live depuis frm.doc pour éviter les closures stale
    const live_row = (frm.doc.tache || []).find(r => r.name === row.name);
    if (!live_row) return;

    const snapshot  = (frm._row_snapshots || {})[live_row.name] || {};
    const async_tasks = [];
    // Champs à remettre à la valeur snapshot (appliqués tous ensemble après les tâches async)
    const field_resets = {};

    // POS Invoice : supprimer seulement si l'utilisateur l'a créée
    const snap_vente = snapshot.vente || "";
    const cur_vente  = live_row.vente || "";
    if (cur_vente && cur_vente !== snap_vente) {
        field_resets["vente"] = snap_vente;
        async_tasks.push(new Promise(resolve => {
            frappe.call({
                method: "customization_app.api.delete_pos_invoice",
                args: { name: cur_vente },
                freeze: false,
                callback: resolve,
                error:    resolve,   // résoudre même en cas d'erreur pour ne pas bloquer le reset
            });
        }));
    }

    // Photos : supprimer le fichier seulement si l'utilisateur l'a ajouté
    ["photo1", "photo2", "photo3"].forEach(field => {
        const snap_val = snapshot[field] || "";
        const cur_val  = live_row[field] || "";
        if (cur_val && cur_val !== snap_val) {
            field_resets[field] = snap_val;
            async_tasks.push(new Promise(resolve => {
                frappe.call({
                    method: "frappe.client.get_list",
                    args: { doctype: "File", filters: { file_url: cur_val }, fields: ["name"], limit: 1 },
                    freeze: false,
                    callback: r => {
                        const files = r.message || [];
                        if (!files.length) return resolve();
                        frappe.call({
                            method: "frappe.client.delete",
                            args: { doctype: "File", name: files[0].name },
                            freeze: false,
                            callback: resolve,
                            error:    resolve,
                        });
                    },
                    error: resolve,
                });
            }));
        }
    });

    // GPS, remarque, commande : comparaison directe avec snapshot
    const snap_gps = snapshot.google_maps || "";
    if ((live_row.google_maps || "") !== snap_gps) field_resets["google_maps"] = snap_gps;

    const snap_rem = snapshot.remarque || "";
    if ((live_row.remarque || "") !== snap_rem) field_resets["remarque"] = snap_rem;

    const snap_cmd = snapshot.commande || "";
    if ((live_row.commande || "") !== snap_cmd) field_resets["commande"] = snap_cmd;

    if (async_tasks.length === 0 && Object.keys(field_resets).length === 0) {
        frappe.show_alert({ message: "Aucune modification à réinitialiser", indicator: "blue" });
        return;
    }

    // Bloquer l'autosave des triggers de champs pendant le reset
    frm._resetting = true;

    Promise.all(async_tasks).then(() => {
        // Appliquer tous les resets d'un coup (sans déclencher d'autosave intermédiaire)
        Object.entries(field_resets).forEach(([field, val]) => {
            frappe.model.set_value(live_row.doctype, live_row.name, field, val);
        });
    }).finally(() => {
        frm._resetting = false;
        // Supprimer le snapshot → prochain affichage le recalcule depuis Tache de travail
        if (frm._row_snapshots) delete frm._row_snapshots[live_row.name];
        frm.dirty();
        frm.refresh_field("tache");
        autosave(frm);
        frappe.show_alert({ message: "Carte réinitialisée", indicator: "green" });
        setTimeout(() => afficher_cartes_taches(frm), 800);
    });
}

function autosave(frm) {
    if (frm._autosaving) return;

    if (frm.is_dirty()) {
        frm._autosaving = true;

        frm.save()
            .then(() => {
                frappe.show_alert({
                    message: "Sauvegardé automatiquement",
                    indicator: "green"
                });
            })
            .finally(() => {
                frm._autosaving = false;
            });
    }
}

} // end frappe guard
