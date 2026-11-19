# Clippeur GTA 6

Deux façons de l'utiliser, toutes deux depuis le téléphone :

* **Automatique** : toutes les 2 heures, le chasseur va chercher les clips GTA les plus regardés du moment sur Twitch et Kick, garde les meilleurs, les monte et les range dans ta galerie (30 clips par jour au plus, hors campagne). Tu n'as plus qu'à appuyer sur **Partager**.
* **À la demande** : tu colles un lien dans une issue, tu reçois le Short monté.

Le calcul se fait sur les serveurs de GitHub : aucun ordinateur à laisser allumé.

## Le circuit automatique

1. **Chasse** (05:17, 11:17 et 17:17 UTC) : clips des dernières 24 h dans les catégories GTA VI et GTA V, en français par défaut, plus les streamers de `streamers.yaml`.
2. **Tri** par vitesse de vues : un clip récent qui décolle passe devant un vieux clip à succès. Un même streamer est pris deux fois par jour au plus.
3. **Montage** des meilleurs. Gemini regarde chaque clip, le note sur 10 et écarte ceux sous 6. Il écrit accroche et titre, repère la webcam du streamer (caméra en haut, jeu en bas) et signale cinématiques et spoilers.
4. **Contrôle GO / NO-GO** du fichier fini. Seuls les GO sont livrés.
5. **Galerie** mise à jour et une issue « 🎬 clips prêts à poster » : l'app GitHub t'envoie une notification.
6. **Toi** : dans la galerie, **Préparer la vidéo**, puis **Partager**, puis TikTok, YouTube ou Instagram. Les boutons **Titre YT** (titre YouTube, toujours sous 100 caractères) et **Légende** (légende TikTok, ou description YouTube) copient le texte à coller. **Posté ?** range le clip.

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

La galerie se met à jour après chaque chasse qui monte des clips, et se republie d'elle-même quand la page, les campagnes ou ses réglages changent.

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
* Sous-titres mot à mot, les mots à venir estompés. Ils sont écrits par Parakeet (NVIDIA), bien plus juste que Whisper sur l'oral de stream, puis Gemini réécoute le clip et corrige les noms et l'argot. Ajoute tes mots (pseudos, personnages, expressions) dans `vocabulaire.yaml`, modifiable depuis le téléphone
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

### Campagnes rémunérées (Content Rewards)

Un bloc `campaign:` sous un streamer (exemple : Dasgas Dom) applique le brief de la campagne. La chasse ne prend que la source autorisée (stream daté, jeu demandé, même live après minuit) et la fait passer avant tout le reste, jusqu'à la date limite. Le montage suit la langue du streamer, écrit son crédit, ajoute les hashtags exigés et la mention `#ad`, coupe la musique si demandé, et met « SPOILER WARNING » sur la couverture d'un moment d'histoire sans rien dévoiler. Dans la galerie, le bouton **Légende** copie la légende complète, le bouton **Titre YT** un titre YouTube de 100 caractères au plus (mention de partenariat en tête, hashtag exigé `title_tags` à la fin), et la fiche rappelle les plateformes acceptées, le réglage « contenu commercial » et le délai de soumission du lien. Les jours de stream, une ligne `cron` de `chasse.yml` ajoute des passages réservés aux campagnes.

**Veille des lives** (`live.yml`) : la chasse de campagne part dès que le streamer lance son live, puis toutes les heures pendant le live et pendant 6 heures après la fin (l'outil tire aussi ses propres clips de la rediffusion). Tant que la fenêtre du stream est ouverte, une chasse part au moins toutes les 2 heures, même si le live n'est pas vu. GitHub lance les tâches programmées de ce dépôt avec 8 à 14 heures de retard (problème connu des comptes récents) : autour du stream, de 24 heures avant jusqu'à 6 heures après la fin, la veille tourne donc d'un seul tenant (un contrôle toutes les 5 minutes) et se relance elle-même toutes les 5 heures. Elle démarre seule quand `streamers.yaml` change ; sinon **Actions**, **Veille des lives**, **Run workflow**.

## Technique de montage

Chaque clip suit la méthode des comptes de clips qui marchent :

* **Première seconde** : l'IA fait commencer le clip en pleine action, en pleine phrase ou sur la réaction, jamais sur un menu, un chargement ou un silence. Un écran noir au début est retiré d'office. Si le moment fort arrive tard, un aperçu de 1,8 s ouvre le clip (sauf campagne qui l'interdit).
* **Coupes** : l'IA retire les temps morts qu'elle voit (hésitations, répétitions, attente), une coupe toutes les 4 à 8 secondes sur un passage bavard. En plus, l'outil raccourcit les silences entre deux phrases quand le son est calme et que l'image ne bouge presque pas, en laissant un petit souffle. Jamais pendant l'action, jamais plus d'un quart du clip. Un morceau sur deux est légèrement zoomé pour marquer la coupe. `tighten: off` sous un streamer coupe ce resserrage (Dasgas Dom : son brief demande de laisser respirer).
* **Sous-titres** : mot à mot, 3 mots au plus, coupés aux virgules et aux fins de phrase, jamais sur un petit mot qui annonce la suite (« RUN OUT » puis « THE DOOR »). Ils restent même quand le jeu affiche ses propres dialogues sous-titrés. Les gros mots anglais et français sont masqués (« F****** ») dans les sous-titres, l'accroche, le titre et la description, réglage `captions.censor` dans `config.yaml`.
* **Cadrage** : webcam en haut et jeu en bas quand l'IA repère la webcam ; sinon le jeu occupe la moitié de l'écran (`zoom: 1.5`), avec un renfort de netteté.
* **Son** : voix compressée doucement, puis volume cible des plateformes (-14 LUFS).

## Français et anglais

La chasse prend les clips français et anglais (`require_languages` et `twitch.languages` dans `config.yaml`), avec une recherche Twitch par langue et une alternance des deux langues, pour que l'anglais, bien plus vu, ne prenne pas toute la place. Accroche, titre et légende sont écrits dans la langue parlée du clip (`metadata.language: auto`). Dans la galerie, chaque clip porte un badge FR ou EN, et le bouton **FR + EN** filtre par langue. Pour revenir au français seul : `[fr]` sur ces deux lignes.

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
* **Pas de sous-titres** : aucune parole détectée, ou modèle de transcription indisponible. Le clip sort quand même.
* **Un mot revient mal écrit** : ajoute-le dans `vocabulaire.yaml` (crayon sur GitHub, puis **Commit changes**). Gemini s'en sert au clip suivant.
* **Quota** : GitHub offre 2 000 minutes par mois aux dépôts privés, soit environ 300 clips.

## Sur ton ordinateur ou dans une session Claude

```
pip install -r requirements.txt
python -m clipper local video.mp4 --options "0:40-1:10 @streamer #crop"
python -m pytest -q
```
