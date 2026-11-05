"""Pertes et Profits — graphique « Pertes et Profits » de l'onglet Comptabilité et sa page de détail (05/11/2026).

Le graphique (Dashboard Chart « Rapport Bénéfice comptable ») lit le Script Report « Benéfice société », écrit en
base. Son calcul vit désormais ICI ; le script du rapport ne fait plus qu'appeler `rapport` (patch
pertes_profits_rapport — le script d'origine reste dans l'historique du rapport). Même logique qu'avant :
    ventes H.T  = − solde des comptes Revenus,
    stock livré = solde des comptes Charges de Stock,
    dépenses    = solde des comptes Charges Indirectes,
    bénéfice    = ventes − stock − dépenses,
sur les comptes ENFANTS et PETITS-ENFANTS de chaque parent, hors les trois comptes que le rapport ignorait, écritures
hors ouverture. Mêmes périodes que la situation mensuelle (`situation_mensuelle.periode`).

Ce qui change (demande utilisateur du 05/11/2026) : on choisit ce qui COMPTE ou non dans Pertes et Profits, pièce par
pièce, depuis la page /app/pertes-et-profits — omettre en tout ou en partie avec un motif, rétablir. Les omissions
vivent dans le registre « Exclusion Situation » avec l'écran « Pertes et Profits » (`portee`) : elles sont
INDÉPENDANTES de celles de la situation mensuelle (chaque écran ne voit que les siennes). Omissibles : les ventes,
le stock livré et les dépenses.
"""
from __future__ import annotations

from datetime import date

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

from customization_app import situation_mensuelle as S

RAPPORT = "Benéfice société"
GRAPHE = "Rapport Bénéfice comptable"
PARENTS = {"ventes": "Revenus - A&S", "stock": "Charges de Stock - A&S", "depenses": "Charges Indirectes - A&S"}
#: Comptes que le rapport d'origine n'a jamais comptés (TVA collectée, dépenses non déclarées, pertes de non-paiement).
COMPTES_IGNORES = ("TVA collectee - A&S", "Dépenses non déclarées - A&S", "Perte de non paiement - A&S")
#: Rubrique du registre d'exclusions qui porte les omissions de chaque bloc.
RUBRIQUE_DE = {"ventes": "Ventes H.T", "stock": "Coût de la marchandise", "depenses": "Charges"}
TITRES = {"ventes": "Total Vente H.T", "stock": "Total Stock Délivré H.T", "depenses": "Total Dépenses Comptabilisées"}
#: Les revenus sont créditeurs : on les montre en positif (signe × solde).
SIGNE = {"ventes": -1, "stock": 1, "depenses": 1}
PRECISION = 3
COULEURS = ["#343deb", "#eba234", "#eb4034"]


# ── Règles pures ─────────────────────────────────────────────────────────────

def _r(x) -> float:
    """Arrondi au millime sans dépendre du site (`flt(x, 3)` rend 0 hors site, cf. tests purs). Pure."""
    return round(float(x or 0), PRECISION) + 0.0


def comptes_retenus(enfants: list[str], petits_enfants: dict) -> list[str]:
    """Les comptes comptés sous un parent : chaque enfant non ignoré, et ses propres enfants non ignorés (un
    petit-enfant d'un enfant ignoré ne compte pas — c'était la règle du rapport). Pure."""
    out = []
    for e in enfants:
        if e in COMPTES_IGNORES:
            continue
        out.append(e)
        out += [p for p in petits_enfants.get(e, []) if p not in COMPTES_IGNORES]
    return out


def omis(pieces: dict, exclusions: dict) -> float:
    """Ce que les omissions retirent d'un bloc : {(type, n°): solde} × {(type, n°): exclusion}, en solde. Pure."""
    return sum(S._part_omise(e, pieces[k]) for k, e in exclusions.items() if k in pieces)


def resultat(blocs: dict) -> dict:
    """Les chiffres du graphique depuis {bloc: (solde brut, solde omis)}. Montants montrés : ventes en positif. Pure."""
    out = {}
    for bloc, (brut, om) in blocs.items():
        out[bloc] = _r(SIGNE[bloc] * (brut - om))
        out[bloc + "_brut"] = _r(SIGNE[bloc] * brut)
        out[bloc + "_omis"] = _r(SIGNE[bloc] * om)
    out["benefice"] = _r(out["ventes"] - out["stock"] - out["depenses"])
    return out


def sortie_rapport(r: dict, titre: str, nb_omis: int) -> dict:
    """Colonnes, lignes et graphique du Script Report, au format d'origine (mêmes libellés, mêmes couleurs). Pure."""
    columns = [{"label": "Type de Compte", "fieldname": "Type", "fieldtype": "Data"},
               {"label": "Total", "fieldname": "Total", "fieldtype": "Currency"}]
    data = [{"Type": TITRES[b], "Total": r[b]} for b in ("ventes", "stock", "depenses")]
    data.append({"Type": "Bénéfice / Perte", "Total": r["benefice"]})
    pl = "s" if nb_omis > 1 else ""
    if nb_omis:
        data.append({"Type": "Pièce%s omise%s (%d) : ventes %s, stock %s, dépenses %s" % (
            pl, pl, nb_omis, r["ventes_omis"], r["stock_omis"], r["depenses_omis"]), "Total": None})
    gain = r["benefice"]
    datasets = [{"values": [r[b]], "name": TITRES[b]} for b in ("ventes", "stock", "depenses")]
    datasets.append({"values": [gain], "name": "Bénéfice" if gain >= 0 else "Perte"})
    if nb_omis:
        titre = "%s — %d pièce%s omise%s" % (titre, nb_omis, pl, pl)
    chart = {"data": {"labels": [""], "datasets": datasets}, "type": "bar", "title": titre,
             "colors": COULEURS + (["#34eb65"] if gain >= 0 else ["#eb4034"])}
    return {"columns": columns, "data": data, "chart": chart}


def periode_des_filtres(f: dict) -> dict:
    """La période de la page depuis les filtres du graphique (Mensuel, mois, annee). Pure."""
    f = f or {}
    if cint(f.get("Mensuel")) == 1 and f.get("mois"):
        return {"mode": "mois", "mois": f["mois"]}
    if f.get("annee"):
        return {"mode": "annee", "annee": str(f["annee"])}
    return {"mode": "mois"}


# ── Lecture ──────────────────────────────────────────────────────────────────

def _acces():
    if not frappe.get_doc("Report", RAPPORT).is_permitted():
        frappe.throw(_("Pertes et Profits n'est pas ouvert à votre compte."), frappe.PermissionError)


def comptes(bloc: str) -> list[str]:
    parent = PARENTS[bloc]
    enfants = frappe.get_all("Account", filters={"parent_account": parent}, pluck="name")
    petits = {}
    for a in frappe.get_all("Account", filters={"parent_account": ["in", enfants]}, fields=["name", "parent_account"]) if enfants else []:
        petits.setdefault(a.parent_account, []).append(a.name)
    return comptes_retenus(enfants, petits)


def _pieces(cpt: list[str], debut, fin) -> dict:
    if not cpt:
        return {}
    rows = frappe.db.sql("""select voucher_type, voucher_no, sum(debit), sum(credit) from `tabGL Entry`
                            where account in %(cpt)s and is_cancelled = 0 and is_opening = 'No'
                              and posting_date between %(d1)s and %(d2)s
                            group by voucher_type, voucher_no""", {"cpt": tuple(cpt), "d1": debut, "d2": fin})
    return {(vt, vn): flt(d) - flt(c) for vt, vn, d, c in rows}


def _exclusions(bloc: str, pieces: dict) -> dict:
    return S._exclusions(RUBRIQUE_DE[bloc], pieces, vue=S.VUE_PNL)


def calculer(debut, fin) -> dict:
    blocs, nb = {}, 0
    for bloc in PARENTS:
        pieces = _pieces(comptes(bloc), debut, fin)
        exclusions = _exclusions(bloc, pieces)
        nb += len(exclusions)
        blocs[bloc] = (sum(pieces.values()), omis(pieces, exclusions))
    r = resultat(blocs)
    r["nb_omis"] = nb
    return r


def _periode(mode, mois, annee) -> dict:
    return S.periode("mois", mois) if mode == "mois" else S.periode("annee", annee=annee)


@frappe.whitelist()
def rapport(Mensuel=None, mois=None, annee=None) -> dict:
    """Ce que rend le Script Report « Benéfice société » (filtres du graphique : Mensuel, mois, annee)."""
    _acces()
    p = S.periode("mois", mois) if cint(Mensuel) == 1 else S.periode("annee", annee=annee)
    r = calculer(p["debut"], p["fin"])
    return sortie_rapport(r, p["libelle"], r["nb_omis"])


# ── La page /app/pertes-et-profits ───────────────────────────────────────────

@frappe.whitelist()
def get_pnl(mode="mois", mois=None, annee=None) -> dict:
    _acces()
    p = _periode(mode, mois, annee)
    annees = ["Total"] + [str(a) for a in range(getdate(nowdate()).year, 2022, -1)]
    return {"periode": {k: str(v) if isinstance(v, date) else v for k, v in p.items()}, "calcul": calculer(p["debut"], p["fin"]),
            "options": {"mois": S.mois_disponibles(), "annees": annees}, "peut_omettre": S.peut_omettre()}


@frappe.whitelist()
def periode_du_graphe() -> dict:
    """La période choisie dans l'entonnoir du graphique « Pertes et Profits » pour CET utilisateur (sinon celle du
    graphique lui-même) : la page s'ouvre sur ce qu'on regardait."""
    _acces()
    cfg = frappe.parse_json(frappe.db.get_value("Dashboard Settings", frappe.session.user, "chart_config") or "{}") or {}
    f = frappe.parse_json((cfg.get(GRAPHE) or {}).get("filters") or "{}") \
        or frappe.parse_json(frappe.db.get_value("Dashboard Chart", GRAPHE, "filters_json") or "{}")
    return periode_des_filtres(f)


@frappe.whitelist()
def get_detail(bloc, mode="mois", mois=None, annee=None, recherche=None) -> dict:
    """Ce qui a été compté dans un bloc : par compte, puis pièce par pièce, avec les omissions de Pertes et Profits."""
    _acces()
    if bloc not in PARENTS:
        frappe.throw(_("Bloc inconnu : {0}").format(bloc))
    p = _periode(mode, mois, annee)
    q = (recherche or "").strip().lower()
    cpt, signe = comptes(bloc), SIGNE[bloc]
    rows = frappe.db.sql("""select account, voucher_type, voucher_no, min(posting_date), sum(debit), sum(credit)
                            from `tabGL Entry` where account in %(cpt)s and is_cancelled = 0 and is_opening = 'No'
                              and posting_date between %(d1)s and %(d2)s
                            group by account, voucher_type, voucher_no""", {"cpt": tuple(cpt), "d1": p["debut"], "d2": p["fin"]}) if cpt else []
    pieces = _pieces(cpt, p["debut"], p["fin"])
    exclusions = _exclusions(bloc, pieces)
    par_compte = {}
    for compte, vt, vn, d, deb, cre in rows:
        montant = signe * (flt(deb) - flt(cre))
        if montant:
            par_compte.setdefault(compte, []).append({"voucher_type": vt, "voucher_no": vn, "date": str(d), "montant": montant,
                                                      "contribution": signe * pieces.get((vt, vn), 0)})
    a_lire = set()
    for lignes in par_compte.values():
        lignes.sort(key=lambda x: -abs(x["montant"]))
        a_lire.update((l["voucher_type"], l["voucher_no"]) for l in (lignes if q else lignes[:S.LIGNES_PAR_COMPTE]))
    libelles = S._libelles_pieces(list(a_lire))
    sortie = []
    for compte, lignes in par_compte.items():
        om = 0.0
        for l in lignes:
            l["libelle"] = libelles.get((l["voucher_type"], l["voucher_no"]), "")
            e = exclusions.get((l["voucher_type"], l["voucher_no"]))
            l["omission"] = {"name": e.name, "mode": e.mode, "montant": signe * S._part_omise(e, pieces[(l["voucher_type"], l["voucher_no"])]),
                             "motif": e.motif, "par": e.owner} if e else None
            if e:
                om += signe * S._part_omise(e, signe * l["montant"])
        visibles, reste = S._tronquer(lignes, q)
        if q and not visibles:
            continue
        sortie.append({"compte": compte.replace(" - A&S", ""), "total": sum(l["montant"] for l in lignes), "omis": om,
                       "nb": len(lignes), "pieces": visibles, "reste": reste})
    sortie.sort(key=lambda x: -abs(x["total"]))
    brut = signe * sum(pieces.values())
    total_omis = signe * omis(pieces, exclusions)
    return {"bloc": bloc, "titre": TITRES[bloc], "total": brut, "omis": total_omis, "net": brut - total_omis,
            "comptes": sortie, "peut_omettre": S.peut_omettre()}


@frappe.whitelist()
def omettre(bloc, voucher_type, voucher_no, mode="Totale", montant=None, motif=None) -> dict:
    """Omet une pièce de Pertes et Profits, en totalité ou en partie (motif obligatoire). Rejouable : met à jour.
    La situation mensuelle n'en est pas touchée."""
    S._omission()
    if bloc not in PARENTS:
        frappe.throw(_("Bloc inconnu : {0}").format(bloc))
    if not (motif or "").strip():
        frappe.throw(_("Le motif est obligatoire."))
    r = frappe.db.sql("""select min(posting_date), sum(debit), sum(credit) from `tabGL Entry`
                         where account in %s and voucher_type = %s and voucher_no = %s and is_cancelled = 0 and is_opening = 'No'""",
                      (tuple(comptes(bloc)), voucher_type, voucher_no))[0]
    if r[0] is None:
        frappe.throw(_("La pièce {0} ne compte pas dans « {1} ».").format(voucher_no, TITRES[bloc]))
    solde = flt(r[1]) - flt(r[2])
    if mode == "Partielle":
        montant = flt(montant)
        if montant <= 0 or montant > abs(solde) + 0.0005:
            frappe.throw(_("Montant à omettre entre 0 et {0}.").format(frappe.format(abs(solde), {"fieldtype": "Currency"})))
    else:
        mode, montant = "Totale", abs(solde)
    rubrique = RUBRIQUE_DE[bloc]
    nom = frappe.db.get_value(S.DOCTYPE_EXCLUSION, {"rubrique": rubrique, "voucher_type": voucher_type, "voucher_no": voucher_no,
                                                    "portee": S.PORTEE_PNL})
    doc = frappe.get_doc(S.DOCTYPE_EXCLUSION, nom) if nom else frappe.new_doc(S.DOCTYPE_EXCLUSION)
    doc.update({"rubrique": rubrique, "voucher_type": voucher_type, "voucher_no": voucher_no, "mode": mode,
                "montant_exclu": montant, "motif": motif.strip(), "date_piece": r[0], "montant_piece": solde,
                "portee": S.PORTEE_PNL})
    doc.save()
    return {"name": doc.name}


@frappe.whitelist()
def retablir(name) -> dict:
    """La pièce compte de nouveau en entier dans Pertes et Profits."""
    S._omission()
    if frappe.db.get_value(S.DOCTYPE_EXCLUSION, name, "portee") != S.PORTEE_PNL:
        frappe.throw(_("Cette omission n'appartient pas à Pertes et Profits."))
    frappe.delete_doc(S.DOCTYPE_EXCLUSION, name)
    return {"ok": True}


@frappe.whitelist()
def get_omissions(mode="mois", mois=None, annee=None) -> dict:
    _acces()
    p = _periode(mode, mois, annee)
    rows = frappe.get_all(S.DOCTYPE_EXCLUSION, filters={"date_piece": ["between", [p["debut"], p["fin"]]], "portee": S.PORTEE_PNL},
                          fields=["name", "rubrique", "voucher_type", "voucher_no", "mode", "montant_exclu", "montant_piece",
                                  "motif", "owner", "date_piece"], order_by="date_piece desc")
    noms = dict(frappe.get_all("User", filters={"name": ["in", list({r.owner for r in rows})]},
                               fields=["name", "full_name"], as_list=True)) if rows else {}
    bloc_de = {v: k for k, v in RUBRIQUE_DE.items()}
    for r in rows:
        r["par"] = noms.get(r.owner) or r.owner
        r["bloc"] = bloc_de.get(r.rubrique)
        r["titre"] = TITRES.get(r["bloc"], r.rubrique)
        r["montant"] = abs(flt(r.montant_piece)) if r.mode == "Totale" else flt(r.montant_exclu)
    return {"omissions": rows, "peut_omettre": S.peut_omettre()}


#: Le script du rapport en base, posé par le patch pertes_profits_rapport : il délègue tout à `rapport`.
SCRIPT_RAPPORT = '''# Calcul dans customization_app/pertes_profits.py (omissions « Pertes et Profits » comprises,
# gérées depuis la page /app/pertes-et-profits).
r = frappe.call("customization_app.pertes_profits.rapport",
                Mensuel=filters.get("Mensuel"), mois=filters.get("mois"), annee=filters.get("annee"))
data = r["columns"], r["data"], None, r["chart"], None
'''
