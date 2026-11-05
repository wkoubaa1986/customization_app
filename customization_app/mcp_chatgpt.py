"""Plugin ChatGPT = serveur MCP (Streamable HTTP) qui expose les outils de l'assistant (02/11/2026).

OpenAI retire les GPT personnalisés le 11 décembre 2026 ; leurs « actions » ne migrent pas. Les remplaçants, les
« plugins », sont des applications MCP branchées en mode développeur (Paramètres → Sécurité et connexion → Mode
développeur → « + » → application MCP distante). Ce module parle ce protocole, dans ERPNext même : un seul point
d'entrée, JSON-RPC 2.0 sur HTTP, méthodes initialize / tools/list / tools/call / ping.

Authentification : ChatGPT ne sait envoyer ni clé ni en-tête à une application « sans authentification » — le secret
voyage donc dans l'URL : `…/mcp_chatgpt.serveur?cle=<clé>:<secret>`, la clé API de l'utilisateur technique
assistant.chatgpt@aquaworld.local (la même que pour le GPT). Clé fausse ou absente → 401, rien d'autre. Une fois la clé
reconnue, la requête s'exécute SOUS cet utilisateur : mêmes gardes, mêmes outils, mêmes traces que par le GPT.

Un serveur = un CATALOGUE (nom, version, consignes, outils) servi sous un utilisateur. La mécanique (`traiter`, `servir`)
est partagée : mcp_articles expose un second plugin, séparé (autre URL, autre clé, autres outils), avec le même code.
"""
from __future__ import annotations

import json

import frappe
from frappe.utils.password import get_decrypted_password
from werkzeug.wrappers import Response

from customization_app import assistant_chatgpt as A
from customization_app import verrou_chatgpt as V

VERSION_PROTOCOLE = "2025-06-18"
NOM_SERVEUR = "aqua-world-erp"

#: Le catalogue de CE serveur : les outils de l'assistant (dépenses, versements, rendez-vous…). « verrou » : contrôle fait
#: avant chaque outil (verrou_chatgpt : jeton exigé pour les écritures quand un code est posé) ; il rend les arguments
#: à passer à l'outil, ou lève — l'erreur revient à ChatGPT en clair, comme une erreur métier.
CATALOGUE = {"nom": NOM_SERVEUR, "version": "1.3.0", "instructions": A.INSTRUCTIONS_GPT, "outils": A.OUTILS,
             "liste": lambda: V.avec_jeton(A.outils_mcp()), "verrou": V.controler}


# ── Règles pures ─────────────────────────────────────────────────────────────

def traiter(message: dict, appeler, catalogue: dict | None = None) -> dict | None:
    """Réponse JSON-RPC à un message MCP. `appeler(nom, arguments)` exécute l'outil (injecté : testable sans Frappe).
    `catalogue` = {nom, version, instructions, outils, liste} du serveur (celui de l'assistant par défaut).
    None = notification, rien à renvoyer. Pure."""
    c = catalogue or CATALOGUE
    ident = message.get("id")
    methode = message.get("method") or ""
    params = message.get("params") or {}
    if methode.startswith("notifications/"):
        return None
    if methode == "initialize":
        res = {"protocolVersion": VERSION_PROTOCOLE, "capabilities": {"tools": {"listChanged": True}},
               "serverInfo": {"name": c["nom"], "version": c["version"]},
               "instructions": c["instructions"]}
    elif methode == "ping":
        res = {}
    elif methode == "tools/list":
        res = {"tools": c["liste"]()}
    elif methode == "tools/call":
        nom = params.get("name")
        if nom not in c["outils"]:
            return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": "Outil inconnu : %s" % nom}}
        try:
            arguments = params.get("arguments") or {}
            if c.get("verrou"):
                arguments = c["verrou"](nom, arguments)
            resultat = appeler(nom, arguments)
            res = {"content": [{"type": "text", "text": json.dumps(resultat, ensure_ascii=False, default=str)}],
                   "structuredContent": resultat if isinstance(resultat, dict) else {"resultat": resultat}, "isError": False}
        except Exception as e:                       # l'erreur métier revient à ChatGPT en clair, pas en 500
            res = {"content": [{"type": "text", "text": str(e)}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "Méthode inconnue : %s" % methode}}
    return {"jsonrpc": "2.0", "id": ident, "result": res}


# ── Point d'entrée HTTP ──────────────────────────────────────────────────────

def _reponse(corps, statut=200):
    r = Response(json.dumps(corps, ensure_ascii=False) if corps is not None else "", status=statut,
                 mimetype="application/json" if corps is not None else None)
    r.headers["MCP-Protocol-Version"] = VERSION_PROTOCOLE
    return r


def _authentifier(cle: str, utilisateur: str = A.UTILISATEUR) -> bool:
    """`clé:secret` de l'utilisateur technique, comparés en clair avec le secret déchiffré de sa fiche."""
    if not cle or ":" not in cle:
        return False
    api_key, secret = cle.split(":", 1)
    u = frappe.db.get_value("User", utilisateur, ["api_key", "enabled"], as_dict=True)
    if not u or not u.enabled or not u.api_key or u.api_key != api_key:
        return False
    attendu = get_decrypted_password("User", utilisateur, "api_secret", raise_exception=False)
    return bool(attendu) and secret == attendu


def appeler_dans(module):
    """`appeler(nom, arguments)` qui exécute la fonction `nom` du module, sans les arguments vides."""
    def appeler(nom, arguments):
        fn = getattr(module, nom)
        args = {k: v for k, v in (arguments or {}).items() if v is not None and v != ""}
        return fn(**args)
    return appeler


_appeler = appeler_dans(A)


def servir(cle, utilisateur: str, appeler, catalogue: dict):
    """Le corps d'un serveur MCP : clé de `utilisateur` sur l'URL, un message JSON-RPC en POST, exécuté SOUS cet
    utilisateur avec les outils du `catalogue` ; une écriture réussie est acquise (commit).
    GET : pas de flux SSE ici (405). DELETE : fin de session (204)."""
    if frappe.request.method == "GET":
        return _reponse({"erreur": "Ce serveur MCP répond en POST (Streamable HTTP sans flux)."}, 405)
    if frappe.request.method == "DELETE":
        return _reponse(None, 204)
    # Corps JSON → Frappe ne lit plus la chaîne de requête dans form_dict : la clé se prend sur l'URL elle-même.
    cle = cle or frappe.request.args.get("cle") or frappe.get_request_header("X-Cle")
    if not _authentifier(cle, utilisateur):
        return _reponse({"erreur": "Clé invalide."}, 401)
    try:
        message = json.loads(frappe.request.get_data(as_text=True) or "{}")
    except ValueError:
        return _reponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON illisible"}}, 400)
    if isinstance(message, list):
        return _reponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Pas de lots"}}, 400)
    frappe.set_user(utilisateur)
    reponse = traiter(message, appeler, catalogue)
    if reponse is None:
        return _reponse(None, 202)
    if "result" in reponse and message.get("method") == "tools/call" and not reponse["result"].get("isError"):
        frappe.db.commit()                 # une écriture passée par ChatGPT est acquise
    return _reponse(reponse)


@frappe.whitelist(allow_guest=True, methods=["POST", "GET", "DELETE"])
def serveur(cle=None):
    """Le serveur MCP de l'assistant (dépenses, versements, rendez-vous)."""
    return servir(cle, A.UTILISATEUR, _appeler, CATALOGUE)
