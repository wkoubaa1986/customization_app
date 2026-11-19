/**
 * Commande client — « 🔁 Échange d'articles » (19/11/2026).
 *
 * Le cas : le client a reçu le mauvais modèle ; on lui renvoie le bon par Aramex sur une commande qui ne porte que
 * « Livraison », et il renvoie l'autre dans une pochette. Jusqu'ici la consigne s'écrivait dans la tâche, et le stock
 * se corrigeait à la main quand la pochette arrivait.
 *
 * Ici on DÉCLARE l'échange : ce que j'envoie SORT du stock tout de suite ; ce que le client renvoie est ATTENDU —
 * l'échange reste en tête de l'écran Livraisons Aramex jusqu'au bouton « Échange reçu » (photo vérifiée, entrée en
 * stock). Toute la logique est côté serveur : customization_app.echange_aramex.
 */
(function () {
  const M = "customization_app.echange_aramex.";
  const ROLES = ["System Manager", "Accounts Manager", "Accounts User", "Sales Manager", "Sales User",
                 "Stock Manager", "Stock User"];
  const table = (fieldname, label, data, description) => ({
    fieldtype: "Table", fieldname, label, description, in_place_edit: true, reqd: 1, data,
    fields: [
      { fieldtype: "Link", fieldname: "item_code", label: __("Article"), options: "Item", in_list_view: 1, reqd: 1,
        columns: 7, get_query: () => ({ filters: { is_stock_item: 1, disabled: 0 } }) },
      { fieldtype: "Float", fieldname: "qty", label: __("Quantité"), in_list_view: 1, reqd: 1, columns: 3, default: 1 },
    ],
  });

  function ouvrir(frm) {
    frappe.call({ method: M + "proposer", args: { commande: frm.doc.name } }).then((r) => {
      const p = r.message || {};
      if (p.existant) {
        frappe.set_route("Form", "Echange Aramex", p.existant.name);
        return;
      }
      const esc = frappe.utils.escape_html;
      const d = new frappe.ui.Dialog({
        title: __("Échange d’articles — {0}", [esc(p.client || "")]),
        size: "large",
        fields: [
          { fieldtype: "HTML", fieldname: "intro" },
          table("attendus", __("Le client me renvoie"), (p.attendus || []).map((l) => ({ item_code: l.item_code, qty: l.qty })),
                p.bl_origine ? __("Prérempli avec sa dernière livraison ({0} du {1}) : corrigez si besoin.",
                                  [p.bl_origine, frappe.datetime.str_to_user(p.date_origine)]) : ""),
          table("envoyes", __("J’envoie au client"), [],
                p.date_sortie ? __("Ces articles sortent du stock à la date de validation de la commande : le {0}.", [frappe.datetime.str_to_user(p.date_sortie)])
                              : __("Ces articles sortent du stock dès que vous déclarez l’échange.")),
          { fieldtype: "Link", fieldname: "entrepot", label: __("Entrepôt (d’où ça part, où ça revient)"), options: "Warehouse",
            default: p.entrepot, reqd: 1, get_query: () => ({ filters: { is_group: 0, disabled: 0 } }) },
          { fieldtype: "Small Text", fieldname: "note", label: __("Note (facultatif)") },
        ],
        primary_action_label: p.date_sortie ? __("Déclarer l’échange — sortie de stock au {0}", [frappe.datetime.str_to_user(p.date_sortie.slice(0, 10))])
                                            : __("Déclarer l’échange — le stock sort maintenant"),
        primary_action: (v) => {
          const lignes = (t) => (t || []).filter((l) => l.item_code && flt(l.qty) > 0).map((l) => ({ item_code: l.item_code, qty: flt(l.qty) }));
          frappe.call({
            method: M + "declarer", type: "POST", freeze: true, freeze_message: __("Sortie de stock…"),
            args: { commande: frm.doc.name, envoyes: lignes(v.envoyes), attendus: lignes(v.attendus), entrepot: v.entrepot,
                    commande_origine: p.commande_origine || null, note: v.note },
            callback: (rr) => {
              d.hide();
              const m = rr.message || {};
              frappe.msgprint({ title: __("Échange déclaré"), indicator: "green",
                message: __("Échange {0} : les articles envoyés sont sortis du stock (écriture {1}, datée du {3}). Quand la pochette revient, cliquez « Échange reçu » en haut de l’écran {2}.",
                  [`<a href="/app/echange-aramex/${encodeURIComponent(m.echange)}">${esc(m.echange)}</a>`, esc(m.sortie_stock || ""),
                   `<a href="/app/livraison-aramex">${__("Livraisons Aramex")}</a>`, esc(frappe.datetime.str_to_user(m.date_sortie || ""))]) });
              frm.reload_doc();
            },
          });
        },
      });
      d.fields_dict.intro.$wrapper.html(`<div class="text-muted" style="font-size:12.5px;margin-bottom:8px">
        ${__("Le client a reçu le mauvais article : vous lui envoyez le bon, il vous renvoie l’autre. La facturation ne change pas — seul le stock bouge : ce que vous envoyez sort à la date de validation de la commande, ce qui revient rentrera quand vous cliquerez « Échange reçu ».")}
        ${p.bordereau ? "" : `<br>${__("Cette commande n’a pas encore de bordereau Aramex : il sera repris tout seul quand vous le créerez.")}`}
      </div>`);
      d.show();
    });
  }

  frappe.ui.form.on("Sales Order", {
    refresh(frm) {
      if (frm.is_new() || frm.doc.docstatus === 2) return;
      if (!ROLES.some((r) => frappe.user.has_role(r))) return;
      frm.add_custom_button(__("🔁 Échange d’articles"), () => ouvrir(frm));
    },
  });
})();
