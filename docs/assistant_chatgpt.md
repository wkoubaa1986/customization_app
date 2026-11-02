# Assistant ChatGPT — brancher l'app ChatGPT sur ERPNext

⚠️ OpenAI retire les GPT personnalisés le **11 décembre 2026** et leurs actions ne migrent pas. La voie durable est le
**plugin** (application MCP) servi par `customization_app/mcp_chatgpt.py` ; le GPT avec actions reste utilisable jusque-là.

## Plugin (application MCP) — la voie durable

1. Clé API de l'utilisateur **assistant.chatgpt@aquaworld.local** (fiche User → API Access → Générer les clés).
2. Dans ChatGPT : **Paramètres → Sécurité et connexion → Mode développeur** (activer), puis dans la barre de saisie
   **« + » → Plugins / Applications → Créer** (application MCP distante) :
   - URL du serveur : `https://aquaworldservicing.opssync.pro/api/method/customization_app.mcp_chatgpt.serveur?cle=<clé>:<secret>`
   - Authentification : **aucune** (le secret est dans l'URL) ; cocher « Je fais confiance à cette application ».
3. Dans une conversation, activer le plugin « aqua-world-erp » : les mêmes outils que le GPT (dépense, versement,
   dépense en euros, comptes, fournisseurs, clients, stock, rendez-vous), avec les consignes intégrées au serveur.

Le protocole : MCP Streamable HTTP, JSON-RPC 2.0 (`initialize`, `tools/list`, `tools/call`, `ping`), sans flux SSE.


Module : `customization_app/assistant_chatgpt.py` (actions, schéma OpenAPI, consignes du GPT). Utilisateur technique et
rôle posés par le patch `ensure_assistant_chatgpt`.

## Côté ERPNext (une fois, en prod)

1. Ouvrir l'utilisateur **assistant.chatgpt@aquaworld.local** → section « API Access » → **Générer les clés**.
   Le secret ne s'affiche qu'une fois : le copier. La clé à donner à ChatGPT est `token <clé>:<secret>`.
2. Le schéma est servi par `https://aquaworldservicing.opssync.pro/api/method/customization_app.assistant_chatgpt.openapi`.

## Côté ChatGPT (une fois)

1. ChatGPT → **Explorer les GPT** → **Créer** → onglet **Configurer**.
2. Nom : « Aqua World ERP ». Instructions : coller `INSTRUCTIONS_GPT` du module (texte ci-dessous).
3. **Actions** → **Créer une action** → **Importer depuis l'URL** avec l'adresse du schéma (ou coller le JSON).
4. **Authentification** : type *Clé API*, en-tête personnalisé, nom `Authorization`, valeur `token <clé>:<secret>`.
5. Enregistrer (« Moi uniquement »). Dans l'app ChatGPT du téléphone, le GPT apparaît dans « Mes GPT ».

## Ce que le GPT sait faire

| Action | Rôle |
|---|---|
| `comptes_de_charge` | comptes de charge acceptés par la caisse |
| `chercher_fournisseur`, `chercher_client` | par nom, matricule ou téléphone, mots dans n'importe quel ordre |
| `stock_article` | stock par entrepôt et prix de vente standard |
| `saisir_depense` | écriture de journal selon les règles de la caisse, **brouillon** par défaut, `valider=1` pour soumettre |
| `mes_saisies` | les dernières écritures passées par l'assistant |
| `saisir_depense_euros` | dépense payée depuis le compte en euros, taux du jour automatique (même cœur que la page Banque › Dépenses en euros), écriture validée |
| `banques`, `verser_en_banque` | versement d'espèces d'une caisse (Sabah Espèces par défaut) vers Zitouna ou BIAT, avec la référence, validé par défaut |
| `valider_ecriture` | soumet un brouillon (direct pour les siens, jeton pour ceux d'un humain) |
| `corriger_ecriture`, `annuler_ecriture` | correction (annulation + nouvelle écriture, même état) ou annulation d'une écriture de journal, motif obligatoire. Écriture de l'assistant : direct. Écriture d'un humain : la première demande ne fait rien et renvoie un aperçu + un jeton (10 min) ; seule la seconde demande, avec le jeton après accord explicite, agit. Structure inconnue : annulable seulement |
| `rendez_vous` | rendez-vous d'un jour, de plusieurs jours, ou d'un client (passés et à venir) |
| `creneaux_rendez_vous` | demi-journées disponibles pour un client, par le portail de réservation (secteur, capacité, délais) |
| `creer_rendez_vous` | réserve comme l'opératrice au téléphone : tâche au calendrier, SMS et e-mail de confirmation au client |

Garde-fous : plafond 5 000 DT par dépense, date dans les 90 jours, compte parmi ceux de la caisse, modes Espèces /
Carte de crédit / Virement / Chèque. Le rôle « Assistant ChatGPT » n'a aucune permission de doctype : l'API REST
ordinaire lui répond « non autorisé ». Chaque écriture porte « Saisie via ChatGPT » en remarque et le préfixe
« Dépense caisse — » de la caisse journalière.

## Instructions du GPT

```
Tu es l'assistant ERPNext d'Aqua World & Servicing (Tunisie, dinars TND). Tu parles français.
Quand on t'envoie une photo de facture ou de reçu : lis fournisseur, date, montant TTC, TVA et son taux, numéro de pièce.
Appelle comptes_de_charge pour choisir le compte exact. Récapitule en une ligne (date, libellé, TTC, TVA, compte, mode) et
demande confirmation AVANT d'appeler saisir_depense. Mode « Espèces » sauf indication. Laisse en brouillon (valider=0) sauf
si on te dit « valide ». Après la saisie, donne le numéro d'écriture et son lien. Ne devine jamais un montant illisible :
demande. Pour le stock ou un client, utilise stock_article / chercher_client et réponds brièvement.
```
