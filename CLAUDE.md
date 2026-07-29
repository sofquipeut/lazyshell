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
- Règles de verbosité : sortie vide et code 0 donnent « Terminé, aucune
  sortie » (un simple « Terminé » se confondait trop facilement avec un
  silence de la synthèse ou une commande encore en cours) ; moins de
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
- **Mode fichiers (navigateur SFTP), en plus du terminal, pour une
  session SSH.** Ctrl+Maj+F bascule saisie+sortie contre une liste
  (`PanneauSession.liste_fichiers`, un `wx.ListBox` — même contrôle déjà
  utilisé pour les autres listes de l'appli, pas de composant nouveau à
  apprendre à NVDA). `self.repertoire` sert de chemin courant aux deux
  modes à la fois : naviguer en mode fichiers met à jour le même
  répertoire que Ctrl+Maj+D, et inversement, pour que l'un reprenne
  toujours où l'autre s'est arrêté. Remplace entièrement les anciens
  Ctrl+Maj+E / Ctrl+Maj+T (« Envoyer un fichier… » / « Récupérer un
  fichier… », un aller simple par boîtes de dialogue) : le mode fichiers
  couvre le même besoin en mieux, ces deux raccourcis ont été supprimés
  plutôt que gardés en double emploi.
  Actions disponibles, en touches nues locales à la liste (même
  principe que Entrée dans la saisie — pas des raccourcis globaux, donc
  rien dans `Fenetre.sur_touche_globale`) : Entrée (ouvrir un dossier ou
  éditer un fichier), Retour arrière (remonter), Suppr (supprimer, avec
  confirmation — pas de corbeille côté SFTP, c'est définitif), F2
  (renommer). Nouveau dossier reste à Ctrl+Maj+G (combinaison de
  modificateurs, donc dans `sur_touche_globale` comme les autres — voir
  la décision sur la gestion manuelle des raccourcis plus haut).
  Suppression d'un dossier récursive côté client
  (`ExecuteurSSH.supprimer_dossier`, `ssh.py`) : le protocole SFTP ne
  fournit qu'un `rmdir` qui exige un dossier déjà vide.
  Édition : le fichier est téléchargé dans un dossier temporaire,
  ouvert dans le Bloc-notes de Windows (`subprocess.Popen`, on attend sa
  fermeture dans un thread de fond), et renvoyé sur le serveur seulement
  si sa date de modification a changé pendant que Bloc-notes était
  ouvert. Bloc-notes plutôt qu'un éditeur interne : déjà pleinement
  accessible, pas de coloration syntaxique prévue de toute façon, et
  écrire un éditeur de texte accessible from scratch aurait été un
  chantier disproportionné pour cette fonctionnalité. Canal SFTP séparé
  de celui, éphémère, des méthodes `envoyer_fichier`/`recuperer_fichier`
  existantes (celles-ci restent utilisées telles quelles pour le
  téléchargement/renvoi d'édition) : `ExecuteurSSH._sftp`, ouvert à la
  demande et réutilisé pour toute la navigation (listage, renommage,
  suppression...), fermé avec le reste dans `fermer()` — rouvrir un
  canal SFTP à chaque frappe aurait été un aller-retour réseau de plus
  à chaque action.

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
  Quatre changements ensuite : `MESSAGE_ACCUEIL` a été supprimé
  entièrement (il ne s'affiche plus dans le champ de sortie au
  démarrage d'une session) — son contenu (raccourcis clavier) vit
  maintenant dans une documentation HTML dédiée, `docs\index.html`,
  ouverte depuis le nouvel élément « Documentation » du menu Aide
  (`Fenetre.ouvrir_documentation`, même principe que « Ouvrir le
  journal ») ; `compiler.bat` copie ce dossier `docs` dans
  `dist\LazyShell\docs`, donc il est déjà présent dans l'archive
  distribuée. Le titre de la fenêtre (`PanneauSession.rafraichir_statut`)
  affiche désormais aussi le répertoire courant de la session, en local
  comme en SSH ; un changement de répertoire (dialogue Ctrl+Maj+D, ou le
  `pwd` silencieux lancé à la connexion SSH) passe maintenant par
  `PanneauSession.definir_repertoire`, qui rafraîchit le titre
  immédiatement. Et le README a été corrigé : la section d'installation
  simple indiquait de télécharger le client contrôleur NVDA à part, alors
  que `compiler.bat` l'inclut déjà dans `dist\LazyShell` dès qu'il est
  présent à la racine du dépôt au moment de la compilation — l'archive
  publiée sur les Releases le contient donc déjà ; l'instruction de
  récupérer la DLL a été déplacée dans la section « pour modifier le
  code », seule où elle a un sens (avant de compiler soi-même). Restent à
  vérifier au clavier avec NVDA : l'ouverture de `docs\index.html` depuis
  le menu Aide (exe compilé, DLL et dossier `docs` bien copiés
  ensemble), le nouveau contenu du titre en local et en SSH après un
  changement de répertoire, et l'absence du message d'accueil au
  démarrage d'une session.
  Deux correctifs de plus, trouvés en creusant une commande SSH dont la
  sortie était vide et le code de retour 143. Piste initiale (script
  distant qui se tuait lui-même en boucle sur `/proc`) abandonnée :
  l'explication la plus probable est plus simple, et pas un bug du
  script — le processus tué par la commande (« gateway ») est le PID 1
  du conteneur Docker ciblé ; le tuer arrête le conteneur, ce qui coupe
  net la commande `docker exec` encore en cours (celle qui fait le tri
  dans `/proc` et devait afficher le message final), avec ce même 143
  en retour. Un kill réussi, donc, pas un échec — 143 = 128 + SIGTERM
  (15), le signal qu'envoie `kill` sans option : c'est la trace normale
  d'un arrêt demandé de l'extérieur, pas forcément celle d'un échec
  applicatif. Voir la nouvelle fonction `libelle_signal` ci-dessous.
  `Bloc.texte_complet()` (`lazyshell.py`, ce que Ctrl+Maj+C
  et Ctrl+Maj+L placent dans le presse-papiers) ne portait jamais le code
  de retour ni l'état « interrompue » — une commande en échec sans
  sortie se copiait comme un succès silencieux, l'info la plus utile
  disparaissait au collage ; elle inclut maintenant `[code de retour :
  N]` ou `[interrompue]` quand ce n'est pas un succès, rien sinon (même
  philosophie de silence sur succès que `entete()`). Et `Bloc.rendu()`
  n'affiche plus « Commande complète » pour une commande longue mais sur
  une seule ligne (seuil `LONGUEUR_COMMANDE_ENTETE`, 60 caractères) —
  seule une commande réellement multiligne y perd de l'info réelle en
  entête (les retours à la ligne repliés en « ; ») ; répéter un long
  one-liner juste après son en-tête tronqué ne faisait que doubler la
  lecture avant d'atteindre la sortie, sans rien apporter que Ctrl+Maj+C
  ne donne déjà. Enfin, `composer_annonce` (`lazyshell.py`) annonce
  maintenant « Terminé, aucune sortie » plutôt qu'un simple « Terminé »
  sur une commande réussie sans sortie — trop facile à confondre avec un
  silence de la synthèse ou une commande encore en cours ; voir règle de
  verbosité mise à jour plus haut.
  Et directement lié au 143 ci-dessus : jusque-là, tout code de retour
  non nul était systématiquement annoncé comme une « erreur », y compris
  quand il s'agit en fait d'un arrêt par signal (128 + numéro du signal),
  qui ne dit rien en soi sur un succès ou un échec. Nouvelle fonction
  `libelle_signal(code_retour)` (`lazyshell.py`, juste avant `class
  Bloc`) : reconnaît cette plage (129 à 159) et renvoie `"signal N, NOM"`
  (table `NOMS_SIGNAUX`, les 31 signaux POSIX standards) au lieu de
  `None` pour un vrai code d'erreur applicatif (1 à 127), laissé tel
  quel. Branché aux quatre endroits qui répétaient chacun leur propre
  formatage « erreur N » : `Bloc.entete()` (ligne lue par NVDA à la
  navigation), `composer_annonce()` (annonce vocale automatique en fin
  de commande, devient « Terminée, signal 15, SIGTERM, ... » au lieu
  d'« Erreur, code 143, ... »), le statut braille posé dans
  `PanneauSession.ajouter_bloc()` juste après la commande (celui lu en
  premier après une exécution — c'est lui qui affichait encore « erreur
  143 » avant ce correctif), `Bloc.texte_complet()` (presse-papiers) et
  `Bloc.libelle_liste()` (Ctrl+B). Ne cherche pas à deviner si un signal
  donné était voulu ou non : il nomme juste le signal, à l'utilisateur
  de juger — un futur signal hors de la plage 1-31 (temps réel, rare
  ici) retombe sur « erreur N » plutôt que d'inventer un nom.
  Remarqué juste après, sur ce même « Terminé, aucune sortie » entendu
  en vocal : le braille affichait encore « ok, 0 lignes » à la place —
  deux formulations différentes pour le même résultat, posées par deux
  bouts de code séparés dans `ajouter_bloc()` (l'annonce vocale passait
  par `composer_annonce()`, le braille par un résumé compact construit à
  la main, jamais les deux mêmes mots). Le braille reprend maintenant
  l'annonce vocale telle quelle quand elle tient dans `Voix.LIMITE_BRAILLE`
  (120 caractères) ; le résumé compact (« Bloc N, statut, X lignes ») ne
  sert plus que de repli pour une sortie assez longue pour ne pas tenir
  dans cette limite — même règle que `Voix.dire()` applique déjà
  ailleurs pour ne pas perdre le message en braille en dépassant la
  limite de l'afficheur.

- **Mode fichiers (SFTP), nouveau, pas encore testé avec un vrai
  serveur ni au clavier avec NVDA.** Voir la décision correspondante
  plus haut pour le détail. Fait depuis la version 1.1.0 (publiée),
  donc pas encore dans un exécutable compilé ni une Release — reste à
  recompiler et republier quand ce sera vérifié. À vérifier en
  priorité : navigation (Entrée/Retour arrière) sur une vraie
  arborescence distante, renommage et suppression (fichier et dossier
  non vide), création de dossier, et le cycle complet d'édition — un
  fichier ouvert dans le Bloc-notes, modifié, puis fermé, doit revenir
  sur le serveur ; fermé sans modification, ne doit rien renvoyer.
  Deux bugs remontés au premier essai réel. Diagnostic mené en pilotant
  LazyShell lui-même (autorisation explicite donnée) contre le vrai VPS
  du profil enregistré, en lecture seule, puis avec des reproductions
  wx minimales et jetables (aucune des deux méthodes n'a modifié quoi
  que ce soit côté serveur) :
  Le plus sérieux, « Entrée n'ouvre aucun dossier (ni même un fichier) »,
  est confirmé et corrigé. Fausse piste explorée d'abord : les bits de
  permission SFTP absents au listage — écartée, un test en conditions
  réelles contre le VPS montre que `attr.st_mode` est correctement
  renvoyé et que `lister_repertoire` classe déjà bien dossiers et
  fichiers (repli sur `longname` gardé quand même, inoffensif). La
  vraie cause, prouvée par une reproduction wx isolée : **`wx.ListBox`
  ne génère jamais `EVT_KEY_DOWN` pour Entrée ni pour les flèches** —
  le contrôle natif les consomme en interne avant que wx ne les
  transforme en évènement. `PanneauSession.liste_fichiers` avait un
  `Bind(EVT_KEY_DOWN, ...)` local pour Entrée/Retour arrière/Suppr/F2,
  sur le modèle de `saisie` — modèle qui ne s'applique pas à ce
  contrôle. Retiré ; ces quatre touches sont maintenant reconnues dans
  `Fenetre.sur_touche_globale` (`EVT_CHAR_HOOK`, qui lui reçoit ces
  touches de façon fiable, prouvé par la même reproduction), avec un
  garde `panneau.mode_sftp` qui laisse filer la touche à son usage
  normal ailleurs (Entrée envoie la commande, Retour arrière efface du
  texte...) quand ce n'est pas le mode fichiers.
  Le second (Échap qui ramène au terminal après un F2) a fini par être
  identifié, sur un nouveau signalement précis : ça n'arrive QUE en
  annulant le renommage avec Échap, pas en validant avec Entrée — et le
  même phénomène se produit aussi au retour d'un Alt+Tab, partout dans
  l'appli, pas seulement en mode fichiers. Ce deuxième indice a mené à
  la vraie cause, dans du code d'AVANT cette session (Palier 2/3) :
  `Fenetre.rendre_focus_au_champ` (rappelée par `sur_activation` sur
  EVT_ACTIVATE au retour d'Alt+Tab, et par `sur_focus_cadre` sur
  EVT_SET_FOCUS du cadre lui-même — donc aussi juste après la fermeture
  d'une boîte de dialogue, quand le focus transite un instant par le
  cadre avant de se reposer quelque part) posait *toujours*
  inconditionnellement le focus sur `panneau.saisie` — sans dommage
  avant l'existence du mode fichiers, mais posant le focus sur un champ
  caché dès que ce mode existe. Rendue consciente de
  `panneau.mode_sftp`, comme `sur_touche_globale` l'était déjà pour
  Échap. Les chemins d'annulation de `renommer_entree_sftp`,
  `supprimer_entree_sftp` et `creer_dossier_sftp` (Échap ou Non dans
  leur boîte respective) posent en plus explicitement le focus sur
  `liste_fichiers` avant de sortir, plutôt que de compter uniquement
  sur la restauration automatique de wx à la fermeture d'une modale —
  celle-ci n'a jamais été prouvée fiable ici (essayée dans plusieurs
  reproductions isolées sans jamais reproduire le bug, ce qui a
  d'ailleurs orienté ce diagnostic vers autre chose que wx lui-même).

- **Télécharger / Envoyer, en mode fichiers (Ctrl+Maj+T / Ctrl+Maj+E).**
  Absents de la première version du mode fichiers : en supprimant les
  anciens raccourcis du même nom (transfert par boîtes de dialogue à
  chemin tapé), le mode fichiers avait bien navigation/édition/
  renommage/suppression/création, mais aucun moyen de garder une copie
  locale d'un fichier sans l'éditer, ni d'envoyer un fichier quelconque
  vers le dossier affiché — signalé par l'utilisateur après coup.
  Recréés avec la même lettre mnémotechnique qu'avant (E = Envoyer, T =
  Télécharger) mais un comportement adapté au navigateur plutôt qu'à
  des boîtes de dialogue à chemin tapé : Envoyer dépose toujours dans
  le dossier *actuellement affiché*, pas un chemin à saisir ; Télécharger
  demande où garder une copie, et gère un dossier récursivement
  (`ExecuteurSSH.telecharger_dossier`, `ssh.py` — pas de suivi de
  progression détaillé, le nombre de fichiers n'est pas connu à
  l'avance).

- **Placement des menus du mode fichiers, revu une fois le mode en
  place et testé.** Le basculement terminal/fichiers a quitté le menu
  Session pour Affichage : c'est un basculement de vue (comme
  « Listing amélioré », « Horodatage »), pas une action de session — et
  son libellé change avec l'état (« Basculer en mode fichiers » /
  « Basculer en mode terminal », `Fenetre.item_mode_fichiers`,
  synchronisé par `_synchroniser_menu_fichiers()`). Les actions du
  sous-menu « Fichiers distants » (nouveau dossier, renommer, supprimer,
  envoyer, télécharger) sont remontées à la racine du menu Session,
  sans sous-menu : ce sont de vraies actions de session, pas une
  hiérarchie à part. Grisées hors mode fichiers plutôt que masquées :
  wx n'a pas de vrai « cacher » pour un item de menu (seulement
  `Enable(False)`, ou `Remove`/`Insert`, plus fragile à garder juste vu
  la position exacte et les branchements d'événements) ; surtout, un
  menu de forme stable où certains items sont temporairement
  indisponibles se retrouve plus facilement au clavier qu'un menu qui
  change de nombre d'entrées selon le mode — et NVDA annonce déjà
  « grisé », qui porte la même information. `_synchroniser_menu_fichiers()`
  est appelée à la construction des menus, à chaque changement d'onglet
  (chaque session a son propre `mode_sftp`) et après chaque bascule.
  Les raccourcis clavier eux-mêmes ne passent pas par l'état grisé du
  menu (cette appli ne s'appuie jamais sur la table d'accélérateurs
  native de wx, voir plus haut) : ils restent gérés indépendamment dans
  `sur_touche_globale`, qui vérifie déjà `panneau.mode_sftp` de son
  côté.

- **Réglage « Listing amélioré » retiré, le mécanisme reste toujours
  actif.** Devenu inutile comme option une fois le mode fichiers en
  place : parcourir un dossier sans cette réécriture, c'est exactement
  ce que le mode fichiers propose déjà. Item de menu, méthode
  `basculer_listing()` et le réglage persistant `listing_lisible`
  (settings.json) supprimés ; `PanneauSession.executer()` passe
  maintenant `listing_lisible=True` en dur à `self.executeur.executer()`.
  Libère Ctrl+Maj+N (plus mnémotechnique que l'ancien Ctrl+Maj+G) pour
  « Nouveau dossier » en mode fichiers.

- **Vérification des mises à jour, sans installation automatique.**
  Choix délibéré face à un vrai auto-update silencieux (télécharger,
  remplacer l'exe et `_internal` en cours d'exécution, relancer) : trop
  de surface de pannes pour une appli portable sans installeur ni
  signature de code (télécharger au mauvais moment, antivirus qui met
  en quarantaine, écraser par erreur les fichiers de données qui vivent
  à côté de l'exe) — et une mise à jour ratée est un bien pire scénario
  qu'un simple oubli d'aller vérifier sur GitHub, surtout pour un
  utilisateur non-voyant qui ne peut pas juger d'un coup d'œil que
  quelque chose s'est mal passé. À la place : un appel à l'API GitHub
  publique (`GET /repos/sofquipeut/lazyshell/releases/latest`, pas de
  jeton nécessaire) qui compare le tag de la dernière Release à
  `VERSION` (`_version_plus_recente`, comparaison numérique simple,
  sans dépendance `packaging.version`). Vérification silencieuse une
  fois au démarrage (`Fenetre.verifier_mise_a_jour(silencieux=True)`,
  dans `__init__` après `Centre()`) : dégradation silencieuse volontaire
  si pas de réseau ou si déjà à jour, même principe que la DLL NVDA
  absente — un appel réseau qui échoue ne doit jamais inquiéter pour
  une fonctionnalité de confort. Le menu Aide propose la même
  vérification à la demande (`silencieux=False`), avec une réponse dans
  tous les cas cette fois, y compris « déjà à jour ». Si une version
  plus récente existe, une boîte de dialogue propose d'ouvrir la page
  de la Release dans le navigateur (`webbrowser.open`) — rien de plus,
  le téléchargement et la décompression restent manuels.

## Consignes de travail

- Modifier par petites touches vérifiables, pas par réécritures massives :
  relire un différentiel au lecteur d'écran coûte cher.
- Annoncer clairement quels fichiers vont changer avant de les changer.
- Ne jamais introduire de raccourci clavier entrant en conflit avec ceux de
  NVDA, en particulier tout ce qui utilise Inser ou Verrouillage majuscule.
- Après toute modification de l'interface, rappeler à l'utilisateur ce qu'il
  doit vérifier auditivement : c'est lui seul qui peut valider le rendu
  NVDA.
