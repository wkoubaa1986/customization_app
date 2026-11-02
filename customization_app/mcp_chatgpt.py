"""Plugin ChatGPT = serveur MCP (Streamable HTTP) qui expose les outils de l'assistant (02/11/2026).

OpenAI retire les GPT personnalisés le 11 décembre 2026 ; leurs « actions » ne migrent pas. Les remplaçants, les
« plugins », sont des applications MCP branchées en mode développeur (Paramètres → Sécurité et connexion → Mode
développeur → « + » → application MCP distante). Ce module parle ce protocole, dans ERPNext même : un seul point
d'entrée, JSON-RPC 2.0 sur HTTP, méthodes initialize / tools/list / tools/call / ping.

Authentification : ChatGPT ne sait envoyer ni clé ni en-tête à une application « sans authentification » — le secret
voyage donc dans l'URL : `…/mcp_chatgpt.serveur?cle=<clé>:<secret>`, la clé API de l'utilisateur technique
assistant.chatgpt@aquaworld.local (la même que pour le GPT). Clé fausse ou absente → 401, rien d'autre. Une fois la clé
reconnue, la requête s'exécute SOUS cet utilisateur : mêmes gardes, mêmes outils, mêmes traces que par le GPT.
"""
from __future__ import annotations

import json

import frappe
from frappe.utils.password import get_decrypted_password
from werkzeug.wrappers import Response

from customization_app import assistant_chatgpt as A

VERSION_PROTOCOLE = "2025-06-18"
NOM_SERVEUR = "aqua-world-erp"


# ── Règles pures ─────────────────────────────────────────────────────────────

def traiter(message: dict, appeler) -> dict | None:
    """Réponse JSON-RPC à un message MCP. `appeler(nom, arguments)` exécute l'outil (injecté : testable sans Frappe).
    None = notification, rien à renvoyer. Pure."""
    ident = message.get("id")
    methode = message.get("method") or ""
    params = message.get("params") or {}
    if methode.startswith("notifications/"):
        return None
    if methode == "initialize":
        res = {"protocolVersion": VERSION_PROTOCOLE, "capabilities": {"tools": {"listChanged": False}},
               "serverInfo": {"name": NOM_SERVEUR, "version": "1.0.0"},
               "instructions": A.INSTRUCTIONS_GPT}
    elif methode == "ping":
        res = {}
    elif methode == "tools/list":
        res = {"tools": A.outils_mcp()}
    elif methode == "tools/call":
        nom = params.get("name")
        if nom not in A.OUTILS:
            return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": "Outil inconnu : %s" % nom}}
        try:
            resultat = appeler(nom, params.get("arguments") or {})
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


def _authentifier(cle: str) -> bool:
    """`clé:secret` de l'utilisateur technique, comparés en clair avec le secret déchiffré de sa fiche."""
    if not cle or ":" not in cle:
        return False
    api_key, secret = cle.split(":", 1)
    u = frappe.db.get_value("User", A.UTILISATEUR, ["api_key", "enabled"], as_dict=True)
    if not u or not u.enabled or not u.api_key or u.api_key != api_key:
        return False
    attendu = get_decrypted_password("User", A.UTILISATEUR, "api_secret", raise_exception=False)
    return bool(attendu) and secret == attendu


def _appeler(nom, arguments):
    fn = getattr(A, nom)
    args = {k: v for k, v in (arguments or {}).items() if v is not None and v != ""}
    return fn(**args)


@frappe.whitelist(allow_guest=True, methods=["POST", "GET", "DELETE"])
def serveur(cle=None):
    """Le serveur MCP. POST : un message JSON-RPC. GET : pas de flux SSE ici (405). DELETE : fin de session (204)."""
    if frappe.request.method == "GET":
        return _reponse({"erreur": "Ce serveur MCP répond en POST (Streamable HTTP sans flux)."}, 405)
    if frappe.request.method == "DELETE":
        return _reponse(None, 204)
    # Corps JSON → Frappe ne lit plus la chaîne de requête dans form_dict : la clé se prend sur l'URL elle-même.
    cle = cle or frappe.request.args.get("cle") or frappe.get_request_header("X-Cle")
    if not _authentifier(cle):
        return _reponse({"erreur": "Clé invalide."}, 401)
    try:
        message = json.loads(frappe.request.get_data(as_text=True) or "{}")
    except ValueError:
        return _reponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON illisible"}}, 400)
    if isinstance(message, list):
        return _reponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Pas de lots"}}, 400)
    frappe.set_user(A.UTILISATEUR)
    reponse = traiter(message, _appeler)
    if reponse is None:
        return _reponse(None, 202)
    if "result" in reponse and message.get("method") == "tools/call" and not reponse["result"].get("isError"):
        frappe.db.commit()                 # une écriture passée par ChatGPT est acquise
    return _reponse(reponse)
