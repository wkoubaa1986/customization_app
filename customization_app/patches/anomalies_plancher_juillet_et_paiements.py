"""
Recalcule le champ Anomalie après trois changements de règle (19/08/2026) :

  - PLANCHER : la surveillance ne s'applique qu'aux commandes à partir du
    01/07/2026 — l'historique antérieur se vide (≈488 commandes au moment du
    changement) ;
  - « Main d'œuvre sans tâche » ne se lève plus sur une commande validée (ou
    facturée) dont aucun paiement lié n'est parqué en dette ;
  - « Livraison sans tâche » exige qu'un paiement lié attende encore sur
    Livraison Aramex ou Dettes.

Voir customization_app/commande_alertes.py — la règle vit dans _SQL_MOTIF, ce
patch ne fait que rejouer le recalcul complet.
"""

from customization_app.commande_alertes import recalculer_tout


def execute():
    modifiees = recalculer_tout()
    print(f"[anomalies_plancher_juillet_et_paiements] {modifiees} commande(s) requalifiée(s).")
