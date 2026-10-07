"""Pièces à proposer au client — calculées depuis ce qu'il a ACHETÉ (demande utilisateur du 07/10/2026).

Pendant un appel, l'opératrice voit quelles pièces sont probablement à changer : cartouches de l'osmoseur, membrane,
lampe UV, résine d'adoucisseur, cartouches du porte-filtre.

LES RÈGLES (validées par l'utilisateur le 07/10/2026)
-----------------------------------------------------
- Osmoseur : PP, UDF, CTO tous les 6 mois ; T33, Minéral, Alcalin tous les ans ; membrane tous les 24 mois.
- Lampe UV : tous les 9 mois, SAUF si le client a l'activation automatique (Relais 24 V « Rel-24V », l'ancien capteur
  « Acc029-A », ou un osmoseur « UV Automatique »).
- Adoucisseur : résine tous les 3 ans, en proposant d'abord un test de dureté.
- Porte-filtre : selon SON historique — l'intervalle est le rythme auquel CE client rachète la cartouche (6 mois tant
  qu'il ne l'a achetée qu'une fois).
- Les revendeurs (groupes B2B de « Config Relances ») sont ignorés : ils achètent pour leur stock.
- ⚠️ SEULE LA DATE COMPTE, JAMAIS LA QUANTITÉ. Trois minéraux achetés le même jour ne valent pas trois ans : le compteur
  repart de la date d'achat, comme pour une seule cartouche.

COMMENT
-------
On lit les lignes de commande (et leurs lignes « emballées » : un pack ou un osmoseur vendu en ensemble y détaille son
contenu tel qu'il était à la vente). Chaque ligne produit des événements datés : « pièce achetée » ou « machine posée,
qui implique telles pièces ». Pour chaque pièce, le compteur part du plus récent des deux ; échéance = départ + intervalle.

⚠️ LES COMMANDES, PAS LES BL. Les ventes faites par le partenaire Economiq ont un BL de main d'œuvre seulement (les
produits sont à lui) : lire les BL ferait croire que ses clients n'achètent jamais de cartouches.
⚠️ LE GROUPE DE L'ARTICLE AUJOURD'HUI (tabItem), jamais celui recopié sur la ligne : l'arbre des groupes a été refait vers
le 16/08/2025, et 7 579 lignes de 2025 portent encore les anciens noms.

Les intervalles, l'activation et les messages se règlent dans « Config Relances » → table « Pièces à proposer » (vide =
DEFAUT_PIECES). La reconnaissance des articles, elle, est ici : elle dépend des codes du catalogue.
"""
from __future__ import annotations

import re
import statistics

import frappe
from frappe.utils import add_months, cint, date_diff, getdate, nowdate

# ── Les pièces et leurs intervalles par défaut ────────────────────────────────

FIXE = "Fixe"
HISTORIQUE = "Selon historique"

DEFAUT_PIECES = [
    {"cle": "pp", "libelle": "Cartouche PP (sédiments)", "intervalle_mois": 6, "mode": FIXE, "actif": 1, "message": ""},
    {"cle": "udf", "libelle": "Cartouche UDF (charbon en grains)", "intervalle_mois": 6, "mode": FIXE, "actif": 1, "message": ""},
    {"cle": "cto", "libelle": "Cartouche CTO (bloc charbon)", "intervalle_mois": 6, "mode": FIXE, "actif": 1, "message": ""},
    {"cle": "t33", "libelle": "Post-filtre T33", "intervalle_mois": 12, "mode": FIXE, "actif": 1, "message": ""},
    {"cle": "mineral", "libelle": "Cartouche minérale", "intervalle_mois": 12, "mode": FIXE, "actif": 1, "message": ""},
    {"cle": "alcalin", "libelle": "Cartouche alcaline", "intervalle_mois": 12, "mode": FIXE, "actif": 1, "message": ""},
    {"cle": "membrane", "libelle": "Membrane d’osmose", "intervalle_mois": 24, "mode": FIXE, "actif": 1, "message": ""},
    # Pas de rappel quand l'UV de l'osmoseur a l'activation automatique (Relais 24 V) : règle dans `calculer`.
    {"cle": "lampe_uv", "libelle": "Lampe UV", "intervalle_mois": 9, "mode": FIXE, "actif": 1, "message": ""},
    {"cle": "resine", "libelle": "Résine de l’adoucisseur", "intervalle_mois": 36, "mode": FIXE, "actif": 1,
     "message": "Proposer d’abord un test de dureté (Trousse TH) : on ne change la résine que si l’eau reste dure."},
    # Rythme d'achat du client ; l'intervalle ne sert que tant qu'il n'a acheté qu'une fois.
    {"cle": "porte_filtre", "libelle": "Cartouches du porte-filtre", "intervalle_mois": 6, "mode": HISTORIQUE, "actif": 1,
     "message": ""},
]
CLES = [p["cle"] for p in DEFAUT_PIECES]

# Regroupements d'affichage : les cartouches qu'on change ensemble se proposent ensemble.
GROUPES_AFFICHAGE = [
    (("pp", "udf", "cto"), "Pack filtre PP + UDF + CTO"),
    (("t33", "mineral", "alcalin"), None),        # libellé construit : « T33 + Minéral + Alcalin »
]
COURT = {"pp": "PP", "udf": "UDF", "cto": "CTO", "t33": "T33", "mineral": "Minéral", "alcalin": "Alcalin"}

JOURS_BIENTOT = 30
PRIORITE_STATUT = {"retard": 3, "bientot": 2, "ancien": 1, "ok": 0}

# ── Le catalogue ──────────────────────────────────────────────────────────────

GROUPES_RO = ("RO domestique avec pompe", "RO domestique sans pompe", "RO flux direct")
GROUPES_ADOUCISSEUR = ("Adoucisseurs Domestiques", "Adoucisseurs Commerciaux")
GROUPE_UV = "Filtres UV"
GROUPE_PORTE_FILTRES = "Porte-filtres"
GROUPES_CARTOUCHES_PF = ("Cartouches anti-sédiment", "Cartouches à charbon", "Cartouches anti-calcaire",
                         "Cartouches lavables", "Cartouches plissées (anti-bactériennes inf 1 micron)")
# « Porte filtre … osmoseur 10' » : des boîtiers de RECHANGE pour l'osmoseur, pas une machine.
PORTE_FILTRES_RECHANGE = {"P-F-10'-O", "P-F-10'-T"}
# L'activation automatique de l'UV (Relais 24 V « Activation Automatique UV », et son ancien capteur de débit).
ARTICLES_AUTO_UV = {"Rel-24V", "Acc029-A"}
# Le pack filtre est un article de STOCK (pas un ensemble) : son contenu ne figure nulle part, on le déplie ici.
PACKS_PREFILTRES = {"PF-10'-PP-UDF-CTO", "P-QF-10'-PP-UDF-CTO"}
RESINES = {"R-L", "RC-001X7"}

_UV_W = re.compile(r"UV-(\d+)\s*W", re.I)
_PF_10 = re.compile(r"^P-F-([SDT])-10'", re.I)
_MEMBRANE = re.compile(r"^M-\d{2,3}-")
_ETAPES = re.compile(r"^[5-8]$")


def pieces_de_l_article(code: str, groupe: str = "") -> set:
    """Pièces qu'un article REMPLACE quand on l'achète. PURE.

    Les cartouches 10" de l'osmoseur ont leur clé ; toute autre cartouche de porte-filtre (20", BIG, bobinée, plissée,
    anti-calcaire, lavable) est suivie à l'article, sous la clé « pf:<code> »."""
    c = (code or "").strip()
    g = (groupe or "").strip()
    u = c.upper()
    if c in PACKS_PREFILTRES:
        return {"pp", "udf", "cto"}
    # Packs d'entretien (P-E-…) : leur contenu est sur leurs lignes emballées. Les anciens packs désactivés sont rangés
    # dans « Cartouches anti-sédiment » et passeraient sinon pour une cartouche de porte-filtre.
    if u.startswith("P-E-"):
        return set()
    if u.startswith(("C-10'-PP-", "QF-10'-PP-")):
        return {"pp"}
    if u.startswith(("C-10'-UDF", "QF-10'-UDF", "C-10'B-UDF")):
        return {"udf"}
    if u.startswith(("C-10'-CTO", "QF-10'-CTO", "C-10'B-CTO")):
        return {"cto"}
    if u.startswith("F-T33-C"):
        return {"t33"}
    if u.startswith("F-T33-M"):
        return {"mineral"}
    if u.startswith("F-T33-A"):
        return {"alcalin"}
    if _MEMBRANE.match(c) or g.startswith("Membranes RO domestiques"):
        return {"membrane"}
    if u.startswith("L-UV-"):
        w = watts_uv(c)
        return {"lampe_uv:%s" % w} if w else {"lampe_uv"}
    if c in RESINES:
        return {"resine"}
    if g in GROUPES_CARTOUCHES_PF:
        return {"pf:" + c}
    return set()


def watts_uv(code: str):
    m = _UV_W.search(code or "")
    return int(m.group(1)) if m else None


def etapes_du_code(code: str) -> dict:
    """Ce que le CODE d'un osmoseur dit de ses étages, quand ses lignes emballées ne le disent pas. PURE.

    « AP-M-AJ-7-MA-SM » → minéral + alcalin ; « …-6-U-… » → UV ; « …-8-MAUA-… » → tout + UV automatique.
    Sans lettres après le chiffre : 7 = minéral + alcalin, 8 = minéral + alcalin + UV (AP-7-P, AP-SM-8-DF)."""
    parts = (code or "").upper().split("-")
    out = {"mineral": False, "alcalin": False, "uv": False, "auto": False}
    for i, p in enumerate(parts):
        if not _ETAPES.match(p):
            continue
        n = int(p)
        lettres = parts[i + 1] if i + 1 < len(parts) and re.fullmatch(r"[MAU]+", parts[i + 1] or "") else ""
        if lettres:
            out["mineral"] = "M" in lettres
            out["uv"] = "U" in lettres
            out["auto"] = "UA" in lettres
            out["alcalin"] = "A" in lettres.replace("UA", "U")
        elif n >= 7:
            out["mineral"] = out["alcalin"] = True
            out["uv"] = n >= 8
        return out
    return out


def machine_de_la_ligne(code: str, groupe: str, emballes: list) -> dict | None:
    """La machine qu'une ligne de commande pose chez le client, et les pièces qu'elle implique. PURE.

    `emballes` : les codes des lignes emballées de CETTE ligne (contenu de l'ensemble à la vente).
    → {"type", "pieces": set, "auto": bool} ou None."""
    c = (code or "").strip()
    g = (groupe or "").strip()
    em = [e for e in (emballes or []) if e]
    if g in GROUPES_RO:
        pieces = {"pp", "udf", "cto", "t33", "membrane"}
        code_dit = etapes_du_code(c)
        if any(e.upper().startswith("F-T33-M") for e in em) or code_dit["mineral"]:
            pieces.add("mineral")
        if any(e.upper().startswith("F-T33-A") for e in em) or code_dit["alcalin"]:
            pieces.add("alcalin")
        uv = [watts_uv(e) for e in em if e.upper().startswith("F-UV-")]
        if uv or code_dit["uv"]:
            pieces.add("lampe_uv:%s" % ((uv and uv[0]) or 6))
        auto = code_dit["auto"] or any(e in ARTICLES_AUTO_UV for e in em)
        return {"type": "osmoseur", "pieces": pieces, "auto": auto}
    if g in GROUPES_ADOUCISSEUR:
        return {"type": "adoucisseur", "pieces": {"resine"}, "auto": False}
    if g == GROUPE_UV and c.upper().startswith("F-UV-"):
        w = watts_uv(c)
        return {"type": "uv", "pieces": {"lampe_uv:%s" % w} if w else {"lampe_uv"}, "auto": False}
    if g == GROUPE_PORTE_FILTRES and c not in PORTE_FILTRES_RECHANGE:
        m = _PF_10.match(c)
        pieces = {"S": {"pp"}, "D": {"pp", "udf"}, "T": {"pp", "udf", "cto"}}.get(m.group(1).upper(), set()) if m else set()
        return {"type": "porte_filtre", "pieces": pieces, "auto": False}
    return None


# ── Le calcul (pur) ───────────────────────────────────────────────────────────

def _regle(cle: str, regles: dict) -> dict | None:
    base = cle.split(":", 1)[0]
    if base == "pf":
        base = "porte_filtre"
    r = regles.get(base)
    return r if r and cint(r.get("actif", 1)) else None


def _rythme(dates: list, defaut: int) -> tuple[int, bool]:
    """Intervalle en mois tiré des dates d'achat distinctes : médiane des écarts, bornée 1–24. → (mois, appris?)."""
    ds = sorted({getdate(d) for d in dates if d})
    if len(ds) < 2:
        return defaut, False
    ecarts = [date_diff(b, a) for a, b in zip(ds, ds[1:]) if date_diff(b, a) > 15]
    if not ecarts:
        return defaut, False
    mois = round(statistics.median(ecarts) / 30.44)
    return max(1, min(24, mois)), True


def calculer(evenements: list, regles: dict, aujourd_hui=None, libelles: dict | None = None) -> list:
    """Les pièces suivies et leur échéance. PURE.

    `evenements` : [{"date", "achat": set de clés, "machine": {"type", "pieces", "auto"} | None, "code"}].
    `regles` : {cle: {"libelle", "intervalle_mois", "mode", "actif", "message"}}.
    → [{"cle", "libelle", "depuis", "origine", "echeance", "statut", "retard_jours", "intervalle_mois", "appris", "message"}]
    """
    today = getdate(aujourd_hui or nowdate())
    libelles = libelles or {}
    achats, poses, machines = {}, {}, set()
    auto_uv = False
    for e in evenements:
        d = getdate(e["date"]) if e.get("date") else None
        if not d:
            continue
        for k in e.get("achat") or ():
            achats.setdefault(k, []).append((d, e.get("code")))
        m = e.get("machine")
        if m:
            machines.add(m["type"])
            auto_uv = auto_uv or bool(m.get("auto"))
            for k in m.get("pieces") or ():
                poses.setdefault(k, []).append((d, e.get("code")))
        if e.get("auto_uv"):
            auto_uv = True
    # Un client qui rachète membrane ou post-filtres A un osmoseur, même acheté ailleurs ou avant le logiciel.
    if achats.keys() & {"membrane", "t33", "mineral", "alcalin"}:
        machines.add("osmoseur")

    # Une lampe UV « générique » (code sans puissance) rejoint la seule lampe connue du client.
    lampes = {k for k in set(achats) | set(poses) if k.startswith("lampe_uv:")}
    if "lampe_uv" in achats and len(lampes) == 1:
        achats.setdefault(next(iter(lampes)), []).extend(achats.pop("lampe_uv"))

    out = []
    for cle in sorted(set(achats) | set(poses)):
        regle = _regle(cle, regles)
        if not regle:
            continue
        # L'activation automatique n'existe que pour l'UV de l'osmoseur (6 W) : les UV « toute la maison » tournent en
        # continu et gardent leurs 9 mois.
        if cle.startswith("lampe_uv") and auto_uv and cle in ("lampe_uv", "lampe_uv:6"):
            continue
        dernier_achat = max(achats.get(cle, []), default=None)
        derniere_pose = max(poses.get(cle, []), default=None)
        depart = max([x for x in (dernier_achat, derniere_pose) if x], key=lambda x: x[0])
        intervalle, appris = cint(regle.get("intervalle_mois")) or 6, False
        # Porte-filtre : le rythme du client. Les cartouches 10" partagées avec l'osmoseur gardent la règle de
        # l'osmoseur dès qu'il en a un ; sans osmoseur, elles suivent aussi le rythme du client.
        selon_historique = cle.startswith("pf:") or (cle in ("pp", "udf", "cto") and "osmoseur" not in machines
                                                    and "porte_filtre" in machines)
        if selon_historique:
            base = regles.get("porte_filtre") or {}
            if cint(base.get("actif", 1)) and (base.get("mode") or HISTORIQUE) == HISTORIQUE:
                intervalle, appris = _rythme([d for d, _ in achats.get(cle, [])], cint(base.get("intervalle_mois")) or 6)
        echeance = getdate(add_months(depart[0], intervalle))
        jours = date_diff(today, echeance)
        # « ancien » : en retard de plus de deux intervalles — le client a sans doute changé de fournisseur ou retiré
        # la cartouche. Montré dans le panneau, jamais proposé sur la carte d'appel (bruit).
        if jours >= 0:
            statut = "ancien" if jours > 2 * intervalle * 30.44 else "retard"
        else:
            statut = "bientot" if -jours <= JOURS_BIENTOT else "ok"
        origine = "achat" if dernier_achat and dernier_achat[0] >= depart[0] else "pose"
        if cle.startswith("pf:"):
            libelle = libelles.get(cle[3:]) or cle[3:]
        elif cle.startswith("lampe_uv:"):
            libelle = "%s %s W" % (regle.get("libelle") or "Lampe UV", cle.split(":", 1)[1])
        else:
            libelle = regle.get("libelle") or cle
        out.append({"cle": cle, "libelle": libelle, "depuis": str(depart[0]), "article_depart": depart[1] or "",
                    "origine": origine, "echeance": str(echeance), "statut": statut, "retard_jours": max(0, jours),
                    "intervalle_mois": intervalle, "appris": appris, "message": regle.get("message") or ""})
    out.sort(key=lambda p: (p["echeance"], p["cle"]))
    return out


def regrouper(pieces: list) -> list:
    """Fusionne pour l'affichage les cartouches qui tombent ensemble (PP + UDF + CTO → « Pack filtre »). PURE."""
    restantes = list(pieces)
    out = []
    for cles, libelle in GROUPES_AFFICHAGE:
        membres = [p for p in restantes if p["cle"] in cles]
        if len(membres) < 2:
            continue
        # Ensemble seulement si les échéances tombent à moins d'un mois d'écart.
        ref = min(membres, key=lambda p: p["echeance"])
        lot = [p for p in membres if abs(date_diff(p["echeance"], ref["echeance"])) <= 31]
        if len(lot) < 2:
            continue
        if libelle and {p["cle"] for p in lot} != set(cles):
            libelle = None
        nom = libelle or " + ".join(COURT.get(p["cle"], p["libelle"]) for p in sorted(lot, key=lambda p: cles.index(p["cle"])))
        pire = max(lot, key=lambda p: (PRIORITE_STATUT.get(p["statut"], 0), p["retard_jours"]))
        out.append({**ref, "cle": "+".join(p["cle"] for p in lot), "libelle": nom, "statut": pire["statut"],
                    "retard_jours": pire["retard_jours"], "depuis": min(p["depuis"] for p in lot),
                    "echeance": ref["echeance"], "membres": [p["cle"] for p in lot]})
        restantes = [p for p in restantes if p not in lot]
    out += restantes
    out.sort(key=lambda p: (p["echeance"], p["cle"]))
    return out


def texte_court(pieces: list) -> str:
    """« Pack filtre PP + UDF + CTO (retard 3 mois) · Lampe UV 6 W (dans 12 j) ». PURE."""
    bouts = []
    for p in pieces:
        if p["statut"] == "retard":
            mois = p["retard_jours"] // 30
            quand = "retard %s mois" % mois if mois >= 1 else "à changer"
        elif p["statut"] == "bientot":
            quand = "dans %s j" % max(0, date_diff(p["echeance"], nowdate()))
        else:
            continue
        bouts.append("%s (%s)" % (p["libelle"], quand))
    return " · ".join(bouts)


# ── Lecture de la base ────────────────────────────────────────────────────────

def regles() -> dict:
    """{cle: règle} : table « Pièces à proposer » de Config Relances si elle a des lignes, sinon DEFAUT_PIECES."""
    rows = []
    try:
        if frappe.db.table_exists("Config Relances Piece"):
            rows = frappe.get_all("Config Relances Piece", filters={"parent": "Config Relances", "parenttype": "Config Relances"},
                                  fields=["cle", "libelle", "intervalle_mois", "mode", "actif", "message"], order_by="idx")
    except Exception:
        rows = []
    out = {p["cle"]: dict(p) for p in DEFAUT_PIECES}
    if rows:
        out = {}
        for r in rows:
            cle = (r.get("cle") or "").strip()
            if cle:
                defaut = next((p for p in DEFAUT_PIECES if p["cle"] == cle), {})
                out[cle] = {"cle": cle, "libelle": (r.get("libelle") or "").strip() or defaut.get("libelle") or cle,
                            "intervalle_mois": cint(r.get("intervalle_mois")) or defaut.get("intervalle_mois") or 6,
                            "mode": r.get("mode") or defaut.get("mode") or FIXE, "actif": cint(r.get("actif")),
                            "message": (r.get("message") or "").strip()}
    return out


def _groupes_b2b() -> set:
    try:
        from customization_app import relances_config as RC
        return set(RC.config().get("groupes_b2b_liste") or [])
    except Exception:
        return set()


def evenements_par_client(clients: list) -> tuple[dict, dict]:
    """→ ({client: [événements]}, {code: nom d'article}). Deux requêtes pour tous les clients."""
    clients = [c for c in dict.fromkeys(clients or []) if c]
    if not clients:
        return {}, {}
    lignes = frappe.db.sql(
        """SELECT so.customer, COALESCE(so.delivery_date, so.transaction_date) AS d, soi.name AS ligne,
                  soi.item_code, soi.item_name, i.item_group
           FROM `tabSales Order Item` soi
           JOIN `tabSales Order` so ON so.name = soi.parent
           LEFT JOIN `tabItem` i ON i.name = soi.item_code
           WHERE so.docstatus = 1 AND so.status NOT IN ('Closed', 'Cancelled') AND so.customer IN %(c)s""",
        {"c": clients}, as_dict=True)
    emballes = {}
    noms = {}
    if lignes:
        for r in frappe.db.sql(
                """SELECT pi.parent_detail_docname AS ligne, pi.item_code, pi.item_name, i.item_group
                   FROM `tabPacked Item` pi
                   JOIN `tabSales Order` so ON so.name = pi.parent AND pi.parenttype = 'Sales Order'
                   LEFT JOIN `tabItem` i ON i.name = pi.item_code
                   WHERE so.docstatus = 1 AND so.status NOT IN ('Closed', 'Cancelled') AND so.customer IN %(c)s""",
                {"c": clients}, as_dict=True):
            emballes.setdefault(r.ligne, []).append(r)
            noms[r.item_code] = r.item_name
    par_client = {}
    # Machines des échéanciers : le parc posé AVANT que les ventes passent par des commandes (échéanciers saisis à la
    # main en 2022-2023, sans commande liée — ex. l'osmoseur AP-6-DF de Sami Messoud). Une machine déjà vendue par
    # commande y figure aussi : même machine, date voisine, sans effet sur le calcul.
    for m in frappe.db.sql(
            """SELECT ms.customer, msi.start_date AS d, msi.item_code, msi.item_name, i.item_group
               FROM `tabMaintenance Schedule Item` msi
               JOIN `tabMaintenance Schedule` ms ON ms.name = msi.parent AND ms.docstatus = 1
               LEFT JOIN `tabItem` i ON i.name = msi.item_code
               WHERE ms.customer IN %(c)s AND msi.start_date IS NOT NULL""", {"c": clients}, as_dict=True):
        machine = machine_de_la_ligne(m.item_code, m.item_group, [])
        if machine:
            noms[m.item_code] = m.item_name
            par_client.setdefault(m.customer, []).append(
                {"date": m.d, "achat": set(), "machine": machine, "auto_uv": False, "code": m.item_code})
    for l in lignes:
        noms[l.item_code] = l.item_name
        contenu = emballes.get(l.ligne, [])
        codes_contenu = [x.item_code for x in contenu]
        machine = machine_de_la_ligne(l.item_code, l.item_group, codes_contenu)
        # Un osmoseur emballé dans un ensemble (rare : ligne « pack » contenant la machine) compte aussi.
        if not machine:
            for x in contenu:
                machine = machine_de_la_ligne(x.item_code, x.item_group, codes_contenu)
                if machine:
                    break
        achat = set(pieces_de_l_article(l.item_code, l.item_group))
        # Le contenu d'une MACHINE (membrane, minéral, cartouches du porte-filtre neuf) est « posé avec elle » : il est
        # déjà dans `machine["pieces"]`. Le contenu d'un PACK d'entretien, lui, est un achat de pièces.
        if not machine:
            for x in contenu:
                achat |= pieces_de_l_article(x.item_code, x.item_group)
        auto = l.item_code in ARTICLES_AUTO_UV or any(c in ARTICLES_AUTO_UV for c in codes_contenu)
        if achat or machine or auto:
            par_client.setdefault(l.customer, []).append(
                {"date": l.d, "achat": achat, "machine": machine, "auto_uv": auto, "code": l.item_code})
    return par_client, noms


def pour_clients(clients: list, aujourd_hui=None) -> dict:
    """{client: {"revendeur": bool, "pieces": [...regroupées], "detail": [...], "texte": str}} — en lot."""
    clients = [c for c in dict.fromkeys(clients or []) if c]
    if not clients:
        return {}
    b2b = _groupes_b2b()
    groupes = dict(frappe.db.sql("SELECT name, customer_group FROM `tabCustomer` WHERE name IN %(c)s", {"c": clients}))
    regs = regles()
    a_calculer = [c for c in clients if groupes.get(c) not in b2b]
    evts, noms = evenements_par_client(a_calculer)
    out = {}
    for c in clients:
        if c not in a_calculer:
            out[c] = {"revendeur": True, "groupe": groupes.get(c), "pieces": [], "detail": [], "texte": ""}
            continue
        detail = calculer(evts.get(c, []), regs, aujourd_hui, noms)
        pieces = regrouper(detail)
        out[c] = {"revendeur": False, "groupe": groupes.get(c), "pieces": pieces, "detail": detail,
                  "texte": texte_court(pieces)}
    return out


@frappe.whitelist()
def pieces_par_defaut() -> list:
    """Les règles d'origine, pour le bouton « ↩️ Pièces par défaut » de Config Relances."""
    frappe.only_for(["System Manager", "Maintenance Manager", "Sales Manager"])
    return [dict(p) for p in DEFAUT_PIECES]
