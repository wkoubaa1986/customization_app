"""Analyse d'une FAMILLE d'articles sur une période, en bloc, avec relecture IA (demande du 02/11/2026).

« Voir toutes les citernes par groupe, leurs mouvements, et analyser ça en bloc pour un article. » Un écart
sur un article s'explique souvent par son voisin : une citerne 4G vendue sous le code de la 3.2G, un échange
(kit E-C-3.2-4 : +1 C-4 et −1 C-3.2) mal compris, une réception saisie sur le mauvais code, une quantité ×10.
Article par article, chaque écart paraît inexpliqué ; famille par famille, ils se répondent.

LE DOSSIER (tout vient d'ERPNext) : pour chaque article de la famille, sur la période (12 mois par défaut) et
l'entrepôt (Magasin par défaut) — stock au début et à la fin, achats, ventes, transferts, rapprochements mois
par mois, les épisodes de stock NÉGATIF (vendu au-delà du stock = sortie de trop, souvent un mauvais code),
les lignes d'achat (quantité, unité, prix) et de vente (quantité, prix, client, kit), les rapprochements avec
leur écart.

LES RÈGLES (`suspicions`, pure, testée) repèrent les signatures d'ERREUR DE SAISIE :
  - stock négatif atteint ; rapprochements de sens opposé entre deux articles de la famille (substitution) ;
  - le même article corrigé plusieurs fois dans le même sens (fuite récurrente) ;
  - achat ou vente à quantité hors norme, unité d'achat ≠ pièce, prix d'achat qui saute ;
  - vente au prix d'un AUTRE article de la famille ;
  - bilan de famille : rapprochements qui se compensent entre articles = confusions de codes, pas des pertes.
Puis l'IA (aquaworld_ia → OpenAI, si clé et plafond) relit le dossier et rédige par article : diagnostic,
hypothèses classées (erreur de code, de quantité, échange, perte réelle), pièce à vérifier en premier.
Elle ne tranche pas seule : chaque hypothèse cite la pièce.

EN TÂCHE DE FOND, ENREGISTRÉE (02/11/2026, après une « requête expirée » en prod : l'appel IA dépassait le délai
de la passerelle). Chaque analyse est une fiche « Analyse Famille Stock » (famille, période, entrepôt, inventaire
d'origine, qui, quand, statut, résultat JSON, conclusion) : avec IA elle tourne dans la file « long » et l'utilisateur
est prévenu à la fin (événement temps réel + notification Desk) ; sans IA elle est immédiate. Depuis la feuille
d'inventaire, les analyses lancées sur cet inventaire se relisent à tout moment.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import add_months, cint, flt, getdate, now_datetime, nowdate

from customization_app import stock_entrepots as S

PERIODE_MOIS = 12
MAX_ARTICLES = 40
MAX_LIGNES = 400
TOL = 1e-6
FONCTIONNALITE_IA = "Enquête écarts stock"      # option existante du Select du Journal IA (aquaworld_ia)
DOCTYPE_ANALYSE = "Analyse Famille Stock"
EVENEMENT = "analyse_famille_terminee"
STATUT_EN_COURS, STATUT_TERMINEE, STATUT_ECHEC = "En cours", "Terminée", "Échec"
NATURES = {"Purchase Receipt": "achat", "Purchase Invoice": "achat", "Delivery Note": "vente",
           "Sales Invoice": "vente", "Stock Entry": "transfert", "Stock Reconciliation": "rapprochement"}


# ── Règles pures ─────────────────────────────────────────────────────────────

def nature(voucher_type: str, qte: float) -> str:
    """achat | vente | retour_vente | transfert_in | transfert_out | rapprochement | autre_in | autre_out."""
    n = NATURES.get(voucher_type, "autre")
    if n == "vente":
        return "retour_vente" if qte > 0 else "vente"
    if n in ("transfert", "autre"):
        return f"{n}_in" if qte > 0 else f"{n}_out"
    return n


def mensualiser(mouvements: list[dict]) -> dict:
    """item_code → mois (AAAA-MM) → {nature: quantité (toujours positive, sauf rapprochement signé)}. Pure.
    `mouvements` : [{item_code, date, voucher_type, qte (signée)}]."""
    out: dict = {}
    for m in mouvements:
        q = flt(m.get("qte"))
        n = nature(m.get("voucher_type") or "", q)
        mois = str(m.get("date") or "")[:7]
        d = out.setdefault(m["item_code"], {}).setdefault(mois, defaultdict(float))
        d[n] += q if n == "rapprochement" else abs(q)
    return {c: {m: dict(v) for m, v in mois.items()} for c, mois in out.items()}


def bilan_article(code: str, debut: float, mensuel: dict, fin: float) -> dict:
    """Totaux de la période pour un article et contrôle début + entrées − sorties = fin. Pure."""
    t = defaultdict(float)
    for d in (mensuel or {}).values():
        for n, q in d.items():
            t[n] += q
    entrees = t["achat"] + t["retour_vente"] + t["transfert_in"] + t["autre_in"] + max(t["rapprochement"], 0)
    sorties = t["vente"] + t["transfert_out"] + t["autre_out"] + max(-t["rapprochement"], 0)
    return {"item_code": code, "debut": flt(debut, 6), "achat": flt(t["achat"], 6), "vente": flt(t["vente"], 6),
            "retour_vente": flt(t["retour_vente"], 6), "transfert_in": flt(t["transfert_in"], 6),
            "transfert_out": flt(t["transfert_out"], 6), "autre_in": flt(t["autre_in"], 6), "autre_out": flt(t["autre_out"], 6),
            "rapprochement": flt(t["rapprochement"], 6), "fin": flt(fin, 6),
            "ecart_calcul": flt(debut + entrees - sorties - fin, 6)}


def _mediane(vals: list[float]) -> float:
    vals = [flt(v) for v in vals if v is not None]
    return statistics.median(vals) if vals else 0.0


def prix_typiques(ventes: list[dict]) -> dict[str, float]:
    """item_code → prix de vente médian (lignes à prix > 0). Pure."""
    par = defaultdict(list)
    for v in ventes:
        if flt(v.get("rate")) > 0:
            par[v["item_code"]].append(flt(v["rate"]))
    return {c: flt(_mediane(p), 3) for c, p in par.items()}


def suspicions(codes: list[str], rapprochements: list[dict], negatifs: list[dict], achats: list[dict],
               ventes: list[dict], prix: dict[str, float], bilans: list[dict], noms: dict | None = None) -> list[dict]:
    """Signatures d'erreur de saisie dans la famille. Chaque suspicion : {type, gravite (1-3), item_code,
    date, piece, piece_type, qte, texte}. Pure.

    rapprochements : [{item_code, date, piece, avant, apres, diff}]
    negatifs       : [{item_code, date, piece, piece_type, apres}] premier passage sous zéro, puis minimum
    achats         : [{item_code, date, piece, piece_type, qte, uom, conversion, rate, fournisseur}]
    ventes         : [{item_code, date, piece, piece_type, qte, rate, client, kit}]
    prix           : item_code → prix de vente typique
    bilans         : [bilan_article(...)]"""
    noms = noms or {}
    nom = lambda c: f"{c} ({noms[c]})" if noms.get(c) else c
    out = []
    # 1. Stock négatif : vendu au-delà du stock
    for n in negatifs:
        out.append({"type": "stock_negatif", "gravite": 3, "item_code": n["item_code"], "date": n["date"],
                    "piece": n.get("piece"), "piece_type": n.get("piece_type"), "qte": flt(n.get("apres"), 6),
                    "texte": _("{0} : stock système passé à {1} le {2} ({3}). Des sorties au-delà du stock : "
                               "quantité ou code d'article faux sur une pièce, ou entrée jamais saisie.")
                    .format(nom(n["item_code"]), _q(n.get("apres")), n["date"], n.get("piece") or "?")})
    # 2. Rapprochements de sens opposé entre deux articles (≤ 30 jours) : substitution. Chaque correction
    #    négative est appariée au plus proche positif d'un AUTRE article, une seule fois.
    recs = [r for r in rapprochements if abs(flt(r.get("diff"))) > TOL]
    pris = set()
    for moins in sorted((r for r in recs if flt(r["diff"]) < 0), key=lambda r: str(r["date"])):
        cand = [p for p in recs if flt(p["diff"]) > 0 and p["item_code"] != moins["item_code"] and p["piece"] not in pris
                and abs((getdate(p["date"]) - getdate(moins["date"])).days) <= 30]
        if not cand:
            continue
        plus = min(cand, key=lambda p: abs((getdate(p["date"]) - getdate(moins["date"])).days))
        pris.add(plus["piece"])
        out.append({"type": "substitution", "gravite": 2, "item_code": moins["item_code"], "date": moins["date"],
                    "piece": moins["piece"], "piece_type": "Stock Reconciliation", "qte": flt(moins["diff"], 6),
                    "texte": _("{0} corrigé de {1} ({2}) et {3} de +{4} ({5}) à moins de 30 jours : l'un a "
                               "probablement été vendu ou reçu sous le code de l'autre.")
                    .format(nom(moins["item_code"]), _q(moins["diff"]), moins["date"], nom(plus["item_code"]),
                            _q(plus["diff"]), plus["date"])})
    # 3. Même article corrigé ≥ 3 fois dans le même sens
    par_item = defaultdict(list)
    for r in recs:
        par_item[r["item_code"]].append(r)
    for c, rs in par_item.items():
        for signe in (1, -1):
            memes = [r for r in rs if flt(r["diff"]) * signe > 0]
            if len(memes) >= 3:
                total = flt(sum(flt(r["diff"]) for r in memes), 6)
                out.append({"type": "recurrent", "gravite": 2, "item_code": c, "date": memes[-1]["date"],
                            "piece": memes[-1]["piece"], "piece_type": "Stock Reconciliation", "qte": total,
                            "texte": _("{0} corrigé {1} fois dans le même sens sur la période (total {2}) : "
                                       "{3}").format(nom(c), len(memes), ("+" if total > 0 else "") + _q(total),
                                                     _("des entrées qui ne sont jamais saisies (retours, échanges, "
                                                       "réceptions) ?") if signe > 0 else
                                                     _("des sorties sans document (consommation, prêts, casse) ?"))})
    # 4. Achats : quantité hors norme, unité ≠ pièce, prix qui saute
    achats_par = defaultdict(list)
    for a in sorted(achats, key=lambda x: str(x.get("date"))):
        achats_par[a["item_code"]].append(a)
    for c, rows in achats_par.items():
        qtes = [flt(a["qte"]) for a in rows]
        med = _mediane(qtes)
        prec = None
        for a in rows:
            if flt(a.get("conversion")) and abs(flt(a["conversion"]) - 1) > TOL:
                out.append({"type": "achat_unite", "gravite": 2, "item_code": c, "date": a["date"], "piece": a["piece"],
                            "piece_type": a.get("piece_type"), "qte": flt(a["qte"], 6),
                            "texte": _("{0} : achat de {1} {2} (×{3} en stock) le {4} ({5}) — vérifier l'unité.")
                            .format(nom(c), _q(a["qte"]), a.get("uom") or "", _q(a["conversion"]), a["date"], a["piece"])})
            if len(rows) >= 2 and med > 0 and flt(a["qte"]) >= 3 * med and flt(a["qte"]) >= 10:
                out.append({"type": "achat_quantite", "gravite": 2, "item_code": c, "date": a["date"], "piece": a["piece"],
                            "piece_type": a.get("piece_type"), "qte": flt(a["qte"], 6),
                            "texte": _("{0} : réception de {1} le {2} ({3}) pour une réception habituelle de {4} — "
                                       "quantité ou code à vérifier.").format(nom(c), _q(a["qte"]), a["date"], a["piece"], _q(med))})
            if prec and flt(prec.get("rate")) > 0 and flt(a.get("rate")) > 0:
                ratio = flt(a["rate"]) / flt(prec["rate"])
                if ratio >= 2 or ratio <= 0.5:
                    out.append({"type": "achat_prix", "gravite": 1, "item_code": c, "date": a["date"], "piece": a["piece"],
                                "piece_type": a.get("piece_type"), "qte": flt(a["qte"], 6),
                                "texte": _("{0} : prix d'achat {1} le {2} ({3}) contre {4} la fois d'avant ({5}) — "
                                           "unité, devise ou article à vérifier.")
                                .format(nom(c), _q(a["rate"]), a["date"], a["piece"], _q(prec["rate"]), prec["date"])})
            prec = a
    # 5. Ventes : quantité hors norme ; prix d'un autre article de la famille
    ventes_par = defaultdict(list)
    for v in ventes:
        if not v.get("kit"):
            ventes_par[v["item_code"]].append(v)
    for c, rows in ventes_par.items():
        med = _mediane([flt(v["qte"]) for v in rows]) or 1
        for v in rows:
            if flt(v["qte"]) >= max(8, 5 * med):
                out.append({"type": "vente_quantite", "gravite": 2, "item_code": c, "date": v["date"], "piece": v["piece"],
                            "piece_type": v.get("piece_type"), "qte": -flt(v["qte"], 6),
                            "texte": _("{0} : {1} vendus d'un coup le {2} ({3}{4}) pour une vente habituelle de {5} — "
                                       "quantité à confirmer.").format(nom(c), _q(v["qte"]), v["date"], v["piece"],
                                                                        " · " + v["client"] if v.get("client") else "", _q(med))})
            p, mien = flt(v.get("rate")), flt(prix.get(c))
            if p > 0 and mien > 0 and abs(p - mien) / mien > 0.2:
                autres = [o for o in codes if o != c and flt(prix.get(o)) > 0 and abs(p - flt(prix[o])) / flt(prix[o]) <= 0.05]
                if autres:
                    out.append({"type": "prix_autre_article", "gravite": 2, "item_code": c, "date": v["date"], "piece": v["piece"],
                                "piece_type": v.get("piece_type"), "qte": -flt(v["qte"], 6),
                                "texte": _("{0} vendu {1} le {2} ({3}) : c'est le prix de {4} (habituel {5} contre {6}) — "
                                           "mauvais code sur la vente ?").format(nom(c), _q(p), v["date"], v["piece"],
                                                                                 ", ".join(nom(o) for o in autres[:2]),
                                                                                 _q(prix[autres[0]]), _q(mien))})
    # 6. Bilan de famille : rapprochements qui se compensent
    net = flt(sum(flt(b.get("rapprochement")) for b in bilans), 6)
    brut = flt(sum(abs(flt(b.get("rapprochement"))) for b in bilans), 6)
    if brut > TOL and len([b for b in bilans if abs(flt(b.get("rapprochement"))) > TOL]) >= 2 and abs(net) <= 0.25 * brut:
        out.append({"type": "famille_compensee", "gravite": 2, "item_code": None, "date": None, "piece": None, "piece_type": None,
                    "qte": net, "texte": _("Sur la famille, les rapprochements se compensent presque (net {0} pour {1} corrigés "
                                           "en valeur absolue) : des confusions de codes entre articles plutôt que des pertes.")
                    .format(("+" if net > 0 else "") + _q(net), _q(brut))})
    out.sort(key=lambda s: (-s["gravite"], str(s.get("date") or "")))
    return out


def _q(n) -> str:
    n = flt(n, 6)
    return str(int(n)) if abs(n - int(n)) < TOL else ("%.3f" % n).rstrip("0").rstrip(".")


# ── Lecture ──────────────────────────────────────────────────────────────────

def _articles(groupe=None, articles=None, item_code=None) -> tuple[list[dict], str]:
    """Les articles de stock actifs de la famille et son libellé."""
    if articles:
        codes = [a for a in (json.loads(articles) if isinstance(articles, str) else articles) if a]
        rows = frappe.get_all("Item", filters={"name": ["in", codes], "is_stock_item": 1},
                              fields=["name", "item_name", "item_group"], order_by="name")
        return rows[:MAX_ARTICLES], _("{0} article(s)").format(len(rows))
    if item_code and not groupe:
        groupe = frappe.db.get_value("Item", item_code, "item_group")
    if not groupe:
        frappe.throw(_("Indiquez un groupe d'articles ou un article."))
    rows = frappe.get_all("Item", filters={"item_group": groupe, "is_stock_item": 1, "disabled": 0, "has_variants": 0},
                          fields=["name", "item_name", "item_group"], order_by="name")
    return rows[:MAX_ARTICLES], groupe


def _stocks_par_entrepot(codes, entrepot, quand) -> dict[tuple[str, str], float]:
    """(article, entrepôt) → stock à `quand` = « quantité après » du DERNIER mouvement (un rapprochement porte
    actual_qty = 0 au grand livre : la somme des actual_qty ne donne pas le stock)."""
    cond = "and warehouse = %(w)s" if entrepot else ""
    rows = frappe.db.sql(f"""select item_code, warehouse, qty_after_transaction as q from (
                               select item_code, warehouse, qty_after_transaction,
                                      row_number() over (partition by item_code, warehouse
                                                         order by posting_date desc, posting_time desc, creation desc) as rn
                               from `tabStock Ledger Entry`
                               where is_cancelled = 0 and item_code in %(codes)s {cond}
                                 and timestamp(posting_date, posting_time) <= %(q)s) t where rn = 1""",
                         {"codes": tuple(codes), "w": entrepot, "q": quand}, as_dict=True)
    return {(r.item_code, r.warehouse): flt(r.q, 6) for r in rows}


def _stocks_a(codes, entrepot, quand) -> dict[str, float]:
    out = defaultdict(float)
    for (c, _w), q in _stocks_par_entrepot(codes, entrepot, quand).items():
        out[c] += q
    return dict(out)


def _mouvements(codes, entrepot, debut, fin) -> list[dict]:
    cond = "and sle.warehouse = %(w)s" if entrepot else ""
    rows = frappe.db.sql(f"""select sle.item_code, sle.posting_date as date, sle.voucher_type, sle.voucher_no,
                                    sle.voucher_detail_no, sle.actual_qty as qte, sle.qty_after_transaction as apres, sle.warehouse
                             from `tabStock Ledger Entry` sle
                             where sle.is_cancelled = 0 and sle.item_code in %(codes)s {cond}
                               and timestamp(sle.posting_date, sle.posting_time) > %(d)s
                               and timestamp(sle.posting_date, sle.posting_time) <= %(f)s
                             order by sle.posting_date, sle.posting_time, sle.creation""",
                         {"codes": tuple(codes), "w": entrepot, "d": debut, "f": fin}, as_dict=True)
    # Rapprochement : actual_qty = 0 au grand livre ; le mouvement vaut « après » − stock précédent de l'entrepôt.
    courant = _stocks_par_entrepot(codes, entrepot, debut)
    for m in rows:
        cle = (m.item_code, m.warehouse)
        if m.voucher_type == "Stock Reconciliation":
            m.qte = flt(m.apres, 6) - flt(courant.get(cle, 0.0), 6)
        courant[cle] = flt(m.apres, 6)
    return rows


def _negatifs(mouvements, entrepot) -> list[dict]:
    """Premier passage sous zéro de chaque article (par entrepôt), avec le minimum atteint."""
    out, vus = [], {}
    for m in mouvements:
        if flt(m.apres) < -TOL:
            cle = (m.item_code, m.warehouse)
            if cle not in vus:
                vus[cle] = {"item_code": m.item_code, "entrepot": m.warehouse, "date": str(m.date), "piece": m.voucher_no,
                            "piece_type": m.voucher_type, "apres": flt(m.apres, 6), "minimum": flt(m.apres, 6)}
                out.append(vus[cle])
            vus[cle]["minimum"] = min(vus[cle]["minimum"], flt(m.apres, 6))
    for n in out:
        n["apres"] = n["minimum"]
    return out


def _achats(codes, entrepot, debut, fin) -> list[dict]:
    out = []
    cond = "and l.warehouse = %(w)s" if entrepot else ""
    for dt, ligne in (("Purchase Receipt", "Purchase Receipt Item"), ("Purchase Invoice", "Purchase Invoice Item")):
        extra = "and p.update_stock = 1" if dt == "Purchase Invoice" else ""
        rows = frappe.db.sql(f"""select p.name as piece, p.posting_date as date, p.supplier_name as fournisseur, l.item_code,
                                        l.qty as qte, l.uom, l.conversion_factor as conversion, l.rate, l.purchase_order
                                 from `tab{ligne}` l join `tab{dt}` p on p.name = l.parent
                                 where p.docstatus = 1 and l.item_code in %(codes)s {cond} {extra}
                                   and p.posting_date > %(d)s and p.posting_date <= %(f)s order by p.posting_date""",
                             {"codes": tuple(codes), "w": entrepot, "d": str(debut)[:10], "f": str(fin)[:10]}, as_dict=True)
        for r in rows:
            out.append({"item_code": r.item_code, "date": str(r.date), "piece": r.piece, "piece_type": dt, "qte": flt(r.qte, 6),
                        "uom": r.uom, "conversion": flt(r.conversion, 6), "rate": flt(r.rate, 3), "fournisseur": r.fournisseur,
                        "commande_achat": r.purchase_order})
    return sorted(out, key=lambda a: a["date"])[:MAX_LIGNES]


def _ventes(codes, entrepot, debut, fin) -> list[dict]:
    cond = "and l.warehouse = %(w)s" if entrepot else ""
    params = {"codes": tuple(codes), "w": entrepot, "d": str(debut)[:10], "f": str(fin)[:10]}
    rows = frappe.db.sql(f"""select p.name as piece, p.posting_date as date, p.customer_name as client, p.is_return,
                                    l.item_code, l.qty as qte, l.rate, null as kit
                             from `tabDelivery Note Item` l join `tabDelivery Note` p on p.name = l.parent
                             where p.docstatus = 1 and l.item_code in %(codes)s {cond}
                               and p.posting_date > %(d)s and p.posting_date <= %(f)s""", params, as_dict=True)
    kits = frappe.db.sql(f"""select p.name as piece, p.posting_date as date, p.customer_name as client, p.is_return,
                                    l.item_code, l.qty as qte, l.rate, l.parent_item as kit
                             from `tabPacked Item` l join `tabDelivery Note` p on p.name = l.parent
                             where p.docstatus = 1 and l.item_code in %(codes)s {cond}
                               and p.posting_date > %(d)s and p.posting_date <= %(f)s""", params, as_dict=True)
    out = [{"item_code": r.item_code, "date": str(r.date), "piece": r.piece, "piece_type": "Delivery Note",
            "qte": flt(r.qte, 6), "rate": flt(r.rate, 3), "client": r.client, "kit": r.kit, "retour": cint(r.is_return)}
           for r in rows + kits]
    return sorted(out, key=lambda v: v["date"])[:MAX_LIGNES]


def _rapprochements(codes, entrepot, debut, fin) -> list[dict]:
    cond = "and l.warehouse = %(w)s" if entrepot else ""
    rows = frappe.db.sql(f"""select p.name as piece, p.posting_date as date, l.item_code, l.current_qty as avant, l.qty as apres,
                                    l.quantity_difference as diff, l.warehouse
                             from `tabStock Reconciliation Item` l join `tabStock Reconciliation` p on p.name = l.parent
                             where p.docstatus = 1 and l.item_code in %(codes)s {cond}
                               and p.posting_date > %(d)s and p.posting_date <= %(f)s order by p.posting_date""",
                         {"codes": tuple(codes), "w": entrepot, "d": str(debut)[:10], "f": str(fin)[:10]}, as_dict=True)
    return [{"item_code": r.item_code, "date": str(r.date), "piece": r.piece, "avant": flt(r.avant, 6), "apres": flt(r.apres, 6),
             "diff": flt(r.diff, 6), "entrepot": r.warehouse} for r in rows]


def _kits(codes) -> list[dict]:
    rows = frappe.db.sql("""select pb.new_item_code as kit, i.item_code, i.qty from `tabProduct Bundle` pb
                            join `tabProduct Bundle Item` i on i.parent = pb.name
                            where pb.name in (select pb2.name from `tabProduct Bundle` pb2 join `tabProduct Bundle Item` i2
                                              on i2.parent = pb2.name where i2.item_code in %(codes)s)
                            order by pb.new_item_code, i.idx""", {"codes": tuple(codes)}, as_dict=True)
    par = defaultdict(list)
    for r in rows:
        par[r.kit].append(f"{'+' if flt(r.qty) > 0 else ''}{_q(r.qty)} {r.item_code}")
    return [{"kit": k, "composition": ", ".join(v)} for k, v in par.items()]


# ── IA ───────────────────────────────────────────────────────────────────────

SYSTEM_FAMILLE = """Tu es le contrôleur de gestion d'Aqua World (traitement d'eau, Tunisie). Tu reçois le dossier d'une FAMILLE
d'articles de stock sur une période et un entrepôt : par article, stock au début et à la fin, achats, ventes, transferts et
rapprochements d'inventaire mois par mois, les épisodes de stock négatif, les lignes d'achat (quantité, unité, prix) et de
vente (quantité, prix, client, kit d'échange), les rapprochements avec leur écart, la composition des kits, et les SUSPICIONS
déjà relevées par des règles (signatures d'erreur de saisie). Un rapprochement d'inventaire positif = on a trouvé plus que le
système, négatif = il manquait. Un stock négatif = on a enregistré plus de sorties que d'entrées : quantité fausse, mauvais
code d'article (souvent un voisin de la même famille), ou entrée jamais saisie. Ton travail : dire, article par article, ce
qui s'est probablement passé en privilégiant les ERREURS DE SAISIE (code voisin, quantité ×10, unité, double saisie, échange
mal compris) avant les pertes réelles, en citant TOUJOURS la pièce (numéro) et la date qui te font le dire. N'invente aucun
document. Réponds en français, concis, en JSON strict :
{"conclusion": "3 à 6 phrases pour le responsable magasin : la lecture d'ensemble de la famille et les 2-3 vérifications à faire en premier",
 "articles": [{"item_code": "...", "diagnostic": "1 à 3 phrases", "confiance": "haute|moyenne|basse",
               "hypotheses": [{"type": "code voisin|quantité|unité|double saisie|échange|entrée non saisie|perte réelle|comptage",
                               "piece": "numéro de pièce ou null", "detail": "1 phrase"}],
               "action": "ce qu'il faut vérifier ou corriger en premier"}]}"""


def _ia(doss: dict) -> dict | None:
    try:
        from aquaworld_ia.ia.chat import chat_json
        from aquaworld_ia.ia.journal import verifier_plafond
    except Exception:
        return None
    compact = {k: doss[k] for k in ("famille", "periode", "entrepot", "articles", "mensuel", "negatifs", "achats",
                                    "ventes_remarquables", "rapprochements", "kits", "suspicions")}
    try:
        verifier_plafond(0.10)
        rep = chat_json(SYSTEM_FAMILLE, json.dumps(compact, ensure_ascii=False, default=str), fonctionnalite=FONCTIONNALITE_IA)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Analyse famille : appel IA")
        return None
    if not isinstance(rep, dict):
        return None
    arts = []
    for a in rep.get("articles") or []:
        if isinstance(a, dict) and a.get("item_code"):
            arts.append({"item_code": a["item_code"], "diagnostic": (a.get("diagnostic") or "")[:800],
                         "confiance": a.get("confiance"), "action": (a.get("action") or "")[:400],
                         "hypotheses": [{"type": h.get("type"), "piece": h.get("piece"), "detail": (h.get("detail") or "")[:300]}
                                        for h in (a.get("hypotheses") or []) if isinstance(h, dict)][:6]})
    return {"conclusion": (rep.get("conclusion") or "")[:2500], "articles": arts}


# ── Calcul ───────────────────────────────────────────────────────────────────

def _calculer(groupe=None, articles=None, item_code=None, entrepot=None, mois=None, fin=None) -> dict:
    """Le dossier complet (règles comprises, sans IA)."""
    items, famille = _articles(groupe, articles, item_code)
    if not items:
        frappe.throw(_("Aucun article de stock dans cette famille."))
    codes = [i.name for i in items]
    noms = {i.name: i.item_name for i in items}
    entrepot = None if entrepot == "*" else (entrepot or S.magasin())
    fin_d = getdate(fin) if fin else getdate(nowdate())
    debut_d = add_months(fin_d, -cint(mois or PERIODE_MOIS))
    debut, fin_ts = f"{debut_d} 23:59:59", f"{fin_d} 23:59:59"
    mvts = _mouvements(codes, entrepot, debut, fin_ts)
    stocks_debut, stocks_fin = _stocks_a(codes, entrepot, debut), _stocks_a(codes, entrepot, fin_ts)
    mensuel = mensualiser([{"item_code": m.item_code, "date": m.date, "voucher_type": m.voucher_type, "qte": m.qte} for m in mvts])
    bilans = [bilan_article(c, stocks_debut.get(c, 0.0), mensuel.get(c, {}), stocks_fin.get(c, 0.0)) for c in codes]
    for b in bilans:
        b["item_name"] = noms.get(b["item_code"])
        b["mouvements"] = sum(1 for m in mvts if m.item_code == b["item_code"])
    achats, ventes = _achats(codes, entrepot, debut, fin_ts), _ventes(codes, entrepot, debut, fin_ts)
    recs, negatifs = _rapprochements(codes, entrepot, debut, fin_ts), _negatifs(mvts, entrepot)
    prix = prix_typiques([v for v in ventes if not v.get("kit") and not v.get("retour")])
    susp = suspicions(codes, recs, negatifs, achats, ventes, prix, bilans, noms)
    med_vente = {c: (_mediane([flt(v["qte"]) for v in ventes if v["item_code"] == c and not v.get("kit")]) or 1) for c in codes}
    remarquables = [v for v in ventes if v.get("kit") or flt(v["qte"]) >= max(3, 3 * med_vente.get(v["item_code"], 1))
                    or (flt(v.get("rate")) > 0 and flt(prix.get(v["item_code"])) > 0
                        and abs(flt(v["rate"]) - prix[v["item_code"]]) / prix[v["item_code"]] > 0.2)][:120]
    return {"famille": famille, "groupe": groupe or (frappe.db.get_value("Item", item_code, "item_group") if item_code else None),
            "periode": {"debut": str(debut_d), "fin": str(fin_d), "mois": cint(mois or PERIODE_MOIS)},
            "entrepot": entrepot or _("Tous les entrepôts"), "articles": bilans, "mensuel": mensuel,
            "negatifs": negatifs, "achats": achats, "ventes_remarquables": remarquables, "rapprochements": recs,
            "kits": _kits(codes), "suspicions": susp, "prix_typiques": prix, "nb_ventes": len(ventes), "ia": None}


def _notifier(doc, sujet: str):
    """Prévenir qui a lancé : événement temps réel (page ouverte) + notification Desk (cloche), avec le lien."""
    try:
        frappe.get_doc({"doctype": "Notification Log", "for_user": doc.lancee_par, "type": "Alert", "subject": sujet,
                        "document_type": DOCTYPE_ANALYSE, "document_name": doc.name}).insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Analyse famille : notification")
    frappe.publish_realtime(EVENEMENT, {"name": doc.name, "famille": doc.famille, "statut": doc.statut,
                                        "inventaire": doc.inventaire, "sujet": sujet}, user=doc.lancee_par, after_commit=True)


def executer(name: str):
    """La tâche : calcul, IA si demandée, résultat enregistré sur la fiche, notification. Relançable."""
    doc = frappe.get_doc(DOCTYPE_ANALYSE, name)
    try:
        doss = _calculer(groupe=doc.groupe, item_code=doc.item_code, entrepot=doc.entrepot or None,
                         mois=doc.mois, fin=doc.periode_fin)
        if cint(doc.avec_ia):
            doss["ia"] = _ia(doss)
        doc.update({"famille": doss["famille"], "periode_debut": doss["periode"]["debut"], "statut": STATUT_TERMINEE,
                    "terminee_le": now_datetime(), "erreur": None, "resultat": json.dumps(doss, ensure_ascii=False, default=str),
                    "conclusion": (doss["ia"] or {}).get("conclusion") if doss.get("ia") else
                                  _("{0} point(s) à vérifier d'après les règles{1}.").format(
                                      len(doss["suspicions"]), _(" — l'IA n'a pas répondu") if cint(doc.avec_ia) else "")})
        doc.save(ignore_permissions=True)
        _notifier(doc, (_("Analyse IA terminée : {0}") if cint(doc.avec_ia) else _("Analyse terminée : {0}")).format(doc.famille))
    except Exception:
        doc.update({"statut": STATUT_ECHEC, "terminee_le": now_datetime(), "erreur": frappe.get_traceback()[-1500:]})
        doc.save(ignore_permissions=True)
        frappe.log_error(frappe.get_traceback(), f"Analyse famille {name}")
        _notifier(doc, _("Analyse de {0} en échec").format(doc.famille))
    frappe.db.commit()
    return doc.name


def _lire(doc) -> dict:
    r = json.loads(doc.resultat) if doc.resultat else {}
    r.update({"analyse": doc.name, "statut": doc.statut, "inventaire": doc.inventaire, "lancee_par": frappe.utils.get_fullname(doc.lancee_par),
              "lancee_le": str(doc.lancee_le)[:16] if doc.lancee_le else None, "terminee_le": str(doc.terminee_le)[:16] if doc.terminee_le else None,
              "avec_ia": cint(doc.avec_ia), "erreur": doc.erreur, "famille": doc.famille or r.get("famille")})
    return r


# ── Points d'entrée ──────────────────────────────────────────────────────────

def _acces():
    S._lecture()
    if not S.est_responsable():
        frappe.throw(_("L'analyse d'une famille est réservée aux responsables."), frappe.PermissionError)


@frappe.whitelist()
def analyser_famille(groupe=None, articles=None, item_code=None, entrepot=None, mois=None, fin=None, ia=1, inventaire=None):
    """Lance une analyse : fiche « Analyse Famille Stock » liée à l'inventaire d'origine. Avec IA → tâche de fond
    (réponse {"analyse", "en_fond": True}, l'utilisateur est prévenu à la fin) ; sans IA → immédiate, résultat rendu."""
    _acces()
    if not (groupe or item_code or articles):
        frappe.throw(_("Indiquez un groupe d'articles ou un article."))
    if articles:
        frappe.throw(_("Donnez un groupe d'articles ou un article de la famille."))
    if item_code and not groupe:
        groupe = frappe.db.get_value("Item", item_code, "item_group")
    entrepot = None if entrepot == "*" else (entrepot or S.magasin())
    fin_d = getdate(fin) if fin else getdate(nowdate())
    doc = frappe.get_doc({"doctype": DOCTYPE_ANALYSE, "famille": groupe, "groupe": groupe, "item_code": item_code,
                          "entrepot": entrepot or "*", "mois": cint(mois or PERIODE_MOIS), "periode_fin": fin_d,
                          "periode_debut": add_months(fin_d, -cint(mois or PERIODE_MOIS)),
                          "inventaire": inventaire or None, "avec_ia": 1 if cint(ia) else 0, "statut": STATUT_EN_COURS,
                          "lancee_par": frappe.session.user, "lancee_le": now_datetime()})
    doc.insert(ignore_permissions=True)
    if cint(ia):
        frappe.enqueue("customization_app.analyse_famille.executer", name=doc.name, queue="long", timeout=1500,
                       enqueue_after_commit=True, job_name=f"analyse_famille {doc.name}")
        return {"analyse": doc.name, "en_fond": True, "famille": doc.famille}
    executer(doc.name)
    return _lire(frappe.get_doc(DOCTYPE_ANALYSE, doc.name))


@frappe.whitelist()
def lire_analyse(name):
    _acces()
    return _lire(frappe.get_doc(DOCTYPE_ANALYSE, name))


@frappe.whitelist()
def analyses_de(inventaire):
    """Les analyses lancées depuis cet inventaire, la plus récente d'abord."""
    _acces()
    rows = frappe.get_all(DOCTYPE_ANALYSE, filters={"inventaire": inventaire},
                          fields=["name", "famille", "item_code", "statut", "avec_ia", "lancee_par", "lancee_le", "terminee_le", "conclusion", "mois"],
                          order_by="lancee_le desc", limit=20)
    for r in rows:
        r.lancee_par = frappe.utils.get_fullname(r.lancee_par)
        r.lancee_le, r.terminee_le = (str(r.lancee_le)[:16] if r.lancee_le else None), (str(r.terminee_le)[:16] if r.terminee_le else None)
    return rows
