"""
Requalification après la correction de « Livraison sans tâche ».

La première version de l'affinage du 19/08 exigeait un paiement lié en attente
(Aramex/Dettes) — les brouillons WEB fraîchement arrivés, qui n'ont AUCUN
paiement, avaient disparu du radar. La règle corrigée les alerte de nouveau ;
ce patch rejoue le calcul sur toute la base pour restaurer leurs motifs.
"""

from customization_app.commande_alertes import recalculer_tout


def execute():
    modifiees = recalculer_tout()
    print(f"[recalc_anomalies_brouillons] {modifiees} commande(s) requalifiée(s).")
