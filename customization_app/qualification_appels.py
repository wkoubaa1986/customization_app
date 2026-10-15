"""Qualification automatique des réponses d'appel en texte libre (15/10/2026).

Sur 300 appels de requalification, 64 « Autres reponses » : « pas besoin », « pas d'adoucisseur », « pas en Tunisie »,
« injoignable », « rappel fin décembre »… Salma écrit la bonne information, mais rien ne la lisait : le client restait
« À requalifier » pour toujours. Chaque matin (06:30, avant les listes de 07:00), un petit modèle (gpt-4o-mini, la clé
d'AI Settings comme la LCI) classe les réponses libres des listes validées pas encore qualifiées, et le système en tire
les mêmes effets que les réponses à choix : « perdu » → statut Perdu + Non à l'entretien ; « rappeler » → relance
reportée à la date demandée (sinon 3 mois) ; le reste est seulement noté sur la ligne. Chaque effet laisse un
commentaire 🤖 sur la fiche client avec le texte d'origine. Le modèle ne décide jamais d'un rendez-vous ni d'un SMS.
"""
from __future__ import annotations

import json
import re

import frappe
from frappe.utils import add_months, getdate, nowdate

from customization_app.utils.run_safely import run_safely

CATEGORIES = ("perdu", "rappeler", "injoignable", "fait_lui_meme", "rendez_vous", "magasin", "autre")
MODELE_DEFAUT = "gpt-4o-mini"      # bon marché : classer une phrase, pas raisonner
MAX_TOKENS = 120
RAPPEL_DEFAUT_MOIS = 3
LOT_MAX = 200                      # réponses qualifiées par passage
REPONSE_LIBRE = "Autres reponses"

CONSIGNE = """Tu classes la réponse d'un client tunisien, notée en français (parfois en abrégé ou en dialecte) par une
téléopératrice après un appel de relance d'entretien d'un appareil de traitement d'eau (osmoseur, adoucisseur, filtre).
Réponds UNIQUEMENT en JSON : {"categorie": "...", "date": "AAAA-MM-JJ" ou null, "motif": "3 à 8 mots"}.
Catégories :
- perdu : SEULEMENT si c'est définitif — ne veut plus d'entretien, pas besoin, n'a plus l'appareil, pas d'appareil,
  déménagé définitivement, décédé, numéro qui n'est plus le sien, demande à ne plus être appelé ;
- rappeler : demande à être rappelé plus tard, ou absent pour un temps (à l'étranger, pas en Tunisie, en voyage,
  « à partir de telle date ») — date = la date ou le délai demandé si on peut le déduire, sinon null ;
- injoignable : ne répond pas, pas à la maison, boîte vocale, numéro ne passe pas, faux numéro probable ;
- fait_lui_meme : fait l'entretien lui-même ou avec quelqu'un d'autre ;
- rendez_vous : un rendez-vous est réellement fixé (jour connu) ;
- magasin : passera au magasin ;
- autre : tout le reste (hésite, vérifie avec quelqu'un, rappellera lui-même, texte sans réponse du client).
Aujourd'hui : {today}."""


# ── Règles pures ─────────────────────────────────────────────────────────────

def _normaliser(texte) -> str:
    """« Fait lui-même » → « fait_lui_meme » : sans accent, en minuscules, tout séparateur = « _ ». PURE."""
    import unicodedata
    plat = unicodedata.normalize("NFKD", str(texte or "")).encode("ascii", "ignore").decode()
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", plat.lower())).strip("_")


def interpreter(brut: str, today) -> dict:
    """Le JSON du modèle → {categorie, date, motif} sûr : catégorie inconnue = « autre », date illisible ou passée = None.
    PURE."""
    try:
        d = json.loads(brut or "{}")
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    cat = _normaliser(d.get("categorie"))
    if cat not in CATEGORIES:
        cat = "autre"
    date = None
    m = re.search(r"\d{4}-\d{2}-\d{2}", str(d.get("date") or ""))
    if m:
        try:
            date = getdate(m.group(0))
            if date <= getdate(today):
                date = None
        except Exception:
            date = None
    return {"categorie": cat, "date": date, "motif": (str(d.get("motif") or "")).strip()[:120]}


def date_rappel(categorie: str, date, today, defaut_mois=RAPPEL_DEFAUT_MOIS):
    """La date de reprise de la relance pour « rappeler » : celle demandée, sinon dans `defaut_mois`. None sinon. PURE."""
    if categorie != "rappeler":
        return None
    return getdate(date) if date else add_months(getdate(today), defaut_mois)


def effet(categorie: str, statut_actuel: str) -> str:
    """Ce que la qualification change sur la fiche client : 'perdu' | 'rappeler' | ''. Un client déjà « Perdu » ne bouge
    plus (décision humaine ou déjà prise). PURE."""
    if categorie == "perdu" and statut_actuel != "Perdu":
        return "perdu"
    if categorie == "rappeler":
        return "rappeler"
    return ""


def etiquette(q: dict) -> str:
    """Ce qu'on écrit sur la ligne d'appel : « rappeler (2026-12-31) : rappel fin décembre ». PURE."""
    out = q["categorie"]
    if q.get("date"):
        out += " (%s)" % q["date"]
    if q.get("motif"):
        out += " : " + q["motif"]
    return out[:140]


# ── Modèle ───────────────────────────────────────────────────────────────────

def _cle():
    from customization_app.liste_commande_import import _ai_setting
    return (_ai_setting("openai_api_key") or frappe.conf.get("openai_api_key") or "").strip()


def _modele():
    return frappe.conf.get("qualification_openai_model") or MODELE_DEFAUT


def _appeler(cle, modele, texte, today):
    import requests

    messages = [{"role": "system", "content": CONSIGNE.replace("{today}", str(today))}, {"role": "user", "content": texte[:500]}]
    corps = {"model": modele, "messages": messages, "max_completion_tokens": MAX_TOKENS, "response_format": {"type": "json_object"}}
    r = requests.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": "Bearer %s" % cle}, json=corps, timeout=30)
    if r.status_code == 400 and "max_completion_tokens" in r.text:
        corps["max_tokens"] = corps.pop("max_completion_tokens")
        r = requests.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": "Bearer %s" % cle}, json=corps, timeout=30)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def qualifier_texte(texte: str, today=None, cle=None, modele=None) -> dict:
    today = today or nowdate()
    return interpreter(_appeler(cle or _cle(), modele or _modele(), texte, today), today)


# ── Base ─────────────────────────────────────────────────────────────────────

def lignes_a_qualifier(limit=LOT_MAX) -> list:
    """Les réponses libres des listes VALIDÉES pas encore qualifiées, les plus récentes d'abord."""
    if not frappe.db.has_column("Appelle Client", "qualification_ia"):
        return []
    return frappe.db.sql("""select a.name, a.parent, a.client, a.reponse_client, l.type_liste
                            from `tabAppelle Client` a join `tabListe Appelle Entretien` l on l.name = a.parent
                            where l.docstatus = 1 and a.resume_appel = %s and ifnull(a.reponse_client, '') <> ''
                              and ifnull(a.qualification_ia, '') = '' and ifnull(a.client, '') <> ''
                            order by l.creation desc limit %s""", (REPONSE_LIBRE, int(limit)), as_dict=True)


def appliquer(client: str, q: dict, texte: str, today, source: str) -> str:
    """Les effets d'une qualification sur la fiche client, avec une trace. → effet appliqué ('' si aucun)."""
    statut = frappe.db.get_value("Customer", client, "custom_statut_relance") or ""
    e = effet(q["categorie"], statut)
    if e == "perdu":
        frappe.db.set_value("Customer", client, {"custom_statut_relance": "Perdu", "custom_intéressé_par_le_service_entretien": "Non"})
        _commenter(client, "🤖 Relance arrêtée (Perdu, Non à l’entretien) d’après la réponse de %s : « %s » — %s." % (source, texte.strip()[:200], q.get("motif") or q["categorie"]))
    elif e == "rappeler":
        d = date_rappel(q["categorie"], q.get("date"), today)
        frappe.db.set_value("Customer", client, "custom_relance_reportee_au", d)
        _commenter(client, "🤖 Relance reportée au %s d’après la réponse de %s : « %s »." % (d.strftime("%d/%m/%Y"), source, texte.strip()[:200]))
    return e


def _commenter(client, contenu):
    frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": "Customer", "reference_name": client,
                    "content": contenu}).insert(ignore_permissions=True)


def qualifier_reponses(dry_run: bool = False, limit=LOT_MAX) -> dict:
    """Le passage du matin : classe chaque réponse libre, l'écrit sur la ligne, applique l'effet. Chaque ligne est
    isolée ; sans clé, rien ne se passe (journalisé). dry_run : appelle le modèle, n'écrit rien."""
    cle = _cle()
    resume = {"lignes": 0, "perdu": 0, "rappeler": 0, "notees": 0, "erreurs": 0, "sans_cle": not cle}
    if not cle:
        log("[QUALIF] aucune clé OpenAI (AI Settings > openai_api_key) : rien à faire")
        return resume
    today, modele = nowdate(), _modele()
    for l in lignes_a_qualifier(limit):
        resume["lignes"] += 1
        try:
            q = qualifier_texte(l.reponse_client, today, cle, modele)
            if dry_run:
                log(f"[QUALIF dry] {l.client} ({l.type_liste}) « {l.reponse_client.strip()[:80]} » → {etiquette(q)}")
                continue
            frappe.db.savepoint("qualif")
            frappe.db.set_value("Appelle Client", l.name, "qualification_ia", etiquette(q), update_modified=False)
            e = appliquer(l.client, q, l.reponse_client, today, "la liste %s" % l.parent)
            resume[e or "notees"] += 1
            log(f"[QUALIF] {l.client} ({l.type_liste}) « {l.reponse_client.strip()[:80]} » → {etiquette(q)}{' → ' + e if e else ''}")
        except Exception:
            if not dry_run:
                frappe.db.rollback(save_point="qualif")
            resume["erreurs"] += 1
            frappe.log_error(frappe.get_traceback(), f"Qualification IA — ligne {l.name} ({l.client})")
    log("[QUALIF SUMMARY] %s" % resume)
    return resume


def log(msg):
    from customization_app import relances_config as RC
    RC.journal("qualification_appels").info(msg)


def run_cron():
    return run_safely("Cron - Qualification IA des réponses d'appel", qualifier_reponses)
