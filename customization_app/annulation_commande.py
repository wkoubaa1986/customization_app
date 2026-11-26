"""Annulation d'une commande client : cascade BL + échéancier + paiements, suppression forcée.

LE GESTE MÉTIER
---------------
Une commande annulée traîne souvent derrière elle un BL, un échéancier
d'entretien, des paiements et des références dans le calendrier d'autres
échéanciers. Jusqu'au 26/11/2026, deux mécanismes s'en partageaient le
travail : ce module (hook Python « before_cancel », qui passe AVANT les Server
Scripts dans Document.run_method) et le Server Script « cancel sales order
payment » (Before Cancel, jamais versionné, modifié le 16/05/2025). Le script
est repris ici (paiements et écritures, à l'identique et dans le même ordre)
et éteint par le patch `script_annulation_commande_dans_l_app` ; ses deux
autres boucles (BL, échéanciers) ne trouvaient déjà plus rien, ce module
étant passé avant.

CE QUI EST FAIT À L'ANNULATION (before_cancel), DANS CET ORDRE
-------------------------------------------------------------
  1. BL liés (brouillons ou validés) : annulés puis SUPPRIMÉS. L'annulation
     fait le reposting standard (SLE/GL inversés : le stock revient dans le
     magasin d'origine) ;
     magasin désactivé sur le BL : réactivé le temps de l'annulation (la
     validation SLE refuse un magasin désactivé), le stock repris est ensuite
     TRANSFÉRÉ vers le magasin par défaut (Stock Settings) par une entrée de
     stock, puis le magasin est re-désactivé ;
     ORDRE (26/11/2026, relecture) : les BL de RETOUR d'abord, puis les plus
     récents. ERPNext refuse d'annuler un BL tant qu'un retour validé le cite
     (return_against) : une commande dont la machine avait été rendue ne
     s'annulait plus (mesuré : SAL-ORD-2026-04731 après le retour de son
     osmoseur, « Delivery Note … is linked with Delivery Note … »). Une reprise
     d'ÉCHANGE (custom_echange_de) n'est pas annulée la première : elle
     s'annule avec son échange (retour_echange.before_cancel_bl refuse sinon) ;
  2. échéanciers dont la table Articles référence la commande : ANNULÉS, avec
     un commentaire (commande, date, qui) — plus JAMAIS supprimés (26/11/2026,
     proposition 7 de l'analyse « cartographie entretien ») : la suppression
     effaçait l'historique des visites et des relances (mesuré sur la copie
     du 25/11/2026 : 62 échéanciers supprimés ainsi en 2026, dont 18 visites
     réalisées, relancées ou appelées), et les relances s'arrêtent tout autant
     sur un échéancier annulé (SMS, listes d'appel et passage de nuit ne
     lisent que les échéanciers validés). Un brouillon reste tel quel, avec
     le commentaire. La fiche CLIENT reçoit aussi un commentaire qui nomme les
     échéanciers annulés (la page Historique ne lit que les échéanciers
     validés). Une liste d'appels BROUILLON qui cite l'échéancier ne
     s'enregistrerait plus (Frappe refuse un lien vers une pièce annulée) :
     sa ligne est rattachée à un autre échéancier du client, sinon retirée
     (un appel déjà saisi est recopié sur la fiche client) —
     degager_des_listes_brouillon ;
  3. lignes du CALENDRIER (Maintenance Schedule Detail) d'autres échéanciers
     non annulés portant la commande dans custom_sales_order : la référence est
     retirée et la ligne repasse « En Attente » (completion_status = Pending,
     date réelle effacée). Les lignes d'un échéancier ANNULÉ gardent la leur :
     c'est son historique ;
  4. reprise du Server Script : Payment Entry VALIDÉS de la commande annulés
     puis supprimés, puis Journal Entry liés (brouillons, validés, annulés)
     annulés s'il le faut puis supprimés. Les droits de l'utilisateur
     s'appliquent à ces annulations, exactement comme avec le script : son
     « frappe.flags.ignore_permissions = True » ne levait rien (dans un Server
     Script, frappe.flags est un dictionnaire du bac à sable).

À LA SUPPRESSION (on_trash, commandes ANNULÉES uniquement)
----------------------------------------------------------
Une commande annulée doit pouvoir être supprimée même si d'autres pièces la
référencent encore (Tâche de travail, brouillons de paiement…). on_trash passe
AVANT le contrôle des liens de delete_doc : on retire ici toute référence
encore bloquante (liens statiques et dynamiques, pièces non annulées), avec un
commentaire de traçabilité sur chaque pièce déliée. Les brouillons de commande
gardent le comportement standard (pas de passe-droit).
Pour la suppression, Frappe compte AUSSI les pièces annulées qui portent un
lien statique (frappe.model.delete_doc.get_linked_docs, méthode « Delete ») :
l'échéancier désormais annulé à l'étape 2 bloquerait la suppression de sa
commande (727 commandes annulées puis supprimées en 2026 ; aucune commande
annulée n'est restée dans la base). Ses
références à la commande sont donc retirées à la suppression, la commande étant
nommée dans un commentaire — l'échéancier annulé reste, sans lien vers une
pièce disparue (les fantômes du 20/08/2026 étaient des échéanciers restés
VALIDÉS derrière une commande supprimée).
"""
from __future__ import annotations

import html as html_lib
import json

import frappe
from frappe import _
from frappe.utils import cint, flt, get_fullname, now_datetime

LISTE = "Liste Appelle Entretien"
LIGNE_LISTE = "Appelle Client"
CHAMP_ECHEANCIER = "échéancier_dentretien"      # sur la ligne de liste d'appels
CHAMP_ECHANGE = "custom_echange_de"             # sur un BL retour : le BL d'échange dont il est la reprise


# --------------------------------------------------------------- annulation

def before_cancel_sales_order(doc, method=None):
    """Cascade complète, dans la transaction de l'annulation : tout écart
    lève et annule l'ensemble (rollback), la commande reste intacte. L'ordre
    est celui qui tournait jusqu'au 26/11/2026 : ce module (BL, échéanciers,
    calendrier) puis le Server Script (paiements, écritures).
    Le temps de la cascade, frappe.flags.annulation_commande_en_cours porte la commande : un BL de retour annulé PAR
    la cascade (ou la reprise d'un échange qu'elle annule) ne doit pas rétablir l'échéancier de sa machine
    (retours_machines.on_cancel_delivery_note) — c'est la commande entière qui est annulée."""
    precedent = frappe.flags.get("annulation_commande_en_cours")
    frappe.flags.annulation_commande_en_cours = doc.name
    try:
        _annuler_et_supprimer_bls(doc)
        bilan = _nettoyer_echeanciers(doc)
        _supprimer_paiements_et_ecritures(doc)
    finally:
        frappe.flags.annulation_commande_en_cours = precedent
    _noter(bilan)


def annulation_commande_en_cours() -> str | None:
    """La commande dont la cascade d'annulation est en cours dans cette requête, sinon None."""
    return frappe.flags.get("annulation_commande_en_cours") or None


def _bls_de(commande: str) -> list:
    """Les BL de la commande, dans l'ordre où ils peuvent être annulés : les retours d'abord (hors reprises d'échange),
    puis du plus récent au plus ancien (26/11/2026, relecture : un retour validé bloque l'annulation du BL qu'il rend ;
    un échange plus récent que le BL d'origine de sa reprise passe aussi avant lui)."""
    reprise = ("ifnull(dn.`%s`, '') <> ''" % CHAMP_ECHANGE) if frappe.db.has_column("Delivery Note", CHAMP_ECHANGE) \
        else "0"
    return frappe.db.sql(
        """select dn.name, dn.docstatus, dn.is_return
           from `tabDelivery Note` dn
           where dn.name in (select dni.parent from `tabDelivery Note Item` dni where dni.against_sales_order = %s)
           order by (ifnull(dn.is_return, 0) = 1 and not ({0})) desc, dn.posting_date desc, dn.posting_time desc,
                    dn.creation desc, dn.name desc""".format(reprise),
        commande, as_dict=True)


def _reprises_d_autres_commandes(bls) -> list:
    """Les reprises d'échange VALIDÉES qui rendent un BL de cette commande pour le compte d'un échange passé sur une AUTRE
    commande : elles ne sont pas dans _bls_de (aucune ligne ne porte la commande) et bloquent l'annulation du BL qu'elles
    rendent (relecture finale du 26/11/2026, préexistant : SAL-ORD-2026-02255 / reprise MAT-DN-2026-05844 de l'échange
    MAT-DN-2026-05813). → [{reprise, bl, echange, commande}]."""
    if not bls or not frappe.db.has_column("Delivery Note", CHAMP_ECHANGE):
        return []
    lignes = frappe.db.sql("""select r.name as reprise, r.return_against as bl, r.`{0}` as echange,
                                     (select max(i.against_sales_order) from `tabDelivery Note Item` i where i.parent = r.`{0}`) as commande
                              from `tabDelivery Note` r
                              where r.docstatus = 1 and ifnull(r.is_return, 0) = 1 and r.return_against in %(b)s
                                and r.name not in %(b)s and ifnull(r.`{0}`, '') <> ''""".format(CHAMP_ECHANGE),
                           {"b": tuple(bls)}, as_dict=True)
    return [dict(l) for l in lignes]


def _annuler_et_supprimer_bls(doc):
    bloquants = _reprises_d_autres_commandes([b.name for b in _bls_de(doc.name)])
    if bloquants:
        frappe.throw("<br>".join(
            "Le BL %s de cette commande a été repris par %s pour l'échange %s (commande %s). Annulez d'abord cet échange, "
            "puis cette commande." % (b["bl"], b["reprise"], b["echange"], b.get("commande") or "?") for b in bloquants),
            title="Échange à annuler d'abord")
    for b in _bls_de(doc.name):
        # relu à chaque tour : une reprise d'échange est partie avec son échange (retour_echange.before_cancel_bl)
        docstatus = frappe.db.get_value("Delivery Note", b.name, "docstatus")
        if docstatus is None:
            continue
        if docstatus != 1:
            # brouillon (aucun mouvement de stock) ou déjà annulé (reposting
            # déjà fait) : suppression directe
            frappe.delete_doc("Delivery Note", b.name, force=True, ignore_permissions=True)
            continue

        dn = frappe.get_doc("Delivery Note", b.name)
        magasins = {it.warehouse for it in dn.items if it.warehouse}
        desactives = [w for w in magasins if frappe.db.get_value("Warehouse", w, "disabled")]

        # la validation SLE refuse un magasin désactivé : réactivation temporaire
        for w in desactives:
            frappe.db.set_value("Warehouse", w, "disabled", 0, update_modified=False)

        dn.flags.ignore_permissions = True
        dn.cancel()  # reposting standard : le stock revient dans le magasin d'origine

        # le stock repris dans un magasin désactivé n'a rien à y faire :
        # transfert vers le magasin par défaut AVANT de re-désactiver
        if desactives:
            _transferer_vers_magasin_defaut(dn, desactives)

        # AVANT la suppression : le repost créé par l'annulation référence le
        # BL par son nom et échouerait à jamais (« BL introuvable ») une fois
        # la pièce supprimée — on le remplace par des recalculs par
        # (article, magasin), qui ne dépendent plus du BL.
        _convertir_reposts_du_bl(dn)

        frappe.delete_doc("Delivery Note", dn.name, force=True, ignore_permissions=True)

        for w in desactives:
            frappe.db.set_value("Warehouse", w, "disabled", 1, update_modified=False)


def _convertir_reposts_du_bl(dn):
    """Bascule les Repost Item Valuation « Transaction » du BL (en attente) en
    reposts « Item and Warehouse » à la date du BL, puis marque les originaux
    Skipped. Sans cela, chaque annulation laisserait un repost en échec."""
    en_attente = frappe.get_all(
        "Repost Item Valuation",
        filters={"voucher_type": "Delivery Note", "voucher_no": dn.name,
                 "status": ["in", ["Queued", "In Progress"]]},
        pluck="name")
    if not en_attente:
        return
    for nom in en_attente:
        frappe.db.set_value("Repost Item Valuation", nom, "status", "Skipped",
                            update_modified=False)
    paires = {(it.item_code, it.warehouse) for it in dn.items
              if it.warehouse and frappe.db.get_value("Item", it.item_code, "is_stock_item")}
    for article, entrepot in paires:
        riv = frappe.get_doc({
            "doctype": "Repost Item Valuation",
            "based_on": "Item and Warehouse",
            "item_code": article,
            "warehouse": entrepot,
            "posting_date": dn.posting_date,
            "posting_time": dn.posting_time,
            "company": dn.company,
            "allow_negative_stock": 1,
        })
        riv.flags.ignore_permissions = True
        riv.insert()
        riv.submit()


def _transferer_vers_magasin_defaut(dn, magasins_desactives: list):
    defaut = frappe.db.get_single_value("Stock Settings", "default_warehouse")
    if not defaut:
        frappe.throw(_(
            "Le BL {0} touche un magasin désactivé ({1}) mais aucun magasin par défaut "
            "n'est défini dans les paramètres de stock — annulation abandonnée.")
            .format(dn.name, ", ".join(magasins_desactives)))

    # seuls les articles STOCKÉS bougent : une ligne de main d'œuvre (M-E-OD…)
    # figure sur le BL mais n'a ni SLE ni transfert possible
    lignes = [it for it in dn.items
              if it.warehouse in magasins_desactives and flt(it.stock_qty) > 0
              and it.warehouse != defaut
              and frappe.db.get_value("Item", it.item_code, "is_stock_item")]
    if not lignes:
        return

    se = frappe.new_doc("Stock Entry")
    se.stock_entry_type = "Material Transfer"
    se.purpose = "Material Transfer"
    se.company = dn.company
    # À LA DATE DU BL ANNULÉ, pas à aujourd'hui : l'annulation rend le stock
    # dans l'historique à la date du BL — un transfert daté du jour laisserait
    # le stock « posé » dans le magasin désactivé sur toute la période, et
    # fausserait la valorisation en aval (avertissements de stock négatif).
    se.set_posting_time = 1
    se.posting_date = dn.posting_date
    se.posting_time = dn.posting_time
    se.remarks = _("Reprise du BL {0} (commande {1} annulée) : stock déplacé du "
                   "magasin désactivé vers {2}.").format(
        dn.name, ", ".join({it.against_sales_order for it in lignes if it.against_sales_order}) or "?",
        defaut)
    for it in lignes:
        se.append("items", {
            "item_code": it.item_code,
            "qty": flt(it.stock_qty),
            "uom": it.stock_uom,
            "stock_uom": it.stock_uom,
            "conversion_factor": 1,
            "s_warehouse": it.warehouse,
            "t_warehouse": defaut,
        })
    se.flags.ignore_permissions = True
    # Reprise forcée d'un magasin désactivé : hors trajets permis de la page Stock (stock_entrepots).
    se.flags.ignore_trajets = True
    se.insert()
    se.submit()


def qui_agit(user=None) -> str:
    """« Prénom Nom (identifiant) », ou l'identifiant seul quand il n'a pas de nom."""
    user = user or frappe.session.user
    nom = (get_fullname(user) or "").strip()
    return "%s (%s)" % (nom, user) if nom and nom != user else user


def texte_echeancier_annule(commande: str, quand, qui: str) -> str:
    """Le commentaire posé sur l'échéancier annulé avec sa commande. PURE."""
    return ("❌ Échéancier annulé : la commande %s a été annulée le %s par %s. Plus de relance ; "
            "ses visites et ses relances restent consultables." % (commande, _horodatage(quand), qui))


def texte_brouillon_laisse(commande: str, quand, qui: str) -> str:
    """Un échéancier BROUILLON ne peut pas être annulé (seul un document validé l'est) : il est laissé, et dit. PURE."""
    return ("❌ La commande %s a été annulée le %s par %s. Cet échéancier est un brouillon : il est laissé tel quel "
            "(jamais relancé tant qu'il n'est pas validé)." % (commande, _horodatage(quand), qui))


def _horodatage(quand) -> str:
    try:
        return quand.strftime("%d/%m/%Y à %H:%M")
    except AttributeError:
        return str(quand or "")


def texte_client_commande_annulee(commande: str, echeanciers, quand, qui: str) -> str:
    """Le commentaire de la fiche CLIENT quand sa commande annulée emporte un ou des échéanciers (26/11/2026,
    relecture : l'annulation gardait l'historique sur l'échéancier, mais ni la page Historique — qui ne lit que les
    échéanciers validés — ni la fiche client ne le montraient). PURE."""
    return ("❌ Commande %s annulée le %s par %s : échéancier(s) %s annulé(s), le client n'est plus relancé pour cette "
            "machine. Ses visites et ses relances restent consultables sur l'échéancier."
            % (commande, _horodatage(quand), qui, ", ".join(echeanciers)))


# ------------------------------------------------- listes d'appels brouillon

def articles_de_la_ligne(detail_articles) -> list:
    """Les articles d'une ligne de liste d'appels (JSON {article: date}, clés « _… » écartées, comme
    liste_appels.articles_appeles) ; vide si illisible. PURE."""
    try:
        d = json.loads(detail_articles or "{}")
    except Exception:
        return []
    return sorted(k for k in (d if isinstance(d, dict) else {}) if k and not str(k).startswith("_"))


def sort_ligne_liste(ligne, remplacant=None) -> tuple:
    """Que faire d'une ligne de liste d'appels BROUILLON qui cite un échéancier qu'on annule ? PURE.
    → ("repointer", remplaçant) : un autre échéancier validé du client porte un article de la ligne, l'appel reste dû ;
      ("retirer", None) : ligne pas encore touchée (ni « a été appelé », ni résumé, ni réponse, ni tâche), le client
                          n'est plus à appeler pour cette machine ;
      ("retirer_appel", None) : l'appel est déjà saisi — la ligne part aussi (son échéancier est OBLIGATOIRE sur la
                          ligne : vidé, la liste ne s'enregistrerait pas davantage, MandatoryError mesurée le
                          26/11/2026), et son appel est recopié sur la fiche client (page Historique)."""
    if remplacant:
        return "repointer", remplacant
    touchee = (cint(ligne.get("appele")) or (ligne.get("resume_appel") or "").strip()
               or (ligne.get("reponse_client") or "").strip() or ligne.get("tache_de_travail"))
    return ("retirer_appel", None) if touchee else ("retirer", None)


def texte_liste(echeancier: str, pourquoi: str, faits) -> str:
    """Le commentaire posé sur la liste d'appels brouillon retouchée. `faits` : [(action, client, remplaçant)]. PURE."""
    quoi = []
    for action, client, remplacant in faits:
        if action == "retirer":
            quoi.append("ligne de %s retirée (pas encore appelée)" % client)
        elif action == "repointer":
            quoi.append("ligne de %s rattachée à %s (autre machine du client)" % (client, remplacant))
        else:
            quoi.append("ligne de %s retirée, son appel est recopié sur la fiche client" % client)
    return "%s : l'échéancier %s est annulé — %s." % (pourquoi, echeancier, " ; ".join(quoi))


def texte_appel_recopie(liste: str, ligne, echeancier: str, pourquoi: str) -> str:
    """Le commentaire de la fiche client qui garde l'appel d'une ligne retirée (« 📞 » : la page Historique le range
    dans les appels). Le texte saisi par l'opérateur est échappé. PURE."""
    quoi = []
    if (ligne.get("resume_appel") or "").strip():
        quoi.append("« %s »" % html_lib.escape(ligne.get("resume_appel").strip()))
    if (ligne.get("reponse_client") or "").strip():
        quoi.append("réponse « %s »" % html_lib.escape(ligne.get("reponse_client").strip()[:300]))
    if ligne.get("tache_de_travail"):
        quoi.append("tâche %s à revoir" % html_lib.escape(ligne.get("tache_de_travail")))
    return ("📞 Appel noté dans la liste d'appels %s%s (%s). La ligne est retirée de la liste : l'échéancier %s est "
            "annulé — %s." % (liste, " : " + ", ".join(quoi) if quoi else "",
                              "a été appelé" if cint(ligne.get("appele")) else "pas encore coché « a été appelé »",
                              echeancier, pourquoi))


def _autre_echeancier_du_client(client, articles, sauf) -> str | None:
    if not client or not articles:
        return None
    noms = frappe.db.sql_list("""select distinct ms.name from `tabMaintenance Schedule` ms
                                 join `tabMaintenance Schedule Detail` d on d.parent = ms.name
                                 where ms.customer = %s and ms.docstatus = 1 and ms.name <> %s and d.item_code in %s
                                 order by ms.name limit 1""", (client, sauf, tuple(articles)))
    return noms[0] if noms else None


def lignes_listes_brouillon(echeancier, sauf_listes=()) -> list:
    """Les lignes des listes d'appels BROUILLON qui citent cet échéancier (hors `sauf_listes`)."""
    if not frappe.db.table_exists(LIGNE_LISTE) or not frappe.db.table_exists(LISTE):
        return []
    return frappe.db.sql("""select a.name, a.parent, a.client, a.`a_été_appelé` as appele, a.resume_appel,
                                   a.reponse_client, a.tache_de_travail, a.detail_articles
                            from `tabAppelle Client` a join `tabListe Appelle Entretien` l on l.name = a.parent
                            where a.`{0}` = %s and a.parenttype = %s and l.docstatus = 0 and l.name not in %s
                            order by a.parent, a.idx""".format(CHAMP_ECHEANCIER),
                         (echeancier, LISTE, tuple(sauf_listes) or ("",)), as_dict=True)


def degager_des_listes_brouillon(echeancier: str, pourquoi: str, sauf_listes=()) -> list:
    """Avant de laisser un échéancier ANNULÉ derrière soi : les listes d'appels BROUILLON qui le citent (26/11/2026,
    relecture). Frappe refuse d'enregistrer un document soumettable lié à une pièce annulée (CancelledLinkError) : la
    liste entière était bloquée pour l'opérateur, et la validation automatique de 07:00 échouait (mesuré sur la copie :
    LIST-25-11-2026-00001 refusée dès que MAT-MSH-2026-00391 est annulé ; 4 brouillons ouverts, jusqu'à 37 jours en
    2026). Chaque ligne est rattachée à un autre échéancier du client, sinon retirée (sort_ligne_liste ; un appel déjà
    saisi est recopié sur la fiche client — le champ échéancier de la ligne est obligatoire, le vider ne suffit pas).
    En SQL, la liste restant un brouillon ; sa date de modification avance pour qu'un formulaire resté ouvert demande à
    être rechargé au lieu de réécrire la ligne. Un commentaire sur la liste dit ce qui a été fait.
    `sauf_listes` : les listes à ne pas toucher — celle qu'on est en train de valider (liste_validation), dont les
    lignes en mémoire seraient réécrites par-dessus.
    → [(liste, action, client, remplaçant)]."""
    lignes = lignes_listes_brouillon(echeancier, sauf_listes)
    if not lignes:
        return []
    faits, par_liste, non_appelees = [], {}, {}
    for l in lignes:
        remplacant = _autre_echeancier_du_client(l.client, articles_de_la_ligne(l.detail_articles), echeancier)
        action, remplacant = sort_ligne_liste(l, remplacant)
        if action == "repointer":
            frappe.db.sql("update `tabAppelle Client` set `{0}` = %s where name = %s".format(CHAMP_ECHEANCIER),
                          (remplacant, l.name))
        else:
            frappe.db.sql("delete from `tabAppelle Client` where name = %s", l.name)
            if not cint(l.appele):
                non_appelees[l.parent] = non_appelees.get(l.parent, 0) + 1
            if action == "retirer_appel" and l.client and frappe.db.exists("Customer", l.client):
                try:
                    frappe.get_doc("Customer", l.client).add_comment(
                        "Comment", texte_appel_recopie(l.parent, l, echeancier, pourquoi))
                except Exception:
                    pass  # la traçabilité ne doit jamais empêcher l'annulation
        par_liste.setdefault(l.parent, []).append((action, l.client or "?", remplacant))
        faits.append((l.parent, action, l.client, remplacant))
    quand = now_datetime()
    for liste, actions in par_liste.items():
        retirees = sum(1 for a, _c, _r in actions if a != "repointer")
        if retirees:
            # numérotation continue des lignes restantes, et le compteur de l'écran (lignes non appelées) suit
            for i, nom in enumerate(frappe.db.sql_list("""select name from `tabAppelle Client` where parent = %s
                                                          and parenttype = %s order by idx, name""", (liste, LISTE)), 1):
                frappe.db.sql("update `tabAppelle Client` set idx = %s where name = %s", (i, nom))
            restant = frappe.db.get_value(LISTE, liste, "nb_appels_restant")
            if non_appelees.get(liste) and str(restant or "").strip().lstrip("-").isdigit():
                frappe.db.sql("update `tabListe Appelle Entretien` set nb_appels_restant = %s where name = %s",
                              (str(max(0, cint(restant) - non_appelees[liste])), liste))
        frappe.db.sql("update `tabListe Appelle Entretien` set modified = %s, modified_by = %s where name = %s",
                      (quand, frappe.session.user, liste))
        try:
            frappe.get_doc(LISTE, liste).add_comment("Comment", texte_liste(echeancier, pourquoi, actions))
            frappe.publish_realtime("doc_update", {"doctype": LISTE, "name": liste, "modified": str(quand)},
                                    doctype=LISTE, docname=liste, after_commit=True)
        except Exception:
            pass  # la traçabilité ne doit jamais empêcher l'annulation
    return faits


def _nettoyer_echeanciers(doc) -> dict:
    """Échéanciers de la commande ANNULÉS (jamais supprimés depuis le 26/11/2026, voir l'en-tête), puis calendrier des
    autres échéanciers remis « En Attente ».
    → {"annules": [...], "brouillons": [...], "lignes_remises": n, "listes": [(liste, action, client, remplaçant)]}."""
    quand, qui = now_datetime(), qui_agit()
    bilan = {"annules": [], "brouillons": [], "lignes_remises": 0, "listes": []}

    # 1. la commande est dans la table Articles → l'échéancier est annulé (ignore_links : ses visites soumises, une
    #    ligne de liste d'appel qui le cite, ne doivent pas bloquer), avec un commentaire qui dit pourquoi.
    parents = sorted(set(frappe.get_all("Maintenance Schedule Item",
                                        filters={"sales_order": doc.name}, pluck="parent")))
    for nom in parents:
        if not frappe.db.exists("Maintenance Schedule", nom):
            continue
        ms = frappe.get_doc("Maintenance Schedule", nom)
        if ms.docstatus == 1:
            ms.flags.ignore_permissions = True
            ms.flags.ignore_links = True
            ms.cancel()
            ms.add_comment("Comment", texte_echeancier_annule(doc.name, quand, qui))
            bilan["annules"].append(nom)
            bilan["listes"] += degager_des_listes_brouillon(nom, "❌ Commande %s annulée" % doc.name)
        elif ms.docstatus == 0:
            ms.add_comment("Comment", texte_brouillon_laisse(doc.name, quand, qui))
            bilan["brouillons"].append(nom)
        # déjà annulé : rien à faire, son commentaire est déjà là
    if bilan["annules"] and doc.get("customer") and frappe.db.exists("Customer", doc.customer):
        frappe.get_doc("Customer", doc.customer).add_comment(
            "Comment", texte_client_commande_annulee(doc.name, bilan["annules"], quand, qui))

    # 2. la commande est posée sur une ligne du CALENDRIER d'un autre
    #    échéancier non annulé (custom_sales_order) → on retire la référence et
    #    la ligne repasse « En Attente ». SQL direct : les parents sont soumis,
    #    un save() de ligne enfant serait refusé. Un échéancier annulé (celui
    #    de l'étape 1 compris) garde ses lignes : c'est son historique, et leur
    #    référence est retirée à la suppression de la commande (on_trash).
    lignes = frappe.db.sql(
        """select d.name, d.parent from `tabMaintenance Schedule Detail` d
           left join `tabMaintenance Schedule` ms on ms.name = d.parent
           where d.custom_sales_order = %s and ifnull(ms.docstatus, 0) < 2""", doc.name, as_dict=True)
    for l in lignes:
        frappe.db.sql(
            """update `tabMaintenance Schedule Detail`
               set custom_sales_order = null, completion_status = 'Pending',
                   actual_date = null
               where name = %s""", l.name)
    bilan["lignes_remises"] = len(lignes)
    for parent in sorted({l.parent for l in lignes if l.parent}):
        if frappe.db.exists("Maintenance Schedule", parent):
            frappe.get_doc("Maintenance Schedule", parent).add_comment(
                "Comment",
                _("Commande {0} annulée : ligne(s) du calendrier remise(s) « En Attente ».")
                .format(doc.name))
    return bilan


def _paiements_de(commande: str) -> list:
    """Les Payment Entry VALIDÉS qui portent la commande dans leurs références — exactement ce que lisait le Server
    Script (frappe.get_all("Payment Entry", filters={"reference_doctype": …, "reference_name": …, "docstatus": 1}) :
    Frappe cherche ces deux champs dans la table enfant « Payment Entry Reference » ; mesuré identique sur 300
    commandes le 26/11/2026). Sans doublon : une commande citée sur deux lignes du même paiement le faisait sortir deux
    fois, et le script plantait sur la 2e annulation (1 cas, SAL-ORD-2023-00407)."""
    return frappe.db.sql_list(
        """select distinct pe.name from `tabPayment Entry` pe
           join `tabPayment Entry Reference` per on per.parent = pe.name
           where per.reference_doctype = 'Sales Order' and per.reference_name = %s and pe.docstatus = 1
           order by pe.name""", commande)


def _ecritures_de(commande: str) -> list:
    """Les Journal Entry dont une ligne référence la commande, quel que soit leur état (comme le script)."""
    return frappe.db.sql_list(
        """select distinct parent from `tabJournal Entry Account`
           where reference_type = 'Sales Order' and reference_name = %s order by parent""", commande)


def _supprimer_paiements_et_ecritures(doc):
    """Reprise À L'IDENTIQUE du Server Script « cancel sales order payment » (26/11/2026) : chaque paiement validé
    est annulé puis supprimé, puis chaque écriture liée est annulée (si validée) puis supprimée. Une erreur lève :
    l'annulation entière est refusée, la commande reste intacte (comportement du script).
    Les droits de l'utilisateur s'appliquent à l'annulation des paiements et des écritures, exactement comme avec le
    script : son « frappe.flags.ignore_permissions = True » visait le frappe.flags du bac à sable (safe_exec,
    frappe._dict() neuf), qui ne levait rien ; et Frappe v15 ne lit nulle part ce drapeau global pour cancel(), seul
    doc.flags.ignore_permissions compte. Le poser ici sur chaque pièce changerait donc les droits réels (relecture du
    26/11/2026) : un vendeur sans droit d'annuler un paiement voit toujours l'annulation de sa commande refusée. La
    suppression, elle, passe outre les droits (ignore_permissions=True), comme dans le script."""
    for nom in _paiements_de(doc.name):
        pe = frappe.get_doc("Payment Entry", nom)
        pe.cancel()
        frappe.delete_doc("Payment Entry", pe.name, ignore_permissions=True)
    for nom in _ecritures_de(doc.name):
        je = frappe.get_doc("Journal Entry", nom)
        if je.docstatus == 1:
            je.cancel()
        frappe.delete_doc("Journal Entry", nom, ignore_permissions=True)


def _noter(bilan: dict):
    """Compteurs du jour pour l'entonnoir des relances (contrat partagé du 25/11/2026). Ne lève jamais."""
    try:
        from customization_app.entonnoir_relances import noter
    except ImportError:
        return
    try:
        noter("annulations_commande", {"commandes_annulees": 1,
                                       "echeanciers_annules": len(bilan.get("annules") or []),
                                       "echeanciers_brouillons": len(bilan.get("brouillons") or []),
                                       "lignes_calendrier_remises": bilan.get("lignes_remises") or 0,
                                       "lignes_listes_brouillon": len(bilan.get("listes") or [])})
    except Exception:
        pass


# -------------------------------------------------------------- suppression

def on_trash_customer(doc, method=None):
    """Hook on_trash du client (relecture finale du 26/11/2026). Les échéanciers sont désormais ANNULÉS au lieu d'être
    supprimés : un échéancier annulé bloquait la suppression du client (LinkExistsError, « désactivez-le plutôt ») —
    le geste habituel après une commande en double (« complexe sanitaire de jebel oust », 28/04/2026). Les échéanciers
    ANNULÉS du client partent avec lui (trace dans « Deleted Document ») ; un échéancier validé bloque toujours."""
    for nom in frappe.get_all("Maintenance Schedule", filters={"customer": doc.name, "docstatus": 2}, pluck="name"):
        frappe.delete_doc("Maintenance Schedule", nom, force=True, ignore_permissions=True)


def on_trash_sales_order(doc, method=None):
    """Une commande ANNULÉE se supprime même encore référencée ailleurs :
    on retire les références bloquantes avant le contrôle des liens."""
    if doc.docstatus != 2:
        return
    _delier_echeanciers_annules(doc)
    _delier_references(doc)


def texte_reference_retiree(commande: str, lignes_machine: int, visites: int) -> str:
    """Le commentaire posé sur un échéancier annulé quand sa commande est supprimée. PURE."""
    quoi = []
    if lignes_machine:
        quoi.append("%d ligne(s) machine" % lignes_machine)
    if visites:
        quoi.append("%d visite(s)" % visites)
    return ("🗑️ Commande %s supprimée : sa référence est retirée de cet échéancier annulé (%s). "
            "L'historique des visites reste." % (commande, ", ".join(quoi) or "aucune ligne"))


def _delier_echeanciers_annules(doc):
    """Un échéancier ANNULÉ qui cite la commande (ligne machine ou visite) bloquerait sa suppression : Frappe compte les
    pièces annulées pour un lien statique. La référence est retirée en SQL (le parent est annulé, un save() serait
    refusé), la commande est nommée dans un commentaire. Les échéanciers validés ou brouillons restent l'affaire de
    _delier_references (inchangé)."""
    par_ms = {}
    for champ, table, cle in (("sales_order", "Maintenance Schedule Item", "machines"),
                              ("custom_sales_order", "Maintenance Schedule Detail", "visites")):
        lignes = frappe.db.sql(
            """select x.name, x.parent from `tab{0}` x join `tabMaintenance Schedule` ms on ms.name = x.parent
               where x.`{1}` = %s and ms.docstatus = 2""".format(table, champ), doc.name, as_dict=True)
        for l in lignes:
            frappe.db.sql("update `tab{0}` set `{1}` = null where name = %s".format(table, champ), l.name)
            par_ms.setdefault(l.parent, {"machines": 0, "visites": 0})[cle] += 1
    for nom, n in sorted(par_ms.items()):
        try:
            frappe.get_doc("Maintenance Schedule", nom).add_comment(
                "Comment", texte_reference_retiree(doc.name, n["machines"], n["visites"]))
        except Exception:
            pass  # la traçabilité ne doit jamais empêcher la suppression
    return par_ms


def _delier_references(doc):
    from frappe.model.dynamic_links import get_dynamic_link_map
    from frappe.model.rename_doc import get_link_fields

    ignores = set(frappe.get_hooks("ignore_links_on_delete") or [])
    delies = set()  # (doctype, nom) des pièces déliées, pour la traçabilité

    # liens statiques (champs Link → Sales Order), tables enfants comprises
    for df in get_link_fields("Sales Order"):
        dt, champ = df.get("parent"), df.get("fieldname")
        if not dt or df.get("issingle") or dt in ignores or not frappe.db.table_exists(dt):
            continue
        meta = frappe.get_meta(dt)
        colonnes = ["name", "docstatus"] + (["parent", "parenttype"] if meta.istable else [])
        lignes = frappe.db.get_values(dt, {champ: doc.name}, colonnes, as_dict=True)
        for l in lignes:
            # pièces annulées laissées telles quelles (leur lien bloque pourtant
            # aussi la suppression, voir l'en-tête : les échéanciers annulés
            # sont déliés à part, par _delier_echeanciers_annules)
            if l.docstatus == 2:
                continue
            porteur = (l.parenttype, l.parent) if meta.istable else (dt, l.name)
            if porteur[0] in ignores or porteur == ("Sales Order", doc.name):
                continue
            frappe.db.sql(
                "update `tab{0}` set `{1}` = null where name = %s".format(dt, champ),
                l.name)
            delies.add(porteur)

    # liens dynamiques (reference_doctype/reference_name et assimilés)
    for df in get_dynamic_link_map().get("Sales Order", []):
        dt = df.parent
        if dt in ignores or not frappe.db.table_exists(dt):
            continue
        meta = frappe.get_meta(dt)
        if meta.issingle:
            continue
        colonnes = ["name", "docstatus"] + (["parent", "parenttype"] if meta.istable else [])
        lignes = frappe.db.get_values(
            dt, {df.options: "Sales Order", df.fieldname: doc.name}, colonnes, as_dict=True)
        for l in lignes:
            if l.docstatus == 2:
                continue
            porteur = (l.parenttype, l.parent) if meta.istable else (dt, l.name)
            if porteur[0] in ignores:
                continue
            frappe.db.sql(
                "update `tab{0}` set `{1}` = null, `{2}` = null where name = %s".format(
                    dt, df.fieldname, df.options),
                l.name)
            delies.add(porteur)

    for dt, nom in delies:
        if dt and nom and frappe.db.exists(dt, nom):
            try:
                frappe.get_doc(dt, nom).add_comment(
                    "Comment",
                    _("Référence retirée : la commande annulée {0} a été supprimée.")
                    .format(doc.name))
            except Exception:
                pass  # la traçabilité ne doit jamais empêcher la suppression
