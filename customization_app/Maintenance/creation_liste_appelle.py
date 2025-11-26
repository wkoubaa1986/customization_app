import frappe
import json

from customization_app.Maintenance.update_schedule import MACHINE_FAMILY_BY_GROUP
from customization_app.Maintenance.relance_maintenance_sms import (
    FAMILY_MAINTENANCE_ITEM,
    FAMILY_SMS_LABEL,
)
from customization_app.utils.run_safely import run_safely


# =============================================================================
# Utils génériques
# =============================================================================

def log(msg):
    frappe.logger("creation_liste_appelle").info(msg)


def get_price_for_item(item_code, price_list="Vente standard"):
    """Retourne le price_list_rate pour un item + price list, ou None."""
    return frappe.db.get_value(
        "Item Price",
        {
            "item_code": item_code,
            "price_list": price_list,
            "selling": 1,
        },
        "price_list_rate",
    )


def has_draft_of_type(list_type: str) -> bool:
    """
    True s'il existe un draft de Liste Appelle Entretien pour ce type_liste.
    list_type ∈ {"Normal", "Relance", "Urgence"}.
    """
    return bool(
        frappe.db.exists(
            "Liste Appelle Entretien",
            {
                "docstatus": 0,
                "type_liste": list_type,
            },
        )
    )


def get_recent_rdv_customers(days_back=15):
    """
    Retourne l'ensemble des clients qui ont un RDV
    (Livraison ou Entretien) :
      - dans les `days_back` derniers jours
      - OU dans le futur.
    Ces clients seront exclus des listes (normal, urgence, 2e appel).
    """
    today = frappe.utils.getdate()
    date_min = frappe.utils.add_days(today, -days_back)

    rows = frappe.db.sql(
        """
        SELECT DISTINCT
            TT.custom_client
        FROM
            `tabTache de travail` TT
        WHERE
            TT.status IN ('Open','Completed')
            AND DATE(TT.starts_on) >= %s
            AND TT.dans_local != 'Oui'
            AND TT.custom_client IS NOT NULL
            AND TT.custom_type_dintervention IN ('Livraison','Entretien')
        """,
        (date_min,),
        as_dict=True,
    )

    clients = {r["custom_client"] for r in rows if r.get("custom_client")}
    log(f"[RDV] Clients avec RDV récent/futur : {clients}")
    return clients


# =============================================================================
# 0) Logique URGENCE factorisée
# =============================================================================

def _verification_rendez_vous(rendez_vous_list, list_total):
    """
    Vérifie si les rendez-vous passés en param ont déjà une intervention
    planifiée le même jour dans List_Total (liste de noms de Tache de travail).
    Retourne True si TOUTES les dates existent déjà, False sinon.
    """
    date_intervention_l = []
    for name in list_total:
        try:
            intr_i = frappe.get_doc("Tache de travail", name)
            date_str = str(frappe.utils.getdate(intr_i.starts_on))
            if date_str not in date_intervention_l:
                date_intervention_l.append(date_str)
        except Exception:
            continue

    exist = True
    for r in rendez_vous_list:
        date_r = str(frappe.utils.getdate(r["starts_on"]))
        log(f"[URGENCE] Vérif date RDV: {date_r}")
        if date_r not in date_intervention_l:
            exist = False

    return exist


def get_urgence_context(today):
    """
    Retourne le contexte d'urgence pour Secteurs 7/8/9 :

    {
      "gener_urgence": bool,
      "secteur_urgence": [ ... ],
      "related_tache": [ ... ]
    }
    """
    # Listes urgence déjà existantes
    Liste_Appel_urgence = frappe.db.sql(
        """
        SELECT 
            ms.name AS liste_appelle,
            ms.liste_maintenance
        FROM 
            `tabListe Appelle Entretien` ms 
        WHERE
            ms.type_liste = "Urgence"
        """,
        as_dict=True,
    )

    # Toutes les interventions déjà listées en urgence
    List_Total = []
    for ilist in Liste_Appel_urgence:
        nom_list = ilist["liste_maintenance"] or ""
        nom_list = nom_list.replace("[", "").replace("]", "").replace("'", "")
        nom_list = nom_list.split(",")
        for i in nom_list:
            i = i.strip()
            if i and i not in List_Total:
                List_Total.append(i)

    rend_date = frappe.utils.add_days(today, 0)

    rendez_vous_secteur_7_8 = frappe.db.sql(
        """
        SELECT DISTINCT
            TT.name,
            TT.custom_client,
            TT.custom_employé,
            TT.custom_type_dintervention,
            TT.starts_on,
            TT.ends_on,
            TT.secteur,
            C.custom_envoi_sms,
            C.custom_liste_telephone
        FROM
            `tabTache de travail` TT
        JOIN
            `tabCustomer` C ON C.name = TT.custom_client
        WHERE
            TT.status IN ('Open','Completed')
            AND DATE(TT.starts_on) >= %s
            AND TT.dans_local != 'Oui'
            AND TT.custom_client IS NOT NULL
            AND TT.secteur IN ('Secteur 7','Secteur 8','Secteur 9')
            AND TT.custom_type_dintervention IN ('Installation','Entretien','Réparation','Visite')
        """,
        (rend_date,),
        as_dict=True,
    )

    log(f"[URGENCE] RDV Secteur 7/8/9: {rendez_vous_secteur_7_8}")
    gener_urgence = False
    secteur_urgence = []
    related_tache = []
    log(f"[URGENCE] List_Total existant: {List_Total}")

    if rendez_vous_secteur_7_8:
        for icus in rendez_vous_secteur_7_8:
            if icus["name"] not in List_Total and not _verification_rendez_vous(
                [icus], List_Total
            ):
                log(f"[URGENCE] Nouveau RDV urgence: {icus['name']}")
                gener_urgence = True
                if icus["name"] not in related_tache:
                    related_tache.append(icus["name"])
                if icus["secteur"] not in secteur_urgence:
                    secteur_urgence.append(icus["secteur"])

    return {
        "gener_urgence": gener_urgence,
        "secteur_urgence": secteur_urgence,
        "related_tache": related_tache,
    }


# =============================================================================
# 1) Gestion des appels sur Maintenance Schedule
# =============================================================================

def _ajout_Liste_appel(Mainten_name, today):
    """
    Retourne un dict {item_code: last_scheduled_date, 'Nb_Calls': n} pour les items
    qui ont :
      - scheduled_date < today
      - custom_sms_1 et custom_sms_2 remplis
      - actual_date vide
      - custom_appelle vide
    et pour lesquels la dernière intervention n'est pas trop récente (>= 160 jours).
    """
    months3ago = frappe.utils.add_days(today, -160)
    months3ago = frappe.utils.getdate(months3ago)

    def get_number_of_appelle(Mainten_name):
        Number_of_calls = frappe.db.sql(
            """
            SELECT 
                ms.name AS liste_appelle
            FROM 
                `tabListe Appelle Entretien` ms 
            LEFT JOIN 
                `tabAppelle Client` msi ON ms.name = msi.parent
            WHERE
                ms.docstatus = 1
                AND (ms.type_liste = "Normal" OR ms.type_liste IS NULL)
                AND msi.échéancier_dentretien = %s
            """,
            (Mainten_name,),
            as_dict=True,
        )
        return Number_of_calls

    Item_relanced = {}
    last_intervention = {}
    today_dt = frappe.utils.getdate(today)
    maintenance = frappe.get_doc("Maintenance Schedule", Mainten_name)

    for item in maintenance.schedules:
        if (
            item.scheduled_date
            and item.scheduled_date < today_dt
            and item.custom_sms_1
            and item.custom_sms_2
            and not item.actual_date
            and not item.custom_appelle
        ):
            Item_relanced[item.item_code] = item.scheduled_date

        if item.item_code in Item_relanced and item.actual_date:
            if item.item_code not in last_intervention:
                last_intervention[item.item_code] = item.actual_date
            elif item.actual_date >= last_intervention[item.item_code]:
                last_intervention[item.item_code] = item.actual_date

    if Item_relanced:
        for ic in list(last_intervention.keys()):
            if ic in Item_relanced and last_intervention[ic] >= months3ago:
                del Item_relanced[ic]

        if Item_relanced:
            Nb_calls = get_number_of_appelle(Mainten_name)
            Item_relanced["Nb_Calls"] = len(Nb_calls)

    return Item_relanced


def _last_call_date(Mainten_name):
    last_date = None
    maintenance = frappe.get_doc("Maintenance Schedule", Mainten_name)
    for item in maintenance.schedules:
        if item.custom_appelle and not last_date:
            last_date = item.custom_appelle
        elif last_date and item.custom_appelle and item.custom_appelle > last_date:
            last_date = item.custom_appelle
    return last_date


# =============================================================================
# 2) Création d'une Liste Appelle Entretien (Normal / Urgence) factorisée
# =============================================================================

def _create_liste_appel_doc(today, list_appel_dict, type_liste, related_tache=None):
    """
    Crée un document Liste Appelle Entretien (type Normal ou Urgence)
    à partir de list_appel_dict = { schedule_name: {...infos...} }.

    Retourne un résumé :
    {
      "created": bool,
      "type": "Normal" / "Urgence",
      "count_clients": int,
      "name": doc.name
    }
    """
    if not list_appel_dict:
        log(f"[CREATE {type_liste}] Aucun élément à générer.")
        return {
            "created": False,
            "type": type_liste,
            "count_clients": 0,
            "name": None,
        }

    new_list = frappe.new_doc("Liste Appelle Entretien")
    N_max = 99
    nb_c_n_e = 0
    nb_c_n_r = 0
    T_t = 0

    def get_adresse(Ad):
        return f"{Ad.get('address_line1','')}, {Ad.get('city','')}, {Ad.get('state','')}"

    for index, key in enumerate(list_appel_dict):
        if index > N_max:
            break

        T_t += 1
        liste_article = {}

        familles = set()
        cout = get_price_for_item("M-E-OD") or 0  # Coût main d'oeuvre de base

        for arti, val in list_appel_dict[key].items():
            if arti in ("secteurs", "customer", "Nb_Calls"):
                continue

            item = frappe.get_doc("Item", arti)
            group = item.item_group
            family = MACHINE_FAMILY_BY_GROUP.get(group)

            if family:
                familles.add(family)
                maint_item_code = FAMILY_MAINTENANCE_ITEM.get(family)
                if maint_item_code:
                    item_price = get_price_for_item(maint_item_code)
                    if item_price is not None and item_price > cout:
                        cout = item_price

            # date d'échéance par article
            liste_article[arti] = frappe.utils.getdate(
                list_appel_dict[key][arti]
            ).isoformat()

        # Construction du message à partir des familles
        lignes = []
        for fam in familles:
            label = FAMILY_SMS_LABEL.get(
                fam, "votre appareil de traitement d'eau"
            )
            if fam == "ADOUCISSEUR":
                lignes.append(
                    f"{label}: vérification, test de la dureté et contrôle général."
                )
                lignes.append(
                    "Promo sac sels: pour l'achat de 5 sacs, le prix du sac est à 28 DT au lieu de 35 DT."
                )
            else:
                lignes.append(
                    f"{label}: changement des filtres et contrôle général."
                )

        if not lignes:
            lignes.append("Entretien de votre installation de traitement d'eau.")

        message = "\n".join(lignes)
        message += f"\nCout main d'oeuvre: {cout} DT"

        # Création de la ligne Appelle Client
        new_appel = frappe.new_doc("Appelle Client")
        customer = frappe.get_doc("Customer", list_appel_dict[key]["customer"])

        new_appel.échéancier_dentretien = key
        new_appel.telephone = customer.custom_liste_telephone
        new_appel.intéressé_par_le_service_dentretien = (
            customer.custom_intéressé_par_le_service_entretien
        )

        addresses = frappe.get_all(
            "Address",
            filters={"link_doctype": "Customer", "link_name": customer.name},
            fields=["*"],
        )
        Ad_T = ""
        for Ad in addresses:
            Ad_T += get_adresse(Ad) + "\n"

        new_appel.adresse = Ad_T

        if customer.custom_intéressé_par_le_service_entretien == "Non":
            nb_c_n_e += 1

        new_appel.intéressé_par_le_service_de_relance = customer.custom_envoi_sms
        if customer.custom_envoi_sms == "Non":
            nb_c_n_r += 1

        new_appel.info = message
        new_appel.detail_articles = json.dumps(liste_article)
        new_appel.secteur = list_appel_dict[key]["secteurs"]

        new_list.append("clients", new_appel)

    new_list.type_liste = type_liste
    new_list.date = today

    new_list.nb_appels_restant = T_t
    new_list.nb_r_p = 0
    new_list.nb_r_c = 0
    new_list.nb_c_n_e = nb_c_n_e
    new_list.nb_c_n_r = nb_c_n_r

    if type_liste == "Urgence" and related_tache:
        new_list.liste_maintenance = str(related_tache)
        new_list.titre = f"{type_liste}!!! {new_list.date}"
    else:
        new_list.titre = f"{type_liste} {new_list.date}"

    new_list.save()
    frappe.db.commit()

    log(f"[CREATE {type_liste}] Créé {new_list.name} avec {T_t} clients")

    return {
        "created": True,
        "type": type_liste,
        "count_clients": T_t,
        "name": new_list.name,
    }


# =============================================================================
# 3) Génération liste "2e appel" (Relance)
# =============================================================================

def _generate_second_call_list(today, rdv_clients):
    """
    Génère la liste '2ème appel' (type_liste = 'Relance') pour les clients
    'Ne répond pas 1er appel', en excluant ceux avec RDV récent/futur.
    """
    lastweek = frappe.utils.add_days(today, -3)

    liste_appel_pour_2eme = frappe.db.sql(
        """
        SELECT 
            ms.name AS liste_appelle
        FROM 
            `tabListe Appelle Entretien` ms 
        WHERE
            ms.docstatus = 1
            AND (ms.type_liste != "Relance" OR ms.type_liste IS NULL)
            AND ms.liste_2iéme_relance = 0
            AND ms.date_fin <= %s
        """,
        (lastweek,),
        as_dict=True,
    )

    if not liste_appel_pour_2eme:
        log("[2e APPEL] Aucune liste éligible trouvée.")
        return {
            "created": False,
            "count_clients": 0,
        }

    new_rel = frappe.new_doc("Liste Appelle Entretien")
    T_t = 0

    for ilist in liste_appel_pour_2eme:
        appel_list = frappe.get_doc("Liste Appelle Entretien", ilist["liste_appelle"])
        log("------------------------------------------------------")
        log(f"[2e APPEL] Traitement liste: {ilist['liste_appelle']}")

        for iclient in appel_list.clients:
            if iclient.resume_appel != "Ne répond pas 1er appel":
                continue

            # exclusion si RDV récent/futur
            if iclient.client in rdv_clients:
                log(
                    f"[2e APPEL] Skip {iclient.client} (RDV récent/futur → pas de 2e appel)"
                )
                continue

            still_exist = True
            try:
                if iclient.échéancier_dentretien:
                    frappe.get_doc("Maintenance Schedule", iclient.échéancier_dentretien)
                else:
                    still_exist = False
            except Exception:
                still_exist = False

            if still_exist:
                log(f"[2e APPEL] Ajout client: {iclient.client}")
                T_t += 1
                old_value = iclient.a_été_appelé
                iclient.a_été_appelé = 0
                new_rel.append("clients", iclient)
                iclient.a_été_appelé = old_value

        appel_list.liste_2iéme_relance = 1
        appel_list.save()
        log("------------------------------------------------------")

    if new_rel.clients:
        log("[2e APPEL] Création d'une nouvelle liste 2ème appel")
        new_rel.nb_appels_restant = T_t
        new_rel.nb_r_p = 0
        new_rel.nb_r_c = 0
        for iclient in new_rel.clients:
            iclient.a_été_appelé = 0

        new_rel.date = today
        new_rel.type_liste = "Relance"
        new_rel.titre = f"{new_rel.type_liste} {new_rel.date}"
        new_rel.save()
        frappe.db.commit()
        return {
            "created": True,
            "count_clients": T_t,
            "name": new_rel.name,
        }

    log("[2e APPEL] Aucun client éligible (après filtre RDV).")
    return {
        "created": False,
        "count_clients": 0,
        "name": None,
    }


# =============================================================================
# 4) Génération des listes Normal / Urgence
# =============================================================================

def _generate_normal_urgence_list(
    today,
    rdv_clients,
    create_normal=True,
    create_urgence=True,
    ignore_draft_normal=False,
):
    """
    Génère :
      - une liste NORMAL (type_liste = "Normal") si create_normal
      - une liste URGENCE (type_liste = "Urgence") si create_urgence & gener_urgence

    - Exclut les clients avec RDV récent/futur
    - Utilise la logique URGENCE factorisée (Secteur 7/8/9)
    """
    results = {
        "normal": None,
        "urgence": None,
    }

    # Contexte d'urgence
    urg_ctx = get_urgence_context(today)
    gener_urgence = urg_ctx["gener_urgence"]
    secteur_urgence = urg_ctx["secteur_urgence"]
    related_tache = urg_ctx["related_tache"]

    # Toutes les maintenances
    All_maintenance = frappe.db.sql(
        """
        SELECT 
            ms.name AS schedule_name,
            ms.customer,
            ms.status,
            CONCAT_WS(', ', 
                GROUP_CONCAT(DISTINCT msi.sales_order SEPARATOR ', '), 
                GROUP_CONCAT(DISTINCT msd.custom_sales_order SEPARATOR ', ')
            ) AS sales_orders,
            GROUP_CONCAT(DISTINCT addr.custom_secteur SEPARATOR ', ') AS secteurs
        FROM 
            `tabMaintenance Schedule` ms
        LEFT JOIN 
            `tabMaintenance Schedule Item` msi ON ms.name = msi.parent
        LEFT JOIN 
            `tabMaintenance Schedule Detail` msd ON ms.name = msd.parent
        LEFT JOIN 
            `tabDynamic Link` dl ON dl.link_name = ms.customer AND dl.link_doctype = 'Customer'
        LEFT JOIN 
            `tabAddress` addr ON addr.name = dl.parent
        WHERE 
            ms.status = 'Submitted'
        GROUP BY 
            ms.name, ms.customer, ms.status
        """,
        as_dict=True,
    )

    months3ago_global = frappe.utils.add_days(today, -60)

    List_appel = {}

    for imant in All_maintenance:
        customer_name = imant["customer"]

        # Exclusion si RDV récent / futur
        if customer_name in rdv_clients:
            log(
                f"[LISTE NORMALE] Skip maintenance {imant['schedule_name']} - "
                f"client {customer_name} (RDV récent/futur)"
            )
            continue

        if imant["secteurs"] == "Hors Secteur":
            continue

        Item_relanced = _ajout_Liste_appel(imant["schedule_name"], today)
        last_date = _last_call_date(imant["schedule_name"])

        if Item_relanced and (
            (last_date and last_date <= frappe.utils.getdate(months3ago_global))
            or not last_date
        ):
            Item_relanced.setdefault("customer", customer_name)
            Item_relanced.setdefault("secteurs", imant["secteurs"])
            if not Item_relanced["secteurs"]:
                Item_relanced["secteurs"] = "Hors Secteur"
            List_appel[imant["schedule_name"]] = Item_relanced

    # tri par nb d'appels puis par secteur
    List_appel = dict(
        sorted(
            List_appel.items(),
            key=lambda item: (item[1]["Nb_Calls"], item[1]["secteurs"]),
        )
    )

    # On sépare en deux : urgence vs normal
    List_appel_urgence = {}
    List_appel_normal = {}

    for key, val in List_appel.items():
        secteur = val.get("secteurs")
        if gener_urgence and secteur in secteur_urgence:
            List_appel_urgence[key] = val
        else:
            List_appel_normal[key] = val

    # 4.1 Génération URGENCE (toujours quand gener_urgence & create_urgence)
    if create_urgence and gener_urgence and List_appel_urgence:
        results["urgence"] = _create_liste_appel_doc(
            today,
            List_appel_urgence,
            type_liste="Urgence",
            related_tache=related_tache,
        )
    else:
        log("[URGENCE] Aucune liste urgence générée (pas d'urgence ou pas de données).")

    # 4.2 Génération NORMAL (selon draft + flag create_normal)
    draft_normal_exists = has_draft_of_type("Normal")

    if create_normal:
        if draft_normal_exists and not ignore_draft_normal:
            log("[NORMAL] Draft Normal déjà présent → pas de génération (cron).")
            results["normal"] = {
                "created": False,
                "type": "Normal",
                "count_clients": 0,
                "skipped": True,
                "reason": "draft_normal_exists",
            }
        else:
            if List_appel_normal:
                results["normal"] = _create_liste_appel_doc(
                    today,
                    List_appel_normal,
                    type_liste="Normal",
                )
            else:
                log("[NORMAL] Aucune donnée pour générer une liste normale.")
                results["normal"] = {
                    "created": False,
                    "type": "Normal",
                    "count_clients": 0,
                }

    return results


# =============================================================================
# 5) Pipelines global + Cron + Manuel
# =============================================================================

def _run_cron_internal():
    """
    Pipeline CRON :
      - 2e appel (Relance) => UNIQUEMENT si pas de draft 'Relance'
      - Normal           => UNIQUEMENT si pas de draft 'Normal'
      - Urgence          => TOUJOURS générée si urgence détectée
    """
    today = frappe.utils.nowdate()
    rdv_clients = get_recent_rdv_customers(days_back=15)

    results = {}

    # 2e appel / Relance
    if not has_draft_of_type("Relance"):
        results["second_call"] = _generate_second_call_list(today, rdv_clients)
    else:
        log("[CRON] Draft 'Relance' déjà présent → pas de nouvelle liste 2e appel.")
        results["second_call"] = {
            "created": False,
            "skipped": True,
            "reason": "draft_relance_exists",
        }

    # Normal + Urgence
    results["normal_urgence"] = _generate_normal_urgence_list(
        today,
        rdv_clients,
        create_normal=not has_draft_of_type("Normal"),
        create_urgence=True,          # l'urgence se génère toujours si besoin
        ignore_draft_normal=False,    # on respecte la règle de draft en cron
    )

    log(f"[CRON SUMMARY] {results}")
    return results


def _run_manual_internal(list_type: str | None):
    """
    Pipeline MANUEL :
      list_type ∈ {"all", "normal", "relance", "urgence"} (insensible à la casse)
      - Ici on ignore la règle des drafts pour NORMAL (pour laisser la main à l'utilisateur).
    """
    today = frappe.utils.nowdate()
    rdv_clients = get_recent_rdv_customers(days_back=15)

    lt = (list_type or "all").lower()
    results = {}

    # Relance / 2e appel
    if lt in ("all", "relance", "2e", "2e_appel", "second", "second_call"):
        results["second_call"] = _generate_second_call_list(today, rdv_clients)

    # Normal / Urgence
    if lt in ("all", "normal", "urgence"):
        create_normal = lt in ("all", "normal")
        create_urgence = lt in ("all", "urgence")
        results["normal_urgence"] = _generate_normal_urgence_list(
            today,
            rdv_clients,
            create_normal=create_normal,
            create_urgence=create_urgence,
            ignore_draft_normal=True,   # en manuel, on laisse l'utilisateur forcer
        )

    log(f"[MANUAL SUMMARY] ({lt}) {results}")
    return results


def run_cron():
    """
    Point d'entrée utilisé par le scheduler dans hooks.py, par ex :

    scheduler_events = {
        "cron": {
            "0 2 * * *": [
                "customization_app.Maintenance.creation_liste_appelle.run_cron"
            ]
        }
    }
    """
    return run_safely(
        "Cron - Génération listes d'appels entretien",
        _run_cron_internal,
    )


@frappe.whitelist()
def run_manual(list_type=None):
    """
    Point d'entrée MANUEL (bouton).

    Exemples d'appel côté JS (vue liste / bouton) :

        // Tout
        frappe.call({
            method: "customization_app.Maintenance.creation_liste_appelle.run_manual",
            args: { list_type: "all" },
            callback(r) {
                frappe.msgprint(__("Génération terminée (voir logs pour le détail)."));
            }
        });

        // Juste Normal
        frappe.call({
            method: "customization_app.Maintenance.creation_liste_appelle.run_manual",
            args: { list_type: "normal" },
        });

        // Juste Relance (2e appel)
        frappe.call({
            method: "customization_app.Maintenance.creation_liste_appelle.run_manual",
            args: { list_type: "relance" },
        });

        // Juste Urgence
        frappe.call({
            method: "customization_app.Maintenance.creation_liste_appelle.run_manual",
            args: { list_type: "urgence" },
        });

    """
    return run_safely(
        f"Manuel - Génération listes d'appels ({list_type or 'all'})",
        lambda: _run_manual_internal(list_type),
    )
