"""Machine rendue : son échéancier d'entretien est annulé à la validation du BL de retour (26/11/2026).

POURQUOI
--------
Proposition 19 de l'analyse « cartographie entretien » (25/11/2026) : un BL de retour ne retirait rien. La machine
rendue restait suivie et le client continuait d'être relancé (SMS, listes d'appel) pour une machine qu'il n'a plus.
Remesuré sur la copie de prod du 25/11/2026 : 6 BL de retour validés en 2026 rendent une machine (7 lignes) ; 5
échéanciers sont encore actifs avec 9 à 10 visites à venir chacun (MAT-MSH-2026-00889, 00928, 01042, 01059, 01259 :
49 visites, premières relances dès le 20/02/2027) ; le 6e (Ghali, deux porte-filtres) n'a jamais eu d'échéancier.

LA RÈGLE (prudente : dans le doute, on garde et on le dit)
---------------------------------------------------------
À la validation d'un BL de RETOUR (is_return = 1), pour chaque ligne qui rend une MACHINE (famille machine du réglage
« Config Relances », update_schedule.famille_machine) :
  - la commande d'origine est celle de la ligne (against_sales_order), sinon celle de la ligne du BL rendu (dn_detail),
    sinon l'unique commande qui a livré cet article sur le BL rendu (return_against) — quantites() compte le rendu
    avec les mêmes trois règles (relecture du 26/11/2026 : le 3e repli trouvait l'échéancier mais ne comptait pas la
    machine rendue, l'échéancier était gardé avec « 0 rendue(s) sur 1 ») ;
  - l'échéancier visé est l'échéancier VALIDÉ dont une ligne machine porte cette commande et cet article ;
  - il est ANNULÉ (jamais supprimé : l'historique des visites reste) seulement si TOUTE la quantité de chacune de ses
    machines est rendue (livré par les BL validés de la commande ≤ rendu par ses BL de retour validés) — une machine de
    l'échéancier sans commande, ou pas entièrement rendue, est supposée encore chez le client ;
  - sinon un commentaire seulement sur l'échéancier (combien rendu sur combien livré) ;
  - un retour d'ÉCHANGE (custom_echange_de, retour_echange) n'est pas un rendu : la pièce est remplacée, commentaire
    seulement ;
  - garde-fou : si la commande est encore une candidate du passage de nuit (livrée, dans la fenêtre, client suivi) et
    que la nuit ne compte pas un échéancier annulé comme couverture (update_schedule.commandes_couvertes), elle
    recréerait aussitôt un échéancier neuf pour la machine rendue : on garde l'échéancier, avec un commentaire.
    Mesuré le 26/11/2026 sur la copie : après le retour complet d'un osmoseur (SAL-ORD-2026-04731), la commande retombe
    « Livrée en partie » (ERPNext baisse la quantité livrée) et les 5 commandes rendues de 2026 sont « Fermées » — la
    nuit ne les relit pas. Le garde-fou couvre une commande remise à 100 % (à la main, per_delivered_montant) ; il ne
    sert plus du tout depuis que la nuit compte les échéanciers annulés (lot 4 du 26/11/2026) ;
  - les listes d'appels BROUILLON qui citent l'échéancier annulé sont dégagées (annulation_commande.
    degager_des_listes_brouillon) : sans cela la liste entière ne s'enregistrait plus (lien vers une pièce annulée).
Rien ne bloque jamais la validation du BL : chaque échéancier est traité sous savepoint, toute erreur est journalisée.

LA MACHINE REVIENT (relecture du 26/11/2026)
-------------------------------------------
Un échéancier annulé couvre sa commande pour la nuit (lot 4) : sans rien de plus, une machine revenue chez le client
restait sans suivi pour toujours (mesuré sur la copie : retour complet de SAL-ORD-2026-04731 puis remplacement livré
sur la MÊME commande, ce que propose ERPNext, ou retour annulé — dans les deux cas la commande est candidate de la nuit,
couverte, sans échéancier validé). Désormais, quand la machine revient :
  - BL NORMAL validé sur la même commande et le même article (remplacement) ;
  - BL de RETOUR annulé (saisi par erreur, refait) — sauf pendant la cascade d'annulation de la commande entière ;
l'échéancier annulé par « ↩️ Machine rendue » est RÉTABLI par un amendement (copie à l'identique : mêmes machines,
mêmes visites et relances, amended_from), si une de ses machines est de nouveau chez le client (livré > rendu) et
qu'aucun échéancier validé ne la suit déjà. Commentaire sur l'ancien, le nouveau et le client. Le validate d'ERPNext
n'est pas rejoué sur la copie (ignore_validate) : il RÉGÉNÈRE le calendrier dès que le nombre de visites ne colle plus
à no_of_visits (777 échéanciers validés sur 3 263 dans ce cas sur la copie, après décalages et prolongations), et
l'historique serait perdu ; la règle « une commande, un échéancier validé » (validate_sales_order) est appliquée à la
main, comme la nuit (_nouvel_echeancier : la ligne perd son lien de commande si un autre échéancier validé la porte).

Rattrapage des retours déjà validés : patch `echeanciers_machines_rendues` (mêmes règles, rejouable).
"""
from __future__ import annotations

import frappe
from frappe.utils import add_months, cint, flt, formatdate, getdate, nowdate

from customization_app import annulation_commande as A
from customization_app import relances_config as RC
from customization_app.Maintenance import update_schedule as U

TRACE = "↩️ Machine rendue"
TRACE_REVENUE = "↩️ Machine revenue"
CHAMP_ECHANGE = "custom_echange_de"     # sur un BL retour : le BL d'échange dont il est la reprise (retour_echange)
MOTIF_ANNULE = "%" + TRACE + " — BL de retour %Échéancier annulé%"   # le commentaire posé par traiter_retour


# ── Règles pures ─────────────────────────────────────────────────────────────

def _q(x) -> str:
    """Une quantité lisible : 1 et non 1.0. PURE."""
    x = flt(x)
    return str(int(x)) if x == int(x) else str(x)


def toute_rendue(livre, rendu) -> bool:
    """Toute la quantité livrée est revenue. Rien de livré : on ne sait pas, donc non. PURE."""
    return flt(livre) > 0 and flt(rendu) >= flt(livre) - 1e-9


def encore_chez_le_client(livre, rendu) -> bool:
    """Il reste au moins une machine livrée et non rendue. Rien de livré : non (≠ « pas toute rendue »). PURE."""
    return flt(livre) - flt(rendu) > 1e-9


def decision(lignes_ms, quantites, est_machine) -> tuple:
    """Que faire de l'échéancier ? → ("annuler", "") ou ("garder", motif). PURE.
    `lignes_ms` : [(item_code, sales_order)] de sa table Articles ; `quantites` : {(sales_order, item_code): (livré,
    rendu)} ; `est_machine(item_code)` : vrai pour un article d'une famille machine. Les lignes non machines ne comptent
    pas ; une machine sans commande, ou pas entièrement rendue, est supposée encore chez le client."""
    machines = [(i, s) for i, s in lignes_ms if est_machine(i)]
    if not machines:
        return "garder", "aucune machine sur l'échéancier"
    restes = []
    for item, so in machines:
        if not so:
            restes.append("%s (sans commande)" % item)
            continue
        livre, rendu = quantites.get((so, item), (0, 0))
        if not toute_rendue(livre, rendu):
            restes.append("%s : %s rendue(s) sur %s livrée(s)" % (item, _q(rendu), _q(livre)))
    if restes:
        return "garder", "encore chez le client — " + " ; ".join(restes)
    return "annuler", ""


def decision_retablir(lignes_ms, quantites, est_machine, suivies=None, commandes_ouvertes=None) -> tuple:
    """L'échéancier annulé par un retour doit-il être rétabli ? → ("retablir", [machines revenues]) ou ("laisser",
    motif). PURE.
    `suivies` : {item_code: [échéanciers VALIDÉS qui portent déjà (commande, article)]} ; `commandes_ouvertes` :
    commandes encore validées (une commande annulée ne fait rien revenir)."""
    suivies, ouvertes = suivies or {}, commandes_ouvertes
    revenues = [i for i, s in lignes_ms
                if s and est_machine(i) and (ouvertes is None or s in ouvertes)
                and encore_chez_le_client(*quantites.get((s, i), (0, 0)))]
    if not revenues:
        return "laisser", "toutes ses machines restent rendues"
    deja = sorted({n for i in revenues for n in (suivies.get(i) or [])})
    if deja:
        return "laisser", "la machine est déjà suivie par %s" % ", ".join(deja)
    return "retablir", revenues


def _intro(bl, date_bl, commande, articles) -> str:
    return "%s — BL de retour %s du %s (commande %s) : %s." % (TRACE, bl, formatdate(date_bl, "dd/MM/yyyy"),
                                                               commande or "?", ", ".join(articles))


def texte_annule(bl, date_bl, commande, articles, rattrapage=False) -> str:
    """Commentaire de l'échéancier annulé. PURE."""
    return (_intro(bl, date_bl, commande, articles) + " Échéancier annulé : plus de relance, ses visites restent "
            "consultables." + (" (rattrapage du 26/11/2026)" if rattrapage else ""))


def texte_garde(bl, date_bl, commande, articles, motif, rattrapage=False) -> str:
    """Commentaire de l'échéancier gardé. PURE."""
    return (_intro(bl, date_bl, commande, articles) + " Échéancier gardé : %s." % motif
            + (" (rattrapage du 26/11/2026)" if rattrapage else ""))


def texte_client(bl, echeancier, articles) -> str:
    """Commentaire de la fiche client quand l'échéancier est annulé. PURE."""
    return ("%s (%s, BL de retour %s) : l'échéancier %s est annulé, le client n'est plus relancé pour cette machine."
            % (TRACE, ", ".join(articles), bl, echeancier))


def texte_retabli(raison, ancien, nouveau, articles, sans_lien=()) -> str:
    """Commentaire posé sur l'ancien échéancier, le nouveau et le client quand la machine revient. PURE."""
    t = ("%s — %s : %s de nouveau chez le client. L'échéancier %s, annulé à son retour, est rétabli : %s (mêmes "
         "machines, mêmes visites et relances)." % (TRACE_REVENUE, raison, ", ".join(articles), ancien, nouveau))
    if sans_lien:
        t += (" Commande(s) %s déjà portée(s) par un autre échéancier validé : la ligne machine est rétablie sans ce "
              "lien (règle d'ERPNext, comme la nuit)." % ", ".join(sans_lien))
    return t


def texte_retour_annule(bl, echeancier, motif) -> str:
    """Commentaire posé quand le retour qui avait annulé l'échéancier est annulé SANS que l'échéancier soit rétabli.
    PURE."""
    return ("%s — le BL de retour %s est annulé ; l'échéancier %s, annulé à ce retour, n'est pas rétabli : %s."
            % (TRACE, bl, echeancier, motif))


MOTIF_ECHANGE = "c'est la reprise d'un échange (%s), la pièce est remplacée — un échange n'est pas un rendu"
MOTIF_NUIT = ("la commande %s compte encore comme livrée et le passage de nuit recréerait aussitôt un échéancier neuf "
              "pour cette machine")


def dans_la_fenetre(date_livraison, cfg, aujourd_hui) -> bool:
    """La commande est-elle dans la fenêtre du passage de nuit (commandes_a_traiter) ? PURE."""
    if not date_livraison:
        return False
    d = getdate(date_livraison)
    return getdate(add_months(aujourd_hui, -cint(cfg.get("fenetre_mois_avant") or 0))) <= d \
        <= getdate(add_months(aujourd_hui, cint(cfg.get("fenetre_mois_apres") or 0)))


# ── Lecture de la base ───────────────────────────────────────────────────────

def _a_colonne_echange() -> bool:
    return bool(frappe.db.has_column("Delivery Note", CHAMP_ECHANGE))


def commande_de_la_ligne(ligne, return_against=None) -> str | None:
    """La commande d'origine d'une ligne de retour : la sienne, celle de la ligne rendue, sinon l'unique commande qui a
    livré cet article sur le BL rendu. Mêmes trois règles que le rendu de quantites()."""
    so = ligne.get("against_sales_order")
    if so:
        return so
    if ligne.get("dn_detail"):
        so = frappe.db.get_value("Delivery Note Item", ligne.get("dn_detail"), "against_sales_order")
        if so:
            return so
    if return_against:
        sos = frappe.db.sql_list("""select distinct against_sales_order from `tabDelivery Note Item`
                                    where parent = %s and item_code = %s and ifnull(against_sales_order, '') <> ''""",
                                 (return_against, ligne.get("item_code")))
        if len(sos) == 1:
            return sos[0]
    return None


def quantites(commande, article) -> tuple:
    """(livré, rendu) de cet article pour cette commande, sur les BL VALIDÉS. Le rendu compte les retours rattachés à la
    commande par leur ligne, par la ligne rendue (dn_detail), sinon par le BL rendu (return_against) quand il n'a livré
    cet article que pour cette commande — les trois règles de commande_de_la_ligne (relecture du 26/11/2026 : sans la
    3e, un retour saisi avec seulement return_against trouvait l'échéancier sans compter la machine rendue). Les
    reprises d'échange n'en sont pas."""
    livre = frappe.db.sql("""select ifnull(sum(dni.qty), 0) from `tabDelivery Note Item` dni
                             join `tabDelivery Note` dn on dn.name = dni.parent
                             where dn.docstatus = 1 and ifnull(dn.is_return, 0) = 0
                               and dni.against_sales_order = %s and dni.item_code = %s""", (commande, article))[0][0]
    sans_echange = (" and ifnull(dn.`%s`, '') = ''" % CHAMP_ECHANGE) if _a_colonne_echange() else ""
    rendu = frappe.db.sql("""select ifnull(sum(-r.qty), 0) from `tabDelivery Note Item` r
                             join `tabDelivery Note` dn on dn.name = r.parent
                             left join `tabDelivery Note Item` o on o.name = r.dn_detail
                             where dn.docstatus = 1 and dn.is_return = 1 and r.item_code = %(a)s
                               and (r.against_sales_order = %(c)s
                                    or (ifnull(r.against_sales_order, '') = '' and o.against_sales_order = %(c)s)
                                    or (ifnull(r.against_sales_order, '') = '' and ifnull(o.against_sales_order, '') = ''
                                        and (select count(distinct x.against_sales_order) from `tabDelivery Note Item` x
                                             where x.parent = dn.return_against and x.item_code = r.item_code
                                               and ifnull(x.against_sales_order, '') <> '') = 1
                                        and (select max(x.against_sales_order) from `tabDelivery Note Item` x
                                             where x.parent = dn.return_against and x.item_code = r.item_code) = %(c)s))"""
                          + sans_echange, {"a": article, "c": commande})[0][0]
    return flt(livre), flt(rendu)


def echeanciers_de(commande, article, docstatus=1) -> list:
    return frappe.db.sql_list("""select distinct ms.name from `tabMaintenance Schedule` ms
                                 join `tabMaintenance Schedule Item` msi on msi.parent = ms.name
                                 where msi.sales_order = %s and msi.item_code = %s and ms.docstatus = %s
                                 order by ms.name""", (commande, article, docstatus))


def _deja_commente(echeancier, bl) -> bool:
    return bool(frappe.db.sql("""select name from `tabComment` where reference_doctype = 'Maintenance Schedule'
                                 and reference_name = %s and comment_type = 'Comment' and content like %s limit 1""",
                              (echeancier, "%" + TRACE + "%" + bl + "%")))


def la_nuit_le_recreerait(commande, cfg=None) -> bool:
    """Après l'annulation (dans la même transaction) : la commande serait-elle reprise cette nuit, faute d'échéancier
    validé qui la couvre ? Même lecture que le passage de nuit (commandes_a_traiter + commandes_couvertes)."""
    cfg = cfg or RC.config()
    so = frappe.db.get_value("Sales Order", commande, ["docstatus", "delivery_date"], as_dict=True)
    if not so or so.docstatus != 1 or not dans_la_fenetre(so.delivery_date, cfg, nowdate()):
        return False
    # Les filtres de clients (B2B, intéressé ou déjà suivi) sont ceux de la nuit elle-même : commandes_a_traiter fait foi.
    candidates = {c["sales_order"] for c in U.commandes_a_traiter(cfg, de=so.delivery_date, a=so.delivery_date)}
    return commande in candidates and commande not in U.commandes_couvertes()


def lignes_machines(dn, fams=None, cache=None) -> list:
    """[{item_code, sales_order, qty}] des lignes du retour qui rendent une machine (quantité négative)."""
    fams = fams or RC.familles()
    cache = cache if cache is not None else {}
    out = []
    for it in dn.get("items") or []:
        if flt(it.get("qty")) >= 0:
            continue
        if not U.famille_machine(U._item(it.get("item_code"), cache), fams):
            continue
        out.append({"item_code": it.get("item_code"), "qty": -flt(it.get("qty")),
                    "sales_order": commande_de_la_ligne(it, dn.get("return_against"))})
    return out


def echeanciers_rendus_de(paires) -> list:
    """Les échéanciers ANNULÉS par « ↩️ Machine rendue » (commentaire de traiter_retour) dont une ligne porte une de ces
    (commande, article), et pas encore amendés (un amendement, même annulé ensuite, prend la suite)."""
    paires = {(s, i) for s, i in paires if s and i}
    if not paires:
        return []
    lignes = frappe.db.sql("""select distinct ms.name, msi.sales_order, msi.item_code from `tabMaintenance Schedule` ms
                              join `tabMaintenance Schedule Item` msi on msi.parent = ms.name
                              where ms.docstatus = 2 and msi.sales_order in %s""",
                           (tuple(sorted({s for s, _i in paires})),), as_dict=True)
    noms = sorted({l.name for l in lignes if (l.sales_order, l.item_code) in paires})
    return [n for n in noms
            if not frappe.db.exists("Maintenance Schedule", {"amended_from": n})
            and frappe.db.sql("""select 1 from `tabComment` where reference_doctype = 'Maintenance Schedule'
                                 and reference_name = %s and comment_type = 'Comment' and content like %s limit 1""",
                              (n, MOTIF_ANNULE))]


# ── Le geste ─────────────────────────────────────────────────────────────────

def traiter_retour(dn, rattrapage=False, cfg=None, fams=None, cache=None) -> dict:
    """Applique la règle au BL de retour `dn` (document validé). → compte rendu {bl, annules, gardes, deja_annules,
    sans_echeancier, listes, erreurs}. Chaque échéancier est isolé (savepoint) ; une erreur est journalisée, jamais
    levée."""
    cfg, fams = cfg or RC.config(), fams or RC.familles()
    cache = cache if cache is not None else {}
    cr = {"bl": dn.name, "client": dn.customer, "annules": [], "gardes": [], "deja_annules": [], "sans_echeancier": [],
          "listes": [], "erreurs": 0}
    if not cint(dn.get("is_return")) or dn.docstatus != 1:
        return cr
    lignes = lignes_machines(dn, fams, cache)
    if not lignes:
        return cr
    echange = dn.get(CHAMP_ECHANGE) if _a_colonne_echange() else None

    def est_machine(code):
        return bool(U.famille_machine(U._item(code, cache), fams))

    # Les échéanciers visés, chacun avec les articles rendus par CE retour.
    par_ms, commande_de = {}, {}
    for l in lignes:
        if not l["sales_order"]:
            cr["sans_echeancier"].append((l["item_code"], None))
            continue
        noms = echeanciers_de(l["sales_order"], l["item_code"])
        if not noms:
            deja = echeanciers_de(l["sales_order"], l["item_code"], docstatus=2)
            if deja:
                cr["deja_annules"].extend(n for n in deja if n not in cr["deja_annules"])
            else:
                cr["sans_echeancier"].append((l["item_code"], l["sales_order"]))
            continue
        for n in noms:
            par_ms.setdefault(n, [])
            if l["item_code"] not in par_ms[n]:
                par_ms[n].append(l["item_code"])
            commande_de.setdefault(n, l["sales_order"])

    for nom, articles in sorted(par_ms.items()):
        frappe.db.savepoint("retour_machine")
        try:
            ms = frappe.get_doc("Maintenance Schedule", nom)
            commande = commande_de[nom]
            if echange:
                action, motif = "garder", MOTIF_ECHANGE % echange
            else:
                lignes_ms = [(i.item_code, i.sales_order) for i in ms.items]
                q = {(s, i): quantites(s, i) for i, s in lignes_ms if s and est_machine(i)}
                action, motif = decision(lignes_ms, q, est_machine)
            if action == "annuler":
                ms.flags.ignore_permissions = True
                ms.flags.ignore_links = True      # une ligne de liste d'appel qui le cite ne doit pas bloquer
                ms.cancel()
                if la_nuit_le_recreerait(commande, cfg):
                    # Annulation défaite (savepoint) : un échéancier neuf la remplacerait dès cette nuit.
                    frappe.db.rollback(save_point="retour_machine")
                    ms = frappe.get_doc("Maintenance Schedule", nom)
                    action, motif = "garder", MOTIF_NUIT % commande
            if action == "annuler":
                ms.add_comment("Comment", texte_annule(dn.name, dn.posting_date, commande, articles, rattrapage))
                if dn.customer and frappe.db.exists("Customer", dn.customer):
                    frappe.get_doc("Customer", dn.customer).add_comment(
                        "Comment", texte_client(dn.name, nom, articles))
                # Les listes d'appels brouillon qui le citent ne s'enregistreraient plus (lien vers une pièce annulée).
                cr["listes"] += A.degager_des_listes_brouillon(
                    nom, "%s (BL de retour %s, %s)" % (TRACE, dn.name, ", ".join(articles)))
                cr["annules"].append(nom)
            else:
                if not _deja_commente(nom, dn.name):
                    ms.add_comment("Comment", texte_garde(dn.name, dn.posting_date, commande, articles, motif, rattrapage))
                cr["gardes"].append((nom, motif))
        except Exception:
            frappe.db.rollback(save_point="retour_machine")
            cr["erreurs"] += 1
            frappe.log_error(title="Machine rendue — BL %s, échéancier %s" % (dn.name, nom),
                             message=frappe.get_traceback())
    return cr


def _amender(ms):
    """Le rétablissement : une copie À L'IDENTIQUE de l'échéancier annulé, validée (amended_from = l'ancien). → (nouvel
    échéancier, [commandes dont le lien est retiré]).
    - ignore_validate : le validate d'ERPNext régénère le calendrier (toutes les visites recalculées, historique perdu)
      dès que len(schedules) ≠ Σ no_of_visits — 777 des 3 263 échéanciers validés de la copie du 25/11/2026 ; et sa
      règle validate_sales_order est appliquée ici, comme la nuit (_nouvel_echeancier) ;
    - ignore_links : la copie reprend des liens qui ont pu être annulés ou supprimés depuis (visite soldée par une
      commande annulée) ;
    - les lignes machines gardent leurs liens avec le calendrier (item_reference, lu par on_submit pour l'agenda)."""
    new = frappe.copy_doc(ms)
    new.docstatus = 0
    new.amended_from = ms.name
    new.status = "Draft"
    # Frappe renomme TOUTES les lignes à l'insertion : la ligne machine de chaque visite est retenue par sa position,
    # et item_reference est recâblé après insert(), avant submit() (qui l'écrit, puis le lit pour l'agenda).
    anciens = [it.name for it in ms.items]
    position = [anciens.index(v.item_reference) if v.item_reference in anciens else None for v in new.schedules]
    sans_lien = []
    for ligne in new.items:
        if ligne.sales_order and U._commande_deja_portee(ligne.sales_order):
            sans_lien.append(ligne.sales_order)
            ligne.sales_order = None
    new.flags.ignore_permissions = True
    new.flags.ignore_validate = True
    new.flags.ignore_links = True
    new.insert()
    for v, p in zip(new.schedules, position):
        if p is not None:
            v.item_reference = new.items[p].name
    new.submit()
    return new, sans_lien


def retablir(paires, raison, client=None, fams=None, cache=None) -> dict:
    """La machine revient (remplacement livré sur la même commande, retour annulé) : chaque échéancier annulé par
    « ↩️ Machine rendue » qui porte une de ces (commande, article) est rétabli par un amendement si decision_retablir le
    dit. Chaque échéancier est isolé (savepoint) ; une erreur est journalisée, jamais levée.
    → {retablis: [(ancien, nouveau)], laisses: [(ancien, motif)], erreurs}."""
    fams = fams or RC.familles()
    cache = cache if cache is not None else {}
    cr = {"retablis": [], "laisses": [], "erreurs": 0}
    candidats = echeanciers_rendus_de(paires)
    if not candidats:
        return cr

    def est_machine(code):
        return bool(U.famille_machine(U._item(code, cache), fams))

    for nom in candidats:
        frappe.db.savepoint("retour_machine_revenue")
        try:
            ms = frappe.get_doc("Maintenance Schedule", nom)
            lignes_ms = [(i.item_code, i.sales_order) for i in ms.items]
            machines = [(i, s) for i, s in lignes_ms if s and est_machine(i)]
            q = {(s, i): quantites(s, i) for i, s in machines}
            suivies = {i: echeanciers_de(s, i, 1) for i, s in machines}
            ouvertes = {s for _i, s in machines if frappe.db.get_value("Sales Order", s, "docstatus") == 1}
            action, quoi = decision_retablir(lignes_ms, q, est_machine, suivies, ouvertes)
            if action != "retablir":
                cr["laisses"].append((nom, quoi))
                continue
            new, sans_lien = _amender(ms)
            texte = texte_retabli(raison, nom, new.name, quoi, sans_lien)
            ms.add_comment("Comment", texte)
            new.add_comment("Comment", texte)
            cli = ms.customer or client
            if cli and frappe.db.exists("Customer", cli):
                frappe.get_doc("Customer", cli).add_comment("Comment", texte)
            cr["retablis"].append((nom, new.name))
        except Exception:
            frappe.db.rollback(save_point="retour_machine_revenue")
            cr["erreurs"] += 1
            frappe.log_error(title="Machine revenue — échéancier %s" % nom, message=frappe.get_traceback())
    return cr


def paires_livrees(dn) -> set:
    """{(commande, article)} des lignes LIVRÉES (quantité positive) d'un BL normal rattachées à une commande. PURE."""
    return {(it.get("against_sales_order"), it.get("item_code")) for it in dn.get("items") or []
            if flt(it.get("qty")) > 0 and it.get("against_sales_order") and it.get("item_code")}


def _noter(compteurs: dict):
    """Compteurs du jour pour l'entonnoir des relances (contrat partagé du 25/11/2026). Ne lève jamais."""
    try:
        from customization_app.entonnoir_relances import noter
    except ImportError:
        return
    try:
        noter("retours_machines", compteurs)
    except Exception:
        pass


def _compteurs_retour(cr: dict) -> dict:
    return {"bl_retour_machine": 1, "echeanciers_annules": len(cr["annules"]), "echeanciers_gardes": len(cr["gardes"]),
            "machines_sans_echeancier": len(cr["sans_echeancier"]),
            "lignes_listes_brouillon": len(cr.get("listes") or []), "erreurs": cr["erreurs"]}


def on_submit_delivery_note(doc, method=None):
    """Hook « on_submit » du BL (à placer en DERNIER : après per_delivered_montant, qui fixe le statut de livraison de la
    commande que lit le garde-fou de nuit). Un RETOUR : la machine rendue n'est plus suivie (traiter_retour). Un BL
    NORMAL : une machine rendue qui revient sur la même commande retrouve son échéancier (retablir) — une seule requête
    quand la commande n'a aucun échéancier annulé, le cas de toutes les ventes. Ne bloque jamais la validation."""
    retour = cint(doc.get("is_return"))
    paires = set() if retour else paires_livrees(doc)
    if not retour and not paires:
        return
    try:
        frappe.db.savepoint("retours_machines")
    except Exception:
        return
    try:
        if retour:
            cr = traiter_retour(doc)
            if cr["annules"] or cr["gardes"] or cr["sans_echeancier"] or cr["deja_annules"] or cr["erreurs"]:
                _noter(_compteurs_retour(cr))
            if cr["annules"]:
                frappe.msgprint("↩️ Machine rendue : échéancier %s annulé, le client n'est plus relancé pour cette "
                                "machine." % ", ".join(cr["annules"]), indicator="orange", alert=True)
        else:
            raison = "BL %s du %s (livraison sur la commande %s)" % (
                doc.name, formatdate(doc.posting_date, "dd/MM/yyyy"), ", ".join(sorted({s for s, _i in paires})))
            cr = retablir(paires, raison, client=doc.get("customer"))
            if cr["retablis"] or cr["erreurs"]:
                _noter({"echeanciers_retablis": len(cr["retablis"]), "erreurs": cr["erreurs"]})
            if cr["retablis"]:
                frappe.msgprint("↩️ Machine revenue : échéancier %s rétabli, le suivi reprend."
                                % ", ".join("%s → %s" % p for p in cr["retablis"]), indicator="green", alert=True)
    except Exception:
        frappe.db.rollback(save_point="retours_machines")
        frappe.log_error(title="Machine rendue — BL %s" % doc.name, message=frappe.get_traceback())


def echeanciers_annules_par(bl) -> list:
    """Les échéanciers encore annulés que ce BL de retour a annulés (lus dans leur commentaire)."""
    return frappe.db.sql_list("""select distinct c.reference_name from `tabComment` c
                                 join `tabMaintenance Schedule` ms on ms.name = c.reference_name
                                 where c.reference_doctype = 'Maintenance Schedule' and c.comment_type = 'Comment'
                                   and c.content like %s and ms.docstatus = 2 order by c.reference_name""",
                              ("%" + TRACE + " — BL de retour " + bl + " %Échéancier annulé%",))


def on_cancel_delivery_note(doc, method=None):
    """Hook « on_cancel » du BL : un retour annulé (saisi par erreur, refait) remet la machine chez le client — son
    échéancier, annulé à ce retour, est RÉTABLI par un amendement (retablir). Depuis que la nuit compte un échéancier
    annulé comme couverture, personne d'autre ne le recréerait. Quand il n'est pas rétabli (machine encore rendue par un
    autre retour, déjà suivie ailleurs), on le DIT sur l'échéancier, sur le client et à la personne qui annule.
    Rien pendant la cascade d'annulation d'une commande (annulation_commande) : c'est la commande entière qui part, et
    « recréez son échéancier » serait faux. Ne bloque jamais l'annulation."""
    if A.annulation_commande_en_cours():
        return
    if not cint(doc.get("is_return")):
        _defaire_retablissements(doc)
        return
    try:
        frappe.db.savepoint("retours_machines_annulation")
    except Exception:
        return
    try:
        cache = {}
        paires = {(l["sales_order"], l["item_code"]) for l in lignes_machines(doc, cache=cache) if l["sales_order"]}
        cr = retablir(paires, "le BL de retour %s est annulé" % doc.name, client=doc.get("customer"), cache=cache)
        retablis = {a for a, _n in cr["retablis"]}
        laisses = dict(cr["laisses"])
        # déjà amendé (rétabli plus tôt, par un remplacement livré) : rien à dire
        restent = [n for n in echeanciers_annules_par(doc.name)
                   if n not in retablis and not frappe.db.exists("Maintenance Schedule", {"amended_from": n})]
        for nom in restent:
            texte = texte_retour_annule(doc.name, nom, laisses.get(nom) or "voir son historique")
            frappe.get_doc("Maintenance Schedule", nom).add_comment("Comment", texte)
            if doc.customer and frappe.db.exists("Customer", doc.customer):
                frappe.get_doc("Customer", doc.customer).add_comment("Comment", texte)
        if cr["retablis"] or cr["erreurs"]:
            _noter({"echeanciers_retablis": len(cr["retablis"]), "erreurs": cr["erreurs"]})
        if cr["retablis"]:
            frappe.msgprint("↩️ Ce retour avait annulé l'échéancier %s : il est rétabli, le suivi reprend."
                            % ", ".join("%s (→ %s)" % p for p in cr["retablis"]), indicator="green")
        if restent:
            frappe.msgprint("↩️ Ce retour avait annulé l'échéancier %s : il n'est pas rétabli (%s)."
                            % (", ".join(restent), " ; ".join(laisses.get(n) or "voir son historique" for n in restent)),
                            indicator="orange")
    except Exception:
        frappe.db.rollback(save_point="retours_machines_annulation")
        frappe.log_error(title="Machine rendue — annulation du BL %s" % doc.name, message=frappe.get_traceback())


def texte_retablissement_defait(bl, echeancier) -> str:
    """Commentaire quand le BL de remplacement qui avait rétabli l'échéancier est annulé. PURE."""
    return ("%s — le BL %s, qui avait rétabli cet échéancier (%s), est annulé : la machine n'est plus chez le client. "
            "Échéancier annulé de nouveau, plus de relance pour cette machine." % (TRACE, bl, echeancier))


def _defaire_retablissements(dn):
    """Relecture finale du 26/11/2026 : un BL NORMAL qui avait rétabli un échéancier (machine revenue) est annulé — mauvais
    BL, machine finalement pas livrée. Sans ceci, l'échéancier rétabli restait validé et ses visites échues pendant
    l'absence de la machine repartaient en SMS et en listes d'appel. Chaque échéancier rétabli PAR CE BL (commentaire
    « Machine revenue — BL <ce BL> ») est relu : si la machine n'est plus chez le client (decision), il est annulé avec
    un commentaire. Isolé, ne bloque jamais l'annulation."""
    try:
        frappe.db.savepoint("retours_machines_defaire")
    except Exception:
        return
    try:
        noms = frappe.db.sql_list("""select distinct c.reference_name from tabComment c
                                     join `tabMaintenance Schedule` ms on ms.name = c.reference_name and ms.docstatus = 1
                                     where c.reference_doctype = 'Maintenance Schedule' and c.comment_type = 'Comment'
                                       and c.content like %s""", ("%" + TRACE_REVENUE + " — BL " + dn.name + " du%",))
        if not noms:
            return
        fams, cache = RC.familles(), {}

        def est_machine(code):
            return bool(U.famille_machine(U._item(code, cache), fams))

        annules = []
        for nom in noms:
            ms = frappe.get_doc("Maintenance Schedule", nom)
            lignes_ms = [(i.item_code, i.sales_order) for i in ms.items]
            q = {(s, i): quantites(s, i) for i, s in lignes_ms if s and est_machine(i)}
            action, _motif = decision(lignes_ms, q, est_machine)
            if action != "annuler":
                continue
            ms.flags.ignore_links = ms.flags.ignore_permissions = True
            ms.cancel()
            texte = texte_retablissement_defait(dn.name, nom)
            ms.add_comment("Comment", texte)
            if ms.customer and frappe.db.exists("Customer", ms.customer):
                frappe.get_doc("Customer", ms.customer).add_comment("Comment", texte)
            A.degager_des_listes_brouillon(nom, TRACE + " : BL de remplacement %s annulé" % dn.name)
            annules.append(nom)
        if annules:
            _noter({"echeanciers_annules": len(annules)})
            frappe.msgprint("↩️ Ce BL avait rétabli l'échéancier %s : la machine n'étant plus chez le client, il est annulé "
                            "de nouveau." % ", ".join(annules), indicator="orange")
    except Exception:
        frappe.db.rollback(save_point="retours_machines_defaire")
        frappe.log_error(title="Machine revenue — annulation du BL %s" % dn.name, message=frappe.get_traceback())


# ── Rattrapage ───────────────────────────────────────────────────────────────

def retours_machines_valides(depuis) -> list:
    """Les BL de retour VALIDÉS depuis `depuis` dont une ligne rend un article d'un groupe machine du réglage."""
    groupes = tuple(sorted({g for f in RC.familles() for g in f["groupes_machines"]})) or ("",)
    return frappe.db.sql_list("""select distinct dn.name from `tabDelivery Note` dn
                                 join `tabDelivery Note Item` dni on dni.parent = dn.name
                                 join `tabItem` i on i.name = dni.item_code
                                 where dn.docstatus = 1 and dn.is_return = 1 and dn.posting_date >= %s
                                   and dni.qty < 0 and i.item_group in %s
                                 order by dn.posting_date, dn.name""", (depuis, groupes))


def rattraper(depuis="2026-01-01") -> dict:
    """Rejoue la règle sur les retours déjà validés (rejouable : un échéancier déjà annulé est compté, pas retouché ; un
    commentaire « gardé » n'est jamais posé deux fois). → {bl, annules, gardes, deja_annules, sans_echeancier, listes,
    erreurs, detail}."""
    cfg, fams, cache = RC.config(), RC.familles(), {}
    total = {"bl": 0, "annules": [], "gardes": [], "deja_annules": [], "sans_echeancier": [], "listes": [], "erreurs": 0,
             "detail": []}
    for nom in retours_machines_valides(depuis):
        try:
            cr = traiter_retour(frappe.get_doc("Delivery Note", nom), rattrapage=True, cfg=cfg, fams=fams, cache=cache)
        except Exception:
            total["erreurs"] += 1
            frappe.log_error(title="Machine rendue (rattrapage) — BL %s" % nom, message=frappe.get_traceback())
            continue
        total["bl"] += 1
        for k in ("annules", "gardes", "deja_annules", "sans_echeancier", "listes"):
            total[k].extend(cr[k])
        total["erreurs"] += cr["erreurs"]
        total["detail"].append(cr)
    return total
