# -*- coding: utf-8 -*-
"""Échéanciers de maintenance — création à la vente d'une machine, décalage à l'achat de consommables,
prolongation quand les visites sont épuisées, nettoyage des clients B2B / non intéressés. Cron de nuit (daily_long).

Revue du 03/10/2026 (bugs corrigés ici, règles dans « Config Relances » = customization_app.relances_config) :
- la prolongation ne s'exécutait JAMAIS : elle lisait `sms_1`/`sms_2` au lieu de `custom_sms_1`/`custom_sms_2`, et aurait
  planté en écrivant le statut « En Attente » (inexistant) ; 23 échéanciers étaient muets ;
- le décalage prenait « la ligne la plus proche » sans regarder l'article : acheter des cartouches d'osmoseur marquait la
  visite de l'adoucisseur comme faite, écrasait une visite déjà réalisée, et une commande rejouée re-décalait tout ;
  → décalage PAR FAMILLE et par article, lignes réalisées intouchées, une commande n'agit qu'une fois ;
- une commande en erreur arrêtait tout le passage (et l'état partiel était enregistré) → chaque commande est isolée ;
- le nettoyage B2B visait aussi les brouillons (cancel() impossible → crash quotidien) et corrigeait les clients protégés ;
- la famille « devinée » sortait d'un set (ordre aléatoire) → ordre de priorité du réglage ;
- un échéancier deviné n'était jamais remplacé quand la vraie machine arrivait → il est retiré s'il n'a servi à rien ;
- les journaux [SUMMARY] partaient en INFO que la prod n'écrit pas → journal dédié qui écrit.

Revue du 25/11/2026 (analyse « cartographie entretien », copie de prod) :
- la prolongation attendait que TOUTES les visites aient reçu un SMS : une visite faite sans SMS ou une vieille visite
  jamais relancée la bloquait pour toujours (1 511 échéanciers sur 3 263, 38 déjà sans visite à venir) → chaque ARTICLE
  sans visite à venir est prolongé (articles_a_prolonger) ;
- la « devinette » retirée à l'arrivée de la vraie machine visait toute machine type sans visite réalisée : deux
  AP-M-AJ-5-SM vendus au même client se supprimaient l'un l'autre chaque nuit → on ne retire que si la commande
  d'origine ne vend pas cette machine (commande_vend_la_famille), et dans le doute on garde.
- (26/11/2026, relecture) la prolongation par article ranimait aussi les flux en double (échéanciers aux visites
  identiques, porte-filtre d'un client dont la machine est suivie) : 14 des 42 échéanciers de la 1re nuit → rafales de SMS ;
  ils restent gelés (articles_geles).

Lots 4 et 5 du 26/11/2026 (décisions par défaut du propriétaire, prudentes et réglables) :
- la nuit ne SUPPRIME plus jamais un échéancier : le nettoyage B2B, la devinette remplacée par la vraie machine et les
  doublons porte-filtre sont ANNULÉS (cancel), avec un commentaire sur l'échéancier ET sur le client (pourquoi, date,
  échéancier qui le remplace). 581 suppressions depuis juin 2025, ~150 visites effacées en 5 semaines. Un échéancier
  annulé COUVRE encore ses commandes (commandes_couvertes) : sinon la nuit suivante le recréerait ;
- un client « Non » à l'entretien reçoit quand même l'échéancier de la machine qu'il achète (mémoire de la machine,
  28 machines de septembre à novembre), sans relance (SMS et listes : relances_config.clients_exclus_relance l'écarte
  par son statut) ; le nettoyage ne touche plus aux « Non » (sinon boucle création / annulation) ; pas de devinette
  pour eux ; il ne redevient jamais « Oui » tout seul ; il ne passe plus « À requalifier » ; jamais PASSAGER ;
- un achat solde d'abord la famille désignée par sa main-d'œuvre d'entretien, le porte-filtre est soldé AVEC l'osmoseur
  qu'il protège (même échéancier, même date, même commande), et une cartouche ne solde qu'UNE famille
  (familles_a_solder) : 114 visites échues restaient ouvertes sur les familles laissées ;
- un achat sans main-d'œuvre d'entretien ne consomme une visite que si elle est échue ou due dans
  `jours_avance_sans_main_oeuvre` jours (60) : 78 des 143 avances de 7 semaines n'avaient aucune main-d'œuvre ;
- un achat antérieur à la naissance d'un échéancier ne le solde pas ; la prolongation passe avant les commandes ;
- le compte rendu de la nuit est noté dans l'entonnoir des relances (noter("nuit", …)) ;
- deux nuits simulées de suite sur la copie du 25/11/2026 : la 2e ne crée, n'annule, ne prolonge ni ne marque rien.

Relecture des lots 4 et 5 (26/11/2026) :
- la devinette n'était JAMAIS retirée : elle solde sa 1re visite dès sa naissance (commande d'origine), et la garde
  « des visites y sont réalisées » la gardait toujours (599 devinettes soumises, toutes avec une visite faite ; 2 clients
  ont leur devinette à côté de la vraie machine). Seules comptent désormais les visites faites par une AUTRE commande
  ou à la main (devinette_sans_histoire) ;
- un échéancier annulé par le nettoyage B2B ne couvre plus ses commandes quand le client n'est plus B2B (mal classé
  puis corrigé) : la suppression d'avant les libérait, la nuit recréait sa machine ;
- après une réponse « pas de système » dont l'échéancier a été SUPPRIMÉ (ancien Server Script « validation finale liste
  appelle », 362 suppressions), les commandes livrées jusqu'à ce jour-là ne créent plus rien (clients_pas_de_systeme) :
  sinon, la nuit lisant désormais les clients « Non », elle recréerait la machine que le client dit ne pas avoir ;
- le commentaire d'un « Non » réactivé par un achat dit s'il peut de nouveau être relancé (`relancer_non_interesses`).
"""
from __future__ import unicode_literals

import frappe
from frappe.utils import add_days, add_months, add_years, cint, date_diff, getdate, nowdate

from customization_app import relances_config as RC
from customization_app.utils.run_safely import run_safely

# ----------------------------------------------------------------------
# Compatibilité : anciens dictionnaires encore importés ailleurs (relance_maintenance_sms, creation_liste_appelle).
# La VÉRITÉ est dans Config Relances (relances_config) ; ceux-ci sont les valeurs historiques.
# ----------------------------------------------------------------------
LIST_TO_IGNORE = tuple(RC.liste(RC.DEFAUTS["clients_proteges"]))
B2B_GROUPS = tuple(RC.liste(RC.DEFAUTS["groupes_b2b"]))
DEFAULT_MACHINE_ITEM_BY_FAMILY = {f["code"]: f["article_type"] for f in RC.DEFAUT_FAMILLES if f["article_type"]}
MACHINE_FAMILY_BY_GROUP = {g: f["code"] for f in RC.DEFAUT_FAMILLES for g in RC.liste(f["groupes_machines"])}
MACHINE_ITEM_GROUPS = set(MACHINE_FAMILY_BY_GROUP)
CONSUMABLE_FAMILY_BY_GROUP = {}
for _f in RC.DEFAUT_FAMILLES:
    for _g in RC.liste(_f["groupes_consommables"]):
        CONSUMABLE_FAMILY_BY_GROUP.setdefault(_g, []).append(_f["code"])
CONSUMABLE_ITEM_GROUPS = set(CONSUMABLE_FAMILY_BY_GROUP)

PERIODICITY_MONTHS = {"Monthly": 1, "Quarterly": 3, "Half Yearly": 6, "Yearly": 12}
SALES_PERSON = "Équipe des Ventes"


def _logger():
    return RC.journal("maintenance_scheduler")


def log(msg):
    _logger().info(msg)


# ----------------------------------------------------------------------
# Clients
# ----------------------------------------------------------------------

def is_b2b_group(customer_group, cfg=None):
    return RC.est_b2b(customer_group, cfg)


def is_customer_interested(customer):
    val = (getattr(customer, "custom_intéressé_par_le_service_entretien", "") or "").strip()
    return val.upper() == "OUI"


def _client(name, cache=None):
    """Les 3 champs utiles du client, en une requête, mémorisés par passage."""
    cache = cache if cache is not None else {}
    if name not in cache:
        cache[name] = frappe.db.get_value("Customer", name, ["name", "customer_group", "custom_intéressé_par_le_service_entretien"],
                                          as_dict=True) or frappe._dict(name=name)
    return cache[name]


# ----------------------------------------------------------------------
# 1) NETTOYAGE
# ----------------------------------------------------------------------

def texte_annulation(nom, motif, jour, remplace_par=None, commandes=()) -> str:
    """Le commentaire d'une annulation par la nuit, le même sur l'échéancier et sur le client. PURE.

    26/11/2026 (lot 4) : la nuit annule au lieu de supprimer. Le commentaire est la seule explication lisible de
    l'annulation : pourquoi, quand, et quel échéancier prend le relais (ou aucun)."""
    # Relecture du 26/11/2026 : « lisibles » sur l'échéancier annulé lui-même ; le panneau « Historique client » ne
    # lit que les échéanciers soumis (changement proposé à historique_client.echeancier).
    texte = "🗄️ Échéancier {0} annulé le {1} par le passage de nuit (pas supprimé : ses visites, SMS et appels restent " \
            "lisibles sur l’échéancier annulé) : {2}.".format(nom, _jj(jour), motif)
    texte += " Remplacé par l’échéancier {0}.".format(remplace_par) if remplace_par else " Aucun échéancier ne le remplace."
    commandes = [c for c in (commandes or ()) if c]
    if commandes:
        texte += " Commande(s) : {0}.".format(", ".join(commandes))
    return texte


def _commenter(doctype, name, texte):
    frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": doctype, "reference_name": name,
                    "content": texte}).insert(ignore_permissions=True)


def annuler_echeancier(ms, motif, remplace_par=None, commandes=(), jour=None) -> str:
    """Annule (cancel) un échéancier soumis, jamais delete_doc, et laisse le même commentaire sur l'échéancier et sur le
    client (26/11/2026, proposition 7 de l'analyse : 581 suppressions depuis juin 2025 effaçaient l'historique, et un
    achat d'entretien ne trouvait plus rien à inscrire). ERPNext efface les événements de calendrier à l'annulation
    (on_cancel → delete_events) : rien ne reste dans l'agenda. `ms` : document ou nom. → le texte du commentaire."""
    doc = frappe.get_doc("Maintenance Schedule", ms) if isinstance(ms, str) else ms
    doc.flags.ignore_links = doc.flags.ignore_permissions = True
    doc.cancel()
    texte = texte_annulation(doc.name, motif, jour or nowdate(), remplace_par, commandes)
    # Une liste d'appels BROUILLON qui le cite ne s'enregistrerait plus (CancelledLinkError) : ses lignes sont rattachées à
    # un autre échéancier du client, sinon retirées (relecture du 26/11/2026, annulation_commande).
    from customization_app.annulation_commande import degager_des_listes_brouillon
    degager_des_listes_brouillon(doc.name, "🗄️ Échéancier annulé : " + motif)
    _commenter("Maintenance Schedule", doc.name, texte)
    if doc.customer:
        _commenter("Customer", doc.customer, texte)
    return texte


# Le motif d'une annulation B2B. Son début (MARQUE_B2B) est relu par commandes_couvertes : un échéancier annulé pour ce
# motif ne couvre plus ses commandes quand le client n'est plus B2B (relecture du 26/11/2026). Ne pas le reformuler sans
# garder ce début.
MARQUE_B2B = "client professionnel (groupe"
MOTIF_B2B = MARQUE_B2B + " « {0} »), sans suivi d’entretien"


def verify_data_base(cfg=None):
    """B2B : la case « intéressé » repasse à Non et leurs échéanciers SOUMIS sont ANNULÉS (26/11/2026 : plus supprimés),
    avec un commentaire sur l'échéancier et sur le client. Les clients protégés ne sont pas touchés.

    Ce qui a changé le 26/11/2026 (lot 4) :
    - les clients « Non à l'entretien + Non aux SMS » ne perdent plus leur échéancier : il est la mémoire de leur machine,
      les relances les écartent par leur statut (relances_config.clients_exclus_relance). Le garder évite aussi une boucle :
      la nuit crée désormais l'échéancier d'une machine vendue à un « Non », elle ne doit pas l'annuler ensuite ;
    - seuls les échéanciers SOUMIS sont lus : un échéancier annulé n'est jamais relu (sinon il serait retraité chaque
      nuit) ; un brouillon B2B est laissé tel quel (cancel() impossible, et plus de suppression) et seulement compté.
    Chaque annulation est isolée : une erreur n'empêche ni les autres ni la suite du passage."""
    cfg = cfg or RC.config()
    b2b = tuple(cfg["groupes_b2b_liste"]) or ("",)
    proteges = tuple(cfg["clients_proteges_liste"]) or ("",)
    corriges = 0
    for name in frappe.db.sql_list("""select name from tabCustomer where custom_intéressé_par_le_service_entretien = 'Oui'
                                     and customer_group in %s and name not in %s""", (b2b, proteges)):
        frappe.db.set_value("Customer", name, "custom_intéressé_par_le_service_entretien", "Non")
        corriges += 1
    log(f"[CLEANUP] Clients B2B corrigés (intéressé -> Non) : {corriges}")

    a_annuler = frappe.db.sql("""select sm.name, sm.customer, c.customer_group from `tabMaintenance Schedule` sm
                                 join tabCustomer c on c.name = sm.customer
                                 where sm.docstatus = 1 and c.name not in %s and c.customer_group in %s""",
                              (proteges, b2b), as_dict=True)
    brouillons = frappe.db.sql("""select count(*) from `tabMaintenance Schedule` sm join tabCustomer c on c.name = sm.customer
                                  where sm.docstatus = 0 and c.name not in %s and c.customer_group in %s""", (proteges, b2b))[0][0]
    annules, erreurs = 0, 0
    for row in a_annuler:
        frappe.db.savepoint("ms_cleanup")
        try:
            annuler_echeancier(row.name, MOTIF_B2B.format(row.customer_group))
            annules += 1
            log(f"[CLEANUP] Échéancier {row.name} annulé (client B2B {row.customer})")
        except Exception:
            frappe.db.rollback(save_point="ms_cleanup")
            erreurs += 1
            frappe.log_error(frappe.get_traceback(), f"Nettoyage échéancier {row.name}")
    log(f"[CLEANUP] Échéanciers B2B annulés : {annules}, brouillons B2B laissés : {brouillons}, erreurs : {erreurs}")
    # « supprimes » reste à 0 pour les lecteurs du compte rendu d'avant le 26/11/2026 (entonnoir, journaux).
    return {"corriges": corriges, "annules": annules, "supprimes": 0, "brouillons_laisses": cint(brouillons), "erreurs": erreurs}


# ----------------------------------------------------------------------
# 2) ARTICLES & FAMILLES
# ----------------------------------------------------------------------

def _item(code, cache=None):
    """{item_code, item_name, item_group} ou None, en une requête, mémorisé."""
    cache = cache if cache is not None else {}
    if code not in cache:
        cache[code] = frappe.db.get_value("Item", code, ["item_code", "item_name", "item_group"], as_dict=True)
    return cache[code]


def resoudre_article(brut, cache=None):
    """Le code tel quel, puis sans espaces, puis avec « GPD » recollé : les codes de la vente ont parfois été ressaisis."""
    essais = [brut, (brut or "").strip(), (brut or "").replace(" ", ""), (brut or "").replace(" ", "").replace("GPD", " GPD")]
    vus = set()
    for code in essais:
        if code and code not in vus:
            vus.add(code)
            item = _item(code, cache)
            if item:
                return item
    return None


def famille_machine(item, fams=None):
    """La famille d'une MACHINE par son groupe d'articles, sinon None."""
    return RC.famille_du_groupe_machine((item or {}).get("item_group"), fams) if item else None


def familles_consommable(item, fams=None):
    """Les familles qu'un CONSOMMABLE peut concerner : groupe du réglage, sinon indices du nom (4040/8040, 3012, 50/75/100 GPD,
    UV, « sel » = adoucisseur). PURE avec `fams`."""
    if not item:
        return []
    fams = fams or RC.familles()
    out = RC.familles_du_groupe_consommable(item.get("item_group"), fams)
    if out:
        return out
    nom = (item.get("item_name") or "").replace(" ", "").lower()
    if "sel" in nom and RC.famille("ADOUCISSEUR", fams):
        return ["ADOUCISSEUR"]
    if "4040" in nom or "8040" in nom:
        return ["RO_IND"]
    if any(x in nom for x in ("3012", "3013", "600gpd", "800gpd")):
        return ["RO_COM"]
    if any(x in nom for x in ("50gpd", "75gpd", "100gpd")):
        return ["RO_DOM"]
    if "uv" in nom:
        return ["UV"]
    return []


def est_consommable(item, fams=None):
    if not item:
        return False
    return (item.get("item_group") or "").strip() in RC.groupes_consommables(fams) or "sel" in (item.get("item_name") or "").lower()


# Compat (anciens appelants)
def map_item_to_machine_family(item, only_machine=True):
    d = {"item_code": getattr(item, "item_code", None), "item_name": getattr(item, "item_name", None), "item_group": getattr(item, "item_group", None)}
    if only_machine:
        f = famille_machine(d)
        return [f] if f else []
    return familles_consommable(d)


def get_default_item_for_family(family, fams=None):
    f = RC.famille(family, fams)
    code = (f or {}).get("article_type")
    if not code:
        log(f"[GUESS] Aucune machine type configurée pour la famille {family}")
        return None
    item = _item(code)
    if not item:
        log(f"[GUESS] Machine type '{code}' introuvable pour la famille {family}")
    return item


# ----------------------------------------------------------------------
# 3) ÉCHÉANCIERS D'UN CLIENT, PAR FAMILLE
# ----------------------------------------------------------------------

def familles_des_echeanciers(customer_name, fams=None, cache=None):
    """{nom_echeancier: {familles}} pour les échéanciers SOUMIS du client (une requête, pas un get_doc par échéancier)."""
    out = {}
    for ms, code in frappe.db.sql("""select msi.parent, msi.item_code from `tabMaintenance Schedule Item` msi
                                     join `tabMaintenance Schedule` ms on ms.name = msi.parent
                                     where ms.customer = %s and ms.docstatus = 1""", (customer_name,)):
        f = famille_machine(_item(code, cache), fams)
        out.setdefault(ms, set())
        if f:
            out[ms].add(f)
    return out


def get_customer_machine_families(customer_name, fams=None, cache=None):
    return set().union(*familles_des_echeanciers(customer_name, fams, cache).values()) if customer_name else set()


def find_machine_schedules(customer_name, family, fams=None, cache=None):
    return [frappe.get_doc("Maintenance Schedule", name) for name, fs in familles_des_echeanciers(customer_name, fams, cache).items() if family in fs]


def maintenance_has_family(maintenance, family, fams=None, cache=None):
    return any(famille_machine(_item(i.item_code, cache), fams) == family for i in getattr(maintenance, "items", []))


def _famille_de_la_ligne(ms, item_code, fams=None, cache=None):
    return famille_machine(_item(item_code, cache), fams)


# ----------------------------------------------------------------------
# 4) PROLONGATION
# ----------------------------------------------------------------------

def visite_a_venir(r, aujourd_hui) -> bool:
    """Une visite encore à faire, prévue APRÈS aujourd'hui. PURE."""
    return bool(r.scheduled_date and getdate(r.scheduled_date) > getdate(aujourd_hui)
                and not getattr(r, "actual_date", None) and getattr(r, "completion_status", None) != "Fully Completed")


def articles_a_prolonger(lignes, aujourd_hui) -> list:
    """Les articles d'un échéancier qui n'ont plus AUCUNE visite à venir (analyse du 25/11/2026). PURE.

    Avant, un échéancier ne se prolongeait que lorsque TOUTES ses visites avaient reçu un SMS. Or une visite réalisée sans
    SMS (entretien fait en avance, échéancier deviné dont la première visite est soldée à la création) ou une ancienne
    visite échue jamais relancée (seule la plus récente l'est depuis le 02/11/2026) comptait comme « reste à relancer » :
    1 511 échéanciers sur 3 263 ne se prolongeaient jamais, 38 n'avaient déjà plus aucune visite à venir. Et un
    échéancier à deux machines dont l'une avait encore des visites bloquait l'autre. Désormais : chaque article sans
    visite à venir est prolongé, quoi qu'il en soit des SMS. → [item_code], dans l'ordre des lignes."""
    par_article = {}
    for r in lignes or []:
        if r.item_code and r.scheduled_date:
            par_article.setdefault(r.item_code, []).append(r)
    return [code for code, rows in par_article.items() if not any(visite_a_venir(r, aujourd_hui) for r in rows)]


def has_free_sms_slots(ms, aujourd_hui=None):
    """Compatibilité : vrai tant que CHAQUE article de l'échéancier a encore une visite à venir (rien à prolonger).
    Le nom date de l'ancienne règle (« reste-t-il une visite sans SMS ? »), remplacée le 25/11/2026 : articles_a_prolonger."""
    return not articles_a_prolonger(getattr(ms, "schedules", []), aujourd_hui or nowdate())


def build_item_periodicity_map(ms):
    return {i.item_code: PERIODICITY_MONTHS.get((getattr(i, "periodicity", None) or "Half Yearly").strip(), 6)
            for i in getattr(ms, "items", []) if i.item_code}


def prochaines_dates(derniere, mois, n, aujourd_hui):
    """Les n prochaines visites après `derniere`, au pas de `mois` : on saute les dates déjà trop anciennes pour que la
    première visite ajoutée soit la prochaine réellement due (au plus une période de retard). PURE."""
    derniere, aujourd_hui = getdate(derniere), getdate(aujourd_hui)
    plancher = add_months(aujourd_hui, -mois)
    d = add_months(derniere, mois)
    while d < plancher:
        d = add_months(d, mois)
    return [add_months(d, mois * i) for i in range(n)]


def couvert_par(realisees_moi, realisees_autre) -> bool:
    """Un flux (échéancier × article) en DOUBLE un autre de la même famille chez le même client quand chacune de ses visites
    réalisées l'a aussi été sur l'autre : même commande (un achat de cartouches marquait les deux), ou visite faite à la
    main le même jour (« main:<date> »). Sans visite réalisée, rien ne le prouve. PURE.
    Relecture du 26/11/2026 : Ben Salem Néjib (MAT-MSH-2023-00847 / 01074, mêmes 6 commandes) ; Ambassade de Turky
    (MAT-MSH-2023-00143 / 01080, 10 visites identiques dont une à la main le 15/01/2025)."""
    a, b = set(realisees_moi or []), set(realisees_autre or [])
    return bool(a) and "" not in a and a <= b


def _cle_flux(code, famille_de):
    return famille_de(code) or ("article:" + code)


def articles_a_garder_geles(ms_name, a_prolonger, miens, autres, famille_de, protege=None) -> dict:
    """{item_code: motif} — les articles épuisés d'un échéancier qu'on NE prolonge PAS (revue du 26/11/2026). PURE.

    La prolongation par article (25/11/2026) ranimait aussi des flux qui en doublent un autre et s'étaient éteints :
    14 des 42 échéanciers prolongés la 1re nuit avaient un autre échéancier de la même famille chez le même client, d'où
    des SMS en rafale (KEMVET : 10 jours de SMS en 4 mois au lieu de 4) et des « cycles » comptés deux fois. Ne repartent pas :
    - un PORTE-FILTRE (famille accessoire) quand le client a une vraie machine suivie : il est changé à son entretien ;
    - un DOUBLON (`couvert_par`) d'un flux qui a encore une visite à venir, ou d'un flux gelé qui a des visites en plus ;
    - de deux jumeaux gelés (mêmes visites), le plus ancien : seul le plus récent repart.
    `miens` / `autres` : [{ms, item_code, creation, a_venir, realisees}] de cet échéancier / des autres échéanciers soumis
    du client ; `famille_de` : item_code → famille ou None."""
    tous = list(miens) + list(autres)
    out = {}
    for code in a_prolonger:
        moi = next((f for f in miens if f["item_code"] == code), None)
        if moi is None:
            continue
        fam = _cle_flux(code, famille_de)
        if fam in FAMILLES_ACCESSOIRES:
            # Seulement une machine qui le PROTÈGE (pf_protege, relecture finale du 26/11/2026) : à côté d'un adoucisseur
            # ou d'un UV seuls, rien ne solde le porte-filtre — gelé, son flux mourait (21 flux chez 17 clients).
            protege = protege or (lambda fm: bool(fm) and fm not in FAMILLES_ACCESSOIRES)
            machine = next((f for f in tous if protege(famille_de(f["item_code"]))), None)
            if machine:
                out[code] = "porte-filtre changé à l'entretien de la machine (%s)" % machine["ms"]
                continue
        for f in tous:
            if f is moi or _cle_flux(f["item_code"], famille_de) != fam or not couvert_par(moi["realisees"], f["realisees"]):
                continue
            if f["a_venir"]:
                out[code] = "doublon de %s (mêmes visites), qui a encore une visite à venir" % f["ms"]
                break
            if not couvert_par(f["realisees"], moi["realisees"]):
                out[code] = "doublon de %s (qui a des visites en plus) : c'est lui qui repart" % f["ms"]
                break
            if (str(f["creation"]), f["ms"], f["item_code"]) > (str(moi["creation"]), moi["ms"], moi["item_code"]):
                out[code] = "jumeau gelé de %s, plus récent : seul celui-ci repart" % f["ms"]
                break
    return out


def flux_de_l_echeancier(ms, aujourd_hui) -> list:
    """Les flux (un par article) d'un échéancier EN MÉMOIRE (il peut porter des visites pas encore enregistrées). PURE."""
    par = {}
    for r in getattr(ms, "schedules", []) or []:
        if not (r.item_code and r.scheduled_date):
            continue
        f = par.setdefault(r.item_code, {"ms": ms.name, "item_code": r.item_code, "creation": str(getattr(ms, "creation", "") or ""),
                                         "a_venir": False, "realisees": []})
        f["a_venir"] = f["a_venir"] or visite_a_venir(r, aujourd_hui)
        if getattr(r, "actual_date", None):
            f["realisees"].append((getattr(r, "custom_sales_order", None) or "").strip() or "main:%s" % getdate(r.actual_date))
    return list(par.values())


def flux_des_autres_echeanciers(customer, sauf_ms, aujourd_hui) -> list:
    """Les flux (échéancier × article) des AUTRES échéanciers soumis du client — une requête."""
    rows = frappe.db.sql("""
        select ms.name, ms.creation, d.item_code,
               max(d.scheduled_date > %(t)s and d.actual_date is null and ifnull(d.completion_status, '') != 'Fully Completed') as a_venir,
               group_concat(case when d.actual_date is not null then
                   if(trim(ifnull(d.custom_sales_order, '')) = '', concat('main:', d.actual_date), trim(d.custom_sales_order)) end
                   separator '|') as realisees
        from `tabMaintenance Schedule Detail` d join `tabMaintenance Schedule` ms on ms.name = d.parent
        where ms.docstatus = 1 and ms.customer = %(c)s and ms.name != %(m)s
          and ifnull(d.item_code, '') != '' and d.scheduled_date is not null
        group by ms.name, ms.creation, d.item_code""", {"t": getdate(aujourd_hui), "c": customer, "m": sauf_ms or ""}, as_dict=True)
    return [{"ms": r.name, "item_code": r.item_code, "creation": str(r.creation), "a_venir": bool(cint(r.a_venir)),
             "realisees": [] if r.realisees is None else r.realisees.split("|")} for r in rows]


def articles_geles(ms, a_prolonger, aujourd_hui, fams=None, cache=None) -> dict:
    """`articles_a_garder_geles` pour un échéancier réel (ses autres échéanciers lus en base). Sans client (objet de test) : {}."""
    customer = getattr(ms, "customer", None)
    if not (a_prolonger and customer):
        return {}
    fams, cache = fams or RC.familles(), cache if cache is not None else {}
    return articles_a_garder_geles(ms.name, a_prolonger, flux_de_l_echeancier(ms, aujourd_hui),
                                   flux_des_autres_echeanciers(customer, ms.name, aujourd_hui),
                                   lambda code: famille_machine(_item(code, cache), fams),
                                   protege=lambda fm: pf_protege(fm, fams))


def extend_schedule_for_sms(ms, extra_visits_per_item=None, aujourd_hui=None, exclus=None):
    """Chaque article qui n'a plus de visite à venir (articles_a_prolonger) reçoit `visites_ajoutees` visites (réglage),
    au pas de sa périodicité, après sa dernière visite. Statut « Pending », nom d'article et vendeur repris de la ligne
    précédente. Les articles qui ont encore une visite à venir ne bougent pas, ni ceux qui doublent un autre flux du client
    (`articles_geles`, 26/11/2026 ; `exclus` les donne déjà calculés) : ils sont notés dans ms.flags.articles_geles.
    → nombre de lignes ajoutées."""
    aujourd_hui = aujourd_hui or nowdate()
    lignes = getattr(ms, "schedules", [])
    a_prolonger = articles_a_prolonger(lignes, aujourd_hui)
    if not a_prolonger:
        return 0
    geles = articles_geles(ms, a_prolonger, aujourd_hui) if exclus is None else dict(exclus)
    flags = getattr(ms, "flags", None)
    if flags is not None:
        flags.articles_geles = geles
    a_prolonger = [c for c in a_prolonger if c not in geles]
    if not a_prolonger:
        return 0
    n = cint(extra_visits_per_item) or RC.config()["visites_ajoutees"]
    periodicites = build_item_periodicity_map(ms)
    par_article = {}
    for r in lignes:
        if r.item_code in a_prolonger:
            par_article.setdefault(r.item_code, []).append(r)
    ajoutees = 0
    for code, rows in par_article.items():
        dates = [getdate(r.scheduled_date) for r in rows if r.scheduled_date]
        if not dates:
            continue
        modele = rows[-1]
        for d in prochaines_dates(max(dates), periodicites.get(code, 6), n, aujourd_hui):
            ms.append("schedules", {"item_code": code, "item_name": getattr(modele, "item_name", None), "scheduled_date": d,
                                    "completion_status": "Pending", "sales_person": getattr(modele, "sales_person", None) or SALES_PERSON})
            ajoutees += 1
    if ajoutees:
        log(f"[EXTEND] {ms.name}: +{ajoutees} visite(s) ({len(par_article)} article(s) × {n})")
    return ajoutees


# ----------------------------------------------------------------------
# 5) DÉCALAGE À L'ACHAT DE CONSOMMABLES
# ----------------------------------------------------------------------

def ligne_a_marquer(lignes, delivery_date, famille, famille_de, deja_commande=None):
    """La visite que l'achat « réalise », parmi les lignes de la FAMILLE concernée non réalisées : la PLUS ANCIENNE en
    retard (le client vient enfin faire son entretien, c'est la visite qu'il devait), sinon la prochaine à venir.
    L'ancienne règle « la plus proche de la livraison » marquait la visite voisine et laissait la visite en retard
    ouverte pour toujours : 1 625 visites orphelines en base au 03/10/2026. None si la commande a déjà été appliquée à
    cette FAMILLE de l'échéancier ou s'il n'y a rien à marquer. PURE. `lignes` : objets avec item_code, scheduled_date,
    actual_date, completion_status, custom_sales_order ; `famille_de(item_code)` → famille.
    26/11/2026 : « déjà appliquée » se juge par famille, plus pour tout l'échéancier. Sur un échéancier à deux familles
    (osmoseur + porte-filtre, osmoseur + adoucisseur), la première famille soldée bloquait l'autre : 114 visites échues
    restaient ouvertes. Quelles familles une commande solde, c'est familles_a_solder qui le décide."""
    delivery_date = getdate(delivery_date)
    meme_famille = (lambda r: True) if famille is None else (lambda r: famille_de(r.item_code) == famille)
    if deja_commande and any((getattr(r, "custom_sales_order", None) or "") == deja_commande and meme_famille(r) for r in lignes):
        return None
    candidates = [r for r in lignes if r.scheduled_date and not getattr(r, "actual_date", None)
                  and getattr(r, "completion_status", None) != "Fully Completed" and meme_famille(r)]
    if not candidates:
        return None
    en_retard = [r for r in candidates if getdate(r.scheduled_date) <= delivery_date]
    if en_retard:
        return min(en_retard, key=lambda r: (str(r.scheduled_date), r.idx or 0))
    return min(candidates, key=lambda r: (str(r.scheduled_date), r.idx or 0))


CHAMPS_RELANCE = ("custom_sms_1", "custom_sms_2", "custom_sms1_status", "custom_sms2_status", "custom_appelle", "custom_1er_appel")

# Proposition 13 de l'analyse du 25/11/2026 : sans main-d'œuvre d'entretien, un achat ne consomme une visite que si
# elle est échue ou due dans ce nombre de jours (réglage `jours_avance_sans_main_oeuvre` de Config Relances).
JOURS_AVANCE_SANS_MAIN_OEUVRE = 60


def jours_avance_sans_main_oeuvre(cfg=None) -> int:
    """Le délai d'avance permis à un achat sans main-d'œuvre (0 = seulement une visite déjà échue). PURE avec `cfg`."""
    cfg = RC.config() if cfg is None else cfg
    v = cfg.get("jours_avance_sans_main_oeuvre", JOURS_AVANCE_SANS_MAIN_OEUVRE)
    return max(0, JOURS_AVANCE_SANS_MAIN_OEUVRE if v in (None, "") else cint(v))


def est_main_oeuvre_entretien(code, fams=None) -> bool:
    """Une main-d'œuvre d'ENTRETIEN : l'article d'entretien d'une famille du réglage (M-E-OD, M-E-Ad…), ou tout code
    « M-E-… » (convention des articles : M-E = entretien, M-I = installation, M-R = réparation ; M-E-PF « Entretien
    Porte Filtre » n'est l'article d'aucune famille du réglage). PURE avec `fams`."""
    code = (code or "").strip()
    if not code:
        return False
    return code.startswith("M-E-") or code in {f["article_entretien"] for f in (fams or RC.familles()) if f.get("article_entretien")}


def commande_avec_main_oeuvre(items, fams=None) -> bool:
    """La commande porte-t-elle une main-d'œuvre d'entretien ? `items` : [{item_code, …}] résolus. PURE avec `fams`."""
    return any(est_main_oeuvre_entretien((it or {}).get("item_code"), fams) for it in items or [])


def avance_permise(scheduled_date, delivery_date, jours_avance) -> bool:
    """Un achat peut-il consommer cette visite ? Oui si elle est échue (au plus tard le jour de la livraison) ou due dans
    `jours_avance` jours ; `jours_avance` None = sans limite (achat avec main-d'œuvre d'entretien, règle d'avant). PURE.

    26/11/2026 (proposition 13) : 40 à 44 % des visites soldées la nuit étaient des visites À VENIR ; sur 143 avances en
    7 semaines, 78 venaient d'un achat sans main-d'œuvre (raccord, cartouche de rechange…). Une commande passée 9 j après
    un entretien consommait la visite d'avril 2027, et la visite faite sans SMS bloquait la prolongation."""
    if jours_avance is None:
        return True
    return getdate(scheduled_date) <= add_days(getdate(delivery_date), cint(jours_avance))


def pf_protege(famille, fams=None) -> bool:
    """Le porte-filtre suit-il cette famille ? Oui pour une vraie machine qui prend les MÊMES cartouches qu'un porte-filtre
    (groupes consommables communs dans le réglage) : osmoseurs domestiques, commerciaux, industriels, bi-osmose,
    fontaine. Non pour l'adoucisseur (sel) ou l'UV (lampe) : on ne change pas le pré-filtre en leur rendant visite. PURE.
    26/11/2026 : « le porte-filtre est soldé avec l'osmoseur qu'il protège »."""
    fams = fams or RC.familles()
    if not famille or famille in FAMILLES_ACCESSOIRES:
        return False
    f = RC.famille(famille, fams)
    if not f:
        return False
    groupes_pf = {g for a in FAMILLES_ACCESSOIRES for g in (RC.famille(a, fams) or {}).get("groupes_consommables", [])}
    return bool(set(f.get("groupes_consommables") or []) & groupes_pf)


def _realiser(lignes, ref, delivery_date, sales_order):
    """Marque `ref` réalisée par la commande à la date de livraison et décale d'autant les visites SUIVANTES du même
    article (une visite repoussée du passé vers l'avenir redevient neuve). PURE sur les lignes.
    → (date prévue d'avant, décalage en jours, [(avant, après, {champ: valeur effacée})])."""
    delivery_date = getdate(delivery_date)
    ancienne = getdate(ref.scheduled_date)
    decalage = date_diff(delivery_date, ancienne)
    ref.actual_date = delivery_date
    ref.custom_sales_order = sales_order
    ref.completion_status = "Fully Completed"
    ref.scheduled_date = delivery_date
    deplacees = []
    for r in lignes:
        if r is ref or r.item_code != ref.item_code or not r.scheduled_date:
            continue
        if getdate(r.scheduled_date) > ancienne and not getattr(r, "actual_date", None):
            etait_passee = getdate(r.scheduled_date) <= delivery_date
            avant = getdate(r.scheduled_date)
            r.scheduled_date = add_days(getdate(r.scheduled_date), decalage)
            effaces = {}
            # Une visite repoussée du passé vers l'avenir redevient une visite NEUVE : ses SMS et appels d'alors ne
            # comptent plus (sinon elle ne serait plus jamais relancée).
            if etait_passee and getdate(r.scheduled_date) > delivery_date:
                for champ in CHAMPS_RELANCE:
                    if getattr(r, champ, None):
                        effaces[champ] = getattr(r, champ)
                    setattr(r, champ, None)
            deplacees.append((avant, getdate(r.scheduled_date), effaces))
    return ancienne, decalage, deplacees


def _compter(stats, cle, n=1):
    if stats is not None:
        stats[cle] = stats.get(cle, 0) + n


def _effaces_en_clair(effaces):
    """{champ: valeur} → « SMS1 27/07/2026 ✓, SMS2 03/08/2026 ✗, appelé 03/10/2026 ». PURE."""
    bouts = []
    for date_champ, statut_champ, libelle in (("custom_sms_1", "custom_sms1_status", "SMS1"),
                                              ("custom_sms_2", "custom_sms2_status", "SMS2")):
        if effaces.get(date_champ):
            statut = effaces.get(statut_champ)
            marque = "" if not statut else (" ✓" if statut == "Success" else " ✗ " + str(statut))
            bouts.append("{0} {1}{2}".format(libelle, _jj(effaces[date_champ]), marque))
    for champ, libelle in (("custom_1er_appel", "1er appel"), ("custom_appelle", "appelé")):
        if effaces.get(champ):
            bouts.append("{0} {1}".format(libelle, _jj(effaces[champ])))
    return ", ".join(bouts)


def _jj(d):
    return getdate(d).strftime("%d/%m/%Y") if d else ""


def texte_decalage(item_code, ancienne, livraison, sales_order, decalage, deplacees, note=""):
    """La trace d'un décalage, en clair. PURE.

    ⚠️ C'EST LA SEULE MÉMOIRE DE CE QUI EST EFFACÉ. L'échéancier n'a pas de suivi des modifications : une visite repoussée
    perd ses dates de SMS et d'appel, et rien ne disait plus qu'elle avait été relancée (constaté le 07/10/2026 en
    construisant l'historique client). Ce commentaire est relu par le panneau « Historique client » : la tête
    (« Visite … du … réalisée par … (livraison du …) ») ne change pas ; `note` s'ajoute en dernier (26/11/2026).
    `deplacees` : [(date avant, date après, {champ: valeur effacée})]."""
    tete = "📅 Visite {0} du {1} réalisée par {2} (livraison du {3})".format(
        item_code, _jj(ancienne), sales_order, _jj(livraison))
    fin = (" " + note.strip()) if (note or "").strip() else ""
    if not deplacees:
        return tete + "." + fin
    bouts = []
    for avant, apres, effaces in deplacees:
        b = "{0} → {1}".format(_jj(avant), _jj(apres))
        clair = _effaces_en_clair(effaces or {})
        if clair:
            b += " (effacés : {0})".format(clair)
        bouts.append(b)
    return "{0}. {1} visite(s) suivante(s) décalée(s) de {2} j : {3}.{4}".format(
        tete, len(deplacees), decalage, " ; ".join(bouts), fin)


def _tracer(ms, texte):
    """Commentaire Info sur l'échéancier ; jamais bloquant (un échec de trace ne doit pas défaire le décalage)."""
    add = getattr(ms, "add_comment", None)
    if not callable(add):
        return
    try:
        add("Info", texte)
    except Exception:
        log(f"[UPDATE] trace de décalage non écrite sur {getattr(ms, 'name', '?')}")


NOTE_PORTE_FILTRE = "Porte-filtre soldé avec la machine qu’il protège (même échéancier, même date, même commande)."


def shift_schedule_for_delivery(target_ms, delivery_date, sales_order, famille=None, fams=None, cache=None, aujourd_hui=None,
                                jours_avance=None, stats=None, avec_porte_filtre=True):
    """Marque la visite de la famille concernée comme réalisée par la commande, et décale les visites SUIVANTES du même
    article d'autant. Les autres machines de l'échéancier et les visites déjà réalisées ne bougent pas ; une commande
    déjà appliquée à cette famille ne rejoue pas. → True si quelque chose a changé.

    26/11/2026 (lot 5) :
    - `jours_avance` (achat SANS main-d'œuvre d'entretien) : une visite qui n'est ni échue ni due dans ce délai n'est
      pas consommée (avance_permise) ; rien n'est écrit, la raison est journalisée. None = règle d'avant ;
    - le porte-filtre du MÊME échéancier est soldé avec la machine qu'il protège (pf_protege), même date, même commande,
      même règle d'avance : il est changé pendant l'entretien de l'osmoseur. Avant, la commande soldait l'osmoseur et
      laissait le porte-filtre en retard, relancé pour rien : 10 cas en 7 semaines avec le code d'alors (x10_multifam,
      142 échéanciers à plusieurs familles dont 57 « osmoseur + porte-filtre »)."""
    lignes = getattr(target_ms, "schedules", None) or []
    if not lignes:
        log(f"[UPDATE] {target_ms.name} sans visites")
        return False
    delivery_date = getdate(delivery_date)
    famille_de = lambda code: _famille_de_la_ligne(target_ms, code, fams, cache)
    ref = ligne_a_marquer(lignes, delivery_date, famille, famille_de, sales_order)
    if ref is None:
        log(f"[UPDATE] {target_ms.name} : rien à marquer pour {sales_order} (famille {famille}) — déjà appliqué ou aucune visite en attente")
        return False
    if not avance_permise(ref.scheduled_date, delivery_date, jours_avance):
        _compter(stats, "avances_evitees")
        log(f"[AVANCE] {target_ms.name} : visite {ref.item_code} du {ref.scheduled_date} gardée — {sales_order} (livré le "
            f"{delivery_date}) n'a pas de main-d'œuvre d'entretien et la visite est due dans plus de {jours_avance} j")
        return False
    marques = [(ref, _realiser(lignes, ref, delivery_date, sales_order), "")]
    if avec_porte_filtre and pf_protege(famille, fams):
        for accessoire in FAMILLES_ACCESSOIRES:
            ref_pf = ligne_a_marquer(lignes, delivery_date, accessoire, famille_de, sales_order)
            if ref_pf is None or not avance_permise(ref_pf.scheduled_date, delivery_date, jours_avance):
                continue
            marques.append((ref_pf, _realiser(lignes, ref_pf, delivery_date, sales_order), NOTE_PORTE_FILTRE))
            _compter(stats, "porte_filtres_avec_machine")
    extend_schedule_for_sms(target_ms, aujourd_hui=aujourd_hui)
    target_ms.flags.ignore_permissions = True
    target_ms.save()
    for r, (ancienne, decalage, deplacees), note in marques:
        _tracer(target_ms, texte_decalage(r.item_code, ancienne, delivery_date, sales_order, decalage, deplacees, note))
        log(f"[UPDATE] {target_ms.name} ({target_ms.customer}) : visite {r.item_code} du {ancienne} réalisée par {sales_order}, "
            f"suivantes décalées de {decalage} j" + (" (porte-filtre avec la machine)" if note else ""))
    _reactiver_client(target_ms.customer)
    return True


# ----------------------------------------------------------------------
# 6) CRÉATION
# ----------------------------------------------------------------------

def _commande_deja_portee(sales_order) -> str | None:
    """L'échéancier soumis qui porte déjà cette commande sur une ligne machine, sinon None."""
    if not sales_order:
        return None
    noms = frappe.db.sql_list("""select ms.name from `tabMaintenance Schedule` ms join `tabMaintenance Schedule Item` msi
                                 on msi.parent = ms.name where msi.sales_order = %s and ms.docstatus = 1 limit 1""",
                              (sales_order,))
    return noms[0] if noms else None


def _nouvel_echeancier(customer, delivery_date, lignes_items, cfg):
    ms = frappe.new_doc("Maintenance Schedule")
    ms.customer = customer
    ms.transaction_date = delivery_date
    for it in lignes_items:
        sales_order = it["sales_order"]
        # ERPNext n'admet qu'UN échéancier soumis par commande (validate_sales_order). Une commande qui crée une
        # machine (porte-filtre) PUIS fait deviner une autre famille (joint torique → osmoseur) en créait deux :
        # la seconde levait « Maintenance Schedule … exists against … », la commande entière était annulée et
        # réessayée chaque nuit (WEB1-008619, 26-27/10/2026). Le second échéancier se crée sans le lien de commande
        # sur sa ligne machine ; la visite décalée garde le lien (custom_sales_order), la commande reste couverte.
        deja = _commande_deja_portee(sales_order)
        if deja:
            log(f"[LINK] SO {sales_order} déjà portée par {deja} : nouvel échéancier {it['item_code']} sans lien de commande")
            sales_order = None
        ms.append("items", {"item_code": it["item_code"], "item_name": it["item_name"], "start_date": delivery_date,
                            "end_date": add_years(getdate(delivery_date), 5), "sales_order": sales_order,
                            "periodicity": cfg["periodicite_defaut"], "no_of_visits": cfg["nb_visites"], "sales_person": SALES_PERSON})
    ms.flags.ignore_permissions = True
    ms.insert()
    ms.submit()
    return ms


def commande_vend_la_famille(sales_order, famille, fams=None, cache=None):
    """La commande liée à une ligne machine vend-elle une machine de cette famille ? True : oui (vraie vente) ; False :
    non (achat de consommables — c'est d'elle qu'est née la devinette) ; None : commande absente ou supprimée, on ne
    sait pas. Les codes sont résolus comme à la création (resoudre_article)."""
    if not sales_order or not frappe.db.exists("Sales Order", sales_order):
        return None
    for code in frappe.db.sql_list("select item_code from `tabSales Order Item` where parent = %s", (sales_order,)):
        item = resoudre_article(code, cache)
        if item and famille_machine(item, fams) == famille:
            return True
    return False


def devinette_sans_histoire(lignes, origine) -> bool:
    """Une devinette n'a servi à rien d'autre qu'à naître : aucune visite faite, sauf celle que sa commande d'origine a
    soldée à sa naissance. PURE.

    Relecture du 26/11/2026 : la devinette solde sa 1re visite dès sa création (update_single_machine_schedule appelle
    shift_schedule_for_delivery avec la commande d'origine). L'ancienne garde « aucune visite réalisée » la gardait donc
    TOUJOURS : 599 devinettes soumises sur la copie du 25/11/2026, toutes avec une visite faite, 467 sans autre visite
    que celle-là ; Amjad Mejri (MAT-MSH-2026-00524 / 01455) et Monoom Mrabti (00656 / 00867) gardent leur devinette à
    côté de la vraie machine. Une visite faite par une AUTRE commande, ou à la main (sans commande, ou « Fully
    Completed » sans date), est une histoire propre : on garde. Sans commande d'origine, toute visite faite garde."""
    origine = (origine or "").strip()
    for r in lignes or []:
        if getattr(r, "actual_date", None) or getattr(r, "completion_status", None) == "Fully Completed":
            if not origine or (getattr(r, "custom_sales_order", None) or "").strip() != origine:
                return False
    return True


def _retirer_echeancier_devine(customer_name, famille, fams, cache, remplace_par=None, sales_order=None, stats=None):
    """La vraie machine arrive : l'échéancier DEVINÉ de la même famille est retiré — seulement s'il est sûr que c'est une
    devinette : la machine type seule, aucune visite réalisée hors celle de sa naissance (devinette_sans_histoire), et une
    ligne machine liée à une commande qui ne vend PAS cette famille. Avant le 25/11/2026, la machine type suffisait : un
    client qui achète deux AP-M-AJ-5-SM le même jour (SAL-ORD-2026-03459 / 03460, deux BL) voyait chaque nuit l'une des
    commandes supprimer l'échéancier de l'autre, prise pour une devinette — 34 suppressions du 23/10 au 25/11, une
    machine jamais suivie. Dans le doute, on garde.
    26/11/2026 (lot 4) : la devinette est ANNULÉE, plus supprimée, avec un commentaire qui nomme l'échéancier de la vraie
    machine (`remplace_par`, créé juste avant). Elle continue de couvrir sa commande d'origine (commandes_couvertes) :
    libre, cet ancien achat de cartouches serait relu chaque nuit (achat_anterieur l'écarterait de la machine neuve).
    Relecture du 26/11/2026 : la visite soldée à sa naissance ne la protège plus (ce chemin ne s'exécutait jamais)."""
    f = RC.famille(famille, fams)
    type_code = (f or {}).get("article_type")
    if not type_code:
        return
    for ms in find_machine_schedules(customer_name, famille, fams, cache):
        if remplace_par and ms.name == remplace_par:
            continue
        if [i.item_code for i in ms.items] != [type_code]:
            continue
        origine = (ms.items[0].sales_order or "") if ms.items else ""
        if not devinette_sans_histoire(ms.schedules, origine):
            log(f"[GUESS] {ms.name} deviné ({type_code}) gardé : des visites y sont réalisées par une autre commande ou à la main")
            continue
        vend = commande_vend_la_famille(origine, famille, fams, cache)
        if vend is not False:
            log(f"[GUESS] {ms.name} ({type_code}) gardé : "
                + (f"vraie vente de la machine ({origine})" if vend else "commande d'origine inconnue"))
            continue
        annuler_echeancier(ms, "échéancier deviné (machine type %s, née de l’achat %s) : la vraie machine %s est vendue%s"
                           % (type_code, origine or "—", RC.libelle_famille(famille, fams).replace("votre ", ""),
                              (" par " + sales_order) if sales_order else ""),
                           remplace_par=remplace_par, commandes=[origine, sales_order])
        _compter(stats, "devines_annules")
        log(f"[GUESS] {ms.name} deviné ({type_code}) annulé : la vraie machine {famille} est vendue ({remplace_par})")


# Un porte-filtre (famille PF) vendu à un client qui a DÉJÀ un échéancier est une pièce posée pendant l'entretien, pas une
# machine : il ne crée pas d'échéancier. Constat du 15/10/2026 : 191 échéanciers « porte-filtre » doublaient l'échéancier
# de l'osmoseur (221 SMS en double en 2026) et, la commande étant « portée » par ce doublon, la visite de l'osmoseur
# restait non réalisée pour toujours. Vendu seul à un client sans échéancier, le porte-filtre reste une machine suivie.
FAMILLES_ACCESSOIRES = ("PF",)


def machines_de_la_commande(items, fams, client_suivi):
    """[(item, famille)] des machines d'une commande qui méritent un échéancier : une famille « accessoire » ne compte
    que si le client n'a encore aucun échéancier. Un article n'est compté qu'une fois. PURE."""
    out, vus = [], set()
    for item in items:
        fam = famille_machine(item, fams)
        if not fam or item["item_code"] in vus:
            continue
        if client_suivi and fam in FAMILLES_ACCESSOIRES:
            continue
        vus.add(item["item_code"])
        out.append((item, fam))
    return out


def debut_echeancier(ms):
    """Le premier jour de suivi d'un échéancier : sa date de transaction ou le début de la plus ancienne machine. PURE."""
    dates = [getdate(d) for d in [getattr(ms, "transaction_date", None)]
             + [getattr(i, "start_date", None) for i in (getattr(ms, "items", None) or [])] if d]
    return min(dates) if dates else None


def achat_anterieur(delivery_date, ms) -> bool:
    """Un achat livré AVANT la naissance de l'échéancier n'est pas l'entretien de cette machine : il ne la solde pas.
    PURE. Mesure du 26/11/2026 (deux nuits simulées) : depuis que la nuit crée l'échéancier d'un client « Non », ses
    achats plus anciens que la machine — lus avant elle la 1re nuit, après elle la 2e — étaient comparés à la machine
    neuve (26 de plus la 2e nuit). Avec une main-d'œuvre, l'entretien d'une ANCIENNE machine aurait soldé la 1re visite
    de la nouvelle."""
    debut = debut_echeancier(ms)
    return bool(debut and getdate(delivery_date) < debut)


def echeanciers_hors_commande(echeanciers, sales_order):
    """Les échéanciers que cette commande n'a pas elle-même créés (ligne machine liée à la commande) : une installation ne
    doit pas « réaliser » la première visite de la machine qu'elle vient de poser. PURE."""
    return [ms for ms in echeanciers
            if not any((getattr(i, "sales_order", None) or "") == sales_order for i in (getattr(ms, "items", None) or []))]


def _client_suivi(customer_name) -> bool:
    return bool(frappe.db.exists("Maintenance Schedule", {"customer": customer_name, "docstatus": 1}))


def create_maintenance_for_machines(i_sal, cfg=None, fams=None, cache=None, stats=None):
    """Un échéancier pour les MACHINES de la commande (groupes « machine » du réglage). → True si créé.
    26/11/2026 (lot 4, propositions 10 et 14) : un client « Non » à l'entretien reçoit QUAND MÊME l'échéancier de la
    machine qu'il achète — c'est la mémoire de la machine : 28 machines vendues à des « Non » de septembre à novembre
    n'étaient suivies nulle part, et leurs 21 entretiens payés depuis le 01/09 n'avaient aucune visite où s'inscrire.
    Il n'est pas relancé pour autant : les SMS et les listes d'appel l'écartent par son statut
    (relances_config.clients_exclus_relance), la lassitude aussi (clients_a_requalifier) ; le 2e appel et le SMS de
    réveil ne filtrent pas encore la case « Non » (compte rendu du 26/11/2026 : 0 client visé ce jour-là, 61 « Non »
    « À requalifier » joignables par le réveil plus tard). Les B2B, eux, restent sans échéancier."""
    cfg, fams = cfg or RC.config(), fams or RC.familles()
    client = _client(i_sal["customer"])
    if is_b2b_group(client.customer_group, cfg) or i_sal["customer"] in clients_generiques(cfg):
        return False
    interesse = is_customer_interested(client)
    items = []
    for brut in [x.strip() for x in (i_sal.get("items") or "").split(",,") if x.strip()]:
        item = resoudre_article(brut, cache)
        if not item:
            log(f"[WARN] SO {i_sal['sales_order']} : article '{brut}' introuvable")
            continue
        items.append(item)
    retenues = machines_de_la_commande(items, fams, _client_suivi(i_sal["customer"]))
    machines = [{"item_code": it["item_code"], "item_name": it["item_name"], "sales_order": i_sal["sales_order"]} for it, _ in retenues]
    familles_vendues = {fam for _, fam in retenues}
    if not machines:
        return False
    ms = _nouvel_echeancier(i_sal["customer"], i_sal["delivery_date"], machines, cfg)
    # Après la naissance (26/11/2026) : la devinette annulée nomme l'échéancier qui la remplace.
    for fam in sorted(familles_vendues):
        _retirer_echeancier_devine(i_sal["customer"], fam, fams, cache, remplace_par=ms.name, sales_order=i_sal["sales_order"], stats=stats)
    log(f"[CREATE] {ms.name} pour {i_sal['customer']}, SO {i_sal['sales_order']}, machines : {', '.join(m['item_code'] for m in machines)}"
        + ("" if interesse else " — client « Non » à l'entretien : mémoire de la machine, sans relance"))
    if not interesse:
        _compter(stats, "new_ms_non_interesses")
    _reactiver_client(i_sal["customer"])
    return True


def create_maintenance_from_consumable_guess(customer_name, family, delivery_date, sales_order, cfg=None, fams=None):
    cfg, fams = cfg or RC.config(), fams or RC.familles()
    item = get_default_item_for_family(family, fams)
    if not item:
        return None
    ms = _nouvel_echeancier(customer_name, delivery_date, [{"item_code": item["item_code"], "item_name": item["item_name"], "sales_order": sales_order}], cfg)
    log(f"[GUESS] {ms.name} deviné ({family} / {item['item_code']}) pour {customer_name} depuis {sales_order}")
    return ms


def update_single_machine_schedule(customer_name, family, delivery_date, sales_order, cfg=None, fams=None, cache=None, creer=True,
                                   jours_avance=None, stats=None, filtre=None):
    """Les échéanciers de cette famille chez ce client sont décalés (sauf celui que la commande vient de créer) ; s'il n'y
    en a aucun et que `creer` le permet, un échéancier deviné est créé. `jours_avance` : voir avance_permise (achat sans
    main-d'œuvre) ; la devinette, elle, solde toujours sa 1re visite (c'est sa naissance, pas une avance : elle fait
    partir le cycle de la date d'achat). `filtre(ms)` : ne garder que certains échéanciers (porte-filtres seuls)."""
    cfg, fams = cfg or RC.config(), fams or RC.familles()
    delivery_date = getdate(delivery_date)
    tous = find_machine_schedules(customer_name, family, fams, cache)
    if filtre is not None:
        tous = [ms for ms in tous if filtre(ms)]
    if not tous:
        if not creer or filtre is not None:
            return False
        ms = create_maintenance_from_consumable_guess(customer_name, family, delivery_date, sales_order, cfg, fams)
        if ms:
            shift_schedule_for_delivery(ms, delivery_date, sales_order, family, fams, cache, stats=stats)
        return bool(ms)
    existants = echeanciers_hors_commande(tous, sales_order)
    if not existants:
        log(f"[UPDATE] {customer_name} : l'échéancier {family} a été créé par {sales_order}, rien à décaler")
        return False
    anterieurs = [ms for ms in existants if achat_anterieur(delivery_date, ms)]
    if anterieurs:
        _compter(stats, "achats_anterieurs", len(anterieurs))
        log(f"[UPDATE] {sales_order} (livré le {delivery_date}) antérieur à " + ", ".join(ms.name for ms in anterieurs) + " : rien à y solder")
        noms = {ms.name for ms in anterieurs}
        existants = [ms for ms in existants if ms.name not in noms]
    fait = False
    for ms in existants:
        fait = shift_schedule_for_delivery(ms, delivery_date, sales_order, family, fams, cache, jours_avance=jours_avance, stats=stats) or fait
    return fait


def plan_des_familles(items, existantes, fams=None) -> dict:
    """Ce qu'un achat peut solder chez ce client, avant de regarder les visites. PURE avec `fams`.
    → {"sures": [(famille, motif)], "cartouches": [[familles candidates, dans l'ordre du réglage]]}, restreint aux
    familles que le client possède (`existantes`) :
    - « main-d'œuvre » : la famille de chaque main-d'œuvre d'entretien (M-E-OD → RO_DOM, M-E-Ad → ADOUCISSEUR…) ;
    - « consommable » : la famille d'un consommable qui n'en concerne qu'une (sel → adoucisseur, membrane 4040 → RO_IND) ;
    - une cartouche commune à plusieurs familles (charbon, sédiment…) : ses familles candidates."""
    fams = fams or RC.familles()
    existantes = set(existantes or ())
    ordre = [f["code"] for f in fams]
    rang = lambda c: (ordre.index(c) if c in ordre else len(ordre), c)
    entretien = {f["article_entretien"]: f["code"] for f in fams if f.get("article_entretien")}
    mo, sures, cartouches = [], [], []
    for item in items or []:
        code = ((item or {}).get("item_code") or "").strip()
        if code in entretien:
            mo.append(entretien[code])
            continue
        if not est_consommable(item, fams):
            continue
        fs = familles_consommable(item, fams)
        if len(fs) == 1:
            sures.append(fs[0])
        elif fs:
            candidates = sorted({f for f in fs if f in existantes}, key=rang)
            if candidates and candidates not in cartouches:
                cartouches.append(candidates)
    out = [(f, "main-d'œuvre") for f in sorted(set(mo), key=rang) if f in existantes]
    out += [(f, "consommable") for f in sorted(set(sures) - set(mo), key=rang) if f in existantes]
    return {"sures": out, "cartouches": cartouches}


def solder_selon_le_plan(plan, essayer) -> list:
    """Applique le plan : `essayer(famille)` solde cette famille chez le client et dit si une visite l'a été. PURE avec
    `essayer`. → [(famille soldée, motif)].

    Revue du 26/11/2026 (proposition 6, mesures x09_multifam) : une commande ne soldait que la première famille du
    réglage parmi celles du client, même quand sa main-d'œuvre désignait l'autre (M-E-Ad sur un échéancier osmoseur +
    adoucisseur soldait l'osmoseur) ; 114 visites échues restaient ouvertes sur les familles laissées, dont 52
    appelables. Désormais :
    1. chaque famille « sûre » (main-d'œuvre, puis consommable à famille unique) est essayée ;
    2. une cartouche commune ne solde qu'UNE famille : rien si elle est expliquée par une famille sûre ou par une
       cartouche déjà soldée (hors porte-filtre) — l'entretien de l'osmoseur domestique avec ses 3 cartouches ne solde
       pas aussi l'osmoseur commercial ; sinon la PREMIÈRE de ses candidates, dans l'ordre du réglage, qui a vraiment
       une visite à solder (MAT-MSH-2023-00469 : l'osmoseur domestique n'avait plus rien d'ouvert, la cartouche
       revenait au commercial ; s'arrêter à la première candidate l'aurait perdue).
    Le porte-filtre n'est pas choisi ici pour accompagner une machine : il suit l'osmoseur qu'il protège
    (shift_schedule_for_delivery et _solder_porte_filtres_seuls)."""
    soldees, essayees, expliquees = [], {}, set()

    def tenter(fam):
        if fam not in essayees:
            essayees[fam] = bool(essayer(fam))
        return essayees[fam]

    for fam, motif in plan.get("sures") or []:
        if tenter(fam) and (fam, motif) not in soldees:
            soldees.append((fam, motif))
        if fam not in FAMILLES_ACCESSOIRES:
            expliquees.add(fam)
    for candidates in plan.get("cartouches") or []:
        if expliquees & set(candidates):
            continue
        for fam in candidates:
            if fam in essayees:
                continue
            if tenter(fam):
                soldees.append((fam, "cartouche"))
                if fam not in FAMILLES_ACCESSOIRES:
                    expliquees.add(fam)
                break
    return soldees


def familles_a_solder(items, existantes, fams=None, essayer=None) -> list:
    """Les familles qu'un achat solde chez ce client, dans l'ordre : [(famille, motif)] (plan_des_familles puis
    solder_selon_le_plan). Sans `essayer`, chaque famille est supposée avoir une visite à solder : la vue « sur le
    papier », PURE avec `fams`."""
    return solder_selon_le_plan(plan_des_familles(items, existantes, fams), essayer or (lambda fam: True))


def _solder_porte_filtres_seuls(customer_name, delivery_date, sales_order, cfg, fams, cache, jours_avance=None, stats=None):
    """Les échéanciers faits UNIQUEMENT d'un porte-filtre (26 clients au 26/11/2026 ont un tel échéancier À CÔTÉ de
    celui de leur osmoseur) sont soldés avec l'osmoseur, comme le porte-filtre d'un même échéancier : avant, une cartouche
    commune les soldait aussi (toutes les familles du client), et familles_a_solder ne choisit plus qu'une famille par
    cartouche. → True si quelque chose a changé."""
    fait = False
    for accessoire in FAMILLES_ACCESSOIRES:
        if update_single_machine_schedule(customer_name, accessoire, delivery_date, sales_order, cfg, fams, cache, creer=False,
                                          jours_avance=jours_avance, stats=stats,
                                          filtre=lambda ms: est_accessoire({famille_machine(_item(i.item_code, cache), fams)
                                                                           for i in ms.items} - {None})):
            fait = True
            _compter(stats, "porte_filtres_avec_machine")
    return fait


def familles_cibles(items, fams=None):
    """(toutes les familles possibles, familles « sûres » = consommables à famille unique) pour les articles d'une commande.
    La main-d'œuvre d'entretien d'une famille (`article_entretien` du réglage : M-E-OD, M-E-Ad…) est le signal le plus sûr :
    1 121 entretiens de 2026 la portent, et 45 d'entre eux n'avaient AUCUN consommable connu (filtres offerts, pièces hors
    familles) et restaient invisibles. PURE."""
    cibles, sures = set(), set()
    entretien = {f["article_entretien"]: f["code"] for f in (fams or RC.familles()) if f.get("article_entretien")}
    for item in items:
        code = ((item or {}).get("item_code") or "").strip()
        if code in entretien:
            cibles.add(entretien[code])
            sures.add(entretien[code])
            continue
        if not est_consommable(item, fams):
            continue
        fs = familles_consommable(item, fams)
        cibles.update(fs)
        if len(fs) == 1:
            sures.add(fs[0])
    return cibles, sures


def update_maintenance_schedule(i_sal, cfg=None, fams=None, cache=None, creer=True, stats=None, annule_le=None):
    """Achat de consommables : on décale les échéanciers des familles concernées que le client possède ; s'il n'en a aucune,
    on en devine UNE (famille sûre puis ordre de priorité du réglage — plus de tirage dans un set).
    Un client « Non » à l'entretien qui a quand même un échéancier voit sa visite marquée réalisée (il a bien fait
    l'entretien : 104 clients « Non » entretenus en 12 mois au 15/10/2026 restaient « en retard » pour toujours) ; le
    « Non » ne gouverne que les relances et la DEVINETTE : pas d'échéancier deviné pour lui sur un simple achat de
    cartouches, mais OUI s'il paie une main-d'œuvre d'entretien (devinette_pour_un_non, 26/11/2026) — sans relance. Il
    reste « Non » : rien ici ne touche à cette case. `creer=False` (rattrapage) : jamais de devinette.
    26/11/2026 (lot 5) : les familles soldées sont celles de familles_a_solder (main-d'œuvre d'abord, une seule famille
    par cartouche), le porte-filtre suit l'osmoseur, et sans main-d'œuvre d'entretien une visite n'est consommée que si
    elle est échue ou due dans `jours_avance_sans_main_oeuvre` jours."""
    cfg, fams = cfg or RC.config(), fams or RC.familles()
    client = _client(i_sal["customer"])
    if is_b2b_group(client.customer_group, cfg) or i_sal["customer"] in clients_generiques(cfg):
        return "skip"
    interesse = is_customer_interested(client)
    items = [it for it in (resoudre_article(b, cache) for b in (i_sal.get("items") or "").split(",,") if b.strip()) if it]
    cibles, sures = familles_cibles(items, fams)
    if not cibles:
        log(f"[NO-LINK] SO {i_sal['sales_order']} : aucun lien machine/consommable")
        return "no-link"
    existantes = get_customer_machine_families(i_sal["customer"], fams, cache)
    if cibles & existantes:
        jours = None if commande_avec_main_oeuvre(items, fams) else jours_avance_sans_main_oeuvre(cfg)
        plan = plan_des_familles(items, existantes, fams)
        soldees = solder_selon_le_plan(plan, lambda fam: update_single_machine_schedule(
            i_sal["customer"], fam, i_sal["delivery_date"], i_sal["sales_order"], cfg, fams, cache, creer=creer,
            jours_avance=jours, stats=stats))
        log(f"[UPDATE] SO {i_sal['sales_order']} : plan {plan}, soldées {soldees}"
            + ("" if jours is None else f" (sans main-d'œuvre : avance de {jours} j au plus)"))
        fait = bool(soldees)
        # Porte-filtres seuls : avec l'osmoseur soldé, sauf si le plan a déjà essayé leur famille (cartouche propre au
        # porte-filtre, ou cartouche commune qu'il a reçue).
        deja_pf = {f for f, _m in plan["sures"] + soldees if f in FAMILLES_ACCESSOIRES}
        if any(pf_protege(f, fams) for f, _m in soldees) and (existantes & set(FAMILLES_ACCESSOIRES)) - deja_pf:
            fait = _solder_porte_filtres_seuls(i_sal["customer"], i_sal["delivery_date"], i_sal["sales_order"], cfg, fams, cache,
                                               jours_avance=jours, stats=stats) or fait
        return "update" if fait else "rien"
    if not creer:
        return "skip"
    pour_un_non = not interesse
    if pour_un_non and not devinette_pour_un_non(items, cfg, fams):
        return "skip"
    if not devinette_permise(i_sal["delivery_date"], annule_le):
        # Relecture finale du 26/11/2026 : l'échéancier du client a été ANNULÉ (machine rendue, commande annulée, « pas de
        # système », à la main) après cet achat. Un vieil achat de cartouches resté libre (règle des 60 jours) faisait
        # naître la nuit suivante une machine devinée — relancée pour une machine que le client n'a plus.
        if stats is not None:
            stats["achats_avant_annulation"] = stats.get("achats_avant_annulation", 0) + 1
        log(f"[ANNULE] SO {i_sal['sales_order']} ({i_sal['customer']}) : livrée le {i_sal['delivery_date']}, avant l'annulation "
            f"d'un échéancier du client le {annule_le} — pas de devinette")
        return "skip"
    devinee = RC.famille_prioritaire(sures or cibles, fams)
    log(f"[GUESS] SO {i_sal['sales_order']} : famille devinée {devinee} (sûres={sorted(sures)}, cibles={sorted(cibles)})"
        + (" — client « Non » qui paie un entretien : échéancier sans relance" if pour_un_non else ""))
    devine = update_single_machine_schedule(i_sal["customer"], devinee, i_sal["delivery_date"], i_sal["sales_order"], cfg, fams, cache)
    if devine and pour_un_non and stats is not None:
        stats["devines_non_interesses"] = stats.get("devines_non_interesses", 0) + 1
    return "guess" if devine else "no-guess"


def devinette_pour_un_non(items, cfg=None, fams=None) -> bool:
    """Un client « Non » à l'entretien qui PAIE une main-d'œuvre d'entretien (M-E-…) et n'a aucun échéancier de la famille
    reçoit quand même un échéancier deviné — sa mémoire, SANS relance (il reste « Non » : SMS, listes d'appel et réveil
    l'écartent par son statut). Sa demande du 26/11/2026 : sur 45 jours, 11 entretiens payés par des clients « Non » dont
    la machine n'a pas été achetée chez nous n'étaient inscrits nulle part (Hbib Ghrairi, client depuis 2024, 7 commandes ;
    Nizar Chamli, deux entretiens en octobre). Un simple achat de cartouches, sans main-d'œuvre, ne devine toujours rien
    pour un « Non ». Réglage `echeancier_non_avec_entretien` (oui par défaut). PURE avec `cfg` et `fams`."""
    cfg = RC.config() if cfg is None else cfg
    v = cfg.get("echeancier_non_avec_entretien", 1)
    return bool(cint(1 if v in (None, "") else v)) and commande_avec_main_oeuvre(items, fams)


def devinette_permise(delivery_date, annule_le) -> bool:
    """Un achat de consommables peut-il deviner une machine ? Non s'il a été livré le jour ou avant la dernière
    annulation d'un échéancier du client (`annule_le`). Un achat livré APRÈS devine comme toujours. PURE."""
    return not (annule_le and getdate(delivery_date) <= getdate(annule_le))


def clients_echeancier_annule(depuis) -> dict:
    """{client: jour de la dernière annulation d'un de ses échéanciers} depuis `depuis` — une requête (date de
    modification de l'échéancier annulé : l'annulation est sa dernière écriture)."""
    return {c: getdate(d) for c, d in frappe.db.sql("""
        select customer, max(date(modified)) from `tabMaintenance Schedule`
        where docstatus = 2 and ifnull(customer, '') <> '' and modified >= %s group by customer""", (getdate(depuis),))}


def traiter_commande(i_sal, cfg, fams, cache, creer=True, stats=None, annule_le=None):
    """Une commande livrée : ses machines (échéancier neuf) PUIS ses consommables (visite réalisée sur les échéanciers
    existants). Avant le 15/10/2026, une commande qui créait une machine n'allait pas plus loin : l'entretien d'un
    osmoseur avec un porte-filtre de rechange créait un échéancier « porte-filtre » et ne soldait jamais la visite.
    → (machine créée ?, résultat des consommables)."""
    cree = bool(creer and create_maintenance_for_machines(i_sal, cfg, fams, cache, stats=stats))
    return cree, update_maintenance_schedule(i_sal, cfg, fams, cache, creer=creer, stats=stats, annule_le=annule_le)


# ----------------------------------------------------------------------
# 7) PASSAGE PRINCIPAL
# ----------------------------------------------------------------------

CLIENTS_GENERIQUES_DEFAUT = "PASSAGER"


def clients_generiques(cfg=None) -> list:
    """Les clients « de comptoir » (réglage `clients_generiques`, PASSAGER par défaut) : une vente à PASSAGER n'a pas de
    propriétaire connu, sa machine ne se suit pas. Mesure du 26/11/2026 : ouvrir la nuit aux clients « Non » donnait 11
    échéanciers à PASSAGER (« Non », 416 commandes), et chacune de ses commandes de cartouches était comparée à ces 11
    machines. Avant ce jour, il était écarté parce que « Non » ; il l'est désormais nommément. PURE avec `cfg`."""
    cfg = RC.config() if cfg is None else cfg
    v = cfg.get("clients_generiques", CLIENTS_GENERIQUES_DEFAUT)
    return RC.liste(v if str(v or "").strip() else CLIENTS_GENERIQUES_DEFAUT)


def commandes_a_traiter(cfg, de=None, a=None):
    """Commandes livrées (ou BL rapproché) de clients non B2B, dans la fenêtre du réglage (ou [de, a] pour un rattrapage).
    26/11/2026 (lot 4) : les clients « Non » à l'entretien sans échéancier sont lus aussi — leur machine reçoit son
    échéancier (create_maintenance_for_machines) ; leurs achats de consommables ne créent rien (pas de devinette). Les
    clients génériques (PASSAGER) ne sont jamais lus."""
    today = nowdate()
    return frappe.db.sql("""
        SELECT so.name AS sales_order, so.customer, so.delivery_date,
               GROUP_CONCAT(DISTINCT so_item.item_code SEPARATOR ",, ") AS items
        FROM `tabSales Order` so
        JOIN `tabCustomer` cust ON so.customer = cust.name
        LEFT JOIN `tabSales Order Item` so_item ON so.name = so_item.parent
        LEFT JOIN `tabDelivery Note Item` dni ON so.name = dni.against_sales_order
        LEFT JOIN `tabDelivery Note` dn ON dni.parent = dn.name AND dn.docstatus = 1 AND dn.status != 'Closed'
        WHERE so.docstatus = 1
          AND IFNULL(cust.customer_group, '') NOT IN %(b2b)s
          AND cust.name NOT IN %(generiques)s
          AND (so.delivery_status = 'Fully Delivered' OR (dn.name IS NOT NULL AND dn.custom_reconciliation_stock IS NOT NULL))
          AND so.delivery_date BETWEEN %(de)s AND %(a)s
        GROUP BY so.name, so.customer, so.delivery_date
        ORDER BY so.delivery_date""",
        {"b2b": tuple(cfg["groupes_b2b_liste"]) or ("",), "generiques": tuple(clients_generiques(cfg)) or ("",),
         "de": de or add_months(today, -cfg["fenetre_mois_avant"]), "a": a or add_months(today, cfg["fenetre_mois_apres"])},
        as_dict=True)


def commandes_couvertes(cfg=None):
    """Les commandes déjà portées par un échéancier soumis OU ANNULÉ (ligne machine ou visite réalisée).

    26/11/2026 (lot 4) : la nuit annule au lieu de supprimer, et un échéancier annulé continue de couvrir ses commandes.
    Sinon la nuit suivante les relirait :
    - devinette annulée à l'arrivée de la vraie machine : son achat de cartouches, libre, serait relu chaque nuit ;
    - « pas de système » (liste d'appel), annulation à la main : la machine serait recréée la nuit même, la décision
      humaine défaite ;
    - B2B : tant que le client est B2B, ses commandes ne sont de toute façon pas lues.
    Exceptions voulues :
    - le doublon porte-filtre annulé par retirer_echeanciers_accessoires_en_double libère explicitement sa commande
      (lien effacé, nommé dans le commentaire) pour que l'osmoseur la reçoive ;
    - relecture du 26/11/2026 : un échéancier annulé par le nettoyage B2B (commentaire MARQUE_B2B) ne couvre plus rien
      quand son client n'est PLUS dans un groupe B2B (mal classé, puis corrigé). La suppression d'avant ce lot libérait
      ses commandes et la nuit recréait sa machine ; sans cette exception, elle ne serait plus jamais suivie. Pas de
      boucle : redevenu B2B, le client n'est plus lu, et l'échéancier recréé couvre à son tour sa commande.
    `_commande_deja_portee` reste, lui, limité aux échéanciers soumis : c'est la règle d'ERPNext (validate_sales_order)
    qu'il contourne. Avant ce lot, tout échéancier annulé était aussitôt supprimé : 0 annulé sur la copie du 25/11/2026."""
    cfg = cfg or RC.config()
    vivant = """(ms.docstatus = 1 or (ms.docstatus = 2 and not (
                    ifnull((select c.customer_group from tabCustomer c where c.name = ms.customer), '') not in %(b2b)s
                    and exists (select 1 from tabComment cm where cm.reference_doctype = 'Maintenance Schedule'
                                and cm.reference_name = ms.name and cm.comment_type = 'Info' and cm.content like %(marque)s))))"""
    out = set()
    for (so,) in frappe.db.sql("""select msi.sales_order from `tabMaintenance Schedule Item` msi join `tabMaintenance Schedule` ms on ms.name = msi.parent
                                  where {0} and ifnull(msi.sales_order, '') <> ''
                                  union select msd.custom_sales_order from `tabMaintenance Schedule Detail` msd join `tabMaintenance Schedule` ms on ms.name = msd.parent
                                  where {0} and ifnull(msd.custom_sales_order, '') <> ''""".format(vivant),
                               {"b2b": tuple(cfg["groupes_b2b_liste"]) or ("",), "marque": "%" + MARQUE_B2B + "%"}):
        for s in (so or "").split(","):
            if s.strip():
                out.add(s.strip())
    return out


# Le libellé de la réponse d'appel (liste_appels.PAS_DE_SYSTEME), repris si ce module ne s'importe pas.
PAS_DE_SYSTEME_DEFAUT = "Il n'y a pas de système de traitement installé"


def creation_permise(delivery_date, refus_le) -> bool:
    """Une commande peut-elle créer un échéancier (machine ou devinette) ? Non si le client a répondu « pas de système »
    le `refus_le` ou après sa livraison (l'échéancier supprimé ce jour-là venait d'elle ou d'une plus ancienne). PURE.
    Une commande livrée APRÈS la réponse est une nouvelle machine : elle crée, comme toujours."""
    return not (refus_le and getdate(delivery_date) <= getdate(refus_le))


def clients_pas_de_systeme(depuis) -> dict:
    """{client: dernier jour où il a répondu « pas de système »} — listes d'appel validées depuis `depuis`, et seulement
    quand l'échéancier de la ligne n'existe plus (SUPPRIMÉ). Une requête légère (lignes d'appel, pas de Deleted Document).

    Relecture du 26/11/2026 : l'ancien Server Script « validation finale liste appelle » SUPPRIME l'échéancier (cancel puis
    delete_doc) quand le client répond qu'il n'a pas de système : 362 suppressions sur la copie du 25/11/2026. Une
    suppression ne couvre plus rien (commandes_couvertes ne lit que les docstatus 1 et 2). Avant le lot 4, le client,
    mis « Non » à l'appel, n'était plus lu ; la nuit lit désormais les « Non », et elle recréerait l'échéancier de la
    machine que le client dit ne pas avoir. liste_validation (25/11/2026) ANNULE au lieu de supprimer (l'échéancier
    annulé couvre alors sa commande) et le patch listes_appels_dans_l_app éteint l'ancien script… seulement si son code
    n'a pas changé : cette règle rend la nuit sûre quel que soit l'ordre du déploiement. Les commandes de ces clients
    livrées jusqu'à ce jour-là sont lues SANS création (creation_permise) : leurs achats d'entretien s'inscrivent encore
    sur leurs autres échéanciers. Lire les traces « Deleted Document » de ces échéanciers (6,7 Go sur la copie, 97 426
    suppressions depuis juin, sans index sur deleted_name) coûtait 18 s par nuit : écarté. Sur la copie, aucune des 262
    commandes de ces échéanciers n'est dans la fenêtre de la nuit.
    « N'existe plus » se juge par la date : un échéancier de ce nom créé APRÈS la liste n'est pas celui qui a été appelé.
    Frappe reprend le numéro d'un document supprimé s'il était le dernier de sa série (revert_series_if_last, cas des
    fantômes Moalla) : l'échéancier recréé portait le nom du supprimé, et la règle se serait éteinte d'elle-même la nuit
    suivante (constaté à l'essai du 26/11/2026)."""
    try:
        from customization_app.liste_appels import PAS_DE_SYSTEME
    except Exception:
        PAS_DE_SYSTEME = PAS_DE_SYSTEME_DEFAUT
    return {c: getdate(d) for c, d in frappe.db.sql("""
        select a.client, max(date(ifnull(l.date_fin, l.modified))) from `tabAppelle Client` a
        join `tabListe Appelle Entretien` l on l.name = a.parent
        where a.resume_appel = %s and l.docstatus = 1 and ifnull(a.client, '') <> ''
          and ifnull(a.`échéancier_dentretien`, '') <> '' and date(ifnull(l.date_fin, l.modified)) >= %s
          and not exists (select 1 from `tabMaintenance Schedule` ms where ms.name = a.`échéancier_dentretien`
                          and ms.creation <= l.creation)
        group by a.client""", (PAS_DE_SYSTEME, getdate(depuis)))}


@frappe.whitelist()
def run_maintenance_planning():
    """Nettoyage (B2B annulés), prolongation, puis chaque commande livrée de la fenêtre : machine → échéancier,
    consommables → décalage / devinette ; enfin la lassitude. CHAQUE COMMANDE EST ISOLÉE (savepoint) : une erreur est
    journalisée et n'arrête pas les autres. Le compte rendu va dans le journal, l'entonnoir (« nuit ») et, en cas
    d'erreur, l'alerte e-mail. Rejouée la même nuit, elle ne crée, n'annule ni ne marque rien de plus (deux nuits
    simulées de suite sur la copie du 25/11/2026)."""
    if frappe.session.user != "Administrator" and not frappe.flags.in_test:
        frappe.only_for(("System Manager", "Maintenance Manager"))
    cfg, fams, cache = RC.config(), RC.familles(), {}
    log("========== [CRON] Début run_maintenance_planning ==========")
    # Compteurs des lots 4 et 5 (26/11/2026), alimentés au fil des commandes. Une commande en erreur est annulée en
    # entier (savepoint) : ses compteurs sont recopiés seulement si elle aboutit.
    detail = {"new_ms_non_interesses": 0, "devines_annules": 0, "avances_evitees": 0, "porte_filtres_avec_machine": 0,
              "achats_anterieurs": 0, "pas_de_systeme": 0, "achats_avant_annulation": 0, "devines_non_interesses": 0}
    summary = {"total_sales_orders": 0, "new_ms_created": 0, "updated_from_consumables": 0, "guessed": 0,
               "no_link": 0, "already_covered": 0, "errors": 0,
               "cleanup": _etape(lambda: verify_data_base(cfg), "Nettoyage échéancier — passage de nuit",
                                 {"corriges": 0, "annules": 0, "supprimes": 0, "brouillons_laisses": 0, "erreurs": 1})}
    summary.update(detail)
    # La prolongation passe AVANT les commandes (26/11/2026) : un entretien payé sur un échéancier qui n'avait plus de
    # visite (SAL-ORD-2026-03311, 04730 sur la copie) ne trouvait rien à solder, puis la prolongation ajoutait les
    # visites, et la commande ne s'inscrivait que la nuit SUIVANTE. Les visites que les commandes consomment sont
    # reprolongées au fil de l'eau (shift_schedule_for_delivery → extend_schedule_for_sms).
    summary["prolonges"] = _etape(prolonger_echeanciers_epuises, "Prolongation échéancier — passage de nuit", None)
    if summary["prolonges"] is None:
        summary["prolonges"], summary["errors"] = 0, summary["errors"] + 1
    commandes = commandes_a_traiter(cfg)
    couvertes = commandes_couvertes(cfg)
    # Relecture du 26/11/2026 : après une réponse « pas de système » dont l'échéancier a été SUPPRIMÉ, les commandes
    # livrées jusqu'à ce jour-là ne créent plus rien. Une panne de cette lecture n'arrête pas la nuit (rien d'écarté,
    # erreur comptée et journalisée).
    debut = add_months(nowdate(), -(cint(cfg["fenetre_mois_avant"]) + cint(cfg["fenetre_mois_apres"])))
    refus = _etape(lambda: clients_pas_de_systeme(debut), "Échéancier maintenance — réponses « pas de système »", None)
    if refus is None:
        refus, summary["errors"] = {}, summary["errors"] + 1
    annules = _etape(lambda: clients_echeancier_annule(debut), "Échéancier maintenance — échéanciers annulés", None)
    if annules is None:
        annules, summary["errors"] = {}, summary["errors"] + 1
    summary["total_sales_orders"] = len(commandes)
    erreurs = []
    for i_sal in commandes:
        if i_sal["sales_order"] in couvertes:
            summary["already_covered"] += 1
            continue
        creer = creation_permise(i_sal["delivery_date"], refus.get(i_sal["customer"]))
        if not creer:
            summary["pas_de_systeme"] += 1
            log(f"[PAS-DE-SYSTEME] {i_sal['sales_order']} ({i_sal['customer']}) lue sans création : réponse « pas de système » "
                f"le {refus.get(i_sal['customer'])}")
        frappe.db.savepoint("ms_so")
        stats = {}
        try:
            cree, r = traiter_commande(i_sal, cfg, fams, cache, creer=creer, stats=stats, annule_le=annules.get(i_sal["customer"]))
            if cree:
                summary["new_ms_created"] += 1
            if r == "update":
                summary["updated_from_consumables"] += 1
            elif r == "guess":
                summary["guessed"] += 1
            elif r == "no-link" and not cree:
                summary["no_link"] += 1
            for cle, n in stats.items():
                summary[cle] = summary.get(cle, 0) + n
        except Exception:
            frappe.db.rollback(save_point="ms_so")
            summary["errors"] += 1
            erreurs.append(i_sal["sales_order"])
            frappe.log_error(frappe.get_traceback(), f"Échéancier maintenance — commande {i_sal['sales_order']}")
    summary["requalifies"] = _etape(lambda: requalifier_clients_silencieux(cfg), "Échéancier maintenance — lassitude", None)
    if summary["requalifies"] is None:
        summary["requalifies"], summary["errors"] = 0, summary["errors"] + 1
    summary_line = ("[SUMMARY] total=%(total_sales_orders)s new_ms=%(new_ms_created)s new_ms_non=%(new_ms_non_interesses)s "
                    "from_conso=%(updated_from_consumables)s guessed=%(guessed)s no_link=%(no_link)s already_covered=%(already_covered)s "
                    "devines_annules=%(devines_annules)s avances_evitees=%(avances_evitees)s achats_anterieurs=%(achats_anterieurs)s "
                    "pf_avec_machine=%(porte_filtres_avec_machine)s pas_de_systeme=%(pas_de_systeme)s "
                    "avant_annulation=%(achats_avant_annulation)s devines_non=%(devines_non_interesses)s "
                    "prolonges=%(prolonges)s requalifies=%(requalifies)s errors=%(errors)s" % summary)
    summary_line += " cleanup_annules=%s" % summary["cleanup"].get("annules", 0)
    if erreurs:
        summary_line += " (" + ", ".join(erreurs[:10]) + ")"
    log(summary_line)
    log("========== [CRON] Fin run_maintenance_planning ==========")
    nb_err = summary["errors"] + summary["cleanup"]["erreurs"]
    if nb_err:
        RC.alerter("[ERPNext] Échéanciers de maintenance : %d erreur(s) cette nuit" % nb_err,
                   "<p>%s</p><p>Commandes en erreur : %s</p><p>Détail dans Journal des erreurs (/app/error-log) et la page Journaux des relances.</p>"
                   % (frappe.utils.escape_html(summary_line), ", ".join(erreurs) or "—"), cfg)
    _noter_entonnoir(summary)
    return {"summary": summary, "log": summary_line}


def _etape(fn, titre, si_erreur):
    """Une étape de la nuit qui ne doit pas arrêter les autres (26/11/2026 : la prolongation passe avant les commandes,
    une panne de sa requête ne doit pas priver la nuit des commandes ni du compte rendu). Erreur → journal + `si_erreur`.
    Les titres commencent comme ceux que relances_config.CRONS rattache à ce passage."""
    frappe.db.savepoint("ms_etape")
    try:
        return fn()
    except Exception:
        frappe.db.rollback(save_point="ms_etape")
        frappe.log_error(frappe.get_traceback(), titre)
        return si_erreur


def _noter_entonnoir(summary) -> bool:
    """Contrat partagé du 25/11/2026 : le compte rendu de la nuit va dans l'entonnoir des relances
    (entonnoir_relances.noter, qui ne lève jamais). Le module peut manquer (déploiement partiel) : jamais bloquant."""
    try:
        from customization_app.entonnoir_relances import noter
    except Exception:
        return False
    try:
        return bool(noter("nuit", summary))
    except Exception:
        log("[ENTONNOIR] compte rendu de nuit non noté")
        return False


def texte_reactivation(non, relancer_non) -> str:
    """Le commentaire d'un client « À requalifier » réactivé par un achat d'entretien. PURE. Il commence par « ✅ » (le
    panneau « Historique client » ne garde que ces débuts). Relecture du 26/11/2026 : un « Non » ne redevient pas « Oui »,
    mais il n'est « ni SMS ni appel » que si « Relancer les non intéressés » n'est pas coché — coché, clients_exclus_relance
    ne l'écarte plus et, son statut levé, il est de nouveau relancé."""
    if not non:
        return "✅ Relance réactivée : achat lié à l’entretien."
    debut = "✅ Statut « À requalifier » levé : achat lié à l’entretien. Le client reste « Non » à l’entretien"
    if relancer_non:
        return debut + ", mais il peut de nouveau être relancé : « Relancer les non intéressés » est coché dans Config Relances."
    return debut + " : toujours ni SMS ni appel."


def _reactiver_client(customer, cfg=None):
    """Un achat lié à l'entretien remet à vide un client « À requalifier » (pas « Perdu », décision humaine).
    26/11/2026 : la case « intéressé par l'entretien » n'est JAMAIS touchée ici — un client « Non » qui paie un entretien
    reste « Non » (pas de bascule automatique : c'est sa réponse, pas une déduction). Son commentaire le dit
    (texte_reactivation), au lieu d'annoncer une relance qui n'aura pas lieu."""
    if frappe.db.has_column("Customer", "custom_statut_relance") and frappe.db.get_value("Customer", customer, "custom_statut_relance") == "À requalifier":
        frappe.db.set_value("Customer", customer, "custom_statut_relance", "")
        non = (frappe.db.get_value("Customer", customer, "custom_intéressé_par_le_service_entretien") or "").strip().upper() == "NON"
        relancer_non = bool(cint((cfg or RC.config()).get("relancer_non_interesses")))
        _commenter("Customer", customer, texte_reactivation(non, relancer_non))
        log(f"[REQUALIF] {customer} réactivé par un achat" + (" (reste « Non »)" if non else ""))


def clients_non_interesses(cfg=None) -> set:
    """Les clients « Non » à l'entretien quand le réglage ne les relance pas (`relancer_non_interesses` = 0) — la même
    source que relances_config.clients_exclus_relance ; ensemble vide si on les relance."""
    cfg = cfg or RC.config()
    if cint(cfg.get("relancer_non_interesses")):
        return set()
    return set(frappe.get_all("Customer", filters={"custom_intéressé_par_le_service_entretien": "Non"}, pluck="name"))


def cycles_sans_reponse_par_client(cfg=None, statut="") -> dict:
    """{client: nombre de CYCLES sans réponse} — un cycle = un mois d'échéance distinct avec visite échue, SMS 1 + SMS 2 +
    appel, non réalisée, POSTÉRIEURE à la dernière visite réalisée du client ; hors B2B. Jusqu'au 15/10/2026 on comptait
    des VISITES : un client à trois appareils ratait un seul cycle et passait « À requalifier » (23 clients sur 401).
    Relecture finale du 26/11/2026 : jamais plus de cycles que de dates d'appel distinctes — un seul appel marque une
    visite par article, à des mois d'échéance différents, et comptait pour 2 ou 3 cycles (Fathi Mekni : 1 appel, 2 cycles ;
    Afef dridi suspendue après 2 appels). Ne fait que baisser un compte (15 clients de 2 à 1 sur la copie du 25/11).
    `statut` : les clients de ce statut de relance ('' = relancés normalement). Une requête."""
    cfg = cfg or RC.config()
    return dict(frappe.db.sql("""
        select ms.customer, least(count(distinct date_format(d.scheduled_date, '%%Y-%%m')), count(distinct d.custom_appelle))
        from `tabMaintenance Schedule Detail` d
        join `tabMaintenance Schedule` ms on ms.name = d.parent and ms.docstatus = 1
        join tabCustomer c on c.name = ms.customer
        where d.actual_date is null and d.scheduled_date < %(today)s
          and d.custom_sms_1 is not null and d.custom_sms_2 is not null and d.custom_appelle is not null
          and ifnull(c.custom_statut_relance, '') = %(statut)s and ifnull(c.customer_group, '') not in %(b2b)s
          and d.scheduled_date > ifnull((select max(d2.actual_date) from `tabMaintenance Schedule Detail` d2
                                          join `tabMaintenance Schedule` m2 on m2.name = d2.parent where m2.customer = ms.customer), '1900-01-01')
        group by ms.customer""", {"today": nowdate(), "statut": statut, "b2b": tuple(cfg["groupes_b2b_liste"]) or ("",)}))


def clients_a_requalifier(cfg=None) -> list:
    """Clients avec au moins `cycles_sans_reponse` cycles sans réponse, encore relancés normalement, SANS rendez-vous
    récent ou à venir : un client requalifié qui prenait rendez-vous à l'appel était réactivé par la liste puis
    re-requalifié la nuit même (ses visites restent ouvertes jusqu'à l'intervention) — 19 clients marqués 2 à 4 fois
    au 15/10/2026.
    26/11/2026 : ni les clients « Non » à l'entretien (sauf réglage `relancer_non_interesses`) : on ne les relance pas,
    la lassitude n'a pas de sens pour eux, et « À requalifier » les ferait entrer dans la liste d'appel Requalification
    (qui ne filtre que ce statut). Leur échéancier est gardé depuis ce jour : leurs anciens SMS compteraient."""
    cfg = cfg or RC.config()
    n = cint(cfg["cycles_sans_reponse"])
    if n <= 0 or not frappe.db.has_column("Customer", "custom_statut_relance"):
        return []
    hors = RC.clients_avec_rdv(cfg) | RC.clients_reportes() | clients_non_interesses(cfg)
    return [c for c, cycles in cycles_sans_reponse_par_client(cfg, "").items() if cycles >= n and c not in hors]


def requalifies_a_tort(cfg=None) -> list:
    """Les « À requalifier » qui n'atteignent pas (ou plus) le seuil de cycles (comptés en visites avant le 15/10/2026, ou
    visites soldées depuis par le rattrapage des entretiens), ou qui ont un rendez-vous. → [(client, cycles)]."""
    cfg = cfg or RC.config()
    n = cint(cfg["cycles_sans_reponse"])
    if n <= 0 or not frappe.db.has_column("Customer", "custom_statut_relance"):
        return []
    cycles = cycles_sans_reponse_par_client(cfg, "À requalifier")
    rdv = RC.clients_avec_rdv(cfg)
    return [(c, cycles.get(c, 0)) for c in frappe.get_all("Customer", filters={"custom_statut_relance": "À requalifier"}, pluck="name")
            if cycles.get(c, 0) < n or c in rdv]


def reactiver_requalifies_a_tort(cfg=None) -> int:
    cfg = cfg or RC.config()
    n = 0
    for customer, cycles in requalifies_a_tort(cfg):
        frappe.db.set_value("Customer", customer, "custom_statut_relance", "")
        frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": "Customer", "reference_name": customer,
                        "content": "✅ Relance réactivée le %s : %s." % (_jj(nowdate()),
                                   "rendez-vous récent ou à venir" if cycles >= cint(cfg["cycles_sans_reponse"]) else
                                   "%d cycle(s) sans réponse, sous le seuil de %d (la règle comptait des visites, pas des cycles)"
                                   % (cycles, cint(cfg["cycles_sans_reponse"])))}).insert(ignore_permissions=True)
        n += 1
    log(f"[REQUALIF] clients réactivés (sous le seuil de cycles) : {n}")
    return n


def requalifier_clients_silencieux(cfg=None) -> int:
    """Chaque nuit : les clients silencieux passent « À requalifier » (sortie de la relance automatique, un dernier appel via
    la liste Requalification). → nombre de clients marqués."""
    cfg = cfg or RC.config()
    marques = 0
    for customer in clients_a_requalifier(cfg):
        frappe.db.set_value("Customer", customer, "custom_statut_relance", "À requalifier")
        frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": "Customer", "reference_name": customer,
                        "content": "⏸️ Relance d’entretien suspendue : %d cycles (SMS 1, SMS 2, appel) sans réponse. Dernier appel via la liste « Requalification »."
                                   % cint(cfg["cycles_sans_reponse"])}).insert(ignore_permissions=True)
        marques += 1
    log(f"[REQUALIF] clients passés « À requalifier » : {marques}")
    return marques


def echeanciers_epuises(aujourd_hui=None) -> list:
    """Les échéanciers soumis dont au moins un article n'a plus aucune visite à venir — une requête, même règle que
    articles_a_prolonger (25/11/2026 ; avant : « toutes les visites ont reçu un SMS », qui en bloquait 1 511)."""
    return frappe.db.sql_list("""
        select distinct x.parent from (
            select d.parent, d.item_code,
                   sum(d.scheduled_date > %(today)s and d.actual_date is null
                       and ifnull(d.completion_status, '') != 'Fully Completed') as a_venir
            from `tabMaintenance Schedule Detail` d
            join `tabMaintenance Schedule` ms on ms.name = d.parent
            where ms.docstatus = 1 and ifnull(d.item_code, '') != '' and d.scheduled_date is not null
            group by d.parent, d.item_code) x
        where x.a_venir = 0
        order by x.parent""", {"today": getdate(aujourd_hui or nowdate())})


def prolonger_echeanciers_epuises(extra_visits_per_item=None):
    """Chaque nuit : les articles qui n'ont plus de visite à venir reçoivent leurs visites suivantes (la prolongation « au
    fil des SMS » ne rattrape pas un échéancier qui n'a plus rien à relancer — 23 échéanciers muets au 03/10/2026, 38 au
    25/11/2026), sauf ceux qui doublent un autre flux du client (`articles_geles`, 26/11/2026). Chaque échéancier est
    isolé. → nombre prolongés."""
    n, erreurs, geles = 0, 0, 0
    jour = nowdate()
    for name in echeanciers_epuises(jour):
        frappe.db.savepoint("ms_extend")
        try:
            ms = frappe.get_doc("Maintenance Schedule", name)
            if extend_schedule_for_sms(ms, cint(extra_visits_per_item) or None, aujourd_hui=jour):
                ms.flags.ignore_permissions = True
                ms.save()
                n += 1
            geles += len(ms.flags.get("articles_geles") or {})
        except Exception:
            frappe.db.rollback(save_point="ms_extend")
            erreurs += 1
            frappe.log_error(frappe.get_traceback(), f"Prolongation échéancier {name}")
    log(f"[EXTEND] échéanciers épuisés prolongés : {n}, articles laissés gelés (jumeau / porte-filtre) : {geles}, erreurs : {erreurs}")
    return n


def echeanciers_a_prolonger(aujourd_hui=None) -> list:
    """Les échéanciers épuisés qui ont au moins un article à ranimer (hors jumeaux / porte-filtres gardés gelés) : doit
    valoir [] après le passage de nuit. Pour l'entonnoir et les contrôles."""
    jour = aujourd_hui or nowdate()
    out, fams, cache = [], RC.familles(), {}
    for name in echeanciers_epuises(jour):
        ms = frappe.get_doc("Maintenance Schedule", name)
        a = articles_a_prolonger(ms.schedules, jour)
        if [c for c in a if c not in articles_geles(ms, a, jour, fams, cache)]:
            out.append(name)
    return out


@frappe.whitelist()
def extend_sms_for_all_active_schedules(extra_visits_per_item=None):
    """Même chose, à la main (bouton / console)."""
    frappe.only_for(("System Manager", "Maintenance Manager"))
    return prolonger_echeanciers_epuises(extra_visits_per_item)


# ----------------------------------------------------------------------
# 8) RATTRAPAGE (15/10/2026) — commandes d'entretien jamais appliquées à l'échéancier
# ----------------------------------------------------------------------

def commande_rattrapable(delivery_date, derniere_realisee) -> bool:
    """Une commande ne se rejoue que si elle est POSTÉRIEURE à la dernière visite réalisée du client : avant, l'échéancier
    a déjà avancé, la marquer ne changerait rien aux relances et déplacerait des visites déjà cyclées. PURE."""
    return not derniere_realisee or getdate(delivery_date) > getdate(derniere_realisee)


def est_accessoire(familles) -> bool:
    """Un échéancier fait uniquement de machines accessoires (porte-filtre). PURE."""
    return bool(familles) and set(familles) <= set(FAMILLES_ACCESSOIRES)


def _familles_par_echeancier(fams=None, cache=None) -> dict:
    """{échéancier soumis: (client, {familles de ses machines})} — une requête pour toute la base."""
    out = {}
    for ms, customer, code in frappe.db.sql("""select ms.name, ms.customer, msi.item_code from `tabMaintenance Schedule` ms
                                               join `tabMaintenance Schedule Item` msi on msi.parent = ms.name where ms.docstatus = 1"""):
        cust, fs = out.setdefault(ms, (customer, set()))
        f = famille_machine(_item(code, cache), fams)
        if f:
            fs.add(f)
    return out


def _dernieres_visites_realisees(par_ms) -> dict:
    """{client: date de la dernière visite réalisée} sur les échéanciers soumis, hors accessoires — une requête."""
    out = {}
    for ms, customer, derniere in frappe.db.sql("""select ms.name, ms.customer, max(d.actual_date) from `tabMaintenance Schedule` ms
                                                   join `tabMaintenance Schedule Detail` d on d.parent = ms.name
                                                   where ms.docstatus = 1 and d.actual_date is not null group by ms.name, ms.customer"""):
        if est_accessoire(par_ms.get(ms, (None, set()))[1]):
            continue
        if not out.get(customer) or getdate(derniere) > getdate(out[customer]):
            out[customer] = derniere
    return out


def doublon_sans_histoire_propre(realisees_doublon, realisees_autres) -> bool:
    """Un doublon se retire s'il ne porte aucune visite réalisée, ou si chacune de ses visites réalisées l'a été par une
    commande que l'échéancier de la vraie machine porte aussi (l'achat de cartouches avait marqué les deux). Une visite
    réalisée sans commande (à la main) le protège. PURE. `realisees_*` : commandes des visites réalisées ('' = sans)."""
    r = set(realisees_doublon)
    return not r or ("" not in r and r <= set(realisees_autres))


def echeanciers_accessoires_en_double(fams=None, cache=None) -> list:
    """Les échéanciers soumis faits UNIQUEMENT de machines accessoires (porte-filtre), chez un client qui a un autre
    échéancier soumis d'une vraie famille, et sans histoire propre (`doublon_sans_histoire_propre`) : des doublons nés
    d'un entretien. → [{name, customer, autre, sales_orders}]."""
    fams = fams or RC.familles()
    par_ms = _familles_par_echeancier(fams, cache)
    par_client = {}
    for name, (customer, fs) in par_ms.items():
        par_client.setdefault(customer, []).append((name, fs))
    realisees = {}
    for parent, so in frappe.db.sql("""select parent, ifnull(custom_sales_order, '') from `tabMaintenance Schedule Detail` where actual_date is not null"""):
        realisees.setdefault(parent, []).append((so or "").strip())
    out = []
    for name, (customer, fs) in par_ms.items():
        if not est_accessoire(fs):
            continue
        autres = [n for n, f2 in par_client[customer] if n != name and f2 and not est_accessoire(f2)]
        if not autres:
            continue
        if not doublon_sans_histoire_propre(realisees.get(name, []), [so for n in autres for so in realisees.get(n, [])]):
            continue
        so = frappe.db.sql_list("select distinct ifnull(sales_order, '') from `tabMaintenance Schedule Item` where parent = %s", (name,))
        out.append({"name": name, "customer": customer, "autre": autres[0], "sales_orders": [s for s in so if s]})
    return out


def retirer_echeanciers_accessoires_en_double(fams=None) -> dict:
    """Retire ces doublons, avec une trace sur l'échéancier et sur le client : la commande qui les avait créés redevient
    libre et le rattrapage l'applique à l'échéancier de l'osmoseur.
    26/11/2026 (lot 4) : ANNULÉS, plus supprimés. Un échéancier annulé couvre d'ordinaire ses commandes
    (commandes_couvertes) ; ici la libération est voulue, donc le lien « commande » de ses lignes machine est effacé —
    la commande est nommée dans le commentaire. Ses visites réalisées, elles, sont toutes portées aussi par l'échéancier
    de la machine (doublon_sans_histoire_propre) : rien d'autre à libérer. Relancé, il ne retrouve rien (seuls les
    échéanciers soumis sont lus). Libre, la commande ne recrée pas de porte-filtre : le client est suivi
    (machines_de_la_commande)."""
    annules, erreurs = 0, 0
    for d in echeanciers_accessoires_en_double(fams):
        frappe.db.savepoint("ms_accessoire")
        try:
            annuler_echeancier(d["name"], "porte-filtre posé lors d’un entretien de la machine, aucune visite réalisée qui ne "
                                          "soit déjà portée par celle-ci ; commande(s) libérée(s) pour l’échéancier de la machine",
                               remplace_par=d["autre"], commandes=d["sales_orders"])
            frappe.db.sql("""update `tabMaintenance Schedule Item` set sales_order = null where parent = %s""", (d["name"],))
            annules += 1
            log(f"[ACCESSOIRE] {d['name']} ({d['customer']}) annulé, doublon de {d['autre']}")
        except Exception:
            frappe.db.rollback(save_point="ms_accessoire")
            erreurs += 1
            frappe.log_error(frappe.get_traceback(), f"Retrait échéancier accessoire {d['name']}")
    log(f"[ACCESSOIRE] doublons porte-filtre annulés : {annules}, erreurs : {erreurs}")
    # « supprimes » : la clé d'avant le 26/11/2026, lue par le patch rattraper_echeanciers_entretien.
    return {"annules": annules, "supprimes": annules, "erreurs": erreurs}


def rattraper_commandes_entretien(depuis="2025-01-01", cfg=None) -> dict:
    """Rejoue, sur les échéanciers EXISTANTS seulement (jamais de création ni de devinette), les commandes livrées depuis
    `depuis` que le cron n'a jamais appliquées : clients « Non » (jamais lus avant le 15/10/2026), commandes avec
    porte-filtre (absorbées par le doublon), commandes sorties de la fenêtre de 4 mois. Chronologique, chaque commande
    isolée, rejouable sans effet (une commande déjà appliquée est refusée par `ligne_a_marquer`)."""
    cfg, fams, cache = cfg or RC.config(), RC.familles(), {}
    commandes = commandes_a_traiter(cfg, de=depuis, a=nowdate())
    couvertes = commandes_couvertes()
    dernieres = _dernieres_visites_realisees(_familles_par_echeancier(fams, cache))
    summary = {"total": len(commandes), "deja_couvertes": 0, "historiques": 0, "decalees": 0, "sans_lien": 0, "ignorees": 0, "erreurs": 0}
    erreurs = []
    for i_sal in commandes:
        if i_sal["sales_order"] in couvertes:
            summary["deja_couvertes"] += 1
            continue
        if not commande_rattrapable(i_sal["delivery_date"], dernieres.get(i_sal["customer"])):
            summary["historiques"] += 1
            continue
        frappe.db.savepoint("ms_rattrapage")
        try:
            r = update_maintenance_schedule(i_sal, cfg, fams, cache, creer=False)
            if r == "update":
                summary["decalees"] += 1
                dernieres[i_sal["customer"]] = max(getdate(i_sal["delivery_date"]), getdate(dernieres.get(i_sal["customer"]) or i_sal["delivery_date"]))
            elif r == "no-link":
                summary["sans_lien"] += 1
            else:
                summary["ignorees"] += 1
        except Exception:
            frappe.db.rollback(save_point="ms_rattrapage")
            summary["erreurs"] += 1
            erreurs.append(i_sal["sales_order"])
            frappe.log_error(frappe.get_traceback(), f"Rattrapage échéancier — commande {i_sal['sales_order']}")
    line = ("[RATTRAPAGE] total=%(total)s deja_couvertes=%(deja_couvertes)s historiques=%(historiques)s decalees=%(decalees)s "
            "sans_lien=%(sans_lien)s ignorees=%(ignorees)s erreurs=%(erreurs)s" % summary)
    if erreurs:
        line += " (" + ", ".join(erreurs[:10]) + ")"
    log(line)
    summary["log"] = line
    return summary


def run_cron():
    return run_safely("Cron - Mise à jour échéancier maintenance", run_maintenance_planning)
