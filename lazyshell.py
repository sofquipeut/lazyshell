#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LazyShell — terminal accessible pour Windows, pilotable entièrement au
clavier et conçu pour NVDA (et un second canal d'annonce pour JAWS).

Fenêtre à onglets de session (locale ou SSH), champ de saisie, champ de
sortie découpé en blocs, historique, commandes enregistrées, transfert de
fichiers SFTP, couche vocale.

Les identifiants sont en français : une synthèse vocale française lit
correctement « traiter_sortie » et massacre « handle_output ».
"""

from __future__ import annotations

import argparse
import ctypes
import json
import logging
import queue
import re
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import wx

from execution import ExecuteurLocal, Resultat
from ssh import (
    EntreeDistante,
    ExecuteurSSH,
    FavoriDossier,
    ProfilConnexion,
    charger_profils,
    enregistrer_profils,
    enregistrer_secret,
    lire_secret,
    supprimer_secret,
)

APP_NOM = "LazyShell"
VERSION = "1.4.0"

# Dépôt GitHub public du projet, pour la vérification des mises à jour.
URL_DERNIERE_RELEASE = "https://api.github.com/repos/sofquipeut/lazyshell/releases/latest"
# Page du dépôt lui-même (pas l'API), pour l'ouvrir dans le navigateur
# depuis le menu Aide.
URL_DEPOT = "https://github.com/sofquipeut/lazyshell"

AUTEUR_NOM = "Sof"
AUTEUR_COURRIEL = "hellosof@gmail.com"

# Niveaux de verbosité de l'annonce vocale
VERBOSITE_RESUME = 0
VERBOSITE_RESUME_PLUS = 1
VERBOSITE_TOUT = 2

NOMS_VERBOSITE = {
    VERBOSITE_RESUME: "résumé seul",
    VERBOSITE_RESUME_PLUS: "résumé et 5 premières lignes",
    VERBOSITE_TOUT: "sortie complète",
}

# Seuils des règles adaptatives
SEUIL_LECTURE_INTEGRALE = 10   # en dessous, on lit tout quoi qu'il arrive
LIGNES_APERCU = 5              # niveau 1
LIGNES_ERREUR = 15             # sur code de retour non nul
SEUIL_DUREE_ANNONCEE = 3.0     # en deçà, la durée n'est pas mentionnée
LONGUEUR_COMMANDE_ENTETE = 60  # au-delà, la commande est tronquée
                               # dans l'en-tête et rappelée en entier

# Une invite reconnue comme mot de passe masque la saisie : elle n'a pas
# besoin d'être vue, et le braille ne devrait pas l'afficher en clair.
MOTIF_MOT_DE_PASSE = re.compile(r"password|mot de passe|passphrase", re.IGNORECASE)

# Taille de police : bornes larges, pensées pour un usage malvoyant, pas
# seulement pour un confort de lecture ordinaire.
TAILLE_POLICE_DEFAUT = 11
TAILLE_POLICE_MIN = 8
TAILLE_POLICE_MAX = 32
TAILLES_POLICE_PRESETS = {
    "Petite": 9,
    "Normale": TAILLE_POLICE_DEFAUT,
    "Grande": 16,
    "Très grande": 22,
}


# --------------------------------------------------------------------------
# Chemins et journal
# --------------------------------------------------------------------------

def dossier_base() -> Path:
    """Dossier de travail : à côté de l'exe si compilé, du script sinon."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _chemin_reglages() -> Path:
    return dossier_base() / "settings.json"


def charger_reglages() -> dict:
    """Lit le fichier de réglages. Repli silencieux sur un dict vide si le
    fichier est absent ou illisible : un réglage de confort ne doit jamais
    empêcher l'application de démarrer."""
    try:
        return json.loads(_chemin_reglages().read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def enregistrer_reglages(reglages: "Reglages") -> None:
    """Réécrit le fichier de réglages en entier. Appelé à chaque
    changement pour rester simple : le fichier est minuscule, pas besoin
    d'écriture incrémentale."""
    donnees = {
        "taille_police": reglages.taille_police,
        "verbosite": reglages.verbosite,
        "suivre_sortie": reglages.suivre_sortie,
        "afficher_horodatage": reglages.afficher_horodatage,
    }
    try:
        _chemin_reglages().write_text(json.dumps(donnees), encoding="utf-8")
    except OSError:
        logging.exception("Impossible d'enregistrer les réglages.")


def configurer_journal() -> Path:
    chemin = dossier_base() / "lazyshell.log"
    logging.basicConfig(
        filename=str(chemin),
        filemode="a",
        level=logging.DEBUG,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        encoding="utf-8",
    )
    logging.info("=" * 60)
    logging.info("Démarrage de %s version %s", APP_NOM, VERSION)
    logging.info("Python %s", sys.version.replace("\n", " "))
    logging.info("Exécutable : %s", sys.executable)
    logging.info("Dossier de base : %s", dossier_base())

    def hook(type_exc, valeur, trace):
        logging.critical(
            "Exception non rattrapée",
            exc_info=(type_exc, valeur, trace),
        )
        texte = "".join(traceback.format_exception(type_exc, valeur, trace))
        try:
            wx.MessageBox(
                "Une erreur inattendue s'est produite.\n\n"
                f"{valeur}\n\n"
                f"Le détail complet est dans :\n{chemin}",
                "Erreur",
                wx.OK | wx.ICON_ERROR,
            )
        except Exception:
            sys.stderr.write(texte)

    sys.excepthook = hook
    return chemin


# --------------------------------------------------------------------------
# Vérification des mises à jour
# --------------------------------------------------------------------------

def _version_plus_recente(distante: str, locale: str) -> bool:
    """Compare deux versions "major.minor.patch" (le tag GitHub porte un
    "v" en tête, ex. "v1.2.0" : ignoré). Pas de dépendance externe
    (packaging.version) pour une comparaison aussi simple — les versions
    de ce projet suivent toujours ce format."""
    def parties(v: str) -> tuple[int, ...]:
        return tuple(int(p) for p in v.lstrip("vV").split("."))
    try:
        return parties(distante) > parties(locale)
    except ValueError:
        return False


def _verifier_derniere_version() -> tuple[str, str] | None:
    """Interroge l'API GitHub pour la dernière Release publiée. Bloque :
    à appeler hors thread principal.

    Renvoie (version, url) si une version plus récente que VERSION est
    disponible, None sinon — déjà à jour, ou vérification impossible.
    Dégradation silencieuse volontaire (même principe que la DLL NVDA
    absente) : une vérification de confort ne doit jamais faire échouer
    ni inquiéter l'utilisateur pour un problème réseau.
    """
    import urllib.error
    import urllib.request

    requete = urllib.request.Request(
        URL_DERNIERE_RELEASE, headers={"User-Agent": f"{APP_NOM}/{VERSION}"}
    )
    try:
        with urllib.request.urlopen(requete, timeout=5) as reponse:
            donnees = json.loads(reponse.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as erreur:
        logging.info("Vérification des mises à jour impossible : %s", erreur)
        return None
    tag = donnees.get("tag_name", "")
    url = donnees.get("html_url", "")
    if tag and url and _version_plus_recente(tag, VERSION):
        return tag.lstrip("vV"), url
    return None


# --------------------------------------------------------------------------
# Couche vocale : client contrôleur NVDA
# --------------------------------------------------------------------------

class Voix:
    """
    Parle via le client contrôleur de NVDA, et par un second canal pour
    JAWS (voir _charger_jaws).

    La DLL n'est PAS fournie avec NVDA : il faut la télécharger séparément
    (voir README.md) et la déposer à côté de ce script, ou dans un
    sous-dossier « dll ». En son absence l'application fonctionne
    normalement, simplement sans annonce automatique.
    """

    NOMS_DLL = (
        "nvdaControllerClient64.dll",
        "nvdaControllerClient.dll",
        "nvdaControllerClient32.dll",
    )

    # Identifiants COM candidats pour JAWS. Contrairement à la DLL NVDA,
    # il n'existe pas de SDK officiel simple et unique pour ça : ce sont
    # les identifiants les plus couramment cités pour l'automatisation
    # JAWS, jamais vérifiés avec un JAWS réel faute d'accès à l'un ou
    # l'autre. D'où la liste (on essaie chacun) plutôt qu'un seul nom
    # supposé certain, et la dégradation silencieuse si aucun ne répond.
    NOMS_COM_JAWS = (
        "FreedomScientific.JawsApi",
        "jfwapi.JawsApi",
    )

    # Au delà, on n'envoie pas le texte a l'afficheur braille : un message
    # braille long chasse ce que l'utilisateur est en train de lire.
    LIMITE_BRAILLE = 120

    def __init__(self, muet: bool = False):
        self.muet = muet
        self._dll = None
        self._jaws = None
        if muet:
            logging.info("Couche vocale désactivée (option --muet).")
            return
        self._charger()
        self._charger_jaws()

    # Dossiers volumineux qu'il est inutile de parcourir.
    IGNORER = {"venv", "build", "dist", ".git", "__pycache__", "node_modules"}

    def _candidats(self, base: Path) -> list[Path]:
        """Cherche la DLL n'importe ou sous le dossier du projet.

        Les archives de NV Access rangent les binaires dans des dossiers
        x86, x64 et arm64, souvent eux-mêmes dans un dossier portant le nom
        de l'archive. On ne fait donc aucune hypothèse sur la profondeur.
        """
        trouves: list[Path] = []
        for chemin in base.rglob("nvdaControllerClient*.dll"):
            relatif = chemin.relative_to(base)
            if any(part in self.IGNORER for part in relatif.parts):
                continue
            if chemin.is_file() and chemin not in trouves:
                trouves.append(chemin)

        def rang(p: Path) -> int:
            nom = p.name.lower()
            if "64" in nom:
                return 0      # on préfère le 64 bits
            if "32" in nom:
                return 2
            return 1

        trouves.sort(key=rang)
        return trouves

    def _charger(self) -> None:
        base = dossier_base()
        candidats = self._candidats(base)

        if candidats:
            logging.info(
                "Fichiers candidats repérés : %s",
                ", ".join(str(c) for c in candidats),
            )

        for chemin in candidats:
            try:
                dll = ctypes.windll.LoadLibrary(str(chemin))
            except OSError:
                logging.exception(
                    "Fichier trouvé mais chargement impossible : %s "
                    "(architecture incompatible ? Python est en 64 bits, "
                    "il faut la DLL du dossier x64)", chemin,
                )
                continue

            dll.nvdaController_speakText.argtypes = [ctypes.c_wchar_p]
            dll.nvdaController_brailleMessage.argtypes = [ctypes.c_wchar_p]
            self._dll = dll
            logging.info("Client contrôleur NVDA chargé : %s", chemin)
            self._diagnostiquer()
            return

        logging.warning(
            "Aucun fichier nvdaControllerClient*.dll sous %s, à quelque "
            "profondeur que ce soit. L'application fonctionne, mais sans "
            "annonce automatique. Voir README.md.", base,
        )

    def _diagnostiquer(self) -> None:
        try:
            code = self._dll.nvdaController_testIfRunning()
        except Exception:
            logging.exception("Appel de testIfRunning impossible.")
            return
        if code == 0:
            logging.info("NVDA répond : annonce automatique opérationnelle.")
        else:
            logging.warning(
                "NVDA ne répond pas (code %s). La DLL est chargée mais NVDA "
                "n'est probablement pas lancé.", code,
            )

    def _charger_jaws(self) -> None:
        """Canal JAWS, en parallèle du client NVDA.

        Repose sur l'interface COM d'automatisation de JAWS (SayString),
        jamais testée avec un JAWS réel — voir NOMS_COM_JAWS. Si pywin32
        est absent, si JAWS n'est pas installé, ou si l'identifiant COM
        est incorrect, ceci échoue en silence exactement comme l'absence
        de la DLL NVDA : aucune conséquence pour un utilisateur NVDA.
        """
        try:
            import win32com.client
        except ImportError:
            logging.info("pywin32 absent : canal JAWS indisponible.")
            return

        for prog_id in self.NOMS_COM_JAWS:
            try:
                jaws = win32com.client.Dispatch(prog_id)
                jaws.SayString("", False)  # confirme que l'appel ne lève pas
            except Exception:
                continue
            self._jaws = jaws
            logging.info("Client JAWS chargé via %s.", prog_id)
            return

        logging.info(
            "JAWS non détecté (aucun identifiant COM connu n'a répondu) : "
            "canal JAWS inactif."
        )

    @property
    def disponible(self) -> bool:
        return (self._dll is not None or self._jaws is not None) and not self.muet

    def dire(
        self,
        texte: str = "",
        braille: str | None = None,
        interrompre: bool = False,
    ) -> None:
        """Parle, affiche en braille, ou les deux.

        Le braille passe par un canal séparé que cancelSpeech ne touche
        pas : un message court y reste lisible même quand la parole a été
        interrompue. On y envoie donc un statut compact plutôt que le
        texte intégral.

        interrompre reste a False par défaut : couper la parole systema-
        tiquement fait entrer nos annonces en concurrence avec celles que
        NVDA produit lui-même sur les changements de focus.
        """
        if not self.disponible:
            return
        if self._dll is not None:
            try:
                if texte:
                    if interrompre:
                        self._dll.nvdaController_cancelSpeech()
                    self._dll.nvdaController_speakText(texte)
                message = braille if braille is not None else texte
                if message and len(message) <= self.LIMITE_BRAILLE:
                    self._dll.nvdaController_brailleMessage(message)
            except Exception:
                logging.exception("Échec de l'annonce NVDA.")
        # JAWS n'a pas d'équivalent confirmé du canal braille séparé de
        # NVDA : seule la parole passe par ce canal, JAWS gérant son
        # afficheur braille lui-même via son suivi de focus habituel.
        if self._jaws is not None and texte:
            try:
                self._jaws.SayString(texte, interrompre)
            except Exception:
                logging.exception("Échec de l'annonce JAWS.")

    def taire(self) -> None:
        if not self.disponible:
            return
        if self._dll is not None:
            try:
                self._dll.nvdaController_cancelSpeech()
            except Exception:
                logging.exception("Échec de l'interruption de la parole (NVDA).")
        if self._jaws is not None:
            try:
                self._jaws.StopSpeech()
            except Exception:
                logging.exception("Échec de l'interruption de la parole (JAWS).")


def bip_travail() -> None:
    """Signal pour une commande qui dure : deux notes montantes.

    Distinct des bips de fin, qui sont d'une seule note. Assez long pour
    être perçu : à 40 ms les deux notes se confondaient en un seul clic.
    """
    def jouer():
        try:
            import winsound
            # Le silence intercalé est indispensable : deux notes
            # enchaînées sans interruption sont perçues comme un seul son
            # qui monte, et non comme deux bips.
            winsound.Beep(400, 80)
            time.sleep(0.09)
            winsound.Beep(600, 80)
        except Exception:
            pass

    threading.Thread(target=jouer, daemon=True).start()


def bip(succes: bool) -> None:
    """Signal sonore court, joué dans un thread pour ne pas figer l'interface."""
    def jouer():
        try:
            import winsound
            if succes:
                winsound.Beep(660, 70)
            else:
                winsound.Beep(340, 90)
                winsound.Beep(280, 110)
        except Exception:
            pass

    threading.Thread(target=jouer, daemon=True).start()


# --------------------------------------------------------------------------
# Modèle de blocs
# --------------------------------------------------------------------------

# Signaux POSIX standards (1-31), pour reconnaître un code de retour de la
# forme 128 + numéro du signal — la convention par laquelle un shell
# distant signale qu'un processus a été tué plutôt que d'avoir échoué de
# lui-même. kill sans option envoie SIGTERM (15), d'où le 143 courant.
NOMS_SIGNAUX = {
    1: "SIGHUP", 2: "SIGINT", 3: "SIGQUIT", 4: "SIGILL", 5: "SIGTRAP",
    6: "SIGABRT", 7: "SIGBUS", 8: "SIGFPE", 9: "SIGKILL", 10: "SIGUSR1",
    11: "SIGSEGV", 12: "SIGUSR2", 13: "SIGPIPE", 14: "SIGALRM", 15: "SIGTERM",
    16: "SIGSTKFLT", 17: "SIGCHLD", 18: "SIGCONT", 19: "SIGSTOP", 20: "SIGTSTP",
    21: "SIGTTIN", 22: "SIGTTOU", 23: "SIGURG", 24: "SIGXCPU", 25: "SIGXFSZ",
    26: "SIGVTALRM", 27: "SIGPROF", 28: "SIGWINCH", 29: "SIGIO", 30: "SIGPWR",
    31: "SIGSYS",
}


def libelle_signal(code_retour: int) -> str | None:
    """Décrit un code de retour comme un arrêt par signal, si c'est le cas.

    Renvoie None hors de cette plage : on n'invente pas un signal pour un
    vrai code d'erreur applicatif (1 à 127), qui reste annoncé comme une
    erreur. Un code 128 + N n'est PAS forcément un échec — c'est aussi la
    trace normale d'un arrêt demandé depuis l'extérieur : un kill explicite
    d'un autre processus par la commande elle-même, un conteneur arrêté
    proprement... On ne tranche donc pas à la place de l'utilisateur, on
    nomme juste ce qui s'est passé au lieu de crier « erreur » à tort.
    """
    numero = code_retour - 128
    if not 0 < numero <= 31:
        return None
    nom = NOMS_SIGNAUX.get(numero)
    return f"signal {numero}, {nom}" if nom else f"signal {numero}"


@dataclass
class Bloc:
    numero: int
    commande: str
    sortie: str
    code_retour: int
    session: str
    horodatage: datetime = field(default_factory=datetime.now)
    debut: int = 0   # position de départ dans le champ de sortie
    fin: int = 0
    duree: float = 0.0
    interrompue: bool = False

    @property
    def nb_lignes(self) -> int:
        return len(self.sortie.splitlines()) if self.sortie else 0

    def entete(self, avec_heure: bool = False) -> str:
        """Ligne d'en-tête du bloc.

        C'est la ligne sur laquelle se pose le curseur en navigation, donc
        celle que NVDA lit : elle doit porter l'information utile. La
        commande vient donc en premier, l'heure est optionnelle, et le code
        de retour n'apparaît que s'il est non nul — sur une commande qui a
        réussi, le silence est le signal.
        """
        morceaux = [f"Bloc {self.numero}"]
        if avec_heure:
            morceaux.append(self.horodatage.strftime("%H:%M:%S"))

        # Une commande multiligne est repliée : un saut de ligne dans
        # l'en-tête casserait la ligne que NVDA doit lire d'un bloc.
        commande = " ; ".join(
            ligne.strip() for ligne in self.commande.splitlines() if ligne.strip()
        )
        if len(commande) > LONGUEUR_COMMANDE_ENTETE:
            commande = commande[:LONGUEUR_COMMANDE_ENTETE].rstrip() + "..."
        morceaux.append(commande)

        # Un code de retour négatif après interruption n'a aucun sens
        # pour l'utilisateur : c'est le signal qui a tué le processus.
        if self.interrompue:
            morceaux.append("interrompue")
        elif self.code_retour != 0:
            morceaux.append(libelle_signal(self.code_retour) or f"erreur {self.code_retour}")

        n = self.nb_lignes
        morceaux.append(decompte(n))
        if not self.interrompue and self.duree >= SEUIL_DUREE_ANNONCEE:
            morceaux.append(f"{self.duree:.0f} s")
        return ", ".join(morceaux)

    def rendu(self, avec_heure: bool = False) -> str:
        """Texte inséré dans le champ de sortie. Aucun caractère décoratif :
        une ligne de tirets est illisible en vocal comme en braille."""
        morceaux = [self.entete(avec_heure)]
        # Seule une commande multiligne perd de l'information réelle en
        # entête (les retours à la ligne, repliés en « ; »). Une commande
        # longue mais sur une seule ligne n'y perd rien de structurel :
        # la répéter en entier ici ne ferait que doubler ce qui vient
        # d'être dit, avant même d'atteindre la sortie.
        if "\n" in self.commande.strip():
            morceaux.append(f"Commande complète :\n{self.commande}")
        if self.sortie:
            morceaux.append(self.sortie.rstrip("\n"))
        morceaux.append("")
        return "\n".join(morceaux) + "\n"

    def texte_complet(self) -> str:
        """Ce que Ctrl+Maj+C (et Ctrl+Maj+L) placent dans le presse-papiers.

        Le code de retour n'y figurait jamais : une commande en échec sans
        rien écrire en sortie (interrompue avant d'avoir produit quoi que
        ce soit, par exemple) se copiait comme si elle avait réussi en
        silence — l'information la plus utile disparaissait au collage.
        """
        morceaux = [self.commande]
        if self.interrompue:
            morceaux.append("[interrompue]")
        elif self.code_retour != 0:
            signal = libelle_signal(self.code_retour)
            statut = signal if signal else f"code de retour : {self.code_retour}"
            morceaux.append(f"[{statut}]")
        if self.sortie:
            morceaux.append(self.sortie)
        return "\n".join(morceaux).rstrip() + "\n"

    def libelle_liste(self) -> str:
        heure = self.horodatage.strftime("%H:%M:%S")
        if self.code_retour == 0:
            etat = "ok"
        else:
            etat = libelle_signal(self.code_retour) or f"erreur {self.code_retour}"
        commande = " ; ".join(
            ligne.strip() for ligne in self.commande.splitlines() if ligne.strip()
        )
        n = self.nb_lignes
        decompte = "1 ligne" if n == 1 else f"{n} lignes"
        return f"{self.numero}. {heure} — {commande} — {decompte} — {etat}"


def decompte(n: int) -> str:
    """Accord de « ligne » : « 1 ligne » et non « 1 lignes »."""
    return "1 ligne" if n == 1 else f"{n} lignes"


def _pour_la_voix(ligne: str) -> str:
    """Rend une ligne de sortie prononçable.

    Le marqueur [erreur] est utile à l'écran et en braille, mais lu tel
    quel il donne « crochet ouvrant erreur crochet fermant ».
    """
    if ligne.startswith("[erreur] "):
        return ligne[len("[erreur] "):]
    return ligne


def composer_annonce(bloc: Bloc, verbosite: int) -> str:
    """Applique les règles adaptatives décidées avec l'utilisateur.

    Le statut passe TOUJOURS en premier : on peut ainsi couper la parole
    des qu'on sait que la commande a réussi, sans subir toute la sortie.
    """
    lignes = [_pour_la_voix(l) for l in bloc.sortie.splitlines()]
    nb = len(lignes)

    if bloc.interrompue:
        return f"Interrompue après {bloc.duree:.0f} secondes."

    if bloc.code_retour != 0:
        signal = libelle_signal(bloc.code_retour)
        if signal:
            tete = f"Terminée, {signal}, {decompte(nb)}."
        else:
            tete = f"Erreur, code {bloc.code_retour}, {decompte(nb)}."
        if not lignes:
            return tete
        extrait = lignes[:LIGNES_ERREUR]
        suite = "" if nb <= LIGNES_ERREUR else f" Et {nb - LIGNES_ERREUR} lignes de plus."
        return tete + " " + " ".join(extrait) + suite

    # Sur une commande qui a réussi, le bip a déjà dit « c'est fini, tout
    # va bien » — instantanément, et sans occuper la parole. Répéter
    # « Terminé, N lignes » avant chaque sortie ne fait que retarder
    # l'information utile. On entre donc directement dans le contenu.
    #
    # Absence de sortie mise à part : là, il n'y a pas de contenu à
    # enchaîner derrière un « Terminé » sec, qui se confond facilement
    # avec un silence de la synthèse ou une commande encore en cours. Le
    # dire explicitement lève l'ambiguïté.
    if nb == 0:
        return "Terminé, aucune sortie."

    if nb <= SEUIL_LECTURE_INTEGRALE or verbosite == VERBOSITE_TOUT:
        return " ".join(lignes)

    if verbosite == VERBOSITE_RESUME:
        return decompte(nb) + "."

    extrait = lignes[:LIGNES_APERCU]
    return (
        " ".join(extrait)
        + f" Et {nb - LIGNES_APERCU} lignes de plus."
    )


def copier_presse_papiers(texte: str) -> bool:
    if not wx.TheClipboard.Open():
        logging.warning("Presse-papiers inaccessible.")
        return False
    try:
        wx.TheClipboard.SetData(wx.TextDataObject(texte))
        wx.TheClipboard.Flush()
    finally:
        wx.TheClipboard.Close()
    return True


def _joindre_chemin_distant(base: str, nom: str) -> str:
    """Chemins POSIX toujours en « / », jamais via les fonctions de
    Path qui utiliseraient le séparateur Windows sur cette machine."""
    base = base.rstrip("/")
    return f"{base}/{nom}" if base else f"/{nom}"


def _parent_chemin_distant(chemin: str) -> str:
    return chemin.rsplit("/", 1)[0] or "/"


def _libelle_entree_sftp(entree: EntreeDistante) -> str:
    """Libellé d'une ligne du navigateur de fichiers distant. Mêmes
    intitulés que le listing amélioré local et SSH (dossier/fichier/lien),
    pour rester cohérent d'un bout à l'autre de l'appli."""
    if entree.dossier:
        genre = "lien vers un dossier" if entree.lien else "dossier"
        return f"{entree.nom} — {genre}"
    genre = "lien" if entree.lien else "fichier"
    modifie = entree.modifie.strftime("%d/%m/%Y %H:%M") if entree.modifie else ""
    return f"{entree.nom} — {genre} — {entree.taille} octets — {modifie}"


def _libelle_resultat_recherche(chemin: str, entree: EntreeDistante) -> str:
    """Libellé d'une ligne de DialogueRechercheFichiers : le chemin
    complet plutôt que le seul nom (repris de _libelle_entree_sftp), un
    résultat pouvant venir de n'importe quelle profondeur sous le
    dossier de recherche."""
    if entree.dossier:
        genre = "lien vers un dossier" if entree.lien else "dossier"
        return f"{chemin} — {genre}"
    genre = "lien" if entree.lien else "fichier"
    return f"{chemin} — {genre} — {entree.taille} octets"


# --------------------------------------------------------------------------
# Transferts (SFTP)
# --------------------------------------------------------------------------

def _formater_vitesse(octets_par_seconde: float) -> str:
    if octets_par_seconde >= 1024 * 1024:
        return f"{octets_par_seconde / (1024 * 1024):.1f} Mo/s"
    if octets_par_seconde >= 1024:
        return f"{octets_par_seconde / 1024:.0f} Ko/s"
    return f"{octets_par_seconde:.0f} o/s"


@dataclass
class Transfert:
    """Un envoi ou une réception, en attente puis en cours. nom est le
    fichier ou dossier choisi au départ ; fichier_actuel est celui
    réellement en train de passer — le même que nom pour un fichier
    seul, celui du moment pour un dossier (change en cours de route)."""
    numero: int
    direction: str  # "envoi" ou "reception"
    nom: str
    chemin_local: str
    chemin_distant: str
    dossier: bool = False
    etat: str = "en attente"  # "en attente"/"en cours"/"terminé"/"échoué"/"annulé"
    fichier_actuel: str = ""
    pourcentage: int | None = None
    vitesse: str = ""
    erreur: str = ""

    def libelle(self) -> str:
        # Le pourcentage n'est plus répété ici : il vit dans le titre de
        # DialogueProgression (voir titre_fenetre), pas la peine de
        # l'avoir deux fois dans la même fenêtre.
        verbe = "Envoi" if self.direction == "envoi" else "Réception"
        morceaux = [f"{verbe} : {self.fichier_actuel or self.nom}", self.etat]
        if self.etat == "en cours" and self.vitesse:
            morceaux.append(self.vitesse)
        if self.etat == "échoué" and self.erreur:
            morceaux.append(self.erreur)
        return ", ".join(morceaux)

    def titre_fenetre(self) -> str:
        """Titre de DialogueProgression : « x% nom » pendant le transfert
        (le format demandé), un état en toutes lettres avant que le
        premier pourcentage soit connu ou une fois le transfert dans un
        état final."""
        nom = self.fichier_actuel or self.nom
        if self.etat == "en cours" and self.pourcentage is not None:
            return f"{self.pourcentage}% {nom}"
        etats = {
            "en attente": "En attente",
            "en cours": "Démarrage",
            "terminé": "Terminé",
            "échoué": "Échoué",
            "annulé": "Annulé",
        }
        return f"{etats.get(self.etat, self.etat)} : {nom}"


# --------------------------------------------------------------------------
# Réglages partages
# --------------------------------------------------------------------------

class Reglages:
    """État de configuration commun à la fenêtre et à toutes les sessions.

    Passer par un objet partagé évite que les panneaux aient besoin d'une
    référence remontante vers la fenêtre.
    """

    def __init__(self):
        donnees = charger_reglages()
        self.sons = True
        try:
            verbosite = int(donnees.get("verbosite", VERBOSITE_RESUME_PLUS))
            self.verbosite = verbosite if verbosite in NOMS_VERBOSITE else VERBOSITE_RESUME_PLUS
        except (TypeError, ValueError):
            self.verbosite = VERBOSITE_RESUME_PLUS
        self.afficher_horodatage = bool(donnees.get("afficher_horodatage", False))
        self.suivre_sortie = bool(donnees.get("suivre_sortie", False))
        try:
            taille = int(donnees.get("taille_police", TAILLE_POLICE_DEFAUT))
        except (TypeError, ValueError):
            taille = TAILLE_POLICE_DEFAUT
        self.taille_police = max(TAILLE_POLICE_MIN, min(TAILLE_POLICE_MAX, taille))


# --------------------------------------------------------------------------
# Commandes enregistrées
# --------------------------------------------------------------------------

@dataclass
class CommandeEnregistree:
    nom: str
    commande: str


def _chemin_commandes() -> Path:
    return dossier_base() / "commands.json"


def charger_commandes() -> list[CommandeEnregistree]:
    chemin = _chemin_commandes()
    if not chemin.exists():
        return []
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
        return [CommandeEnregistree(**d) for d in donnees]
    except (OSError, json.JSONDecodeError, TypeError):
        logging.exception("Commandes enregistrées illisibles, ignorées : %s", chemin)
        return []


def enregistrer_commandes(commandes: list[CommandeEnregistree]) -> None:
    chemin = _chemin_commandes()
    donnees = [asdict(c) for c in commandes]
    try:
        chemin.write_text(
            json.dumps(donnees, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        logging.exception("Impossible d'enregistrer les commandes.")


# --------------------------------------------------------------------------
# Panneau d'une session
# --------------------------------------------------------------------------


class PanneauSession(wx.Panel):
    """Une session = un onglet = un champ de saisie, un champ de sortie,
    un historique et une liste de blocs qui lui sont propres."""

    def __init__(
        self, parent, nom: str, voix: Voix, reglages: Reglages,
        executeur=None, distant: bool = False, profil: ProfilConnexion | None = None,
    ):
        super().__init__(parent)
        self.nom = nom
        self.voix = voix
        self.reglages = reglages
        self.blocs: list[Bloc] = []
        self.historique: list[str] = []
        self.index_historique = 0
        # Textes mis de côté pendant la navigation dans l'historique.
        # La clé len(historique) correspond à la saisie en cours, qui
        # n'a pas encore été envoyée.
        self._brouillons: dict[int, str] = {}
        self.executeur = executeur if executeur is not None else ExecuteurLocal()
        # Une session distante n'a pas de répertoire local à proposer par
        # défaut, et « changer de répertoire » y demande un chemin tapé
        # plutôt qu'un dossier parcouru sur cette machine.
        self.distant = distant
        # Profil d'origine de la connexion (SSH seulement) : sert à lire
        # et sauvegarder ses dossiers favoris (voir aller_au_favori /
        # sauvegarder_favoris plus bas).
        self.profil = profil
        self.en_cours = False
        self.repertoire = "" if distant else str(Path.home())
        self._commande_en_cours = ""
        self._debut = 0.0
        self._invite_boite: wx.TextEntryDialog | None = None
        # Mode navigation (SSH seulement) : bascule saisie+sortie contre un
        # navigateur SFTP. self.repertoire sert de chemin courant aux deux
        # modes, pour que l'un reprenne où l'autre s'est arrêté.
        self.mode_navigation = False
        self._entrees_sftp: list[EntreeDistante] = []

        # File d'attente des transferts (SSH seulement) : un fil de
        # travail dédié les traite un par un, dans l'ordre — jamais deux
        # à la fois, le canal SFTP partagé de la navigation ne s'y prête
        # pas (voir ExecuteurSSH.telecharger_dossier).
        self.transferts: list[Transfert] = []
        self._compteur_transferts = 0
        self._file_transferts: queue.Queue[Transfert] = queue.Queue()
        # Le dernier transfert démarré (terminé ou non) : ce que montre
        # la fenêtre de progression, toujours au premier plan et rouverte
        # automatiquement au prochain transfert si l'utilisateur l'a fermée.
        self.transfert_actuel: Transfert | None = None
        self.fenetre_progression: DialogueProgression | None = None
        if distant:
            threading.Thread(
                target=self._travailleur_transferts, daemon=True
            ).start()

        self.etiquette_saisie = wx.StaticText(self, label=f"&Commande — {nom} :")
        # Multiligne pour accepter les commandes sur plusieurs lignes.
        # Entrée envoie, Maj+Entrée saute une ligne : on gère les deux
        # dans sur_touche_saisie plutôt que par TE_PROCESS_ENTER, dont le
        # comportement sur un contrôle multiligne varie selon les versions.
        self.saisie = wx.TextCtrl(self, style=wx.TE_MULTILINE)
        self.saisie.SetName(f"Commande, {nom}")
        self.saisie.SetMinSize((-1, 64))

        self.etiquette_sortie = wx.StaticText(self, label=f"&Sortie — {nom} :")
        self.sortie = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.TE_DONTWRAP,
        )
        self.sortie.SetName(f"Sortie, {nom}")

        self.etiquette_fichiers = wx.StaticText(self, label=f"&Fichiers distants — {nom} :")
        self.liste_fichiers = wx.ListBox(self)
        self.liste_fichiers.SetName(f"Fichiers distants, {nom}")
        self.etiquette_fichiers.Hide()
        self.liste_fichiers.Hide()

        self.appliquer_taille_police()

        boite = wx.BoxSizer(wx.VERTICAL)
        boite.Add(self.etiquette_saisie, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
        boite.Add(self.saisie, 0, wx.EXPAND | wx.ALL, 6)
        boite.Add(self.etiquette_sortie, 0, wx.LEFT | wx.RIGHT, 6)
        boite.Add(self.sortie, 1, wx.EXPAND | wx.ALL, 6)
        boite.Add(self.etiquette_fichiers, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
        boite.Add(self.liste_fichiers, 1, wx.EXPAND | wx.ALL, 6)
        self.SetSizer(boite)

        self.saisie.Bind(wx.EVT_KEY_DOWN, self.sur_touche_saisie)
        self.sortie.Bind(wx.EVT_CHAR, self.sur_frappe_dans_sortie)
        # Pas de EVT_KEY_DOWN local sur liste_fichiers, à la différence de
        # saisie ci-dessus : vérifié par un test isolé, wx.ListBox ne
        # génère tout simplement pas cet évènement pour Entrée ni les
        # flèches (consommées en interne par le contrôle natif avant
        # d'atteindre le niveau événementiel de wx). Entrée/Retour
        # arrière/Suppr/F2 pour ce contrôle sont donc gérés dans
        # Fenetre.sur_touche_globale (EVT_CHAR_HOOK, qui lui reçoit ces
        # touches de façon fiable), pas ici.

    def appliquer_taille_police(self) -> None:
        """Reconstruit et repose la police sur les champs, à la taille
        actuellement réglée. Appelé à la création du panneau, et de
        nouveau par Fenetre sur chaque session ouverte quand la taille
        change en cours d'usage."""
        police = wx.Font(
            wx.FontInfo(self.reglages.taille_police).Family(wx.FONTFAMILY_TELETYPE)
        )
        self.saisie.SetFont(police)
        self.sortie.SetFont(police)
        self.liste_fichiers.SetFont(police)

    # -- saisie ------------------------------------------------------------

    def envoyer(self) -> None:
        commande = self.saisie.GetValue().strip()
        if not commande:
            return
        self.saisie.SetValue("")
        self.historique.append(commande)
        self.index_historique = len(self.historique)
        self._brouillons.clear()
        self.executer(commande)

    def _ligne_logique(self) -> tuple[int, int]:
        """Position du curseur en lignes reelles, et nombre de sauts.

        On compte les sauts de ligne du texte plutot que d'interroger le
        controle : sous Windows, une zone de texte qui replie les lignes
        longues compte les lignes AFFICHEES, pas les lignes reelles. Une
        commande longue repliee sur trois lignes a l'ecran serait alors
        prise pour une commande multiligne, et les fleches cesseraient de
        rappeler l'historique — precisement le cas le plus frequent.
        """
        texte = self.saisie.GetValue()
        position = self.saisie.GetInsertionPoint()
        return texte[:position].count("\n"), texte.count("\n")

    def _rappeler_historique(self, delta: int) -> None:
        """Navigue dans l'historique sans jamais perdre le texte courant.

        Avant de changer de position, le contenu du champ est mis de côté
        à sa position actuelle. Une commande en cours de frappe est donc
        retrouvée intacte en redescendant, et une modification apportée à
        une commande rappelée survit à un aller-retour.
        """
        if not self.historique:
            return
        cible = self.index_historique + delta
        if cible < 0:
            self.voix.dire("Début de l'historique.", interrompre=True)
            return
        if cible > len(self.historique):
            return

        self._brouillons[self.index_historique] = self.saisie.GetValue()
        self.index_historique = cible

        texte = self._brouillons.get(cible)
        if texte is None:
            texte = "" if cible == len(self.historique) else self.historique[cible]
        self.saisie.SetValue(texte)
        self.saisie.SetInsertionPointEnd()

        # SetValue ne déclenche aucune annonce : sans cela, on ne sait pas
        # ce qui vient d'être rappelé.
        if cible == len(self.historique):
            self.voix.dire(
                texte if texte else "Saisie vide.",
                braille=texte or "(vide)",
                interrompre=True,
            )
        else:
            rang = len(self.historique) - cible
            self.voix.dire(texte, braille=f"{rang}: {texte}", interrompre=True)

    def sur_touche_saisie(self, evt):
        code = evt.GetKeyCode()
        maj = evt.ShiftDown()
        ctrl = evt.ControlDown()

        if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            if maj:
                evt.Skip()          # Maj+Entrée : saut de ligne
                return
            self.envoyer()
            return

        # L'historique ne prend la main qu'aux extrémités du texte : au
        # milieu d'une commande multiligne, les flèches doivent déplacer
        # le curseur normalement.
        if code == wx.WXK_UP and not ctrl and not maj:
            ligne, _sauts = self._ligne_logique()
            if ligne == 0:
                self._rappeler_historique(-1)
                return

        if code == wx.WXK_DOWN and not ctrl and not maj:
            ligne, sauts = self._ligne_logique()
            if ligne >= sauts:
                self._rappeler_historique(1)
                return

        evt.Skip()

    def repeter_saisie(self) -> None:
        """Relit le contenu du champ de saisie, sans rien déplacer.

        Les flèches haut et bas servant à l'historique, il n'y avait plus
        moyen de se faire relire une commande en cours de composition.
        """
        texte = self.saisie.GetValue()
        if not texte.strip():
            self.voix.dire("Saisie vide.", braille="(vide)", interrompre=True)
            return
        self.voix.dire(texte, braille=texte, interrompre=True)

    def sur_frappe_dans_sortie(self, evt):
        """Une frappe dans le champ de sortie bascule vers la saisie.

        Le champ étant en lecture seule, la frappe serait perdue en
        silence — on ne s'en aperçoit qu'après avoir tape une phrase
        entière. On redirige donc le focus et on réinjecte le caractère.
        """
        if evt.ControlDown() or evt.AltDown():
            evt.Skip()
            return

        caractere = evt.GetUnicodeKey()
        if caractere == wx.WXK_NONE or caractere < 32:
            evt.Skip()              # flèches, tabulation, Échap, etc.
            return

        self.saisie.SetFocus()
        self.saisie.SetInsertionPointEnd()
        self.saisie.WriteText(chr(caractere))

    # -- exécution -----------------------------------------------------------

    def executer(self, commande: str) -> None:
        """Lance la commande dans un thread et rend la main aussitôt.

        Rien d'autre ne doit se produire ici : toute attente dans le
        thread principal figerait l'interface, ce qui pour un utilisateur
        de lecteur d'écran équivaut à une application morte.
        """
        if self.en_cours:
            self.voix.dire(
                "Une commande est déjà en cours. Ctrl+Maj+K pour l'interrompre.",
                interrompre=True,
            )
            return

        self.en_cours = True
        self.saisie.SetEditable(False)
        self._commande_en_cours = commande
        self._debut = time.monotonic()
        self.rafraichir_statut()

        def travailler():
            resultat = self.executeur.executer(
                commande,
                repertoire=self.repertoire,
                sur_lenteur=lambda: wx.CallAfter(self._signaler_lenteur),
                sur_invite=self._repondre_invite,
                # Toujours vrai désormais : plus de réglage pour le
                # désactiver (voir décision correspondante, CLAUDE.md) —
                # le mode navigation couvre le besoin de parcourir sans
                # cette réécriture.
                listing_lisible=True,
            )
            wx.CallAfter(self._commande_terminee, commande, resultat)

        threading.Thread(target=travailler, daemon=True).start()

    def definir_repertoire(self, repertoire: str) -> None:
        """Change le répertoire courant et rafraîchit le titre aussitôt,
        que ce soit depuis le dialogue Ctrl+Maj+D ou depuis le pwd
        silencieux lancé à la connexion d'une session SSH."""
        self.repertoire = repertoire
        self.rafraichir_statut()

    def rafraichir_statut(self) -> None:
        """Reflète l'état de CETTE session dans la barre de statut et le
        titre de la fenêtre.

        La barre de statut reste affichée en continu, contrairement au
        bip et au braille fugace de _signaler_lenteur — mais rien ne la
        lit au retour d'un Alt+Tab. Le titre, lui, est ce que Windows et
        NVDA annoncent quand la fenêtre reprend le focus : c'est donc lui
        qui porte l'information « une commande tourne encore », pas de
        caractère décoratif, juste du texte. Le répertoire courant y
        figure aussi (local ou distant) : sans lui, on perd le fil de
        l'endroit où on se trouve après un Alt+Tab ou un changement
        d'onglet, en local comme en SSH.
        """
        fenetre = self.GetTopLevelParent()
        if fenetre.session() is not self:
            return
        nom = f"{self.nom} (commande en cours)" if self.en_cours else self.nom
        segments = [nom]
        if self.repertoire:
            segments.append(self.repertoire)
        segments.append(APP_NOM)
        fenetre.SetTitle(" — ".join(segments))
        if self.en_cours:
            resume = self._commande_en_cours.splitlines()[0].strip()
            if len(resume) > 60:
                resume = resume[:60].rstrip() + "..."
            fenetre.SetStatusText(f"Commande en cours : {resume}")
        else:
            fenetre.SetStatusText("Prêt")

    def _signaler_lenteur(self) -> None:
        """Appelée depuis le thread de travail via CallAfter.

        La barre de statut (rafraichir_statut) reste affichée mais n'est
        pas annoncée automatiquement par NVDA : sans un mot prononcé ici,
        rien n'indique qu'une commande est toujours en cours au-delà du
        bip. On ne le fait qu'après ce seuil de lenteur, pas dès le
        départ, pour ne pas parler par-dessus chaque commande rapide.
        """
        if not self.en_cours:
            return
        self.voix.dire("Commande en cours.", braille="En cours...")
        if self.reglages.sons:
            bip_travail()

    def _repondre_invite(self, texte: str) -> str | None:
        """Appelée depuis le thread d'exécution : ouvre une boîte de
        dialogue accessible et bloque jusqu'à la réponse.

        Un contrôle Win32 natif est lu directement par NVDA dès qu'il
        prend le focus : aucune annonce manuelle n'est nécessaire ici.
        """
        logging.info("Invite de saisie détectée : %s", texte)
        resultat: dict[str, str | None] = {"valeur": None}
        evenement = threading.Event()

        def ouvrir():
            style = wx.OK | wx.CANCEL | wx.CENTRE
            if MOTIF_MOT_DE_PASSE.search(texte):
                style |= wx.TE_PASSWORD
            boite = wx.TextEntryDialog(
                self, texte, f"Saisie attendue — {self.nom}", style=style,
            )
            self._invite_boite = boite
            code = boite.ShowModal()
            resultat["valeur"] = boite.GetValue() if code == wx.ID_OK else None
            boite.Destroy()
            self._invite_boite = None
            evenement.set()

        wx.CallAfter(ouvrir)
        evenement.wait()
        return resultat["valeur"]

    def _commande_terminee(self, commande: str, resultat: Resultat) -> None:
        """Retour dans le thread principal : on peut toucher à l'interface."""
        self.en_cours = False
        self.saisie.SetEditable(True)
        self.rafraichir_statut()

        sortie = resultat.sortie
        if resultat.interrompue:
            sortie = (sortie + "\n" if sortie else "") + "[Commande interrompue.]"

        # durée et interrompue sont posés AVANT ajouter_bloc : l'en-tête
        # et l'annonce s'en servent au moment de la création du bloc.
        self._duree = resultat.duree
        self._interrompue = resultat.interrompue
        bloc = self.ajouter_bloc(commande, sortie, resultat.code_retour)

        # Un message braille est fugace : il s'efface au bout de quelques
        # secondes. En posant le curseur sur la ligne d'en-tête du bloc,
        # le statut devient au contraire durable sous les doigts, puisque
        # l'afficheur suit le curseur.
        if self.reglages.suivre_sortie:
            self.sortie.SetFocus()
            self.sortie.SetInsertionPoint(bloc.debut + 1)
            self.sortie.ShowPosition(bloc.debut + 1)

        # code_retour == -1 signale un échec au lancement même de la
        # commande (voir ExecuteurSSH.executer), jamais un vrai code de
        # sortie distant — un signal assez fiable pour distinguer une
        # commande qui a juste échoué d'une connexion tombée. Vérifié en
        # plus avec connexion_active pour ne pas proposer une
        # reconnexion sur un -1 qui aurait une autre cause.
        if (
            self.distant and not resultat.interrompue
            and resultat.code_retour == -1
            and not self.executeur.connexion_active
        ):
            self._proposer_reconnexion()

    def _proposer_reconnexion(self) -> None:
        if self.profil is None:
            return
        self.voix.dire(f"Connexion perdue à {self.nom}.", interrompre=True)
        if wx.MessageBox(
            f"La connexion à « {self.nom} » semble perdue. Se reconnecter ?",
            "Connexion perdue", wx.YES_NO | wx.ICON_WARNING,
        ) == wx.YES:
            self.GetTopLevelParent().reconnecter_ssh(self)

    def interrompre(self) -> None:
        if not self.en_cours:
            self.voix.dire("Aucune commande en cours.", interrompre=True)
            return
        self.voix.dire("Interruption demandée.", interrompre=True)
        # Une invite en attente laisserait sinon l'utilisateur bloqué sur
        # une boîte de dialogue orpheline pendant que le processus est tué.
        boite = self._invite_boite
        if boite is not None:
            wx.CallAfter(boite.EndModal, wx.ID_CANCEL)
        threading.Thread(target=self.executeur.interrompre, daemon=True).start()

    # -- blocs -------------------------------------------------------------

    def ajouter_bloc(self, commande: str, sortie: str, code_retour: int) -> Bloc:
        bloc = Bloc(
            numero=len(self.blocs) + 1,
            commande=commande,
            sortie=sortie,
            code_retour=code_retour,
            session=self.nom,
            duree=getattr(self, "_duree", 0.0),
            interrompue=getattr(self, "_interrompue", False),
        )
        self._duree, self._interrompue = 0.0, False
        bloc.debut = self.sortie.GetLastPosition()
        self.sortie.AppendText(
            "\n" + bloc.rendu(self.reglages.afficher_horodatage)
        )
        bloc.fin = self.sortie.GetLastPosition()
        self.blocs.append(bloc)

        # Le point d'insertion se place au debut du nouveau bloc : quand on
        # bascule avec F6, on arrive directement sur le contenu frais.
        self.sortie.SetInsertionPoint(bloc.debut + 1)
        self.sortie.ShowPosition(bloc.debut + 1)

        # Le bip part en premier : il est instantane, la synthèse vocale non.
        if self.reglages.sons:
            bip(code_retour == 0)
        if bloc.interrompue:
            statut = "interrompue"
        elif code_retour == 0:
            statut = "ok"
        else:
            statut = libelle_signal(code_retour) or f"erreur {code_retour}"
        annonce = composer_annonce(bloc, self.reglages.verbosite)
        # Le braille reprend l'annonce vocale telle quelle quand elle
        # tient dans la limite : sans ça, un « Terminé, aucune sortie »
        # entendu se voyait réduit à « ok, 0 lignes » en braille, deux
        # formulations différentes pour la même chose. Seule une annonce
        # trop longue (sortie lue en entier) retombe sur le résumé
        # compact, pour ne pas dépasser LIMITE_BRAILLE et perdre le
        # message en braille aussi (voir Voix.dire).
        resume = f"Bloc {bloc.numero}, {statut}, {decompte(bloc.nb_lignes)}"
        braille = annonce if len(annonce) <= Voix.LIMITE_BRAILLE else resume
        self.voix.dire(annonce, braille=braille, interrompre=True)
        return bloc

    @property
    def commande_en_cours(self) -> str:
        return self._commande_en_cours if self.en_cours else ""

    def redessiner(self) -> None:
        """Reconstruit le champ de sortie à partir des blocs.

        Nécessaire quand un réglage d'affichage change : sans cela, la
        bascule ne s'appliquerait qu'aux blocs a venir.
        """
        position = self.sortie.GetInsertionPoint()
        courant = self.bloc_courant()
        self.sortie.SetValue("")
        for bloc in self.blocs:
            bloc.debut = self.sortie.GetLastPosition()
            self.sortie.AppendText(
                "\n" + bloc.rendu(self.reglages.afficher_horodatage)
            )
            bloc.fin = self.sortie.GetLastPosition()
        if courant is not None:
            self.sortie.SetInsertionPoint(courant.debut + 1)
            self.sortie.ShowPosition(courant.debut + 1)
        else:
            self.sortie.SetInsertionPoint(min(position, self.sortie.GetLastPosition()))

    def bloc_courant(self) -> Bloc | None:
        if not self.blocs:
            return None
        position = self.sortie.GetInsertionPoint()
        for bloc in reversed(self.blocs):
            if position >= bloc.debut:
                return bloc
        return self.blocs[0]

    def aller_au_bloc(self, bloc: Bloc) -> None:
        self.sortie.SetFocus()
        self.sortie.SetInsertionPoint(bloc.debut + 1)
        self.sortie.ShowPosition(bloc.debut + 1)
        # Pas de parole ici : en déplaçant le curseur sur la ligne
        # d'en-tête, NVDA la lit de lui-même. Doubler l'annonce revient a
        # entendre deux fois la même chose, ou a ce que l'une coupe l'autre.
        self.voix.dire(braille=bloc.entete())

    # -- mode navigation (SFTP) -----------------------------------------------

    def basculer_mode_navigation(self) -> None:
        """Bascule entre le terminal (saisie + sortie) et le navigateur de
        fichiers distant (liste). Réservé aux sessions SSH : une session
        locale n'a pas de canal SFTP à parcourir."""
        if not self.distant:
            self.voix.dire("Cette action nécessite une session SSH.", interrompre=True)
            return
        self.mode_navigation = not self.mode_navigation
        # sizer.Show(), pas juste window.Show() : sans passer par le
        # sizer, l'espace du contrôle caché resterait réservé, vide, au
        # lieu d'être repris par l'autre mode.
        sizer = self.GetSizer()
        sizer.Show(self.etiquette_saisie, not self.mode_navigation)
        sizer.Show(self.saisie, not self.mode_navigation)
        sizer.Show(self.etiquette_sortie, not self.mode_navigation)
        sizer.Show(self.sortie, not self.mode_navigation)
        sizer.Show(self.etiquette_fichiers, self.mode_navigation)
        sizer.Show(self.liste_fichiers, self.mode_navigation)
        sizer.Layout()
        if self.mode_navigation:
            self.voix.dire("Mode navigation.", interrompre=True)
            self.charger_dossier_sftp(self.repertoire or ".")
        else:
            self.voix.dire("Mode terminal.", interrompre=True)
            self.saisie.SetFocus()

    def charger_dossier_sftp(
        self, chemin: str, nom_a_selectionner: str | None = None,
    ) -> None:
        """(Re)charge un dossier distant dans la liste, en tâche de fond :
        un listage reste un aller-retour réseau, il ne doit pas figer
        l'interface le temps qu'il revienne.

        nom_a_selectionner sélectionne une entrée précise une fois le
        dossier chargé (au lieu de la première par défaut) — utilisé
        par DialogueRechercheFichiers pour amener directement sur le
        fichier trouvé, pas seulement dans son dossier.

        Aussi appelée depuis wx.CallAfter (renommage, suppression,
        création de dossier réussis) : la session peut avoir été fermée
        entre-temps, d'où le garde — même raison que dans
        _dossier_sftp_charge ci-dessous."""
        if not self:
            return
        self.liste_fichiers.Set(["Chargement…"])

        def travailler():
            try:
                reel = self.executeur.chemin_absolu(chemin)
                entrees = self.executeur.lister_repertoire(reel)
            except Exception as erreur:
                logging.exception("Listage SFTP échoué : %s", chemin)
                wx.CallAfter(self._echec_action_sftp, "Listage", erreur)
                wx.CallAfter(self._vider_liste_fichiers_sftp)
                return
            wx.CallAfter(self._dossier_sftp_charge, reel, entrees, nom_a_selectionner)

        threading.Thread(target=travailler, daemon=True).start()

    def _vider_liste_fichiers_sftp(self) -> None:
        if not self:
            return
        self.liste_fichiers.Set([])

    def _dossier_sftp_charge(
        self,
        chemin: str,
        entrees: list[EntreeDistante],
        nom_a_selectionner: str | None = None,
    ) -> None:
        # La session peut avoir été fermée (Ctrl+W) pendant que ce listage
        # était encore en vol côté réseau : le rappel wx.CallAfter arrive
        # quand même, sur un panneau déjà détruit — sans ce garde,
        # toucher liste_fichiers lève « wrapped C/C++ object ... deleted ».
        if not self:
            return
        self._entrees_sftp = entrees
        self.definir_repertoire(chemin)
        self.liste_fichiers.Set([_libelle_entree_sftp(e) for e in entrees])
        if entrees:
            index = 0
            if nom_a_selectionner is not None:
                for i, entree in enumerate(entrees):
                    if entree.nom == nom_a_selectionner:
                        index = i
                        break
            self.liste_fichiers.SetSelection(index)
        if self.mode_navigation:
            self.liste_fichiers.SetFocus()

    def _echec_action_sftp(self, verbe: str, erreur: Exception) -> None:
        if not self:
            return
        wx.MessageBox(
            f"{verbe} impossible :\n{erreur}", f"{verbe} échoué",
            wx.OK | wx.ICON_ERROR,
        )
        self.voix.dire(f"{verbe} échoué.", interrompre=True)

    def activer_entree_sftp_selectionnee(self) -> None:
        """Point d'entrée pour Entrée en mode navigation, appelé depuis
        Fenetre.sur_touche_globale (voir plus bas pourquoi PAS depuis un
        gestionnaire local sur la liste)."""
        index = self.liste_fichiers.GetSelection()
        if index != wx.NOT_FOUND:
            self._activer_entree_sftp(index)

    def _activer_entree_sftp(self, index: int) -> None:
        entree = self._entrees_sftp[index]
        chemin = _joindre_chemin_distant(self.repertoire, entree.nom)
        logging.debug(
            "Entrée activée : %s (dossier=%s) -> %s", entree.nom, entree.dossier, chemin
        )
        if entree.dossier:
            self.charger_dossier_sftp(chemin)
        else:
            self._editer_fichier_sftp(chemin, entree.nom)

    def remonter_sftp(self) -> None:
        if not self.mode_navigation:
            return
        if self.repertoire in ("", "/"):
            self.voix.dire("Déjà à la racine.", interrompre=True)
            return
        parent = self.repertoire.rsplit("/", 1)[0]
        self.charger_dossier_sftp(parent or "/")

    def aller_au_favori(self, chemin: str) -> None:
        """Saute directement à un chemin favori — utilisable en mode
        fichiers (recharge la liste) comme en mode terminal (équivalent
        silencieux de Ctrl+Maj+D, mais pré-rempli)."""
        if self.mode_navigation:
            self.charger_dossier_sftp(chemin)
        else:
            self.definir_repertoire(chemin)
        self.GetTopLevelParent().SetStatusText(f"Répertoire : {chemin}")
        self.voix.dire(f"Répertoire : {chemin}", interrompre=True)

    def aller_a_resultat_recherche(self, chemin: str, dossier: bool) -> None:
        """Ouvre le résultat d'une recherche (DialogueRechercheFichiers) :
        le dossier lui-même s'il en est un, sinon son dossier parent
        avec le fichier sélectionné — un résultat peut venir de
        n'importe quelle profondeur sous le dossier où la recherche a
        démarré, contrairement à aller_au_favori qui ne pointe toujours
        que sur un dossier."""
        if dossier:
            self.charger_dossier_sftp(chemin)
        else:
            parent = _parent_chemin_distant(chemin)
            nom = chemin.rsplit("/", 1)[-1]
            self.charger_dossier_sftp(parent, nom_a_selectionner=nom)

    def sauvegarder_favoris(self) -> None:
        """Réécrit ssh_profiles.json avec les favoris à jour de ce
        profil. Recharge la liste complète plutôt que de ne réécrire que
        ce profil : évite d'écraser un changement fait entretemps ailleurs
        (Gestion des profils SSH) sur un autre profil."""
        if self.profil is None:
            return
        profils = charger_profils()
        for p in profils:
            if p.nom == self.profil.nom:
                p.favoris = list(self.profil.favoris)
                break
        enregistrer_profils(profils)

    def renommer_entree_sftp(self) -> None:
        if not self.mode_navigation:
            self.voix.dire(
                "Cette action nécessite le mode navigation (Ctrl+Maj+F).",
                interrompre=True,
            )
            return
        index = self.liste_fichiers.GetSelection()
        if index == wx.NOT_FOUND:
            self.voix.dire("Aucun élément sélectionné.", interrompre=True)
            return
        entree = self._entrees_sftp[index]
        with wx.TextEntryDialog(
            self, "Nouveau nom :", "Renommer", entree.nom,
        ) as boite:
            if boite.ShowModal() != wx.ID_OK:
                # wx ne restaure pas fiablement le focus sur la liste après
                # une boîte annulée (à la différence du chemin OK, qui
                # passe par charger_dossier_sftp et le refait) : sans cet
                # appel explicite, le clavier reste sur on ne sait quoi.
                self.liste_fichiers.SetFocus()
                return
            nouveau_nom = boite.GetValue().strip()
        if not nouveau_nom or nouveau_nom == entree.nom:
            self.liste_fichiers.SetFocus()
            return
        ancien_chemin = _joindre_chemin_distant(self.repertoire, entree.nom)
        nouveau_chemin = _joindre_chemin_distant(self.repertoire, nouveau_nom)
        chemin_courant = self.repertoire
        self.voix.dire(f"Renommage de {entree.nom}.", interrompre=True)

        def travailler():
            try:
                self.executeur.renommer(ancien_chemin, nouveau_chemin)
            except Exception as erreur:
                logging.exception("Renommage SFTP échoué : %s", ancien_chemin)
                wx.CallAfter(self._echec_action_sftp, "Renommage", erreur)
                return
            wx.CallAfter(self.voix.dire, f"Renommé en {nouveau_nom}.", interrompre=True)
            wx.CallAfter(self.charger_dossier_sftp, chemin_courant)

        threading.Thread(target=travailler, daemon=True).start()

    def supprimer_entree_sftp(self) -> None:
        if not self.mode_navigation:
            self.voix.dire(
                "Cette action nécessite le mode navigation (Ctrl+Maj+F).",
                interrompre=True,
            )
            return
        index = self.liste_fichiers.GetSelection()
        if index == wx.NOT_FOUND:
            self.voix.dire("Aucun élément sélectionné.", interrompre=True)
            return
        entree = self._entrees_sftp[index]
        chemin = _joindre_chemin_distant(self.repertoire, entree.nom)
        chemin_courant = self.repertoire

        message = f"Supprimer {'le dossier' if entree.dossier else 'le fichier'} « {entree.nom} » ?"
        if entree.dossier:
            message += (
                "\n\nAttention : s'il n'est pas vide, tout son contenu "
                "sera supprimé avec lui, sans confirmation supplémentaire."
            )
        if wx.MessageBox(
            message, "Confirmer la suppression", wx.YES_NO | wx.ICON_WARNING
        ) != wx.YES:
            self.liste_fichiers.SetFocus()
            return
        self.voix.dire(f"Suppression de {entree.nom}.", interrompre=True)

        def travailler():
            try:
                if entree.dossier:
                    self.executeur.supprimer_dossier(chemin)
                else:
                    self.executeur.supprimer_fichier(chemin)
            except Exception as erreur:
                logging.exception("Suppression SFTP échouée : %s", chemin)
                wx.CallAfter(self._echec_action_sftp, "Suppression", erreur)
                return
            wx.CallAfter(self.voix.dire, f"{entree.nom} supprimé.", interrompre=True)
            wx.CallAfter(self.charger_dossier_sftp, chemin_courant)

        threading.Thread(target=travailler, daemon=True).start()

    def creer_dossier_sftp(self) -> None:
        if not self.mode_navigation:
            self.voix.dire(
                "Cette action nécessite le mode navigation (Ctrl+Maj+F).",
                interrompre=True,
            )
            return
        with wx.TextEntryDialog(
            self, "Nom du nouveau dossier :", "Nouveau dossier",
        ) as boite:
            if boite.ShowModal() != wx.ID_OK:
                self.liste_fichiers.SetFocus()
                return
            nom = boite.GetValue().strip()
        if not nom:
            self.liste_fichiers.SetFocus()
            return
        chemin = _joindre_chemin_distant(self.repertoire, nom)
        chemin_courant = self.repertoire
        self.voix.dire(f"Création de {nom}.", interrompre=True)

        def travailler():
            try:
                self.executeur.creer_dossier(chemin)
            except Exception as erreur:
                logging.exception("Création de dossier SFTP échouée : %s", chemin)
                wx.CallAfter(self._echec_action_sftp, "Création du dossier", erreur)
                return
            wx.CallAfter(self.voix.dire, f"Dossier {nom} créé.", interrompre=True)
            wx.CallAfter(self.charger_dossier_sftp, chemin_courant)

        threading.Thread(target=travailler, daemon=True).start()

    def telecharger_entree_sftp(self) -> None:
        """Copie l'élément sélectionné (fichier ou dossier, récursif)
        vers un emplacement choisi sur cette machine. Distinct de
        l'édition (Entrée) : ici on choisit où et on garde une copie,
        sans passer par Notepad ni la renvoyer automatiquement."""
        if not self.mode_navigation:
            self.voix.dire(
                "Cette action nécessite le mode navigation (Ctrl+Maj+F).",
                interrompre=True,
            )
            return
        index = self.liste_fichiers.GetSelection()
        if index == wx.NOT_FOUND:
            self.voix.dire("Aucun élément sélectionné.", interrompre=True)
            return
        entree = self._entrees_sftp[index]
        chemin_distant = _joindre_chemin_distant(self.repertoire, entree.nom)

        if entree.dossier:
            with wx.DirDialog(
                self, "Choisissez où télécharger ce dossier",
            ) as boite:
                if boite.ShowModal() != wx.ID_OK:
                    self.liste_fichiers.SetFocus()
                    return
                chemin_local = str(Path(boite.GetPath()) / entree.nom)
        else:
            with wx.FileDialog(
                self, "Enregistrer sous", defaultFile=entree.nom,
                style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
            ) as boite:
                if boite.ShowModal() != wx.ID_OK:
                    self.liste_fichiers.SetFocus()
                    return
                chemin_local = boite.GetPath()

        self._compteur_transferts += 1
        self._enfiler_transfert(Transfert(
            numero=self._compteur_transferts,
            direction="reception",
            nom=entree.nom,
            chemin_local=chemin_local,
            chemin_distant=chemin_distant,
            dossier=entree.dossier,
        ))

    def envoyer_fichier_sftp(self) -> None:
        """Envoie un fichier choisi sur cette machine vers le répertoire
        distant actuellement affiché — la cible est toujours « ici », pas
        un chemin à taper, sur le principe même du navigateur."""
        if not self.mode_navigation:
            self.voix.dire(
                "Cette action nécessite le mode navigation (Ctrl+Maj+F).",
                interrompre=True,
            )
            return
        with wx.FileDialog(
            self, "Choisir le fichier à envoyer",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as boite:
            if boite.ShowModal() != wx.ID_OK:
                self.liste_fichiers.SetFocus()
                return
            chemin_local = boite.GetPath()

        nom = Path(chemin_local).name
        chemin_distant = _joindre_chemin_distant(self.repertoire, nom)
        self._compteur_transferts += 1
        self._enfiler_transfert(Transfert(
            numero=self._compteur_transferts,
            direction="envoi",
            nom=nom,
            chemin_local=chemin_local,
            chemin_distant=chemin_distant,
        ))

    # -- file d'attente des transferts --------------------------------------

    def _enfiler_transfert(self, transfert: Transfert) -> None:
        self.transferts.append(transfert)
        verbe = "Envoi" if transfert.direction == "envoi" else "Téléchargement"
        deja_en_cours = any(
            t.etat in ("en attente", "en cours") for t in self.transferts[:-1]
        )
        if deja_en_cours:
            self.voix.dire(
                f"{verbe} de {transfert.nom} en file d'attente.", interrompre=True
            )
        else:
            self.voix.dire(f"{verbe} de {transfert.nom}.", interrompre=True)
        self._file_transferts.put(transfert)

    def _travailleur_transferts(self) -> None:
        """Traite les transferts un par un, dans l'ordre — jamais deux à
        la fois (voir la note sur le canal SFTP partagé dans __init__).
        Tourne pour toute la durée de la session, même hors mode
        fichiers : un transfert lancé avant de repasser en mode terminal
        doit continuer normalement."""
        while True:
            transfert = self._file_transferts.get()
            if transfert.etat == "annulé":
                continue
            wx.CallAfter(self._transfert_demarre, transfert)

            # Vitesse mesurée entre deux appels sur le MÊME fichier
            # (nom_vu détecte le changement — pour un dossier, on passe
            # au suivant). Texte limité à 5 rafraîchissements par
            # seconde (fluide sans ralentir le transfert, le callback de
            # sftp.put/get arrive à chaque paquet, bien plus souvent).
            # Pas d'annonce vocale répétée ici : signalée trop bavarde à
            # l'usage (parle sans arrêt, empêche de consulter autre
            # chose) — la zone de texte de la fenêtre de progression,
            # mise à jour en temps réel, suffit (braille compris, qui
            # suit le contenu du champ focalisé sans qu'il soit besoin
            # de le faire dire). Seuls le démarrage et la fin du
            # transfert restent annoncés (_transfert_demarre et
            # consorts, plus bas), parce que ce sont des évènements
            # ponctuels et non un flux continu.
            nom_vu = None
            temps_mesure = 0.0
            octets_mesure = 0
            dernier_texte = 0.0

            def progresser(nom, fait, total, t=transfert):
                nonlocal nom_vu, temps_mesure, octets_mesure, dernier_texte
                if not total:
                    return
                maintenant = time.monotonic()
                nouveau_fichier = nom != nom_vu
                vitesse = None
                if nouveau_fichier:
                    nom_vu = nom
                    temps_mesure = maintenant
                    octets_mesure = fait
                else:
                    delta = maintenant - temps_mesure
                    if delta >= 0.5:
                        vitesse = _formater_vitesse((fait - octets_mesure) / delta)
                        temps_mesure = maintenant
                        octets_mesure = fait

                pourcentage = int(fait * 100 / total)
                maj_texte = (
                    nouveau_fichier or pourcentage == 100
                    or maintenant - dernier_texte >= 0.2
                )
                if not maj_texte:
                    return
                dernier_texte = maintenant
                wx.CallAfter(
                    self._maj_progression_transfert, t, nom, pourcentage, vitesse,
                )

            try:
                if transfert.direction == "envoi":
                    self.executeur.envoyer_fichier(
                        transfert.chemin_local, transfert.chemin_distant,
                        lambda fait, total: progresser(transfert.nom, fait, total),
                    )
                elif transfert.dossier:
                    self.executeur.telecharger_dossier(
                        transfert.chemin_distant, transfert.chemin_local, progresser,
                    )
                else:
                    self.executeur.recuperer_fichier(
                        transfert.chemin_distant, transfert.chemin_local,
                        lambda fait, total: progresser(transfert.nom, fait, total),
                    )
            except Exception as erreur:
                # annuler_transfert() pose déjà etat="annulé" avant de
                # fermer le canal : l'exception qui en résulte ici n'est
                # donc pas un vrai échec, juste la conséquence attendue.
                if transfert.etat == "annulé":
                    wx.CallAfter(self._transfert_annule_confirme, transfert)
                else:
                    logging.exception(
                        "Transfert échoué (%s) : %s", transfert.direction, transfert.nom
                    )
                    wx.CallAfter(self._transfert_echoue, transfert, erreur)
            else:
                wx.CallAfter(self._transfert_reussi, transfert)

    def _transfert_demarre(self, transfert: Transfert) -> None:
        transfert.etat = "en cours"
        transfert.fichier_actuel = transfert.nom
        self.transfert_actuel = transfert
        self._rafraichir_transfert(transfert)
        self.voix.dire(transfert.libelle(), interrompre=True)
        self._afficher_fenetre_progression()

    def _afficher_fenetre_progression(self) -> None:
        """ShowModal() bloque ici jusqu'à ce que la boîte se ferme — ce
        qui n'arrive, par construction (_sur_fermeture), qu'une fois le
        transfert terminé et l'utilisateur prêt à reprendre la main.

        Si une boîte est déjà ouverte (transfert précédent pas encore
        refermé par l'utilisateur quand celui-ci démarre), on ne rouvre
        pas de deuxième modale par-dessus : rafraichir_transfert (appelé
        juste avant) l'a déjà mise à jour sur ce nouveau transfert.
        """
        if self.fenetre_progression is not None:
            return
        self.fenetre_progression = DialogueProgression(
            self.GetTopLevelParent(), self
        )
        self.fenetre_progression.ShowModal()
        self.fenetre_progression.Destroy()
        self.fenetre_progression = None

    def _maj_progression_transfert(
        self, transfert: Transfert, fichier_actuel: str,
        pourcentage: int, vitesse: str | None,
    ) -> None:
        transfert.fichier_actuel = fichier_actuel
        transfert.pourcentage = pourcentage
        if vitesse is not None:
            transfert.vitesse = vitesse
        self._rafraichir_transfert(transfert)

    def annuler_transfert(self, transfert: Transfert) -> None:
        if transfert.etat == "en attente":
            transfert.etat = "annulé"
            self._rafraichir_transfert(transfert)
            self.voix.dire(f"{transfert.nom} retiré de la file.", interrompre=True)
        elif transfert.etat == "en cours":
            # Un seul transfert « en cours » possible à la fois : celui-ci
            # est forcément celui que le canal actif d'ExecuteurSSH sert
            # en ce moment.
            transfert.etat = "annulé"
            self.executeur.annuler_transfert()
            self._rafraichir_transfert(transfert)
            self.voix.dire(f"Annulation de {transfert.nom} demandée.", interrompre=True)

    def _transfert_annule_confirme(self, transfert: Transfert) -> None:
        self._rafraichir_transfert(transfert)
        self.voix.dire(f"{transfert.nom} annulé.", interrompre=True)

    def _transfert_reussi(self, transfert: Transfert) -> None:
        transfert.etat = "terminé"
        self._rafraichir_transfert(transfert)
        verbe = "envoyé" if transfert.direction == "envoi" else "téléchargé"
        self.voix.dire(f"{transfert.nom} {verbe}.", interrompre=True)
        # self.repertoire, pas un chemin capturé à l'envoi : le temps
        # passé en file d'attente est indéterminé, mieux vaut vérifier
        # qu'on regarde toujours le bon dossier que de le supposer.
        if (
            transfert.direction == "envoi"
            and self.repertoire == _parent_chemin_distant(transfert.chemin_distant)
        ):
            self.charger_dossier_sftp(self.repertoire)

    def _transfert_echoue(self, transfert: Transfert, erreur: Exception) -> None:
        transfert.etat = "échoué"
        transfert.erreur = str(erreur)
        self._rafraichir_transfert(transfert)
        verbe = "Envoi" if transfert.direction == "envoi" else "Téléchargement"
        self.voix.dire(f"{verbe} de {transfert.nom} échoué.", interrompre=True)

    def _rafraichir_transfert(self, transfert: Transfert) -> None:
        """Statut dans la barre si cette session est affichée, et la
        fenêtre de progression si elle est ouverte — l'annonce vocale
        est décidée par l'appelant (voir _maj_progression_transfert),
        pas ici, pour ne pas parler à chaque rafraîchissement visuel."""
        fenetre = self.GetTopLevelParent()
        if fenetre.session() is self:
            fenetre.SetStatusText(transfert.libelle())
        if self.fenetre_progression is not None:
            self.fenetre_progression.rafraichir()

    def _editer_fichier_sftp(self, chemin_distant: str, nom: str) -> None:
        """Télécharge le fichier, ouvre Notepad et attend sa fermeture,
        puis renvoie le fichier seulement s'il a été modifié.

        Bloc-notes plutôt qu'un éditeur interne : il est déjà pleinement
        accessible et connu de l'utilisateur, écrire et maintenir un
        éditeur de texte accessible depuis zéro serait un chantier bien
        plus lourd que le reste de cette fonctionnalité pour un bénéfice
        incertain (pas de coloration syntaxique prévue de toute façon).
        """
        self.voix.dire(f"Téléchargement de {nom}.", interrompre=True)

        def travailler():
            import os
            import shutil
            import subprocess
            import tempfile

            dossier_tmp = tempfile.mkdtemp(prefix="lazyshell_")
            chemin_local = str(Path(dossier_tmp) / nom)
            try:
                try:
                    self.executeur.recuperer_fichier(chemin_distant, chemin_local)
                except Exception as erreur:
                    logging.exception(
                        "Téléchargement pour édition échoué : %s", chemin_distant
                    )
                    wx.CallAfter(self._echec_action_sftp, "Téléchargement", erreur)
                    return

                avant = os.path.getmtime(chemin_local)
                try:
                    subprocess.Popen(["notepad.exe", chemin_local]).wait()
                except Exception as erreur:
                    logging.exception("Lancement de Notepad impossible.")
                    wx.CallAfter(
                        self._echec_action_sftp, "Ouverture dans Notepad", erreur
                    )
                    return

                if os.path.getmtime(chemin_local) == avant:
                    wx.CallAfter(self.voix.dire, "Aucune modification.", interrompre=True)
                    return

                wx.CallAfter(self.voix.dire, f"Envoi de {nom}.", interrompre=True)
                try:
                    self.executeur.envoyer_fichier(chemin_local, chemin_distant)
                except Exception as erreur:
                    logging.exception(
                        "Envoi après édition échoué : %s", chemin_distant
                    )
                    wx.CallAfter(self._echec_action_sftp, "Envoi", erreur)
                    return
                wx.CallAfter(
                    self.voix.dire, f"{nom} enregistré sur le serveur.", interrompre=True
                )
                # self.repertoire, pas un chemin capturé au départ : le
                # temps que Notepad reste ouvert est indéterminé, et
                # rien n'empêche d'avoir navigué ailleurs entre-temps.
                # Rafraîchir « où on est maintenant » est plus juste que
                # de revenir de force à l'ancien dossier.
                wx.CallAfter(self.charger_dossier_sftp, self.repertoire)
            finally:
                shutil.rmtree(dossier_tmp, ignore_errors=True)

        threading.Thread(target=travailler, daemon=True).start()


# --------------------------------------------------------------------------
# Boîte de dialogue : liste des blocs
# --------------------------------------------------------------------------

class DialogueListeBlocs(wx.Dialog):
    """Liste des blocs, avec un bouton Copier accessible au Tab.

    wx.SingleChoiceDialog ne laisse pas de place pour un bouton
    supplémentaire : on reconstitue la même disposition à la main pour
    pouvoir copier un bloc sans d'abord y aller.
    """

    def __init__(self, parent, blocs: list[Bloc], voix: Voix):
        super().__init__(
            parent, title="Liste des blocs",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.blocs = blocs
        self.voix = voix
        self.bloc_choisi: Bloc | None = None

        etiquette = wx.StaticText(self, label="&Blocs :")
        self.liste = wx.ListBox(self, choices=[b.libelle_liste() for b in blocs])
        self.liste.SetSelection(len(blocs) - 1)
        self.liste.Bind(wx.EVT_LISTBOX_DCLICK, self._sur_aller)

        bouton_aller = wx.Button(self, label="&Aller au bloc")
        bouton_aller.SetDefault()
        bouton_aller.Bind(wx.EVT_BUTTON, self._sur_aller)
        bouton_copier = wx.Button(self, label="&Copier le bloc")
        bouton_copier.Bind(wx.EVT_BUTTON, self._sur_copier)
        bouton_fermer = wx.Button(self, id=wx.ID_CANCEL, label="Fer&mer")

        boutons = wx.BoxSizer(wx.HORIZONTAL)
        boutons.Add(bouton_aller, 0, wx.RIGHT, 6)
        boutons.Add(bouton_copier, 0, wx.RIGHT, 6)
        boutons.Add(bouton_fermer, 0)

        boite = wx.BoxSizer(wx.VERTICAL)
        boite.Add(etiquette, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        boite.Add(self.liste, 1, wx.EXPAND | wx.ALL, 8)
        boite.Add(boutons, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.SetSizer(boite)
        self.SetSize((480, 360))
        self.liste.SetFocus()

    def _bloc_selectionne(self) -> Bloc | None:
        index = self.liste.GetSelection()
        if index == wx.NOT_FOUND:
            return None
        return self.blocs[index]

    def _sur_aller(self, evt):
        self.bloc_choisi = self._bloc_selectionne()
        self.EndModal(wx.ID_OK)

    def _sur_copier(self, evt):
        bloc = self._bloc_selectionne()
        if bloc is None:
            return
        if copier_presse_papiers(bloc.texte_complet()):
            self.voix.dire(
                f"Bloc {bloc.numero} copié, {decompte(bloc.nb_lignes)}.",
                interrompre=True,
            )


# --------------------------------------------------------------------------
# Fenêtre : progression d'un transfert
# --------------------------------------------------------------------------

class DialogueProgression(wx.Dialog):
    """Modale : bloque le reste de LazyShell tant qu'un transfert est en
    cours, par préférence explicite de l'utilisateur à une fenêtre
    discrète en arrière-plan (essayée d'abord) qui ne se faisait pas
    remarquer et dont les annonces vocales périodiques arrivaient sans
    prévenir pendant qu'il faisait autre chose. Ici, l'attente est
    assumée : le focus modal fait lire l'état par NVDA normalement,
    comme n'importe quelle autre boîte de dialogue de l'appli — pas de
    mécanisme d'annonce séparé à comprendre.

    Le titre de la fenêtre porte le pourcentage (« 42% fichier.bin »,
    voir Transfert.titre_fenetre) : c'est lui qu'on consulte pour
    l'avancement chiffré, la zone de texte (champ) ne le répète plus.

    Ne se ferme pas par Échap ni par la croix tant que le transfert
    n'est pas terminé : c'est le seul moyen de le suivre ou de l'annuler
    pendant ce temps (le raccourci de rappel a été retiré, voir
    CLAUDE.md), la faire disparaître par erreur laisserait le transfert
    continuer sans plus aucune prise dessus. Le bouton Annuler fait
    exception à cette règle : il ferme la fenêtre tout de suite après
    avoir demandé l'annulation, sans repasser par Fermer — annuler est
    déjà un geste délibéré, le confirmer une seconde fois n'apportait
    rien qu'une manipulation en trop.
    """

    def __init__(self, parent, panneau: PanneauSession):
        super().__init__(parent, title="Transfert en cours")
        self.panneau = panneau

        self.champ = wx.TextCtrl(
            self, style=wx.TE_MULTILINE | wx.TE_READONLY,
        )
        self.champ.SetName("État du transfert")

        bouton_annuler = wx.Button(self, label="&Annuler")
        bouton_annuler.Bind(wx.EVT_BUTTON, self._sur_annuler)
        self.bouton_fermer = wx.Button(self, id=wx.ID_CANCEL, label="Fer&mer")

        boutons = wx.BoxSizer(wx.HORIZONTAL)
        boutons.Add(bouton_annuler, 0, wx.RIGHT, 6)
        boutons.Add(self.bouton_fermer, 0)

        boite = wx.BoxSizer(wx.VERTICAL)
        boite.Add(self.champ, 1, wx.EXPAND | wx.ALL, 10)
        boite.Add(boutons, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.SetSizer(boite)
        self.SetSize((420, 150))

        self.Bind(wx.EVT_CLOSE, self._sur_fermeture)
        self.Bind(wx.EVT_BUTTON, self._sur_fermeture, id=wx.ID_CANCEL)

        self.rafraichir()
        self.champ.SetFocus()

    def rafraichir(self) -> None:
        if not self:
            return
        transfert = self.panneau.transfert_actuel
        if transfert is None:
            return
        self.SetTitle(transfert.titre_fenetre())
        self.champ.SetValue(transfert.libelle())
        self.bouton_fermer.Enable(transfert.etat not in ("en attente", "en cours"))

    def _sur_annuler(self, evt):
        transfert = self.panneau.transfert_actuel
        if transfert is None or transfert.etat not in ("en attente", "en cours"):
            self.panneau.voix.dire("Aucun transfert à annuler.", interrompre=True)
            return
        self.panneau.annuler_transfert(transfert)
        self.EndModal(wx.ID_CANCEL)

    def _sur_fermeture(self, evt):
        transfert = self.panneau.transfert_actuel
        if transfert is not None and transfert.etat in ("en attente", "en cours"):
            self.panneau.voix.dire(
                "Le transfert est toujours en cours. Annuler pour l'arrêter.",
                interrompre=True,
            )
            return
        self.EndModal(wx.ID_CANCEL)


# --------------------------------------------------------------------------
# Boîtes de dialogue : dossiers favoris
# --------------------------------------------------------------------------

class DialogueFavoriDossier(wx.Dialog):
    """Création ou modification d'un favori — même principe que
    DialogueCommandeEnregistree pour les commandes : un nom explicite
    plutôt que le chemin brut, qui peut être long ou peu parlant."""

    def __init__(
        self, parent, favori: FavoriDossier | None = None, chemin_initial: str = "",
    ):
        titre = "Modifier le favori" if favori else "Nouveau favori"
        super().__init__(
            parent, title=titre, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )

        etiquette_nom = wx.StaticText(self, label="&Nom :")
        self.champ_nom = wx.TextCtrl(self, value=favori.nom if favori else "")

        etiquette_chemin = wx.StaticText(self, label="&Chemin distant :")
        valeur_chemin = favori.chemin if favori else chemin_initial
        self.champ_chemin = wx.TextCtrl(self, value=valeur_chemin)

        boutons = wx.StdDialogButtonSizer()
        boutons.AddButton(wx.Button(self, id=wx.ID_OK, label="&Enregistrer"))
        boutons.AddButton(wx.Button(self, id=wx.ID_CANCEL, label="Ann&uler"))
        boutons.Realize()
        self.Bind(wx.EVT_BUTTON, self._sur_ok, id=wx.ID_OK)

        boite = wx.BoxSizer(wx.VERTICAL)
        boite.Add(etiquette_nom, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        boite.Add(self.champ_nom, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        boite.Add(etiquette_chemin, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        boite.Add(self.champ_chemin, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        boite.Add(boutons, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizerAndFit(boite)
        self.SetSize((420, 180))
        self.champ_nom.SetFocus()

    def _sur_ok(self, evt):
        if not self.champ_nom.GetValue().strip():
            wx.MessageBox(
                "Le nom est obligatoire.", "Favori incomplet", wx.OK | wx.ICON_WARNING,
            )
            self.champ_nom.SetFocus()
            return
        if not self.champ_chemin.GetValue().strip():
            wx.MessageBox(
                "Le chemin est obligatoire.", "Favori incomplet", wx.OK | wx.ICON_WARNING,
            )
            self.champ_chemin.SetFocus()
            return
        self.EndModal(wx.ID_OK)

    def favori(self) -> FavoriDossier:
        return FavoriDossier(
            nom=self.champ_nom.GetValue().strip(),
            chemin=self.champ_chemin.GetValue().strip(),
        )


class DialogueFavoris(wx.Dialog):
    """Dossiers favoris du profil SSH de cette session : sauter
    directement à un chemin fréquent, sans reparcourir depuis le dossier
    de connexion à chaque fois."""

    def __init__(self, parent, panneau: PanneauSession):
        super().__init__(
            parent, title=f"Favoris — {panneau.profil.nom}",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.panneau = panneau

        etiquette = wx.StaticText(self, label="&Favoris :")
        self.liste = wx.ListBox(self, choices=self._libelles())
        self.liste.Bind(wx.EVT_LISTBOX_DCLICK, self._sur_aller)

        bouton_aller = wx.Button(self, label="&Aller")
        bouton_aller.SetDefault()
        bouton_aller.Bind(wx.EVT_BUTTON, self._sur_aller)
        bouton_ajouter = wx.Button(self, label="A&jouter le dossier courant…")
        bouton_ajouter.Bind(wx.EVT_BUTTON, self._sur_ajouter)
        bouton_modifier = wx.Button(self, label="&Modifier…")
        bouton_modifier.Bind(wx.EVT_BUTTON, self._sur_modifier)
        bouton_supprimer = wx.Button(self, label="&Supprimer")
        bouton_supprimer.Bind(wx.EVT_BUTTON, self._sur_supprimer)
        bouton_fermer = wx.Button(self, id=wx.ID_CANCEL, label="Fer&mer")

        boutons = wx.BoxSizer(wx.HORIZONTAL)
        for bouton in (
            bouton_aller, bouton_ajouter, bouton_modifier, bouton_supprimer,
            bouton_fermer,
        ):
            boutons.Add(bouton, 0, wx.RIGHT, 6)

        boite = wx.BoxSizer(wx.VERTICAL)
        boite.Add(etiquette, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        boite.Add(self.liste, 1, wx.EXPAND | wx.ALL, 8)
        boite.Add(boutons, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.SetSizer(boite)
        self.SetSize((520, 320))
        self.liste.SetFocus()

    def _libelles(self) -> list[str]:
        return [f"{f.nom} — {f.chemin}" for f in self.panneau.profil.favoris]

    def _selection(self) -> FavoriDossier | None:
        index = self.liste.GetSelection()
        if index == wx.NOT_FOUND:
            return None
        return self.panneau.profil.favoris[index]

    def _rafraichir(self, selectionner: FavoriDossier | None = None) -> None:
        self.liste.Set(self._libelles())
        if selectionner is not None and selectionner in self.panneau.profil.favoris:
            self.liste.SetSelection(self.panneau.profil.favoris.index(selectionner))
        self.panneau.sauvegarder_favoris()

    def _sur_aller(self, evt):
        favori = self._selection()
        if favori is None:
            return
        self.panneau.aller_au_favori(favori.chemin)
        self.EndModal(wx.ID_OK)

    def _sur_ajouter(self, evt):
        if not self.panneau.repertoire:
            wx.MessageBox(
                "Aucun répertoire courant à ajouter.",
                "Favoris", wx.OK | wx.ICON_WARNING,
            )
            return
        with DialogueFavoriDossier(
            self, chemin_initial=self.panneau.repertoire,
        ) as boite:
            if boite.ShowModal() != wx.ID_OK:
                return
            favori = boite.favori()
        self.panneau.profil.favoris.append(favori)
        self._rafraichir(favori)
        self.panneau.voix.dire(f"Favori {favori.nom} ajouté.", interrompre=True)

    def _sur_modifier(self, evt):
        favori = self._selection()
        if favori is None:
            return
        with DialogueFavoriDossier(self, favori) as boite:
            if boite.ShowModal() != wx.ID_OK:
                return
            nouveau = boite.favori()
        index = self.panneau.profil.favoris.index(favori)
        self.panneau.profil.favoris[index] = nouveau
        self._rafraichir(nouveau)
        self.panneau.voix.dire(f"Favori {nouveau.nom} modifié.", interrompre=True)

    def _sur_supprimer(self, evt):
        favori = self._selection()
        if favori is None:
            return
        if wx.MessageBox(
            f"Retirer « {favori.nom} » des favoris ?",
            "Confirmer", wx.YES_NO | wx.ICON_QUESTION,
        ) != wx.YES:
            return
        self.panneau.profil.favoris.remove(favori)
        self._rafraichir()
        self.panneau.voix.dire("Favori supprimé.", interrompre=True)


# --------------------------------------------------------------------------
# Boîte de dialogue : recherche de fichiers (SFTP)
# --------------------------------------------------------------------------

class DialogueRechercheFichiers(wx.Dialog):
    """Recherche récursive de fichiers ou dossiers par nom, à partir du
    dossier actuellement affiché en mode navigation — même esprit que la
    recherche de l'Explorateur Windows ou `find`. Simple sous-chaîne du
    nom, insensible à la casse : pas de motif plus riche (glob, regex),
    cohérent avec la simplicité déjà en place ailleurs dans l'appli
    (listing amélioré, etc.).

    Le parcours tourne dans un thread de fond (toute E/S réseau hors du
    thread principal, un parcours profond peut prendre du temps) : le
    statut se met à jour en direct (nombre de dossiers explorés), limité
    à 5 rafraîchissements par seconde pour ne pas inonder le thread
    principal de wx.CallAfter sur une arborescence avec beaucoup de
    petits dossiers — même principe que la fenêtre de progression des
    transferts. Les résultats, eux, ne s'affichent qu'une fois la
    recherche terminée ou annulée : ExecuteurSSH.rechercher_fichiers ne
    les fait remonter qu'à la toute fin, pas au fil de l'eau.

    Modale comme les autres boîtes de dialogue SFTP de cette appli
    (Favoris, Profils) : rien n'empêche de la laisser ouverte pendant
    que la recherche tourne, Annuler l'interrompt sans fermer la boîte
    pour permettre d'enchaîner une nouvelle recherche."""

    def __init__(self, parent, panneau: PanneauSession):
        super().__init__(
            parent, title="Recherche de fichiers",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.panneau = panneau
        self._annulation: threading.Event | None = None
        self._en_cours = False
        self._resultats: list[tuple[str, EntreeDistante]] = []

        etiquette_motif = wx.StaticText(self, label="&Motif :")
        self.champ_motif = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.champ_motif.Bind(wx.EVT_TEXT_ENTER, self._sur_rechercher)

        self.bouton_rechercher = wx.Button(self, label="&Rechercher")
        self.bouton_rechercher.SetDefault()
        self.bouton_rechercher.Bind(wx.EVT_BUTTON, self._sur_rechercher)
        self.bouton_annuler_recherche = wx.Button(self, label="A&nnuler la recherche")
        self.bouton_annuler_recherche.Bind(wx.EVT_BUTTON, self._sur_annuler_recherche)
        self.bouton_annuler_recherche.Disable()

        ligne_motif = wx.BoxSizer(wx.HORIZONTAL)
        ligne_motif.Add(self.champ_motif, 1, wx.RIGHT | wx.ALIGN_CENTER_VERTICAL, 6)
        ligne_motif.Add(self.bouton_rechercher, 0, wx.RIGHT, 6)
        ligne_motif.Add(self.bouton_annuler_recherche, 0)

        self.statut = wx.StaticText(self, label="Tapez un motif puis Rechercher.")
        self.statut.SetName("Statut de la recherche")

        etiquette_resultats = wx.StaticText(self, label="&Résultats :")
        self.liste = wx.ListBox(self)
        self.liste.Bind(wx.EVT_LISTBOX_DCLICK, self._sur_aller)

        self.bouton_aller = wx.Button(self, label="&Aller au résultat")
        self.bouton_aller.Bind(wx.EVT_BUTTON, self._sur_aller)
        self.bouton_aller.Disable()
        bouton_fermer = wx.Button(self, id=wx.ID_CANCEL, label="Fer&mer")

        boutons = wx.BoxSizer(wx.HORIZONTAL)
        boutons.Add(self.bouton_aller, 0, wx.RIGHT, 6)
        boutons.Add(bouton_fermer, 0)

        boite = wx.BoxSizer(wx.VERTICAL)
        boite.Add(etiquette_motif, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        boite.Add(ligne_motif, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        boite.Add(self.statut, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        boite.Add(etiquette_resultats, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        boite.Add(self.liste, 1, wx.EXPAND | wx.ALL, 8)
        boite.Add(boutons, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.SetSizer(boite)
        self.SetSize((520, 420))

        self.Bind(wx.EVT_CLOSE, self._sur_fermeture)
        self.Bind(wx.EVT_BUTTON, self._sur_fermeture, id=wx.ID_CANCEL)
        self.champ_motif.SetFocus()

    def _sur_rechercher(self, evt):
        if self._en_cours:
            return
        motif = self.champ_motif.GetValue().strip()
        if not motif:
            self.panneau.voix.dire("Tapez un motif à rechercher.", interrompre=True)
            self.champ_motif.SetFocus()
            return

        self._en_cours = True
        self._resultats = []
        annulation = threading.Event()
        self._annulation = annulation
        self.liste.Set([])
        self.bouton_aller.Disable()
        self.bouton_rechercher.Disable()
        self.bouton_annuler_recherche.Enable()
        self.statut.SetLabel(f"Recherche de « {motif} »…")
        self.panneau.voix.dire(f"Recherche de {motif}.", interrompre=True)

        chemin_racine = self.panneau.repertoire
        executeur = self.panneau.executeur
        dernier_texte = [0.0]
        compteur_dossiers = [0]

        def sur_dossier_explore(chemin):
            compteur_dossiers[0] += 1
            maintenant = time.monotonic()
            if maintenant - dernier_texte[0] < 0.2:
                return
            dernier_texte[0] = maintenant
            wx.CallAfter(self._maj_statut_recherche, compteur_dossiers[0])

        def travailler():
            try:
                resultats = executeur.rechercher_fichiers(
                    chemin_racine, motif, sur_dossier_explore, annulation.is_set,
                )
            except Exception as erreur:
                logging.exception("Recherche SFTP échouée : %s", chemin_racine)
                wx.CallAfter(self._echec_recherche, erreur)
                return
            wx.CallAfter(self._recherche_terminee, resultats, annulation.is_set())

        threading.Thread(target=travailler, daemon=True).start()

    def _maj_statut_recherche(self, nb_dossiers: int) -> None:
        if not self:
            return
        self.statut.SetLabel(f"Recherche en cours… {nb_dossiers} dossier(s) exploré(s).")

    def _recherche_terminee(
        self, resultats: list[tuple[str, EntreeDistante]], annulee: bool,
    ) -> None:
        if not self:
            return
        self._en_cours = False
        self._resultats = resultats
        self.bouton_rechercher.Enable()
        self.bouton_annuler_recherche.Disable()
        self.liste.Set([_libelle_resultat_recherche(c, e) for c, e in resultats])
        if resultats:
            self.liste.SetSelection(0)
            self.bouton_aller.Enable()
        nb = len(resultats)
        decompte_resultats = "1 résultat" if nb == 1 else f"{nb} résultats"
        verbe = "annulée" if annulee else "terminée"
        message = f"Recherche {verbe} : {decompte_resultats}."
        self.statut.SetLabel(message)
        self.panneau.voix.dire(message, interrompre=True)
        self.liste.SetFocus()

    def _echec_recherche(self, erreur: Exception) -> None:
        if not self:
            return
        self._en_cours = False
        self.bouton_rechercher.Enable()
        self.bouton_annuler_recherche.Disable()
        self.statut.SetLabel(f"Recherche échouée : {erreur}")
        self.panneau.voix.dire("Recherche échouée.", interrompre=True)

    def _sur_annuler_recherche(self, evt):
        if self._annulation is not None:
            self._annulation.set()
        self.statut.SetLabel("Annulation demandée…")

    def _resultat_selectionne(self) -> tuple[str, EntreeDistante] | None:
        index = self.liste.GetSelection()
        if index == wx.NOT_FOUND or index >= len(self._resultats):
            return None
        return self._resultats[index]

    def _sur_aller(self, evt):
        resultat = self._resultat_selectionne()
        if resultat is None:
            return
        chemin, entree = resultat
        self.panneau.aller_a_resultat_recherche(chemin, entree.dossier)
        self.EndModal(wx.ID_OK)

    def _sur_fermeture(self, evt):
        if self._en_cours and self._annulation is not None:
            self._annulation.set()
        self.EndModal(wx.ID_CANCEL)


# --------------------------------------------------------------------------
# Boîtes de dialogue : profils de connexion SSH
# --------------------------------------------------------------------------

class DialogueProfilSSH(wx.Dialog):
    """Création ou modification d'un profil de connexion SSH.

    Le secret (mot de passe ou passphrase) n'est jamais pré-rempli, même
    en modification d'un profil existant : un champ laissé vide signifie
    « ne pas changer le secret déjà mémorisé », pas « l'effacer ».
    """

    def __init__(self, parent, profil: ProfilConnexion | None = None):
        titre = "Modifier le profil" if profil else "Nouveau profil"
        super().__init__(parent, title=titre)
        # Conservé pour profil() : reconstruire un ProfilConnexion à
        # partir des seuls champs du formulaire perdrait sinon les
        # favoris du profil édité, qui n'ont pas leur propre champ ici.
        self._profil_existant = profil

        etiquette_nom = wx.StaticText(self, label="&Nom du profil :")
        self.champ_nom = wx.TextCtrl(self, value=profil.nom if profil else "")

        etiquette_hote = wx.StaticText(self, label="&Hôte :")
        self.champ_hote = wx.TextCtrl(self, value=profil.hote if profil else "")

        etiquette_port = wx.StaticText(self, label="&Port :")
        self.champ_port = wx.TextCtrl(self, value=str(profil.port if profil else 22))

        etiquette_utilisateur = wx.StaticText(self, label="&Utilisateur :")
        self.champ_utilisateur = wx.TextCtrl(
            self, value=profil.utilisateur if profil else ""
        )

        self.choix_auth = wx.RadioBox(
            self, label="Authentification", choices=["Mot de passe", "Clé SSH"],
        )
        self.choix_auth.SetSelection(1 if profil and profil.mode_auth == "cle" else 0)
        self.choix_auth.Bind(wx.EVT_RADIOBOX, self._sur_changement_mode)

        etiquette_cle = wx.StaticText(self, label="Chemin de la &clé privée :")
        self.champ_cle = wx.TextCtrl(self, value=profil.chemin_cle if profil else "")
        self.bouton_parcourir = wx.Button(self, label="&Parcourir…")
        self.bouton_parcourir.Bind(wx.EVT_BUTTON, self._sur_parcourir)

        self.etiquette_secret = wx.StaticText(self, label="")
        self.champ_secret = wx.TextCtrl(self, style=wx.TE_PASSWORD)
        self._maj_libelle_secret()
        aide_secret = wx.StaticText(
            self,
            label="(laisser vide pour conserver le secret déjà mémorisé)"
            if profil else "(facultatif ici : demandé à la connexion si absent)",
        )

        self._activer_champs_cle()

        boutons = wx.StdDialogButtonSizer()
        boutons.AddButton(wx.Button(self, id=wx.ID_OK, label="&Enregistrer"))
        boutons.AddButton(wx.Button(self, id=wx.ID_CANCEL, label="Ann&uler"))
        boutons.Realize()
        self.Bind(wx.EVT_BUTTON, self._sur_ok, id=wx.ID_OK)

        grille = wx.FlexGridSizer(cols=2, gap=(8, 6))
        grille.AddGrowableCol(1)
        for etiquette, champ in (
            (etiquette_nom, self.champ_nom),
            (etiquette_hote, self.champ_hote),
            (etiquette_port, self.champ_port),
            (etiquette_utilisateur, self.champ_utilisateur),
        ):
            grille.Add(etiquette, 0, wx.ALIGN_CENTER_VERTICAL)
            grille.Add(champ, 1, wx.EXPAND)

        ligne_cle = wx.BoxSizer(wx.HORIZONTAL)
        ligne_cle.Add(self.champ_cle, 1, wx.EXPAND | wx.RIGHT, 6)
        ligne_cle.Add(self.bouton_parcourir, 0)

        boite = wx.BoxSizer(wx.VERTICAL)
        boite.Add(grille, 0, wx.EXPAND | wx.ALL, 10)
        boite.Add(self.choix_auth, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 10)
        boite.Add(etiquette_cle, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        boite.Add(ligne_cle, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        boite.Add(self.etiquette_secret, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        boite.Add(self.champ_secret, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        boite.Add(aide_secret, 0, wx.LEFT | wx.RIGHT | wx.TOP, 4)
        boite.Add(boutons, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizerAndFit(boite)
        self.champ_nom.SetFocus()

    def _sur_changement_mode(self, evt):
        self._activer_champs_cle()
        self._maj_libelle_secret()

    def _maj_libelle_secret(self):
        est_cle = self.choix_auth.GetSelection() == 1
        self.etiquette_secret.SetLabel(
            "&Passphrase de la clé :" if est_cle else "&Mot de passe :"
        )

    def _activer_champs_cle(self):
        est_cle = self.choix_auth.GetSelection() == 1
        self.champ_cle.Enable(est_cle)
        self.bouton_parcourir.Enable(est_cle)

    def _sur_parcourir(self, evt):
        with wx.FileDialog(
            self, "Choisir la clé privée", style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        ) as boite:
            if boite.ShowModal() == wx.ID_OK:
                self.champ_cle.SetValue(boite.GetPath())

    def _sur_ok(self, evt):
        if not self.champ_nom.GetValue().strip():
            wx.MessageBox(
                "Le nom du profil est obligatoire.",
                "Profil incomplet", wx.OK | wx.ICON_WARNING,
            )
            self.champ_nom.SetFocus()
            return
        if not self.champ_hote.GetValue().strip():
            wx.MessageBox(
                "L'hôte est obligatoire.",
                "Profil incomplet", wx.OK | wx.ICON_WARNING,
            )
            self.champ_hote.SetFocus()
            return
        try:
            port = int(self.champ_port.GetValue().strip())
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            wx.MessageBox(
                "Le port doit être un nombre entre 1 et 65535.",
                "Profil incomplet", wx.OK | wx.ICON_WARNING,
            )
            self.champ_port.SetFocus()
            return
        if self.choix_auth.GetSelection() == 1 and not self.champ_cle.GetValue().strip():
            wx.MessageBox(
                "Le chemin de la clé est obligatoire en authentification par clé.",
                "Profil incomplet", wx.OK | wx.ICON_WARNING,
            )
            self.champ_cle.SetFocus()
            return
        self.EndModal(wx.ID_OK)

    def profil(self) -> ProfilConnexion:
        return ProfilConnexion(
            nom=self.champ_nom.GetValue().strip(),
            hote=self.champ_hote.GetValue().strip(),
            port=int(self.champ_port.GetValue().strip()),
            utilisateur=self.champ_utilisateur.GetValue().strip(),
            mode_auth="cle" if self.choix_auth.GetSelection() == 1 else "mot_de_passe",
            chemin_cle=self.champ_cle.GetValue().strip(),
            favoris=list(self._profil_existant.favoris) if self._profil_existant else [],
        )

    def secret(self) -> str:
        return self.champ_secret.GetValue()


class DialogueGestionProfils(wx.Dialog):
    """Créer, modifier ou supprimer des profils de connexion SSH."""

    def __init__(self, parent):
        super().__init__(
            parent, title="Profils de connexion SSH",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.profils = charger_profils()

        etiquette = wx.StaticText(self, label="&Profils :")
        self.liste = wx.ListBox(self, choices=self._libelles())

        bouton_nouveau = wx.Button(self, label="&Nouveau…")
        bouton_nouveau.Bind(wx.EVT_BUTTON, self._sur_nouveau)
        bouton_modifier = wx.Button(self, label="&Modifier…")
        bouton_modifier.Bind(wx.EVT_BUTTON, self._sur_modifier)
        bouton_supprimer = wx.Button(self, label="&Supprimer")
        bouton_supprimer.Bind(wx.EVT_BUTTON, self._sur_supprimer)
        bouton_fermer = wx.Button(self, id=wx.ID_CANCEL, label="Fer&mer")

        boutons = wx.BoxSizer(wx.HORIZONTAL)
        for bouton in (bouton_nouveau, bouton_modifier, bouton_supprimer, bouton_fermer):
            boutons.Add(bouton, 0, wx.RIGHT, 6)

        boite = wx.BoxSizer(wx.VERTICAL)
        boite.Add(etiquette, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        boite.Add(self.liste, 1, wx.EXPAND | wx.ALL, 8)
        boite.Add(boutons, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.SetSizer(boite)
        self.SetSize((520, 320))
        self.liste.SetFocus()

    def _libelles(self):
        return [f"{p.nom} — {p.utilisateur}@{p.hote}:{p.port}" for p in self.profils]

    def _selection(self) -> ProfilConnexion | None:
        index = self.liste.GetSelection()
        if index == wx.NOT_FOUND:
            return None
        return self.profils[index]

    def _rafraichir(self, selectionner: str | None = None):
        self.liste.Set(self._libelles())
        if selectionner is not None:
            for i, p in enumerate(self.profils):
                if p.nom == selectionner:
                    self.liste.SetSelection(i)
                    break
        enregistrer_profils(self.profils)

    def _sur_nouveau(self, evt):
        with DialogueProfilSSH(self) as boite:
            if boite.ShowModal() != wx.ID_OK:
                return
            profil = boite.profil()
            if any(p.nom == profil.nom for p in self.profils):
                wx.MessageBox(
                    f"Un profil « {profil.nom} » existe déjà.",
                    "Nom déjà utilisé", wx.OK | wx.ICON_WARNING,
                )
                return
            secret = boite.secret()
            self.profils.append(profil)
            if secret:
                enregistrer_secret(profil, secret)
            self._rafraichir(profil.nom)

    def _sur_modifier(self, evt):
        profil = self._selection()
        if profil is None:
            return
        with DialogueProfilSSH(self, profil) as boite:
            if boite.ShowModal() != wx.ID_OK:
                return
            nouveau = boite.profil()
            index = self.profils.index(profil)
            secret = boite.secret()
            if secret:
                enregistrer_secret(nouveau, secret)
            elif nouveau.nom != profil.nom:
                # Le secret est mémorisé sous l'ancien nom : le faire suivre.
                ancien_secret = lire_secret(profil)
                if ancien_secret:
                    enregistrer_secret(nouveau, ancien_secret)
                    supprimer_secret(profil)
            self.profils[index] = nouveau
            self._rafraichir(nouveau.nom)

    def _sur_supprimer(self, evt):
        profil = self._selection()
        if profil is None:
            return
        reponse = wx.MessageBox(
            f"Supprimer le profil « {profil.nom} » ? "
            "Le secret mémorisé sera aussi supprimé.",
            "Supprimer le profil", wx.YES_NO | wx.ICON_QUESTION,
        )
        if reponse != wx.YES:
            return
        supprimer_secret(profil)
        self.profils.remove(profil)
        self._rafraichir()


# --------------------------------------------------------------------------
# Boîtes de dialogue : commandes enregistrées
# --------------------------------------------------------------------------

class DialogueCommandeEnregistree(wx.Dialog):
    """Création ou modification d'une commande enregistrée."""

    def __init__(
        self, parent, commande: CommandeEnregistree | None = None,
        texte_initial: str = "",
    ):
        titre = "Modifier la commande" if commande else "Nouvelle commande enregistrée"
        super().__init__(parent, title=titre, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)

        etiquette_nom = wx.StaticText(self, label="&Nom :")
        self.champ_nom = wx.TextCtrl(self, value=commande.nom if commande else "")

        etiquette_commande = wx.StaticText(self, label="&Commande :")
        valeur_commande = commande.commande if commande else texte_initial
        self.champ_commande = wx.TextCtrl(
            self, value=valeur_commande, style=wx.TE_MULTILINE,
        )
        self.champ_commande.SetMinSize((360, 80))

        boutons = wx.StdDialogButtonSizer()
        boutons.AddButton(wx.Button(self, id=wx.ID_OK, label="&Enregistrer"))
        boutons.AddButton(wx.Button(self, id=wx.ID_CANCEL, label="Ann&uler"))
        boutons.Realize()
        self.Bind(wx.EVT_BUTTON, self._sur_ok, id=wx.ID_OK)

        boite = wx.BoxSizer(wx.VERTICAL)
        boite.Add(etiquette_nom, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        boite.Add(self.champ_nom, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        boite.Add(etiquette_commande, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        boite.Add(self.champ_commande, 1, wx.EXPAND | wx.ALL, 10)
        boite.Add(boutons, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizerAndFit(boite)
        self.SetSize((420, 260))
        self.champ_nom.SetFocus()

    def _sur_ok(self, evt):
        if not self.champ_nom.GetValue().strip():
            wx.MessageBox(
                "Le nom est obligatoire.",
                "Commande incomplète", wx.OK | wx.ICON_WARNING,
            )
            self.champ_nom.SetFocus()
            return
        if not self.champ_commande.GetValue().strip():
            wx.MessageBox(
                "La commande est obligatoire.",
                "Commande incomplète", wx.OK | wx.ICON_WARNING,
            )
            self.champ_commande.SetFocus()
            return
        self.EndModal(wx.ID_OK)

    def commande_enregistree(self) -> CommandeEnregistree:
        return CommandeEnregistree(
            nom=self.champ_nom.GetValue().strip(),
            commande=self.champ_commande.GetValue().strip(),
        )


class DialogueGestionCommandes(wx.Dialog):
    """Créer, modifier ou supprimer des commandes enregistrées."""

    def __init__(self, parent):
        super().__init__(
            parent, title="Commandes enregistrées",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.commandes = charger_commandes()

        etiquette = wx.StaticText(self, label="&Commandes :")
        self.liste = wx.ListBox(self, choices=self._libelles())

        bouton_nouveau = wx.Button(self, label="&Nouvelle…")
        bouton_nouveau.Bind(wx.EVT_BUTTON, self._sur_nouveau)
        bouton_modifier = wx.Button(self, label="&Modifier…")
        bouton_modifier.Bind(wx.EVT_BUTTON, self._sur_modifier)
        bouton_supprimer = wx.Button(self, label="&Supprimer")
        bouton_supprimer.Bind(wx.EVT_BUTTON, self._sur_supprimer)
        bouton_fermer = wx.Button(self, id=wx.ID_CANCEL, label="Fer&mer")

        boutons = wx.BoxSizer(wx.HORIZONTAL)
        for bouton in (bouton_nouveau, bouton_modifier, bouton_supprimer, bouton_fermer):
            boutons.Add(bouton, 0, wx.RIGHT, 6)

        boite = wx.BoxSizer(wx.VERTICAL)
        boite.Add(etiquette, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        boite.Add(self.liste, 1, wx.EXPAND | wx.ALL, 8)
        boite.Add(boutons, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.SetSizer(boite)
        self.SetSize((520, 320))
        self.liste.SetFocus()

    def _libelles(self):
        return [f"{c.nom} — {c.commande}" for c in self.commandes]

    def _selection(self) -> CommandeEnregistree | None:
        index = self.liste.GetSelection()
        if index == wx.NOT_FOUND:
            return None
        return self.commandes[index]

    def _rafraichir(self, selectionner: str | None = None):
        self.liste.Set(self._libelles())
        if selectionner is not None:
            for i, c in enumerate(self.commandes):
                if c.nom == selectionner:
                    self.liste.SetSelection(i)
                    break
        enregistrer_commandes(self.commandes)

    def _sur_nouveau(self, evt):
        with DialogueCommandeEnregistree(self) as boite:
            if boite.ShowModal() != wx.ID_OK:
                return
            commande = boite.commande_enregistree()
            if any(c.nom == commande.nom for c in self.commandes):
                wx.MessageBox(
                    f"Une commande « {commande.nom} » existe déjà.",
                    "Nom déjà utilisé", wx.OK | wx.ICON_WARNING,
                )
                return
            self.commandes.append(commande)
            self._rafraichir(commande.nom)

    def _sur_modifier(self, evt):
        commande = self._selection()
        if commande is None:
            return
        with DialogueCommandeEnregistree(self, commande) as boite:
            if boite.ShowModal() != wx.ID_OK:
                return
            nouvelle = boite.commande_enregistree()
            index = self.commandes.index(commande)
            self.commandes[index] = nouvelle
            self._rafraichir(nouvelle.nom)

    def _sur_supprimer(self, evt):
        commande = self._selection()
        if commande is None:
            return
        reponse = wx.MessageBox(
            f"Supprimer la commande « {commande.nom} » ?",
            "Supprimer la commande", wx.YES_NO | wx.ICON_QUESTION,
        )
        if reponse != wx.YES:
            return
        self.commandes.remove(commande)
        self._rafraichir()


class DialogueChoisirCommande(wx.Dialog):
    """Choisir une commande enregistrée à placer dans la saisie.

    Même disposition qu'un choix de bloc (DialogueListeBlocs) : la
    commande choisie est déposée dans le champ de saisie de la session
    courante, jamais exécutée directement — on garde le principe d'une
    commande à la fois, avec relecture possible avant l'envoi."""

    def __init__(self, parent, commandes: list[CommandeEnregistree]):
        super().__init__(
            parent, title="Utiliser une commande enregistrée",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.commandes = commandes
        self.commande_choisie: CommandeEnregistree | None = None

        etiquette = wx.StaticText(self, label="&Commandes :")
        self.liste = wx.ListBox(self, choices=self._libelles())
        self.liste.SetSelection(0)
        self.liste.Bind(wx.EVT_LISTBOX_DCLICK, self._sur_utiliser)

        bouton_utiliser = wx.Button(self, label="&Utiliser")
        bouton_utiliser.SetDefault()
        bouton_utiliser.Bind(wx.EVT_BUTTON, self._sur_utiliser)
        bouton_fermer = wx.Button(self, id=wx.ID_CANCEL, label="Fer&mer")

        boutons = wx.BoxSizer(wx.HORIZONTAL)
        boutons.Add(bouton_utiliser, 0, wx.RIGHT, 6)
        boutons.Add(bouton_fermer, 0)

        boite = wx.BoxSizer(wx.VERTICAL)
        boite.Add(etiquette, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        boite.Add(self.liste, 1, wx.EXPAND | wx.ALL, 8)
        boite.Add(boutons, 0, wx.ALIGN_RIGHT | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.SetSizer(boite)
        self.SetSize((480, 360))
        self.liste.SetFocus()

    def _libelles(self):
        return [f"{c.nom} — {c.commande}" for c in self.commandes]

    def _sur_utiliser(self, evt):
        index = self.liste.GetSelection()
        if index == wx.NOT_FOUND:
            return
        self.commande_choisie = self.commandes[index]
        self.EndModal(wx.ID_OK)


# --------------------------------------------------------------------------
# Fenêtre principale
# --------------------------------------------------------------------------

class Fenetre(wx.Frame):

    def __init__(self, voix: Voix, chemin_journal: Path):
        super().__init__(None, title=APP_NOM, size=(960, 640))
        self.voix = voix
        self.chemin_journal = chemin_journal
        self.reglages = Reglages()
        self.compteur_sessions = 0

        self.carnet = wx.Notebook(self)
        self.carnet.SetName("Sessions")

        self._construire_menus()
        self.CreateStatusBar()
        self.SetStatusText("Prêt")

        self.Bind(wx.EVT_CHAR_HOOK, self.sur_touche_globale)
        self.carnet.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.sur_changement_page)

        self.Bind(wx.EVT_CLOSE, self.sur_fermeture)
        self.Bind(wx.EVT_ACTIVATE, self.sur_activation)
        # Second filet : si le focus atterrit sur le cadre lui-même
        # plutôt que sur un contrôle, on le redirige aussitôt.
        self.Bind(wx.EVT_SET_FOCUS, self.sur_focus_cadre)

        self.nouvelle_session("Local")
        self.Centre()
        # Silencieux : une vérification de confort au démarrage ne doit
        # rien annoncer si tout est déjà à jour ou si le réseau manque.
        self.verifier_mise_a_jour(silencieux=True)

    def sur_focus_cadre(self, evt):
        wx.CallAfter(self.rendre_focus_au_champ)
        evt.Skip()

    def sur_activation(self, evt):
        """Au retour d'un Alt+Tab, redonne toujours le focus à la saisie.

        Sans cela le focus atterrit sur la fenêtre elle-même, ou reste sur
        un endroit imprévisible selon ce que Windows choisit de restaurer :
        dans les deux cas NVDA n'annonce rien d'exploitable. Plutôt que de
        deviner où l'utilisateur se trouvait avant de basculer d'appli, on
        revient systématiquement au champ de saisie de commandes.
        Le CallLater est nécessaire, car Windows repositionne encore le
        focus après cet événement.
        """
        if evt.GetActive():
            wx.CallLater(80, self.rendre_focus_au_champ)
        evt.Skip()

    def rendre_focus_au_champ(self):
        """Pose le focus sur le champ principal de la session courante :
        la saisie en mode terminal, la liste en mode navigation.

        Longtemps figé sur panneau.saisie sans condition — sans dommage
        avant l'existence du mode navigation, mais posait le focus sur un
        champ caché (saisie) dès qu'une boîte de dialogue du mode
        fichiers se refermait, ou au retour d'un Alt+Tab pendant qu'une
        session était en mode navigation.
        """
        if not self:                 # fenêtre détruite entre-temps
            return
        panneau = self.session()
        if panneau is None:
            return
        cible = panneau.liste_fichiers if panneau.mode_navigation else panneau.saisie
        cible.SetFocus()

    def sur_fermeture(self, evt):
        """Une commande en cours doit être tuée : sinon le processus
        survivrait à la fenêtre, invisible et sans moyen de l'arrêter."""
        occupees = [
            self.carnet.GetPage(i).nom
            for i in range(self.carnet.GetPageCount())
            if self.carnet.GetPage(i).en_cours
        ]
        if occupees and evt.CanVeto():
            reponse = wx.MessageBox(
                "Une commande est en cours dans : "
                + ", ".join(occupees)
                + ".\n\nQuitter et l'interrompre ?",
                "Commande en cours", wx.YES_NO | wx.ICON_QUESTION,
            )
            if reponse != wx.YES:
                evt.Veto()
                return
        for index in range(self.carnet.GetPageCount()):
            panneau = self.carnet.GetPage(index)
            if panneau.en_cours:
                panneau.executeur.interrompre()
            panneau.executeur.fermer()
        logging.info("Fermeture de la fenêtre.")
        evt.Skip()

    # -- construction ------------------------------------------------------

    def _construire_menus(self):
        barre = wx.MenuBar()

        m_session = wx.Menu()

        m_nouvelle = wx.Menu()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.nouvelle_session(),
                  m_nouvelle.Append(wx.ID_ANY, "Session &locale\tCtrl+T"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.nouvelle_session_ssh(),
                  m_nouvelle.Append(wx.ID_ANY, "Session &SSH…  Ctrl+Maj+O"))
        m_session.AppendSubMenu(m_nouvelle, "&Nouvelle session")

        self.Bind(wx.EVT_MENU,
                  lambda e: self.gerer_profils_ssh(),
                  m_session.Append(wx.ID_ANY, "&Gestion des profils SSH…"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.fermer_session(),
                  m_session.Append(wx.ID_ANY, "&Fermer la session\tCtrl+W"))
        m_session.AppendSeparator()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.interrompre_commande(),
                  m_session.Append(wx.ID_ANY, "&Interrompre la commande  Ctrl+Maj+K"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.changer_repertoire(),
                  m_session.Append(wx.ID_ANY, "Changer de &répertoire  Ctrl+Maj+D"))

        # Actions du mode navigation, à la racine plutôt que dans un
        # sous-menu : ce sont de vraies actions de session (comme
        # « Changer de répertoire » juste au-dessus), pas une hiérarchie
        # à part. Grisées hors mode navigation (_synchroniser_menu_navigation)
        # plutôt que masquées : un menu de forme stable, avec certains
        # items temporairement indisponibles, se retrouve plus facilement
        # au clavier qu'un menu qui change de nombre d'entrées — et NVDA
        # annonce déjà « grisé », qui porte la même information.
        m_session.AppendSeparator()
        self.item_nouveau_dossier_sftp = m_session.Append(
            wx.ID_ANY, "&Nouveau dossier…  Ctrl+Maj+N"
        )
        self.Bind(wx.EVT_MENU, lambda e: self.creer_dossier_sftp(),
                  self.item_nouveau_dossier_sftp)
        self.item_renommer_sftp = m_session.Append(wx.ID_ANY, "&Renommer…\tF2")
        self.Bind(wx.EVT_MENU, lambda e: self.renommer_entree_sftp(),
                  self.item_renommer_sftp)
        self.item_supprimer_sftp = m_session.Append(wx.ID_ANY, "&Supprimer\tSuppr")
        self.Bind(wx.EVT_MENU, lambda e: self.supprimer_entree_sftp(),
                  self.item_supprimer_sftp)
        self.item_envoyer_sftp = m_session.Append(
            wx.ID_ANY, "&Envoyer un fichier…  Ctrl+Maj+E"
        )
        self.Bind(wx.EVT_MENU, lambda e: self.envoyer_fichier_sftp(),
                  self.item_envoyer_sftp)
        self.item_telecharger_sftp = m_session.Append(
            wx.ID_ANY, "&Télécharger l'élément sélectionné…  Ctrl+Maj+T"
        )
        self.Bind(wx.EVT_MENU, lambda e: self.telecharger_entree_sftp(),
                  self.item_telecharger_sftp)
        self.item_rechercher_sftp = m_session.Append(
            wx.ID_ANY, "Re&chercher des fichiers…  Ctrl+Maj+G"
        )
        self.Bind(wx.EVT_MENU, lambda e: self.rechercher_fichiers_sftp(),
                  self.item_rechercher_sftp)
        self.items_action_fichiers = [
            self.item_nouveau_dossier_sftp, self.item_renommer_sftp,
            self.item_supprimer_sftp, self.item_envoyer_sftp,
            self.item_telecharger_sftp, self.item_rechercher_sftp,
        ]
        self.Bind(wx.EVT_MENU,
                  lambda e: self.gerer_favoris_sftp(),
                  m_session.Append(wx.ID_ANY, "&Dossiers favoris…  Ctrl+Maj+A"))
        m_session.AppendSeparator()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.Close(),
                  m_session.Append(wx.ID_EXIT, "&Quitter\tAlt+F4"))
        barre.Append(m_session, "&Session")

        m_commandes = wx.Menu()
        self.item_utiliser_commande = m_commandes.Append(
            wx.ID_ANY, "&Utiliser une commande enregistrée…  Ctrl+Maj+J"
        )
        self.Bind(wx.EVT_MENU, lambda e: self.utiliser_commande_enregistree(),
                  self.item_utiliser_commande)
        self.item_enregistrer_commande = m_commandes.Append(
            wx.ID_ANY, "Enregistrer la commande &actuelle…  Ctrl+Maj+M"
        )
        self.Bind(wx.EVT_MENU, lambda e: self.enregistrer_commande_actuelle(),
                  self.item_enregistrer_commande)
        self.item_gerer_commandes = m_commandes.Append(
            wx.ID_ANY, "&Gérer les commandes enregistrées…"
        )
        self.Bind(wx.EVT_MENU, lambda e: self.gerer_commandes_enregistrees(),
                  self.item_gerer_commandes)
        # Inutiles en mode navigation (pas de saisie à retaper ou
        # enregistrer) : grisés par _synchroniser_menu_navigation, même
        # principe que items_action_fichiers juste au-dessus mais avec
        # la condition inversée.
        self.items_commandes = [
            self.item_utiliser_commande, self.item_enregistrer_commande,
            self.item_gerer_commandes,
        ]
        barre.Append(m_commandes, "&Commandes")

        m_bloc = wx.Menu()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.naviguer_bloc(-1),
                  m_bloc.Append(wx.ID_ANY, "Bloc &précédent\tAlt+Haut"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.naviguer_bloc(1),
                  m_bloc.Append(wx.ID_ANY, "Bloc &suivant\tAlt+Bas"))
        m_bloc.AppendSeparator()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.copier_bloc(complet=True),
                  m_bloc.Append(wx.ID_ANY, "&Copier le bloc courant  Ctrl+Maj+C"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.copier_bloc(complet=False),
                  m_bloc.Append(wx.ID_ANY, "Copier la sortie &seule  Ctrl+Maj+S"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.copier_dernier_bloc(),
                  m_bloc.Append(wx.ID_ANY, "Copier le &dernier bloc  Ctrl+Maj+L"))
        m_bloc.AppendSeparator()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.lister_blocs(),
                  m_bloc.Append(wx.ID_ANY, "&Liste des blocs\tCtrl+B"))
        barre.Append(m_bloc, "&Blocs")

        m_affichage = wx.Menu()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.basculer_champ(),
                  m_affichage.Append(wx.ID_ANY, "&Basculer saisie / sortie\tF6"))
        # Libellé mis à jour par _synchroniser_menu_navigation (annonce ce
        # vers quoi on bascule, pas juste « basculer ») : un basculement
        # de vue comme celui-ci, pas une action de session.
        self.item_mode_navigation = m_affichage.Append(
            wx.ID_ANY, "Basculer en mode &navigation  Ctrl+Maj+F"
        )
        self.Bind(wx.EVT_MENU, lambda e: self.basculer_mode_navigation(),
                  self.item_mode_navigation)
        self.Bind(wx.EVT_MENU,
                  lambda e: self.repeter_saisie(),
                  m_affichage.Append(wx.ID_ANY, "&Relire la saisie  Ctrl+Maj+R"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.changer_verbosite(),
                  m_affichage.Append(wx.ID_ANY, "Niveau de &verbosité  Ctrl+Maj+V"))
        self.item_suivre = m_affichage.Append(
            wx.ID_ANY, "Aller a&utomatiquement à la sortie  Ctrl+Maj+U",
            "Après chaque commande, place le curseur sur l'en-tête du bloc",
            wx.ITEM_CHECK,
        )
        self.item_suivre.Check(self.reglages.suivre_sortie)
        self.Bind(wx.EVT_MENU,
                  lambda e: self.basculer_suivi(),
                  self.item_suivre)
        self.item_horodatage = m_affichage.Append(
            wx.ID_ANY, "Afficher l'&horodatage des blocs  Ctrl+Maj+H",
            "Ajoute l'heure dans la ligne d'en-tête de chaque bloc",
            wx.ITEM_CHECK,
        )
        self.item_horodatage.Check(self.reglages.afficher_horodatage)
        self.Bind(wx.EVT_MENU,
                  lambda e: self.basculer_horodatage(),
                  self.item_horodatage)

        m_taille = wx.Menu()
        self.items_taille_police = []
        for nom_preset, valeur in TAILLES_POLICE_PRESETS.items():
            item = m_taille.Append(
                wx.ID_ANY, f"{nom_preset} ({valeur})", "", wx.ITEM_CHECK,
            )
            self.items_taille_police.append((item, valeur))
            self.Bind(wx.EVT_MENU,
                      lambda e, v=valeur: self.definir_taille_police(v),
                      item)
        self._synchroniser_taille_police()
        m_affichage.AppendSubMenu(m_taille, "&Taille de la police")

        self.Bind(wx.EVT_MENU,
                  lambda e: self.effacer_sortie(),
                  m_affichage.Append(wx.ID_ANY, "&Effacer la sortie"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.vider_historique(),
                  m_affichage.Append(wx.ID_ANY, "Vider l'&historique des commandes"))
        barre.Append(m_affichage, "&Affichage")

        m_aide = wx.Menu()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.ouvrir_documentation(),
                  m_aide.Append(wx.ID_ANY, "&Documentation"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.ouvrir_journal(),
                  m_aide.Append(wx.ID_ANY, "Ouvrir le &journal"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.verifier_mise_a_jour(silencieux=False),
                  m_aide.Append(wx.ID_ANY, "&Vérifier les mises à jour"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.ouvrir_depot_github(),
                  m_aide.Append(wx.ID_ANY, "&Dépôt GitHub"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.a_propos(),
                  m_aide.Append(wx.ID_ABOUT, "&À propos"))
        barre.Append(m_aide, "&Aide")

        self.SetMenuBar(barre)
        self._synchroniser_menu_navigation()

    # -- sessions ----------------------------------------------------------

    def nouvelle_session(self, nom: str | None = None):
        if nom is None:
            self.compteur_sessions += 1
            nom = f"Session {self.compteur_sessions + 1}"
        panneau = PanneauSession(self.carnet, nom, self.voix, self.reglages)
        self.carnet.AddPage(panneau, nom, select=True)
        panneau.saisie.SetFocus()
        logging.info("Session créée : %s", nom)
        self.voix.dire(braille=f"Session {nom}")

    def fermer_session(self):
        if self.carnet.GetPageCount() <= 1:
            self.voix.dire("Impossible de fermer la dernière session.")
            return
        index = self.carnet.GetSelection()
        nom = self.carnet.GetPageText(index)
        reponse = wx.MessageBox(
            f"Fermer la session « {nom} » ?",
            "Fermer la session",
            wx.YES_NO | wx.ICON_QUESTION,
        )
        if reponse == wx.YES:
            panneau = self.carnet.GetPage(index)
            if panneau.en_cours:
                panneau.executeur.interrompre()
            panneau.executeur.fermer()
            self.carnet.DeletePage(index)
            logging.info("Session fermée : %s", nom)

    # -- SSH -----------------------------------------------------------

    def gerer_profils_ssh(self):
        with DialogueGestionProfils(self) as boite:
            boite.ShowModal()

    # -- commandes enregistrées ---------------------------------------------

    def utiliser_commande_enregistree(self) -> None:
        panneau = self.session()
        if panneau is None:
            return
        commandes = charger_commandes()
        if not commandes:
            wx.MessageBox(
                "Aucune commande enregistrée pour l'instant. Tapez une "
                "commande dans la saisie, puis utilisez le menu Commandes "
                "→ Enregistrer la commande actuelle.",
                "Aucune commande enregistrée", wx.OK | wx.ICON_INFORMATION,
            )
            return
        with DialogueChoisirCommande(self, commandes) as boite:
            if boite.ShowModal() != wx.ID_OK or boite.commande_choisie is None:
                return
            choisie = boite.commande_choisie
        panneau.saisie.SetValue(choisie.commande)
        panneau.saisie.SetFocus()
        panneau.saisie.SetInsertionPointEnd()
        self.voix.dire(
            choisie.commande, braille=choisie.commande, interrompre=True,
        )

    def enregistrer_commande_actuelle(self) -> None:
        panneau = self.session()
        if panneau is None:
            return
        texte = panneau.saisie.GetValue().strip()
        if not texte:
            wx.MessageBox(
                "Le champ de saisie est vide : rien à enregistrer.",
                "Rien à enregistrer", wx.OK | wx.ICON_WARNING,
            )
            return
        commandes = charger_commandes()
        with DialogueCommandeEnregistree(self, texte_initial=texte) as boite:
            if boite.ShowModal() != wx.ID_OK:
                return
            nouvelle = boite.commande_enregistree()
        if any(c.nom == nouvelle.nom for c in commandes):
            wx.MessageBox(
                f"Une commande « {nouvelle.nom} » existe déjà.",
                "Nom déjà utilisé", wx.OK | wx.ICON_WARNING,
            )
            return
        commandes.append(nouvelle)
        enregistrer_commandes(commandes)
        self.voix.dire(f"Commande « {nouvelle.nom} » enregistrée.", interrompre=True)

    def gerer_commandes_enregistrees(self) -> None:
        with DialogueGestionCommandes(self) as boite:
            boite.ShowModal()

    def _verifier_hote_ssh(self, message: str) -> bool:
        """Appelée depuis le thread de connexion : bloque jusqu'à la
        réponse. Un contrôle Win32 natif est lu directement par NVDA."""
        resultat: dict[str, bool] = {"valeur": False}
        evenement = threading.Event()

        def ouvrir():
            reponse = wx.MessageBox(
                message, "Vérification de la clé d'hôte", wx.YES_NO | wx.ICON_WARNING,
            )
            resultat["valeur"] = reponse == wx.YES
            evenement.set()

        wx.CallAfter(ouvrir)
        evenement.wait()
        return resultat["valeur"]

    def nouvelle_session_ssh(self):
        profils = charger_profils()
        if not profils:
            reponse = wx.MessageBox(
                "Aucun profil de connexion enregistré. En créer un maintenant ?",
                "Aucun profil", wx.YES_NO | wx.ICON_QUESTION,
            )
            if reponse == wx.YES:
                self.gerer_profils_ssh()
            return

        choix = [f"{p.nom} — {p.utilisateur}@{p.hote}:{p.port}" for p in profils]
        with wx.SingleChoiceDialog(
            self, "Se connecter avec quel profil ?", "Nouvelle session SSH", choix,
        ) as boite:
            if boite.ShowModal() != wx.ID_OK:
                return
            profil = profils[boite.GetSelection()]

        secret = lire_secret(profil)
        if secret is None:
            libelle = (
                "Passphrase de la clé (laisser vide si aucune) :"
                if profil.mode_auth == "cle" else "Mot de passe :"
            )
            with wx.TextEntryDialog(
                self, libelle, f"Connexion à {profil.nom}", style=wx.TE_PASSWORD,
            ) as boite:
                if boite.ShowModal() != wx.ID_OK:
                    return
                secret = boite.GetValue()
            if secret and wx.MessageBox(
                "Mémoriser ce secret dans le Gestionnaire d'identifiants "
                "Windows pour la prochaine fois ?",
                "Mémoriser le secret", wx.YES_NO | wx.ICON_QUESTION,
            ) == wx.YES:
                enregistrer_secret(profil, secret)

        self.SetStatusText(f"Connexion à {profil.nom}…")
        self.voix.dire(f"Connexion à {profil.nom}.", interrompre=True)

        def connecter():
            executeur = ExecuteurSSH()
            try:
                executeur.connecter(profil, secret, self._verifier_hote_ssh)
            except Exception as erreur:
                logging.exception("Connexion SSH échouée à %s", profil.nom)
                wx.CallAfter(self._echec_connexion_ssh, profil, erreur)
                return
            wx.CallAfter(self._connexion_ssh_reussie, profil, executeur)

        threading.Thread(target=connecter, daemon=True).start()

    def _echec_connexion_ssh(self, profil: ProfilConnexion, erreur: Exception):
        self.SetStatusText("Connexion échouée.")
        wx.MessageBox(
            f"Impossible de se connecter à « {profil.nom} » :\n{erreur}",
            "Connexion échouée", wx.OK | wx.ICON_ERROR,
        )
        self.voix.dire("Connexion échouée.", interrompre=True)

    def reconnecter_ssh(self, panneau: PanneauSession) -> None:
        """Reconnecte la session existante à la place — même onglet,
        même historique de blocs — plutôt que d'ouvrir une nouvelle
        session comme le ferait Ctrl+Maj+O."""
        profil = panneau.profil
        if profil is None:
            return
        secret = lire_secret(profil)
        if secret is None:
            libelle = (
                "Passphrase de la clé (laisser vide si aucune) :"
                if profil.mode_auth == "cle" else "Mot de passe :"
            )
            with wx.TextEntryDialog(
                self, libelle, f"Reconnexion à {profil.nom}", style=wx.TE_PASSWORD,
            ) as boite:
                if boite.ShowModal() != wx.ID_OK:
                    return
                secret = boite.GetValue()

        self.SetStatusText(f"Reconnexion à {profil.nom}…")
        self.voix.dire(f"Reconnexion à {profil.nom}.", interrompre=True)

        def travailler():
            panneau.executeur.fermer()
            try:
                panneau.executeur.connecter(profil, secret, self._verifier_hote_ssh)
            except Exception as erreur:
                logging.exception("Reconnexion SSH échouée à %s", profil.nom)
                wx.CallAfter(self._echec_reconnexion_ssh, profil, erreur)
                return
            wx.CallAfter(self._reconnexion_ssh_reussie, panneau, profil)

        threading.Thread(target=travailler, daemon=True).start()

    def _echec_reconnexion_ssh(self, profil: ProfilConnexion, erreur: Exception):
        self.SetStatusText("Reconnexion échouée.")
        wx.MessageBox(
            f"Impossible de se reconnecter à « {profil.nom} » :\n{erreur}",
            "Reconnexion échouée", wx.OK | wx.ICON_ERROR,
        )
        self.voix.dire("Reconnexion échouée.", interrompre=True)

    def _reconnexion_ssh_reussie(self, panneau: PanneauSession, profil: ProfilConnexion):
        self.SetStatusText(f"Reconnecté à {profil.nom}.")
        self.voix.dire(f"Reconnecté à {profil.nom}.", interrompre=True)
        logging.info("Session SSH reconnectée : %s", profil.nom)

    def _connexion_ssh_reussie(self, profil: ProfilConnexion, executeur: ExecuteurSSH):
        panneau = PanneauSession(
            self.carnet, profil.nom, self.voix, self.reglages,
            executeur=executeur, distant=True, profil=profil,
        )
        self.carnet.AddPage(panneau, profil.nom, select=True)
        panneau.saisie.SetFocus()
        logging.info("Session SSH créée : %s", profil.nom)
        self.SetStatusText(f"Connecté à {profil.nom}.")
        self.voix.dire(f"Connecté à {profil.nom}.", interrompre=True)

        def recuperer_repertoire():
            # En silence, sans passer par panneau.executer() : ça créerait
            # un bloc « pwd » visible que l'utilisateur n'a pas demandé.
            # Sans ça, le champ de Ctrl+Maj+D resterait vide tant qu'aucun
            # changement de répertoire n'a été fait à la main.
            resultat = executeur.executer("pwd", listing_lisible=False)
            chemin = resultat.sortie.strip()
            if resultat.code_retour == 0 and chemin:
                wx.CallAfter(panneau.definir_repertoire, chemin)

        threading.Thread(target=recuperer_repertoire, daemon=True).start()

    def session(self) -> PanneauSession | None:
        index = self.carnet.GetSelection()
        if index == wx.NOT_FOUND:
            return None
        return self.carnet.GetPage(index)

    def changer_session(self, delta: int):
        total = self.carnet.GetPageCount()
        if total <= 1:
            return
        index = (self.carnet.GetSelection() + delta) % total
        self.carnet.SetSelection(index)

    def aller_session(self, index: int):
        if 0 <= index < self.carnet.GetPageCount():
            self.carnet.SetSelection(index)

    def sur_changement_page(self, evt):
        panneau = self.session()
        if panneau is not None:
            # NVDA annonce l'onglet puis le champ, dont le nom accessible
            # contient déjà la session. On se contente du braille.
            self.voix.dire(braille=f"Session {panneau.nom}")
            # Le titre et la barre de statut sont partagés entre les
            # sessions : sans ça, changer d'onglet garderait affiché le
            # statut (et le titre) de la session précédente. C'est
            # rafraichir_statut qui pose le titre, pas ce gestionnaire :
            # il doit rester la seule source pour ne pas écraser un
            # « commande en cours » par erreur.
            panneau.rafraichir_statut()

            # Le focus ne suit QUE si le changement vient d'ailleurs que
            # de la barre d'onglets. Sinon, parcourir les onglets aux
            # flèches deviendrait impossible : chaque flèche renverrait
            # aussitôt vers le champ de saisie.
            if wx.Window.FindFocus() is not self.carnet:
                cible = panneau.liste_fichiers if panneau.mode_navigation else panneau.saisie
                wx.CallAfter(cible.SetFocus)
        self._synchroniser_menu_navigation()
        evt.Skip()

    # -- navigation et copie ----------------------------------------------

    def basculer_champ(self):
        panneau = self.session()
        if panneau is None:
            return
        if panneau.mode_navigation:
            # Un seul champ en mode navigation : rien à basculer, F6 ramène
            # juste dessus si le focus s'en était échappé (Alt+Tab...).
            panneau.liste_fichiers.SetFocus()
            return
        if panneau.saisie.HasFocus():
            panneau.sortie.SetFocus()
        else:
            panneau.saisie.SetFocus()

    def naviguer_bloc(self, delta: int):
        panneau = self.session()
        if panneau is None or not panneau.blocs:
            self.voix.dire("Aucun bloc.")
            return
        courant = panneau.bloc_courant()
        index = panneau.blocs.index(courant) if courant else 0
        cible = index + delta
        if cible < 0:
            self.voix.dire("Premier bloc.")
            cible = 0
        elif cible >= len(panneau.blocs):
            self.voix.dire("Dernier bloc.")
            cible = len(panneau.blocs) - 1
        panneau.aller_au_bloc(panneau.blocs[cible])

    def copier_bloc(self, complet: bool = True):
        panneau = self.session()
        if panneau is None:
            return
        bloc = panneau.bloc_courant()
        if bloc is None:
            self.voix.dire("Aucun bloc à copier.")
            return
        texte = bloc.texte_complet() if complet else bloc.sortie
        if copier_presse_papiers(texte):
            quoi = "Bloc" if complet else "Sortie"
            self.voix.dire(f"{quoi} {bloc.numero} copié, {decompte(bloc.nb_lignes)}.",
                           interrompre=True)

    def copier_dernier_bloc(self):
        panneau = self.session()
        if panneau is None or not panneau.blocs:
            self.voix.dire("Aucun bloc à copier.")
            return
        bloc = panneau.blocs[-1]
        if copier_presse_papiers(bloc.texte_complet()):
            self.voix.dire(f"Dernier bloc copié, {decompte(bloc.nb_lignes)}.",
                           interrompre=True)

    def lister_blocs(self):
        panneau = self.session()
        if panneau is None or not panneau.blocs:
            self.voix.dire("Aucun bloc.")
            return
        with DialogueListeBlocs(self, panneau.blocs, self.voix) as boite:
            if boite.ShowModal() == wx.ID_OK and boite.bloc_choisi is not None:
                panneau.aller_au_bloc(boite.bloc_choisi)

    def changer_verbosite(self):
        self.reglages.verbosite = (self.reglages.verbosite + 1) % 3
        nom = NOMS_VERBOSITE[self.reglages.verbosite]
        enregistrer_reglages(self.reglages)
        self.SetStatusText(f"Verbosité : {nom}")
        self.voix.dire(f"Verbosité : {nom}.", interrompre=True)
        logging.info("Verbosité changée : %s", nom)

    def interrompre_commande(self):
        panneau = self.session()
        if panneau is not None:
            panneau.interrompre()

    def changer_repertoire(self):
        panneau = self.session()
        if panneau is None:
            return
        if panneau.distant:
            # Pas de sélecteur de dossier possible : le système de
            # fichiers parcouru serait celui de cette machine, pas celui
            # du serveur distant. On tape donc le chemin directement.
            with wx.TextEntryDialog(
                self, "Répertoire distant (chemin sur le serveur) :",
                "Changer de répertoire", panneau.repertoire,
            ) as boite:
                if boite.ShowModal() != wx.ID_OK:
                    return
                # En mode navigation, c'est charger_dossier_sftp (pas juste
                # definir_repertoire) qui doit poser le nouveau chemin :
                # sinon la liste resterait affichée sur l'ancien dossier
                # pendant que le titre afficherait déjà le nouveau.
                if panneau.mode_navigation:
                    panneau.charger_dossier_sftp(boite.GetValue().strip())
                else:
                    panneau.definir_repertoire(boite.GetValue().strip())
        else:
            with wx.DirDialog(
                self, "Choisissez le répertoire de travail",
                defaultPath=panneau.repertoire,
                style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST,
            ) as boite:
                if boite.ShowModal() != wx.ID_OK:
                    return
                panneau.definir_repertoire(boite.GetPath())
        self.SetStatusText(f"Répertoire : {panneau.repertoire}")
        self.voix.dire(f"Répertoire : {panneau.repertoire}", interrompre=True)
        logging.info("[%s] répertoire : %s", panneau.nom, panneau.repertoire)

    # -- navigateur de fichiers distant (SFTP) ------------------------------
    #
    # Le gros de la logique vit sur PanneauSession (même principe que
    # executer/interrompre) : ces méthodes ne font que retrouver la
    # session courante, comme copier_bloc ou changer_repertoire un peu
    # plus haut.

    def basculer_mode_navigation(self):
        panneau = self.session()
        if panneau is not None:
            panneau.basculer_mode_navigation()
        self._synchroniser_menu_navigation()

    def creer_dossier_sftp(self):
        panneau = self.session()
        if panneau is not None:
            panneau.creer_dossier_sftp()

    def renommer_entree_sftp(self):
        panneau = self.session()
        if panneau is not None:
            panneau.renommer_entree_sftp()

    def supprimer_entree_sftp(self):
        panneau = self.session()
        if panneau is not None:
            panneau.supprimer_entree_sftp()

    def telecharger_entree_sftp(self):
        panneau = self.session()
        if panneau is not None:
            panneau.telecharger_entree_sftp()

    def envoyer_fichier_sftp(self):
        panneau = self.session()
        if panneau is not None:
            panneau.envoyer_fichier_sftp()

    def gerer_favoris_sftp(self):
        panneau = self.session()
        if panneau is None or not panneau.distant or panneau.profil is None:
            self.voix.dire("Cette action nécessite une session SSH.", interrompre=True)
            return
        with DialogueFavoris(self, panneau) as boite:
            boite.ShowModal()
        if panneau.mode_navigation:
            panneau.liste_fichiers.SetFocus()
        else:
            panneau.saisie.SetFocus()

    def rechercher_fichiers_sftp(self):
        panneau = self.session()
        if panneau is None or not panneau.mode_navigation:
            self.voix.dire(
                "Cette action nécessite le mode navigation (Ctrl+Maj+F).",
                interrompre=True,
            )
            return
        with DialogueRechercheFichiers(self, panneau) as boite:
            boite.ShowModal()
        panneau.liste_fichiers.SetFocus()

    def repeter_saisie(self):
        panneau = self.session()
        if panneau is not None:
            panneau.repeter_saisie()

    def basculer_suivi(self):
        self.reglages.suivre_sortie = not self.reglages.suivre_sortie
        self.item_suivre.Check(self.reglages.suivre_sortie)
        enregistrer_reglages(self.reglages)
        etat = "activé" if self.reglages.suivre_sortie else "désactivé"
        self.SetStatusText(f"Suivi de la sortie {etat}")
        self.voix.dire(f"Suivi de la sortie {etat}.", interrompre=True)
        logging.info("Suivi de la sortie %s", etat)

    def basculer_horodatage(self):
        self.reglages.afficher_horodatage = not self.reglages.afficher_horodatage
        self.item_horodatage.Check(self.reglages.afficher_horodatage)
        for index in range(self.carnet.GetPageCount()):
            self.carnet.GetPage(index).redessiner()
        enregistrer_reglages(self.reglages)
        etat = "affiche" if self.reglages.afficher_horodatage else "masque"
        self.SetStatusText(f"Horodatage {etat}")
        self.voix.dire(f"Horodatage {etat}.", interrompre=True)
        logging.info("Horodatage %s", etat)

    def definir_taille_police(self, taille: int) -> None:
        taille = max(TAILLE_POLICE_MIN, min(TAILLE_POLICE_MAX, taille))
        if taille == self.reglages.taille_police:
            return
        self.reglages.taille_police = taille
        for index in range(self.carnet.GetPageCount()):
            self.carnet.GetPage(index).appliquer_taille_police()
        self._synchroniser_taille_police()
        enregistrer_reglages(self.reglages)
        self.SetStatusText(f"Taille de police : {taille}")
        self.voix.dire(f"Taille de police {taille}.", interrompre=True)
        logging.info("Taille de police réglée à %s", taille)

    def ajuster_taille_police(self, delta: int) -> None:
        self.definir_taille_police(self.reglages.taille_police + delta)

    def _synchroniser_taille_police(self) -> None:
        """Coche l'entrée du sous-menu correspondant à la taille active.

        Aucune entrée n'est cochée si la taille courante (ajustée via
        Ctrl+- ou Ctrl++) ne correspond à aucun des préréglages.
        """
        for item, valeur in self.items_taille_police:
            item.Check(valeur == self.reglages.taille_police)

    def _synchroniser_menu_navigation(self) -> None:
        """Libellé du basculement de vue et disponibilité des actions du
        mode navigation, à jour avec la session actuellement affichée.

        Chaque session porte son propre mode_navigation : changer d'onglet doit
        changer ce que ce menu propose, pas seulement le fait de
        basculer soi-même.
        """
        panneau = self.session()
        actif = panneau is not None and panneau.mode_navigation
        self.item_mode_navigation.SetItemLabel(
            "Basculer en mode &terminal  Ctrl+Maj+F" if actif
            else "Basculer en mode &navigation  Ctrl+Maj+F"
        )
        for item in self.items_action_fichiers:
            item.Enable(actif)
        # Inverse : pas de saisie à retaper ou enregistrer en mode
        # fichiers, ces trois actions n'y servent à rien.
        for item in self.items_commandes:
            item.Enable(not actif)

    def effacer_sortie(self):
        panneau = self.session()
        if panneau is None:
            return
        panneau.sortie.SetValue("")
        panneau.blocs.clear()
        self.voix.dire("Sortie effacée.", interrompre=True)

    def vider_historique(self):
        panneau = self.session()
        if panneau is None:
            return
        panneau.historique.clear()
        panneau.index_historique = 0
        panneau._brouillons.clear()
        self.voix.dire("Historique des commandes vidé.", interrompre=True)
        logging.info("[%s] historique des commandes vidé", panneau.nom)

    # -- divers ------------------------------------------------------------

    def ouvrir_documentation(self):
        """Ouvre la documentation HTML dans le navigateur par défaut.

        Le dossier docs vit à côté de l'exe (ou du script), sur le même
        principe que settings.json : voir dossier_base(). compiler.bat le
        copie dans dist\\LazyShell, donc il est déjà présent dans
        l'archive téléchargée depuis les Releases, sans étape à part.
        """
        chemin = dossier_base() / "docs" / "index.html"
        try:
            import os
            os.startfile(str(chemin))
        except Exception:
            logging.exception("Impossible d'ouvrir la documentation.")
            wx.MessageBox(
                f"La documentation se trouve ici :\n{chemin}",
                "Documentation", wx.OK | wx.ICON_INFORMATION,
            )

    def ouvrir_journal(self):
        try:
            import os
            os.startfile(str(self.chemin_journal))
        except Exception:
            logging.exception("Impossible d'ouvrir le journal.")
            wx.MessageBox(
                f"Le journal se trouve ici :\n{self.chemin_journal}",
                "Journal", wx.OK | wx.ICON_INFORMATION,
            )

    def ouvrir_depot_github(self):
        """Ouvre la page du dépôt GitHub dans le navigateur par défaut."""
        import webbrowser
        webbrowser.open(URL_DEPOT)

    def verifier_mise_a_jour(self, silencieux: bool = False) -> None:
        """Vérifie s'il existe une version plus récente sur GitHub.

        silencieux=True pour la vérification automatique au démarrage :
        ne dit rien si tout est déjà à jour ou si la vérification a
        échoué (pas de réseau, GitHub inaccessible...), sur le même
        principe de dégradation silencieuse que la DLL NVDA absente.
        Depuis le menu Aide (silencieux=False), l'utilisateur a demandé
        explicitement, donc on répond dans tous les cas.
        """
        def travailler():
            resultat = _verifier_derniere_version()
            wx.CallAfter(self._resultat_verification_maj, resultat, silencieux)

        threading.Thread(target=travailler, daemon=True).start()

    def _resultat_verification_maj(
        self, resultat: tuple[str, str] | None, silencieux: bool,
    ) -> None:
        if resultat is None:
            if not silencieux:
                wx.MessageBox(
                    f"Vous avez déjà la dernière version ({VERSION}).",
                    "Mises à jour", wx.OK | wx.ICON_INFORMATION,
                )
                self.voix.dire("Vous avez déjà la dernière version.", interrompre=True)
            return

        version, url = resultat
        logging.info("Nouvelle version disponible : %s", version)
        self.voix.dire(f"Version {version} disponible.", interrompre=True)
        if wx.MessageBox(
            f"Une nouvelle version est disponible : {version} "
            f"(vous avez la {VERSION}).\n\nOuvrir la page de téléchargement ?",
            "Mise à jour disponible", wx.YES_NO | wx.ICON_INFORMATION,
        ) == wx.YES:
            import webbrowser
            webbrowser.open(url)

    def a_propos(self):
        if self.voix.muet:
            etat = "désactivée (mode muet)"
        else:
            canaux = []
            if self.voix._dll is not None:
                canaux.append("NVDA")
            if self.voix._jaws is not None:
                canaux.append("JAWS")
            etat = f"active ({', '.join(canaux)})" if canaux else "aucun lecteur d'écran détecté"
        wx.MessageBox(
            f"{APP_NOM}\nVersion {VERSION}\n\n"
            f"Auteur : {AUTEUR_NOM} ({AUTEUR_COURRIEL})\n"
            f"Dépôt GitHub : {URL_DEPOT}\n\n"
            f"Annonce vocale : {etat}\n"
            f"Journal : {self.chemin_journal}",
            "À propos", wx.OK | wx.ICON_INFORMATION,
        )

    # -- clavier global ----------------------------------------------------

    def sur_touche_globale(self, evt):
        # Garde-fou de principe : si une boîte de dialogue modale a la
        # main, ce gestionnaire ne doit toucher à rien (vérifié séparément
        # qu'EVT_CHAR_HOOK ne remonte de toute façon pas jusqu'ici tant
        # qu'une modale est ouverte — mais autant ne pas en dépendre).
        if wx.GetActiveWindow() is not self:
            evt.Skip()
            return

        code = evt.GetKeyCode()
        ctrl = evt.ControlDown()
        maj = evt.ShiftDown()
        alt = evt.AltDown()

        if code == wx.WXK_F6 and not ctrl and not alt:
            self.basculer_champ()
            return

        if code == wx.WXK_ESCAPE and not ctrl and not alt:
            panneau = self.session()
            if panneau is not None:
                cible = panneau.liste_fichiers if panneau.mode_navigation else panneau.saisie
                cible.SetFocus()
            return

        # Entrée/Retour arrière/Suppr/F2 du mode navigation : pas de
        # gestionnaire local sur liste_fichiers, voir le commentaire dans
        # PanneauSession.__init__ à côté de ses Bind(). Ne rien faire (pas
        # de return) quand ce n'est pas le mode navigation, pour laisser
        # ces touches à leur usage normal ailleurs (Entrée envoie la
        # commande, Retour arrière efface du texte...).
        if code in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and not ctrl and not alt and not maj:
            panneau = self.session()
            if panneau is not None and panneau.mode_navigation:
                panneau.activer_entree_sftp_selectionnee()
                return

        if code == wx.WXK_BACK and not ctrl and not alt and not maj:
            panneau = self.session()
            if panneau is not None and panneau.mode_navigation:
                panneau.remonter_sftp()
                return

        if code == wx.WXK_DELETE and not ctrl and not alt and not maj:
            panneau = self.session()
            if panneau is not None and panneau.mode_navigation:
                panneau.supprimer_entree_sftp()
                return

        if code == wx.WXK_F2 and not ctrl and not alt and not maj:
            panneau = self.session()
            if panneau is not None and panneau.mode_navigation:
                panneau.renommer_entree_sftp()
                return

        if ctrl and code in (wx.WXK_PAUSE, wx.WXK_CANCEL):
            panneau = self.session()
            if panneau is not None:
                panneau.interrompre()
            return

        # Second raccourci pour interrompre : la touche Pause est absente
        # ou remappée sur certains claviers, en particulier pour un
        # utilisateur qui ne s'en sert jamais et l'a réaffectée ailleurs.
        if ctrl and maj and code == ord("K"):
            panneau = self.session()
            if panneau is not None:
                panneau.interrompre()
            return

        if ctrl and maj and code == ord("D"):
            self.changer_repertoire()
            return

        if ctrl and maj and code == ord("U"):
            self.basculer_suivi()
            return

        # Comme dans un navigateur : Ctrl+= agrandit, Ctrl+- réduit.
        # Le contrôle du Maj n'exclut rien : sur un clavier où + exige
        # Maj, Ctrl+Maj+= doit fonctionner aussi bien que Ctrl+=.
        if ctrl and code in (ord("="), ord("+"), wx.WXK_NUMPAD_ADD):
            self.ajuster_taille_police(1)
            return

        if ctrl and code in (ord("-"), wx.WXK_NUMPAD_SUBTRACT):
            self.ajuster_taille_police(-1)
            return

        if ctrl and maj and code == ord("R"):
            panneau = self.session()
            if panneau is not None:
                panneau.repeter_saisie()
            return

        if ctrl and maj and code == ord("N"):
            self.creer_dossier_sftp()
            return

        if ctrl and maj and code == ord("H"):
            self.basculer_horodatage()
            return

        if ctrl and code == wx.WXK_TAB:
            self.changer_session(-1 if maj else 1)
            return

        if ctrl and not alt and not maj and ord("1") <= code <= ord("9"):
            self.aller_session(code - ord("1"))
            return

        if alt and not ctrl and code in (wx.WXK_UP, wx.WXK_DOWN):
            # Fonctionne depuis les deux champs : dans la saisie, les flèches
            # nues servent a l'historique, Alt les libère pour les blocs.
            self.naviguer_bloc(-1 if code == wx.WXK_UP else 1)
            return

        # Raccourcis restants, repris ici plutôt que laissés à la seule
        # table d'accélérateurs native de wx : celle-ci ne reconnaît que
        # les noms de touches anglais ("Shift"), incompatible avec les
        # libellés de menu en français ("Maj"). Regrouper la gestion ici
        # permet aux libellés d'afficher "Maj" sans casser le raccourci.
        if ctrl and not maj and code == ord("T"):
            self.nouvelle_session()
            return

        if ctrl and maj and code == ord("O"):
            self.nouvelle_session_ssh()
            return

        if ctrl and not maj and code == ord("W"):
            self.fermer_session()
            return

        if ctrl and maj and code == ord("F"):
            self.basculer_mode_navigation()
            return

        if ctrl and maj and code == ord("E"):
            self.envoyer_fichier_sftp()
            return

        if ctrl and maj and code == ord("T"):
            self.telecharger_entree_sftp()
            return

        if ctrl and maj and code == ord("G"):
            self.rechercher_fichiers_sftp()
            return

        if ctrl and maj and code == ord("A"):
            self.gerer_favoris_sftp()
            return

        if ctrl and maj and code == ord("J"):
            self.utiliser_commande_enregistree()
            return

        if ctrl and maj and code == ord("M"):
            self.enregistrer_commande_actuelle()
            return

        if ctrl and maj and code == ord("C"):
            self.copier_bloc(complet=True)
            return

        if ctrl and maj and code == ord("S"):
            self.copier_bloc(complet=False)
            return

        if ctrl and maj and code == ord("L"):
            self.copier_dernier_bloc()
            return

        if ctrl and not maj and code == ord("B"):
            self.lister_blocs()
            return

        if ctrl and maj and code == ord("V"):
            self.changer_verbosite()
            return

        evt.Skip()


# --------------------------------------------------------------------------
# Application
# --------------------------------------------------------------------------

class Application(wx.App):

    def __init__(self, voix: Voix, chemin_journal: Path):
        self.voix = voix
        self.chemin_journal = chemin_journal
        super().__init__(False)

    def OnInit(self):
        self.SetAppName(APP_NOM)
        fenetre = Fenetre(self.voix, self.chemin_journal)
        fenetre.Show()
        self.SetTopWindow(fenetre)
        return True


def main() -> int:
    analyseur = argparse.ArgumentParser(description=APP_NOM)
    analyseur.add_argument(
        "--muet", action="store_true",
        help="désactive toute annonce vocale (utile pendant le développement)",
    )
    args = analyseur.parse_args()

    chemin_journal = configurer_journal()
    voix = Voix(muet=args.muet)

    try:
        app = Application(voix, chemin_journal)
        app.MainLoop()
    except Exception:
        logging.exception("Arrêt sur erreur.")
        raise
    logging.info("Arrêt normal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
