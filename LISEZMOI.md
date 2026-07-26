# Terminal accessible — palier 0

Coquille de l'interface. L'exécution réelle des commandes n'est pas encore
branchée : ce palier sert à valider l'ergonomie, l'accessibilité NVDA et la
chaîne d'outils avant d'empiler quoi que ce soit dessus.

## Installation, une seule fois

1. Déposer tous ces fichiers dans un dossier, par exemple
   `C:\Projets\terminal-accessible`.
2. Lancer **`installer.bat`** avec Entrée. Il vérifie la présence de Python,
   crée un environnement isolé dans le sous-dossier `venv`, puis installe les
   dépendances. Compter 2 à 4 minutes.

Si Python est absent, le script le dit et s'arrête. Installer alors Python
3.11 ou 3.12 depuis python.org, en cochant **« Add python.exe to PATH »**
pendant l'installation, puis relancer `installer.bat`.

## Utilisation quotidienne

- **`lancer.bat`** — démarre l'application. À utiliser à chaque itération.
  Aucune compilation n'est nécessaire pendant le développement.
- **`lancer_muet.bat`** — identique, mais sans aucune annonce envoyée à NVDA.
  À utiliser dès qu'on touche à la couche vocale, pour se prémunir d'une
  boucle qui partirait en vrille.
- **`compiler.bat`** — produit l'exécutable unique dans le sous-dossier
  `dist`. À réserver aux paliers, pas aux itérations.

Une fenêtre console apparaît pendant l'exécution via `lancer.bat` : c'est
volontaire, elle capte les erreurs de démarrage. L'exécutable compilé, lui,
n'en ouvre aucune.

## Le journal

Tout est consigné dans **`terminal.log`**, à côté du script : démarrage,
sessions ouvertes, commandes, erreurs, et la trace complète de toute
exception. Le menu Aide propose « Ouvrir le journal ».

En cas de problème, copier les vingt dernières lignes de ce fichier suffit
généralement à identifier la cause.

## Annonce vocale : la DLL à récupérer

L'annonce automatique passe par le **client contrôleur de NVDA**. Point
important : cette DLL **n'est pas livrée avec NVDA**, il faut la récupérer
séparément — c'est une source de confusion classique.

Elle se télécharge depuis les publications de NV Access, sous la forme d'une
archive nommée `…controllerClient.zip`, disponible dans le dossier des
versions sur `download.nvaccess.org`. La documentation officielle se trouve
sur le dépôt GitHub de NVDA, dans `extras/controllerClient`.

Une fois l'archive décompressée, prendre le fichier du dossier **x64**
(`nvdaControllerClient64.dll` ou `nvdaControllerClient.dll` selon la version)
et le déposer soit à côté de `terminal_accessible.py`, soit dans un
sous-dossier `dll`.

Sans cette DLL, l'application démarre et fonctionne normalement : seule
l'annonce automatique est inactive, et le journal l'indique clairement au
démarrage. C'est d'ailleurs le premier point à vérifier dans le journal.

La bibliothèque est publiée sous licence LGPL 2.1, ce qui autorise sa
redistribution avec l'exécutable.

## Raccourcis clavier

| Touche | Action |
|---|---|
| Entrée | Envoyer la commande |
| F6 | Basculer entre saisie et sortie |
| Échap | Revenir au champ de saisie |
| Flèche haut / bas | Historique des commandes (dans la saisie) |
| Alt+Haut / Alt+Bas | Bloc précédent / suivant |
| Ctrl+Maj+C | Copier le bloc courant (commande et sortie) |
| Ctrl+Maj+S | Copier la sortie seule du bloc courant |
| Ctrl+Maj+L | Copier le dernier bloc |
| Ctrl+B | Liste des blocs |
| Ctrl+Maj+V | Changer le niveau de verbosité |
| Ctrl+T | Nouvelle session |
| Ctrl+W | Fermer la session |
| Ctrl+Tab / Ctrl+Maj+Tab | Session suivante / précédente |
| Ctrl+1 à Ctrl+9 | Aller directement à une session |

Tout figure également dans la barre de menus.

## Commandes de démonstration

L'exécution étant simulée, quatre mots-clés déclenchent des cas différents
pour éprouver les règles d'annonce :

- `erreur` — code de retour non nul, l'erreur doit être lue intégralement
- `long` — 40 lignes, la verbosité doit s'appliquer
- `vide` — aucune sortie, l'annonce doit se réduire à « Terminé »
- n'importe quoi d'autre — 3 lignes, doit être lu en entier

## Ce qu'il faut vérifier à ce palier

1. NVDA annonce bien « Commande, Local, édition » et « Sortie, Local,
   édition, lecture seule » à la prise de focus.
2. F6 bascule entre les deux champs, Échap revient à la saisie.
3. Le champ de sortie se parcourt normalement aux flèches, et l'afficheur
   braille suit.
4. Alt+Haut et Alt+Bas sautent de bloc en bloc en annonçant l'en-tête.
5. Ctrl+Maj+C copie bien le bloc entier, collable ailleurs.
6. Ctrl+B ouvre une liste navigable aux flèches.
7. Ctrl+T crée une session et NVDA annonce le changement ; Ctrl+Tab et
   Ctrl+1 à 9 circulent correctement.
8. Le nom de session est bien présent dans l'annonce des champs — c'est le
   garde-fou contre la commande tapée dans la mauvaise session.
9. Les bips distinguent réussite et échec.

## Ensuite

- Palier 1 — exécution locale réelle, en tâche de fond, avec interruption et
  détection des invites de saisie.
- Palier 2 — SSH via Paramiko, profils de connexion, identifiants dans le
  Gestionnaire d'identifiants Windows.
- Palier 3 — exécutable final.

Un premier passage par `compiler.bat` est toutefois recommandé **dès
maintenant**, sur cette base minimale : c'est le moment le moins coûteux pour
découvrir un problème d'empaquetage.
