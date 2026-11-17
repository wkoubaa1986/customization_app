/**
 * Sales Order — la case « Appliquer Ristourne » est DOUBLÉE dans « Termes de paiement ».
 *
 * Demande du 17/11/2026 : « déplace-la là-bas, laisse celle d'en haut ; en gros tu la dupliques, elle sera
 * tout le temps liée : celle d'en haut change, celle d'en bas change, et vice versa ».
 *
 * La case d'en haut (champs `custom_appliquer_ristourne` et `custom_ristourne_disponible`, application
 * booking_ristourne, Client Script « Ristourne ») reste LA case : c'est elle qui porte la valeur et qui
 * déclenche le calcul de la remise. Celle d'en bas n'est qu'un MIROIR, posé sous « Paiement avec Avoir » :
 *   - on la change  -> elle écrit dans la case d'en haut (`frm.set_value`), qui fait tout le reste ;
 *   - la case d'en haut change (valeur, affichée ou non, note, lecture seule) -> le miroir suit.
 * Aucune donnée de plus, aucun calcul ici : il ne peut donc pas y avoir deux valeurs différentes.
 *
 * Le Client Script « Ristourne » montre et cache sa case APRÈS un appel au serveur : pour suivre sans rien
 * savoir de son rythme, le miroir observe les deux champs d'en haut (MutationObserver).
 */
(function () {
  const CASE = "custom_appliquer_ristourne";
  const NOTE = "custom_ristourne_disponible";
  const ANCRES = ["custom_paiement_avec_avoir", "payment_terms_template"];   // sous la case d'avoir, sinon au-dessus du modèle
  const CLASSE = "so-ristourne-miroir";

  function oui(v) {
    return v === 1 || v === true || v === "Oui" || v === "Yes";
  }

  function miroir(frm) {
    let $m = frm.layout.wrapper.find("." + CLASSE);
    if ($m.length) return $m;
    const ancre = ANCRES.map((n) => frm.fields_dict[n]).find((f) => f && f.wrapper);
    if (!ancre) return $();
    $m = $(`<div class="${CLASSE} frappe-control" style="margin-bottom:14px;display:none">
        <div class="so-ristourne-note" style="margin-bottom:6px"></div>
        <div class="form-group" style="margin-bottom:0;max-width:50%;min-width:260px">
          <div class="clearfix"><label class="control-label" style="padding-right:0">${__("Appliquer Ristourne")}</label></div>
          <div class="control-input-wrapper"><div class="control-input">
            <select class="input-with-feedback form-control ellipsis" aria-label="${__("Appliquer Ristourne")}">
              <option value="Non">${__("Non")}</option><option value="Oui">${__("Oui")}</option>
            </select></div></div>
        </div>
      </div>`);
    // Sous la case d'avoir ; à défaut (champ absent), juste avant le modèle de termes de paiement.
    if (ancre.df.fieldname === "payment_terms_template") $m.insertBefore(ancre.wrapper);
    else $m.insertAfter(ancre.wrapper);
    $m.find("select").on("change", function () {
      // Le miroir n'écrit que dans la case d'en haut : c'est elle qui applique ou retire la ristourne.
      const voulu = $(this).val();
      if ((oui(frm.doc[CASE]) ? "Oui" : "Non") !== voulu) frm.set_value(CASE, voulu);
    });
    return $m;
  }

  function suivre(frm) {
    const haut = frm.fields_dict[CASE];
    if (!haut) return;                                  // booking_ristourne absent : pas de miroir
    const $m = miroir(frm);
    if (!$m.length) return;
    // Affiché quand la case d'en haut l'est (le Client Script la cache si le client n'a pas de ristourne).
    const visible = !haut.df.hidden;
    $m.toggle(!!visible);
    if (!visible) return;
    const note = frm.fields_dict[NOTE];
    const html = note ? note.df.options || "" : "";
    const $note = $m.find(".so-ristourne-note");
    if ($note.data("html") !== html) $note.data("html", html).html(html);
    const $select = $m.find("select");
    const valeur = oui(frm.doc[CASE]) ? "Oui" : "Non";
    if ($select.val() !== valeur) $select.val(valeur);
    $select.prop("disabled", frm.doc.docstatus !== 0 || !!haut.df.read_only);
  }

  function observer(frm) {
    if (frm._ristourne_observateur) frm._ristourne_observateur.disconnect();
    if (typeof MutationObserver === "undefined") return;
    let attente = null;
    const obs = new MutationObserver(() => {
      clearTimeout(attente);
      attente = setTimeout(() => suivre(frm), 80);
    });
    [CASE, NOTE].forEach((n) => {
      const f = frm.fields_dict[n];
      if (f && f.wrapper) obs.observe(f.wrapper, { attributes: true, childList: true, subtree: true, characterData: true });
    });
    frm._ristourne_observateur = obs;
  }

  function plus_tard(frm) {
    suivre(frm);
    [300, 1200, 3000].forEach((ms) => setTimeout(() => suivre(frm), ms));   // le calcul de la ristourne répond du serveur
  }

  frappe.ui.form.on("Sales Order", {
    refresh(frm) {
      observer(frm);
      plus_tard(frm);
    },
    customer: plus_tard,
    transaction_date: plus_tard,
    [CASE]: plus_tard,
    discount_amount: (frm) => suivre(frm),
  });
})();
