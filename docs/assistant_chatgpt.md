# Assistant ChatGPT — brancher l'app ChatGPT sur ERPNext

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
