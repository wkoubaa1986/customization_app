"""Départ d'un employé (décision utilisateur 10/11/2026).

Quand un technicien quitte l'entreprise, son nom reste partout : rendez-vous clients à venir, jours de
récupération et congés dans le calendrier, réglages (portail de rendez-vous, prise de rendez-vous ERPNext,
tournées, véhicule GPS, suivi d'activité, primes, responsable réparation, caisse…), vérifications de stock,
machines en réparation, et son stock d'articles. Le désactiver à la main laissait des rendez-vous sans
technicien et un portail qui proposait encore son nom (Mohamed Hedi, Akram — 10/11/2026).

UN SEUL GESTE, depuis la fiche Employé (« Départ : transférer son travail »), réservé à la direction :

  1. RETRAIT, PAS REMPLACEMENT, dans tous les réglages : la ligne qui le cite est supprimée. Les tables de
     réglage sont trouvées PAR LE SCHÉMA (toute table enfant d'un réglage unique qui porte un lien Employé
     ou Utilisateur), pas par une liste écrite en dur : un réglage ajouté demain est couvert. S'il était le
     technicien par défaut du portail de rendez-vous, le dialogue exige d'en choisir un autre.
  2. CALENDRIER NETTOYÉ : ses congés et jours de récupération à venir sont annulés (demandes de congé,
     récupérations planifiées, règle de récupération, tâches d'agenda orphelines). Le passé reste.
  3. RENDEZ-VOUS CLIENTS À VENIR transférés au remplaçant choisi, avec un commentaire sur chaque tâche.
     Les tâches ouvertes du passé, jamais clôturées, sont annulées avec la raison « départ ».
  4. STOCK : vérification FINALE par le responsable principal du magasin (Config Stock Entrepot), avec une
     tâche claire « Compter, fermer et transférer ». Quand cette vérification est terminée, le stock restant
     part au magasin, l'entrepôt est désactivé et retiré des réglages (`stock_apres_verification`, hook).
     Tant que ce n'est pas fait, la fiche Employé le montre.
  5. FERMETURE : fiche « Left » datée, utilisateur désactivé (sessions coupées par Frappe), Raven coupé,
     machines en réparation et activités réaffectées, direction prévenue, journal sur la fiche.
  6. FILET : passer un employé en « Left » SANS ce bouton est refusé tant qu'il lui reste quelque chose
     (`employee_validate`), et chaque nuit les rendez-vous à venir d'un employé inactif sont signalés
     (`alerte_quotidienne`).

Les fonctions `classer_taches`, `lignes_a_retirer`, `reste_a_faire`, `texte_journal` sont PURES (testées
sans site).
"""
from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime, nowdate

ROLES = ("System Manager", "HR Manager", "Congés RH")
TYPES_ABSENCE = ("Congé", "Jour de récupération")
DOCTYPE_TACHE = "Tache de travail"
STATUTS_REPARATION_FINIS = ("Rendue au client",)
STATUTS_ACTIVITE_FINIS = ("Terminée", "Annulée")
CHAMP_DATE = "custom_depart_le"
CHAMP_VERIF = "custom_depart_verification"
CHAMP_STOCK_CLOS = "custom_depart_stock_clos"
CHAMP_JOURNAL = "custom_depart_journal"
RAISON_ANNULATION = "Autre"


# ── Logique pure ─────────────────────────────────────────────────────────────

def _normaliser(texte: str) -> list[str]:
    import unicodedata
    t = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode().lower()
    return [m for m in "".join(c if c.isalnum() else " " for c in t).split() if len(m) >= 3]


def lignes_salaire_de(nom_employe: str, lignes: list[dict]) -> list[dict]:
    """Les dépenses récurrentes de type « Salaire » (application banque) qui portent le NOM DE FAMILLE de
    l'employé, à l'accent et à une lettre près (« Chouchène » / « Chouchane »). Le prénom seul ne suffit pas :
    « Hedi » désigne deux personnes. PURE."""
    from difflib import SequenceMatcher
    mots = _normaliser(nom_employe)
    if not mots:
        return []
    famille = mots[-1]
    out = []
    for l in lignes:
        if l.get("type") != "Salaire":
            continue
        for m in _normaliser(l.get("libelle") or ""):
            if m == famille or SequenceMatcher(None, m, famille).ratio() >= 0.85:
                out.append(l)
                break
    return out


def classer_taches(taches: list[dict], aujourdhui) -> dict:
    """Répartit les tâches OUVERTES d'un employé : {a_venir, passees, absences}.
    `absences` = congés / jours de récupération (tout horizon à venir) ; `a_venir` = rendez-vous clients et
    autres tâches qui commencent aujourd'hui ou après ; `passees` = ouvertes mais déjà dépassées. PURE."""
    aujourdhui = getdate(aujourdhui)
    out = {"a_venir": [], "passees": [], "absences": []}
    for t in taches:
        debut = getdate(str(t.get("starts_on") or "")[:10]) if t.get("starts_on") else None
        if t.get("custom_type_dintervention") in TYPES_ABSENCE:
            if debut is None or debut >= aujourdhui:
                out["absences"].append(t)
            continue
        if debut is not None and debut < aujourdhui:
            out["passees"].append(t)
        else:
            out["a_venir"].append(t)
    return out


def lignes_a_retirer(lignes: list[dict], champ: str, valeur) -> list[dict]:
    """Les lignes d'une table de réglage qui citent `valeur` dans `champ`. PURE."""
    return [l for l in lignes if valeur and (l.get(champ) or "") == valeur]


def reste_a_faire(inv: dict) -> list[str]:
    """Ce qui empêche encore un départ « propre » : libellés lisibles, vide si rien. PURE."""
    reste = []
    if inv.get("taches_a_venir"):
        reste.append("%d rendez-vous à venir" % len(inv["taches_a_venir"]))
    if inv.get("absences_a_venir") or inv.get("conges_a_venir") or inv.get("recup_planifiees"):
        reste.append("congés / récupérations à venir dans le calendrier")
    if inv.get("reglages"):
        reste.append("%d réglage(s) qui le citent" % len(inv["reglages"]))
    if inv.get("portail_defaut"):
        reste.append("technicien par défaut du portail de rendez-vous")
    if inv.get("verifications_ouvertes"):
        reste.append("%d vérification(s) de stock ouverte(s)" % len(inv["verifications_ouvertes"]))
    if inv.get("stock") and flt(inv["stock"].get("quantite")) > 0:
        reste.append("du stock dans son entrepôt")
    return reste


def texte_journal(journal: list[str]) -> str:
    return "\n".join("• " + j for j in journal)


# ── Inventaire ───────────────────────────────────────────────────────────────

def _tables_reglages() -> list[dict]:
    """Les tables enfants des RÉGLAGES UNIQUES (Single) qui portent un lien Employé ou Utilisateur :
    [{table, champ, options, parent}]. Lu du schéma, en cache une heure."""
    cle = "depart_employe|tables_reglages"
    cache = frappe.cache().get_value(cle)
    if cache is not None:
        return cache
    rows = frappe.db.sql("""
        select f.parent dt, f.fieldname, f.options from `tabDocField` f
         join `tabDocType` d on d.name = f.parent
        where f.fieldtype = 'Link' and f.options in ('Employee', 'User') and d.istable = 1
        union
        select c.dt, c.fieldname, c.options from `tabCustom Field` c
         join `tabDocType` d on d.name = c.dt
        where c.fieldtype = 'Link' and c.options in ('Employee', 'User') and d.istable = 1""", as_dict=True)
    out = []
    for r in rows:
        if not frappe.db.table_exists(r.dt):
            continue
        for (parent,) in frappe.db.sql("select distinct parenttype from `tab%s` where ifnull(parenttype, '') != ''" % r.dt):
            if frappe.db.exists("DocType", parent) and frappe.get_meta(parent).issingle:
                out.append({"table": r.dt, "champ": r.fieldname, "options": r.options, "parent": parent})
    frappe.cache().set_value(cle, out, expires_in_sec=3600)
    return out


def _lignes_reglages(employe: str, user: str | None) -> list[dict]:
    """Les lignes de réglage qui citent l'employé (ou son utilisateur) : [{parent, table, champ, name, libelle}]."""
    out = []
    for t in _tables_reglages():
        valeur = employe if t["options"] == "Employee" else user
        if not valeur:
            continue
        for l in frappe.get_all(t["table"], filters={"parenttype": t["parent"], t["champ"]: valeur},
                                fields=["name", "parent", "parentfield"]):
            out.append({"parent": t["parent"], "table": t["table"], "champ": t["champ"], "name": l.name,
                        "parentfield": l.parentfield, "libelle": "%s › %s" % (_(t["parent"]), _(t["table"]))})
    # La caisse cite l'employé par son NOM de caisse (Config Caisse › dates de départ).
    nom = frappe.db.get_value("Employee", employe, "employee_name")
    if frappe.db.exists("DocType", "Config Caisse Depart"):
        for l in frappe.get_all("Config Caisse Depart", filters={"parenttype": "Config Caisse", "caisse": nom},
                                fields=["name", "parent", "parentfield"]):
            out.append({"parent": "Config Caisse", "table": "Config Caisse Depart", "champ": "caisse", "name": l.name,
                        "parentfield": l.parentfield, "libelle": "Config Caisse › date de départ de la caisse"})
    return out


def _stock(entrepot: str | None) -> dict | None:
    if not entrepot or not frappe.db.exists("Warehouse", entrepot):
        return None
    q = frappe.db.sql("""select count(*), coalesce(sum(actual_qty), 0), coalesce(sum(stock_value), 0)
                         from tabBin where warehouse = %s and actual_qty > 0""", entrepot)[0]
    return {"entrepot": entrepot, "articles": cint(q[0]), "quantite": flt(q[1], 3), "valeur": flt(q[2], 3),
            "desactive": cint(frappe.db.get_value("Warehouse", entrepot, "disabled"))}


@frappe.whitelist()
def inventaire(employe: str) -> dict:
    """Tout ce qui reste au nom de l'employé, pour le dialogue."""
    frappe.only_for(ROLES)
    return _inventaire(employe)


def _inventaire(employe: str) -> dict:
    emp = frappe.get_doc("Employee", employe)
    auj = nowdate()
    from customization_app.conges_recuperations import jours_planifies_a_venir
    from customization_app.stock_entrepots import OUVERTS, VERIF

    taches = frappe.get_all(DOCTYPE_TACHE, filters={"custom_choix_du_staff": employe, "status": "Open"},
                            fields=["name", "starts_on", "custom_type_dintervention", "custom_client", "titre", "subject"],
                            order_by="starts_on", limit_page_length=0)
    classes = classer_taches(taches, auj)
    conges = frappe.get_all("Leave Application", filters={"employee": employe, "docstatus": ["<", 2],
                                                           "to_date": [">=", auj]},
                            fields=["name", "leave_type", "from_date", "to_date", "docstatus"], order_by="from_date")
    verifs = frappe.get_all(VERIF, filters={"employe": employe, "statut": ["in", OUVERTS]}, fields=["name", "statut", "date"])
    reparations = frappe.get_all("Machine Reparation", filters={"responsable": employe,
                                                                "statut": ["not in", STATUTS_REPARATION_FINIS]},
                                 fields=["name", "statut"]) if frappe.db.exists("DocType", "Machine Reparation") else []
    activites = []
    if frappe.db.exists("DocType", "Activite Affectation"):
        for a in frappe.get_all("Activite Affectation", filters={"employe": employe, "parenttype": "Activite Employe"},
                                fields=["name", "parent"]):
            if frappe.db.get_value("Activite Employe", a.parent, "statut") not in STATUTS_ACTIVITE_FINIS:
                activites.append(a)
    reunions = []
    if frappe.db.exists("DocType", "Reunion Participant"):
        for r in frappe.get_all("Reunion Participant", filters={"employe": employe, "parenttype": "Reunion Activite"},
                                fields=["name", "parent"]):
            d = frappe.db.get_value("Reunion Activite", r.parent, ["date", "tenue_le"], as_dict=True)
            if d and not d.tenue_le and (not d.date or str(d.date) >= auj):
                reunions.append(r)
    portail_defaut = (frappe.db.exists("DocType", "Config Portail RDV")
                      and frappe.db.get_single_value("Config Portail RDV", "employe_defaut") == employe)
    return {
        "employe": employe, "nom": emp.employee_name, "statut": emp.status, "user": emp.user_id,
        "user_actif": cint(frappe.db.get_value("User", emp.user_id, "enabled")) if emp.user_id else 0,
        "taches_a_venir": classes["a_venir"], "taches_passees": len(classes["passees"]),
        "absences_a_venir": len(classes["absences"]),
        "conges_a_venir": conges, "recup_planifiees": len(jours_planifies_a_venir(employe)),
        "regle_recup": bool(frappe.db.exists("Regle Recuperation", employe)),
        "reglages": _lignes_reglages(employe, emp.user_id), "portail_defaut": bool(portail_defaut),
        "verifications_ouvertes": verifs, "reparations": reparations, "activites": len(activites), "reunions": len(reunions),
        "stock": _stock(emp.get("custom_warehouse")),
        "responsable_stock": frappe.db.get_single_value("Config Stock Entrepot", "responsable_verification")
        if frappe.db.exists("DocType", "Config Stock Entrepot") else None,
        "depart_le": str(emp.get(CHAMP_DATE) or "") or None, "verification_depart": emp.get(CHAMP_VERIF),
        "stock_clos": str(emp.get(CHAMP_STOCK_CLOS) or "")[:16] or None,
        "techniciens": frappe.get_all("Employee", filters={"status": "Active", "name": ["!=", employe]},
                                      fields=["name", "employee_name"], order_by="employee_name"),
    }


# ── Exécution ────────────────────────────────────────────────────────────────

def _commenter(doctype: str, name: str, texte: str):
    frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": doctype,
                    "reference_name": name, "content": texte}).insert(ignore_permissions=True)


def _transferer_taches(taches: list[dict], de: str, vers: str, nom_de: str, nom_vers: str) -> int:
    n = 0
    for t in taches:
        doc = frappe.get_doc(DOCTYPE_TACHE, t["name"])
        if doc.status != "Open" or doc.custom_choix_du_staff != de:
            continue
        doc.custom_choix_du_staff = vers
        doc.flags.ignore_permissions = True
        try:
            doc.save()
        except frappe.ValidationError:
            # Tâche ancienne qui porte une valeur de liste périmée : la sauvegarde complète la refuse, on
            # écrit le strict nécessaire (le nom affiché suit le choix du staff, comme le hook le ferait).
            frappe.db.set_value(DOCTYPE_TACHE, doc.name, {"custom_choix_du_staff": vers, "custom_employé": nom_vers},
                                update_modified=True)
        _commenter(DOCTYPE_TACHE, doc.name, _("🔁 Transférée de {0} à {1} (départ de {0}).").format(nom_de, nom_vers))
        n += 1
    return n


def _annuler_taches_passees(taches: list[dict], nom: str) -> int:
    n = 0
    for t in taches:
        doc = frappe.get_doc(DOCTYPE_TACHE, t["name"])
        if doc.status != "Open":
            continue
        doc.status = "Cancelled"
        if doc.meta.has_field("raison_annulation"):
            doc.raison_annulation = RAISON_ANNULATION
        doc.flags.ignore_permissions = True
        try:
            doc.save()
        except frappe.ValidationError:
            # Valeur de liste périmée sur une vieille tâche (« recontacte », constaté le 10/11/2026) : on
            # ferme sans repasser par la validation complète.
            frappe.db.set_value(DOCTYPE_TACHE, doc.name, {"status": "Cancelled", "raison_annulation": RAISON_ANNULATION},
                                update_modified=True)
        _commenter(DOCTYPE_TACHE, doc.name, _("❌ Annulée : départ de {0}, tâche passée jamais clôturée.").format(nom))
        n += 1
    return n


def _nettoyer_calendrier(employe: str, conges: list[dict], journal: list[str]):
    """Congés et récupérations À VENIR hors du calendrier : demandes annulées (le hook retire la tâche et la
    trace), jours planifiés annulés, règle supprimée, tâches d'absence orphelines supprimées."""
    from customization_app.conges_recuperations import annuler_planifies

    annules, echecs = 0, []
    for c in conges:
        try:
            doc = frappe.get_doc("Leave Application", c["name"])
            if doc.docstatus == 1:
                doc.flags.ignore_permissions = True
                doc.cancel()
            elif doc.docstatus == 0:
                doc.delete(ignore_permissions=True)
            annules += 1
        except Exception as e:
            echecs.append("%s (%s)" % (c["name"], str(e)[:80]))
    if annules:
        journal.append(_("{0} congé(s) / récupération(s) à venir annulé(s)").format(annules))
    if echecs:
        journal.append(_("⚠️ congés non annulés : {0}").format(", ".join(echecs)))
    planifies = annuler_planifies(employe)
    if planifies:
        journal.append(_("{0} jour(s) de récupération planifié(s) retiré(s)").format(len(planifies)))
    if frappe.db.exists("Regle Recuperation", employe):
        frappe.delete_doc("Regle Recuperation", employe, ignore_permissions=True, force=True)
        journal.append(_("règle de récupération supprimée"))
    orphelines = frappe.get_all(DOCTYPE_TACHE, filters={"custom_choix_du_staff": employe, "status": "Open",
                                                        "custom_type_dintervention": ["in", TYPES_ABSENCE],
                                                        "starts_on": [">=", nowdate()]}, pluck="name")
    for nom in orphelines:
        frappe.delete_doc(DOCTYPE_TACHE, nom, ignore_permissions=True, force=True)
    if orphelines:
        journal.append(_("{0} jour(s) d'absence retiré(s) du calendrier").format(len(orphelines)))


def _retirer_des_reglages(lignes: list[dict], journal: list[str]):
    """Supprime les lignes de réglage (regroupées par réglage unique, une sauvegarde chacun)."""
    par_parent: dict[str, list[dict]] = {}
    for l in lignes:
        par_parent.setdefault(l["parent"], []).append(l)
    for parent, ls in par_parent.items():
        doc = frappe.get_single(parent)
        noms = {l["name"] for l in ls}
        for champ in {l["parentfield"] for l in ls}:
            doc.set(champ, [r for r in (doc.get(champ) or []) if r.name not in noms])
        doc.flags.ignore_permissions = True
        doc.save()
        journal.append(_("retiré de {0} ({1} ligne(s))").format(_(parent), len(noms)))


def _retirer_portail_defaut(employe: str, remplacant_portail: str | None, journal: list[str]):
    if not frappe.db.exists("DocType", "Config Portail RDV"):
        return
    cfg = frappe.get_single("Config Portail RDV")
    if cfg.get("employe_defaut") != employe:
        return
    if not remplacant_portail:
        frappe.throw(_("{0} est le technicien par défaut du portail de rendez-vous : choisissez qui le remplace.")
                     .format(frappe.db.get_value("Employee", employe, "employee_name")))
    cfg.employe_defaut = remplacant_portail
    cfg.flags.ignore_permissions = True
    cfg.save()
    journal.append(_("portail de rendez-vous : technicien par défaut → {0}")
                   .format(frappe.db.get_value("Employee", remplacant_portail, "employee_name")))


def _retirer_activites_reunions(employe: str, journal: list[str]):
    n = 0
    if frappe.db.exists("DocType", "Activite Affectation"):
        for a in frappe.get_all("Activite Affectation", filters={"employe": employe, "parenttype": "Activite Employe"},
                                fields=["name", "parent"]):
            act = frappe.get_doc("Activite Employe", a.parent)
            if act.statut in STATUTS_ACTIVITE_FINIS:
                continue
            for champ in {r.parentfield for r in act.get_all_children() if r.name == a.name}:
                act.set(champ, [r for r in act.get(champ) if r.name != a.name])
            act.flags.ignore_permissions = True
            act.save()
            n += 1
    if n:
        journal.append(_("retiré de {0} activité(s) en cours").format(n))
    m = 0
    if frappe.db.exists("DocType", "Reunion Participant"):
        for r in frappe.get_all("Reunion Participant", filters={"employe": employe, "parenttype": "Reunion Activite"},
                                fields=["name", "parent"]):
            reu = frappe.get_doc("Reunion Activite", r.parent)
            if reu.get("tenue_le") or (reu.get("date") and str(reu.date) < nowdate()):
                continue
            for champ in {x.parentfield for x in reu.get_all_children() if x.name == r.name}:
                reu.set(champ, [x for x in reu.get(champ) if x.name != r.name])
            reu.flags.ignore_permissions = True
            reu.save()
            m += 1
    if m:
        journal.append(_("retiré de {0} réunion(s) à venir").format(m))


def _couper_salaire_banque(nom: str, journal: list[str]):
    """Application banque (bank_retenue_sync) : la dépense récurrente « Salaire » du partant est désactivée,
    sinon le virement attendu chaque fin de mois serait signalé manquant (décision utilisateur 10/11/2026)."""
    if "bank_retenue_sync" not in frappe.get_installed_apps() or not frappe.db.exists("DocType", "BRS Depense Recurrente"):
        return
    cfg = frappe.get_single("Bank Retenue Sync Settings")
    lignes = [r for r in (cfg.get("depenses_recurrentes") or []) if cint(r.get("actif"))]
    touchees = lignes_salaire_de(nom, [r.as_dict() for r in lignes])
    noms = {t["name"] for t in touchees}
    if not noms:
        journal.append(_("⚠️ application banque : aucun salaire actif reconnu pour {0} — à vérifier à la main").format(nom))
        return
    for r in lignes:
        if r.name in noms:
            r.actif = 0
            r.notes = ((r.notes + "\n") if r.notes else "") + _("Désactivé le {0} : départ de {1}.").format(
                frappe.utils.formatdate(nowdate()), nom)
    cfg.flags.ignore_permissions = True
    cfg.save()
    journal.append(_("application banque : salaire désactivé ({0})").format(
        ", ".join(t.get("libelle") or t["name"] for t in touchees)))


def _retirer_permissions_utilisateur(employe: str, user: str | None, journal: list[str]):
    """Qui voit le calendrier de qui = les « permissions utilisateur » sur Employé (mécanisme standard, tenu à la
    main depuis 2023). Le partant sort des listes des autres, et sa propre liste est vidée."""
    noms = frappe.get_all("User Permission", filters={"allow": "Employee", "for_value": employe}, pluck="name")
    if user:
        noms += frappe.get_all("User Permission", filters={"user": user}, pluck="name")
    for n in set(noms):
        frappe.delete_doc("User Permission", n, ignore_permissions=True, force=True)
    if noms:
        journal.append(_("{0} permission(s) utilisateur retirée(s) (visibilité des calendriers)").format(len(set(noms))))


def _reaffecter_reparations(employe: str, vers: str | None, journal: list[str]):
    if not frappe.db.exists("DocType", "Machine Reparation"):
        return
    noms = frappe.get_all("Machine Reparation", filters={"responsable": employe,
                                                          "statut": ["not in", STATUTS_REPARATION_FINIS]}, pluck="name")
    for nom in noms:
        doc = frappe.get_doc("Machine Reparation", nom)
        doc.responsable = vers
        doc.flags.ignore_permissions = True
        doc.save()
    if noms:
        journal.append(_("{0} machine(s) en réparation → {1}").format(
            len(noms), frappe.db.get_value("Employee", vers, "employee_name") if vers else _("sans responsable")))


def _annuler_verifications(verifs: list[dict], nom: str, journal: list[str]):
    from customization_app import stock_entrepots as SE
    for v in verifs:
        doc = frappe.get_doc(SE.VERIF, v["name"])
        if doc.statut in SE.OUVERTS:
            SE._annuler(doc, _("Départ de {0} : remplacée par la vérification finale.").format(nom), frappe.session.user)
    if verifs:
        journal.append(_("{0} vérification(s) de stock en cours annulée(s)").format(len(verifs)))


def _tache_verification_finale(responsable: str, nom: str, entrepot: str, quand) -> str:
    from customization_app.api import compute_tache_color
    libelle = frappe.db.get_value("Warehouse", entrepot, "warehouse_name") or entrepot
    doc = frappe.get_doc({
        "doctype": DOCTYPE_TACHE, "custom_type_dintervention": "Autre", "custom_choix_du_staff": responsable,
        "starts_on": quand, "ends_on": frappe.utils.add_to_date(quand, hours=2), "temps": ">=2 heures", "status": "Open",
        "titre": "📦 Départ de %s\nCompter, fermer et transférer\n%s" % (nom, libelle),
        "subject": "Départ de %s — vérification finale de son stock (%s) : compter les articles dans Stock › Vérif., "
                   "terminer la fiche ; le stock restant part ensuite au magasin et l'entrepôt est fermé." % (nom, libelle),
    })
    try:
        doc.color = compute_tache_color(doc)
    except Exception:
        pass
    doc.flags.ignore_permissions = True
    doc.flags.duree_fixee = True
    doc.insert()
    return doc.name


def _ouvrir_verification_finale(emp, date_depart, journal: list[str]) -> str | None:
    """Le stock de l'employé passe par une vérification FINALE du responsable principal du magasin. Sans
    stock : l'entrepôt est fermé tout de suite."""
    from customization_app import stock_entrepots as SE
    entrepot = emp.get("custom_warehouse")
    if not entrepot or not frappe.db.exists("Warehouse", entrepot):
        return None
    stock = _stock(entrepot)
    if not stock or stock["quantite"] <= 0:
        _fermer_entrepot(entrepot, journal)
        return None
    cfg = SE.config_verification()
    responsable = cfg["responsable"]
    if not responsable:
        frappe.throw(_("Réglez le responsable principal du magasin (Config Stock Entrepot › vérifications) : "
                       "c'est lui qui fait la vérification finale du stock."))
    # La vérification HEBDOMADAIRE de ce stock n'a plus lieu d'être : sa ligne sort du réglage tout de suite.
    # (Le réglage refuse toute sauvegarde tant qu'une ligne vise un stock sans employé actif — constaté le
    #  10/11/2026 à la création d'un technicien après le départ de Mohamed Hedi.) Le seuil reste jusqu'à la
    # fermeture de l'entrepôt (`_fermer_entrepot`).
    _retirer_planning_verification(entrepot, journal)
    nom = SE.ouvrir_verification(entrepot, date_depart, avec_taches=False)
    v = frappe.get_doc(SE.VERIF, nom)
    quand = frappe.utils.get_datetime("%s 09:00:00" % date_depart)
    v.responsable = responsable
    v.employe = emp.name
    v.note = _("Vérification FINALE — départ de {0} : compter, fermer, transférer au magasin.").format(emp.employee_name)
    if not v.tache_responsable:
        v.tache_responsable = _tache_verification_finale(responsable, emp.employee_name, entrepot, quand)
    SE._sauver(v)
    journal.append(_("vérification finale du stock {0} ouverte ({1}) : tâche au responsable {2}").format(
        entrepot, nom, frappe.db.get_value("Employee", responsable, "employee_name")))
    return nom


def _retirer_planning_verification(entrepot: str, journal: list[str]):
    from customization_app import stock_entrepots as SE
    if not frappe.db.exists("DocType", SE.CONFIG):
        return
    cfg = frappe.get_single(SE.CONFIG)
    avant = cfg.get("verifications") or []
    apres = [r for r in avant if r.get("entrepot") != entrepot]
    if len(apres) != len(avant):
        cfg.set("verifications", apres)
        cfg.flags.ignore_permissions = True
        cfg.save()
        journal.append(_("vérification hebdomadaire de {0} retirée du planning").format(entrepot))


def _fermer_entrepot(entrepot: str, journal: list[str]):
    """Entrepôt désactivé et retiré des réglages de stock (vérifications, seuils)."""
    from customization_app import stock_entrepots as SE
    if frappe.db.exists("DocType", SE.CONFIG):
        cfg = frappe.get_single(SE.CONFIG)
        change = False
        for champ in ("verifications", "seuils"):
            avant = cfg.get(champ) or []
            apres = [r for r in avant if r.get("entrepot") != entrepot]
            if len(apres) != len(avant):
                cfg.set(champ, apres)
                change = True
        if change:
            cfg.flags.ignore_permissions = True
            cfg.save()
    if not cint(frappe.db.get_value("Warehouse", entrepot, "disabled")):
        frappe.db.set_value("Warehouse", entrepot, "disabled", 1)
    journal.append(_("entrepôt {0} fermé et retiré des réglages").format(entrepot))


def _direction() -> list[str]:
    users = set(frappe.get_all("Has Role", filters={"role": "Congés RH", "parenttype": "User"}, pluck="parent"))
    return [u for u in users if cint(frappe.db.get_value("User", u, "enabled")) and not u.startswith("agent-ia")]


def _prevenir_direction(sujet: str, doctype: str, name: str):
    for u in _direction():
        try:
            frappe.get_doc({"doctype": "Notification Log", "for_user": u, "type": "Alert", "subject": sujet,
                            "document_type": doctype, "document_name": name}).insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "depart_employe: notification")


@frappe.whitelist(methods=["POST"])
def executer(employe: str, remplacant: str = None, remplacant_portail: str = None, date_depart=None,
             annuler_passees: int = 1) -> dict:
    """Le départ en un geste. Tout ou rien : une erreur annule l'ensemble."""
    frappe.only_for(ROLES)
    emp = frappe.get_doc("Employee", employe)
    if emp.get(CHAMP_DATE):
        frappe.throw(_("Le départ de {0} a déjà été traité le {1}.").format(emp.employee_name, emp.get(CHAMP_DATE)))
    date_depart = getdate(date_depart or nowdate())
    inv = _inventaire(employe)
    nom = emp.employee_name
    if inv["taches_a_venir"] and not remplacant:
        frappe.throw(_("{0} a {1} rendez-vous à venir : choisissez qui les reprend.").format(nom, len(inv["taches_a_venir"])))
    if remplacant and remplacant == employe:
        frappe.throw(_("Le remplaçant ne peut pas être l'employé qui part."))
    journal: list[str] = []

    if inv["taches_a_venir"]:
        nom_vers = frappe.db.get_value("Employee", remplacant, "employee_name")
        n = _transferer_taches(inv["taches_a_venir"], employe, remplacant, nom, nom_vers)
        journal.append(_("{0} rendez-vous à venir transféré(s) à {1}").format(n, nom_vers))
    if cint(annuler_passees) and inv["taches_passees"]:
        passees = frappe.get_all(DOCTYPE_TACHE, filters={"custom_choix_du_staff": employe, "status": "Open",
                                                         "starts_on": ["<", nowdate()],
                                                         "custom_type_dintervention": ["not in", TYPES_ABSENCE]},
                                 fields=["name"], limit_page_length=0)
        n = _annuler_taches_passees(passees, nom)
        journal.append(_("{0} tâche(s) passée(s) jamais clôturée(s) annulée(s)").format(n))
    _nettoyer_calendrier(employe, inv["conges_a_venir"], journal)
    _retirer_portail_defaut(employe, remplacant_portail, journal)
    _retirer_des_reglages(inv["reglages"], journal)
    _retirer_activites_reunions(employe, journal)
    _retirer_permissions_utilisateur(employe, emp.user_id, journal)
    _couper_salaire_banque(nom, journal)
    _reaffecter_reparations(employe, remplacant or inv.get("responsable_stock"), journal)
    _annuler_verifications(inv["verifications_ouvertes"], nom, journal)
    verif = _ouvrir_verification_finale(emp, date_depart, journal)

    # Fermeture des accès.
    if emp.user_id and frappe.db.exists("User", emp.user_id):
        user = frappe.get_doc("User", emp.user_id)
        if cint(user.enabled):
            user.enabled = 0
            user.flags.ignore_permissions = True
            user.save()      # Frappe coupe ses sessions à la désactivation
            journal.append(_("utilisateur {0} désactivé").format(emp.user_id))
        if frappe.db.exists("DocType", "Raven User"):
            for r in frappe.get_all("Raven User", filters={"user": emp.user_id, "enabled": 1}, pluck="name"):
                frappe.db.set_value("Raven User", r, "enabled", 0)
                journal.append(_("Raven coupé"))

    emp.reload()
    emp.status = "Left"
    emp.relieving_date = date_depart
    emp.set(CHAMP_DATE, date_depart)
    emp.set(CHAMP_VERIF, verif)
    emp.set(CHAMP_JOURNAL, texte_journal(journal))
    emp.flags.ignore_permissions = True
    emp.flags.depart_employe = True
    emp.save()
    _commenter("Employee", employe, _("🚪 Départ traité par {0} le {1} :<br>{2}").format(
        frappe.utils.get_fullname(frappe.session.user), frappe.utils.formatdate(date_depart),
        "<br>".join(frappe.utils.escape_html(j) for j in journal)))
    _prevenir_direction(_("🚪 Départ de {0} traité — {1} action(s){2}").format(
        nom, len(journal), _(" ; vérification finale du stock à faire") if verif else ""), "Employee", employe)
    return {"journal": journal, "verification": verif}


# ── Après la vérification finale : le stock part au magasin ──────────────────

def stock_apres_verification(doc, method=None):
    """Hook Verification Stock.on_update : la vérification FINALE d'un départ terminée → le stock restant
    part au magasin, l'entrepôt est fermé. Idempotent (`custom_depart_stock_clos`)."""
    from customization_app import stock_entrepots as SE
    if doc.statut != SE.TERMINEE or not frappe.db.has_column("Employee", CHAMP_VERIF):
        return
    employe = frappe.db.get_value("Employee", {CHAMP_VERIF: doc.name}, "name")
    if not employe or frappe.db.get_value("Employee", employe, CHAMP_STOCK_CLOS):
        return
    cloturer_stock(employe)


def cloturer_stock(employe: str) -> list[str]:
    from customization_app import stock_entrepots as SE
    emp = frappe.get_doc("Employee", employe)
    entrepot = emp.get("custom_warehouse")
    journal: list[str] = []
    if entrepot:
        lignes = [(r.item_code, flt(r.actual_qty, 6)) for r in frappe.get_all(
            "Bin", filters={"warehouse": entrepot, "actual_qty": [">", 0]}, fields=["item_code", "actual_qty"])]
        if lignes:
            se = SE.ecriture_transfert(entrepot, SE.magasin(), lignes,
                                       _("Départ de {0} : stock restant rendu au magasin après vérification finale.").format(emp.employee_name),
                                       ignore_trajets=True)
            journal.append(_("{0} article(s) transféré(s) au magasin ({1})").format(len(lignes), se.name))
        _fermer_entrepot(entrepot, journal)
    emp.db_set(CHAMP_STOCK_CLOS, now_datetime(), update_modified=False)
    emp.db_set(CHAMP_JOURNAL, ((emp.get(CHAMP_JOURNAL) or "") + "\n" + texte_journal(journal)).strip(), update_modified=False)
    _commenter("Employee", employe, _("📦 Stock clos après la vérification finale :<br>{0}").format(
        "<br>".join(frappe.utils.escape_html(j) for j in journal)))
    _prevenir_direction(_("📦 Départ de {0} : stock rendu au magasin, entrepôt fermé").format(emp.employee_name), "Employee", employe)
    return journal


# ── Filet ────────────────────────────────────────────────────────────────────

def employee_validate(doc, method=None):
    """Hook Employee.validate : « Left » sans le bouton est refusé tant qu'il reste quelque chose."""
    if doc.status != "Left" or doc.flags.get("depart_employe") or doc.is_new():
        return
    avant = doc.get_doc_before_save()
    if avant and avant.status == "Left":
        return
    if doc.get(CHAMP_DATE):
        return
    reste = reste_a_faire(_inventaire(doc.name))
    if reste:
        frappe.throw(_("{0} a encore : {1}. Utilisez « Départ : transférer son travail » sur sa fiche, "
                       "qui s'occupe de tout.").format(doc.employee_name, " ; ".join(reste)))


def alerte_quotidienne() -> dict:
    """Cron : rendez-vous à venir sur un employé inactif (parti ou utilisateur désactivé) → la direction."""
    auj = nowdate()
    inactifs = {e.name: e for e in frappe.get_all("Employee", fields=["name", "employee_name", "status", "user_id"])
                if e.status != "Active" or (e.user_id and not cint(frappe.db.get_value("User", e.user_id, "enabled")))}
    if not inactifs:
        return {"employes": [], "taches": 0}
    rows = frappe.db.sql("""select custom_choix_du_staff employe, count(*) n from `tabTache de travail`
                            where status = 'Open' and starts_on >= %s and custom_choix_du_staff in %s
                              and ifnull(custom_type_dintervention, '') not in %s
                            group by custom_choix_du_staff""", (auj, tuple(inactifs), TYPES_ABSENCE), as_dict=True)
    total = sum(r.n for r in rows)
    for r in rows:
        e = inactifs[r.employe]
        _prevenir_direction(_("⚠️ {0} rendez-vous à venir sur {1} (inactif) — traitez son départ depuis sa fiche")
                            .format(r.n, e.employee_name), "Employee", e.name)
    # Vérification finale de départ qui traîne.
    if frappe.db.has_column("Employee", CHAMP_VERIF):
        for e in frappe.get_all("Employee", filters={CHAMP_VERIF: ["is", "set"], CHAMP_STOCK_CLOS: ["is", "not set"]},
                                fields=["name", "employee_name", CHAMP_VERIF, CHAMP_DATE]):
            if e.get(CHAMP_DATE) and frappe.utils.date_diff(auj, e.get(CHAMP_DATE)) >= 7:
                _prevenir_direction(_("📦 Départ de {0} : la vérification finale du stock ({1}) n'est pas terminée")
                                    .format(e.employee_name, e.get(CHAMP_VERIF)), "Employee", e.name)
    return {"employes": [r.employe for r in rows], "taches": total}
