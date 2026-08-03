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

- **File d'attente des transferts, favoris de dossiers, reconnexion
  SSH.** Trois idées proposées par l'utilisateur (avec deux autres,
  recherche dans l'historique et aperçu de fichiers, mises de côté
  comme pas urgentes) et prises dans cet ordre : progression + file
  d'attente d'abord (même socle technique), favoris et reconnexion
  ensuite. Le presse-papiers façon WinSCP (Ctrl+C sur un fichier
  distant, Ctrl+V dans l'Explorateur) reste à faire, volontairement en
  dernier — le plus nouveau, à peaufiner à part.

  *File d'attente* (`Transfert`, `PanneauSession.transferts`) : Envoyer
  et Télécharger ne lancent plus un thread jetable chacun, ils déposent
  un `Transfert` dans `self._file_transferts` (`queue.Queue`), traité
  par un fil de travail dédié par session (`_travailleur_transferts`,
  démarré à la création du panneau, seulement pour une session SSH) —
  **un seul transfert à la fois, jamais en parallèle** : le canal SFTP
  partagé de la navigation (`ExecuteurSSH._sftp_persistant`) ne
  supporterait pas des appels concurrents depuis plusieurs threads.
  `ExecuteurSSH.telecharger_dossier` a été changé en conséquence : il
  ouvre maintenant son propre canal SFTP dédié pour toute la durée du
  transfert plutôt que d'emprunter le canal partagé, pour ne pas
  bloquer une action de navigation (renommer, lister...) déclenchée
  pendant qu'un gros fichier est en cours de copie — seul `lister_repertoire`,
  appelé pendant la récursion pour connaître le contenu de chaque
  sous-dossier, reste sur le canal partagé (des appels brefs, contrairement
  à un `get()` qui peut durer). Progression limitée à 10 rafraîchissements
  par seconde (`dernier_envoi`/`time.monotonic()` dans
  `_travailleur_transferts`) : le `callback()` de `sftp.put`/`get` est
  appelé à chaque paquet, sans limite ça aurait inondé le thread principal
  de `CallAfter` et fait réannoncer la ligne à NVDA en continu dans la
  boîte de la file si elle est ouverte. `DialogueFileTransferts` est
  **non modale** (`Show()`, pas `ShowModal()`) : le transfert continue
  en tâche de fond, il faut pouvoir la laisser ouverte et retourner
  naviguer pendant qu'il travaille. `Ctrl+Maj+Q` et l'entrée de menu
  correspondante ne sont *pas* grisées hors mode fichiers, à la
  différence des autres actions du mode fichiers : un transfert lancé
  avant de repasser en terminal continue, la file reste utile à
  consulter dans les deux modes.

  *Favoris* (`ProfilConnexion.favoris`, `ssh.py`) : liste de chemins
  distants par profil, persistée dans `ssh_profiles.json` comme le
  reste du profil. Bug trouvé et corrigé en cours de route :
  `DialogueProfilSSH.profil()` reconstruisait un `ProfilConnexion` tout
  neuf à partir des seuls champs du formulaire (nom, hôte, port...),
  qui n'a pas de champ pour les favoris — modifier un profil existant
  via « Gestion des profils SSH » aurait donc silencieusement effacé
  ses favoris à chaque édition. Corrigé en conservant une référence au
  profil d'origine (`self._profil_existant`) et en reportant ses
  favoris sur le nouveau. `PanneauSession.profil` (nouveau paramètre de
  `__init__`, posé par `_connexion_ssh_reussie`) garde la session reliée
  à son profil pour lire/écrire ses favoris ; `sauvegarder_favoris()`
  recharge `ssh_profiles.json` en entier et n'y modifie que ce profil,
  pour ne pas écraser un changement fait entretemps ailleurs (une autre
  session, ou la boîte de gestion des profils). Utilisables dans les
  deux modes : en mode fichiers `aller_au_favori` recharge la liste, en
  mode terminal c'est un équivalent silencieux de Ctrl+Maj+D déjà
  rempli.

  *Reconnexion* (`ExecuteurSSH.connexion_active`, nouvelle propriété) :
  distincte de `connecte` (qui dit juste qu'un objet client existe) —
  vérifie que `transport.is_active()` répond encore, ce qui permet de
  distinguer une connexion tombée d'une commande qui a simplement
  échoué. Détection dans `PanneauSession._commande_terminee` : un
  `code_retour == -1` (déjà le signal d'un échec au lancement de la
  commande plutôt qu'un vrai code de sortie distant, voir plus haut
  dans ce journal) combiné à `connexion_active` faux déclenche une
  proposition de reconnexion, pas un simple message d'erreur.
  `Fenetre.reconnecter_ssh` réutilise le même onglet et le même
  exécuteur (juste `fermer()` puis `connecter()` à nouveau) plutôt que
  d'ouvrir une nouvelle session : l'historique de blocs de la session
  ne se perd pas. Portée volontairement limitée : la détection est
  réactive (à la prochaine commande), pas un sondage périodique en
  tâche de fond — suffisant pour l'usage décrit, sans la complexité
  d'un minuteur à coordonner avec le reste.

- **File d'attente des transferts, revue après un vrai essai.** Signalée
  trop opaque à l'usage : « en attente » ou « 0 fichiers copiés »
  n'apprenaient pas grand-chose, aucun moyen d'agir dessus à part
  fermer la fenêtre, et un seul fichier vu passer a suffi à douter que
  ça marchait vraiment. Deux manques distincts, corrigés séparément :

  *Visibilité* : `ExecuteurSSH.telecharger_dossier` appelait
  `sur_fichier()` seulement après chaque fichier reçu (juste un
  compteur) — rien n'indiquait ce qui était *en train* de passer. Le
  contrat du callback devient `sur_fichier(nom, termine)`, appelé deux
  fois par fichier (avant et après), sans la limite de fréquence
  utilisée pour la progression en octets d'un fichier seul : un appel
  par fichier reste rare même sur beaucoup de petits fichiers, alors
  que la limite se justifiait pour un appel par *paquet*. `Transfert`
  gagne `fichier_actuel`, réutilisé par `libelle()`.

  *Contrôle* : `ExecuteurSSH._canal_transfert` retient le canal SFTP du
  transfert de fichier actif (`envoyer_fichier`/`recuperer_fichier`/
  `telecharger_dossier`, posé et retiré à l'ouverture/fermeture de leur
  propre canal), et `annuler_transfert()` le ferme depuis l'extérieur —
  même principe que `interrompre()` pour une commande en cours, qui
  fait déjà exactement ça avec `_canal`. `PanneauSession.annuler_transfert`
  distingue deux cas : un transfert encore *en attente* est juste
  marqué `etat="annulé"` (le fil de travail le voit au moment de le
  dépiler et l'ignore, sans jamais l'exécuter) ; un transfert *en
  cours* est marqué pareil puis son canal est fermé, ce qui fait lever
  une exception dans le `sftp.put`/`get` bloquant — rattrapée
  normalement dans `_travailleur_transferts`, qui vérifie `etat ==
  "annulé"` avant de conclure à un vrai échec. Assumé et documenté
  plutôt que caché : annuler un transfert en cours laisse le fichier
  partiel sur place (local ou distant selon le sens), rien ne le
  nettoie automatiquement. `vider_file_transferts()` annule en bloc
  tout ce qui est encore en attente, sans toucher au transfert en cours
  (qui se retire individuellement avec le même bouton Annuler). Boutons
  ajoutés à `DialogueFileTransferts` : Annuler ce transfert, Vider la
  file d'attente.

- **`DialogueFileTransferts` et `vider_file_transferts()` (ci-dessus)
  retirés à leur tour, remplacés par une fenêtre de progression unique.**
  L'utilisateur transfère surtout de petits fichiers, un par un : la
  vue « liste consultable avec en-attente/en-cours/terminé » réglait un
  problème (plusieurs transferts empilés) qu'il n'a pas vraiment, et
  cachait celui qu'il a vraiment (savoir ce qui se passe *là,
  maintenant*, avec un vrai pourcentage et une vitesse). Nouvelle
  classe `DialogueProgression`, non modale et `wx.STAY_ON_TOP` :
  s'ouvre toute seule au premier transfert (`PanneauSession.
  _afficher_fenetre_progression`, appelée depuis `_transfert_demarre`),
  reste ouverte après la fin (dernier état lisible) jusqu'à fermeture
  manuelle, réutilisée pour le transfert suivant si elle est encore
  ouverte. Ne prend jamais le focus toute seule (un transfert peut
  démarrer pendant qu'on tape ailleurs). Le mécanisme de file interne
  (`queue.Queue`, un seul transfert à la fois, canal SFTP partagé
  oblige) n'a pas changé, seule la vue disparaît — `Ctrl+Maj+Q` sert
  maintenant à ramener au premier plan la fenêtre de progression (fermée
  par erreur, ou passée derrière autre chose), pas à ouvrir une liste.

  Vitesse et détection de changement de fichier reposent sur la même
  info : `ExecuteurSSH.telecharger_dossier` a changé de contrat de
  callback une seconde fois, de `sur_fichier(nom, termine)` à
  `sur_fichier(nom, fait, total)` — le même que pour un fichier seul,
  avec le nom en plus, réutilisant directement le `callback` que
  `sftp.get()` appelle déjà pendant la copie (pas seulement à la fin).
  Un changement de nom d'un appel à l'autre signale qu'on est passé au
  fichier suivant, sans qu'`ExecuteurSSH` ait besoin de le dire
  explicitement. La vitesse elle-même se mesure côté appelant
  (`_travailleur_transferts`) entre deux appels sur le *même* fichier
  (delta d'octets / delta de temps, remesurée au moins toutes les
  0,5 s) — `ExecuteurSSH` reste un simple relais de ce que Paramiko
  fournit, sans logique de débit à lui.

  Deux rythmes de rafraîchissement bien séparés, pas confondus comme
  avant : le texte de la fenêtre suit à 5/s (fluide, jamais senti en
  retard), l'annonce vocale est bien plus rare (2 s), sauf au
  changement de fichier qui passe toujours immédiatement — c'est le
  moment le plus informatif, il ne doit pas attendre son tour.

- **`DialogueProgression` revue une seconde fois, non modale à
  modale, après un vrai essai de la version ci-dessus.** Signalée
  inutilisable telle quelle : le transfert démarrait bien, mais rien
  n'amenait la fenêtre à portée sans passer par Ctrl+Maj+Q, et pendant
  ce temps l'annonce vocale périodique parlait toute seule, coupée
  n'importe quand, sans rien dire d'exploitable une fois interrompue.
  Préférence exprimée sans ambiguïté : une fenêtre au premier plan,
  progression et bouton Annuler, qui prend le pas sur le reste — tant
  pis si ça empêche de continuer à travailler dans LazyShell pendant un
  transfert, plus clair ainsi qu'une fenêtre discrète censée ne pas
  déranger. `DialogueProgression` est donc devenue une vraie boîte
  modale (`ShowModal()` au lieu de `Show()` + `wx.STAY_ON_TOP`) :
  `_afficher_fenetre_progression` bloque jusqu'à sa fermeture, ce qui
  n'arrive, par construction, qu'une fois le transfert dans un état
  final. Elle refuse de se fermer avant (Échap et la croix redirigés
  vers un avertissement vocal, `_sur_fermeture`) : c'est le seul moyen
  de suivre ou d'annuler le transfert pendant qu'il tourne, la faire
  disparaître par erreur laisserait le transfert continuer sans plus
  aucune prise dessus. Le bouton Fermer reste désactivé tant que l'état
  n'est pas final. Le focus modal fait lire l'état par NVDA normalement,
  à l'ouverture comme à chaque `rafraichir()` : plus besoin d'un
  mécanisme d'annonce séparé, la logique de rythme vocal (5/s texte,
  2 s parole, immédiat au changement de fichier) décrite ci-dessus n'a
  pas changé. **`Ctrl+Maj+Q` et `Fenetre.voir_file_transferts()` sont
  retirés entièrement** (raccourci, liaison clavier, entrée de menu
  Session) : sa seule raison d'être — ramener une fenêtre non modale
  égarée — disparaît avec le passage en modale, la fenêtre étant
  désormais toujours au premier plan par construction tant qu'un
  transfert est en cours.

- **Favoris de dossiers distants, revus après un vrai essai : nommés,
  sur le modèle des commandes enregistrées.** La première version
  (`ProfilConnexion.favoris: list[str]`, chemin brut affiché tel quel)
  jugée insuffisante — même logique que `CommandeEnregistree` déjà en
  place pour les commandes : un chemin distant est rarement parlant tel
  quel, autant lui donner un nom explicite. Nouvelle dataclasse
  `FavoriDossier(nom, chemin)` dans `ssh.py`, `ProfilConnexion.favoris`
  devient `list[FavoriDossier]`. `DialogueFavoriDossier` (`lazyshell.py`)
  reprend exactement la forme de `DialogueCommandeEnregistree` — deux
  champs, bouton Enregistrer qui refuse un nom ou un chemin vide.
  `DialogueFavoris` affiche `"nom — chemin"` dans la liste et gagne un
  bouton Modifier en plus d'Ajouter/Supprimer/Aller/Fermer. La
  commodité « enregistrer le dossier courant comme favori », demandée
  explicitement à garder, survit sous la forme d'un bouton « Ajouter le
  dossier courant… » qui pré-remplit le champ chemin avec
  `panneau.repertoire` et laisse seulement le nom à saisir.

  Bug de compatibilité trouvé et corrigé avant qu'il n'atteigne
  l'utilisateur : le vrai `ssh_profiles.json` contenait déjà un favori
  dans l'ancien format (une simple chaîne), ce que `FavoriDossier(**f)`
  ne sait pas déballer (on ne peut pas faire `**` sur une chaîne) —
  `charger_profils()` aurait planté au tout premier lancement après
  cette modification, sur les données réelles de l'utilisateur, pas
  seulement dans un scénario de test. Repéré en vérifiant par prudence,
  en lecture seule, que le fichier réel se chargeait toujours après le
  changement — avant que l'utilisateur n'ait eu l'occasion de le
  constater lui-même. Corrigé par `_favori_depuis_donnees()` : une
  chaîne (ancien format) devient un `FavoriDossier` dont le nom est le
  chemin lui-même, modifiable ensuite par Modifier ; un dict (format
  actuel) passe tel quel à `FavoriDossier(**d)`. Vérifié contre le vrai
  fichier (lecture seule, jamais réécrit pendant le test) : le favori
  existant du profil « VPS » se charge sans erreur.

- **Bug réel remonté en usage (mode fichiers) : connexion SSH tombée
  après un Retour arrière, puis plantage en boucle.** Signalé avec le
  journal complet (`lazyshell.log`) : un aller-retour dans l'arborescence
  (Retour arrière vers la racine) a fait tomber la connexion SSH
  (`paramiko.ssh_exception.SSHException: Server connection dropped`),
  suivi d'un `Listage SFTP échoué`, puis d'une boîte d'erreur répétée
  cinq fois de suite : `RuntimeError: wrapped C/C++ object of type
  ListBox has been deleted`. Deux causes distinctes, l'une menant à
  l'autre :

  *Cause racine* : `ExecuteurSSH._sftp_persistant()` (le canal SFTP
  partagé de la navigation) n'a jamais été protégé contre des appels
  concurrents — seule la référence `self._sftp` elle-même l'était
  brièvement (`self._verrou`), pas les vraies opérations réseau
  (`normalize`, `listdir_attr`, `stat`, `rename`, `remove`, `mkdir`) qui
  s'exécutaient hors de tout verrou. `charger_dossier_sftp`,
  `renommer_entree_sftp`, `supprimer_entree_sftp` et
  `creer_dossier_sftp` (`lazyshell.py`) lancent chacune un thread neuf à
  chaque appel, sans aucune protection contre le chevauchement — à la
  différence de `executer()` pour une commande, qui refuse d'en lancer
  une deuxième tant que `en_cours` est vrai. Plusieurs Retour arrière
  pressés coup sur coup (avant que le listage précédent soit revenu)
  suffisent à faire partir deux threads en parallèle sur le même canal
  SFTP — non thread-safe côté Paramiko — ce qui corrompt le flux du
  protocole et fait tomber la connexion entière, pas juste l'opération
  en cours. Corrigé par un second verrou dans `ExecuteurSSH.__init__`,
  `self._verrou_navigation` (un `RLock`, pas un `Lock` : `supprimer_dossier`
  s'appelle elle-même récursivement sur le même thread), qui enveloppe
  maintenant le corps entier de `chemin_absolu`, `lister_repertoire`,
  `renommer`, `supprimer_fichier`, `supprimer_dossier` et
  `creer_dossier` — plus seulement la lecture de la référence au canal.
  Des appels concurrents s'exécutent donc maintenant en séquence plutôt
  qu'en parallèle : plus lent en cas de rafale de touches, mais plus
  jamais destructeur. `self._verrou` (bookkeeping de `_client`/`_sftp`)
  reste séparé pour ne pas risquer un blocage mutuel avec ce nouveau
  verrou.

  *Conséquence en cascade* : une fois la connexion tombée, les threads
  de listage encore en vol ont fini par échouer et ont posté leur
  callback d'échec par `wx.CallAfter` — mais entre-temps, l'onglet de
  session avait déjà été fermé (Ctrl+W), détruisant `liste_fichiers`
  (le `wx.ListBox`) avec le reste du panneau. `wx.CallAfter` exécute son
  rappel plus tard, sans savoir que sa cible n'existe plus : appeler une
  méthode sur un contrôle wx détruit lève ce `RuntimeError`, non
  rattrapé, d'où la boîte d'erreur générique répétée une fois par
  callback en attente. Corrigé en ajoutant `if not self: return` (même
  idiome déjà utilisé dans `DialogueProgression.rafraichir()`) en tête
  de `charger_dossier_sftp`, `_dossier_sftp_charge` et
  `_echec_action_sftp` — les trois points d'entrée de `wx.CallAfter`
  côté navigation SFTP qui touchent directement `liste_fichiers` ou
  affichent une boîte de dialogue. Le seul appel qui passait une méthode
  de contrôle wx directement comme cible de `wx.CallAfter`
  (`wx.CallAfter(self.liste_fichiers.Set, [])`, sans aucun moyen d'y
  glisser un garde) a été remplacé par un petit relais dédié,
  `_vider_liste_fichiers_sftp()`, qui porte le même garde. Les callbacks
  de transfert (`_transfert_demarre` et consorts) n'ont pas eu besoin du
  même traitement : la fenêtre de progression étant modale (voir
  ci-dessus), l'utilisateur ne peut pas fermer l'onglet pendant qu'un
  transfert est en cours, cette course-là ne peut donc pas se produire
  de ce côté. Vérifié par un script jetable (deux threads factices
  frappant un faux canal SFTP en boucle, plus un vrai `PanneauSession`
  détruit puis sollicité directement) : plus de chevauchement détecté,
  plus d'exception levée sur un panneau détruit.

- **`DialogueProgression` revue une troisième fois, après un vrai essai
  de la version modale.** La fenêtre restait bien au premier plan
  (l'objectif de la revue précédente était atteint), mais deux défauts
  concrets remontés à l'usage :

  *Annonce vocale répétée en boucle* : `_maj_progression_transfert`
  parlait toutes les 2 secondes (ou à chaque changement de fichier),
  empêchant de consulter quoi que ce soit d'autre — signalé comme
  gênant, avec la même zone de texte lue en braille jugée suffisante à
  elle seule. Le paramètre `annoncer` de `progresser()`/
  `_maj_progression_transfert` (calcul de `dernier_vocal`, `annoncer =
  nouveau_fichier or maintenant - dernier_vocal >= 2.0`) est retiré
  entièrement — plus aucune annonce vocale pendant la progression, la
  mise à jour de `self.champ` (via `_rafraichir_transfert`) reste seule
  responsable de refléter l'avancement, y compris en braille (qui suit
  le contenu du champ focalisé sans qu'il soit besoin de le faire dire).
  Les annonces ponctuelles (démarrage dans `_transfert_demarre`, fin
  dans `_transfert_reussi`/`_transfert_echoue`/`_transfert_annule_confirme`)
  sont conservées : ce sont des évènements uniques, pas un flux continu,
  la distinction faite par l'utilisateur entre les deux est exactement
  celle appliquée ici.

  *Annuler n'importe pas la fenêtre* : cliquer sur Annuler arrêtait bien
  le transfert, mais laissait la fenêtre ouverte — il fallait ensuite
  cliquer sur Fermer séparément, une manipulation en trop pour un geste
  déjà délibéré. `DialogueProgression._sur_annuler` appelle maintenant
  `self.EndModal(wx.ID_CANCEL)` juste après `panneau.annuler_transfert(...)`,
  fermant la fenêtre dans le même geste. Le garde de `_sur_fermeture`
  (refus de fermer par Échap/croix tant que le transfert n'est pas dans
  un état final) reste inchangé et ne s'applique toujours qu'à ces deux
  chemins-là — Annuler passe désormais outre délibérément, puisqu'il
  vient justement de faire passer le transfert à l'état « annulé ».
  Vérifié par un script jetable (transfert factice démarré, Annuler
  déclenché via un `wx.CallLater` pendant que `ShowModal()` bloque) :
  la fenêtre se ferme bien toute seule, une seule annonce vocale
  enregistrée avant le clic (celle du démarrage, aucune répétition).

- **Bug réel remonté en usage : Ctrl+D seul déclenchait « Changer de
  répertoire », alors que seul Ctrl+Maj+D devrait le faire.** Repéré
  avec un test isolé sur `wx.MenuItem.GetAccel()` : le texte
  `"...\tCtrl+Maj+D"` utilisé pour l'affichage produit, une fois passé
  au parseur d'accélérateur natif de wx, un vrai `AcceleratorEntry`
  **« Ctrl+D »** — pas rien, comme on l'aurait attendu. wx ignore
  silencieusement tout token de modificateur qu'il ne reconnaît pas
  (« Maj », mot français) au lieu d'échouer complètement : il garde les
  modificateurs reconnus (« Ctrl ») et la touche finale (« D »), et
  enregistre ce résultat partiel comme accélérateur fonctionnel de
  l'item de menu — indépendamment de notre propre gestion manuelle des
  raccourcis (`sur_touche_globale`, `EVT_CHAR_HOOK`), documentée comme
  seule source de vérité (voir décision plus haut), mais qui ne
  protège pas contre CE mécanisme parallèle : wx construit sa table
  d'accélérateurs native automatiquement à partir du texte de tout item
  de menu dès `SetMenuBar()`, qu'on le veuille ou non.

  La décision d'origine sur la gestion manuelle des raccourcis avait
  bien identifié que wx ne reconnaît pas les noms de touches français
  dans ce texte, mais supposait qu'un modificateur non reconnu faisait
  simplement échouer tout le parsing (comme c'est effectivement le cas
  pour `"Alt+Haut"`/`"Alt+Bas"`, vérifié au passage : aucun accélérateur
  n'en résulte, parce que c'est la touche finale, pas juste un
  modificateur, qui n'est pas reconnue). Ce n'est vrai que si le token
  non reconnu n'est pas un modificateur en tête de liste — « Maj » en
  affecte tous, silencieusement.

  Bug systémique, pas isolé à Ctrl+D : **les 19 items de menu utilisant
  `\tCtrl+Maj+X`** produisaient chacun un accélérateur fantôme
  `Ctrl+X` — dont certains sur des lettres à fort risque de collision
  avec l'édition de texte standard (`Ctrl+C`, `Ctrl+A` notamment).
  Corrigé par un remplacement mécanique dans `lazyshell.py` : la
  séquence `"\tCtrl+Maj+"` devient `"  Ctrl+Maj+"` (deux espaces) dans
  tous ces libellés — le caractère de tabulation est ce qui déclenche
  le parsing d'accélérateur côté wx, un simple espace ne le déclenche
  pas, et le texte reste identique à l'oreille pour NVDA. Conséquence
  visuelle mineure assumée : ces libellés perdent l'alignement en
  colonne du raccourci que la tabulation donnait aux items encore
  légitimement simples (`Ctrl+T`, `Ctrl+W`, `Ctrl+B`, `Alt+F4`,
  volontairement non touchés, aucun mot français dedans). Vérifié à la
  fois par un test isolé sur chaque libellé et par une inspection du
  vrai menu construit par `Fenetre._construire_menus` : plus aucun
  accélérateur contenant « Maj » dans son libellé ne produit
  d'`AcceleratorEntry`, les raccourcis simples légitimes fonctionnent
  toujours normalement.

- **Pourcentage déplacé dans le titre de `DialogueProgression`, sous la
  forme « x% nom ».** Demandé après un vrai essai de la fenêtre modale :
  le pourcentage vivait dans la zone de texte, redondant avec l'idée
  de pouvoir le lire d'un coup d'œil (ou d'un accès rapide NVDA au
  titre de fenêtre) sans avoir à parcourir toute la ligne. Nouvelle
  méthode `Transfert.titre_fenetre()` : `"{pourcentage}% {nom}"`
  pendant un transfert en cours une fois le premier pourcentage connu,
  sinon un état en toutes lettres (« En attente : nom », « Terminé :
  nom »...). `DialogueProgression.rafraichir()` appelle `self.SetTitle(...)`
  en plus de mettre à jour `self.champ`. `Transfert.libelle()` (le
  texte de `self.champ`) ne répète plus le pourcentage, seulement
  l'état et la vitesse — même logique que le retrait de l'annonce
  vocale périodique juste avant : une information ne devrait vivre
  qu'à un seul endroit dans cette fenêtre.

- **Menu Commandes grisé en mode navigation.** Idée notée de longue
  date (voir ancien contenu de la section « Idées à reprendre plus
  tard » ci-dessous) : ses trois items (Utiliser une commande
  enregistrée, Enregistrer la commande actuelle, Gérer les commandes
  enregistrées) ne servent à rien hors du mode terminal, il n'y a pas
  de saisie à retaper ou enregistrer en mode navigation. Même mécanisme
  que le grisage déjà en place pour les actions de fichiers dans le
  menu Session, condition inversée : `Fenetre.items_commandes` (les
  trois `wx.MenuItem`), grisés/dégrisés par
  `_synchroniser_menu_navigation()` (renommée dans la foulée, voir
  point suivant) aux côtés de `items_action_fichiers`.

- **Renommage complet de « mode fichiers » en « mode navigation ».**
  Confirmé explicitement avec l'utilisateur : renommage complet
  (identifiants internes inclus), pas seulement l'habillage visible —
  l'ancienne note listait cette ambiguïté comme à trancher avant de s'y
  mettre. `PanneauSession.mode_sftp` devient `mode_navigation` (et donc
  `basculer_mode_sftp` devient `basculer_mode_navigation`, dans
  `PanneauSession` comme dans `Fenetre`, par simple inclusion du même
  remplacement) ; `Fenetre.item_mode_fichiers` devient
  `item_mode_navigation` ; `Fenetre._synchroniser_menu_fichiers` devient
  `_synchroniser_menu_navigation`. Libellé de menu « Basculer en mode
  &fichiers » devient « Basculer en mode &navigation » (mnémonique « n »,
  vérifié libre dans le menu Affichage ; « Basculer en mode &terminal »,
  affiché en alternance selon l'état, ne change pas). Annonce vocale
  « Mode fichiers. » devient « Mode navigation. ». Tous les commentaires
  et messages utilisateur contenant l'expression « mode fichiers »
  (dont les messages d'erreur « Cette action nécessite le mode fichiers
  (Ctrl+Maj+F). ») sont repassés en « mode navigation », de même que
  README et `docs/index.html` (bullet de fonctionnalités, tableau de
  raccourcis, section dédiée — dont l'ancre `#fichiers`, renommée
  `#navigation`). Ce qui n'a **pas** été renommé, volontairement :
  les méthodes et identifiants propres aux actions SFTP elles-mêmes
  (`charger_dossier_sftp`, `renommer_entree_sftp`,
  `envoyer_fichier_sftp`, `_entrees_sftp`, `liste_fichiers`,
  `etiquette_fichiers`, `items_action_fichiers`...) — elles décrivent
  des actions sur des fichiers via SFTP, un concept distinct du nom du
  mode d'affichage qui les rend disponibles, et restent donc
  légitimement nommées ainsi. Le raccourci clavier reste Ctrl+Maj+F
  dans tous les cas : seul le nom du mode change, pas la touche.
  Vérifié par un script jetable (bascule réelle via
  `Fenetre.basculer_mode_navigation()`, avec un exécuteur SSH factice) :
  l'attribut `mode_sftp` n'existe plus du tout sur `PanneauSession`,
  `mode_navigation` bascule correctement, le libellé du menu et
  l'annonce vocale suivent l'état à chaque bascule.

- **Libellé du menu Session « &Favoris… » renommé en « &Dossiers
  favoris… ».** Demande ponctuelle après un essai complet de la
  session : plus explicite pour un item de menu lu isolément par NVDA,
  sans contexte de phrase autour. Changement limité au libellé de ce
  seul item (`m_session.Append`) — le titre de `DialogueFavoris`, son
  étiquette interne et le texte de la boîte d'avertissement associée
  n'ont pas été touchés, non demandés.

- **Version 1.4.0.** Regroupe tout ce qui précède depuis la 1.3.0:
  correctifs SSH/SFTP (verrou de navigation, garde panneau détruit,
  accélérateurs Ctrl+Maj fantômes), fenêtre de transfert modale revue
  deux fois (silence vocal, Annuler qui ferme, pourcentage dans le
  titre), favoris nommés, menu Commandes grisé en mode navigation,
  renommage complet « mode fichiers » -> « mode navigation », et ce
  dernier changement de libellé.

- **Menu Aide : lien vers le dépôt GitHub, et « À propos » complété
  avec l'auteur.** Nouvel item « Dépôt GitHub » (`Fenetre.
  ouvrir_depot_github`, constante `URL_DEPOT`) qui ouvre la page du
  dépôt dans le navigateur par défaut, sur le même principe que
  `webbrowser.open` déjà utilisé pour la vérification des mises à
  jour. La boîte « À propos » affiche en plus l'auteur (Sof,
  hellosof@gmail.com) et ce même lien. README corrigé au passage
  (quelques fautes de frappe et d'accord) avec une section Auteur
  ajoutée avant la licence.

- **Presse-papiers façon WinSCP (Ctrl+C/Ctrl+V) en mode navigation :
  tenté, puis abandonné et revenu en arrière (`git revert`) avant
  toute vérification réelle.** Reprenait l'idée notée de longue date
  ci-dessus (favoris/reconnexion/file d'attente), en remplacement des
  Ctrl+Maj+E/Ctrl+Maj+T existants : Ctrl+C sur un élément distant le
  téléchargeait vers un dossier temporaire puis le posait sur le
  presse-papiers Windows (`wx.FileDataObject`, format CF_HDROP) pour
  un Ctrl+V dans l'Explorateur ; Ctrl+V dans la liste envoyait les
  fichiers du presse-papiers vers le répertoire distant affiché.

  Problème identifié avant toute vérification au clavier, à la seule
  réflexion sur l'usage réel : CF_HDROP exige que le fichier existe
  *déjà* sur le disque local au moment où l'Explorateur colle — ce
  format ne transporte qu'une liste de chemins, pas un contenu à la
  demande. Le téléchargement devait donc forcément démarrer dès le
  Ctrl+C, avant même de savoir si et où l'utilisateur allait coller,
  avec une fenêtre modale bloquante qui s'ouvrait immédiatement.
  Un vrai copier-coller doit être instantané ; celui-ci obligeait à
  copier, basculer vers l'Explorateur, puis attendre la fin du
  transfert avant de pouvoir coller — un aller-retour bien plus lourd
  que l'ancien Ctrl+Maj+T (boîte de dialogue « Enregistrer sous »,
  mais sans cette attente imposée entre deux fenêtres).

  Le mécanisme qui permettrait un vrai différé (WinSCP, pièces jointes
  Outlook) existe (formats COM `FileGroupDescriptorW` +
  `FileContents`, où l'Explorateur ne réclame le contenu qu'au moment
  du collage via un `IDataObject.GetData` différé), mais demande un
  vrai objet COM écrit à la main (`pywin32`/`pythoncom`, `wx.
  FileDataObject` ne fait que du CF_HDROP), avec le thread de
  l'Explorateur qui resterait bloqué en attendant la réponse — un
  réseau SSH lent y afficherait « Ne répond pas ». Jugé disproportionné
  et fragile pour ce projet, sans précédent comparable (seul le canal
  JAWS partage cette prudence face à un mécanisme non vérifié en
  conditions réelles). Les anciens Ctrl+Maj+E (Envoyer) / Ctrl+Maj+T
  (Télécharger), qui fonctionnaient bien, sont donc restés en place
  tels quels — voir leur description plus haut dans ce journal.

- **Recherche récursive de fichiers en mode navigation, Ctrl+Maj+G.**
  Nouvelle boîte `DialogueRechercheFichiers` (motif tapé, bouton
  Rechercher, liste de résultats avec chemin complet, bouton « Aller au
  résultat »). Correspondance simple sur une sous-chaîne du nom,
  insensible à la casse — pas de glob ni de regex, cohérent avec la
  simplicité déjà en place ailleurs (listing amélioré, etc.).

  Côté SSH, `ExecuteurSSH.rechercher_fichiers`/
  `_rechercher_fichiers_recursif` (`ssh.py`) parcourt récursivement en
  s'appuyant sur `lister_repertoire` déjà existant (même schéma que
  `supprimer_dossier`/`_telecharger_dossier_recursif` pour la
  récursivité côté client, le protocole SFTP n'offrant rien de
  récursif). Un lien vers un dossier n'est jamais suivi : rien ne
  garantit qu'il ne se referme pas sur un de ses propres ancêtres, ce
  qui boucherait indéfiniment. Un dossier illisible en cours de route
  (droits refusés) est ignoré (`except OSError`, journalisé) plutôt que
  d'interrompre toute la recherche pour une seule branche
  inaccessible.

  Le parcours tourne dans un thread de fond, comme toute E/S réseau
  dans cette appli (contrainte non négociable, voir en tête de ce
  fichier) : `DialogueRechercheFichiers` lance un thread à chaque clic
  sur Rechercher, remonte l'état par `wx.CallAfter`. Le statut
  (« N dossiers explorés ») se met à jour en direct pendant la
  recherche, limité à 5 rafraîchissements par seconde (même principe
  que la fenêtre de progression des transferts, pour ne pas inonder le
  thread principal sur une arborescence à beaucoup de petits dossiers)
  — les résultats eux-mêmes ne s'affichent qu'à la fin, puisque
  `rechercher_fichiers` ne les fait remonter qu'une fois le parcours
  entièrement terminé, pas au fil de l'eau. Annulation par
  `threading.Event`, vérifié entre chaque dossier (`doit_annuler`
  passé jusqu'au fond de la récursion) : les résultats déjà trouvés
  sont conservés, une recherche annulée n'est pas un échec.

  « Aller au résultat » (`PanneauSession.aller_a_resultat_recherche`)
  ouvre directement le dossier trouvé si c'est un dossier, ou son
  dossier parent avec le fichier sélectionné sinon — contrairement à
  `aller_au_favori`, qui ne pointe toujours que sur un dossier, un
  résultat de recherche peut être un fichier à n'importe quelle
  profondeur. Nécessite un nouveau paramètre optionnel
  `nom_a_selectionner` sur `charger_dossier_sftp`/`_dossier_sftp_charge`,
  pour sélectionner une entrée précise après chargement au lieu de la
  première par défaut (repli sur la première si le nom n'est plus
  présent — supprimé ou renommé entre-temps).

  Raccourci Ctrl+Maj+G (lettre libre, sans mnémonique évident en
  français — la plupart des lettres associées à « recherche »,
  « chercher », « trouver », « fichier » étaient déjà prises par
  d'autres raccourcis de ce même menu). Grisé comme Nouveau
  dossier/Renommer/Supprimer (`items_action_fichiers`) : pas de
  collision possible avec un usage en mode terminal (contrairement à
  l'expérience Ctrl+C/Ctrl+V abandonnée juste au-dessus), donc pas
  besoin d'insertion/retrait dynamique du menu.

  Vérifié par des reproductions isolées jetables (aucune connexion
  SSH réelle) : la logique de parcours récursif sur une arborescence
  factice (sensibilité à la casse, lien non suivi, dossier illisible
  ignoré, annulation immédiate et en cours de route) ; le grisage du
  menu et la construction de la boîte ; le flux complet thread de
  fond + `wx.CallAfter` + activation du bouton « Aller au résultat » ;
  la sélection d'une entrée précise par nom après chargement d'un
  dossier (`_dossier_sftp_charge`). Reste à vérifier en conditions
  réelles avec NVDA : une recherche sur un vrai serveur SSH (y compris
  un dossier avec des droits refusés dedans, et une annulation en
  cours de route), l'annonce vocale à chaque étape, et la navigation
  effective vers un résultat trouvé en profondeur.

- **Recherche de fichiers, revue après un retour direct sur la
  première version (jamais testée en conditions réelles) : trop
  lente, aucun résultat visible avant la toute fin, Annuler ne
  fermait pas la boîte.** Trois défauts distincts, corrigés ensemble :

  *Lenteur* : `ExecuteurSSH.rechercher_fichiers` parcourait
  l'arborescence dossier par dossier via SFTP (`lister_repertoire`),
  soit un aller-retour réseau par dossier exploré — inévitablement
  lent sur une arborescence profonde dès qu'il y a de la latence.
  Remplacé entièrement par la commande `find` distante, exécutée sur
  un canal `exec_command` dédié (comme `executer()`, mais sans repasser
  par toute sa logique propre au terminal — pas de détection d'invite
  ni de troncature de sortie, hors sujet ici) : un seul aller-retour
  réseau, dont la sortie (`find <racine> -iname '*motif*' -printf
  '%y\t%s\t%p\n' 2>/dev/null`) arrive en flux, lu ligne par ligne au
  fil de l'eau plutôt qu'attendue en bloc. Suppose un find GNU
  (coreutils) sur le serveur, déjà une hypothèse existante de cette
  appli pour le listing amélioré (`reecrire_listing`, qui utilise déjà
  `find -printf`) — pas une nouvelle dépendance. `-iname` gère
  nativement l'insensibilité à la casse et ignore par défaut les liens
  symboliques (jamais suivis sans `-L`) : plus de parcours récursif ni
  de garde anti-boucle à écrire à la main côté Python. Conséquence
  acceptée : un lien vers un dossier remonte comme un simple lien, pas
  comme un dossier (sa cible n'est jamais résolue, pour ne pas
  ralentir la recherche pour ce cas marginal) — « Aller au résultat »
  sur un tel lien ouvre donc son dossier parent avec le lien
  sélectionné, pas le contenu du dossier ciblé. Un dossier illisible en
  cours de route (droits refusés) est ignoré par find lui-même (stderr
  vers `/dev/null`), qui continue sur le reste — aucune gestion
  particulière à écrire côté appelant, contrairement à l'ancienne
  version qui devait explicitement rattraper l'exception dossier par
  dossier. Le motif tapé passe par `shlex.quote()` avant d'entrer dans
  la commande shell distante : nécessaire puisqu'il vient directement
  de la saisie utilisateur et atterrit dans une commande exécutée par
  un vrai shell distant — sans cet échappement, un motif contenant par
  exemple `; rm -rf /` s'exécuterait tel quel côté serveur. Vérifié par
  un test isolé avec un faux `find` qui tente cette injection
  précise : le motif dangereux ressort bien comme un seul argument
  shell entre quotes, jamais comme une commande séparée.

  Annulation revue en conséquence : `ExecuteurSSH.annuler_recherche()`
  ferme le canal `exec_command` depuis l'extérieur — même principe
  qu'`annuler_transfert()` pour un transfert de fichier — ce qui
  débloque le `recv()` en cours côté thread de fond. Nouvel attribut
  `_canal_recherche`, à côté de `_canal_transfert` (même bookkeeping,
  remis à `None` dans `fermer()`).

  *Aucun résultat avant la fin* : `rechercher_fichiers` ne renvoyait
  qu'une liste complète, une fois le parcours entièrement terminé.
  Nouveau contrat : `sur_resultat(chemin, dossier, lien, taille)`
  appelé pour chaque ligne au fil de l'eau. Côté
  `DialogueRechercheFichiers`, les résultats passent par une
  `queue.Queue` (remplie depuis le thread de fond) vidée par un
  `wx.Timer` démarré à chaque recherche (150 ms) plutôt qu'un
  `wx.CallAfter` par résultat individuel — plus réactif qu'un
  `CallAfter` par résultat sur une arborescence à beaucoup de
  correspondances, sans inonder le thread principal. Le focus se
  déplace sur la liste de résultats dès le lancement de la recherche
  (plus à l'ouverture de la boîte ni en fin de recherche) : c'est elle
  qui se remplit en direct, plus utile à suivre que de rester sur le
  bouton Rechercher pendant que ça travaille. Le statut affiche
  maintenant le nombre de résultats trouvés jusqu'ici et le temps
  écoulé (plutôt que « N dossiers explorés », qui n'a plus de sens
  avec `find` : il n'y a plus de notion de dossier visité un par un
  côté appelant) — mis à jour à chaque nouveau résultat, ou au moins
  chaque seconde pour rester un signe de vie même sur une recherche
  sans aucune correspondance pendant un long moment.

  *Annuler ne fermait pas la boîte* : il ne faisait qu'arrêter la
  recherche, laissant la boîte ouverte — il fallait ensuite cliquer
  sur Fermer séparément. Corrigé sur le même principe que
  `DialogueProgression` pour un transfert : le bouton Annuler appelle
  `EndModal(wx.ID_CANCEL)` juste après avoir demandé l'annulation,
  fermant la boîte dans le même geste (le focus revient alors à la
  liste de navigation principale, déjà géré par
  `Fenetre.rechercher_fichiers_sftp` après `ShowModal()`). Échap et la
  croix, eux, refusent toujours de fermer tant qu'une recherche est en
  cours — même logique que `DialogueProgression`, Annuler reste le
  seul moyen explicite de l'interrompre.

  Corrigé au passage, repéré à la seule lecture du code (jamais
  constaté à l'usage, mais un bug plausible de ce genre de boîte) : le
  focus posé sur le champ de motif directement dans `__init__`, avant
  l'affichage réel de la boîte par `ShowModal()`, risque de ne pas
  « tenir » — Windows repositionne parfois le focus une fois la boîte
  devenue visible, un piège wx déjà connu. Posé maintenant via
  `wx.CallAfter(self.champ_motif.SetFocus)`.

  Vérifié par des reproductions isolées jetables (toujours sans
  connexion SSH réelle) : parsing du flux `find` avec des morceaux
  reçus n'importe où, y compris coupés en plein milieu d'une ligne
  (robustesse du tampon inter-paquets) ; échappement d'un motif
  contenant une tentative d'injection shell ; annulation qui interrompt
  bien un flux en cours en moins d'une seconde plutôt que d'attendre
  la fin ; et, côté boîte de dialogue, un vrai `wx.MainLoop()` borné
  (`wx.CallLater`) montrant des résultats déjà visibles dans la liste
  pendant que la recherche tourne encore, puis un Annuler qui ferme
  bien la boîte dans le même geste.

  Vérifié ensuite en conditions réelles avec NVDA sur un vrai serveur :
  focus correct (champ de motif à l'ouverture, liste de résultats dès
  Entrée pressée), et vitesse jugée bonne (16 résultats en quelques
  secondes) — plus long attendu sur une arborescence à beaucoup de
  fichiers, sans que ça ait été chiffré précisément.

- **Recherche de fichiers : Échap ferme la boîte même pendant une
  recherche en cours, malgré le blocage codé juste au-dessus —
  constaté à l'usage, et volontairement laissé ainsi.** Cause :
  Échap déclenche la fermeture native de wx pour le bouton
  `id=wx.ID_CANCEL` directement (`EndModal(wx.ID_CANCEL)` interne),
  sans jamais passer par `EVT_CLOSE` ni `EVT_BUTTON` — le blocage
  précédent dans `_sur_fermeture` ne pouvait donc pas s'appliquer à
  Échap, seulement au bouton Fermer et à la croix. Plutôt que de
  chercher à bloquer Échap aussi (un hook `EVT_CHAR_HOOK` en
  intercepterait la touche, mais irait à l'encontre de ce qui a été
  expérimenté et accepté), `_sur_fermeture` est simplifiée pour ne
  plus jamais refuser de fermer : Fermer, la croix et Échap ferment
  tous la boîte sans condition désormais, une recherche encore en
  cours étant simplement annulée au passage
  (`_annuler_recherche_en_cours`, factorisée, appelée aussi depuis un
  nouveau hook `EVT_CHAR_HOOK` dédié à Échap — qui ne fait qu'annuler
  puis `evt.Skip()`, jamais `EndModal` lui-même, pour ne pas doubler
  avec la fermeture native qui suit). Différent de
  `DialogueProgression`, qui refuse toujours de fermer par Échap/croix
  pendant un transfert : cette boîte-ci n'a pas cette même contrainte
  vérifiée à l'usage, pas de raison de lui imposer la même rigidité.

- **Version 1.5.0.** Regroupe tout ce qui précède depuis la 1.4.0 :
  lien vers le dépôt GitHub et auteur dans le menu Aide/« À propos »,
  corrections d'orthographe du README, et la nouvelle recherche
  récursive de fichiers en mode navigation (Ctrl+Maj+G), rapide (`find`
  distant, un seul aller-retour réseau) et avec résultats affichés en
  temps réel. (L'essai puis l'abandon du presse-papiers façon WinSCP
  pour Ctrl+C/Ctrl+V n'apparaît pas ici : revenu en arrière avant
  publication, aucun effet net pour l'utilisateur — voir plus haut dans
  ce journal pour le détail.)

- **Boutons Monter/Descendre pour réordonner, et touche Suppr pour
  supprimer, dans les commandes enregistrées, les dossiers favoris et le
  gestionnaire de profils SSH.** Demandé par l'utilisateur. Monter/Descendre
  (`_sur_monter`/`_sur_descendre` dans `DialogueGestionCommandes` et
  `DialogueFavoris`, mnémoniques `o`/`D` pour ne pas entrer en collision
  avec Modifier/Nouvelle/Supprimer/Fermer) échangent l'élément sélectionné
  avec son voisin, la sélection suit l'élément déplacé, l'ordre est
  persisté normalement (`_rafraichir()` appelle déjà
  `enregistrer_commandes`/`sauvegarder_favoris`). Suppr agit comme le
  bouton Supprimer (même boîte de confirmation) dans ces deux dialogues et
  dans `DialogueGestionProfils` (celui-ci sans réordonnancement, non
  demandé). Implémenté via `EVT_CHAR_HOOK` sur la boîte de dialogue plutôt
  qu'un `Bind(EVT_KEY_DOWN)` sur la `wx.ListBox` elle-même : même
  précaution que pour Entrée/flèches/Suppr/F2 en mode navigation SFTP (voir
  plus haut), ce contrôle ne remontant pas toutes les touches de façon
  fiable par ce mécanisme. Vérifié par l'utilisateur au clavier avec
  NVDA : Monter/Descendre dans Favoris et Commandes, persistance de
  l'ordre après réouverture, et Suppr dans les trois dialogues.

- **Bug réel remonté en usage : l'annonce vocale et le statut
  d'« Horodatage » (Ctrl+Maj+H) perdaient l'accent final d'« affiché »/
  « masqué ».** `basculer_horodatage` écrivait `"affiche"`/`"masque"`
  (sans le é), contrairement à `basculer_suivi` juste au-dessus qui écrit
  correctement `"activé"`/`"désactivé"` — pas un simple accent avalé à la
  lecture, mais un mot différent (« affiche », nom ou verbe, prononcé
  autrement qu'« affiché »). Corrigé en ajoutant les deux é. Vérifié par
  l'utilisateur.

- **Complétion de chemin en local, Ctrl+Espace.** Sur le dernier mot
  avant le curseur dans la saisie, propose les fichiers/dossiers du
  répertoire courant dont ce mot est un préfixe (insensible à la casse).
  Local uniquement (`PanneauSession._completer_chemin`) : une session
  distante n'a pas d'arborescence à consulter sans un aller-retour
  réseau à chaque frappe, hors de question sur le fil principal
  (contrainte non négociable, voir en tête de ce fichier). Nouvelle
  boîte `DialogueCompletionChemin`, même schéma que
  `DialogueChoisirCommande` (liste déjà connue à l'ouverture, Entrée
  choisit via le bouton par défaut, Échap referme sans rien changer) au
  lieu de `wx.TextCtrl.AutoComplete()` : la saisie est en
  `TE_MULTILINE`, où Entrée envoie déjà la commande — un popup natif
  disputerait Entrée/flèches à ce comportement existant, même famille
  de risque que les accélérateurs fantômes de menu (voir la décision
  sur la gestion manuelle des raccourcis). Un nom de fichier avec un
  espace est entouré d'apostrophes à l'insertion pour rester un seul
  argument PowerShell (`_jeton_courant` reconnaît une apostrophe encore
  ouverte par une complétion précédente, pour pouvoir enchaîner
  Ctrl+Espace segment par segment d'un chemin sans que l'espace du
  segment déjà complété soit pris pour une séparation d'arguments) ; un
  candidat qui est un dossier reçoit un séparateur en fin de nom, pour
  pouvoir relancer Ctrl+Espace dessus et descendre d'un niveau. Vérifié
  par l'utilisateur au clavier avec NVDA.

- **Version 1.6.0.** Regroupe tout ce qui précède depuis la 1.5.0 :
  Monter/Descendre/Suppr dans les commandes enregistrées, les favoris et
  la gestion des profils SSH, correction de l'accent d'« affiché »/
  « masqué » pour l'horodatage, et la nouvelle complétion de chemin en
  local (Ctrl+Espace).

- **Avertissement avant l'envoi d'un `cd`/`Set-Location` tapé seul.**
  Question posée par l'utilisateur après la sortie de la 1.6.0, à propos
  d'un ancien sujet resté en suspens (commandes façon `cd` pour changer
  de répertoire directement dans le terminal) : pourquoi le modèle
  « une commande à la fois » de ce projet l'empêche, concrètement ?
  Réponse vérifiée dans `execution.py` : chaque commande envoyée démarre
  un `subprocess.Popen` tout neuf (`-NonInteractive`, `cwd=` fourni
  explicitement) — jamais de shell qui reste ouvert d'une commande à
  l'autre. Un `cd` tapé comme une commande normale change donc bien de
  répertoire, mais seulement à l'intérieur de ce processus éphémère, qui
  se termine aussitôt sans rien laisser derrière lui : code de retour 0,
  aucune erreur affichée, et la commande suivante repart silencieusement
  du même répertoire qu'avant. Pire qu'une erreur franche : une perte de
  contexte totalement silencieuse, pas de quoi remarquer que quelque
  chose a raté sans réafficher l'invite à l'écran. C'est exactement pour
  ça que Ctrl+Maj+D existe à part (`Fenetre.changer_repertoire`) : il ne
  passe jamais par un `subprocess`, il modifie directement
  `PanneauSession.repertoire` côté Python, qui est ensuite réinjecté en
  `cwd=` à chaque commande suivante — le seul mécanisme qui « tient »
  d'un bloc à l'autre dans cette architecture.

  Vérifié au passage : rien n'avertissait de ça avant ce correctif, ni
  en local ni en SSH (`get_pty=False`, même modèle par commande côté
  distant). Nouvelle fonction `_commande_cd_isolee` (module-level,
  juste après `libelle_signal`) : détecte, par une regex simple
  (`cd`, `chdir`, `set-location`, `sl`, `pushd`, `popd`, insensible à la
  casse), une commande qui n'est QUE ça — rien d'enchaîné derrière avec
  `;`/`&`/`|` ou un saut de ligne. Ce dernier point est volontaire : un
  one-liner du style `cd Documents; git status` reste parfaitement
  valide et n'est pas signalé, le changement de répertoire profite
  bel et bien à la suite de cette même commande, dans ce même
  processus — seule une commande de changement de répertoire vraiment
  seule, sans suite, est un piège. Détectée depuis `envoyer()`, avant
  tout le reste (avant même de vider la saisie ou de toucher à
  l'historique, pour que le blocage laisse tout intact et modifiable).

  Première version avec une confirmation Oui/Non façon
  `_proposer_reconnexion` (« envoyer quand même ? ») — retirée presque
  aussitôt, sur une objection de l'utilisateur pendant la relecture :
  il n'existe pratiquement aucun cas où « envoyer quand même » est le
  bon choix. Soit l'intention est vraiment de changer de répertoire, et
  la réponse est toujours d'utiliser Ctrl+Maj+D à la place ; soit c'est
  un besoin ponctuel du genre « juste vérifier qu'un chemin existe »,
  et `Test-Path` le couvre déjà proprement, sans le piège de silence.
  Proposer un choix qui n'en est pas vraiment un n'ajoutait que de la
  friction. Simplifié en un blocage pur et simple, sans boîte modale :
  annonce vocale + braille (« cd/Set-Location seul n'est pas conservé
  pour la suite. Utilisez Ctrl+Maj+D. Commande non envoyée. »), la
  commande n'est pas envoyée, la saisie reste telle quelle. Exactement
  le même modèle que le blocage déjà existant sur `en_cours` juste en
  dessous dans `executer()` (« Une commande est déjà en cours... ») —
  ce garde-fou s'aligne dessus plutôt que de réutiliser le modèle
  Oui/Non des confirmations destructives (suppression, hôte SSH
  changé), qui ne s'applique pas ici : rien à confirmer, juste à
  rediriger vers le bon outil.

  Vérifié par un script jetable (une douzaine de cas : `cd`,
  `cd Documents`, `Set-Location -Path ...`, `sl ..`, `pushd`/`popd`, un
  one-liner avec `;`, une commande multiligne, et de simples faux
  positifs à éviter comme `cdignore`) — tous corrects. Reste à
  vérifier au clavier avec NVDA : l'annonce (parole et braille) au
  moment d'envoyer un `cd` isolé, en local comme en SSH, que la
  commande n'est bien pas envoyée (aucun nouveau bloc), et qu'un
  one-liner `cd X; commande` part bien sans aucun avertissement.

- **Version 1.7.0.** L'avertissement `cd`/`Set-Location` ci-dessus.

## Idées à reprendre plus tard

Notées en passant, pas encore faites — pas de quoi se précipiter dessus
sans confirmation. (Les deux idées précédemment listées ici — griser le
menu Commandes hors mode navigation, renommer « mode fichiers » en
« mode navigation » — sont faites, voir État d'avancement ci-dessus.)

## Consignes de travail

- Modifier par petites touches vérifiables, pas par réécritures massives :
  relire un différentiel au lecteur d'écran coûte cher.
- Annoncer clairement quels fichiers vont changer avant de les changer.
- Ne jamais introduire de raccourci clavier entrant en conflit avec ceux de
  NVDA, en particulier tout ce qui utilise Inser ou Verrouillage majuscule.
- Après toute modification de l'interface, rappeler à l'utilisateur ce qu'il
  doit vérifier auditivement : c'est lui seul qui peut valider le rendu
  NVDA.
