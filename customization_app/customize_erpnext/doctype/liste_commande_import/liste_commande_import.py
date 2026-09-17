"""Liste Commande Import — préparation d'une demande de cotation fournisseur.

Le controller recalcule les volumes (ligne + total), les montants et les
écarts, puis capitalise le volume unitaire vers la fiche Article
(Item.custom_volume_m3) pour les listes futures.

Quatre montants cohabitent, et c'est voulu — les confondre est la source des
« la somme ne correspond pas à l'Excel » :

  montant_cible   notre cible de départ            : notre qté × notre prix cible
  montant_propose ce que coûterait NOTRE commande  : notre qté × son prix
  montant_fichier ce que SON fichier additionne    : sa qté × son prix
  montant_retenu  ce qu'on retient après arbitrage : qté retenue × prix retenu

`montant_propose` et `montant_fichier` divergent dès que le fournisseur cote
une autre quantité que la nôtre (MOQ, arrondi au carton) — d'où le compteur de
lignes divergentes, qui dit combien de lignes expliquent l'écart.
"""

import json

import frappe
from frappe.model.document import Document
from frappe.utils import flt

from customization_app.lci_observation import (
    maj_ligne,
    prix_retenu,
    qty_retenue,
    total_fichier_ligne,
)


def _nb_conteneurs(doc):
    """Nombre de conteneurs du dernier plan de chargement appliqué."""
    try:
        return len((json.loads(doc.plan_conteneurs or "{}") or {}).get("conteneurs") or [])
    except Exception:
        return 0


def _adds(row):
    """Articles additionnels embarqués sur une ligne pack (champ JSON)."""
    try:
        return json.loads(row.articles_additionnels or "[]") or []
    except Exception:
        return []


class ListeCommandeImport(Document):
    def validate(self):
        # lignes libres : une ligne totalement vide est retirée silencieusement ;
        # une ligne avec du contenu mais sans désignation est auto-nommée.
        kept = []
        for row in self.articles:
            if not row.item_code and not (row.item_name or "").strip():
                has_content = ((row.description or "").strip()
                               or (row.image or "").strip())
                if not has_content:
                    continue  # ligne placeholder vide -> on la retire
                row.item_name = f"Article libre {len(kept) + 1}"
            kept.append(row)
        if len(kept) != len(self.articles):
            self.articles = kept
            for i, row in enumerate(self.articles, 1):
                row.idx = i

        total = 0.0
        montant_cible = 0.0
        montant_propose = 0.0
        montant_fichier = 0.0
        montant_retenu = 0.0
        cotees = 0
        divergentes = 0
        langue = self.langue_cible or "Français"
        devise = self.devise or "USD"
        for row in self.articles:
            # le fournisseur cote au carton : le volume unitaire s'en déduit et
            # prime sur la valeur saisie à la main (source la plus fiable).
            if flt(row.qty_par_carton) > 0 and flt(row.volume_carton_m3) > 0:
                row.volume_unitaire_m3 = flt(row.volume_carton_m3) / flt(row.qty_par_carton)

            row.total_fournisseur = flt(row.qty) * flt(row.prix_fournisseur)
            row.ecart_pct = (
                (flt(row.prix_fournisseur) - flt(row.prix_cible)) / flt(row.prix_cible) * 100
                if flt(row.prix_cible) > 0 and flt(row.prix_fournisseur) > 0
                else 0
            )
            montant_cible += flt(row.qty) * flt(row.prix_cible)
            montant_propose += flt(row.total_fournisseur)
            if flt(row.prix_fournisseur) > 0:
                cotees += 1
            if flt(row.qty_fournisseur) > 0 and flt(row.qty) > 0 \
                    and abs(flt(row.qty_fournisseur) - flt(row.qty)) > 0.001:
                divergentes += 1
            montant_fichier += total_fichier_ligne(row)

            # la quantité retenue est celle qui part réellement : c'est donc
            # elle qui compte le volume et remplit les conteneurs.
            q_ret = qty_retenue(row)
            abandon = (row.decision or "") == "Abandonné"
            row.total_retenu = 0.0 if abandon else q_ret * prix_retenu(row)
            montant_retenu += flt(row.total_retenu)
            maj_ligne(row, langue, devise)

            vol = q_ret * flt(row.volume_unitaire_m3)
            for a in _adds(row):  # volume des additionnels embarqués
                vol += q_ret * flt(a.get("qty_par_pack")) * flt(a.get("volume_unitaire_m3"))
            row.volume_ligne_m3 = vol
            total += row.volume_ligne_m3
            # capitaliser le volume unitaire vers la fiche Article — jamais une
            # estimation IA : une supposition ne doit pas devenir la référence
            # de l'article pour toutes les listes suivantes.
            if row.item_code and flt(row.volume_unitaire_m3) > 0 and not row.volume_estime:
                current = flt(frappe.db.get_value("Item", row.item_code, "custom_volume_m3"))
                if abs(current - flt(row.volume_unitaire_m3)) > 0.00005:
                    frappe.db.set_value("Item", row.item_code, "custom_volume_m3",
                                        flt(row.volume_unitaire_m3), update_modified=False)
        self.volume_total_m3 = total
        self.nb_articles = len(self.articles)
        self.montant_cible = montant_cible
        self.montant_propose = montant_propose
        self.montant_retenu = montant_retenu
        self.nb_lignes_cotees = cotees
        self.nb_lignes_divergentes = divergentes
        # le montant du fichier reste celui lu à l'import tant qu'aucune ligne
        # ne porte de trace de sa cotation (sinon on effacerait son addition).
        if montant_fichier or not flt(self.montant_fichier):
            self.montant_fichier = montant_fichier
        self.nb_conteneurs = _nb_conteneurs(self)
        # l'écart global ne compare que ce qui est comparable : les lignes qui
        # ont À LA FOIS un prix cible et un prix fournisseur.
        base = sum(flt(r.qty) * flt(r.prix_cible) for r in self.articles
                   if flt(r.prix_cible) > 0 and flt(r.prix_fournisseur) > 0)
        cote = sum(flt(r.total_fournisseur) for r in self.articles
                   if flt(r.prix_cible) > 0 and flt(r.prix_fournisseur) > 0)
        self.ecart_global_pct = (cote - base) / base * 100 if base else 0
