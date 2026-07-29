# LazyShell

Terminal accessible pour Windows, pilotable entièrement au clavier et conçu
pour NVDA (avec un second canal d'annonce pour JAWS). Il exécute des
commandes en local (PowerShell) et à distance par SSH, sans jamais ouvrir de
fenêtre de console.

Ce projet est né d'un besoin réel : un terminal utilisable par une personne
non-voyante, avec un afficheur braille, qui n'utilise jamais la souris.
L'accessibilité n'est pas une option ici, c'est le cahier des charges.

## Fonctionnalités

- Sessions multiples par onglets, locales ou SSH, exécutées en tâche de
  fond : l'interface ne se fige jamais pendant qu'une commande tourne.
- Sortie découpée en blocs (une commande, sa sortie, son code de retour,
  son horodatage optionnel), navigables et copiables indépendamment.
- Historique des commandes, détection des invites de saisie (mot de passe,
  confirmation) ouvrant une boîte de dialogue accessible.
- SSH par Paramiko : profils de connexion, identifiants dans le
  Gestionnaire d'identifiants Windows, mémorisation de la clé d'hôte à la
  première connexion.
- Mode fichiers (SSH) : navigateur SFTP accessible en liste (parcourir,
  renommer, supprimer, créer un dossier), avec édition des fichiers dans
  le Bloc-notes de Windows et renvoi automatique sur le serveur.
- Commandes enregistrées : associer un nom à une commande longue tapée
  régulièrement, pour l'insérer sans avoir à la retaper.
- Annonce vocale automatique via le client contrôleur NVDA, avec un second
  canal pour JAWS ; dégradation silencieuse si aucun des deux n'est présent.
- Réglages persistants : verbosité de l'annonce, suivi automatique de la
  sortie, listing amélioré (`dir`/`ls`), horodatage des blocs, taille de
  police.

## Installation (utilisation simple, sans compiler)

1. Télécharger la dernière version compilée depuis la page
   [Releases](../../releases) de ce dépôt et décompresser l'archive
   n'importe où (une clé USB convient : rien ne s'installe dans le
   système). Le dossier obtenu contient `LazyShell.exe`, un sous-dossier
   `_internal` (les deux doivent rester ensemble) et un sous-dossier
   `docs` (documentation HTML, aussi accessible depuis le menu Aide de
   l'appli une fois lancée).
2. Lancer `LazyShell.exe`.

Le **client contrôleur NVDA**, nécessaire à l'annonce vocale automatique
via NVDA, est déjà inclus dans l'archive : rien à récupérer séparément.
S'il venait à manquer (compilation locale sans la DLL, voir plus bas),
l'application démarre et fonctionne normalement : seule l'annonce
automatique est inactive, et le journal (`lazyshell.log`, à côté de
l'exe) l'indique clairement au démarrage.

Le canal JAWS, lui, est actif automatiquement si JAWS est installé et en
cours d'exécution — aucun fichier à récupérer. Sa fiabilité n'a toutefois
jamais été confirmée avec un JAWS réel (voir « Limites connues »).

## Installation (pour modifier le code)

Nécessite Python 3.11 ou 3.12.

1. `installer.bat` — crée un environnement virtuel dans `venv` et installe
   les dépendances (`requirements.txt`). Compter 2 à 4 minutes.
2. `lancer.bat` — démarre l'application depuis les sources.
3. `lancer_muet.bat` — identique, sans aucune annonce envoyée à NVDA : à
   utiliser dès qu'on touche à la couche vocale, pour éviter une boucle de
   parole incontrôlée.
4. `compiler.bat` — produit l'application compilée dans `dist\LazyShell`
   (via PyInstaller, mode dossier). C'est ce dossier complet qu'il faut
   distribuer, jamais `LazyShell.exe` seul : il a besoin du sous-dossier
   `_internal` à côté de lui. Le sous-dossier `docs` du dépôt y est copié
   automatiquement. Pour que l'annonce vocale automatique fonctionne dans
   l'exécutable produit, déposer au préalable le **client contrôleur
   NVDA** (non fourni avec NVDA lui-même — archive `…controllerClient.zip`
   publiée par NV Access sur `download.nvaccess.org`, fichier du dossier
   **x64**) à la racine du dépôt, sous le nom `nvdaControllerClient64.dll`
   ou `nvdaControllerClient.dll` : `compiler.bat` le détecte et le copie
   à côté de `LazyShell.exe`. Sans lui, l'exécutable fonctionne quand même,
   simplement sans annonce automatique.

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
| Ctrl+Maj+F | Basculer entre terminal et mode fichiers (session SSH) |

**Mode fichiers (SFTP)**

| Touche | Action |
|---|---|
| Entrée | Ouvrir le dossier, ou éditer le fichier sélectionné |
| Retour arrière | Remonter au dossier parent |
| F2 | Renommer l'élément sélectionné |
| Suppr | Supprimer l'élément sélectionné (avec confirmation) |
| Ctrl+Maj+G | Créer un dossier |
| Ctrl+Maj+E | Envoyer un fichier de cette machine vers le dossier affiché |
| Ctrl+Maj+T | Télécharger l'élément sélectionné vers cette machine |

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
| Ctrl+Maj+N | Listing amélioré (nom de fichier en tête de ligne) |
| Ctrl+Maj+H | Afficher ou masquer l'horodatage des blocs |

## Le journal

Tout est consigné dans `lazyshell.log`, à côté de l'exécutable (ou du
script) : démarrage, sessions ouvertes, commandes, erreurs, et la trace
complète de toute exception. Le menu Aide propose « Ouvrir le journal ».
En cas de problème, copier les vingt dernières lignes suffit généralement
à identifier la cause.

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

## Licence

Ce projet est distribué sous licence MIT — voir [LICENSE](LICENSE).
