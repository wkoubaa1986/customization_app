"""Plugin ChatGPT « articles » = second serveur MCP (Streamable HTTP), SÉPARÉ du plugin assistant (05/11/2026).

Autre URL (`…/mcp_articles.serveur?cle=<clé>:<secret>`), autre clé (celle de l'utilisateur technique
articles.chatgpt@aquaworld.local), autres outils (assistant_articles : chercher, fiche, créer, modifier, prix, image).
Dans ChatGPT on le branche comme une application MCP distante à part entière : on peut l'activer sans l'autre.
Même protocole et même mécanique que mcp_chatgpt (JSON-RPC 2.0 : initialize / tools/list / tools/call / ping), servie
par `mcp_chatgpt.servir` avec ce catalogue-ci.
"""
from __future__ import annotations

import frappe

from customization_app import assistant_articles as R
from customization_app import mcp_chatgpt as M

NOM_SERVEUR = "aqua-world-articles"

CATALOGUE = {"nom": NOM_SERVEUR, "version": "1.0.0", "instructions": R.INSTRUCTIONS, "outils": R.OUTILS, "liste": R.outils_mcp}

_appeler = M.appeler_dans(R)


@frappe.whitelist(allow_guest=True, methods=["POST", "GET", "DELETE"])
def serveur(cle=None):
    """Le serveur MCP des articles. POST : un message JSON-RPC. GET : 405. DELETE : 204."""
    return M.servir(cle, R.UTILISATEUR, _appeler, CATALOGUE)
