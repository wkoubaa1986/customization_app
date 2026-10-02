/**
 * Stock cible (réglage « Config Stock Entrepot » → stock cible générique, et fiche « Stock Cible » d'un
 * véhicule) : seuls les articles suivis en stock sont proposés, et une liste se colle en un bloc.
 *
 * « 📋 Coller une liste » : une ligne par article — code ou désignation, puis la quantité (tabulation,
 * « ; », « , », « x » ou espace) ; sans quantité, la quantité par défaut. Les lignes reconnues entrent
 * dans la table (un article déjà présent prend la nouvelle quantité), les inconnues sont listées.
 * Règle de lecture côté serveur : customization_app.stock_entrepots.resoudre_liste_articles.
 */
(() => {
  const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));

  function filtrer_articles(frm, table) {
    frm.set_query("item_code", table, () => ({ filters: { is_stock_item: 1, disabled: 0 } }));
  }

  function coller_liste(frm, table) {
    const d = new frappe.ui.Dialog({
      title: "📋 Coller une liste d’articles",
      fields: [
        { fieldtype: "HTML", options: `<div class="text-muted" style="font-size:12.5px;margin-bottom:6px">
            Une ligne par article : <b>code ou désignation</b>, puis la quantité (tabulation, « ; », « x » ou espace).<br>
            Exemples : <code>C-10'-CTO 10</code> · <code>Cartouche, UDF 10' ; 5</code> · <code>PF-10'-PP-UDF-CTO</code> (quantité par défaut).<br>
            Copié depuis Excel, deux colonnes code / quantité conviennent telles quelles.</div>` },
        { fieldtype: "Small Text", fieldname: "texte", label: "Liste", reqd: 1 },
        { fieldtype: "Float", fieldname: "qte_defaut", label: "Quantité par défaut (ligne sans quantité)", default: 1 },
        { fieldtype: "Check", fieldname: "remplacer", label: "Remplacer la table (sinon : ajouter aux lignes existantes)" },
      ],
      primary_action_label: "Ajouter à la table",
      primary_action: async (v) => {
        const r = (await frappe.call({ method: "customization_app.stock_entrepots.resoudre_liste_articles",
          args: { texte: v.texte, qte_defaut: v.qte_defaut || 1 }, freeze: true })).message;
        d.hide();
        if (v.remplacer) frm.clear_table(table);
        let ajoutes = 0, maj = 0;
        r.lignes.forEach((l) => {
          const existe = (frm.doc[table] || []).find((x) => x.item_code === l.item_code);
          if (existe) { frappe.model.set_value(existe.doctype, existe.name, "qte_cible", l.qte); maj += 1; }
          else { const row = frm.add_child(table); row.item_code = l.item_code; row.item_name = l.item_name; row.qte_cible = l.qte; ajoutes += 1; }
        });
        frm.refresh_field(table);
        frm.dirty();
        const probleme = [];
        if (r.inconnus.length) probleme.push(`<b>Introuvables (${r.inconnus.length})</b> : ${r.inconnus.map(esc).join(", ")}`);
        if (r.non_suivis.length) probleme.push(`<b>Non suivis en stock / désactivés (${r.non_suivis.length})</b> : ${r.non_suivis.map(esc).join(", ")}`);
        if (probleme.length) frappe.msgprint({ title: "Lignes non reprises", indicator: "orange", message: probleme.join("<br>") });
        frappe.show_alert({ message: `📋 ${ajoutes} article(s) ajouté(s), ${maj} quantité(s) mise(s) à jour — enregistrez la fiche`, indicator: "green" }, 6);
      },
    });
    d.show();
  }

  function brancher(doctype, table) {
    frappe.ui.form.on(doctype, {
      setup(frm) { filtrer_articles(frm, table); },
      refresh(frm) {
        frm.add_custom_button("📋 Coller une liste", () => coller_liste(frm, table));
      },
    });
  }

  brancher("Config Stock Entrepot", "modele_cible");
  brancher("Stock Cible", "lignes");
})();
