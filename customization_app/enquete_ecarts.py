"""Enquête sur les écarts d'une vérification de stock d'employé (29/10/2026).

`stock_entrepots._analyser` dit ce que les MOUVEMENTS du stock montrent (« aucune sortie enregistrée :
consommé sans document »). L'enquête va plus loin : elle regarde ce que l'EMPLOYÉ a fait entre la
vérification précédente et celle-ci, et dit si chaque écart s'explique.

Le dossier (tout vient d'ERPNext, rien n'est inventé) :
  - ses interventions (Tâches de travail) : type, client, commande, passage GPS, articles de la commande ;
  - ses BL : ceux livrés par lui, et ceux dont une ligne sort d'un AUTRE stock que le sien (il a pris la
    pièce dans son véhicule, le BL l'a sortie du Magasin : le Magasin manque, le véhicule est en trop) ;
  - les BROUILLONS (BL, transferts) qui le concernent : une sortie saisie mais jamais validée ;
  - les transferts validés vers / depuis son stock ;
  - la BALANCE DES KITS : un pack filtre (PF-10'-PP-UDF-CTO) est trois cartouches ; +1 pack et −1 de
    chaque cartouche, c'est le même objet compté sous deux noms — écart expliqué, valeur nulle.

Deux couches : les RÈGLES (ce module, pures, testées) posent un verdict par écart — explicable,
probable, inexpliqué — avec le fait qui le justifie ; puis l'IA (OpenAI via aquaworld_ia, si l'app est là
et qu'une clé existe) relit le dossier complet et rédige, par écart, l'explication et l'action conseillée,
plus une conclusion. L'IA ne décide pas seule : ses verdicts restent à côté des faits qui les portent.
"""
from __future__ import annotations

import json
import re

from frappe.utils import cint, flt

EXPLICABLE, PROBABLE, INEXPLIQUE = "Explicable", "Probable", "Inexpliqué"
TOL = 1e-6

# Interventions où des consommables partent sans forcément de ligne sur la commande.
TYPES_CONSOMMABLES = ("Entretien", "Réparation", "Installation")
# Familles d'articles consommées en entretien / installation (préfixe de code).
PREFIXES_CONSOMMABLES = ("C-", "PF-", "M-", "F-", "Acc", "Ad-")


# ── Règles pures ─────────────────────────────────────────────────────────────

def composition_pack(item_code: str, existants) -> dict[str, float] | None:
    """La composition d'un pack filtre d'après son code, quand ni BOM ni Product Bundle ne la donnent.

    « PF-10'-PP-UDF-CTO » = une cartouche PP 10' (5 microns par défaut), une UDF 10', une CTO 10'.
    `existants` : les codes d'articles connus (pour choisir la variante PP qui existe). None si le code
    n'est pas un pack ou si un composant est introuvable : on n'invente pas de composition.
    """
    m = re.match(r"^PF-([^-]+)-(.+)$", item_code or "")
    if not m:
        return None
    taille, reste = m.group(1), m.group(2)
    existants = set(existants or ())
    out = {}
    for tok in reste.split("-"):
        candidats = [f"C-{taille}-{tok}"]
        if tok.upper() == "PP":
            candidats = [f"C-{taille}-PP-5m", f"C-{taille}-PP-1m", f"C-{taille}-PP-20m", f"C-{taille}-PP-25m"]
        code = next((c for c in candidats if c in existants), None)
        if not code:
            return None
        out[code] = out.get(code, 0.0) + 1.0
    return out or None


def balance_kits(ecarts: dict[str, float], compositions: dict[str, dict[str, float]]) -> dict:
    """Compense l'écart d'un kit par les écarts opposés de ses composants.

    ecarts       : {article: écart} (≠ 0)
    compositions : {kit: {composant: qté par kit}}
    -> {"explique": {article: qté expliquée (signée)}, "residuel": {article: écart restant},
        "kits": [{"kit", "n", "composants": {...}}]} — `n` kits compensés (n > 0 : kit compté en plus,
    composants en moins ; n < 0 : l'inverse).
    """
    residuel = {c: flt(e) for c, e in ecarts.items() if abs(flt(e)) > TOL}
    explique: dict[str, float] = {}
    kits = []
    for kit, compo in compositions.items():
        ek = residuel.get(kit, 0.0)
        if abs(ek) <= TOL or not compo:
            continue
        signe = 1 if ek > 0 else -1
        # combien de kits les composants peuvent absorber, chacun dans le sens opposé au kit
        n = abs(ek)
        for comp, q in compo.items():
            ec = residuel.get(comp, 0.0)
            if ec * signe >= -TOL:          # composant dans le même sens (ou nul) : rien à compenser
                n = 0
                break
            n = min(n, abs(ec) / flt(q))
        n = int(n + TOL)
        if n <= 0:
            continue
        residuel[kit] = round(ek - signe * n, 6)
        explique[kit] = explique.get(kit, 0.0) + signe * n
        detail = {}
        for comp, q in compo.items():
            delta = signe * n * flt(q)      # le composant va dans le sens opposé
            residuel[comp] = round(residuel[comp] + delta, 6)
            explique[comp] = explique.get(comp, 0.0) - delta
            detail[comp] = -delta
        kits.append({"kit": kit, "n": signe * n, "composants": detail})
    residuel = {c: e for c, e in residuel.items() if abs(e) > TOL}
    return {"explique": explique, "residuel": residuel, "kits": kits}


def bls_mauvais_stock(bls: list[dict], entrepot: str) -> list[dict]:
    """Les lignes de BL livrés par l'employé qui sont sorties d'un AUTRE stock que le sien.
    bls : [{name, date, client, docstatus, lignes: [{item_code, qty, warehouse}]}]
    -> [{bl, date, client, item_code, qty, warehouse}]"""
    out = []
    for bl in bls:
        if cint(bl.get("docstatus")) != 1:
            continue
        for l in bl.get("lignes") or []:
            if l.get("warehouse") and l["warehouse"] != entrepot and flt(l.get("qty")) > 0:
                out.append({"bl": bl["name"], "date": bl.get("date"), "client": bl.get("client"),
                            "item_code": l["item_code"], "qty": flt(l["qty"]), "warehouse": l["warehouse"]})
    return out


def _famille(code: str) -> str:
    return (code or "").split("-")[0].upper()


def interventions_candidates(item_code: str, ecart: float, interventions: list[dict]) -> list[dict]:
    """Les interventions de la période qui peuvent expliquer un MANQUE de cet article : la commande liée
    le contient (sans qu'il soit sorti du véhicule), ou c'est un entretien / une réparation / une
    installation et l'article est un consommable de la même famille que ce qu'on y pose.
    interventions : [{name, date, type, client, commande, gps, articles: [{item_code, qty}]}]"""
    if flt(ecart) >= 0:
        return []
    out = []
    for t in interventions:
        if t.get("statut") == "Cancelled":
            continue
        codes = {a.get("item_code") for a in (t.get("articles") or [])}
        if item_code in codes:
            out.append(dict(t, raison="article sur la commande"))
        elif t.get("type") in TYPES_CONSOMMABLES and any((item_code or "").startswith(p) for p in PREFIXES_CONSOMMABLES):
            out.append(dict(t, raison="consommable d'%s" % t["type"].lower()))
    return out


def verdicts_regles(ecarts: list[dict], balance: dict, mauvais_stock: list[dict], brouillons: list[dict],
                    interventions: list[dict]) -> dict[str, dict]:
    """Par article en écart : {"verdict", "texte", "faits": [...]} d'après les règles, avant l'IA.
    ecarts : [{item_code, item_name, ecart}]"""
    out = {}
    kits_par_article: dict[str, list] = {}
    for k in balance.get("kits") or []:
        kits_par_article.setdefault(k["kit"], []).append(k)
        for c in k["composants"]:
            kits_par_article.setdefault(c, []).append(k)
    for e in ecarts:
        code, ecart = e["item_code"], flt(e["ecart"])
        nom = e.get("item_name") or code
        faits, verdict, texte = [], INEXPLIQUE, None
        reste = flt(balance.get("residuel", {}).get(code, 0.0)) if code in (balance.get("explique") or {}) else ecart
        for k in kits_par_article.get(code, []):
            n = k["n"]
            if code == k["kit"]:
                faits.append("%+d kit(s) contre %s" % (n, ", ".join("%+g %s" % (q, c) for c, q in k["composants"].items())))
            else:
                faits.append("%+g compensé par %+d kit(s) %s" % (k["composants"][code], n, k["kit"]))
        if faits and abs(reste) <= TOL:
            verdict, texte = EXPLICABLE, "Balance des kits : même objet compté sous deux noms — " + " ; ".join(faits)
        ms = [m for m in mauvais_stock if m["item_code"] == code]
        if ms and verdict != EXPLICABLE:
            q = sum(m["qty"] for m in ms)
            faits.append("sorti d'un autre stock sur %s" % ", ".join("%s (%s, %g de %s)" % (m["bl"], m.get("date"), m["qty"], m["warehouse"]) for m in ms))
            if reste < 0 and abs(abs(reste) - q) <= TOL:
                verdict, texte = EXPLICABLE, "Le BL a sorti la pièce d'un autre stock : " + faits[-1]
            elif reste < 0:
                verdict, texte = PROBABLE, "Une partie sortie d'un autre stock : " + faits[-1]
        br = [b for b in brouillons if code in {l.get("item_code") for l in b.get("lignes") or []}]
        if br and verdict == INEXPLIQUE:
            faits.append("brouillon non validé : " + ", ".join("%s (%s)" % (b["name"], b.get("type")) for b in br))
            verdict, texte = PROBABLE, "Sortie saisie mais jamais validée — " + faits[-1]
        if verdict == INEXPLIQUE:
            cand = interventions_candidates(code, reste, interventions)
            if cand:
                faits.append("interventions : " + ", ".join("%s %s %s %s (%s)" % (t.get("name"), t.get("date"), t.get("type"), t.get("client") or "", t["raison"]) for t in cand[:4]))
                verdict = PROBABLE if any(t["raison"] == "article sur la commande" for t in cand) else INEXPLIQUE
                texte = ("Probablement consommé en intervention sans document : " if verdict == PROBABLE
                         else "Peut-être consommé en intervention, sans preuve : ") + faits[-1]
        if texte is None:
            texte = "Aucune action de l'employé ne l'explique sur la période" if reste < 0 else "Entrée sans document, aucune action ne l'explique"
        out[code] = {"verdict": verdict, "texte": texte, "faits": faits, "nom": nom, "ecart": ecart, "residuel": reste}
    return out


def synthese(verdicts: dict[str, dict]) -> str:
    n = {EXPLICABLE: 0, PROBABLE: 0, INEXPLIQUE: 0}
    for v in verdicts.values():
        n[v["verdict"]] = n.get(v["verdict"], 0) + 1
    parts = [f"{k} {lib}" for lib, k in ((EXPLICABLE.lower() + "(s)", n[EXPLICABLE]), (PROBABLE.lower() + "(s)", n[PROBABLE]),
                                        (INEXPLIQUE.lower() + "(s)", n[INEXPLIQUE])) if k]
    return ", ".join(parts)


# ── Dossier (données ERPNext) ────────────────────────────────────────────────

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, getdate, now_datetime

FONCTIONNALITE_IA = "Enquête écarts stock"   # option du Select « fonctionnalité » du Journal IA (aquaworld_ia)


def _periode(v):
    """Du dernier point d'appui (vérification terminée ou annulée précédente, ou dernier rapprochement du
    stock) jusqu'à la clôture de cette fiche — même logique que stock_entrepots._analyser."""
    from customization_app import stock_entrepots as S
    fin = v.termine_le or now_datetime()
    prec = frappe.get_all(S.VERIF, filters={"entrepot": v.entrepot, "statut": ["in", [S.TERMINEE, S.ANNULEE]], "name": ["!=", v.name],
                                            "termine_le": ["<", fin]}, fields=["name", "termine_le"], order_by="termine_le desc", limit=1)
    depuis = prec[0].termine_le if prec else get_datetime(add_days(getdate(fin), -30))
    rappro = frappe.db.sql("""SELECT MAX(TIMESTAMP(sr.posting_date, sr.posting_time)) FROM `tabStock Reconciliation` sr
        JOIN `tabStock Reconciliation Item` i ON i.parent = sr.name
        WHERE sr.docstatus = 1 AND i.warehouse = %s AND sr.name != IFNULL(%s, '') AND TIMESTAMP(sr.posting_date, sr.posting_time) < %s""",
        (v.entrepot, v.rapprochement, fin))[0][0]
    if rappro and get_datetime(rappro) > get_datetime(depuis):
        depuis = get_datetime(rappro)
    return get_datetime(depuis), get_datetime(fin), (prec[0].name if prec else None)


def _interventions(employe: str, depuis, jusqu_a) -> list[dict]:
    if not employe:
        return []
    taches = frappe.get_all("Tache de travail", filters={"custom_choix_du_staff": employe, "starts_on": ["between", [depuis, jusqu_a]]},
                            fields=["name", "starts_on", "custom_type_dintervention", "custom_client", "nom_client", "commande_client",
                                    "status", "custom_gps_statut", "machine_reparation"], order_by="starts_on")
    commandes = list({t.commande_client for t in taches if t.commande_client})
    articles: dict[str, list] = {}
    if commandes:
        for r in frappe.get_all("Sales Order Item", filters={"parent": ["in", commandes]},
                                fields=["parent", "item_code", "item_name", "qty", "delivered_qty", "warehouse"], order_by="parent, idx"):
            articles.setdefault(r.parent, []).append({"item_code": r.item_code, "item_name": r.item_name, "qty": flt(r.qty),
                                                      "livre": flt(r.delivered_qty), "warehouse": r.warehouse})
    return [{"name": t.name, "date": str(getdate(t.starts_on)) if t.starts_on else None, "type": t.custom_type_dintervention,
             "client": t.nom_client or t.custom_client, "commande": t.commande_client, "statut": t.status, "gps": t.custom_gps_statut,
             "machine": t.machine_reparation, "articles": articles.get(t.commande_client, [])} for t in taches]


def _bls(employe: str, entrepot: str, depuis, jusqu_a) -> list[dict]:
    """BL livrés par l'employé OU dont une ligne touche son stock, validés et brouillons, sur la période."""
    noms = set(frappe.get_all("Delivery Note", filters={"custom_livré_par": employe or "", "docstatus": ["in", [0, 1]],
                                                        "posting_date": ["between", [getdate(depuis), getdate(jusqu_a)]]}, pluck="name"))
    noms |= set(frappe.db.sql_list("""SELECT DISTINCT dn.name FROM `tabDelivery Note` dn JOIN `tabDelivery Note Item` i ON i.parent = dn.name
        WHERE i.warehouse = %s AND dn.docstatus IN (0, 1) AND dn.posting_date BETWEEN %s AND %s""", (entrepot, getdate(depuis), getdate(jusqu_a))))
    if not noms:
        return []
    entetes = {d.name: d for d in frappe.get_all("Delivery Note", filters={"name": ["in", list(noms)]},
                                                 fields=["name", "posting_date", "customer_name", "docstatus", "is_return", "custom_livré_par"])}
    lignes: dict[str, list] = {}
    for r in frappe.get_all("Delivery Note Item", filters={"parent": ["in", list(noms)]}, fields=["parent", "item_code", "qty", "warehouse"], order_by="parent, idx"):
        lignes.setdefault(r.parent, []).append({"item_code": r.item_code, "qty": flt(r.qty), "warehouse": r.warehouse})
    return [{"name": n, "date": str(d.posting_date), "client": d.customer_name, "docstatus": d.docstatus, "retour": cint(d.is_return),
             "livre_par_lui": d.get("custom_livré_par") == employe, "lignes": lignes.get(n, [])}
            for n, d in sorted(entetes.items(), key=lambda x: str(x[1].posting_date))]


def _transferts(entrepot: str, depuis, jusqu_a) -> list[dict]:
    rows = frappe.db.sql("""SELECT s.name, s.posting_date, s.docstatus, s.purpose, d.item_code, d.qty, d.s_warehouse, d.t_warehouse
        FROM `tabStock Entry` s JOIN `tabStock Entry Detail` d ON d.parent = s.name
        WHERE s.docstatus IN (0, 1) AND (d.s_warehouse = %(w)s OR d.t_warehouse = %(w)s) AND s.posting_date BETWEEN %(d)s AND %(j)s
        ORDER BY s.posting_date, s.name, d.idx""", {"w": entrepot, "d": getdate(depuis), "j": getdate(jusqu_a)}, as_dict=True)
    out: dict[str, dict] = {}
    for r in rows:
        e = out.setdefault(r.name, {"name": r.name, "date": str(r.posting_date), "docstatus": r.docstatus, "type": "Stock Entry", "lignes": []})
        e["lignes"].append({"item_code": r.item_code, "qty": flt(r.qty) * (-1 if r.s_warehouse == entrepot else 1),
                            "de": r.s_warehouse, "vers": r.t_warehouse})
    return list(out.values())


def _compositions(codes: list[str]) -> dict[str, dict[str, float]]:
    """{kit: {composant: qté}} pour les articles en écart : BOM active par défaut, sinon Product Bundle,
    sinon le code d'un pack filtre (composition_pack). Un kit sans composition connue est ignoré."""
    out: dict[str, dict[str, float]] = {}
    if not codes:
        return out
    for b in frappe.get_all("BOM", filters={"item": ["in", codes], "is_active": 1, "docstatus": 1}, fields=["name", "item", "quantity", "is_default"],
                            order_by="is_default desc, modified desc"):
        if b.item in out:
            continue
        compo = {r.item_code: flt(r.qty) / (flt(b.quantity) or 1) for r in frappe.get_all("BOM Item", filters={"parent": b.name}, fields=["item_code", "qty"])}
        if compo:
            out[b.item] = compo
    for r in frappe.db.sql("""SELECT pb.new_item_code AS kit, i.item_code, i.qty FROM `tabProduct Bundle Item` i
        JOIN `tabProduct Bundle` pb ON pb.name = i.parent WHERE pb.disabled = 0 AND pb.new_item_code IN %s""", (tuple(codes),), as_dict=True):
        if r.kit not in out and flt(r.qty) > 0:
            out.setdefault(r.kit, {})[r.item_code] = flt(r.qty)
    packs = [c for c in codes if c not in out and str(c).startswith("PF-")]
    if packs:
        existants = set(frappe.get_all("Item", filters={"name": ["like", "C-%"], "disabled": 0}, pluck="name"))
        for c in packs:
            compo = composition_pack(c, existants)
            if compo:
                out[c] = compo
    return out


def dossier(v) -> dict:
    """Tout ce que l'enquête regarde, prêt pour les règles et pour l'IA."""
    depuis, jusqu_a, precedente = _periode(v)
    lignes = [l for l in v.lignes if l.compte and abs(flt(l.ecart, 6)) > TOL]
    ecarts = [{"item_code": l.item_code, "item_name": l.item_name, "ecart": flt(l.ecart, 6), "qte_systeme": flt(l.qte_systeme, 6),
               "qte_comptee": flt(l.qte_comptee, 6), "valeur": flt(l.valeur_ecart, 3), "mouvements": l.analyse} for l in lignes]
    emp = frappe.db.get_value("Employee", v.employe, ["name", "employee_name", "user_id"], as_dict=True) if v.employe else None
    employe = emp.name if emp else None
    bls = _bls(employe, v.entrepot, depuis, jusqu_a)
    transferts = _transferts(v.entrepot, depuis, jusqu_a)
    brouillons = ([dict(b, type="Delivery Note") for b in bls if cint(b["docstatus"]) == 0]
                  + [t for t in transferts if cint(t["docstatus"]) == 0])
    interventions = _interventions(employe, depuis, jusqu_a)
    codes = [e["item_code"] for e in ecarts]
    balance = balance_kits({e["item_code"]: e["ecart"] for e in ecarts}, _compositions(codes))
    mauvais = bls_mauvais_stock([b for b in bls if b["livre_par_lui"]], v.entrepot)
    verdicts = verdicts_regles(ecarts, balance, mauvais, brouillons, interventions)
    return {"fiche": v.name, "entrepot": v.entrepot, "employe": emp.employee_name if emp else None,
            "periode": {"depuis": str(depuis)[:16], "jusqu_a": str(jusqu_a)[:16], "precedente": precedente},
            "ecarts": ecarts, "interventions": interventions,
            "bls": [b for b in bls if cint(b["docstatus"]) == 1], "mauvais_stock": mauvais, "brouillons": brouillons,
            "transferts": [t for t in transferts if cint(t["docstatus"]) == 1], "balance": balance, "verdicts": verdicts,
            "synthese": synthese(verdicts)}


# ── IA ───────────────────────────────────────────────────────────────────────

SYSTEM_IA = """Tu es le contrôleur de gestion d'Aqua World, installateur d'osmoseurs et de traitement d'eau en Tunisie.
Un employé a un stock dans son véhicule. À chaque vérification hebdomadaire, on compare le stock compté au stock système.
Tu reçois le dossier d'une vérification : les écarts (négatif = il manque, positif = en trop), ce que les mouvements du stock montrent,
et TOUT ce que l'employé a fait sur la période : interventions (type, client, commande, articles de la commande, passage GPS),
bons de livraison (BL) validés — avec, pour chaque ligne, l'entrepôt d'où elle est sortie —, brouillons jamais validés, transferts,
et la balance des kits (un pack filtre est composé de cartouches : +1 pack et −1 de chaque cartouche = même objet compté sous deux noms).
Les règles ont déjà posé un verdict par écart avec les faits qui le portent : tu peux le confirmer ou le nuancer, en t'appuyant UNIQUEMENT
sur les faits du dossier. N'invente ni intervention, ni document. Réponds en français, concis, en JSON strict :
{"conclusion": "2 à 4 phrases pour le responsable magasin : ce qui s'explique, ce qui ne s'explique pas, ce qu'il faut faire",
 "ecarts": [{"item_code": "...", "verdict": "Explicable|Probable|Inexpliqué", "explication": "1 à 2 phrases, avec les documents ou interventions cités",
             "action": "régulariser par BL sur la commande X | enregistrer le transfert | demander à l'employé | rien à faire | ..."}]}"""


def _ia(doss: dict, doc=None) -> dict | None:
    """Relecture du dossier par l'IA (aquaworld_ia → OpenAI). None si l'app ou la clé manquent, ou si
    la réponse est inutilisable : l'enquête reste alors sur les règles."""
    try:
        from aquaworld_ia.ia.chat import chat_json
        from aquaworld_ia.ia.journal import verifier_plafond
    except Exception:
        return None
    compact = {k: doss[k] for k in ("employe", "periode", "ecarts", "interventions", "bls", "mauvais_stock", "brouillons", "transferts", "balance")}
    compact["verdicts_regles"] = {c: {"verdict": x["verdict"], "faits": x["faits"]} for c, x in doss["verdicts"].items()}
    try:
        verifier_plafond(0.05)
        rep = chat_json(SYSTEM_IA, json.dumps(compact, ensure_ascii=False, default=str), fonctionnalite=FONCTIONNALITE_IA, doc=doc)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Enquête écarts : appel IA")
        return None
    if not isinstance(rep, dict):
        return None
    ecarts = {}
    for e in rep.get("ecarts") or []:
        if isinstance(e, dict) and e.get("item_code"):
            verdict = str(e.get("verdict") or "").strip().capitalize()
            ecarts[e["item_code"]] = {"verdict": verdict if verdict in (EXPLICABLE, PROBABLE, INEXPLIQUE) else None,
                                      "explication": (e.get("explication") or "")[:600], "action": (e.get("action") or "")[:300]}
    return {"conclusion": (rep.get("conclusion") or "")[:1500], "ecarts": ecarts}


# ── Enregistrement et accès ─────────────────────────────────────────────────

def enqueter_doc(v, ia: bool = True) -> dict:
    from customization_app import stock_entrepots as S
    doss = dossier(v)
    rep = _ia(doss, doc=v) if ia and doss["ecarts"] else None
    for l in v.lignes:
        r = doss["verdicts"].get(l.item_code)
        if not r:
            l.verdict, l.enquete = None, None
            continue
        ia_l = (rep or {}).get("ecarts", {}).get(l.item_code) or {}
        l.verdict = ia_l.get("verdict") or r["verdict"]
        texte = ia_l.get("explication") or r["texte"]
        if ia_l.get("action"):
            texte += " → " + ia_l["action"]
        l.enquete = texte[:900]
    verdicts_finaux = {c: {"verdict": l.verdict} for c in doss["verdicts"] for l in v.lignes if l.item_code == c}
    v.conclusion_ia = (rep or {}).get("conclusion") or None
    v.enquete = json.dumps(dict(doss, ia=bool(rep), synthese=synthese(verdicts_finaux)), ensure_ascii=False, default=str)
    v.enquete_le, v.enquete_par = now_datetime(), frappe.session.user
    S._sauver(v)
    return resume_enquete(v)


def resume_enquete(v) -> dict | None:
    """Ce que l'écran montre : conclusion, synthèse, dossier (interventions, BL, brouillons, transferts, balance)."""
    if not v.get("enquete"):
        return None
    try:
        d = json.loads(v.enquete)
    except Exception:
        return None
    return {"le": str(v.enquete_le or "")[:16], "par": frappe.utils.get_fullname(v.enquete_par) if v.enquete_par else None,
            "ia": bool(d.get("ia")), "conclusion": v.get("conclusion_ia"), "synthese": d.get("synthese"), "periode": d.get("periode"),
            "interventions": d.get("interventions") or [], "bls": d.get("bls") or [], "mauvais_stock": d.get("mauvais_stock") or [],
            "brouillons": [{"name": b["name"], "type": b.get("type"), "date": b.get("date")} for b in d.get("brouillons") or []],
            "transferts": d.get("transferts") or [], "kits": (d.get("balance") or {}).get("kits") or []}


@frappe.whitelist(methods=["POST"])
def enqueter(name, ia=1):
    """Bouton « Enquêter » du responsable magasin sur une fiche terminée (relance possible)."""
    from customization_app import stock_entrepots as S
    S._responsable()
    v = frappe.get_doc(S.VERIF, name)
    if v.statut != S.TERMINEE:
        frappe.throw(_("L’enquête porte sur une vérification terminée ({0}).").format(v.statut))
    return enqueter_doc(v, ia=bool(cint(ia)))


def enqueter_en_tache(name: str):
    """Tâche de fond lancée à la clôture d'une fiche avec écarts (après commit)."""
    from customization_app import stock_entrepots as S
    v = frappe.get_doc(S.VERIF, name)
    if v.statut != S.TERMINEE or not cint(v.nb_ecarts):
        return
    enqueter_doc(v, ia=True)
    frappe.db.commit()
