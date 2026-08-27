"""Page publique /rdv — prise de rendez-vous client (OTP par SMS).

Toute la logique vit dans customization_app/portail_rdv.py ; cette page ne
fait que servir l'écran. no_cache : la page porte un formulaire à étapes,
aucune raison de la laisser vieillir dans un cache.
"""

import frappe


def get_context(context):
    context.no_cache = 1
    # Coordonnées affichées sous le logo — réglées dans Config Portail RDV.
    portail = frappe.db.get_singles_dict("Config Portail RDV") or {}
    context.adresse = portail.get("adresse") or ""
    context.site_web = (portail.get("site_web") or "").strip().rstrip("/")
    whatsapp = "".join(c for c in (portail.get("whatsapp") or "") if c.isdigit())
    context.whatsapp = whatsapp[-8:] if len(whatsapp) >= 8 else ""
    # L'adresse est cliquable : lien Google Maps du réglage, sinon une
    # recherche Maps construite depuis l'adresse elle-même.
    context.lien_maps = (portail.get("lien_google_maps") or "").strip()
    if not context.lien_maps and context.adresse:
        from urllib.parse import quote
        context.lien_maps = ("https://www.google.com/maps/search/?api=1&query="
                             + quote(context.adresse))
    context.tel_commercial = (portail.get("tel_commercial") or "").strip()
    context.tel_support = (portail.get("tel_support") or "").strip()
    return context
