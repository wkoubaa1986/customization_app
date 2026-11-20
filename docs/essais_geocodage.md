# Comparateur de géocodage — DEV

Installé le 20 novembre 2026 dans **Config Optimisation Tournees**, bouton
**Comparer les géocodeurs**. Ce comparateur ne change ni les adresses, ni le
planning, ni le choix de fournisseur de l'optimiseur existant.

État actuel vérifié : les quatre services répondent. Google utilise désormais
la clé dédiée `google_geocoding_cle` et renvoie des résultats sur les 12 adresses
de l'échantillon. Les résultats restent à contrôler avant un usage opérationnel.

## Configurer les accès

- **Geoapify** : créer un projet gratuit dans <https://myprojects.geoapify.com/>,
  puis saisir sa clé dans **Clé Geoapify — essais**.
- **LocationIQ** : obtenir le jeton dans <https://my.locationiq.com/>, puis le
  saisir dans **Clé LocationIQ — essais**.
- **Google** : activer **Geocoding API** dans le projet qui possède la clé utilisée,
  puis autoriser cette API dans les restrictions de cette clé. Une clé dédiée
  peut être saisie dans **Clé Google Geocoding — essais** ; sinon la clé Routes
  existante est utilisée. Le dernier essai réel avec la clé dédiée a renvoyé `OK`.
- **Nominatim public** : accès sans clé, essais ponctuels uniquement. Pas
  d'autocomplétion ni de traitement périodique. Consulter ses
  [conditions d'utilisation](https://operations.osmfoundation.org/policies/nominatim/).

Les clés sont des champs Password Frappe chiffrés ; elles ne sont jamais
renvoyées par le comparateur au navigateur. Aucun abonnement n'a été souscrit.

## Essayer

1. Enregistrer les clés et les réglages.
2. Ouvrir **Comparer les géocodeurs**.
3. Choisir les fournisseurs et consulter le texte exact des adresses affichées.
4. Lancer la comparaison. Le traitement se fait dans la file `long` avec suivi
   de progression. Les résultats restent disponibles 24 heures avec **Dernier essai**.

L'échantillon comprend par défaut 12 adresses récentes accessibles au demandeur,
dont jusqu'à 8 avec une position issue d'un lien. Maximum 50 adresses par essai.
Le serveur relit les adresses et les permissions au lancement, puis dans le
traitement. Seuls les champs adresse postale sont envoyés, sans joindre les
champs client, téléphone ou position de référence. Aucun remplacement par un
centre de ville n'est fait si le géocodeur ne trouve rien.

Le premier résultat est comparé à la position de référence ; deux autres
candidats peuvent être affichés. Une référence issue d'un lien peut elle-même
être erronée. L'écart médian et les nombres d'écarts ≤ 200 m ou > 1 km sont des
indicateurs de comparaison, pas un classement certifié. Les indices de confiance
propres aux fournisseurs ne sont pas directement comparables.

Protection des essais : maximum 200 tentatives réseau par jour, compteur Redis
atomique propre au site ; ne couvre pas les autres usages des comptes et est
réinitialisable par une suppression du cache. Les quotas fournisseurs restent
la protection à utiliser pour plafonner la facturation. Les résultats sont mis
en cache 24 heures, y compris les adresses introuvables. Une modification de clé
invalide ce cache. Un service refusé est arrêté pour le reste du lot. Les appels
du comparateur sont sérialisés et espacés d'au moins 1,1 seconde.

Les limites ont été portées de 30 à 50 adresses et de 100 à 200 appels à la
demande d'une comparaison de 50 nouvelles adresses avec les quatre services.
Les quotas fournisseurs et les autres protections restent appliqués.

## Vérifications réalisées

- 18 tests unitaires sans base ni réseau : permissions, erreurs et confidentialité
  des clés, quota, cache, réponses ambiguës, statistiques, absence de sauvegarde
  métier et arrêt d'un service indisponible.
- Test Chromium de l'interface complète avec échantillon et résultats simulés :
  ouverture, lancement, tableau de comparaison, aucune erreur JavaScript.
- Trois recherches réelles Nominatim sur des lieux publics : avenue Habib
  Bourguiba, place de la Kasbah et musée national du Bardo à Tunis. Résultats
  reçus ; cela ne mesure pas sa précision sur les adresses clients.
- Après accord explicite, essai Nominatim sur les 12 textes exacts de
  l'échantillon préparé : 2 résultats, 10 adresses introuvables, aucune erreur
  de service. Les deux résultats se situent à 477 m et 653 m des positions
  existantes issues de liens (écart médian 565 m). Ces références ne sont pas
  une vérité terrain certifiée. Ce petit essai porte sur les textes tels quels,
  sans nettoyage supplémentaire ; il ne mesure pas tous les usages du service.
  Les empreintes des 12 documents Address sont identiques avant et après.
  Rapport local :
  `/workspace/development/exports/essai-geocodage-2026-11-20/comparaison-nominatim.csv`.
- Comparaison relancée après enregistrement des clés, sur les 12 textes
  inchangés, via la file de traitement réelle :
  - Geoapify : 10 résultats sur 12 ; 7 comparables aux références, écart médian
    1 811 m, dont 4 au-delà de 1 km (maximum 12 755 m).
  - LocationIQ : 7 résultats sur 12 ; 5 comparables aux références, écart médian
    813 m, dont 1 au-delà de 1 km.
  - Nominatim : les 12 réponses précédentes ont été reprises du cache ;
    2 résultats, tous deux comparables, écart médian 565 m.
  - Google Geocoding : toujours refusé pour API non activée. Une seule requête
    a été tentée ; le fournisseur a ensuite été arrêté pour le reste du lot.
  Lors de ce premier comparatif, aucun premier résultat comparable n'était à
  200 m ou moins de sa référence.
  Les médianes portent sur des sous-ensembles différents : elles ne permettent
  pas à elles seules de classer les fournisseurs. Le lot a consommé 25 appels
  réseau ; les empreintes des 12 documents Address sont restées identiques.
  Rapport local détaillé :
  `/workspace/development/exports/essai-geocodage-2026-11-20/comparaison-quatre-services.csv`.
- Vérification Google intermédiaire, après configuration dans la console :
  `REQUEST_DENIED`, avec le message « This API is not activated on your API
  project. » Le champ `google_geocoding_cle` était alors vide dans le site DEV ;
  l'application utilisait donc la clé `google_routes_cle` existante. Si une
  nouvelle clé a été configurée dans Google Cloud, elle doit être enregistrée
  dans **Clé Google Geocoding — essais**. Sinon, l'activation doit concerner le
  projet de la clé Routes déjà utilisée. Une requête Google dans le lot et une
  vérification sur un lieu public ont été effectuées ; les 36 autres réponses
  du lot provenaient du cache. Aucun document Address n'a été modifié.
  Rapport :
  `/workspace/development/exports/essai-geocodage-2026-11-20/comparaison-google-activation.csv`.
- Dernière vérification après enregistrement de la clé dédiée Google :
  un appel réel sur un lieu public renvoie `OK`, puis les 12 recherches
  autorisées réussissent sans erreur de service. Sur les 8 positions de
  référence disponibles : écart médian 215 m, 4 résultats à 200 m ou moins,
  3 au-delà de 1 km. Google signale 10 correspondances partielles sur 12.
  Les 12 textes et les 8 positions de référence sont identiques à ceux des
  essais précédents. Les résultats des trois autres services ont été repris
  du rapport précédent, sans nouvel appel réseau. Aucun document Address n'a
  été modifié. Rapport consolidé :
  `/workspace/development/exports/essai-geocodage-2026-11-20/comparaison-finale-geocodage.csv`.
- Essai élargi demandé par l'utilisateur : 50 autres adresses, tirées au sort
  avec graine enregistrée parmi 1 996 textes distincts disposant d'une position
  issue d'un lien. Les 12 adresses et textes du premier essai sont exclus.
  Les 50 adresses couvrent 8 secteurs ; leurs 50 positions de référence servent
  uniquement à l'évaluation et ne sont pas transmises aux fournisseurs.
  Les 200 recherches réelles sont terminées sans erreur de service ni cache.
  Les textes, références et empreintes des 50 documents Address sont inchangés.

  | Service | Résultats sur 50 | À 200 m ou moins | Écart médian des résultats trouvés | Au-delà de 1 km |
  | --- | ---: | ---: | ---: | ---: |
  | Google | 50 | 14 | 662 m | 20 |
  | Geoapify | 46 | 8 | 1 642 m | 30 |
  | LocationIQ | 32 | 4 | 1 656 m | 24 |
  | Nominatim | 8 | 4 | 169 m | 2 |

  Les médianes ci-dessus portent sur des nombres de résultats différents.
  Sur les 8 adresses trouvées par les quatre services, les écarts médians sont
  de 684 m pour Google, 412 m pour Geoapify, 168 m pour LocationIQ et 169 m pour
  Nominatim. Sur les 46 adresses communes à Google et Geoapify, Google est plus
  proche de la référence dans 28 cas, Geoapify dans 17 cas, avec une égalité.
  Google signale 39 correspondances partielles sur 50. Sa couverture et le
  nombre de résultats proches sont supérieurs sur ce lot, mais aucun service
  ne garantit une position exacte. Les références enregistrées ne sont pas
  certifiées et le sous-ensemble étudié possède déjà des liens de position.
  Rapport détaillé et synthèse :
  `/workspace/development/exports/essai-geocodage-50-2026-11-20/comparaison-50-adresses.csv`
  et `synthese-50-adresses.csv` dans le même dossier. Le manifeste conserve les
  critères de sélection, la graine, les textes exacts et les empreintes.

Le contrôle automatique d'approbation avait refusé l'envoi de 12 adresses
internes sans accord explicite sur les données et les destinations. L'utilisateur
a ensuite répondu : « Oui, j'autorise l'envoi de ces 12 adresses aux quatre
services ». L'essai Nominatim a alors été exécuté, puis la comparaison complète
a été relancée à sa demande après enregistrement des clés Geoapify et LocationIQ
dans le site DEV. Le test Google a ensuite réussi avec une clé dédiée
enregistrée dans les réglages.
L'échantillon exact est conservé dans
`/workspace/development/exports/essai-geocodage-2026-11-20/echantillon-a-approuver.csv`.
Le manifeste voisin garde les identifiants et empreintes des textes pour
revérifier le périmètre avant les autres essais autorisés ; son statut initial
décrit la préparation ; `essai-nominatim.json` et `essai-comparatif.json`
consignent l'accord et les lancements respectifs.

Commande des tests :

```sh
PYTHONDONTWRITEBYTECODE=1 ../../env/bin/python -B -m unittest customization_app.tests.test_geocodage_essais -v
```

## Localisation des tournées : amélioration installée sur DEV

Le moteur opérationnel est dans `customization_app/geocodage_adresses.py`.
Il utilise Google et, seulement si une URL privée est renseignée, Nominatim.
`geocodage_liens.py` lit aussi les liens de lieux dont l'URL ne contient pas
directement de latitude/longitude : identifiant explicite ou paire hexadécimale
du lien développé. La conversion de cette paire repose sur un format observé,
non garanti par Google : la réponse de Geocoding API doit renvoyer exactement
le même identifiant, sans correspondance partielle, pour un établissement,
bâtiment ou point d'intérêt. Un quartier, une ville, une réponse ambiguë ou un
autre identifiant ne sont jamais acceptés comme substitut.
Les coordonnées de caméra `@lat,lng` ne remplacent pas un identifiant de lieu.
Les redirections restent limitées aux domaines Maps/consentement Google
autorisés ; aucun HTML de Google Maps n'est collecté.
Les appels par identifiant partagent le plafond de 200 et le verrou Google.
La source **lien google lieu**, l'expiration et l'affichage Google Maps sont
conservés, y compris après confirmation manuelle. Un ancien échec du lecteur
de liens ne condamne plus le lien pendant six heures.
Geoapify et LocationIQ restent disponibles dans le comparateur d'essais.
L'utilisateur n'ayant pas d'instance privée, Nominatim est actuellement absent
du moteur opérationnel. Le serveur public n'est jamais appelé par celui-ci.

L'activation `geocodage_texte_actif` est **activée sur DEV**, après l'accord
explicite de l'utilisateur du 20 novembre 2026 pour les adresses nécessaires
aux tournées, la préparation des 14 prochains jours et le plafond partagé de
200 appels par jour. Le contrôle automatique avait refusé l'activation par
défaut ; cet accord a ensuite permis l'activation du seul site DEV.
Les clés existantes sont inchangées, Nominatim reste non configuré. Un appel
réel du nouveau moteur sur le musée du Bardo (lieu public) a renvoyé une
proposition Google marquée « À vérifier », sans écriture de fiche.
Le planificateur global DEV est inactif : les recherches à la demande sont
disponibles, mais la préparation quotidienne ne s'exécute pas sur ce site.

Après activation, la recherche concerne les tâches demandées dans le calendrier
et les tâches ouvertes des 14 prochains jours lors du traitement quotidien.
La préparation quotidienne ne parcourt plus toute la base clients. Un traitement
manuel borné reste accessible dans les réglages. Le plafond de 200 appels réseau
par jour est partagé avec le comparateur ; les comptes Google peuvent avoir
d'autres usages et leurs propres limites de facturation.

Règles de décision :

- Une position GPS confirmée ou un lien contenant déjà des coordonnées passe
  avant une recherche du texte. Les positions historiques approximatives ne sont
  plus considérées comme fiables lorsque le nouveau contrôle est activé.
- Aucune moyenne de coordonnées. Un bâtiment n'est accepté automatiquement que
  si numéro, rue et ville concordent, sans correspondance partielle. Un désaccord
  de plus de 200 mètres, plusieurs bâtiments possibles, une rue seule ou une
  adresse sans numéro restent « À vérifier » sur la fiche. Le calcul automatique
  peut néanmoins utiliser le meilleur candidat comme estimation, sans prétendre
  qu’il s’agit d’un GPS confirmé. « Fiable » est une règle prudente, pas une garantie
  de précision géographique.
- Les deux fournisseurs peuvent répondre simultanément lorsqu'un serveur privé
  existe. Les appels d'un même fournisseur sont sérialisés dans le site ; Redis
  et MariaDB restent sur le thread principal. Les réponses sont réutilisées
  pendant 24 heures et une panne suspend les tentatives pendant cinq minutes.
- Pour les tournées, une adresse ambiguë utilise automatiquement la meilleure
  position disponible : correspondance de ville et de rue, précision du résultat,
  ordre du fournisseur et cohérence avec le secteur. Les points hors de Tunisie,
  périmés ou à plus de 30 km du centre d’un secteur connu sont écartés. À défaut,
  le centre du secteur ou une position approximative du téléphone peut servir.
  Aucune moyenne de candidats n’est calculée.
- Les estimations mémorisées sont réutilisées sans nouvel appel. Un ancien échec
  ou une ancienne ambiguïté sans candidat disponible peut être recherché à nouveau
  automatiquement, dans les limites du cache et des quotas. Une modification du
  texte invalide l’ancienne position. Une estimation n’écrase jamais un GPS confirmé
  ou une adresse modifiée pendant la recherche.
- Les positions Google ont une expiration calculée depuis leur acquisition,
  conservée lors d'une confirmation depuis le cache. Une position expirée ne
  peut plus servir au calcul ; la purge quotidienne retire les coordonnées.
  Les parcours issus de ces positions s'ouvrent sur Google Maps.

Dans une fiche **Adresse → Localisation**, **Vérifier la position** présente les
propositions, leur précision et un lien vers la carte. **Confirmer après
vérification** conserve le candidat choisi. **Renseigner un point GPS** permet
d'enregistrer une position de première main, par coordonnées ou lien partagé.
L'ouverture de la fiche ne déclenche aucune recherche externe.

Dans **Optimiser la journée**, **Calculer** enchaîne automatiquement la localisation
et l’optimisation. Il n’y a plus d’étape obligatoire de confirmation des positions.
Le résultat signale les interventions utilisant une position estimée ; les temps
et distances correspondants restent approximatifs. Les positions Google estimées
sont enregistrées avec la source `texte google estimé`, le statut `À vérifier`,
sans utilisateur de validation, et avec la même expiration de 30 jours. La fiche
Adresse conserve les outils de correction et de confirmation, facultatifs.

Le bouton **Appliquer** apparaît seulement pour une proposition avec des
changements. Une modification de la date, des employés, des horaires ou des
paramètres retire cette proposition. Les réponses tardives d'un ancien calcul
ou d'une ancienne date sont ignorées. Le corps de la fenêtre défile en conservant
les boutons accessibles, y compris sur petit écran.

Si aucune position n’existe malgré ces replis, toute la tournée de l’employé
concerné reste au planning actuel et apparaît séparément dans le résultat : on
ne lui ajoute pas de tâches en supposant un déplacement fictif. Les autres
employés restent optimisables. Le serveur accepte une estimation encore valable
à l’application et refuse un point devenu indisponible. Les contraintes « Heure
fixe » et « Employé fixe », la redistribution et le recalage du trafic Google
sont conservés.

Validation : tests sans réseau de sélection, appels concurrents, cache, quota,
protection des confirmations, estimations automatiques et application ; tests MariaDB
avec adresses synthétiques et rollback ; régression du solveur et du trafic.
Deux tests historiques d'itinéraire dépendent d'un employé absent de ce DEV et
sont ignorés. Test Chromium de la fiche Adresse avec réponses et écritures
simulées, contrôle de l'échappement HTML et vérification du document inchangé.
Les API réelles ont été validées lors des comparaisons précédentes ; cette
amélioration n'a transmis aucune nouvelle adresse client pendant ses tests.

```sh
PYTHONDONTWRITEBYTECODE=1 ../../env/bin/python -B -m unittest customization_app.tests.test_geocodage_adresses customization_app.tests.test_geocodage_essais -q
```

Régression Chromium du dialogue sur DEV, depuis `frappe-bench` :

```sh
env/bin/python apps/customization_app/customization_app/tests/check_optimisation_ui_dev.py
```

Ce parcours simule les réponses de calcul : proposition immédiate avec des
estimations, absence de confirmation de position, invalidation d’une proposition,
réponse périmée, erreur de calcul, date vide et affichage mobile. Il n’applique
aucun planning et bloque les connexions externes du navigateur.

Les tests `test_geocodage_adresses_db` demandent un contexte Frappe initialisé
sur DEV. Ils annulent leurs données synthétiques et interdisent le réseau.

## Passage en production

Le déploiement du code ne copie ni la base DEV, ni ses clés, ni son activation.
Inclure les nouveaux modules `geocodage_adresses.py`, `geocodage_essais.py`, `geocodage_liens.py`,
le patch `patches/ensure_geocodage_qualite.py`, les scripts
`public/js/address_geocodage.js` et `public/js/geocodage_positions.js`, les tests et les modifications des hooks,
des réglages et des tournées dans le commit destiné à la production.
Au moment de cette préparation, les nouveaux fichiers sont encore non suivis
par Git : un simple `git commit -am` ne les inclurait pas.

Après récupération de ce code sur le serveur, le déploiement habituel doit
exécuter `bench --site NOM_DU_SITE_PROD migrate`, construire les assets et
redémarrer les processus web/workers. La migration synchronise le DocType
de configuration puis le hook `after_migrate` crée les champs de qualité sur
Address de manière idempotente. Elle n'active pas le service et ne modifie
pas le planning. Ne pas restaurer la base DEV pour déployer cette fonctionnalité.

Dans **Config Optimisation Tournees** du site de production :

1. Renseigner **Clé Google Geocoding**. La clé Routes existante sert de repli
   si ce champ est vide ; elle doit alors aussi autoriser Geocoding API dans
   son propre projet Google Cloud. Une clé distincte pour la production facilite
   le suivi et permet de limiter ses accès au serveur de production.
2. Laisser **URL Nominatim privé** vide tant qu'aucune instance n'est disponible.
3. Activer **Autoriser la recherche des adresses clients avec Google et
   Nominatim privé**, puis tester une adresse et une proposition de tournée.
4. Vérifier que le scheduler du site et les workers fonctionnent pour la
   préparation quotidienne des tâches ouvertes des 14 prochains jours.

Le plafond de 200 concerne les recherches de géocodage du site (moteur et
comparateur réunis). Les appels de calcul routier Google Routes ont leur propre
plafond existant. Les compteurs DEV et PROD sont séparés ; si la même clé/projet
Google sert aux deux sites, les consommations s'additionnent côté Google.
La préparation quotidienne mémorise les localisations sans déplacer les
rendez-vous ; l'application d'une proposition reste une action explicite.
Les estimations sont utilisables automatiquement ; leur correction sur la fiche Adresse reste facultative.

Références : [conditions Nominatim public](https://operations.osmfoundation.org/policies/nominatim/),
[conservation et affichage Google](https://developers.google.com/maps/documentation/geocoding/policies).

Sources des adaptateurs : [Google Geocoding](https://developers.google.com/maps/documentation/geocoding/guides-v3/requests-geocoding),
[Geoapify](https://apidocs.geoapify.com/docs/geocoding/),
[LocationIQ](https://docs.locationiq.com/docs/search-forward-geocoding),
[Nominatim](https://nominatim.org/release-docs/latest/api/Search/).

## Limite stricte de déplacement des rendez-vous

Le réglage `fenetre` est désormais une borne stricte autour de l’heure actuelle :
120 minutes signifie au plus deux heures d’avance ou de retard. Le solveur ne
la double plus et ne passe plus automatiquement en horaires libres. Une fenêtre
sans intersection avec les heures de visite produit une tâche non placée.
Les arrondis à cinq minutes restent à l’intérieur des bornes.

Après le recalage des trajets, une visite dépassant cette limite retrouve son
heure et son employé initiaux ; les trajets sont recalculés dans le nouvel ordre.
Les éventuels retards sont affichés et propagés, sans modifier l’heure promise.
Le réglage distinct de dépassement de fin de journée conserve son comportement.

Le bouton Appliquer transmet la fenêtre utilisée. Le serveur verrouille les
lignes et contrôle tout le plan avant la première écriture. Un ancien écran qui
ne transmet pas la fenêtre est soumis au réglage serveur. Une heure devenue fixe
reste prioritaire. Les horaires libres restent possibles lorsque l’utilisateur
les choisit explicitement (`fenetre=0`).

Régressions : `test_tournee_fenetre`, tests de journée surchargée et application
atomique dans `test_tournee_optimisation`, propagation des retards dans
`test_tournee_google`, et transmission de la limite dans `check_optimisation_ui_dev`.

## Redistribution et charge des techniciens

Le rôle `Technicien` doit être attribué au compte utilisateur lié à l’employé
actif ; le compte doit être activé. Le métier ou l’intitulé de poste ne remplace
pas ce rôle. Sans ce rôle, les rendez-vous restent chez leur employé : seul son
parcours et ses horaires sont optimisés. Les tâches des techniciens ne peuvent
être transférées qu’aux autres techniciens participant au calcul, sous réserve
des épingles et de la limite horaire. Le serveur revérifie ce rôle à l’application.

L’équilibrage utilise une dimension de charge sans temps d’attente : durée des
interventions + trajets aller/retour compris + stationnement + rangement. Le
poids configuré pénalise la charge la plus élevée parmi les techniciens, en plus
du coût des trajets. Les employés hors de ce groupe ne pèsent pas dans ce critère.
Une seule personne éligible ne déclenche pas d’équilibrage entre employés.
Les charges fixes des tâches conservées hors du modèle restent comptées pour
leurs interventions et marges. Les trajets complets sont réévalués après calcul.

Les minutes de charge avant/après et leur détail apparaissent sur chaque tournée.
Les durées restent des prévisions de travail : celles du parcours proposé incluent
les trajets recalculés avec Google quand il est disponible, sinon leur estimation
OSRM. Attente et pause ne sont pas du travail actif. Le nombre de tâches n’est pas
un objectif d’égalité : une longue réparation peut valoir plusieurs entretiens.

Tests : `test_tournee_charge`, scénarios sans technicien ou avec un seul technicien,
refus d’une redistribution après changement de rôle, limites horaires existantes
et affichage de la charge dans le navigateur. Aucun rôle réel n’est modifié pour
ces essais.

Vérification DEV du 24/11/2026 : les comptes de Sadok et Jamel ne possèdent pas
ce rôle. La proposition conserve donc respectivement leurs 8 et 1 tâches, sans
transfert, et respecte la limite de ±120 minutes. Le planning réel reste inchangé.
L’interface a aussi été vérifiée dans Chromium, sur ordinateur et mobile, avec
les endpoints d’application simulés.
