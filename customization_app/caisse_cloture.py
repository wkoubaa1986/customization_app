"""
Validation (clôture) de la caisse journalière.

L'ÉTAT AVANT / APRÈS (décision utilisateur 19/08) :
  - AVANT  : le solde d'ouverture = le report de la DERNIÈRE clôture validée de
             cette caisse — les espèces COMPTÉES si l'employé les a saisies,
             sinon le théorique. Première clôture : 0 ;
  - le jour : encaissements espèces (totaux de la période, avances hors période
             exclues) − dépenses espèces de la caisse ;
  - APRÈS  : solde théorique = ouverture + encaissements − dépenses. L'employé
             saisit les espèces réellement comptées ; l'écart est figé avec.

QUI VALIDE QUOI :
  - chaque employé valide SA caisse ;
  - la DIRECTION (Wassim, Jamel, Néjib — même cercle que la visibilité
    Economiq) valide n'importe quelle caisse ET la caisse globale
    « Tous les employés ».

L'INSTANTANÉ : la clôture est un document SOUMIS (immuable), avec un PDF du
récapitulatif (état, modes, dépenses, détail par employé pour la globale)
attaché au moment de la validation. Une seule clôture par caisse et par jour.
"""

import re
from html import unescape

import frappe
from frappe import _
from frappe.utils import add_days, flt, get_datetime, getdate, now_datetime, nowdate

from customization_app.rapport_caisse_journaliere import (
    CAISSE_ACCOUNTS_LIKE,
    PEUVENT_VOIR_ECONOMIQ as DIRECTION,
    _exclusions,
    _ma_caisse,
    get_data,
)

CAISSE_GLOBALE = "Tous les employés"
ROLES = ("System Manager", "Accounts Manager", "Accounts User",
         "Sales Manager", "Sales User",
         "Sous-traitant")   # il compte et remet SA caisse (_controler_droits le limite à la sienne)

# ── Rapprochement de la caisse GLOBALE (décision utilisateur 19/08) ──────────
# Chaque mode a sa nature :
#   - espèces : stock cumulatif (déjà l'avant/après historique) ;
#   - chèques / traites : PORTEFEUILLE — entrent à la saisie, sortent à la
#     remise en banque (custom_remise_en_banque, marquée depuis le bouton
#     « Remise en banque ») : avant + reçus − remis = après ;
#   - dettes : ENCOURS comptable (compte Dettes - A&S), rien à compter — les
#     PE de dette sont supprimées/recréées à l'encaissement (convention maison),
#     donc l'« avant » est le report de la dernière clôture, jamais un recalcul.
COMPTE_DETTES = "Dettes - A&S"
MODES_PORTEFEUILLE = (("cheques", "Chèque"), ("traites", "Traite bancaire"))
# Même plancher que le reste de la caisse : les chèques antérieurs (jamais
# marqués remis) ne polluent pas le portefeuille.
PLANCHER_PORTEFEUILLE = "2026-07-01"

# ── Justification d'un point de contrôle (05/10/2026) ────────────────────────
# Un MOTIF choisi dans la liste de son type + un commentaire d'au moins 10 caractères.
# Avant, n'importe quel texte passait : en septembre, 17 points sur 28 justifiés par « rs ».
COMMENTAIRE_MIN = 10
MOTIFS = {
    "tache": ["Client absent ou injoignable", "Rendez-vous reporté par le client",
              "Intervention à terminer (pièce, temps)", "Tâche faite, clôture oubliée", "Autre"],
    "dette": ["Le client paiera plus tard (date dans le commentaire)", "Dette accordée par la direction (qui ?)",
              "Client professionnel : paiement sur facture ou virement", "Paiement reçu, pas encore saisi",
              "Litige avec le client", "Autre"],
    "ancien_exclu": ["Déjà encaissé dans une autre caisse ou un autre jour",
                     "Paiement régénéré (facture refaite ou annulée)", "Autre"],
    "caisse_non_collectee": ["Employé absent, collecte au prochain passage", "Collecte en cours", "Autre"],
    # Dépense saisie par un collègue mais payée de CETTE caisse (« Payée de la caisse de », 24/11/2026) : le payeur
    # la confirme à son comptage — ou dit qu'il ne l'a pas payée, et pourquoi.
    "depense_collegue": ["Je confirme l'avoir payée de ma caisse", "Je ne l'ai pas payée : à corriger", "Autre"],
}
# Motifs qui se suffisent : pas de commentaire exigé (confirmer une dépense qu'on a bien payée).
MOTIFS_SANS_COMMENTAIRE = {"Je confirme l'avoir payée de ma caisse"}
FAMILLE_DU_POINT = {"tache_ouverte": "tache", "tache_sans_commande": "tache", "dette_hors_aramex": "dette",
                    "ancien_exclu": "ancien_exclu", "caisse_non_collectee": "caisse_non_collectee",
                    "depense_collegue": "depense_collegue"}


def _est_direction():
    return (frappe.session.user in DIRECTION
            or "System Manager" in frappe.get_roles())


def _portefeuille_mode(mode, date):
    """Le portefeuille d'un mode (Chèque / Traite bancaire) autour de `date` :
    entrée = date de SAISIE (la pièce arrive physiquement quand on l'enregistre),
    sortie = date de remise en banque. Renvoie les quatre chiffres du
    rapprochement + la liste nominative encore en portefeuille au soir."""
    date = getdate(date)
    like = " OR ".join(["pe.paid_to LIKE %s"] * len(CAISSE_ACCOUNTS_LIKE))
    rows = frappe.db.sql(
        f"""SELECT pe.name, DATE(pe.creation) AS entre_le, pe.posting_date,
                   pe.party_name, pe.party, pe.paid_amount, pe.reference_no,
                   pe.custom_remise_en_banque AS remis_le
            FROM `tabPayment Entry` pe
            WHERE pe.docstatus = 1 AND pe.payment_type = 'Receive'
              AND pe.mode_of_payment = %s AND ({like})
              AND DATE(pe.creation) BETWEEN %s AND %s
            ORDER BY pe.creation""",
        (mode, *CAISSE_ACCOUNTS_LIKE, PLANCHER_PORTEFEUILLE, date), as_dict=True)

    avant = recus = remis = apres = 0.0
    liste = []
    for r in rows:
        entre, sorti, montant = getdate(r.entre_le), r.remis_le, flt(r.paid_amount, 3)
        if entre < date and (not sorti or getdate(sorti) >= date):
            avant += montant
        if entre == date:
            recus += montant
        if sorti and getdate(sorti) == date:
            remis += montant
        if not sorti or getdate(sorti) > date:
            apres += montant
            liste.append({
                "name": r.name, "date": str(r.entre_le),
                "client": r.party_name or r.party or "",
                "reference_no": r.reference_no or "",
                "montant": montant, "mode": mode,
            })
    return {"avant": flt(avant, 3), "recus": flt(recus, 3),
            "remis": flt(remis, 3), "apres": flt(apres, 3), "liste": liste}


def _encours_dettes(date):
    """Le solde du compte Dettes - A&S à la date donnée (débit − crédit)."""
    v = frappe.db.sql(
        """SELECT SUM(debit - credit) FROM `tabGL Entry`
           WHERE account = %s AND is_cancelled = 0 AND posting_date <= %s""",
        (COMPTE_DETTES, date))
    return flt(v[0][0] if v and v[0][0] is not None else 0, 3)


def _rapprochement(date):
    date = getdate(date)
    out = {cle: _portefeuille_mode(mode, date) for cle, mode in MODES_PORTEFEUILLE}
    prev = frappe.db.sql(
        """SELECT dettes_apres FROM `tabCloture Caisse`
           WHERE docstatus = 1 AND caisse = %s AND date_cloture < %s
             AND dettes_apres IS NOT NULL
           ORDER BY date_cloture DESC, creation DESC LIMIT 1""",
        (CAISSE_GLOBALE, date))
    apres = _encours_dettes(date)
    avant = flt(prev[0][0], 3) if prev else _encours_dettes(add_days(date, -1))
    out["dettes"] = {"avant": avant, "apres": apres,
                     "variation": flt(apres - avant, 3)}
    return out


@frappe.whitelist()
def portefeuille(date):
    """Les chèques et traites encore en portefeuille au soir de `date`, pour le
    dialogue « Remise en banque » (direction)."""
    frappe.only_for(ROLES)
    if not _est_direction():
        frappe.throw(_("La remise en banque est réservée à la direction."))
    date = getdate(date)
    pieces = []
    for _cle, mode in MODES_PORTEFEUILLE:
        pieces += _portefeuille_mode(mode, date)["liste"]
    pieces.sort(key=lambda p: (p["date"], p["name"]))
    return {"date": str(date), "pieces": pieces,
            "total": flt(sum(p["montant"] for p in pieces), 3)}


@frappe.whitelist()
def remettre_en_banque(paiements, date):
    """Marque des chèques/traites comme remis en banque à `date` — la SORTIE du
    portefeuille, tracée en commentaire sur chaque pièce."""
    frappe.only_for(ROLES)
    if not _est_direction():
        frappe.throw(_("La remise en banque est réservée à la direction."))
    import json as _json
    noms = _json.loads(paiements) if isinstance(paiements, str) else (paiements or [])
    date = getdate(date)
    faits = []
    for nom in noms:
        pe = frappe.db.get_value(
            "Payment Entry", nom,
            ["docstatus", "mode_of_payment", "custom_remise_en_banque"], as_dict=True)
        if not pe or pe.docstatus != 1:
            frappe.throw(_("Paiement {0} introuvable ou non validé.").format(nom))
        if pe.mode_of_payment not in dict(MODES_PORTEFEUILLE).values():
            frappe.throw(_("{0} n'est ni un chèque ni une traite.").format(nom))
        if pe.custom_remise_en_banque:
            continue
        frappe.db.set_value("Payment Entry", nom, "custom_remise_en_banque", date,
                            update_modified=False)
        frappe.get_doc({
            "doctype": "Comment", "comment_type": "Info",
            "reference_doctype": "Payment Entry", "reference_name": nom,
            "content": "Remis en banque le %s par %s (caisse journalière)"
                       % (date, frappe.session.user),
        }).insert(ignore_permissions=True)
        faits.append(nom)
    frappe.db.commit()
    return {"remis": faits, "date": str(date)}


def _controler_droits(caisse):
    """Chaque employé -> sa caisse ; la direction -> toutes + la globale ; le responsable de
    collecte (titulaire) -> toutes les caisses individuelles (il compte pour un employé absent,
    puis valide directement depuis la collecte — 04/10/2026)."""
    if frappe.session.user in DIRECTION or "System Manager" in frappe.get_roles():
        return
    from customization_app import caisse_collecte as CC
    # (titulaire de la collecte ; ou Responsable sous-traitant sur la caisse d'un sous-traitant — 31/10/2026)
    if caisse != CAISSE_GLOBALE and CC.role_collecte_pour(caisse) == "titulaire":
        return
    # _ma_caisse a besoin des listes ; on la reconstruit a minima.
    exclus_u, exclus_e = _exclusions()
    employees = [e for e in frappe.db.sql(
        """SELECT e.name AS employee_id, e.employee_name, e.user_id AS user_email
           FROM `tabEmployee` e""", as_dict=True)
        if e.employee_id not in exclus_e and (e.user_email or "") not in exclus_u]
    users = frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"},
                           fields=["name", "full_name"])
    users = [u for u in users if u.name not in exclus_u]
    mienne = _ma_caisse(employees, users)
    if caisse != mienne:
        frappe.throw(_("Vous ne pouvez valider que votre propre caisse ({0}).")
                     .format(mienne or _("aucune")))


def _est_caisse_sous_traitant(caisse) -> bool:
    if not caisse or caisse == CAISSE_GLOBALE or not frappe.db.has_column("Employee", "custom_sous_traitant"):
        return False
    return bool(frappe.db.get_value("Employee", {"employee_name": caisse, "status": "Active"}, "custom_sous_traitant"))


def debut_periode(caisse, date):
    """Le premier jour que couvre la clôture datée `date`. Un employé compte chaque jour : la journée. Un
    SOUS-TRAITANT compte et remet quand on le voit (chaque semaine, décision utilisateur 31/10/2026) : sa
    caisse court depuis le lendemain de sa dernière remise (comptée ou validée), ou depuis la date de départ
    des caisses, ou depuis son arrivée — jamais avant la remise à zéro (Config Caisse)."""
    date = getdate(date)
    if not _est_caisse_sous_traitant(caisse):
        return date
    from customization_app import caisse_collecte as CC
    plancher = CC.plancher_report(date, CC.date_depart_pour(caisse, CC.config()))
    derniere = frappe.db.sql(
        """SELECT MAX(date_cloture) FROM `tabCloture Caisse`
           WHERE caisse = %s AND date_cloture < %s AND date_cloture >= %s
             AND (docstatus = 1 OR (docstatus = 0 AND statut IN %s))""",
        (caisse, date, plancher or "1900-01-01", tuple(CC.STATUTS_EN_ATTENTE)))
    if derniere and derniere[0][0]:
        return min(add_days(getdate(derniere[0][0]), 1), date)
    arrivee = frappe.db.get_value("Employee", {"employee_name": caisse, "status": "Active"}, "date_of_joining")
    debut = max(d for d in (plancher, getdate(arrivee) if arrivee else None) if d) if (plancher or arrivee) else date
    return min(getdate(debut), date)


def _mesures(caisse, date):
    """Les chiffres de la caisse — la journée, ou la période depuis la dernière remise d'un sous-traitant
    (`debut_periode`) —, depuis la MÊME source que la page."""
    debut = debut_periode(caisse, date)
    data = get_data(str(debut), str(date), employe=("" if caisse == CAISSE_GLOBALE else caisse))
    recap = data.get("recap") or {}
    par_mode = recap.get("par_mode") or {}
    depenses = data.get("depenses") or {}
    dep_par_mode = depenses.get("par_mode") or {}
    especes = flt(par_mode.get("Espèces"), 3)
    cheques = flt(par_mode.get("Chèque"), 3)
    autres = flt(flt(recap.get("grand_total"), 3) - especes - cheques, 3)
    return {
        "encaissements_especes": especes,
        "total_cheques": cheques,
        "total_autres_modes": autres,
        "depenses_especes": flt(dep_par_mode.get("Espèces"), 3),
        "debut": str(debut),
        "data": data,
    }


def _ouverture(caisse, date):
    """Le report de la dernière clôture validée : espèces comptées si saisies,
    sinon le théorique."""
    # Remise à zéro (Config Caisse, date de départ) : à partir de ce jour, les clôtures
    # antérieures ne font plus report — chaque caisse repart de 0.
    from customization_app import caisse_collecte as CC
    plancher = CC.plancher_report(date, CC.date_depart_pour(caisse, CC.config()))
    ligne = frappe.db.sql(
        """SELECT solde_theorique, especes_comptees, especes_remises, fond_conserve FROM `tabCloture Caisse`
           WHERE docstatus = 1 AND caisse = %s AND date_cloture < %s AND date_cloture >= %s
           ORDER BY date_cloture DESC, creation DESC LIMIT 1""",
        (caisse, date, plancher or "1900-01-01"), as_dict=True)
    if not ligne:
        return 0.0
    r = ligne[0]
    # Double validation (04/10/2026) : l'employé repart avec ce qu'il a GARDÉ,
    # pas avec tout son comptage — les espèces remises au responsable sont parties.
    # `fond_conserve` fait foi (recalculé quand l'employé accepte le chiffre du
    # responsable) ; sinon comptées − remises ; clôtures historiques : comptées.
    if r.fond_conserve is not None:
        return flt(r.fond_conserve, 3)
    base = r.especes_comptees if r.especes_comptees is not None else r.solde_theorique
    return flt(flt(base, 3) - flt(r.especes_remises, 3), 3)


def _fmt_montant(v):
    """« 1 315,500 » — le format des écrans de caisse, pas le point décimal machine."""
    return "{:,.3f}".format(flt(v)).replace(",", " ").replace(".", ",")


def _controles(data):
    """Les points de contrôle de la clôture (décisions utilisateur 19/08) :

      tache_ouverte    : une intervention du jour reste ouverte -> JUSTIFIER
                         (avec le montant en dette de la commande, s'il y en a) ;
      bl_non_valide    : tâche terminée mais bon de livraison en brouillon ->
                         BLOQUANT, le BL doit être validé (bouton dédié) ;
      dette_hors_aramex: un paiement en « Dette non payée » hors flux Aramex sur une
                         commande dont la tâche n'est plus ouverte -> JUSTIFIER.
                         Jusqu'au 05/10/2026, seulement commande validée + tâche
                         terminée : une dette sans tâche, ou sur tâche annulée, passait ;
      ancien_exclu     : un paiement d'ancienne commande a été EXCLU de la
                         caisse -> JUSTIFIER l'exclusion.
    Les tâches ouvertes SANS commande : `_taches_sans_commande` (hors rapport)."""
    def _qui(o):
        """« SAL-ORD-… · Client · tâche TASK-… (Entretien, Akram) » — le lecteur du point de
        contrôle décide en une ligne, sans aller ouvrir la commande (demande utilisateur
        21/08 : « donne plus de détails, la tâche, le nom du client »)."""
        bouts = [o["sales_order"], o.get("customer") or "client ?"]
        if o.get("tache_reference"):
            tache = o["tache_reference"]
            precisions = [x for x in (o.get("intervention"), o.get("tache_employee")) if x]
            if precisions:
                tache += " (%s)" % ", ".join(precisions)
            bouts.append("tâche " + tache)
        return " · ".join(bouts)

    points = []
    for e in data.get("employees") or []:
        for o in e.get("orders") or []:
            dette = 0 if o.get("is_aramex") else flt(sum(
                flt(p.get("amount")) for p in o.get("payments") or [] if p.get("mode") == "Dette non payée"), 3)
            if o.get("task_open"):
                points.append({
                    "cle": "tache_ouverte:%s" % o["sales_order"],
                    "type": "tache_ouverte", "bloquant": 0,
                    "commande": o["sales_order"], "tache": o.get("tache_reference"),
                    "client": o.get("customer"), "montant": dette,
                    "libelle": "Tâche ouverte — %s%s" % (
                        _qui(o), (" — %s DT en dette" % _fmt_montant(dette)) if dette else ""),
                })
            if o.get("tache_status") == "Completed":
                for dn in o.get("delivery_notes") or []:
                    if dn.get("docstatus") == 0:
                        points.append({
                            "cle": "bl_non_valide:%s" % dn["name"],
                            "type": "bl_non_valide", "bloquant": 1,
                            "commande": o["sales_order"], "bl": dn["name"],
                            "libelle": "Tâche terminée mais BL %s (%s DT) non validé — %s" % (
                                dn["name"], _fmt_montant(dn.get("grand_total")), _qui(o)),
                        })
            if dette and not o.get("task_open"):
                if o.get("is_validated") and o.get("tache_status") == "Completed":
                    libelle = "Commande validée, tâche terminée, mais %s DT en dette (hors Aramex) — %s" % (
                        _fmt_montant(dette), _qui(o))
                else:
                    situation = ("tâche annulée" if o.get("tache_status") == "Cancelled"
                                 else "commande sans tâche" if not o.get("tache_status")
                                 else "commande non validée")
                    libelle = "%s DT en dette (hors Aramex), %s — %s" % (_fmt_montant(dette), situation, _qui(o))
                points.append({
                    "cle": "dette_hors_aramex:%s" % o["sales_order"],
                    "type": "dette_hors_aramex", "bloquant": 0,
                    "commande": o["sales_order"], "tache": o.get("tache_reference"), "client": o.get("customer"),
                    "montant": dette, "libelle": libelle,
                })
    for pmt in (data.get("anciens") or {}).get("paiements") or []:
        if pmt.get("exclu"):
            points.append({
                "cle": "ancien_exclu:%s" % pmt["name"],
                "type": "ancien_exclu", "bloquant": 0,
                "paiement": pmt["name"], "client": pmt.get("customer_name"),
                "libelle": "Paiement %s (%s, %s DT) exclu de la caisse" % (
                    pmt["name"], pmt.get("customer_name") or "?", pmt.get("amount")),
            })
    return points


def _taches_sans_commande(caisse, date, debut=None):
    """Les tâches du jour ENCORE OUVERTES et sans commande (Autre, Visite, entretien dont la commande
    n'est pas encore créée…). Le rapport de caisse part des commandes : sans ce contrôle, une tâche
    sans commande restée ouverte ne se voyait jamais (05/10/2026). « Tous les employés » : toutes."""
    from customization_app.rapport_caisse_journaliere import COMPANY
    from customization_app.tournee_optimisation import TYPES_HORS_JOURNEE
    date = getdate(date)
    debut = getdate(debut) if debut else date
    filtre, valeurs = "", [COMPANY, "%s 00:00:00" % debut, "%s 23:59:59" % date, list(TYPES_HORS_JOURNEE)]
    if caisse != CAISSE_GLOBALE:
        filtre = "AND e.employee_name = %s"
        valeurs.append(caisse)
    exclus_u, exclus_e = _exclusions()
    points = []
    for t in frappe.db.sql(
            f"""SELECT tt.name, tt.custom_type_dintervention AS type_i, tt.subject, tt.starts_on,
                       COALESCE(NULLIF(tt.nom_client, ''), tt.custom_client) AS client,
                       e.name AS employee_id, e.employee_name, e.user_id
                FROM `tabTache de travail` tt INNER JOIN `tabEmployee` e ON e.name = tt.custom_choix_du_staff
                WHERE e.company = %s AND tt.status = 'Open' AND IFNULL(tt.commande_client, '') = ''
                  AND tt.starts_on BETWEEN %s AND %s AND IFNULL(tt.custom_type_dintervention, '') NOT IN %s {filtre}
                ORDER BY tt.starts_on""", tuple(valeurs), as_dict=True):
        if t.employee_id in exclus_e or (t.user_id or "") in exclus_u:
            continue
        objet = " ".join((t.subject or "").split())
        points.append({
            "cle": "tache_sans_commande:%s" % t.name, "type": "tache_sans_commande", "bloquant": 0,
            "tache": t.name, "client": t.client,
            "libelle": "Tâche ouverte sans commande — %s (%s, %s, %s)%s%s" % (
                t.name, t.type_i or "?", t.employee_name, str(t.starts_on)[11:16],
                (" · " + t.client) if t.client else "",
                (" · « %s »" % (objet[:60] + ("…" if len(objet) > 60 else ""))) if objet else ""),
        })
    return points


def libelle_machine_a_receptionner(m) -> str:
    """« Machine reprise chez X (OSM-…) le 23/11 à 12:52, pas encore réceptionnée au magasin ». PURE."""
    quand = str(m.get("reprise_le") or "")
    le = (" le %s/%s à %s" % (quand[8:10], quand[5:7], quand[11:16])) if len(quand) >= 16 else ""
    return "Machine reprise chez %s (%s)%s, pas encore réceptionnée au magasin — pendant %s %s" % (
        m.get("client") or "le client", m.get("machine"), le, (m.get("type_i") or "l'intervention").lower(), m.get("tache") or "")


def _machines_a_receptionner(caisse, date):
    """Le technicien est reparti de chez un client avec une machine (Ma journée › « Machine reprise ») : il ne compte
    pas sa caisse tant qu'elle n'est pas RÉCEPTIONNÉE au magasin (demande du 23/11/2026 : « il l'oblige à faire la
    réception de la machine avant de fermer sa caisse »). BLOQUANT, comme un BL en brouillon. La caisse globale n'est
    pas concernée : elle n'est celle d'aucun technicien."""
    if caisse == CAISSE_GLOBALE:
        return []
    try:
        from customization_app import reparation_osmoseur as RO
        machines = RO.machines_a_receptionner(caisse, date)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "caisse : machines reprises à réceptionner")
        return []
    return [{"cle": "machine_a_receptionner:%s" % m.machine, "type": "machine_a_receptionner", "bloquant": 1,
             "machine": m.machine, "tache": m.tache, "client": m.client,
             "libelle": libelle_machine_a_receptionner(m)} for m in machines]


def message_bloquants(points) -> str:
    """Ce qui empêche de compter la caisse, dit en clair. PURE."""
    bls = [p["bl"] for p in points if p.get("bloquant") and p.get("bl")]
    machines = [p["machine"] for p in points if p.get("bloquant") and p.get("machine")]
    bouts = []
    if bls:
        bouts.append("bon(s) de livraison à valider d'abord : %s" % ", ".join(bls))
    if machines:
        bouts.append("machine(s) reprise(s) à réceptionner au magasin d'abord : %s" % ", ".join(machines))
    return "Validation refusée — " + " ; ".join(bouts or ["un point bloquant reste à régler"])


def motifs_du_point(point):
    famille = FAMILLE_DU_POINT.get(point.get("type"))
    return MOTIFS[famille] if famille else ["Autre"]


def _depenses_collegues(data):
    """Les dépenses qu'un COLLÈGUE a saisies en les déclarant payées de cette caisse : le titulaire les confirme
    à son comptage (24/11/2026). Fonction pure sur le rapport filtré sur la caisse."""
    points = []
    for l in ((data or {}).get("depenses") or {}).get("lignes") or []:
        if not l.get("payee_par_collegue"):
            continue
        cle = "paiement" if l.get("doctype") == "Payment Entry" else "ecriture"
        points.append({
            "cle": "depense_collegue:%s" % l["name"], "type": "depense_collegue", "bloquant": 0, cle: l["name"],
            "montant": flt(l.get("montant"), 3),
            "libelle": "Dépense saisie par %s, payée de votre caisse — %s — %s DT (%s, %s)" % (
                l.get("saisi_par") or "?", l.get("description") or l.get("type") or "", _fmt_montant(l.get("montant")),
                l.get("mode") or "", l["name"]),
        })
    return points


def points_controle(caisse, date, data, debut=None):
    """Tous les points de la clôture de cette caisse, chacun avec la liste de ses motifs."""
    from customization_app import caisse_collecte as CC
    points = _machines_a_receptionner(caisse, date) + _controles(data) + _taches_sans_commande(caisse, date, debut)
    if caisse != CAISSE_GLOBALE:
        points += _depenses_collegues(data)
    if caisse == CAISSE_GLOBALE:
        points += CC.controles_globale(date, data)
    for p in points:
        if not p.get("bloquant"):
            p["motifs"] = motifs_du_point(p)
    return points


def verifier_justifications(points, justifs):
    """-> (texte à stocker, erreurs). Chaque point non bloquant attend {"motif": un motif de SA liste,
    "commentaire": au moins COMMENTAIRE_MIN caractères}. Le texte garde le format historique
    (« libellé\\n  → motif — commentaire ») que relisent le PDF et la réouverture. Fonction pure."""
    lignes, erreurs = [], []
    for p in points:
        if p.get("bloquant"):
            continue
        j = (justifs or {}).get(p["cle"])
        if not isinstance(j, dict):
            # Une chaîne = l'ancienne page encore en cache dans le navigateur.
            erreurs.append("%s : choisissez un motif%s" % (
                p["libelle"], " (rechargez la page : Ctrl+Maj+R)" if isinstance(j, str) and j.strip() else ""))
            continue
        motif = (j.get("motif") or "").strip()
        commentaire = " ".join(str(j.get("commentaire") or "").split())
        if motif not in (p.get("motifs") or motifs_du_point(p)):
            erreurs.append("%s : choisissez un motif" % p["libelle"])
        elif len(commentaire) < COMMENTAIRE_MIN and motif not in MOTIFS_SANS_COMMENTAIRE:
            erreurs.append("%s : commentaire trop court (%s caractères minimum)" % (p["libelle"], COMMENTAIRE_MIN))
        else:
            lignes.append("%s\n  → %s — %s" % (p["libelle"], motif, commentaire))
    return "\n".join(lignes), erreurs


# ── Justifications recopiées sur les documents liés (06/10/2026) ─────────────
# Chaque point justifié devient un COMMENTAIRE sur sa commande, sa tâche ou son paiement : le
# responsable de collecte (et Salma, pastille 💬 de la commande) le lit sur la fiche elle-même.
# Le nom de la clôture entre parenthèses sert de marque : rouvrir ou annuler le comptage les remplace.
MARQUE_COMMENTAIRE = "💵 Caisse du "
DOCTYPES_DU_POINT = (("commande", "Sales Order"), ("tache", "Tache de travail"), ("paiement", "Payment Entry"),
                     ("ecriture", "Journal Entry"))
NATURE_DU_POINT = {"tache_ouverte": "Tâche ouverte", "tache_sans_commande": "Tâche ouverte sans commande",
                   "dette_hors_aramex": "Dette hors Aramex", "ancien_exclu": "Paiement exclu de la caisse",
                   "depense_collegue": "Dépense payée pour un collègue"}


def documents_du_point(point):
    """[(doctype, nom)] des documents qu'un point de contrôle concerne. Fonction pure."""
    return [(dt, point[cle]) for cle, dt in DOCTYPES_DU_POINT if point.get(cle)]


def texte_commentaire(point, justif, cloture, caisse, date, auteur):
    """Le commentaire posé sur la commande / la tâche : la justification d'abord (c'est elle que la
    pastille 💬 de la commande montre), puis qui l'a écrite et dans quelle clôture. Fonction pure."""
    esc = frappe.utils.escape_html
    nature = NATURE_DU_POINT.get(point.get("type"), point.get("type") or "Point de contrôle")
    if point.get("montant"):
        nature += " (%s DT)" % _fmt_montant(point["montant"])
    commentaire = " ".join(str(justif.get("commentaire") or "").split())
    return "%s%s — %s : <b>%s</b> — %s<br><small>Justifié par %s au comptage de la caisse %s (%s)</small>" % (
        MARQUE_COMMENTAIRE, getdate(date).strftime("%d/%m/%Y"), esc(nature), esc((justif.get("motif") or "").strip()),
        esc(commentaire), esc(auteur), esc(caisse), cloture)


def retirer_justifications(cloture):
    """Supprime les commentaires posés par cette clôture (réouverture, annulation du comptage)."""
    noms = frappe.get_all("Comment", filters={
        "comment_type": "Comment", "reference_doctype": ["in", [dt for _c, dt in DOCTYPES_DU_POINT]],
        "content": ["like", "%%(%s)%%" % cloture]}, fields=["name", "content"])
    for c in noms:
        if (c.content or "").startswith(MARQUE_COMMENTAIRE):
            frappe.delete_doc("Comment", c.name, ignore_permissions=True, force=True)
    return len(noms)


def publier_justifications(doc, points, justifs):
    """Un commentaire par document lié à chaque point justifié. Jamais bloquant : un document
    disparu ne doit pas empêcher de compter la caisse. La caisse globale (direction) ne publie pas :
    elle reprend les points de toutes les caisses, déjà publiés par chaque employé."""
    if doc.caisse == CAISSE_GLOBALE:
        return 0
    auteur = frappe.utils.get_fullname(frappe.session.user) or frappe.session.user
    n = 0
    for p in points:
        j = (justifs or {}).get(p.get("cle"))
        if p.get("bloquant") or not isinstance(j, dict):
            continue
        texte = texte_commentaire(p, j, doc.name, doc.caisse, doc.date_cloture, auteur)
        for doctype, nom in documents_du_point(p):
            if not frappe.db.exists(doctype, nom):
                continue
            try:
                frappe.get_doc({"doctype": "Comment", "comment_type": "Comment", "reference_doctype": doctype,
                                "reference_name": nom, "content": texte, "comment_email": frappe.session.user,
                                "comment_by": auteur}).insert(ignore_permissions=True)
                n += 1
            except Exception:
                frappe.log_error(frappe.get_traceback(), "caisse: commentaire %s %s" % (doctype, nom))
    return n


@frappe.whitelist()
def valider_bl(bl):
    """Valide un bon de livraison en brouillon depuis le contrôle de clôture —
    le droit demandé : même geste que le magasin (workflow « Validation
    Magasin » : Approved puis soumission)."""
    frappe.only_for(ROLES)
    doc = frappe.get_doc("Delivery Note", bl)
    if doc.docstatus == 1:
        return {"bl": bl, "deja": True}
    if doc.docstatus != 0:
        frappe.throw(_("Le BL {0} est annulé — rien à valider.").format(bl))
    if doc.get("workflow_state") is not None:
        doc.db_set("workflow_state", "Approved", update_modified=False)
        doc.reload()
    frappe.flags.in_import = True
    try:
        doc.submit()
    finally:
        frappe.flags.in_import = False
    frappe.db.commit()
    return {"bl": bl, "valide": True}


@frappe.whitelist()
def periode_sous_traitant(date=None):
    """La page Caisse d'un sous-traitant s'ouvre sur SA période : du lendemain de sa dernière remise à
    aujourd'hui (il remet chaque semaine). None pour tout autre utilisateur."""
    try:
        from customization_app.sous_traitant import sous_traitant_courant
        emp = sous_traitant_courant()
    except Exception:
        emp = None
    if not emp:
        return None
    fin = getdate(date) if date else getdate(nowdate())
    return {"caisse": emp.employee_name, "debut": str(debut_periode(emp.employee_name, fin)), "fin": str(fin)}


def compte_pour_autrui(caisse, user=None):
    """Le nom du titulaire quand on compte la caisse d'un AUTRE (24/11/2026), None sinon. La caisse globale et celles
    des sous-traitants (comptées par leur responsable, par conception) ne sont pas concernées."""
    user = user or frappe.session.user
    if not caisse or caisse == CAISSE_GLOBALE or _est_caisse_sous_traitant(caisse):
        return None
    from customization_app import caisse_collecte as CC
    return None if CC._caisse_de(user) == caisse else caisse


def _prevenir_titulaire(doc, titulaire):
    """Le titulaire apprend qu'on a compté sa caisse à sa place : il peut contester tant qu'elle n'est pas collectée."""
    user = frappe.db.get_value("Employee", {"employee_name": titulaire, "status": "Active"}, "user_id")
    if not user or user == frappe.session.user:
        return
    texte = _("💵 {0} a compté votre caisse du {1} à votre place : {2} DT comptés, {3} DT remis — « {4} »").format(
        frappe.utils.get_fullname(frappe.session.user), getdate(doc.date_cloture).strftime("%d/%m/%Y"),
        _fmt_montant(doc.especes_comptees), _fmt_montant(doc.especes_remises), (doc.note or "").strip())
    try:
        frappe.get_doc({"doctype": "Notification Log", "for_user": user, "type": "Alert", "subject": texte,
                        "document_type": "Cloture Caisse", "document_name": doc.name}).insert(ignore_permissions=True)
        if frappe.db.exists("DocType", "PWA Notification"):
            frappe.get_doc({"doctype": "PWA Notification", "from_user": frappe.session.user, "to_user": user,
                            "message": texte, "reference_document_type": "Cloture Caisse",
                            "reference_document_name": doc.name}).insert(ignore_permissions=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Caisse : prévenir le titulaire")


@frappe.whitelist()
def etat(caisse, date):
    """L'état de la caisse pour le dialogue : avant, mouvements, après."""
    frappe.only_for(ROLES)
    caisse = (caisse or "").strip() or CAISSE_GLOBALE
    _controler_droits(caisse)
    date = getdate(date)
    m = _mesures(caisse, date)
    ouverture = _ouverture(caisse, date)
    theorique = flt(ouverture + m["encaissements_especes"] - m["depenses_especes"], 3)
    deja = frappe.db.get_value("Cloture Caisse",
                               {"caisse": caisse, "date_cloture": date, "docstatus": 1})
    pdf_url = None
    if deja:
        pdf_url = frappe.db.get_value(
            "File", {"attached_to_doctype": "Cloture Caisse", "attached_to_name": deja,
                     "file_name": ["like", "%.pdf"]}, "file_url")
    from customization_app import caisse_collecte as CC
    en_attente = frappe.db.get_value(
        "Cloture Caisse", {"caisse": caisse, "date_cloture": date, "docstatus": 0,
                           "statut": ["in", CC.STATUTS_EN_ATTENTE]},
        ["name", "statut", "tours_ecart", "especes_remises", "especes_recues", "collecte_par", "valide_par",
         "especes_comptees", "nb_cheques_remis", "nb_traites_remis", "note", "controles"], as_dict=True)
    if en_attente:
        # Réouverture (04/10/2026) : tant que le responsable n'a pas confirmé la réception, l'employé
        # peut rouvrir et modifier son comptage — le dialogue se pré-remplit avec ses valeurs.
        en_attente["caisse"] = caisse
        en_attente["mienne"] = CC.peut_agir_sur(en_attente)
        en_attente["rouvrable"] = en_attente["mienne"]
        en_attente["justifications_precedentes"] = CC.justifications_depuis_controles(en_attente.get("controles"))
    points = points_controle(caisse, date, m["data"], m["debut"])
    return {
        "caisse": caisse, "date": str(date), "debut": m["debut"],
        "controles": points,
        "en_attente": en_attente,
        "mode_validation": CC.mode_validation(caisse),
        "photo_obligatoire": CC.config()["photo_obligatoire"],
        # Comptée à la place du titulaire (24/11/2026) : motif écrit + photo des espèces obligatoires.
        "pour_autrui": compte_pour_autrui(caisse),
        "commentaire_min": COMMENTAIRE_MIN,
        "date_depart": CC.date_depart_pour(caisse, CC.config()),
        "solde_ouverture": ouverture,
        "encaissements_especes": m["encaissements_especes"],
        "depenses_especes": m["depenses_especes"],
        "solde_theorique": theorique,
        "total_cheques": m["total_cheques"],
        "total_autres_modes": m["total_autres_modes"],
        "deja_validee": deja,
        "pdf_url": pdf_url,
        # Le rapprochement chèques/traites/dettes ne vaut que pour la GLOBALE :
        # le portefeuille est physique et commun, l'encours dettes est comptable.
        "rapprochement": (_rapprochement(date) if caisse == CAISSE_GLOBALE else None),
    }


@frappe.whitelist()
def valider(caisse, date, especes_comptees=None, note=None, justifications=None,
            especes_remises=None, nb_cheques_remis=0, nb_traites_remis=0, photo=None, photo_nom=None,
            rouvrir=None):
    """L'employé COMPTE et REMET (double validation, 04/10/2026) : la clôture
    naît en brouillon « À collecter » et c'est le responsable de collecte qui la
    soumet (caisse_collecte.collecter). Validation SEULE (soumise ici, PDF
    attaché) : la caisse du responsable de collecte et la caisse globale."""
    frappe.only_for(ROLES)
    caisse = (caisse or "").strip() or CAISSE_GLOBALE
    _controler_droits(caisse)
    date = getdate(date)
    if frappe.db.get_value("Cloture Caisse",
                           {"caisse": caisse, "date_cloture": date, "docstatus": 1}):
        frappe.throw(_("La caisse « {0} » du {1} est déjà validée.").format(caisse, date))
    from customization_app import caisse_collecte as CC
    attente = frappe.db.get_value("Cloture Caisse",
                                  {"caisse": caisse, "date_cloture": date, "docstatus": 0,
                                   "statut": ["in", CC.STATUTS_EN_ATTENTE]})
    existante = None
    if attente and rouvrir and rouvrir == attente:
        # Réouverture : on MODIFIE le brouillon (même numéro), tant qu'il n'est pas collecté.
        existante = frappe.get_doc("Cloture Caisse", attente)
        if not CC.peut_agir_sur(existante):
            frappe.throw(_("Seul {0} (ou le titulaire de la caisse) peut rouvrir ce comptage.").format(existante.valide_par))
    elif attente:
        frappe.throw(_("La caisse « {0} » du {1} est déjà comptée ({2}) : elle attend la collecte.")
                     .format(caisse, date, attente))
    elif rouvrir:
        frappe.throw(_("Le comptage {0} n'est plus modifiable : il a été collecté ou n'existe plus.").format(rouvrir))
    mode = CC.mode_validation(caisse)

    m = _mesures(caisse, date)

    # Les contrôles sont REJOUÉS côté serveur : un BL encore en brouillon bloque,
    # chaque autre point exige un motif et un commentaire.
    import json as _json
    justifs = (_json.loads(justifications) if isinstance(justifications, str)
               else (justifications or {}))
    points = points_controle(caisse, date, m["data"], m["debut"])
    bloquants = [p for p in points if p["bloquant"]]
    if bloquants:
        frappe.throw(message_bloquants(bloquants))
    controles_txt, erreurs = verifier_justifications(points, justifs)
    if erreurs:
        frappe.throw(_("Justification incomplète :<br>• {0}")
                     .format("<br>• ".join(frappe.utils.escape_html(x) for x in erreurs)))

    ouverture = _ouverture(caisse, date)
    theorique = flt(ouverture + m["encaissements_especes"] - m["depenses_especes"], 3)
    comptees = flt(especes_comptees, 3) if especes_comptees not in (None, "") else None
    remises = flt(especes_remises, 3) if especes_remises not in (None, "") else None
    if comptees is not None and comptees < 0:
        # Un solde théorique négatif (dépense d'un collègue saisie ici) rendait le dialogue inutilisable :
        # pré-rempli à −76 DT, la remise négative était refusée sans explication (Hedi, 09/11/2026).
        frappe.throw(_("Les espèces comptées ne peuvent pas être négatives : comptez ce qu'il y a "
                       "réellement dans la caisse (0 si rien) ; l'écart s'explique dans la note."))
    if mode == "double":
        try:
            CC.controler_remise(comptees, remises)
        except ValueError as exc:
            frappe.throw(str(exc))
        if CC.config()["photo_obligatoire"] and not photo:
            frappe.throw(_("La photo de la remise est obligatoire (Config Caisse)."))
    # DEUX PERSONNES (24/11/2026) : compter la caisse d'un autre se justifie et se photographie. Le 20/11, la caisse
    # de Sadok du 16/11 a été comptée ET validée en 24 secondes par le collecteur, au montant attendu, sans rien
    # qui dise pourquoi ni ce qu'il y avait vraiment.
    autrui = compte_pour_autrui(caisse)
    if autrui:
        if len(" ".join((note or "").split())) < COMMENTAIRE_MIN:
            frappe.throw(_("Vous comptez la caisse de {0} à sa place : écrivez pourquoi dans la note (absent, parti…, "
                           "{1} caractères minimum).").format(autrui, COMMENTAIRE_MIN))
        if not photo and not existante:
            frappe.throw(_("Vous comptez la caisse de {0} à sa place : la photo des espèces comptées est obligatoire.")
                         .format(autrui))
    else:
        remises = 0.0

    rap = _rapprochement(date) if caisse == CAISSE_GLOBALE else None
    champs_rap = {}
    if rap:
        champs_rap = {
            "cheques_avant": rap["cheques"]["avant"], "cheques_recus": rap["cheques"]["recus"],
            "cheques_remis": rap["cheques"]["remis"], "cheques_apres": rap["cheques"]["apres"],
            "traites_avant": rap["traites"]["avant"], "traites_recues": rap["traites"]["recus"],
            "traites_remises": rap["traites"]["remis"], "traites_apres": rap["traites"]["apres"],
            "dettes_avant": rap["dettes"]["avant"], "dettes_apres": rap["dettes"]["apres"],
        }

    valeurs = {
        "caisse": caisse,
        "date_cloture": date,
        "valide_par": frappe.session.user,
        "compte_le": now_datetime(),
        "statut": CC.STATUT_A_COLLECTER,
        "especes_remises": remises,
        "fond_conserve": (CC.fond_conserve(comptees, remises) if comptees is not None else None),
        "nb_cheques_remis": frappe.utils.cint(nb_cheques_remis),
        "nb_traites_remis": frappe.utils.cint(nb_traites_remis),
        "solde_ouverture": ouverture,
        "encaissements_especes": m["encaissements_especes"],
        "depenses_especes": m["depenses_especes"],
        "solde_theorique": theorique,
        "especes_comptees": comptees,
        "ecart": (flt(comptees - theorique, 3) if comptees is not None else 0),
        "total_cheques": m["total_cheques"],
        "total_autres_modes": m["total_autres_modes"],
        "note": (note or "").strip(),
        "controles": controles_txt,
        **champs_rap,
    }
    justifs_avant = existante.controles if existante else None
    if existante:
        doc = CC.rouvrir_comptage(existante, valeurs)
    else:
        doc = frappe.get_doc({"doctype": "Cloture Caisse", **valeurs})
        doc.insert(ignore_permissions=True)
    CC.enregistrer_photo_remise(doc, photo, photo_nom)
    # Les justifications vont aussi sur les commandes / tâches concernées (06/10/2026) ; à la
    # réouverture, seulement si elles ont changé (sinon la pastille 💬 changerait de date pour rien).
    if not existante or (justifs_avant or "") != (controles_txt or ""):
        if existante:
            retirer_justifications(doc.name)
        publier_justifications(doc, points, justifs)

    if mode == "seule":
        CC.finaliser(doc, m, rap, validation_seule=True)
    else:
        CC.notifier_collecteurs(doc, rouverte=bool(existante))
    if autrui:
        _prevenir_titulaire(doc, autrui)
    frappe.db.commit()
    return {"name": doc.name, "statut": doc.statut, "mode": mode, "rouverte": bool(existante)}


# Le PDF de clôture (refondu le 24/11/2026 : « améliore le rendu pdf des caisses ») — en-tête de l'entreprise, verdict
# en tête, noms au lieu des e-mails, dates jj/mm/aaaa, montants « 1 315,500 DT » (le symbole د.ت renversait les
# cellules), une ligne par commande, points de contrôle lisibles, validations et pagination en pied.
OPTIONS_PDF = {"margin-top": "10mm", "margin-bottom": "16mm", "margin-left": "12mm", "margin-right": "12mm"}

# Les couleurs des modes de la page « Rapport Caisse Journalière » (RCJ_MODE), pour retrouver les mêmes pastilles.
COULEURS_MODES = {
    "Espèces": ("#d4edda", "#155724"),
    "Chèque": ("#cce5ff", "#004085"),
    "Virement": ("#d1f2f0", "#0c5460"),
    "Carte de crédit": ("#dfe2ff", "#2b2f77"),
    "Traite bancaire LC": ("#e2d4f0", "#4a235a"),
    "Dette non payée": ("#f8d7da", "#721c24"),
    "Retenue a la source vente": ("#fff3cd", "#856404"),
    "Perte de paiement": ("#f5c6cb", "#491217"),
}
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
STATUTS_TACHE = {"Open": "ouverte", "Completed": "terminée", "Cancelled": "annulée"}
STATUTS_COMMANDE = {"Draft": "Brouillon", "To Deliver and Bill": "À livrer et facturer", "To Bill": "À facturer",
                    "To Deliver": "À livrer", "Completed": "Terminé", "Cancelled": "Annulé", "Closed": "Fermé",
                    "On Hold": "En attente"}
RE_COMMANDE = re.compile(r"SAL-ORD-\d{4}-\d+")
RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Emojis des commentaires (❌ 🔁 📞…) : la police du PDF ne les a pas, wkhtmltopdf dessine des carrés.
RE_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2700-\u27BF\uFE0F\u200D]")
TRACE_LONGUEUR_MAX = 240


def points_du_texte(controles):
    """Le texte des justifications (verifier_justifications : « libellé » puis « → motif — commentaire ») relu en
    [(libellé, [(motif, commentaire)])]. Fonction pure."""
    points = []
    for ligne in (controles or "").splitlines():
        s = ligne.strip()
        if s.startswith("→") and points:
            motif, _sep, commentaire = s[1:].strip().partition(" — ")
            points[-1][1].append((motif, commentaire))
        elif s:
            points.append((s, []))
    return points


def _texte_trace(contenu, noms):
    """Un commentaire du fil d'une pièce, en une ligne lisible : sans HTML, e-mails remplacés par les noms."""
    texte = RE_EMOJI.sub("", unescape(frappe.utils.strip_html(contenu or ""))).replace("د.ت", "DT")
    texte = RE_EMAIL.sub(lambda m: noms(m.group(0)), " ".join(texte.split()))
    return texte if len(texte) <= TRACE_LONGUEUR_MAX else texte[:TRACE_LONGUEUR_MAX - 1].rstrip() + "…"


def _traces(orders, debut, fin, date_cloture):
    """La traçabilité de chaque commande de la caisse (demande du 24/11/2026 : « la traçabilité sur le pdf, chaque
    intervention ») : {commande: [(moment, qui, texte)]} dans l'ordre, de `debut` (la journée) à `fin` (la
    collecte). Commande créée (toujours, même avant la journée), statuts, paiements saisis, tâche (créée, départ
    GPS, terminée), BL, commentaires et infos de la commande et de sa tâche. Une requête par doctype."""
    sos = [o["sales_order"] for o in orders if o.get("sales_order")]
    if not sos:
        return {}
    taches = {o["tache_reference"]: o["sales_order"] for o in orders if o.get("tache_reference")}
    paiements, bls = {}, {}
    for o in orders:
        for p in o.get("payments") or []:
            if p.get("name"):
                paiements.setdefault(p["name"], []).append((o["sales_order"], p))
        for dn in o.get("delivery_notes") or []:
            if dn.get("name"):
                bls[dn["name"]] = o["sales_order"]
    cache = {}

    def nom(user):
        if not user:
            return "?"
        if user not in cache:
            cache[user] = (frappe.utils.get_fullname(user) or user) if ("@" in user or user == "Administrator") else user
        return cache[user]

    def dans(moment):
        return bool(moment) and debut <= get_datetime(moment) <= fin

    ev = {so: [] for so in sos}
    for r in frappe.db.sql("select name, owner, creation from `tabSales Order` where name in %(n)s",
                           {"n": sos}, as_dict=True):
        ev[r.name].append((get_datetime(r.creation), nom(r.owner), "commande créée"))
    if paiements:
        for r in frappe.db.sql("select name, owner, creation from `tabPayment Entry` where name in %(n)s",
                               {"n": list(paiements)}, as_dict=True):
            if not dans(r.creation):
                continue
            for so, p in paiements[r.name]:
                ref = (p.get("reference_no") or "").strip()
                ev[so].append((get_datetime(r.creation), nom(r.owner), "paiement %s %s DT saisi · %s%s" % (
                    p.get("mode") or "", _fmt_montant(p.get("amount")), r.name,
                    (" · réf. %s" % ref) if ref and ref != so else "")))
    if taches:
        for r in frappe.db.sql("""select name, owner, creation, status, modified, modified_by, custom_gps_depart,
                                         `custom_employé` as employe
                                  from `tabTache de travail` where name in %(n)s""", {"n": list(taches)}, as_dict=True):
            so = taches[r.name]
            if dans(r.creation):
                ev[so].append((get_datetime(r.creation), nom(r.owner), "tâche %s créée%s" % (
                    r.name, (" pour %s" % r.employe) if r.employe else "")))
            if dans(r.custom_gps_depart):
                ev[so].append((get_datetime(r.custom_gps_depart), r.employe or "?", "départ vers le client (GPS)"))
            if r.status in ("Completed", "Cancelled") and dans(r.modified):
                ev[so].append((get_datetime(r.modified), nom(r.modified_by), "tâche %s %s" % (
                    r.name, STATUTS_TACHE[r.status])))
    if bls:
        for r in frappe.db.sql("""select name, owner, creation, docstatus, modified, modified_by
                                  from `tabDelivery Note` where name in %(n)s""", {"n": list(bls)}, as_dict=True):
            so = bls[r.name]
            if r.docstatus == 1 and dans(r.modified) and dans(r.creation) and r.modified_by == r.owner:
                ev[so].append((get_datetime(r.modified), nom(r.owner), "BL %s créé et validé" % r.name))
                continue
            if dans(r.creation):
                ev[so].append((get_datetime(r.creation), nom(r.owner), "BL %s créé" % r.name))
            if r.docstatus == 1 and dans(r.modified):
                ev[so].append((get_datetime(r.modified), nom(r.modified_by), "BL %s validé" % r.name))
    # Les justifications de CETTE clôture (« 💵 Caisse du jj/mm/aaaa — … ») sont affichées à part, sous la commande.
    propre = "💵 Caisse du %s" % getdate(date_cloture).strftime("%d/%m/%Y")
    vus = set()
    for c in frappe.db.sql("""select reference_doctype, reference_name, comment_type, owner, creation, content
                              from `tabComment`
                              where comment_type in ('Comment', 'Info', 'Label')
                                and ((reference_doctype = 'Sales Order' and reference_name in %(sos)s)
                                     or (reference_doctype = 'Tache de travail' and reference_name in %(taches)s))
                                and creation between %(debut)s and %(fin)s
                              order by creation""",
                           {"sos": sos, "taches": list(taches) or [""], "debut": debut, "fin": fin}, as_dict=True):
        so = c.reference_name if c.reference_doctype == "Sales Order" else taches.get(c.reference_name)
        if not so:
            continue
        if c.comment_type == "Label":
            statut = STATUTS_COMMANDE.get((c.content or "").strip(), (c.content or "").strip())
            derniers = [t for _m, _q, t in ev[so] if t.startswith("statut → ")]
            if derniers and derniers[-1] == "statut → " + statut:
                continue
            ev[so].append((get_datetime(c.creation), nom(c.owner), "statut → " + statut))
            continue
        texte = _texte_trace(c.content, nom)
        if not texte or texte.startswith(propre):
            continue
        cle = (so, texte)
        if cle in vus:                      # même commentaire recopié sur la commande ET sur sa tâche
            continue
        vus.add(cle)
        ev[so].append((get_datetime(c.creation), nom(c.owner), ("« %s »" % texte) if c.comment_type == "Comment"
                       else texte))
    return {so: sorted(lignes, key=lambda x: x[0]) for so, lignes in ev.items()}

# Le <style> reste HORS formatage % : le CSS est truffé de « % » littéraux.
CSS_PDF = """
<style>
  body { font-family: "Noto Sans", "DejaVu Sans", sans-serif; font-size: 10.5px; color: #222; }
  table { border-collapse: collapse; width: 100%; font-size: inherit; }
  thead { display: table-header-group; }
  tr { page-break-inside: avoid; }
  .n { text-align: right; white-space: nowrap; }
  .nw { white-space: nowrap; }
  .gris { color: #777; }
  .petit { font-size: 9px; }
  .entete td { vertical-align: middle; }
  .entete .titre { text-align: right; }
  .entete .titre .t1 { font-size: 24px; font-weight: bold; letter-spacing: 1.2px; color: #0b3d7a; line-height: 1.1; }
  .entete .titre .t2 { font-size: 14px; margin-top: 6px; }
  .entete .titre .t3 { font-size: 11px; color: #555; margin-top: 2px; }
  .verdict { margin: 12px 0 10px; padding: 9px 12px; border-radius: 6px; border: 1px solid; }
  .verdict .v1 { font-size: 14px; font-weight: bold; }
  .verdict .v2 { margin-top: 3px; font-size: 10.5px; }
  .ok { background: #eaf6ee; border-color: #9fd3ad; color: #155724; }
  .ko { background: #fdecec; border-color: #f1a9a9; color: #8a1f1f; }
  .neutre { background: #f3f5f8; border-color: #cfd6df; color: #333; }
  .deux > tbody > tr > td, .deux > tr > td { vertical-align: top; width: 50%; padding: 0; }
  .bloc { border: 1px solid #d5dbe3; border-radius: 6px; padding: 7px 10px; }
  .bloc.bleu { background: #f2f7fd; border-color: #c9dcf2; }
  .bloc.gris { background: #f7f7f7; border-color: #dcdcdc; color: #222; }
  .bloc h3 { margin: 0 0 4px; font-size: 11.5px; color: #0b3d7a; }
  .bloc td { padding: 2.5px 0; border: none; }
  .bloc tr.total td { border-top: 1px solid #b9c6d6; font-weight: bold; padding-top: 4px; }
  .bloc tr.fort td { font-weight: bold; }
  .ecart-ok { color: #1d6f42; } .ecart-ko { color: #c0392b; }
  h2 { font-size: 12.5px; color: #0b3d7a; margin: 16px 0 5px; padding-bottom: 3px; border-bottom: 2px solid #0b3d7a; }
  h2 .droite { float: right; font-weight: normal; color: #555; font-size: 10.5px; }
  .liste { table-layout: fixed; }
  .liste th { background: #eef2f7; color: #334; font-weight: 600; text-align: left; padding: 4px 6px;
    border-bottom: 1px solid #c9d3df; font-size: 9.5px; }
  .liste td { padding: 4px 6px; border-bottom: 1px solid #e6e9ee; vertical-align: top; }
  .liste th.n { text-align: right; }
  .liste tr.pied td { font-weight: bold; border-top: 1.5px solid #9aa8b8; border-bottom: none; }
  .liste tr.alerte td { border-bottom: 1px solid #e6e9ee; padding-top: 0; }
  .liste tr.sans-trait td { border-bottom: none; }
  .liste tr.exclu td { color: #999; text-decoration: line-through; }
  .pastille { display: inline-block; padding: 1px 6px; border-radius: 9px; font-size: 9px; font-weight: 600;
    white-space: nowrap; }
  .pmt + .pmt { margin-top: 3px; }
  .warn { color: #a93226; font-size: 9.5px; }
  .flag { color: #7a5d10; font-size: 9px; }
  .modes td { width: 25%; padding: 5px 8px; border: 1px solid #e1e5eb; vertical-align: top; }
  .modes .mt { font-size: 12px; font-weight: bold; margin-top: 3px; white-space: nowrap; }
  .modes .tot { background: #f2f7fd; }
  .garde { page-break-inside: avoid; }
  .inter { border: 1px solid #dfe4ea; border-left: 3px solid #0b3d7a; padding: 6px 9px;
    margin: 0 0 7px; page-break-inside: avoid; }
  .inter.alerte { border-left-color: #c0392b; }
  .itete td { padding: 0; vertical-align: top; }
  .ipmts { margin-top: 4px; }
  .ipmts .p { display: block; margin-top: 2px; }
  .garde .inter { page-break-inside: auto; }   /* imbriqué dans un bloc insécable : wkhtmltopdf trace des traits */
  .ijustif { margin-top: 5px; background: #fff8e1; border: 1px solid #f3dc9a; border-radius: 4px; padding: 4px 7px; }
  .trace { margin-top: 5px; table-layout: fixed; }
  .trace td { padding: 2px 4px 2px 0; border-top: 1px dotted #d5dbe3; font-size: 9.5px; vertical-align: top; }
  .trace td.h { width: 12%; color: #555; white-space: nowrap; }
  .trace td.qui { width: 19%; color: #0b3d7a; }
  .point { padding: 5px 0 6px; border-bottom: 1px solid #e6e9ee; page-break-inside: avoid; }
  .point .rep { margin-top: 2px; color: #333; }
  .motif { display: inline-block; background: #eef2f7; border: 1px solid #d5dbe3; border-radius: 9px;
    padding: 0 6px; font-size: 9.5px; }
  .texte { white-space: pre-wrap; background: #fafafa; border: 1px solid #e6e9ee; border-radius: 6px;
    padding: 6px 9px; }
  .signatures { margin-top: 18px; page-break-inside: avoid; }
  .signatures td { width: 50%; vertical-align: top; padding: 0; }
  .signatures .bloc { min-height: 48px; }
  .signatures .qui { font-size: 12px; font-weight: bold; margin-top: 2px; }
</style>"""


def _html_instantane(doc, data, rap=None):
    """Le PDF FIGÉ de la clôture = le rapport COMPLET du jour (décision
    utilisateur 19/08) : état espèces, rapprochement (globale), récap par mode
    et par employé, DÉTAIL par employé (commandes, paiements, avertissements,
    avances), paiements sur anciennes commandes, dépenses, portefeuille
    chèques/traites restant, contrôles justifiés et note."""
    esc = frappe.utils.escape_html
    recap = data.get("recap") or {}
    depenses = data.get("depenses") or {}
    globale = doc.caisse == CAISSE_GLOBALE
    seule = bool(doc.get("validation_seule"))

    def mt(v):
        return _fmt_montant(v)

    def dt_(v):
        return "<span class='nw'>%s&nbsp;DT</span>" % mt(v)

    def jour(v):
        return getdate(v).strftime("%d/%m/%Y") if v else ""

    def heure(v):
        if not v:
            return ""
        v = get_datetime(v)
        return "%s à %s" % (v.strftime("%d/%m/%Y"), v.strftime("%H:%M"))

    def nom(user):
        if user and "@" in str(user):
            return frappe.utils.get_fullname(user) or user
        return user or "?"

    def pastille(mode):
        fond, texte = COULEURS_MODES.get(mode or "", ("#eceff3", "#333"))
        return "<span class='pastille' style='background:%s;color:%s'>%s</span>" % (fond, texte, esc(mode or "—"))

    def ecart_txt(v):
        v = flt(v, 3)
        if abs(v) < 0.0005:
            return "<span class='ecart-ok'>0,000&nbsp;DT</span>"
        return "<span class='ecart-ko'>%s%s&nbsp;DT</span>" % ("+" if v > 0 else "−", mt(abs(v)))

    def section(titre, colonnes, unites, vide="aucun"):
        """Un titre et son tableau. Le titre part avec la première ligne (jamais seul en bas de page) ; la suite est un
        second tableau aux mêmes largeurs de colonnes (disposition fixe), libre de se couper entre deux pages."""
        cols = "<colgroup>%s</colgroup>" % "".join("<col style='width:%s%%'>" % c[1] for c in colonnes)
        tete = "<thead><tr>%s</tr></thead>" % "".join(
            "<th%s>%s</th>" % (" class='n'" if len(c) > 2 and c[2] else "", c[0]) for c in colonnes)
        unites = list(unites) or ["<tr><td colspan='%s' class='gris'>%s</td></tr>" % (len(colonnes), vide)]
        bloc = "<div class='garde'><h2>%s</h2><table class='liste'>%s%s%s</table></div>" % (titre, cols, tete, unites[0])
        if len(unites) > 1:
            bloc += "<table class='liste'>%s%s</table>" % (cols, "".join(unites[1:]))
        return bloc

    def sans_emails(texte):
        """Le journal des échanges écrit « [2026-11-23 09:22] ↩ x@y.com … » : noms et dates jj/mm/aaaa."""
        import re
        texte = re.sub(r"\[(\d{4})-(\d{2})-(\d{2})( \d{2}:\d{2})?[^\]]*\]",
                       lambda m: "[%s/%s/%s%s]" % (m.group(3), m.group(2), m.group(1), m.group(4) or ""), texte or "")
        return re.sub(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", lambda m: nom(m.group(0)), texte)

    html = [CSS_PDF]

    # ── Pied de page : rappel du document + pagination (frappe remplit .page / .topage) ──────────────────────────
    html.append(
        "<div id='footer-html'><div class='letter-head-footer'><div style='font-size:8.5px;color:#888;"
        "border-top:1px solid #ddd;padding-top:3px'>"
        "<span style='float:right'>page <span class='page'></span> / <span class='topage'></span></span>"
        "Clôture de caisse %s · %s · journée du %s</div></div></div>" % (
            esc(doc.name), esc(doc.caisse), jour(doc.date_cloture)))

    # ── En-tête : logo de l'entreprise + titre ───────────────────────────────────────────────────────────────────
    logo = ""
    try:
        from booking_ristourne.impression import aqua_identite
        logo = "<img src='%s' style='width:100%%;height:auto'>" % esc(aqua_identite().logo_url)
    except Exception:
        frappe.log_error(title="Clôture de caisse : logo introuvable pour le PDF")
    d = getdate(doc.date_cloture)
    html.append(
        "<table class='entete'><tr><td style='width:42%%'><div style='width:88%%'>%s</div></td>"
        "<td class='titre'><div class='t1'>CLÔTURE DE CAISSE</div>"
        "<div class='t2'>%s</div><div class='t3'>Journée du %s %s · %s</div></td></tr></table>" % (
            logo, ("Toutes les caisses" if globale else "Caisse de <b>%s</b>" % esc(doc.caisse)),
            JOURS[d.weekday()], jour(d), esc(doc.name)))

    # ── Verdict : la caisse est-elle juste, et l'argent a-t-il changé de mains ? ─────────────────────────────────
    comptees = doc.especes_comptees
    ecart = flt(doc.ecart, 3)
    ecart_remise = flt(doc.get("ecart_remise"), 3)
    collectee = not seule and doc.get("collecte_par")
    if comptees is None:
        classe, titre = "neutre", "Caisse sans comptage d'espèces"
    elif abs(ecart) < 0.0005:
        classe, titre = "ok", "✔ Caisse juste — %s comptés pour %s attendus" % (dt_(comptees), dt_(doc.solde_theorique))
    else:
        classe = "ko"
        titre = "⚠ %s de caisse : %s — %s comptés pour %s attendus" % (
            "Excédent" if ecart > 0 else "Manque", ecart_txt(ecart), dt_(comptees), dt_(doc.solde_theorique))
    if collectee:
        detail = "%s remis par %s, %s reçus par %s le %s" % (
            dt_(doc.especes_remises), esc(nom(doc.valide_par)), dt_(doc.get("especes_recues")),
            esc(nom(doc.collecte_par)), heure(doc.get("collecte_le")))
        if doc.get("par_delegation"):
            detail += " (par délégation, passation %s)" % esc(doc.get("passation") or "")
        if abs(ecart_remise) >= 0.0005:
            classe = "ko"
            detail += " — <b>écart de remise %s</b>" % ecart_txt(ecart_remise)
        if flt(doc.get("fond_conserve")):
            detail += " · fond de caisse conservé %s" % dt_(doc.fond_conserve)
    elif seule:
        detail = "Validée seule par %s le %s" % (esc(nom(doc.valide_par)), heure(doc.get("compte_le") or doc.modified))
    else:
        detail = "Comptée par %s — en attente de collecte" % esc(nom(doc.valide_par))
    html.append("<div class='verdict %s'><div class='v1'>%s</div><div class='v2'>%s</div></div>"
                % (classe, titre, detail))

    # ── Deux blocs : les espèces de la journée | la remise (ou les modes si validée seule) ──────────────────────
    bloc_especes = (
        "<div class='bloc bleu'><h3>Espèces de la journée</h3><table>"
        "<tr><td>Solde d'ouverture</td><td class='n'>%s</td></tr>"
        "<tr><td>+ Encaissements en espèces</td><td class='n'>%s</td></tr>"
        "<tr><td>− Dépenses en espèces</td><td class='n'>%s</td></tr>"
        "<tr class='total'><td>= Attendu en caisse</td><td class='n'>%s</td></tr>"
        "<tr><td>Compté</td><td class='n'>%s</td></tr>"
        "<tr class='fort'><td>Écart</td><td class='n'>%s</td></tr>"
        "</table></div>" % (
            dt_(doc.solde_ouverture), dt_(doc.encaissements_especes), dt_(doc.depenses_especes),
            dt_(doc.solde_theorique), (dt_(comptees) if comptees is not None else "—"),
            (ecart_txt(ecart) if comptees is not None else "—")))
    if collectee:
        bloc_droite = (
            "<div class='bloc gris'><h3>Remise à la collecte</h3><table>"
            "<tr><td>Remis par %s</td><td class='n'>%s</td></tr>"
            "<tr><td>Reçu et recompté par %s</td><td class='n'>%s</td></tr>"
            "<tr class='fort'><td>Écart de remise</td><td class='n'>%s</td></tr>"
            "<tr><td>Fond de caisse conservé</td><td class='n'>%s</td></tr>"
            "<tr><td>Chèques / traites remis</td><td class='n'>%s / %s</td></tr>"
            "<tr><td>Chèques / traites reçus</td><td class='n'>%s / %s</td></tr>"
            "</table></div>" % (
                esc(nom(doc.valide_par)), dt_(doc.especes_remises),
                esc(nom(doc.collecte_par)), dt_(doc.get("especes_recues")),
                ecart_txt(ecart_remise), dt_(doc.get("fond_conserve")),
                doc.get("nb_cheques_remis") or 0, doc.get("nb_traites_remis") or 0,
                doc.get("nb_cheques_recus") or 0, doc.get("nb_traites_recus") or 0))
    else:
        bloc_droite = (
            "<div class='bloc gris'><h3>Validation</h3><table>"
            "<tr><td>Comptée par</td><td class='n'>%s</td></tr>"
            "<tr><td>Le</td><td class='n'>%s</td></tr>"
            "<tr><td>Chèques / traites</td><td class='n'>%s / %s</td></tr>"
            "</table></div>" % (esc(nom(doc.valide_par)), heure(doc.get("compte_le") or doc.modified),
                                doc.get("nb_cheques_remis") or 0, doc.get("nb_traites_remis") or 0))
    html.append("<table class='deux'><tr><td style='padding-right:6px'>%s</td><td style='padding-left:6px'>%s</td>"
                "</tr></table>" % (bloc_especes, bloc_droite))
    if doc.get("echanges"):
        html.append("<h2>Échanges sur la remise</h2><div class='texte'>%s</div>" % esc(sans_emails(doc.echanges)))

    # ── Encaissements du jour par mode : pastilles, 4 par ligne, total en dernier ───────────────────────────────
    cases = ["<div>%s</div><div class='mt'>%s</div>" % (pastille(m), dt_(v))
             for m, v in (recap.get("par_mode") or {}).items() if flt(v)]
    cases.append("<div class='gris'>Total encaissé</div><div class='mt'>%s</div>" % dt_(recap.get("grand_total")))
    rangees = []
    for i in range(0, len(cases), 4):
        bout = cases[i:i + 4]
        cellules = ["<td%s>%s</td>" % (" class='tot'" if i + j == len(cases) - 1 else "", c)
                    for j, c in enumerate(bout)]
        cellules += ["<td style='border:none'></td>"] * (4 - len(bout))
        rangees.append("<tr>%s</tr>" % "".join(cellules))
    html.append("<h2>Encaissements du jour par mode</h2><table class='modes'>%s</table>" % "".join(rangees))

    # ── Rapprochement (caisse globale) ───────────────────────────────────────────────────────────────────────────
    if rap:
        html.append(section(
            "Rapprochement — chèques, traites, dettes",
            [("", 32), ("Avant", 17, 1), ("+ Entrées", 17, 1), ("− Sorties", 17, 1), ("= Après", 17, 1)],
            ["<tr><td>Chèques en portefeuille</td><td class='n'>%s</td><td class='n'>%s</td><td class='n'>%s</td>"
             "<td class='n'><b>%s</b></td></tr>" % (
                 mt(rap["cheques"]["avant"]), mt(rap["cheques"]["recus"]), mt(rap["cheques"]["remis"]),
                 mt(rap["cheques"]["apres"])),
             "<tr><td>Traites en portefeuille</td><td class='n'>%s</td><td class='n'>%s</td><td class='n'>%s</td>"
             "<td class='n'><b>%s</b></td></tr>" % (
                 mt(rap["traites"]["avant"]), mt(rap["traites"]["recus"]), mt(rap["traites"]["remis"]),
                 mt(rap["traites"]["apres"])),
             "<tr><td>Encours dettes (comptable)</td><td class='n'>%s</td><td class='n' colspan='2'>variation %s</td>"
             "<td class='n'><b>%s</b></td></tr>" % (
                 mt(rap["dettes"]["avant"]), mt(rap["dettes"]["variation"]), mt(rap["dettes"]["apres"]))]))
        pieces = (rap["cheques"]["liste"] or []) + (rap["traites"]["liste"] or [])
        if pieces:
            html.append(section(
                "Pièces en portefeuille à la clôture <span class='droite'>à compter physiquement</span>",
                [("Reçu le", 14), ("Mode", 16), ("Client", 34), ("N° / référence", 20), ("Montant (DT)", 16, 1)],
                ["<tr><td>%s</td><td>%s</td><td>%s</td><td>%s<div class='gris petit'>%s</div></td>"
                 "<td class='n'>%s</td></tr>" % (
                     jour(pc["date"]), pastille(pc["mode"]), esc(pc["client"] or ""), esc(pc["reference_no"] or ""),
                     esc(pc["name"]), mt(pc["montant"]))
                 for pc in sorted(pieces, key=lambda x: (x["date"], x["name"]))]))

    if globale and (recap.get("par_employe") or []):
        html.append(section(
            "Par employé", [("Employé", 50), ("dont anciennes commandes (DT)", 25, 1), ("Total (DT)", 25, 1)],
            ["<tr><td>%s</td><td class='n'>%s</td><td class='n'><b>%s</b></td></tr>" % (
                esc(e["employe"]), mt(e.get("anciens")), mt(e["total"])) for e in recap["par_employe"]]))

    # ── Commandes du jour : une fiche par intervention, avec sa traçabilité en lignes (24/11/2026) ─────────────────
    tous_orders = [o for e in (data.get("employees") or []) for o in (e.get("orders") or [])]
    affichees = {o.get("sales_order") for o in tous_orders}
    points_commande, points_restants = {}, []
    for lib, reps in points_du_texte(doc.get("controles")):
        cibles = [s for s in RE_COMMANDE.findall(lib) if s in affichees]
        if cibles:
            points_commande.setdefault(cibles[0], []).append((lib, reps))
        else:
            points_restants.append((lib, reps))
    debut = get_datetime(str(d))
    # Jusqu'à la collecte (ou au comptage d'une validation seule) ; les clôtures d'avant le champ « compte_le »
    # s'arrêtent à leur création — jamais aux événements des jours suivants.
    fin = get_datetime(doc.get("collecte_le") or doc.get("compte_le") or doc.get("creation") or now_datetime())
    try:
        traces = _traces(tous_orders, debut, fin, d)
    except Exception:
        # Le PDF ne doit jamais faire échouer la collecte : sans traçabilité plutôt que sans PDF.
        frappe.log_error(title="Clôture de caisse : traçabilité du PDF")
        traces = {}

    def moment(t):
        return t.strftime("%H:%M") if t.date() == d else t.strftime("%d/%m %H:%M")

    def _paiement(p, trace_so):
        deja_trace = any(p.get("name") and p["name"] in t for _m, _q, t in trace_so)
        bits = ["%s <b>%s</b>" % (pastille(p.get("mode")), mt(p.get("amount")))]
        if not deja_trace:
            sous = [esc(p.get("name") or "")]
            if p.get("date") and getdate(p["date"]) != d:
                sous.append("du %s" % jour(p["date"]))
            bits.append("<span class='gris petit'>%s</span>" % " · ".join(sous))
        if p.get("hors_periode"):
            bits.append("<span class='flag'>⏪ avance hors période — non comptée</span>")
        elif p.get("antidate"):
            bits.append("<span class='flag'>⚠ antidaté (saisi le %s)</span>" % jour(p.get("creation_date")))
        return "<span class='p'>%s</span>" % " ".join(bits)

    def _fiche(o):
        so = o["sales_order"]
        trace_so = traces.get(so) or []
        sous = [esc(o.get("status") or "")]
        if o.get("date") and getdate(o["date"]) != d:
            sous.append("du %s" % jour(o["date"]))
        if o.get("intervention"):
            sous.append("intervention %s" % esc(o["intervention"]))
        if o.get("tache_reference"):
            sous.append("tâche %s%s%s" % (
                esc(o["tache_reference"]), (" (%s)" % esc(o["tache_employee"])) if o.get("tache_employee") else "",
                (" " + STATUTS_TACHE.get(o.get("tache_status"), o.get("tache_status") or "")) if o.get("tache_status") else ""))
        droite = "TTC <b>%s</b>" % dt_(o.get("grand_total"))
        if abs(flt(o.get("total_paid")) - flt(o.get("grand_total"))) >= 0.0005:
            droite += "<div class='gris petit'>payé %s</div>" % dt_(o.get("total_paid"))
        morceaux = ["<table class='itete'><tr><td><b>%s</b> · %s<div class='gris petit'>%s</div></td>"
                    "<td class='n'>%s</td></tr></table>" % (esc(so), esc(o.get("customer") or ""), " · ".join(sous),
                                                           droite)]
        pmts = o.get("payments") or []
        morceaux.append("<div class='ipmts'>%s</div>" % (
            "".join(_paiement(p, trace_so) for p in pmts) or "<span class='gris'>aucun paiement</span>"))
        avance = o.get("avance_anterieure") or {}
        if flt(avance.get("total")):
            morceaux.append("<div class='flag'>⏪ Avance reçue avant la période : %s (%s) — hors totaux du jour</div>"
                            % (dt_(avance["total"]), esc(", ".join(avance.get("modes") or []))))
        for w in o.get("warnings") or []:
            morceaux.append("<div class='warn'>⚠ %s</div>" % esc(w.get("message") or ""))
        pts = points_commande.get(so) or []
        for lib, reps in pts:
            court = lib.split(" — " + so)[0]
            morceaux.append("<div class='ijustif'><div>⚑ %s</div>%s</div>" % (esc(court), "".join(
                "<div>↳ <span class='motif'>%s</span>%s <span class='gris petit'>— justifié au comptage par %s</span></div>"
                % (esc(m), (" — <i>%s</i>" % esc(c)) if c else "", esc(nom(doc.valide_par))) for m, c in reps)))
        if trace_so:
            morceaux.append("<table class='trace'>%s</table>" % "".join(
                "<tr><td class='h'>%s</td><td class='qui'>%s</td><td>%s</td></tr>" % (moment(t), esc(q), esc(x))
                for t, q, x in trace_so))
        alerte = bool(o.get("warnings") or pts)
        return "<div class='inter%s'>%s</div>" % (" alerte" if alerte else "", "".join(morceaux))

    for e in (data.get("employees") or []):
        fiches = [_fiche(o) for o in (e.get("orders") or [])] or ["<div class='gris'>aucune commande</div>"]
        html.append("<div class='garde'><h2>Commandes du jour — %s <span class='droite'>%s commande(s) · %s</span></h2>"
                    "%s</div>%s" % (esc(e["employe"]), e.get("nb_commandes") or 0, dt_(e.get("total")), fiches[0],
                                    "".join(fiches[1:])))

    # ── Paiements sur anciennes commandes ────────────────────────────────────────────────────────────────────────
    anciens = data.get("anciens") or {}
    if anciens.get("paiements"):
        unites = []
        for p in anciens["paiements"]:
            pieces_txt = ", ".join("%s (%s)" % (esc(pc["name"]), jour(pc["date"])) for pc in (p.get("pieces") or []))
            flags = []
            if p.get("antidate"):
                flags.append("⚠ antidaté (saisi le %s)" % jour(p.get("creation_date")))
            if p.get("exclu"):
                flags.append("EXCLU de la caisse")
            unites.append(
                "<tr%s><td>%s</td><td>%s<div class='gris petit'>saisi par %s</div></td><td>%s</td>"
                "<td>%s<div class='gris petit'>%s</div></td><td class='n'>%s</td><td class='flag'>%s</td></tr>" % (
                    " class='exclu'" if p.get("exclu") else "", jour(p.get("date")),
                    esc(p.get("customer_name") or ""), esc(p.get("saisi_par") or ""), pastille(p.get("mode")),
                    pieces_txt, esc(p.get("name") or ""), mt(p.get("amount")), " · ".join(flags)))
        html.append(section(
            "Paiements sur anciennes commandes <span class='droite'>%s</span>" % dt_(anciens.get("total")),
            [("Date", 12), ("Client", 23), ("Mode", 13), ("Commande d'origine", 25), ("Montant (DT)", 12, 1),
             ("", 15)], unites))

    # ── Dépenses de la caisse ────────────────────────────────────────────────────────────────────────────────────
    if depenses.get("lignes"):
        unites = []
        for l in depenses["lignes"]:
            qui = esc(l.get("saisi_par") or "")
            if l.get("payee_par_collegue") and l.get("caisse"):
                qui += "<div class='flag'>payée de la caisse de %s</div>" % esc(l["caisse"])
            unites.append(
                "<tr><td>%s</td><td>%s<div class='gris petit'>%s</div></td><td>%s</td><td>%s</td>"
                "<td class='n'>%s</td></tr>" % (
                    jour(l.get("date")), esc(l.get("description") or ""), esc(l.get("type") or ""), qui,
                    pastille(l.get("mode")), mt(l.get("montant"))))
        html.append(section(
            "Dépenses de la caisse <span class='droite'>%s</span>" % dt_(depenses.get("total")),
            [("Date", 12), ("Description", 44), ("Saisie par", 18), ("Mode", 12), ("Montant (DT)", 14, 1)], unites))

    # ── Points de contrôle qui ne portent pas sur une commande de la journée (les autres sont sous leur commande) ──
    if points_restants:
        blocs = ["<div class='point'>%s%s</div>" % (esc(lib), "".join(
            "<div class='rep'>↳ <span class='motif'>%s</span>%s</div>" % (esc(m), (" — <i>%s</i>" % esc(c)) if c else "")
            for m, c in reps)) for lib, reps in points_restants]
        titre_pts = "<h2>%s <span class='droite'>%s point%s</span></h2>" % (
            "Autres points de contrôle justifiés" if points_commande else "Points de contrôle justifiés",
            len(blocs), "s" if len(blocs) > 1 else "")
        # Le titre part avec le premier point : jamais seul en bas de page.
        html.append("<div class='garde'>%s%s</div>%s" % (titre_pts, blocs[0], "".join(blocs[1:])))
    if doc.note:
        html.append("<h2>Note</h2><div class='texte'>%s</div>" % esc(doc.note))

    # ── Validations (électroniques, horodatées) ──────────────────────────────────────────────────────────────────
    gauche = ("<div class='bloc'><div class='gris petit'>%s</div><div class='qui'>%s</div>"
              "<div class='gris petit'>le %s</div></div>" % (
                  "Validée seule par" if seule else "Comptée et remise par", esc(nom(doc.valide_par)),
                  heure(doc.get("compte_le") or doc.modified)))
    droite = ""
    if collectee:
        droite = ("<div class='bloc'><div class='gris petit'>Reçue et recomptée par</div><div class='qui'>%s</div>"
                  "<div class='gris petit'>le %s</div></div>" % (esc(nom(doc.collecte_par)), heure(doc.get("collecte_le"))))
    html.append("<div class='signatures'><table><tr><td style='padding-right:6px'>%s</td>"
                "<td style='padding-left:6px'>%s</td></tr></table><div class='gris petit' style='margin-top:4px'>"
                "Validations faites dans ERPNext, horodatées ; document figé au moment de la collecte.</div></div>"
                % (gauche, droite))

    return "".join(html)


@frappe.whitelist()
def cloture_info(caisse, date):
    """La clôture validée de cette caisse à cette date, pour la BANNIÈRE de la
    page (lecture seule) : nom, PDF, qui, écart. None si pas encore validée."""
    frappe.only_for(ROLES)
    caisse = (caisse or "").strip() or CAISSE_GLOBALE
    from customization_app import caisse_collecte as CC
    if (not CC.peut_voir_toutes_les_caisses() and caisse != CC._caisse_de(frappe.session.user)
            and caisse not in CC.perimetre_sous_traitants()):
        return None
    champs = ["name", "valide_par", "solde_theorique", "especes_comptees", "ecart", "statut",
              "especes_remises", "especes_recues", "ecart_remise", "collecte_par", "collecte_le",
              "validation_seule", "par_delegation", "passation", "tours_ecart", "echanges", "docstatus"]
    row = frappe.db.get_value(
        "Cloture Caisse",
        {"caisse": caisse, "date_cloture": getdate(date), "docstatus": 1}, champs, as_dict=True)
    if not row:
        from customization_app import caisse_collecte as CC
        row = frappe.db.get_value(
            "Cloture Caisse",
            {"caisse": caisse, "date_cloture": getdate(date), "docstatus": 0,
             "statut": ["in", CC.STATUTS_EN_ATTENTE]}, champs, as_dict=True)
        if not row:
            return None
        row["caisse"] = caisse
        row["mienne"] = CC.peut_agir_sur(row)
        row["pdf_url"] = None
        return row
    row["caisse"] = caisse
    row["mienne"] = CC.peut_agir_sur(row)
    row["pdf_url"] = frappe.db.get_value(
        "File", {"attached_to_doctype": "Cloture Caisse", "attached_to_name": row.name,
                 "file_name": ["like", "%.pdf"]}, "file_url")
    return row
