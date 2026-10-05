"""Double validation de la caisse journalière (décision utilisateur 04/10/2026).

Le circuit :
  1. l'EMPLOYÉ compte sa caisse et déclare ce qu'il REMET (espèces, nombre de
     chèques et de traites) et ce qu'il garde en fond de caisse — la Clôture
     Caisse naît en brouillon, statut « À collecter », signée par lui ;
  2. le RESPONSABLE DE COLLECTE (Config Caisse) reçoit, recompte et saisit ce
     qu'il a reçu : égal -> la clôture est soumise avec les deux signatures ;
     différent -> « Écart de remise », l'employé accepte le chiffre du
     responsable ou maintient le sien, le responsable tranche au tour suivant ;
  3. un DÉLÉGUÉ (Config Caisse) collecte en l'absence du titulaire : chaque
     caisse qu'il collecte entre dans une PASSATION ouverte à son nom, que le
     titulaire confirme à son retour (même boucle d'écart).
  4. le collecteur LIT les justifications des points de contrôle sur la carte de
     collecte et peut les CONTESTER (« Justification contestée », 05/10/2026) :
     l'employé rouvre et corrige ; collecter quand même = les accepter en l'état.

Sans double validation : la propre caisse du titulaire et la caisse globale
« Tous les employés » (direction), marquées `validation_seule`.

Le report du lendemain = espèces comptées − espèces remises (fond conservé).
Une Clôture Caisse ou une Passation ne se soumet QUE par ces API (hook
before_submit) : jamais depuis le formulaire standard.
"""

import base64

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime, nowdate

from customization_app import caisse_cloture as CL

STATUT_A_COLLECTER = "À collecter"
STATUT_ECART = "Écart de remise"
STATUT_VALIDEE = "Validée"
STATUT_JUSTIF = "Justification contestée"
STATUTS_EN_ATTENTE = (STATUT_A_COLLECTER, STATUT_ECART, STATUT_JUSTIF)

PAS_OUVERTE = "Ouverte"
PAS_A_REMETTRE = "À remettre"
PAS_ECART = "Écart"
PAS_VALIDEE = "Validée"

EVENEMENT_TEMPS_REEL = "caisse_collecte"


# ── Config et rôles ──────────────────────────────────────────────────────────

def config():
    try:
        doc = frappe.get_cached_doc("Config Caisse")
    except Exception:
        return {"responsable": None, "responsables": [], "delegues": [], "hors_periode": [],
                "photo_obligatoire": 0, "date_depart": None, "departs": {}}
    titulaire = doc.get("responsable") or None
    jour = nowdate()
    lignes_co = [r for r in (doc.get("responsables") or []) if r.user and r.user != titulaire]
    co = [r.user for r in lignes_co if delegation_active(r.get("date_debut"), r.get("date_fin"), jour)]
    lignes_del = [d for d in (doc.get("delegues") or []) if d.user and d.user != titulaire and d.user not in co]
    delegues = [d.user for d in lignes_del if delegation_active(d.get("date_debut"), d.get("date_fin"), jour)]
    # Inscrit avec une période DÉPASSÉE (ou pas encore ouverte) : aucun droit, même membre de la
    # direction codée en dur — la période que l'utilisateur a saisie fait foi (04/10/2026).
    hors_periode = [x.user for x in lignes_co + lignes_del if x.user not in co and x.user not in delegues]
    return {
        "responsable": titulaire,
        # Tous les titulaires (le responsable + les co-titulaires) : collectent sans passation.
        "responsables": ([titulaire] if titulaire else []) + co,
        # Délégués EN PÉRIODE seulement : hors période, simple employé (sa caisse, rien d'autre).
        "delegues": delegues,
        "hors_periode": hors_periode,
        "photo_obligatoire": cint(doc.get("photo_remise_obligatoire")),
        # Remise à zéro : les clôtures antérieures à cette date ne font plus report.
        "date_depart": (str(doc.get("date_depart")) if doc.get("date_depart") else None),
        # Par caisse (prime sur la date générale) : {nom de caisse: date}.
        "departs": {(r.caisse or "").strip(): str(r.date_depart) for r in (doc.get("departs") or [])
                    if r.caisse and r.date_depart},
    }


@frappe.whitelist()
def noms_caisses():
    """Pour le réglage « Date de départ par caisse » : les noms exacts acceptés."""
    frappe.only_for(("System Manager", "Accounts Manager"))
    from customization_app.rapport_caisse_journaliere import noms_caisses as _noms
    return _noms()


def date_depart_pour(caisse, cfg):
    """La date de remise à zéro qui vaut pour CETTE caisse : la sienne si réglée, sinon la générale."""
    return (cfg.get("departs") or {}).get((caisse or "").strip()) or cfg.get("date_depart")


def delegation_active(date_debut, date_fin, jour):
    """Une ligne (délégué ou co-titulaire) vaut pendant sa période ; bornes vides = sans limite."""
    jour = getdate(jour)
    if date_debut and jour < getdate(date_debut):
        return False
    if date_fin and jour > getdate(date_fin):
        return False
    return True


def peut_voir_toutes_les_caisses(user=None):
    """Qui voit les caisses des AUTRES (décision utilisateur 04/10/2026) : les responsables de
    collecte, les délégués en période, la direction, System Manager. Les autres : leur caisse."""
    user = user or frappe.session.user
    if "System Manager" in frappe.get_roles(user):
        return True
    return role_collecte(user) is not None


def role_collecte(user=None):
    """« titulaire » (le responsable de collecte), « delegue », « direction »
    (collecte comme un délégué : l'argent passe par ses mains, donc passation)
    ou None. Fonction pure sur (user, config, est_direction)."""
    user = user or frappe.session.user
    c = config()
    return _role(user, c["responsables"], c["delegues"],
                 user in CL.DIRECTION or "System Manager" in frappe.get_roles(user),
                 hors_periode=c["hors_periode"])


def _role(user, responsables, delegues, est_direction, hors_periode=()):
    """`responsables` = le titulaire et ses co-titulaires (liste). `hors_periode` = inscrits dont
    la période est dépassée : aucun droit, la période prime sur la direction codée en dur."""
    if user in (responsables or []):
        return "titulaire"
    if user in (delegues or []):
        return "delegue"
    if user in (hors_periode or ()):
        return None
    if est_direction:
        # Sans titulaire configuré, la direction collecte en titulaire (pas de passation possible :
        # personne pour la recevoir) ; avec un titulaire, l'argent passe par ses mains -> passation.
        return "titulaire" if not responsables else "direction"
    return None


def _exiger_collecteur():
    role = role_collecte()
    if not role:
        frappe.throw(_("La collecte des caisses est réservée au responsable de collecte, "
                       "à ses délégués et à la direction (Config Caisse)."))
    return role


def mode_validation(caisse, user=None):
    """« seule » (titulaire sur SA caisse, direction sur la globale) ou « double »."""
    user = user or frappe.session.user
    if caisse == CL.CAISSE_GLOBALE:
        return "seule"
    if role_collecte(user) == "titulaire" and _caisse_de(user) == caisse:
        return "seule"
    return "double"


def _caisse_de(user):
    """Le nom de caisse (nom affiché) d'un utilisateur, comme la page le fait."""
    exclus_u, exclus_e = CL._exclusions()
    employees = [e for e in frappe.db.sql(
        """SELECT e.name AS employee_id, e.employee_name, e.user_id AS user_email
           FROM `tabEmployee` e""", as_dict=True)
        if e.employee_id not in exclus_e and (e.user_email or "") not in exclus_u]
    for e in employees:
        if (e.user_email or "") == user:
            return e.employee_name
    full = frappe.db.get_value("User", user, "full_name")
    return full or None


# ── Calculs purs ─────────────────────────────────────────────────────────────

def peut_agir_sur(cl, user=None):
    """Qui répond, rouvre ou annule un comptage : la personne qui a compté, le PROPRIÉTAIRE de la
    caisse (même si quelqu'un d'autre a compté pour lui), la direction."""
    user = user or frappe.session.user
    if cl.valide_par == user or CL._est_direction():
        return True
    return bool(cl.caisse) and cl.caisse == _caisse_de(user)


def ecart_remise(declare, recu):
    """(espèces reçues − espèces remises), 3 décimales."""
    return round(float(recu or 0) - float(declare or 0), 3)


def remise_identique(declare, recu):
    """Vrai quand le responsable a reçu exactement ce que l'employé déclare :
    espèces à 0,001 près ET mêmes nombres de chèques et de traites."""
    return (abs(ecart_remise(declare["especes"], recu["especes"])) < 0.0005
            and int(declare.get("nb_cheques") or 0) == int(recu.get("nb_cheques") or 0)
            and int(declare.get("nb_traites") or 0) == int(recu.get("nb_traites") or 0))


def fond_conserve(comptees, remises):
    return round(float(comptees or 0) - float(remises or 0), 3)


def controler_remise(comptees, remises):
    """La remise ne peut ni être négative ni dépasser le comptage."""
    if comptees is None:
        raise ValueError("Comptez d'abord les espèces (montant obligatoire).")
    if remises is None:
        raise ValueError("Indiquez les espèces remises au responsable (0 si rien).")
    if float(remises) < 0:
        raise ValueError("Les espèces remises ne peuvent pas être négatives.")
    if float(remises) - float(comptees) > 0.0005:
        raise ValueError("Les espèces remises (%s) dépassent les espèces comptées (%s)."
                         % (remises, comptees))


def _ligne_echange(prefixe, texte):
    return "[%s] %s — %s" % (str(now_datetime())[:16], prefixe, (texte or "").strip() or "—")


# ── Notifications (jamais bloquantes) ────────────────────────────────────────

def _prevenir(user, sujet, doctype, name):
    if not user or user == frappe.session.user:
        return
    try:
        frappe.get_doc({"doctype": "Notification Log", "for_user": user, "type": "Alert",
                        "subject": sujet, "document_type": doctype, "document_name": name,
                        }).insert(ignore_permissions=True)
        frappe.publish_realtime(EVENEMENT_TEMPS_REEL, {"name": name}, user=user, after_commit=True)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "caisse_collecte: notification")


def _collecteurs():
    c = config()
    users = set(c["responsables"])
    users.update(c["delegues"])
    return users


def notifier_collecteurs(doc, rouverte=False):
    sujet = "%s : %s (%s) — %s DT remis" % (
        "Comptage modifié par l'employé, à recollecter" if rouverte else "Caisse à collecter",
        doc.caisse, doc.date_cloture, CL._fmt_montant(doc.especes_remises))
    for u in _collecteurs() | ({doc.get("collecte_par_precedent")} - {None}):
        _prevenir(u, sujet, "Cloture Caisse", doc.name)


def justifications_depuis_controles(texte):
    """Inverse du format stocké (« libellé\n  → justification ») : {libellé: justification},
    pour pré-remplir le dialogue à la réouverture. Fonction pure."""
    out, libelle = {}, None
    for ligne in (texte or "").splitlines():
        if ligne.startswith("  → "):
            if libelle:
                out[libelle] = ligne[4:].strip()
        elif ligne.strip():
            libelle = ligne.strip()
    return out


def controles_en_liste(texte):
    """Le texte stocké des points justifiés -> [{libelle, justification}] dans l'ordre, pour la carte
    de collecte. Fonction pure."""
    return [{"libelle": k, "justification": v} for k, v in justifications_depuis_controles(texte).items()]


def rouvrir_comptage(doc, valeurs):
    """L'employé rouvre son comptage (04/10/2026) : le brouillon garde son numéro, reprend les
    nouvelles valeurs, et tout ce que le collecteur avait saisi est effacé — il devra recollecter.
    Refusé dès que la clôture est validée (docstatus 1)."""
    if doc.docstatus != 0 or doc.statut not in STATUTS_EN_ATTENTE:
        frappe.throw(_("La clôture {0} est validée : elle ne se rouvre plus.").format(doc.name))
    avant = "remis %s DT, comptées %s DT, %s chèque(s), %s traite(s)" % (
        CL._fmt_montant(doc.especes_remises), CL._fmt_montant(doc.especes_comptees),
        cint(doc.nb_cheques_remis), cint(doc.nb_traites_remis))
    ancien_collecteur = doc.collecte_par
    justifs_modifiees = (valeurs.get("controles") or "") != (doc.controles or "")
    doc.update(valeurs)
    doc.collecte_par = None
    doc.collecte_le = None
    doc.especes_recues = 0
    doc.nb_cheques_recus = 0
    doc.nb_traites_recus = 0
    doc.ecart_remise = 0
    doc.statut = STATUT_A_COLLECTER
    doc.echanges = ((doc.echanges or "") + "\n" + _ligne_echange(
        "↩ %s rouvre et modifie son comptage (avant : %s)" % (frappe.session.user, avant),
        "maintenant : remis %s DT, comptées %s DT, %s chèque(s), %s traite(s)" % (
            CL._fmt_montant(doc.especes_remises), CL._fmt_montant(doc.especes_comptees),
            cint(doc.nb_cheques_remis), cint(doc.nb_traites_remis))
        + (" · justifications modifiées" if justifs_modifiees else ""))).strip()
    doc.flags.ignore_permissions = True
    doc.save(ignore_permissions=True)
    doc.collecte_par_precedent = ancien_collecteur
    return doc


# ── Finalisation (la seule porte vers docstatus 1) ───────────────────────────

def finaliser(doc, mesures=None, rapprochement=None, validation_seule=False):
    """Soumet la clôture (deux signatures, ou une seule marquée) et attache le PDF."""
    if validation_seule:
        doc.validation_seule = 1
        doc.collecte_par = None
        doc.collecte_le = None
    else:
        if not doc.collecte_par:
            frappe.throw(_("Clôture {0} : aucun collecteur — soumission refusée.").format(doc.name))
        doc.collecte_le = now_datetime()
    doc.statut = STATUT_VALIDEE
    doc.flags.collecte_ok = True
    doc.flags.ignore_permissions = True
    doc.save(ignore_permissions=True)
    doc.submit()

    m = mesures or CL._mesures(doc.caisse, doc.date_cloture)
    rap = rapprochement
    if rap is None and doc.caisse == CL.CAISSE_GLOBALE:
        rap = CL._rapprochement(doc.date_cloture)
    from frappe.utils.pdf import get_pdf
    from frappe.utils.file_manager import save_file
    pdf = get_pdf(CL._html_instantane(doc, m["data"], rap))
    save_file("caisse-%s-%s.pdf" % (doc.caisse.replace(" ", "_"), doc.date_cloture), pdf,
              "Cloture Caisse", doc.name, is_private=1)
    if not validation_seule:
        _prevenir(doc.valide_par, "Caisse %s du %s validée par %s%s" % (
            doc.caisse, doc.date_cloture, doc.collecte_par,
            (" — écart de remise %s DT" % CL._fmt_montant(doc.ecart_remise)) if flt(doc.ecart_remise) else ""),
            "Cloture Caisse", doc.name)
    return doc


def cloture_caisse_before_submit(doc, method=None):
    """Garde-fou : une Clôture Caisse ne se soumet que par la page (collecte ou
    validation seule) — jamais depuis le formulaire, même en System Manager."""
    if doc.flags.get("collecte_ok"):
        return
    frappe.throw(_("Une clôture de caisse se valide depuis la page Caisse journalière "
                   "(comptage de l'employé puis collecte par le responsable), jamais depuis ce formulaire."))


def passation_caisse_before_submit(doc, method=None):
    if doc.flags.get("collecte_ok"):
        return
    frappe.throw(_("Une passation se valide depuis la page Caisse journalière "
                   "(réception par le responsable de collecte), jamais depuis ce formulaire."))


def enregistrer_photo_remise(doc, photo, photo_nom=None):
    """La photo de l'enveloppe / du bordereau (data URL base64) attachée à la clôture."""
    if not photo:
        return
    from frappe.utils.file_manager import save_file
    contenu = photo.split(",", 1)[-1]
    f = save_file(photo_nom or "remise-%s-%s.jpg" % (doc.caisse.replace(" ", "_"), doc.date_cloture),
                  base64.b64decode(contenu), "Cloture Caisse", doc.name, is_private=1)
    doc.db_set("photo_remise", f.file_url, update_modified=False)
    doc.photo_remise = f.file_url


# ── Contrôles de la caisse globale ───────────────────────────────────────────

def controles_globale(date, data):
    """Pour « Tous les employés » : chaque caisse ayant encaissé des espèces ce
    jour-là doit être VALIDÉE (collectée) ; sinon la direction justifie."""
    date = getdate(date)
    points = []
    recap = (data or {}).get("recap") or {}
    etats = {r.caisse: r for r in frappe.get_all(
        "Cloture Caisse", filters={"date_cloture": date, "docstatus": ["<", 2]},
        fields=["caisse", "statut", "docstatus", "name"])}
    for e in recap.get("par_employe") or []:
        especes = flt((e.get("par_mode") or {}).get("Espèces"), 3)
        if especes <= 0:
            continue
        cl = etats.get(e["employe"])
        if cl and cl.docstatus == 1:
            continue
        if cl:
            libelle = "Caisse %s : %s DT d'espèces, comptée mais pas encore collectée (%s, %s)" % (
                e["employe"], CL._fmt_montant(especes), cl.name, cl.statut)
        else:
            libelle = "Caisse %s : %s DT d'espèces, pas comptée ni collectée" % (
                e["employe"], CL._fmt_montant(especes))
        points.append({"cle": "caisse_non_collectee:%s" % e["employe"], "type": "caisse_non_collectee",
                       "bloquant": 0, "caisse": e["employe"], "montant": especes, "libelle": libelle})
    return points


# ── Côté collecteur ──────────────────────────────────────────────────────────

def _dict_cloture(c):
    d = {
        "name": c.name, "caisse": c.caisse, "date": str(c.date_cloture), "statut": c.statut,
        "valide_par": c.valide_par, "valide_par_nom": frappe.utils.get_fullname(c.valide_par) or c.valide_par,
        "compte_le": str(c.compte_le or "")[:16],
        "especes_comptees": flt(c.especes_comptees, 3), "solde_theorique": flt(c.solde_theorique, 3),
        "ecart": flt(c.ecart, 3),
        "especes_remises": flt(c.especes_remises, 3), "fond_conserve": flt(c.fond_conserve, 3),
        "nb_cheques_remis": cint(c.nb_cheques_remis), "nb_traites_remis": cint(c.nb_traites_remis),
        "total_cheques": flt(c.total_cheques, 3), "photo_remise": c.photo_remise,
        # Les colonnes Currency valent 0 tant que personne n'a saisi : « reçu » n'existe qu'après un passage du collecteur.
        "especes_recues": flt(c.especes_recues, 3) if c.collecte_par else None,
        "nb_cheques_recus": cint(c.nb_cheques_recus), "nb_traites_recus": cint(c.nb_traites_recus),
        "ecart_remise": flt(c.ecart_remise, 3), "tours_ecart": cint(c.tours_ecart),
        "echanges": c.echanges or "", "collecte_par": c.collecte_par, "note": c.note or "",
        "mienne": c.valide_par == frappe.session.user or c.caisse == _caisse_de(frappe.session.user),
        # Les points de contrôle et leurs justifications : le collecteur les LIT avant de confirmer.
        "controles": controles_en_liste(c.controles),
        "justif_contestee": c.statut == STATUT_JUSTIF,
    }
    d["forcable"] = cint(c.tours_ecart) >= 1 and c.statut == STATUT_A_COLLECTER
    return d


@frappe.whitelist()
def a_collecter(date=None):
    """Les clôtures en attente de collecte (toutes dates ≤ `date`) + les caisses
    du jour qui ont encaissé des espèces sans avoir compté."""
    frappe.only_for(CL.ROLES)
    role = _exiger_collecteur()
    date = getdate(date or nowdate())
    rows = frappe.get_all("Cloture Caisse",
                          filters={"docstatus": 0, "statut": ["in", STATUTS_EN_ATTENTE],
                                   "date_cloture": ["<=", date]},
                          fields=["*"], order_by="date_cloture asc, compte_le asc")
    clotures = [_dict_cloture(frappe._dict(r)) for r in rows]
    for c in clotures:
        # Caisse comptée par le collecteur lui-même (pour un employé absent, ou la sienne) :
        #   - délégué / direction : versée telle quelle dans sa passation ;
        #   - titulaire : validation DIRECTE, sans seconde signature (demande 04/10/2026).
        c["auto_passation"] = c["mienne"] and role != "titulaire"
        c["validation_directe"] = c["mienne"] and role == "titulaire"
        c["collectable"] = True

    data = CL.get_data(str(date), str(date), employe="")
    comptees = {c["caisse"] for c in frappe.get_all(
        "Cloture Caisse", filters={"date_cloture": date, "docstatus": ["<", 2]}, fields=["caisse"])}
    non_comptees = []
    for e in (data.get("recap") or {}).get("par_employe") or []:
        especes = flt((e.get("par_mode") or {}).get("Espèces"), 3)
        if especes > 0 and e["employe"] not in comptees:
            non_comptees.append({"caisse": e["employe"], "especes": especes, "total": flt(e.get("total"), 3)})
    return {"role": role, "date": str(date), "clotures": clotures, "non_comptees": non_comptees,
            "par_delegation": role != "titulaire"}


def _passation_ouverte(delegue):
    nom = frappe.db.get_value("Passation Caisse", {"delegue": delegue, "docstatus": 0,
                                                    "statut": ["in", (PAS_OUVERTE, PAS_A_REMETTRE, PAS_ECART)]})
    if nom:
        return frappe.get_doc("Passation Caisse", nom)
    doc = frappe.get_doc({"doctype": "Passation Caisse", "delegue": delegue,
                          "responsable": config()["responsable"], "statut": PAS_OUVERTE,
                          "date_debut": nowdate()})
    doc.insert(ignore_permissions=True)
    return doc


def _recalculer_passation(p):
    p.total_especes = flt(sum(flt(l.especes) for l in p.clotures), 3)
    p.total_cheques = sum(cint(l.nb_cheques) for l in p.clotures)
    p.total_traites = sum(cint(l.nb_traites) for l in p.clotures)
    p.date_fin = max([getdate(l.date_cloture) for l in p.clotures] or [getdate(nowdate())])


def _verser_dans_passation(cl, delegue):
    p = _passation_ouverte(delegue)
    if p.statut in (PAS_A_REMETTRE, PAS_ECART):
        # Une passation déjà annoncée au titulaire repart « Ouverte » : son total change.
        p.statut = PAS_OUVERTE
        p.echanges = ((p.echanges or "") + "\n" + _ligne_echange(
            "Caisse ajoutée après l'annonce de remise", "%s (%s)" % (cl.caisse, cl.date_cloture))).strip()
    p.append("clotures", {"cloture": cl.name, "caisse": cl.caisse, "date_cloture": cl.date_cloture,
                          "employe": cl.valide_par, "especes": flt(cl.especes_recues, 3),
                          "nb_cheques": cint(cl.nb_cheques_recus), "nb_traites": cint(cl.nb_traites_recus)})
    _recalculer_passation(p)
    p.save(ignore_permissions=True)
    cl.par_delegation = 1
    cl.passation = p.name
    return p


@frappe.whitelist()
def collecter(name, especes_recues, nb_cheques_recus=0, nb_traites_recus=0, commentaire=None, forcer=0):
    """Le responsable (ou un délégué) saisit ce qu'il a REÇU. Identique au
    déclaré -> validée ; différent -> « Écart de remise » renvoyé à l'employé ;
    `forcer` (après au moins un tour d'écart) -> le responsable tranche."""
    frappe.only_for(CL.ROLES)
    role = _exiger_collecteur()
    cl = frappe.get_doc("Cloture Caisse", name)
    if cl.docstatus != 0 or cl.statut not in STATUTS_EN_ATTENTE:
        frappe.throw(_("La clôture {0} n'est pas en attente de collecte ({1}).").format(name, cl.statut))
    moi = frappe.session.user
    auto = cl.valide_par == moi or cl.caisse == _caisse_de(moi)
    if auto and role == "titulaire":
        # Le responsable a compté lui-même (employé absent) : il valide DIRECTEMENT, une seule
        # signature, tracée comme telle — la remise n'a pas de sens, l'argent est déjà chez lui.
        cl.echanges = ((cl.echanges or "") + "\n" + _ligne_echange(
            "✅ %s valide directement (comptée par lui-même, sans seconde signature)" % moi, commentaire)).strip()
        cl.collecte_par = moi
        finaliser(cl, validation_seule=True)
        cl.db_set("collecte_par", moi, update_modified=False)
        cl.db_set("collecte_le", now_datetime(), update_modified=False)
        frappe.db.commit()
        return {"name": cl.name, "statut": cl.statut, "ecart_remise": 0, "validation_directe": True}

    recu = {"especes": flt(especes_recues, 3), "nb_cheques": cint(nb_cheques_recus),
            "nb_traites": cint(nb_traites_recus)}
    declare = {"especes": flt(cl.especes_remises, 3), "nb_cheques": cint(cl.nb_cheques_remis),
               "nb_traites": cint(cl.nb_traites_remis)}
    if auto:
        # Sa propre caisse versée dans la passation : rien à confronter, le titulaire tranchera à la remise.
        recu = dict(declare)

    cl.especes_recues = recu["especes"]
    cl.nb_cheques_recus = recu["nb_cheques"]
    cl.nb_traites_recus = recu["nb_traites"]
    cl.ecart_remise = ecart_remise(declare["especes"], recu["especes"])
    cl.collecte_par = moi
    identique = remise_identique(declare, recu)
    if cl.statut == STATUT_JUSTIF:
        cl.echanges = ((cl.echanges or "") + "\n" + _ligne_echange(
            "📋 %s collecte quand même" % moi, "justifications acceptées en l'état")).strip()

    if not identique and not (cint(forcer) and cint(cl.tours_ecart) >= 1):
        cl.statut = STATUT_ECART
        cl.tours_ecart = cint(cl.tours_ecart) + 1
        cl.echanges = ((cl.echanges or "") + "\n" + _ligne_echange(
            "⚠️ %s a reçu %s DT, %s chèque(s), %s traite(s) au lieu de %s DT, %s, %s" % (
                moi, CL._fmt_montant(recu["especes"]), recu["nb_cheques"], recu["nb_traites"],
                CL._fmt_montant(declare["especes"]), declare["nb_cheques"], declare["nb_traites"]),
            commentaire)).strip()
        cl.flags.ignore_permissions = True
        cl.save(ignore_permissions=True)
        _prevenir(cl.valide_par, "Remise contestée — caisse %s du %s : %s a reçu %s DT au lieu de %s" % (
            cl.caisse, cl.date_cloture, moi, CL._fmt_montant(recu["especes"]),
            CL._fmt_montant(declare["especes"])), "Cloture Caisse", cl.name)
        frappe.db.commit()
        return {"name": cl.name, "statut": cl.statut, "ecart_remise": cl.ecart_remise}

    if not identique:
        cl.echanges = ((cl.echanges or "") + "\n" + _ligne_echange(
            "⚖️ %s tranche : écart de remise %s DT retenu" % (moi, CL._fmt_montant(cl.ecart_remise)),
            commentaire)).strip()
    elif commentaire:
        cl.echanges = ((cl.echanges or "") + "\n" + _ligne_echange("✅ %s" % moi, commentaire)).strip()

    if role != "titulaire":
        _verser_dans_passation(cl, moi)
    finaliser(cl)
    frappe.db.commit()
    return {"name": cl.name, "statut": cl.statut, "ecart_remise": cl.ecart_remise,
            "passation": cl.passation}


@frappe.whitelist()
def contester_justifications(name, commentaire):
    """Le collecteur conteste les justifications des points de contrôle (05/10/2026) : la clôture
    passe « Justification contestée », l'employé est prévenu, rouvre son comptage et les corrige
    (retour « À collecter »). Le collecteur garde le dernier mot : collecter = les accepter."""
    frappe.only_for(CL.ROLES)
    _exiger_collecteur()
    cl = frappe.get_doc("Cloture Caisse", name)
    if cl.docstatus != 0 or cl.statut != STATUT_A_COLLECTER:
        frappe.throw(_("La clôture {0} n'attend pas la collecte ({1}) : rien à contester.").format(name, cl.statut))
    if not (cl.controles or "").strip():
        frappe.throw(_("La clôture {0} n'a aucun point de contrôle justifié.").format(name))
    moi = frappe.session.user
    if cl.valide_par == moi:
        frappe.throw(_("Vous avez compté cette caisse vous-même : rouvrez votre comptage pour corriger."))
    commentaire = " ".join((commentaire or "").split())
    if len(commentaire) < 5:
        frappe.throw(_("Dites à l'employé ce qui ne va pas (commentaire obligatoire)."))
    cl.statut = STATUT_JUSTIF
    cl.echanges = ((cl.echanges or "") + "\n" + _ligne_echange(
        "📋 %s conteste les justifications" % moi, commentaire)).strip()
    cl.flags.ignore_permissions = True
    cl.save(ignore_permissions=True)
    _prevenir(cl.valide_par, "Justifications contestées — caisse %s du %s : %s" % (
        cl.caisse, cl.date_cloture, commentaire[:80]), "Cloture Caisse", cl.name)
    frappe.db.commit()
    return {"name": cl.name, "statut": cl.statut}


@frappe.whitelist()
def repondre_ecart(name, accepter, commentaire=None):
    """L'employé répond à un écart de remise : accepte le chiffre du responsable
    (-> validée, l'écart reste tracé) ou maintient son comptage (-> le
    responsable revalide et tranche)."""
    frappe.only_for(CL.ROLES)
    cl = frappe.get_doc("Cloture Caisse", name)
    if not peut_agir_sur(cl):
        frappe.throw(_("Seul {0} (ou le titulaire de la caisse) peut répondre à cet écart.").format(cl.valide_par))
    if cl.docstatus != 0 or cl.statut != STATUT_ECART:
        frappe.throw(_("La clôture {0} n'est pas en écart de remise.").format(name))
    if cint(accepter):
        cl.echanges = ((cl.echanges or "") + "\n" + _ligne_echange(
            "✅ %s accepte le chiffre du responsable (%s DT, %s chèque(s), %s traite(s))" % (
                frappe.session.user, CL._fmt_montant(cl.especes_recues), cint(cl.nb_cheques_recus),
                cint(cl.nb_traites_recus)), commentaire)).strip()
        # L'écart reste tracé (ecart_remise) ; le fond conservé suit ce qui est réellement parti.
        cl.fond_conserve = fond_conserve(cl.especes_comptees, cl.especes_recues)
        if cl.collecte_par and role_collecte(cl.collecte_par) != "titulaire":
            _verser_dans_passation(cl, cl.collecte_par)
        finaliser(cl)
        frappe.db.commit()
        return {"name": cl.name, "statut": cl.statut}
    cl.statut = STATUT_A_COLLECTER
    cl.echanges = ((cl.echanges or "") + "\n" + _ligne_echange(
        "✋ %s maintient son comptage (%s DT remis)" % (frappe.session.user, CL._fmt_montant(cl.especes_remises)),
        commentaire)).strip()
    cl.flags.ignore_permissions = True
    cl.save(ignore_permissions=True)
    _prevenir(cl.collecte_par, "Caisse %s du %s : %s maintient son comptage — à trancher" % (
        cl.caisse, cl.date_cloture, frappe.session.user), "Cloture Caisse", cl.name)
    frappe.db.commit()
    return {"name": cl.name, "statut": cl.statut}


@frappe.whitelist()
def annuler_comptage(name):
    """L'employé reprend son comptage tant que personne ne l'a touché."""
    frappe.only_for(CL.ROLES)
    cl = frappe.get_doc("Cloture Caisse", name)
    if not peut_agir_sur(cl):
        frappe.throw(_("Seul {0} (ou le titulaire de la caisse) peut annuler ce comptage.").format(cl.valide_par))
    if cl.docstatus != 0 or cl.statut != STATUT_A_COLLECTER or cint(cl.tours_ecart):
        frappe.throw(_("Ce comptage a déjà été traité par le responsable : il ne s'annule plus."))
    frappe.delete_doc("Cloture Caisse", name, ignore_permissions=True, force=True)
    frappe.db.commit()
    return {"supprimee": name}


# ── Passation délégué -> titulaire ───────────────────────────────────────────

def _dict_passation(p):
    return {
        "name": p.name, "delegue": p.delegue, "delegue_nom": frappe.utils.get_fullname(p.delegue) or p.delegue,
        "responsable": p.responsable, "statut": p.statut,
        "date_debut": str(p.date_debut or ""), "date_fin": str(p.date_fin or ""),
        "total_especes": flt(p.total_especes, 3), "total_cheques": cint(p.total_cheques),
        "total_traites": cint(p.total_traites),
        "especes_recues": flt(p.especes_recues, 3) if cint(p.tours_ecart) or p.statut == PAS_VALIDEE else None,
        "nb_cheques_recus": cint(p.nb_cheques_recus), "nb_traites_recus": cint(p.nb_traites_recus),
        "ecart": flt(p.ecart, 3), "tours_ecart": cint(p.tours_ecart), "echanges": p.echanges or "",
        "lignes": [{"cloture": l.cloture, "caisse": l.caisse, "date": str(l.date_cloture),
                    "employe": l.employe, "employe_nom": frappe.utils.get_fullname(l.employe) or l.employe,
                    "especes": flt(l.especes, 3),
                    "nb_cheques": cint(l.nb_cheques), "nb_traites": cint(l.nb_traites)}
                   for l in p.clotures],
        "mienne": p.delegue == frappe.session.user,
        "forcable": cint(p.tours_ecart) >= 1 and p.statut in (PAS_OUVERTE, PAS_A_REMETTRE),
    }


@frappe.whitelist()
def passations():
    """Les passations en cours : celles que je dois remettre (délégué) et celles
    que je dois recevoir (titulaire / direction)."""
    frappe.only_for(CL.ROLES)
    role = role_collecte()
    if not role:
        return {"role": None, "a_remettre": [], "a_recevoir": []}
    rows = frappe.get_all("Passation Caisse", filters={"docstatus": 0}, fields=["name"],
                          order_by="date_debut asc")
    docs = [frappe.get_doc("Passation Caisse", r.name) for r in rows]
    moi = frappe.session.user
    a_remettre = [_dict_passation(p) for p in docs if p.delegue == moi]
    a_recevoir = [_dict_passation(p) for p in docs
                  if p.delegue != moi and role in ("titulaire", "direction")]
    return {"role": role, "a_remettre": a_remettre, "a_recevoir": a_recevoir}


@frappe.whitelist()
def remettre_passation(name, note=None):
    """Le délégué annonce qu'il remet le tout au titulaire."""
    frappe.only_for(CL.ROLES)
    p = frappe.get_doc("Passation Caisse", name)
    if p.delegue != frappe.session.user:
        frappe.throw(_("Seul le délégué {0} peut remettre cette passation.").format(p.delegue))
    if p.docstatus != 0 or p.statut not in (PAS_OUVERTE, PAS_ECART):
        frappe.throw(_("La passation {0} n'est pas remettable ({1}).").format(name, p.statut))
    p.statut = PAS_A_REMETTRE
    p.remise_le = now_datetime()
    if note:
        p.note = ((p.note or "") + "\n" + note.strip()).strip()
    p.flags.ignore_permissions = True
    p.save(ignore_permissions=True)
    for u in set(config()["responsables"]) | {p.responsable} | set(CL.DIRECTION):
        _prevenir(u, "Passation %s : %s remet %s DT, %s chèque(s), %s traite(s)" % (
            p.name, p.delegue, CL._fmt_montant(p.total_especes), cint(p.total_cheques), cint(p.total_traites)),
            "Passation Caisse", p.name)
    frappe.db.commit()
    return _dict_passation(p)


@frappe.whitelist()
def valider_passation(name, especes_recues, nb_cheques_recus=0, nb_traites_recus=0, commentaire=None, forcer=0):
    """Le titulaire (ou la direction) reçoit du délégué. Même boucle que la collecte."""
    frappe.only_for(CL.ROLES)
    role = _exiger_collecteur()
    p = frappe.get_doc("Passation Caisse", name)
    if p.delegue == frappe.session.user:
        frappe.throw(_("Vous ne pouvez pas recevoir votre propre passation."))
    if role not in ("titulaire", "direction"):
        frappe.throw(_("Seul le responsable de collecte (ou la direction) reçoit une passation."))
    if p.docstatus != 0 or p.statut == PAS_VALIDEE:
        frappe.throw(_("La passation {0} n'est pas en attente.").format(name))
    if not p.clotures:
        frappe.throw(_("La passation {0} est vide.").format(name))
    moi = frappe.session.user
    recu = {"especes": flt(especes_recues, 3), "nb_cheques": cint(nb_cheques_recus),
            "nb_traites": cint(nb_traites_recus)}
    declare = {"especes": flt(p.total_especes, 3), "nb_cheques": cint(p.total_cheques),
               "nb_traites": cint(p.total_traites)}
    p.especes_recues, p.nb_cheques_recus, p.nb_traites_recus = recu["especes"], recu["nb_cheques"], recu["nb_traites"]
    p.ecart = ecart_remise(declare["especes"], recu["especes"])
    p.responsable = moi
    identique = remise_identique(declare, recu)
    if not identique and not (cint(forcer) and cint(p.tours_ecart) >= 1):
        p.statut = PAS_ECART
        p.tours_ecart = cint(p.tours_ecart) + 1
        p.echanges = ((p.echanges or "") + "\n" + _ligne_echange(
            "⚠️ %s a reçu %s DT, %s chèque(s), %s traite(s) au lieu de %s DT, %s, %s" % (
                moi, CL._fmt_montant(recu["especes"]), recu["nb_cheques"], recu["nb_traites"],
                CL._fmt_montant(declare["especes"]), declare["nb_cheques"], declare["nb_traites"]),
            commentaire)).strip()
        p.flags.ignore_permissions = True
        p.save(ignore_permissions=True)
        _prevenir(p.delegue, "Passation %s contestée : %s a reçu %s DT au lieu de %s" % (
            p.name, moi, CL._fmt_montant(recu["especes"]), CL._fmt_montant(declare["especes"])),
            "Passation Caisse", p.name)
        frappe.db.commit()
        return _dict_passation(p)
    if not identique:
        p.echanges = ((p.echanges or "") + "\n" + _ligne_echange(
            "⚖️ %s tranche : écart %s DT retenu" % (moi, CL._fmt_montant(p.ecart)), commentaire)).strip()
    elif commentaire:
        p.echanges = ((p.echanges or "") + "\n" + _ligne_echange("✅ %s" % moi, commentaire)).strip()
    _soumettre_passation(p)
    frappe.db.commit()
    return _dict_passation(p)


def _soumettre_passation(p):
    p.statut = PAS_VALIDEE
    p.validee_le = now_datetime()
    if not p.date_fin:
        p.date_fin = nowdate()
    p.flags.collecte_ok = True
    p.flags.ignore_permissions = True
    p.save(ignore_permissions=True)
    p.submit()
    _prevenir(p.delegue, "Passation %s validée par %s%s" % (
        p.name, p.responsable, (" — écart %s DT" % CL._fmt_montant(p.ecart)) if flt(p.ecart) else ""),
        "Passation Caisse", p.name)


@frappe.whitelist()
def repondre_ecart_passation(name, accepter, commentaire=None):
    frappe.only_for(CL.ROLES)
    p = frappe.get_doc("Passation Caisse", name)
    if p.delegue != frappe.session.user:
        frappe.throw(_("Seul le délégué {0} peut répondre à cet écart.").format(p.delegue))
    if p.docstatus != 0 or p.statut != PAS_ECART:
        frappe.throw(_("La passation {0} n'est pas en écart.").format(name))
    if cint(accepter):
        p.echanges = ((p.echanges or "") + "\n" + _ligne_echange(
            "✅ %s accepte le chiffre du responsable (%s DT)" % (frappe.session.user, CL._fmt_montant(p.especes_recues)),
            commentaire)).strip()
        _soumettre_passation(p)
        frappe.db.commit()
        return _dict_passation(p)
    p.statut = PAS_A_REMETTRE
    p.echanges = ((p.echanges or "") + "\n" + _ligne_echange(
        "✋ %s maintient ses totaux (%s DT)" % (frappe.session.user, CL._fmt_montant(p.total_especes)),
        commentaire)).strip()
    p.flags.ignore_permissions = True
    p.save(ignore_permissions=True)
    _prevenir(p.responsable, "Passation %s : %s maintient ses totaux — à trancher" % (p.name, p.delegue),
              "Passation Caisse", p.name)
    frappe.db.commit()
    return _dict_passation(p)


# ── Résumés pour les bandeaux (page caisse, Ma journée) ──────────────────────

@frappe.whitelist()
def contexte(date=None):
    """Ce que la page doit afficher pour l'utilisateur connecté : son rôle de
    collecte, le nombre de caisses à collecter, ses propres clôtures contestées,
    les passations à remettre / à recevoir."""
    frappe.only_for(CL.ROLES)
    return resume(frappe.session.user, date)


def resume(user, date=None):
    role = role_collecte(user)
    date = getdate(date or nowdate())
    out = {"role": role, "photo_obligatoire": config()["photo_obligatoire"],
           "responsable": config()["responsable"],
           "a_collecter": 0, "mes_ecarts": [], "passations_a_remettre": 0, "passations_a_recevoir": 0}
    if role:
        out["a_collecter"] = frappe.db.count("Cloture Caisse", {
            "docstatus": 0, "statut": ["in", STATUTS_EN_ATTENTE], "valide_par": ["!=", user],
            "date_cloture": ["<=", date]})
        out["a_collecter"] += frappe.db.count("Cloture Caisse", {
            "docstatus": 0, "statut": STATUT_A_COLLECTER, "valide_par": user,
            "date_cloture": ["<=", date]}) if role != "titulaire" else 0
        pas = frappe.get_all("Passation Caisse", filters={"docstatus": 0}, fields=["delegue", "statut"])
        out["passations_a_remettre"] = sum(1 for p in pas if p.delegue == user)
        if role in ("titulaire", "direction"):
            out["passations_a_recevoir"] = sum(1 for p in pas if p.delegue != user)
    out["mes_ecarts"] = [
        {"name": c.name, "caisse": c.caisse, "date": str(c.date_cloture),
         "especes_remises": flt(c.especes_remises, 3), "especes_recues": flt(c.especes_recues, 3),
         "collecte_par": c.collecte_par}
        for c in frappe.get_all("Cloture Caisse",
                                filters={"docstatus": 0, "statut": STATUT_ECART, "valide_par": user},
                                fields=["name", "caisse", "date_cloture", "especes_remises",
                                        "especes_recues", "collecte_par"])]
    ma_caisse = _caisse_de(user)
    out["mes_justifs"] = [
        {"name": c.name, "caisse": c.caisse, "date": str(c.date_cloture)}
        for c in frappe.get_all("Cloture Caisse", filters={"docstatus": 0, "statut": STATUT_JUSTIF},
                                fields=["name", "caisse", "date_cloture", "valide_par"])
        if c.valide_par == user or (ma_caisse and c.caisse == ma_caisse)]
    out["mes_passations_ecart"] = [
        {"name": p.name, "total_especes": flt(p.total_especes, 3), "especes_recues": flt(p.especes_recues, 3)}
        for p in frappe.get_all("Passation Caisse", filters={"docstatus": 0, "statut": PAS_ECART, "delegue": user},
                                fields=["name", "total_especes", "especes_recues"])]
    return out


def plancher_report(date, date_depart):
    """La date à partir de laquelle une clôture peut servir de report pour une
    clôture datée `date` : la date de départ si elle est fixée et atteinte, sinon
    rien (tout l'historique compte). Fonction pure."""
    if not date_depart:
        return None
    if getdate(date) < getdate(date_depart):
        return None
    return getdate(date_depart)
