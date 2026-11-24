"""Calcul d'une fiche de paie tunisienne — PUR (aucun accès à la base), testé sur les bulletins du comptable.

Règles retrouvées le 24/11/2026 sur « FICHE DE PAIE STE AQUA 10 2026.pdf » (6 bulletins d'octobre 2026, tous
reproduits) :
- brut = salaire de base + prime de présence 2,080 + prime de transport 36,112 (pas de primes pour un CIVP) ;
- CNSS salariale 9,68 % du brut — CDI seulement (ni le gérant, ni le CIVP) ;
- imposable = brut − CNSS ; IRPP sur l'annuel (× 12) diminué des frais professionnels (10 %, plafonnés à 2 000),
  ce revenu net ARRONDI AU DINAR SUPÉRIEUR, barème par tranches, puis ÷ 12 au millime ;
- CSS (contribution sociale de solidarité) = 0,5 % du même revenu net annuel NON arrondi, ÷ 12 ;
- net à payer arrondi au dinar (fait par la fiche de paie elle-même : `rounded_total`).
Écart connu : la CSS du gérant sort à 9,382 au lieu de 9,383 (1 millime) ; tout le reste est exact au millime.
Le barème au-delà de 30 000 DT/an n'a été vérifié sur aucun bulletin (aucun salaire aussi haut).
Pas de déduction chef de famille / enfants : le comptable n'en applique aucune (gérant « Marié(e) 0 »).
"""
import math
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

CDI, CIVP, GERANT = "CDI", "CIVP", "Gérant"
PROFILS = {
    # primes : présence + transport ; cnss : retenue salariale ; impot : IRPP + CSS
    CDI: {"primes": True, "cnss": True, "impot": True},
    CIVP: {"primes": False, "cnss": False, "impot": False},
    GERANT: {"primes": True, "cnss": False, "impot": True},
}
JOURS_MOIS = 26
PRIME_PRESENCE = 2.080
PRIME_TRANSPORT = 36.112
TAUX_CNSS = 0.0968
FRAIS_PRO_TAUX = 0.10
FRAIS_PRO_PLAFOND = 2000.0
TAUX_CSS = 0.005
# (plafond annuel de la tranche, taux) — barème IRPP en vigueur depuis 2025.
BAREME_IRPP = [(5000, 0.0), (10000, 0.15), (20000, 0.25), (30000, 0.30), (40000, 0.33), (50000, 0.36), (None, 0.40)]


def m3(x) -> float:
    """Arrondi au millime, demi vers le haut (comme le logiciel du comptable)."""
    return float(Decimal(repr(float(x))).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def irpp_annuel(revenu: float) -> float:
    impot, bas = 0.0, 0.0
    for haut, taux in BAREME_IRPP:
        if revenu > bas:
            impot += ((min(revenu, haut) if haut else revenu) - bas) * taux
        if haut is None or revenu <= haut:
            break
        bas = haut
    return impot


def impots_du_mois(imposable: float) -> tuple[float, float]:
    """(IRPP, CSS) du mois pour un salaire imposable mensuel."""
    annuel = imposable * 12
    net_annuel = annuel - min(annuel * FRAIS_PRO_TAUX, FRAIS_PRO_PLAFOND)
    if net_annuel <= 0:
        return 0.0, 0.0
    return m3(irpp_annuel(math.ceil(round(net_annuel, 6))) / 12), m3(net_annuel * TAUX_CSS / 12)


def bulletin(profil: str, base: float, jours: float = JOURS_MOIS, autres_gains: float = 0.0,
             autres_retenues: float = 0.0) -> dict:
    """Les montants d'une fiche. `jours` : jours payés sur 26 (entrée / sortie en cours de mois, absences non
    payées) ; `autres_gains` : primes exceptionnelles soumises (CNSS, IRPP) ; `autres_retenues` : retenues après
    impôt (avance…)."""
    regles = PROFILS[profil]
    ratio = max(0.0, min(float(jours), JOURS_MOIS)) / JOURS_MOIS
    out = {"profil": profil, "jours": float(jours), "base": m3(base * ratio),
           "prime_presence": m3(PRIME_PRESENCE * ratio) if regles["primes"] else 0.0,
           "prime_transport": m3(PRIME_TRANSPORT * ratio) if regles["primes"] else 0.0}
    out["brut"] = m3(out["base"] + out["prime_presence"] + out["prime_transport"] + (autres_gains or 0))
    out["cnss"] = m3(out["brut"] * TAUX_CNSS) if regles["cnss"] else 0.0
    out["imposable"] = m3(out["brut"] - out["cnss"])
    out["irpp"], out["css"] = impots_du_mois(out["imposable"]) if regles["impot"] else (0.0, 0.0)
    out["net"] = m3(out["imposable"] - out["irpp"] - out["css"] - (autres_retenues or 0))
    return out


def jours_payes(debut_mois: date, fin_mois: date, debut_paie: date | None = None, embauche: date | None = None,
                sortie: date | None = None, absences: float = 0) -> float:
    """26 pour un mois complet ; sinon les jours ouvrables (lundi → samedi) de la période payée, au plus 26 ;
    moins les jours d'absence non payés saisis sur la fiche."""
    deb = max(d for d in (debut_mois, debut_paie, embauche) if d)
    fin = min(d for d in (fin_mois, sortie) if d)
    if deb > fin:
        return 0.0
    if deb <= debut_mois and fin >= fin_mois:
        jours = JOURS_MOIS
    else:
        jours = min(JOURS_MOIS, sum(1 for i in range((fin - deb).days + 1) if (deb + timedelta(days=i)).weekday() != 6))
    return max(0.0, float(jours) - float(absences or 0))


def mois_a_traiter(debut_paie: date, aujourd_hui: date, sortie: date | None = None,
                   premier_mois: date = date(2026, 1, 1)) -> list[date]:
    """Premiers jours des mois qui doivent avoir une fiche : du mois de début (pas avant janvier 2026) au mois en
    cours, sans dépasser le mois de sortie."""
    m = max(debut_paie.replace(day=1), premier_mois)
    fin = aujourd_hui.replace(day=1)
    if sortie:
        fin = min(fin, sortie.replace(day=1))
    out = []
    while m <= fin:
        out.append(m)
        m = (m.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def fin_du_mois(premier: date) -> date:
    return (premier.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


JOURS_AVANT_FIN = 4


def a_valider(premier: date, aujourd_hui: date) -> bool:
    """La fiche du mois se valide toute seule 4 jours avant la fin du mois (demande du 24/11/2026) : le 26 pour un
    mois de 30 jours, le 27 pour 31 jours. Jusque-là, elle reste modifiable."""
    return aujourd_hui >= fin_du_mois(premier) - timedelta(days=JOURS_AVANT_FIN)
