// Voir le scan pendant qu-on saisit la facture ou la commande d-achat.
//
// ⚠️ UN PANNEAU, PAS UNE POP-UP. Une fenetre modale bloque le formulaire : on la ferme pour
// taper, on la rouvre pour relire, et on recommence a chaque ligne. Le document se pose donc a
// COTE, docke a droite, et la saisie continue dessous — c-est tout l-interet du bouton.
//
// ⚠️ ET LE SCAN N-EST PAS SUR LA PIECE QU-ON SAISIT. Il a ete pris en caisse et attache a la
// fiche de la file ; la facture d-achat, elle, nait vide. Le serveur fait le chemin inverse,
// y compris AVANT enregistrement, par le numero de facture que le bouton vient de preremplir.

const API_SCANS = "customization_app.caisse_depenses.scans_a_saisir";
const ID_PANNEAU = "cad-panneau";

["Purchase Invoice", "Purchase Order", "Purchase Receipt"].forEach((dt) => {
  frappe.ui.form.on(dt, {
    refresh(frm) {
      if (frm.doc.docstatus === 2) return;
      frm.add_custom_button(__("📄 Voir le document"), () => basculer(frm));
    },
    onload_post_render(frm) {
      // Le panneau ne survit pas au changement de fiche : on le referme pour ne pas laisser
      // le scan d-une autre piece a l-ecran.
      fermer();
    },
  });
});

function poser_css() {
  if (document.getElementById("cad-css")) return;
  const st = document.createElement("style");
  st.id = "cad-css";
  st.textContent = `
    #${ID_PANNEAU} { position: fixed; top: 0; right: 0; bottom: 0; width: 42vw;
        min-width: 340px; background: var(--card-bg, #fff); z-index: 1010;
        border-left: 1px solid var(--border-color, #d5dae1);
        box-shadow: -8px 0 24px rgba(0,0,0,.12); display: flex; flex-direction: column; }
    #${ID_PANNEAU} .cad-tete { display: flex; align-items: center; gap: 8px; padding: 8px 10px;
        border-bottom: 1px solid var(--border-color, #e6e9ee); font-size: 12.5px; }
    #${ID_PANNEAU} .cad-tete select { flex: 1; height: 28px; font-size: 12px; }
    #${ID_PANNEAU} .cad-corps { flex: 1; overflow: auto; background: #f4f5f7; }
    #${ID_PANNEAU} .cad-corps iframe { width: 100%; height: 100%; border: 0; }
    #${ID_PANNEAU} .cad-corps img { width: 100%; display: block; }
    #${ID_PANNEAU} .cad-btn { border: 1px solid var(--border-color, #d5dae1); background: #fff;
        border-radius: 6px; padding: 2px 9px; font-size: 12px; cursor: pointer; }
    /* Le formulaire se retrecit d-autant : sans cela le panneau recouvre les champs de droite,
       et on saisit a l-aveugle. */
    body.cad-ouvert .layout-main, body.cad-ouvert .page-head > .container {
        padding-right: 43vw !important; }
    @media (max-width: 900px) {
      #${ID_PANNEAU} { width: 100vw; }
      body.cad-ouvert .layout-main, body.cad-ouvert .page-head > .container {
          padding-right: 0 !important; }
    }`;
  document.head.appendChild(st);
}

function fermer() {
  const p = document.getElementById(ID_PANNEAU);
  if (p) p.remove();
  document.body.classList.remove("cad-ouvert");
}

async function basculer(frm) {
  if (document.getElementById(ID_PANNEAU)) return fermer();
  let r;
  try {
    r = (await frappe.call({
      method: API_SCANS, freeze: true, freeze_message: __("Recherche du document…"),
      args: {
        doctype: frm.doc.doctype,
        name: frm.is_new() ? null : frm.doc.name,
        fiche: frm.doc.custom_fiche_caisse || null,
        bill_no: frm.doc.bill_no || null,
        supplier: frm.doc.supplier || null,
      },
    })).message;
  } catch (e) { return; }

  const scans = (r && r.scans) || [];
  if (!scans.length) {
    frappe.msgprint({
      title: __("Aucun document"),
      indicator: "orange",
      message: __("Aucun scan n-a été trouvé pour cette pièce. Il est cherché sur la pièce elle-même, puis sur la fiche de la file — par son numéro de facture tant que la pièce n-est pas enregistrée."),
    });
    return;
  }
  ouvrir(scans);
}

function ouvrir(scans) {
  poser_css();
  const esc = frappe.utils.escape_html;
  const el = document.createElement("div");
  el.id = ID_PANNEAU;
  el.innerHTML = `
    <div class="cad-tete">
      <select>${scans.map((s, i) =>
        `<option value="${i}">${esc(s.nom)} — ${esc(s.origine)}</option>`).join("")}</select>
      <button class="cad-btn" data-onglet>${__("Onglet")}</button>
      <button class="cad-btn" data-fermer>✕</button>
    </div>
    <div class="cad-corps"></div>`;
  document.body.appendChild(el);
  document.body.classList.add("cad-ouvert");

  const $corps = el.querySelector(".cad-corps");
  const montrer = (i) => {
    const s = scans[i];
    // Le PDF passe par un iframe, l-image par une balise img : un iframe sur une image
    // n-offre ni zoom ni defilement utilisable.
    $corps.innerHTML = s.pdf
      ? `<iframe src="${esc(s.url)}#toolbar=1&navpanes=0"></iframe>`
      : `<img src="${esc(s.url)}" alt="${esc(s.nom)}">`;
    el.querySelector("[data-onglet]").onclick = () => window.open(s.url, "_blank");
  };
  el.querySelector("select").onchange = (e) => montrer(Number(e.target.value));
  el.querySelector("[data-fermer]").onclick = fermer;
  montrer(0);
}
