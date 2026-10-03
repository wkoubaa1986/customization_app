/**
 * « Clients partenaire » — qui est géré par le partenaire (exclu de nos relances), qui est à vérifier (fiche créée par
 * son compte mais non marquée, ou client à nous qu'il travaille), et nos clients dans ses zones d'exécution.
 * Les règles et les écritures sont côté serveur (customization_app.partenaire_clients) ; ici on coche et on bascule.
 */
frappe.pages["clients-partenaire"].on_page_load = function (wrapper) {
  frappe.ui.make_app_page({ parent: wrapper, title: "Clients partenaire", single_column: true });
  $(wrapper).find(".layout-main-section").html(frappe.render_template("clients_partenaire", {}));
  wrapper.cp = new ClientsPartenaire(wrapper);
};
frappe.pages["clients-partenaire"].on_page_show = function (wrapper) {
  if (wrapper.cp && wrapper.cp.pret) wrapper.cp.charger();
};

const CP_API = "customization_app.partenaire_clients";
const cp_esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
const CP_EXPLICATIONS = {
  geres: "Ces clients sont relancés par le partenaire : nos SMS, e-mails et listes d’appels d’entretien les ignorent. « Repasser chez nous » les réintègre.",
  a_verifier: "Fiches créées par le compte partenaire mais non marquées, ou clients créés par nous dont les commandes ou la majorité des tâches sont à lui. À trancher : géré par lui, ou chez nous.",
  zones: "Nos clients dans les gouvernorats du partenaire : relancés par nous (SMS dédié, liste d’appels « Zone partenaire »), rendez-vous exécutés par lui.",
  recherche: "Tapez un nom, un code client ou un numéro de téléphone.",
};

class ClientsPartenaire {
  constructor(wrapper) {
    this.$root = $(wrapper).find(".cp-page");
    this.onglet = null;
    this.lignes = [];
    this.pret = true;
    this.$root.on("click", ".cp-kpi", (e) => this.aller($(e.currentTarget).data("onglet")));
    this.$root.find("#cp-recherche").on("keydown", (e) => { if (e.key === "Enter") this.charger(); });
    this.$root.find("#cp-tout").on("click", () => {
      const $c = this.$root.find("input.cp-coche");
      const tous = $c.length && $c.filter(":checked").length === $c.length;
      $c.prop("checked", !tous);
    });
    this.$root.find("#cp-marquer").on("click", () => this.basculer(1));
    this.$root.find("#cp-reprendre").on("click", () => this.basculer(0));
    this.$root.on("click", ".cp-ligne-bascule", (e) => {
      const $b = $(e.currentTarget);
      this.basculer(Number($b.data("valeur")), [$b.data("client")]);
    });
    this.charger();
  }

  async charger() {
    const r = (await frappe.call({ method: CP_API + ".resume" })).message || {};
    this.resume = r;
    if (!r.champs) {
      this.$root.find("#cp-intro").html("Les champs « Géré par le partenaire » n’existent pas encore sur la fiche client : jouer le patch ensure_partenaire_fields (migrate).");
      return;
    }
    const zones = (r.zones || []).map((z) => `<b>${cp_esc(z.nom)}</b> : ${cp_esc(z.gouvernorats.join(", "))}`).join(" · ") || "aucune zone déclarée (Config Portail RDV → Partenaires)";
    this.$root.find("#cp-intro").html(
      `Compte partenaire : <b>${cp_esc(r.partenaire_user)}</b>${r.partenaire_employe ? ` (employé ${cp_esc(r.partenaire_employe)})` : ""}. ` +
      `Une fiche créée par ce compte est marquée automatiquement « gérée par le partenaire » et sort de nos relances. Zones d’exécution — ${zones}.`
    );
    this.$root.find("#cp-n-geres").text(r.geres);
    this.$root.find("#cp-n-verif").text(r.a_verifier);
    this.$root.find("#cp-n-zones").text(r.nos_en_zone);
    if (!this.onglet) this.onglet = r.a_verifier ? "a_verifier" : "geres";
    await this.lister();
  }

  aller(onglet) {
    this.onglet = onglet;
    this.lister();
  }

  async lister() {
    this.$root.find(".cp-kpi").removeClass("actif").filter(`[data-onglet="${this.onglet}"]`).addClass("actif");
    this.$root.find("#cp-explication").text(CP_EXPLICATIONS[this.onglet] || "");
    const $rech = this.$root.find("#cp-recherche").toggle(this.onglet === "recherche");
    if (this.onglet === "recherche" && ($rech.val() || "").trim().length < 4) {
      this.$root.find("#cp-liste").html(`<div class="cp-vide">Saisissez au moins 4 caractères puis Entrée.</div>`);
      $rech.focus();
      return;
    }
    this.$root.find("#cp-liste").html(`<div class="cp-vide">Chargement…</div>`);
    this.lignes = (await frappe.call({ method: CP_API + ".liste", args: { onglet: this.onglet, recherche: $rech.val() } })).message || [];
    this.rendre();
  }

  rendre() {
    if (!this.lignes.length) {
      this.$root.find("#cp-liste").html(`<div class="cp-vide">Aucun client dans cet onglet.</div>`);
      return;
    }
    const ligne = (l) => {
      const etat = l.gere ? `<span class="cp-badge gere">🤝 géré par ${cp_esc(l.partenaire || "le partenaire")}</span>` : `<span class="cp-badge nous">chez nous</span>`;
      const origine = l.cree_par_partenaire ? `<span class="cp-badge compte">compte partenaire</span>` : cp_esc((l.cree_par || "").split("@")[0]);
      const zone = l.zone ? `<span class="cp-badge zone">📍 ${cp_esc(l.zone)}</span>` : "";
      const bouton = l.gere
        ? `<button class="btn btn-xs btn-default cp-ligne-bascule" data-client="${cp_esc(l.name)}" data-valeur="0">↩️ chez nous</button>`
        : `<button class="btn btn-xs btn-default cp-ligne-bascule" data-client="${cp_esc(l.name)}" data-valeur="1">🤝 partenaire</button>`;
      return `<tr>
        <td><input type="checkbox" class="cp-coche" value="${cp_esc(l.name)}"></td>
        <td><a href="/app/customer/${encodeURIComponent(l.name)}" target="_blank"><b>${cp_esc(l.client)}</b></a><br><span class="text-muted" style="font-size:11px">${cp_esc(l.telephone)}</span></td>
        <td>${cp_esc(l.gouvernorats)} ${zone}<br><span class="text-muted" style="font-size:11px">${cp_esc(l.secteurs)}</span></td>
        <td>${origine}<br><span class="text-muted" style="font-size:11px">${cp_esc(l.creation)}</span></td>
        <td class="cp-num" title="commandes du partenaire / total"><b>${l.commandes_partenaire}</b> / ${l.commandes}</td>
        <td class="cp-num" title="tâches du partenaire / total sur 12 mois"><b>${l.taches_partenaire}</b> / ${l.taches}<br><span class="text-muted" style="font-size:11px">${cp_esc(l.derniere_tache)}</span></td>
        <td class="cp-num">${cp_esc(l.prochaine_echeance) || "—"}</td>
        <td>${etat}</td>
        <td>${bouton}</td></tr>`;
    };
    this.$root.find("#cp-liste").html(`<table class="cp-table"><thead><tr>
      <th></th><th>Client</th><th>Gouvernorat · secteur</th><th>Créé par</th><th>Cmd. part.</th><th>Tâches part. (12 m)</th><th>Proch. échéance</th><th>État</th><th></th>
      </tr></thead><tbody>${this.lignes.map(ligne).join("")}</tbody></table>
      <div class="text-muted" style="font-size:11.5px;margin-top:6px">${this.lignes.length} client(s). « Cmd. part. » = commandes passées par le compte partenaire ; « Tâches part. » = tâches affectées au partenaire.</div>`);
  }

  async basculer(valeur, clients) {
    clients = clients || this.$root.find("input.cp-coche:checked").map((_i, el) => el.value).get();
    if (!clients.length) {
      frappe.show_alert({ message: "Cochez au moins un client.", indicator: "orange" });
      return;
    }
    const q = valeur
      ? `Marquer ${clients.length} client(s) « géré(s) par le partenaire » ? Ils sortiront de nos relances SMS, e-mail et appels.`
      : `Reprendre ${clients.length} client(s) chez nous ? Ils seront de nouveau relancés par notre équipe.`;
    frappe.confirm(q, async () => {
      const r = (await frappe.call({ method: CP_API + ".basculer", args: { clients, valeur }, freeze: true })).message || {};
      frappe.show_alert({ message: `${(r.clients || []).length} client(s) ${valeur ? "marqué(s) géré(s) par le partenaire" : "repris chez nous"}.`, indicator: "green" });
      await this.charger();
    });
  }
}
