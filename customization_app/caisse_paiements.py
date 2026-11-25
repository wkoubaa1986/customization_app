"""Chaque paiement est pris par UNE clôture de caisse, et elle s'en souvient (25/11/2026).

Avant, une clôture recalculait « les paiements du jour » à partir des dates. Trois fuites ont été mesurées sur les
caisses du 05/10 au 24/11/2026 :
  - un jour compté en retard reprenait un paiement saisi plus tard, que le jour de sa saisie comptait aussi : 3
    paiements, 328 DT comptés par deux clôtures (FAYCEL DRISS 112 DT chez Radhouan le 21/11 ET chez Salma le 23/11) ;
  - un paiement saisi le soir APRÈS le comptage du jour n'était compté nulle part (Ayari Mehdi, 280 DT, 28/10) ;
  - un paiement compté puis annulé laissait la caisse « avoir reçu » un argent que la comptabilité n'a plus, sans
    que personne ne le sache : 601 DT (Nejib 14/10 : 500 DT…).

Désormais :
  1. au comptage, la clôture enregistre les paiements qu'elle a pris (`paiements_pris`, JSON) et chaque paiement
     porte son numéro (`Payment Entry.custom_cloture_caisse`) ;
  2. un paiement pris ne compte plus que dans la caisse et le jour de SA clôture (le rapport le montre « déjà
     compté ») ; un paiement réémis (« …-1 ») hérite de la clôture de l'original ;
  3. un paiement jamais pris parce que saisi APRÈS le comptage de son jour est REPRIS par le comptage suivant de
     sa caisse (« saisi après le comptage du … ») ;
  4. un paiement compté (espèces, chèque, traite) puis annulé sans être ressaisi à l'identique devient un point à
     justifier au comptage suivant de sa caisse, et le collecteur et la direction sont prévenus tout de suite.

Seuls les paiements saisis depuis la mise en route (Config Caisse « paiements_suivis_depuis », posée par le patch
caisse_suivi_paiements) peuvent être repris : l'historique n'est pas rebalayé.
"""
import json

import frappe
from frappe import _
from frappe.utils import flt, get_datetime, getdate

CHAMP = "custom_cloture_caisse"
CAISSE_GLOBALE = "Tous les employés"
# L'argent qui se touche : ce qu'une annulation après comptage fait « disparaître » d'une caisse.
MODES_PHYSIQUES = ("Espèces", "Chèque", "Traite bancaire LC")
COMPTES_PHYSIQUES_LIKE = ("Espèces%", "Chèques - %", "Traite Bancaire%")
REPRISE_JOURS_MAX = 31
MARQUE_DISPARU = "Compté puis disparu — "


def suivi_depuis():
    """Le moment de la mise en route : rien d'avant n'est repris. None = pas encore en route."""
    v = frappe.db.sql("SELECT value FROM `tabSingles` WHERE doctype = 'Config Caisse' AND field = 'paiements_suivis_depuis'")
    return get_datetime(v[0][0]) if v and v[0][0] else None


def _champ_existe():
    return frappe.db.has_column("Payment Entry", CHAMP)


# ── 1-2. Le rapport : un paiement pris ne compte que dans SA clôture ─────────────────────────────────────────────

def liens(noms) -> dict:
    """{paiement: {cloture, caisse, date}} pour les paiements pris par une clôture encore vivante."""
    noms = [n for n in set(noms or []) if n]
    if not noms or not _champ_existe():
        return {}
    out = {}
    for r in frappe.db.sql(
            """SELECT pe.name, c.name AS cloture, c.caisse, c.date_cloture FROM `tabPayment Entry` pe
               JOIN `tabCloture Caisse` c ON c.name = pe.custom_cloture_caisse AND c.docstatus < 2
               WHERE pe.name IN %s""", (tuple(noms),), as_dict=True):
        out[r.name] = {"cloture": r.cloture, "caisse": r.caisse, "date": str(r.date_cloture)}
    return out


def a_sa_place(lien, caisse, d1, d2) -> bool:
    """Le paiement pris par `lien` compte-t-il dans le rapport de `caisse` sur [d1, d2] ? Fonction pure."""
    if not lien:
        return True
    return lien["caisse"] == caisse and getdate(d1) <= getdate(lien["date"]) <= getdate(d2)


def appliquer_clotures(employes, anciens, grand_par_mode, d1, d2, filtre=None):
    """Dans le rapport (get_data, avant le filtre par caisse) : retire des totaux les paiements déjà pris par une
    autre clôture (`deja_compte`), et ajoute ceux qu'une clôture de la période a REPRIS (`repris`)."""
    if not _champ_existe():
        return
    noms = {p["name"] for e in employes for o in e["orders"] for p in o["payments"]}
    noms |= {p["name"] for p in anciens.get("paiements") or []}
    lien_de = liens(noms)
    for e in employes:
        for o in e["orders"]:
            for p in o["payments"]:
                l = lien_de.get(p["name"])
                if a_sa_place(l, e["employe"], d1, d2):
                    continue
                p["deja_compte"] = l
                if not p.get("hors_periode"):
                    m = p["mode"]
                    e["totaux_par_mode"][m] = flt(flt(e["totaux_par_mode"].get(m)) - flt(p["amount"]), 3)
                    grand_par_mode[m] = flt(flt(grand_par_mode.get(m)) - flt(p["amount"]), 3)
        e["total"] = flt(sum(e["totaux_par_mode"].values()), 3)
    for p in anciens.get("paiements") or []:
        l = lien_de.get(p["name"])
        if not a_sa_place(l, p.get("saisi_par"), d1, d2):
            p["deja_compte"] = l
    # Les paiements qu'une clôture de la période a repris (saisis après le comptage de leur jour).
    conditions, valeurs = ["c.docstatus < 2", "c.caisse != %s", "c.date_cloture BETWEEN %s AND %s"], [CAISSE_GLOBALE, d1, d2]
    if filtre:
        conditions.append("c.caisse = %s")
        valeurs.append(filtre)
    for r in frappe.db.sql(
            """SELECT pe.name, pe.posting_date, DATE(pe.creation) AS creation_date, pe.owner, pe.party, pe.party_name,
                      pe.mode_of_payment, pe.paid_to, pe.paid_amount, pe.reference_no, c.name AS cloture, c.caisse,
                      c.date_cloture, IFNULL(pe.custom_exclu_caisse, 0) AS exclu
               FROM `tabPayment Entry` pe JOIN `tabCloture Caisse` c ON c.name = pe.custom_cloture_caisse
               WHERE pe.docstatus = 1 AND """ + " AND ".join(conditions), tuple(valeurs), as_dict=True):
        if r.name in noms:
            continue
        anciens.setdefault("paiements", []).append({
            "exclu": bool(r.exclu), "name": r.name, "date": str(r.posting_date or ""),
            "creation_date": str(r.creation_date or ""), "antidate": True,
            "saisi_par": r.caisse, "saisi_par_reel": frappe.utils.get_fullname(r.owner),
            "customer": r.party, "customer_name": r.party_name or r.party, "mode": r.mode_of_payment or "",
            "compte": r.paid_to, "amount": flt(r.paid_amount), "reference_no": r.reference_no or "", "pieces": [],
            "repris": {"cloture": r.cloture, "caisse": r.caisse, "date": str(r.date_cloture)},
        })
    compter = [p for p in anciens.get("paiements") or [] if not p.get("exclu") and not p.get("deja_compte")]
    par_mode = {}
    for p in compter:
        par_mode[p["mode"] or "—"] = flt(par_mode.get(p["mode"] or "—", 0)) + flt(p["amount"])
    anciens["par_mode"] = par_mode
    anciens["total"] = flt(sum(par_mode.values()))


# ── 3. Les paiements jamais pris : saisis après le comptage de leur jour ─────────────────────────────────────────

def _ou_compte(nom, jour, cache):
    """[(caisse, jour)] où le rapport du `jour` compte ce paiement (commande ou ancienne commande)."""
    j = str(getdate(jour))
    if j not in cache:
        from customization_app.rapport_caisse_journaliere import get_data
        avant = frappe.flags.get("caisse_vue_complete")
        frappe.flags.caisse_vue_complete = True
        try:
            cache[j] = get_data(j, j, employe="")
        finally:
            frappe.flags.caisse_vue_complete = avant
    data, out = cache[j], []
    for e in data.get("employees") or []:
        for o in e.get("orders") or []:
            if any(p["name"] == nom and not p.get("hors_periode") for p in o.get("payments") or []):
                out.append(e["employe"])
    for p in (data.get("anciens") or {}).get("paiements") or []:
        if p["name"] == nom and not p.get("exclu"):
            out.append(p.get("saisi_par"))
    return [(c, j) for c in out if c]


def caisse_de_reprise(places, clotures_comptees, jour_saisie):
    """La caisse qui doit reprendre un paiement jamais pris. `places` : [(caisse, jour)] où le rapport le compte ;
    `clotures_comptees` : {(caisse, jour)} déjà comptés AVANT la saisie du paiement. Si une seule de ces places n'est
    pas encore comptée, elle le prendra elle-même : None. Sinon, la caisse du jour de SAISIE (là où l'argent est
    entré), à défaut la première. Fonction pure."""
    if not places or any(p not in clotures_comptees for p in places):
        return None
    saisie = [c for c, j in places if j == str(getdate(jour_saisie))]
    return saisie[0] if saisie else places[0][0]


def orphelins(caisse, debut, exclues=()):
    """Les paiements que le comptage de `caisse` (période commençant le `debut`) doit REPRENDRE : saisis depuis la
    mise en route, jamais pris, hors de la période, et dont toutes les places ont déjà été comptées avant eux."""
    depuis = suivi_depuis()
    if caisse == CAISSE_GLOBALE or not depuis or not _champ_existe():
        return []
    debut = getdate(debut)
    plancher = max(depuis, get_datetime(frappe.utils.add_days(debut, -REPRISE_JOURS_MAX)))
    like = " OR ".join(["pe.paid_to LIKE %s"] * len(COMPTES_PHYSIQUES_LIKE))
    candidats = frappe.db.sql(
        f"""SELECT pe.name, pe.posting_date, pe.creation, pe.owner, pe.party, pe.party_name, pe.mode_of_payment,
                   pe.paid_to, pe.paid_amount, pe.reference_no
            FROM `tabPayment Entry` pe
            WHERE pe.docstatus = 1 AND pe.payment_type = 'Receive' AND IFNULL(pe.custom_cloture_caisse, '') = ''
              AND IFNULL(pe.custom_exclu_caisse, 0) = 0 AND pe.amended_from IS NULL
              AND pe.creation >= %s AND pe.posting_date < %s AND DATE(pe.creation) < %s AND ({like})
              AND EXISTS (SELECT 1 FROM `tabCloture Caisse` c WHERE c.docstatus < 2 AND c.caisse != %s
                          AND c.date_cloture IN (pe.posting_date, DATE(pe.creation))
                          AND IFNULL(c.compte_le, c.creation) < pe.creation)
            ORDER BY pe.creation""",
        (plancher, debut, debut, *COMPTES_PHYSIQUES_LIKE, CAISSE_GLOBALE), as_dict=True)
    cache, out = {}, []
    for r in candidats:
        places = []
        for jour in sorted({str(r.posting_date), str(getdate(r.creation))}):
            places += _ou_compte(r.name, jour, cache)
        if any(c in exclues for c, _j in places):
            continue
        comptees = {(x.caisse, str(x.date_cloture)) for x in frappe.db.sql(
            """SELECT caisse, date_cloture FROM `tabCloture Caisse` WHERE docstatus < 2 AND date_cloture IN %s
                 AND IFNULL(compte_le, creation) < %s""",
            (tuple({j for _c, j in places} or {"1900-01-01"}), r.creation), as_dict=True)}
        if caisse_de_reprise(places, comptees, getdate(r.creation)) != caisse:
            continue
        out.append({
            "exclu": False, "name": r.name, "date": str(r.posting_date), "creation_date": str(getdate(r.creation)),
            "antidate": True, "saisi_par": caisse, "saisi_par_reel": frappe.utils.get_fullname(r.owner),
            "customer": r.party, "customer_name": r.party_name or r.party, "mode": r.mode_of_payment or "",
            "compte": r.paid_to, "amount": flt(r.paid_amount), "reference_no": r.reference_no or "", "pieces": [],
            "orphelin": {"saisi_le": str(r.creation)[:16], "jours": sorted({j for _c, j in places})},
        })
    return out


def ajouter_orphelins(data, caisse, lignes):
    """Les paiements repris entrent dans le rapport de la caisse comme des anciennes commandes (totaux compris)."""
    if not lignes:
        return
    anciens = data.setdefault("anciens", {"paiements": [], "par_mode": {}, "total": 0.0})
    anciens.setdefault("paiements", []).extend(lignes)
    recap = data.setdefault("recap", {})
    par_mode = recap.setdefault("par_mode", {})
    for l in lignes:
        m = l["mode"] or "—"
        par_mode[m] = flt(flt(par_mode.get(m)) + flt(l["amount"]), 3)
        anciens.setdefault("par_mode", {})[m] = flt(flt(anciens["par_mode"].get(m)) + flt(l["amount"]), 3)
    recap["grand_total"] = flt(sum(flt(v) for v in par_mode.values()), 3)
    anciens["total"] = flt(sum(flt(v) for v in anciens["par_mode"].values()), 3)


# ── Le lien, posé au comptage ────────────────────────────────────────────────────────────────────────────────────

def pris_par(data, caisse):
    """[{name, mode, montant}] : ce que compte la caisse dans son rapport (filtré sur elle). Fonction pure."""
    vus, out = set(), []

    def ajouter(p):
        if p["name"] in vus:
            return
        vus.add(p["name"])
        out.append({"name": p["name"], "mode": p.get("mode") or "", "montant": flt(p.get("amount"), 3)})

    for e in (data or {}).get("employees") or []:
        if e.get("employe") != caisse:
            continue
        for o in e.get("orders") or []:
            for p in o.get("payments") or []:
                if not p.get("hors_periode") and not p.get("deja_compte"):
                    ajouter(p)
    for p in ((data or {}).get("anciens") or {}).get("paiements") or []:
        if (p.get("saisi_par") == caisse and not p.get("exclu") and not p.get("deja_compte")):
            ajouter(p)
    return out


def lier(doc, lignes):
    """Pose le lien paiement → clôture (sans voler celui d'une autre clôture) et garde la liste sur la clôture."""
    if doc.caisse == CAISSE_GLOBALE or not _champ_existe():
        return
    noms = [l["name"] for l in lignes]
    frappe.db.sql("UPDATE `tabPayment Entry` SET custom_cloture_caisse = NULL WHERE custom_cloture_caisse = %s"
                  + (" AND name NOT IN %s" if noms else ""), ((doc.name, tuple(noms)) if noms else (doc.name,)))
    if noms:
        frappe.db.sql("""UPDATE `tabPayment Entry` SET custom_cloture_caisse = %s
                         WHERE name IN %s AND IFNULL(custom_cloture_caisse, '') IN ('', %s)""",
                      (doc.name, tuple(noms), doc.name))
    doc.db_set("paiements_pris", json.dumps(lignes, ensure_ascii=False), update_modified=False)


def delier(cloture):
    """Clôture supprimée (comptage annulé) ou annulée : ses paiements redeviennent libres."""
    if _champ_existe():
        frappe.db.sql("UPDATE `tabPayment Entry` SET custom_cloture_caisse = NULL WHERE custom_cloture_caisse = %s",
                      (cloture,))


def heriter(doc):
    """Un paiement réémis (« …-1 », annulation d'une facture) reste pris par la clôture de l'original : c'est le même
    argent, il ne doit ni compter deux fois, ni être repris."""
    if doc.get("amended_from") and not doc.get(CHAMP) and _champ_existe():
        cloture = frappe.db.get_value("Payment Entry", doc.amended_from, CHAMP)
        if cloture:
            doc.set(CHAMP, cloture)


# ── 4. Compté puis disparu ───────────────────────────────────────────────────────────────────────────────────────

def etat_paiement(nom, mode, montant):
    """None si le paiement compté est toujours là (lui, ou sa réémission à l'identique) ; sinon une phrase."""
    pe = frappe.db.get_value("Payment Entry", nom, ["docstatus", "mode_of_payment", "paid_amount", "modified",
                                                     "modified_by"], as_dict=True)
    if not pe:
        return _("supprimé depuis")
    if pe.docstatus == 1:
        if (pe.mode_of_payment or "") == mode and abs(flt(pe.paid_amount) - flt(montant)) < 0.0005:
            return None
        return _("changé depuis en {0} DT {1}").format(_fmt(pe.paid_amount), pe.mode_of_payment or "")
    # Annulé : sa réémission à l'identique (même mode, même montant) le remplace.
    suivants, a_voir, vus = [], [nom], set()
    while a_voir:
        n = a_voir.pop()
        for s in frappe.get_all("Payment Entry", filters={"amended_from": n}, fields=["name", "docstatus",
                                                                                       "mode_of_payment", "paid_amount"]):
            if s.name in vus:
                continue
            vus.add(s.name)
            a_voir.append(s.name)
            if s.docstatus == 1:
                if (s.mode_of_payment or "") == mode and abs(flt(s.paid_amount) - flt(montant)) < 0.0005:
                    return None
                suivants.append(s)
    phrase = _("annulé le {0} par {1}").format(getdate(pe.modified).strftime("%d/%m/%Y"),
                                                 frappe.utils.get_fullname(pe.modified_by))
    if suivants:
        phrase += _(", remplacé par {0} ({1} DT {2})").format(suivants[0].name, _fmt(suivants[0].paid_amount),
                                                               suivants[0].mode_of_payment or "")
    return phrase


def _fmt(v):
    return "{:,.3f}".format(flt(v)).replace(",", " ").replace(".", ",")


def disparus(caisse, date, plancher=None):
    """Les points « compté puis disparu » à justifier au comptage de `caisse` du `date` : les paiements physiques
    qu'une clôture ANTÉRIEURE de cette caisse a comptés et qui n'existent plus tels quels — une seule fois chacun
    (le libellé justifié reste dans les contrôles de la clôture qui l'a traité)."""
    if caisse == CAISSE_GLOBALE or not frappe.db.has_column("Cloture Caisse", "paiements_pris"):
        return []
    clotures = frappe.db.sql(
        """SELECT name, date_cloture, paiements_pris FROM `tabCloture Caisse`
           WHERE caisse = %s AND docstatus < 2 AND date_cloture < %s AND date_cloture >= %s
             AND IFNULL(paiements_pris, '') NOT IN ('', '[]')
           ORDER BY date_cloture""", (caisse, date, plancher or "1900-01-01"), as_dict=True)
    if not clotures:
        return []
    deja = frappe.db.sql("""SELECT controles FROM `tabCloture Caisse` WHERE caisse = %s AND docstatus < 2
                            AND controles LIKE %s""", (caisse, "%" + MARQUE_DISPARU + "%"))
    deja = "\n".join(r[0] or "" for r in deja)
    points = []
    for c in clotures:
        try:
            lignes = json.loads(c.paiements_pris)
        except Exception:
            continue
        for l in lignes:
            if l.get("mode") not in MODES_PHYSIQUES or (MARQUE_DISPARU + l["name"] + " ") in deja:
                continue
            situation = etat_paiement(l["name"], l.get("mode"), l.get("montant"))
            if not situation:
                continue
            client = frappe.db.get_value("Payment Entry", l["name"], "party_name") or ""
            points.append({
                "cle": "paiement_disparu:%s" % l["name"], "type": "paiement_disparu", "bloquant": 0,
                "paiement": l["name"], "montant": flt(l.get("montant"), 3), "client": client,
                "libelle": "%s%s (%s, %s DT %s) : compté dans la caisse du %s (%s), %s" % (
                    MARQUE_DISPARU, l["name"], client or "?", _fmt(l.get("montant")), l.get("mode"),
                    getdate(c.date_cloture).strftime("%d/%m/%Y"), c.name, situation),
            })
    return points


def verifier_annulation(nom):
    """Tâche lancée après l'annulation d'un paiement (une fois la transaction écrite, donc après une éventuelle
    réémission) : s'il avait été compté et n'est pas remplacé à l'identique, le collecteur, la direction et le
    titulaire de la caisse sont prévenus tout de suite."""
    cloture = frappe.db.get_value("Payment Entry", nom, CHAMP)
    if not cloture:
        return
    c = frappe.db.get_value("Cloture Caisse", cloture, ["caisse", "date_cloture", "docstatus", "paiements_pris"],
                            as_dict=True)
    if not c or c.docstatus == 2:
        return
    try:
        ligne = next((l for l in json.loads(c.paiements_pris or "[]") if l["name"] == nom), None)
    except Exception:
        ligne = None
    if not ligne or ligne.get("mode") not in MODES_PHYSIQUES:
        return
    situation = etat_paiement(nom, ligne.get("mode"), ligne.get("montant"))
    if not situation:
        return
    from customization_app import caisse_collecte as CC
    from customization_app.caisse_cloture import DIRECTION
    sujet = _("⚠️ Paiement {0} ({1} DT {2}) déjà compté dans la caisse de {3} du {4}, {5}. Il sera à justifier "
              "à son prochain comptage.").format(nom, _fmt(ligne.get("montant")), ligne.get("mode"), c.caisse,
                                                 getdate(c.date_cloture).strftime("%d/%m/%Y"), situation)
    titulaire = frappe.db.get_value("Employee", {"employee_name": c.caisse, "status": "Active"}, "user_id")
    for u in (CC._collecteurs() | set(DIRECTION) | ({titulaire} if titulaire else set())):
        if frappe.db.get_value("User", u, "enabled"):
            CC._prevenir(u, sujet, "Payment Entry", nom)


def apres_annulation(doc, method=None):
    """Hook Payment Entry on_cancel (via commande_alertes) : la vérification attend la fin de la transaction."""
    if doc.get(CHAMP):
        frappe.enqueue("customization_app.caisse_paiements.verifier_annulation", nom=doc.name,
                       enqueue_after_commit=True, queue="short")
