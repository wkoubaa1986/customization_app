"""Scénarios d'une journée à plusieurs clients : le moteur de messages tourne toutes les 2 min ; on le rejoue à des
instants choisis sur l'état « suivi » d'un technicien (ce que flotte_gps._suivi rend), en accumulant ce qui a déjà
été envoyé comme le fait le journal. Vérifie : qui reçoit quoi, quand, une seule fois, et rien aux mauvais clients."""

import unittest
from datetime import datetime, timedelta

from customization_app import flotte_gps_messages as fm

J = datetime(2026, 10, 9)
CFG = {"en_route": 1, "retard": 1, "lien": 1, "alerte": 1, "delai_max": 45, "tolerance": 30, "heure_min": 8 * 60, "heure_max": 19 * 60}


def H(h):
    return J + timedelta(hours=int(h), minutes=round((h % 1) * 60))


def tache(nom, debut, etape, eta=None, client=None):
    return {"name": nom, "debut": H(debut), "etape": etape, "eta": H(eta) if eta is not None else None, "client": client or nom}


class Journee:
    """Un technicien, N clients ; `tick` = un tour du moteur à un instant donné sur un état donné."""

    def __init__(self):
        self.deja = {}
        self.envoyes = []

    def tick(self, heure, etat, taches, employe="HR-EMP-00010"):
        emp = {"employe": employe, "nom": "Akram", "etat": etat, "taches": taches}
        out = fm.decider([emp], H(heure), CFG, self.deja)
        for m in out:
            self.deja.setdefault(m["tache"], set()).add(m["type"])
            self.envoyes.append((heure, m["tache"], m["type"]))
        return [(m["tache"], m["type"]) for m in out]


class TestJourneeTroisClients(unittest.TestCase):
    def test_tournee_a_l_heure(self):
        """A 09:00, B 10:30, C 14:00 — tout à l'heure : chaque client reçoit UN « en route », rien d'autre."""
        j = Journee()
        # 08:40 : départ du Magasin, A dans 15 min
        self.assertEqual(j.tick(8.67, "en mouvement", [tache("A", 9, "en route", 8.92), tache("B", 10.5, "à venir", 10.5), tache("C", 14, "à venir", 14)]),
                         [("A", fm.EN_ROUTE)])
        # 08:44 : même état, deux minutes plus tard → rien (déjà envoyé)
        self.assertEqual(j.tick(8.73, "en mouvement", [tache("A", 9, "en route", 8.95), tache("B", 10.5, "à venir", 10.5), tache("C", 14, "à venir", 14)]), [])
        # 09:00–09:40 chez A : à l'arrêt → rien pour B (il n'est pas en route)
        self.assertEqual(j.tick(9.3, "à l’arrêt", [tache("A", 9, "sur place", 9), tache("B", 10.5, "à venir", 10.6), tache("C", 14, "à venir", 14)]), [])
        # 09:42 : reparti ; B est à 48 min de route → pas encore (seuil 45)
        self.assertEqual(j.tick(9.7, "en mouvement", [tache("A", 9, "passée"), tache("B", 10.5, "en route", 10.5), tache("C", 14, "à venir", 14)]), [])
        # 09:50 : B dans 40 min → « en route » pour B seulement
        self.assertEqual(j.tick(9.83, "en mouvement", [tache("A", 9, "passée"), tache("B", 10.5, "en route", 10.5), tache("C", 14, "à venir", 14)]),
                         [("B", fm.EN_ROUTE)])
        # 13:30 : vers C, dans 25 min
        self.assertEqual(j.tick(13.5, "en mouvement", [tache("A", 9, "passée"), tache("B", 10.5, "passée"), tache("C", 14, "en route", 13.92)]),
                         [("C", fm.EN_ROUTE)])
        self.assertEqual([x[1:] for x in j.envoyes], [("A", fm.EN_ROUTE), ("B", fm.EN_ROUTE), ("C", fm.EN_ROUTE)])

    def test_retard_qui_se_propage(self):
        """A dure 1 h 30 au lieu de 30 min : B et C glissent → « retard » à B puis à C, chacun une fois, Salma alertée ;
        puis « en route » à B quand il part — sans second SMS de retard."""
        j = Journee()
        j.tick(8.67, "en mouvement", [tache("A", 9, "en route", 8.92), tache("B", 10.5, "à venir", 10.5), tache("C", 14, "à venir", 14)])
        # 10:05 : toujours chez A ; projection : B à 11:15 (+45), C à 14:00 (marge absorbée)
        r = j.tick(10.08, "à l’arrêt", [tache("A", 9, "sur place", 9), tache("B", 10.5, "à venir", 11.25), tache("C", 14, "à venir", 14)])
        self.assertEqual(sorted(r), [("B", fm.ALERTE), ("B", fm.RETARD)])
        # 10:20 : encore chez A ; B à 11:30 : rien de nouveau (déjà prévenu), C encore à l'heure
        self.assertEqual(j.tick(10.33, "à l’arrêt", [tache("A", 9, "sur place", 9), tache("B", 10.5, "à venir", 11.5), tache("C", 14, "à venir", 14.2)]), [])
        # 10:40 : parti vers B, arrivée 11:20 (dans 40 min). Le créneau de B est dépassé : pas de « en route » (B a déjà
        # reçu « retard » avec l'heure estimée, et on ne sait pas vers qui il roule) ; rien pour C.
        self.assertEqual(j.tick(10.67, "en mouvement", [tache("A", 9, "passée"), tache("B", 10.5, "en retard", 11.33), tache("C", 14, "à venir", 14.2)]), [])
        # 12:30 : chez B qui s'éternise ; C projeté à 14:45 (+45) → « retard » à C une fois
        r = j.tick(12.5, "à l’arrêt", [tache("A", 9, "passée"), tache("B", 10.5, "sur place", 11.33), tache("C", 14, "à venir", 14.75)])
        self.assertEqual(sorted(r), [("C", fm.ALERTE), ("C", fm.RETARD)])
        self.assertEqual(j.tick(12.6, "à l’arrêt", [tache("A", 9, "passée"), tache("B", 10.5, "sur place", 11.33), tache("C", 14, "à venir", 14.9)]), [])
        types_B = sorted(t for h, n, t in j.envoyes if n == "B")
        self.assertEqual(types_B, [fm.ALERTE, fm.RETARD])                   # un seul SMS à B, celui qui porte l'heure estimée

    def test_client_saute_puis_tournee_qui_continue(self):
        """Le technicien saute B (va directement à C) : B = alerte interne « à reprogrammer », jamais de SMS « en route » ;
        C reçoit son « en route » normalement ; D (après) n'est pas faussé."""
        j = Journee()
        j.tick(8.67, "en mouvement", [tache("A", 9, "en route", 8.92), tache("B", 10.5, "à venir", 10.5), tache("C", 12, "à venir", 12), tache("D", 15, "à venir", 15)])
        # 11:40 : A faite, il roule (vers C en réalité). B, créneau dépassé, est « en retard » : on ne sait pas vers qui il
        # roule → B reçoit « retard » (heure estimée) + alerte, JAMAIS « en route » ; C n'est pas la prochaine → rien.
        r = j.tick(11.67, "en mouvement", [tache("A", 9, "passée"), tache("B", 10.5, "en retard", 11.83), tache("C", 12, "à venir", 12.2), tache("D", 15, "à venir", 15)])
        self.assertEqual(sorted(r), [("B", fm.ALERTE), ("B", fm.RETARD)])
        # 12:05 : sur place chez C → B sautée : alerte « non faite, à reprogrammer » (distincte de l'alerte retard)
        r = j.tick(12.08, "à l’arrêt", [tache("A", 9, "passée"), tache("B", 10.5, "sautée"), tache("C", 12, "sur place", 12), tache("D", 15, "à venir", 15)])
        self.assertEqual(r, [("B", fm.NON_FAITE)])
        self.assertNotIn(fm.EN_ROUTE, j.deja.get("B", set()))
        # 14:40 : vers D, à l'heure → « en route » à D ; B toujours muette
        self.assertEqual(j.tick(14.67, "en mouvement", [tache("A", 9, "passée"), tache("B", 10.5, "sautée"), tache("C", 12, "passée"), tache("D", 15, "en route", 14.95)]),
                         [("D", fm.EN_ROUTE)])

    def test_deux_techniciens_ne_se_melangent_pas(self):
        j = Journee()
        akram = {"employe": "HR-EMP-00010", "nom": "Akram", "etat": "en mouvement", "taches": [tache("A1", 10, "en route", 9.9, "Client Akram")]}
        hedi = {"employe": "HR-EMP-00006", "nom": "Hedi", "etat": "à l’arrêt", "taches": [tache("H1", 10, "sur place", 10, "Client Hedi"), tache("H2", 11, "à venir", 12, "Client Hedi 2")]}
        r = fm.decider([akram, hedi], H(9.5), CFG, {})
        self.assertEqual(sorted((m["tache"], m["type"]) for m in r), [("A1", fm.EN_ROUTE), ("H2", fm.ALERTE), ("H2", fm.RETARD)])
        self.assertEqual({m["employe"] for m in r if m["tache"] == "A1"}, {"HR-EMP-00010"})
        self.assertEqual({m["employe"] for m in r if m["tache"] == "H2"}, {"HR-EMP-00006"})


if __name__ == "__main__":
    unittest.main()
