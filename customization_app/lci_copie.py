"""Copier des lignes d'une Liste Commande Import (demande utilisateur 25/09/2026 : « copier une ligne
entière, ou en cocher plusieurs et les copier ») : en double dans la même liste, ou vers une autre
liste (brouillon existant ou nouvelle). Ce qui se copie = ce que NOUS avons saisi ; la réponse du
fournisseur (prix, quantités cotées, décision, remarques, observations, conteneurs) reste sur la
ligne d'origine. Pur : miroir de LCI_CHAMPS_COPIES dans liste_commande_import.js.
"""

from __future__ import annotations

CHAMPS_COPIES = (
	"item_code", "item_name", "item_group", "qty", "uom", "volume_unitaire_m3", "image", "description",
	"item_name_traduit", "description_traduite", "articles_additionnels", "prix_cible", "moq",
	"qty_par_carton", "volume_carton_m3", "volume_estime",
)


def ligne_copiee(ligne: dict) -> dict:
	"""La copie d'une ligne : ses champs saisis seulement, jamais la réponse du fournisseur ni
	les identifiants (name, idx, parent)."""
	lire = ligne.get if isinstance(ligne, dict) else (lambda k, d=None: getattr(ligne, k, d))
	out = {}
	for champ in CHAMPS_COPIES:
		v = lire(champ)
		if v not in (None, ""):
			out[champ] = v
	return out


def lignes_dans_l_ordre(lignes: list, noms: list) -> list:
	"""Les lignes dont le `name` est dans `noms`, dans l'ordre de la liste (pas celui des clics)."""
	voulus = set(noms or [])
	lire = lambda l, k: l.get(k) if isinstance(l, dict) else getattr(l, k, None)
	return [l for l in lignes if lire(l, "name") in voulus]
