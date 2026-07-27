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
- **Le statut passe toujours avant le contenu dans une annonce**, pour
  qu'on puisse couper la parole dès qu'on sait que la commande a réussi.
- Règles de verbosité : sortie vide et code 0 donnent « Terminé » ; moins de
  10 lignes sont lues intégralement ; un code de retour non nul fait lire
  l'erreur en entier quel que soit le niveau réglé.
- Pas de mot de passe en clair dans un fichier de configuration. Clés SSH,
  ou Gestionnaire d'identifiants Windows via `keyring`.

## Comment lancer et tester

- `lancer.bat` — démarre l'application (aucune compilation nécessaire).
- `lancer_muet.bat` — idem sans annonce vocale, à utiliser dès qu'on touche
  à la couche vocale, pour éviter une boucle de parole incontrôlée.
- `compiler.bat` — produit l'exécutable via PyInstaller. Réservé aux paliers.
- `terminal.log` — journal complet, y compris les exceptions non rattrapées.
  C'est la première chose à lire en cas de problème.

L'environnement Python est dans `venv`. Utiliser
`venv\Scripts\python.exe`, jamais le Python global.

## État d'avancement

- **Palier 0, fait** — interface, sessions, modèle de blocs, navigation,
  copie, couche vocale, journal. L'exécution des commandes est simulée dans
  `PanneauSession.executer()`.
- **Palier 1, fait** — exécution locale réelle en tâche de fond (PowerShell
  via `execution.ExecuteurLocal`), interruption par Ctrl+Pause, historique,
  détection des invites de saisie (fragment de ligne sans retour à la ligne,
  silencieux plus de 1,5 s) ouvrant une boîte de dialogue accessible.
- **Palier 2, fait** — SSH par Paramiko (`ssh.ExecuteurSSH`, même contrat
  que `ExecuteurLocal`), profils de connexion (`profils_ssh.json`, non
  secrets), identifiants dans le Gestionnaire d'identifiants Windows via
  `keyring`, mémorisation de la clé d'hôte à la première connexion
  (`hotes_ssh_connus`, jamais le known_hosts de l'utilisateur).
- **Palier 3, à faire** — exécutable final.

## Consignes de travail

- Modifier par petites touches vérifiables, pas par réécritures massives :
  relire un différentiel au lecteur d'écran coûte cher.
- Annoncer clairement quels fichiers vont changer avant de les changer.
- Ne jamais introduire de raccourci clavier entrant en conflit avec ceux de
  NVDA, en particulier tout ce qui utilise Inser ou Verrouillage majuscule.
- Après toute modification de l'interface, rappeler à l'utilisateur ce qu'il
  doit vérifier auditivement : c'est lui seul qui peut valider le rendu
  NVDA.
