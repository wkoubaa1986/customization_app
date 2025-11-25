# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import frappe
import json

from customization_app.Maintenance.update_schedule import MACHINE_FAMILY_BY_GROUP
from customization_app.Maintenance.relance_maintenance_sms import (
    FAMILY_MAINTENANCE_ITEM,
    FAMILY_SMS_LABEL,
)
from customization_app.utils.run_safely import run_safely
# ----------------------------------------------------------------------------
# LOGGING
# ----------------------------------------------------------------------------

def _logger():
    return frappe.logger("liste_appelle_entretien")

def log(msg):
    _logger().info(msg)


# ----------------------------------------------------------------------------
# Helpers génériques
# ----------------------------------------------------------------------------

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


def verification_rendez_vous(rendez_vous_list, list_total):
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
        log(f"[CHECK] Date RDV secteur 7/8/9: {date_r}")
        if date_r not in date_intervention_l:
            exist = False

    return exist


def ajout_Liste_appel(Mainten_name, today):
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


def last_call_date(Mainten_name):
    last_date = None
    maintenance = frappe.get_doc("Maintenance Schedule", Mainten_name)
    for item in maintenance.schedules:
        if item.custom_appelle and not last_date:
            last_date = item.custom_appelle
        elif last_date and item.custom_appelle and item.custom_appelle > last_date:
            last_date = item.custom_appelle
    return last_date


def get_adresse(Ad):
    return f"{Ad.get('address_line1','')}, {Ad.get('city','')}, {Ad.get('state','')}"


# ----------------------------------------------------------------------------
# FONCTION PRINCIPALE (pour CRON)
# ----------------------------------------------------------------------------

@frappe.whitelist()
def generate_liste_appelle_entretien():
    """
    Génère :
    - la liste "2ème appel" (clients 'Ne répond pas 1er appel')
    - gère la logique d'urgence pour secteurs 7/8/9
    - crée une nouvelle 'Liste Appelle Entretien' Normal ou Urgence

    Retourne :
    {
        "summary": {...},
        "log": ["...", "..."]
    }
    """
    log("========== [CRON] Début generate_liste_appelle_entretien ==========")

    today = frappe.utils.nowdate()

    summary = {
        "date": today,
        "draft_count_initial": 0,
        "second_call_source_lists": 0,
        "second_call_clients_added": 0,
        "urgence_lists_found": 0,
        "urgence_enabled": False,
        "nb_rdv_secteur_7_8_9": 0,
        "nb_all_maintenance": 0,
        "nb_rendez_vous_recent": 0,
        "nb_list_appel_candidates": 0,
        "nb_list_appel_urgence_candidates": 0,
        "nb_clients_in_final_list": 0,
        "nb_c_n_e": 0,
        "nb_c_n_r": 0,
        "type_liste_finale": None,
        "liste_created": False,
    }

    # ----------------------------------------------------------------------------
    # 1) Génération de la liste "2ème appel" (clients "Ne répond pas 1er appel")
    # ----------------------------------------------------------------------------
    draft_count = frappe.db.count("Liste Appelle Entretien", filters={"docstatus": 0})
    summary["draft_count_initial"] = draft_count

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

    summary["second_call_source_lists"] = len(liste_appel_pour_2eme)

    new_rel = frappe.new_doc("Liste Appelle Entretien")
    T_t_second_call = 0  # nombre total de clients mis dans la liste 2ème appel

    for ilist in liste_appel_pour_2eme:
        appel_list = frappe.get_doc("Liste Appelle Entretien", ilist["liste_appelle"])
        log("------------------------------------------------------")
        log(f"[2ème APPEL] Liste source : {ilist['liste_appelle']}")

        for iclient in appel_list.clients:
            if iclient.resume_appel == "Ne répond pas 1er appel":
                still_exist = True
                try:
                    frappe.get_doc("Maintenance Schedule", iclient.échéancier_dentretien)
                except Exception:
                    still_exist = False

                if still_exist:
                    log(f"[2ème APPEL] Client ajouté : {iclient.client}")
                    T_t_second_call += 1
                    old_value = iclient.a_été_appelé
                    iclient.a_été_appelé = 0
                    new_rel.append("clients", iclient)
                    iclient.a_été_appelé = old_value

        appel_list.liste_2iéme_relance = 1
        appel_list.save()
        log("------------------------------------------------------")

    summary["second_call_clients_added"] = T_t_second_call

    if new_rel.clients:
        log("[2ème APPEL] Création liste 2ème appel")
        new_rel.nb_appels_restant = T_t_second_call
        new_rel.nb_r_p = 0
        new_rel.nb_r_c = 0
        for iclient in new_rel.clients:
            iclient.a_été_appelé = 0
        new_rel.date = today
        new_rel.type_liste = "Relance"
        new_rel.titre = f"{new_rel.type_liste} {new_rel.date}"
        new_rel.save()

    # ----------------------------------------------------------------------------
    # 2) Gestion des listes "Urgence" Secteur 7 / 8 / 9
    # ----------------------------------------------------------------------------

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
    summary["urgence_lists_found"] = len(Liste_Appel_urgence)

    List_Total = []
    for ilist in Liste_Appel_urgence:
        nom_list = ilist["liste_maintenance"] or ""
        nom_list = nom_list.replace("[", "").replace("]", "").replace("'", "")
        nom_list = nom_list.split(",")
        for i in nom_list:
            i = i.strip()
            if i and i not in List_Total:
                List_Total.append(i)

    today = frappe.utils.nowdate()
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

    summary["nb_rdv_secteur_7_8_9"] = len(rendez_vous_secteur_7_8)

    log(f"[URGENCE] RDV Secteur 7/8/9 trouvés: {len(rendez_vous_secteur_7_8)}")
    log(f"[URGENCE] List_Total (tâches déjà prises en compte): {List_Total}")

    gener_urgence = False
    secteur_urgence = []
    related_tache = []

    if rendez_vous_secteur_7_8:
        for icus in rendez_vous_secteur_7_8:
            if icus["name"] not in List_Total and not verification_rendez_vous(
                [icus], List_Total
            ):
                log(f"[URGENCE] Ajout tâche urgence : {icus['name']}")
                gener_urgence = True
                if icus["name"] not in related_tache:
                    related_tache.append(icus["name"])
                if icus["secteur"] not in secteur_urgence:
                    secteur_urgence.append(icus["secteur"])

    summary["urgence_enabled"] = gener_urgence

    # ----------------------------------------------------------------------------
    # 3) Génération de la liste d'appels "Normale" (ou Urgence)
    # ----------------------------------------------------------------------------

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

    summary["nb_all_maintenance"] = len(All_maintenance)

    today = frappe.utils.nowdate()
    months3ago_global = frappe.utils.add_days(today, -60)

    rendez_vous = frappe.db.sql(
        """
        SELECT DISTINCT
            TT.custom_client,
            TT.custom_employé,
            TT.custom_type_dintervention,
            TT.starts_on,
            TT.ends_on,
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
            AND TT.custom_type_dintervention IN ('Livraison','Entretien')
        """,
        (today,),
        as_dict=True,
    )

    summary["nb_rendez_vous_recent"] = len(rendez_vous)

    List_appel = {}
    liste_Rendezvous = list({x["custom_client"] for x in rendez_vous})

    for imant in All_maintenance:
        if imant["customer"] not in liste_Rendezvous and imant["secteurs"] != "Hors Secteur":
            Item_relanced = ajout_Liste_appel(imant["schedule_name"], today)
            last_date = last_call_date(imant["schedule_name"])
            if Item_relanced and (
                (last_date and last_date <= frappe.utils.getdate(months3ago_global))
                or not last_date
            ):
                Item_relanced.setdefault("customer", imant["customer"])
                Item_relanced.setdefault("secteurs", imant["secteurs"])
                if not Item_relanced["secteurs"]:
                    Item_relanced["secteurs"] = "Hors Secteur"
                List_appel[imant["schedule_name"]] = Item_relanced

    List_appel = dict(
        sorted(
            List_appel.items(),
            key=lambda item: (item[1]["Nb_Calls"], item[1]["secteurs"]),
        )
    )

    summary["nb_list_appel_candidates"] = len(List_appel)

    List_appel_urgence = {}
    if gener_urgence:
        for key in List_appel:
            if List_appel[key]["secteurs"] in secteur_urgence:
                List_appel_urgence[key] = List_appel[key]
        if List_appel_urgence:
            List_appel = List_appel_urgence
        else:
            gener_urgence = False  # pas de match réel

    summary["nb_list_appel_urgence_candidates"] = len(List_appel)

    # ----------------------------------------------------------------------------
    # 4) Création de la nouvelle "Liste Appelle Entretien"
    # ----------------------------------------------------------------------------

    if draft_count == 0 or gener_urgence:
        log(f"[LISTE APPEL] List_appel sélectionné: {List_appel}")
        new_list = frappe.new_doc("Liste Appelle Entretien")
        N_max = 99
        nb_c_n_e = 0
        nb_c_n_r = 0
        T_t = 0  # nb clients dans la liste finale

        for index, key in enumerate(List_appel):
            if index > N_max:
                break

            T_t += 1
            liste_article = {}

            # ----------------------------
            # Même logique "famille machine"
            # ----------------------------
            familles = set()
            cout = get_price_for_item("M-E-OD") or 0  # Coût main d'oeuvre de base

            for arti, val in List_appel[key].items():
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

                # on garde aussi la date d'échéance par article pour le détail
                liste_article[arti] = frappe.utils.getdate(
                    List_appel[key][arti]
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

            # ----------------------------
            # Création du Appelle Client
            # ----------------------------
            new_appel = frappe.new_doc("Appelle Client")
            customer = frappe.get_doc("Customer", List_appel[key]["customer"])

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
            new_appel.secteur = List_appel[key]["secteurs"]

            new_list.append("clients", new_appel)

        new_list.type_liste = "Normal"
        new_list.date = today

        new_list.nb_appels_restant = T_t
        new_list.nb_r_p = 0
        new_list.nb_r_c = 0
        new_list.nb_c_n_e = nb_c_n_e
        new_list.nb_c_n_r = nb_c_n_r
        new_list.titre = f"{new_list.type_liste} {new_list.date}"

        if gener_urgence:
            new_list.type_liste = "Urgence"
            new_list.liste_maintenance = str(related_tache)
            new_list.titre = f"{new_list.type_liste}!!! {new_list.date}"

        new_list.save()
        frappe.db.commit()

        summary["nb_clients_in_final_list"] = T_t
        summary["nb_c_n_e"] = nb_c_n_e
        summary["nb_c_n_r"] = nb_c_n_r
        summary["type_liste_finale"] = new_list.type_liste
        summary["liste_created"] = True

        log(
            f"[LISTE APPEL] Nouvelle liste créée: {new_list.name} "
            f"(type={new_list.type_liste}, clients={T_t})"
        )
    else:
        log("[LISTE APPEL] Liste en brouillon déjà existante, aucune nouvelle liste créée.")

    summary_line = (
        "[SUMMARY] "
        f"date={summary['date']}, "
        f"draft_initial={summary['draft_count_initial']}, "
        f"second_call_lists={summary['second_call_source_lists']}, "
        f"second_call_clients_added={summary['second_call_clients_added']}, "
        f"urgence_found={summary['urgence_lists_found']}, "
        f"urgence_enabled={summary['urgence_enabled']}, "
        f"rdv_7_8_9={summary['nb_rdv_secteur_7_8_9']}, "
        f"all_ms={summary['nb_all_maintenance']}, "
        f"rdv_recents={summary['nb_rendez_vous_recent']}, "
        f"list_appel_candidates={summary['nb_list_appel_candidates']}, "
        f"list_appel_urgence_candidates={summary['nb_list_appel_urgence_candidates']}, "
        f"final_clients={summary['nb_clients_in_final_list']}, "
        f"nb_c_n_e={summary['nb_c_n_e']}, "
        f"nb_c_n_r={summary['nb_c_n_r']}, "
        f"type_final={summary['type_liste_finale']}, "
        f"liste_created={summary['liste_created']}"
    )
    end_line = "========== [CRON] Fin generate_liste_appelle_entretien =========="

    log(summary_line)
    log(end_line)

    return {
        "summary": summary,
        "log": [summary_line, end_line],
    }

def run_cron():
    run_safely("Cron - Création liste d'appel entretien", generate_liste_appelle_entretien)
