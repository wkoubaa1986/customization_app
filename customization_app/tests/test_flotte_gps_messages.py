"""Messages clients GPS : la décision (pure) — quoi envoyer, à qui, et une seule fois."""

import unittest
from datetime import datetime, timedelta

from customization_app import flotte_gps_messages as fm

J = datetime(2026, 10, 8)
CFG = {"en_route": 1, "retard": 1, "lien": 1, "alerte": 1, "delai_max": 45, "tolerance": 30, "heure_min": 8 * 60, "heure_max": 19 * 60}


def _t(nom, h, etape, eta_h=None, client="Client X"):
    d = J + timedelta(hours=int(h), minutes=round((h % 1) * 60))
    return {"name": nom, "debut": d, "etape": etape, "client": client,
            "eta": (J + timedelta(hours=int(eta_h), minutes=round((eta_h % 1) * 60))) if eta_h is not None else None}


def _e(etat, taches, employe="HR-EMP-00010"):
    return {"employe": employe, "nom": "Akram", "etat": etat, "taches": taches}


class TestDecider(unittest.TestCase):
    def test_en_route_vers_la_prochaine_seulement(self):
        now = J + timedelta(hours=14, minutes=10)
        emp = _e("en mouvement", [_t("A", 12, "passée"), _t("B", 14.5, "à venir", 14.5), _t("C", 16, "à venir", 16.1)])
        r = fm.decider([emp], now, CFG, {})
        self.assertEqual([(m["tache"], m["type"]) for m in r], [("B", fm.EN_ROUTE)])

    def test_pas_de_en_route_si_trop_loin_ou_a_l_arret(self):
        now = J + timedelta(hours=10)
        emp = _e("en mouvement", [_t("B", 12, "à venir", 11.5)])           # dans 90 min > 45
        self.assertEqual(fm.decider([emp], now, CFG, {}), [])
        emp = _e("à l’arrêt", [_t("B", 10.5, "à venir", 10.5)])
        self.assertEqual(fm.decider([emp], now, CFG, {}), [])

    def test_une_seule_fois(self):
        now = J + timedelta(hours=14, minutes=10)
        emp = _e("en mouvement", [_t("B", 14.5, "en route", 14.5)])
        self.assertEqual(fm.decider([emp], now, CFG, {"B": {fm.EN_ROUTE}}), [])

    def test_retard_et_alerte_interne(self):
        now = J + timedelta(hours=14)
        emp = _e("à l’arrêt", [_t("B", 14, "sur place", 14), _t("C", 15, "à venir", 16)])   # C : +60 min
        r = fm.decider([emp], now, CFG, {})
        self.assertEqual(sorted((m["tache"], m["type"]) for m in r), [("C", fm.ALERTE), ("C", fm.RETARD)])
        self.assertEqual(r[0]["ecart"], 60)
        # Déjà prévenus : plus rien.
        self.assertEqual(fm.decider([emp], now, CFG, {"C": {fm.RETARD, fm.ALERTE}}), [])

    def test_en_route_avec_retard_ne_double_pas_le_sms(self):
        # Prochaine intervention, en mouvement, arrivée dans 20 min mais 50 min après l'heure annoncée :
        # un SMS « en route » (qui porte déjà l'heure estimée) + l'alerte interne, pas de second SMS « retard ».
        now = J + timedelta(hours=14, minutes=30)
        emp = _e("en mouvement", [_t("B", 14, "en retard", 14.83)])
        r = fm.decider([emp], now, CFG, {})
        self.assertEqual(sorted(m["type"] for m in r), [fm.ALERTE, fm.EN_ROUTE])

    def test_hors_plage_horaire_sms_mais_alerte_interne(self):
        now = J + timedelta(hours=19, minutes=30)
        emp = _e("en mouvement", [_t("B", 19, "en retard", 20)])
        r = fm.decider([emp], now, CFG, {})
        self.assertEqual([m["type"] for m in r], [fm.ALERTE])

    def test_sautee_alerte_interne_jamais_de_sms(self):
        now = J + timedelta(hours=14, minutes=10)
        emp = _e("en mouvement", [_t("B", 10.5, "sautée"), _t("C", 14.5, "en route", 14.5)])
        r = fm.decider([emp], now, CFG, {})
        self.assertEqual(sorted((m["tache"], m["type"]) for m in r), [("B", fm.ALERTE), ("C", fm.EN_ROUTE)])
        self.assertEqual(fm.decider([emp], now, CFG, {"B": {fm.ALERTE}, "C": {fm.EN_ROUTE}}), [])

    def test_reglage_eteint(self):
        now = J + timedelta(hours=14)
        emp = _e("en mouvement", [_t("B", 14.5, "à venir", 15.5)])
        cfg = dict(CFG, en_route=0, retard=0, alerte=0)
        self.assertEqual(fm.decider([emp], now, cfg, {}), [])


if __name__ == "__main__":
    unittest.main()
