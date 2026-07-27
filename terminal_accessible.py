#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Terminal accessible — palier 0

Coquille complète de l'interface : fenêtre, onglets de session, champ de
saisie, champ de sortie, modèle de blocs, navigation, copie et couche
vocale NVDA.

L'exécution réelle des commandes n'est PAS encore branchée : appuyer sur
Entrée créé un bloc factice. L'objectif de ce palier est de valider
l'ergonomie et l'accessibilité avant d'empiler quoi que ce soit dessus.

Les identifiants sont en français : une synthèse vocale française lit
correctement « traiter_sortie » et massacre « handle_output ».
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import re
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import wx

from execution import ExecuteurLocal, Resultat
from ssh import (
    ExecuteurSSH,
    ProfilConnexion,
    charger_profils,
    enregistrer_profils,
    enregistrer_secret,
    lire_secret,
    supprimer_secret,
)

APP_NOM = "Terminal accessible"
VERSION = "0.3 (palier 2)"

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


# --------------------------------------------------------------------------
# Chemins et journal
# --------------------------------------------------------------------------

def dossier_base() -> Path:
    """Dossier de travail : à côté de l'exe si compilé, du script sinon."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def configurer_journal() -> Path:
    chemin = dossier_base() / "terminal.log"
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
# Couche vocale : client contrôleur NVDA
# --------------------------------------------------------------------------

class Voix:
    """
    Parle via le client contrôleur de NVDA.

    La DLL n'est PAS fournie avec NVDA : il faut la télécharger séparément
    (voir LISEZMOI.md) et la déposer à côté de ce script, ou dans un
    sous-dossier « dll ». En son absence l'application fonctionne
    normalement, simplement sans annonce automatique.
    """

    NOMS_DLL = (
        "nvdaControllerClient64.dll",
        "nvdaControllerClient.dll",
        "nvdaControllerClient32.dll",
    )

    # Au delà, on n'envoie pas le texte a l'afficheur braille : un message
    # braille long chasse ce que l'utilisateur est en train de lire.
    LIMITE_BRAILLE = 120

    def __init__(self, muet: bool = False):
        self.muet = muet
        self._dll = None
        if muet:
            logging.info("Couche vocale désactivée (option --muet).")
            return
        self._charger()

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
            if any(part in self.IGNORER for part in chemin.parts):
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
            "annonce automatique. Voir LISEZMOI.md.", base,
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

    @property
    def disponible(self) -> bool:
        return self._dll is not None and not self.muet

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
        try:
            if texte:
                if interrompre:
                    self._dll.nvdaController_cancelSpeech()
                self._dll.nvdaController_speakText(texte)
            message = braille if braille is not None else texte
            if message and len(message) <= self.LIMITE_BRAILLE:
                self._dll.nvdaController_brailleMessage(message)
        except Exception:
            logging.exception("Échec de l'annonce.")

    def taire(self) -> None:
        if not self.disponible:
            return
        try:
            self._dll.nvdaController_cancelSpeech()
        except Exception:
            logging.exception("Échec de l'interruption de la parole.")


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
            morceaux.append(f"erreur {self.code_retour}")

        n = self.nb_lignes
        morceaux.append(decompte(n))
        if not self.interrompue and self.duree >= SEUIL_DUREE_ANNONCEE:
            morceaux.append(f"{self.duree:.0f} s")
        return ", ".join(morceaux)

    def rendu(self, avec_heure: bool = False) -> str:
        """Texte inséré dans le champ de sortie. Aucun caractère décoratif :
        une ligne de tirets est illisible en vocal comme en braille."""
        morceaux = [self.entete(avec_heure)]
        multiligne = "\n" in self.commande.strip()
        if multiligne or len(self.commande) > LONGUEUR_COMMANDE_ENTETE:
            morceaux.append(f"Commande complète :\n{self.commande}")
        if self.sortie:
            morceaux.append(self.sortie.rstrip("\n"))
        morceaux.append("")
        return "\n".join(morceaux) + "\n"

    def texte_complet(self) -> str:
        """Ce que Ctrl+Maj+C place dans le presse-papiers."""
        return f"{self.commande}\n{self.sortie}".rstrip() + "\n"

    def libelle_liste(self) -> str:
        heure = self.horodatage.strftime("%H:%M:%S")
        etat = "ok" if self.code_retour == 0 else f"erreur {self.code_retour}"
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
    if nb == 0:
        return "Terminé."

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


# --------------------------------------------------------------------------
# Réglages partages
# --------------------------------------------------------------------------

class Reglages:
    """État de configuration commun à la fenêtre et à toutes les sessions.

    Passer par un objet partagé évite que les panneaux aient besoin d'une
    référence remontante vers la fenêtre.
    """

    def __init__(self):
        self.verbosite = VERBOSITE_RESUME_PLUS
        self.sons = True
        self.afficher_horodatage = False
        self.listing_lisible = True
        self.suivre_sortie = False


# --------------------------------------------------------------------------
# Panneau d'une session
# --------------------------------------------------------------------------

MESSAGE_ACCUEIL = """\
Terminal accessible, palier 2.

Les commandes sont exécutées réellement, en local via PowerShell, ou à
distance par SSH (menu Session → Nouvelle session). Aucune fenêtre de
console n'apparaît.

Si une commande semble attendre une saisie (mot de passe, confirmation),
une boîte de dialogue accessible s'ouvre automatiquement pour y répondre.

Raccourcis :
  Entrée              envoyer la commande
  Maj+Entrée          saut de ligne dans la saisie (commande multiligne)
  Ctrl+Maj+K          interrompre la commande en cours
  Ctrl+Pause          idem, si cette touche existe sur ton clavier
  F6                  basculer entre saisie et sortie
  Échap               revenir au champ de saisie
  Flèche haut / bas   historique des commandes, quand le curseur est
                      sur la première ou la dernière ligne de la saisie
  Alt+Haut / Alt+Bas  bloc précédent / suivant
  Ctrl+Maj+C          copier le bloc courant
  Ctrl+Maj+S          copier la sortie seule du bloc courant
  Ctrl+Maj+L          copier le dernier bloc
  Ctrl+B              liste des blocs
  Ctrl+Maj+V          changer le niveau de verbosité vocale
  Ctrl+Maj+H          afficher ou masquer l'horodatage des blocs
  Ctrl+Maj+D          changer de répertoire courant
  Ctrl+Maj+N          listing amélioré (nom en tête de ligne)
  Ctrl+Maj+R          relire la saisie en cours
  Ctrl+Maj+U          aller automatiquement à la sortie après chaque commande
  Ctrl+T              nouvelle session
  Ctrl+Maj+O          nouvelle session SSH
  Ctrl+Tab            session suivante
  Ctrl+1 à Ctrl+9     aller directement à une session

Tout est également accessible depuis la barre de menus.
"""


class PanneauSession(wx.Panel):
    """Une session = un onglet = un champ de saisie, un champ de sortie,
    un historique et une liste de blocs qui lui sont propres."""

    def __init__(
        self, parent, nom: str, voix: Voix, reglages: Reglages,
        executeur=None, distant: bool = False,
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
        self.en_cours = False
        self.repertoire = "" if distant else str(Path.home())
        self._commande_en_cours = ""
        self._debut = 0.0
        self._invite_boite: wx.TextEntryDialog | None = None

        police = wx.Font(wx.FontInfo(11).Family(wx.FONTFAMILY_TELETYPE))

        etiquette_saisie = wx.StaticText(self, label=f"&Commande — {nom} :")
        # Multiligne pour accepter les commandes sur plusieurs lignes.
        # Entrée envoie, Maj+Entrée saute une ligne : on gère les deux
        # dans sur_touche_saisie plutôt que par TE_PROCESS_ENTER, dont le
        # comportement sur un contrôle multiligne varie selon les versions.
        self.saisie = wx.TextCtrl(self, style=wx.TE_MULTILINE)
        self.saisie.SetName(f"Commande, {nom}")
        self.saisie.SetFont(police)
        self.saisie.SetMinSize((-1, 64))

        etiquette_sortie = wx.StaticText(self, label=f"&Sortie — {nom} :")
        self.sortie = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 | wx.TE_DONTWRAP,
        )
        self.sortie.SetName(f"Sortie, {nom}")
        self.sortie.SetFont(police)
        self.sortie.SetValue(MESSAGE_ACCUEIL)
        self.sortie.SetInsertionPoint(0)

        boite = wx.BoxSizer(wx.VERTICAL)
        boite.Add(etiquette_saisie, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)
        boite.Add(self.saisie, 0, wx.EXPAND | wx.ALL, 6)
        boite.Add(etiquette_sortie, 0, wx.LEFT | wx.RIGHT, 6)
        boite.Add(self.sortie, 1, wx.EXPAND | wx.ALL, 6)
        self.SetSizer(boite)

        self.saisie.Bind(wx.EVT_KEY_DOWN, self.sur_touche_saisie)
        self.sortie.Bind(wx.EVT_CHAR, self.sur_frappe_dans_sortie)

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

    # -- exécution (factice a ce palier) -----------------------------------

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
                listing_lisible=self.reglages.listing_lisible,
            )
            wx.CallAfter(self._commande_terminee, commande, resultat)

        threading.Thread(target=travailler, daemon=True).start()

    def rafraichir_statut(self) -> None:
        """Reflète l'état de CETTE session dans la barre de statut et le
        titre de la fenêtre.

        La barre de statut reste affichée en continu, contrairement au
        bip et au braille fugace de _signaler_lenteur — mais rien ne la
        lit au retour d'un Alt+Tab. Le titre, lui, est ce que Windows et
        NVDA annoncent quand la fenêtre reprend le focus : c'est donc lui
        qui porte l'information « une commande tourne encore », pas de
        caractère décoratif, juste du texte.
        """
        fenetre = self.GetTopLevelParent()
        if fenetre.session() is not self:
            return
        if self.en_cours:
            resume = self._commande_en_cours.splitlines()[0].strip()
            if len(resume) > 60:
                resume = resume[:60].rstrip() + "..."
            fenetre.SetStatusText(f"Commande en cours : {resume}")
            fenetre.SetTitle(f"{self.nom} (commande en cours) — {APP_NOM}")
        else:
            fenetre.SetStatusText("Prêt")
            fenetre.SetTitle(f"{self.nom} — {APP_NOM}")

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
            statut = f"erreur {code_retour}"
        self.voix.dire(
            composer_annonce(bloc, self.reglages.verbosite),
            braille=f"Bloc {bloc.numero}, {statut}, {decompte(bloc.nb_lignes)}",
            interrompre=True,
        )
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

        boutons = self.CreateButtonSizer(wx.OK | wx.CANCEL)
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
        """Pose le focus sur le champ de saisie de la session courante."""
        if not self:                 # fenêtre détruite entre-temps
            return
        panneau = self.session()
        if panneau is None:
            return
        panneau.saisie.SetFocus()

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
                  m_nouvelle.Append(wx.ID_ANY, "Session &SSH…\tCtrl+Shift+O"))
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
                  m_session.Append(wx.ID_ANY, "&Interrompre la commande\tCtrl+Shift+K"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.changer_repertoire(),
                  m_session.Append(wx.ID_ANY, "Changer de &répertoire\tCtrl+Shift+D"))
        m_session.AppendSeparator()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.Close(),
                  m_session.Append(wx.ID_EXIT, "&Quitter\tAlt+F4"))
        barre.Append(m_session, "&Session")

        m_bloc = wx.Menu()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.naviguer_bloc(-1),
                  m_bloc.Append(wx.ID_ANY, "Bloc &précédent\tAlt+Up"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.naviguer_bloc(1),
                  m_bloc.Append(wx.ID_ANY, "Bloc &suivant\tAlt+Down"))
        m_bloc.AppendSeparator()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.copier_bloc(complet=True),
                  m_bloc.Append(wx.ID_ANY, "&Copier le bloc courant\tCtrl+Shift+C"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.copier_bloc(complet=False),
                  m_bloc.Append(wx.ID_ANY, "Copier la sortie &seule\tCtrl+Shift+S"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.copier_dernier_bloc(),
                  m_bloc.Append(wx.ID_ANY, "Copier le &dernier bloc\tCtrl+Shift+L"))
        m_bloc.AppendSeparator()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.lister_blocs(),
                  m_bloc.Append(wx.ID_ANY, "&Liste des blocs\tCtrl+B"))
        barre.Append(m_bloc, "&Blocs")

        m_affichage = wx.Menu()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.basculer_champ(),
                  m_affichage.Append(wx.ID_ANY, "&Basculer saisie / sortie\tF6"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.repeter_saisie(),
                  m_affichage.Append(wx.ID_ANY, "&Relire la saisie\tCtrl+Shift+R"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.changer_verbosite(),
                  m_affichage.Append(wx.ID_ANY, "Niveau de &verbosité\tCtrl+Shift+V"))
        self.item_suivre = m_affichage.Append(
            wx.ID_ANY, "Aller a&utomatiquement à la sortie\tCtrl+Shift+U",
            "Après chaque commande, place le curseur sur l'en-tête du bloc",
            wx.ITEM_CHECK,
        )
        self.item_suivre.Check(self.reglages.suivre_sortie)
        self.Bind(wx.EVT_MENU,
                  lambda e: self.basculer_suivi(),
                  self.item_suivre)
        self.item_listing = m_affichage.Append(
            wx.ID_ANY, "Listing a&mélioré\tCtrl+Shift+N",
            "Place le nom du fichier en tête de ligne dans dir et ls",
            wx.ITEM_CHECK,
        )
        self.item_listing.Check(self.reglages.listing_lisible)
        self.Bind(wx.EVT_MENU,
                  lambda e: self.basculer_listing(),
                  self.item_listing)
        self.item_horodatage = m_affichage.Append(
            wx.ID_ANY, "Afficher l'&horodatage des blocs\tCtrl+Shift+H",
            "Ajoute l'heure dans la ligne d'en-tête de chaque bloc",
            wx.ITEM_CHECK,
        )
        self.item_horodatage.Check(self.reglages.afficher_horodatage)
        self.Bind(wx.EVT_MENU,
                  lambda e: self.basculer_horodatage(),
                  self.item_horodatage)
        self.Bind(wx.EVT_MENU,
                  lambda e: self.effacer_sortie(),
                  m_affichage.Append(wx.ID_ANY, "&Effacer la sortie"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.vider_historique(),
                  m_affichage.Append(wx.ID_ANY, "Vider l'&historique des commandes"))
        barre.Append(m_affichage, "&Affichage")

        m_aide = wx.Menu()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.ouvrir_journal(),
                  m_aide.Append(wx.ID_ANY, "Ouvrir le &journal"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.a_propos(),
                  m_aide.Append(wx.ID_ABOUT, "&À propos"))
        barre.Append(m_aide, "&Aide")

        self.SetMenuBar(barre)

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

    def _connexion_ssh_reussie(self, profil: ProfilConnexion, executeur: ExecuteurSSH):
        panneau = PanneauSession(
            self.carnet, profil.nom, self.voix, self.reglages,
            executeur=executeur, distant=True,
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
                wx.CallAfter(setattr, panneau, "repertoire", chemin)

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
                wx.CallAfter(panneau.saisie.SetFocus)
        evt.Skip()

    # -- navigation et copie ----------------------------------------------

    def basculer_champ(self):
        panneau = self.session()
        if panneau is None:
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
                panneau.repertoire = boite.GetValue().strip()
        else:
            with wx.DirDialog(
                self, "Choisissez le répertoire de travail",
                defaultPath=panneau.repertoire,
                style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST,
            ) as boite:
                if boite.ShowModal() != wx.ID_OK:
                    return
                panneau.repertoire = boite.GetPath()
        self.SetStatusText(f"Répertoire : {panneau.repertoire}")
        self.voix.dire(f"Répertoire : {panneau.repertoire}", interrompre=True)
        logging.info("[%s] répertoire : %s", panneau.nom, panneau.repertoire)

    def repeter_saisie(self):
        panneau = self.session()
        if panneau is not None:
            panneau.repeter_saisie()

    def basculer_suivi(self):
        self.reglages.suivre_sortie = not self.reglages.suivre_sortie
        self.item_suivre.Check(self.reglages.suivre_sortie)
        etat = "activé" if self.reglages.suivre_sortie else "désactivé"
        self.SetStatusText(f"Suivi de la sortie {etat}")
        self.voix.dire(f"Suivi de la sortie {etat}.", interrompre=True)
        logging.info("Suivi de la sortie %s", etat)

    def basculer_listing(self):
        self.reglages.listing_lisible = not self.reglages.listing_lisible
        self.item_listing.Check(self.reglages.listing_lisible)
        etat = "activé" if self.reglages.listing_lisible else "désactivé"
        self.SetStatusText(f"Listing amélioré {etat}")
        self.voix.dire(f"Listing amélioré {etat}.", interrompre=True)
        logging.info("Listing amélioré %s", etat)

    def basculer_horodatage(self):
        self.reglages.afficher_horodatage = not self.reglages.afficher_horodatage
        self.item_horodatage.Check(self.reglages.afficher_horodatage)
        for index in range(self.carnet.GetPageCount()):
            self.carnet.GetPage(index).redessiner()
        etat = "affiche" if self.reglages.afficher_horodatage else "masque"
        self.SetStatusText(f"Horodatage {etat}")
        self.voix.dire(f"Horodatage {etat}.", interrompre=True)
        logging.info("Horodatage %s", etat)

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

    def a_propos(self):
        etat = (
            "opérationnelle" if self.voix.disponible
            else "indisponible (client contrôleur NVDA absent ou mode muet)"
        )
        wx.MessageBox(
            f"{APP_NOM}\nVersion {VERSION}\n\n"
            f"Annonce vocale : {etat}\n"
            f"Journal : {self.chemin_journal}",
            "À propos", wx.OK | wx.ICON_INFORMATION,
        )

    # -- clavier global ----------------------------------------------------

    def sur_touche_globale(self, evt):
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
                panneau.saisie.SetFocus()
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

        if ctrl and maj and code == ord("R"):
            panneau = self.session()
            if panneau is not None:
                panneau.repeter_saisie()
            return

        if ctrl and maj and code == ord("N"):
            self.basculer_listing()
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
