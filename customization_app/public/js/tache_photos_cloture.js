/**
 * Clôture guidée d'une Tache de travail : les photos, puis Completed.
 *
 * Remplace l'ancien hack du modal Caméra (setInterval jQuery qui bloquait le
 * bouton « Valider » sous 3 images) ET l'ancienne section « Prendre des
 * photos » (onglet masqué — les champs liste_photos_* restent le stockage).
 *
 *   - le bouton « 📷 Photos de clôture » apparaît sur les types concernés ;
 *     il est GRIS tant que les photos manquent, VERT quand les exigences sont
 *     remplies (ou dispense par code superviseur) ;
 *   - passer le statut à « Completed » ouvre automatiquement le dialogue si
 *     les photos manquent — on ne découvre pas le blocage au moment du save ;
 *   - l'enregistrement reste bloqué côté SERVEUR tant que les photos (ou le
 *     code) ne sont pas là : cloture_tache.verifier_photos_cloture ;
 *   - « 📍 Ma position » sous le lien Google Map écrit la position GPS réelle
 *     dans le champ (ou la remplace) — la synchro d'adresse ne la touche plus.
 */

const TCT_LABEL_PHOTOS = "📷 Photos de clôture";

// Tout ce que la clôture exige est-il là ? Photos par champ, position Google
// Map, commande liée et rapport d'intervention (règles du doctype). Le code
// superviseur (dispense) ne couvre QUE les preuves — photos et position — pas
// la commande ni le rapport, qui sont des données, pas des preuves.
function tache_exigences_completes(ex) {
    if (ex.commande_requise && !ex.commande) return false;
    // Commande en brouillon/annulée ou BL en brouillon : la clôture est
    // refusée côté serveur QUEL QUE SOIT le code superviseur.
    if (ex.commande_brouillon || ex.commande_annulee) return false;
    if (((ex.commande_infos || {}).bls || []).some((b) => b.brouillon)) return false;
    if (ex.rapport_requis && !ex.rapport) return false;
    if (ex.dispense) return true;
    if (ex.gmap_requis && !ex.gmap) return false;
    return ex.photos.avant >= ex.minima.avant && ex.photos.apres >= ex.minima.apres;
}

frappe.ui.form.on("Tache de travail", {
    refresh(frm) {
        tache_bouton_gps(frm);
        if (frm.is_new() || frm.doc.status === "Cancelled") return;
        tache_maj_bouton_photos(frm);
    },

    status(frm) {
        // Le technicien passe la tâche à « Terminé » : si les photos manquent,
        // le guide s'ouvre TOUT DE SUITE — pas au moment où le save échoue.
        if (frm.is_new() || frm.doc.status !== "Completed") return;
        frappe.call({
            method: "customization_app.cloture_tache.exigences",
            args: { tache: frm.doc.name },
            callback: (r) => {
                const ex = r.message || {};
                if (!ex.concerne || !ex.actif) return;
                if (tache_exigences_completes(ex)) return;
                tache_dialogue_cloture(frm, ex);
            },
        });
    },
});

function tache_maj_bouton_photos(frm) {
    frappe.call({
        method: "customization_app.cloture_tache.exigences",
        args: { tache: frm.doc.name },
        callback: (r) => {
            const ex = r.message || {};
            if (!ex.concerne || !ex.actif) return;
            const complet = tache_exigences_completes(ex);
            if (!frm.custom_buttons || !frm.custom_buttons[TCT_LABEL_PHOTOS]) {
                frm.add_custom_button(TCT_LABEL_PHOTOS, () => {
                    frappe.call({
                        method: "customization_app.cloture_tache.exigences",
                        args: { tache: frm.doc.name },
                        callback: (r2) => tache_dialogue_cloture(frm, r2.message || ex),
                    });
                });
            }
            const $btn = frm.custom_buttons[TCT_LABEL_PHOTOS];
            if ($btn) {
                // Vert = tout est là (photos ou code) : on peut enregistrer.
                $btn.toggleClass("btn-success", complet);
                $btn.toggleClass("btn-default", !complet);
                $btn.attr("title", complet
                    ? __("Photos validées — la clôture passera")
                    : __("Photos manquantes — la clôture sera refusée"));
            }
        },
    });
}

// « 📍 Ma position » sous le lien Google Map : la géolocalisation du navigateur
// écrit un lien maps.google.com/?q=lat,lng dans le champ — ou le remplace.
function tache_bouton_gps(frm) {
    const ctrl = frm.fields_dict && frm.fields_dict.google_map;
    if (!ctrl || !ctrl.$wrapper) return;
    if (ctrl.$wrapper.find(".tct-gps-btn").length) return;
    const $btn = $(
        `<button type="button" class="btn btn-xs btn-default tct-gps-btn"
            style="margin-top:4px">📍 ${__("Ma position actuelle")}</button>`
    ).appendTo(ctrl.$wrapper);
    $btn.on("click", (e) => {
        e.preventDefault();
        if (!navigator.geolocation) {
            frappe.msgprint(__("La géolocalisation n'est pas disponible sur cet appareil."));
            return;
        }
        $btn.prop("disabled", true).text("📍 " + __("Localisation…"));
        navigator.geolocation.getCurrentPosition(
            (pos) => {
                const lien = `https://maps.google.com/?q=${pos.coords.latitude},${pos.coords.longitude}`;
                frm.set_value("google_map", lien);
                $btn.prop("disabled", false).text("📍 " + __("Ma position actuelle"));
                frappe.show_alert({
                    message: __("Position enregistrée dans le lien Google Map — pensez à sauvegarder."),
                    indicator: "green",
                });
            },
            (err) => {
                $btn.prop("disabled", false).text("📍 " + __("Ma position actuelle"));
                frappe.msgprint(__("Position introuvable : {0}", [err.message || ""]));
            },
            { enableHighAccuracy: true, timeout: 15000 }
        );
    });
}

function tache_dialogue_cloture(frm, exigences) {
    const esc = frappe.utils.escape_html;
    let ex = exigences;

    // Commande liée et rapport d'intervention : les règles du DOCTYPE les
    // exigent au save — le dialogue les rend remplissables SUR PLACE plutôt
    // que de laisser le save échouer sur « champ obligatoire ».
    const champs = [{ fieldtype: "HTML", fieldname: "zone" }];
    if (ex.commande_requise && !ex.commande) {
        champs.push({
            fieldtype: "Link", fieldname: "commande", options: "Sales Order",
            label: __("Commande client liée"), reqd: 1,
            // only_select : le « Créer une nouvelle commande » NATIF du champ
            // naviguerait hors de la tâche — notre bouton ➕ ouvre en popup.
            only_select: 1,
            get_query: () => ({
                filters: ex.client
                    ? { status: ["!=", "Cancelled"], customer: ex.client }
                    : { status: ["!=", "Cancelled"] },
            }),
        });
    }
    if (ex.rapport_requis) {
        champs.push({
            fieldtype: "Small Text", fieldname: "rapport",
            label: __("Rapport d'intervention"), reqd: ex.rapport ? 0 : 1,
            default: ex.rapport_texte || "",
        });
    }
    // Sous le rapport : paiements reçus de la commande + bons de livraison.
    champs.push({ fieldtype: "HTML", fieldname: "zone_infos" });
    champs.push(
        { fieldtype: "Section Break", label: __("Clôture sans photos") },
        {
            fieldtype: "Password", fieldname: "code",
            label: __("Code superviseur"),
            description: __("Dispense des photos et de la position — jamais de la commande ni du rapport."),
        }
    );

    const d = new frappe.ui.Dialog({
        title: __("Clôture — {0}", [ex.type || ""]),
        fields: champs,
        primary_action_label: __("✅ Clôturer la tâche"),
        primary_action: (v) => {
            const commande_ok = !ex.commande_requise || ex.commande || v.commande;
            const rapport_ok = !ex.rapport_requis || ex.rapport
                || !!(v.rapport || "").trim();
            if (!commande_ok || !rapport_ok) {
                frappe.msgprint(__("La commande liée et le rapport d'intervention sont obligatoires pour ce type de tâche — le code superviseur ne les remplace pas."));
                return;
            }
            if (ex.commande_brouillon || ex.commande_annulee) {
                frappe.msgprint(__("La commande liée {0} est {1} — validez-la (bouton « Ouvrir ») avant de clôturer. Le code superviseur ne couvre pas l'état des pièces.",
                    [ex.commande, ex.commande_annulee ? __("annulée") : __("en brouillon")]));
                return;
            }
            const bls_brouillon = ((ex.commande_infos || {}).bls || []).filter((b) => b.brouillon);
            if (bls_brouillon.length) {
                frappe.msgprint(__("Bon(s) de livraison en brouillon : {0} — validez-le(s) (« Ouvrir pour valider ») avant de clôturer.",
                    [bls_brouillon.map((b) => b.bl).join(", ")]));
                return;
            }
            const complet = tache_exigences_completes({
                ...ex,
                commande: ex.commande || v.commande,
                rapport: ex.rapport || !!(v.rapport || "").trim(),
            });
            const code = d.get_value("code");
            // ⚠️ RELIRE AVANT DE SAUVER. Photos, commande et rapport sont posés
            // en base par db_set : le formulaire, lui, garde les champs chargés
            // à l'ouverture — sauver tel quel renverrait les valeurs périmées.
            const cloturer = () => {
                const enregistrer =
                    v.commande || (v.rapport || "").trim()
                        ? frappe.call({
                              method: "customization_app.cloture_tache.completer_champs",
                              args: { tache: frm.docname, commande: v.commande, rapport: v.rapport },
                          })
                        : Promise.resolve();
                enregistrer.then(() => {
                    d.hide();
                    frm.reload_doc().then(() => {
                        frm.set_value("status", "Completed");
                        frm.save();
                    });
                });
            };
            if (complet) return cloturer();
            if (!code) {
                frappe.msgprint(
                    __("Il manque des photos — ajoutez-les, ou saisissez le code superviseur.")
                );
                return;
            }
            frappe.call({
                method: "customization_app.cloture_tache.deverrouiller",
                args: { tache: frm.doc.name, code },
                callback: () => {
                    frappe.show_alert({
                        message: __("Clôture déverrouillée (trace posée sur la tâche)"),
                        indicator: "orange",
                    });
                    cloturer();
                },
            });
        },
    });

    // Le serveur compte des photos par champ, pas des sujets : les coches se
    // distribuent donc DANS L'ORDRE des prises d'un même champ — 1 photo
    // « après » sur 2 coche la première ligne, la seconde reste à faire.
    function rendre() {
        const rang = { avant: 0, apres: 0 };
        const lignes = (ex.slots || [])
            .map((s, i) => {
                const n = ex.photos[s.champ] || 0;
                const ok = n > rang[s.champ]++;
                return `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;
                        border-bottom:1px solid var(--border-color,#eee)">
                    <span style="font-size:16px">${ok ? "✅" : "📷"}</span>
                    <span style="flex:1">${esc(s.label)}</span>
                    <button class="btn btn-xs ${ok ? "btn-default" : "btn-primary"}"
                        data-slot="${i}">${__("Ajouter")}</button>
                </div>`;
            })
            .join("");
        // La position Google Map fait partie de la clôture : sa ligne vit dans
        // le même guide, avec la capture GPS en un geste.
        const ligne_gmap = ex.gmap_requis
            ? `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;
                    border-bottom:1px solid var(--border-color,#eee)">
                <span style="font-size:16px">${ex.gmap ? "✅" : "📍"}</span>
                <span style="flex:1">${__("Position Google Map")}</span>
                <button class="btn btn-xs ${ex.gmap ? "btn-default" : "btn-primary"}"
                    data-gps="1">${__("📍 Ma position")}</button>
            </div>`
            : "";
        // Commande DÉJÀ liée : affichée avec un bouton « Ouvrir » — la fiche
        // s'ouvre en popup (fenêtre partagée) pour ajuster les articles,
        // saisir un paiement, valider… sans quitter la clôture.
        const etat_commande = ex.commande_annulee
            ? ` · <span style="color:#dc2626;font-weight:600">${__("Annulée !")}</span>`
            : ex.commande_brouillon
                ? ` · <span style="color:#b45309;font-weight:600">${__("Brouillon — à valider")}</span>`
                : ` · <span style="color:#16a34a">${__("Validée")}</span>`;
        const ligne_commande = ex.commande
            ? `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;
                    border-bottom:1px solid var(--border-color,#eee)">
                <span style="font-size:16px">${ex.commande_brouillon || ex.commande_annulee ? "📋" : "✅"}</span>
                <span style="flex:1">${__("Commande liée")} :
                    <b>${esc(ex.commande)}</b>${etat_commande}</span>
                <button class="btn btn-xs ${ex.commande_brouillon ? "btn-primary" : "btn-default"}"
                    data-ouvrir-commande="1">
                    ${ex.commande_brouillon ? __("Ouvrir pour valider") : __("Ouvrir")}</button>
            </div>`
            : "";
        const etat = `<div style="margin-top:8px;font-size:12px;color:var(--text-muted)">
            ${__("Photos avant : {0} / {1} — après : {2} / {3}", [
                ex.photos.avant, ex.minima.avant, ex.photos.apres, ex.minima.apres,
            ])}${ex.dispense ? " · 🔓 " + __("dispense active") : ""}</div>`;
        d.fields_dict.zone.$wrapper.html(lignes + ligne_gmap + ligne_commande + etat);

        d.fields_dict.zone.$wrapper.find("[data-ouvrir-commande]").on("click", function () {
            frappe
                .require("/assets/customization_app/js/ouvrir_document.js")
                .then(() =>
                    customization_app.ouvrir_document("Sales Order", ex.commande, {
                        a_la_fermeture: rafraichir,
                    })
                );
        });

        // ⚠️ Les boutons « Ajouter » (photos) et « 📍 Ma position » se branchent
        // ICI, dans rendre() : ils vivent dans `zone`, pas dans `zone_infos`.
        // Les brancher dans rendre_infos() les tuait dès qu'aucune commande
        // n'était liée — son `return` anticipé (pas de commande_infos) sortait
        // AVANT les .on("click"), et tout le dialogue restait inerte
        // (régression v5.18.3, constatée sur les Entretiens en prod).
        brancher_boutons_zone();

        rendre_infos();
    }

    // Relire les exigences et repeindre tout le dialogue — après chaque popup
    // (commande, BL) : paiements, BL validé, bordereau… tout peut avoir bougé.
    function rafraichir() {
        return frappe
            .call({
                method: "customization_app.cloture_tache.exigences",
                args: { tache: frm.docname },
            })
            .then((r) => {
                ex = r.message || ex;
                rendre();
                tache_maj_bouton_photos(frm);
            });
    }

    // 💰 Paiements reçus + 🚛 bons de livraison de la commande liée, sous le
    // rapport. Un BL en BROUILLON s'ouvre en popup pour être validé avant la
    // clôture — c'est la pièce qui dit ce qui est réellement parti.
    function rendre_infos() {
        const zi = d.fields_dict.zone_infos;
        if (!zi) return;
        const ci = ex.commande_infos;
        if (!ci) {
            zi.$wrapper.html("");
            return;
        }
        const dt = (v) => format_currency(v, "TND");
        let html = "";
        if ((ci.paiements || []).length) {
            const solde = ci.total_paye >= ci.total - 0.005;
            // ⚠️ Un paiement encore sur DETTES est de l'argent que le client
            // doit toujours : il se signale en avertissement. L'attente sur
            // Livraison Aramex, elle, est NORMALE — c'est le transporteur qui
            // versera, pas le client.
            const est_dette = (p) => (p.compte || "").includes("Dettes");
            const est_aramex = (p) => (p.compte || "").includes("Livraison Aramex");
            const total_dettes = ci.paiements
                .filter(est_dette)
                .reduce((s, p) => s + (p.montant || 0), 0);
            html += `<div style="font-weight:600;margin-top:6px">💰 ${__("Paiements reçus")} —
                ${dt(ci.total_paye)} / ${dt(ci.total)} ${solde ? "✅" : `<span style="color:#b45309">(${__("reste")} ${dt(ci.total - ci.total_paye)})</span>`}</div>`;
            if (total_dettes > 0.005) {
                html += `<div style="margin:2px 0;padding:4px 8px;border-radius:6px;
                        background:#fef3c7;color:#92400e;font-weight:600">
                    ⚠️ ${__("{0} encore en dette — à encaisser auprès du client", [dt(total_dettes)])}</div>`;
            }
            html += ci.paiements
                .map((p) => {
                    const dette = est_dette(p);
                    return `<div style="font-size:12px;padding:2px 0 2px 18px;${
                        dette ? "color:#92400e;font-weight:600" : "color:var(--text-muted)"}">
                    ${esc(p.paiement)} · ${frappe.datetime.str_to_user(p.date)} · ${esc(p.mode || "")}
                    · <b>${dt(p.montant)}</b>${
                        est_aramex(p) ? " · 🚚 " + __("en attente Aramex")
                        : dette ? " · ⚠️ " + __("DETTE non encaissée") : ""}</div>`;
                })
                .join("");
        } else {
            html += `<div style="margin-top:6px;color:#b45309">💰 ${__("Aucun paiement reçu")} —
                ${__("total commande")} ${dt(ci.total)}</div>`;
        }
        (ci.bls || []).forEach((b) => {
            html += `<div style="display:flex;align-items:center;gap:8px;padding:4px 0;
                    border-top:1px solid var(--border-color,#eee);margin-top:4px">
                <span style="font-size:16px">🚛</span>
                <span style="flex:1">${esc(b.bl)} · ${dt(b.total)} ·
                    ${b.brouillon
                        ? `<span style="color:#b45309;font-weight:600">${__("Brouillon — à valider")}</span>`
                        : `<span style="color:#16a34a">${__("Validé")}</span>`}</span>
                <button class="btn btn-xs ${b.brouillon ? "btn-primary" : "btn-default"}"
                    data-ouvrir-bl="${esc(b.bl)}">${b.brouillon ? __("Ouvrir pour valider") : __("Ouvrir")}</button>
            </div>`;
        });
        zi.$wrapper.html(html);
        zi.$wrapper.find("[data-ouvrir-bl]").on("click", function () {
            const bl = $(this).attr("data-ouvrir-bl");
            frappe
                .require("/assets/customization_app/js/ouvrir_document.js")
                .then(() =>
                    customization_app.ouvrir_document("Delivery Note", bl, {
                        a_la_fermeture: rafraichir,
                    })
                );
        });
    }

    function brancher_boutons_zone() {
        d.fields_dict.zone.$wrapper.find("[data-gps]").on("click", function () {
            const $b = $(this);
            if (!navigator.geolocation) {
                frappe.msgprint(__("La géolocalisation n'est pas disponible sur cet appareil."));
                return;
            }
            $b.prop("disabled", true).text(__("Localisation…"));
            navigator.geolocation.getCurrentPosition(
                (pos) => {
                    const lien = `https://maps.google.com/?q=${pos.coords.latitude},${pos.coords.longitude}`;
                    // Écrit en base tout de suite : cloturer() relit le doc
                    // avant de sauver, la valeur du formulaire suivra.
                    frappe.db
                        .set_value(frm.doctype, frm.docname, "google_map", lien)
                        .then(() => {
                            ex.gmap = true;
                            rendre();
                            frm.reload_doc();
                            tache_maj_bouton_photos(frm);
                        });
                },
                (err) => {
                    $b.prop("disabled", false).text(__("📍 Ma position"));
                    frappe.msgprint(__("Position introuvable : {0}", [err.message || ""]));
                },
                { enableHighAccuracy: true, timeout: 15000 }
            );
        });

        d.fields_dict.zone.$wrapper.find("[data-slot]").on("click", function () {
            const slot = ex.slots[Number($(this).attr("data-slot"))];
            new frappe.ui.FileUploader({
                doctype: frm.doctype,
                docname: frm.docname,
                folder: "Home/Attachments",
                allow_multiple: !!slot.multiple,
                restrictions: { allowed_file_types: ["image/*"] },
                on_success: (file) => {
                    const fichiers = Array.isArray(file) ? file : [file];
                    Promise.all(
                        fichiers.map((f) =>
                            frappe.call({
                                method: "customization_app.cloture_tache.enregistrer_photo",
                                args: {
                                    tache: frm.docname,
                                    champ: slot.champ,
                                    file_url: f.file_url,
                                },
                            }).then((r) => {
                                if ((r.message || {}).deja) {
                                    frappe.show_alert({
                                        message: __("Cette photo est déjà enregistrée — chaque prise doit être une photo différente."),
                                        indicator: "orange",
                                    }, 6);
                                }
                            })
                        )
                    ).then(() =>
                        frappe
                            .call({
                                method: "customization_app.cloture_tache.exigences",
                                args: { tache: frm.docname },
                            })
                            .then((r) => {
                                ex = r.message || ex;
                                rendre();
                                // Garder le formulaire en phase avec la base :
                                // ses listes de photos viennent d'y changer.
                                frm.reload_doc();
                                tache_maj_bouton_photos(frm);
                            })
                    );
                },
            });
        });
    }

    rendre();
    d.show();
    tache_bouton_nouvelle_commande(d, ex);
}

// « ➕ Créer une nouvelle commande » sous le champ Commande du dialogue :
// la commande s'ouvre en POPUP (fenêtre partagée ouvrir_document, la tâche
// reste derrière), pré-remplie avec le client de la tâche et le MAGASIN de
// l'employé affecté. À la fermeture, la commande créée est reprise dans le
// champ automatiquement.
function tache_bouton_nouvelle_commande(d, ex) {
    const ctrl = d.fields_dict && d.fields_dict.commande;
    if (!ctrl || !ctrl.$wrapper) return;

    const derniere_commande = () =>
        frappe.db
            .get_list("Sales Order", {
                filters: ex.client ? { customer: ex.client } : {},
                order_by: "creation desc",
                limit: 1,
            })
            .then((r) => (r && r.length ? r[0].name : null));

    const $btn = $(
        `<button type="button" class="btn btn-xs btn-default" style="margin-top:4px">
            ➕ ${__("Créer une nouvelle commande")}</button>`
    ).appendTo(ctrl.$wrapper);

    $btn.on("click", (e) => {
        e.preventDefault();
        derniere_commande().then((avant) => {
            const qs = new URLSearchParams(ex.nouvelle_commande || {}).toString();
            frappe
                .require("/assets/customization_app/js/ouvrir_document.js")
                .then(() => {
                    const dlg = customization_app.ouvrir_document("Sales Order", "new", {
                        url: `/app/sales-order/new${qs ? "?" + qs : ""}`,
                        titre: __("Nouvelle commande — {0}", [ex.client || ""]),
                        a_la_fermeture: () =>
                            derniere_commande().then((apres) => {
                                if (apres && apres !== avant) {
                                    d.set_value("commande", apres);
                                    frappe.show_alert({
                                        message: __("Commande {0} liée à la clôture", [apres]),
                                        indicator: "green",
                                    });
                                }
                            }),
                    });
                    // Date de livraison : la cascade client du formulaire la
                    // VIDE après les paramètres d'URL. On s'accroche à l'iframe
                    // ICI (fichier versionné — ouvrir_document.js part en
                    // frappe.require SANS suffixe, donc cache navigateur 12 h)
                    // et on REPOSE la date tant qu'elle est vide : la cascade
                    // peut finir après nous, la boucle a le dernier mot.
                    const date = (ex.nouvelle_commande || {}).delivery_date;
                    if (date && dlg && dlg.$body) {
                        dlg.$body.find("iframe").on("load", function () {
                            const win = this.contentWindow;
                            let tics = 0;
                            const minuteur = setInterval(() => {
                                try {
                                    const f = win.cur_frm;
                                    if (f && f.doc && f.is_new() && !f.doc.delivery_date) {
                                        f.set_value("delivery_date", date);
                                    }
                                } catch (e) {
                                    clearInterval(minuteur);
                                }
                                if (++tics > 20) clearInterval(minuteur);
                            }, 600);
                        });
                    }
                });
        });
    });
}
