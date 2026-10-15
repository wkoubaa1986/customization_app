"""Données (15/10/2026) : la requalification comptait des VISITES et non des cycles (un client à trois appareils passait
« À requalifier » après un seul cycle raté), et le rattrapage des entretiens vient de solder des visites qui comptaient.
Les « À requalifier » sous le seuil de cycles reprennent la relance normale, avec une trace. Rejouable sans effet."""
import frappe

from customization_app.Maintenance import update_schedule as U


def execute():
    n = U.reactiver_requalifies_a_tort()
    print("recalculer_requalification : %d client(s) réactivé(s)" % n)
    frappe.db.commit()
