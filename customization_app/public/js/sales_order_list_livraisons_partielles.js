/**
 * Liste des commandes client — bouton « Livraisons partielles ».
 *
 * Toutes les commandes validées livrées en partie (livré net des retours
 * entre 0 et le total), que leur dette soit surévaluée ou non : le motif
 * d'anomalie ne retient que la dette surévaluée, alors que la décision de
 * ramener une commande à ce qui a été livré se pose pour chacune. Le tableau
 * donne commandé / livré / revenu / réglé / dette affichée / dette réelle et,
 * par ligne, le bouton « Régulariser » (même dialogue que la fiche, partagé
 * par sales_order_livraison_partielle.js) ou ce qui bloque encore.
 *
 * Même accrochage que sales_order_list_alertes.js : on étend le prototype de
 * la vue liste, frappe.listview_settings["Sales Order"] étant réassigné en
 * entier par woocommerce_fusion.
 *
 * Serveur : customization_app.livraison_partielle.livraisons_partielles.
 */

frappe.provide("frappe.views");
frappe.provide("customization_app.livraison_partielle");

(function () {
  const LIBELLE = "Livraisons partielles";

  // Le bouton vit dans la barre des filtres, juste après « Statut Aramex »
  // (demande 30/09) : c'est là que l'on trie les commandes à traiter.
  // Deux pièges : le filtre « Statut Aramex » visible est celui que
  // sales_order_list_alertes.js pose par-dessus le natif (masqué, même
  // fieldname), et il le déplace dans son propre after_render — on se place
  // donc APRÈS toute la chaîne (setTimeout 0) et on se repositionne à chaque
  // rendu, jamais en double.
  function _poser_bouton(listview) {
    setTimeout(() => _placer_bouton(listview), 0);
  }

  function _placer_bouton(listview) {
    const $section = listview.filter_area && listview.filter_area.standard_filters_wrapper;
    if (!$section || !$section.length) return;
    let $btn = $section.find(".so-livraisons-partielles");
    if (!$btn.length) {
      $btn = $(
        `<button type="button" class="btn btn-xs btn-default so-livraisons-partielles"
            title="${__("Toutes les commandes livrées en partie, avec « Régulariser sur le livré »")}"
            style="align-self:center; white-space:nowrap; margin-left:6px;">📦 ${__(LIBELLE)}</button>`
      );
      $btn.on("click", (e) => {
        e.preventDefault();
        ouvrir(listview);
      });
    }
    // Après le ✕ du filtre Statut Aramex (posé par les alertes, masqué tant
    // que rien n'est coché), sinon après le contrôle VISIBLE de ce filtre.
    const $effacer = $section.find(".so-statut-aramex-effacer").last();
    const $statut = $section
      .find('[data-fieldname="custom_statut_aramex"]')
      .filter((_, el) => $(el).css("display") !== "none")
      .last();
    const $ancre = $effacer.length ? $effacer : $statut;
    if ($ancre.length) {
      if ($ancre.next()[0] !== $btn[0]) $btn.insertAfter($ancre);
    } else if (!$btn.parent().length) {
      $section.append($btn);
    }
  }

  function ouvrir(listview, dialogue_precedent) {
    frappe.call({
      method: "customization_app.livraison_partielle.livraisons_partielles",
      freeze: true,
      freeze_message: __("Recherche des livraisons partielles…"),
      callback: (r) => {
        if (dialogue_precedent) dialogue_precedent.hide();
        afficher(listview, r.message || { commandes: [] });
      },
    });
  }

  function afficher(listview, reponse) {
    const commandes = reponse.commandes || [];
    const esc = frappe.utils.escape_html;
    // <bdi> : « د.ت » s'écrit de droite à gauche et retourne ce qui l'entoure.
    const f = (v, dev) => `<bdi>${format_currency(v, dev)}</bdi>`;
    const avec_retour = commandes.some((c) => flt(c.retourne) > 0);

    let corps;
    if (!commandes.length) {
      corps = `<p style="color:#6b7280;">${__("Aucune commande livrée en partie.")}</p>`;
    } else {
      const lignes = commandes
        .map((c, i) => {
          const dev = c.devise;
          const action = c.regularisable
            ? `<button class="btn btn-xs btn-primary so-lp-regulariser" data-i="${i}">${__("Régulariser")}</button>`
            : `<span style="color:#6b7280;">${esc(c.empechement || "")}</span>`;
          const dette = c.surevaluee
            ? `<b style="color:#d46b08;" title="${__("Dette surévaluée : compte de la marchandise jamais sortie")}">${f(c.dette_ligne, dev)}</b>`
            : f(c.dette_ligne, dev);
          const retour = avec_retour
            ? `<td style="text-align:right; color:#b91c1c;">${flt(c.retourne) > 0 ? f(c.retourne, dev) : ""}</td>`
            : "";
          return `<tr>
            <td style="white-space:nowrap;">
              <a href="/app/sales-order/${encodeURIComponent(c.name)}" style="font-weight:600;">${esc(c.name)}</a>
              <div style="color:#6b7280; font-size:11px;">${esc(frappe.datetime.str_to_user(c.date) || c.date)} · ${esc(__(c.status || ""))}</div>
            </td>
            <td>${esc(c.customer_name || c.customer || "")}</td>
            <td style="text-align:right;">${f(c.total, dev)}</td>
            <td style="text-align:right;"><b>${f(c.livre, dev)}</b></td>
            ${retour}
            <td style="text-align:right;">${f(c.regle, dev)}</td>
            <td style="text-align:right;">${dette}</td>
            <td style="text-align:right;">${f(c.dette_reelle, dev)}</td>
            <td>${action}</td>
          </tr>`;
        })
        .join("");
      corps = `<div style="overflow:auto; max-height:60vh;">
        <table class="table table-bordered table-sm" style="font-size:12.5px; margin:0;">
          <thead><tr>
            <th>${__("Commande")}</th>
            <th>${__("Client")}</th>
            <th style="text-align:right;">${__("Commandé")}</th>
            <th style="text-align:right;">${__("Livré")}</th>
            ${avec_retour ? `<th style="text-align:right;">${__("Revenu")}</th>` : ""}
            <th style="text-align:right;">${__("Réglé")}</th>
            <th style="text-align:right;">${__("Dette affichée")}</th>
            <th style="text-align:right;">${__("Dette réelle")}</th>
            <th>${__("Régulariser ?")}</th>
          </tr></thead>
          <tbody>${lignes}</tbody>
        </table>
      </div>`;
    }

    const d = new frappe.ui.Dialog({
      title: __(LIBELLE),
      size: "extra-large",
      fields: [
        {
          fieldname: "explication",
          fieldtype: "HTML",
          options: `<div style="margin-bottom:8px; line-height:1.6;">
            ${__("Commandes validées dont une partie seulement est sortie (bons de livraison, nets des retours).")}
            ${__("« Régulariser » ramène la commande à ce qui a été livré, au tarif des BL, et sa dette au reste dû — le client ne prendra pas le reste.")}
            <span style="color:#6b7280;">${__("{0} commande(s).", [commandes.length])}</span>
          </div>`,
        },
        { fieldname: "tableau", fieldtype: "HTML", options: corps },
      ],
      primary_action_label: commandes.length ? __("Afficher dans la liste") : __("Fermer"),
      primary_action() {
        d.hide();
        if (!commandes.length) return;
        // Le filtre « nom parmi » fait de la liste elle-même la vue de travail :
        // pastilles, colonnes et ouverture d'une commande comme d'habitude.
        listview.filter_area.clear(false).then(() =>
          listview.filter_area.add([["Sales Order", "name", "in", commandes.map((c) => c.name)]])
        );
      },
    });
    d.$wrapper.on("click", ".so-lp-regulariser", (e) => {
      const c = commandes[cint($(e.currentTarget).data("i"))];
      if (!c) return;
      // Même dialogue que la fiche ; une fois faite, la liste se recharge.
      customization_app.livraison_partielle.confirmer(c.name, c.devise, c, () => ouvrir(listview, d));
    });
    d.show();
  }

  const _after_render = frappe.views.ListView.prototype.after_render;
  frappe.views.ListView.prototype.after_render = function () {
    _after_render.apply(this, arguments);
    if (this.doctype !== "Sales Order") return;
    try {
      _poser_bouton(this);
    } catch (e) {
      console.error("Livraisons partielles :", e);
    }
  };
})();
