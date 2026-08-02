# LazyShell, le terminal pour fainéants

LazyShell est un terminal et un navigateur SSH/SFTP accessible pour Windows, pilotable entièrement au clavier et conçu
principalement pour une utilisation avec NVDA (plus un second canal d'annonce pour JAWS). Il exécute des
commandes en local (PowerShell) et à distance par SSH, sans jamais ouvrir de
fenêtre de console.

Ce projet est né d'un besoin réel. Je n'aime pas du tout travailler en ligne de commandes. Mais j'avais besoin d'accéder en SSH à un VPS et pour régler les problèmes, il fallait sans cesse taper des lignes de commandes que l'IA ou Internet me fournissait. Et comme se rappeler de commandes interminables était trop compliqué pour moi, il me fallait un programme me permettant de les copier-coller facilement ou de lire les sorties de commandes sans galérer comme avec l'invite de commandes Windows ou tous les autres programmes similaires que j'ai pu tester.
De plus, j'utilisais WinSCP en parallèle pour parcourir un serveur distant, afin d'éditer ou transférer des fichiers. Désormais, plus besoin de WinSCP, LazyShell fait le job, c'est du tout en un ! Grâce au vibe-coding, ce besoin a été comblé en quelques jours.

## Fonctionnalités
- Une vraie zone de saisie et un champ de sortie bien standards dans lesquels on peut utiliser les commandes de sélection et de copier-coller habituelles.
- Possibilité de créer des sessions multiples par onglets, locales ou SSH et on peut passer de l'une à l'autre instantanément au clavier en sachant toujours dans laquelle on se trouve, tout est vocalisé et indiqué en braille
- Sortie découpée en blocs (une commande, sa sortie, son code de retour,
  son horodatage optionnel), navigables et copiables indépendamment. Ces blocs sont également accessibles à partir d'une liste.
- Historique des commandes, détection des invites de saisie (mot de passe,
  confirmation) ouvrant une boîte de dialogue accessible.
- Profils de connexion SSH, identifiants dans le
  Gestionnaire d'identifiants Windows, mémorisation de la clé d'hôte à la
  première connexion.
- Mode navigation (SSH) : navigateur SFTP accessible en liste, similaire à l'explorateur Windows, pour parcourir,
  renommer, supprimer, créer un dossier, envoyer et télécharger, avec
  édition des fichiers dans le Bloc-notes de Windows et renvoi
  automatique sur le serveur. Envois et téléchargements traités un par
  un, avec une fenêtre de progression modale (nom du fichier en cours,
  pourcentage, vitesse, bouton Annuler) qui bloque le reste de
  l'application jusqu'à la fin du transfert, et des dossiers favoris
  nommés par profil SSH (nom explicite et chemin, comme les commandes
  enregistrées) pour y sauter directement. Recherche récursive de
  fichiers ou dossiers par nom à partir du dossier affiché, avec un
  résultat qui amène directement dessus (dans son dossier parent pour
  un fichier).
- Reconnexion proposée automatiquement si la connexion SSH tombe en
  cours de session (mot de passe redemandé seulement s'il n'est pas
  déjà mémorisé), sans perdre l'historique de la session en cours.
- Possibilité d'enregistrer de longues commandes fréquemment utilisées et de leur associer un nom explicite afin de les rappeler à partir d'une liste.
- Toute action ou confirmation est énoncée automatiquement par NVDA via le client contrôleur de ce dernier, s'il est installé, avec possibilité d'agir sur la verbosité de ces annonces.
- Listing amélioré (`dir`/`ls`, nom de fichier en tête de ligne) toujours
  actif, en local comme en SSH.
- Vérification silencieuse des mises à jour au démarrage (menu Aide pour
  la refaire à la demande).
- Complétion de chemin en session locale (Ctrl+Espace) : propose les
  fichiers/dossiers du répertoire courant à partir du mot en train
  d'être tapé (détail dans la [documentation](docs/index.html#fonctionnement)).
- Réordonnancement des commandes enregistrées et des favoris de
  dossiers distants (boutons Monter/Descendre ou Ctrl+Flèche haut/bas,
  avec annonce vocale de la nouvelle position) ; touche Suppr pour
  supprimer directement dans ces listes et dans la gestion des profils
  SSH (détail dans la [documentation](docs/index.html#commandes)).

## Installation

1. Télécharger la dernière version depuis la page
   [Releases](../../releases) de ce dépôt et décompresser l'archive
   n'importe où (une clé USB convient : rien ne s'installe dans le système).
2. Lancer `LazyShell.exe`.

Le **client contrôleur NVDA**, nécessaire à l'annonce vocale automatique
via NVDA, est déjà inclus dans l'archive : rien à récupérer séparément.
S'il venait à manquer, l'application démarre et fonctionne normalement : seule l'annonce
automatique est inactive, et le journal (`lazyshell.log`, à côté de
l'exe) l'indique clairement au démarrage.

Le canal JAWS, lui, est actif automatiquement si JAWS est installé et en
cours d'exécution — aucun fichier à récupérer. Sa fiabilité n'a toutefois
jamais été confirmée avec un JAWS réel (voir « Limites connues »).

## Raccourcis clavier

Tout est également accessible depuis la barre de menus.

| Touche | Action |
|---|---|
| Entrée | Envoyer la commande |
| Maj+Entrée | Saut de ligne dans la saisie (commande multiligne) |
| F6 | Basculer entre saisie et sortie |
| Échap | Revenir au champ de saisie |
| Flèche haut / bas | Historique des commandes (dans la saisie) |
| Ctrl+Maj+K (ou Ctrl+Pause) | Interrompre la commande en cours |
| Ctrl+= / Ctrl+- | Agrandir / réduire la police |

**Session**

| Touche | Action |
|---|---|
| Ctrl+T | Nouvelle session locale |
| Ctrl+Maj+O | Nouvelle session SSH |
| Ctrl+W | Fermer la session |
| Ctrl+Tab / Ctrl+Maj+Tab | Session suivante / précédente |
| Ctrl+1 à Ctrl+9 | Aller directement à une session |
| Ctrl+Maj+D | Changer de répertoire courant |
| Ctrl+Maj+A | Favoris (dossiers distants, par profil SSH) |

**Mode navigation (SFTP)** — actions grisées dans le menu Session hors mode navigation

| Touche | Action |
|---|---|
| Entrée | Ouvrir le dossier, ou éditer le fichier sélectionné |
| Retour arrière | Remonter au dossier parent |
| F2 | Renommer l'élément sélectionné |
| Suppr | Supprimer l'élément sélectionné (avec confirmation) |
| Ctrl+Maj+N | Créer un dossier |
| Ctrl+Maj+E | Envoyer un fichier vers le dossier distant affiché |
| Ctrl+Maj+T | Télécharger l'élément sélectionné |
| Ctrl+Maj+G | Rechercher des fichiers (récursif, à partir du dossier affiché) |

**Commandes enregistrées**

| Touche | Action |
|---|---|
| Ctrl+Maj+J | Utiliser une commande enregistrée |
| Ctrl+Maj+M | Enregistrer la commande actuelle |

**Blocs de sortie**

| Touche | Action |
|---|---|
| Alt+Haut / Alt+Bas | Bloc précédent / suivant |
| Ctrl+Maj+C | Copier le bloc courant (commande et sortie) |
| Ctrl+Maj+S | Copier la sortie seule du bloc courant |
| Ctrl+Maj+L | Copier le dernier bloc |
| Ctrl+B | Liste des blocs |

**Affichage**

| Touche | Action |
|---|---|
| Ctrl+Maj+R | Relire la saisie en cours |
| Ctrl+Maj+V | Changer le niveau de verbosité vocale |
| Ctrl+Maj+U | Aller automatiquement à la sortie après chaque commande |
| Ctrl+Maj+H | Afficher ou masquer l'horodatage des blocs |
| Ctrl+Maj+F | Basculer entre terminal et mode navigation (session SSH) |

## Limites connues

- SSH fonctionne sans pseudo-terminal (`get_pty=False`) : stdout et
  stderr restent séparés, comme en local. Conséquence acceptée : une
  invite purement interactive côté shell distant (par exemple `read -p`
  de bash, qui n'écrit rien du tout hors mode interactif) n'écrit rien de
  détectable ; seule l'interruption manuelle (Ctrl+Maj+K) s'applique
  alors. Même limite que `Read-Host` bloqué en local par
  `-NonInteractive`.
- Le canal d'annonce JAWS n'a jamais été vérifié avec un JAWS réel :
  l'identifiant COM et les noms de méthode utilisés viennent des
  références les plus courantes sur l'automatisation JAWS, pas d'une
  confirmation. Le canal NVDA, lui, est indépendant et validé.

## Auteur

Sof — hellosof@gmail.com

## Licence

Ce projet est distribué sous licence MIT — voir [LICENSE](LICENSE).
