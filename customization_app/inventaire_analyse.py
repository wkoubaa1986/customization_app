"""Analyse d'un écart d'inventaire du Magasin, article par article (demande du 02/11/2026).

« Quand j'ai un grand écart, est-ce que je peux analyser pour un article pourquoi ? » Sur une ligne comptée
avec écart, le bouton 🔎 rassemble ce qu'ERPNext sait de cet article au Magasin, SANS rien inventer :
  - le comptage précédent (dernier inventaire clôturé où l'article a été compté) : compté, système, écart ;
  - les MOUVEMENTS du Magasin depuis ce comptage précédent (ou 90 jours) jusqu'au moment du comptage :
    BL (client, commande, tâche), transferts (d'où, vers où, demandé / validé par), achats, rapprochements,
    avec le stock après chaque mouvement (grand livre de stock, pièces annulées exclues) ;
  - ce qui est SORTI SANS ÉCRITURE : BL en attente de validation magasin, transferts à double validation
    pas encore validés, commandes validées pas encore livrées — la pièce est peut-être déjà partie ;
  - l'article AILLEURS : stock dans les autres entrepôts (Hall, véhicules) — peut-être rangé ou compté ailleurs ;
  - les cases NON COMPTÉES : un emplacement de l'article laissé vide sur la feuille = compté incomplet ;
  - l'historique des comptages de l'article (écart récurrent ?).
Les RÈGLES (`expliquer`, pure, testée) en tirent des phrases : expliqué, probable, inexpliqué — chacune
appuyée sur le fait qui la porte.
"""
from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_datetime, now_datetime

from customization_app import inventaire_magasin as I
from customization_app import stock_entrepots as S

JOURS_PAR_DEFAUT = 90
MAX_MOUVEMENTS = 200
MAX_HISTORIQUE = 8
TOL = 1e-6
ETAT_ATTENTE_MAGASIN = "En attente validation magasin"


# ── Règles pures ─────────────────────────────────────────────────────────────

def bilan_mouvements(mouvements: list[dict]) -> dict:
    """Totaux des entrées et sorties, par libellé de pièce. `mouvements` : [{libelle, qte (signée)}]."""
    out = {"entrees": 0.0, "sorties": 0.0, "n": 0, "par_type": {}}
    for m in mouvements or []:
        q = flt(m.get("qte"))
        t = out["par_type"].setdefault(m.get("libelle") or "?", {"entrees": 0.0, "sorties": 0.0, "n": 0})
        t["n"] += 1
        out["n"] += 1
        if q >= 0:
            t["entrees"] += q
            out["entrees"] += q
        else:
            t["sorties"] += -q
            out["sorties"] += -q
    for t in [out] + list(out["par_type"].values()):
        t["entrees"], t["sorties"] = flt(t["entrees"], 6), flt(t["sorties"], 6)
    return out


def _q(n) -> str:
    n = flt(n, 6)
    return str(int(n)) if abs(n - int(n)) < TOL else ("%.3f" % n).rstrip("0").rstrip(".")


def expliquer(ecart: float, bilan: dict, precedent: dict | None, brouillons: list[dict], a_livrer: list[dict],
              ailleurs: list[dict], cases_vides: list[str], depuis: str | None) -> dict:
    """{"verdict": Expliqué | Probable | Inexpliqué, "phrases": [...]} d'après les faits. Pure.

    ecart      : compté − système (< 0 : il manque)
    bilan      : `bilan_mouvements` de la période
    precedent  : {"name", "date", "ecart", "qte_comptee", "qte_systeme"} ou None
    brouillons : [{"type", "name", "qte", ...}] pièces non validées qui sortent l'article du Magasin
    a_livrer   : [{"commande", "client", "reste"}] commandes validées pas encore livrées
    ailleurs   : [{"entrepot", "qte"}] stock de l'article dans d'autres entrepôts
    cases_vides: emplacements de l'article laissés sans quantité sur la feuille
    depuis     : date de début de période (texte), pour les libellés"""
    ecart = flt(ecart, 6)
    phrases, verdict = [], "Inexpliqué"
    if abs(ecart) < TOL:
        return {"verdict": "Expliqué", "phrases": [_("Aucun écart sur cet article.")]}
    manque = ecart < 0
    # 1. Ce qui est sorti sans écriture (il manque)
    sorti = flt(sum(flt(b.get("qte")) for b in brouillons), 6)
    if manque and sorti > TOL:
        noms = ", ".join(f"{b.get('name')} ({_q(b.get('qte'))})" for b in brouillons)
        if sorti + TOL >= -ecart:
            verdict = "Expliqué"
            phrases.append(_("Il manque {0} : {1} déjà sorti(s) sur des pièces non validées ({2}). "
                             "Les pièces sont parties, l'écriture n'est pas encore passée : "
                             "ne pas rapprocher avant de valider ces pièces.").format(_q(-ecart), _q(sorti), noms))
        else:
            verdict = "Probable"
            phrases.append(_("{0} des {1} manquants sont sur des pièces non validées ({2}) ; "
                             "le reste ({3}) n'a pas de document.").format(_q(sorti), _q(-ecart), noms, _q(-ecart - sorti)))
    # 2. Commandes validées, pas encore livrées : peut-être préparées ou parties sans BL
    reste = flt(sum(flt(c.get("reste")) for c in a_livrer), 6)
    if manque and reste > TOL and verdict != "Expliqué":
        noms = ", ".join(f"{c.get('commande')}{' · ' + c['client'] if c.get('client') else ''} ({_q(c.get('reste'))})"
                         for c in a_livrer[:4])
        verdict = "Probable" if verdict == "Inexpliqué" else verdict
        phrases.append(_("{0} à livrer sur des commandes validées sans BL ({1}) : la pièce a pu être préparée "
                         "ou partir avant le BL.").format(_q(reste), noms))
    # 3. Cases vides : comptage incomplet
    if cases_vides:
        if verdict == "Inexpliqué":
            verdict = "Probable"
        phrases.append(_("Emplacement(s) laissé(s) vide(s) sur la feuille : {0}. Si l'article y est rangé, le "
                         "compté est incomplet.").format(", ".join(cases_vides)))
    # 4. Ailleurs
    if ailleurs:
        total = flt(sum(flt(a.get("qte")) for a in ailleurs), 6)
        noms = ", ".join(f"{a.get('entrepot')} : {_q(a.get('qte'))}" for a in ailleurs[:5])
        if manque and verdict == "Inexpliqué":
            verdict = "Probable"
        phrases.append(_("L'article est aussi en stock ailleurs ({0}, total {1}) : peut-être rangé ou compté "
                         "dans un autre entrepôt.").format(noms, _q(total)))
    # 5. Mouvements de la période
    if bilan.get("n"):
        detail = " ; ".join(f"{lib} : {'+' + _q(t['entrees']) if t['entrees'] > TOL else ''}"
                            f"{' ' if t['entrees'] > TOL and t['sorties'] > TOL else ''}"
                            f"{'−' + _q(t['sorties']) if t['sorties'] > TOL else ''}"
                            for lib, t in bilan["par_type"].items())
        phrases.append(_("Mouvements enregistrés depuis le {0} : +{1} entrées, −{2} sorties ({3}). Le système "
                         "les a déjà comptés : l'écart est ce qui a bougé SANS écriture.")
                       .format(depuis or "?", _q(bilan["entrees"]), _q(bilan["sorties"]), detail))
    elif depuis:
        phrases.append(_("Aucun mouvement enregistré au Magasin depuis le {0}.").format(depuis))
    if manque and verdict == "Inexpliqué":
        phrases.append(_("Il manque {0} sans aucun document : consommé ou pris sans BL ni transfert, ou erreur "
                         "de comptage.").format(_q(-ecart)))
    if not manque and verdict == "Inexpliqué":
        phrases.append(_("{0} en trop sans document : entrée non enregistrée (retour client, achat sans "
                         "réception), pièce rendue sans BL retour, ou erreur de comptage — ou un autre article "
                         "compté sous ce nom.").format(_q(ecart)))
    # 6. Précédent
    if precedent:
        pe = flt(precedent.get("ecart"), 6)
        if abs(pe) > TOL and (pe < 0) == manque:
            phrases.append(_("Écart RÉCURRENT : au comptage précédent ({0}, {1}) il y avait déjà {2} "
                             "(compté {3} pour {4} au système). Le rapprochement d'alors a aligné le stock ; "
                             "l'écart s'est reformé depuis.").format(precedent.get("date"), precedent.get("name"),
                                                                     ("+" if pe > 0 else "") + _q(pe),
                                                                     _q(precedent.get("qte_comptee")),
                                                                     _q(precedent.get("qte_systeme"))))
        else:
            phrases.append(_("Comptage précédent ({0}, {1}) : {2}.").format(
                precedent.get("date"), precedent.get("name"),
                _("sans écart") if abs(pe) < TOL else ("+" if pe > 0 else "") + _q(pe)))
    return {"verdict": verdict, "phrases": phrases}


# ── Lecture ──────────────────────────────────────────────────────────────────

def _ligne(v, item_code):
    l = next((x for x in v.lignes if x.item_code == item_code), None)
    if not l:
        frappe.throw(_("Article {0} absent de l'inventaire {1}.").format(item_code, v.name))
    return l


def _precedent(v, l) -> dict | None:
    """Dernier comptage CLÔTURÉ de l'article au même entrepôt, avant celui-ci."""
    fin = get_datetime(l.compte_le) if l.compte_le else now_datetime()
    rows = frappe.db.sql("""select p.name, li.compte_le, li.qte_systeme, li.qte_comptee, li.ecart, li.qte_rapprochee
                            from `tabInventaire Magasin Ligne` li join `tabInventaire Magasin` p on p.name = li.parent
                            where li.item_code = %s and p.entrepot = %s and p.statut = %s and li.compte = 1
                              and p.name != %s and li.compte_le < %s
                            order by li.compte_le desc limit 1""",
                         (l.item_code, v.entrepot, I.TERMINE, v.name, fin), as_dict=True)
    if not rows:
        return None
    r = rows[0]
    return {"name": r.name, "date": str(r.compte_le)[:16], "qte_systeme": flt(r.qte_systeme, 6),
            "qte_comptee": flt(r.qte_comptee, 6), "ecart": flt(r.ecart, 6), "qte_rapprochee": flt(r.qte_rapprochee, 6)}


def _historique(v, l) -> list[dict]:
    rows = frappe.db.sql("""select p.name, li.compte_le, li.qte_systeme, li.qte_comptee, li.ecart, li.compte_par
                            from `tabInventaire Magasin Ligne` li join `tabInventaire Magasin` p on p.name = li.parent
                            where li.item_code = %s and p.entrepot = %s and p.statut = %s and li.compte = 1
                            order by li.compte_le desc limit %s""",
                         (l.item_code, v.entrepot, I.TERMINE, MAX_HISTORIQUE), as_dict=True)
    return [{"name": r.name, "date": str(r.compte_le)[:16], "qte_systeme": flt(r.qte_systeme, 6),
             "qte_comptee": flt(r.qte_comptee, 6), "ecart": flt(r.ecart, 6),
             "par": frappe.utils.get_fullname(r.compte_par) if r.compte_par else None} for r in rows]


def _mouvements(item_code, entrepot, debut, fin) -> list[dict]:
    rows = frappe.db.sql("""select sle.posting_date, sle.posting_time, sle.warehouse, sle.voucher_type, sle.voucher_no,
                                   sle.voucher_detail_no, sle.actual_qty as signe, abs(sle.actual_qty) as qte,
                                   sle.qty_after_transaction as apres
                            from `tabStock Ledger Entry` sle
                            where sle.item_code = %(item)s and sle.warehouse = %(entrepot)s and sle.is_cancelled = 0
                              and timestamp(sle.posting_date, sle.posting_time) > %(debut)s
                              and timestamp(sle.posting_date, sle.posting_time) <= %(fin)s
                            order by sle.posting_date desc, sle.posting_time desc, sle.creation desc
                            limit %(limite)s""",
                         {"item": item_code, "entrepot": entrepot, "debut": debut, "fin": fin, "limite": MAX_MOUVEMENTS},
                         as_dict=True)
    lignes = S.enrichir_mouvements(rows)
    # Kit (Product Bundle) : la ligne du BL est un « Packed Item », pas une ligne d'article → commande et kit à part.
    packed = S._par_nom("Packed Item", [r.voucher_detail_no for r in rows if r.voucher_type == "Delivery Note"],
                        ["parent_item", "parent_detail_docname"])
    kits_lignes = S._par_nom("Delivery Note Item", [p.parent_detail_docname for p in packed.values()], ["against_sales_order"])
    for r, l in zip(rows, lignes):
        l["qte"] = flt(r.signe, 6)
        l["apres"] = flt(r.apres, 6)
        p = packed.get(r.voucher_detail_no)
        if p:
            l["kit"] = p.parent_item
            l["commande"] = l["commande"] or (kits_lignes.get(p.parent_detail_docname) or {}).get("against_sales_order")
    return lignes


def _brouillons(item_code, entrepot) -> list[dict]:
    out = []
    for r in frappe.db.sql("""select dn.name, dn.workflow_state, dn.customer_name, dn.modified, dn.owner, sum(dni.qty) as qte
                              from `tabDelivery Note Item` dni join `tabDelivery Note` dn on dn.name = dni.parent
                              where dn.docstatus = 0 and dni.item_code = %s and dni.warehouse = %s
                              group by dn.name""", (item_code, entrepot), as_dict=True):
        out.append({"type": "Delivery Note", "name": r.name, "qte": flt(r.qte, 6), "client": r.customer_name,
                    "etat": r.workflow_state or _("Brouillon"), "par": frappe.utils.get_fullname(r.owner),
                    "le": str(r.modified)[:16]})
    for r in frappe.db.sql("""select se.name, se.stock_entry_type, se.owner, se.modified, sed.t_warehouse, sum(sed.qty) as qte
                              from `tabStock Entry Detail` sed join `tabStock Entry` se on se.name = sed.parent
                              where se.docstatus = 0 and sed.item_code = %s and sed.s_warehouse = %s
                              group by se.name, sed.t_warehouse""", (item_code, entrepot), as_dict=True):
        out.append({"type": "Stock Entry", "name": r.name, "qte": flt(r.qte, 6), "vers": r.t_warehouse,
                    "etat": _("Transfert non validé"), "par": frappe.utils.get_fullname(r.owner), "le": str(r.modified)[:16]})
    return out


def _a_livrer(item_code, entrepot) -> list[dict]:
    rows = frappe.db.sql("""select so.name, so.customer_name, sum(soi.qty - soi.delivered_qty) as reste
                            from `tabSales Order Item` soi join `tabSales Order` so on so.name = soi.parent
                            where so.docstatus = 1 and so.status not in ('Closed', 'Completed', 'Cancelled')
                              and soi.item_code = %s and soi.warehouse = %s and soi.qty > soi.delivered_qty
                            group by so.name order by so.transaction_date desc limit 10""", (item_code, entrepot), as_dict=True)
    return [{"commande": r.name, "client": r.customer_name, "reste": flt(r.reste, 6)} for r in rows if flt(r.reste) > TOL]


def _ailleurs(item_code, entrepot) -> list[dict]:
    rows = frappe.get_all("Bin", filters={"item_code": item_code, "warehouse": ["!=", entrepot], "actual_qty": [">", 0]},
                          fields=["warehouse", "actual_qty"], order_by="actual_qty desc")
    return [{"entrepot": r.warehouse, "qte": flt(r.actual_qty, 6)} for r in rows]


def _cases_vides(l) -> list[str]:
    cases = I._charger_cases(l)
    zones = [z for z in (l.zones or "").split(I.Z.SEPARATEUR) if z]
    codes = I._codes_zones()
    return [codes.get(z, z) for z in zones if z not in cases]


@frappe.whitelist()
def analyser_ligne(name, item_code):
    """Le dossier d'un écart : comptage, précédent, mouvements, pièces non validées, à livrer, ailleurs, cases
    vides, historique, et les phrases d'explication. Lecture seule."""
    I._acces()
    v = frappe.get_doc(I.INV, name)
    l = _ligne(v, item_code)
    fin = get_datetime(l.compte_le) if l.compte_le else now_datetime()
    precedent = _precedent(v, l)
    debut = get_datetime(precedent["date"]) if precedent else add_days(fin, -JOURS_PAR_DEFAUT)
    mouvements = _mouvements(item_code, v.entrepot, debut, fin)
    brouillons = _brouillons(item_code, v.entrepot)
    a_livrer = _a_livrer(item_code, v.entrepot)
    ailleurs = _ailleurs(item_code, v.entrepot)
    cases_vides = _cases_vides(l) if l.compte else []
    ecart = flt(l.ecart, 6) if l.compte else 0.0
    bilan = bilan_mouvements(mouvements)
    expl = expliquer(ecart, bilan, precedent, brouillons, a_livrer, ailleurs, cases_vides, str(debut)[:10])
    infos = I._infos_articles([item_code]).get(item_code) or {}
    return {"fiche": name, "entrepot": v.entrepot, "item_code": item_code, "item_name": l.item_name,
            "image": infos.get("image"), "compte": cint(l.compte), "qte_systeme": flt(l.qte_systeme, 6),
            "qte_comptee": flt(l.qte_comptee, 6) if l.compte else None, "ecart": ecart,
            "compte_par": frappe.utils.get_fullname(l.compte_par) if l.compte_par else None,
            "compte_le": str(l.compte_le)[:16] if l.compte_le else None, "cases": I._charger_cases(l),
            "commentaire": l.commentaire, "periode": {"debut": str(debut)[:16], "fin": str(fin)[:16]},
            "precedent": precedent, "mouvements": mouvements, "bilan": bilan, "brouillons": brouillons,
            "a_livrer": a_livrer, "ailleurs": ailleurs, "cases_vides": cases_vides, "historique": _historique(v, l),
            "verdict": expl["verdict"], "phrases": expl["phrases"], "voir_valeur": S.est_responsable()}
