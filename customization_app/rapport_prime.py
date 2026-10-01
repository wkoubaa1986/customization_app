"""
Rapport Prime (Page Desk « rapport-prime ») — prime trimestrielle des vendeurs.

Porte l'ancien Script Report « Prime Jamel » (stocké en base, non versionné,
rapport préparé) vers une API whitelisted rendue par
customize_erpnext/page/rapport_prime. Toutes les règles vivent ici, le front
n'affiche que.

Règles métier (inchangées par rapport au Script Report) :
- Une commande compte pour un employé si elle est CRÉÉE par son compte
  (owner = user_id) ou si son client porte l'employé dans son équipe de vente ;
  et, quand la commande a elle-même une équipe de vente, seulement si l'employé
  y figure.
- Elle compte quand elle est livrée : « Fully Delivered », ou un BL validé (non
  clôturé) portant une réconciliation de stock ; le BL doit être daté dans la
  période.
- Période = du 1er janvier à la FIN DU TRIMESTRE EN COURS (année courante) ou
  au 31 décembre (année passée). C'est ce que faisait déjà le rapport, et c'est
  sur cette base cumulée que les primes ont été versées.
- Prime = COMMISSION × HT éligible ; le HT éligible exclut la main-d'œuvre et
  la livraison : lignes dont le GROUPE est dans GROUPES_NON_ELIGIBLES ou dont
  le code est dans CODES_NON_ELIGIBLES.
  ⚠️ Le Script Report cherchait le groupe « Main d'oeuvre » (apostrophe droite,
  « oe ») alors qu'il s'appelle « Main d’œuvre » (U+2019, « œ ») : aucune ligne
  de main-d'œuvre n'était exclue, et seul le code « Liv » l'était pour la
  livraison (pas F-km, Tr). Sur 2026, ~225 DT de prime en trop pour un seul
  vendeur. Corrigé ici le 30/09/2026 : les chiffres sont donc PLUS BAS que
  ceux de l'ancien rapport, et c'est voulu.
- Type « E/R » : une tâche de travail terminée, Entretien ou Réparation, sur la
  commande.

Nouveauté : chaque commande est rangée dans le trimestre de sa PREMIÈRE
livraison (date du BL). C'est le trimestre où le rapport l'a fait apparaître,
donc celui sur lequel la prime a été versée. Les versements sont saisis dans
« Prime Versee » (un par tranche, lié si possible à l'écriture de caisse) et
déduits : reste = calculé − versé, par trimestre et en cumul.
"""

from __future__ import annotations

import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

COMMISSION = 0.02  # taux historique des ventes ; la valeur vivante est dans « Config Prime »
GROUPES_MAIN_OEUVRE = ("Main d’œuvre",)                 # nom exact en base (U+2019, œ)
GROUPES_NON_ELIGIBLES = GROUPES_MAIN_OEUVRE + ("Livraison",)
CODES_NON_ELIGIBLES = ("Liv", "A-D", "Main d'oeuvre")  # ceux de l'ancien rapport, conservés
TYPES_ER = ("Entretien", "Réparation")
TYPES_TRAVAIL = ("Installation", "Entretien", "Réparation")  # tâches qui « réalisent » la main-d'œuvre
MODE_VENTES = "Ventes"
MODE_MO = "Main d'œuvre"
MODE_LES_DEUX = "Ventes + Main d'œuvre"
MODE_EXCLU = "Exclu"
MODES = (MODE_VENTES, MODE_MO, MODE_LES_DEUX, MODE_EXCLU)
ROLES = ("System Manager", "Banque")
PREMIERE_ANNEE = 2023
TRIMESTRES = ("Q1", "Q2", "Q3", "Q4")
LIBELLES = {"Q1": "1er trimestre", "Q2": "2e trimestre", "Q3": "3e trimestre", "Q4": "4e trimestre"}
PRECISION = 3


# --------------------------------------------------------------------------- règles pures

def bornes_trimestre(annee: int, trimestre: str) -> tuple[str, str]:
    i = TRIMESTRES.index(trimestre)
    debut = f"{annee}-{i * 3 + 1:02d}-01"
    fin = {"Q1": f"{annee}-03-31", "Q2": f"{annee}-06-30",
           "Q3": f"{annee}-09-30", "Q4": f"{annee}-12-31"}[trimestre]
    return debut, fin


def trimestre_de(date) -> str:
    """Trimestre calendaire d'une date (Q1..Q4)."""
    return TRIMESTRES[(getdate(date).month - 1) // 3]


def trimestre_courant(annee: int, aujourdhui=None) -> str:
    """Dernier trimestre pris en compte : celui d'aujourd'hui pour l'année en
    cours, Q4 pour une année passée, Q1 pour une année future (rien encore)."""
    aujourdhui = getdate(aujourdhui or nowdate())
    if aujourdhui.year > annee:
        return "Q4"
    if aujourdhui.year < annee:
        return "Q1"
    return trimestre_de(aujourdhui)


def periode(annee: int, aujourdhui=None) -> tuple[str, str]:
    """Du 1er janvier à la fin du trimestre courant — la borne du Script Report."""
    return f"{annee}-01-01", bornes_trimestre(annee, trimestre_courant(annee, aujourdhui))[1]


def ligne_eligible(item_group: str | None, item_code: str | None) -> bool:
    return not (item_group in GROUPES_NON_ELIGIBLES or item_code in CODES_NON_ELIGIBLES)


def prime_de(ht_eligible: float, taux: float = COMMISSION) -> float:
    """Sans arrondi par ligne : le Script Report sommait les primes brutes, et
    l'arrondi au millime ligne par ligne décalait le total de quelques dizaines
    de millimes sur mille commandes. Les totaux sont arrondis, pas les lignes."""
    return flt(flt(taux) * flt(ht_eligible))


def part_main_oeuvre(ht_mo: float, nb_techniciens: int) -> float:
    """Main-d'œuvre d'une commande partagée à parts égales entre les techniciens
    qui y ont réalisé une tâche (15 commandes sur 2026 en ont plusieurs)."""
    return flt(flt(ht_mo) / max(cint(nb_techniciens), 1))


def compte_ventes(mode: str) -> bool:
    return mode in (MODE_VENTES, MODE_LES_DEUX)


def compte_main_oeuvre(mode: str) -> bool:
    return mode in (MODE_MO, MODE_LES_DEUX)


def appartient(sales_team: list[str], employee_name: str) -> bool:
    """Sans équipe de vente sur la commande, on la garde ; sinon l'employé doit y être."""
    return not sales_team or employee_name in sales_team


def reste(calcule: float, verse: float) -> float:
    return flt(flt(calcule) - flt(verse), PRECISION)


def applique_coefficient(brut: float, coefficient: float | None) -> float:
    """Prime retenue = brut × coefficient % (100 % sans fiche)."""
    coef = 100.0 if coefficient is None else flt(coefficient)
    return flt(flt(brut) * coef / 100.0, PRECISION)


# --------------------------------------------------------------------------- accès données

def _config() -> dict:
    """Taux (en fraction) et mode par employé. Sans fiche : 2 % ventes, tout le monde en « Ventes »."""
    cfg = frappe.get_cached_doc("Config Prime") if frappe.db.exists("DocType", "Config Prime") else None
    if not cfg:
        return {"taux_vente": COMMISSION, "taux_mo": COMMISSION, "modes": {}}
    modes = {l.employee: (l.mode if l.mode in MODES else MODE_VENTES) for l in (cfg.employes or [])}
    return {"taux_vente": flt(cfg.taux_vente) / 100.0, "taux_mo": flt(cfg.taux_main_oeuvre) / 100.0, "modes": modes}


def _mode_de(config: dict, employee: str) -> str:
    return config["modes"].get(employee, MODE_VENTES)


def _verifier_acces():
    if frappe.session.user == "Administrator":
        return
    roles = set(frappe.get_roles())
    if not roles.intersection(ROLES):
        frappe.throw(_("Accès non autorisé"), frappe.PermissionError)


def _employes_vendeurs(annee: int, config: dict | None = None) -> list[dict]:
    """Employés actifs concernés : ceux qui ont une fiche Sales Person, ou qui ont
    créé au moins une commande dans l'année, ou qui ont déjà une prime versée, ou
    qui figurent dans Config Prime — moins ceux qui y sont « Exclu »."""
    config = config or _config()
    rows = frappe.db.sql("""
        SELECT e.name, e.employee_name, e.user_id
        FROM `tabEmployee` e
        WHERE e.status = 'Active'
          AND (
            EXISTS (SELECT 1 FROM `tabSales Person` sp WHERE sp.employee = e.name OR sp.name = e.employee_name)
            OR (e.user_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM `tabSales Order` so
                WHERE so.owner = e.user_id AND so.docstatus = 1
                  AND so.transaction_date BETWEEN %(d)s AND %(f)s))
            OR EXISTS (SELECT 1 FROM `tabPrime Versee` pv WHERE pv.employee = e.name AND pv.annee = %(annee)s)
            OR e.name IN %(config)s
          )
        ORDER BY e.employee_name
    """, {"d": f"{annee}-01-01", "f": f"{annee}-12-31", "annee": annee,
          "config": tuple(config["modes"]) or ("",)}, as_dict=True)
    return [r for r in rows if _mode_de(config, r.name) != MODE_EXCLU]


def _clients_de(employee_name: str) -> tuple:
    clients = frappe.db.sql_list("""
        SELECT DISTINCT c.name FROM `tabCustomer` c
        JOIN `tabSales Team` st ON st.parent = c.name AND st.parenttype = 'Customer'
        WHERE st.sales_person = %s
    """, (employee_name,))
    return tuple(clients) or ("",)


def _commandes_livrees(user_id: str, clients: tuple, debut: str, fin: str) -> list[dict]:
    """Commandes de l'employé livrées dans la période, avec la date de PREMIÈRE
    livraison (BL validé) qui décide du trimestre. Même périmètre que le Script
    Report : owner OU client de l'équipe de vente, BL daté dans la période."""
    return frappe.db.sql("""
        SELECT so.name, so.customer, so.customer_name, so.transaction_date,
               so.delivery_date, MIN(dn.posting_date) AS date_livraison
        FROM `tabSales Order` so
        LEFT JOIN `tabDelivery Note Item` dni ON dni.against_sales_order = so.name
        LEFT JOIN `tabDelivery Note` dn ON dn.name = dni.parent
             AND dn.docstatus = 1 AND dn.status != 'Closed'
        WHERE so.docstatus = 1
          AND (so.owner = %(user)s OR so.customer IN %(clients)s)
          AND (so.delivery_status = 'Fully Delivered'
               OR (dn.name IS NOT NULL AND dn.custom_reconciliation_stock IS NOT NULL))
          AND dn.posting_date BETWEEN %(debut)s AND %(fin)s
        GROUP BY so.name
    """, {"user": user_id or "", "clients": clients, "debut": debut, "fin": fin}, as_dict=True)


def _equipes_de_vente(noms: list[str]) -> dict[str, list[str]]:
    equipes = defaultdict(list)
    if not noms:
        return equipes
    for r in frappe.db.sql("""
        SELECT parent, sales_person FROM `tabSales Team`
        WHERE parenttype = 'Sales Order' AND parent IN %s
    """, (tuple(noms),), as_dict=True):
        equipes[r.parent].append(r.sales_person)
    return equipes


def _montants(noms: list[str]) -> dict[str, dict]:
    """TTC / HT totaux et éligibles par commande, en une requête."""
    if not noms:
        return {}
    rows = frappe.db.sql("""
        SELECT parent,
               SUM(amount) AS ttc, SUM(net_amount) AS ht,
               SUM(IF(item_group IN %(grp)s OR item_code IN %(codes)s, 0, amount)) AS ttc_e,
               SUM(IF(item_group IN %(grp)s OR item_code IN %(codes)s, 0, net_amount)) AS ht_e
        FROM `tabSales Order Item`
        WHERE parent IN %(noms)s
        GROUP BY parent
    """, {"grp": GROUPES_NON_ELIGIBLES, "codes": CODES_NON_ELIGIBLES, "noms": tuple(noms)}, as_dict=True)
    return {r.parent: r for r in rows}


def _commandes_er(noms: list[str]) -> set[str]:
    if not noms:
        return set()
    return set(frappe.db.sql_list("""
        SELECT DISTINCT commande_client FROM `tabTache de travail`
        WHERE status = 'Completed' AND custom_type_dintervention IN %s
          AND commande_client IN %s
    """, (TYPES_ER, tuple(noms))))


def _coefficients(annee: int) -> dict[tuple[str, str], dict]:
    """{(employé, trimestre): {coefficient, remarque, name}} pour l'année."""
    out = {}
    for r in frappe.get_all("Prime Coefficient", filters={"annee": annee},
                            fields=["name", "employee", "trimestre", "coefficient", "remarque", "appreciation"]):
        out[(r.employee, r.trimestre)] = {"name": r.name, "coefficient": flt(r.coefficient), "remarque": r.remarque,
                                          "appreciation": (r.appreciation or "").strip()}
    return out


def _versements(annee: int, employee: str | None = None) -> list[dict]:
    filtres = {"annee": annee}
    if employee:
        filtres["employee"] = employee
    return frappe.get_all(
        "Prime Versee", filters=filtres,
        fields=["name", "employee", "employee_name", "trimestre", "date_versement",
                "montant", "journal_entry", "remarque", "ecriture_soldee"],
        order_by="date_versement asc, creation asc")


def _lignes_employe(emp: dict, annee: int, debut: str, fin: str, taux: float = COMMISSION) -> list[dict]:
    commandes = _commandes_livrees(emp.user_id, _clients_de(emp.employee_name), debut, fin)
    noms = [c.name for c in commandes]
    equipes = _equipes_de_vente(noms)
    montants = _montants(noms)
    er = _commandes_er(noms)
    lignes = []
    for c in commandes:
        if not appartient(equipes.get(c.name, []), emp.employee_name):
            continue
        m = montants.get(c.name) or {}
        ht_e = flt(m.get("ht_e"))
        date_liv = c.date_livraison or c.delivery_date or c.transaction_date
        lignes.append({
            "commande": c.name,
            "date_commande": str(c.transaction_date),
            "date_livraison": str(date_liv),
            "trimestre": trimestre_de(date_liv) if getdate(date_liv).year == annee else None,
            "client": c.customer,
            "client_nom": c.customer_name,
            "ttc": flt(m.get("ttc"), PRECISION),
            "ttc_e": flt(m.get("ttc_e"), PRECISION),
            "ht": flt(m.get("ht"), PRECISION),
            "ht_e": flt(ht_e, PRECISION),
            "prime": prime_de(ht_e, taux),
            "type": "E/R" if c.name in er else "",
        })
    lignes.sort(key=lambda l: (l["date_livraison"], l["commande"]))
    return lignes


def _taches_employe(emp: dict, annee: int, debut: str, fin: str, taux: float) -> list[dict]:
    """Main-d'œuvre RÉALISÉE : une ligne par commande où le technicien a terminé
    une tâche Installation / Entretien / Réparation, datée de sa première tâche.
    Montant = HT des lignes « Main d’œuvre » de la commande, partagé si plusieurs
    techniciens ont travaillé sur la même commande."""
    rows = frappe.db.sql("""
        SELECT t.commande_client AS commande, MIN(t.starts_on) AS premiere, COUNT(*) AS nb_taches,
               GROUP_CONCAT(DISTINCT t.custom_type_dintervention ORDER BY t.custom_type_dintervention SEPARATOR ', ') AS types,
               so.customer, so.customer_name, so.transaction_date
        FROM `tabTache de travail` t
        JOIN `tabSales Order` so ON so.name = t.commande_client AND so.docstatus = 1
        WHERE t.status = 'Completed' AND t.custom_choix_du_staff = %(emp)s
          AND t.custom_type_dintervention IN %(types)s
          AND t.starts_on >= %(debut)s AND t.starts_on < DATE_ADD(%(fin)s, INTERVAL 1 DAY)
        GROUP BY t.commande_client
    """, {"emp": emp.name, "types": TYPES_TRAVAIL, "debut": debut, "fin": fin}, as_dict=True)
    if not rows:
        return []
    noms = tuple(r.commande for r in rows)
    mo = {r.parent: flt(r.ht) for r in frappe.db.sql("""
        SELECT parent, SUM(net_amount) AS ht FROM `tabSales Order Item`
        WHERE parent IN %s AND item_group IN %s GROUP BY parent
    """, (noms, GROUPES_MAIN_OEUVRE), as_dict=True)}
    techniciens = {r.commande: cint(r.n) for r in frappe.db.sql("""
        SELECT commande_client AS commande, COUNT(DISTINCT custom_choix_du_staff) AS n
        FROM `tabTache de travail`
        WHERE status = 'Completed' AND custom_type_dintervention IN %s AND commande_client IN %s
          AND IFNULL(custom_choix_du_staff, '') != ''
        GROUP BY commande_client
    """, (TYPES_TRAVAIL, noms), as_dict=True)}
    lignes = []
    for r in rows:
        ht_mo = flt(mo.get(r.commande))
        nb = techniciens.get(r.commande, 1)
        part = part_main_oeuvre(ht_mo, nb)
        date_t = getdate(r.premiere)
        lignes.append({
            "commande": r.commande, "date_commande": str(r.transaction_date), "date_tache": str(date_t),
            "trimestre": trimestre_de(date_t) if date_t.year == annee else None,
            "client": r.customer, "client_nom": r.customer_name, "types": r.types, "nb_taches": cint(r.nb_taches),
            "ht_mo": flt(ht_mo, PRECISION), "nb_techniciens": nb, "part": flt(part, PRECISION),
            "prime": prime_de(part, taux),
        })
    lignes.sort(key=lambda l: (l["date_tache"], l["commande"]))
    return lignes


def _synthese(lignes: list[dict], versements: list[dict], dernier: str, taches: list[dict] | None = None,
              coefs: dict | None = None) -> dict:
    """Par trimestre : brut (ventes + main-d'œuvre), coefficient, calculé (= brut × coef), versé, reste ;
    cumul jusqu'au trimestre courant. `coefs` : {trimestre: {coefficient, remarque}}."""
    taches = taches or []
    coefs = coefs or {}
    calc = defaultdict(float)
    calc_vente = defaultdict(float)
    calc_mo = defaultdict(float)
    nb = defaultdict(int)
    nb_taches = defaultdict(int)
    for l in lignes:
        if l["trimestre"]:
            calc[l["trimestre"]] += l["prime"]
            calc_vente[l["trimestre"]] += l["prime"]
            nb[l["trimestre"]] += 1
    for t in taches:
        if t["trimestre"]:
            calc[t["trimestre"]] += t["prime"]
            calc_mo[t["trimestre"]] += t["prime"]
            nb_taches[t["trimestre"]] += 1
    verse = defaultdict(float)
    for v in versements:
        verse[v["trimestre"]] += flt(v["montant"])
    trimestres = []
    cumul_calc = cumul_verse = 0.0
    cumul_brut = 0.0
    for t in TRIMESTRES:
        ouvert = TRIMESTRES.index(t) <= TRIMESTRES.index(dernier)
        coef_info = coefs.get(t) or {}
        coef = flt(coef_info.get("coefficient")) if coef_info else 100.0
        brut = flt(calc[t], PRECISION)
        c, v = applique_coefficient(brut, coef), flt(verse[t], PRECISION)
        if ouvert:
            cumul_brut += brut
            cumul_calc += c
            cumul_verse += v
        trimestres.append({
            "code": t, "libelle": LIBELLES[t], "ouvert": ouvert, "commandes": nb[t], "taches": nb_taches[t],
            "brut": brut, "coefficient": coef, "coefficient_fiche": coef_info.get("name"),
            "coefficient_remarque": coef_info.get("remarque"),
            "appreciation": coef_info.get("appreciation") or "",
            "calcule": c, "calcule_vente": applique_coefficient(calc_vente[t], coef),
            "calcule_mo": applique_coefficient(calc_mo[t], coef),
            "verse": v, "reste": reste(c, v),
            "cumul_calcule": flt(cumul_calc, PRECISION), "cumul_verse": flt(cumul_verse, PRECISION),
            "cumul_reste": reste(cumul_calc, cumul_verse),
        })
    total_verse = flt(sum(flt(v["montant"]) for v in versements), PRECISION)
    ouverts = [t for t in trimestres if t["ouvert"]]
    return {
        "trimestres": trimestres,
        "total_brut": flt(cumul_brut, PRECISION),
        "total_calcule": flt(cumul_calc, PRECISION),
        "total_vente": flt(sum(t["calcule_vente"] for t in ouverts), PRECISION),
        "total_mo": flt(sum(t["calcule_mo"] for t in ouverts), PRECISION),
        "total_verse": total_verse,
        "reste": reste(cumul_calc, total_verse),
        "hors_annee": flt(sum(l["prime"] for l in lignes if not l["trimestre"])
                          + sum(t["prime"] for t in taches if not t["trimestre"]), PRECISION),
    }


def _calcul_employe(emp: dict, annee: int, debut: str, fin: str, config: dict) -> tuple[list, list, str]:
    """Lignes ventes et lignes main-d'œuvre selon le mode de l'employé."""
    mode = _mode_de(config, emp.name)
    lignes = _lignes_employe(emp, annee, debut, fin, config["taux_vente"]) if compte_ventes(mode) else []
    taches = _taches_employe(emp, annee, debut, fin, config["taux_mo"]) if compte_main_oeuvre(mode) else []
    return lignes, taches, mode


# --------------------------------------------------------------------------- API

@frappe.whitelist()
def get_data(annee=None, employee=None):
    """Synthèse de tous les vendeurs pour l'année et, si `employee`, son détail."""
    _verifier_acces()
    annee = cint(annee) or getdate(nowdate()).year
    debut, fin = periode(annee)
    dernier = trimestre_courant(annee)
    config = _config()
    employes = _employes_vendeurs(annee, config)
    versements_annee = _versements(annee)
    par_emp = defaultdict(list)
    for v in versements_annee:
        par_emp[v["employee"]].append(v)
    coefs_annee = _coefficients(annee)

    def coefs_de(nom):
        return {t: c for (e, t), c in coefs_annee.items() if e == nom}

    synthese = []
    detail = None
    for emp in employes:
        lignes, taches, mode = _calcul_employe(emp, annee, debut, fin, config)
        s = _synthese(lignes, par_emp.get(emp.name, []), dernier, taches, coefs_de(emp.name))
        synthese.append({"employee": emp.name, "employee_name": emp.employee_name, "mode": mode, **s})
        if employee and emp.name == employee:
            detail = {
                "employee": emp.name, "employee_name": emp.employee_name, "mode": mode,
                "lignes": lignes, "taches": taches, "versements": par_emp.get(emp.name, []), **s,
            }
    if employee and detail is None:
        # Employé hors de la liste (inactif, sans vente, ou exclu) : on le calcule quand même.
        emp = frappe.db.get_value("Employee", employee, ["name", "employee_name", "user_id"], as_dict=True)
        if not emp:
            frappe.throw(_("Employé introuvable : {0}").format(employee))
        lignes, taches, mode = _calcul_employe(emp, annee, debut, fin, config)
        s = _synthese(lignes, par_emp.get(emp.name, []), dernier, taches, coefs_de(emp.name))
        detail = {"employee": emp.name, "employee_name": emp.employee_name, "mode": mode,
                  "lignes": lignes, "taches": taches, "versements": par_emp.get(emp.name, []), **s}

    annee_courante = getdate(nowdate()).year
    return {
        "annee": annee,
        "annees": list(range(annee_courante, PREMIERE_ANNEE - 1, -1)),
        "periode": {"debut": debut, "fin": fin, "dernier_trimestre": dernier,
                    "libelle": LIBELLES[dernier]},
        "commission": config["taux_vente"],
        "taux_vente": config["taux_vente"], "taux_mo": config["taux_mo"],
        "currency": frappe.get_cached_value("Company", frappe.defaults.get_global_default("company"),
                                            "default_currency") or "TND",
        "employes": [{"employee": e.name, "employee_name": e.employee_name} for e in employes],
        "synthese": synthese,
        "detail": detail,
        "ecritures_candidates": ecritures_prime_non_rattachees(annee),
        "peut_modifier": frappe.has_permission("Prime Versee", "create"),
    }


def ecritures_prime_non_rattachees(annee: int) -> list[dict]:
    """Écritures de caisse de l'année dont la remarque parle de prime et dont une
    partie au moins n'est rattachée à aucun versement. Une écriture peut porter
    la prime de deux vendeurs, ou plus que la prime : on propose le RESTE, que
    l'utilisateur réduit s'il ne prend qu'une part."""
    rattache = defaultdict(float)
    soldees = set()
    for r in frappe.get_all("Prime Versee", filters={"journal_entry": ["is", "set"]},
                            fields=["journal_entry", "montant", "ecriture_soldee"]):
        rattache[r.journal_entry] += flt(r.montant)
        if cint(r.ecriture_soldee):
            # Le reste de l'écriture est une AUTRE prime (hors calcul des ventes) : on ne la repropose pas.
            soldees.add(r.journal_entry)
    rows = frappe.db.sql("""
        SELECT name, posting_date, total_debit AS montant, user_remark
        FROM `tabJournal Entry`
        WHERE docstatus = 1 AND posting_date BETWEEN %s AND %s
          AND LOWER(IFNULL(user_remark, '')) LIKE '%%prime%%'
        ORDER BY posting_date DESC
    """, (f"{annee}-01-01", f"{annee}-12-31"), as_dict=True)
    out = []
    for r in rows:
        deja = flt(rattache.get(r.name), PRECISION)
        restant = flt(flt(r.montant) - deja, PRECISION)
        if restant <= 0.005 or r.name in soldees:
            continue
        out.append({"name": r.name, "date": str(r.posting_date), "montant": flt(r.montant, PRECISION),
                    "deja_rattache": deja, "reste": restant,
                    "remarque": (r.user_remark or "").split("\n")[0]})
    return out


@frappe.whitelist()
def enregistrer_versement(employee, annee, trimestre, montant, date_versement=None,
                          journal_entry=None, remarque=None, name=None, ecriture_soldee=0):
    """Crée (ou met à jour si `name`) un versement de prime."""
    _verifier_acces()
    if trimestre not in TRIMESTRES:
        frappe.throw(_("Trimestre invalide : {0}").format(trimestre))
    if flt(montant) <= 0:
        frappe.throw(_("Le montant doit être positif."))
    # Une écriture peut être partagée entre plusieurs versements : le plafond
    # (somme ≤ montant de l'écriture) est vérifié par Prime Versee.validate.
    doc = frappe.get_doc("Prime Versee", name) if name else frappe.new_doc("Prime Versee")
    doc.update({
        "employee": employee, "annee": cint(annee), "trimestre": trimestre,
        "montant": flt(montant, PRECISION),
        "date_versement": date_versement or nowdate(),
        "journal_entry": journal_entry or None, "remarque": remarque,
        "ecriture_soldee": cint(ecriture_soldee) if journal_entry else 0,
    })
    doc.save()
    return doc.name


@frappe.whitelist()
def enregistrer_coefficient(employee, annee, trimestre, coefficient, remarque=None):
    """Pose (ou remplace) le coefficient d'un employé pour un trimestre. 100 % = fiche supprimée."""
    _verifier_acces()
    if trimestre not in TRIMESTRES:
        frappe.throw(_("Trimestre invalide : {0}").format(trimestre))
    coef = flt(coefficient)
    if coef < 0 or coef > 100:
        frappe.throw(_("Le coefficient doit être entre 0 et 100 %."))
    existant = frappe.db.get_value("Prime Coefficient", {"employee": employee, "annee": cint(annee),
                                                         "trimestre": trimestre}, ["name", "appreciation"], as_dict=True)
    if coef == 100 and not (remarque or "").strip() and not (existant and (existant.appreciation or "").strip()):
        if existant:
            frappe.delete_doc("Prime Coefficient", existant.name)
        return None
    doc = frappe.get_doc("Prime Coefficient", existant.name) if existant else frappe.new_doc("Prime Coefficient")
    doc.update({"employee": employee, "annee": cint(annee), "trimestre": trimestre,
                "coefficient": coef, "remarque": remarque})
    doc.save()
    return doc.name


@frappe.whitelist()
def enregistrer_appreciation(employee, annee, trimestre, appreciation=None):
    """Commentaire du responsable sous le trimestre (imprimé en rouge dans le PDF).
    Vide, et coefficient à 100 % sans motif : la fiche est supprimée."""
    _verifier_acces()
    if trimestre not in TRIMESTRES:
        frappe.throw(_("Trimestre invalide : {0}").format(trimestre))
    texte = (appreciation or "").strip()
    existant = frappe.db.get_value("Prime Coefficient", {"employee": employee, "annee": cint(annee),
                                                         "trimestre": trimestre},
                                   ["name", "coefficient", "remarque"], as_dict=True)
    if not texte:
        if existant and flt(existant.coefficient) == 100 and not (existant.remarque or "").strip():
            frappe.delete_doc("Prime Coefficient", existant.name)
            return None
        if not existant:
            return None
    doc = frappe.get_doc("Prime Coefficient", existant.name) if existant else frappe.new_doc("Prime Coefficient")
    if not existant:
        doc.update({"employee": employee, "annee": cint(annee), "trimestre": trimestre, "coefficient": 100})
    doc.appreciation = texte
    doc.save()
    return doc.name


def prompt_appreciation(texte: str, employee_name: str, trimestre: str, annee: int, contexte: dict | None = None) -> tuple[str, str]:
    """(system, user) pour reformuler l'appréciation : même fond, même langue, ton
    professionnel et bienveillant, 2 à 4 phrases, sans inventer de chiffre."""
    c = contexte or {}
    system = (
        "Tu aides un responsable d'entreprise (Tunisie, français) à rédiger l'appréciation trimestrielle "
        "d'un employé, imprimée sur son relevé de prime. Reformule le texte fourni : garde exactement le fond "
        "et les faits, améliore la formulation (clair, professionnel, respectueux, direct), corrige "
        "l'orthographe. 2 à 4 phrases, pas de titre, pas de guillemets, pas de liste. N'invente aucun chiffre "
        "ni aucun fait absent du texte. Réponds dans la langue du texte fourni."
    )
    user = (
        f"Employé : {employee_name}. Période : {LIBELLES.get(trimestre, trimestre)} {annee}.\n"
        + (f"Contexte chiffré (à ne citer que s'il est déjà évoqué) : prime brute {c.get('brut')}, "
           f"coefficient {c.get('coefficient')} %, {c.get('commandes')} commandes, {c.get('taches')} tâches.\n" if c else "")
        + f"Texte à reformuler :\n{texte.strip()}"
    )
    return system, user


@frappe.whitelist()
def ameliorer_appreciation(texte, employee, annee, trimestre, contexte=None):
    """Renvoie une reformulation IA du commentaire (OpenAI, mêmes réglages que la LCI)."""
    _verifier_acces()
    texte = (texte or "").strip()
    if len(texte) < 3:
        frappe.throw(_("Écrivez d'abord quelques mots à améliorer."))
    if isinstance(contexte, str):
        contexte = json.loads(contexte or "{}")
    from customization_app.liste_commande_import import _model, _openai_client
    nom = frappe.db.get_value("Employee", employee, "employee_name") or employee
    system, user = prompt_appreciation(texte, nom, trimestre, cint(annee), contexte)
    client = _openai_client()
    params = {"model": _model(), "messages": [{"role": "system", "content": system},
                                             {"role": "user", "content": user}]}
    try:
        resp = client.chat.completions.create(temperature=0.4, **params)
    except Exception as e:
        if "temperature" in str(e):
            resp = client.chat.completions.create(**params)
        else:
            raise
    return (resp.choices[0].message.content or "").strip().strip('"')


@frappe.whitelist()
def supprimer_versement(name):
    _verifier_acces()
    frappe.delete_doc("Prime Versee", name)
    return True


# --------------------------------------------------------------------------- PDF par employé

def _detail_employe(annee: int, employee: str) -> dict:
    """Le détail d'UN employé (sans recalculer les autres) : trimestres, versements, commandes."""
    emp = frappe.db.get_value("Employee", employee, ["name", "employee_name", "user_id"], as_dict=True)
    if not emp:
        frappe.throw(_("Employé introuvable : {0}").format(employee))
    debut, fin = periode(annee)
    dernier = trimestre_courant(annee)
    config = _config()
    lignes, taches, mode = _calcul_employe(emp, annee, debut, fin, config)
    versements = _versements(annee, emp.name)
    coefs = {t: c for (e, t), c in _coefficients(annee).items() if e == emp.name}
    return {"employee": emp.name, "employee_name": emp.employee_name, "annee": annee, "mode": mode,
            "taux_vente": config["taux_vente"], "taux_mo": config["taux_mo"],
            "periode": {"debut": debut, "fin": fin, "libelle": LIBELLES[dernier]},
            "lignes": lignes, "taches": taches, "versements": versements,
            **_synthese(lignes, versements, dernier, taches, coefs)}


def _html_pdf(d: dict, devise: str) -> str:
    """Rapport imprimable d'un vendeur : trimestres, versements, commandes prises en compte.
    Rien d'autre (pas la synthèse des autres vendeurs, pas les écritures à rattacher)."""
    from frappe.utils import escape_html as esc, format_date, fmt_money

    def m(v):
        return fmt_money(v, precision=3, currency=None)

    def cls(v):
        return "pos" if flt(v) > 0.0005 else "neg" if flt(v) < -0.0005 else "zero"

    h = [f"""<!doctype html><html><head><meta charset="utf-8"><style>
      body {{ font-family: 'Helvetica', 'Arial', sans-serif; font-size: 10.5px; color: #222; margin: 0; }}
      h1 {{ font-size: 17px; margin: 0 0 2px; }} h2 {{ font-size: 12.5px; margin: 16px 0 6px; border-bottom: 1px solid #999; padding-bottom: 3px; }}
      .sub {{ color: #666; font-size: 10px; margin-bottom: 8px; }}
      table {{ width: 100%; border-collapse: collapse; }} th, td {{ padding: 3px 6px; border-bottom: 1px solid #e3e3e3; }}
      th {{ background: #f1f3f5; text-align: left; font-size: 9.5px; text-transform: uppercase; color: #555; }}
      .num {{ text-align: right; white-space: nowrap; }} .pos {{ color: #1e7e34; font-weight: bold; }} .neg {{ color: #b02a37; font-weight: bold; }}
      .zero {{ color: #888; }} .muted {{ color: #888; }} tr.tot td {{ font-weight: bold; background: #f7f7f7; }}
      tr.trim td {{ background: #eef2f7; font-weight: bold; }} .kpi {{ font-size: 14px; font-weight: bold; }}
      .badge {{ font-size: 8.5px; font-weight: bold; padding: 1px 5px; border: 1px solid #9bb4d8; border-radius: 6px; color: #1f3a5f; }}
      .ferme td {{ color: #aaa; }}
    </style></head><body>
    <h1>Rapport Prime {d['annee']} — {esc(d['employee_name'])}</h1>
    <div class="sub">{esc(d['employee'])} · commandes livrées du {format_date(d['periode']['debut'])} au {format_date(d['periode']['fin'])}
      (fin du {d['periode']['libelle']}) · mode « {esc(d.get('mode', MODE_VENTES))} » · ventes : {flt(d.get('taux_vente', COMMISSION) * 100, 2):g} % du HT éligible (hors main-d'œuvre et livraison)
      · main-d'œuvre : {flt(d.get('taux_mo', COMMISSION) * 100, 2):g} % du HT de main-d'œuvre des tâches réalisées · montants en {esc(devise)}
      · édité le {format_date(nowdate())}</div>

    <h2>Prime par trimestre</h2>
    <table><thead><tr><th>Trimestre</th><th class="num">Commandes</th><th class="num">Tâches</th><th class="num">Prime ventes</th><th class="num">Prime main-d'œuvre</th><th class="num">Brut</th><th class="num">Coef.</th><th class="num">Prime retenue</th><th class="num">Versé</th>
      <th class="num">Reste</th><th class="num">Cumul calculé</th><th class="num">Cumul versé</th><th class="num">Cumul reste</th></tr></thead><tbody>"""]
    for t in d["trimestres"]:
        if t["ouvert"]:
            appr = (t.get("appreciation") or "").strip()
            h.append(f"<tr><td>{t['libelle']}</td><td class='num'>{t['commandes']}</td><td class='num'>{t['taches']}</td>"
                     f"<td class='num'>{m(t['calcule_vente'])}</td><td class='num'>{m(t['calcule_mo'])}</td>"
                     f"<td class='num'>{m(t['brut'])}</td><td class='num'>{flt(t['coefficient'], 2):g} %</td><td class='num'><b>{m(t['calcule'])}</b></td>"
                     f"<td class='num'>{m(t['verse'])}</td><td class='num {cls(t['reste'])}'>{m(t['reste'])}</td>"
                     f"<td class='num'>{m(t['cumul_calcule'])}</td><td class='num'>{m(t['cumul_verse'])}</td>"
                     f"<td class='num {cls(t['cumul_reste'])}'>{m(t['cumul_reste'])}</td></tr>")
            if appr:
                # Sous le trimestre, en rouge : l'appréciation du responsable.
                h.append(f"<tr class='appr'><td colspan='13' style='color:#b02a37;font-style:italic;padding:2px 6px 8px 18px'>"
                         f"Appréciation {t['libelle']} : {esc(appr)}</td></tr>")
        else:
            h.append(f"<tr class='ferme'><td>{t['libelle']} (à venir)</td>" + "<td class='num'>—</td>" * 12 + "</tr>")
    h.append(f"<tr class='tot'><td>Total à ce jour</td><td class='num'>{sum(t['commandes'] for t in d['trimestres'] if t['ouvert'])}</td>"
             f"<td class='num'>{sum(t['taches'] for t in d['trimestres'] if t['ouvert'])}</td>"
             f"<td class='num'>{m(d['total_vente'])}</td><td class='num'>{m(d['total_mo'])}</td>"
             f"<td class='num'>{m(d['total_brut'])}</td><td></td>"
             f"<td class='num'>{m(d['total_calcule'])}</td><td class='num'>{m(d['total_verse'])}</td>"
             f"<td class='num {cls(d['reste'])}'>{m(d['reste'])}</td><td colspan='3' class='muted'>reste à verser = calculé − versé</td></tr>")
    h.append("</tbody></table>")

    h.append("<h2>Primes versées</h2>")
    if not d["versements"]:
        h.append("<div class='muted'>Aucun versement enregistré.</div>")
    else:
        h.append("<table><thead><tr><th>Date</th><th>Trimestre</th><th class='num'>Montant</th><th>Écriture de caisse</th><th>Remarque</th></tr></thead><tbody>")
        for v in d["versements"]:
            je = esc(v["journal_entry"] or "—") + (" (part)" if cint(v.get("ecriture_soldee")) else "")
            h.append(f"<tr><td>{format_date(v['date_versement'])}</td><td><span class='badge'>{v['trimestre']}</span></td>"
                     f"<td class='num'>{m(v['montant'])}</td><td>{je}</td><td class='muted'>{esc(v['remarque'] or '')}</td></tr>")
        h.append(f"<tr class='tot'><td colspan='2'>Total versé</td><td class='num'>{m(d['total_verse'])}</td><td colspan='2'></td></tr></tbody></table>")

    taches = d.get("taches") or []
    if taches or compte_main_oeuvre(d.get("mode", MODE_VENTES)):
        h.append(f"<h2>Main-d'œuvre réalisée — tâches prises en compte ({len(taches)})</h2>")
        if not taches:
            h.append("<div class='muted'>Aucune tâche terminée sur la période.</div>")
        else:
            h.append("<table><thead><tr><th>Commande</th><th>Date cde</th><th>1re tâche</th><th>Client</th><th>Types</th>"
                     "<th class='num'>Tâches</th><th class='num'>HT main-d'œuvre</th><th class='num'>Techniciens</th><th class='num'>Part</th><th class='num'>Prime</th></tr></thead><tbody>")
            groupes = defaultdict(list)
            for t in taches:
                groupes[t["trimestre"] or "hors"].append(t)
            for q in list(TRIMESTRES) + ["hors"]:
                ls = groupes.get(q)
                if not ls:
                    continue
                lib = LIBELLES.get(q, f"Hors {d['annee']} (non comptées)")
                h.append(f"<tr class='trim'><td colspan='6'>{lib} · {len(ls)} commande{'s' if len(ls) > 1 else ''}</td>"
                         f"<td class='num'>{m(sum(t['ht_mo'] for t in ls))}</td><td></td><td class='num'>{m(sum(t['part'] for t in ls))}</td>"
                         f"<td class='num'>{m(sum(t['prime'] for t in ls))}</td></tr>")
                for t in ls:
                    h.append(f"<tr><td>{esc(t['commande'])}</td><td>{format_date(t['date_commande'])}</td><td>{format_date(t['date_tache'])}</td>"
                             f"<td>{esc(t['client_nom'] or t['client'])}</td><td>{esc(t['types'])}</td><td class='num'>{t['nb_taches']}</td>"
                             f"<td class='num'>{m(t['ht_mo'])}</td><td class='num'>{t['nb_techniciens']}</td><td class='num'>{m(t['part'])}</td>"
                             f"<td class='num'><b>{m(t['prime'])}</b></td></tr>")
            h.append("</tbody></table>")

    if d["lignes"] or compte_ventes(d.get("mode", MODE_VENTES)):
        h.append(f"<h2>Ventes — commandes prises en compte ({len(d['lignes'])})</h2>")
    if not compte_ventes(d.get("mode", MODE_VENTES)) and not d["lignes"]:
        pass
    elif not d["lignes"]:
        h.append("<div class='muted'>Aucune commande livrée sur la période.</div>")
    else:
        h.append("<table><thead><tr><th>Commande</th><th>Date cde</th><th>Livraison</th><th>Client</th><th class='num'>TTC</th>"
                 "<th class='num'>TTC élig.</th><th class='num'>HT</th><th class='num'>HT élig.</th><th class='num'>Prime</th><th>Type</th></tr></thead><tbody>")
        groupes = defaultdict(list)
        for l in d["lignes"]:
            groupes[l["trimestre"] or "hors"].append(l)
        for t in list(TRIMESTRES) + ["hors"]:
            ls = groupes.get(t)
            if not ls:
                continue
            lib = LIBELLES.get(t, f"Hors {d['annee']} (non comptées)")
            tot = {k: sum(l[k] for l in ls) for k in ("ttc", "ttc_e", "ht", "ht_e", "prime")}
            h.append(f"<tr class='trim'><td colspan='4'>{lib} · {len(ls)} commande{'s' if len(ls) > 1 else ''}</td>"
                     f"<td class='num'>{m(tot['ttc'])}</td><td class='num'>{m(tot['ttc_e'])}</td><td class='num'>{m(tot['ht'])}</td>"
                     f"<td class='num'>{m(tot['ht_e'])}</td><td class='num'>{m(tot['prime'])}</td><td></td></tr>")
            for l in ls:
                h.append(f"<tr><td>{esc(l['commande'])}</td><td>{format_date(l['date_commande'])}</td><td>{format_date(l['date_livraison'])}</td>"
                         f"<td>{esc(l['client_nom'] or l['client'])}</td><td class='num'>{m(l['ttc'])}</td><td class='num'>{m(l['ttc_e'])}</td>"
                         f"<td class='num'>{m(l['ht'])}</td><td class='num'>{m(l['ht_e'])}</td><td class='num'><b>{m(l['prime'])}</b></td>"
                         f"<td>{'<span class=badge>E/R</span>' if l['type'] else ''}</td></tr>")
        h.append("</tbody></table>")
    h.append("</body></html>")
    return "".join(h)


@frappe.whitelist()
def telecharger_pdf(annee, employee):
    """PDF d'UN vendeur : trimestres, versements, commandes prises en compte (paysage)."""
    from frappe.utils.pdf import get_pdf

    _verifier_acces()
    annee = cint(annee) or getdate(nowdate()).year
    d = _detail_employe(annee, employee)
    devise = frappe.get_cached_value("Company", frappe.defaults.get_global_default("company"),
                                     "default_currency") or "TND"
    pdf = get_pdf(_html_pdf(d, devise), options={"orientation": "Landscape", "margin-top": "10mm",
                                                 "margin-bottom": "10mm", "margin-left": "8mm", "margin-right": "8mm"})
    nom = frappe.scrub(d["employee_name"]).replace("_", "-")
    frappe.local.response.filename = f"prime-{annee}-{nom}.pdf"
    frappe.local.response.filecontent = pdf
    frappe.local.response.type = "download"
