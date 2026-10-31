"""Onglet « Sous-traitant » de la barre de gauche = le poste d'administration des sous-traitants (31/10/2026).

Réservé aux System Manager (le sous-traitant, lui, a sa barre de navigation en bas de l'écran). Rejoué à
chaque migrate (after_migrate), idempotent : l'onglet appartient au code, son contenu est réécrit.

Contenu : un bloc HTML « Sous-traitants (administration) » dont le script délègue tout à
`window.sousTraitant_admin` (public/js/sous_traitant_admin.js : liste des sous-traitants et leur
situation, bouton « Nouveau sous-traitant », raccourcis par sous-traitant, marche à suivre), puis des
raccourcis vers les listes et pages utiles.
"""

import json

import frappe

ESPACE = "Sous-traitant"
BLOC = "Sous-traitants (administration)"
ROLES = ("System Manager",)
#: (libellé, type, cible, couleur, filtre de liste)
RACCOURCIS = (
    ("Sous-traitants (employés)", "DocType", "Employee", "Blue", [["Employee", "custom_sous_traitant", "=", 1, False]]),
    ("Calendrier de travail", "URL", "/app/tache-de-travail/view/calendar/Calendrier de travail", "Grey", None),
    ("Stock et transferts", "Page", "stock-entrepots", "Grey", None),
    ("Vérifications de stock", "DocType", "Verification Stock", "Grey", None),
    ("Clôtures de caisse", "DocType", "Cloture Caisse", "Grey", None),
    ("Caisse journalière", "Page", "caisse-journaliere", "Grey", None),
)
BLOC_HTML = '<div class="st-admin"><p class="text-muted">Chargement du panneau des sous-traitants…</p></div>'
BLOC_SCRIPT = """if (window.sousTraitant_admin) { window.sousTraitant_admin(root_element); }
else { root_element.querySelector('.st-admin').innerHTML = '<p>Le script sous_traitant_admin.js n\\'est pas chargé : lancez <code>bench build --app customization_app</code> puis rechargez.</p>'; }"""


def _assurer_bloc():
    if frappe.db.exists("Custom HTML Block", BLOC):
        bloc = frappe.get_doc("Custom HTML Block", BLOC)
    else:
        bloc = frappe.new_doc("Custom HTML Block")
        bloc.name = BLOC
    if bloc.html == BLOC_HTML and bloc.script == BLOC_SCRIPT and {r.role for r in bloc.roles or []} == set(ROLES):
        return
    bloc.update({"html": BLOC_HTML, "script": BLOC_SCRIPT, "style": "", "private": 0})
    bloc.set("roles", [{"role": r} for r in ROLES])
    bloc.flags.ignore_permissions = True
    bloc.save()


def execute():
    if not frappe.db.exists("Custom Field", {"dt": "Employee", "fieldname": "custom_sous_traitant"}):
        return  # le patch sous_traitant_role n'est pas encore passé
    _assurer_bloc()
    if frappe.db.exists("Workspace", ESPACE):
        ws = frappe.get_doc("Workspace", ESPACE)
    else:
        ws = frappe.new_doc("Workspace")
        ws.update({"name": ESPACE, "label": ESPACE, "sequence_id": 1.1, "module": "Customize erpnext"})
    ws.update({"title": ESPACE, "public": 1, "is_hidden": 0, "icon": "tool", "for_user": "", "parent_page": ""})

    ws.set("shortcuts", [])
    ws.set("links", [])
    ws.set("custom_blocks", [{"custom_block_name": BLOC}])
    blocs = [{"id": "stAdminEntete", "type": "header",
              "data": {"text": "<span class=\"h4\"><b>Sous-traitants</b></span>", "col": 12}},
             {"id": "stAdminBloc", "type": "custom_block", "data": {"custom_block_name": BLOC, "col": 12}},
             {"id": "stAdminRac", "type": "header",
              "data": {"text": "<span class=\"h5\">Listes et pages</span>", "col": 12}}]
    for i, (libelle, type_, cible, couleur, filtre) in enumerate(RACCOURCIS):
        ligne = {"type": type_, "label": libelle, "color": couleur}
        if type_ == "URL":
            ligne["url"] = cible
        else:
            ligne["link_to"] = cible
        if filtre:
            ligne["stats_filter"] = json.dumps(filtre)
        ws.append("shortcuts", ligne)
        blocs.append({"id": "stAdminRac%d" % i, "type": "shortcut", "data": {"shortcut_name": libelle, "col": 3}})
    ws.content = json.dumps(blocs)
    ws.set("roles", [{"role": r} for r in ROLES])

    ws.flags.ignore_permissions = True
    ws.save()
    frappe.clear_cache()
    frappe.db.commit()
