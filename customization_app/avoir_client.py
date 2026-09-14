"""
Avoir client : création, solde disponible et imputation sur une commande.

Le process complet, en trois temps :
  1. BL de retour (Delivery Note négatif) — la marchandise revient.
  2. « Avoir > Créer un avoir » sur la commande d'origine : une écriture de
     journal FLOTTANTE crédite le client.
       - CRÉDIT  « Débiteurs - A&S » (party = client) → le client gagne un avoir.
       - DÉBIT   « Compte temporaire - compte  d'overture - A&S »
       - Journal Entry simple (voucher_type "Journal Entry"), réf. « Avoir <client> ».
     Aucun lien avec une commande : c'est un crédit client réutilisable.
  3. « Avoir > Utiliser un avoir » sur une AUTRE commande : on ajoute à son
     échéancier une ligne au mode « Avoir client » et on diminue d'autant une
     autre ligne (le total doit rester égal au TTC). Les Server Scripts
     « Generation payement » / « re-generate payment after sales order » créent
     alors l'écriture « Credit Note » mode « Avoir client » référencée sur la
     commande — elle REMPLACE le Payment Entry de la part diminuée. Le total
     alloué à la commande ne bouge donc pas : `advance_paid` reste le même,
     seule la nature du règlement change (une dette devient un avoir).

L'étape 3 était purement manuelle et invisible : d'où `avoirs_disponibles()`
(le solde, affiché en bandeau sur la commande) et `appliquer_avoir()` (le
rééquilibrage de l'échéancier). Rien ici n'écrit de comptabilité : on ne touche
QUE l'échéancier, les Server Scripts existants font l'écriture.

Attention : rien ne relie une utilisation à l'avoir d'origine. Le solde se lit
en MONTANTS (créés − utilisés), exactement comme les indicateurs « Avoirs
créés / utilisés / disponibles » de la fiche Client.
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, nowdate

RECEIVABLE_ACCOUNT = "Débiteurs - A&S"
# Nom exact tel qu'en base (double espace volontaire dans « compte  d'overture »).
DEFAULT_DEBIT_ACCOUNT = "Compte temporaire - compte  d'overture - A&S"

MODE_AVOIR = "Avoir client"
MODE_DETTE = "Dette non payée"

# Quelles échéances un avoir a le droit de remplacer.
#
# EN ATTENTE DE VALIDATION MÉTIER (ticket #23) : par défaut, la seule « Dette
# non payée ». C'est le choix prudent, et le seul qui soit défendable sans
# arbitrage du gérant :
#   - c'est le seul mode qui ne correspond à AUCUN encaissement réel ; le
#     Server Script « re-generate payment after sales order » le dit lui-même
#     (`pe_is_locked` : « Une "Dette non payée" n'est jamais un encaissement
#     reel : elle reste toujours regenerable ») ;
#   - diminuer une ligne Espèces / Chèque / Virement déjà encaissée serait de
#     toute façon refusée en aval : le script détecte le Payment Entry
#     verrouillé, RESTAURE les anciennes valeurs de la ligne en base et se
#     contente d'un message — l'échéancier se retrouverait alors avec une ligne
#     d'avoir en trop et un total supérieur au TTC ;
#   - rembourser un encaissement réel par un avoir, c'est une décision de
#     caisse (rendre l'argent ou porter un crédit), pas un rééquilibrage.
#
# Pour élargir, ajouter les modes ici : le serveur, le dialogue et le montant
# proposé s'y alignent automatiquement.
MODES_REDUCTIBLES = (MODE_DETTE,)

# Millime : la précision monétaire du site. Sert de marge aux comparaisons.
TOLERANCE = 0.001

# Statuts sur lesquels on ne peut plus rien imputer par l'échéancier.
STATUTS_BLOQUANTS = {
    "Closed": "fermée",
    "Cancelled": "annulée",
    "On Hold": "en attente",
}


@frappe.whitelist()
def create_avoir_from_sales_order(sales_order, amount, debit_account=None,
                                  reference=None, remark=None, posting_date=None):
    """Crée (et soumet) un Journal Entry d'avoir pour le client de la commande."""
    if not sales_order:
        frappe.throw(_("Commande manquante."))

    so = frappe.get_doc("Sales Order", sales_order)
    if so.docstatus != 1:
        frappe.throw(_("La commande doit être validée pour créer un avoir."))

    amount = flt(amount, 3)
    if amount <= 0:
        frappe.throw(_("Le montant de l'avoir doit être supérieur à 0."))

    customer = so.customer
    debit_account = debit_account or DEFAULT_DEBIT_ACCOUNT
    posting_date = posting_date or nowdate()
    reference = (reference or ("Avoir " + (customer or ""))).strip()

    if not frappe.db.exists("Account", debit_account):
        frappe.throw(_("Compte de contrepartie introuvable : {0}").format(debit_account))

    je = frappe.new_doc("Journal Entry")
    je.voucher_type = "Journal Entry"
    je.company = so.company
    je.posting_date = posting_date
    je.cheque_no = reference           # « Numéro de référence » (ex. « Avoir Technolab »)
    je.cheque_date = posting_date
    je.user_remark = (remark or reference)
    je.is_opening = "No"

    # Ligne 1 : crédit du client → il obtient un avoir réutilisable.
    je.append("accounts", {
        "account": RECEIVABLE_ACCOUNT,
        "party_type": "Customer",
        "party": customer,
        "credit_in_account_currency": amount,
    })
    # Ligne 2 : débit du compte de contrepartie (compte temporaire).
    je.append("accounts", {
        "account": debit_account,
        "debit_in_account_currency": amount,
    })

    je.insert(ignore_permissions=True)
    je.submit()

    return {
        "journal_entry": je.name,
        "customer": customer,
        "amount": amount,
        "advance_paid": flt(so.advance_paid, 3),
        "over_paid": amount > flt(so.advance_paid, 3),
    }


# ───────────────────────── Solde d'avoir du client ─────────────────────────
# Les fonctions ci-dessous doivent rester appelables sans site (tests unitaires
# purs) : messages en français sans `_()`, et arrondis via `_millimes` — `flt(x, 3)`
# lit le format de nombre du site et renvoie 0 en dehors.


def _millimes(valeur):
    """Un montant arrondi au millime, la précision monétaire du site."""
    return round(flt(valeur), 3)


@frappe.whitelist()
def avoirs_disponibles(customer):
    """Avoirs d'un client : créés (flottants), utilisés (imputés), disponible.

    Même lecture que les indicateurs de la fiche Client (Server Script « get
    customer information ») : un avoir CRÉÉ est un crédit « Débiteurs » sans
    référence dont la contrepartie est le compte temporaire d'ouverture ; un
    avoir UTILISÉ est le crédit référencé d'une écriture mode « Avoir client ».
    """
    if not customer:
        return {"cree": 0.0, "utilise": 0.0, "disponible": 0.0, "ecritures": []}

    # Le solde d'avoir est une donnée du client : on ne le livre qu'à qui a le
    # droit de lire sa fiche.
    frappe.has_permission("Customer", "read", doc=customer, throw=True)

    flottants = frappe.db.sql("""
        SELECT je.name AS journal_entry, je.posting_date AS date,
               jea.credit_in_account_currency AS montant
        FROM `tabJournal Entry` je
        JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
        WHERE je.docstatus = 1 AND jea.party_type = 'Customer' AND jea.party = %s
          AND jea.account = %s AND jea.credit_in_account_currency > 0
          AND (jea.reference_name IS NULL OR jea.reference_name = '')
          AND EXISTS (SELECT 1 FROM `tabJournal Entry Account` j2
                      WHERE j2.parent = je.name AND j2.account = %s)
        ORDER BY je.posting_date DESC, je.name DESC
    """, (customer, RECEIVABLE_ACCOUNT, DEFAULT_DEBIT_ACCOUNT), as_dict=True)

    utilises = frappe.db.sql("""
        SELECT SUM(jea.credit_in_account_currency) AS montant
        FROM `tabJournal Entry` je
        JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
        WHERE je.docstatus = 1 AND jea.party_type = 'Customer' AND jea.party = %s
          AND jea.account = %s AND je.mode_of_payment = %s
          AND jea.credit_in_account_currency > 0
          AND jea.reference_name IS NOT NULL AND jea.reference_name != ''
    """, (customer, RECEIVABLE_ACCOUNT, MODE_AVOIR), as_dict=True)

    cree = _millimes(sum(flt(f.get("montant")) for f in flottants))
    utilise = _millimes(utilises[0].get("montant") if utilises else 0)
    return {
        "cree": cree,
        "utilise": utilise,
        "disponible": _millimes(cree - utilise),
        "ecritures": [{
            "journal_entry": f.get("journal_entry"),
            "date": str(f.get("date") or ""),
            "montant": _millimes(f.get("montant")),
        } for f in flottants],
    }


def montant_imputable(disponible, ligne):
    """Ce qu'on peut imputer : ni plus que l'avoir disponible du client, ni plus
    que ce que porte la dette qu'on va diminuer.

    Ce n'est SURTOUT PAS « TTC − avance payée ». Sur ce site, chaque ligne de
    l'échéancier engendre un Payment Entry alloué à la commande — y compris les
    « Dette non payée », qui ne sont encaissées nulle part : `advance_paid` vaut
    donc le TTC dès la validation, même quand le client doit encore tout. S'en
    servir de plafond fermerait la porte à toutes les commandes.

    C'est aussi pourquoi imputer un avoir ne fait PAS monter `advance_paid` : on
    remplace une allocation par une autre, le total alloué ne bouge pas.

    Quelles échéances sont remplaçables : cf. `MODES_REDUCTIBLES`.
    """
    if not ligne or (ligne.get("mode_of_payment") or "") not in MODES_REDUCTIBLES:
        return 0.0
    porte = _millimes(ligne.get("payment_amount"))
    return max(_millimes(min(flt(disponible), porte)), 0.0)


def erreur_application(commande, disponible, montant=None, ligne=None):
    """Pourquoi l'imputation est refusée, en français — ou None si elle passe.

    `commande` : dict (docstatus, status, per_billed).
    `montant` et `ligne` sont facultatifs : sans eux on ne juge que l'éligibilité
    de la commande (c'est ce qui décide d'afficher ou non le bouton).
    """
    if flt(commande.get("docstatus")) != 1:
        return "La commande doit être validée pour y imputer un avoir."

    statut = commande.get("status") or ""
    if statut in STATUTS_BLOQUANTS:
        return ("La commande est %s : imputez l'avoir sur la facture, via "
                "Rapprochement de paiement." % STATUTS_BLOQUANTS[statut])

    if flt(commande.get("per_billed")) > 0:
        return ("La commande est déjà facturée : imputez l'avoir sur la facture, "
                "via Rapprochement de paiement.")

    disponible = _millimes(disponible)
    if disponible <= 0:
        return "Ce client n'a aucun avoir disponible."

    if montant is None:
        return None

    montant = _millimes(montant)
    if montant <= 0:
        return "Le montant à imputer doit être supérieur à 0."
    if montant > disponible + TOLERANCE:
        return ("Le montant à imputer (%s) dépasse l'avoir disponible du client (%s)."
                % (montant_lisible(montant), montant_lisible(disponible)))

    if ligne is None:
        return None

    mode = ligne.get("mode_of_payment") or ""

    # Diminuer une ligne « Avoir client » déjà imputée ferait diverger
    # l'échéancier et la comptabilité : à la régénération, le script conserve
    # l'écriture existante (même uid de ligne) sans corriger son montant, PUIS
    # crée celle de la nouvelle ligne.
    if mode == MODE_AVOIR:
        return ("L'échéance choisie est déjà un avoir : choisissez une ligne d'un "
                "autre mode de paiement.")

    # Un encaissement réel ne se remplace pas par un avoir (cf. MODES_REDUCTIBLES).
    if mode not in MODES_REDUCTIBLES:
        return ("Une échéance « %s » ne peut pas être remplacée par un avoir : "
                "seules les lignes « %s » le peuvent. Pour rendre un encaissement "
                "déjà reçu, passez par la caisse."
                % (mode or "sans mode de paiement", " » / « ".join(MODES_REDUCTIBLES)))

    if _millimes(ligne.get("payment_amount")) + TOLERANCE < montant:
        return ("L'échéance choisie (%s) ne couvre pas le montant de l'avoir (%s) : "
                "choisissez une autre ligne de l'échéancier."
                % (montant_lisible(ligne.get("payment_amount")), montant_lisible(montant)))

    return None


def montant_lisible(valeur):
    """« 1 234,500 DT » — pour les messages, sans dépendre du format du site."""
    texte = "{:,.3f}".format(_millimes(valeur))
    return texte.replace(",", " ").replace(".", ",") + " DT"


# ─────────────────────── Imputation sur une commande ───────────────────────


def index_lignes_reductibles(lignes):
    """Les échéances qu'on a le droit de diminuer, par index : celles dont le
    mode figure dans `MODES_REDUCTIBLES`, et qui portent un montant."""
    return [i for i, l in enumerate(lignes)
            if _millimes(l.get("payment_amount")) > 0
            and (l.get("mode_of_payment") or "") in MODES_REDUCTIBLES]


def index_ligne_a_reduire(lignes):
    """Quelle échéance diminuer par défaut pour faire place à l'avoir.

    Une « Dette non payée » d'abord — la plus grosse : c'est la seule ligne qui
    ne correspond à aucun encaissement réel, la diminuer ne touche à aucune
    caisse. À défaut (si `MODES_REDUCTIBLES` est élargi), la dernière ligne
    réductible.
    """
    reductibles = index_lignes_reductibles(lignes)
    dettes = [i for i in reductibles if (lignes[i].get("mode_of_payment") or "") == MODE_DETTE]
    if dettes:
        return max(dettes, key=lambda i: _millimes(lignes[i].get("payment_amount")))
    if reductibles:
        return reductibles[-1]
    return None


def date_echeance_libre(dates_prises, depart):
    """ERPNext refuse deux échéances à la même date : on décale d'un jour
    jusqu'à en trouver une de libre."""
    prises = {getdate(d) for d in dates_prises if d}
    jour = getdate(depart)
    while jour in prises:
        jour = getdate(add_days(jour, 1))
    return jour


def plan_application_avoir(lignes, index, montant, date_commande=None):
    """La modification à appliquer à l'échéancier, sans rien enregistrer.

    Le total ne bouge pas : la ligne choisie perd exactement ce que gagne la
    nouvelle ligne « Avoir client ». Si elle tombe à zéro elle disparaît — une
    échéance à 0 ferait échouer la génération du paiement.

    `invoice_portion` est répartie au prorata des montants : ERPNext recalcule
    `payment_amount` à partir d'elle à chaque enregistrement (set_payment_schedule),
    la laisser inchangée annulerait la réduction au premier save.
    """
    montant = _millimes(montant)
    ligne = lignes[index]
    initial = _millimes(ligne.get("payment_amount"))
    reste = _millimes(initial - montant)
    supprimer = reste < TOLERANCE

    portion = flt(ligne.get("invoice_portion"))
    portion_avoir = round(portion * montant / initial, 6) if portion and initial else 0

    # La date de la ligne supprimée redevient libre pour la ligne d'avoir.
    dates = [l.get("due_date") for i, l in enumerate(lignes) if not (supprimer and i == index)]
    echeance = date_echeance_libre(
        dates, ligne.get("due_date") or date_commande or nowdate())

    return {
        "index": index,
        "montant": montant,
        "supprimer_ligne": supprimer,
        "nouveau_montant_ligne": 0.0 if supprimer else reste,
        "nouvelle_portion_ligne": 0 if supprimer else round(portion - portion_avoir, 6),
        "ligne_avoir": {
            "due_date": echeance,
            "payment_amount": montant,
            "invoice_portion": portion_avoir,
            "mode_of_payment": MODE_AVOIR,
            "description": "Avoir client imputé sur cette commande",
        },
    }


def _lignes_echeancier(so):
    """L'échéancier réduit à ce dont les fonctions pures ont besoin."""
    return [{
        "nom": r.name,
        "idx": r.idx,
        "mode_of_payment": r.mode_of_payment,
        "payment_amount": _millimes(r.payment_amount),
        "invoice_portion": flt(r.invoice_portion),
        "due_date": r.due_date,
    } for r in so.payment_schedule]


def _resume_commande(so):
    return {
        "docstatus": so.docstatus,
        "status": so.status,
        "per_billed": flt(so.per_billed),
        "grand_total": _millimes(so.grand_total),
    }


def _commande(sales_order, permission):
    """La commande, une fois le droit `permission` vérifié pour l'utilisateur.

    `check_permission` lève une PermissionError explicite : ces méthodes sont
    appelables depuis le client, elles ne peuvent pas se contenter du fait que
    l'appelant connaît le nom d'une commande.
    """
    if not sales_order:
        frappe.throw(_("Commande manquante."))
    so = frappe.get_doc("Sales Order", sales_order)
    so.check_permission(permission)
    return so


@frappe.whitelist()
def contexte_avoir(sales_order):
    """Tout ce que la fiche commande affiche : le solde d'avoir du client, le
    montant imputable, les échéances diminuables et celle proposée."""
    so = _commande(sales_order, "read")
    avoirs = avoirs_disponibles(so.customer)
    commande = _resume_commande(so)
    lignes = _lignes_echeancier(so)
    reductibles = [lignes[i] for i in index_lignes_reductibles(lignes)]
    index = index_ligne_a_reduire(lignes)
    empechement = erreur_application(commande, avoirs["disponible"])
    if not empechement and index is None:
        empechement = ("Aucune échéance « %s » à diminuer sur cette commande : "
                       "il n'y a rien qu'un avoir puisse remplacer."
                       % " » / « ".join(MODES_REDUCTIBLES))

    return {
        "customer": so.customer,
        "devise": so.currency,
        "cree": avoirs["cree"],
        "utilise": avoirs["utilise"],
        "disponible": avoirs["disponible"],
        "ecritures": avoirs["ecritures"],
        "grand_total": commande["grand_total"],
        # Seules les échéances remplaçables sont proposées (MODES_REDUCTIBLES) :
        # ni un avoir déjà imputé, ni un encaissement réel.
        "lignes": reductibles,
        "modes_reductibles": list(MODES_REDUCTIBLES),
        "ligne_par_defaut": lignes[index]["nom"] if index is not None else None,
        "montant_propose": montant_imputable(
            avoirs["disponible"], lignes[index] if index is not None else None),
        "imputable": not empechement,
        "empechement": empechement,
    }


@frappe.whitelist()
def appliquer_avoir(sales_order, montant, ligne=None):
    """Impute un avoir disponible sur une commande, via son échéancier.

    On ne touche QUE l'échéancier : la ligne « Avoir client » ajoutée déclenche
    le Server Script « re-generate payment after sales order », qui crée
    l'écriture « Credit Note » référencée sur la commande. Créer nous-mêmes une
    écriture ici la compterait deux fois.
    """
    so = _commande(sales_order, "write")
    avoirs = avoirs_disponibles(so.customer)
    lignes = _lignes_echeancier(so)

    index = None
    if ligne:
        index = next((i for i, l in enumerate(lignes) if l["nom"] == ligne), None)
        if index is None:
            frappe.throw(_("Ligne d'échéancier introuvable sur cette commande."))
    else:
        index = index_ligne_a_reduire(lignes)
    if index is None:
        frappe.throw(_("Aucune ligne d'échéancier ne peut être diminuée sur cette commande."))

    erreur = erreur_application(
        _resume_commande(so), avoirs["disponible"], montant, lignes[index])
    if erreur:
        frappe.throw(erreur)

    plan = plan_application_avoir(lignes, index, montant, so.transaction_date)

    row = so.payment_schedule[plan["index"]]
    if plan["supprimer_ligne"]:
        so.payment_schedule = [r for r in so.payment_schedule if r is not row]
    else:
        row.payment_amount = plan["nouveau_montant_ligne"]
        if flt(row.invoice_portion):
            row.invoice_portion = plan["nouvelle_portion_ligne"]
    so.append("payment_schedule", plan["ligne_avoir"])
    # `append` numérote à partir de la longueur de la table : après une
    # suppression, l'idx obtenu ferait doublon.
    for position, r in enumerate(so.payment_schedule, start=1):
        r.idx = position

    so.save()

    return {
        "sales_order": so.name,
        "montant": plan["montant"],
        "ligne_reduite": row.name,
        "ligne_supprimee": plan["supprimer_ligne"],
        "disponible": _millimes(avoirs["disponible"] - plan["montant"]),
    }
