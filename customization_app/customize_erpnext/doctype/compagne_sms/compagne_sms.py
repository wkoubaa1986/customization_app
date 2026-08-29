import math
import re
from functools import lru_cache

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cstr
from frappe.core.doctype.sms_settings.sms_settings import send_sms


# ------------------------------------------------------------------ GSM-7
# UN SEUL caractère hors alphabet GSM 03.38 bascule TOUT le SMS en unicode :
# 67 caractères par segment au lieu de 153 — un message de 320 caractères passe
# de 3 à 5 segments (constaté au réel le 29/08/2026 : « — » dans un modèle →
# 4 segments unicode facturés). On translittère les coupables usuels ; é è à ù
# ì ò font PARTIE de l'alphabet GSM et restent intacts.
_TRANSLIT_SMS = str.maketrans({
    "—": "-", "–": "-", "−": "-",
    "’": "'", "‘": "'", "´": "'", "`": "'",
    "“": '"', "”": '"', "«": '"', "»": '"',
    "…": "...",
    "œ": "oe", "Œ": "OE",
    "â": "a", "ê": "e", "î": "i", "ô": "o", "û": "u",
    "ë": "e", "ï": "i", "ç": "c", "Ç": "C",
    # fmt_money sépare les milliers par des espaces INSÉCABLES : hors GSM.
    "\u00a0": " ", "\u202f": " ",
})


def normaliser_sms(texte: str) -> str:
    return (texte or "").translate(_TRANSLIT_SMS)


# La passerelle répond HTTP 200 même quand elle refuse (crédit, numéro…) :
# le refus est DANS le corps. Marqueurs relevés sur les réponses WinSMS.
MARQUEURS_ERREUR_SMS = ("error", "invalid", "insufficient", "failed", "\"ko\"",
                        "not enough", "expired", "unauthorized")


def envoyer_sms_verifie(numero: str, message: str, tentatives: int = 3) -> str:
    """Envoi direct sur la passerelle avec réponse JSON exigée et VÉRIFIÉE,
    et RELANCE sur panne réseau.

    Contrairement à _send_sms_with_fallback (qui avale les échecs — assumé
    pour les campagnes de masse), ici un refus de la passerelle LÈVE : c'est
    le chemin des messages dont on veut un verdict honnête (OTP du portail,
    messages client des tâches). -> le corps de la réponse (tronqué).

    Retry : UNIQUEMENT sur les pannes réseau (timeout, connexion) — la
    passerelle n'a probablement rien reçu, on retente après 2 s puis 5 s.
    Un REFUS FERME (HTTP d'erreur, marqueur dans le corps) ne se retente
    JAMAIS : le SMS a pu partir malgré le marqueur, et un crédit épuisé ne
    se répare pas en insistant — relancer doublerait les messages.
    """
    import time
    import urllib.error
    import urllib.request
    from urllib.parse import urlencode

    ss = frappe.get_doc("SMS Settings", "SMS Settings")
    params = {p.parameter: p.value for p in ss.get("parameters") if not p.header}
    params[ss.receiver_parameter] = numero
    params[ss.message_parameter] = normaliser_sms(message)
    params.setdefault("response", "json")
    url = ss.sms_gateway_url + "?" + urlencode(params)

    derniere = None
    for essai in range(max(1, tentatives)):
        if essai:
            time.sleep((2, 5)[min(essai - 1, 1)])
        try:
            reponse = urllib.request.urlopen(url, timeout=15)
        except urllib.error.HTTPError as e:
            corps = (e.read() or b"").decode("utf-8", "replace")[:300]
            raise Exception("passerelle HTTP %s : %s" % (e.code, corps))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            derniere = e
            continue
        corps = (reponse.read() or b"").decode("utf-8", "replace")[:300]
        if any(m in corps.lower() for m in MARQUEURS_ERREUR_SMS):
            raise Exception("passerelle en erreur : %s" % corps)
        return corps
    raise Exception("passerelle injoignable après %d tentatives : %s"
                    % (max(1, tentatives), str(derniere)[:120]))


def _send_sms_with_fallback(phones: list, message: str) -> None:
    """Envoie un SMS via Frappe SMS Settings. Fallback urllib direct si erreur (ex: 429)."""
    message = normaliser_sms(message)
    import urllib.request
    import urllib.error
    from urllib.parse import urlencode

    for phone in phones:
        # Tentative 1 : via couche Frappe
        try:
            send_sms([phone], message)
            continue
        except Exception as e1:
            frappe.log_error(f"phone={phone} err={str(e1)[:80]}", "SMS: fallback urllib")

        # Tentative 2 : appel direct WinSMSPro via urllib
        try:
            ss = frappe.get_doc("SMS Settings", "SMS Settings")
            params = {p.parameter: p.value for p in ss.get("parameters") if not p.header}
            params[ss.receiver_parameter] = phone
            params[ss.message_parameter] = message
            url = ss.sms_gateway_url + "?" + urlencode(params)
            try:
                resp = urllib.request.urlopen(url, timeout=15)
                resp.read()
            except urllib.error.HTTPError as http_err:
                body = http_err.read().decode() if http_err else ""
                frappe.log_error(f"phone={phone} status={http_err.code} body={body}", "SMS: fallback HTTP error")
        except Exception as e2:
            frappe.log_error(frappe.get_traceback(), "SMS: échec fallback urllib")

# -------------------------------------------------------------------
#  Constantes
# -------------------------------------------------------------------

DRY_RUN = False  # Pour tests locaux sans envoi de SMS
APP_NAME = "booking_ristourne"

# ⚠️ METS ICI LE(S) FIELDNAME(S) EXACT(S) DU CHAMP SUR CUSTOMER
#     (colonne "Nom" dans le DocType "Customer", PAS l'étiquette)
AUTORISATION_FIELDS = [
    "custom_autoriser_accès_fiche_client",
]


# -------------------------------------------------------------------
#  Helpers autorisation
# -------------------------------------------------------------------

def _get_autorisation_from_dict(row: dict) -> int | None:
    """
    Essaie de récupérer la valeur d'autorisation dans un dict (résultat de get_all).
    Retourne:
        - 0 ou 1 (ou autre int) si trouvé
        - None si aucun des champs n'existe
    """
    for f in AUTORISATION_FIELDS:
        if f in row:
            return row.get(f)
    return None


def _get_autorisation_from_customer(name: str) -> int | None:
    """
    Essaie de récupérer la valeur d'autorisation directement en DB pour un client.
    Retourne:
        - 0 ou 1 si trouvé
        - None si aucun champ d'autorisation n'existe
    """
    for f in AUTORISATION_FIELDS:
        if frappe.db.has_column("Customer", f):
            return frappe.db.get_value("Customer", name, f)
    return None


def is_customer_authorized_from_dict(row: dict) -> bool:
    """
    True  -> client autorisé
    False -> client explicitement non autorisé (valeur 0)
    Par défaut, on considère AUTORISÉ si le champ n'existe pas / est None.
    """
    val = _get_autorisation_from_dict(row)
    if val is None:
        return True  # pas de champ -> on autorise
    return int(val) != 0


def is_customer_authorized_from_name(name: str) -> bool:
    """
    Idem que ci-dessus, mais à partir du nom du client (lecture DB).
    """
    val = _get_autorisation_from_customer(name)
    if val is None:
        return True
    return int(val) != 0


# -------------------------------------------------------------------
#  Helpers booking_ristourne
# -------------------------------------------------------------------

@lru_cache
def _is_booking_app_installed() -> bool:
    """Vérifie une seule fois si booking_ristourne est installée (cache)."""
    return bool(
        frappe.db.exists("Installed Application", {"app_name": APP_NAME})
    )


# -------------------------------------------------------------------
#  Fonctions métier pour les ristournes (séparées)
# -------------------------------------------------------------------

def get_ristourne_acc(customer_name: str) -> float:
    """
    Ristourne accumulée du client.
    Lit le champ 'ristourne' du résultat de generate_ristourne_report(customer).
    """
    if not _is_booking_app_installed():
        return 0.0

    try:
        from booking_ristourne.ristourne import generate_ristourne_report

        current_ristourne = generate_ristourne_report(customer_name) or {}
        ristourne_value = current_ristourne.get("ristourne", 0.0)

        return float(ristourne_value or 0.0)

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"[Compagne SMS] Erreur get_ristourne_acc pour {customer_name}",
        )
        return 0.0


def get_ristourne_uti(customer_name: str) -> float:
    """
    Ristourne disponible à date.
    Lit le champ 'available_to_date' du résultat de
    get_available_for_sales_order(customer=...).
    """
    if not _is_booking_app_installed():
        return 0.0

    try:
        from booking_ristourne.sales_order import get_available_for_sales_order

        ristourne_situation = get_available_for_sales_order(customer=customer_name) or {}
        available_to_date = ristourne_situation.get("available_to_date", 0.0)

        return float(available_to_date or 0.0)

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            f"[Compagne SMS] Erreur get_ristourne_uti pour {customer_name}",
        )
        return 0.0


# -------------------------------------------------------------------
#  Normalisation et filtrage des numéros tunisiens
# -------------------------------------------------------------------

def normaliser_numero(telephone_raw):
    """Nettoie un numéro et renvoie 0, 1 ou 2 numéros (8 chiffres) possibles."""
    tel = (telephone_raw or "").replace(" ", "").replace("-", "")
    if not tel:
        return []

    nums = []

    if tel.startswith("+216"):
        tel = tel[4:]

    if len(tel) == 8:
        nums.append(tel)
    elif len(tel) == 16:
        nums.append(tel[:8])
        nums.append(tel[8:])

    return nums


def is_mobile_tunisien(num):
    """Mobiles tunisiens classiques : 2,4,5,9."""
    return len(num) == 8 and num[0] in ("2", "4", "5", "9")


def traiter_numero_tel(champ_tel):
    """
    Prend liste_tel (multi-ligne ou séparée par virgule),
    renvoie la liste de numéros mobiles tunisiens valides (8 chiffres, uniques).
    """
    tel_to_send = []

    # on accepte séparateurs : retour ligne, virgule, point-virgule, slash
    raw_global = re.sub(r"[;\n/]+", ",", champ_tel or "")

    for raw in raw_global.split(","):
        raw = raw.strip()
        if not raw:
            continue
        for num in normaliser_numero(raw):
            if is_mobile_tunisien(num):
                tel_to_send.append(num)

    # dédoublonnage
    seen = set()
    unique = []
    for n in tel_to_send:
        if n not in seen:
            seen.add(n)
            unique.append(n)

    return unique


# -------------------------------------------------------------------
#  Classe principale : Compagne SMS
# -------------------------------------------------------------------


class CompagneSMS(Document):
    """
    Backend du DocType 'Compagne SMS'.

    - Bouton 'générer_liste_des_clients'       -> generer_liste_des_clients()
    - Bouton 'générer_liste_des_distinataires' -> generer_liste_des_distinataires()
    - Bouton 'envoyer_sms'                     -> envoyer_sms()
    """

    # ----------------- HOOKS -----------------

    def validate(self):
        # À chaque sauvegarde, on met à jour les stats sur le message
        self._update_message_stats()

    # ----------------- UTILS -----------------

    def _update_message_stats(self):
        """Calcule total_des_charactères et total_message_s en fonction de message."""
        txt = self.message or ""
        self.total_des_charactères = len(txt)
        self.total_message_s = math.ceil(len(txt) / 160.0) if txt else 0

    def _clear_clients_list(self):
        """Vide la table 'liste_des_clients'."""
        self.set("liste_des_clients", [])

    def _add_sms_client_row(self, customer_name, customer_label, customer_group, phones):
        """
        Ajoute une ligne dans la table 'SMS client'.

          - client       : Link Customer
          - nom_client   : Data
          - group_client : Data
          - liste_tel    : Small Text
        """
        row = self.append("liste_des_clients", {})
        row.client = customer_name
        row.nom_client = customer_label
        row.group_client = customer_group
        row.liste_tel = phones

    # ----------------- GÉNÉRATION LISTE CLIENTS -----------------

    def _build_clients_from_groups(self):
        """
        Construit la table 'liste_des_clients' à partir de 'groupes_des_clients'
        (Doctype enfant 'Group Client SMS', champ: group_client -> Customer Group).

        Ne garde que les clients autorisés.
        Renvoie la liste des clients ignorés pour info.
        """
        if not self.groupes_des_clients:
            frappe.throw(_("Veuillez sélectionner au moins un groupe de clients."))

        self._clear_clients_list()
        clients_non_autorises = []

        for g in self.groupes_des_clients:
            group_name = getattr(g, "group_client", None)
            if not group_name:
                continue

            customers = frappe.get_all(
                "Customer",
                filters={"customer_group": group_name},
                fields=[
                    "name",
                    "customer_name",
                    "customer_group",
                    "custom_liste_telephone",
                    *AUTORISATION_FIELDS,
                ],
            )
            if not customers:
                continue

            for cust in customers:
                cust_name = cust["name"]
                cust_label = cust["customer_name"]
                cust_group = cust["customer_group"]
                phones_raw = cust.get("custom_liste_telephone") or ""

                # Autorisation d'accès fiche client
                if not is_customer_authorized_from_dict(cust):
                    clients_non_autorises.append(cust_label)
                    continue

                # Client autorisé -> on ajoute une ligne
                self._add_sms_client_row(
                    customer_name=cust_name,
                    customer_label=cust_label,
                    customer_group=cust_group,
                    phones=phones_raw,
                )

        return clients_non_autorises

    @frappe.whitelist()
    def generer_liste_des_clients(self):
        """
        Bouton 'Générer liste des clients' :
        - Remplit la table 'SMS client' à partir des groupes de clients.
        - Ignore les clients sans autorisation d'accès fiche client.
        """
        clients_non_autorises = self._build_clients_from_groups()
        self._update_message_stats()

        msg = _("Liste des clients générée : {0} lignes.").format(
            len(self.liste_des_clients or [])
        )
        if clients_non_autorises:
            msg += "<br><br>" + _(
                "⚠ Les clients suivants ont 'Autoriser accès fiche client' à 0 et ont été ignorés :"
            ) + "<br>" + ", ".join(clients_non_autorises)
            frappe.msgprint(msg, title=_("Attention"), indicator="orange")
        else:
            frappe.msgprint(msg)

    # ----------------- GÉNÉRATION LISTE DESTINATAIRES -----------------

    @frappe.whitelist()
    def generer_liste_des_distinataires(self):
        """
        Bouton 'Générer liste des distinatataires' :

        Construit le champ texte 'liste_des_destinataires' à partir de la table 'SMS client'.

        Format de chaque ligne :
            nom_client - group_client - num1, num2, ...
        Utilise traiter_numero_tel pour ne garder que les mobiles tunisiens valides.
        """
        lignes = []
        clients_sans_num = []
        clients_non_autorises = []

        for row in self.liste_des_clients or []:
            nom_client = row.nom_client or row.client or ""
            group_client = row.group_client or ""

            # Vérifier l'autorisation à partir du nom du client
            if row.client and not is_customer_authorized_from_name(row.client):
                clients_non_autorises.append(nom_client)
                continue

            nums_valides = traiter_numero_tel(row.liste_tel)

            if nums_valides:
                # on met à jour liste_tel avec la version normalisée
                row.liste_tel = ", ".join(nums_valides)
                lignes.append(f"{nom_client} - {group_client} - {row.liste_tel}")
            else:
                clients_sans_num.append(nom_client)

        self.liste_des_destinataires = "\n".join(lignes)

        msg = _("Liste des destinataires générée : {0} lignes.").format(len(lignes))
        if clients_sans_num:
            msg += "<br><br>" + _(
                "⚠ Les clients suivants n'ont aucun numéro mobile tunisien valide :"
            ) + "<br>" + ", ".join(clients_sans_num)
        if clients_non_autorises:
            msg += "<br><br>" + _(
                "⚠ Les clients suivants ont 'Autoriser accès fiche client' à 0 et ont été ignorés :"
            ) + "<br>" + ", ".join(clients_non_autorises)

        frappe.msgprint(msg, title=_("Attention"), indicator="orange")

    # ----------------- ENVOI DES SMS -----------------

    @frappe.whitelist()
    def envoyer_sms(self):
        """
        Bouton 'Envoyer SMS'.

        - Seulement si la campagne est en brouillon (docstatus = 0)
        - Parcourt la table 'SMS client'
        - Nettoie les numéros avec traiter_numero_tel()
        - Rend le message comme template Jinja avec variables :

            {{ nom_client }}
            {{ group_client }}
            {{ ristourne_acc }}
            {{ ristourne_uti }}

        - Calcule nombre_total_compagne (total de SMS segments)
        - Si DRY_RUN = False : envoie les SMS et soumet le document
        - Si DRY_RUN = True  : ne fait qu'un rapport (aucun SMS envoyé, pas de submit)
        """
        if self.docstatus != 0:
            frappe.throw(_("Les SMS ont déjà été envoyés ou la campagne n'est plus en brouillon."))

        if not self.message:
            frappe.throw(_("Merci de saisir un message."))

        template = self.message
        total_numbers = 0          # nombre total de numéros appelés
        total_segments = 0         # nombre total de SMS (segments de 160 caractères)
        dry_run_log = []

        for row in self.liste_des_clients or []:
            if not row.liste_tel:
                continue

            # Vérifier autorisation encore une fois
            if row.client and not is_customer_authorized_from_name(row.client):
                continue

            customer_name = row.client
            nom_client = row.nom_client or ""
            group_client = row.group_client or ""

            if customer_name:
                ristourne_acc = get_ristourne_acc(customer_name)
                ristourne_uti = get_ristourne_uti(customer_name)
            else:
                ristourne_acc = 0.0
                ristourne_uti = 0.0

            context = {
                "nom_client": nom_client,
                "group_client": group_client,
                "ristourne_acc": ristourne_acc,
                "ristourne_uti": ristourne_uti,
            }

            # Numéros nets (8 chiffres tunisiens)
            nums_valides = traiter_numero_tel(row.liste_tel)
            if not nums_valides:
                continue

            # 👉 Ajout du prefixe international '216' pour chaque numéro
            nums_to_send = [f"216{n}" for n in nums_valides]

            # Rendu du message avec le contexte
            msg_text = frappe.render_template(template, context)

            # Nombre de segments pour CE message
            segments = max(1, math.ceil(len(msg_text) / 160.0))
            total_segments += segments * len(nums_to_send)
            total_numbers += len(nums_to_send)

            if DRY_RUN:
                dry_run_log.append(
                    f"{nom_client} ({group_client}) -> {', '.join(nums_to_send)} | "
                    f"{len(msg_text)} chars, {segments} SMS"
                )
            else:
                # Envoi réel
                _send_sms_with_fallback(nums_to_send, cstr(msg_text))

        # 👉 On met le total des SMS segments dans le champ nombre_total_compagne
        self.nombre_total_compagne = total_segments

        if DRY_RUN:
            log_text = "<br>".join(dry_run_log) if dry_run_log else _("Aucun destinataire valide.")
            frappe.msgprint(
                _(
                    "[DRY RUN] Aucun SMS envoyé.<br>"
                    "Destinataires simulés : {0} numéros, {1} SMS (segments).<br><br>{2}"
                ).format(total_numbers, total_segments, log_text),
                title=_("Simulation d'envoi de SMS"),
                indicator="blue",
            )
        else:
            frappe.msgprint(
                _(
                    "SMS envoyés à {0} numéros. Estimation du nombre total de SMS (segments) : {1}."
                ).format(total_numbers, total_segments)
            )
            # On soumet la campagne pour la figer (plus de modifications)
            self.submit()
