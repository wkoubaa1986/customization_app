/**
 * Le bouton « 🗓️ Ma journée » sur la liste et le calendrier des tâches.
 *
 * Il ouvre l'écran où chacun mène ses interventions du jour au bout : appeler,
 * ouvrir l'adresse, photographier, saisir le bordereau Aramex, clôturer.
 *
 * ⚠️ PAR LE PROTOTYPE, PAS PAR `frappe.listview_settings`. D'autres fichiers
 * réassignent cet objet en entier pour certaines doctypes et sont concaténés
 * après le nôtre : la déclaration serait silencieusement écrasée. Même raison
 * que `sales_order_rdv.js` et `sales_order_list_alertes.js`.
 *
 * Le bouton s'affiche pour TOUT LE MONDE : le serveur, lui, n'ouvre la journée
 * d'un autre qu'à ceux qui supervisent déjà le planning. Un technicien qui
 * clique tombe sur SA journée, ce qui est exactement le but.
 */

frappe.provide("frappe.views");

(function () {
    const LIBELLE = "🗓️ Ma journée";

    function ouvrir() {
        frappe.set_route("ma-journee");
    }

    const _after_render = frappe.views.ListView.prototype.after_render;
    frappe.views.ListView.prototype.after_render = function () {
        _after_render.apply(this, arguments);
        if (this.doctype !== "Tache de travail" || this._mj_bouton) return;
        try {
            this._mj_bouton = true;
            this.page.add_inner_button(__(LIBELLE), ouvrir);
        } catch (e) {
            // Un bouton qui ne se pose pas ne doit pas casser la liste.
            this._mj_bouton = false;
            console.error("Bouton Ma journée :", e);
        }
    };

    // La vue CALENDRIER n'est pas une ListView : elle a son propre rendu, et
    // c'est pourtant là qu'on regarde sa journée. On y pose le même bouton.
    const _cal_render = frappe.views.CalendarView
        && frappe.views.CalendarView.prototype
        && frappe.views.CalendarView.prototype.render;
    if (_cal_render) {
        frappe.views.CalendarView.prototype.render = function () {
            const out = _cal_render.apply(this, arguments);
            try {
                if (this.doctype === "Tache de travail" && !this._mj_bouton) {
                    this._mj_bouton = true;
                    this.page.add_inner_button(__(LIBELLE), ouvrir);
                }
            } catch (e) {
                this._mj_bouton = false;
            }
            return out;
        };
    }
})();
