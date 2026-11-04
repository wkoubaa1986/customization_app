"""Clients B2B — le tableau de bord du commercial (demande utilisateur du 04/11/2026).

Qui sont nos clients professionnels (Technicien, Quincaillerie, Compte Pro, Pro Grand Rayon), ce qu'ils
nous achètent, à quel rythme, s'ils se servent de leur fiche client (/fiche_client) et ce qu'ils y font —
et surtout : LESQUELS VOIR CETTE SEMAINE, et pourquoi.

⚠️ LES VENTES, C'EST LES BONS DE LIVRAISON. CA, panier, rythme : bons de livraison validés (retours
déduits du CA, jamais comptés comme un achat). Ni les commandes (une commande n'est pas livrée), ni les
factures (facturation mensuelle pour les comptes pro : les chiffres n'ont rien à voir). C'est aussi la base
de la ristourne. Seul l'impayé se lit sur les factures. (Règle fixée par l'utilisateur le 04/11/2026.)

⚠️ UN RETARD SE MESURE AU RYTHME DU CLIENT, PAS À UN SEUIL FIXE. Un compte pro qui livre tous les 18 jours
et silencieux depuis 40 est plus urgent qu'un technicien qui achète tous les 31 jours. Le rythme est la
médiane des écarts entre ses derniers achats (jours distincts) ; à moins de 3 achats, on prend celui de son
groupe.

CE QU'ON SAIT DE LA FICHE CLIENT : les connexions depuis décembre 2025 (« Ristourne Dashboard Logging ») et,
depuis le 04/11/2026, chaque action (recherche, photo, liste de prix, PDF, rapport — booking_ristourne.journal).
Une ligne sans `action` est une connexion d'avant le journal.

LE LIEN AVEC LES TOURNÉES COMMERCIALES (tournee.py, page Ma Tournée) : le tableau dit QUI voir ; la tournée dit QUAND
et AVEC QUEL RÉSULTAT. Dans un sens, `planifier_visites` met les clients cochés dans une tournée (existante, ou une
nouvelle Tâche « Tournée commerciale » pour un commercial et une date) — le commercial la déroule dans Ma Tournée.
Dans l'autre, chaque ligne lit ses « Visite Commerciale » : dernière visite réalisée (résultat, intérêt, prochaine
action, relance), prochaine visite planifiée, et l'alerte « relance dépassée ». La clôture d'une visite dans Ma Tournée
met déjà à jour la dernière visite du client (tournee._maj_derniere_visite).

LECTURE SEULE, sauf `visite_faite` (date de dernière visite + commentaire), `rattacher_commercial` et
`planifier_visites`.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta

import frappe
from frappe import _
from frappe.utils import add_months, getdate, nowdate, strip_html

GROUPES_B2B = ("Technicien", "Quincaillerie", "Compte Pro", "Pro Grand Rayon")
#: Réservé au responsable B2B et à la direction (demande utilisateur 04/11/2026) — pas à tout vendeur :
#: presque tous les comptes ont Sales User / Sales Manager. Le rôle est en fixture (fixtures/role.json),
#: l'onglet « Clients B2B » de la barre latérale aussi (workspace.json).
ROLES = ("System Manager", "Responsable B2B")

FACTEUR_RETARD = 1.5          # en retard : silence > 1,5 × son rythme
MIN_ACHATS_RYTHME = 3         # en dessous, le rythme du groupe fait foi
NB_ACHATS_RYTHME = 10         # le rythme se lit sur les derniers achats, pas sur deux ans
HISTORIQUE_MOIS = 24          # fenêtre lue pour le rythme et la tendance
SEUIL_BAISSE = -0.20          # 6 derniers mois vs 6 précédents
CONNEXION_RECENTE_JOURS = 30  # une connexion plus vieille n'est plus une intention
DELAI_INTENTION_JOURS = 2     # on laisse 2 jours au client pour passer commande
SEUIL_IMPAYE = 1.0

#: code -> (libellé, poids pour le tri, explication pour le commercial)
ALERTES = {
    "retard": ("En retard", 3, "Pas de livraison depuis plus de 1,5 × son rythme habituel : à appeler."),
    "perdu": ("Perdu ?", 3, "A déjà acheté, mais rien sur la période : à reconquérir."),
    "intention": ("Connecté sans acheter", 3, "S'est connecté à sa fiche récemment sans livraison depuis : il cherche quelque chose."),
    "baisse": ("CA en baisse", 2, "Les 6 derniers mois font au moins 20 % de moins que les 6 précédents."),
    "impaye": ("Impayé", 2, "Factures validées non réglées."),
    "relance": ("Relance dépassée", 2, "La relance prévue à la dernière visite commerciale est passée, sans nouvelle visite ni livraison depuis."),
    "jamais_connecte": ("Jamais connecté", 1, "Accès à la fiche client autorisé, jamais utilisé : à lui montrer en visite."),
    "sans_commercial": ("Sans commercial", 1, "Aucun commercial rattaché sur sa fiche."),
}


# ------------------------------------------------------------------ règles pures

def _d(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return getdate(v)


def mois_glissants(aujourd_hui: date, n: int = 12) -> list[str]:
    """Les n derniers mois, du plus ancien au plus récent, 'AAAA-MM' (le mois courant inclus)."""
    out = []
    annee, mois = aujourd_hui.year, aujourd_hui.month
    for _i in range(n):
        out.append("%04d-%02d" % (annee, mois))
        mois -= 1
        if mois == 0:
            annee, mois = annee - 1, 12
    return list(reversed(out))


def rythme(dates) -> float | None:
    """Jours entre deux achats, médiane des écarts sur les derniers achats (jours distincts). None si trop peu."""
    jours = sorted({_d(x) for x in dates or [] if _d(x)})[-NB_ACHATS_RYTHME:]
    if len(jours) < MIN_ACHATS_RYTHME:
        return None
    ecarts = [(b - a).days for a, b in zip(jours, jours[1:])]
    return round(float(statistics.median(ecarts)), 1)


def tendance(par_mois: dict, aujourd_hui: date) -> dict:
    """6 derniers mois vs 6 précédents. variation = None quand il n'y a rien avant (pas une hausse infinie)."""
    mois = mois_glissants(aujourd_hui, 12)
    avant = round(sum(float(par_mois.get(m) or 0) for m in mois[:6]), 3)
    recent = round(sum(float(par_mois.get(m) or 0) for m in mois[6:]), 3)
    variation = round((recent - avant) / avant, 3) if avant > 0 else None
    return {"recent": recent, "avant": avant, "variation": variation}


def palier(ca_ht_annee: float, paliers: list) -> dict | None:
    """Où en est le client dans les paliers de ristourne de son groupe, et ce qui lui manque pour le suivant."""
    if not paliers:
        return None
    tries = sorted(paliers, key=lambda p: float(p.get("minimum") or 0))
    atteint = None
    prochain = None
    for p in tries:
        if float(p.get("minimum") or 0) <= ca_ht_annee:
            atteint = p
        elif prochain is None:
            prochain = p
    out = {"taux": float(atteint["taux"]) if atteint else 0.0,
           "seuil": float(atteint["minimum"]) if atteint else 0.0}
    if prochain:
        out["prochain"] = {"taux": float(prochain["taux"]), "seuil": float(prochain["minimum"]),
                           "manque": round(float(prochain["minimum"]) - ca_ht_annee, 3)}
    return out


def alertes(l: dict, aujourd_hui: date, rythme_groupe: float | None) -> list[str]:
    """Les alertes d'un client. `l` : nb_bl, derniere_bl, a_deja_achete, rythme, derniere_connexion,
    acces_fiche, tendance, nb_bl_12m, impaye, commercial."""
    out = []
    derniere_bl = _d(l.get("derniere_bl"))
    if not l.get("nb_bl") and l.get("a_deja_achete"):
        out.append("perdu")
    elif derniere_bl:
        r = l.get("rythme") or rythme_groupe
        if r and (aujourd_hui - derniere_bl).days > FACTEUR_RETARD * r:
            out.append("retard")
    dc = _d(l.get("derniere_connexion"))
    if dc:
        depuis = (aujourd_hui - dc).days
        if DELAI_INTENTION_JOURS <= depuis <= CONNEXION_RECENTE_JOURS and (not derniere_bl or derniere_bl < dc):
            out.append("intention")
    elif l.get("acces_fiche"):
        out.append("jamais_connecte")
    v = (l.get("tendance") or {}).get("variation")
    if v is not None and v <= SEUIL_BAISSE and (l.get("nb_bl_12m") or 0) >= MIN_ACHATS_RYTHME:
        out.append("baisse")
    if float(l.get("impaye") or 0) > SEUIL_IMPAYE:
        out.append("impaye")
    relance = _d(((l.get("visite") or {}).get("derniere") or {}).get("date_relance"))
    if relance and relance <= aujourd_hui and not (l.get("visite") or {}).get("prochaine") \
            and not (derniere_bl and derniere_bl >= relance):
        out.append("relance")
    if not l.get("commercial"):
        out.append("sans_commercial")
    return out


def score(codes: list) -> int:
    return sum(ALERTES[c][1] for c in codes if c in ALERTES)


# ------------------------------------------------------------------ lecture

def _guard():
    if not set(frappe.get_roles()) & set(ROLES):
        frappe.throw(_("Réservé aux rôles : {0}.").format(", ".join(ROLES)), frappe.PermissionError)


def _debut_periode(periode: str, aujourd_hui: date) -> date:
    if periode == "annee":
        return date(aujourd_hui.year, 1, 1)
    return _d(add_months(aujourd_hui, -12))


def _paliers_par_groupe(aujourd_hui: date) -> dict:
    rows = frappe.db.sql(
        """select r.group_client, p.montant_ht_minimum minimum, p.montant_ht_maximum maximum,
                  p.`pourcentage_de_réduction` taux
           from `tabRistourne` r join `tabPalier ristourne` p on p.parent = r.name
           where r.docstatus = 1 and ifnull(r.client, '') = ''
             and %(j)s between r.valable_de and r.`valable_jusquà`""", {"j": aujourd_hui}, as_dict=True)
    out = defaultdict(list)
    for r in rows:
        out[r.group_client].append(r)
    return out


def _visites_par_client(noms: list, aujourd_hui: date) -> dict:
    """Pour chaque client : sa dernière visite commerciale réalisée et sa prochaine visite planifiée (tournee.py)."""
    if not noms or not frappe.db.table_exists("Visite Commerciale"):
        return {}
    rows = frappe.db.sql(
        """select v.name, v.client, v.statut, v.resultat, v.niveau_interet, v.prochaine_action, v.date_relance,
                  v.tache_relance, v.montant_potentiel, v.tournee, t.date_tournee, t.tache_de_travail,
                  e.employee_name commercial
           from `tabVisite Commerciale` v
           join `tabTournee Commerciale` t on t.name = v.tournee
           left join `tabEmployee` e on e.name = ifnull(v.commercial, t.commercial)
           where v.client in %(c)s and v.statut in ('Réalisée', 'Planifiée', 'En cours')
           order by t.date_tournee desc, v.creation desc""", {"c": noms}, as_dict=True)
    out = defaultdict(lambda: {"derniere": None, "prochaine": None})
    for r in rows:
        d = _d(r.date_tournee)
        if r.statut == "Réalisée":
            if out[r.client]["derniere"] is None:
                out[r.client]["derniere"] = {
                    "name": r.name, "date": str(d) if d else None, "resultat": r.resultat,
                    "interet": r.niveau_interet, "prochaine_action": r.prochaine_action,
                    "date_relance": str(r.date_relance) if r.date_relance else None,
                    "tache_relance": r.tache_relance, "montant_potentiel": float(r.montant_potentiel or 0),
                    "commercial": r.commercial, "tournee": r.tournee, "tache": r.tache_de_travail}
        elif d is None or d >= aujourd_hui:
            # La plus proche des visites planifiées (le tri est décroissant : on garde la dernière vue).
            out[r.client]["prochaine"] = {"name": r.name, "date": str(d) if d else None, "tournee": r.tournee,
                                          "tache": r.tache_de_travail, "commercial": r.commercial}
    return dict(out)


def _geo_par_client(noms: list) -> dict:
    """Secteur, gouvernorat, ville et position GPS de chaque client, lus sur son adresse principale (sinon la plus
    récente active). Les secteurs (« Secteur 1 » = Raoued-Marsa-Gammarth…) et les positions viennent du géocodage
    quotidien des adresses (tournee_optimisation) : c'est ce qui permet de composer une tournée par zone."""
    if not noms:
        return {}
    rows = frappe.db.sql(
        """select dl.link_name client, a.name adresse, a.custom_secteur secteur, a.state gouvernorat, a.city ville,
                  a.custom_latitude lat, a.custom_longitude lng, a.address_line1 ligne, a.custom_lien_google_map lien,
                  (a.name = c.customer_primary_address) principale, a.modified
           from `tabDynamic Link` dl join `tabAddress` a on a.name = dl.parent
           join `tabCustomer` c on c.name = dl.link_name
           where dl.parenttype = 'Address' and dl.link_doctype = 'Customer' and dl.link_name in %(c)s and a.disabled = 0
           order by principale desc, a.modified desc""", {"c": noms}, as_dict=True)
    out = {}
    for r in rows:
        if r.client in out:
            continue
        lat, lng = float(r.lat or 0), float(r.lng or 0)
        out[r.client] = {"adresse": r.adresse, "secteur": r.secteur or "", "gouvernorat": r.gouvernorat or "",
                         "ville": r.ville or "", "ligne": r.ligne or "", "lien": r.lien or "",
                         "lat": lat if lat and lng else None, "lng": lng if lat and lng else None}
    return out


def secteurs() -> list:
    """Les secteurs géographiques, avec leur description (« Secteur 1 : Raoued-Marsa-Gammarth-Goulette-Aouina »)."""
    if not frappe.db.table_exists("Secteur geographique"):
        return []
    rows = frappe.get_all("Secteur geographique", fields=["name", "description"], limit_page_length=0)
    cle = lambda r: (r.name == "Hors Secteur", int("".join(ch for ch in r.name if ch.isdigit()) or 0), r.name)
    return [{"nom": r.name, "description": r.description or ""} for r in sorted(rows, key=cle)]


def _charger(groupes, commercial=None, periode="12m", client=None, aujourd_hui: date | None = None) -> dict:
    aujourd_hui = aujourd_hui or _d(nowdate())
    debut = _debut_periode(periode, aujourd_hui)
    debut_hist = _d(add_months(aujourd_hui, -HISTORIQUE_MOIS))
    debut_12m = _d(add_months(aujourd_hui, -12))
    jan1 = date(aujourd_hui.year, 1, 1)

    filtres = {"disabled": 0, "customer_group": ["in", list(groupes)]}
    if client:
        filtres["name"] = client
    clients = frappe.get_all(
        "Customer", filters=filtres,
        fields=["name", "customer_name", "customer_group", "mobile_no", "custom_telephone", "territory",
                "custom_derniere_visite", "custom_autoriser_accès_fiche_client as acces_fiche",
                "custom_gere_par_partenaire as partenaire"],
        limit_page_length=0)
    if not clients:
        return {"lignes": [], "kpis": {}, "rythmes_groupe": {}}
    noms = [c.name for c in clients]

    commerciaux = {}
    for r in frappe.db.sql("""select parent, sales_person from `tabSales Team`
                              where parenttype = 'Customer' and parent in %(c)s order by idx""",
                           {"c": noms}, as_dict=True):
        commerciaux.setdefault(r.parent, r.sales_person)
    if commercial:
        if commercial == "(aucun)":
            clients = [c for c in clients if not commerciaux.get(c.name)]
        else:
            clients = [c for c in clients if commerciaux.get(c.name) == commercial]
        noms = [c.name for c in clients]
        if not noms:
            return {"lignes": [], "kpis": {}, "rythmes_groupe": {}}

    bls = frappe.db.sql(
        """select customer, posting_date, base_net_total, grand_total, is_return from `tabDelivery Note`
           where docstatus = 1 and customer in %(c)s and posting_date >= %(d)s""",
        {"c": noms, "d": debut_hist}, as_dict=True)
    premiers = {r.customer: r.premiere for r in frappe.db.sql(
        """select customer, min(posting_date) premiere from `tabDelivery Note`
           where docstatus = 1 and is_return = 0 and customer in %(c)s group by customer""",
        {"c": noms}, as_dict=True)}
    impayes = {r.customer: float(r.m) for r in frappe.db.sql(
        """select customer, sum(outstanding_amount) m from `tabSales Invoice`
           where docstatus = 1 and outstanding_amount > 0 and customer in %(c)s group by customer""",
        {"c": noms}, as_dict=True)}
    journal = frappe.db.sql(
        """select client, logging_time, ifnull(action, '') action, detail from `tabRistourne Dashboard Logging`
           where docstatus = 1 and client in %(c)s and logging_time >= %(d)s order by logging_time""",
        {"c": noms, "d": debut_12m}, as_dict=True)
    connexions_ever = {r.client: r for r in frappe.db.sql(
        """select client, max(logging_time) derniere, count(*) nb from `tabRistourne Dashboard Logging`
           where docstatus = 1 and client in %(c)s and ifnull(action, 'connexion') = 'connexion'
           group by client""", {"c": noms}, as_dict=True)}
    listes = {r.custom_client: r for r in frappe.db.sql(
        """select custom_client, count(*) nb, max(creation) derniere from `tabListe prix documents`
           where docstatus = 1 and custom_client in %(c)s and creation >= %(d)s group by custom_client""",
        {"c": noms, "d": debut_12m}, as_dict=True)}
    ristournes = {r.customer: r for r in frappe.db.sql(
        """select customer, annual_amount, valid_from, valid_to from `tabRistourne Active`
           where docstatus = 1 and customer in %(c)s and %(j)s between valid_from and valid_to""",
        {"c": noms, "j": aujourd_hui}, as_dict=True)}
    utilisees = {r.customer: float(r.m) for r in frappe.db.sql(
        """select customer, sum(applied_amount) m from `tabRistourne used`
           where docstatus = 1 and customer in %(c)s and posting_date >= %(d)s group by customer""",
        {"c": noms, "d": jan1}, as_dict=True)}
    paliers = _paliers_par_groupe(aujourd_hui)
    visites = _visites_par_client(noms, aujourd_hui)
    geo = _geo_par_client(noms)

    # Les bons de livraison, par client.
    achats = defaultdict(list)          # dates d'achat (hors retours), sur l'historique
    par_mois = defaultdict(lambda: defaultdict(float))
    ca_periode = defaultdict(float)
    nb_periode = defaultdict(int)
    ca_annee = defaultdict(float)
    derniere_bl = {}
    for b in bls:
        d = _d(b.posting_date)
        ht = float(b.base_net_total or 0)
        par_mois[b.customer][d.strftime("%Y-%m")] += ht
        if d >= jan1:
            ca_annee[b.customer] += ht
        if d >= debut:
            ca_periode[b.customer] += ht
            if not b.is_return:
                nb_periode[b.customer] += 1
        if not b.is_return:
            achats[b.customer].append(d)
            if b.customer not in derniere_bl or d > derniere_bl[b.customer]:
                derniere_bl[b.customer] = d

    # Le journal de la fiche, par client.
    conn_periode = defaultdict(int)
    conn_90 = defaultdict(int)
    actions_90 = defaultdict(lambda: defaultdict(int))
    derniere_action = {}
    il_y_a_90 = aujourd_hui - timedelta(days=90)
    for j in journal:
        d = _d(j.logging_time)
        action = j.action or "connexion"
        if action == "connexion":
            if d >= debut:
                conn_periode[j.client] += 1
            if d >= il_y_a_90:
                conn_90[j.client] += 1
        elif d >= il_y_a_90:
            actions_90[j.client][action] += 1
        if action != "connexion":
            derniere_action[j.client] = {"quand": str(j.logging_time), "action": action, "detail": j.detail}

    lignes = []
    for c in clients:
        n = c.name
        r = rythme(achats.get(n))
        dbl = derniere_bl.get(n)
        conn = connexions_ever.get(n)
        dc = _d(conn.derniere) if conn else None
        nb_bl_12m = sum(1 for d in achats.get(n, []) if d >= debut_12m)
        t = tendance(par_mois.get(n, {}), aujourd_hui)
        ra = ristournes.get(n)
        l = {
            "client": n, "nom": c.customer_name, "groupe": c.customer_group,
            "telephone": c.mobile_no or c.custom_telephone or "", "territoire": c.territory,
            "commercial": commerciaux.get(n), "partenaire": bool(c.partenaire),
            "acces_fiche": bool(c.acces_fiche),
            "ca_ht": round(ca_periode.get(n, 0.0), 3),
            "nb_bl": nb_periode.get(n, 0),
            "panier": round(ca_periode[n] / nb_periode[n], 3) if nb_periode.get(n) else None,
            "rythme": r,
            "derniere_bl": str(dbl) if dbl else None,
            "jours_depuis_bl": (aujourd_hui - dbl).days if dbl else None,
            "a_deja_achete": n in premiers,
            "premier_achat": str(premiers[n]) if n in premiers else None,
            "nb_bl_12m": nb_bl_12m,
            "tendance": t,
            "impaye": round(impayes.get(n, 0.0), 3),
            "derniere_connexion": str(conn.derniere) if conn else None,
            "jours_depuis_connexion": (aujourd_hui - dc).days if dc else None,
            "connexions": conn_periode.get(n, 0),
            "connexions_90": conn_90.get(n, 0),
            "actions_90": dict(actions_90.get(n, {})),
            "derniere_action": derniere_action.get(n),
            "listes_prix": int(listes[n].nb) if n in listes else 0,
            "derniere_liste": str(listes[n].derniere) if n in listes else None,
            "ca_ht_annee": round(ca_annee.get(n, 0.0), 3),
            "ristourne": {"acquise": round(float(ra.annual_amount or 0), 3) if ra else None,
                          "utilisee": round(utilisees.get(n, 0.0), 3),
                          "palier": palier(ca_annee.get(n, 0.0), paliers.get(c.customer_group))},
            "derniere_visite": str(c.custom_derniere_visite) if c.custom_derniere_visite else None,
            "jours_depuis_visite": (aujourd_hui - _d(c.custom_derniere_visite)).days
            if c.custom_derniere_visite else None,
            "visite": visites.get(n, {"derniere": None, "prochaine": None}),
            "geo": geo.get(n, {"adresse": None, "secteur": "", "gouvernorat": "", "ville": "", "ligne": "",
                               "lien": "", "lat": None, "lng": None}),
        }
        lignes.append(l)

    rythmes_groupe = {}
    for g in groupes:
        valeurs = [l["rythme"] for l in lignes if l["groupe"] == g and l["rythme"]]
        rythmes_groupe[g] = round(float(statistics.median(valeurs)), 1) if valeurs else None
    for l in lignes:
        l["alertes"] = alertes(l, aujourd_hui, rythmes_groupe.get(l["groupe"]))
        l["score"] = score(l["alertes"])
    lignes.sort(key=lambda l: (-l["score"], -l["ca_ht"], l["nom"] or ""))

    kpis = {}
    for g in groupes:
        gl = [l for l in lignes if l["groupe"] == g]
        if not gl:
            continue
        actifs = [l for l in gl if l["nb_bl"]]
        ca = round(sum(l["ca_ht"] for l in gl), 3)
        kpis[g] = {
            "clients": len(gl), "actifs": len(actifs), "ca_ht": ca,
            "panier": round(ca / sum(l["nb_bl"] for l in actifs), 3) if actifs else None,
            "rythme": rythmes_groupe.get(g),
            "connectes": sum(1 for l in gl if l["connexions"]),
            # « À voir » = au moins une alerte qui pèse (retard, perdu, intention, baisse, impayé) — pas deux
            # alertes mineures (jamais connecté + sans commercial), sinon tout le monde est à voir.
            "a_voir": sum(1 for l in gl if any(ALERTES[a][1] >= 2 for a in l["alertes"])),
            "impaye": round(sum(l["impaye"] for l in gl), 3),
            "silencieux": sum(1 for l in gl if "retard" in l["alertes"] or "perdu" in l["alertes"]),
        }
    return {"lignes": lignes, "kpis": kpis, "rythmes_groupe": rythmes_groupe,
            "debut": str(debut), "aujourd_hui": str(aujourd_hui)}


@frappe.whitelist()
def tableau(groupes=None, commercial=None, periode="12m") -> dict:
    """Le tableau de bord : une ligne par client B2B, les KPI par groupe, les alertes."""
    _guard()
    if isinstance(groupes, str):
        groupes = frappe.parse_json(groupes) if groupes.startswith("[") else [groupes]
    groupes = [g for g in (groupes or GROUPES_B2B) if g in GROUPES_B2B] or list(GROUPES_B2B)
    out = _charger(groupes, commercial or None, periode or "12m")
    out["groupes"] = list(GROUPES_B2B)
    out["commerciaux"] = [r.name for r in frappe.get_all("Sales Person", filters={"enabled": 1, "is_group": 0},
                                                         order_by="name", limit_page_length=0)]
    out["alertes"] = {k: {"libelle": v[0], "poids": v[1], "explication": v[2]} for k, v in ALERTES.items()}
    out["periode"] = periode or "12m"
    out["acces"] = qui_a_acces()
    out["roles"] = list(ROLES)
    out["tournees"] = tournees_ouvertes()
    out["commerciaux_employes"] = _commerciaux_employes()
    out["secteurs"] = secteurs()
    return out


def _commerciaux_employes() -> list:
    """Les employés qui font des tournées : ceux rattachés à un Sales Person, sinon tous les employés actifs."""
    rows = frappe.db.sql(
        """select e.name, e.employee_name from `tabEmployee` e
           where e.status = 'Active' and exists (select 1 from `tabSales Person` sp where sp.employee = e.name
                                                 and sp.enabled = 1)
           order by e.employee_name""", as_dict=True)
    if not rows:
        rows = frappe.get_all("Employee", filters={"status": "Active"}, fields=["name", "employee_name"],
                              order_by="employee_name", limit_page_length=0)
    return [{"employee": r.name, "nom": r.employee_name} for r in rows]


@frappe.whitelist()
def tournees_ouvertes() -> list:
    """Les tournées commerciales encore à faire (en cours, datées d'aujourd'hui ou plus tard), pour y ajouter des clients."""
    _guard()
    if not frappe.db.table_exists("Tournee Commerciale"):
        return []
    rows = frappe.db.sql(
        """select t.name, t.date_tournee, t.tache_de_travail, t.objectif, e.employee_name commercial,
                  (select count(*) from `tabVisite Commerciale` v where v.tournee = t.name and v.statut <> 'Annulée') nb
           from `tabTournee Commerciale` t left join `tabEmployee` e on e.name = t.commercial
           where t.statut = 'En cours' and (t.date_tournee is null or t.date_tournee >= %(j)s)
           order by t.date_tournee, t.name""", {"j": nowdate()}, as_dict=True)
    return [{"name": r.name, "date": str(r.date_tournee) if r.date_tournee else None, "tache": r.tache_de_travail,
             "commercial": r.commercial, "objectif": r.objectif, "nb_visites": int(r.nb or 0)} for r in rows]


@frappe.whitelist()
def planifier_visites(clients, tournee=None, commercial=None, date_tournee=None, objectif=None) -> dict:
    """Met des clients dans une tournée commerciale — existante, ou nouvelle (Tâche « Tournée commerciale » + Tournee
    Commerciale) pour un commercial et une date. Une visite « Planifiée » par client ; un client déjà dans la tournée
    n'y est pas remis. -> {tournee, tache, ajoutes, deja}."""
    from customization_app import tournee as T

    _guard()
    clients = frappe.parse_json(clients) if isinstance(clients, str) else list(clients or [])
    clients = [c for c in clients if c and frappe.db.exists("Customer", c)]
    if not clients:
        frappe.throw(_("Aucun client à planifier."))
    if tournee:
        doc = frappe.get_doc("Tournee Commerciale", tournee)
        if doc.statut != "En cours":
            frappe.throw(_("La tournée {0} est {1}.").format(tournee, doc.statut))
    else:
        if not commercial or not frappe.db.exists("Employee", commercial):
            frappe.throw(_("Choisissez le commercial de la tournée."))
        jour = getdate(date_tournee or nowdate())
        nom_commercial = frappe.db.get_value("Employee", commercial, "employee_name") or commercial
        tache = frappe.get_doc({
            "doctype": "Tache de travail",
            "subject": _("Tournée commerciale — {0} client(s) B2B").format(len(clients)),
            "custom_type_dintervention": T.INTERVENTION_TOURNEE,
            "custom_choix_du_staff": commercial,
            "custom_employé": nom_commercial,
            "starts_on": "%s 09:00:00" % jour,
            "ends_on": "%s 17:00:00" % jour,
            "status": "Open",
            "description": objectif or "",
        })
        tache.flags.ignore_permissions = True
        tache.insert(ignore_permissions=True, ignore_mandatory=True)
        doc = frappe.get_doc({"doctype": "Tournee Commerciale", "tache_de_travail": tache.name,
                              "commercial": commercial, "date_tournee": jour, "objectif": objectif or ""})
        doc.flags.ignore_permissions = True
        doc.insert(ignore_permissions=True)
    deja = set(frappe.get_all("Visite Commerciale", filters={"tournee": doc.name, "statut": ["!=", "Annulée"]},
                              pluck="client"))
    ajoutes = []
    for c in clients:
        if c in deja:
            continue
        v = frappe.get_doc({"doctype": "Visite Commerciale", "tournee": doc.name, "client": c, "statut": "Planifiée",
                            "commercial": doc.commercial})
        v.flags.ignore_permissions = True
        v.insert(ignore_permissions=True)
        ajoutes.append(c)
    doc.reload()
    doc.nb_visites_prevues = frappe.db.count("Visite Commerciale", {"tournee": doc.name, "statut": ["!=", "Annulée"]})
    doc.flags.ignore_permissions = True
    doc.save(ignore_permissions=True)
    # La tâche du calendrier suit le nombre de clients ; son titre (ce que le calendrier affiche) est recomposé par
    # before_save_tache_de_travail quand il est vide.
    if ajoutes and doc.tache_de_travail:
        tache = frappe.get_doc("Tache de travail", doc.tache_de_travail)
        prefixe = _("Tournée commerciale —")
        if (tache.subject or "").startswith(prefixe):
            tache.subject = _("Tournée commerciale — {0} client(s) B2B").format(doc.nb_visites_prevues)
            tache.titre = ""
            tache.flags.ignore_permissions = True
            tache.save(ignore_permissions=True)
    return {"tournee": doc.name, "tache": doc.tache_de_travail, "date": str(doc.date_tournee),
            "ajoutes": ajoutes, "deja": sorted(deja & set(clients))}


def qui_a_acces() -> list:
    """Les comptes actifs qui voient ce tableau (rôle Responsable B2B ou System Manager), pour l'encart d'aide."""
    rows = frappe.db.sql(
        """select u.name, u.full_name, group_concat(distinct h.role order by h.role separator ', ') roles
           from `tabUser` u join `tabHas Role` h on h.parent = u.name and h.parenttype = 'User'
           where u.enabled = 1 and u.user_type = 'System User' and u.name not in ('Administrator', 'Guest')
             and h.role in %(r)s group by u.name order by u.full_name""", {"r": list(ROLES)}, as_dict=True)
    return [{"user": r.name, "nom": r.full_name or r.name, "roles": r.roles} for r in rows]


@frappe.whitelist()
def fiche(client: str) -> dict:
    """Le détail d'un client : mois par mois, familles achetées, dernières livraisons, actions sur sa fiche."""
    _guard()
    groupe = frappe.db.get_value("Customer", client, "customer_group")
    if groupe not in GROUPES_B2B:
        frappe.throw(_("{0} n'est pas un client B2B (groupe {1}).").format(client, groupe))
    aujourd_hui = _d(nowdate())
    base = _charger([groupe], client=client, aujourd_hui=aujourd_hui)
    ligne = base["lignes"][0] if base["lignes"] else None
    debut_12m = _d(add_months(aujourd_hui, -12))
    mois = mois_glissants(aujourd_hui, 12)

    serie = {m: {"mois": m, "ca_ht": 0.0, "nb_bl": 0, "connexions": 0, "actions": 0} for m in mois}
    for b in frappe.get_all("Delivery Note", filters={"docstatus": 1, "customer": client,
                                                      "posting_date": [">=", debut_12m]},
                            fields=["posting_date", "base_net_total", "is_return"], limit_page_length=0):
        m = _d(b.posting_date).strftime("%Y-%m")
        if m in serie:
            serie[m]["ca_ht"] += float(b.base_net_total or 0)
            if not b.is_return:
                serie[m]["nb_bl"] += 1
    actions = frappe.db.sql(
        """select logging_time, ifnull(action, '') action, detail from `tabRistourne Dashboard Logging`
           where docstatus = 1 and client = %(c)s order by logging_time desc limit 300""",
        {"c": client}, as_dict=True)
    for a in actions:
        m = _d(a.logging_time).strftime("%Y-%m")
        if m in serie:
            serie[m]["connexions" if (a.action or "connexion") == "connexion" else "actions"] += 1
    for s in serie.values():
        s["ca_ht"] = round(s["ca_ht"], 3)

    familles = frappe.db.sql(
        """select i.item_group famille, round(sum(i.base_net_amount), 3) ht, round(sum(i.qty), 2) qte
           from `tabDelivery Note Item` i join `tabDelivery Note` dn on dn.name = i.parent
           where dn.docstatus = 1 and dn.customer = %(c)s and dn.posting_date >= %(d)s
           group by i.item_group order by ht desc limit 8""", {"c": client, "d": debut_12m}, as_dict=True)
    articles = frappe.db.sql(
        """select i.item_code, i.item_name, round(sum(i.base_net_amount), 3) ht, round(sum(i.qty), 2) qte,
                  max(dn.posting_date) dernier
           from `tabDelivery Note Item` i join `tabDelivery Note` dn on dn.name = i.parent
           where dn.docstatus = 1 and dn.customer = %(c)s and dn.posting_date >= %(d)s
           group by i.item_code, i.item_name order by ht desc limit 10""",
        {"c": client, "d": debut_12m}, as_dict=True)
    bls = frappe.get_all("Delivery Note", filters={"docstatus": 1, "customer": client},
                         fields=["name", "posting_date", "base_net_total", "grand_total", "is_return", "status"],
                         order_by="posting_date desc, creation desc", limit_page_length=10)
    listes = frappe.get_all("Liste prix documents", filters={"docstatus": 1, "custom_client": client},
                            fields=["name", "creation", "liste_des_prix"], order_by="creation desc",
                            limit_page_length=5)
    commentaires = frappe.get_all(
        "Comment", filters={"reference_doctype": "Customer", "reference_name": client, "comment_type": "Comment"},
        fields=["creation", "comment_email", "comment_by", "content"], order_by="creation desc", limit_page_length=8)
    for c in commentaires:
        c["content"] = strip_html(c.content or "")[:300]
    visites = frappe.db.sql(
        """select v.name, v.statut, v.resultat, v.niveau_interet, v.personne_rencontree, v.resume_discussion,
                  v.prochaine_action, v.date_relance, v.montant_potentiel, v.tache_relance, v.opportunite,
                  v.tournee, t.date_tournee, t.tache_de_travail, e.employee_name commercial
           from `tabVisite Commerciale` v join `tabTournee Commerciale` t on t.name = v.tournee
           left join `tabEmployee` e on e.name = ifnull(v.commercial, t.commercial)
           where v.client = %(c)s order by t.date_tournee desc, v.creation desc limit 8""",
        {"c": client}, as_dict=True) if frappe.db.table_exists("Visite Commerciale") else []
    for v in visites:
        v["resume_discussion"] = strip_html(v.resume_discussion or "")[:400]
    return {
        "ligne": ligne,
        "visites": visites,
        "mois": [serie[m] for m in mois],
        "familles": familles,
        "articles": articles,
        "bls": bls,
        "actions": actions[:30],
        "listes": listes,
        "commentaires": commentaires,
        "alertes": {k: {"libelle": v[0], "explication": v[2]} for k, v in ALERTES.items()},
    }


# ------------------------------------------------------------------ écritures

@frappe.whitelist()
def visite_faite(client: str, note: str = None) -> dict:
    """Le commercial est passé : date de dernière visite + commentaire sur la fiche (lisible dans l'historique)."""
    _guard()
    doc = frappe.get_doc("Customer", client)
    aujourd_hui = nowdate()
    doc.db_set("custom_derniere_visite", aujourd_hui, update_modified=False)
    texte = "Visite commerciale du %s par %s" % (frappe.utils.formatdate(aujourd_hui, "dd/MM/yyyy"),
                                                 frappe.utils.get_fullname(frappe.session.user))
    note = (note or "").strip()
    if note:
        texte += " : " + frappe.utils.escape_html(note)
    doc.add_comment("Comment", texte)
    return {"derniere_visite": aujourd_hui}


@frappe.whitelist()
def rattacher_commercial(client: str, sales_person: str = None) -> dict:
    """Un seul commercial par client (100 %). Vide = aucun."""
    _guard()
    if sales_person and not frappe.db.exists("Sales Person", sales_person):
        frappe.throw(_("Commercial inconnu : {0}").format(sales_person))
    doc = frappe.get_doc("Customer", client)
    doc.set("sales_team", [])
    if sales_person:
        doc.append("sales_team", {"sales_person": sales_person, "allocated_percentage": 100})
    doc.flags.ignore_permissions = True
    doc.save()
    return {"commercial": sales_person or None}
