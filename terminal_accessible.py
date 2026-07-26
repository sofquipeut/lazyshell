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
import random
import sys
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import wx

APP_NOM = "Terminal accessible"
VERSION = "0.1 (palier 0)"

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
LONGUEUR_COMMANDE_ENTETE = 60  # au-delà, la commande est tronquée
                               # dans l'en-tête et rappelée en entier


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

        if self.code_retour != 0:
            morceaux.append(f"erreur {self.code_retour}")

        n = self.nb_lignes
        morceaux.append("1 ligne" if n == 1 else f"{n} lignes")
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


def composer_annonce(bloc: Bloc, verbosite: int) -> str:
    """Applique les règles adaptatives décidées avec l'utilisateur.

    Le statut passe TOUJOURS en premier : on peut ainsi couper la parole
    des qu'on sait que la commande a réussi, sans subir toute la sortie.
    """
    lignes = bloc.sortie.splitlines()
    nb = len(lignes)

    if bloc.code_retour != 0:
        tete = f"Erreur, code {bloc.code_retour}, {nb} lignes."
        if not lignes:
            return tete
        extrait = lignes[:LIGNES_ERREUR]
        suite = "" if nb <= LIGNES_ERREUR else f" Et {nb - LIGNES_ERREUR} lignes de plus."
        return tete + " " + " ".join(extrait) + suite

    if nb == 0:
        return "Terminé."

    if nb <= SEUIL_LECTURE_INTEGRALE or verbosite == VERBOSITE_TOUT:
        return f"Terminé, {nb} lignes. " + " ".join(lignes)

    if verbosite == VERBOSITE_RESUME:
        return f"Terminé, {nb} lignes."

    extrait = lignes[:LIGNES_APERCU]
    return (
        f"Terminé, {nb} lignes. "
        + " ".join(extrait)
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


# --------------------------------------------------------------------------
# Panneau d'une session
# --------------------------------------------------------------------------

MESSAGE_ACCUEIL = """\
Terminal accessible, palier 0.

L'exécution des commandes n'est pas encore branchée : taper une commande
et valider par Entrée créé un bloc de démonstration. Le but est de valider
la navigation, la copie et l'annonce vocale.

Raccourcis :
  Entrée              envoyer la commande
  Maj+Entrée          saut de ligne dans la saisie (commande multiligne)
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
  Ctrl+Maj+T          tester l'annonce vocale
  Ctrl+T              nouvelle session
  Ctrl+Tab            session suivante
  Ctrl+1 a Ctrl+9     aller directement a une session

Tout est également accessible depuis la barre de menus.
"""


class PanneauSession(wx.Panel):
    """Une session = un onglet = un champ de saisie, un champ de sortie,
    un historique et une liste de blocs qui lui sont propres."""

    def __init__(self, parent, nom: str, voix: Voix, reglages: Reglages):
        super().__init__(parent)
        self.nom = nom
        self.voix = voix
        self.reglages = reglages
        self.blocs: list[Bloc] = []
        self.historique: list[str] = []
        self.index_historique = 0

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
        if not self.historique:
            return
        self.index_historique = max(
            0, min(len(self.historique), self.index_historique + delta)
        )
        if self.index_historique == len(self.historique):
            self.saisie.SetValue("")
        else:
            self.saisie.SetValue(self.historique[self.index_historique])
        self.saisie.SetInsertionPointEnd()

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
        """Sera remplacé au palier 1 par l'exécution réelle. La signature et
        le point d'arrivée (ajouter_bloc) ne changeront pas."""
        logging.info("[%s] commande simulée : %s", self.nom, commande)

        if commande in ("erreur", "échec", "fail"):
            sortie = (
                "bash: commande introuvable\n"
                "Vérifiez l'orthographe ou le chemin d'accès."
            )
            code = 127
        elif commande in ("long", "beaucoup"):
            sortie = "\n".join(
                f"ligne {i} de sortie simulée, valeur {random.randint(100, 999)}"
                for i in range(1, 41)
            )
            code = 0
        elif commande in ("vide", "rien"):
            sortie = ""
            code = 0
        else:
            sortie = (
                f"Sortie simulée pour : {commande}\n"
                "L'exécution réelle arrive au palier 1.\n"
                "Essayez aussi les mots : erreur, long, vide."
            )
            code = 0

        self.ajouter_bloc(commande, sortie, code)

    # -- blocs -------------------------------------------------------------

    def ajouter_bloc(self, commande: str, sortie: str, code_retour: int) -> Bloc:
        bloc = Bloc(
            numero=len(self.blocs) + 1,
            commande=commande,
            sortie=sortie,
            code_retour=code_retour,
            session=self.nom,
        )
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
        statut = "ok" if code_retour == 0 else f"erreur {code_retour}"
        self.voix.dire(
            composer_annonce(bloc, self.reglages.verbosite),
            braille=f"Bloc {bloc.numero}, {statut}, {bloc.nb_lignes} lignes",
            interrompre=True,
        )
        return bloc

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

        self.nouvelle_session("Local")
        self.Centre()

    # -- construction ------------------------------------------------------

    def _construire_menus(self):
        barre = wx.MenuBar()

        m_session = wx.Menu()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.nouvelle_session(),
                  m_session.Append(wx.ID_ANY, "&Nouvelle session\tCtrl+T"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.fermer_session(),
                  m_session.Append(wx.ID_ANY, "&Fermer la session\tCtrl+W"))
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
                  lambda e: self.changer_verbosite(),
                  m_affichage.Append(wx.ID_ANY, "Niveau de &verbosité\tCtrl+Shift+V"))
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
        barre.Append(m_affichage, "&Affichage")

        m_aide = wx.Menu()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.tester_voix(),
                  m_aide.Append(wx.ID_ANY, "&Tester l'annonce vocale\tCtrl+Shift+T"))
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
            self.carnet.DeletePage(index)
            logging.info("Session fermée : %s", nom)

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
            self.SetTitle(f"{panneau.nom} — {APP_NOM}")
            # NVDA annonce l'onglet puis le champ, dont le nom accessible
            # contient déjà la session. On se contente du braille.
            self.voix.dire(braille=f"Session {panneau.nom}")

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
            self.voix.dire(f"{quoi} {bloc.numero} copié, {bloc.nb_lignes} lignes.",
                           interrompre=True)

    def copier_dernier_bloc(self):
        panneau = self.session()
        if panneau is None or not panneau.blocs:
            self.voix.dire("Aucun bloc à copier.")
            return
        bloc = panneau.blocs[-1]
        if copier_presse_papiers(bloc.texte_complet()):
            self.voix.dire(f"Dernier bloc copié, {bloc.nb_lignes} lignes.",
                           interrompre=True)

    def lister_blocs(self):
        panneau = self.session()
        if panneau is None or not panneau.blocs:
            self.voix.dire("Aucun bloc.")
            return
        choix = [b.libelle_liste() for b in panneau.blocs]
        with wx.SingleChoiceDialog(
            self, "Choisissez un bloc :", "Liste des blocs", choix
        ) as boite:
            boite.SetSelection(len(choix) - 1)
            if boite.ShowModal() == wx.ID_OK:
                panneau.aller_au_bloc(panneau.blocs[boite.GetSelection()])

    def changer_verbosite(self):
        self.reglages.verbosite = (self.reglages.verbosite + 1) % 3
        nom = NOMS_VERBOSITE[self.reglages.verbosite]
        self.SetStatusText(f"Verbosité : {nom}")
        self.voix.dire(f"Verbosité : {nom}.", interrompre=True)
        logging.info("Verbosité changée : %s", nom)

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

    # -- divers ------------------------------------------------------------

    def tester_voix(self):
        """Vérifie la chaîne d'annonce sans passer par une commande."""
        if self.voix.muet:
            wx.MessageBox(
                "L'application tourne en mode muet (option --muet).\n"
                "Relancez-la avec lancer.bat pour activer l'annonce.",
                "Test de l'annonce", wx.OK | wx.ICON_INFORMATION,
            )
            return
        if not self.voix.disponible:
            wx.MessageBox(
                "Le client contrôleur NVDA n'a pas été chargé.\n\n"
                "Aucun fichier nvdaControllerClient*.dll n'a été trouvé "
                "sous le dossier du projet.\n\n"
                f"Le journal en dit plus :\n{self.chemin_journal}",
                "Test de l'annonce", wx.OK | wx.ICON_WARNING,
            )
            return
        self.voix.dire(
            "Test réussi. Si vous entendez ce message, l'annonce "
            "automatique fonctionne."
        )
        self.SetStatusText("Message de test envoyé à NVDA")

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

        if ctrl and maj and code == ord("H"):
            self.basculer_horodatage()
            return

        if ctrl and maj and code == ord("T"):
            self.tester_voix()
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
