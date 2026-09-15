"""Page /manifeste-aramex — le manifeste Aramex (feuille d'enlevement).

Sans parametre : la SELECTION des colis crees et pas encore enleves, plus la liste des
derniers manifestes. Avec ?manifeste=MAN-… : la feuille a imprimer, reprise du « Manifest
Report » de l'application PC d'Aramex. Reservee aux utilisateurs connectes qui lisent les
commandes ; no_cache : la selection change a chaque colis cree.
⚠️ Le controleur porte des underscores, le gabarit des tirets : c'est ainsi que Frappe les
associe (manifeste-aramex.html <-> manifeste_aramex.py).
"""

import frappe
from frappe.utils import formatdate, nowdate

from customization_app import aramex_manifeste as M


def get_context(context):
    context.no_cache = 1
    if frappe.session.user == "Guest":
        frappe.throw(frappe._("Connectez-vous pour voir le manifeste."), frappe.PermissionError)
    nom = (frappe.form_dict.get("manifeste") or "").strip()
    context.peut_generer = frappe.has_permission(M.DOCTYPE_MANIFESTE, "create")
    context.peut_modifier = frappe.has_permission(M.DOCTYPE_MANIFESTE, "write")
    context.imprime_le = formatdate(nowdate(), "dd/MM/yyyy")
    # La page ne passe pas par le gabarit de site de Frappe : le jeton CSRF des appels POST
    # se pose ici (sans lui, « Requête invalide » depuis le Desk).
    context.csrf_token = frappe.sessions.get_csrf_token()
    if nom:
        context.mode = "feuille"
        context.update(M.get_data(nom))
        context.date_lisible = formatdate(context.date, "dd/MM/yyyy")
        context.title = "Manifeste Aramex %s" % context.name
    else:
        context.mode = "selection"
        context.candidats = M.candidats()
        # ?preselection=b1,b2 (depuis « Ma journée ») : ce sont CES colis qui sont cochés,
        # à la place de « créés aujourd'hui ».
        pre = [b.strip() for b in (frappe.form_dict.get("preselection") or "").split(",") if b.strip()]
        if pre:
            for c in context.candidats:
                c["coche"] = c["bordereau"] in pre
        context.manifestes = M.liste()
        context.expediteur = M.expediteur()
        context.title = "Manifeste Aramex — sélection"
    return context
