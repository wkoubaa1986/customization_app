# Copyright (c) 2026, Wassim koubaa and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ConfigPortailRDV(Document):
    """Réglages du portail /rdv. La logique vit dans
    customization_app/portail_rdv.py ; ce single ne porte que les réglages —
    et corrige les noms de gouvernorats saisis à la main."""

    def validate(self):
        self._corriger_gouvernorats()
        self._secteurs_voisins()

    def _secteurs_voisins(self):
        """Le seuil ou les ajustements ont pu changer : voisins recalculés (un appel OSRM) et affichés tels que le
        portail les applique. Un nom de secteur inconnu dans les ajustements est SIGNALÉ (il serait ignoré en silence)."""
        from customization_app.portail_rdv_planning import libeller_voisins, paires_secteurs, rafraichir_voisins

        connus = set(frappe.get_all("Territory", pluck="name")) if frappe.db.exists("DocType", "Territory") else set()
        noms = {n for champ in ("voisins_ajouts", "voisins_retraits") for p in paires_secteurs(self.get(champ)) for n in p}
        inconnus = sorted(n for n in noms if not n.startswith("Secteur ") and n not in connus)
        if inconnus:
            frappe.msgprint(_("Secteur(s) non reconnu(s) dans les voisins : <b>{0}</b> — écrivez « Secteur 7, Secteur 3 » "
                              "ou « 7, 3 ».").format(", ".join(inconnus)), title=_("Secteurs voisins"), indicator="orange")

        try:
            self.voisins_calcules = libeller_voisins(rafraichir_voisins(self.as_dict()))
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Portail RDV : secteurs voisins")

    def _corriger_gouvernorats(self):
        """« Monastire », « le kef », « Gabes » -> le nom officiel.

        Un gouvernorat mal orthographié ne lève aucune erreur à l'usage : il ne
        correspond simplement à aucune adresse, et la zone reste silencieusement
        fermée. On corrige donc à la saisie, et on DIT ce qui a été corrigé —
        un nom introuvable est signalé plutôt qu'accepté en silence.
        """
        from customization_app.sectorisation import gouvernorat_proche

        corrections, inconnus = [], []
        for ligne in (self.get("partenaires") or []):
            propres = []
            for brut in (ligne.gouvernorats or "").split(","):
                brut = brut.strip()
                if not brut:
                    continue
                officiel = gouvernorat_proche(brut)
                if not officiel:
                    inconnus.append(brut)
                    propres.append(brut)          # on ne jette pas la saisie
                    continue
                if officiel != brut:
                    corrections.append("%s → %s" % (brut, officiel))
                if officiel not in propres:
                    propres.append(officiel)
            ligne.gouvernorats = ", ".join(propres)

        if corrections:
            frappe.msgprint(
                _("Gouvernorats corrigés : {0}").format(", ".join(corrections)),
                indicator="blue", alert=True)
        if inconnus:
            frappe.msgprint(
                _("Gouvernorat(s) introuvable(s) : <b>{0}</b>.<br>"
                  "Ces zones resteront fermées à la réservation en ligne. "
                  "Vérifiez l'orthographe — elle doit correspondre à la liste "
                  "du formulaire d'adresse.").format(", ".join(inconnus)),
                title=_("Zone partenaire"), indicator="orange")
