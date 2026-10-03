"""Assistant « Campagne SMS » (page /app/campagne-sms) — demande 03/10/2026 : « rendre l'UI Compagne SMS plus interactive
et plus simple ». Même DocType qu'avant (Compagne SMS + lignes SMS client), mais un seul écran :
  1. CIBLER — groupes de clients et secteurs avec les effectifs, consentement SMS respecté, fiches non autorisées et
     clients gérés par le partenaire écartés, un client ajouté à la main ; chaque exclusion a son motif.
  2. LISTE — une ligne par client, cochable, numéros valides ; les exclus restent visibles, grisés.
  3. MESSAGE — balises insérables ({{ nom_client }} …), coût réel (GSM / unicode, segments × numéros), aperçu rendu sur
     les premiers clients, SMS de test vers un numéro, puis envoi en tâche de fond avec progression et verdict PAR NUMÉRO
     (la campagne est figée — soumise — à la fin). « Refaire avec les échecs » repart des numéros en échec.
⛔ En developer_mode tout est SIMULÉ (simulation_dev), comme les autres envois de l'app.
"""
from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, nowdate

from customization_app import modeles_sms
from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import (
    envoyer_sms_verifie, get_ristourne_acc, get_ristourne_uti, is_customer_authorized_from_dict, normaliser_sms,
    simulation_dev, traiter_numero_tel)

DOCTYPE = "Compagne SMS"
ROLES = ("System Manager", "Sales Manager")
BALISES = ["{{ nom_client }}", "{{ group_client }}", "{{ secteur }}", "{{ ristourne_acc }}", "{{ ristourne_uti }}"]
APERCU_MAX = 5
LOT_REALTIME = 5


def _acces():
    if not set(frappe.get_roles()) & set(ROLES):
        frappe.throw(_("Réservé aux responsables (System Manager, Sales Manager)."), frappe.PermissionError)


def _json(v, defaut):
    if isinstance(v, str):
        try:
            return json.loads(v) if v.strip() else defaut
        except Exception:
            return defaut
    return v if v is not None else defaut


# ── 1. Cibler ────────────────────────────────────────────────────────────────

@frappe.whitelist()
def options():
    _acces()
    groupes = frappe.db.sql("""select customer_group nom, count(*) clients,
                                      sum(ifnull(custom_liste_telephone, '') <> '' and ifnull(custom_envoi_sms, 'Oui') <> 'Non') joignables
                               from tabCustomer where disabled = 0 group by customer_group order by clients desc""", as_dict=True)
    secteurs = frappe.db.sql_list("""select distinct custom_secteur from tabAddress where ifnull(custom_secteur, '') <> '' order by custom_secteur""")
    recentes = frappe.get_all(DOCTYPE, fields=["name", "titre", "statut", "envoyes", "echecs", "invalides", "envoye_le", "creation", "message", "docstatus"],
                              order_by="creation desc", limit=12)
    emp = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "cell_number")
    return {"groupes": groupes, "secteurs": secteurs, "recentes": recentes, "balises": BALISES,
            "simulation": simulation_dev(), "mon_numero": (traiter_numero_tel(emp or "") or [""])[0]}


def _secteurs_clients(noms):
    if not noms:
        return {}
    out = {}
    for c, s in frappe.db.sql("""select dl.link_name, a.custom_secteur from `tabDynamic Link` dl join tabAddress a on a.name = dl.parent
                                 where dl.parenttype = 'Address' and dl.link_doctype = 'Customer' and dl.link_name in %s
                                   and ifnull(a.custom_secteur, '') <> '' order by a.is_primary_address desc, a.creation""", (tuple(noms),)):
        out.setdefault(c, s)
    return out


def classer_client(c: dict, secteur: str, filtres: dict, geres: set) -> str | None:
    """Le motif d'exclusion d'un client, ou None s'il est ciblé. PURE.
    `c` : name, customer_group, custom_liste_telephone, custom_envoi_sms, custom_intéressé_par_le_service_entretien,
    custom_autoriser_accès_fiche_client (+ champs d'autorisation) ; `filtres` : consent, autorisation, exclure_partenaire,
    interesse, secteurs."""
    if filtres.get("secteurs") and secteur not in filtres["secteurs"]:
        return "hors secteurs choisis"
    if filtres.get("interesse") and (c.get("custom_intéressé_par_le_service_entretien") or "") != filtres["interesse"]:
        return "intéressé entretien ≠ %s" % filtres["interesse"]
    if filtres.get("consent", True) and (c.get("custom_envoi_sms") or "Oui") == "Non":
        return "a refusé les SMS"
    if filtres.get("autorisation", True) and not is_customer_authorized_from_dict(c):
        return "fiche client non autorisée"
    if filtres.get("exclure_partenaire", True) and c["name"] in geres:
        return "géré par le partenaire"
    if not traiter_numero_tel(c.get("custom_liste_telephone") or ""):
        return "aucun numéro mobile valide"
    return None


@frappe.whitelist()
def cibler(filtres=None):
    """La cible : clients des groupes choisis (+ clients ajoutés à la main), chacun avec ses numéros valides ou son motif
    d'exclusion. {lignes: [...], stats: {...}}"""
    _acces()
    f = _json(filtres, {})
    from customization_app import partenaire_clients
    champs = ["name", "customer_name", "customer_group", "custom_liste_telephone", "custom_envoi_sms",
              "custom_intéressé_par_le_service_entretien", "custom_autoriser_accès_fiche_client"]
    clients = []
    if f.get("groupes"):
        clients += frappe.get_all("Customer", filters={"customer_group": ["in", f["groupes"]], "disabled": 0}, fields=champs, limit_page_length=0)
    ajoutes = [a for a in (f.get("clients") or []) if a]
    if ajoutes:
        deja = {c.name for c in clients}
        clients += [c for c in frappe.get_all("Customer", filters={"name": ["in", ajoutes]}, fields=champs) if c.name not in deja]
    if not clients:
        return {"lignes": [], "stats": {"total": 0, "cibles": 0, "numeros": 0, "exclus": {}}}
    secteurs = _secteurs_clients([c.name for c in clients])
    geres = partenaire_clients.clients_geres() if f.get("exclure_partenaire", True) else set()
    lignes, exclus, numeros = [], {}, 0
    for c in sorted(clients, key=lambda x: (x.customer_group or "", x.customer_name or "")):
        secteur = secteurs.get(c.name, "")
        force = c.name in ajoutes
        motif = None if force and traiter_numero_tel(c.custom_liste_telephone or "") else classer_client(c, secteur, f, geres)
        nums = traiter_numero_tel(c.custom_liste_telephone or "") if not motif else []
        if motif:
            exclus[motif] = exclus.get(motif, 0) + 1
        else:
            numeros += len(nums)
        lignes.append({"client": c.name, "nom": c.customer_name or c.name, "groupe": c.customer_group or "", "secteur": secteur,
                       "numeros": nums, "exclu": motif, "ajoute": force})
    return {"lignes": lignes, "stats": {"total": len(lignes), "cibles": len(lignes) - sum(exclus.values()), "numeros": numeros, "exclus": exclus}}


@frappe.whitelist()
def rechercher_clients(texte):
    _acces()
    q = "%%%s%%" % (texte or "").strip()
    if len(q) < 5:
        return []
    return frappe.get_all("Customer", or_filters=[["name", "like", q], ["customer_name", "like", q], ["custom_liste_telephone", "like", q]],
                          fields=["name", "customer_name", "customer_group", "custom_liste_telephone"], limit=10)


# ── 2. Message ───────────────────────────────────────────────────────────────

def contexte(client: str, nom: str, groupe: str, secteur: str = "") -> dict:
    return {"nom_client": nom or "", "group_client": groupe or "", "secteur": secteur or "",
            "ristourne_acc": get_ristourne_acc(client) if client else 0.0, "ristourne_uti": get_ristourne_uti(client) if client else 0.0}


def rendre(message: str, ctx: dict) -> str:
    try:
        return frappe.render_template(message or "", ctx)
    except Exception as e:
        frappe.throw(_("Balise ou syntaxe invalide dans le message : {0}").format(str(e)[:120]))


@frappe.whitelist()
def apercu(message, lignes=None):
    """Le message rendu pour les premiers clients ciblés (+ coût), et l'estimation globale segments × numéros."""
    _acces()
    lignes = [l for l in _json(lignes, []) if not l.get("exclu")]
    out, total_segments, total_numeros = [], 0, 0
    for l in lignes[:APERCU_MAX]:
        texte = rendre(message, contexte(l.get("client"), l.get("nom"), l.get("groupe"), l.get("secteur")))
        out.append({"nom": l.get("nom"), "texte": texte, "analyse": modeles_sms.analyser(texte)})
    # Estimation : le rendu change peu d'un client à l'autre (nom) → segments du plus long aperçu × numéros.
    seg = max((a["analyse"]["segments"] for a in out), default=modeles_sms.analyser(message or "")["segments"])
    total_numeros = sum(len(l.get("numeros") or []) for l in lignes)
    total_segments = seg * total_numeros
    return {"apercus": out, "segments_par_sms": seg, "numeros": total_numeros, "segments": total_segments,
            "analyse_brute": modeles_sms.analyser(message or "")}


# ── 3. Enregistrer / test / envoi ────────────────────────────────────────────

@frappe.whitelist(methods=["POST"])
def enregistrer(titre, message, filtres=None, lignes=None, name=None):
    """Crée ou met à jour la campagne (brouillon) : groupes, lignes cochées (avec numéros normalisés), message, ciblage."""
    _acces()
    f, ls = _json(filtres, {}), [l for l in _json(lignes, []) if not l.get("exclu") and l.get("coche", True)]
    if not (message or "").strip():
        frappe.throw(_("Écrivez le message avant d'enregistrer."))
    if not ls:
        frappe.throw(_("Aucun client ciblé : choisissez des groupes ou ajoutez des clients."))
    doc = frappe.get_doc(DOCTYPE, name) if name else frappe.new_doc(DOCTYPE)
    if doc.docstatus != 0:
        frappe.throw(_("Cette campagne est déjà envoyée : dupliquez-la."))
    doc.titre = (titre or "").strip() or _("Campagne du {0}").format(nowdate())
    doc.message = message
    doc.filtres = json.dumps(f, ensure_ascii=False)
    doc.statut = "Brouillon"
    doc.set("groupes_des_clients", [{"group_client": g} for g in (f.get("groupes") or []) if frappe.db.exists("Customer Group", g)])
    doc.set("liste_des_clients", [{"client": l["client"], "nom_client": l.get("nom"), "group_client": l.get("groupe"), "secteur": l.get("secteur"),
                                   "liste_tel": ", ".join(l.get("numeros") or []), "envoyer": 1} for l in ls])
    doc.liste_des_destinataires = "\n".join("%s - %s - %s" % (l.get("nom"), l.get("groupe"), ", ".join(l.get("numeros") or [])) for l in ls)
    est = apercu(message, ls)
    doc.segments_estimes = est["segments"]
    doc.flags.ignore_permissions = True
    doc.save()
    return {"name": doc.name, "clients": len(ls), "numeros": est["numeros"], "segments": est["segments"]}


def _envoyer_un(numero: str, texte: str) -> tuple:
    """(statut, détail) pour UN numéro : 'Simulé' en dev, 'Envoyé' si la passerelle accepte, 'Échec' sinon."""
    if simulation_dev():
        return "Simulé", "🧪 dev"
    try:
        corps = envoyer_sms_verifie("216" + numero if len(numero) == 8 else numero, texte, tentatives=2)
        return "Envoyé", (corps or "")[:120]
    except Exception as e:
        return "Échec", str(e)[:200]


@frappe.whitelist(methods=["POST"])
def envoyer_test(numero, message, client=None, nom=None, groupe=None):
    """Un seul SMS, vers le numéro donné, rendu comme pour le client choisi (ou un exemple)."""
    _acces()
    nums = traiter_numero_tel(numero or "")
    if not nums:
        frappe.throw(_("Numéro de test invalide (mobile tunisien à 8 chiffres)."))
    texte = rendre(message, contexte(client, nom or "Ahmed Farhat", groupe or "Individuel"))
    statut, detail = _envoyer_un(nums[0], texte)
    return {"statut": statut, "detail": detail, "texte": texte, "analyse": modeles_sms.analyser(texte)}


@frappe.whitelist(methods=["POST"])
def lancer(name):
    """Démarre l'envoi en tâche de fond ; l'écran suit la progression (realtime campagne_sms_progress)."""
    _acces()
    doc = frappe.get_doc(DOCTYPE, name)
    if doc.docstatus != 0 or doc.statut == "En cours":
        frappe.throw(_("Cette campagne est déjà envoyée ou en cours."))
    a_envoyer = [r for r in doc.liste_des_clients if cint(r.envoyer) and traiter_numero_tel(r.liste_tel or "")]
    if not a_envoyer:
        frappe.throw(_("Aucune ligne cochée avec un numéro valide."))
    frappe.db.set_value(DOCTYPE, name, {"statut": "En cours"}, update_modified=False)
    frappe.enqueue("customization_app.campagne_sms._executer", queue="long", timeout=3600, name=name, enqueue_after_commit=True, job_name="campagne_sms_%s" % name)
    return {"lignes": len(a_envoyer), "simulation": simulation_dev()}


def _executer(name):
    doc = frappe.get_doc(DOCTYPE, name)
    lignes = [r for r in doc.liste_des_clients if cint(r.envoyer)]
    total, envoyes, echecs, invalides, segments = len(lignes), 0, 0, 0, 0
    for i, r in enumerate(lignes, 1):
        nums = traiter_numero_tel(r.liste_tel or "")
        if not nums:
            invalides += 1
            frappe.db.set_value("SMS client", r.name, {"statut_envoi": "Sans numéro", "envoye_le": now_datetime()}, update_modified=False)
            continue
        try:
            texte = rendre(doc.message, contexte(r.client, r.nom_client, r.group_client, r.get("secteur")))
        except Exception as e:
            echecs += 1
            frappe.db.set_value("SMS client", r.name, {"statut_envoi": "Échec", "detail": str(e)[:200], "envoye_le": now_datetime()}, update_modified=False)
            continue
        seg = modeles_sms.analyser(texte)["segments"]
        verdicts = []
        for n in nums:
            statut, detail = _envoyer_un(n, texte)
            verdicts.append((n, statut, detail))
            if statut in ("Envoyé", "Simulé"):
                envoyes += 1
                segments += seg
            else:
                echecs += 1
        ok = [n for n, s, _d in verdicts if s in ("Envoyé", "Simulé")]
        ko = [n for n, s, _d in verdicts if s == "Échec"]
        statut_ligne = ("Simulé" if simulation_dev() else "Envoyé") if ok and not ko else ("Partiel" if ok else "Échec")
        frappe.db.set_value("SMS client", r.name, {"statut_envoi": statut_ligne, "envoye_le": now_datetime(),
                                                  "detail": " ; ".join("%s : %s%s" % (n, s, (" — " + d) if s == "Échec" and d else "") for n, s, d in verdicts)[:500]},
                            update_modified=False)
        if i % LOT_REALTIME == 0 or i == total:
            frappe.db.commit()
            frappe.publish_realtime("campagne_sms_progress", {"name": name, "fait": i, "total": total, "envoyes": envoyes, "echecs": echecs}, user=doc.owner)
    statut = "Envoyée" if envoyes and not echecs else ("Partielle" if envoyes else "Échec")
    frappe.db.set_value(DOCTYPE, name, {"statut": statut, "envoye_le": now_datetime(), "envoyes": envoyes, "echecs": echecs, "invalides": invalides,
                                        "nombre_total_compagne": segments}, update_modified=False)
    frappe.db.commit()
    if envoyes:
        try:
            d = frappe.get_doc(DOCTYPE, name)
            d.flags.ignore_permissions = True
            d.submit()        # figée : l'historique ne se modifie plus
            frappe.db.commit()
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Campagne SMS : soumission %s" % name)
    frappe.publish_realtime("campagne_sms_progress", {"name": name, "fait": total, "total": total, "envoyes": envoyes, "echecs": echecs, "fini": True, "statut": statut}, user=doc.owner)


@frappe.whitelist()
def etat(name):
    _acces()
    doc = frappe.get_doc(DOCTYPE, name)
    return {"name": doc.name, "titre": doc.titre, "statut": doc.statut, "docstatus": doc.docstatus, "message": doc.message,
            "envoye_le": str(doc.envoye_le or "")[:16], "envoyes": doc.envoyes, "echecs": doc.echecs, "invalides": doc.invalides,
            "segments": doc.nombre_total_compagne, "filtres": _json(doc.filtres, {}),
            "lignes": [{"client": r.client, "nom": r.nom_client, "groupe": r.group_client, "secteur": r.get("secteur"),
                        "numeros": traiter_numero_tel(r.liste_tel or ""), "envoyer": cint(r.envoyer), "statut": r.get("statut_envoi") or "",
                        "detail": r.get("detail") or "", "le": str(r.get("envoye_le") or "")[:16]} for r in doc.liste_des_clients]}


@frappe.whitelist(methods=["POST"])
def dupliquer(name, seulement_echecs=0):
    """Nouvelle campagne brouillon à partir d'une ancienne (tous les clients, ou seulement ceux en échec / partiel)."""
    _acces()
    src = frappe.get_doc(DOCTYPE, name)
    lignes = [r for r in src.liste_des_clients if not cint(seulement_echecs) or (r.get("statut_envoi") or "") in ("Échec", "Partiel")]
    if not lignes:
        frappe.throw(_("Aucune ligne à reprendre."))
    doc = frappe.new_doc(DOCTYPE)
    doc.titre = (src.titre or src.name) + (_(" — reprise des échecs") if cint(seulement_echecs) else _(" — copie"))
    doc.message, doc.filtres, doc.statut = src.message, src.filtres, "Brouillon"
    doc.set("groupes_des_clients", [{"group_client": g.group_client} for g in src.groupes_des_clients])
    doc.set("liste_des_clients", [{"client": r.client, "nom_client": r.nom_client, "group_client": r.group_client, "secteur": r.get("secteur"),
                                   "liste_tel": r.liste_tel, "envoyer": 1} for r in lignes])
    doc.flags.ignore_permissions = True
    doc.insert()
    return {"name": doc.name, "clients": len(lignes)}
