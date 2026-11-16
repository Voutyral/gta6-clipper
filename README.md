# Clippeur GTA 6

Tu colles un lien depuis ton téléphone, tu reçois un Short 9:16 monté, avec son titre et sa description.
Le calcul se fait sur les serveurs gratuits de GitHub : aucun ordinateur à laisser allumé.

## Le circuit

1. Tu ouvres une issue GitHub dont le titre contient le lien (app GitHub, 10 secondes).
2. GitHub ne télécharge que le passage utile, repère les moments forts et monte le clip.
3. Un contrôle GO / NO-GO ouvre le fichier fini et vérifie format, son, poids et risques.
4. Tu reçois la vidéo sur Telegram, avec titre et description à copier d'un appui, plus un lien de téléchargement dans l'issue.
5. Tu publies sur YouTube, TikTok et Instagram. L'envoi automatique sur YouTube existe en option.

## Mise en route (5 minutes, tout au téléphone)

### 1. Telegram, pour recevoir les clips

1. Dans Telegram, ouvre `@BotFather`, envoie `/newbot`, choisis un nom puis un identifiant qui finit par `bot`.
2. Copie le jeton qu'il te donne.
3. Ouvre ton nouveau bot et appuie sur **Démarrer**.
4. Sur github.com dans ton navigateur (pas l'app) : ton dépôt, **Settings**, **Secrets and variables**, **Actions**, **New repository secret**. Nom `TELEGRAM_BOT_TOKEN`, valeur : le jeton.

Sans Telegram, tout marche quand même : le clip arrive par le lien de téléchargement posté dans l'issue.

### 2. Clé Gemini gratuite, pour que l'IA regarde vraiment la vidéo (recommandé)

1. Va sur `aistudio.google.com`, **Get API key**, **Create API key**.
2. Ajoute-la en secret GitHub sous le nom `GEMINI_API_KEY`.

Avec cette clé, Gemini voit l'image et entend le son de chaque passage : il choisit le meilleur, resserre la coupe, écrit accroche et titre, et signale cinématiques, spoilers et violence en ouverture. Sans elle, le programme se rabat sur l'IA gratuite de GitHub (texte et planche d'images), puis sur des règles simples.

### 3. Premier essai

Ouvre une issue avec pour titre un lien de clip Twitch. Un 👀 apparaît quand le travail démarre. Compte 4 à 8 minutes.

## Écrire une demande

Texte libre, dans le titre ou le corps de l'issue. Un lien par ligne si tu en envoies plusieurs.

| Tu écris | Tu obtiens |
| --- | --- |
| `LIEN` | Clip court : monté en entier. Longue vidéo : les 2 meilleurs moments |
| `LIEN 1:02:30-1:03:10` | Exactement cette plage |
| `LIEN 1:02:30` | Un clip autour de cet instant |
| `LIEN x3` | Les 3 meilleurs moments |
| `LIEN @streamer` | Crédit affiché et cité dans la description |
| `LIEN "MON ACCROCHE"` | Ton accroche à la place de celle de l'IA |
| `apprends LIEN ta question` | Une fiche méthode tirée de la vidéo (voir plus bas) |

Options à ajouter au besoin : `#crop` (plein écran), `#blur` (jeu centré sur fond flou), `#split` (caméra en haut), `#noteaser`, `#teaser`, `#nopunch`, `#nosfx`, `#nomusic`, `#nosub`, `#brut` (aucun effet), `#noai`, `#en` (titres en anglais).

Pour enchaîner, commente la même issue avec un nouveau lien, ou passe par **Actions**, **Clip**, **Run workflow**.

## Ce que le montage ajoute

* Accroche en haut et ligne de contexte pendant les premières secondes
* Sous-titres mot à mot, les mots à venir estompés
* Aperçu de 2 secondes du moment fort en ouverture, puis retour au début
* Zoom et secousse brefs sur chaque impact, repérés au son
* Bruitages (whoosh, impact) fabriqués par le programme, donc libres de droits
* Musique de fond baissée automatiquement sous les voix : dépose tes pistes libres de droits, ou celle imposée par une campagne, dans `assets/music/`
* Son normalisé au niveau des plateformes

Ce que le programme ne fait pas, volontairement : filtre couleur, miroir, changement de vitesse ou de hauteur du son. Ces retouches servent à tromper la détection, pas le spectateur. Elles ne donnent aucun droit sur la vidéo et ne comptent pas comme valeur ajoutée.

## Apprendre d'une vidéo : regarder, retenir, refaire

Issue : `apprends https://youtu.be/XXXX comment il rythme ses clips`

Avec la clé Gemini, un lien YouTube public est lu directement chez Google (image et son), sans téléchargement. Les autres liens sont téléchargés en petite qualité puis analysés. La fiche est rangée dans `knowledge/` et postée dans l'issue. Ces fiches sont la matière première des skills : donne-les à Claude pour qu'il en fasse un skill ou qu'il ajuste `config.yaml`.

## Streamers autorisés et radar

`streamers.yaml` liste les streamers que tu as le droit de clipper : campagne rejointe ou accord écrit. Pour chacun : crédit, position de sa caméra, campagne rémunérée ou non.

Avec `auto: true`, le radar récupère chaque matin ses clips Twitch les plus vus des dernières 24 heures, les monte et te les envoie. Un clip d'une source absente de la liste est monté quand même, mais marqué « accord à vérifier ».

## Options avancées

### Envoi automatique sur YouTube

Depuis le 2 novembre 2026, Google ne verrouille plus en privé les vidéos envoyées par un projet non audité (journal des révisions de l'API YouTube). Le programme relit quand même le statut renvoyé par YouTube et te l'annonce tel quel.

1. `console.cloud.google.com` : crée un projet, puis active **YouTube Data API v3**.
2. Écran de consentement OAuth : type **Externe**, puis **publie l'application** (statut « En production »). En mode « Test », le jeton meurt au bout de 7 jours.
3. Identifiants, **ID client OAuth**, type **Application Web**, URI de redirection `https://developers.google.com/oauthplayground`.
4. Sur `developers.google.com/oauthplayground` : roue dentée, **Use your own OAuth credentials**, colle l'ID et le secret. Saisis le scope `https://www.googleapis.com/auth/youtube.upload`, autorise avec ta chaîne, puis **Exchange authorization code for tokens**. Copie le **Refresh token**.
5. Secrets GitHub : `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`.
6. Dans `config.yaml` : `youtube.mode: private` (tu relis puis tu publies dans l'app) ou `public`.

Les intitulés de la console Google changent souvent : cherche le mot le plus proche.

### Liens YouTube comme source vidéo

YouTube refuse en général les téléchargements venant de serveurs comme ceux de GitHub. Trois issues : un lien Twitch ou Kick, un lien Google Drive public vers le fichier, ou un proxy résidentiel dans le secret `YTDLP_PROXY`.

### Clé Claude

Secret `ANTHROPIC_API_KEY` : utilisée si Gemini est absent.

## Règles à garder en tête

* **Accord** : ce qui te couvre, c'est la campagne ou l'accord du streamer, pas le montage.
* **Rockstar** : pas de cinématique seule, pas de fin du jeu ni de grosse révélation, pas de contenu en jeu isolé (émissions télé, radio). Le contrôle rend NO-GO quand l'IA en repère.
* **YouTube** : la violence graphique dans les 7 premières secondes prive le clip de publicité. La radio du jeu peut déclencher une réclamation de droits.
* **Campagne rémunérée** : coche « promotion payante » à la publication, sur chaque plateforme.
* **Republier sans rien ajouter** réduit la portée des Shorts et bloque la monétisation de la chaîne.

## Ce qui a été vérifié avant livraison

Vérifié en conditions réelles : lecture des demandes, repérage des moments forts, les quatre mises en page, aperçu, zoom, bruitages, musique, sous-titres, contrôle GO / NO-GO, coupe à l'image près (33,07 s pour 33 s demandés), niveau sonore (−14,8 LUFS), téléchargement par yt-dlp d'une section et d'une longue vidéo à travers un serveur web local.

Pas vérifiable hors de GitHub, donc validé seulement au premier vrai lancement : les sites eux-mêmes (Twitch, Kick, YouTube), le modèle de sous-titres Whisper, Telegram, le lien de téléchargement, les IA (Gemini, GitHub Models, Claude) et l'envoi YouTube. Chaque échec est écrit en clair dans l'issue avec la marche à suivre.

## Dépannage

* **Rien ne se passe** : l'issue doit être ouverte par le propriétaire du dépôt. Regarde l'onglet **Actions**.
* **Pas de message Telegram** : appuie sur **Démarrer** dans ton bot, puis relance. Le programme retient ta discussion au premier passage.
* **Pas de sous-titres** : aucune parole détectée, ou modèle Whisper indisponible. Le clip sort quand même.
* **Quota** : GitHub offre 2 000 minutes par mois aux dépôts privés, soit environ 300 clips.

## Sur ton ordinateur ou dans une session Claude

```
pip install -r requirements.txt
python -m clipper local video.mp4 --options "0:40-1:10 @streamer #crop"
python -m pytest -q
```
