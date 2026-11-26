"""Vieux échéanciers qui suivent un SAC DE SEL ou un PORTE-MEMBRANE au lieu d'une machine (proposition 9 de l'analyse
« cartographie entretien » du 25/11/2026 ; corrigé le 26/11/2026, défauts prudents retenus par le propriétaire).

LE CAS MESURÉ (copie de prod du 25/11/2026) : 73 échéanciers soumis, nés en 2024-2025 de l'ancien script (un achat de
sel créait un échéancier « S-S25 »), n'ont AUCUNE machine d'une famille du réglage : 56 S-S25 (Sac de sel -25kg),
12 P-M-1812, 2 P-M-3013, 1 P-M-3012 (porte-membranes), 1 R-L (résine), 1 BRZS-DN15-220V (électrovanne). Ils portent 174
visites échues jamais soldées (update_schedule.find_machine_schedules ne trouve un échéancier que par la famille de sa
machine : un achat de sel ou d'entretien ne les touche jamais) et ont reçu 32 SMS1 depuis le 01/09/2026 (« votre
appareil de traitement d'eau »). Pire : faute d'échéancier ADOUCISSEUR, un achat de sel en a fait DEVINER un (Ad-MB-30L)
chez 10 de ces clients — le client est alors suivi deux fois.

FAMILLES (vérifiées dans relances_config le 26/11/2026) : le sel est un consommable de l'ADOUCISSEUR (groupe
« Consommables & Accessoires », et le nom « sel » → ADOUCISSEUR dans update_schedule.familles_consommable) ; le groupe
« Porte-membranes RO » n'est dans aucune famille : 1812 = membrane domestique → RO_DOM, 3012 / 3013 = membrane
commerciale → RO_COM (familles_consommable : 3012/3013 → RO_COM). La résine et l'électrovanne restent un DOUTE.

RÈGLE, par client et par famille visée :
1. le client a un AUTRE échéancier soumis de cette famille → l'échéancier hérité est ANNULÉ (jamais supprimé : il reste
   consultable), avec une trace 🧹 qui cite les visites réalisées qu'il portait, posée sur l'échéancier annulé, dans le
   fil de la fiche client ET sur l'échéancier GARDÉ (relecture du 26/11/2026 : « Historique client » ne lit que les
   échéanciers soumis et, sur la fiche client, les seules traces ⏸️ / ✅ — sur le gardé, la trace et l'histoire de
   l'annulé restent visibles dans son journal) ;
2. sinon → UN échéancier hérité (celui qui a le plus de visites réalisées, puis le plus récent) devient une devinette :
   son article est remplacé par la machine type de la famille (update_schedule.get_default_item_for_family, comme une
   devinette) sur la ligne machine ET sur les visites — dates, SMS, appels et historique intouchés — pour que les achats
   de sel / d'entretien le soldent ; les autres échéanciers hérités de la même famille chez ce client sont annulés
   comme au 1 ;
3. doute → laissé, cité dans le compte rendu : article sans famille sûre (résine, électrovanne), porte-membrane chez un
   client qui n'a qu'un osmoseur d'une AUTRE famille, commande encore dans la fenêtre du passage de nuit (par prudence :
   jusqu'au lot 4 du 26/11/2026, un échéancier annulé ne « couvrait » plus ses commandes et la nuit les relisait ; aucune
   sur la copie), pas de machine type réglée.
Les adoucisseurs DEVINÉS en double (machine type seule, née d'une commande qui ne vend pas d'adoucisseur) chez ces
clients sont annulés s'ils n'ont aucune histoire propre (update_schedule.doublon_sans_histoire_propre) face à un autre
échéancier de la famille qui n'est pas une devinette. Mesuré : les 10 devinés nés d'un achat de sel sont les SEULS
adoucisseurs de leur client et portent l'achat qui les a créés → gardés ; c'est l'échéancier « sel » qui est annulé (1).

ESSAI SUR LA COPIE (sans trace, 26/11/2026) : 43 annulés (30 « sel », 13 porte-membranes ; le client avait déjà la
famille), 28 convertis (26 « sel » → Ad-MB-30L, 2 P-M-1812 → AP-M-AJ-5-SM ; 3 clients avaient deux « sel » : le plus
récent converti, l'autre annulé), 2 laissés (R-L, BRZS-DN15-220V), 11 devinés gardés (10 Ad-MB-30L nés d'un achat de sel,
1 AP-M-AJ-5-SM), 0 deviné annulé, 0 erreur. Chez les 62 clients : 129 → 86 échéanciers soumis, 240 → 133 visites échues
ouvertes, 15 → 12 clients à appeler, cycles de lassitude 43 → 35 (« À requalifier » : 44 → 28). Nuit simulée puis SMS de
10:00 en aperçu : 2 SMS1 de moins (ils partaient sur des échéanciers hérités annulés), et la nuit solde enfin une devinette
convertie (MAT-MSH-2025-00487, Belhssan Chikh, par SAL-ORD-2026-03827 du 18/09/2026, jusque-là sans effet).
Rejoué après la relecture du 26/11/2026 : mêmes chiffres ; les 43 traces 🧹 se lisent dans « Historique client » (sur
l'échéancier gardé) ; 14 « à surveiller » (3 convertis et 11 devinés gardés qui portent une visite réalisée) ; 0 ligne
d'appel vivante à réécrire sur la copie (un brouillon de test qui citait MAT-MSH-2025-00453 et MAT-MSH-2025-00742 :
renvoyé vers MAT-MSH-2024-00382 / {"Ad-MB-25L": …} et {"Ad-MB-30L": …}, puis réenregistré sans erreur).

LISTES D'APPELS (relecture du 26/11/2026). Une ligne d'appel (Appelle Client) cite un échéancier
(`échéancier_dentretien`) et ses articles (`detail_articles`, JSON {article: date}) ; liste_appels (marque « appelé »,
« Fait lui-même ») et liste_validation (« pas de système ») cherchent les visites PAR ARTICLE, et le 2e appel recopie la
ligne. Deux pièges, réglés ici dans les listes encore VIVANTES — brouillons, et lignes de listes validées que le 2e
appel relira (ligne_vivante) ; l'historique n'est pas touché :
- Frappe refuse d'enregistrer ou de valider une liste qui cite un échéancier ANNULÉ (CancelledLinkError, reproduit) :
  le brouillon du jour serait bloqué, et sa validation automatique de 08:00 / 19:00 échouerait à chaque passage. La
  ligne est renvoyée vers l'échéancier gardé ;
- après une conversion, une ligne qui cite encore S-S25 ne trouverait plus la visite (devenue Ad-MB-30L) : l'appel ne
  serait pas inscrit, « Fait lui-même » ne solderait rien, et la Relance du 2e appel serait « aveugle »
  (MAT-MSH-2025-00742 : LIST-20-11-2026-00002 → Relance LIST-23-11-2026-00001 avec {"S-S25": …}). La clé est renommée
  vers le nouvel article (articles_renommes) ; elle reste aussi si un autre échéancier soumis du client porte encore
  l'ancien article.
Un brouillon réécrit voit sa date de modification relevée : un formulaire resté ouvert pendant la migration doit être
rechargé (sinon il renverrait l'ancienne ligne). Mesuré sur la copie : 0 ligne vivante concernée (les 207 lignes qui
citent ces échéanciers ou leurs articles sont de l'historique) — c'est un risque lié au moment du déploiement.

À SURVEILLER (relecture du 26/11/2026) : la nuit ne retire une devinette, à la vente de la vraie machine, que si elle n'a
AUCUNE visite réalisée (update_schedule._retirer_echeancier_devine). Un échéancier converti qui porte déjà une visite
réalisée (3 sur la copie : MAT-MSH-2024-00245, MAT-MSH-2025-00416, MAT-MSH-2025-00379), comme les devinés gardés (11),
resterait alors à côté de l'échéancier de la vraie machine : ils sont listés dans le compte rendu (« à surveiller »).

Hors périmètre, cités : les échéanciers qui ont une vraie machine ET une ligne S-S25 (4 sur la copie :
MAT-MSH-2025-00198, 00627, 00669, 00737) — leur ligne « sel » double celle de l'adoucisseur, à décider à part.
Annulation : ignore_links (des lignes d'appel validées les citent). Écritures en SQL direct pour le remplacement et les
lignes d'appel (la table des machines n'est pas modifiable après validation ; aucun enregistrement, donc ni hook ni
Server Script).
Chaque client est isolé (point de sauvegarde). Rejouable sans effet : un échéancier annulé ou converti n'est plus
« sans famille ». → compte rendu (aussi dans le Journal des erreurs)."""
import json

import frappe
from frappe.utils import add_months, cint, getdate, nowdate

from customization_app import relances_config as RC
from customization_app.Maintenance import update_schedule as U

FAMILLE_DE_L_ARTICLE = {"S-S25": "ADOUCISSEUR", "P-M-1812": "RO_DOM", "P-M-3012": "RO_COM", "P-M-3013": "RO_COM"}
FAMILLES_OSMOSE = ("RO_DOM", "RO_COM", "RO_IND", "BIO", "FONTAINE")
LIBELLE = {"ADOUCISSEUR": "adoucisseur", "RO_DOM": "osmoseur domestique", "RO_COM": "osmoseur commercial"}


# ── Règles PURES ─────────────────────────────────────────────────────────────

def famille_visee(items):
    """La famille que suit vraiment un échéancier hérité : une seule machine, connue de FAMILLE_DE_L_ARTICLE. PURE."""
    codes = list(dict.fromkeys(c for c in items or [] if c))
    return FAMILLE_DE_L_ARTICLE.get(codes[0]) if len(codes) == 1 else None


def plan_client(heritages, autres, article_type) -> list:
    """Ce qu'on fait des échéanciers hérités d'UN client. PURE.
    `heritages` : [{ms, item_code, famille (ou None), creation, realisees: [commandes], recente: bool}] ;
    `autres` : ses autres échéanciers soumis [{ms, familles: set}] ; `article_type` : {famille: machine type}.
    → [{ms, action: annuler | remplacer | laisser, motif, vers, garde}]"""
    out, groupes = [], {}
    for h in heritages:
        if not h.get("famille"):
            out.append({"ms": h["ms"], "action": "laisser", "vers": None, "garde": None,
                        "motif": "article %s sans famille sûre" % h.get("item_code")})
        else:
            groupes.setdefault(h["famille"], []).append(h)
    for fam in sorted(groupes):
        groupe = groupes[fam]
        porteurs = sorted(o["ms"] for o in autres if fam in (o.get("familles") or ()))
        if porteurs:
            decisions = [{"ms": h["ms"], "action": "annuler", "vers": None, "garde": porteurs[0],
                          "motif": "le client a déjà l'échéancier %s (%s)" % (", ".join(porteurs), LIBELLE.get(fam, fam))}
                         for h in groupe]
        else:
            voisins = sorted({f for o in autres for f in (o.get("familles") or ()) if f in FAMILLES_OSMOSE}) \
                if fam in FAMILLES_OSMOSE else []
            type_code = (article_type or {}).get(fam)
            if voisins or not type_code:
                motif = ("le client n'a qu'un osmoseur d'une autre famille (%s) : lequel porte ce porte-membrane ?" % ", ".join(voisins)
                         if voisins else "pas de machine type réglée pour la famille %s" % fam)
                out += [{"ms": h["ms"], "action": "laisser", "vers": None, "garde": None, "motif": motif} for h in groupe]
                continue
            garde = max(groupe, key=lambda h: (len(h.get("realisees") or []), str(h.get("creation") or ""), h["ms"]))
            decisions = [{"ms": garde["ms"], "action": "remplacer", "vers": type_code, "garde": None,
                          "motif": "aucun autre échéancier %s : devient une devinette %s" % (LIBELLE.get(fam, fam), type_code)}]
            decisions += [{"ms": h["ms"], "action": "annuler", "vers": None, "garde": garde["ms"],
                           "motif": "doublon de %s, converti en %s" % (garde["ms"], type_code)} for h in groupe if h is not garde]
        recentes = {h["ms"] for h in groupe if h.get("recente")}
        for d in decisions:
            if d["action"] == "annuler" and d["ms"] in recentes:
                d.update(action="laisser", vers=None, garde=None,
                         motif="commande encore dans la fenêtre du passage de nuit : annuler la ferait relire")
        out += decisions
    return out


def devines_a_annuler(echeanciers) -> list:
    """Les échéanciers DEVINÉS en double d'une famille chez un client : une devinette qui n'a aucune histoire propre
    (update_schedule.doublon_sans_histoire_propre) face aux échéanciers de la famille qui ne sont pas des devinettes.
    PURE. `echeanciers` : [{ms, devine: bool, realisees: [commandes]}] — ceux qui RESTENT après le plan."""
    vrais = [e for e in echeanciers if not e.get("devine")]
    if not vrais:
        return []
    leurs = [so for e in vrais for so in e.get("realisees") or []]
    return sorted(e["ms"] for e in echeanciers if e.get("devine") and U.doublon_sans_histoire_propre(e.get("realisees") or [], leurs))


def _jj(d):
    return getdate(d).strftime("%d/%m/%Y") if d else "—"


def texte_annulation(ms, item, motif, realisees, jour, devine=False) -> str:
    """La trace d'une annulation (échéancier et fiche client). PURE. `realisees` : [(date, commande)] ; `devine` :
    une devinette en double (elle suit bien une machine, la machine type de sa famille)."""
    if devine:
        t = "🧹 Échéancier deviné %s (%s) annulé le %s. %s." % (ms, item.get("item_code"), _jj(jour), motif[:1].upper() + motif[1:])
    else:
        t = ("🧹 Échéancier hérité %s annulé le %s : il suivait %s (%s) au lieu d'une machine, jamais soldé par un achat. %s."
             % (ms, _jj(jour), item.get("item_code"), item.get("item_name") or "?", motif[:1].upper() + motif[1:]))
    if realisees:
        t += " Visites réalisées qu'il portait : %s." % ", ".join("%s (%s)" % (_jj(d), so or "à la main") for d, so in realisees)
    return t + " Il reste consultable (annulé)."


def texte_pour_le_garde(ms, item, realisees, jour, devine=False) -> str:
    """La trace posée sur l'échéancier GARDÉ quand un doublon est annulé (relecture du 26/11/2026) : « Historique client »
    ne lit que les échéanciers soumis, c'est là que l'histoire de l'annulé reste visible. PURE."""
    t = "🧹 L'échéancier %s %s (%s%s) de ce client a été annulé le %s : il doublait celui-ci." % (
        "deviné" if devine else "hérité", ms, item.get("item_code"),
        "" if devine or not item.get("item_name") else ", " + item["item_name"], _jj(jour))
    if realisees:
        t += " Visites réalisées qu'il portait : %s." % ", ".join("%s (%s)" % (_jj(d), so or "à la main") for d, so in realisees)
    return t + " Il reste consultable (annulé)."


def ligne_vivante(l):
    """Une ligne d'appel encore LUE par les listes, ou None (historique) — relecture du 26/11/2026. PURE.
    « brouillon » : la liste n'est pas validée (enregistrée, appelée, validée à 08:00 / 19:00) ;
    « a_relire » : liste validée pas encore reprise (liste_2iéme_relance = 0) dont le 2e appel relira la ligne
    (creation_liste_appelle._generate_second_call_list) : un « Ne répond pas 1er appel » coché d'une liste qui n'est ni
    Relance ni Hors secteur, ou une ligne non appelée d'une liste Relance (_reprises_relance)."""
    if cint(l.get("docstatus")) == 0:
        return "brouillon"
    if cint(l.get("docstatus")) != 1 or cint(l.get("r2")):
        return None
    type_liste = l.get("type_liste") or ""
    if type_liste not in ("Relance", "Hors secteur") and l.get("resume_appel") == "Ne répond pas 1er appel" \
            and cint(l.get("appele")):
        return "a_relire"
    if type_liste == "Relance" and not cint(l.get("appele")):
        return "a_relire"
    return None


def articles_renommes(detail_articles, correspondance, encore_portes=()):
    """Le JSON {article: date} d'une ligne d'appel, l'ancien article renommé vers le nouveau (`correspondance`), ou None
    s'il ne change pas (ou s'il est illisible). L'ancien est gardé à côté du nouveau quand un autre échéancier soumis du
    client le porte encore (`encore_portes`) ; si le nouveau est déjà cité, sa date reste. Les clés « _… » (méta) ne sont
    jamais renommées. PURE."""
    try:
        d = json.loads(detail_articles or "{}")
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    out = {}
    for k, v in d.items():
        n = None if str(k).startswith("_") else (correspondance or {}).get(k)
        if n and n != k:
            if k in (encore_portes or ()):
                out.setdefault(k, v)
            if n not in d:
                out.setdefault(n, v)
        else:
            out.setdefault(k, v)
    if list(out.items()) == list(d.items()):
        return None
    return json.dumps(out)


def texte_remplacement(ms, ancien, nouveau, nb_visites, jour) -> str:
    """La trace d'un remplacement d'article. PURE."""
    return ("🔁 Échéancier hérité %s, le %s : l'article %s (%s) est remplacé par %s (%s), la machine type de sa famille, "
            "sur la ligne machine et ses %d visites — dates, SMS et appels inchangés. Les achats de consommables et "
            "d'entretien le soldent désormais." % (ms, _jj(jour), ancien.get("item_code"), ancien.get("item_name") or "?",
                                                   nouveau.get("item_code"), nouveau.get("item_name") or "?", nb_visites))


# ── Base ─────────────────────────────────────────────────────────────────────

def _charger(fams, cache):
    """{échéancier soumis: {ms, customer, creation, items, familles, realisees, dates_realisees, origine}} — trois requêtes."""
    par_ms = {}
    for ms, customer, creation, code, so in frappe.db.sql("""
            select ms.name, ms.customer, ms.creation, msi.item_code, ifnull(msi.sales_order, '')
            from `tabMaintenance Schedule` ms join `tabMaintenance Schedule Item` msi on msi.parent = ms.name
            where ms.docstatus = 1 order by ms.name, msi.idx"""):
        e = par_ms.setdefault(ms, {"ms": ms, "customer": customer, "creation": creation, "items": [], "familles": set(),
                                   "realisees": [], "dates_realisees": [], "commandes": set(), "origine": so})
        e["items"].append(code)
        if so:
            e["commandes"].add(so)
        f = U.famille_machine(U._item(code, cache), fams)
        if f:
            e["familles"].add(f)
    for parent, so, d in frappe.db.sql("""
            select d.parent, ifnull(d.custom_sales_order, ''), d.actual_date from `tabMaintenance Schedule Detail` d
            join `tabMaintenance Schedule` ms on ms.name = d.parent
            where ms.docstatus = 1 and d.actual_date is not null order by d.parent, d.actual_date"""):
        if parent in par_ms:
            par_ms[parent]["realisees"].append(so.strip())
            par_ms[parent]["dates_realisees"].append((d, so.strip()))
            if so.strip():
                par_ms[parent]["commandes"].add(so.strip())
    return par_ms


def _commandes_recentes(commandes, cfg, jour) -> set:
    """Les commandes livrées dans la fenêtre du passage de nuit (update_schedule.commandes_a_traiter) : un échéancier qui
    les porte, une fois annulé, ne les « couvrirait » plus et la nuit les relirait."""
    if not commandes:
        return set()
    debut = add_months(getdate(jour), -(cint(cfg.get("fenetre_mois_avant")) or 4))
    return set(frappe.db.sql_list("select name from `tabSales Order` where name in %s and delivery_date >= %s",
                                  (tuple(commandes), debut)))


def _est_devine(e, fam, type_code, cache, fams) -> bool:
    """La machine type seule, née d'une commande qui ne vend PAS cette famille (même règle que _retirer_echeancier_devine)."""
    return bool(type_code) and e["items"] == [type_code] and U.commande_vend_la_famille(e["origine"], fam, fams, cache) is False


def _item_de(code):
    return frappe.db.get_value("Item", code, ["item_code", "item_name"], as_dict=True) or {"item_code": code}


def _annuler(e, motif, jour, devine=False):
    """Annule l'échéancier ; trace 🧹 sur lui et dans le fil de la fiche client (« Historique client » ne lit ni l'un
    ni l'autre : la trace visible est posée sur l'échéancier gardé, _tracer_le_garde)."""
    doc = frappe.get_doc("Maintenance Schedule", e["ms"])
    doc.flags.ignore_links = doc.flags.ignore_permissions = True
    doc.cancel()
    texte = texte_annulation(e["ms"], _item_de(e["items"][0]), motif, e["dates_realisees"], jour, devine)
    doc.add_comment("Info", texte)
    frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": "Customer",
                    "reference_name": e["customer"], "content": texte}).insert(ignore_permissions=True)


def _tracer_le_garde(e, garde, jour, devine=False):
    """La trace 🧹 de l'annulation de `e` sur l'échéancier gardé, s'il est soumis : c'est elle que « Historique client »
    montre (son journal relit les commentaires Info des échéanciers soumis)."""
    if garde and frappe.db.get_value("Maintenance Schedule", garde, "docstatus") == 1:
        frappe.get_doc("Maintenance Schedule", garde).add_comment(
            "Info", texte_pour_le_garde(e["ms"], _item_de(e["items"][0]), e["dates_realisees"], jour, devine))


def _article_de(e, fam, fams, cache):
    """Le premier article de l'échéancier `e` qui est une machine de la famille `fam` (None sinon)."""
    for code in dict.fromkeys(e["items"]):
        if U.famille_machine(U._item(code, cache), fams) == fam:
            return code
    return None


def _reecrire_lignes(customer, renvois, correspondance) -> list:
    """Les lignes d'appel VIVANTES (ligne_vivante) du client : celles qui citent un échéancier annulé sont renvoyées vers
    l'échéancier gardé (`renvois`), et leurs articles renommés (`correspondance`). SQL direct ; un brouillon réécrit voit
    sa date de modification relevée (un formulaire resté ouvert doit être rechargé). → lignes du compte rendu."""
    if not (renvois or correspondance):
        return []
    lignes = frappe.db.sql("""
        select a.name, a.idx, a.parent, a.`échéancier_dentretien` as ech, a.detail_articles, a.resume_appel,
               ifnull(a.`a_été_appelé`, 0) as appele, l.docstatus, l.type_liste, ifnull(l.`liste_2iéme_relance`, 0) as r2
        from `tabAppelle Client` a join `tabListe Appelle Entretien` l on l.name = a.parent
        where a.parenttype = 'Liste Appelle Entretien' and l.docstatus < 2
          and (a.client = %s or a.`échéancier_dentretien` in %s)
        order by a.parent, a.idx""", (customer, tuple(renvois) or ("",)), as_dict=True)
    encore = set(frappe.db.sql_list("""select distinct d.item_code from `tabMaintenance Schedule Detail` d
                                       join `tabMaintenance Schedule` ms on ms.name = d.parent
                                       where ms.customer = %s and ms.docstatus = 1""", (customer,)))
    out, brouillons = [], set()
    for l in lignes:
        quoi = ligne_vivante(l)
        if not quoi:
            continue
        ech = renvois.get(l.ech) or l.ech
        detail = articles_renommes(l.detail_articles, correspondance, encore)
        if ech == l.ech and detail is None:
            continue
        frappe.db.sql("update `tabAppelle Client` set `échéancier_dentretien` = %s, detail_articles = %s where name = %s",
                      (ech, l.detail_articles if detail is None else detail, l.name))
        if quoi == "brouillon":
            brouillons.add(l.parent)
        bouts = []
        if ech != l.ech:
            bouts.append("échéancier %s → %s" % (l.ech, ech))
        if detail is not None:
            bouts.append("articles %s → %s" % (l.detail_articles, detail))
        out.append("%s ligne %s (%s, %s) : %s" % (l.parent, l.idx, customer,
                                                  "brouillon" if quoi == "brouillon" else "relue par le 2e appel",
                                                  " ; ".join(bouts)))
    if brouillons:
        frappe.db.sql("update `tabListe Appelle Entretien` set modified = %s where name in %s and docstatus = 0",
                      (frappe.utils.now(), tuple(brouillons)))
    return out


def _remplacer(e, type_code, jour):
    ancien = frappe.db.get_value("Item", e["items"][0], ["item_code", "item_name"], as_dict=True) or {"item_code": e["items"][0]}
    nouveau = frappe.db.get_value("Item", type_code, ["item_code", "item_name", "description"], as_dict=True)
    frappe.db.sql("""update `tabMaintenance Schedule Item` set item_code = %s, item_name = %s, description = %s
                     where parent = %s and item_code = %s""",
                  (nouveau.item_code, nouveau.item_name, nouveau.description or nouveau.item_name, e["ms"], ancien["item_code"]))
    n = frappe.db.sql("""update `tabMaintenance Schedule Detail` set item_code = %s, item_name = %s
                         where parent = %s and item_code = %s""", (nouveau.item_code, nouveau.item_name, e["ms"], ancien["item_code"]))
    nb = frappe.db.count("Maintenance Schedule Detail", {"parent": e["ms"], "item_code": nouveau.item_code})
    # La date de modification change : un formulaire resté ouvert ne réécrira pas l'ancien article.
    frappe.db.set_value("Maintenance Schedule", e["ms"], "modified", frappe.utils.now(), update_modified=False)
    frappe.get_doc("Maintenance Schedule", e["ms"]).add_comment("Info", texte_remplacement(e["ms"], ancien, nouveau, nb, jour))
    return n


def resoudre(correspondance) -> dict:
    """Suit les chaînes d'une correspondance ({a: b, b: c} → {a: c, b: c}) : un échéancier annulé renvoie vers un
    gardé qui peut lui-même être un deviné annulé ensuite. Une boucle s'arrête au dernier nom vu. PURE."""
    out = {}
    for k in correspondance or {}:
        vus, v = {k}, correspondance[k]
        while v in correspondance and v not in vus:
            vus.add(v)
            v = correspondance[v]
        out[k] = v
    return out


def a_surveiller(ms, customer, quoi, dates_realisees) -> str:
    """La ligne « à surveiller » d'un échéancier qui suit la machine type mais porte déjà une visite réalisée (relecture
    du 26/11/2026) : update_schedule._retirer_echeancier_devine ne retire qu'une devinette SANS visite réalisée, il
    resterait donc à côté de l'échéancier de la vraie machine quand elle sera vendue. PURE."""
    return ("%s (%s) : %s, %d visite(s) réalisée(s) (%s) — à la vente d'une vraie machine de la famille, la nuit le "
            "gardera à côté de son échéancier : à annuler à la main" % (
                ms, customer, quoi, len(dates_realisees),
                ", ".join("%s %s" % (_jj(d), so or "à la main") for d, so in dates_realisees)))


def executer(jour=None) -> dict:
    jour = getdate(jour or nowdate())
    cfg, fams, cache = RC.config(), RC.familles(), {}
    article_type = {f["code"]: f["article_type"] for f in fams if f.get("article_type")}
    par_ms = _charger(fams, cache)
    heritages = {n: e for n, e in par_ms.items() if not e["familles"]}
    mixtes = sorted(n for n, e in par_ms.items() if e["familles"] and any(c in FAMILLE_DE_L_ARTICLE for c in e["items"]))
    par_client = {}
    for n, e in par_ms.items():
        par_client.setdefault(e["customer"], []).append(e)
    recentes = _commandes_recentes({so for e in heritages.values() for so in e["commandes"]}, cfg, jour)
    bilan = {"annules": [], "remplaces": [], "laisses": [], "devines_annules": [], "devines_gardes": [], "a_surveiller": [],
             "lignes_appel": [], "erreurs": [], "mixtes": mixtes}
    for customer in sorted({e["customer"] for e in heritages.values()}):
        siens = par_client[customer]
        h = [{"ms": e["ms"], "item_code": e["items"][0] if e["items"] else "", "famille": famille_visee(e["items"]),
              "creation": e["creation"], "realisees": e["realisees"], "recente": bool(e["commandes"] & recentes)}
             for e in siens if e["ms"] in heritages]
        autres = [{"ms": e["ms"], "familles": e["familles"]} for e in siens if e["ms"] not in heritages]
        plan = plan_client(h, autres, article_type)
        famille_de = {x["ms"]: x["famille"] for x in h}
        item_de = {x["ms"]: x["item_code"] for x in h}
        convertis = {d["ms"]: d["vers"] for d in plan if d["action"] == "remplacer"}
        frappe.db.savepoint("heritage_client")
        try:
            faits, renvois, correspondance, a_tracer = [], {}, {}, []

            def noter_annulation(ms, garde, ancien, nouveau, devine):
                renvois[ms] = garde
                if ancien and nouveau and ancien != nouveau:
                    correspondance.setdefault(ancien, nouveau)
                a_tracer.append((par_ms[ms], devine))

            for d in plan:
                e = par_ms[d["ms"]]
                if d["action"] == "annuler":
                    _annuler(e, d["motif"], jour)
                    fam, garde = famille_de[d["ms"]], d["garde"]
                    nouveau = convertis.get(garde) or (_article_de(par_ms[garde], fam, fams, cache) if garde in par_ms else None)
                    noter_annulation(d["ms"], garde, item_de[d["ms"]], nouveau, False)
                    faits.append(("annules", "%s %s (%s) : annulé — %s" % (d["ms"], item_de[d["ms"]], customer, d["motif"])))
                elif d["action"] == "remplacer":
                    _remplacer(e, d["vers"], jour)
                    correspondance.setdefault(item_de[d["ms"]], d["vers"])
                    faits.append(("remplaces", "%s : %s → %s (%s) — %s" % (d["ms"], item_de[d["ms"]], d["vers"], customer, d["motif"])))
                    if e["dates_realisees"]:
                        faits.append(("a_surveiller", a_surveiller(d["ms"], customer, "converti en %s" % d["vers"], e["dates_realisees"])))
                else:
                    faits.append(("laisses", "%s %s (%s) : laissé — %s" % (d["ms"], item_de[d["ms"]], customer, d["motif"])))
            # Devinettes en double, dans les familles où le plan a agi : parmi les échéanciers de la famille qui restent
            # (un échéancier converti n'apparaît que s'il n'y en avait aucun autre : il ne peut doubler personne).
            annules = {d["ms"] for d in plan if d["action"] == "annuler"}
            for fam in sorted({famille_de[d["ms"]] for d in plan if d["action"] != "laisser"}):
                type_code = article_type.get(fam)
                lot = [{"ms": e["ms"], "devine": _est_devine(e, fam, type_code, cache, fams), "realisees": e["realisees"]}
                       for e in siens if e["ms"] not in annules and e["ms"] not in heritages and fam in e["familles"]]
                a_annuler = devines_a_annuler(lot)
                vrai = min((x["ms"] for x in lot if not x["devine"]), default=None)
                for x in lot:
                    if not x["devine"]:
                        continue
                    if x["ms"] in a_annuler:
                        _annuler(par_ms[x["ms"]], "%s deviné en double d'un autre échéancier de la famille, sans histoire propre"
                                 % LIBELLE.get(fam, fam), jour, devine=True)
                        noter_annulation(x["ms"], vrai, type_code, _article_de(par_ms[vrai], fam, fams, cache), True)
                        faits.append(("devines_annules", "%s (%s) : deviné en double, annulé" % (x["ms"], customer)))
                    else:
                        faits.append(("devines_gardes", "%s (%s) : deviné %s gardé — %s" % (
                            x["ms"], customer, type_code, "seul échéancier de sa famille" if len(lot) == 1 else "il a une histoire propre")))
                        if par_ms[x["ms"]]["dates_realisees"]:
                            faits.append(("a_surveiller", a_surveiller(x["ms"], customer, "deviné %s gardé" % type_code,
                                                                       par_ms[x["ms"]]["dates_realisees"])))
            # Le gardé final (un gardé peut être un deviné annulé juste après) : trace 🧹 visible dans « Historique
            # client », puis les lignes d'appel vivantes renvoyées vers lui.
            renvois, correspondance = resoudre(renvois), resoudre(correspondance)
            for e, devine in a_tracer:
                _tracer_le_garde(e, renvois.get(e["ms"]), jour, devine)
            faits += [("lignes_appel", l) for l in _reecrire_lignes(customer, renvois, correspondance)]
            for cle, ligne in faits:
                bilan[cle].append(ligne)
        except Exception:
            frappe.db.rollback(save_point="heritage_client")
            bilan["erreurs"].append("%s : %s" % (customer, frappe.get_traceback().strip().splitlines()[-1]))
    return bilan


def compte_rendu(bilan) -> str:
    """Le compte rendu en clair. PURE."""
    lignes = ["Échéanciers hérités « sel / porte-membrane » : %d annulé(s), %d converti(s) en devinette, %d laissé(s) (doute), "
              "%d deviné(s) en double annulé(s), %d deviné(s) gardé(s), %d à surveiller, %d erreur(s)."
              % (len(bilan["annules"]), len(bilan["remplaces"]), len(bilan["laisses"]), len(bilan["devines_annules"]),
                 len(bilan["devines_gardes"]), len(bilan.get("a_surveiller") or []), len(bilan["erreurs"]))]
    if bilan.get("lignes_appel"):
        lignes.append("%d ligne(s) de listes d'appels encore vivantes réécrites (brouillons, ou relues par le 2e appel)."
                      % len(bilan["lignes_appel"]))
    for cle, titre in (("annules", "Annulés"), ("remplaces", "Convertis"), ("laisses", "Laissés"),
                       ("devines_annules", "Devinés annulés"), ("devines_gardes", "Devinés gardés"),
                       ("a_surveiller", "À surveiller à la vente d'une machine"),
                       ("lignes_appel", "Lignes d'appel réécrites"), ("erreurs", "Erreurs")):
        if bilan.get(cle):
            lignes.append("%s :" % titre)
            lignes += ["  " + l for l in bilan[cle]]
    if bilan.get("mixtes"):
        lignes.append("Hors périmètre (une vraie machine ET une ligne sel / porte-membrane), laissés : %s" % ", ".join(bilan["mixtes"]))
    return "\n".join(lignes)


def execute():
    bilan = executer()
    texte = compte_rendu(bilan)
    frappe.log_error(title="Échéanciers hérités sel / porte-membrane : compte rendu", message=texte)
    try:
        from customization_app.entonnoir_relances import noter
        noter("patch_sel_porte_membrane", {k: len(v) for k, v in bilan.items()})
    except ImportError:
        pass
    return {"bilan": bilan, "texte": texte}
