"""Réglage « Config Relances » — tout ce qui était codé en dur dans les trois crons de l'entretien
(update_schedule : échéanciers, relance_maintenance_sms : SMS 10:00, creation_liste_appelle : listes d'appels 07:00),
et les règles PARTAGÉES entre eux (clients avec rendez-vous, famille prioritaire, coût de l'entretien, journal).

Décision 03/10/2026 : un réglage visible dans l'onglet « Relances et partenaire ». Un champ vide = la valeur
historique (DEFAUTS), donc un site qui n'a jamais enregistré le réglage se comporte exactement comme avant.
Les listes à une valeur par ligne acceptent aussi la virgule.
"""
from __future__ import annotations

import logging

import frappe
from frappe.utils import add_days, cint, getdate

DOCTYPE = "Config Relances"

DEFAUTS = {
    "groupes_b2b": "Compte Pro\nQuincaillerie\nTechnicien\nPro Grand Rayon",
    "clients_proteges": "Ayman Belguith\nKoubaâ Néjib\nJamel Aloui\nKoubaâ Néjib - 1",
    "fenetre_mois_avant": 4, "fenetre_mois_apres": 1,
    "visites_ajoutees": 2, "nb_visites": 10, "periodicite_defaut": "Half Yearly",
    "max_sms_par_jour": 110, "delai_sms2_jours": 7, "jours_rdv_recent": 15,
    "types_rdv_exclusion": "Entretien\nRéparation\nInstallation\nVisite\nLivraison",
    "plafond_liste": 100, "jours_entre_deux_appels": 60, "jours_apres_intervention": 160,
    "delai_apres_sms2_jours": 2, "delai_2e_appel_jours": 3,
    "secteurs_urgence": "Secteur 7\nSecteur 8\nSecteur 9",
}

# L'ORDRE est la priorité : famille retenue pour le coût du SMS / de l'appel, et pour deviner une machine.
DEFAUT_FAMILLES = [
    {"code": "RO_DOM", "libelle_sms": "votre osmoseur domestique", "article_entretien": "M-E-OD", "article_type": "AP-M-AJ-5-SM",
     "groupes_machines": "RO domestique avec pompe\nRO domestique sans pompe\nRO flux direct",
     "groupes_consommables": "RO Consommables & Kits d’entretien\nCartouches à charbon\nCartouches anti-sédiment\n"
                             "Cartouches plissées (anti-bactériennes inf 1 micron)\nFiltres T33\nAccessoires divers\nMembranes RO domestiques (≤100 GPD)"},
    {"code": "ADOUCISSEUR", "libelle_sms": "votre adoucisseur", "article_entretien": "M-E-Ad", "article_type": "Ad-30L",
     "groupes_machines": "Adoucisseurs Domestiques\nAdoucisseurs Commerciaux\nVannes adoucisseurs automatiques\nVannes adoucisseurs manuelles",
     "groupes_consommables": "Consommables & Accessoires"},
    {"code": "RO_COM", "libelle_sms": "votre osmoseur commercial", "article_entretien": "M-E-OC", "article_type": "AP-SM-5-200GPD",
     "groupes_machines": "Appareils commerciaux",
     "groupes_consommables": "Cartouches à charbon\nCartouches anti-sédiment\nCartouches plissées (anti-bactériennes inf 1 micron)\n"
                             "Consommables commerciaux\nMembranes RO commerciales (≤800 GPD)"},
    {"code": "RO_IND", "libelle_sms": "votre osmoseur industriel", "article_entretien": "M-E-OI", "article_type": "OsI-1500GPD",
     "groupes_machines": "Osmoseurs Industriels",
     "groupes_consommables": "Cartouches à charbon\nCartouches anti-sédiment\nCartouches plissées (anti-bactériennes inf 1 micron)\n"
                             "Membranes RO industrielles (4040/8040)\nMédias filtrants\nAntiscalants"},
    {"code": "BIO", "libelle_sms": "votre système de bi-osmose", "article_entretien": "", "article_type": "AP-SM-6-Bi2-75GPD",
     "groupes_machines": "Bi-osmose",
     "groupes_consommables": "Cartouches à charbon\nCartouches anti-sédiment\nCartouches plissées (anti-bactériennes inf 1 micron)"},
    {"code": "FONTAINE", "libelle_sms": "votre fontaine à eau", "article_entretien": "", "article_type": "FF-5-Mini-75GPD",
     "groupes_machines": "Fontaines", "groupes_consommables": "Cartouches à charbon\nCartouches anti-sédiment"},
    # « Filtres UV » n'est plus AUSSI un consommable (il créait un échéancier de 5 ans pour une lampe de rechange).
    {"code": "UV", "libelle_sms": "votre stérilisateur UV", "article_entretien": "", "article_type": "F-UV-6w",
     "groupes_machines": "Filtres UV", "groupes_consommables": "Accessoires UV"},
    {"code": "PF", "libelle_sms": "votre porte-filtre", "article_entretien": "", "article_type": "P-F-T-10'-T-SC",
     "groupes_machines": "Bouteilles FRP\nPorte-filtres",
     "groupes_consommables": "Cartouches à charbon\nCartouches anti-calcaire\nCartouches anti-sédiment\nCartouches lavables\n"
                             "Cartouches plissées (anti-bactériennes inf 1 micron)"},
    {"code": "POMPE", "libelle_sms": "votre pompe", "article_entretien": "", "article_type": "", "groupes_machines": "", "groupes_consommables": ""},
]
LIBELLE_FAMILLE_DEFAUT = "votre appareil de traitement d'eau"
ARTICLE_ENTRETIEN_DEFAUT = "M-E-OD"
PRIX_LISTE = "Vente standard"


def liste(texte) -> list:
    """« a\\nb » ou « a, b » → ["a", "b"], sans vides ni doublons, ordre conservé. PURE."""
    out = []
    for morceau in (texte or "").replace(",", "\n").splitlines():
        v = morceau.strip()
        if v and v not in out:
            out.append(v)
    return out


def _singles() -> dict:
    try:
        if getattr(frappe.local, "db", None):
            return dict(frappe.db.sql("select field, value from tabSingles where doctype = %s", (DOCTYPE,)))
    except Exception:
        pass
    return {}


def config() -> dict:
    """Valeurs du réglage, défauts historiques pour tout champ vide. Mémo par requête."""
    cache = getattr(frappe.local, "_config_relances", None)
    if cache is not None:
        return cache
    brut = _singles()
    out = {}
    for champ, defaut in DEFAUTS.items():
        v = brut.get(champ)
        if isinstance(defaut, int):
            out[champ] = cint(v) if v not in (None, "") else defaut
        else:
            out[champ] = v if (v or "").strip() else defaut
    # Clients protégés : table (Link Customer) si le réglage en a, sinon la liste historique.
    try:
        if getattr(frappe.local, "db", None) and frappe.db.table_exists("Config Relances Client Protege"):
            prot = frappe.get_all("Config Relances Client Protege", filters={"parent": DOCTYPE, "parenttype": DOCTYPE}, pluck="client")
            if prot or brut:                       # réglage enregistré : sa table fait foi, même vide
                out["clients_proteges"] = "\n".join(prot)
    except Exception:
        pass
    for champ in ("groupes_b2b", "clients_proteges", "types_rdv_exclusion", "secteurs_urgence"):
        out[champ + "_liste"] = liste(out[champ])
    out["familles"] = familles()
    frappe.local._config_relances = out
    return out


def familles() -> list:
    """Les familles de machines dans l'ordre de priorité : table du réglage si elle a des lignes, sinon DEFAUT_FAMILLES."""
    rows = []
    try:
        if getattr(frappe.local, "db", None) and frappe.db.table_exists("Config Relances Famille"):
            rows = frappe.get_all("Config Relances Famille", filters={"parent": DOCTYPE, "parenttype": DOCTYPE}, order_by="idx",
                                  fields=["code", "libelle_sms", "article_entretien", "article_type", "groupes_machines", "groupes_consommables"])
    except Exception:
        rows = []
    src = rows or DEFAUT_FAMILLES
    out = []
    for r in src:
        code = (r.get("code") or "").strip()
        if not code:
            continue
        out.append({"code": code, "libelle_sms": (r.get("libelle_sms") or "").strip() or LIBELLE_FAMILLE_DEFAUT,
                    "article_entretien": (r.get("article_entretien") or "").strip(), "article_type": (r.get("article_type") or "").strip(),
                    "groupes_machines": liste(r.get("groupes_machines")), "groupes_consommables": liste(r.get("groupes_consommables"))})
    return out


# ── Familles : accès dérivés ─────────────────────────────────────────────────

def famille_du_groupe_machine(groupe: str, fams: list | None = None):
    g = (groupe or "").strip()
    for f in fams or familles():
        if g in f["groupes_machines"]:
            return f["code"]
    return None


def familles_du_groupe_consommable(groupe: str, fams: list | None = None) -> list:
    g = (groupe or "").strip()
    return [f["code"] for f in (fams or familles()) if g in f["groupes_consommables"]]


def groupes_machines(fams: list | None = None) -> set:
    return {g for f in (fams or familles()) for g in f["groupes_machines"]}


def groupes_consommables(fams: list | None = None) -> set:
    return {g for f in (fams or familles()) for g in f["groupes_consommables"]}


def famille(code: str, fams: list | None = None) -> dict | None:
    return next((f for f in (fams or familles()) if f["code"] == code), None)


def famille_prioritaire(codes, fams: list | None = None):
    """La première famille, dans l'ordre du réglage, parmi `codes` (déterministe). PURE avec `fams`."""
    ordre = [f["code"] for f in (fams or familles())]
    for c in ordre:
        if c in (codes or ()):
            return c
    reste = sorted(c for c in (codes or ()) if c)
    return reste[0] if reste else None


def libelle_famille(code, fams: list | None = None) -> str:
    f = famille(code, fams)
    return f["libelle_sms"] if f else LIBELLE_FAMILLE_DEFAUT


def prix_article(code: str):
    if not code:
        return None
    return frappe.db.get_value("Item Price", {"item_code": code, "price_list": PRIX_LISTE, "selling": 1}, "price_list_rate")


def cout_entretien(codes, fams: list | None = None):
    """(famille retenue, prix main-d'œuvre) : la famille PRIORITAIRE, son article d'entretien (sinon M-E-OD).
    Une seule règle pour le SMS et pour la liste d'appels (ils divergeaient : max ici, priorité là)."""
    fam = famille_prioritaire(codes, fams)
    f = famille(fam, fams) if fam else None
    article = (f or {}).get("article_entretien") or ARTICLE_ENTRETIEN_DEFAUT
    prix = prix_article(article)
    if prix is None and article != ARTICLE_ENTRETIEN_DEFAUT:
        prix = prix_article(ARTICLE_ENTRETIEN_DEFAUT)
    return fam, prix


# ── Règles partagées ─────────────────────────────────────────────────────────

def est_b2b(groupe_client: str, cfg: dict | None = None) -> bool:
    return (groupe_client or "").strip() in (cfg or config())["groupes_b2b_liste"]


def clients_avec_rdv(cfg: dict | None = None, jours: int | None = None, types: list | None = None) -> set:
    """Clients qui ont une tâche de terrain (Open/Completed) depuis `jours` jours ou à venir, d'un des `types` :
    ni SMS ni appel pour eux. `dans_local` NULL compte comme « pas dans nos locaux » (le filtre `!= 'Oui'` les perdait)."""
    cfg = cfg or config()
    jours = cfg["jours_rdv_recent"] if jours is None else cint(jours)
    types = types or cfg["types_rdv_exclusion_liste"]
    if not types:
        return set()
    rows = frappe.db.sql("""select distinct custom_client from `tabTache de travail`
                            where status in ('Open', 'Completed') and starts_on >= %s and custom_client is not null
                              and ifnull(dans_local, '') <> 'Oui' and custom_type_dintervention in %s""",
                         (str(add_days(getdate(), -jours)) + " 00:00:00", tuple(types)))
    return {r[0] for r in rows if r[0]}


def journal(nom: str):
    """Journal de cron QUI ÉCRIT : frappe.logger() est au niveau ERROR en prod, donc les [SUMMARY] n'existaient nulle part."""
    lg = frappe.logger(nom, allow_site=True)
    lg.setLevel(logging.INFO)
    return lg


@frappe.whitelist()
def familles_par_defaut():
    frappe.only_for(("System Manager", "Sales Manager", "Maintenance Manager"))
    return DEFAUT_FAMILLES
