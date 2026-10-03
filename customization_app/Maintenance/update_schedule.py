# -*- coding: utf-8 -*-
"""Échéanciers de maintenance — création à la vente d'une machine, décalage à l'achat de consommables,
prolongation quand les visites sont épuisées, nettoyage des clients B2B / non intéressés. Cron de nuit (daily_long).

Revue du 03/10/2026 (bugs corrigés ici, règles dans « Config Relances » = customization_app.relances_config) :
- la prolongation ne s'exécutait JAMAIS : elle lisait `sms_1`/`sms_2` au lieu de `custom_sms_1`/`custom_sms_2`, et aurait
  planté en écrivant le statut « En Attente » (inexistant) ; 23 échéanciers étaient muets ;
- le décalage prenait « la ligne la plus proche » sans regarder l'article : acheter des cartouches d'osmoseur marquait la
  visite de l'adoucisseur comme faite, écrasait une visite déjà réalisée, et une commande rejouée re-décalait tout ;
  → décalage PAR FAMILLE et par article, lignes réalisées intouchées, une commande n'agit qu'une fois ;
- une commande en erreur arrêtait tout le passage (et l'état partiel était enregistré) → chaque commande est isolée ;
- le nettoyage B2B visait aussi les brouillons (cancel() impossible → crash quotidien) et corrigeait les clients protégés ;
- la famille « devinée » sortait d'un set (ordre aléatoire) → ordre de priorité du réglage ;
- un échéancier deviné n'était jamais remplacé quand la vraie machine arrivait → il est retiré s'il n'a servi à rien ;
- les journaux [SUMMARY] partaient en INFO que la prod n'écrit pas → journal dédié qui écrit.
"""
from __future__ import unicode_literals

import frappe
from frappe.utils import add_days, add_months, add_years, cint, date_diff, getdate, nowdate

from customization_app import relances_config as RC
from customization_app.utils.run_safely import run_safely

# ----------------------------------------------------------------------
# Compatibilité : anciens dictionnaires encore importés ailleurs (relance_maintenance_sms, creation_liste_appelle).
# La VÉRITÉ est dans Config Relances (relances_config) ; ceux-ci sont les valeurs historiques.
# ----------------------------------------------------------------------
LIST_TO_IGNORE = tuple(RC.liste(RC.DEFAUTS["clients_proteges"]))
B2B_GROUPS = tuple(RC.liste(RC.DEFAUTS["groupes_b2b"]))
DEFAULT_MACHINE_ITEM_BY_FAMILY = {f["code"]: f["article_type"] for f in RC.DEFAUT_FAMILLES if f["article_type"]}
MACHINE_FAMILY_BY_GROUP = {g: f["code"] for f in RC.DEFAUT_FAMILLES for g in RC.liste(f["groupes_machines"])}
MACHINE_ITEM_GROUPS = set(MACHINE_FAMILY_BY_GROUP)
CONSUMABLE_FAMILY_BY_GROUP = {}
for _f in RC.DEFAUT_FAMILLES:
    for _g in RC.liste(_f["groupes_consommables"]):
        CONSUMABLE_FAMILY_BY_GROUP.setdefault(_g, []).append(_f["code"])
CONSUMABLE_ITEM_GROUPS = set(CONSUMABLE_FAMILY_BY_GROUP)

PERIODICITY_MONTHS = {"Monthly": 1, "Quarterly": 3, "Half Yearly": 6, "Yearly": 12}
SALES_PERSON = "Équipe des Ventes"


def _logger():
    return RC.journal("maintenance_scheduler")


def log(msg):
    _logger().info(msg)


# ----------------------------------------------------------------------
# Clients
# ----------------------------------------------------------------------

def is_b2b_group(customer_group, cfg=None):
    return RC.est_b2b(customer_group, cfg)


def is_customer_interested(customer):
    val = (getattr(customer, "custom_intéressé_par_le_service_entretien", "") or "").strip()
    return val.upper() == "OUI"


def _client(name, cache=None):
    """Les 3 champs utiles du client, en une requête, mémorisés par passage."""
    cache = cache if cache is not None else {}
    if name not in cache:
        cache[name] = frappe.db.get_value("Customer", name, ["name", "customer_group", "custom_intéressé_par_le_service_entretien"],
                                          as_dict=True) or frappe._dict(name=name)
    return cache[name]


# ----------------------------------------------------------------------
# 1) NETTOYAGE
# ----------------------------------------------------------------------

def verify_data_base(cfg=None):
    """B2B : la case « intéressé » repasse à Non et leurs échéanciers SOUMIS sont retirés ; idem pour les clients
    « pas intéressé + pas de SMS ». Les clients protégés ne sont touchés par aucune des deux règles. Un brouillon ou un
    échéancier déjà annulé est supprimé sans cancel() (c'est ce qui faisait planter tout le cron). Chaque suppression
    est isolée : une erreur n'empêche ni les autres ni la suite du passage."""
    cfg = cfg or RC.config()
    b2b = tuple(cfg["groupes_b2b_liste"]) or ("",)
    proteges = tuple(cfg["clients_proteges_liste"]) or ("",)
    corriges = 0
    for name in frappe.db.sql_list("""select name from tabCustomer where custom_intéressé_par_le_service_entretien = 'Oui'
                                     and customer_group in %s and name not in %s""", (b2b, proteges)):
        frappe.db.set_value("Customer", name, "custom_intéressé_par_le_service_entretien", "Non")
        corriges += 1
    log(f"[CLEANUP] Clients B2B corrigés (intéressé -> Non) : {corriges}")

    a_retirer = frappe.db.sql("""select sm.name, sm.docstatus, sm.customer from `tabMaintenance Schedule` sm join tabCustomer c on c.name = sm.customer
                                 where c.name not in %s and ((c.customer_group in %s)
                                    or (c.custom_intéressé_par_le_service_entretien = 'Non' and c.custom_envoi_sms = 'Non' and sm.docstatus = 1))""",
                              (proteges, b2b), as_dict=True)
    supprimes, erreurs = 0, 0
    for row in a_retirer:
        frappe.db.savepoint("ms_cleanup")
        try:
            if row.docstatus == 1:
                doc = frappe.get_doc("Maintenance Schedule", row.name)
                doc.flags.ignore_links = True
                doc.flags.ignore_permissions = True
                doc.cancel()
            frappe.delete_doc("Maintenance Schedule", row.name, force=True, ignore_permissions=True)
            supprimes += 1
            log(f"[CLEANUP] Échéancier {row.name} supprimé (client {row.customer}, docstatus {row.docstatus})")
        except Exception:
            frappe.db.rollback(save_point="ms_cleanup")
            erreurs += 1
            frappe.log_error(frappe.get_traceback(), f"Nettoyage échéancier {row.name}")
    log(f"[CLEANUP] Échéanciers supprimés : {supprimes}, erreurs : {erreurs}")
    return {"corriges": corriges, "supprimes": supprimes, "erreurs": erreurs}


# ----------------------------------------------------------------------
# 2) ARTICLES & FAMILLES
# ----------------------------------------------------------------------

def _item(code, cache=None):
    """{item_code, item_name, item_group} ou None, en une requête, mémorisé."""
    cache = cache if cache is not None else {}
    if code not in cache:
        cache[code] = frappe.db.get_value("Item", code, ["item_code", "item_name", "item_group"], as_dict=True)
    return cache[code]


def resoudre_article(brut, cache=None):
    """Le code tel quel, puis sans espaces, puis avec « GPD » recollé : les codes de la vente ont parfois été ressaisis."""
    essais = [brut, (brut or "").strip(), (brut or "").replace(" ", ""), (brut or "").replace(" ", "").replace("GPD", " GPD")]
    vus = set()
    for code in essais:
        if code and code not in vus:
            vus.add(code)
            item = _item(code, cache)
            if item:
                return item
    return None


def famille_machine(item, fams=None):
    """La famille d'une MACHINE par son groupe d'articles, sinon None."""
    return RC.famille_du_groupe_machine((item or {}).get("item_group"), fams) if item else None


def familles_consommable(item, fams=None):
    """Les familles qu'un CONSOMMABLE peut concerner : groupe du réglage, sinon indices du nom (4040/8040, 3012, 50/75/100 GPD,
    UV, « sel » = adoucisseur). PURE avec `fams`."""
    if not item:
        return []
    fams = fams or RC.familles()
    out = RC.familles_du_groupe_consommable(item.get("item_group"), fams)
    if out:
        return out
    nom = (item.get("item_name") or "").replace(" ", "").lower()
    if "sel" in nom and RC.famille("ADOUCISSEUR", fams):
        return ["ADOUCISSEUR"]
    if "4040" in nom or "8040" in nom:
        return ["RO_IND"]
    if any(x in nom for x in ("3012", "3013", "600gpd", "800gpd")):
        return ["RO_COM"]
    if any(x in nom for x in ("50gpd", "75gpd", "100gpd")):
        return ["RO_DOM"]
    if "uv" in nom:
        return ["UV"]
    return []


def est_consommable(item, fams=None):
    if not item:
        return False
    return (item.get("item_group") or "").strip() in RC.groupes_consommables(fams) or "sel" in (item.get("item_name") or "").lower()


# Compat (anciens appelants)
def map_item_to_machine_family(item, only_machine=True):
    d = {"item_code": getattr(item, "item_code", None), "item_name": getattr(item, "item_name", None), "item_group": getattr(item, "item_group", None)}
    if only_machine:
        f = famille_machine(d)
        return [f] if f else []
    return familles_consommable(d)


def get_default_item_for_family(family, fams=None):
    f = RC.famille(family, fams)
    code = (f or {}).get("article_type")
    if not code:
        log(f"[GUESS] Aucune machine type configurée pour la famille {family}")
        return None
    item = _item(code)
    if not item:
        log(f"[GUESS] Machine type '{code}' introuvable pour la famille {family}")
    return item


# ----------------------------------------------------------------------
# 3) ÉCHÉANCIERS D'UN CLIENT, PAR FAMILLE
# ----------------------------------------------------------------------

def familles_des_echeanciers(customer_name, fams=None, cache=None):
    """{nom_echeancier: {familles}} pour les échéanciers SOUMIS du client (une requête, pas un get_doc par échéancier)."""
    out = {}
    for ms, code in frappe.db.sql("""select msi.parent, msi.item_code from `tabMaintenance Schedule Item` msi
                                     join `tabMaintenance Schedule` ms on ms.name = msi.parent
                                     where ms.customer = %s and ms.docstatus = 1""", (customer_name,)):
        f = famille_machine(_item(code, cache), fams)
        out.setdefault(ms, set())
        if f:
            out[ms].add(f)
    return out


def get_customer_machine_families(customer_name, fams=None, cache=None):
    return set().union(*familles_des_echeanciers(customer_name, fams, cache).values()) if customer_name else set()


def find_machine_schedules(customer_name, family, fams=None, cache=None):
    return [frappe.get_doc("Maintenance Schedule", name) for name, fs in familles_des_echeanciers(customer_name, fams, cache).items() if family in fs]


def maintenance_has_family(maintenance, family, fams=None, cache=None):
    return any(famille_machine(_item(i.item_code, cache), fams) == family for i in getattr(maintenance, "items", []))


def _famille_de_la_ligne(ms, item_code, fams=None, cache=None):
    return famille_machine(_item(item_code, cache), fams)


# ----------------------------------------------------------------------
# 4) PROLONGATION
# ----------------------------------------------------------------------

def has_free_sms_slots(ms):
    """Reste-t-il une visite qui n'a encore reçu aucun SMS ? (champs réels : custom_sms_1 / custom_sms_2)"""
    return any(not getattr(r, "custom_sms_1", None) and not getattr(r, "custom_sms_2", None) for r in getattr(ms, "schedules", []))


def build_item_periodicity_map(ms):
    return {i.item_code: PERIODICITY_MONTHS.get((getattr(i, "periodicity", None) or "Half Yearly").strip(), 6)
            for i in getattr(ms, "items", []) if i.item_code}


def prochaines_dates(derniere, mois, n, aujourd_hui):
    """Les n prochaines visites après `derniere`, au pas de `mois` : on saute les dates déjà trop anciennes pour que la
    première visite ajoutée soit la prochaine réellement due (au plus une période de retard). PURE."""
    derniere, aujourd_hui = getdate(derniere), getdate(aujourd_hui)
    plancher = add_months(aujourd_hui, -mois)
    d = add_months(derniere, mois)
    while d < plancher:
        d = add_months(d, mois)
    return [add_months(d, mois * i) for i in range(n)]


def extend_schedule_for_sms(ms, extra_visits_per_item=None, aujourd_hui=None):
    """Quand toutes les visites ont reçu leurs SMS, on ajoute `visites_ajoutees` visites par article (réglage). Statut
    « Pending », nom d'article et vendeur repris de la ligne précédente. → nombre de lignes ajoutées."""
    if has_free_sms_slots(ms):
        return 0
    lignes = getattr(ms, "schedules", [])
    if not lignes:
        return 0
    n = cint(extra_visits_per_item) or RC.config()["visites_ajoutees"]
    periodicites = build_item_periodicity_map(ms)
    par_article = {}
    for r in lignes:
        if r.item_code:
            par_article.setdefault(r.item_code, []).append(r)
    ajoutees = 0
    for code, rows in par_article.items():
        dates = [getdate(r.scheduled_date) for r in rows if r.scheduled_date]
        if not dates:
            continue
        modele = rows[-1]
        for d in prochaines_dates(max(dates), periodicites.get(code, 6), n, aujourd_hui or nowdate()):
            ms.append("schedules", {"item_code": code, "item_name": getattr(modele, "item_name", None), "scheduled_date": d,
                                    "completion_status": "Pending", "sales_person": getattr(modele, "sales_person", None) or SALES_PERSON})
            ajoutees += 1
    if ajoutees:
        log(f"[EXTEND] {ms.name}: +{ajoutees} visite(s) ({len(par_article)} article(s) × {n})")
    return ajoutees


# ----------------------------------------------------------------------
# 5) DÉCALAGE À L'ACHAT DE CONSOMMABLES
# ----------------------------------------------------------------------

def ligne_a_marquer(lignes, delivery_date, famille, famille_de, deja_commande=None):
    """La visite que l'achat « réalise » : parmi les lignes de la FAMILLE concernée, non réalisées, la plus proche de la
    livraison. None si la commande a déjà été appliquée à cet échéancier ou s'il n'y a rien à marquer. PURE.
    `lignes` : objets avec item_code, scheduled_date, actual_date, completion_status, custom_sales_order ;
    `famille_de(item_code)` → famille."""
    delivery_date = getdate(delivery_date)
    if deja_commande and any((getattr(r, "custom_sales_order", None) or "") == deja_commande for r in lignes):
        return None
    candidates = [r for r in lignes if r.scheduled_date and not getattr(r, "actual_date", None)
                  and getattr(r, "completion_status", None) != "Fully Completed"
                  and (famille is None or famille_de(r.item_code) == famille)]
    if not candidates:
        return None
    return min(candidates, key=lambda r: (abs(date_diff(getdate(r.scheduled_date), delivery_date)), str(r.scheduled_date), r.idx or 0))


def shift_schedule_for_delivery(target_ms, delivery_date, sales_order, famille=None, fams=None, cache=None, aujourd_hui=None):
    """Marque la visite de la famille concernée comme réalisée par la commande, et décale les visites SUIVANTES du même
    article d'autant. Les autres machines de l'échéancier et les visites déjà réalisées ne bougent pas ; une commande
    déjà appliquée ne rejoue pas. → True si quelque chose a changé."""
    lignes = getattr(target_ms, "schedules", None) or []
    if not lignes:
        log(f"[UPDATE] {target_ms.name} sans visites")
        return False
    delivery_date = getdate(delivery_date)
    ref = ligne_a_marquer(lignes, delivery_date, famille, lambda code: _famille_de_la_ligne(target_ms, code, fams, cache), sales_order)
    if ref is None:
        log(f"[UPDATE] {target_ms.name} : rien à marquer pour {sales_order} (famille {famille}) — déjà appliqué ou aucune visite en attente")
        return False
    ancienne = getdate(ref.scheduled_date)
    decalage = date_diff(delivery_date, ancienne)
    ref.actual_date = delivery_date
    ref.custom_sales_order = sales_order
    ref.completion_status = "Fully Completed"
    ref.scheduled_date = delivery_date
    for r in lignes:
        if r is ref or r.item_code != ref.item_code or not r.scheduled_date:
            continue
        if getdate(r.scheduled_date) > ancienne and not getattr(r, "actual_date", None):
            r.scheduled_date = add_days(getdate(r.scheduled_date), decalage)
    extend_schedule_for_sms(target_ms, aujourd_hui=aujourd_hui)
    target_ms.flags.ignore_permissions = True
    target_ms.save()
    log(f"[UPDATE] {target_ms.name} ({target_ms.customer}) : visite {ref.item_code} du {ancienne} réalisée par {sales_order}, "
        f"suivantes décalées de {decalage} j")
    return True


# ----------------------------------------------------------------------
# 6) CRÉATION
# ----------------------------------------------------------------------

def _nouvel_echeancier(customer, delivery_date, lignes_items, cfg):
    ms = frappe.new_doc("Maintenance Schedule")
    ms.customer = customer
    ms.transaction_date = delivery_date
    for it in lignes_items:
        ms.append("items", {"item_code": it["item_code"], "item_name": it["item_name"], "start_date": delivery_date,
                            "end_date": add_years(getdate(delivery_date), 5), "sales_order": it["sales_order"],
                            "periodicity": cfg["periodicite_defaut"], "no_of_visits": cfg["nb_visites"], "sales_person": SALES_PERSON})
    ms.flags.ignore_permissions = True
    ms.insert()
    ms.submit()
    return ms


def _retirer_echeancier_devine(customer_name, famille, fams, cache):
    """La vraie machine arrive : l'échéancier deviné de la même famille (machine type, aucune visite réalisée) est retiré.
    S'il porte déjà des visites réalisées, on le garde et on le dit."""
    f = RC.famille(famille, fams)
    type_code = (f or {}).get("article_type")
    if not type_code:
        return
    for ms in find_machine_schedules(customer_name, famille, fams, cache):
        if [i.item_code for i in ms.items] != [type_code]:
            continue
        if any(r.actual_date for r in ms.schedules):
            log(f"[GUESS] {ms.name} deviné ({type_code}) gardé : des visites y sont réalisées")
            continue
        ms.flags.ignore_links = ms.flags.ignore_permissions = True
        ms.cancel()
        frappe.delete_doc("Maintenance Schedule", ms.name, force=True, ignore_permissions=True)
        log(f"[GUESS] {ms.name} deviné ({type_code}) retiré : la vraie machine {famille} est vendue")


def create_maintenance_for_machines(i_sal, cfg=None, fams=None, cache=None):
    """Un échéancier pour les MACHINES de la commande (groupes « machine » du réglage). → True si créé."""
    cfg, fams = cfg or RC.config(), fams or RC.familles()
    client = _client(i_sal["customer"])
    if is_b2b_group(client.customer_group, cfg) or (client.custom_intéressé_par_le_service_entretien or "").upper() != "OUI":
        return False
    machines, vus, familles_vendues = [], set(), set()
    for brut in [x.strip() for x in (i_sal.get("items") or "").split(",,") if x.strip()]:
        item = resoudre_article(brut, cache)
        if not item:
            log(f"[WARN] SO {i_sal['sales_order']} : article '{brut}' introuvable")
            continue
        fam = famille_machine(item, fams)
        if not fam or item["item_code"] in vus:
            continue
        vus.add(item["item_code"])
        familles_vendues.add(fam)
        machines.append({"item_code": item["item_code"], "item_name": item["item_name"], "sales_order": i_sal["sales_order"]})
    if not machines:
        return False
    for fam in familles_vendues:
        _retirer_echeancier_devine(i_sal["customer"], fam, fams, cache)
    ms = _nouvel_echeancier(i_sal["customer"], i_sal["delivery_date"], machines, cfg)
    log(f"[CREATE] {ms.name} pour {i_sal['customer']}, SO {i_sal['sales_order']}, machines : {', '.join(m['item_code'] for m in machines)}")
    return True


def create_maintenance_from_consumable_guess(customer_name, family, delivery_date, sales_order, cfg=None, fams=None):
    cfg, fams = cfg or RC.config(), fams or RC.familles()
    item = get_default_item_for_family(family, fams)
    if not item:
        return None
    ms = _nouvel_echeancier(customer_name, delivery_date, [{"item_code": item["item_code"], "item_name": item["item_name"], "sales_order": sales_order}], cfg)
    log(f"[GUESS] {ms.name} deviné ({family} / {item['item_code']}) pour {customer_name} depuis {sales_order}")
    return ms


def update_single_machine_schedule(customer_name, family, delivery_date, sales_order, cfg=None, fams=None, cache=None):
    """Les échéanciers de cette famille chez ce client sont décalés ; s'il n'y en a aucun, un échéancier deviné est créé."""
    cfg, fams = cfg or RC.config(), fams or RC.familles()
    delivery_date = getdate(delivery_date)
    existants = find_machine_schedules(customer_name, family, fams, cache)
    if not existants:
        ms = create_maintenance_from_consumable_guess(customer_name, family, delivery_date, sales_order, cfg, fams)
        if ms:
            shift_schedule_for_delivery(ms, delivery_date, sales_order, family, fams, cache)
        return bool(ms)
    fait = False
    for ms in existants:
        fait = shift_schedule_for_delivery(ms, delivery_date, sales_order, family, fams, cache) or fait
    return fait


def familles_cibles(items, fams=None):
    """(toutes les familles possibles, familles « sûres » = consommables à famille unique) pour les articles d'une commande. PURE."""
    cibles, sures = set(), set()
    for item in items:
        if not est_consommable(item, fams):
            continue
        fs = familles_consommable(item, fams)
        cibles.update(fs)
        if len(fs) == 1:
            sures.add(fs[0])
    return cibles, sures


def update_maintenance_schedule(i_sal, cfg=None, fams=None, cache=None):
    """Achat de consommables : on décale les échéanciers des familles concernées que le client possède ; s'il n'en a aucune,
    on en devine UNE (famille sûre puis ordre de priorité du réglage — plus de tirage dans un set)."""
    cfg, fams = cfg or RC.config(), fams or RC.familles()
    client = _client(i_sal["customer"])
    if is_b2b_group(client.customer_group, cfg) or (client.custom_intéressé_par_le_service_entretien or "").upper() != "OUI":
        return "skip"
    items = [it for it in (resoudre_article(b, cache) for b in (i_sal.get("items") or "").split(",,") if b.strip()) if it]
    cibles, sures = familles_cibles(items, fams)
    if not cibles:
        log(f"[NO-LINK] SO {i_sal['sales_order']} : aucun lien machine/consommable")
        return "no-link"
    existantes = get_customer_machine_families(i_sal["customer"], fams, cache)
    communes = cibles & existantes
    if communes:
        for fam in sorted(communes, key=lambda c: ([f["code"] for f in fams] + [c]).index(c)):
            update_single_machine_schedule(i_sal["customer"], fam, i_sal["delivery_date"], i_sal["sales_order"], cfg, fams, cache)
        return "update"
    devinee = RC.famille_prioritaire(sures or cibles, fams)
    log(f"[GUESS] SO {i_sal['sales_order']} : famille devinée {devinee} (sûres={sorted(sures)}, cibles={sorted(cibles)})")
    return "guess" if update_single_machine_schedule(i_sal["customer"], devinee, i_sal["delivery_date"], i_sal["sales_order"], cfg, fams, cache) else "no-guess"


# ----------------------------------------------------------------------
# 7) PASSAGE PRINCIPAL
# ----------------------------------------------------------------------

def commandes_a_traiter(cfg):
    """Commandes livrées (ou BL rapproché) de clients intéressés non B2B, dans la fenêtre du réglage."""
    today = nowdate()
    return frappe.db.sql("""
        SELECT so.name AS sales_order, so.customer, so.delivery_date,
               GROUP_CONCAT(DISTINCT so_item.item_code SEPARATOR ",, ") AS items
        FROM `tabSales Order` so
        JOIN `tabCustomer` cust ON so.customer = cust.name
        LEFT JOIN `tabSales Order Item` so_item ON so.name = so_item.parent
        LEFT JOIN `tabDelivery Note Item` dni ON so.name = dni.against_sales_order
        LEFT JOIN `tabDelivery Note` dn ON dni.parent = dn.name AND dn.docstatus = 1 AND dn.status != 'Closed'
        WHERE so.docstatus = 1
          AND IFNULL(cust.customer_group, '') NOT IN %(b2b)s
          AND UPPER(IFNULL(cust.custom_intéressé_par_le_service_entretien, '')) = 'OUI'
          AND (so.delivery_status = 'Fully Delivered' OR (dn.name IS NOT NULL AND dn.custom_reconciliation_stock IS NOT NULL))
          AND so.delivery_date BETWEEN %(de)s AND %(a)s
        GROUP BY so.name, so.customer, so.delivery_date
        ORDER BY so.delivery_date""",
        {"b2b": tuple(cfg["groupes_b2b_liste"]) or ("",), "de": add_months(today, -cfg["fenetre_mois_avant"]), "a": add_months(today, cfg["fenetre_mois_apres"])},
        as_dict=True)


def commandes_couvertes():
    """Les commandes déjà portées par un échéancier soumis (ligne machine ou visite réalisée)."""
    out = set()
    for (so,) in frappe.db.sql("""select msi.sales_order from `tabMaintenance Schedule Item` msi join `tabMaintenance Schedule` ms on ms.name = msi.parent
                                  where ms.docstatus = 1 and ifnull(msi.sales_order, '') <> ''
                                  union select msd.custom_sales_order from `tabMaintenance Schedule Detail` msd join `tabMaintenance Schedule` ms on ms.name = msd.parent
                                  where ms.docstatus = 1 and ifnull(msd.custom_sales_order, '') <> ''"""):
        for s in (so or "").split(","):
            if s.strip():
                out.add(s.strip())
    return out


@frappe.whitelist()
def run_maintenance_planning():
    """Nettoyage, puis chaque commande livrée de la fenêtre : machine → échéancier, consommables → décalage / devinette.
    CHAQUE COMMANDE EST ISOLÉE (savepoint) : une erreur est journalisée et n'arrête pas les autres."""
    if frappe.session.user != "Administrator" and not frappe.flags.in_test:
        frappe.only_for(("System Manager", "Maintenance Manager"))
    cfg, fams, cache = RC.config(), RC.familles(), {}
    log("========== [CRON] Début run_maintenance_planning ==========")
    summary = {"total_sales_orders": 0, "new_ms_created": 0, "updated_from_consumables": 0, "guessed": 0,
               "no_link": 0, "already_covered": 0, "errors": 0, "cleanup": verify_data_base(cfg)}
    commandes = commandes_a_traiter(cfg)
    couvertes = commandes_couvertes()
    summary["total_sales_orders"] = len(commandes)
    erreurs = []
    for i_sal in commandes:
        if i_sal["sales_order"] in couvertes:
            summary["already_covered"] += 1
            continue
        frappe.db.savepoint("ms_so")
        try:
            if create_maintenance_for_machines(i_sal, cfg, fams, cache):
                summary["new_ms_created"] += 1
            else:
                r = update_maintenance_schedule(i_sal, cfg, fams, cache)
                if r == "update":
                    summary["updated_from_consumables"] += 1
                elif r == "guess":
                    summary["guessed"] += 1
                else:
                    summary["no_link"] += 1
        except Exception:
            frappe.db.rollback(save_point="ms_so")
            summary["errors"] += 1
            erreurs.append(i_sal["sales_order"])
            frappe.log_error(frappe.get_traceback(), f"Échéancier maintenance — commande {i_sal['sales_order']}")
    summary["prolonges"] = prolonger_echeanciers_epuises()
    summary_line = ("[SUMMARY] total=%(total_sales_orders)s new_ms=%(new_ms_created)s from_conso=%(updated_from_consumables)s "
                    "guessed=%(guessed)s no_link=%(no_link)s already_covered=%(already_covered)s prolonges=%(prolonges)s errors=%(errors)s" % summary)
    if erreurs:
        summary_line += " (" + ", ".join(erreurs[:10]) + ")"
    log(summary_line)
    log("========== [CRON] Fin run_maintenance_planning ==========")
    return {"summary": summary, "log": summary_line}


def echeanciers_epuises() -> list:
    """Les échéanciers soumis dont TOUTES les visites ont déjà reçu un SMS (plus rien à relancer) — une requête."""
    return frappe.db.sql_list("""select ms.name from `tabMaintenance Schedule` ms join `tabMaintenance Schedule Detail` d on d.parent = ms.name
                                 where ms.docstatus = 1 group by ms.name
                                 having sum(d.custom_sms_1 is null and d.custom_sms_2 is null) = 0""")


def prolonger_echeanciers_epuises(extra_visits_per_item=None):
    """Chaque nuit : les échéanciers épuisés reçoivent leurs visites suivantes (la prolongation « au fil des SMS » ne les
    rattrape jamais, puisqu'ils n'ont plus de visite à relancer — 23 échéanciers muets au 03/10/2026). Chaque échéancier
    est isolé. → nombre prolongés."""
    n, erreurs = 0, 0
    for name in echeanciers_epuises():
        frappe.db.savepoint("ms_extend")
        try:
            ms = frappe.get_doc("Maintenance Schedule", name)
            if extend_schedule_for_sms(ms, cint(extra_visits_per_item) or None):
                ms.flags.ignore_permissions = True
                ms.save()
                n += 1
        except Exception:
            frappe.db.rollback(save_point="ms_extend")
            erreurs += 1
            frappe.log_error(frappe.get_traceback(), f"Prolongation échéancier {name}")
    log(f"[EXTEND] échéanciers épuisés prolongés : {n}, erreurs : {erreurs}")
    return n


@frappe.whitelist()
def extend_sms_for_all_active_schedules(extra_visits_per_item=None):
    """Même chose, à la main (bouton / console)."""
    frappe.only_for(("System Manager", "Maintenance Manager"))
    return prolonger_echeanciers_epuises(extra_visits_per_item)


def run_cron():
    return run_safely("Cron - Mise à jour échéancier maintenance", run_maintenance_planning)
