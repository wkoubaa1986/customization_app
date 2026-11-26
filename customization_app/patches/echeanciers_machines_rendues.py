"""Rattrapage : les machines RENDUES en 2026 ne sont plus relancées (proposition 19 de l'analyse « cartographie entretien »).

Jusqu'au 26/11/2026, un BL de retour ne retirait rien : l'échéancier de la machine rendue restait actif et le client
continuait de recevoir SMS et appels pour une machine qu'il n'a plus. Le hook `retours_machines.on_submit_delivery_note`
l'annule désormais à la validation du retour ; ce patch rejoue la MÊME règle (retours_machines.traiter_retour) sur les
BL de retour déjà validés depuis le 01/01/2026 :
  - échéancier ANNULÉ (jamais supprimé), avec un commentaire sur l'échéancier et sur le client, seulement si toute la
    quantité de chacune de ses machines est rendue ;
  - sinon un commentaire seulement (pas deux fois) ; une reprise d'échange n'est jamais un rendu ;
  - les listes d'appels BROUILLON qui citent un échéancier annulé sont dégagées au moment du migrate (ligne retirée si
    pas encore appelée, rattachée à un autre échéancier du client, ou gardée sans lien) : sinon la liste ne
    s'enregistrerait plus (relecture du 26/11/2026). Aucune sur la copie du 25/11/2026.
Mesuré sur la copie de prod du 25/11/2026 : 6 BL de retour rendent une machine (7 lignes) ; 5 échéanciers annulés
(MAT-MSH-2026-00889, 00928, 01042, 01059, 01259 — 49 visites à venir, premières relances dès le 20/02/2027 ; commandes
fermées, la nuit ne les recrée pas : vérifié par une nuit simulée) ; 1 BL sans échéancier (deux porte-filtres de Ghali,
jamais suivis). Rejoué une 2e fois : 0 annulé, 5 « déjà annulés », aucun commentaire en double.
Rejouable sans effet : un échéancier déjà annulé est seulement compté. Le compte rendu est rendu, imprimé et journalisé."""
import frappe

from customization_app import retours_machines as RM

DEPUIS = "2026-01-01"


def compte_rendu(total: dict) -> str:
    """Le compte rendu lisible du rattrapage. PURE."""
    lignes = ["Machines rendues depuis le %s : %d BL de retour lu(s)." % (DEPUIS, total["bl"]),
              "Échéanciers annulés : %d%s" % (len(total["annules"]),
                                              (" (" + ", ".join(total["annules"]) + ")") if total["annules"] else ""),
              "Échéanciers gardés : %d" % len(total["gardes"])]
    lignes += ["  - %s : %s" % (nom, motif) for nom, motif in total["gardes"]]
    lignes.append("Déjà annulés : %d%s" % (len(total["deja_annules"]),
                                           (" (" + ", ".join(total["deja_annules"]) + ")") if total["deja_annules"] else ""))
    lignes.append("Machines rendues sans échéancier : %d%s" % (
        len(total["sans_echeancier"]),
        (" (" + ", ".join("%s / %s" % (a, c or "commande inconnue") for a, c in total["sans_echeancier"]) + ")")
        if total["sans_echeancier"] else ""))
    listes = total.get("listes") or []
    lignes.append("Lignes de listes d'appels brouillon dégagées : %d%s" % (
        len(listes), (" (" + ", ".join("%s : %s %s%s" % (l, action, client or "?", (" → " + r) if r else "")
                                       for l, action, client, r in listes) + ")") if listes else ""))
    lignes.append("Erreurs : %d" % total["erreurs"])
    return "\n".join(lignes)


def execute():
    total = RM.rattraper(DEPUIS)
    texte = compte_rendu(total)
    print(texte)
    frappe.log_error(title="Machines rendues (rattrapage %s)" % DEPUIS, message=texte)
    return total
