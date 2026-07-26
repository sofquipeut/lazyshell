#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Terminal accessible — palier 0

Coquille complète de l'interface : fenetre, onglets de session, champ de
saisie, champ de sortie, modele de blocs, navigation, copie et couche
vocale NVDA.

L'execution reelle des commandes n'est PAS encore branchee : appuyer sur
Entree cree un bloc factice. L'objectif de ce palier est de valider
l'ergonomie et l'accessibilite avant d'empiler quoi que ce soit dessus.

Les identifiants sont en francais : une synthese vocale francaise lit
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

# Niveaux de verbosite de l'annonce vocale
VERBOSITE_RESUME = 0
VERBOSITE_RESUME_PLUS = 1
VERBOSITE_TOUT = 2

NOMS_VERBOSITE = {
    VERBOSITE_RESUME: "resume seul",
    VERBOSITE_RESUME_PLUS: "resume et 5 premieres lignes",
    VERBOSITE_TOUT: "sortie complete",
}

# Seuils des regles adaptatives
SEUIL_LECTURE_INTEGRALE = 10   # en dessous, on lit tout quoi qu'il arrive
LIGNES_APERCU = 5              # niveau 1
LIGNES_ERREUR = 15             # sur code de retour non nul


# --------------------------------------------------------------------------
# Chemins et journal
# --------------------------------------------------------------------------

def dossier_base() -> Path:
    """Dossier de travail : a cote de l'exe si compile, du script sinon."""
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
    logging.info("Demarrage de %s version %s", APP_NOM, VERSION)
    logging.info("Python %s", sys.version.replace("\n", " "))
    logging.info("Executable : %s", sys.executable)
    logging.info("Dossier de base : %s", dossier_base())

    def hook(type_exc, valeur, trace):
        logging.critical(
            "Exception non rattrapee",
            exc_info=(type_exc, valeur, trace),
        )
        texte = "".join(traceback.format_exception(type_exc, valeur, trace))
        try:
            wx.MessageBox(
                "Une erreur inattendue s'est produite.\n\n"
                f"{valeur}\n\n"
                f"Le detail complet est dans :\n{chemin}",
                "Erreur",
                wx.OK | wx.ICON_ERROR,
            )
        except Exception:
            sys.stderr.write(texte)

    sys.excepthook = hook
    return chemin


# --------------------------------------------------------------------------
# Couche vocale : client controleur NVDA
# --------------------------------------------------------------------------

class Voix:
    """
    Parle via le client controleur de NVDA.

    La DLL n'est PAS fournie avec NVDA : il faut la telecharger separement
    (voir LISEZMOI.md) et la deposer a cote de ce script, ou dans un
    sous-dossier « dll ». En son absence l'application fonctionne
    normalement, simplement sans annonce automatique.
    """

    NOMS_DLL = (
        "nvdaControllerClient64.dll",
        "nvdaControllerClient.dll",
        "nvdaControllerClient32.dll",
    )

    # Au dela, on n'envoie pas le texte a l'afficheur braille : un message
    # braille long chasse ce que l'utilisateur est en train de lire.
    LIMITE_BRAILLE = 120

    def __init__(self, muet: bool = False):
        self.muet = muet
        self._dll = None
        if muet:
            logging.info("Couche vocale desactivee (option --muet).")
            return
        self._charger()

    def _charger(self) -> None:
        base = dossier_base()
        candidats = []
        for nom in self.NOMS_DLL:
            candidats.append(base / nom)
            candidats.append(base / "dll" / nom)

        for chemin in candidats:
            if not chemin.is_file():
                continue
            try:
                dll = ctypes.windll.LoadLibrary(str(chemin))
            except OSError:
                logging.exception("Echec du chargement de %s", chemin)
                continue

            dll.nvdaController_speakText.argtypes = [ctypes.c_wchar_p]
            dll.nvdaController_brailleMessage.argtypes = [ctypes.c_wchar_p]
            self._dll = dll
            logging.info("Client controleur NVDA charge : %s", chemin)
            self._diagnostiquer()
            return

        logging.warning(
            "Aucun client controleur NVDA trouve. Cherche dans %s sous les "
            "noms %s. L'annonce automatique est desactivee.",
            base, ", ".join(self.NOMS_DLL),
        )

    def _diagnostiquer(self) -> None:
        try:
            code = self._dll.nvdaController_testIfRunning()
        except Exception:
            logging.exception("Appel de testIfRunning impossible.")
            return
        if code == 0:
            logging.info("NVDA repond : annonce automatique operationnelle.")
        else:
            logging.warning(
                "NVDA ne repond pas (code %s). La DLL est chargee mais NVDA "
                "n'est probablement pas lance.", code,
            )

    @property
    def disponible(self) -> bool:
        return self._dll is not None and not self.muet

    def dire(self, texte: str, interrompre: bool = True) -> None:
        if not texte or not self.disponible:
            return
        try:
            if interrompre:
                self._dll.nvdaController_cancelSpeech()
            self._dll.nvdaController_speakText(texte)
            if len(texte) <= self.LIMITE_BRAILLE:
                self._dll.nvdaController_brailleMessage(texte)
        except Exception:
            logging.exception("Echec de l'annonce vocale.")

    def taire(self) -> None:
        if not self.disponible:
            return
        try:
            self._dll.nvdaController_cancelSpeech()
        except Exception:
            logging.exception("Echec de l'interruption de la parole.")


def bip(succes: bool) -> None:
    """Signal sonore court, joue dans un thread pour ne pas figer l'interface."""
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
# Modele de blocs
# --------------------------------------------------------------------------

@dataclass
class Bloc:
    numero: int
    commande: str
    sortie: str
    code_retour: int
    session: str
    horodatage: datetime = field(default_factory=datetime.now)
    debut: int = 0   # position de depart dans le champ de sortie
    fin: int = 0

    @property
    def nb_lignes(self) -> int:
        return len(self.sortie.splitlines()) if self.sortie else 0

    def entete(self) -> str:
        heure = self.horodatage.strftime("%H:%M:%S")
        return (
            f"Bloc {self.numero}, {heure}, {self.session}, "
            f"code {self.code_retour}"
        )

    def rendu(self) -> str:
        """Texte insere dans le champ de sortie. Aucun caractere decoratif :
        une ligne de tirets est illisible en vocal comme en braille."""
        morceaux = [self.entete(), f"Commande : {self.commande}"]
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
        return (
            f"{self.numero}. {heure} — {self.commande} — "
            f"{self.nb_lignes} lignes — {etat}"
        )


def composer_annonce(bloc: Bloc, verbosite: int) -> str:
    """Applique les regles adaptatives decidees avec l'utilisateur.

    Le statut passe TOUJOURS en premier : on peut ainsi couper la parole
    des qu'on sait que la commande a reussi, sans subir toute la sortie.
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
        return "Termine."

    if nb <= SEUIL_LECTURE_INTEGRALE or verbosite == VERBOSITE_TOUT:
        return f"Termine, {nb} lignes. " + " ".join(lignes)

    if verbosite == VERBOSITE_RESUME:
        return f"Termine, {nb} lignes."

    extrait = lignes[:LIGNES_APERCU]
    return (
        f"Termine, {nb} lignes. "
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
# Panneau d'une session
# --------------------------------------------------------------------------

MESSAGE_ACCUEIL = """\
Terminal accessible, palier 0.

L'execution des commandes n'est pas encore branchee : taper une commande
et valider par Entree cree un bloc de demonstration. Le but est de valider
la navigation, la copie et l'annonce vocale.

Raccourcis :
  Entree              envoyer la commande
  F6                  basculer entre saisie et sortie
  Echap               revenir au champ de saisie
  Fleche haut / bas   historique des commandes (dans le champ de saisie)
  Alt+Haut / Alt+Bas  bloc precedent / suivant
  Ctrl+Maj+C          copier le bloc courant
  Ctrl+Maj+S          copier la sortie seule du bloc courant
  Ctrl+Maj+L          copier le dernier bloc
  Ctrl+B              liste des blocs
  Ctrl+Maj+V          changer le niveau de verbosite vocale
  Ctrl+T              nouvelle session
  Ctrl+Tab            session suivante
  Ctrl+1 a Ctrl+9     aller directement a une session

Tout est egalement accessible depuis la barre de menus.
"""


class PanneauSession(wx.Panel):
    """Une session = un onglet = un champ de saisie, un champ de sortie,
    un historique et une liste de blocs qui lui sont propres."""

    def __init__(self, parent, nom: str, voix: Voix):
        super().__init__(parent)
        self.nom = nom
        self.voix = voix
        self.blocs: list[Bloc] = []
        self.historique: list[str] = []
        self.index_historique = 0

        police = wx.Font(wx.FontInfo(11).Family(wx.FONTFAMILY_TELETYPE))

        etiquette_saisie = wx.StaticText(self, label=f"&Commande — {nom} :")
        self.saisie = wx.TextCtrl(self, style=wx.TE_PROCESS_ENTER)
        self.saisie.SetName(f"Commande, {nom}")
        self.saisie.SetFont(police)

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

        self.saisie.Bind(wx.EVT_TEXT_ENTER, self.sur_entree)
        self.saisie.Bind(wx.EVT_KEY_DOWN, self.sur_touche_saisie)

    # -- saisie ------------------------------------------------------------

    def sur_entree(self, evt):
        commande = self.saisie.GetValue().strip()
        if not commande:
            return
        self.saisie.SetValue("")
        self.historique.append(commande)
        self.index_historique = len(self.historique)
        self.executer(commande)

    def sur_touche_saisie(self, evt):
        code = evt.GetKeyCode()
        if code == wx.WXK_UP and self.historique:
            self.index_historique = max(0, self.index_historique - 1)
            self.saisie.SetValue(self.historique[self.index_historique])
            self.saisie.SetInsertionPointEnd()
            return
        if code == wx.WXK_DOWN and self.historique:
            self.index_historique = min(
                len(self.historique), self.index_historique + 1
            )
            if self.index_historique == len(self.historique):
                self.saisie.SetValue("")
            else:
                self.saisie.SetValue(self.historique[self.index_historique])
            self.saisie.SetInsertionPointEnd()
            return
        evt.Skip()

    # -- execution (factice a ce palier) -----------------------------------

    def executer(self, commande: str) -> None:
        """Sera remplace au palier 1 par l'execution reelle. La signature et
        le point d'arrivee (ajouter_bloc) ne changeront pas."""
        logging.info("[%s] commande simulee : %s", self.nom, commande)

        if commande in ("erreur", "echec", "fail"):
            sortie = (
                "bash: commande introuvable\n"
                "Verifiez l'orthographe ou le chemin d'acces."
            )
            code = 127
        elif commande in ("long", "beaucoup"):
            sortie = "\n".join(
                f"ligne {i} de sortie simulee, valeur {random.randint(100, 999)}"
                for i in range(1, 41)
            )
            code = 0
        elif commande in ("vide", "rien"):
            sortie = ""
            code = 0
        else:
            sortie = (
                f"Sortie simulee pour : {commande}\n"
                "L'execution reelle arrive au palier 1.\n"
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
        self.sortie.AppendText("\n" + bloc.rendu())
        bloc.fin = self.sortie.GetLastPosition()
        self.blocs.append(bloc)

        # Le point d'insertion se place au debut du nouveau bloc : quand on
        # bascule avec F6, on arrive directement sur le contenu frais.
        self.sortie.SetInsertionPoint(bloc.debut + 1)
        self.sortie.ShowPosition(bloc.debut + 1)

        bip(code_retour == 0)
        return bloc

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
        self.voix.dire(
            f"{bloc.entete()}. {bloc.commande}. {bloc.nb_lignes} lignes."
        )


# --------------------------------------------------------------------------
# Fenetre principale
# --------------------------------------------------------------------------

class Fenetre(wx.Frame):

    def __init__(self, voix: Voix, chemin_journal: Path):
        super().__init__(None, title=APP_NOM, size=(960, 640))
        self.voix = voix
        self.chemin_journal = chemin_journal
        self.verbosite = VERBOSITE_RESUME_PLUS
        self.compteur_sessions = 0

        self.carnet = wx.Notebook(self)
        self.carnet.SetName("Sessions")

        self._construire_menus()
        self.CreateStatusBar()
        self.SetStatusText("Pret")

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
                  m_bloc.Append(wx.ID_ANY, "Bloc &precedent\tAlt+Up"))
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
                  m_affichage.Append(wx.ID_ANY, "Niveau de &verbosite\tCtrl+Shift+V"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.effacer_sortie(),
                  m_affichage.Append(wx.ID_ANY, "&Effacer la sortie"))
        barre.Append(m_affichage, "&Affichage")

        m_aide = wx.Menu()
        self.Bind(wx.EVT_MENU,
                  lambda e: self.ouvrir_journal(),
                  m_aide.Append(wx.ID_ANY, "Ouvrir le &journal"))
        self.Bind(wx.EVT_MENU,
                  lambda e: self.a_propos(),
                  m_aide.Append(wx.ID_ABOUT, "&A propos"))
        barre.Append(m_aide, "&Aide")

        self.SetMenuBar(barre)

    # -- sessions ----------------------------------------------------------

    def nouvelle_session(self, nom: str | None = None):
        if nom is None:
            self.compteur_sessions += 1
            nom = f"Session {self.compteur_sessions + 1}"
        panneau = PanneauSession(self.carnet, nom, self.voix)
        self.carnet.AddPage(panneau, nom, select=True)
        panneau.saisie.SetFocus()
        logging.info("Session creee : %s", nom)
        self.voix.dire(f"Session {nom} ouverte.")

    def fermer_session(self):
        if self.carnet.GetPageCount() <= 1:
            self.voix.dire("Impossible de fermer la derniere session.")
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
            logging.info("Session fermee : %s", nom)

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
            self.voix.dire(f"Session {panneau.nom}.")
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
            self.voix.dire("Aucun bloc a copier.")
            return
        texte = bloc.texte_complet() if complet else bloc.sortie
        if copier_presse_papiers(texte):
            quoi = "Bloc" if complet else "Sortie"
            self.voix.dire(f"{quoi} {bloc.numero} copie, {bloc.nb_lignes} lignes.")

    def copier_dernier_bloc(self):
        panneau = self.session()
        if panneau is None or not panneau.blocs:
            self.voix.dire("Aucun bloc a copier.")
            return
        bloc = panneau.blocs[-1]
        if copier_presse_papiers(bloc.texte_complet()):
            self.voix.dire(f"Dernier bloc copie, {bloc.nb_lignes} lignes.")

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
        self.verbosite = (self.verbosite + 1) % 3
        nom = NOMS_VERBOSITE[self.verbosite]
        self.SetStatusText(f"Verbosite : {nom}")
        self.voix.dire(f"Verbosite : {nom}.")
        logging.info("Verbosite changee : %s", nom)

    def effacer_sortie(self):
        panneau = self.session()
        if panneau is None:
            return
        panneau.sortie.SetValue("")
        panneau.blocs.clear()
        self.voix.dire("Sortie effacee.")

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
            "operationnelle" if self.voix.disponible
            else "indisponible (client controleur NVDA absent ou mode muet)"
        )
        wx.MessageBox(
            f"{APP_NOM}\nVersion {VERSION}\n\n"
            f"Annonce vocale : {etat}\n"
            f"Journal : {self.chemin_journal}",
            "A propos", wx.OK | wx.ICON_INFORMATION,
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

        if ctrl and code == wx.WXK_TAB:
            self.changer_session(-1 if maj else 1)
            return

        if ctrl and not alt and not maj and ord("1") <= code <= ord("9"):
            self.aller_session(code - ord("1"))
            return

        if alt and not ctrl and code in (wx.WXK_UP, wx.WXK_DOWN):
            # Fonctionne depuis les deux champs : dans la saisie, les fleches
            # nues servent a l'historique, Alt les libere pour les blocs.
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
        help="desactive toute annonce vocale (utile pendant le developpement)",
    )
    args = analyseur.parse_args()

    chemin_journal = configurer_journal()
    voix = Voix(muet=args.muet)

    try:
        app = Application(voix, chemin_journal)
        app.MainLoop()
    except Exception:
        logging.exception("Arret sur erreur.")
        raise
    logging.info("Arret normal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
