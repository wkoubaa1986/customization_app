"""Situation mensuelle interactive — page /app/situation-mensuelle et graphe de l'onglet Comptabilité.

MÊME LOGIQUE que le Script Report « Test » (décision utilisateur du 01/10/2026 : « garde la même
logique ») :
    bénéfice = ventes livrées (BL validés, TTC) − coût de la marchandise (Charges de Stock)
               − charges (Charges Indirectes) − TVA achat (débits des comptes TVA)
               + bénéfice initial (vues « Total » et « 2023 » seulement) + ajustements d'ouverture.
Rubriques de charges : comptes enfants ET petits-enfants du compte parent, écritures non annulées
et hors ouverture. Cas particulier repris tel quel : en vue mensuelle d'octobre 2023, la TVA achat
part du 01/08/2023.

Ce qui change : les pièces que le rapport excluait par une liste écrite dans son code sont
désormais des lignes du registre « Exclusion Situation », visibles et réversibles, et l'on peut en
omettre de nouvelles depuis l'écran — en TOTALITÉ (comme l'ancienne liste) ou en PARTIE.
"""

from __future__ import annotations

import calendar
from datetime import date

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

DOCTYPE_EXCLUSION = "Exclusion Situation"
RUBRIQUES = {                       # rubrique -> (compte parent, débits seulement ?)
    "Coût de la marchandise": ("Charges de Stock - A&S", False),
    "Charges": ("Charges Indirectes - A&S", False),
    "TVA Achat": ("TVA - A&S", True),
}
CLES = {"Coût de la marchandise": "cout", "Charges": "charges", "TVA Achat": "tva"}
BENEFICE_INITIAL = 138185.90         # repris du rapport : ajouté aux vues « Total » et « 2023 »
AJUSTEMENTS_OUVERTURE = ("ACC-JV-2024-00494", "ACC-JV-2024-00491", "ACC-JV-2024-00486", "ACC-JV-2024-00680",
                         "ACC-JV-2024-00731", "ACC-JV-2025-00089", "ACC-JV-2025-00099")
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
        "novembre", "décembre"]
MOIS_COURTS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
DEBUT = date(2023, 10, 1)            # premier mois proposé par le rapport


# ── Accès ────────────────────────────────────────────────────────────────────

ROLES_LECTURE = ("System Manager", "Accounts Manager", "Accounts User", "Banque")
ROLES_OMISSION = ("System Manager", "Banque")


def _lecture():
    if not set(ROLES_LECTURE) & set(frappe.get_roles()):
        frappe.throw(_("La situation mensuelle n'est pas ouverte à votre compte."), frappe.PermissionError)


def peut_omettre() -> bool:
    return bool(set(ROLES_OMISSION) & set(frappe.get_roles()))


def _omission():
    if not peut_omettre():
        frappe.throw(_("Omettre une dépense est réservé au rôle « Banque »."), frappe.PermissionError)


# ── Périodes (mêmes règles que le rapport) ───────────────────────────────────

def libelle_mois(d) -> str:
    d = getdate(d)
    return "%s %s" % (MOIS[d.month - 1], d.year)


def mois_disponibles() -> list[str]:
    aujourd = getdate(nowdate())
    out, d = [], DEBUT
    while d <= aujourd:
        out.append(libelle_mois(d))
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    return list(reversed(out))


def periode(mode="mois", mois=None, annee=None) -> dict:
    aujourd = getdate(nowdate())
    if mode == "mois":
        mois = mois or libelle_mois(aujourd)
        try:
            nom, an = mois.split()
            m, an = MOIS.index(nom.lower()) + 1, int(an)
        except Exception:
            frappe.throw(_("Mois invalide : {0}").format(mois))
        debut = date(an, m, 1)
        fin = aujourd if (m, an) == (aujourd.month, aujourd.year) else date(an, m, calendar.monthrange(an, m)[1])
        tva_debut = date(2023, 8, 1) if (m, an) == (10, 2023) else debut
        return {"mode": "mois", "libelle": mois, "debut": debut, "fin": fin, "tva_debut": tva_debut, "initial": 0.0}
    annee = str(annee or "Total")
    if annee == "Total":
        debut, fin, initial = date(2023, 1, 1), aujourd, BENEFICE_INITIAL
    else:
        an = int(annee)
        debut = date(an, 1, 1)
        fin = aujourd if an == aujourd.year else date(an, 12, 31)
        initial = BENEFICE_INITIAL if annee == "2023" else 0.0
    return {"mode": "annee", "libelle": annee, "debut": debut, "fin": fin, "tva_debut": debut, "initial": initial}


# ── Calcul ───────────────────────────────────────────────────────────────────

def comptes(rubrique: str) -> list[str]:
    """Enfants et petits-enfants du compte parent — la profondeur exacte du rapport."""
    parent = RUBRIQUES[rubrique][0]
    enfants = frappe.get_all("Account", filters={"parent_account": parent}, pluck="name")
    petits = frappe.get_all("Account", filters={"parent_account": ["in", enfants]}, pluck="name") if enfants else []
    return enfants + petits


def _pieces(rubrique: str, debut, fin) -> dict:
    """Contribution de chaque pièce à la rubrique sur la période : {(type, n°): montant}."""
    cpt = comptes(rubrique)
    if not cpt:
        return {}
    debit_seul = RUBRIQUES[rubrique][1]
    rows = frappe.db.sql("""select voucher_type, voucher_no, sum(debit), sum(credit) from `tabGL Entry`
                            where account in %(cpt)s and is_cancelled = 0 and is_opening = 'No'
                              and posting_date between %(d1)s and %(d2)s
                            group by voucher_type, voucher_no""", {"cpt": tuple(cpt), "d1": debut, "d2": fin})
    return {(vt, vn): flt(d) if debit_seul else flt(d) - flt(c) for vt, vn, d, c in rows}


def _part_omise(exclusion, contribution: float) -> float:
    if exclusion.mode == "Totale":
        return contribution
    signe = 1 if contribution >= 0 else -1
    return signe * min(flt(exclusion.montant_exclu), abs(contribution))


def _exclusions(rubrique: str, pieces) -> dict:
    if not pieces:
        return {}
    # Le registre est petit : on le lit en entier plutôt que de filtrer sur des milliers de pièces (vue « Total »).
    rows = frappe.get_all(DOCTYPE_EXCLUSION, filters={"rubrique": rubrique},
                          fields=["name", "voucher_type", "voucher_no", "mode", "montant_exclu", "motif", "owner", "creation"])
    return {(r.voucher_type, r.voucher_no): r for r in rows if (r.voucher_type, r.voucher_no) in pieces}


def rubrique_totale(rubrique: str, debut, fin) -> dict:
    pieces = _pieces(rubrique, debut, fin)
    exclusions = _exclusions(rubrique, pieces)
    brut = sum(pieces.values())
    omis = sum(_part_omise(e, pieces[k]) for k, e in exclusions.items())
    return {"brut": brut, "omis": omis, "net": brut - omis, "nb_omis": len(exclusions)}


def ventes(debut, fin) -> float:
    return flt(frappe.db.sql("""select sum(grand_total) from `tabDelivery Note`
                                where docstatus = 1 and posting_date between %s and %s""", (debut, fin))[0][0])


def ajustements(debut, fin) -> float:
    return flt(frappe.db.sql("""select sum(total_debit) from `tabJournal Entry` where posting_date between %s and %s
                                and name in %s and is_opening = 'Yes'""", (debut, fin, AJUSTEMENTS_OUVERTURE))[0][0])


def calculer(p: dict) -> dict:
    r = {"ventes": ventes(p["debut"], p["fin"])}
    for rub, cle in CLES.items():
        r[cle] = rubrique_totale(rub, p["tva_debut"] if cle == "tva" else p["debut"], p["fin"])
    r["initial"] = p["initial"]
    r["ajustement"] = ajustements(p["debut"], p["fin"])
    r["benefice"] = r["ventes"] - r["cout"]["net"] - r["charges"]["net"] - r["tva"]["net"] + r["initial"] + r["ajustement"]
    r.update(ratios(r["ventes"], r["cout"]["net"], r["benefice"]))
    return r


def ratios(ventes, cout, benefice) -> dict:
    """Indicateurs demandés le 01/10/2026, en % des ventes livrées TTC :
    - marge = bénéfice / ventes ;
    - marge brute « coût / ventes » = coût de la marchandise / ventes (définition de l'utilisateur ;
      la marge brute au sens usuel, (ventes − coût) / ventes, est son complément : renvoyée aussi)."""
    if not ventes:
        return {"marge": None, "marge_brute": None, "marge_brute_usuelle": None}
    return {"marge": 100.0 * benefice / ventes, "marge_brute": 100.0 * cout / ventes,
            "marge_brute_usuelle": 100.0 * (ventes - cout) / ventes}


def serie(fin_mois: str | None = None, nb: int = 12) -> list[dict]:
    """Les `nb` mois jusqu'à `fin_mois` (inclus), en vue mensuelle."""
    p = periode("mois", fin_mois)
    d, out = p["debut"], []
    for _i in range(nb):
        if d < DEBUT:
            break
        lib = libelle_mois(d)
        c = calculer(periode("mois", lib))
        out.append({"mois": lib, "court": "%s %s" % (MOIS_COURTS[d.month - 1], str(d.year)[2:]),
                    "ventes": c["ventes"], "cout": c["cout"]["net"], "charges": c["charges"]["net"],
                    "tva": c["tva"]["net"], "benefice": c["benefice"], "marge": c["marge"], "marge_brute": c["marge_brute"],
                    "omis": c["cout"]["omis"] + c["charges"]["omis"] + c["tva"]["omis"]})
        d = date(d.year - (d.month == 1), (d.month - 2) % 12 + 1, 1)
    return list(reversed(out))


# ── API de la page ───────────────────────────────────────────────────────────

@frappe.whitelist()
def get_situation(mode="mois", mois=None, annee=None):
    _lecture()
    p = periode(mode, mois, annee)
    c = calculer(p)
    annees = ["Total"] + [str(a) for a in range(getdate(nowdate()).year, 2022, -1)]
    return {"periode": {k: str(v) if isinstance(v, date) else v for k, v in p.items()}, "calcul": c,
            "serie": serie(p["libelle"] if mode == "mois" else None),
            "options": {"mois": mois_disponibles(), "annees": annees}, "peut_omettre": peut_omettre()}


# Libellé lisible d'une pièce, par type : premières colonnes non vides, dans l'ordre. Chaque colonne est
# vérifiée à l'exécution (has_column) : le Bon de livraison n'a pas de « remarks » — une liste figée a
# fait planter le détail « Coût de la marchandise » le 01/10/2026.
LIBELLES = {
    "Journal Entry": ("user_remark", "cheque_no", "title"),
    "Purchase Invoice": ("supplier_name", "bill_no", "remarks"),
    "Payment Entry": ("party_name", "reference_no", "remarks"),
    "Sales Invoice": ("customer_name", "remarks"),
    "POS Invoice": ("customer_name", "remarks"),
    "Delivery Note": ("customer_name", "title"),
    "Stock Entry": ("stock_entry_type", "remarks", "purpose"),
    "Purchase Receipt": ("supplier_name", "remarks"),
    "Stock Reconciliation": ("purpose",),
}


def _libelles_pieces(cles: list[tuple]) -> dict:
    """Libellé lisible de chaque pièce : remarque de l'écriture, fournisseur, client, type de mouvement…"""
    out, par_type = {}, {}
    for vt, vn in cles:
        par_type.setdefault(vt, []).append(vn)
    for vt, noms in par_type.items():
        colonnes = [c for c in LIBELLES.get(vt, ("title",)) if frappe.db.has_column(vt, c)]
        if not colonnes:
            continue
        for r in frappe.get_all(vt, filters={"name": ["in", noms]}, fields=["name"] + colonnes):
            texte = next((str(r.get(c)).strip() for c in colonnes if r.get(c) and str(r.get(c)).strip()), "")
            out[(vt, r.name)] = texte.split("\n")[0][:140]
    return out


LIGNES_PAR_COMPTE = 200          # au-delà : « … et N autres », la recherche (côté serveur) retrouve le reste


def _correspond(ligne: dict, q: str) -> bool:
    return not q or q in " ".join([ligne["voucher_no"], ligne.get("libelle") or "", ligne["date"]]).lower()


def _tronquer(lignes: list[dict], q: str) -> tuple[list[dict], dict]:
    """Les plus grosses pièces d'abord ; le reste est résumé (nombre, montant) — un compte de coût
    sur la vue « Total » dépasse 10 000 pièces, trop pour un téléphone."""
    gardees = [l for l in lignes if _correspond(l, q)]
    gardees.sort(key=lambda x: -abs(x["montant"]))
    reste = gardees[LIGNES_PAR_COMPTE:]
    return gardees[:LIGNES_PAR_COMPTE], {"n": len(reste), "montant": sum(l["montant"] for l in reste)}


@frappe.whitelist()
def get_detail(rubrique, mode="mois", mois=None, annee=None, recherche=None):
    """Ce qui a été compté dans une rubrique : par compte, puis pièce par pièce, avec les omissions.
    Les totaux portent sur TOUTES les pièces ; la liste affichée est limitée et filtrable."""
    _lecture()
    p = periode(mode, mois, annee)
    q = (recherche or "").strip().lower()
    if rubrique == "Ventes":
        bls = frappe.db.sql("""select name, customer_name, posting_date, grand_total from `tabDelivery Note`
                               where docstatus = 1 and posting_date between %s and %s""", (p["debut"], p["fin"]), as_dict=True)
        lignes = [{"voucher_type": "Delivery Note", "voucher_no": b.name, "date": str(b.posting_date),
                   "libelle": b.customer_name, "montant": flt(b.grand_total), "contribution": flt(b.grand_total),
                   "omission": None} for b in bls]
        total = sum(l["montant"] for l in lignes)
        visibles, reste = _tronquer(lignes, q)
        return {"rubrique": rubrique, "total": total, "omis": 0, "net": total, "peut_omettre": False,
                "comptes": [{"compte": "Bons de livraison validés (TTC)", "total": total, "omis": 0, "nb": len(lignes),
                             "pieces": visibles, "reste": reste}]}
    if rubrique not in RUBRIQUES:
        frappe.throw(_("Rubrique inconnue : {0}").format(rubrique))
    debut = p["tva_debut"] if rubrique == "TVA Achat" else p["debut"]
    cpt = comptes(rubrique)
    debit_seul = RUBRIQUES[rubrique][1]
    rows = frappe.db.sql("""select account, voucher_type, voucher_no, min(posting_date), sum(debit), sum(credit)
                            from `tabGL Entry` where account in %(cpt)s and is_cancelled = 0 and is_opening = 'No'
                              and posting_date between %(d1)s and %(d2)s
                            group by account, voucher_type, voucher_no""", {"cpt": tuple(cpt), "d1": debut, "d2": p["fin"]}) if cpt else []
    pieces = _pieces(rubrique, debut, p["fin"])
    exclusions = _exclusions(rubrique, pieces)
    par_compte = {}
    for compte, vt, vn, d, deb, cre in rows:
        montant = flt(deb) if debit_seul else flt(deb) - flt(cre)
        if montant:
            par_compte.setdefault(compte, []).append({"voucher_type": vt, "voucher_no": vn, "date": str(d), "montant": montant,
                                                      "contribution": pieces.get((vt, vn), montant)})
    # Libellés : seulement pour les pièces qui seront montrées (ou cherchées) — pas 10 000 lectures.
    a_lire = set()
    for lignes in par_compte.values():
        lignes.sort(key=lambda x: -abs(x["montant"]))
        a_lire.update((l["voucher_type"], l["voucher_no"]) for l in (lignes if q else lignes[:LIGNES_PAR_COMPTE]))
    libelles = _libelles_pieces(list(a_lire))
    sortie = []
    for compte, lignes in par_compte.items():
        omis = 0.0
        for l in lignes:
            l["libelle"] = libelles.get((l["voucher_type"], l["voucher_no"]), "")
            e = exclusions.get((l["voucher_type"], l["voucher_no"]))
            l["omission"] = {"name": e.name, "mode": e.mode, "montant": _part_omise(e, pieces[(l["voucher_type"], l["voucher_no"])]),
                             "motif": e.motif, "par": e.owner} if e else None
            if e:
                omis += _part_omise(e, l["montant"])
        visibles, reste = _tronquer(lignes, q)
        if q and not visibles:
            continue
        sortie.append({"compte": compte.replace(" - A&S", ""), "total": sum(l["montant"] for l in lignes), "omis": omis,
                       "nb": len(lignes), "pieces": visibles, "reste": reste})
    sortie.sort(key=lambda x: -abs(x["total"]))
    t = rubrique_totale(rubrique, debut, p["fin"])
    return {"rubrique": rubrique, "total": t["brut"], "omis": t["omis"], "net": t["net"], "comptes": sortie,
            "peut_omettre": peut_omettre()}


@frappe.whitelist()
def omettre(rubrique, voucher_type, voucher_no, mode="Totale", montant=None, motif=None):
    """Omet une pièce de la rubrique, en totalité ou en partie (motif obligatoire). Rejouable : met à jour."""
    _omission()
    if rubrique not in RUBRIQUES:
        frappe.throw(_("Rubrique inconnue : {0}"). format(rubrique))
    if not (motif or "").strip():
        frappe.throw(_("Le motif est obligatoire."))
    cpt = comptes(rubrique)
    debit_seul = RUBRIQUES[rubrique][1]
    r = frappe.db.sql("""select min(posting_date), sum(debit), sum(credit) from `tabGL Entry`
                         where account in %s and voucher_type = %s and voucher_no = %s and is_cancelled = 0 and is_opening = 'No'""",
                      (tuple(cpt), voucher_type, voucher_no))[0]
    if r[0] is None:
        frappe.throw(_("La pièce {0} ne compte pas dans la rubrique {1}.").format(voucher_no, rubrique))
    contribution = flt(r[1]) if debit_seul else flt(r[1]) - flt(r[2])
    if mode == "Partielle":
        montant = flt(montant)
        if montant <= 0 or montant > abs(contribution) + 0.0005:
            frappe.throw(_("Montant à omettre entre 0 et {0}.").format(frappe.format(abs(contribution), {"fieldtype": "Currency"})))
    else:
        mode, montant = "Totale", abs(contribution)
    nom = frappe.db.get_value(DOCTYPE_EXCLUSION, {"rubrique": rubrique, "voucher_type": voucher_type, "voucher_no": voucher_no})
    doc = frappe.get_doc(DOCTYPE_EXCLUSION, nom) if nom else frappe.new_doc(DOCTYPE_EXCLUSION)
    doc.update({"rubrique": rubrique, "voucher_type": voucher_type, "voucher_no": voucher_no, "mode": mode,
                "montant_exclu": montant, "motif": motif.strip(), "date_piece": r[0], "montant_piece": contribution})
    doc.save()
    return {"name": doc.name}


@frappe.whitelist()
def retablir(name):
    """La pièce compte de nouveau en entier (l'omission est supprimée, la suppression reste tracée)."""
    _omission()
    frappe.delete_doc(DOCTYPE_EXCLUSION, name)
    return {"ok": True}


@frappe.whitelist()
def get_omissions(mode="mois", mois=None, annee=None):
    _lecture()
    p = periode(mode, mois, annee)
    rows = frappe.get_all(DOCTYPE_EXCLUSION, filters={"date_piece": ["between", [p["tva_debut"], p["fin"]]]},
                          fields=["name", "rubrique", "voucher_type", "voucher_no", "mode", "montant_exclu", "montant_piece",
                                  "motif", "owner", "date_piece"], order_by="date_piece desc")
    noms = dict(frappe.get_all("User", filters={"name": ["in", list({r.owner for r in rows})]},
                               fields=["name", "full_name"], as_list=True)) if rows else {}
    for r in rows:
        r["par"] = noms.get(r.owner) or r.owner
    return {"omissions": rows, "peut_omettre": peut_omettre()}


GRAPHE_ONGLET = "Situation Mensuelle 12 mois"


@frappe.whitelist()
def periode_du_graphe():
    """La période choisie dans l'entonnoir du graphe de l'onglet (filtres enregistrés par Frappe
    pour CET utilisateur), traduite pour la page : un clic sur le graphe ouvre la même période."""
    _lecture()
    cfg = frappe.parse_json(frappe.db.get_value("Dashboard Settings", frappe.session.user, "chart_config") or "{}") or {}
    f = frappe.parse_json((cfg.get(GRAPHE_ONGLET) or {}).get("filters") or "{}") or {}
    vue = f.get("vue")
    if vue == "Un mois" and f.get("mois"):
        return {"mode": "mois", "mois": f["mois"]}
    if vue == "Une année (mois par mois)" and f.get("annee"):
        return {"mode": "annee", "annee": str(f["annee"])}
    if vue == "Par année":
        return {"mode": "annee", "annee": str(getdate(nowdate()).year)}
    if vue == "Total":
        return {"mode": "annee", "annee": "Total"}
    return {"mode": "mois"}


INDICATEURS = ("ventes", "cout", "charges", "tva", "benefice", "marge", "marge_brute")


def _valeurs(c: dict) -> dict:
    return {"ventes": c["ventes"], "cout": c["cout"]["net"], "charges": c["charges"]["net"], "tva": c["tva"]["net"],
            "benefice": c["benefice"], "marge": c["marge"], "marge_brute": c["marge_brute"],
            "marge_brute_usuelle": c["marge_brute_usuelle"]}


@frappe.whitelist()
def get_comparaison():
    """Comparer les années : pour chaque année, les 12 mois de chaque indicateur (None = mois hors
    période : avant octobre 2023 ou à venir) et les totaux de l'année (même logique que la vue
    « Année » : 2023 comprend le bénéfice initial ; l'année en cours s'arrête à aujourd'hui)."""
    _lecture()
    aujourd = getdate(nowdate())
    annees = []
    for an in range(DEBUT.year, aujourd.year + 1):
        mois = []
        for m in range(1, 13):
            d = date(an, m, 1)
            if d < DEBUT or d > aujourd:
                mois.append(None)
                continue
            mois.append(_valeurs(calculer(periode("mois", libelle_mois(d)))))
        p = periode("annee", annee=str(an))
        c = calculer(p)
        annees.append({"annee": str(an), "mois": mois, "total": _valeurs(c), "initial": c["initial"],
                       "debut": str(max(p["debut"], DEBUT)), "fin": str(p["fin"]),
                       "partielle": an == aujourd.year or an == DEBUT.year})
    return {"annees": annees, "mois": MOIS_COURTS}
