"""Clients gérés par le partenaire, et zones partenaires — décision du 03/10/2026.

Deux réalités derrière « le partenaire » (Economiq / Economic Aqua Solution, compte api.PARTNER_USER,
employé lié par Employee.user_id) :
  1. APPORTEUR à Tunis : son compte crée des clients et des commandes, et affecte nos techniciens. CES
     clients, il les gère : ils sortent de nos relances (SMS 10:00, e-mail, liste d'appels 07:00).
     Marqueur : `Customer.custom_gere_par_partenaire` (+ `custom_partenaire`), posé automatiquement à la
     création par son compte (hook before_insert), corrigeable à la main (page « Clients partenaire », bandeau
     de la fiche client). Repris pour l'existant par le patch ensure_partenaire_fields (109 clients).
  2. EXÉCUTANT à Sousse / Monastir / Mahdia (table « Partenaires » de Config Portail RDV) : les clients y sont
     LES NÔTRES et restent relancés par nous — SMS avec un modèle dédié, et liste d'appels « Zone partenaire »
     à part — ; les rendez-vous lui sont affectés et il exécute depuis notre outil.
Le partage automatique de la liste d'appels de Tunis avec son compte est retiré (hooks.py) : il n'appelle pas.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, nowdate

from customization_app.api import PARTNER_USER

CHAMP = "custom_gere_par_partenaire"
CHAMP_PARTENAIRE = "custom_partenaire"
ROLES = ("System Manager", "Sales Manager", "Maintenance Manager")
HORS_SECTEUR = "Hors Secteur"
TYPE_LISTE_ZONE = "Zone partenaire"


def champs_presents() -> bool:
    return frappe.db.has_column("Customer", CHAMP)


def employe_partenaire(user: str | None = None):
    return frappe.db.get_value("Employee", {"user_id": user or PARTNER_USER, "status": "Active"}, "name")


def clients_geres() -> set:
    """Les clients à exclure de nos relances. Vide tant que le champ n'existe pas."""
    if not champs_presents():
        return set()
    return set(frappe.get_all("Customer", filters={CHAMP: 1}, pluck="name"))


def customer_before_insert(doc, method=None):
    """Hook : une fiche créée par le compte partenaire est à lui dès la naissance."""
    if frappe.session.user == PARTNER_USER and champs_presents():
        doc.set(CHAMP, 1)
        doc.set(CHAMP_PARTENAIRE, employe_partenaire() or None)


# ── Zones partenaires (exécution) ────────────────────────────────────────────

def _normaliser(g: str) -> str:
    from customization_app.sectorisation import gouvernorat_proche
    return gouvernorat_proche(g) or (g or "").strip()


def zones_partenaires() -> dict:
    """{employe: {"nom", "gouvernorats": {normalisés}}} depuis la table Portail RDV Partenaire."""
    if not frappe.db.table_exists("Portail RDV Partenaire"):
        return {}
    out = {}
    for l in frappe.get_all("Portail RDV Partenaire", filters={"parent": "Config Portail RDV", "parenttype": "Config Portail RDV"},
                            fields=["employe", "nom_employe", "gouvernorats"], order_by="idx"):
        if not l.employe:
            continue
        couverts = {_normaliser(g) for g in (l.gouvernorats or "").split(",") if g.strip()}
        nom = l.nom_employe or frappe.db.get_value("Employee", l.employe, "employee_name") or l.employe
        out[l.employe] = {"nom": nom, "gouvernorats": couverts}
    return out


def zone_partenaire(gouvernorats, zones: dict, normaliser=None):
    """{"employe", "nom"} du partenaire qui couvre un des gouvernorats, sinon None. PURE (normalisation injectable)."""
    normaliser = normaliser or _normaliser
    for g in gouvernorats or []:
        cible = normaliser(g)
        for employe, z in zones.items():
            if cible and cible in z["gouvernorats"]:
                return {"employe": employe, "nom": z["nom"]}
    return None


def gouvernorats_du_client(customer: str) -> list:
    from customization_app.Maintenance.relance_maintenance_sms import gouvernorats_du_client as g
    return g(customer)


def zone_partenaire_du_client(customer: str, zones: dict | None = None):
    zones = zones if zones is not None else zones_partenaires()
    return zone_partenaire(gouvernorats_du_client(customer), zones) if zones else None


def classer(secteurs, gere: bool, zone) -> str:
    """Ce qu'une relance fait d'un client. PURE.
    'exclu' (géré par le partenaire) · 'secteur' (au moins une adresse dans nos secteurs) ·
    'zone_partenaire' (hors secteur mais couvert par un partenaire) · 'hors_secteur' (le reste)."""
    if gere:
        return "exclu"
    liste = [s.strip() for s in (secteurs or "").split(",") if s.strip()]
    if any(s != HORS_SECTEUR for s in liste):
        return "secteur"
    return "zone_partenaire" if zone else "hors_secteur"


# ── Page « Clients partenaire » ──────────────────────────────────────────────

def _acces():
    if not set(frappe.get_roles()) & set(ROLES):
        frappe.throw(_("Réservé aux responsables (System Manager, Sales Manager, Maintenance Manager)."), frappe.PermissionError)


def _candidats_a_verifier() -> set:
    """Créés par nous mais travaillés par lui : commandes à son nom, ou majorité de ses tâches sur 12 mois."""
    depuis = add_days(nowdate(), -365)
    so = set(frappe.db.sql_list("""select distinct so.customer from `tabSales Order` so join tabCustomer c on c.name = so.customer
                                   where so.owner = %s and c.owner <> %s and so.docstatus < 2""", (PARTNER_USER, PARTNER_USER)))
    emp = employe_partenaire()
    taches = set()
    if emp:
        taches = set(frappe.db.sql_list("""select t.custom_client from `tabTache de travail` t join tabCustomer c on c.name = t.custom_client
                                           where c.owner <> %s and t.starts_on >= %s and t.status <> 'Cancelled' and t.custom_client is not null
                                           group by t.custom_client having sum(t.custom_choix_du_staff = %s) * 2 > count(*)""",
                                        (PARTNER_USER, depuis, emp)))
    # Majorité de ses tâches chez un client de SA zone d'exécution : c'est le fonctionnement normal (nous gérons, il
    # exécute), pas un signe qu'il gère le client → retiré des cas à vérifier.
    zones = zones_partenaires()
    if zones and taches:
        taches = {c for c in taches if not zone_partenaire_du_client(c, zones)}
    return so | taches


def _gouvernorats_en_zone(zones: dict) -> list:
    """Les valeurs BRUTES de gouvernorat en base qui tombent dans une zone partenaire (comparaison tolérante)."""
    if not zones:
        return []
    bruts = frappe.db.sql_list("""select distinct coalesce(nullif(custom_state_s, ''), state) from tabAddress
                                  where coalesce(nullif(custom_state_s, ''), state) is not null""")
    couverts = set().union(*(z["gouvernorats"] for z in zones.values()))
    return [b for b in bruts if _normaliser(b) in couverts]


@frappe.whitelist()
def resume():
    _acces()
    if not champs_presents():
        return {"champs": False}
    zones = zones_partenaires()
    en_zone = _gouvernorats_en_zone(zones)
    nos_en_zone = 0
    if en_zone:
        nos_en_zone = frappe.db.sql("""select count(distinct c.name) from tabCustomer c
            join `tabDynamic Link` dl on dl.link_doctype = 'Customer' and dl.link_name = c.name and dl.parenttype = 'Address'
            join tabAddress a on a.name = dl.parent
            where ifnull(c.%s, 0) = 0 and coalesce(nullif(a.custom_state_s, ''), a.state) in %%s""" % CHAMP, (tuple(en_zone),))[0][0]
    geres = frappe.db.count("Customer", {CHAMP: 1})
    a_verifier = len([c for c in (_candidats_a_verifier() | set(frappe.get_all("Customer", filters={"owner": PARTNER_USER, CHAMP: 0}, pluck="name")))])
    return {"champs": True, "geres": geres, "a_verifier": a_verifier, "nos_en_zone": nos_en_zone,
            "zones": [{"employe": e, "nom": z["nom"], "gouvernorats": sorted(z["gouvernorats"])} for e, z in zones.items()],
            "partenaire_user": PARTNER_USER, "partenaire_employe": employe_partenaire()}


@frappe.whitelist()
def liste(onglet="geres", recherche=None, limite=300):
    """Les clients de l'onglet, enrichis : créé par, gouvernorats, commandes / tâches du partenaire, prochaine échéance."""
    _acces()
    if not champs_presents():
        return []
    limite = min(cint(limite) or 300, 1000)
    if onglet == "zones":
        limite = 1000
    zones = zones_partenaires()
    if onglet == "geres":
        noms = frappe.get_all("Customer", filters={CHAMP: 1}, pluck="name", order_by="modified desc", limit=limite)
    elif onglet == "a_verifier":
        noms = sorted(_candidats_a_verifier() | set(frappe.get_all("Customer", filters={"owner": PARTNER_USER}, pluck="name")))
        noms = [n for n in noms if not frappe.db.get_value("Customer", n, CHAMP)][:limite]
    elif onglet == "zones":
        en_zone = _gouvernorats_en_zone(zones)
        noms = frappe.db.sql_list("""select distinct c.name from tabCustomer c
            join `tabDynamic Link` dl on dl.link_doctype = 'Customer' and dl.link_name = c.name and dl.parenttype = 'Address'
            join tabAddress a on a.name = dl.parent
            where ifnull(c.%s, 0) = 0 and coalesce(nullif(a.custom_state_s, ''), a.state) in %%s order by c.customer_name limit %%s""" % CHAMP,
            (tuple(en_zone), limite)) if en_zone else []
    else:
        q = "%%%s%%" % (recherche or "").strip()
        if len(q) < 4:
            return []
        noms = frappe.get_all("Customer", or_filters=[["name", "like", q], ["customer_name", "like", q], ["custom_liste_telephone", "like", q]],
                              pluck="name", limit=limite)
    return _enrichir(noms, zones)


def _enrichir(noms: list, zones: dict) -> list:
    if not noms:
        return []
    emp = employe_partenaire()
    depuis = add_days(nowdate(), -365)
    base = {c.name: c for c in frappe.get_all("Customer", filters={"name": ["in", noms]},
                                              fields=["name", "customer_name", "owner", "creation", "custom_liste_telephone", CHAMP, CHAMP_PARTENAIRE])}
    gouv = {}
    for r in frappe.db.sql("""select dl.link_name, group_concat(distinct coalesce(nullif(a.custom_state_s, ''), a.state) separator ', ') g,
                                     group_concat(distinct a.custom_secteur separator ', ') s
                              from tabAddress a join `tabDynamic Link` dl on dl.parent = a.name and dl.parenttype = 'Address' and dl.link_doctype = 'Customer'
                              where dl.link_name in %s group by dl.link_name""", (tuple(noms),), as_dict=True):
        gouv[r.link_name] = r
    cmd = {r.customer: r for r in frappe.db.sql("""select customer, sum(owner = %s) p, count(*) n from `tabSales Order`
                                                    where docstatus < 2 and customer in %s group by customer""", (PARTNER_USER, tuple(noms)), as_dict=True)}
    tch = {r.c: r for r in frappe.db.sql("""select custom_client c, sum(custom_choix_du_staff = %s) p, count(*) n, max(starts_on) d from `tabTache de travail`
                                              where status <> 'Cancelled' and starts_on >= %s and custom_client in %s group by custom_client""",
                                          (emp or "", depuis, tuple(noms)), as_dict=True)}
    ech = {r.customer: r.d for r in frappe.db.sql("""select ms.customer, min(d.scheduled_date) d from `tabMaintenance Schedule Detail` d
                                                      join `tabMaintenance Schedule` ms on ms.name = d.parent and ms.docstatus = 1
                                                      where d.actual_date is null and d.scheduled_date >= %s and ms.customer in %s group by ms.customer""",
                                                   (add_days(nowdate(), -60), tuple(noms)), as_dict=True)}
    noms_emp = {}
    out = []
    for n in noms:
        c = base.get(n)
        if not c:
            continue
        g = gouv.get(n) or {}
        gl = [x.strip() for x in (g.get("g") or "").split(",") if x.strip()]
        zone = zone_partenaire(gl, zones) if zones else None
        part = c.get(CHAMP_PARTENAIRE)
        if part and part not in noms_emp:
            noms_emp[part] = frappe.db.get_value("Employee", part, "employee_name") or part
        tel = (c.custom_liste_telephone or "").strip().splitlines()
        out.append({"name": n, "client": c.customer_name, "cree_par": c.owner, "cree_par_partenaire": c.owner == PARTNER_USER,
                    "creation": str(c.creation)[:10], "telephone": tel[0] if tel else "",
                    "gouvernorats": ", ".join(gl), "secteurs": g.get("s") or "", "zone": zone["nom"] if zone else "",
                    "gere": cint(c.get(CHAMP)), "partenaire": noms_emp.get(part, ""),
                    "commandes_partenaire": cint((cmd.get(n) or {}).get("p")), "commandes": cint((cmd.get(n) or {}).get("n")),
                    "taches_partenaire": cint((tch.get(n) or {}).get("p")), "taches": cint((tch.get(n) or {}).get("n")),
                    "derniere_tache": str((tch.get(n) or {}).get("d") or "")[:10], "prochaine_echeance": str(ech.get(n) or "")[:10]})
    return out


@frappe.whitelist(methods=["POST"])
def basculer(clients, valeur):
    """Marque (1) ou démarque (0) des clients. Trace en commentaire sur chaque fiche."""
    _acces()
    if not champs_presents():
        frappe.throw(_("Champs absents : jouer le patch ensure_partenaire_fields."))
    noms = frappe.parse_json(clients) if isinstance(clients, str) else (clients or [])
    if isinstance(noms, str):
        noms = [noms]
    valeur = 1 if cint(valeur) else 0
    emp = employe_partenaire()
    qui = frappe.utils.get_fullname(frappe.session.user)
    faits = []
    for n in noms:
        if not frappe.db.exists("Customer", n):
            continue
        frappe.db.set_value("Customer", n, {CHAMP: valeur, CHAMP_PARTENAIRE: (emp if valeur else None)}, update_modified=True)
        frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": "Customer", "reference_name": n,
                        "content": (_("🤝 Marqué « géré par le partenaire » par {0} : exclu de nos relances SMS, e-mail et appels.")
                                    if valeur else _("↩️ Repris par nous (démarqué « géré par le partenaire ») par {0}.")).format(qui)
                        }).insert(ignore_permissions=True)
        faits.append(n)
    return {"clients": faits, "valeur": valeur}


@frappe.whitelist()
def etat(customer):
    """Pour le bandeau de la fiche client : marqué ?, par qui, créé par le compte partenaire ?, zone partenaire ?"""
    if not champs_presents():
        return {"champs": False}
    c = frappe.db.get_value("Customer", customer, ["owner", CHAMP, CHAMP_PARTENAIRE], as_dict=True) or {}
    part = c.get(CHAMP_PARTENAIRE)
    zone = zone_partenaire_du_client(customer)
    return {"champs": True, "gere": cint(c.get(CHAMP)), "partenaire": (frappe.db.get_value("Employee", part, "employee_name") if part else "") or "",
            "cree_par_partenaire": c.get("owner") == PARTNER_USER, "zone": zone["nom"] if zone else "",
            "peut_modifier": bool(set(frappe.get_roles()) & set(ROLES))}
