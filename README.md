# Clippeur GTA 6

Deux façons de l'utiliser, toutes deux depuis le téléphone :

* **Automatique** : trois fois par jour, le chasseur va chercher les clips GTA les plus regardés du moment sur Twitch et Kick, garde les meilleurs, les monte et les range dans ta galerie. Tu n'as plus qu'à appuyer sur **Partager**.
* **À la demande** : tu colles un lien dans une issue, tu reçois le Short monté.

Le calcul se fait sur les serveurs de GitHub : aucun ordinateur à laisser allumé.

## Le circuit automatique

1. **Chasse** (05:17, 11:17 et 17:17 UTC) : clips des dernières 24 h dans les catégories GTA VI et GTA V, en français par défaut, plus les streamers de `streamers.yaml`.
2. **Tri** par vitesse de vues : un clip récent qui décolle passe devant un vieux clip à succès. Un même streamer est pris deux fois par jour au plus.
3. **Montage** des meilleurs. Gemini regarde chaque clip, le note sur 10 et écarte ceux sous 6. Il écrit accroche et titre, repère la webcam du streamer (caméra en haut, jeu en bas) et signale cinématiques et spoilers.
4. **Contrôle GO / NO-GO** du fichier fini. Seuls les GO sont livrés.
5. **Galerie** mise à jour et une issue « 🎬 clips prêts à poster » : l'app GitHub t'envoie une notification.
6. **Toi** : dans la galerie, **Préparer la vidéo**, puis **Partager**, puis TikTok, YouTube ou Instagram. Les boutons **Titre** et **Légende** copient le texte à coller. **Posté ?** range le clip.

Pour lancer une chasse sans attendre : **Actions**, **Chasseur de clips**, **Run workflow**. Coche « À blanc » pour voir seulement ce qui serait monté.

## Mise en route (3 minutes, tout au téléphone)

### 1. Clé Gemini gratuite, pour que l'IA regarde vraiment la vidéo (recommandé)

1. Sur `aistudio.google.com/apikey`, copie une clé existante ou crée-en une. Aucune carte bancaire : n'active jamais la facturation.
2. Ouvre `github.com/Voutyral/gta6-clipper/settings/secrets/actions/new` dans le navigateur.
3. **Name** : `GEMINI_API_KEY`. **Secret** : colle la clé. Puis **Add secret**.

Avec cette clé, Gemini voit l'image et entend le son de chaque passage : il choisit le meilleur, resserre la coupe, écrit accroche et titre, et signale cinématiques, spoilers et violence en ouverture. Sans elle, le programme applique des règles simples : moments forts repérés au son, accroche tirée d'une liste. L'IA gratuite de GitHub ne répondait pas sur ce dépôt lors des essais ; le programme la réessaie à chaque lancement et s'en passe si elle reste fermée.

### 2. Galerie (pour poster en deux appuis)

La galerie est une page web hébergée par GitHub Pages. Sur un compte GitHub gratuit, Pages n'existe que pour les dépôts publics.

1. Ton dépôt, **Settings**, **General**, tout en bas **Change visibility**, **Make public**.
2. **Settings**, **Pages**, **Source** : **GitHub Actions**.
3. Lance **Actions**, **Galerie**, **Run workflow**. L'adresse est `https://voutyral.github.io/gta6-clipper/`.
4. Dans Safari, **Partager**, puis **Sur l'écran d'accueil** : la galerie s'ouvre comme une app.

Ce que « public » change : le code, les issues et les clips deviennent visibles par qui trouve l'adresse. Tes clés restent secrètes. En échange, les minutes de calcul GitHub deviennent illimitées.

Sans galerie, tout marche quand même : chaque issue contient les liens de téléchargement.

### 3. Notifications

Dans l'app GitHub, active les notifications. Chaque clip fini arrive comme un commentaire sur ta demande.

### 4. Premier essai

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

## Streamers suivis

`streamers.yaml` liste les streamers dont tu as rejoint la campagne ou obtenu l'accord. Le chasseur suit leurs clips même hors des catégories GTA, leur donne la priorité, utilise leur crédit et la position de leur caméra, et rappelle la mention « promotion payante » si la campagne est rémunérée.

Les autres clips trouvés par la chasse sont montés avec la mention « accord à vérifier ». Pour ne garder que les streamers de la liste : `allowlist_only: true` dans `config.yaml`.

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

Vérifié ensuite sur GitHub, le 16 novembre 2026, avec une vidéo libre de droits : installation, téléchargement, sous-titres Whisper, montage, contrôle et lien de téléchargement.

Vérifié sur GitHub le 18 novembre 2026 : clip Twitch envoyé par issue (Gemini a resserré la coupe et écrit l'accroche) ; chasse réelle (98 clips lus sur Twitch et Kick, 2 clips français montés, notés 8 et 7 sur 10, webcam repérée, GO, issue de notification ouverte) ; Kick lisible et téléchargeable, catégorie GTA VI comprise ; YouTube refuse les serveurs GitHub comme source. La galerie a été testée dans un navigateur au format iPhone (copier, préparer, partager, marquer posté, mémoire après rechargement).

Reste à valider : la publication de la galerie sur GitHub Pages (elle attend que le dépôt soit public et Pages activé) et l'envoi automatique sur YouTube. Chaque échec est écrit en clair dans l'issue avec la marche à suivre.

Limite connue : le cadre de la webcam est donné par Gemini et déborde parfois un peu sur l'interface du jeu. Pour un streamer suivi, la position exacte se règle une fois pour toutes dans `streamers.yaml` (`facecam`).

## Dépannage

* **La chasse ne trouve rien** : regarde le résumé du lancement (onglet **Actions**). Les sources illisibles y sont listées. Les clips déjà vus ne reviennent jamais.
* **Trop peu de clips** : baisse `min_ai_score` ou `min_views`, ou ajoute `en` aux langues dans `config.yaml`.
* **Tout vérifier d'un coup** : onglet **Actions**, **Diagnostic**, **Run workflow**. Le bilan dit si Gemini et l'envoi YouTube répondent, et teste les liens que tu lui donnes.

* **Rien ne se passe** : l'issue doit être ouverte par le propriétaire du dépôt. Regarde l'onglet **Actions**.
* **Pas de sous-titres** : aucune parole détectée, ou modèle Whisper indisponible. Le clip sort quand même.
* **Quota** : GitHub offre 2 000 minutes par mois aux dépôts privés, soit environ 300 clips.

## Sur ton ordinateur ou dans une session Claude

```
pip install -r requirements.txt
python -m clipper local video.mp4 --options "0:40-1:10 @streamer #crop"
python -m pytest -q
```
