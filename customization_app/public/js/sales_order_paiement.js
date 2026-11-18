// Commande client — le PAIEMENT, en un seul bloc interactif dans « Termes de paiement » (18/11/2026).
//
// Demande : « ça se remplit dès le départ par rapport au modèle de termes de paiement, et je peux ajouter
// d'autres formes de paiement qui diminuent les autres ; une UI plus interactive, avec les mêmes validations
// visuelles » — et « je ne vois pas l'augmentation par carte en temps réel ».
//
// CE BLOC N'INVENTE AUCUNE DONNÉE : il lit et écrit le tableau standard `payment_schedule` (qui reste la
// seule vérité : Server Scripts de paiement, contrôles ERPNext, impression n'y voient aucune différence). Le
// tableau est replié sous « Afficher le tableau » tant que la commande est en brouillon.
//
//   - une ligne par paiement : moyen, montant, et la pièce que le moyen exige (n° de chèque à
//     7 chiffres + banque, n° de transaction de la carte, référence Aramex) — en rouge tant qu'elle manque ;
//   - on tape un montant : les autres lignes s'ajustent pour que la somme reste le total (la ligne qu'on
//     vient de taper ne bouge pas ; celles déjà tapées à la main sont touchées en dernier) ;
//   - « ＋ Carte », « ＋ Chèque »… ajoute un paiement ; ✕ le retire et rend son montant aux autres ;
//   - pas de champ « échéance » : un paiement ajouté porte le jour de la commande, et plusieurs paiements
//     peuvent porter le même jour (customization_app.echeances_meme_jour) — la date n'est jamais décalée ;
//   - le total de la commande change (article ajouté, quantité…) : les montants suivent ;
//   - PAIEMENT PAR CARTE : la majoration (customization_app.majoration_carte) est demandée au serveur à
//     chaque changement et APPLIQUÉE TOUT DE SUITE à l'écran — prix des articles, total, montant de la
//     carte. Le vendeur lit le montant exact à passer sur le terminal avant d'enregistrer, et
//     l'enregistrement retrouve la commande déjà dans son état final.
//
// Montants : pour une ligne par carte, on saisit la PART DE LA COMMANDE réglée par carte ; le montant à
// passer sur le terminal (part + majoration) est affiché à côté, et c'est lui que porte le tableau.
(() => {
    const CARTE = "Carte de crédit", ARAMEX = "Livraison Aramex", CLASSE = "so-paiement", TOL = 0.0005, DELAI = 450;
    const PIECE = "custom__n_chèque__transaction", BANQUE = "custom_banque";
    const MODES = ["Espèces", "Chèque", CARTE, "Virement", "Traite bancaire LC", "Dette non payée"];
    const COURT = { "Espèces": "Espèces", "Chèque": "Chèque", [CARTE]: "Carte", "Virement": "Virement",
        "Traite bancaire LC": "Traite", "Dette non payée": "Dette (à payer plus tard)" };
    // Ce que chaque moyen exige — les MÊMES règles que le Client Script « Verification Sales order ».
    const REGLES = {
        "Chèque": { piece: "N° du chèque (7 chiffres)", format: /^\d{7}$/, requise: true, banque: true },
        [CARTE]: { piece: "N° de transaction (ticket du terminal)", requise: true },
        "Virement": { piece: "Référence du virement" },
        "Traite bancaire LC": { piece: "N° de la traite", banque: true },
    };
    const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
    const nombre = (v) => `<span dir="ltr">${format_number(flt(v, 3), null, 3)}</span>`;
    const arrondi = (v) => flt(v, 3);

    function etat(frm) {
        if (!frm.__paiement) frm.__paiement = { fixes: new Set(), application: false, tour: 0, suite: 0, tableau: false };
        return frm.__paiement;
    }
    const lignes = (frm) => frm.doc.payment_schedule || [];
    const estCarte = (r) => r.mode_of_payment === CARTE;
    const total = (frm) => (cint(frm.doc.disable_rounded_total) ? 0 : flt(frm.doc.rounded_total)) || flt(frm.doc.grand_total);
    const majoration = (frm) => flt(frm.doc.custom_majoration_carte, 3);
    const totalBase = (frm) => arrondi(total(frm) - majoration(frm));

    /** {nom de ligne -> part de la commande} : pour la carte, le montant porté moins sa part de majoration. */
    function parts(frm) {
        const rs = lignes(frm), maj = majoration(frm);
        const carte = rs.filter(estCarte).reduce((s, r) => s + flt(r.payment_amount), 0);
        const m = new Map();
        rs.forEach((r) => m.set(r.name, arrondi(estCarte(r) && carte > 0
            ? flt(r.payment_amount) - maj * flt(r.payment_amount) / carte : flt(r.payment_amount))));
        return m;
    }

    /** Ramène la somme des parts à `cible` : `garde` ne bouge pas, les lignes tapées à la main passent en dernier. */
    function equilibrer(frm, m, cible, garde) {
        const st = etat(frm), noms = lignes(frm).map((r) => r.name);
        let ecart = arrondi(noms.reduce((s, n) => s + flt(m.get(n)), 0) - cible);      // > 0 : il y a trop
        if (Math.abs(ecart) < 0.001) return;
        const autres = noms.filter((n) => n !== garde);
        const ordre = autres.filter((n) => !st.fixes.has(n)).concat(autres.filter((n) => st.fixes.has(n)));
        for (const n of ordre) {
            if (Math.abs(ecart) < 0.001) break;
            if (ecart < 0) { m.set(n, arrondi(flt(m.get(n)) - ecart)); ecart = 0; break; }
            const pris = Math.min(flt(m.get(n)), ecart);
            m.set(n, arrondi(flt(m.get(n)) - pris)); ecart = arrondi(ecart - pris);
        }
        if (Math.abs(ecart) >= 0.001 && garde) m.set(garde, Math.max(arrondi(flt(m.get(garde)) - ecart), 0));
    }

    /** Écrit les parts dans le tableau (la carte garde provisoirement sa part : le serveur y ajoute la majoration). */
    function ecrire(frm, m) {
        const t = total(frm);
        lignes(frm).forEach((r) => {
            const v = arrondi(m.get(r.name));
            r.payment_amount = v; r.base_payment_amount = v; r.outstanding = v;
            r.invoice_portion = t ? v / t * 100 : 0;
        });
        frm.refresh_field("payment_schedule");
        frm.dirty();
    }

    /** L'échéance d'un paiement ajouté : le jour de la commande (ou aujourd'hui). Plusieurs paiements le même
        jour sont permis (customization_app.echeances_meme_jour) : la date n'est jamais décalée. */
    function jourDuPaiement(frm) {
        const auj = frappe.datetime.now_date();
        return frm.doc.transaction_date && frm.doc.transaction_date > auj ? frm.doc.transaction_date : auj;
    }

    /** Le moyen de paiement ne suit plus le modèle choisi : le modèle est vidé (sauf Aramex, qui pilote la livraison). */
    function quitterLeModele(frm) {
        const st = etat(frm);
        if (frm.doc.payment_terms_template && frm.doc.payment_terms_template !== ARAMEX) {
            st.modele = frm.doc.payment_terms_template;
            frm.doc.payment_terms_template = "";
            frm.refresh_field("payment_terms_template");
        }
    }

    // ------------------------------------------------------------------ majoration carte, en temps réel

    function carteEnJeu(frm) { return lignes(frm).some(estCarte) || majoration(frm) > 0; }

    function demander(frm, delai = DELAI) {
        const st = etat(frm);
        if (frm.doctype !== "Sales Order" || frm.doc.docstatus !== 0) return;
        if (!carteEnJeu(frm)) { st.apercu = null; rendre(frm); return; }
        clearTimeout(st.minuterie);
        const moi = ++st.tour;
        st.attente = true; rendre(frm);
        st.minuterie = setTimeout(async () => {
            let e = null;
            try { e = await frappe.xcall("customization_app.majoration_carte.apercu", { doc: frm.doc }); } catch (err) { /* réseau */ }
            if (moi !== st.tour || frm.doc.docstatus !== 0) return;        // une demande plus récente est partie
            st.attente = false; st.apercu = e;
            if (e && e.change && st.suite < 4) { st.suite += 1; await appliquer(frm, e); }
            rendre(frm);
        }, delai);
    }

    /** Pose à l'écran ce que l'enregistrement poserait : prix des lignes, majoration, échéances. */
    async function appliquer(frm, e) {
        const st = etat(frm);
        st.application = true;
        try {
            for (const l of e.lignes || []) {
                const ligne = locals["Sales Order Item"] && locals["Sales Order Item"][l.name];
                if (!ligne) continue;
                ligne.custom_prix_avant_carte = l.avant; ligne.custom_prix_avec_carte = l.avec;
                // set_value("rate") : ERPNext pose remise / marge et refait les totaux, comme pour un prix tapé.
                if (Math.abs(flt(ligne.rate) - flt(l.rate)) > TOL) await frappe.model.set_value(ligne.doctype, ligne.name, "rate", l.rate);
            }
            frm.doc.custom_majoration_carte = e.majoration;
            frm.doc.custom_majoration_carte_taux = e.taux;
            for (const x of e.echeances || []) {
                const r = locals["Payment Schedule"] && locals["Payment Schedule"][x.name];
                if (!r) continue;
                r.payment_amount = x.payment_amount; r.base_payment_amount = x.payment_amount;
                r.outstanding = x.payment_amount; r.invoice_portion = x.invoice_portion;
            }
            frm.refresh_field("payment_schedule");
            frm.dirty();
        } finally { st.application = false; }
    }

    // ------------------------------------------------------------------ gestes

    function apresGeste(frm) { etat(frm).suite = 0; carteEnJeu(frm) ? demander(frm, 150) : rendre(frm); }

    function saisirMontant(frm, nom, texte) {
        const st = etat(frm), m = parts(frm), cible = totalBase(frm);
        const v = Math.min(Math.max(flt(String(texte).replace(/\s/g, "").replace(",", "."), 3), 0), cible);
        m.set(nom, v); st.fixes.add(nom);
        equilibrer(frm, m, cible, nom);
        ecrire(frm, m); apresGeste(frm);
    }

    function ajouter(frm, mode) {
        const st = etat(frm), m = parts(frm), vide = !lignes(frm).length;
        const r = frm.add_child("payment_schedule", { mode_of_payment: mode, due_date: jourDuPaiement(frm), payment_amount: 0, invoice_portion: 0 });
        m.set(r.name, vide ? totalBase(frm) : 0);
        if (!vide) quitterLeModele(frm);
        ecrire(frm, m); st.focus = r.name; apresGeste(frm);
    }

    function retirer(frm, nom) {
        const st = etat(frm), m = parts(frm);
        m.delete(nom); st.fixes.delete(nom);
        frm.doc.payment_schedule = lignes(frm).filter((r) => r.name !== nom);
        frm.doc.payment_schedule.forEach((r, i) => { r.idx = i + 1; });
        if (locals["Payment Schedule"]) delete locals["Payment Schedule"][nom];
        quitterLeModele(frm);
        equilibrer(frm, m, totalBase(frm), null);
        ecrire(frm, m); apresGeste(frm);
    }

    function changerMode(frm, nom, mode) {
        const r = lignes(frm).find((x) => x.name === nom);
        if (!r || r.mode_of_payment === mode) return;
        const m = parts(frm);                       // les parts AVANT de changer de moyen (la carte perd sa majoration)
        r.mode_of_payment = mode;
        if (!(REGLES[mode] || {}).banque) r[BANQUE] = "";
        quitterLeModele(frm);
        ecrire(frm, m); apresGeste(frm);
    }

    /** Le total de la commande a bougé (article, quantité, remise…) : les montants suivent. */
    function suivreLeTotal(frm) {
        const st = etat(frm), rs = lignes(frm);
        if (frm.doc.docstatus !== 0 || st.application || !rs.length) return;
        if (carteEnJeu(frm)) { st.suite = 0; demander(frm); return; }           // le serveur recale tout, carte comprise
        const t = total(frm), somme = arrondi(rs.reduce((s, r) => s + flt(r.payment_amount), 0));
        if (Math.abs(somme - t) < 0.001) { rendre(frm); return; }
        const m = parts(frm);
        if (!st.fixes.size && somme > 0) {                                      // rien de tapé : mêmes proportions
            let reste = t;
            rs.forEach((r, i) => {
                const v = i === rs.length - 1 ? arrondi(reste) : arrondi(flt(r.payment_amount) * t / somme);
                m.set(r.name, v); reste -= v;
            });
        } else equilibrer(frm, m, t, null);
        ecrire(frm, m); rendre(frm);
    }

    // ------------------------------------------------------------------ contrôles visuels (mêmes règles que le système)

    /** {piece, banque} : les textes d'erreur de la ligne, vides si tout va bien. */
    function defauts(frm, r) {
        const regle = REGLES[r.mode_of_payment] || {}, piece = String(r[PIECE] || "").trim();
        const aramex = frm.doc.payment_terms_template === ARAMEX && r.mode_of_payment !== "Chèque";
        const out = { piece: "", banque: "" };
        if (regle.format && !regle.format.test(piece)) out.piece = "Numéro du chèque : 7 chiffres";
        else if ((regle.requise || aramex) && !piece) {
            out.piece = aramex ? "Référence de la livraison Aramex à indiquer" : "Référence de la transaction par carte à indiquer";
        }
        if (regle.banque && r.mode_of_payment === "Chèque" && !r[BANQUE]) out.banque = "Banque à choisir";
        return out;
    }

    // ------------------------------------------------------------------ affichage

    function bloc(frm) {
        const champ = frm.fields_dict.payment_schedule;
        if (!champ || !champ.$wrapper) return $();
        let $b = champ.$wrapper.prevAll("." + CLASSE);
        if ($b.length) return $b;
        $b = $(`<div class="${CLASSE}"></div>`).insertBefore(champ.$wrapper);
        if (!document.getElementById("so-paiement-css")) {
            $("head").append(`<style id="so-paiement-css">
              .${CLASSE}{margin:0 0 14px;border:1px solid var(--border-color,#e2e6e9);border-radius:10px;background:var(--card-bg,#fff)}
              .${CLASSE} .sp-tete{display:flex;flex-wrap:wrap;gap:6px 18px;align-items:baseline;padding:12px 14px;border-bottom:1px solid var(--border-color,#e2e6e9)}
              .${CLASSE} .sp-total{font-size:15px;font-weight:600}
              .${CLASSE} .sp-ok{color:#15803d;font-weight:600} .${CLASSE} .sp-ko{color:#b91c1c;font-weight:600}
              .${CLASSE} .sp-ligne{display:flex;flex-wrap:wrap;gap:8px 10px;align-items:flex-start;padding:10px 14px;border-bottom:1px solid var(--border-color,#eef0f2)}
              .${CLASSE} .sp-champ{display:flex;flex-direction:column;gap:2px;min-width:120px}
              .${CLASSE} .sp-champ label{font-size:11px;color:var(--text-muted,#6b7280);margin:0}
              .${CLASSE} .sp-champ .form-control{height:32px;font-size:13px}
              .${CLASSE} .sp-mode{flex:1 1 170px} .${CLASSE} .sp-montant{flex:0 1 130px}
              .${CLASSE} .sp-piece{flex:1 1 190px} .${CLASSE} .sp-banque{flex:1 1 150px}
              .${CLASSE} .sp-montant .form-control{text-align:right;font-weight:600}
              .${CLASSE} .sp-rouge{border:2px solid #dc2626 !important}
              .${CLASSE} .sp-erreur{font-size:11px;color:#b91c1c}
              .${CLASSE} .sp-suppr{align-self:center;border:0;background:none;color:#9ca3af;font-size:18px;line-height:1;padding:4px 6px;cursor:pointer}
              .${CLASSE} .sp-suppr:hover{color:#dc2626}
              .${CLASSE} .sp-carte{flex:1 1 100%;margin-top:2px;padding:7px 10px;border-radius:7px;background:#eff6ff;color:#1e3a8a;font-size:12.5px}
              .${CLASSE} .sp-carte b.sp-terminal{font-size:15px}
              .${CLASSE} .sp-pied{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:10px 14px}
              .${CLASSE} .sp-ajout{border:1px dashed var(--border-color,#cbd5e1);background:none;border-radius:999px;padding:3px 11px;font-size:12.5px;cursor:pointer}
              .${CLASSE} .sp-ajout:hover{background:var(--control-bg,#f3f4f6)}
              .${CLASSE} .sp-lien{margin-left:auto;font-size:12px;color:var(--text-muted,#6b7280);cursor:pointer;text-decoration:underline}
              .${CLASSE} .sp-note{font-size:12px;color:var(--text-muted,#6b7280)}
              .${CLASSE}.sp-lecture{padding:10px 14px;background:#f0fdf4;border-color:#bbf7d0;color:#14532d;font-size:13px}
              @media (max-width:575px){.${CLASSE} .sp-champ{flex:1 1 100% !important}}
            </style>`);
        }
        $b.on("change", "select.sp-in-mode", function () { changerMode(frm, $(this).closest("[data-ligne]").attr("data-ligne"), $(this).val()); });
        $b.on("change", "input.sp-in-montant", function () { saisirMontant(frm, $(this).closest("[data-ligne]").attr("data-ligne"), $(this).val()); });
        $b.on("change", "input.sp-in-piece, select.sp-in-banque", function () {
            const r = lignes(frm).find((x) => x.name === $(this).closest("[data-ligne]").attr("data-ligne"));
            if (!r) return;
            r[$(this).hasClass("sp-in-piece") ? PIECE : BANQUE] = String($(this).val() || "").trim();
            frm.refresh_field("payment_schedule"); frm.dirty(); rendre(frm);
        });
        $b.on("click", ".sp-suppr", function () { retirer(frm, $(this).closest("[data-ligne]").attr("data-ligne")); });
        $b.on("click", ".sp-ajout", function () { ajouter(frm, $(this).attr("data-mode")); });
        $b.on("click", ".sp-lien", function () { etat(frm).tableau = !etat(frm).tableau; rendre(frm); });
        return $b;
    }

    function htmlLigne(frm, r, part, seule) {
        const regle = REGLES[r.mode_of_payment] || {}, d = defauts(frm, r), st = etat(frm);
        const modes = MODES.includes(r.mode_of_payment) || !r.mode_of_payment ? MODES : MODES.concat([r.mode_of_payment]);
        const aramex = frm.doc.payment_terms_template === ARAMEX && r.mode_of_payment !== "Chèque";
        const libPiece = aramex ? "Référence de la livraison Aramex" : (regle.piece || "");
        const banques = String((frappe.meta.get_docfield("Payment Schedule", BANQUE, frm.doc.name) || {}).options || "").split("\n");
        let carte = "";
        if (estCarte(r)) {
            const maj = arrondi(flt(r.payment_amount) - part);
            carte = st.attente ? `<div class="sp-carte">💳 Calcul de la majoration…</div>`
                : maj > 0 ? `<div class="sp-carte">💳 Majoration carte de ${String(flt(frm.doc.custom_majoration_carte_taux)).replace(".", ",")} % :
                    <b>+ ${nombre(maj)}</b>, répartie sur les prix des articles —
                    montant à passer sur le terminal : <b class="sp-terminal">${nombre(r.payment_amount)}</b></div>` : "";
        }
        return `<div class="sp-ligne" data-ligne="${esc(r.name)}">
            <div class="sp-champ sp-mode"><label>Moyen de paiement</label>
              <select class="form-control sp-in-mode ${r.mode_of_payment ? "" : "sp-rouge"}">
                ${r.mode_of_payment ? "" : `<option value=""></option>`}
                ${modes.map((x) => `<option value="${esc(x)}" ${x === r.mode_of_payment ? "selected" : ""}>${esc(COURT[x] || x)}</option>`).join("")}
              </select></div>
            <div class="sp-champ sp-montant"><label>${estCarte(r) ? "Part réglée par carte" : "Montant"}</label>
              <input type="text" inputmode="decimal" class="form-control sp-in-montant" value="${format_number(part, null, 3)}" ${seule ? `title="Un seul paiement : il règle toute la commande"` : ""}></div>
            ${libPiece ? `<div class="sp-champ sp-piece"><label>${esc(libPiece)}</label>
              <input type="text" class="form-control sp-in-piece ${d.piece ? "sp-rouge" : ""}" value="${esc(r[PIECE] || "")}">
              ${d.piece ? `<span class="sp-erreur">${esc(d.piece)}</span>` : ""}</div>` : ""}
            ${regle.banque ? `<div class="sp-champ sp-banque"><label>Banque</label>
              <select class="form-control sp-in-banque ${d.banque ? "sp-rouge" : ""}">
                ${banques.map((x) => `<option value="${esc(x)}" ${x === (r[BANQUE] || "") ? "selected" : ""}>${esc(x)}</option>`).join("")}
              </select>${d.banque ? `<span class="sp-erreur">${esc(d.banque)}</span>` : ""}</div>` : ""}
            ${seule ? "" : `<button type="button" class="sp-suppr" title="Retirer ce paiement">✕</button>`}
            ${carte}
          </div>`;
    }

    function rendre(frm) {
        if (frm.doctype !== "Sales Order") return;
        const $b = bloc(frm), champ = frm.fields_dict.payment_schedule, st = etat(frm);
        if (!$b.length) return;
        const rs = lignes(frm), maj = majoration(frm), t = total(frm);
        if (frm.doc.docstatus !== 0) {
            // Commande validée ou annulée : le tableau standard, et le rappel de la majoration qu'elle porte.
            champ.$wrapper.show();
            const carte = rs.filter(estCarte).reduce((s, r) => s + flt(r.payment_amount), 0);
            $b.toggleClass("sp-lecture", true).toggle(maj > 0).html(maj > 0
                ? `💳 <b>Majoration carte de ${String(flt(frm.doc.custom_majoration_carte_taux)).replace(".", ",")} % : + ${nombre(maj)}</b>, incluse dans les prix des articles.
                   ${carte > 0 ? `Montant réglé par carte : <b>${nombre(carte)}</b>.` : ""}` : "");
            return;
        }
        $b.removeClass("sp-lecture").show();
        champ.$wrapper.toggle(!!st.tableau);
        const somme = arrondi(rs.reduce((s, r) => s + flt(r.payment_amount), 0)), ecart = arrondi(t - somme);
        const verdict = !rs.length ? "" : st.attente ? `<span class="sp-note">calcul…</span>`
            : Math.abs(ecart) < 0.001 ? `<span class="sp-ok">✓ Tout est réparti</span>`
            : ecart > 0 ? `<span class="sp-ko">⚠ Il reste ${nombre(ecart)} à répartir</span>`
            : `<span class="sp-ko">⚠ ${nombre(-ecart)} de trop par rapport au total</span>`;
        const m = parts(frm);
        const tete = `<div class="sp-tete">
            <span class="sp-total">Total à régler : ${nombre(t)}</span>
            ${maj > 0 ? `<span class="sp-note">dont majoration carte ${nombre(maj)} (commande sans carte : ${nombre(totalBase(frm))})</span>` : ""}
            ${verdict}
            ${!frm.doc.payment_terms_template && st.modele && rs.length
                ? `<span class="sp-note">Paiement personnalisé (modèle de départ : ${esc(st.modele)})</span>` : ""}</div>`;
        const corps = rs.length ? rs.map((r) => htmlLigne(frm, r, flt(m.get(r.name)), rs.length === 1)).join("")
            : `<div class="sp-ligne sp-note">Choisissez un modèle de termes de paiement ci-dessus, ou ajoutez un paiement.</div>`;
        const pied = `<div class="sp-pied"><span class="sp-note">Ajouter un paiement :</span>
            ${MODES.map((x) => `<button type="button" class="sp-ajout" data-mode="${esc(x)}">＋ ${esc(COURT[x] || x)}</button>`).join("")}
            <span class="sp-lien">${st.tableau ? "Masquer le tableau" : "Afficher le tableau"}</span></div>`;
        $b.html(tete + corps + pied);
        if (st.focus) {
            const $i = $b.find(`[data-ligne="${st.focus}"] input.sp-in-montant`);
            st.focus = null;
            if ($i.length) { $i.trigger("focus"); $i[0].select(); }
        }
    }

    // ------------------------------------------------------------------ branchements

    let minuterieTotal = null;
    const plusTard = (frm, delai = 700) => { clearTimeout(minuterieTotal); minuterieTotal = setTimeout(() => suivreLeTotal(frm), delai); };

    frappe.ui.form.on("Sales Order", {
        refresh(frm) { etat(frm).suite = 0; rendre(frm); if (frm.doc.docstatus === 0) plusTard(frm, 300); },
        // L'échéancier du modèle arrive par un appel serveur : on repart de lui, plus rien n'est « tapé à la main ».
        payment_terms_template(frm) {
            const st = etat(frm); st.fixes.clear(); st.suite = 0;
            setTimeout(() => { rendre(frm); suivreLeTotal(frm); }, 900);
        },
        discount_amount(frm) { plusTard(frm); },
        additional_discount_percentage(frm) { plusTard(frm); },
        taxes_and_charges(frm) { plusTard(frm, 1600); },
        // Mêmes refus que « Verification Sales order », ligne par ligne (le modèle peut avoir été quitté).
        validate(frm) {
            if (frm.doc.docstatus !== 0) return;
            const manques = lignes(frm).map((r) => defauts(frm, r)).filter((d) => d.piece || d.banque);
            if (manques.length) {
                rendre(frm);
                frappe.msgprint({ title: __("Paiement à compléter"), indicator: "red",
                    message: [...new Set(manques.map((d) => d.piece || d.banque))].map(esc).join("<br>") });
                frappe.validated = false;
            }
        },
    });
    frappe.ui.form.on("Payment Schedule", {
        // Tableau standard modifié à la main (« Afficher le tableau ») : le bloc suit.
        mode_of_payment(frm) { if (frm.doctype === "Sales Order" && !etat(frm).application) apresGeste(frm); },
        payment_amount(frm) { if (frm.doctype === "Sales Order" && !etat(frm).application) apresGeste(frm); },
        payment_schedule_add(frm) { if (frm.doctype === "Sales Order" && !etat(frm).application) rendre(frm); },
        payment_schedule_remove(frm) { if (frm.doctype === "Sales Order" && !etat(frm).application) apresGeste(frm); },
    });
    const ligne = {};
    ["item_code", "qty", "rate", "discount_percentage", "discount_amount", "price_list_rate", "uom"]
        .forEach((f) => { ligne[f] = (frm) => { if (!etat(frm).application) plusTard(frm, 900); }; });
    ligne.items_remove = (frm) => plusTard(frm, 900);
    frappe.ui.form.on("Sales Order Item", ligne);
})();
