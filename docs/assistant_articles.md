# Assistant Articles — second plugin ChatGPT (MCP), séparé

Plugin ChatGPT dédié à la **base article** : chercher, lire, créer, modifier, tarifer, illustrer, réorganiser les
groupes, gérer les ensembles de produits. Il est **séparé** de l'assistant ChatGPT (dépenses, versements,
rendez-vous — `docs/assistant_chatgpt.md`) : autre URL, autre utilisateur technique, autre clé, autre jeu d'outils.
On branche l'un sans l'autre, on révoque l'un sans toucher l'autre.

| | Assistant ChatGPT | Assistant Articles |
|---|---|---|
| Point d'entrée | `customization_app.mcp_chatgpt.serveur` | `customization_app.mcp_articles.serveur` |
| Outils | `assistant_chatgpt.py` | `assistant_articles.py` |
| Utilisateur technique | `assistant.chatgpt@aquaworld.local` | `articles.chatgpt@aquaworld.local` |
| Rôle (sans permission) | Assistant ChatGPT | Assistant Articles |
| Patch | `ensure_assistant_chatgpt` | `ensure_assistant_articles` |
| Nom du serveur MCP | `aqua-world-erp` | `aqua-world-articles` |

Seule la mécanique du protocole est commune (`mcp_chatgpt.traiter` / `servir`, JSON-RPC 2.0 : `initialize`,
`tools/list`, `tools/call`, `ping`, Streamable HTTP sans flux SSE).

## Brancher (une fois, en prod)

1. `bench --site <site> migrate` (le patch crée le rôle et l'utilisateur), ou
   `bench --site <site> execute customization_app.patches.ensure_assistant_articles.execute`.
2. Ouvrir l'utilisateur **articles.chatgpt@aquaworld.local** → « API Access » → **Générer les clés**. Le secret
   ne s'affiche qu'une fois : le copier.
3. Dans ChatGPT : **Paramètres → Sécurité et connexion → Mode développeur**, puis **« + » → Créer** une application
   MCP distante :
   - URL : `https://aquaworldservicing.opssync.pro/api/method/customization_app.mcp_articles.serveur?cle=<clé>:<secret>`
   - Authentification : **aucune** (le secret est dans l'URL) ; cocher « Je fais confiance à cette application ».
4. Dans une conversation, activer le plugin « aqua-world-articles ». Les consignes sont intégrées au serveur
   (`INSTRUCTIONS` du module).

Pour révoquer : régénérer ou effacer les clés de l'utilisateur (l'autre plugin n'est pas touché).

## Ce que le plugin sait faire

| Outil | Rôle |
|---|---|
| `moi`, `references` | repères (listes de prix, taux de TVA 7 / 19, unités en usage, marques, entrepôts) |
| `groupes_articles` | les groupes **feuilles** avec leur chemin et leur nombre d'articles |
| `entrepots` | entrepôts actifs, nombre d'articles et quantité dans chacun |
| `chercher_article` | mots (code, nom, description, marque, ordre libre) ou code-barres, **et/ou** filtres : groupe, entrepôt (avec la quantité qui s'y trouve), en rupture, sans image, désactivés compris |
| `fiche_article` | fiche complète : TVA, prix et **marge** par liste, stock par entrepôt, image, codes-barres, zones magasin, descriptions web, variantes ; pour un ensemble : composants, stock au magasin, coût des composants, exemplaires assemblables |
| `articles_a_completer` | ménage : `sans_image`, `sans_prix`, `sans_prix_achat`, `sans_description`, `sans_tva`, `sans_marque`, `en_rupture`, `en_rupture_sans_image` (avec le total) |
| `mes_modifications` | les derniers articles créés ou modifiés par l'assistant |
| `creer_article` | article **simple** : code définitif, nom, groupe feuille, unité, TVA, prix par liste, code-barres, volume ; sortie principale au magasin ; pas de synchro site web |
| `modifier_article` | seulement les champs fournis : nom, description, groupe, marque, TVA, code-barres, volume, descriptions web, désactiver (**motif obligatoire**) / réactiver, rupture site web, synchro site web (**image obligatoire**) |
| `definir_prix` | `{"Vente standard": 750, "Compte Pro": 600, "Achat standard": 126.5}` — vente TTC, achat HT, marges renvoyées |
| `image_article` | image principale depuis une URL **publique** (jpg, png, webp, 8 Mo au plus ; adresses locales refusées) |
| `creer_groupe` | groupe feuille sous un groupe parent (jamais sous un groupe qui porte des articles) |
| `deplacer_articles` | range 1 à 100 codes dans un groupe feuille, fiche par fiche : la réponse dit lesquels ont bougé ou été refusés |
| `creer_ensemble`, `modifier_ensemble` | Product Bundle sur un parent existant non stocké ou créé ici ; composants `{"CODE": qté}` ; ajouter / retirer / remplacer, activer / désactiver |
| `attributs_articles` | attributs de variantes et leurs valeurs (avec l'abrégé qui compose le code) |
| `creer_modele` | article modèle (parent de variantes) avec ses attributs |
| `rendre_modele` | un article normal devient modèle, **seulement s'il n'a jamais eu de mouvement de stock** (règle ERPNext) ; sinon la réponse explique quoi faire |
| `creer_variante` | variante d'un modèle : `{"Couleur": "Noir", "Taille": "Grand"}`, code composé par ERPNext (modèle + abrégés) ou imposé, **nom et description composés par le Server Script du site** (« nom du modèle, valeur 1, valeur 2 »), prix propres ; `stock_suivi` force la nature ; `composants` `{"AP-C": 1, "M-75-DF": 1, …}` fait de la variante un article **non stocké** qui est un **ensemble** (modèle lui-même compris) |
| `champs_article` | tous les champs simples de la fiche (88) : nom technique, libellé, type, valeurs possibles, modifiable ou non et par quel outil sinon |
| `lire_champs` | valeurs des champs simples d'un article (tous les renseignés, ou ceux demandés) |
| `modifier_champs` | `{"champ technique": valeur}` sur n'importe quel champ simple modifiable (55), valeurs contrôlées selon le type (nombre, liste, lien existant, date) |

**Montage « osmoseurs domestiques »** (AP-C, AP-M, SP-P…) : le modèle est suivi en stock (c'est lui qui est acheté
et compté, 50 AP-C en stock), chaque variante est un article non stocké qui est un ensemble : le modèle ×1 + la
membrane + les accessoires de la configuration. Reproduire : `creer_modele` (`stock_suivi=1`), puis `creer_variante`
avec `composants` pour chaque combinaison ; sur un modèle existant, `creer_variante` avec `composants` suffit.
Recette dev du 05/11/2026 : variante AP-C-F-6-U-AM créée en une fois avec ses 6 composants, coût 186,555 et marge
rapportés, puis annulée.

Champs **protégés** par l'accès générique (ils ont leur propre outil ou sont figés) : code, série de nommage, nature
de stock, unité de stock, méthode de valorisation, numéros de série et lots, immobilisations, groupe, image,
désactivation, synchro site web, modèle / variante. Les tables (taxes, codes-barres, sorties, zones) passent par
`creer_article`, `modifier_article` et `fiche_article`.

**Jamais** : supprimer un article ou un groupe, renommer un code, écriture de stock, changer les attributs d'une
variante existante.

## Garde-fous

- Rôle sans aucune permission de doctype : l'API REST ordinaire répond 403 (lecture d'une fiche, création,
  clients, factures) ; seules les fonctions du module, gardées par `frappe.only_for`, répondent. La clé du plugin
  articles est refusée (401) sur le plugin assistant, et réciproquement.
- Contrôles avant écriture : code valide et libre, groupe feuille, unité et marque connues, TVA 7 ou 19, listes de
  prix connues, prix non négatifs ; un composant d'ensemble n'est jamais lui-même un ensemble ; le parent d'un
  ensemble n'est pas suivi en stock ; une variante a une valeur valide pour chaque attribut du modèle et sa
  combinaison n'existe pas déjà.
- Chaque écriture est faite **sous** l'utilisateur technique (historique Version) et laisse un commentaire
  « … via ChatGPT (articles) » sur la fiche.
- Un article synchronisé avec WooCommerce voit nom et descriptions repartir vers le site à la synchro de nuit : la
  réponse de `modifier_article` le signale.

## Tests

Règles pures et dialogue MCP, sans site :

```
cd apps/customization_app && ../../env/bin/python -m unittest customization_app.tests.test_assistant_articles customization_app.tests.test_mcp_chatgpt
```

Recette sur le dev faite le 05/11/2026 (lecture, création, modification, prix, image, groupes, lot, ensembles,
modèles et variantes, champs génériques, point d'entrée HTTP), écritures annulées par rollback.
