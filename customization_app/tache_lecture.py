"""Tout employé peut LIRE toute tâche de travail — l'historique d'un client ne dépend pas de qui a fait le travail
(demande du 18/11/2026).

« Je veux que tous les techniciens et les employés puissent voir tout ce qui était fait avant, même si ce sont les
tâches d'un autre, même si l'employé est parti et qu'il n'a pas l'autorisation. » — Radhouan ouvrait une ancienne
tâche de Mohamed Hedi depuis l'historique du client : « Pas permis ».

D'où venait le refus : chaque compte porte des Permissions utilisateur sur « Employé » (qui voit le calendrier de
qui, arrivee_employe.py). Frappe les applique à tout document lié à un employé — donc à la tâche, par son « Choix
du Staff » — et refuse même la simple lecture d'une tâche d'un employé hors de la liste (un employé parti n'est
dans la liste de personne).

Ce qui change : la LECTURE d'une tâche (l'ouvrir, l'imprimer) n'est plus soumise à ces permissions.
Ce qui ne change PAS, et que ce module refait à l'identique de Frappe puisque le champ en est désormais dispensé
(`ignore_user_permissions` sur custom_choix_du_staff) :
  - les LISTES et le calendrier : chacun n'y voit que les tâches des employés de sa liste (`condition_liste`) ;
  - l'ÉCRITURE (modifier, créer, supprimer) : seulement sur les tâches des employés de sa liste (`autorise`) ;
  - le sous-traitant : il ne voit toujours que SES tâches, lecture comprise (sous_traitant.py, décision du
    31/10/2026) — ce module n'est consulté qu'après lui ;
  - un compte EXTERNE (rôle « Partenaire » ou « Sous-traitant ») : la lecture reste limitée aux tâches des
    employés de sa liste, comme avant — l'historique de tous les clients n'est ouvert qu'au personnel de la maison.
Un compte sans permission sur « Employé » (ou Administrator) n'a aucune limite, comme avant.
"""
from __future__ import annotations

import frappe

DOCTYPE = "Tache de travail"
CHAMP = "custom_choix_du_staff"
#: ce qui ne fait que regarder la tâche. `None` : Frappe demande « tous les droits d'un coup » pour afficher la fiche.
LECTURE = (None, "read", "print", "email", "select")
#: comptes extérieurs à la maison : pour eux la lecture reste limitée à leur liste
ROLES_EXTERNES = ("Partenaire", "Sous-traitant")


def est_une_lecture(ptype) -> bool:
    return ptype in LECTURE


def peut(ptype, employe_de_la_tache, permis, strict: bool = False, lecture_ouverte: bool = True) -> bool:
    """Ce compte peut-il faire `ptype` sur une tâche confiée à `employe_de_la_tache` ? PURE.

    `permis` : les employés de sa liste, ou None s'il n'a aucune limite. Lire est toujours permis au personnel
    (`lecture_ouverte`) ; le reste seulement sur les tâches des employés de sa liste. Une tâche sans employé suit
    le réglage « permissions strictes » de Frappe (non strict : permise), comme le faisait Frappe."""
    if permis is None or (lecture_ouverte and est_une_lecture(ptype)):
        return True
    if not employe_de_la_tache:
        return not strict
    return employe_de_la_tache in set(permis)


def condition_liste(permis, strict: bool = False, echapper=None) -> str:
    """Le filtre des listes et du calendrier : les tâches des employés de sa liste (et, hors mode strict, celles
    sans employé). « » s'il n'a aucune limite. PURE (`echapper` : la fonction d'échappement SQL)."""
    if permis is None:
        return ""
    echapper = echapper or (lambda v: "'%s'" % str(v).replace("\\", "\\\\").replace("'", "''"))
    noms = ", ".join(echapper(e) for e in permis) or "''"
    dans = "`tab%s`.`%s` in (%s)" % (DOCTYPE, CHAMP, noms)
    if strict:
        return dans
    return "(ifnull(`tab%s`.`%s`, '') = '' or %s)" % (DOCTYPE, CHAMP, dans)


def employes_permis(user=None):
    """Les employés de la liste de ce compte (Permissions utilisateur sur « Employé » valables pour les tâches),
    ou None s'il n'a aucune limite — comme Frappe : Administrator et les comptes sans permission sur « Employé »."""
    user = user or frappe.session.user
    if user == "Administrator":
        return None
    from frappe.permissions import get_user_permissions

    lignes = [p for p in (get_user_permissions(user).get("Employee") or [])
              if not p.get("applicable_for") or p.get("applicable_for") == DOCTYPE]
    if not lignes:
        return None
    return [p.get("doc") for p in lignes if p.get("doc")]


def lecture_ouverte(user=None) -> bool:
    """Le personnel de la maison lit toute tâche ; un compte externe (partenaire, sous-traitant) non."""
    return not (set(frappe.get_roles(user or frappe.session.user)) & set(ROLES_EXTERNES))


def _strict() -> bool:
    return bool(frappe.get_system_settings("apply_strict_user_permissions"))


def autorise(doc, user=None, ptype=None) -> bool:
    """Pour le hook has_permission (appelé par sous_traitant.has_permission_tache)."""
    return peut(ptype, doc.get(CHAMP), employes_permis(user), _strict(), lecture_ouverte(user))


def condition_liste_pour(user=None) -> str:
    """Pour le hook permission_query_conditions (appelé par sous_traitant.permission_query_conditions_tache)."""
    return condition_liste(employes_permis(user), _strict(), frappe.db.escape)


@frappe.whitelist()
def droits(tache):
    """Pour la fiche : ce compte peut-il modifier cette tâche ? Sinon la fiche s'affiche en consultation seule."""
    doc = frappe.get_doc(DOCTYPE, tache)
    doc.check_permission("read")
    modifiable = bool(frappe.has_permission(DOCTYPE, "write", doc=doc))
    employe = doc.get(CHAMP)
    return {"modifiable": modifiable,
            "employe": (frappe.db.get_value("Employee", employe, "employee_name") if employe else None) or employe or ""}
