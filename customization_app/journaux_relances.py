"""Page « Journaux des relances » : pour chaque cron de l'entretien, le dernier passage (journal des tâches planifiées), les
dernières lignes de résumé de son fichier journal, et les erreurs récentes. Lecture seule."""
from __future__ import annotations

import os
import re

import frappe
from frappe.utils import add_days, get_site_path, now_datetime

from customization_app import relances_config as RC

ROLES = ("System Manager", "Sales Manager", "Maintenance Manager")
MOTIF_RESUME = re.compile(r"\[(SUMMARY|CRON SUMMARY|MANUAL SUMMARY|CREATE [^\]]+|CLEANUP|EXTEND|REQUALIF|REPARTITION|2e APPEL|INFO)\]")


def _dernieres_lignes(nom_journal: str, n: int = 12) -> list:
    """Les n dernières lignes « utiles » du fichier logs/<nom>.log du site (puis du bench)."""
    if not nom_journal:
        return []
    for chemin in (get_site_path("logs", nom_journal + ".log"), frappe.get_site_path("..", "..", "logs", nom_journal + ".log")):
        if os.path.exists(chemin):
            try:
                with open(chemin, "rb") as f:
                    f.seek(0, os.SEEK_END)
                    taille = f.tell()
                    f.seek(max(0, taille - 200_000))
                    lignes = f.read().decode("utf-8", "replace").splitlines()
            except Exception:
                return []
            utiles = [l for l in lignes if MOTIF_RESUME.search(l)]
            return (utiles or lignes)[-n:]
    return []


@frappe.whitelist()
def resume(jours=2):
    frappe.only_for(ROLES)
    depuis = add_days(now_datetime(), -int(jours or 2))
    out = []
    for c in RC.CRONS:
        derniers = frappe.get_all("Scheduled Job Log", filters={"scheduled_job_type": ["like", "%" + c["cle"]], "creation": [">=", add_days(now_datetime(), -40)]},
                                  fields=["status", "creation", "details"], order_by="creation desc", limit=5)
        erreurs = []
        for motif in c["erreurs"]:
            erreurs += frappe.get_all("Error Log", filters={"method": ["like", motif], "creation": [">=", depuis]},
                                      fields=["name", "method", "creation"], order_by="creation desc", limit=10)
        erreurs = sorted({e.name: e for e in erreurs}.values(), key=lambda e: e.creation, reverse=True)[:10]
        out.append({"cle": c["cle"], "titre": c["titre"], "quand": c["quand"], "journal": c["journal"],
                    "dernier": derniers[0] if derniers else None, "passages": derniers,
                    "lignes": _dernieres_lignes(c["journal"]), "erreurs": erreurs, "nb_erreurs": len(erreurs)})
    return {"crons": out, "email_alertes": ", ".join(RC.emails_alertes()), "depuis": str(depuis)[:16],
            "liens": {"jobs": "/app/scheduled-job-log?scheduled_job_type=%5B%22like%22%2C%22%25customization_app%25%22%5D",
                      "erreurs": "/app/error-log", "reglage": "/app/config-relances", "listes": "/app/liste-appelle-entretien"}}
