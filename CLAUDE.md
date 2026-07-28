# Contexte du projet

## Objectif

Application de terminal accessible sous Windows, en Python, pilotable
entièrement au clavier et conçue pour NVDA. Elle exécute des commandes en
local et à distance par SSH.

L'utilisateur est non-voyant, travaille avec NVDA et un afficheur braille,
et n'utilise jamais la souris. **L'accessibilité n'est pas une option de
confort ici : c'est le cahier des charges.**

## Contraintes non négociables

- **wxPython uniquement.** Le choix repose sur l'usage de vrais contrôles
  Win32 natifs, lus nativement par NVDA. Ne jamais proposer de migrer vers
  Qt, Tkinter ou une interface web.
- **Aucune fenêtre de console ne doit apparaître.** Tout appel à
  `subprocess` utilise `creationflags=CREATE_NO_WINDOW` (0x08000000).
- **SSH via Paramiko**, jamais par `plink` ni par le `ssh.exe` de Windows :
  OpenSSH lit le mot de passe sur le terminal et non sur stdin, ce qui rend
  l'authentification impossible sans console.
- **Toute entrée-sortie réseau ou processus se fait hors du thread
  principal**, avec remontée vers l'interface par `wx.CallAfter`. Une
  interface figée est un blocage total pour un utilisateur de lecteur
  d'écran.
- **Aucun caractère décoratif dans la sortie.** Pas de lignes de tirets, pas
  de séparateurs en caractères Unicode : une synthèse vocale les énonce un
  par un et un afficheur braille les gaspille.
- **Identifiants et commentaires en français.** Une voix de synthèse
  française lit correctement `traiter_sortie` et massacre `handle_output`.

## Décisions déjà arrêtées

- **Nom du projet : LazyShell.** Choisi pour une distribution GitHub en
  anglais ; assume le ton « terminal pour quelqu'un qui déteste la ligne
  de commande », en cohérence avec l'objectif d'accessibilité maximale.
  Fichiers de données à côté de l'exécutable renommés en anglais en même
  temps : `settings.json` (ex `reglages.json`), `ssh_profiles.json` (ex
  `profils_ssh.json`), `known_hosts` (ex `hotes_ssh_connus`),
  `lazyshell.log` (ex `terminal.log`). Le nom de service utilisé dans le
  Gestionnaire d'identifiants Windows (`ssh.SERVICE_KEYRING`) est passé de
  `TerminalAccessible-SSH` à `LazyShell-SSH` ; le mot de passe déjà
  mémorisé pour un profil de test existant a été recopié manuellement vers
  le nouveau nom de service au moment du renommage, l'ancien n'a pas été
  supprimé par précaution. Les identifiants et commentaires du code
  restent en français (règle inchangée, voir plus haut) : seul ce qui est
  visible depuis l'extérieur du code — nom du produit, fichiers de
  données, exécutable — passe à l'anglais.
- Modèle « une commande à la fois », pas de terminal interactif plein
  écran. Les invites de saisie détectées ouvrent une boîte de dialogue
  accessible.
- Sortie découpée en blocs. Un bloc = une commande, sa sortie, son code de
  retour, son horodatage. Le rendu textuel est secondaire, la structure
  fait foi.
- Multi-sessions par `wx.Notebook` dès le départ. Le nom de session figure
  dans le nom accessible des champs : c'est le garde-fou contre la commande
  tapée dans la mauvaise session.
- Annonce vocale par le client contrôleur NVDA, en ctypes, avec dégradation
  silencieuse si la DLL est absente.
- **Second canal d'annonce pour JAWS** (COM, `pywin32`, voir
  `Voix._charger_jaws`), en parallèle du client NVDA, avec la même
  dégradation silencieuse. À la différence du canal NVDA, celui-ci n'a
  jamais été vérifié avec un JAWS réel : l'identifiant COM et les noms de
  méthode (`SayString`, `StopSpeech`) viennent des références les plus
  courantes sur l'automatisation JAWS, pas d'une confirmation. Si un jour
  quelqu'un peut tester avec un vrai JAWS et que ça ne marche pas, c'est
  le premier endroit à vérifier — pas une raison de douter du canal NVDA,
  qui reste indépendant et déjà validé.
- **Le statut passe toujours avant le contenu dans une annonce**, pour
  qu'on puisse couper la parole dès qu'on sait que la commande a réussi.
- Règles de verbosité : sortie vide et code 0 donnent « Terminé » ; moins de
  10 lignes sont lues intégralement ; un code de retour non nul fait lire
  l'erreur en entier quel que soit le niveau réglé.
- Pas de mot de passe en clair dans un fichier de configuration. Clés SSH,
  ou Gestionnaire d'identifiants Windows via `keyring`.
- Clé d'hôte SSH mémorisée à la première connexion (principe du
  known_hosts d'OpenSSH), dans un fichier propre à l'application
  (`known_hosts`), jamais celui de l'utilisateur. Toute clé qui
  change ensuite doit être confirmée explicitement par une boîte de
  dialogue : jamais d'acceptation automatique et silencieuse.
- SSH sans pseudo-terminal (`get_pty=False`) : stdout et stderr restent
  séparés, comme en local. Conséquence acceptée : une invite purement
  interactive côté shell distant (ex. `read -p` de bash, qui n'écrit rien
  du tout hors mode interactif) n'écrit rien de détectable ; seule
  l'interruption manuelle (Ctrl+Maj+K) s'applique alors. Même famille de
  limite que `Read-Host` bloqué en local par `-NonInteractive`.
- Interruption d'une commande par **Ctrl+Maj+K**, raccourci principal
  (Ctrl+Pause existe toujours en secours mais n'est pas mis en avant :
  absente ou remappée sur certains claviers).
- **Commandes enregistrées** (`CommandeEnregistree`, `commands.json`,
  menu &Commandes) : un nom associé à une commande longue tapée
  régulièrement. Ctrl+Maj+J l'insère dans la saisie de la session
  courante — jamais exécutée directement, exactement comme le rappel
  d'historique aux flèches — pour rester sur le principe d'une commande
  à la fois et laisser une chance de relire ou modifier avant l'envoi.
  Ctrl+Maj+M enregistre le contenu actuel de la saisie sous un nom
  choisi. Aucun secret là-dedans, donc pas de passage par `keyring` :
  juste un fichier JSON de plus à côté de l'exécutable, sur le même
  principe que `ssh_profiles.json`.
- **Compilation en mode dossier (`--onedir`), pas `--onefile`.** Un
  `--onefile` réextrait tout dans un dossier temporaire (`sys._MEIPASS`)
  à chaque lancement, ce qui coûte une vraie latence au démarrage —
  sensible pour un utilisateur qui attend l'annonce vocale. En
  `--onedir`, PyInstaller 6 range déjà tout seul les bibliothèques
  Python dans un sous-dossier `_internal` à côté de l'exe : rien à
  organiser à la main. Le dossier `dist\LazyShell` entier (exe +
  `_internal` + `nvdaControllerClient.dll` copiée à côté par
  `compiler.bat` + fichiers de données créés au premier lancement)
  reste déplaçable tel quel (clé USB, autre machine) sans installation :
  la portabilité tient à `dossier_base()`, qui pointe toujours sur le
  dossier de l'exe, pas au fait que ce soit un fichier unique.
- **Tous les raccourcis clavier sont interceptés à la main**
  (`Fenetre.sur_touche_globale`, sur `wx.EVT_CHAR_HOOK`), jamais laissés
  à la seule table d'accélérateurs native de wx construite depuis le
  texte `"\tCtrl+Shift+X"` des libellés de menu. Deux raisons : wx
  n'accepte que des noms de touches anglais dans ce texte (« Shift »,
  pas « Maj »), incompatible avec des libellés en français ; et ça
  rassemble toute la logique de raccourcis à un seul endroit, plus facile
  à auditer que deux mécanismes qui pourraient un jour se contredire.
  Les libellés de menu affichent donc « Ctrl+Maj+X » / « Alt+Haut » /
  « Alt+Bas » sans que ça touche au fonctionnement réel de la touche.
- **`compiler.bat` sauvegarde et restaure les fichiers de données**
  (`settings.json`, `ssh_profiles.json`, `known_hosts`, `commands.json`)
  autour de l'appel à PyInstaller. Nécessaire depuis le passage en
  `--onedir` : PyInstaller supprime entièrement `dist\LazyShell` avant
  de le reconstruire, donc sans cette sauvegarde chaque recompilation
  effacerait silencieusement les profils SSH et les réglages de
  l'utilisateur qui vivent dans ce même dossier. (Un jeu de données de
  test a été perdu de cette façon pendant la mise au point de ce
  correctif, avant qu'il n'existe — voir Palier 3.)
- **Dépôt GitHub public, sous licence MIT (`LICENSE`).** Renommé de
  `terminal-accessible` à `lazyshell` pour matcher le produit (GitHub
  garde une redirection automatique sur l'ancienne URL). Avant bascule
  en public : historique git entier vérifié (aucun secret, aucun fichier
  de données personnelles n'a jamais été commité, même dans un vieux
  commit — rien à réécrire) ; scripts de tooling propres à un poste de
  développement assisté par Claude Code retirés du dépôt (`claude_ici.bat`,
  tout `Outils/`, `publier.bat` devenu obsolète et risqué — commit
  automatique sans revue de diff) ; mention d'un profil SSH réel
  (« VPS Hostinger ») rendue générique dans ce fichier. `LISEZMOI.md`
  renommé `README.md` : c'est le seul nom que GitHub rend automatiquement
  comme page d'accueil du dépôt, sur le même principe que le passage à
  l'anglais déjà fait pour `settings.json` — seul ce qui est visible de
  l'extérieur change de nom, le contenu reste en français. Contenu
  entièrement réécrit pour refléter l'état réel de l'appli (l'ancienne
  version décrivait encore le Palier 0, exécution simulée).

## Comment lancer et tester

- `lancer.bat` — démarre l'application (aucune compilation nécessaire).
- `lancer_muet.bat` — idem sans annonce vocale, à utiliser dès qu'on touche
  à la couche vocale, pour éviter une boucle de parole incontrôlée.
- `compiler.bat` — produit l'exécutable via PyInstaller, en mode dossier
  (voir décision ci-dessus). Réservé aux paliers. Résultat dans
  `dist\LazyShell\` : c'est ce dossier complet qu'il faut distribuer ou
  copier ailleurs, jamais `LazyShell.exe` seul.
- `lazyshell.log` — journal complet, y compris les exceptions non rattrapées.
  C'est la première chose à lire en cas de problème.

L'environnement Python est dans `venv`. Utiliser
`venv\Scripts\python.exe`, jamais le Python global.

## État d'avancement

- **Palier 0, fait** — interface, sessions, modèle de blocs, navigation,
  copie, couche vocale, journal. L'exécution des commandes est simulée dans
  `PanneauSession.executer()`.
- **Palier 1, fait** — exécution locale réelle en tâche de fond (PowerShell
  via `execution.ExecuteurLocal`), interruption, historique, détection des
  invites de saisie (fragment de ligne sans retour à la ligne, silencieux
  plus de 1,5 s) ouvrant une boîte de dialogue accessible.
- **Palier 2, fait** — SSH par Paramiko (`ssh.ExecuteurSSH`, même contrat
  que `ExecuteurLocal`), profils de connexion (`ssh_profiles.json`, non
  secrets), identifiants dans le Gestionnaire d'identifiants Windows via
  `keyring`, mémorisation de la clé d'hôte à la première connexion.
  Détection d'invite réutilisée sur le canal distant ; listing `ls`
  amélioré sur le même principe que `dir`/`ls` en local (nom en tête,
  seulement pour un `ls` nu, jamais réécrit s'il y a des arguments) ;
  répertoire courant récupéré en silence (`pwd`) juste après connexion ;
  statut « commande en cours » annoncé vocalement et porté dans le titre
  de la fenêtre (utile au retour d'un Alt+Tab, la barre de statut seule
  n'étant pas lue automatiquement par NVDA). Complété ensuite par : filtre
  des séquences ANSI dans la sortie, maintien de connexion SSH
  (keepalive, 30 s), transfert de fichiers SFTP (`ExecuteurSSH.envoyer_fichier`/
  `recuperer_fichier`, menu Session), taille de police ajustable et
  mémorisée (`settings.json`, ignoré par git), et le second canal JAWS
  décrit plus haut.
- **Palier 3, en cours** — exécutable final. Compilation testée avec les
  dépendances SSH et JAWS (`paramiko`, `keyring`, `cryptography`,
  `pywin32`) : l'exe se lance sans erreur, session locale et SSH
  fonctionnelles. Bugs trouvés et corrigés au passage : le filtre de
  dossiers à ignorer lors de la recherche de la DLL NVDA
  (`Voix._candidats`) comparait des noms sur le chemin absolu, ce qui
  excluait systématiquement tout fichier trouvé sous un dossier nommé
  `dist` — exactement celui où vit l'exe compilé, donc la DLL n'était
  jamais détectée même posée juste à côté de lui ; corrigé en comparant
  sur le chemin relatif à `dossier_base()`. `compiler.bat` embarquait
  la DLL via `--add-binary`, qui l'extrait dans un dossier temporaire à
  chaque lancement (`sys._MEIPASS`) — jamais lu par l'appli, qui ne
  cherche que dans le dossier de l'exe : remplacé par une copie de la DLL
  à côté de `LazyShell.exe` après compilation. Et la persistance des
  réglages (`Reglages`) n'écrivait que la taille de police dans
  `settings.json` : le suivi de sortie, le listing amélioré, l'horodatage
  et la verbosité revenaient à leur valeur par défaut à chaque
  redémarrage, y compris depuis l'exe compilé ; généralisé en
  `charger_reglages()`/`enregistrer_reglages()`, qui lisent et
  réécrivent les cinq réglages ensemble. Passage ensuite de `--onefile`
  à `--onedir` (voir décision ci-dessus) : `compiler.bat` produit
  maintenant `dist\LazyShell\LazyShell.exe` avec un sous-dossier
  `_internal`, plus de latence de désarchivage à chaque lancement. Ce
  changement a effacé sans prévenir le profil SSH, la clé d'hôte et les
  commandes enregistrées qui vivaient dans l'ancien `dist\LazyShell`
  (PyInstaller supprime tout le dossier de sortie à chaque
  recompilation) ; `compiler.bat` sauvegarde et restaure désormais ces
  fichiers autour de la compilation (voir décision ci-dessus), mais ce
  jeu de données précis n'a pas pu être récupéré — à recréer à la main
  (le mot de passe, lui, est resté dans le Gestionnaire d'identifiants
  Windows, indépendant de ce dossier). Trois autres corrections dans la
  foulée : le sous-menu de taille de police n'indiquait jamais la taille
  active (aucune coche) ; les entrées sont passées en cases à cocher
  synchronisées par `Fenetre._synchroniser_taille_police()`. Les boîtes
  de dialogue « Profil SSH » et « Enregistrer une commande » utilisaient
  `CreateButtonSizer(wx.OK | wx.CANCEL)`, qui affiche les libellés de
  stock wx non traduits (« Cancel ») faute de catalogue de traduction
  chargé ; remplacé par des `wx.Button` explicites (« Enregistrer » /
  « Annuler »), sur le modèle déjà utilisé ailleurs dans le fichier. Et
  tous les libellés de raccourcis clavier des menus affichaient
  « Shift »/« Up »/« Down » en anglais ; voir la décision sur la gestion
  manuelle des raccourcis ci-dessus. Restent à vérifier au clavier avec
  NVDA : l'exe compilé en mode dossier (pas seulement le venv), la
  persistance des quatre réglages après un redémarrage, la coche de
  taille de police, les nouveaux libellés « Ctrl+Maj+X » / « Alt+Haut » /
  « Alt+Bas » et leur fonctionnement réel au clavier, les boutons
  « Enregistrer »/« Annuler », et idéalement JAWS si l'occasion se
  présente enfin. Version fixée à `1.0.0` et message d'accueil
  (`MESSAGE_ACCUEIL`) nettoyé des mentions de palier pour la première
  publication publique (voir décision sur le dépôt GitHub ci-dessus).

## Consignes de travail

- Modifier par petites touches vérifiables, pas par réécritures massives :
  relire un différentiel au lecteur d'écran coûte cher.
- Annoncer clairement quels fichiers vont changer avant de les changer.
- Ne jamais introduire de raccourci clavier entrant en conflit avec ceux de
  NVDA, en particulier tout ce qui utilise Inser ou Verrouillage majuscule.
- Après toute modification de l'interface, rappeler à l'utilisateur ce qu'il
  doit vérifier auditivement : c'est lui seul qui peut valider le rendu
  NVDA.
