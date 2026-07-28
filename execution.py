# -*- coding: utf-8 -*-
"""
Exécution des commandes, en tâche de fond.

Ce module ne connaît rien de wxPython : il expose une interface par
fonctions de rappel, appelées depuis un thread de travail. C'est à
l'appelant de les réacheminer vers le thread principal (wx.CallAfter).

Deux raisons à cette séparation : le module reste testable sans
interface, et le palier 2 (SSH) pourra fournir un second exécuteur
respectant le même contrat, sans toucher au reste.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

# Empêche toute fenêtre de console d'apparaître. Non négociable : une
# console qui surgit vole le focus au lecteur d'écran.
CREATE_NO_WINDOW = 0x08000000

# Au-delà, on considère que la commande est longue et on prévient.
SEUIL_COMMANDE_LONGUE = 2.0

# Garde-fou mémoire : une commande qui déverse sans fin ne doit pas
# faire gonfler l'application indéfiniment.
MAX_LIGNES = 20000

# Prélude injecté avant chaque commande PowerShell.
#
# Sans lui, PowerShell écrit dans le tuyau avec la page de code OEM du
# système (850 en France) : « Répertoire » arrive alors sous forme
# d'octets que Python, qui attend de l'UTF-8, remplace par des points
# d'interrogation.
#
# UTF8Encoding($false) et non [Text.Encoding]::UTF8 : la seconde ajoute
# une marque d'ordre des octets en tête de sortie, qui apparaîtrait
# comme un caractère parasite sur la première ligne.
PRELUDE_ENCODAGE = (
    "$e = New-Object System.Text.UTF8Encoding $false; "
    "[Console]::OutputEncoding = $e; "
    "$OutputEncoding = $e; "
    "$PSDefaultParameterValues['*:Encoding'] = 'utf8'; "
    "chcp 65001 > $null"
)

# Colonnes du listing lisible, définies une fois dans le prélude pour
# que la commande réécrite reste courte.
#
# On ne touche PAS aux alias : dir, ls et gci sont marqués AllScope,
# une option que Set-Alias ne sait pas retirer — la tentative produit
# une erreur à chaque commande. On réécrit donc la commande côté Python,
# ce qui est déterministe et vérifiable.
PRELUDE_LISTING = (
    "; $_TA_COLONNES = @("
    "@{Label='Nom'; Expression={$_.Name}},"
    "@{Label='Type'; Expression={if ($_.PSIsContainer) {'dossier'} else {'fichier'}}},"
    "@{Label='Taille'; Expression={if ($_.PSIsContainer) {''} else {$_.Length}}},"
    "@{Label='Modifié'; Expression={$_.LastWriteTime.ToString('dd/MM/yyyy HH:mm')}}"
    ")"
)

# Commandes de listing reconnues, au tout début de la ligne uniquement.
MOTIF_LISTING = re.compile(
    r"^\s*(?:dir|ls|gci|Get-ChildItem)(?=\s|$)(?P<reste>.*)$",
    re.IGNORECASE,
)


def reecrire_listing(commande: str) -> str | None:
    """Transforme une commande de listing pour mettre le nom en tête.

    Renvoie None si la commande ne s'y prête pas : présence d'un tube,
    d'une redirection, d'un point-virgule ou de plusieurs lignes. Dans
    ces cas l'utilisateur compose déjà quelque chose de précis, et y
    injecter un formatage casserait son intention.
    """
    if any(c in commande for c in ("|", ">", ";", "\n")):
        return None
    correspondance = MOTIF_LISTING.match(commande)
    if correspondance is None:
        return None
    reste = correspondance.group("reste").strip()
    base = f"Get-ChildItem {reste}".strip()
    return f"{base} | Format-Table -AutoSize $_TA_COLONNES"


# Séquences d'échappement ANSI (couleurs, déplacement du curseur...).
# Certains outils les émettent même sans terminal en face (alias
# `ls --color=always`, variables comme CLICOLOR_FORCE) : sans filtre,
# les codes bruts s'afficheraient tels quels, illisibles en vocal comme
# en braille. Partagé avec ssh.py, d'où le format d'échappement générique
# plutôt qu'une liste des seuls codes couleur.
MOTIF_ANSI = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def nettoyer_ansi(texte: str) -> str:
    return MOTIF_ANSI.sub("", texte)


# Après une interruption, délai au-delà duquel on cesse d'attendre les
# flux : un processus petit-enfant ayant survécu les garderait ouverts.
DELAI_ABANDON_LECTURE = 3.0

# Silence, sur un fragment de ligne encore incomplet (donc pas encore une
# ligne au sens de sur_ligne), au-delà duquel on soupçonne une invite de
# saisie plutôt qu'une commande simplement lente. Une invite typique
# (« Mot de passe : », « Continuer ? [O/n] ») n'envoie jamais de retour à
# la ligne : elle reste sinon invisible à un lecteur d'écran, puisque rien
# ne la distingue d'une commande qui prend son temps.
SEUIL_INVITE = 1.5


@dataclass
class Resultat:
    sortie: str
    code_retour: int
    duree: float
    tronquee: bool = False
    interrompue: bool = False


class ExecuteurLocal:
    """Exécute une commande via PowerShell, sans fenêtre.

    PowerShell plutôt que cmd : il accepte nativement les commandes
    multilignes et son encodage est prévisible une fois forcé en UTF-8.
    """

    def __init__(self):
        self._processus: subprocess.Popen | None = None
        self._verrou = threading.Lock()
        self._shell = self._trouver_shell()

    # -- shell -------------------------------------------------------------

    @staticmethod
    def _trouver_shell() -> str:
        """PowerShell 7 s'il est là, sinon celui livré avec Windows."""
        for nom in ("pwsh.exe", "powershell.exe"):
            chemin = _chercher_dans_path(nom)
            if chemin:
                logging.info("Shell local retenu : %s", chemin)
                return chemin
        logging.warning(
            "Aucun PowerShell trouvé, repli sur cmd.exe. Les commandes "
            "multilignes ne fonctionneront pas."
        )
        return os.environ.get("COMSPEC", "cmd.exe")

    @property
    def nom_shell(self) -> str:
        return os.path.basename(self._shell)

    def _arguments(self, commande: str, listing_lisible: bool = True) -> list[str]:
        if self.nom_shell.lower() in ("pwsh.exe", "powershell.exe"):
            prelude = PRELUDE_ENCODAGE
            if listing_lisible:
                prelude += PRELUDE_LISTING
                reecrite = reecrire_listing(commande)
                if reecrite is not None:
                    logging.info("Listing réécrit : %s", reecrite)
                    commande = reecrite
            return [
                self._shell,
                "-NoProfile",          # démarrage rapide, comportement stable
                "-NonInteractive",     # ne bloque pas sur une invite cachée
                "-ExecutionPolicy", "Bypass",
                "-Command", prelude + "\n" + commande,
            ]
        # cmd.exe : chcp bascule la console en UTF-8.
        return [self._shell, "/c", "chcp 65001 >nul & " + commande]

    # -- exécution ---------------------------------------------------------

    def executer(
        self,
        commande: str,
        repertoire: str | None = None,
        sur_ligne: Callable[[str, bool], None] | None = None,
        sur_lenteur: Callable[[], None] | None = None,
        sur_invite: Callable[[str], str | None] | None = None,
        listing_lisible: bool = True,
    ) -> Resultat:
        """Lance la commande et attend sa fin.

        À appeler depuis un thread de travail : cette méthode bloque.

        sur_ligne(texte, est_erreur) est appelée à chaque ligne produite,
        depuis un thread de lecture. sur_lenteur() est appelée une seule
        fois si la commande dépasse le seuil, pour signaler qu'elle
        travaille encore. sur_invite(texte) est appelée, et son résultat
        attendu, quand un fragment de ligne reste en silence au-delà de
        SEUIL_INVITE : elle doit renvoyer le texte à transmettre au
        processus, ou None si rien ne doit être envoyé.
        """
        debut = time.monotonic()
        logging.info("Exécution [%s] : %s", self.nom_shell, commande)

        demarrage = dict(
            args=self._arguments(commande, listing_lisible),
            cwd=repertoire,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,      # nécessaire pour répondre à une invite
            creationflags=CREATE_NO_WINDOW,
            text=True,
            encoding="utf-8",
            errors="replace",           # jamais d'exception sur un octet bizarre
            bufsize=1,                  # ligne par ligne
        )

        try:
            processus = subprocess.Popen(**demarrage)
        except OSError as erreur:
            logging.exception("Lancement impossible")
            return Resultat(
                sortie=f"Impossible de lancer la commande : {erreur}",
                code_retour=-1,
                duree=time.monotonic() - debut,
            )

        with self._verrou:
            self._processus = processus

        lignes: list[str] = []
        tronquee = False
        fil = queue.Queue()

        # Fragment de ligne encore incomplet, par flux (False = stdout,
        # True = stderr). Lu caractère par caractère plutôt que ligne par
        # ligne : une invite sans retour à la ligne ne serait sinon jamais
        # produite par l'itérateur, qui attend indéfiniment le \n.
        #
        # "tampon" reste la même liste mutée en place (append/clear) : la
        # reconstituer en chaîne à chaque caractère coûterait O(n²) sur une
        # ligne longue sans retour à la ligne. On ne fait le join que
        # lorsqu'une ligne se termine ou qu'une invite est soupçonnée.
        verrou_fragments = threading.Lock()
        fragments = {
            False: {"tampon": [], "temps": 0.0, "signale": False},
            True: {"tampon": [], "temps": 0.0, "signale": False},
        }

        def lire(flux, est_erreur: bool):
            info = fragments[est_erreur]
            try:
                while True:
                    caractere = flux.read(1)
                    if caractere == "":
                        break
                    if caractere == "\n":
                        with verrou_fragments:
                            texte = "".join(info["tampon"])
                            info["tampon"].clear()
                            info["signale"] = False
                        fil.put((nettoyer_ansi(texte), est_erreur))
                    else:
                        with verrou_fragments:
                            info["tampon"].append(caractere)
                            info["temps"] = time.monotonic()
                            info["signale"] = False
            except Exception:
                logging.exception("Lecture du flux interrompue")
            finally:
                with verrou_fragments:
                    texte_restant = "".join(info["tampon"])
                    info["tampon"].clear()
                if texte_restant:
                    fil.put((nettoyer_ansi(texte_restant), est_erreur))
                fil.put(None)

        lecteurs = [
            threading.Thread(target=lire, args=(processus.stdout, False), daemon=True),
            threading.Thread(target=lire, args=(processus.stderr, True), daemon=True),
        ]
        for lecteur in lecteurs:
            lecteur.start()

        termines = 0
        prevenu = False
        instant_interruption: float | None = None

        while termines < 2:
            try:
                element = fil.get(timeout=0.25)
            except queue.Empty:
                # Filet de securite : après une interruption, un processus
                # petit-enfant peut garder les tuyaux ouverts, ce qui
                # bloquerait cette boucle pour toujours. On abandonne la
                # lecture au bout de quelques secondes plutôt que de figer
                # l'application.
                if getattr(processus, "_interrompu", False):
                    if instant_interruption is None:
                        instant_interruption = time.monotonic()
                    elif time.monotonic() - instant_interruption > DELAI_ABANDON_LECTURE:
                        logging.warning(
                            "Flux toujours ouverts %.0f s après interruption : "
                            "un processus enfant a survécu. On abandonne la "
                            "lecture.", DELAI_ABANDON_LECTURE,
                        )
                        break

                if (
                    not prevenu
                    and sur_lenteur is not None
                    and time.monotonic() - debut > SEUIL_COMMANDE_LONGUE
                ):
                    prevenu = True
                    sur_lenteur()

                if sur_invite is not None and processus.poll() is None:
                    candidat = None
                    with verrou_fragments:
                        for info in fragments.values():
                            if (
                                info["tampon"]
                                and not info["signale"]
                                and time.monotonic() - info["temps"] > SEUIL_INVITE
                            ):
                                info["signale"] = True
                                candidat = nettoyer_ansi("".join(info["tampon"]))
                                break
                    if candidat is not None:
                        reponse = sur_invite(candidat)
                        if reponse is not None and processus.stdin is not None:
                            try:
                                processus.stdin.write(reponse + "\n")
                                processus.stdin.flush()
                            except Exception:
                                logging.exception("Écriture sur stdin impossible")
                continue

            if element is None:
                termines += 1
                continue

            texte, est_erreur = element
            if len(lignes) < MAX_LIGNES:
                lignes.append(f"[erreur] {texte}" if est_erreur else texte)
                if sur_ligne is not None:
                    sur_ligne(texte, est_erreur)
            elif not tronquee:
                tronquee = True
                lignes.append(
                    f"[Sortie tronquée : plus de {MAX_LIGNES} lignes.]"
                )

        try:
            code = processus.wait(timeout=DELAI_ABANDON_LECTURE)
        except subprocess.TimeoutExpired:
            logging.warning("Le processus ne rend pas la main, on le tue.")
            try:
                processus.kill()
                code = processus.wait(timeout=2)
            except Exception:
                logging.exception("Impossible de terminer le processus")
                code = -1

        with self._verrou:
            interrompue = getattr(processus, "_interrompu", False)
            self._processus = None

        duree = time.monotonic() - debut
        logging.info("Terminé, code %s, %.1f s, %d lignes", code, duree, len(lignes))

        return Resultat(
            sortie="\n".join(lignes),
            code_retour=code,
            duree=duree,
            tronquee=tronquee,
            interrompue=interrompue,
        )

    # -- interruption ------------------------------------------------------

    @property
    def occupe(self) -> bool:
        with self._verrou:
            return self._processus is not None

    def interrompre(self) -> bool:
        """Tue le processus et toute sa descendance.

        terminate() ne suffit pas : PowerShell lance souvent des processus
        enfants qui lui survivraient. On passe par taskkill /T, qui
        remonte l'arborescence.
        """
        with self._verrou:
            processus = self._processus
            if processus is None:
                return False
            processus._interrompu = True
            pid = processus.pid

        logging.info("Interruption demandée du processus %s", pid)
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                creationflags=CREATE_NO_WINDOW,
                capture_output=True,
                timeout=5,
            )
        except Exception:
            logging.exception("taskkill a échoué, repli sur kill()")
            try:
                processus.kill()
            except Exception:
                logging.exception("kill() a échoué également")
                return False
        return True

    def fermer(self) -> None:
        """Rien à fermer : chaque commande relance et referme son propre
        processus. N'existe que pour respecter le même contrat que
        ExecuteurSSH, dont la connexion persistante doit l'être."""


def _chercher_dans_path(nom: str) -> str | None:
    import shutil
    return shutil.which(nom)
