# Serveur de relevés PokéTrend

Ce dépôt relève les prix de toutes les cartes Pokémon **toutes les heures, 24 h/24**, grâce à GitHub Actions (gratuit pour un dépôt public).
PokéTrend lit ensuite ces relevés : historique complet même quand ton PC est éteint, et chargement en quelques secondes au lieu de 15 à 25 minutes.

## Mise en place (10 minutes, une seule fois)

1. **Crée un compte GitHub** gratuit sur https://github.com/signup
2. **Crée un dépôt** : bouton **New** (ou https://github.com/new)
   - Nom : `poketrend`
   - Visibilité : **Public** (obligatoire pour que PokéTrend puisse lire les relevés, et pour que GitHub Actions soit gratuit sans limite)
   - Clique sur **Create repository**
3. **Envoie les fichiers** : sur la page du dépôt, clique sur **uploading an existing file**, puis glisse **tout le contenu** du dossier `poketrend-serveur` (le dossier `.github`, le dossier `scripts` et ce `README.md`). Clique sur **Commit changes**.
   - Vérifie ensuite que tu vois bien le dossier `.github/workflows/releve.yml` dans le dépôt.
4. **Ajoute ta clé API gratuite** (fortement recommandé) :
   - Crée-la sur https://dev.pokemontcg.io
   - Dans le dépôt : **Settings → Secrets and variables → Actions → New repository secret**
   - Name : `POKEMONTCG_API_KEY` — Secret : ta clé → **Add secret**
   - Sans clé, ça marche aussi, mais toutes les cartes ne sont relevées que toutes les 6 h (les 3 dernières extensions chaque heure).
5. **Lance le premier relevé** : onglet **Actions** → si GitHub le demande, clique sur **I understand my workflows, go ahead and enable them** → **Relevé des prix** → **Run workflow**. Le premier relevé prend 10 à 25 minutes.
6. **Branche PokéTrend dessus** : dans PokéTrend, **⚙︎ Réglages → Serveur de relevés GitHub**, colle :

   ```
   https://raw.githubusercontent.com/TON-PSEUDO/poketrend/data
   ```

   (remplace `TON-PSEUDO` par ton nom d'utilisateur GitHub), puis **Enregistrer**.

Ensuite, le relevé tourne tout seul toutes les heures. Tes amis peuvent coller la même adresse dans leur PokéTrend : ils profitent de tes relevés sans rien installer d'autre.

## Bon à savoir

- **Où sont les données ?** Sur la branche `data` du dépôt (`latest.json`, `hist/`, `history.json.gz`). Elle est réécrite à chaque relevé, le dépôt ne grossit donc pas.
- **Historique conservé** : un relevé par heure sur 26 h, puis un par jour sur 35 jours.
- **Suivre les relevés** : onglet **Actions** (chaque exécution affiche son journal).
- **Mise en pause automatique** : GitHub peut suspendre les tâches planifiées d'un dépôt public sans activité pendant 60 jours. Il envoie un e-mail avant ; il suffit alors de cliquer sur **Enable workflow** dans l'onglet Actions.
- **Coût** : 0 €. GitHub Actions est gratuit et illimité pour les dépôts publics.
