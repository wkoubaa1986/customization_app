"""
Tournées commerciales — logique métier + API des pages Ma Tournée / Analyse Tournées.

Une Tâche de travail de type INTERVENTION_TOURNEE est liée 1-1 à une
« Tournee Commerciale », elle-même composée de « Visite Commerciale » (une par
client/prospect visité : photo, GPS, compte rendu, résultat, prochaine action).

Règles métier centralisées ici :
- La Tâche de travail ne peut passer à Completed que si la tournée est complète
  (≥1 visite, chaque visite non annulée clôturée avec photo + GPS + compte rendu —
  la complétude par visite est validée par le controller VisiteCommerciale).
- Automatisations à la clôture d'une visite : mise à jour de la dernière visite
  du client, création de la tâche de relance, création de l'opportunité.
- Les horodatages de preuve (photo, GPS) sont posés côté serveur.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, flt, get_first_day, get_last_day, getdate, now_datetime, nowdate

INTERVENTION_TOURNEE = "Tournée commerciale"
PROSPECT_GROUP = "Prospect"

# Lien « fiche client » de l'application B2B envoyé aux contacts.
# Lien FIXE (sans identifiant client), toujours basé sur l'adresse HTTPS de la PROD.
B2B_FICHE_CLIENT_URL = "https://aquaworldservicing.opssync.pro/fiche-client"
B2B_MARKER = "[FICHE-B2B]"
RESULTATS_OPPORTUNITE = ("Demande de devis", "Commande possible")
RELANCE_DEFAUT_JOURS = 7  # si « Relance nécessaire » sans date précisée

# Champs de Visite Commerciale modifiables via save_visite (le reste est géré serveur)
VISITE_CHAMPS_SAISIE = (
    "contact", "adresse", "gps_lat", "gps_lng", "lien_google_maps",
    "arrivee", "depart", "personne_rencontree", "resume_discussion",
    "besoin_client", "niveau_interet", "resultat", "prochaine_action",
    "date_relance", "montant_potentiel", "statut",
)


def _check_perm(ptype="read"):
    if not frappe.has_permission("Visite Commerciale", ptype):
        frappe.throw(_("Accès non autorisé"), frappe.PermissionError)


# ---------------------------------------------------------------- blocage clôture

def check_cloture_tournee(doc):
    """Appelé depuis before_save_tache_de_travail : bloque le passage à
    Completed d'une Tâche « Tournée commerciale » si la tournée est incomplète."""
    if doc.custom_type_dintervention != INTERVENTION_TOURNEE or doc.status != "Completed":
        return
    before = doc.get_doc_before_save()
    if before and before.status == "Completed":
        return

    tournee_name = frappe.db.get_value("Tournee Commerciale", {"tache_de_travail": doc.name})
    if not tournee_name:
        frappe.throw(
            _("Cette tâche est une tournée commerciale : ouvrez « Ma Tournée » et "
              "enregistrez au moins une visite complète avant de la clôturer."),
            title=_("Tournée non renseignée"),
        )

    tournee = frappe.get_doc("Tournee Commerciale", tournee_name)
    incompletes = tournee.visites_incompletes()
    if not tournee.est_complete():
        if incompletes:
            details = ", ".join(f"{v.client} ({v.name})" for v in incompletes)
            msg = _("Visites non clôturées : {0}. Chaque visite doit avoir sa photo, "
                    "sa position GPS et son compte rendu.").format(details)
        else:
            msg = _("La tournée {0} n'a aucune visite enregistrée.").format(tournee_name)
        frappe.throw(msg, title=_("Tournée incomplète"))

    if tournee.statut != "Terminée":
        frappe.db.set_value("Tournee Commerciale", tournee_name, "statut", "Terminée")


# ---------------------------------------------------------------- automatisations

def refresh_tournee_compteur(tournee_name):
    if tournee_name and frappe.db.exists("Tournee Commerciale", tournee_name):
        frappe.get_doc("Tournee Commerciale", tournee_name).refresh_compteur()


def on_visite_update(doc):
    """Automatisations à la clôture d'une visite (idempotentes)."""
    refresh_tournee_compteur(doc.tournee)
    if doc.statut != "Réalisée":
        return
    _maj_derniere_visite(doc)
    _creer_tache_relance(doc)
    _creer_opportunite(doc)


def _maj_derniere_visite(visite):
    if not frappe.db.has_column("Customer", "custom_derniere_visite"):
        return
    date_visite = getdate(frappe.db.get_value(
        "Tournee Commerciale", visite.tournee, "date_tournee") or nowdate())
    actuelle = frappe.db.get_value("Customer", visite.client, "custom_derniere_visite")
    if not actuelle or getdate(actuelle) < date_visite:
        frappe.db.set_value("Customer", visite.client, "custom_derniere_visite",
                            date_visite, update_modified=False)


def _creer_tache_relance(visite):
    if visite.tache_relance:
        return
    if not (visite.date_relance or visite.resultat == "Relance nécessaire"):
        return
    commercial = visite.commercial or frappe.db.get_value(
        "Tournee Commerciale", visite.tournee, "commercial")
    if not commercial:
        return  # custom_choix_du_staff est obligatoire sur la tâche
    date_relance = getdate(visite.date_relance) if visite.date_relance \
        else add_days(getdate(nowdate()), RELANCE_DEFAUT_JOURS)

    tache = frappe.get_doc({
        "doctype": "Tache de travail",
        "subject": _("Relance — {0}").format(visite.client),
        "custom_type_dintervention": "Visite",
        "custom_choix_du_staff": commercial,
        "custom_client": visite.client,
        "starts_on": f"{date_relance} 09:00:00",
        "status": "Open",
        "description": visite.prochaine_action or "",
    })
    tache.insert(ignore_permissions=True, ignore_mandatory=True)
    frappe.db.set_value("Visite Commerciale", visite.name, "tache_relance",
                        tache.name, update_modified=False)
    visite.tache_relance = tache.name


def _creer_opportunite(visite):
    if visite.opportunite or visite.resultat not in RESULTATS_OPPORTUNITE:
        return
    if not frappe.db.exists("Lead Source", "Tournée commerciale"):
        frappe.get_doc({"doctype": "Lead Source",
                        "source_name": "Tournée commerciale"}).insert(ignore_permissions=True)
    opp = frappe.get_doc({
        "doctype": "Opportunity",
        "opportunity_from": "Customer",
        "party_name": visite.client,
        "status": "Open",
        "source": "Tournée commerciale",
        "opportunity_amount": flt(visite.montant_potentiel),
        "contact_person": visite.contact,
        "company": frappe.defaults.get_global_default("company"),
    })
    opp.insert(ignore_permissions=True, ignore_mandatory=True)
    frappe.db.set_value("Visite Commerciale", visite.name, "opportunite",
                        opp.name, update_modified=False)
    visite.opportunite = opp.name


# ---------------------------------------------------------------- API « Ma Tournée »

def _visite_dict(name):
    doc = frappe.get_doc("Visite Commerciale", name)
    d = {f: doc.get(f) for f in VISITE_CHAMPS_SAISIE}
    for extra in ("name", "client", "nouveau_prospect", "contact_ajoute",
                  "photo_visite", "photo_horodatage", "gps_horodatage",
                  "tache_relance", "opportunite", "commercial"):
        d[extra] = doc.get(extra)
    d["client_nom"] = frappe.db.get_value("Customer", doc.client, "customer_name") or doc.client
    for k, v in list(d.items()):
        if hasattr(v, "isoformat"):
            d[k] = str(v)
    return d


@frappe.whitelist()
def get_tournee(tache):
    """Renvoie (et crée si besoin) la tournée liée à une Tâche de travail."""
    _check_perm("read")
    tache_doc = frappe.get_doc("Tache de travail", tache)
    if tache_doc.custom_type_dintervention != INTERVENTION_TOURNEE:
        frappe.throw(_("La tâche {0} n'est pas une tournée commerciale.").format(tache))

    name = frappe.db.get_value("Tournee Commerciale", {"tache_de_travail": tache})
    if not name:
        _check_perm("create")
        tournee = frappe.get_doc({
            "doctype": "Tournee Commerciale",
            "tache_de_travail": tache,
            "commercial": tache_doc.custom_choix_du_staff,
            "date_tournee": getdate(tache_doc.starts_on) if tache_doc.starts_on else nowdate(),
        })
        tournee.insert(ignore_permissions=True)
        name = tournee.name
    else:
        tournee = frappe.get_doc("Tournee Commerciale", name)

    visites = frappe.get_all("Visite Commerciale", filters={"tournee": name},
                             order_by="creation", pluck="name")
    return {
        "name": name,
        "tache": tache,
        "tache_status": tache_doc.status,
        "commercial": tournee.commercial,
        "commercial_nom": frappe.db.get_value("Employee", tournee.commercial, "employee_name")
                          if tournee.commercial else None,
        "date_tournee": str(tournee.date_tournee) if tournee.date_tournee else None,
        "statut": tournee.statut,
        "nb_visites_prevues": tournee.nb_visites_prevues,
        "objectif": tournee.objectif,
        "visites": [_visite_dict(v) for v in visites],
    }


@frappe.whitelist()
def get_client_refs(client):
    """Contacts et adresses liés à un client (Dynamic Link)."""
    _check_perm("read")
    contacts = frappe.db.sql(
        """SELECT c.name, c.first_name, c.last_name, c.designation,
                  c.mobile_no, c.phone, c.email_id, c.image
           FROM `tabContact` c
           INNER JOIN `tabDynamic Link` dl ON dl.parent = c.name
                AND dl.parenttype = 'Contact'
           WHERE dl.link_doctype = 'Customer' AND dl.link_name = %s
           ORDER BY c.is_primary_contact DESC, c.modified DESC""",
        (client,), as_dict=True,
    )
    for c in contacts:
        c["nom_complet"] = " ".join(x for x in (c.first_name, c.last_name) if x)
    adresses = frappe.db.sql(
        """SELECT a.name, a.address_line1, a.city, a.address_type,
                  a.custom_lien_google_map AS google_map
           FROM `tabAddress` a
           INNER JOIN `tabDynamic Link` dl ON dl.parent = a.name
                AND dl.parenttype = 'Address'
           WHERE dl.link_doctype = 'Customer' AND dl.link_name = %s
           ORDER BY a.modified DESC""",
        (client,), as_dict=True,
    )
    return {"contacts": contacts, "adresses": adresses}


@frappe.whitelist()
def add_visite(tournee, client, nouveau_prospect=0):
    _check_perm("create")
    visite = frappe.get_doc({
        "doctype": "Visite Commerciale",
        "tournee": tournee,
        "client": client,
        "nouveau_prospect": frappe.utils.cint(nouveau_prospect),
        "statut": "Planifiée",
    })
    visite.insert()
    return _visite_dict(visite.name)


@frappe.whitelist()
def save_visite(name, values):
    """Enregistre les champs de saisie d'une visite. Les horodatages de preuve
    sont posés ici, côté serveur. La validation de complétude (photo/GPS/CR)
    s'applique au passage à « Réalisée » via le controller."""
    _check_perm("write")
    if isinstance(values, str):
        values = json.loads(values)
    doc = frappe.get_doc("Visite Commerciale", name)
    old_lat, old_lng = flt(doc.gps_lat), flt(doc.gps_lng)
    old_adresse = doc.adresse
    for field in VISITE_CHAMPS_SAISIE:
        if field in values:
            doc.set(field, values[field] if values[field] != "" else None)

    # Adresse sélectionnée : reprendre son lien Google Maps s'il existe
    # (champ custom_lien_google_map des adresses, déjà utilisé par les Tâches).
    if doc.adresse and doc.adresse != old_adresse:
        addr_link = (frappe.db.get_value("Address", doc.adresse,
                                         "custom_lien_google_map") or "").strip()
        if addr_link:
            doc.lien_google_maps = addr_link

    gps_change = flt(doc.gps_lat) and flt(doc.gps_lng) and (
        flt(doc.gps_lat) != old_lat or flt(doc.gps_lng) != old_lng or not doc.gps_horodatage
    )
    if gps_change:
        doc.gps_horodatage = now_datetime()
        # ne pas écraser le lien de l'adresse : la position réelle reste
        # consultable via gps_lat/gps_lng (lien coordonnées affiché côté page)
        if not doc.lien_google_maps:
            doc.lien_google_maps = f"https://maps.google.com/?q={doc.gps_lat},{doc.gps_lng}"
    if values.get("statut") == "En cours" and not doc.arrivee:
        doc.arrivee = now_datetime()
    if values.get("statut") == "Réalisée" and not doc.depart:
        doc.depart = now_datetime()
    doc.save()
    return _visite_dict(doc.name)


@frappe.whitelist()
def set_photo(name, file_url):
    _check_perm("write")
    doc = frappe.get_doc("Visite Commerciale", name)
    doc.photo_visite = file_url
    doc.photo_horodatage = now_datetime()
    doc.save()
    return _visite_dict(doc.name)


@frappe.whitelist()
def annuler_visite(name):
    _check_perm("write")
    doc = frappe.get_doc("Visite Commerciale", name)
    doc.statut = "Annulée"
    doc.save()
    return _visite_dict(doc.name)


@frappe.whitelist()
def quick_create_client(nom, telephone=None, email=None, secteur=None):
    """Création rapide d'un prospect : Customer dans le groupe « Prospect »."""
    if not frappe.has_permission("Customer", "create"):
        frappe.throw(_("Accès non autorisé"), frappe.PermissionError)
    if not frappe.db.exists("Customer Group", PROSPECT_GROUP):
        from frappe.utils.nestedset import get_root_of
        frappe.get_doc({
            "doctype": "Customer Group",
            "customer_group_name": PROSPECT_GROUP,
            "parent_customer_group": get_root_of("Customer Group"),
        }).insert(ignore_permissions=True)

    customer = frappe.new_doc("Customer")
    customer.customer_name = nom.strip()
    customer.customer_group = PROSPECT_GROUP
    # « Individual » : la règle métier existante (Server Script verif_mat_fiscale)
    # exige un matricule fiscal pour les sociétés — inconnu à ce stade du terrain.
    customer.customer_type = "Individual"
    if telephone:
        customer.mobile_no = telephone
        if frappe.db.has_column("Customer", "custom_liste_telephone"):
            customer.custom_liste_telephone = telephone
    if email:
        customer.email_id = email
    if secteur:
        if frappe.db.exists("Industry Type", secteur):
            customer.industry = secteur
        else:
            customer.customer_details = _("Secteur d'activité : {0}").format(secteur)
    customer.insert(ignore_mandatory=True)
    return {"name": customer.name, "customer_name": customer.customer_name}


@frappe.whitelist()
def quick_create_contact(client, nom, fonction=None, telephone=None, email=None,
                         photo=None, visite=None):
    if not frappe.has_permission("Contact", "create"):
        frappe.throw(_("Accès non autorisé"), frappe.PermissionError)
    parts = (nom or "").strip().split(" ", 1)
    contact = frappe.new_doc("Contact")
    contact.first_name = parts[0]
    if len(parts) > 1:
        contact.last_name = parts[1]
    if fonction:
        contact.designation = fonction
    if photo:
        contact.image = photo
    if telephone:
        contact.append("phone_nos", {"phone": telephone, "is_primary_mobile_no": 1})
    if email:
        contact.append("email_ids", {"email_id": email, "is_primary": 1})
    contact.append("links", {"link_doctype": "Customer", "link_name": client})
    contact.insert(ignore_mandatory=True)
    if visite and frappe.db.exists("Visite Commerciale", visite):
        frappe.db.set_value("Visite Commerciale", visite, "contact_ajoute", 1,
                            update_modified=False)
    return {"name": contact.name, "nom_complet": nom}


@frappe.whitelist()
def quick_create_adresse(client, adresse, ville=None, gouvernorat=None,
                         lien_google_maps=None, type_adresse="Shipping"):
    """Création rapide d'adresse liée au client. Le Server Script existant
    « sectorisation de l'adresse » calcule custom_secteur depuis
    (state=gouvernorat, city=délégation) et plante si le couple est inconnu :
    on pré-remplit « Hors Secteur » quand le couple manque, et on retombe
    dessus si le référentiel ne le connaît pas."""
    if not frappe.has_permission("Address", "create"):
        frappe.throw(_("Accès non autorisé"), frappe.PermissionError)

    def _make(secteur=None):
        doc = frappe.new_doc("Address")
        doc.address_title = frappe.db.get_value("Customer", client, "customer_name") or client
        doc.address_type = type_adresse
        doc.address_line1 = adresse
        doc.city = ville or ""
        doc.state = gouvernorat or ""
        doc.country = frappe.defaults.get_global_default("country") or "Tunisia"
        if lien_google_maps and frappe.db.has_column("Address", "custom_lien_google_map"):
            doc.custom_lien_google_map = lien_google_maps
        if secteur and frappe.db.has_column("Address", "custom_secteur"):
            doc.custom_secteur = secteur
        doc.append("links", {"link_doctype": "Customer", "link_name": client})
        return doc

    if not (gouvernorat and ville):
        doc = _make("Hors Secteur")
        doc.insert(ignore_mandatory=True)
    else:
        try:
            doc = _make()
            doc.insert(ignore_mandatory=True)
        except Exception:
            # gouvernorat/délégation hors référentiel de sectorisation
            doc = _make("Hors Secteur")
            doc.insert(ignore_mandatory=True)
    return {"name": doc.name, "libelle": f"{adresse}, {ville or ''}".strip(", ")}


@frappe.whitelist()
def terminer_tournee(tournee):
    """Vérifie la complétude puis clôture tournée + Tâche de travail."""
    _check_perm("write")
    doc = frappe.get_doc("Tournee Commerciale", tournee)
    incompletes = doc.visites_incompletes()
    if not doc.est_complete():
        if incompletes:
            frappe.throw(
                _("Visites à clôturer d'abord : {0}").format(
                    ", ".join(v.client for v in incompletes)),
                title=_("Tournée incomplète"))
        frappe.throw(_("Ajoutez au moins une visite avant de terminer la tournée."),
                     title=_("Tournée vide"))
    doc.statut = "Terminée"
    doc.save()
    tache = frappe.get_doc("Tache de travail", doc.tache_de_travail)
    if tache.status != "Completed":
        tache.status = "Completed"
        tache.save()
    return {"statut": "Terminée", "tache_status": "Completed"}


# ---------------------------------------------------------------- IA & fiche B2B

@frappe.whitelist()
def ameliorer_resume(texte):
    """Réorganise le résumé de discussion en puces via OpenAI (clé et modèle
    lus dans AI Settings). Ne renvoie que le texte réorganisé — l'utilisateur
    garde la main (rien n'est enregistré ici)."""
    _check_perm("write")
    texte = (texte or "").strip()
    if len(texte) < 10:
        frappe.throw(_("Écrivez d'abord le résumé de la discussion, même en vrac."))

    ai = frappe.get_single("AI Settings")
    api_key = (ai.get("openai_api_key") or "").strip()
    if not api_key:
        frappe.throw(_("Aucune clé OpenAI dans AI Settings."))
    model = (ai.get("open_ai_model") or "").strip() or "gpt-4o-mini"

    import requests

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": (
                "Tu réorganises le compte rendu d'une visite commerciale terrain "
                "(société tunisienne de traitement d'eau et piscines). Réponds "
                "UNIQUEMENT avec le compte rendu réorganisé en puces courtes en "
                "français, regroupées par thème quand c'est pertinent (contexte, "
                "besoins du client, points discutés, objections, suite à donner). "
                "N'invente aucune information absente du texte.")},
            {"role": "user", "content": texte},
        ],
    }
    temperature = flt(ai.get("open_ai_temperature"))
    if temperature:
        payload["temperature"] = temperature

    def _call(body):
        return requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body, timeout=60,
        )

    try:
        resp = _call(payload)
        if resp.status_code == 400 and "temperature" in resp.text and "temperature" in payload:
            payload.pop("temperature")  # certains modèles refusent ce paramètre
            resp = _call(payload)
        resp.raise_for_status()
        resume = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Tournée: échec amélioration IA")
        frappe.throw(_("Échec de l'appel OpenAI : {0}").format(str(e)[:120]))

    return {"resume": resume}


@frappe.whitelist()
def envoyer_fiche_b2b(client):
    """Envoie par SMS + email, à tous les contacts du client (plus les
    coordonnées de la fiche client elle-même), le lien de sa fiche sur
    l'application B2B (URL prod). Trace un commentaire sur le client."""
    _check_perm("write")
    customer_name = frappe.db.get_value("Customer", client, "customer_name") or client
    lien = B2B_FICHE_CLIENT_URL
    message = _(
        "Bonjour, retrouvez votre fiche client sur notre espace B2B "
        "AquaWorld & Servicing : {0} — connectez-vous avec votre numéro "
        "de téléphone (code OTP)."
    ).format(lien)

    refs = get_client_refs(client)
    phones, emails = [], []
    for c in refs["contacts"]:
        for p in (c.mobile_no, c.phone):
            if p and p.strip() and p.strip() not in phones:
                phones.append(p.strip())
        if c.email_id and c.email_id.strip() and c.email_id.strip() not in emails:
            emails.append(c.email_id.strip())
    cust = frappe.db.get_value("Customer", client,
                               ["mobile_no", "email_id", "custom_liste_telephone"], as_dict=True)
    for p in ((cust.custom_liste_telephone or "").split("\n") + [cust.mobile_no or ""]):
        p = p.strip()
        if p and p not in phones:
            phones.append(p)
    if cust.email_id and cust.email_id.strip() and cust.email_id.strip() not in emails:
        emails.append(cust.email_id.strip())

    if not phones and not emails:
        frappe.throw(_("Aucun téléphone ni email trouvé pour {0} (contacts et fiche client).")
                     .format(customer_name))

    sent, failed = [], []
    if phones:
        try:
            from customization_app.customize_erpnext.doctype.compagne_sms.compagne_sms import (
                _send_sms_with_fallback,
            )
            _send_sms_with_fallback(phones, message)
            sent.extend({"channel": "sms", "to": p} for p in phones)
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Tournée: échec SMS fiche B2B")
            failed.append({"channel": "sms", "reason": str(e)[:100]})
    for email in emails:
        try:
            frappe.sendmail(
                recipients=[email],
                subject=_("Votre fiche client B2B — AquaWorld & Servicing"),
                message=message.replace("\n", "<br>"),
                reference_doctype="Customer",
                reference_name=client,
            )
            sent.append({"channel": "email", "to": email})
        except Exception as e:
            frappe.log_error(frappe.get_traceback(), "Tournée: échec email fiche B2B")
            failed.append({"channel": "email", "to": email, "reason": str(e)[:100]})

    if sent:
        cmt = frappe.get_doc({
            "doctype": "Comment", "comment_type": "Comment",
            "reference_doctype": "Customer", "reference_name": client,
            "content": f"{B2B_MARKER} | Lien fiche B2B envoyé à "
                       + ", ".join(s["to"] for s in sent),
        })
        cmt.insert(ignore_permissions=True)
    frappe.db.commit()
    return {"sent": sent, "failed": failed, "lien": lien}


# ---------------------------------------------------------------- dashboard

def _periode(from_date, to_date):
    today = getdate(nowdate())
    start = getdate(from_date) if from_date else get_first_day(today)
    end = getdate(to_date) if to_date else get_last_day(today)
    return start, end


def _visites_periode(start, end, commercial=None):
    conds = "t.date_tournee BETWEEN %(start)s AND %(end)s"
    vals = {"start": start, "end": end}
    if commercial:
        conds += " AND t.commercial = %(commercial)s"
        vals["commercial"] = commercial
    return frappe.db.sql(
        f"""SELECT v.name, v.tournee, v.client, v.statut, v.resultat,
                   v.nouveau_prospect, v.contact_ajoute, v.photo_visite,
                   v.gps_lat, v.gps_lng, v.montant_potentiel, v.opportunite,
                   v.tache_relance, v.date_relance, v.prochaine_action,
                   v.niveau_interet, v.personne_rencontree, v.resume_discussion,
                   v.photo_horodatage, v.lien_google_maps,
                   t.date_tournee, t.commercial, t.tache_de_travail,
                   t.statut AS tournee_statut,
                   c.customer_name AS client_nom,
                   e.employee_name AS commercial_nom
            FROM `tabVisite Commerciale` v
            INNER JOIN `tabTournee Commerciale` t ON t.name = v.tournee
            LEFT JOIN `tabCustomer` c ON c.name = v.client
            LEFT JOIN `tabEmployee` e ON e.name = t.commercial
            WHERE {conds}
            ORDER BY t.date_tournee DESC, v.creation""",
        vals, as_dict=True,
    )


@frappe.whitelist()
def get_analytics(from_date=None, to_date=None, commercial=None):
    _check_perm("read")
    start, end = _periode(from_date, to_date)
    visites = _visites_periode(start, end, commercial)
    today = getdate(nowdate())

    realisees = [v for v in visites if v.statut == "Réalisée"]
    actives = [v for v in visites if v.statut != "Annulée"]
    devis = [v for v in realisees if v.resultat == "Demande de devis"]
    convertibles = [v for v in realisees if v.resultat in RESULTATS_OPPORTUNITE]

    # relances : à venir / en retard (tâche absente ou encore ouverte)
    relance_status = {}
    tache_names = [v.tache_relance for v in visites if v.tache_relance]
    if tache_names:
        for r in frappe.get_all("Tache de travail",
                                filters={"name": ("in", tache_names)},
                                fields=["name", "status", "starts_on"]):
            relance_status[r.name] = r
    relances = []
    for v in visites:
        if not v.date_relance and not v.tache_relance:
            continue
        tache = relance_status.get(v.tache_relance)
        done = bool(tache and tache.status == "Completed")
        due = getdate(v.date_relance) if v.date_relance \
            else (getdate(tache.starts_on) if tache and tache.starts_on else None)
        relances.append({
            "visite": v.name, "client": v.client, "client_nom": v.client_nom,
            "commercial": v.commercial, "commercial_nom": v.commercial_nom,
            "date_relance": str(due) if due else None,
            "prochaine_action": v.prochaine_action,
            "tache_relance": v.tache_relance,
            "tache_status": tache.status if tache else None,
            "en_retard": bool(due and due < today and not done),
            "faite": done,
        })
    relances.sort(key=lambda r: r["date_relance"] or "9999")

    nb_prevues = sum(1 for _v in actives) or 0
    prevu_declare = frappe.db.sql(
        """SELECT COALESCE(SUM(nb_visites_prevues), 0) FROM `tabTournee Commerciale`
           WHERE date_tournee BETWEEN %s AND %s""" +
        (" AND commercial = %s" if commercial else ""),
        (start, end, commercial) if commercial else (start, end))[0][0]

    repartition = {}
    for v in realisees:
        repartition[v.resultat or "Sans résultat"] = repartition.get(v.resultat or "Sans résultat", 0) + 1

    par_commercial = {}
    for v in actives:
        key = v.commercial or "—"
        stats = par_commercial.setdefault(key, {
            "commercial": key, "commercial_nom": v.commercial_nom or key,
            "visites": 0, "realisees": 0, "devis": 0, "montant": 0.0})
        stats["visites"] += 1
        if v.statut == "Réalisée":
            stats["realisees"] += 1
            stats["montant"] += flt(v.montant_potentiel)
            if v.resultat in RESULTATS_OPPORTUNITE:
                stats["devis"] += 1

    # tournées avec leurs visites (cartes dépliables)
    tournees = {}
    for v in visites:
        t = tournees.setdefault(v.tournee, {
            "name": v.tournee, "date_tournee": str(v.date_tournee) if v.date_tournee else None,
            "commercial_nom": v.commercial_nom, "statut": v.tournee_statut,
            "tache_de_travail": v.tache_de_travail, "visites": []})
        t["visites"].append({k: (str(val) if hasattr(val, "isoformat") else val)
                             for k, val in v.items()})

    kpis = {
        "visites_prevues": max(nb_prevues, int(prevu_declare or 0)),
        "visites_realisees": len(realisees),
        "taux_realisation": round(len(realisees) / len(actives) * 100, 1) if actives else 0,
        "nouveaux_prospects": sum(1 for v in visites if v.nouveau_prospect),
        "contacts_ajoutes": sum(1 for v in visites if v.contact_ajoute),
        "demandes_devis": len(devis),
        "opportunites": sum(1 for v in visites if v.opportunite),
        "montant_potentiel": round(sum(flt(v.montant_potentiel) for v in realisees), 3),
        "taux_conversion": round(len(convertibles) / len(realisees) * 100, 1) if realisees else 0,
        "avec_photo": sum(1 for v in realisees if v.photo_visite),
        "avec_gps": sum(1 for v in realisees if v.gps_lat and v.gps_lng),
        "relances_en_retard": sum(1 for r in relances if r["en_retard"]),
    }

    return {
        "period": {"from": str(start), "to": str(end)},
        "currency": frappe.defaults.get_global_default("currency") or "TND",
        "kpis": kpis,
        "repartition": repartition,
        "par_commercial": sorted(par_commercial.values(), key=lambda s: -s["realisees"]),
        "relances": relances,
        "tournees": sorted(tournees.values(), key=lambda t: t["date_tournee"] or "", reverse=True),
        "commerciaux": frappe.get_all(
            "Tournee Commerciale", distinct=True, pluck="commercial",
            filters={"commercial": ("is", "set")}),
    }


@frappe.whitelist()
def reprogrammer_relance(visite, date_relance, commercial=None):
    """Crée ou replanifie la tâche de relance d'une visite (action dashboard)."""
    _check_perm("write")
    doc = frappe.get_doc("Visite Commerciale", visite)
    commercial = commercial or doc.commercial or frappe.db.get_value(
        "Tournee Commerciale", doc.tournee, "commercial")
    if not commercial:
        frappe.throw(_("Choisissez un commercial pour la relance."))

    starts_on = f"{getdate(date_relance)} 09:00:00"
    if doc.tache_relance and frappe.db.get_value(
            "Tache de travail", doc.tache_relance, "status") == "Open":
        tache = frappe.get_doc("Tache de travail", doc.tache_relance)
        tache.starts_on = starts_on
        tache.custom_choix_du_staff = commercial
        tache.save(ignore_permissions=True)
    else:
        tache = frappe.get_doc({
            "doctype": "Tache de travail",
            "subject": _("Relance — {0}").format(doc.client),
            "custom_type_dintervention": "Visite",
            "custom_choix_du_staff": commercial,
            "custom_client": doc.client,
            "starts_on": starts_on,
            "status": "Open",
            "description": doc.prochaine_action or "",
        })
        tache.insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.db.set_value("Visite Commerciale", visite, "tache_relance", tache.name,
                            update_modified=False)
    frappe.db.set_value("Visite Commerciale", visite, "date_relance",
                        getdate(date_relance), update_modified=False)
    return {"tache": tache.name, "starts_on": starts_on}


@frappe.whitelist()
def download_excel(from_date=None, to_date=None, commercial=None):
    from frappe.utils.xlsxutils import make_xlsx

    _check_perm("read")
    start, end = _periode(from_date, to_date)
    visites = _visites_periode(start, end, commercial)
    rows = [
        [f"Visites commerciales — du {start} au {end}"],
        [],
        ["Date", "Tournée", "Commercial", "Client", "Nouveau prospect", "Statut",
         "Résultat", "Niveau d'intérêt", "Personne rencontrée", "Résumé",
         "Montant potentiel", "Photo", "GPS", "Lien Maps",
         "Date relance", "Tâche relance", "Opportunité"],
    ]
    for v in visites:
        rows.append([
            str(v.date_tournee or ""), v.tournee, v.commercial_nom or "",
            v.client_nom or v.client, "Oui" if v.nouveau_prospect else "Non",
            v.statut or "", v.resultat or "", v.niveau_interet or "",
            v.personne_rencontree or "", (v.resume_discussion or "").replace("\n", " "),
            flt(v.montant_potentiel), "Oui" if v.photo_visite else "Non",
            "Oui" if (v.gps_lat and v.gps_lng) else "Non", v.lien_google_maps or "",
            str(v.date_relance or ""), v.tache_relance or "", v.opportunite or "",
        ])
    xlsx = make_xlsx(rows, "Visites commerciales")
    frappe.response["filename"] = f"visites-commerciales-{start}-{end}.xlsx"
    frappe.response["filecontent"] = xlsx.getvalue()
    frappe.response["type"] = "binary"
