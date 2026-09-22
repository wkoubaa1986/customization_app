// Le dialogue de création d'un bordereau Aramex — UN SEUL, partagé par la fiche commande
// (sales_order_aramex.js) et par « Ma journée » (ma_journee.js). Chargé partout dans le Desk
// (app_include_js) : les deux écrans l'appellent avec leurs propres méthodes serveur.
//
//   window.aramex_dialogue_creation({
//       titre:    "WEB1-008515",
//       preparer: { method: "…preparer", args: { commande } },   // l'appel à blanc
//       creer:    { method: "…creer_bordereau", args: { commande } },
//       on_success: (m) => …,
//   });
//
// La méthode `preparer` rend destinataire / villes / colis / total / avances / motifs /
// paiement_aramex / poser_paiement / adresse_de_facturation / garde_fou_dev ; la méthode
// `creer` reçoit en plus destinataire, colis et poser_paiement.
//
// ⚠️ Le contre-remboursement est en ESPÈCES chez Aramex : la case « Chèque autorisé » est
// décochée par défaut et se coche en connaissance de cause.
(function () {
    // Aramex n'accepte que SES villes (308 pour la Tunisie, à sa graphie). La ville de la
    // commande est rapprochée automatiquement ; la note dit comment, pour que l'employé
    // confirme en connaissance de cause quand le rapprochement n'est pas certain.
    function note_ville(dst) {
        const esc = frappe.utils.escape_html;
        const c = dst.ville_certitude;
        const commande = dst.ville_commande || "";
        if (!commande) return __("Aucune ville sur l'adresse : choisissez-la dans la liste.");
        if (c === "exacte") return __("Ville de la commande reconnue par Aramex.");
        if (c === "alias" || c === "contenue") {
            return __("Ville de la commande « {0} » → Aramex : « {1} ».", [esc(commande), esc(dst.ville || "")]);
        }
        if (c === "approchee") {
            return __("⚠️ « {0} » inconnue d'Aramex, ville la plus proche proposée : « {1} ». Vérifiez-la.{2}",
                [esc(commande), esc(dst.ville || ""),
                 (dst.ville_candidats || []).length ? " " + __("Autres : {0}", [dst.ville_candidats.map(esc).join(", ")]) : ""]);
        }
        if (c === "gouvernorat") {
            return __("⚠️ « {0} » inconnue d'Aramex : chef-lieu « {1} » proposé. Choisissez une ville plus précise si elle existe.",
                [esc(commande), esc(dst.ville || "")]);
        }
        return __("⛔ « {0} » inconnue d'Aramex et aucune proposition : choisissez la ville dans la liste.", [esc(commande)]);
    }

    window.aramex_dialogue_creation = function (opts) {
        const esc = frappe.utils.escape_html;
        frappe.call({
            method: opts.preparer.method,
            args: opts.preparer.args,
            freeze: true,
            freeze_message: __("Lecture de la commande…"),
            callback: (r) => {
                const p = r.message || {};
                const dst = p.destinataire || {};
                const colis = p.colis || {};
                // Les autres adresses de la fiche client : en choisir une remplit l'adresse, la
                // ville (déjà mise en correspondance Aramex par le serveur), le gouvernorat et
                // le code postal — pour le COLIS seulement, la commande n'est pas touchée.
                const adresses = p.adresses || [];
                const choix_adresse = adresses.length > 1 ? [{
                    fieldtype: "Select", fieldname: "adresse_client", label: __("Adresse du client"),
                    options: adresses.map((a) => ({ label: a.libelle, value: a.name })),
                    default: (adresses.find((a) => a.courante) || adresses[0]).name,
                    description: __("{0} adresses sur la fiche. Ne change que le colis, pas la commande.", [adresses.length]),
                    onchange: function () {
                        const a = adresses.find((x) => x.name === d.get_value("adresse_client"));
                        if (!a) return;
                        d.set_value("adresse", a.adresse);
                        d.set_value("ville", a.ville);
                        d.set_value("gouvernorat", a.gouvernorat);
                        d.set_value("code_postal", a.code_postal);
                        d.set_df_property("ville", "description", note_ville(a));
                    },
                }] : [];
                const d = new frappe.ui.Dialog({
                    title: __("Créer le bordereau Aramex — {0}", [opts.titre || ""]),
                    size: "large",
                    fields: [
                        { fieldtype: "HTML", fieldname: "avertissements" },
                        { fieldtype: "Section Break", label: __("Destinataire") },
                        { fieldtype: "Data", fieldname: "nom", label: __("Nom"), default: dst.nom, reqd: 1 },
                        { fieldtype: "Data", fieldname: "telephone", label: __("Téléphone (8 chiffres)"), default: dst.telephone, reqd: 1,
                          description: __("Celui que le livreur appelle. Pré-rempli depuis la commande, sinon la fiche client (Liste Telephone).") },
                        { fieldtype: "Data", fieldname: "telephone2", label: __("Téléphone 2 (repli, facultatif)"), default: dst.telephone2,
                          description: __("Transmis à Aramex en second numéro.") },
                        { fieldtype: "Data", fieldname: "email", label: __("E-mail"), default: dst.email },
                        { fieldtype: "Column Break" },
                        ...choix_adresse,
                        { fieldtype: "Small Text", fieldname: "adresse", label: __("Adresse"), default: dst.adresse, reqd: 1 },
                        { fieldtype: "Autocomplete", fieldname: "ville", label: __("Ville (liste Aramex)"), default: dst.ville, reqd: 1,
                          options: p.villes || [], description: note_ville(dst) },
                        { fieldtype: "Data", fieldname: "gouvernorat", label: __("Gouvernorat"), default: dst.gouvernorat },
                        { fieldtype: "Data", fieldname: "code_postal", label: __("Code postal"), default: dst.code_postal },
                        { fieldtype: "Section Break", label: __("Contre-remboursement") },
                        { fieldtype: "Currency", fieldname: "cod", label: __("Montant à encaisser (TND)"), default: colis.cod,
                          description: __("Reste à payer de la commande : total {0} − déjà payé {1}. Aramex encaisse en espèces.",
                              [format_currency(p.total || 0, "TND"), format_currency(p.avances || 0, "TND")]) },
                        { fieldtype: "Check", fieldname: "cheque_autorise", label: __("Chèque autorisé pour ce contre-remboursement"), default: colis.cheque_autorise ? 1 : 0 },
                        { fieldtype: "Column Break" },
                        { fieldtype: "Check", fieldname: "poser_paiement", label: __("Poser le paiement d'attente Aramex (Dette non payée, « Aramex N: … »)"),
                          default: p.poser_paiement ? 1 : 0,
                          description: p.brouillon
                              ? __("Commande en brouillon : elle sera VALIDÉE d'abord (son échéancier pose le paiement d'attente), puis le bordereau créé et aligné dessus.")
                              : p.paiement_aramex
                                  ? __("Déjà posé : {0} (son libellé sera aligné sur le nouveau numéro).", [esc(p.paiement_aramex)])
                                  : __("Trace de l'argent détenu par Aramex jusqu'à sa remise.") },
                        { fieldtype: "Section Break", label: __("Colis") },
                        { fieldtype: "Float", fieldname: "poids", label: __("Poids (kg)"), default: colis.poids, reqd: 1 },
                        { fieldtype: "Int", fieldname: "pieces", label: __("Nombre de pièces"), default: colis.pieces, reqd: 1 },
                        { fieldtype: "Column Break" },
                        { fieldtype: "Data", fieldname: "description", label: __("Description des marchandises"), default: colis.description },
                    ],
                    primary_action_label: __("Créer le bordereau"),
                    primary_action: (v) => {
                        // Seuls les motifs BLOQUANTS arrêtent ici ; ce qui se corrige dans le
                        // formulaire (téléphone, adresse, ville…) est revalidé par le serveur sur
                        // la saisie — sinon un numéro tapé à la main restait ignoré (5.86.1).
                        if ((p.motifs || []).length) {
                            frappe.msgprint({ title: __("Création impossible"), indicator: "red", message: p.motifs.map(esc).join("<br>") });
                            return;
                        }
                        const cod = v.cod || 0;
                        const pieces = parseInt(v.pieces, 10) || 1;
                        frappe.confirm(
                            __("Créer un vrai colis chez Aramex pour <b>{0}</b>, contre-remboursement <b>{1}</b> ({2}) ?",
                                [esc(v.nom), format_currency(cod, "TND"),
                                 cod > 0 ? (v.cheque_autorise ? __("chèque autorisé") : __("espèces")) : __("rien à encaisser, commande déjà réglée")])
                            // Le nombre de pièces est ce qu'on oublie le plus en enchaînant les colis :
                            // il est répété ici, avec le nombre d'étiquettes qu'il produira.
                            + `<br>${pieces > 1
                                ? __("<b>{0} pièces</b> → un seul bordereau, {0} étiquettes (Pièce 1/{0} … {0}/{0}).", [pieces])
                                : __("<b>1 pièce</b> → une étiquette. Plusieurs cartons ? Corrigez « Nombre de pièces » avant.")}`
                            + (p.brouillon ? `<br><b>${__("La commande sera validée d'abord.")}</b>` : ""),
                            () => {
                                frappe.call({
                                    method: opts.creer.method,
                                    args: Object.assign({}, opts.creer.args, {
                                        destinataire: { nom: v.nom, telephone: v.telephone, telephone2: v.telephone2, email: v.email, adresse: v.adresse,
                                                        ville: v.ville, gouvernorat: v.gouvernorat, code_postal: v.code_postal },
                                        colis: { cod: v.cod, cheque_autorise: v.cheque_autorise ? 1 : 0, poids: v.poids,
                                                 pieces: v.pieces, description: v.description },
                                        poser_paiement: v.poser_paiement ? 1 : 0,
                                    }),
                                    freeze: true,
                                    freeze_message: __("Création du colis chez Aramex…"),
                                    callback: (rc) => {
                                        d.hide();
                                        const m = rc.message || {};
                                        const lien = m.etiquette
                                            ? `<a href="${esc(m.etiquette)}" target="_blank">📄 ${__("Ouvrir l'étiquette")}</a>`
                                            : (m.etiquette_url
                                                ? `<a href="${esc(m.etiquette_url)}" target="_blank">📄 ${__("Étiquette (lien Aramex)")}</a>`
                                                : __("étiquette non récupérée — bouton « Étiquette » pour la redemander"));
                                        frappe.msgprint({
                                            title: __("Bordereau créé"), indicator: "green",
                                            message: __("Bordereau <b>{0}</b> — contre-remboursement {1}.<br>{2}{3}",
                                                [esc(m.bordereau), format_currency(m.cod || 0, "TND"), lien,
                                                 m.payment_entry ? `<br>${__("Paiement d'attente")} ${esc(m.payment_entry)}` : ""]),
                                        });
                                        if (opts.on_success) opts.on_success(m);
                                    },
                                });
                            }
                        );
                    },
                });
                let html = "";
                if ((p.motifs || []).length) {
                    html += `<div style="padding:8px 10px;border-radius:6px;background:#fee2e2;color:#991b1b;margin-bottom:8px">
                        ⛔ ${p.motifs.map(esc).join("<br>")}</div>`;
                }
                if ((p.a_corriger || []).length) {
                    html += `<div style="padding:8px 10px;border-radius:6px;background:#ffedd5;color:#9a3412;margin-bottom:8px">
                        ✏️ ${__("À corriger dans ce formulaire avant de créer :")}<br>${p.a_corriger.map(esc).join("<br>")}</div>`;
                }
                if (!(colis.cod > 0)) {
                    html += `<div style="padding:6px 10px;border-radius:6px;background:#dcfce7;color:#166534;margin-bottom:8px">
                        ✅ ${__("Commande déjà réglée : le colis partira sans contre-remboursement.")}</div>`;
                }
                if (p.brouillon) {
                    html += `<div style="padding:6px 10px;border-radius:6px;background:#e0f2fe;color:#075985;margin-bottom:8px">
                        ℹ️ ${__("Commande en brouillon : « Créer le bordereau » la valide d'abord, puis demande le numéro à Aramex.")}</div>`;
                }
                if (p.adresse_de_facturation) {
                    html += `<div style="padding:6px 10px;border-radius:6px;background:#fef3c7;color:#92400e;margin-bottom:8px">
                        ⚠️ ${__("Pas d'adresse de livraison sur la commande : l'adresse de facturation est proposée.")}</div>`;
                }
                if (p.garde_fou_dev) {
                    html += `<div style="padding:6px 10px;border-radius:6px;background:#e0e7ff;color:#3730a3;margin-bottom:8px">
                        🧪 ${__("Site de développement : la création sera refusée par le serveur (vrai colis).")}</div>`;
                }
                d.fields_dict.avertissements.$wrapper.html(html);
                d.show();
            },
        });
    };
})();
