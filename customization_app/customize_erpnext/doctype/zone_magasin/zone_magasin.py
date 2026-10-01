"""Zones de rangement du magasin, sur deux niveaux.

- un ESPACE (Magasin, Hall…) : `est_espace = 1`, nom = son code (« Hall ») ;
- une ZONE dans un espace : nom complet « Espace - Code » (« Magasin - A1 », « Hall - K3 »).
Le même code existe ainsi dans deux espaces sans confusion. Un article peut être rangé dans
plusieurs zones (ou directement dans un espace) : table `custom_zones_magasin` de l'article, dont
« Emplacement Magasin » (texte) affiche les noms complets joints par « / ».
Voir customization_app.zones_magasin et la page Stock « Zones & sorties d’articles ».
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document

SEPARATEUR = " / "
LIAISON = " - "


def normaliser_code(texte: str) -> str:
    """Espaces réguliers ; un code de rayon « lettre + chiffre » s'écrit « A1 » (« A 1 », « a-1 », « A-1 »)."""
    texte = re.sub(r"\s+", " ", str(texte or "")).strip()
    m = re.fullmatch(r"([A-Za-z])\s*-?\s*(\d{1,2})", texte)
    return "%s%s" % (m.group(1).upper(), m.group(2)) if m else texte


def nom_complet(espace: str | None, code: str) -> str:
    return "%s%s%s" % (espace, LIAISON, code) if espace else code


def decouper_codes(texte: str) -> list[str]:
    """« A1, A2, A3 » ou un code par ligne -> codes normalisés, sans doublon (ordre gardé)."""
    vues, out = set(), []
    for morceau in re.split(r"[,;\n]+", str(texte or "")):
        c = normaliser_code(morceau)
        if c and c.lower() not in vues:
            vues.add(c.lower())
            out.append(c)
    return out


class ZoneMagasin(Document):
    def _calculer(self):
        self.code = normaliser_code(self.code)
        if self.est_espace:
            self.espace = None
        self.libelle = nom_complet(self.espace, self.code)

    def autoname(self):
        self._calculer()
        self.name = self.libelle

    def validate(self):
        self._calculer()
        if not self.code:
            frappe.throw(_("Donnez un code à la zone."))
        if "/" in self.code:
            frappe.throw(_("Une zone est un seul endroit : pas de « / » dans son code ({0}).").format(self.code))
        if not self.est_espace:
            if not self.espace:
                frappe.throw(_("Choisissez l'espace de la zone (Magasin, Hall…)."))
            if not frappe.db.get_value("Zone Magasin", self.espace, "est_espace"):
                frappe.throw(_("« {0} » n'est pas un espace.").format(self.espace))
        if self.is_new():
            existe = frappe.db.sql("select name from `tabZone Magasin` where lower(name) = lower(%s)", self.libelle)
            if existe:
                frappe.throw(_("« {0} » existe déjà.").format(existe[0][0]))
