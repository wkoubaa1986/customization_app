"""Inventaire du Magasin — bouton de la page « Stock par entrepôt » (demande du 27/10/2026).

Un inventaire = une fiche « Inventaire Magasin » sur l'entrepôt Magasin du réglage, avec une ligne par article
suivi en stock au Magasin (une ligne de Bin au Magasin, ou rangé dans une zone), photo et description lues
sur l'article. Chaque ligne se compte PAR EMPLACEMENT : une case par zone de l'article (« Magasin - A1 »,
« Magasin - B2 »…), la somme des cases est la quantité comptée, comparée à la quantité système. Seules les zones
de l'ESPACE du Magasin (réglage `espace_inventaire`, « Magasin » par défaut) font des cases : les zones du Hall
appartiennent à l'entrepôt Hall, qui a son propre stock. Un article sans zone dans cet espace a une seule case
« Non rangé » ; une case peut être ajoutée pour un emplacement où l'article a été trouvé : la zone est AUSSITÔT
ajoutée aux emplacements de l'article (fiche article), et retirable tant que l'inventaire est ouvert.

Le comptage s'enregistre au fil de l'eau (lignes modifiées seulement : plusieurs personnes peuvent compter en
même temps). La quantité système d'une ligne est relue AU MOMENT où elle est comptée : l'écart mesure ce
qui manque ou dépasse à cet instant. À la clôture, l'écart de chaque ligne comptée est appliqué au stock du
moment (rapproché = système actuel + écart) dans UNE écriture « Stock Reconciliation » sur le Magasin, pour
les lignes dont l'écart n'est pas nul. Les articles non comptés ne bougent pas.

Accès : rôle « Inventaire Magasin » (koubaawassim, Nejib Koubaa, Hedi ibidhii — responsable principal du
magasin — par patch) ou System Manager.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, nowdate, strip_html

from customization_app import stock_entrepots as S
from customization_app import zones_magasin as Z

INV = "Inventaire Magasin"
ROLE = "Inventaire Magasin"
ROLES = {ROLE, "System Manager"}
EN_COURS, TERMINE, ANNULE = "En cours", "Terminé", "Annulé"
SANS_ZONE = "__sans__"          # la case unique d'un article sans zone
LIMITE_HISTORIQUE = 10
LONGUEUR_DESCRIPTION = 160
ESPACE_DEFAUT = "Magasin"


# ── Accès ─────────────────────────────────────────────────────────────────────

def est_autorise(user: str | None = None) -> bool:
    return bool(ROLES & set(frappe.get_roles(user)))


def _acces():
    if not est_autorise():
        frappe.throw(_("L’inventaire du Magasin est réservé au rôle « Inventaire Magasin »."), frappe.PermissionError)


# ── Calcul pur ────────────────────────────────────────────────────────────────

def cases_valides(cases, zones_connues: set[str] | None = None) -> dict[str, float]:
    """{zone: quantité} des cases renseignées (vides ignorées, jamais négatif). `zones_connues` : les zones
    acceptées (SANS_ZONE toujours)."""
    out = {}
    for zone, q in (cases or {}).items():
        if q in (None, "") or not zone:
            continue
        if zones_connues is not None and zone != SANS_ZONE and zone not in zones_connues:
            continue
        out[zone] = max(flt(q, 6), 0)
    return out


def sommer_cases(cases: dict[str, float]) -> float:
    return flt(sum(flt(q) for q in cases.values()), 6)


def quantite_rapprochee(qte_systeme_comptage: float, qte_comptee: float, qte_systeme_actuelle: float) -> float:
    """L'écart mesuré au comptage (compté − système d'alors) s'applique au stock du moment ; jamais négatif."""
    return max(flt(qte_systeme_actuelle + (qte_comptee - qte_systeme_comptage), 6), 0)


def _charger_cases(l) -> dict[str, float]:
    try:
        return {k: flt(v, 6) for k, v in (json.loads(l.comptes) if l.comptes else {}).items()}
    except (TypeError, ValueError):
        return {}


# ── Lecture ───────────────────────────────────────────────────────────────────

def espace() -> str | None:
    """L'espace dont les zones font les cases : réglage, sinon l'espace nommé « Magasin » s'il existe."""
    e = frappe.db.get_single_value(S.CONFIG, "espace_inventaire")
    if e and frappe.db.get_value(Z.DOCTYPE_ZONE, e, "est_espace"):
        return e
    return ESPACE_DEFAUT if frappe.db.get_value(Z.DOCTYPE_ZONE, ESPACE_DEFAUT, "est_espace") else None


def zones_de_l_espace(esp: str | None = None) -> list[dict]:
    """L'espace lui-même puis ses zones (« Magasin », « Magasin - A1 »…) ; sans espace connu, toutes les zones."""
    esp = esp or espace()
    filtres = {"name": ["in", [esp] + frappe.get_all(Z.DOCTYPE_ZONE, filters={"espace": esp}, pluck="name")]} if esp else {}
    return frappe.get_all(Z.DOCTYPE_ZONE, filters=filtres, fields=["name", "espace", "code", "est_espace"],
                          order_by="est_espace desc, espace, code")


def _zones_par_article() -> dict[str, list[str]]:
    """Les zones de chaque article DANS l'espace de l'inventaire (les zones du Hall sont ignorées)."""
    connues = {z.name for z in zones_de_l_espace()}
    out = {}
    for r in frappe.get_all(Z.DOCTYPE_LIEN, filters={"parenttype": "Item", "zone": ["in", list(connues) or [""]]},
                            fields=["parent", "zone"], order_by="parent, idx"):
        out.setdefault(r.parent, []).append(r.zone)
    return out


def _articles_magasin(magasin: str) -> list[dict]:
    """Les articles suivis en stock au Magasin : actifs, suivis en stock, avec une ligne de Bin au Magasin
    (même à zéro) ou rangés dans une zone de l'espace du Magasin. Quantité et taux du moment."""
    zones = _zones_par_article()
    rows = frappe.db.sql("""select i.name item_code, i.item_name, coalesce(b.actual_qty, 0) actual_qty,
                                   coalesce(b.valuation_rate, 0) valuation_rate, i.valuation_rate taux_article
                            from tabItem i
                            left join tabBin b on b.item_code = i.name and b.warehouse = %(m)s
                            where i.disabled = 0 and i.is_stock_item = 1 and i.has_variants = 0
                              and (b.name is not null or i.name in %(ranges)s)
                            order by i.item_name, i.name""", {"m": magasin, "ranges": list(zones) or [""]}, as_dict=True)
    return [{"item_code": r.item_code, "item_name": r.item_name, "qte_systeme": flt(r.actual_qty, 6),
             "taux": flt(r.valuation_rate, 6) or flt(r.taux_article, 6),
             "zones": Z.SEPARATEUR.join(zones.get(r.item_code, [])) or None}
            for r in rows]


def _infos_articles(codes: list[str]) -> dict[str, dict]:
    if not codes:
        return {}
    return {i.name: i for i in frappe.get_all("Item", filters={"name": ["in", codes]},
                                              fields=["name", "image", "description", "item_group", "stock_uom"])}


def _zones_choix() -> list[dict]:
    return [{"name": z.name, "espace": z.espace, "code": z.code, "est_espace": cint(z.est_espace)}
            for z in zones_de_l_espace()]


def _resume(v) -> dict:
    return {"name": v.name, "entrepot": v.entrepot, "date": str(v.date), "statut": v.statut,
            "ouvert_par": frappe.utils.get_fullname(v.ouvert_par) if v.ouvert_par else None,
            "nb_lignes": cint(v.nb_lignes), "nb_comptes": cint(v.nb_comptes), "nb_ecarts": cint(v.nb_ecarts),
            "valeur_ecarts": flt(v.valeur_ecarts, 3), "valeur_manquants": flt(v.valeur_manquants, 3),
            "termine_le": str(v.termine_le) if v.termine_le else None,
            "termine_par": frappe.utils.get_fullname(v.termine_par) if v.termine_par else None,
            "rapprochement": v.rapprochement, "note": v.note, "nb_bouges": cint(v.nb_bouges),
            "nb_zones_ajoutees": cint(v.nb_zones_ajoutees), "nb_sans_taux": cint(v.nb_sans_taux)}


def en_cours():
    return frappe.db.get_value(INV, {"statut": EN_COURS}, "name", order_by="creation desc")


@frappe.whitelist()
def inventaires_etat():
    """L'inventaire ouvert (s'il y en a un) et les derniers terminés / annulés."""
    _acces()
    ouvert = en_cours()
    dernieres = [_resume(frappe.get_doc(INV, n)) for n in
                 frappe.get_all(INV, filters={"statut": ["!=", EN_COURS]}, pluck="name", order_by="creation desc",
                                limit=LIMITE_HISTORIQUE)]
    m = S.magasin()
    return {"magasin": m, "libelle": frappe.db.get_value("Warehouse", m, "warehouse_name") or m,
            "en_cours": _resume(frappe.get_doc(INV, ouvert)) if ouvert else None, "dernieres": dernieres}


# ── Écriture ──────────────────────────────────────────────────────────────────

def _sauver(v):
    v.flags.ignore_permissions = True
    v.save()


def _recalculer(v):
    lignes = [{"qte_systeme": l.qte_systeme, "qte_comptee": l.qte_comptee if l.compte else None,
               "taux": flt(l.get("taux_saisi"), 6) or l.taux} for l in v.lignes]
    bilan = S.calculer_ecarts(lignes)
    for l, calc in zip(v.lignes, lignes):
        l.ecart = calc["ecart"] or 0
        l.valeur_ecart = calc["valeur_ecart"] or 0
    v.update(bilan)
    v.nb_lignes = len(v.lignes)


@frappe.whitelist(methods=["POST"])
def ouvrir_inventaire():
    """Ouvre l'inventaire du Magasin (un seul à la fois : l'ouvert est repris)."""
    _acces()
    ouvert = en_cours()
    if ouvert:
        return ouvert
    m = S.magasin()
    S._verifier_entrepot(m)
    v = frappe.new_doc(INV)
    v.update({"entrepot": m, "date": nowdate(), "statut": EN_COURS, "ouvert_par": frappe.session.user})
    for a in _articles_magasin(m):
        v.append("lignes", a)
    _recalculer(v)
    v.flags.ignore_permissions = True
    v.insert()
    return v.name


def _ouvert(name):
    v = frappe.get_doc(INV, name)
    if v.statut != EN_COURS:
        frappe.throw(_("Cet inventaire n’est plus en cours ({0}).").format(v.statut))
    return v


def _rafraichir(v) -> bool:
    """Les lignes NON comptées suivent le stock du jour ; les articles arrivés au Magasin depuis l'ouverture
    s'ajoutent à la feuille. Une ligne comptée garde sa quantité système du comptage (c'est l'écart qui compte).
    Rend True si quelque chose a changé (la fiche est alors enregistrée)."""
    qtes = S._quantites([l.item_code for l in v.lignes], [v.entrepot])
    change = False
    for l in v.lignes:
        if l.compte:
            continue
        nouveau = qtes.get((l.item_code, v.entrepot), 0.0)
        if abs(flt(nouveau, 6) - flt(l.qte_systeme, 6)) > 1e-6:
            l.qte_systeme = nouveau
            change = True
    presents = {l.item_code for l in v.lignes}
    nouveaux = [a for a in _articles_magasin(v.entrepot) if a["item_code"] not in presents]
    if nouveaux:
        change = True
        for a in nouveaux:
            v.append("lignes", a)
        v.lignes.sort(key=lambda l: ((l.item_name or l.item_code or "").lower(), l.item_code))
        for i, l in enumerate(v.lignes, 1):
            l.idx = i
    if change:
        _recalculer(v)
        _sauver(v)
    return change


@frappe.whitelist()
def detail_inventaire(name):
    _acces()
    v = frappe.get_doc(INV, name)
    if v.statut == EN_COURS:
        _rafraichir(v)
    infos = _infos_articles([l.item_code for l in v.lignes])
    qtes = S._quantites([l.item_code for l in v.lignes], [v.entrepot]) if v.statut == EN_COURS else {}
    lignes = []
    for l in v.lignes:
        i = infos.get(l.item_code) or {}
        cases = _charger_cases(l)
        actuelle = qtes.get((l.item_code, v.entrepot), flt(l.qte_systeme, 6)) if v.statut == EN_COURS \
            else (flt(l.qte_systeme_cloture, 6) if l.compte else flt(l.qte_systeme, 6))
        lignes.append({"item_code": l.item_code, "item_name": l.item_name, "image": i.get("image"),
                       "description": (strip_html(i.get("description") or "").strip()[:LONGUEUR_DESCRIPTION]
                                       if (i.get("description") or "").strip() != (l.item_name or "").strip() else ""),
                       "groupe": i.get("item_group"), "uom": i.get("stock_uom"),
                       "zones": [z for z in (l.zones or "").split(Z.SEPARATEUR) if z],
                       "cases": cases, "compte": cint(l.compte),
                       "qte_systeme": flt(l.qte_systeme, 6), "qte_actuelle": flt(actuelle, 6),
                       "bouge": abs(flt(actuelle, 6) - flt(l.qte_systeme, 6)) > 1e-6 if l.compte else False,
                       "qte_comptee": flt(l.qte_comptee, 6) if l.compte else None,
                       "ecart": flt(l.ecart, 6) if l.compte else None, "taux": flt(l.taux, 3),
                       "taux_saisi": flt(l.get("taux_saisi"), 3),
                       "valeur_ecart": flt(l.valeur_ecart, 3) if l.compte else None,
                       "qte_rapprochee": flt(l.qte_rapprochee, 6) if v.statut == TERMINE and l.compte else None,
                       "commentaire": l.commentaire, "sans_taux": cint(l.sans_taux),
                       "compte_par": frappe.utils.get_fullname(l.compte_par) if l.compte_par else None,
                       "compte_le": str(l.compte_le) if l.compte_le else None,
                       "zones_ajoutees": _zones_ajoutees(l)})
    return {"fiche": _resume(v), "zones": _zones_choix(), "espace": espace(), "sans_zone": SANS_ZONE,
            "lignes": lignes, "voir_valeur": S.est_responsable()}


@frappe.whitelist(methods=["POST"])
def enregistrer_inventaire(name, comptes):
    """comptes : {item_code: {"cases": {zone: qte|null}, "commentaire": s}} — les lignes envoyées seulement
    (sauvegarde partielle au fil du comptage). Une ligne dont toutes les cases sont vides redevient « non comptée »."""
    _acces()
    v = _ouvert(name)
    comptes = S._dict_json(comptes)
    if not comptes:
        return _resume(v)
    zones_connues = {z.name for z in zones_de_l_espace()}      # une case du Hall n'a pas sa place ici
    codes = [l.item_code for l in v.lignes if l.item_code in comptes]
    qtes = S._quantites(codes, [v.entrepot])
    for l in v.lignes:
        c = comptes.get(l.item_code)
        if c is None:
            continue
        cases = cases_valides(c.get("cases"), zones_connues)
        l.comptes = json.dumps(cases, ensure_ascii=False) if cases else None
        l.compte = 1 if cases else 0
        l.qte_comptee = sommer_cases(cases) if cases else 0
        l.commentaire = (c.get("commentaire") or "")[:140] or None
        if cases:
            # La quantité système est celle de L'INSTANT du comptage : l'écart dit ce qui manque maintenant.
            l.qte_systeme = qtes.get((l.item_code, v.entrepot), 0.0)
            l.compte_par, l.compte_le = frappe.session.user, now_datetime()
        else:
            l.compte_par, l.compte_le = None, None
    _recalculer(v)
    _sauver(v)
    return dict(_resume(v), enregistre_le=str(now_datetime()))


def _ligne(v, item_code):
    l = next((l for l in v.lignes if l.item_code == item_code), None)
    if not l:
        frappe.throw(_("Article absent de cette feuille : {0}").format(item_code))
    return l


def _zones_ajoutees(l) -> list[str]:
    return [z for z in (l.zones_ajoutees or "").split(Z.SEPARATEUR) if z]


def _poser_zones(l):
    """Réécrit les emplacements de la ligne d'après la fiche article (zones de l'espace seulement)."""
    l.zones = Z.SEPARATEUR.join(_zones_par_article().get(l.item_code, [])) or None


@frappe.whitelist(methods=["POST"])
def ajouter_emplacement(name, item_code, zone):
    """« ＋ » sur une ligne : la zone est AUSSITÔT ajoutée aux emplacements de l'article (fiche article), et la
    feuille gagne une case. Zones de l'espace du Magasin seulement."""
    _acces()
    v = _ouvert(name)
    l = _ligne(v, item_code)
    if zone not in {z.name for z in zones_de_l_espace()}:
        frappe.throw(_("{0} n’est pas un emplacement de l’espace {1}.").format(zone, espace() or "du Magasin"))
    if Z._ajouter(item_code, zone):
        Z._resynchroniser(item_code)
        l.zones_ajoutees = Z.SEPARATEUR.join(_zones_ajoutees(l) + [zone])
    _poser_zones(l)
    _sauver(v)
    return {"zones": [z for z in (l.zones or "").split(Z.SEPARATEUR) if z], "zones_ajoutees": _zones_ajoutees(l)}


@frappe.whitelist(methods=["POST"])
def retirer_emplacement(name, item_code, zone):
    """✕ sur une case ajoutée pendant CET inventaire : la zone est retirée de l'article (erreur de « ＋ »)."""
    _acces()
    v = _ouvert(name)
    l = _ligne(v, item_code)
    if zone not in _zones_ajoutees(l):
        frappe.throw(_("{0} n’a pas été ajouté pendant cet inventaire : retirez-le depuis la page Zones.").format(zone))
    Z._retirer(item_code, zone)
    Z._resynchroniser(item_code)
    l.zones_ajoutees = Z.SEPARATEUR.join(z for z in _zones_ajoutees(l) if z != zone) or None
    cases = _charger_cases(l)
    if zone in cases:
        del cases[zone]
        l.comptes = json.dumps(cases, ensure_ascii=False) if cases else None
        l.compte = 1 if cases else 0
        l.qte_comptee = sommer_cases(cases) if cases else 0
    _poser_zones(l)
    _recalculer(v)
    _sauver(v)
    return {"zones": [z for z in (l.zones or "").split(Z.SEPARATEUR) if z], "zones_ajoutees": _zones_ajoutees(l)}


def _taux(l) -> float:
    """Le taux pour le rapprochement : celui saisi à la clôture (`taux_saisi`, la main de l'utilisateur), sinon
    celui photographié, sinon la fiche article, sinon le dernier achat."""
    if flt(l.get("taux_saisi"), 6) > 0:
        return flt(l.taux_saisi, 6)
    if flt(l.taux, 6) > 0:
        return flt(l.taux, 6)
    r = frappe.db.get_value("Item", l.item_code, ["valuation_rate", "last_purchase_rate"], as_dict=True) or {}
    return flt(r.get("valuation_rate"), 6) or flt(r.get("last_purchase_rate"), 6)


def _preparer_cloture(v) -> dict:
    """Relit le stock du moment et pose, sur chaque ligne comptée, la quantité rapprochée (système actuel +
    écart du comptage). Rend le bilan de ce qui va se passer — PURE côté base : rien n'est enregistré."""
    qtes = S._quantites([l.item_code for l in v.lignes], [v.entrepot])
    bouges, sans_taux, a_rapprocher, zones_nouvelles = [], [], [], []
    for l in v.lignes:
        if not l.compte:
            continue
        actuelle = qtes.get((l.item_code, v.entrepot), 0.0)
        l.qte_systeme_cloture = actuelle
        l.qte_rapprochee = quantite_rapprochee(flt(l.qte_systeme, 6), flt(l.qte_comptee, 6), actuelle)
        l.bouge = 1 if abs(flt(actuelle, 6) - flt(l.qte_systeme, 6)) > 1e-6 else 0
        if l.bouge:
            bouges.append({"item_code": l.item_code, "item_name": l.item_name, "qte_systeme": flt(l.qte_systeme, 6),
                           "qte_actuelle": flt(actuelle, 6), "qte_comptee": flt(l.qte_comptee, 6),
                           "qte_rapprochee": flt(l.qte_rapprochee, 6)})
        l.sans_taux = 0
        if abs(flt(l.qte_rapprochee, 6) - flt(actuelle, 6)) > 1e-6:
            if flt(l.qte_rapprochee, 6) > 0 and _taux(l) <= 0:
                l.sans_taux = 1
                sans_taux.append({"item_code": l.item_code, "item_name": l.item_name,
                                  "qte_rapprochee": flt(l.qte_rapprochee, 6)})
            else:
                a_rapprocher.append(l)
    for l in v.lignes:
        if _zones_ajoutees(l):      # emplacements ajoutés pendant l'inventaire (déjà sur la fiche article)
            zones_nouvelles.append({"item_code": l.item_code, "item_name": l.item_name, "zones": _zones_ajoutees(l)})
    comptes = [l for l in v.lignes if l.compte]
    return {"nb_lignes": len(v.lignes), "nb_comptes": len(comptes), "nb_non_comptes": len(v.lignes) - len(comptes),
            "nb_ecarts": sum(1 for l in comptes if abs(flt(l.ecart, 6)) > 1e-6),
            "valeur_ecarts": flt(sum(flt(l.valeur_ecart) for l in comptes), 3),
            "valeur_manquants": flt(sum(-flt(l.valeur_ecart) for l in comptes if flt(l.ecart) < 0), 3),
            "a_rapprocher": [{"item_code": l.item_code, "item_name": l.item_name, "qte_actuelle": flt(l.qte_systeme_cloture, 6),
                              "qte_rapprochee": flt(l.qte_rapprochee, 6), "ecart": flt(l.ecart, 6),
                              "valeur_ecart": flt(l.valeur_ecart, 3), "taux": flt(_taux(l), 3),
                              "taux_saisi": flt(l.get("taux_saisi"), 3)} for l in a_rapprocher],
            "bouges": bouges, "sans_taux": sans_taux, "zones_nouvelles": zones_nouvelles}


@frappe.whitelist(methods=["POST"])
def definir_taux(name, item_code, taux=None):
    """Donne la main sur le taux de valorisation d'une ligne avant la clôture (demande du 27/10) : il prime sur
    le taux photographié et sort l'article de la liste « sans taux ». Vide ou 0 = retour à l'automatique.
    Réservé aux responsables (ceux qui voient les valeurs)."""
    _acces()
    if not S.est_responsable():
        frappe.throw(_("Le taux de valorisation est réservé aux responsables."), frappe.PermissionError)
    v = _ouvert(name)
    l = _ligne(v, item_code)
    t = flt(taux, 6)
    if t < 0:
        frappe.throw(_("Le taux de valorisation ne peut pas être négatif."))
    l.taux_saisi = t
    _recalculer(v)
    _sauver(v)
    return {"item_code": item_code, "taux": flt(_taux(l), 3), "taux_saisi": flt(l.taux_saisi, 3),
            "valeur_ecart": flt(l.valeur_ecart, 3)}


@frappe.whitelist()
def bilan_cloture(name):
    """Ce que la clôture ferait, sans rien écrire : lignes rapprochées, articles qui ont bougé depuis leur
    comptage, articles sans taux (exclus), zones découvertes."""
    _acces()
    v = _ouvert(name)
    return _preparer_cloture(v)


def _rapprochement(v, lignes) -> str | None:
    if not lignes:
        return None
    societe = S._societe()
    doc = frappe.new_doc("Stock Reconciliation")
    doc.update({"purpose": "Stock Reconciliation", "company": societe,
                "expense_account": frappe.db.get_value("Company", societe, "stock_adjustment_account"),
                "cost_center": frappe.db.get_value("Company", societe, "cost_center")})
    for l in lignes:
        q = flt(l.qte_rapprochee, 6)
        doc.append("items", {"item_code": l.item_code, "warehouse": v.entrepot, "qty": q,
                             "valuation_rate": _taux(l) if q > 0 else 0})
    doc.flags.ignore_permissions = True
    doc.flags.depuis_verification = v.name
    doc.insert()
    doc.submit()
    doc.add_comment("Comment", _("Inventaire du Magasin {0} : {1} article(s) rapproché(s), clôturé par {2}.")
                    .format(v.name, len(lignes), frappe.utils.get_fullname(frappe.session.user)))
    return doc.name


@frappe.whitelist(methods=["POST"])
def cloturer_inventaire(name, note=None):
    """Clôture : UNE écriture Stock Reconciliation sur le Magasin (lignes comptées dont l'écart n'est pas nul,
    quantité = système actuel + écart du comptage), fiche terminée. Les articles non comptés ne bougent pas
    (les emplacements ajoutés par « ＋ » sont déjà sur les fiches articles)."""
    _acces()
    v = _ouvert(name)
    bilan = _preparer_cloture(v)
    lignes = [l for l in v.lignes if l.compte and not l.sans_taux
              and abs(flt(l.qte_rapprochee, 6) - flt(l.qte_systeme_cloture, 6)) > 1e-6]
    v.rapprochement = _rapprochement(v, lignes)
    v.nb_zones_ajoutees = sum(len(_zones_ajoutees(l)) for l in v.lignes)
    v.nb_bouges = len(bilan["bouges"])
    v.nb_sans_taux = len(bilan["sans_taux"])
    v.statut = TERMINE
    v.termine_le, v.termine_par = now_datetime(), frappe.session.user
    if note:
        v.note = note
    _sauver(v)
    return {"name": v.name, "rapprochement": v.rapprochement, "nb_rapproches": len(lignes),
            "nb_comptes": bilan["nb_comptes"], "nb_non_comptes": bilan["nb_non_comptes"], "nb_ecarts": bilan["nb_ecarts"],
            "valeur_ecarts": bilan["valeur_ecarts"], "valeur_manquants": bilan["valeur_manquants"],
            "nb_bouges": v.nb_bouges, "nb_sans_taux": v.nb_sans_taux, "nb_zones_ajoutees": v.nb_zones_ajoutees}


@frappe.whitelist(methods=["POST"])
def annuler_inventaire(name, motif=None):
    _acces()
    v = _ouvert(name)
    v.statut = ANNULE
    v.termine_le, v.termine_par = now_datetime(), frappe.session.user
    if motif:
        v.note = motif
    _sauver(v)
    return _resume(v)
