/**
 * Réparation osmoseurs — la SORTIE d'une machine, là où l'on est déjà (20/11/2026).
 *
 * Constat : le clic « Rendue au client » de l'écran Réparation osmoseurs n'était jamais fait — les machines
 * partaient, les dossiers restaient « à rendre ». La sortie se confirme donc depuis trois endroits, avec le
 * même dialogue :
 *   - l'écran Réparation osmoseurs (bouton « ✅ Rendre au client ») ;
 *   - celui qui vient de CLÔTURER la réparation (fiche, calendrier ou « Ma journée ») : le serveur envoie
 *     l'événement temps réel `osm_a_rendre`, on demande aussitôt « et maintenant ? » ;
 *   - la commande ou le BL du client : un bandeau rappelle que sa machine réparée attend.
 *
 * « Rendue » dit que la machine est SORTIE. Le règlement est constaté, affiché, jamais bloquant : une commande
 * liée suffit (payée ou avec un reste dû — une « Dette non payée » n'est pas un paiement), la garantie se
 * justifie par la commande d'origine, et sans aucune vente il faut un motif et le code superviseur.
 * Toute la règle est côté serveur (reparation_osmoseur.regle_sortie) ; ici on ne fait que la montrer.
 */
(function () {
  const API = "customization_app.reparation_osmoseur";
  const esc = (v) => frappe.utils.escape_html(String(v == null ? "" : v));
  const dt = (v) => format_number(v || 0, null, 3);
  const jour = (d) => (d ? frappe.datetime.str_to_user(String(d).slice(0, 10)) : "");
  const CARTE = "border:1px solid var(--border-color,#e4e8ee);border-radius:10px;padding:10px 12px;margin-bottom:8px";

  function alerte_sms(x, prefixe) {
    if (!x) return prefixe;
    return `${prefixe} · ${x.simule ? "SMS SIMULÉ (dev)" : "SMS envoyé"} → ${x.numeros.join(", ")}`;
  }

  // Le règlement d'une commande, en une phrase et une couleur.
  function phrase_reglement(c) {
    if (c.etat === "du") return [`reste dû ${dt(c.reste)} DT`, "#c2410c"];
    if (c.etat === "zero") return ["0 DT", "#6b7280"];
    return [`payée`, "#166534"];
  }

  // ── ✅ Rendre au client ─────────────────────────────────────────────────────
  async function rendre(machine, opts = {}) {
    const s = (await frappe.call({ method: API + ".situation_sortie", args: { machine } })).message;
    const m = s.machine;
    if (!s.repare) return sans_reparation(machine, opts, s);
    // La commande de la réparation : celle du dossier, celle d'où l'on vient (bandeau), la seule possible —
    // sinon à désigner (null), ou « aucune vente » ("") quand le client n'a rien acheté depuis la réception.
    let choix = opts.commande && s.commandes.some((c) => c.name === opts.commande) ? opts.commande
      : m.commande_client || (s.commandes.length === 1 ? s.commandes[0].name : s.commandes.length ? null : "");
    let photo = m.photo_remise || "";

    const d = new frappe.ui.Dialog({
      title: `✅ Rendre au client — ${machine}`,
      fields: [
        { fieldtype: "HTML", fieldname: "haut" },
        { fieldtype: "Link", fieldname: "commande_garantie", options: "Sales Order", hidden: 1,
          label: "Commande d’origine de l’appareil (justifie la garantie)",
          get_query: () => ({ filters: { customer: m.client, docstatus: 1 } }), onchange: () => peindre() },
        { fieldtype: "Select", fieldname: "motif", hidden: 1, label: "Pourquoi la réparation n’est pas facturée",
          options: [""].concat(s.motifs) },
        { fieldtype: "Data", fieldname: "precision", hidden: 1, label: "Précision (facultatif)" },
        { fieldtype: "Password", fieldname: "code", hidden: 1, label: "Code superviseur" },
        { fieldtype: "Section Break", label: "Remise" },
        { fieldtype: "Data", fieldname: "rendu_a", label: "Remise à (personne)", reqd: 1, default: m.nom_client || "" },
        { fieldtype: "Small Text", fieldname: "remarque", label: "Remarque (facultatif)" },
        { fieldtype: "HTML", fieldname: "photo" },
      ],
      primary_action_label: "Confirmer la sortie",
      primary_action: async (v) => {
        const r = regle();
        if (r.cas === "a_choisir") { frappe.msgprint("Désignez la commande de cette réparation, ou « Aucune vente »."); return; }
        if (r.motif && !v.motif) { frappe.msgprint("Indiquez pourquoi la réparation n’est pas facturée."); return; }
        if (r.code && !v.code) { frappe.msgprint(r.cas === "garantie_sans_preuve"
          ? "Indiquez la commande d’origine de l’appareil, ou saisissez le code superviseur."
          : "Le code superviseur est nécessaire pour sortir une machine sans aucune vente."); return; }
        try {
          const res = await frappe.call({ method: API + ".rendre", freeze: true, args: {
            machine, rendu_a: v.rendu_a, remarque: v.remarque || null,
            commande: choix || null, sans_commande: choix === "" ? 1 : 0,
            commande_garantie: v.commande_garantie || null,
            motif: r.motif ? v.motif + (v.precision ? " : " + v.precision : "") : null,
            code: r.code ? v.code : null } });
          d.hide();
          frappe.show_alert({ message: `${machine} rendue — ${esc((res.message || {}).reglement || "")}`, indicator: "green" }, 7);
          if (opts.apres) opts.apres();
        } catch (e) { /* message déjà affiché par frappe.call */ }
      },
    });

    // La même règle que le serveur (regle_sortie), pour montrer d'avance ce qu'il demandera.
    const regle = () => {
      if (choix === null) return { cas: "a_choisir" };
      if (choix) return { cas: "commande" };
      if (m.garantie === "Sous garantie") {
        return (m.commande_garantie || d.get_value("commande_garantie")) ? { cas: "garantie" } : { cas: "garantie_sans_preuve", code: true };
      }
      return { cas: "non_facturee", motif: true, code: true };
    };

    const peindre = () => {
      const r = regle();
      const c = s.commandes.find((x) => x.name === choix);
      let verdict;
      if (r.cas === "a_choisir") verdict = ["Quelle commande est celle de cette réparation ?", "#6b7280"];
      else if (r.cas === "commande") {
        verdict = c.etat === "du"
          ? [`⚠ Reste dû ${dt(c.reste)} DT sur ${dt(c.total)} — la machine peut sortir, la dette reste sur le compte du client.`, "#c2410c"]
          : c.etat === "zero" ? ["Commande à 0 DT : rien à encaisser.", "#166534"]
          : [`✔ Payée — ${dt(c.total)} DT.`, "#166534"];
      } else if (r.cas === "garantie") verdict = ["🆓 Sous garantie : rien à facturer.", "#166534"];
      else if (r.cas === "garantie_sans_preuve") verdict = ["🆓 Sous garantie, mais sans la commande d’origine de l’appareil : indiquez-la, ou le code superviseur.", "#c2410c"];
      else verdict = ["Hors garantie et aucune vente : un motif et le code superviseur sont nécessaires.", "#c2410c"];

      const lignes = s.commandes.map((x) => {
        const [txt, couleur] = phrase_reglement(x);
        return `<label style="${CARTE};display:flex;gap:10px;align-items:flex-start;cursor:pointer;font-weight:400;${x.name === choix ? "outline:2px solid #0ea5e9;background:rgba(14,165,233,.06)" : ""}">
          <input type="radio" name="ro-cmd" value="${esc(x.name)}" ${x.name === choix ? "checked" : ""} style="margin-top:3px">
          <span style="flex:1"><b>${esc(x.name)}</b> · ${esc(jour(x.transaction_date))} · ${dt(x.total)} DT
            · <b style="color:${couleur}">${txt}</b>
            <span style="display:block;font-size:11.5px;color:var(--text-muted)">${esc(x.articles || "")}</span></span></label>`;
      }).join("");
      d.fields_dict.haut.$wrapper.html(`
        <div style="margin-bottom:10px">Client : <b>${esc(m.nom_client || m.client)}</b> · ${esc(m.tel || "")}
          · ${m.garantie === "Sous garantie" ? "🆓 sous garantie" : "hors garantie"}</div>
        <div style="font-size:11px;font-weight:600;text-transform:uppercase;color:var(--text-muted);margin-bottom:5px">Commande de cette réparation</div>
        ${lignes || `<div style="font-size:12.5px;color:var(--text-muted);margin-bottom:8px">Aucune commande de ce client depuis la réception.</div>`}
        <label style="${CARTE};display:flex;gap:10px;cursor:pointer;font-weight:400;${choix === "" ? "outline:2px solid #0ea5e9;background:rgba(14,165,233,.06)" : ""}">
          <input type="radio" name="ro-cmd" value="" ${choix === "" ? "checked" : ""}> Aucune vente pour cette réparation</label>
        <div style="font-weight:600;color:${verdict[1]};margin:10px 0 4px">${verdict[0]}</div>`);
      d.fields_dict.haut.$wrapper.find("input[name='ro-cmd']").on("change", (e) => { choix = e.currentTarget.value; peindre(); });
      d.set_df_property("commande_garantie", "hidden", r.cas === "garantie_sans_preuve" || (r.cas === "garantie" && !m.commande_garantie) ? 0 : 1);
      d.set_df_property("motif", "hidden", r.motif ? 0 : 1);
      d.set_df_property("precision", "hidden", r.motif ? 0 : 1);
      d.set_df_property("code", "hidden", r.code ? 0 : 1);
    };

    const peindre_photo = () => {
      d.fields_dict.photo.$wrapper.html(`
        <div style="display:flex;align-items:center;gap:10px;padding:6px 0">
          ${photo ? `<a href="${esc(photo)}" target="_blank"><img src="${esc(photo)}" style="width:64px;height:64px;object-fit:cover;border-radius:8px;border:1px solid #e4e8ee"></a>`
                  : `<div style="width:64px;height:64px;border-radius:8px;background:#f1f5f9;display:flex;align-items:center;justify-content:center;font-size:24px;color:#94a3b8">📷</div>`}
          <div style="flex:1"><b>Photo de la remise</b> (facultatif)
            <div style="font-size:11.5px;color:var(--text-muted)">La machine avec la personne qui la récupère, ou le reçu signé.</div></div>
          <button class="btn btn-sm btn-default" data-photo>${photo ? "Reprendre" : "Prendre la photo"}</button>
        </div>`);
      d.fields_dict.photo.$wrapper.find("[data-photo]").on("click", () => {
        new frappe.ui.FileUploader({
          doctype: "Machine Reparation", docname: machine, folder: "Home/Attachments",
          allow_multiple: false, restrictions: { allowed_file_types: ["image/*"] },
          on_success: (file) => {
            frappe.call({ method: API + ".enregistrer_photo", args: { machine, champ: "remise", file_url: file.file_url } })
              .then(() => { photo = file.file_url; peindre_photo(); });
          },
        });
      });
    };
    peindre();
    peindre_photo();
    d.show();
  }

  // ── La machine repart SANS réparation ───────────────────────────────────────
  async function sans_reparation(machine, opts = {}, situation) {
    const s = situation || (await frappe.call({ method: API + ".situation_sortie", args: { machine } })).message;
    const m = s.machine;
    const d = new frappe.ui.Dialog({
      title: `↪️ Sortie sans réparation — ${machine}`,
      fields: [
        { fieldtype: "HTML", fieldname: "haut" },
        { fieldtype: "Select", fieldname: "motif", label: "Pourquoi la machine repart sans réparation", reqd: 1,
          options: [""].concat(s.motifs) },
        { fieldtype: "Data", fieldname: "precision", label: "Précision (facultatif)" },
        { fieldtype: "Data", fieldname: "rendu_a", label: "Remise à (personne)", default: m.nom_client || "" },
      ],
      primary_action_label: "Fermer le dossier",
      primary_action: async (v) => {
        try {
          await frappe.call({ method: API + ".sortir_sans_reparation", freeze: true, args: {
            machine, motif: v.motif + (v.precision ? " : " + v.precision : ""), rendu_a: v.rendu_a || null } });
          d.hide();
          frappe.show_alert({ message: `${machine} fermé — sortie sans réparation`, indicator: "green" }, 6);
          if (opts.apres) opts.apres();
        } catch (e) { /* message déjà affiché */ }
      },
    });
    d.fields_dict.haut.$wrapper.html(`<div style="margin-bottom:8px">Client : <b>${esc(m.nom_client || m.client)}</b> · ${esc(m.tel || "")}</div>
      <div style="font-size:12.5px;color:var(--text-muted);margin-bottom:8px">Le dossier se ferme avec son motif. Les tâches de réparation encore ouvertes sont retirées du calendrier.</div>`);
    d.show();
  }

  // ── 🚚 Planifier la livraison de retour ─────────────────────────────────────
  async function livraison(machine, m, opts = {}) {
    const adresses = (await frappe.call({ method: API + ".adresses_client", args: { client: m.client } })).message || [];
    const d = new frappe.ui.Dialog({
      title: `🚚 Livraison — ${machine}`,
      fields: [
        { fieldtype: "Link", fieldname: "employee", label: "Livreur", options: "Employee", reqd: 1,
          default: m && m.responsable, get_query: () => ({ filters: { status: "Active" } }) },
        { fieldtype: "Date", fieldname: "date", label: "Date", reqd: 1, default: frappe.datetime.add_days(frappe.datetime.get_today(), 1) },
        { fieldtype: "Time", fieldname: "heure", label: "Heure", default: "09:00:00" },
        { fieldtype: "Select", fieldname: "adresse", label: "Adresse de livraison", reqd: adresses.length ? 1 : 0,
          options: adresses.map((a) => a.name).join("\n"), default: adresses.length ? adresses[0].name : "",
          description: adresses.length ? "" : "Ce client n’a pas d’adresse enregistrée : la tâche sera créée sans adresse." },
        { fieldtype: "Small Text", fieldname: "note", label: "Note pour le livreur" },
        { fieldtype: "Check", fieldname: "sms", label: "Prévenir le client par SMS (« nous vous le livrons le … »)", default: 1 },
      ],
      primary_action_label: "Créer la livraison",
      primary_action: async (v) => {
        try {
          const r = await frappe.call({ method: API + ".planifier_livraison",
                                        args: { machine, employee: v.employee, date: v.date, heure: String(v.heure || "09:00").slice(0, 5), adresse: v.adresse || null, note: v.note || null, sms: v.sms ? 1 : 0 } });
          d.hide();
          const x = r.message || {};
          frappe.show_alert({ message: alerte_sms(x.sms, `Livraison ${x.tache} créée le ${x.date} — ${x.employe}${x.adresse ? " · " + x.adresse : ""}`), indicator: "green" }, 8);
          if (opts.apres) opts.apres();
        } catch (e) { /* message déjà affiché */ }
      },
    });
    // libellés lisibles pour les adresses (le Select Frappe n'affiche que la valeur)
    const sel = d.fields_dict.adresse && d.fields_dict.adresse.$input;
    if (sel) adresses.forEach((a) => sel.find(`option[value="${CSS.escape(a.name)}"]`).text(`${a.libelle || a.name}${a.custom_secteur ? " · " + a.custom_secteur : ""}`));
    d.show();
  }

  // ── « La machine est réparée — et maintenant ? » ────────────────────────────
  // Posée à celui qui vient de clôturer la réparation, et disponible depuis l'écran.
  async function suite(machine, opts = {}) {
    const s = (await frappe.call({ method: API + ".situation_sortie", args: { machine } })).message;
    const m = s.machine;
    if (!s.repare) return;                       // entre-temps rendue, ou une autre tâche reste à faire
    const d = new frappe.ui.Dialog({ title: `🧰 ${machine} est réparée — et maintenant ?`, fields: [{ fieldtype: "HTML", fieldname: "zone" }] });
    const bouton = "display:block;width:100%;text-align:left;white-space:normal;padding:10px 12px;margin-bottom:8px;border-radius:10px";
    d.fields_dict.zone.$wrapper.html(`
      <div style="margin-bottom:10px">Client : <b>${esc(m.nom_client || m.client)}</b> · ${esc(m.tel || "")}
        · ${m.garantie === "Sous garantie" ? "🆓 sous garantie" : "hors garantie"}</div>
      <button class="btn btn-primary" style="${bouton}" data-rendre><b>✅ Le client est là : je la lui rends</b>
        <span style="display:block;font-size:12px;opacity:.85">Le dossier se ferme maintenant.</span></button>
      <button class="btn btn-default" style="${bouton}" data-magasin><b>🏬 Elle attend au magasin</b>
        <span style="display:block;font-size:12px;color:var(--text-muted)">Le client viendra la chercher.</span></button>
      <label style="display:block;margin:-2px 0 10px 14px;font-weight:400"><input type="checkbox" data-sms checked> le prévenir par SMS (« votre appareil est réparé… »)</label>
      <button class="btn btn-default" style="${bouton}" data-livrer><b>🚚 Il faut la lui livrer</b>
        <span style="display:block;font-size:12px;color:var(--text-muted)">Une tâche Livraison au calendrier ; sa clôture ferme le dossier.</span></button>`);
    const z = d.fields_dict.zone.$wrapper;
    z.find("[data-rendre]").on("click", () => { d.hide(); rendre(machine, opts); });
    z.find("[data-livrer]").on("click", () => { d.hide(); livraison(machine, m, opts); });
    z.find("[data-magasin]").on("click", async () => {
      const sms = z.find("[data-sms]").is(":checked") ? 1 : 0;
      try {
        // Déjà « Prête au magasin » : seul le SMS reste à envoyer, s'il est demandé.
        const r = m.statut === "Prête au magasin"
          ? (sms ? { message: { sms: (await frappe.call({ method: API + ".envoyer_sms_pret", args: { machine } })).message } } : { message: {} })
          : await frappe.call({ method: API + ".restituer_magasin", args: { machine, sms } });
        d.hide();
        frappe.show_alert({ message: alerte_sms((r.message || {}).sms, `${machine} attend au magasin`), indicator: "green" }, 6);
        if (opts.apres) opts.apres();
      } catch (e) { /* message déjà affiché */ }
    });
    d.show();
  }

  window.ro_sortie = { rendre, sans_reparation, livraison, suite };

  // ── Celui qui clôture la réparation est interrogé tout de suite ─────────────
  $(document).on("app_ready", () => {
    if (!frappe.session || frappe.session.user === "Guest" || !frappe.realtime) return;
    frappe.realtime.on("osm_a_rendre", (data) => {
      if (data && data.machine) setTimeout(() => suite(data.machine).catch(() => {}), 800);   // après l'alerte de clôture
    });
  });

  // ── Bandeau sur la commande et le BL du client ──────────────────────────────
  // Un bloc à nous, posé sous le titre : visible quel que soit l'onglet, et hors de portée des autres scripts
  // (`frm.set_intro("")` de la marge minimale efface tous les messages du formulaire ; le tableau de bord, lui,
  // n'est visible que dans son onglet).
  const bandeau = (frm) => {
    const $zone = $(frm.layout.wrapper);
    $zone.find(".ro-bandeau").remove();
    const client = frm.doc.customer;
    if (!client || frm.is_new() || frm.doc.docstatus === 2 || frm.doc.is_return) return;
    frappe.xcall(API + ".machines_du_client", { client }).then((machines) => {
      $zone.find(".ro-bandeau").remove();
      if (!machines || !machines.length || frm.doc.customer !== client) return;
      const commande = frm.doctype === "Sales Order" ? (frm.doc.docstatus === 1 ? frm.doc.name : null)
        : ((frm.doc.items || []).find((i) => i.against_sales_order) || {}).against_sales_order || null;
      const $b = $(`<div class="ro-bandeau" style="background:#fff7ed;border:1px solid #fdba74;color:#9a3412;border-radius:8px;padding:8px 12px;margin:8px 15px 0;font-size:13px">${
        machines.map((x) => `<div>🧰 La machine <b>${esc(x.name)}</b> de ce client est réparée et attend chez nous depuis le ${esc(jour(x.date_reparee))}.
          <a href="#" class="ro-bandeau-rendre" data-machine="${esc(x.name)}" style="font-weight:700;text-decoration:underline;color:inherit">Elle part avec lui → rendre</a></div>`).join("")}</div>`);
      $b.prependTo($zone);
      $b.find(".ro-bandeau-rendre").on("click", (e) => {
        e.preventDefault();
        rendre($(e.currentTarget).attr("data-machine"), { commande, apres: () => frm.reload_doc() });
      });
    }).catch(() => {});
  };
  frappe.ui.form.on("Sales Order", { refresh: bandeau });
  frappe.ui.form.on("Delivery Note", { refresh: bandeau });
})();
