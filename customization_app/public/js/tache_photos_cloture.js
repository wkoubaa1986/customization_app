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

// Tout ce que la clôture exige est-il là ? Photos par champ ET position
// Google Map (Installation / Visite / Entretien / Livraison hors Aramex).
function tache_exigences_completes(ex) {
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

    const d = new frappe.ui.Dialog({
        title: __("Photos de clôture — {0}", [ex.type || ""]),
        fields: [
            { fieldtype: "HTML", fieldname: "zone" },
            { fieldtype: "Section Break", label: __("Clôture sans photos") },
            {
                fieldtype: "Password", fieldname: "code",
                label: __("Code superviseur"),
                description: __("Chaque utilisation laisse une trace nominative sur la tâche."),
            },
        ],
        primary_action_label: __("✅ Clôturer la tâche"),
        primary_action: () => {
            const complet = tache_exigences_completes(ex);
            const code = d.get_value("code");
            // ⚠️ RELIRE AVANT DE SAUVER. Les photos sont posées en base par
            // enregistrer_photo (db_set) : le formulaire, lui, garde les champs
            // chargés à l'ouverture — sauver tel quel renverrait les listes
            // périmées, écraserait les photos ET ferait rejeter la clôture.
            const cloturer = () => {
                d.hide();
                frm.reload_doc().then(() => {
                    frm.set_value("status", "Completed");
                    frm.save();
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
        const etat = `<div style="margin-top:8px;font-size:12px;color:var(--text-muted)">
            ${__("Photos avant : {0} / {1} — après : {2} / {3}", [
                ex.photos.avant, ex.minima.avant, ex.photos.apres, ex.minima.apres,
            ])}${ex.dispense ? " · 🔓 " + __("dispense active") : ""}</div>`;
        d.fields_dict.zone.$wrapper.html(lignes + ligne_gmap + etat);

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
}
