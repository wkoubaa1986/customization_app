/**
 * Position du téléphone de l'employé, relevée À CHAQUE ACTION dans l'app (demande du 20/11/2026).
 *
 * `window.flotte_position_capturer(action, doctype, name)` : appelé juste après un geste (clôture d'une
 * intervention, photo, appel noté, comptage de caisse). Il ne bloque jamais le geste et ne montre jamais d'erreur :
 * un refus de localisation, un téléphone sans GPS ou une position trop vague passent en silence.
 *
 *   - haute précision demandée (GPS), jusqu'à 20 s d'écoute : la première position à ≤ 150 m gagne, sinon la
 *     meilleure vue ; le serveur refuse au-delà de 200 m (position réseau, à 1-3 km : elle tromperait) ;
 *   - le navigateur affiche LUI-MÊME la demande d'autorisation du téléphone au premier relevé — aucun code ne peut
 *     l'éviter ; l'app prévient une fois, avant, pour que l'employé comprenne ce qu'on lui demande ;
 *   - un même geste n'est relevé qu'une fois (deux photos en 30 s = un relevé).
 *
 * Ce que le serveur en fait : customization_app.flotte_positions (qui est dans quel véhicule, position d'un client
 * qu'on ne savait pas placer).
 */
(() => {
  const API = "customization_app.flotte_positions.enregistrer";
  const CLE_INFO = "fp_info_vue";
  const PRECISION_OK_M = 150;            // on s'arrête d'écouter dès cette précision
  const ATTENTE_MS = 20000;
  const ENTRE_DEUX_MS = 30000;           // deux gestes à moins de 30 s = un seul relevé
  let dernier = 0, en_cours = false;

  const info_une_fois = () => {
    try {
      if (localStorage.getItem(CLE_INFO)) return;
      localStorage.setItem(CLE_INFO, "1");
    } catch (e) { return; }
    frappe.show_alert({
      message: __("📍 Votre position est enregistrée au moment de vos interventions (clôture, photos, appels), pour le suivi terrain. Votre téléphone va vous demander l’autorisation."),
      indicator: "blue",
    }, 12);
  };

  // Même écoute que le bouton « Ma position » de la fiche tâche (tache_photos_cloture.js) : la première position
  // assez précise gagne, sinon la meilleure vue avant la fin de l'attente.
  const position = (ok, ko) => {
    let meilleure = null, fini = false, watch = null;
    const terminer = (fn, ...args) => {
      if (fini) return;
      fini = true;
      if (watch !== null) navigator.geolocation.clearWatch(watch);
      fn(...args);
    };
    const timer = setTimeout(() => (meilleure ? terminer(ok, meilleure) : terminer(ko, null)), ATTENTE_MS);
    watch = navigator.geolocation.watchPosition(
      (pos) => {
        const acc = pos.coords.accuracy;
        if (!meilleure || (acc || Infinity) < (meilleure.coords.accuracy || Infinity)) meilleure = pos;
        if (acc != null && acc <= PRECISION_OK_M) { clearTimeout(timer); terminer(ok, pos); }
      },
      (err) => { clearTimeout(timer); meilleure ? terminer(ok, meilleure) : terminer(ko, err); },
      { enableHighAccuracy: true, timeout: ATTENTE_MS, maximumAge: 0 }
    );
  };

  window.flotte_position_capturer = (action, doctype, name) => {
    try {
      if (!navigator.geolocation || !frappe.session || frappe.session.user === "Guest") return;
      if (en_cours || Date.now() - dernier < ENTRE_DEUX_MS) return;
      en_cours = true;
      info_une_fois();
      position(
        (pos) => {
          en_cours = false; dernier = Date.now();
          frappe.call({
            method: API, type: "POST",
            args: { lat: pos.coords.latitude, lng: pos.coords.longitude, precision: Math.round(pos.coords.accuracy || 0),
                    action: action || "autre", doctype: doctype || null, name: name || null },
          }).catch(() => {});
        },
        () => { en_cours = false; }                      // refusé, indisponible, trop long : rien, sans bruit
      );
    } catch (e) { en_cours = false; }
  };
})();
