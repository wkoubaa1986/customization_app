"""Verrou par code du plugin ChatGPT des dépenses (demande du 05/11/2026) — voir mcp_chatgpt, assistant_chatgpt.

Un téléphone avec ChatGPT ouvert ne doit pas suffire à passer une dépense, un versement ou un rendez-vous : les outils
d'ÉCRITURE du plugin exigent un JETON. Le jeton s'obtient par l'outil `deverrouiller(code)` : bon code → jeton aléatoire
valable `assistant_chatgpt_verrou_minutes` minutes (10 par défaut), gardé dans Redis ; ChatGPT le repasse de lui-même
dans le paramètre `jeton` de chaque écriture. Jeton absent, inconnu ou expiré → l'écriture est refusée avec un message
qui dit à ChatGPT de demander le code. Les lectures ne demandent rien.

Le code est stocké HACHÉ (pbkdf2) dans site_config.json (`assistant_chatgpt_code`), posé par
`bench --site <site> execute customization_app.verrou_chatgpt.definir_code --kwargs "{'code': '4821'}"` ; sans code
posé, le plugin reste comme avant (pas de verrou). Durée 0 = pas de jeton : le code lui-même est exigé à chaque écriture.
Le code en clair est toujours accepté à la place du jeton. Cinq codes faux de suite bloquent le déverrouillage quinze
minutes. Le verrou se contrôle dans mcp_chatgpt.traiter (catalogue « verrou »), AVANT d'appeler l'outil ; le GPT
personnalisé (actions REST, retiré le 11/12/2026) n'est pas couvert.
"""
from __future__ import annotations

import frappe
from frappe import _

CLE_CODE = "assistant_chatgpt_code"
CLE_MINUTES = "assistant_chatgpt_verrou_minutes"
MINUTES_DEFAUT = 10
ECHECS_MAX = 5
BLOCAGE_MINUTES = 15
PREFIXE_JETON = "verrou_chatgpt:jeton:"
CLE_ECHECS = "verrou_chatgpt:echecs"
PARAM = "jeton"

#: Les outils du plugin dépenses qui écrivent : eux seuls exigent le jeton.
ECRITURES = frozenset({"saisir_depense", "verser_en_banque", "saisir_depense_euros", "valider_ecriture", "annuler_ecriture",
                       "corriger_ecriture", "creer_rendez_vous", "deplacer_rendez_vous", "annuler_rendez_vous"})

MESSAGE_VERROUILLE = _("Verrouillé : demande à l'utilisateur son code de déverrouillage, appelle deverrouiller(code), puis rappelle cet "
                       "outil avec le jeton reçu dans `jeton`. Rien n'a été enregistré.")


# ── Règles pures ─────────────────────────────────────────────────────────────

def hacher(code: str) -> str:
    from passlib.hash import pbkdf2_sha256
    return pbkdf2_sha256.hash(str(code).strip())


def code_valide(code, empreinte: str | None) -> bool:
    """Le code correspond-il à l'empreinte stockée ? Pure (pas de secret en dehors des deux arguments)."""
    if not empreinte or code is None or not str(code).strip():
        return False
    from passlib.hash import pbkdf2_sha256
    try:
        return pbkdf2_sha256.verify(str(code).strip(), empreinte)
    except ValueError:
        return False


def exige_jeton(nom: str) -> bool:
    return nom in ECRITURES


def avec_jeton(outils: list[dict]) -> list[dict]:
    """La liste d'outils MCP où chaque outil d'écriture déclare le paramètre `jeton`. Pure."""
    out = []
    for o in outils:
        if exige_jeton(o["name"]):
            o = dict(o, inputSchema=dict(o["inputSchema"], properties=dict(o["inputSchema"]["properties"], **{
                PARAM: {"type": "string", "description": "Jeton de déverrouillage reçu de deverrouiller (ou le code lui-même)"}})))
        out.append(o)
    return out


def decision(nom: str, arguments: dict, empreinte: str | None, jeton_connu) -> tuple[bool, dict]:
    """(autorisé ?, arguments sans le jeton). `jeton_connu(jeton)` dit si le jeton est vivant (injecté). Pure."""
    args = dict(arguments or {})
    jeton = args.pop(PARAM, None)
    if not exige_jeton(nom) or not empreinte:
        return True, args
    if jeton and (jeton_connu(jeton) or code_valide(jeton, empreinte)):
        return True, args
    return False, args


# ── Côté serveur ─────────────────────────────────────────────────────────────

def _conf() -> dict:
    return getattr(frappe.local, "conf", None) or {}          # vide hors site (tests purs) : pas de verrou


def empreinte() -> str | None:
    return _conf().get(CLE_CODE) or None


def minutes() -> int:
    m = _conf().get(CLE_MINUTES)
    return MINUTES_DEFAUT if m is None else max(int(m), 0)


def _jeton_connu(jeton) -> bool:
    return bool(jeton) and frappe.cache.get_value(PREFIXE_JETON + str(jeton), expires=True) is not None


def controler(nom: str, arguments: dict) -> dict:
    """Le contrôle fait par mcp_chatgpt.traiter avant chaque outil : renvoie les arguments sans le jeton, ou lève."""
    ok, args = decision(nom, arguments, empreinte(), _jeton_connu)
    if not ok:
        frappe.throw(MESSAGE_VERROUILLE)
    return args


def deverrouiller(code) -> dict:
    """Bon code → jeton pour `minutes()` minutes (ou simple accusé si la durée est 0). Cinq échecs → quinze minutes de blocage."""
    emp = empreinte()
    if not emp:
        return {"verrou": False, "message": _("Aucun code n'est posé : les écritures ne sont pas verrouillées.")}
    echecs = int(frappe.cache.get_value(CLE_ECHECS, expires=True) or 0)
    if echecs >= ECHECS_MAX:
        frappe.throw(_("Déverrouillage bloqué {0} minutes après {1} codes faux.").format(BLOCAGE_MINUTES, ECHECS_MAX))
    if not code_valide(code, emp):
        frappe.cache.set_value(CLE_ECHECS, echecs + 1, expires_in_sec=BLOCAGE_MINUTES * 60)
        frappe.throw(_("Code refusé ({0} essai(s) restant(s)).").format(ECHECS_MAX - echecs - 1))
    frappe.cache.delete_value(CLE_ECHECS)
    m = minutes()
    if m == 0:
        return {"verrou": True, "jeton": None, "expire_dans_minutes": 0,
                "message": _("Code accepté. Pas de jeton : passe le code lui-même dans `jeton` à chaque écriture.")}
    jeton = frappe.generate_hash(length=32)
    frappe.cache.set_value(PREFIXE_JETON + jeton, frappe.utils.now(), expires_in_sec=m * 60)
    return {"verrou": True, "jeton": jeton, "expire_dans_minutes": m,
            "message": _("Déverrouillé pour {0} minutes : passe ce jeton dans `jeton` à chaque écriture.").format(m)}


# ── Réglage (bench execute) ──────────────────────────────────────────────────

def definir_code(code, minutes=None):
    """`bench --site <site> execute customization_app.verrou_chatgpt.definir_code --kwargs "{'code': '4821', 'minutes': 10}"`
    Pose (ou change) le code, haché, et la durée du jeton (0 = code à chaque écriture)."""
    from frappe.installer import update_site_config
    code = str(code).strip()
    if len(code) < 4:
        frappe.throw(_("Quatre caractères au moins."))
    update_site_config(CLE_CODE, hacher(code))
    if minutes is not None:
        update_site_config(CLE_MINUTES, max(int(minutes), 0))
    frappe.cache.delete_value(CLE_ECHECS)
    return {"verrou": True, "minutes": max(int(minutes), 0) if minutes is not None else minutes_apres_reglage()}


def minutes_apres_reglage() -> int:
    from frappe import get_site_config
    m = get_site_config().get(CLE_MINUTES)
    return MINUTES_DEFAUT if m is None else max(int(m), 0)


def retirer_code():
    """`bench --site <site> execute customization_app.verrou_chatgpt.retirer_code` : plus de verrou (clés retirées du site_config)."""
    import json
    from frappe.installer import get_site_config_path
    from frappe.utils.synchronization import filelock
    with filelock("site_config"):
        chemin = get_site_config_path()
        config = json.loads(open(chemin).read() or "{}")
        for k in (CLE_CODE, CLE_MINUTES):
            config.pop(k, None)
        with open(chemin, "w") as f:
            f.write(json.dumps(config, indent=1, sort_keys=True))
    frappe.cache.delete_value(CLE_ECHECS)
    return {"verrou": False}


def etat() -> dict:
    """`bench --site <site> execute customization_app.verrou_chatgpt.etat` : verrou posé ?, durée, échecs en cours."""
    return {"verrou": bool(empreinte()), "minutes": minutes(), "echecs": int(frappe.cache.get_value(CLE_ECHECS, expires=True) or 0),
            "outils_verrouilles": sorted(ECRITURES)}
