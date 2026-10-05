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

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, now_datetime

from customization_app.rapport_caisse_journaliere import (
    CAISSE_ACCOUNTS_LIKE,
    PEUVENT_VOIR_ECONOMIQ as DIRECTION,
    _exclusions,
    _ma_caisse,
    get_data,
)

CAISSE_GLOBALE = "Tous les employés"
ROLES = ("System Manager", "Accounts Manager", "Accounts User",
         "Sales Manager", "Sales User")

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
}
FAMILLE_DU_POINT = {"tache_ouverte": "tache", "tache_sans_commande": "tache", "dette_hors_aramex": "dette",
                    "ancien_exclu": "ancien_exclu", "caisse_non_collectee": "caisse_non_collectee"}


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
    if caisse != CAISSE_GLOBALE and CC.role_collecte() == "titulaire":
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


def _mesures(caisse, date):
    """Les chiffres du jour pour cette caisse, depuis la MÊME source que la page."""
    data = get_data(str(date), str(date), employe=("" if caisse == CAISSE_GLOBALE else caisse))
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
                    "commande": o["sales_order"], "client": o.get("customer"), "montant": dette,
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
                    "commande": o["sales_order"], "client": o.get("customer"),
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


def _taches_sans_commande(caisse, date):
    """Les tâches du jour ENCORE OUVERTES et sans commande (Autre, Visite, entretien dont la commande
    n'est pas encore créée…). Le rapport de caisse part des commandes : sans ce contrôle, une tâche
    sans commande restée ouverte ne se voyait jamais (05/10/2026). « Tous les employés » : toutes."""
    from customization_app.rapport_caisse_journaliere import COMPANY
    from customization_app.tournee_optimisation import TYPES_HORS_JOURNEE
    date = getdate(date)
    filtre, valeurs = "", [COMPANY, "%s 00:00:00" % date, "%s 23:59:59" % date, list(TYPES_HORS_JOURNEE)]
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


def motifs_du_point(point):
    famille = FAMILLE_DU_POINT.get(point.get("type"))
    return MOTIFS[famille] if famille else ["Autre"]


def points_controle(caisse, date, data):
    """Tous les points de la clôture de cette caisse, chacun avec la liste de ses motifs."""
    from customization_app import caisse_collecte as CC
    points = _controles(data) + _taches_sans_commande(caisse, date)
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
        elif len(commentaire) < COMMENTAIRE_MIN:
            erreurs.append("%s : commentaire trop court (%s caractères minimum)" % (p["libelle"], COMMENTAIRE_MIN))
        else:
            lignes.append("%s\n  → %s — %s" % (p["libelle"], motif, commentaire))
    return "\n".join(lignes), erreurs


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
    points = points_controle(caisse, date, m["data"])
    return {
        "caisse": caisse, "date": str(date),
        "controles": points,
        "en_attente": en_attente,
        "mode_validation": CC.mode_validation(caisse),
        "photo_obligatoire": CC.config()["photo_obligatoire"],
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
    points = points_controle(caisse, date, m["data"])
    bloquants = [p for p in points if p["bloquant"]]
    if bloquants:
        frappe.throw(_("Validation refusée — bon(s) de livraison à valider d'abord : {0}")
                     .format(", ".join(p["bl"] for p in bloquants)))
    controles_txt, erreurs = verifier_justifications(points, justifs)
    if erreurs:
        frappe.throw(_("Justification incomplète :<br>• {0}")
                     .format("<br>• ".join(frappe.utils.escape_html(x) for x in erreurs)))

    ouverture = _ouverture(caisse, date)
    theorique = flt(ouverture + m["encaissements_especes"] - m["depenses_especes"], 3)
    comptees = flt(especes_comptees, 3) if especes_comptees not in (None, "") else None
    remises = flt(especes_remises, 3) if especes_remises not in (None, "") else None
    if mode == "double":
        try:
            CC.controler_remise(comptees, remises)
        except ValueError as exc:
            frappe.throw(str(exc))
        if CC.config()["photo_obligatoire"] and not photo:
            frappe.throw(_("La photo de la remise est obligatoire (Config Caisse)."))
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
    if existante:
        doc = CC.rouvrir_comptage(existante, valeurs)
    else:
        doc = frappe.get_doc({"doctype": "Cloture Caisse", **valeurs})
        doc.insert(ignore_permissions=True)
    CC.enregistrer_photo_remise(doc, photo, photo_nom)

    if mode == "seule":
        CC.finaliser(doc, m, rap, validation_seule=True)
    else:
        CC.notifier_collecteurs(doc, rouverte=bool(existante))
    frappe.db.commit()
    return {"name": doc.name, "statut": doc.statut, "mode": mode, "rouverte": bool(existante)}


def _html_instantane(doc, data, rap=None):
    """Le PDF FIGÉ de la clôture = le rapport COMPLET du jour (décision
    utilisateur 19/08) : état espèces, rapprochement (globale), récap par mode
    et par employé, DÉTAIL par employé (commandes, paiements, avertissements,
    avances), paiements sur anciennes commandes, dépenses, portefeuille
    chèques/traites restant, contrôles justifiés et note."""
    esc = frappe.utils.escape_html
    fmt = lambda v: frappe.utils.fmt_money(flt(v), 3, "TND")  # noqa: E731

    recap = data.get("recap") or {}
    depenses = data.get("depenses") or {}

    # ── État espèces (avant / mouvements / après) ────────────────────────────
    # Le <style> reste HORS formatage % : le CSS est truffé de « % » littéraux.
    html = ["""
    <style>
      body { font-family: sans-serif; font-size: 11px; color: #222; }
      h1 { font-size: 18px; margin-bottom: 2px; } .sous { color: #666; margin-bottom: 14px; }
      table { border-collapse: collapse; width: 100%; margin: 6px 0 12px; }
      th, td { border: 1px solid #ccc; padding: 4px 7px; text-align: left; vertical-align: top; }
      th { background: #f2f2f2; } td.n, th.n { text-align: right; }
      .etat td { font-size: 12.5px; } .etat .val { font-weight: 700; text-align: right; }
      h2 { font-size: 14px; margin: 16px 0 2px; border-bottom: 2px solid #444; padding-bottom: 2px; }
      h3 { font-size: 12.5px; margin: 10px 0 2px; }
      .warn { color: #a93226; font-size: 10.5px; }
      .flag { color: #7a5d10; font-size: 10px; }
      .muted { color: #777; }
      .exclu td { color: #999; text-decoration: line-through; }
      .pmts { margin: 0; width: 100%; } .pmts td { border: none; border-top: 1px dotted #ddd;
        padding: 2px 6px; font-size: 10.5px; }
    </style>"""]
    html.append(
        "<h1>Clôture de caisse — %s</h1>"
        "<div class='sous'>%s · %s · %s · %s</div>"
        % (esc(doc.caisse), esc(str(doc.date_cloture)),
           esc(("validée seule par %s" % doc.valide_par) if doc.get("validation_seule")
               else "comptée par %s · collectée par %s%s" % (
                   doc.valide_par, doc.get("collecte_par") or "?",
                   " (par délégation, passation %s)" % doc.passation if doc.get("par_delegation") else "")),
           esc(str(now_datetime())[:19]), esc(doc.name)))

    html.append("""
    <h2>État des espèces</h2>
    <table class="etat">
      <tr><td>Solde d'ouverture (avant)</td><td class="val">%s</td></tr>
      <tr><td>Encaissements espèces du jour</td><td class="val">+ %s</td></tr>
      <tr><td>Dépenses espèces du jour</td><td class="val">− %s</td></tr>
      <tr><td><b>Solde théorique (après)</b></td><td class="val">%s</td></tr>
      <tr><td>Espèces comptées</td><td class="val">%s</td></tr>
      <tr><td><b>Écart</b></td><td class="val">%s</td></tr>
    </table>""" % (
        fmt(doc.solde_ouverture), fmt(doc.encaissements_especes),
        fmt(doc.depenses_especes), fmt(doc.solde_theorique),
        (fmt(doc.especes_comptees) if doc.especes_comptees is not None else "—"),
        fmt(doc.ecart)))

    # ── Remise au responsable de collecte (double validation) ────────────────
    if not doc.get("validation_seule") and doc.get("collecte_par"):
        html.append("""
        <h2>Remise au responsable de collecte</h2>
        <table class="etat">
          <tr><td>Espèces remises (déclarées par %s)</td><td class="val">%s</td></tr>
          <tr><td>Fond de caisse conservé</td><td class="val">%s</td></tr>
          <tr><td>Chèques / traites remis (nombre)</td><td class="val">%s / %s</td></tr>
          <tr><td>Espèces reçues (comptées par %s)</td><td class="val">%s</td></tr>
          <tr><td>Chèques / traites reçus (nombre)</td><td class="val">%s / %s</td></tr>
          <tr><td><b>Écart de remise (reçu − remis)</b></td><td class="val">%s</td></tr>
          <tr><td>Collectée le</td><td class="val">%s</td></tr>
        </table>""" % (
            esc(doc.valide_par), fmt(doc.especes_remises), fmt(doc.fond_conserve),
            doc.get("nb_cheques_remis") or 0, doc.get("nb_traites_remis") or 0,
            esc(doc.collecte_par), fmt(doc.especes_recues),
            doc.get("nb_cheques_recus") or 0, doc.get("nb_traites_recus") or 0,
            fmt(doc.ecart_remise), esc(str(doc.collecte_le or "")[:16])))
        if doc.get("echanges"):
            html.append("<h3>Échanges sur la remise</h3><pre style='font-size:10.5px'>%s</pre>"
                        % esc(doc.echanges))

    # ── Rapprochement (caisse globale) ───────────────────────────────────────
    if rap:
        html.append("""
        <h2>Rapprochement — chèques, traites, dettes</h2>
        <table>
          <tr><th></th><th class="n">Avant</th><th class="n">+ Entrées</th>
              <th class="n">− Sorties</th><th class="n">= Après</th></tr>
          <tr><td>Chèques en portefeuille</td><td class="n">%s</td><td class="n">%s</td>
              <td class="n">%s</td><td class="n"><b>%s</b></td></tr>
          <tr><td>Traites en portefeuille</td><td class="n">%s</td><td class="n">%s</td>
              <td class="n">%s</td><td class="n"><b>%s</b></td></tr>
          <tr><td>Encours dettes (comptable)</td><td class="n">%s</td>
              <td class="n" colspan="2">variation %s</td><td class="n"><b>%s</b></td></tr>
        </table>""" % (
            fmt(rap["cheques"]["avant"]), fmt(rap["cheques"]["recus"]),
            fmt(rap["cheques"]["remis"]), fmt(rap["cheques"]["apres"]),
            fmt(rap["traites"]["avant"]), fmt(rap["traites"]["recus"]),
            fmt(rap["traites"]["remis"]), fmt(rap["traites"]["apres"]),
            fmt(rap["dettes"]["avant"]), fmt(rap["dettes"]["variation"]),
            fmt(rap["dettes"]["apres"])))

        pieces = (rap["cheques"]["liste"] or []) + (rap["traites"]["liste"] or [])
        if pieces:
            lignes = "".join(
                "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td class='n'>%s</td></tr>" % (
                    esc(pc["name"]), esc(pc["mode"]), esc(pc["date"]),
                    esc(pc["client"]), esc(pc["reference_no"]), fmt(pc["montant"]))
                for pc in sorted(pieces, key=lambda x: (x["date"], x["name"])))
            html.append("""
            <h3>Pièces en portefeuille à la clôture (à compter physiquement)</h3>
            <table><tr><th>Paiement</th><th>Mode</th><th>Reçu le</th><th>Client</th>
              <th>N° / Référence</th><th class="n">Montant</th></tr>%s</table>""" % lignes)

    # ── Récap par mode / par employé ─────────────────────────────────────────
    lignes_modes = "".join(
        "<tr><td>%s</td><td class='n'>%s</td></tr>" % (esc(m), fmt(v))
        for m, v in (recap.get("par_mode") or {}).items() if flt(v))
    html.append("<h2>Encaissements du jour par mode</h2>"
                "<table><tr><th>Mode</th><th class='n'>Montant</th></tr>%s"
                "<tr><td><b>Total</b></td><td class='n'><b>%s</b></td></tr></table>"
                % (lignes_modes or "<tr><td colspan='2'>aucun</td></tr>",
                   fmt(recap.get("grand_total"))))

    if doc.caisse == CAISSE_GLOBALE and (recap.get("par_employe") or []):
        lignes_emp = "".join(
            "<tr><td>%s</td><td class='n'>%s</td><td class='n'>%s</td></tr>" % (
                esc(e["employe"]), fmt(e.get("anciens")), fmt(e["total"]))
            for e in recap["par_employe"])
        html.append("<h3>Par employé</h3><table><tr><th>Employé</th>"
                    "<th class='n'>dont anciennes commandes</th><th class='n'>Total</th></tr>%s</table>"
                    % lignes_emp)

    # ── DÉTAIL par employé : commandes, paiements, avertissements ────────────
    def _ligne_paiement(p):
        drapeaux = []
        if p.get("hors_periode"):
            drapeaux.append("⏪ avance hors période — non comptée")
        elif p.get("antidate"):
            drapeaux.append("⚠ antidaté (saisi le %s)" % esc(p.get("creation_date") or ""))
        return ("<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td class='n'>%s</td><td class='flag'>%s</td></tr>" % (
                    esc(p.get("name") or ""), esc(p.get("date") or ""),
                    esc(p.get("mode") or ""), esc(p.get("reference_no") or ""),
                    fmt(p.get("amount")), " · ".join(drapeaux)))

    for e in (data.get("employees") or []):
        html.append("<h2>Caisse — %s <span class='muted'>(%s commande(s) · %s)</span></h2>"
                    % (esc(e["employe"]), e.get("nb_commandes") or 0, fmt(e.get("total"))))
        blocs = []
        for o in e.get("orders") or []:
            tache = o.get("tache_status") or "—"
            entetes = ("<tr><th colspan='6'>%s — %s · %s · TTC %s · %s · "
                       "intervention : %s · tâche : %s · payé : %s</th></tr>" % (
                           esc(o["sales_order"]), esc(o.get("customer") or ""),
                           esc(o.get("date") or ""), fmt(o.get("grand_total")),
                           esc(o.get("status") or ""), esc(o.get("intervention") or "—"),
                           esc(tache), fmt(o.get("total_paid"))))
            lignes = [entetes]
            avance = o.get("avance_anterieure") or {}
            if flt(avance.get("total")):
                lignes.append("<tr><td colspan='6' class='flag'>⏪ Avance reçue avant la "
                              "période : %s (%s) — hors totaux du jour</td></tr>"
                              % (fmt(avance["total"]), esc(", ".join(avance.get("modes") or []))))
            for w in o.get("warnings") or []:
                lignes.append("<tr><td colspan='6' class='warn'>⚠ %s</td></tr>"
                              % esc(w.get("message") or ""))
            pmts = o.get("payments") or []
            if pmts:
                lignes.append("<tr><td class='muted'>Paiement</td><td class='muted'>Date</td>"
                              "<td class='muted'>Mode</td><td class='muted'>Référence</td>"
                              "<td class='muted n'>Montant</td><td></td></tr>")
                lignes += [_ligne_paiement(p) for p in pmts]
            else:
                lignes.append("<tr><td colspan='6' class='muted'>aucun paiement</td></tr>")
            blocs.append("".join(lignes))
        html.append("<table>%s</table>" % "".join(blocs))

    # ── Paiements sur anciennes commandes ────────────────────────────────────
    anciens = data.get("anciens") or {}
    if anciens.get("paiements"):
        lignes = []
        for p in anciens["paiements"]:
            pieces_txt = ", ".join("%s (%s)" % (pc["name"], pc["date"])
                                   for pc in (p.get("pieces") or []))
            flags = []
            if p.get("antidate"):
                flags.append("⚠ antidaté (saisi le %s)" % esc(p.get("creation_date") or ""))
            if p.get("exclu"):
                flags.append("EXCLU de la caisse")
            lignes.append(
                "<tr%s><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
                "<td class='n'>%s</td><td>%s</td><td class='flag'>%s</td></tr>" % (
                    " class='exclu'" if p.get("exclu") else "",
                    esc(p.get("date") or ""), esc(p.get("saisi_par") or ""),
                    esc(p.get("customer_name") or ""), esc(p.get("mode") or ""),
                    esc(p.get("reference_no") or ""), fmt(p.get("amount")),
                    esc(pieces_txt), " · ".join(flags)))
        html.append("""
        <h2>Paiements sur anciennes commandes — %s</h2>
        <table><tr><th>Date</th><th>Saisi par</th><th>Client</th><th>Mode</th>
          <th>Référence</th><th class="n">Montant</th><th>Pièce(s) d'origine</th><th></th></tr>
        %s</table>""" % (fmt(anciens.get("total")), "".join(lignes)))

    # ── Dépenses de la caisse ────────────────────────────────────────────────
    if depenses.get("lignes"):
        lignes_dep = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td class='n'>%s</td></tr>" % (
                esc(l["date"]), esc(l["saisi_par"]), esc(l["type"]),
                esc(l["description"]), esc(l.get("mode") or ""), fmt(l["montant"]))
            for l in depenses["lignes"])
        html.append("""
        <h2>Dépenses de la caisse — %s</h2>
        <table><tr><th>Date</th><th>Saisie par</th><th>Type</th><th>Description</th>
          <th>Mode</th><th class="n">Montant</th></tr>%s</table>"""
                    % (fmt(depenses.get("total")), lignes_dep))

    # ── Contrôles justifiés + note ───────────────────────────────────────────
    if doc.get("controles"):
        html.append("<h2>Points de contrôle justifiés</h2>"
                    "<pre style='font-size:10.5px'>%s</pre>" % esc(doc.controles))
    if doc.note:
        html.append("<h2>Note</h2><p>%s</p>" % esc(doc.note))

    return "".join(html)


@frappe.whitelist()
def cloture_info(caisse, date):
    """La clôture validée de cette caisse à cette date, pour la BANNIÈRE de la
    page (lecture seule) : nom, PDF, qui, écart. None si pas encore validée."""
    frappe.only_for(ROLES)
    caisse = (caisse or "").strip() or CAISSE_GLOBALE
    from customization_app import caisse_collecte as CC
    if not CC.peut_voir_toutes_les_caisses() and caisse != CC._caisse_de(frappe.session.user):
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
