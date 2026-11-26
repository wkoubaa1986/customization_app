"""Liste d'appels — ce que l'appel change ailleurs (porté du Server Script « update donnee appelle », After Save, qui
n'existait qu'en base ; éteint par patch desactiver_server_script_update_donnee_appelle, 03/10/2026).

Pour chaque ligne cochée « a été appelé » : la fiche client reprend les deux cases « intéressé par l'entretien » et
« envoi SMS » (le script d'origine n'en mettait à jour qu'UNE par passage : `elif`), et les visites concernées
(detail_articles = {article: date}) sont marquées `custom_appelle` sur TOUS les échéanciers soumis du client — un
client regroupé n'a plus qu'une ligne, ses autres échéanciers doivent aussi savoir qu'il a été appelé. Une visite déjà
marquée garde sa date (le script réécrivait la date du jour à chaque enregistrement).

Réponses à choix — décisions du 25/11/2026 (analyse « cartographie entretien » sur la copie de prod du jour) :
- « Fait lui-même » = l'entretien est fait le jour de l'appel (réglage `fait_lui_meme_solde`) : sur chaque échéancier
  du client qui porte un article de la ligne, la plus ancienne visite échue non faite est soldée à la date de l'appel et
  les suivantes sont décalées comme après un achat. Avant, la visite restait ouverte et comptait comme un cycle « sans
  réponse » : 221 des 260 réponses depuis le 01/09 ne soldaient rien, 27 clients relancés étaient à un cycle du seuil.
- « Il va passer au magasin » = rappel dans `jours_rappel_magasin` jours (15) : la relance est reportée d'autant.
- « Fait lui-même » réactive un client « À requalifier », comme « Rendez-vous pris », à condition d'avoir soldé une
  visite (sinon la nuit le requalifierait aussitôt). « Magasin » ne le réactive PAS (relecture du 26/11/2026) : le
  report le protège 15 j, mais ses cycles sans réponse restent et la nuit du jour du rappel le requalifiait (15 sur 15
  sur la copie) ; le report suffit, et ce jour-là la liste Requalification le reprend (clients_reportes_echus).
- Chaque effet d'une ligne est isolé (_etape, relecture du 26/11/2026) : une erreur du marquage des visites n'empêche
  plus « pas de système » → Perdu ni la réactivation d'un rendez-vous.
- Lot 5.1 — les AUTRES visites échues de l'article ne sont PAS marquées « appelées » : mesuré le 25/11/2026 (et
  re-mesuré le 26/11), marquer les 564 visites échues encore non appelées des 355 lignes appelées depuis le 02/11
  faisait monter le compteur de lassitude de 70 clients, dont 64 passaient le seuil de 3 cycles (de 1 à 7 ou 8 cycles
  pour la plupart) et seraient sortis de la relance la nuit même. Une vieille visite est à la place « couverte » par
  l'appel (visite_couverte) : le compteur ne la voit pas, et la liste d'appels ne doit plus la reprendre — la clause
  SQL_VISITE_NON_COUVERTE est à ajouter à la requête des visites candidates (creation_liste_appelle.echeances_a_appeler) ;
  tant qu'elle n'y est pas, le comportement reste celui d'avant (le client revient au bout de `jours_entre_deux_appels`).

Le hook tourne à CHAQUE enregistrement de la liste et à sa validation : tout effet est idempotent (marque déjà posée
conservée, commentaire-témoin par liste pour « Fait lui-même » et « magasin »)."""
from __future__ import annotations

import json

import frappe
from frappe.utils import add_days, cint, date_diff, getdate, nowdate

NE_REPOND_PAS = "Ne répond pas 1er appel"
PAS_DE_SYSTEME = "Il n'y a pas de système de traitement installé"
RENDEZ_VOUS = "Rendez-vous pris"
FAIT_LUI_MEME = "Fait lui-même"
MAGASIN = "Il va passer au magasin"
A_REQUALIFIER = "À requalifier"

# Défauts des réglages lus par RC.config().get(…) (les champs de « Config Relances » arrivent avec la migration).
SOLDE_FAIT_LUI_MEME = 1
JOURS_RAPPEL_MAGASIN = 15

# Débuts des commentaires posés par ce hook : ils servent aussi de TÉMOIN (une liste n'agit qu'une fois par client).
TRACE_FAIT_LUI_MEME = "🔧 Entretien fait par le client lui-même"
TRACE_MAGASIN = "🏪 Passera au magasin"
TRACE_REACTIVATION = "✅ Relance réactivée"

# Lot 5.1 (25/11/2026) : la condition à ajouter aux visites candidates d'une liste d'appels (alias `d` = Maintenance
# Schedule Detail), même règle que visite_couverte. Aucun « % » : elle se colle telle quelle dans une requête à
# paramètres nommés. Mesuré sur la copie de prod (26/11/2026) : 1 765 → 770 visites candidates, 421 → 246 clients ;
# les 175 clients qui sortent ont TOUS été appelés il y a moins de 60 j (108 relançables, les autres exclus de toute
# façon) : ce sont ceux qui seraient revenus en décembre–janvier sur une vieille visite.
SQL_VISITE_NON_COUVERTE = """
          AND NOT EXISTS (SELECT 1 FROM `tabMaintenance Schedule Detail` w
                          WHERE w.parent = d.parent AND w.item_code = d.item_code AND w.name <> d.name
                            AND w.custom_appelle IS NOT NULL AND w.custom_appelle >= d.scheduled_date)"""


# ── Règles pures ─────────────────────────────────────────────────────────────

def _g(r, champ):
    """Lecture d'un champ sur un dict (frappe._dict, ligne SQL) ou sur une ligne de document. PURE."""
    return r.get(champ) if isinstance(r, dict) else getattr(r, champ, None)


def _s(r, champ, valeur):
    if isinstance(r, dict):
        r[champ] = valeur
    else:
        setattr(r, champ, valeur)


def _jj(d) -> str:
    return getdate(d).strftime("%d/%m/%Y") if d else ""


def reglages(cfg=None) -> dict:
    """Les réglages des réponses : défauts tant que « Config Relances » n'a pas ces champs. PURE avec `cfg`."""
    if cfg is None:
        from customization_app import relances_config as RC
        cfg = RC.config()
    return {"fait_lui_meme_solde": bool(cint(cfg.get("fait_lui_meme_solde", SOLDE_FAIT_LUI_MEME))),
            "jours_rappel_magasin": cint(cfg.get("jours_rappel_magasin", JOURS_RAPPEL_MAGASIN))}


def effet_reponse(issue: str, statut: str, visite_soldee: bool = False) -> dict:
    """Ce que la réponse à choix d'un appel écrit sur la fiche client : « pas de système » arrête la relance (Perdu) ;
    « Rendez-vous pris » et « Fait lui-même » réactivent un client « À requalifier » (25/11/2026 : sur les 81
    « À requalifier » qui avaient répondu « Fait lui-même », 56 l'étaient déjà avant de répondre, et la réponse ne les
    faisait jamais revenir).

    Une réponse ne réactive que si elle ENLÈVE la cause de la requalification, sinon la nuit le requalifie (va-et-vient
    de commentaires) : « Fait lui-même » seulement si l'appel a soldé une visite (`visite_soldee` : le compteur de
    lassitude repart alors de zéro, il ne compte que les visites postérieures à la dernière visite faite) ; « Rendez-vous
    pris » toujours (un client avec rendez-vous n'est jamais requalifié). « Il va passer au magasin » JAMAIS (relecture du
    26/11/2026) : le report ne protège que jusqu'au jour du rappel, ses cycles restent, et la nuit de ce jour-là le
    requalifiait — mesuré sur la copie, 15 « À requalifier » sur 15 simulés : réactivés, puis « ⏸️ Relance suspendue »
    à J+15 et liste Requalification à 07:00, exactement comme sans réactivation, avec deux commentaires contradictoires
    et un client affiché « relancé » pendant 15 jours. Le défaut vaut « rien soldé ». Les réponses libres (« Autres
    reponses ») sont lues par qualification_appels le lendemain matin. Un client déjà « Perdu » ne bouge plus. PURE."""
    if issue == PAS_DE_SYSTEME and statut != "Perdu":
        return {"custom_statut_relance": "Perdu"}
    if statut != A_REQUALIFIER:
        return {}
    if issue == RENDEZ_VOUS or (issue == FAIT_LUI_MEME and visite_soldee):
        return {"custom_statut_relance": ""}
    return {}


def date_rappel_magasin(jour, existante, jours=JOURS_RAPPEL_MAGASIN):
    """« Il va passer au magasin » (25/11/2026) : s'il n'est pas passé, on le rappelle `jours` jours après l'appel — la
    relance est reportée jusque-là (ni SMS, ni appel, ni requalification), puis la liste du matin le reprend. Depuis le
    01/09, 41 réponses « magasin » dont 31 laissaient la visite ouverte sans suite. → la nouvelle date, ou None si elle
    n'est pas plus tardive que le report existant (un « rappeler fin décembre » plus lointain reste) ou si le réglage
    vaut 0 (pas de report : un report au jour même ferait rappeler le client dès le lendemain). PURE."""
    jours = cint(jours)
    if jours <= 0:
        return None
    d = add_days(getdate(jour), jours)
    if existante and getdate(existante) >= d:
        return None
    return d


def articles_appeles(detail_articles) -> dict:
    """{article: date} depuis le JSON de la ligne ; vide si illisible. PURE."""
    try:
        d = json.loads(detail_articles or "{}")
        return {k: getdate(v) for k, v in d.items() if k and v and not k.startswith("_")}
    except Exception:
        return {}


def ligne_a_marquer(rows, date_voulue, today):
    """La visite à marquer « appelée » pour un article : celle à la date exacte de la liste, sinon la visite ÉCHUE la plus
    récente non réalisée (la liste porte souvent une date décalée depuis par le cron de nuit : avant, rien n'était marqué,
    125 appels inscrits sur 280 entre le 15/10 et le 02/11/2026). None si aucune. PURE."""
    rows = list(rows or [])
    for r in rows:
        if r.get("scheduled_date") and getdate(r["scheduled_date"]) == getdate(date_voulue):
            return r
    echues = [r for r in rows if r.get("scheduled_date") and getdate(r["scheduled_date"]) <= getdate(today) and not r.get("actual_date")]
    return max(echues, key=lambda r: getdate(r["scheduled_date"])) if echues else None


def visite_faite(r) -> bool:
    """Réalisée : une date réelle ou « Fully Completed ». Une visite faite ne bouge jamais. PURE."""
    return bool(_g(r, "actual_date")) or _g(r, "completion_status") == "Fully Completed"


def visite_couverte(visite, visites_article) -> bool:
    """Lot 5.1 (25/11/2026) — une visite échue non appelée est COUVERTE quand une autre visite du même article, sur le
    même échéancier, a été appelée à une date postérieure ou égale à son échéance : l'appel a eu lieu alors qu'elle était
    déjà due, il valait aussi pour elle. La liste d'appels ne doit pas la reprendre (sinon le client revient tous les
    60 j sur ses vieux cycles : 114 retours attendus en décembre–janvier). On ne la MARQUE pas « appelée » : le compteur
    de lassitude (update_schedule.cycles_sans_reponse_par_client) compte chaque mois d'échéance qui a SMS 1, SMS 2 et
    appel ; marquer ces vieilles visites aurait fait passer 64 clients au-dessus du seuil après un seul appel. Une visite
    échue APRÈS le dernier appel reste un cycle neuf. Même règle que SQL_VISITE_NON_COUVERTE.
    `visites_article` : les visites du même (échéancier, article). PURE."""
    d = _g(visite, "scheduled_date")
    if not d or _g(visite, "custom_appelle") or visite_faite(visite):
        return False
    d = getdate(d)
    return any(_g(w, "custom_appelle") and getdate(_g(w, "custom_appelle")) >= d
               for w in visites_article or [] if w is not visite)


def visite_a_solder(rows, jour):
    """« Fait lui-même » : la visite que l'entretien du client réalise, parmi les visites d'UN article d'un échéancier —
    la PLUS ANCIENNE échue non faite, comme un achat (update_schedule.ligne_a_marquer), mais jamais une visite à venir :
    le client parle de l'entretien en retard. Pas forcément la visite de la liste : 56 des 242 lignes « Fait lui-même »
    depuis le 01/09 avaient une visite ouverte plus ancienne ; la laisser derrière une visite faite, c'est le prochain
    achat qui la solde et repousse tout l'échéancier de plusieurs années. None si rien n'est échu. PURE."""
    jour = getdate(jour)
    echues = [r for r in rows or [] if _g(r, "scheduled_date") and not visite_faite(r)
              and getdate(_g(r, "scheduled_date")) <= jour]
    if not echues:
        return None
    return min(echues, key=lambda r: (getdate(_g(r, "scheduled_date")), _g(r, "idx") or 0))


def solder_fait_lui_meme(rows, jour) -> dict | None:
    """Applique « Fait lui-même » aux visites d'UN article d'un échéancier (objets modifiés sur place).

    La visite à solder (visite_a_solder) reçoit la date de l'appel : réalisée, « Fully Completed », prévue ce jour-là
    comme après un achat — le Server Script actif « Update Schedule based on Real Date » (Before Save d'un document
    soumis) décalerait sinon une 2e fois les visites suivantes de l'écart entre date prévue et date réelle. Les
    visites suivantes non faites sont décalées d'autant ; celles qui passent du passé à l'avenir redeviennent NEUVES
    (SMS et appels effacés, sinon elles ne seraient plus jamais relancées) — exactement la règle de
    update_schedule.shift_schedule_for_delivery. Une visite faite ne bouge jamais.
    → {"ref", "ancienne", "decalage", "deplacees": [(avant, après, {champ: valeur effacée})]} ou None. PURE."""
    from customization_app.Maintenance.update_schedule import CHAMPS_RELANCE
    jour = getdate(jour)
    rows = list(rows or [])
    ref = visite_a_solder(rows, jour)
    if ref is None:
        return None
    ancienne = getdate(_g(ref, "scheduled_date"))
    decalage = date_diff(jour, ancienne)
    _s(ref, "actual_date", jour)
    _s(ref, "completion_status", "Fully Completed")
    _s(ref, "scheduled_date", jour)
    if not _g(ref, "custom_appelle"):
        _s(ref, "custom_appelle", jour)
    deplacees = []
    for r in rows:
        if r is ref or visite_faite(r) or not _g(r, "scheduled_date"):
            continue
        avant = getdate(_g(r, "scheduled_date"))
        if avant <= ancienne:
            continue
        apres = getdate(add_days(avant, decalage))
        _s(r, "scheduled_date", apres)
        effaces = {}
        if avant <= jour < apres:
            for champ in CHAMPS_RELANCE:
                if _g(r, champ):
                    effaces[champ] = _g(r, champ)
                _s(r, champ, None)
        deplacees.append((avant, apres, effaces))
    return {"ref": ref, "ancienne": ancienne, "decalage": decalage, "deplacees": deplacees}


def texte_fait_lui_meme(item_code, ancienne, jour, liste, decalage, deplacees) -> str:
    """La trace sur l'échéancier, en clair. C'est la SEULE mémoire de ce que le décalage efface (l'échéancier n'a pas
    de suivi des modifications) ; le panneau « Historique client » la relit, comme les traces 📅 d'un achat. PURE."""
    from customization_app.Maintenance.update_schedule import _effaces_en_clair
    tete = "{0} — appel du {1}, liste {2} : visite {3} du {4} soldée".format(
        TRACE_FAIT_LUI_MEME, _jj(jour), liste or "?", item_code, _jj(ancienne))
    if not deplacees:
        return tete + "."
    bouts = []
    for avant, apres, effaces in deplacees:
        b = "{0} → {1}".format(_jj(avant), _jj(apres))
        clair = _effaces_en_clair(effaces or {})
        if clair:
            b += " (effacés : {0})".format(clair)
        bouts.append(b)
    return "{0}. {1} visite(s) suivante(s) décalée(s) de {2} j : {3}.".format(tete, len(deplacees), decalage, " ; ".join(bouts))


def texte_client_fait_lui_meme(jour, liste, soldes) -> str:
    """Le témoin sur la fiche client (une fois par liste) : ce qui a été soldé, ou rien. PURE.
    `soldes` : [(échéancier, article, date prévue d'origine)]."""
    tete = "{0} — appel du {1}, liste {2} :".format(TRACE_FAIT_LUI_MEME, _jj(jour), liste)
    if not soldes:
        return tete + " aucune visite échue à solder."
    return "{0} entretien inscrit à la date de l’appel ({1}).".format(
        tete, " ; ".join("{0} {1} du {2}".format(ms, code, _jj(d)) for ms, code, d in soldes))


# ── Base ─────────────────────────────────────────────────────────────────────

def synchroniser_appels(doc, method=None):
    """Hook on_update de Liste Appelle Entretien (chaque enregistrement ET la validation : tout doit être idempotent).
    « Fait lui-même » et « Il va passer au magasin » ne jouent qu'à la VALIDATION de la liste (docstatus 1), quand la
    réponse est définitive (relecture finale du 26/11/2026 : appliqués dès l'enregistrement du brouillon, ils n'étaient
    jamais défaits quand l'opératrice corrigeait sa réponse — visite soldée et suivantes repoussées à tort)."""
    definitif = cint(doc.get("docstatus")) == 1
    for ligne in doc.get("clients") or []:
        if not ligne.get("a_été_appelé") or not ligne.get("client"):
            continue
        try:
            _synchroniser_ligne(ligne, doc.name, definitif=definitif)
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Liste d'appels {doc.name} : ligne {ligne.get('client')}")


def _etape(libelle, liste, client, fn, *args, defaut=None):
    """Un effet de l'appel, isolé (relecture du 26/11/2026) : son échec est journalisé et n'empêche pas les suivants.
    Avant, une erreur du marquage des visites (verrou, délai d'attente, ligne d'échéancier supprimée) emportait le
    statut écrit après lui : un client « pas de système » ne passait pas « Perdu » et recevait encore SMS et appels — et
    une liste validée ne repasse plus jamais par on_update pour le rattraper. → le résultat de `fn`, ou `defaut`."""
    try:
        return fn(*args)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Liste d'appels {liste} : {libelle} {client}")
        return defaut


def _synchroniser_ligne(ligne, liste=None, definitif=True):
    """Les effets d'une ligne appelée, chacun isolé (_etape), dans cet ordre :
    1. les deux cases de la fiche client ;
    2. « Fait lui-même » : visite soldée, suivantes décalées — AVANT le marquage, qui sinon marquerait une visite que le
       décalage efface aussitôt (tout ou rien : en cas d'erreur rien n'est soldé, donc pas de réactivation) ;
    3. le statut de relance (Perdu / réactivé, effet_reponse) — avant le marquage, comme avant le 25/11/2026 ;
    4. la marque « appelé » des visites ;
    5. le report « magasin ».
    `definitif` : la liste est validée — sinon (brouillon) ni « Fait lui-même » ni le report « magasin » ne jouent."""
    liste = liste or ligne.get("parent") or ""
    client = ligne.client
    aujourd_hui = nowdate()
    regl = reglages()
    issue = ligne.get("resume_appel") or ""
    articles = articles_appeles(ligne.get("detail_articles"))
    _etape("cases du client", liste, client, _cases_client, ligne)
    soldee = False
    if issue == FAIT_LUI_MEME and regl["fait_lui_meme_solde"] and definitif:
        soldee = bool(_etape("« Fait lui-même »", liste, client, _fait_lui_meme, client, articles, liste, aujourd_hui, defaut=[]))
    _etape("statut de relance", liste, client, _appliquer_statut, client, issue, soldee, liste, aujourd_hui)
    if articles:
        _etape("marquage des visites", liste, client, _marquer_visites, ligne, articles, aujourd_hui)
    if issue == MAGASIN and definitif:
        _etape("report « magasin »", liste, client, _reporter_magasin, client, liste, aujourd_hui, regl["jours_rappel_magasin"])


def _cases_client(ligne):
    """La fiche client reprend les deux cases « intéressé par l'entretien » et « envoi SMS » de la ligne."""
    client = frappe.db.get_value("Customer", ligne.client, ["custom_intéressé_par_le_service_entretien", "custom_envoi_sms"], as_dict=True)
    if not client:
        return
    maj = {}
    if ligne.get("intéressé_par_le_service_dentretien") and client.custom_intéressé_par_le_service_entretien != ligne.intéressé_par_le_service_dentretien:
        maj["custom_intéressé_par_le_service_entretien"] = ligne.intéressé_par_le_service_dentretien
    if ligne.get("intéressé_par_le_service_de_relance") and client.custom_envoi_sms != ligne.intéressé_par_le_service_de_relance:
        maj["custom_envoi_sms"] = ligne.intéressé_par_le_service_de_relance
    if maj:
        frappe.db.set_value("Customer", ligne.client, maj)


def _appliquer_statut(client, issue, soldee, liste, jour):
    """Issue de l'appel sur la fiche client : Perdu / réactivé (effet_reponse) ; les réponses libres, c'est l'IA du
    lendemain. Idempotent : un client réactivé ou « Perdu » ne repasse plus par ici."""
    if not frappe.db.has_column("Customer", "custom_statut_relance"):
        return
    statut = frappe.db.get_value("Customer", client, "custom_statut_relance") or ""
    maj = effet_reponse(issue, statut, visite_soldee=soldee)
    if not maj:
        return
    frappe.db.set_value("Customer", client, maj)
    if maj.get("custom_statut_relance") == "":
        _commenter_client(client, "{0} : réponse « {1} » à l’appel du {2} (liste {3}).".format(
            TRACE_REACTIVATION, issue, _jj(jour), liste or "?"))


def _marquer_visites(ligne, articles, aujourd_hui):
    """La visite appelée de chaque (échéancier, article) reçoit `custom_appelle` (date déjà posée conservée) et, sur
    « Ne répond pas 1er appel », `custom_1er_appel`. UNE visite par article et par échéancier : les autres visites échues
    sont couvertes par cet appel (visite_couverte), pas marquées — voir l'en-tête du module."""
    rows = frappe.db.sql("""select d.name, d.parent, d.item_code, d.scheduled_date, d.custom_appelle, d.custom_1er_appel, d.actual_date
                            from `tabMaintenance Schedule Detail` d join `tabMaintenance Schedule` ms on ms.name = d.parent
                            where ms.customer = %s and ms.docstatus = 1 and d.item_code in %s""",
                         (ligne.client, tuple(articles)), as_dict=True)
    par_echeancier_article = {}
    for r in rows:
        par_echeancier_article.setdefault((r.parent, r.item_code), []).append(r)
    for (_ech, item_code), lignes_article in par_echeancier_article.items():
        row = ligne_a_marquer(lignes_article, articles[item_code], aujourd_hui)
        if not row:
            continue
        maj = {}
        if not row.custom_appelle:
            maj["custom_appelle"] = aujourd_hui
        if ligne.get("resume_appel") == NE_REPOND_PAS and not row.custom_1er_appel:
            maj["custom_1er_appel"] = aujourd_hui
        if maj:
            frappe.db.set_value("Maintenance Schedule Detail", row.name, maj, update_modified=False)


def _like(texte) -> str:
    return str(texte or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _deja_traite(client, prefixe, liste) -> bool:
    """Le témoin d'une réponse déjà appliquée : un commentaire Info du hook sur la fiche client, pour CETTE liste. La date
    de l'appel n'est écrite nulle part sur la ligne : sans témoin, un enregistrement le lendemain re-solderait une visite
    ou repousserait encore le rappel « magasin »."""
    motif = "%s%%liste %s :%%" % (_like(prefixe), _like(liste))
    return bool(frappe.db.exists("Comment", {"reference_doctype": "Customer", "reference_name": client,
                                             "comment_type": "Info", "content": ["like", motif]}))


def _commenter_client(client, contenu):
    frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": "Customer", "reference_name": client,
                    "content": contenu}).insert(ignore_permissions=True)


def _tracer_echeancier(ms, texte):
    """Commentaire Info sur l'échéancier ; un échec de trace ne défait pas le solde (même règle que update_schedule)."""
    try:
        ms.add_comment("Info", texte)
    except Exception:
        frappe.log_error(frappe.get_traceback(), f"Trace « Fait lui-même » {ms.name}")


def _fait_lui_meme(client, articles, liste, jour) -> list:
    """« Fait lui-même » : l'entretien est inscrit à la date de l'appel.

    QUELS ÉCHÉANCIERS (décision du 25/11/2026) : tous les échéanciers soumis du client qui portent un article de la
    ligne, là où une visite de cet article est ÉCHUE au jour de l'appel — le même périmètre que la marque « appelé ».
    La ligne regroupe depuis le 03/10 tous les échéanciers du client (une ligne par client, la carte cite chaque famille
    due) et la réponse vaut pour l'appel entier ; un doublon de la même machine (164 couples client × famille en ont deux)
    le relancerait sinon six mois plus tard pour rien. Mesuré depuis le 01/09 : 225 lignes sur 260 n'ont qu'un
    échéancier, 18 en ont deux, 3 en ont trois. Jamais une visite à venir, jamais un article absent de la ligne (cet
    appareil n'était pas encore relancé), jamais une visite déjà faite.

    Tout ou rien (point de sauvegarde) ; une seule fois par liste (témoin sur la fiche client), même si la liste est
    réenregistrée un autre jour. → [(échéancier, article, date prévue d'origine)]."""
    if not liste or not articles or _deja_traite(client, TRACE_FAIT_LUI_MEME, liste):
        return []
    from customization_app.Maintenance import update_schedule as U
    noms = frappe.db.sql_list("""select distinct ms.name from `tabMaintenance Schedule` ms
                                 join `tabMaintenance Schedule Detail` d on d.parent = ms.name
                                 where ms.customer = %s and ms.docstatus = 1 and d.item_code in %s
                                 order by ms.name""", (client, tuple(articles)))
    soldes = []
    frappe.db.savepoint("fait_lui_meme")
    try:
        for nom in noms:
            ms = frappe.get_doc("Maintenance Schedule", nom)
            textes = []
            for code in articles:
                res = solder_fait_lui_meme([r for r in ms.schedules if r.item_code == code], jour)
                if not res:
                    continue
                textes.append(texte_fait_lui_meme(code, res["ancienne"], jour, liste, res["decalage"], res["deplacees"]))
                soldes.append((nom, code, res["ancienne"]))
            if not textes:
                continue
            U.extend_schedule_for_sms(ms, aujourd_hui=jour)       # dernière visite soldée : l'article repart
            ms.flags.ignore_permissions = True
            ms.save()
            for t in textes:
                _tracer_echeancier(ms, t)
        _commenter_client(client, texte_client_fait_lui_meme(jour, liste, soldes))
    except Exception:
        frappe.db.rollback(save_point="fait_lui_meme")
        raise
    return soldes


def _reporter_magasin(client, liste, jour, jours):
    """« Il va passer au magasin » : relance reportée à l'appel + `jours` (date_rappel_magasin), une seule fois par
    liste (témoin sur la fiche client : sans lui, chaque enregistrement repousserait encore la date). → date posée ou
    None."""
    if cint(jours) <= 0 or not liste or not frappe.db.has_column("Customer", "custom_relance_reportee_au"):
        return None
    if _deja_traite(client, TRACE_MAGASIN, liste):
        return None
    existante = frappe.db.get_value("Customer", client, "custom_relance_reportee_au")
    d = date_rappel_magasin(jour, existante, jours)
    tete = "{0} — appel du {1}, liste {2} :".format(TRACE_MAGASIN, _jj(jour), liste)
    if d:
        frappe.db.set_value("Customer", client, "custom_relance_reportee_au", d)
        _commenter_client(client, "{0} relance reportée au {1} ; s’il n’est pas passé, il revient dans la liste d’appels.".format(tete, _jj(d)))
    else:
        _commenter_client(client, "{0} relance déjà reportée au {1}, inchangée.".format(tete, _jj(existante)))
    return d
