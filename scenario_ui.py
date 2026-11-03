# -*- coding: utf-8 -*-
"""
Scénarios, côté interface : choix du scénario, enchaînement des étapes,
confirmations et boîte de problème.

Le moteur (chargement, contrôle, journal) est dans scenario.py. Ce
module n'importe pas lazyshell (qui tourne comme __main__ : l'importer en
chargerait une seconde copie) : ce dont il a besoin lui est passé par
lancer_scenario.

L'enchaînement tourne dans un thread de fond, écrit comme une simple
boucle : chaque fois qu'il faut toucher à l'interface (bloc, annonce,
boîte de dialogue), il passe la main au thread principal par
wx.CallAfter et attend la réponse (_sur_interface), sur le modèle de
PanneauSession._repondre_invite.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import wx

from scenario import (
    ALTERNATIVE, ARRETER, CONTINUER, FIN, REESSAYER, SUIVANTE,
    ErreurScenario, JournalScenario, Scenario, charger_scenario,
    lister_scenarios, rapport, substituer, variables_initiales, verifier,
)

# Garde-fou contre un scénario qui tournerait en rond (si_bon qui renvoie
# vers une étape déjà passée, réessais sans fin...).
MAX_ETAPES_EXECUTEES = 500

BON = "Bon"
PROBLEME = "Problème"


def dossier_scenarios(base: Path) -> Path:
    return base / "scenarios"


# --------------------------------------------------------------------------
# Boîtes de dialogue
# --------------------------------------------------------------------------

class DialogueLancementScenario(wx.Dialog):
    """Choix du scénario et du mode (réel ou simulation)."""

    def __init__(self, parent, dossier: Path):
        super().__init__(
            parent, title="Lancer un scénario",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER,
        )
        self.dossier = dossier
        self.scenario: Scenario | None = None
        # Chaque fichier est chargé dès l'ouverture : une erreur de saisie
        # dans un fichier se voit dans la liste, pas au milieu d'une
        # procédure.
        self._fichiers: list[tuple[Path, Scenario | None, str]] = []
        for chemin in lister_scenarios(dossier):
            try:
                self._fichiers.append((chemin, charger_scenario(chemin), ""))
            except ErreurScenario as erreur:
                self._fichiers.append((chemin, None, str(erreur)))

        etiquette = wx.StaticText(self, label="&Scénarios :")
        self.liste = wx.ListBox(self)
        self.liste.SetName("Scénarios")
        for chemin, scenario, erreur in self._fichiers:
            if scenario is None:
                self.liste.Append(f"{chemin.name}, fichier en erreur")
            else:
                etapes = len(scenario.etapes_en_sequence())
                self.liste.Append(f"{scenario.nom}, {etapes} étapes, {chemin.name}")
        if self._fichiers:
            self.liste.SetSelection(0)

        self.case_simulation = wx.CheckBox(
            self, label="Mode &simulation : afficher les commandes sans les exécuter",
        )
        self.case_commandes = wx.CheckBox(
            self, label="&Lire la commande avant chaque étape",
        )
        self.case_commandes.SetValue(True)
        # Coché par défaut : sans pause, des étapes rapides s'enchaînent
        # plus vite que la synthèse ne peut les dire.
        self.case_pas_a_pas = wx.CheckBox(
            self, label="&Pas à pas : attendre ma validation après chaque étape",
        )
        self.case_pas_a_pas.SetValue(True)

        bouton_lancer = wx.Button(self, id=wx.ID_OK, label="&Lancer")
        bouton_dossier = wx.Button(self, label="&Ouvrir le dossier des scénarios")
        bouton_annuler = wx.Button(self, id=wx.ID_CANCEL, label="Ann&uler")
        bouton_lancer.SetDefault()
        self.Bind(wx.EVT_BUTTON, self._sur_lancer, id=wx.ID_OK)
        bouton_dossier.Bind(wx.EVT_BUTTON, lambda e: ouvrir_dossier(self.dossier))
        self.liste.Bind(wx.EVT_LISTBOX_DCLICK, self._sur_lancer)

        boutons = wx.BoxSizer(wx.HORIZONTAL)
        for bouton in (bouton_lancer, bouton_dossier, bouton_annuler):
            boutons.Add(bouton, 0, wx.RIGHT, 6)

        boite = wx.BoxSizer(wx.VERTICAL)
        boite.Add(etiquette, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        boite.Add(self.liste, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)
        boite.Add(self.case_simulation, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        boite.Add(self.case_commandes, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        boite.Add(self.case_pas_a_pas, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        boite.Add(boutons, 0, wx.ALIGN_RIGHT | wx.ALL, 10)
        self.SetSizerAndFit(boite)
        self.SetSize((560, 370))
        # Après l'affichage réel : posé dans __init__, le focus ne tient
        # pas toujours (même précaution que DialogueRechercheFichiers).
        cible = self.liste if self._fichiers else bouton_dossier
        wx.CallAfter(cible.SetFocus)

    def _sur_lancer(self, evt):
        index = self.liste.GetSelection()
        if index == wx.NOT_FOUND:
            wx.MessageBox(
                f"Aucun scénario trouvé. Déposez un fichier .toml dans :\n{self.dossier}",
                "Aucun scénario", wx.OK | wx.ICON_INFORMATION, self,
            )
            return
        chemin, scenario, erreur = self._fichiers[index]
        if scenario is None:
            wx.MessageBox(
                f"{chemin.name} :\n{erreur}", "Scénario en erreur",
                wx.OK | wx.ICON_WARNING, self,
            )
            self.liste.SetFocus()
            return
        self.scenario = scenario
        self.EndModal(wx.ID_OK)


class DialogueVerdict(wx.Dialog):
    """Verdict d'une étape et choix de la suite : après un problème, ou
    après une étape bonne en mode pas à pas. Une vraie boîte de dialogue
    plutôt qu'une annonce vocale : NVDA la lit lui-même à l'ouverture, et
    le scénario attend qu'on l'ait entendue. Copier le rapport (seulement
    si texte_rapport est fourni) ne ferme pas la boîte."""

    def __init__(
        self, parent, titre: str, message: str, sortie: str,
        choix: list[tuple[str, str]], texte_rapport: str | None, copier, voix,
        etiquette_sortie: str = "Sortie de la &commande :",
    ):
        super().__init__(parent, title=titre, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.choix: str | tuple = ARRETER
        self._texte_rapport = texte_rapport
        self._copier = copier
        self._voix = voix

        texte_message = wx.StaticText(self, label=message)
        texte_message.Wrap(520)

        # Créés avant le champ de sortie : l'ordre de tabulation suit
        # l'ordre de création, la première option est à une touche.
        boutons = wx.BoxSizer(wx.HORIZONTAL)
        premier = None
        for valeur, libelle in choix:
            identifiant = wx.ID_CANCEL if valeur == ARRETER else wx.ID_ANY
            bouton = wx.Button(self, id=identifiant, label=libelle)
            bouton.Bind(wx.EVT_BUTTON, lambda e, v=valeur: self._choisir(v))
            boutons.Add(bouton, 0, wx.RIGHT, 6)
            if premier is None:
                premier = bouton
                bouton.SetDefault()
        if texte_rapport is not None:
            bouton_rapport = wx.Button(self, label="Copier le ra&pport")
            bouton_rapport.Bind(wx.EVT_BUTTON, self._sur_copier)
            boutons.Add(bouton_rapport, 0, wx.RIGHT, 6)

        texte_etiquette = wx.StaticText(self, label=etiquette_sortie)
        self.champ_sortie = wx.TextCtrl(
            self, value=sortie or "(aucune sortie)",
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
        )
        self.champ_sortie.SetName(etiquette_sortie.replace("&", "").rstrip(" :"))
        self.champ_sortie.SetMinSize((520, 160))

        boite = wx.BoxSizer(wx.VERTICAL)
        boite.Add(texte_message, 0, wx.ALL, 10)
        boite.Add(boutons, 0, wx.LEFT | wx.RIGHT, 10)
        boite.Add(texte_etiquette, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        boite.Add(self.champ_sortie, 1, wx.EXPAND | wx.ALL, 10)
        self.SetSizerAndFit(boite)
        self.SetEscapeId(wx.ID_CANCEL)
        wx.CallAfter(premier.SetFocus)

    def _choisir(self, valeur) -> None:
        self.choix = valeur
        self.EndModal(wx.ID_OK if valeur != ARRETER else wx.ID_CANCEL)

    def _sur_copier(self, evt) -> None:
        if self._copier(self._texte_rapport):
            self._voix.dire("Rapport copié dans le presse-papiers.", interrompre=True)
        else:
            self._voix.dire("Presse-papiers inaccessible.", interrompre=True)


def ouvrir_dossier(dossier: Path) -> None:
    try:
        dossier.mkdir(parents=True, exist_ok=True)
        os.startfile(str(dossier))
    except OSError:
        logging.exception("Impossible d'ouvrir le dossier des scénarios.")
        wx.MessageBox(
            f"Le dossier des scénarios se trouve ici :\n{dossier}",
            "Scénarios", wx.OK | wx.ICON_INFORMATION,
        )


# --------------------------------------------------------------------------
# Exécution
# --------------------------------------------------------------------------

class ExecutionScenario:
    """Une exécution d'un scénario dans une session (un onglet). Tant
    qu'elle dure, la session est marquée occupée (en_cours) : la saisie
    est verrouillée, et Ctrl+Maj+K l'arrête (PanneauSession.interrompre)."""

    def __init__(
        self, panneau, scenario: Scenario, simulation: bool,
        lire_commandes: bool, dossier_journaux: Path, copier, bip,
        pas_a_pas: bool = True,
    ):
        self.panneau = panneau
        # Gardés à part : le thread de fond s'en sert sans toucher au
        # panneau, qui peut être détruit si l'onglet est fermé.
        self.voix = panneau.voix
        self.executeur = panneau.executeur
        self.distant = panneau.distant
        self.reglages = panneau.reglages
        self.scenario = scenario
        self.simulation = simulation
        self.lire_commandes = lire_commandes
        self.pas_a_pas = pas_a_pas
        self.dossier_journaux = dossier_journaux
        self.copier = copier
        self.bip = bip
        self._arret = threading.Event()
        self._commande_lancee = False
        self.valeurs = variables_initiales(scenario, datetime.now())
        self.sequence = scenario.etapes_en_sequence()
        # Où reprendre une fois une étape alternative réussie : là où
        # serait allée l'étape qu'elle remplace.
        self._retour_alternative: int | None = None
        self.bilan: list[str] = []
        self.nb_bonnes = 0
        # Verdict de la dernière étape jouée, rappelé en tête de la boîte
        # suivante (pas à pas) et de la boîte de fin.
        self._precedente = ""
        self.journal: JournalScenario | None = None
        # Vrai juste après la fermeture d'une boîte de dialogue : NVDA
        # annonce alors le retour du focus dans la fenêtre, ce qui
        # couperait une annonce partie au même instant (voir _dire).
        self._boite_fermee = False

    # -- démarrage et arrêt (thread principal) -------------------------------

    def demarrer(self) -> None:
        p = self.panneau
        try:
            self.journal = JournalScenario(
                self.dossier_journaux, self.scenario, self.simulation, p.nom,
            )
        except OSError as erreur:
            logging.exception("Journal de scénario impossible à créer.")
            wx.MessageBox(
                f"Impossible de créer le journal du scénario :\n{erreur}",
                "Scénario", wx.OK | wx.ICON_ERROR,
            )
            return
        p.scenario = self
        p.en_cours = True
        p.saisie.SetEditable(False)
        p._commande_en_cours = f"Scénario : {self.scenario.nom}"
        p._debut = time.monotonic()
        p.rafraichir_statut()
        self.repertoire = p.repertoire
        logging.info(
            "Scénario lancé : %s (%s)%s", self.scenario.nom,
            self.scenario.chemin, ", simulation" if self.simulation else "",
        )
        mode = "Simulation du scénario" if self.simulation else "Scénario"
        # En pas à pas, la première boîte s'ouvre aussitôt et dit la même
        # chose : une annonce ici serait coupée.
        if not self.pas_a_pas:
            self.voix.dire(
                f"{mode} {self.scenario.nom}, {len(self.sequence)} étapes. "
                "Ctrl+Maj+K pour l'arrêter.",
                braille=f"{mode} {self.scenario.nom}",
                interrompre=True,
            )
        threading.Thread(target=self._boucle, daemon=True).start()

    def arreter(self) -> None:
        if self._arret.is_set():
            return
        self._arret.set()
        self.voix.dire("Arrêt du scénario demandé.", interrompre=True)
        if self._commande_lancee:
            threading.Thread(target=self.executeur.interrompre, daemon=True).start()

    # -- passerelle vers le thread principal ---------------------------------

    def _sur_interface(self, fonction, *args):
        """Exécute fonction dans le thread principal et attend son
        résultat. Rien ne s'exécute si l'onglet a été fermé entre-temps."""
        resultat = {}
        fini = threading.Event()

        def appeler():
            try:
                if self.panneau:
                    resultat["valeur"] = fonction(*args)
            except Exception:
                logging.exception("Scénario : erreur dans l'interface.")
            finally:
                fini.set()

        wx.CallAfter(appeler)
        fini.wait()
        return resultat.get("valeur")

    def _dire(self, texte: str, braille: str | None = None, interrompre: bool = False) -> None:
        if self._boite_fermee:
            self._boite_fermee = False
            time.sleep(0.5)
        self._sur_interface(self.voix.dire, texte, braille, interrompre)

    def _boite(self, fonction, *args):
        """Ouvre une boîte de dialogue dans le thread principal et attend
        sa réponse."""
        try:
            return self._sur_interface(fonction, *args)
        finally:
            self._boite_fermee = True

    # -- enchaînement (thread de fond) ---------------------------------------

    def _libelle(self, i: int) -> str:
        etape = self.scenario.etapes[i]
        if etape.hors_sequence:
            return "Étape alternative"
        return f"Étape {self.sequence.index(etape) + 1} sur {len(self.sequence)}"

    def _suivante(self, i: int) -> int | None:
        etapes = self.scenario.etapes
        for j in range(i + 1, len(etapes)):
            if not etapes[j].hors_sequence:
                return j
        return None

    def _apres_bon(self, i: int) -> int | None:
        etape = self.scenario.etapes[i]
        if etape.si_bon == FIN:
            return None
        if etape.si_bon != SUIVANTE:
            return self.scenario.index(etape.si_bon)
        if etape.hors_sequence:
            return self._retour_alternative
        return self._suivante(i)

    def _boucle(self) -> None:
        conclusion = "terminé"
        problemes_passes = 0
        i: int | None = self.scenario.index(self.sequence[0].id)
        executees = 0
        try:
            while i is not None:
                if self._arret.is_set() or not self.panneau:
                    conclusion = f"arrêté avant l'{self._libelle(i).lower()}"
                    break
                executees += 1
                if executees > MAX_ETAPES_EXECUTEES:
                    conclusion = "arrêté : trop d'étapes exécutées, le scénario tourne en rond"
                    break
                suite = self._executer_etape(i)
                if suite == ARRETER:
                    conclusion = f"arrêté à l'{self._libelle(i).lower()}"
                    break
                if suite == CONTINUER:
                    problemes_passes += 1
                    etape = self.scenario.etapes[i]
                    i = self._retour_alternative if etape.hors_sequence else self._suivante(i)
                elif suite == REESSAYER:
                    continue
                elif isinstance(suite, tuple):
                    if not self.scenario.etapes[i].hors_sequence:
                        self._retour_alternative = self._apres_bon(i)
                    i = self.scenario.index(suite[1])
                else:
                    i = self._apres_bon(i)
        except Exception:
            logging.exception("Scénario : erreur inattendue.")
            conclusion = "arrêté sur une erreur inattendue, voir lazyshell.log"
        if conclusion == "terminé" and problemes_passes:
            conclusion = (
                f"terminé, {problemes_passes} problème passé" if problemes_passes == 1
                else f"terminé, {problemes_passes} problèmes passés"
            )
        if not self._sur_interface(self._terminer, conclusion):
            # Onglet fermé en cours de route : le journal se referme quand
            # même, pour garder la trace de l'arrêt.
            self.journal.fermer(conclusion)

    def _executer_etape(self, i: int):
        """Joue une étape. Renvoie BON (suite normale), ou le choix fait
        après un problème : REESSAYER, CONTINUER, ARRETER, ou
        (ALTERNATIVE, identifiant)."""
        etape = self.scenario.etapes[i]
        libelle = self._libelle(i)
        commande = substituer(etape.commande, self.valeurs)
        question = substituer(etape.question, self.valeurs)

        if self.pas_a_pas:
            # La boîte annonce l'étape à la place de la voix, et rien ne
            # part avant Entrée : le rythme est celui de l'utilisateur.
            if self._boite(self._avant_etape, libelle, etape, commande, question) != BON:
                self.journal.ecrire()
                self.journal.evenement(f"Arrêt demandé avant {libelle.lower()}.")
                return ARRETER
        else:
            annonce = f"{libelle}, {etape.titre}."
            if etape.destructive:
                annonce += " Étape destructive."
            if commande and self.lire_commandes:
                annonce += f" Commande : {commande}"
            self._dire(annonce, braille=f"{libelle}, {etape.titre}")
        self.journal.ecrire()
        self.journal.evenement(f"{libelle} : {etape.titre} (id {etape.id})")
        if commande:
            self.journal.ecrire(f"Commande : {commande}")
        else:
            self.journal.ecrire(f"Question : {question}")
        if etape.destructive:
            self.journal.ecrire("Étape destructive.")

        if self.simulation:
            if commande:
                sortie = "[Simulation : commande non exécutée.]"
            else:
                sortie = f"[Simulation : question non posée.]\n{question}"
            self._sur_interface(
                self._ajouter_bloc, commande or question, sortie, 0, 0.0, False,
                f"{libelle}, {etape.titre}, simulation",
            )
            self.journal.ecrire("Simulation : rien n'est exécuté.")
            self.bilan.append(f"{libelle}, {etape.titre} : simulation")
            self._precedente = f"{libelle}, {etape.titre} : simulation, rien n'a été exécuté."
            return BON

        if etape.destructive and commande:
            accord = self._boite(self._confirmer, libelle, etape.titre, commande)
            if not accord:
                self.journal.evenement("Confirmation refusée.")
                self.bilan.append(f"{libelle}, {etape.titre} : non confirmée")
                return ARRETER
            self.journal.evenement("Confirmation donnée.")

        if question:
            oui = self._boite(self._poser_question, libelle, etape.titre, question)
            reponse = "Oui" if oui else "Non"
            sortie = f"Question : {question}\nRéponse : {reponse}"
            code, duree, interrompue = 0, 0.0, False
            problemes = [] if oui else ["réponse Non à la question"]
            commande_bloc = question
        else:
            self._commande_lancee = True
            try:
                resultat = self.executeur.executer(
                    commande,
                    repertoire=self.repertoire,
                    sur_lenteur=lambda: wx.CallAfter(self._signaler_lenteur),
                    listing_lisible=False,
                )
            finally:
                self._commande_lancee = False
            sortie, code = resultat.sortie, resultat.code_retour
            duree, interrompue = resultat.duree, resultat.interrompue
            problemes = verifier(etape.attendu, sortie, code, self.valeurs)
            # Même signal que PanneauSession._commande_terminee : -1 est un
            # échec au lancement, pas un code de sortie distant.
            if code == -1 and self.distant and not self.executeur.connexion_active:
                problemes.insert(0, "connexion SSH perdue")
            commande_bloc = commande

        if interrompue or self._arret.is_set():
            self._sur_interface(
                self._ajouter_bloc, commande_bloc, sortie, code, duree, True,
                f"{libelle}, {etape.titre}",
            )
            self.journal.ecrire(f"Code de retour : {code}, durée {duree:.1f} s")
            self._journaliser_sortie(etape, sortie)
            self.journal.ecrire("Verdict : interrompue")
            self.bilan.append(f"{libelle}, {etape.titre} : interrompue")
            self._precedente = f"{libelle}, {etape.titre} : interrompue."
            return ARRETER

        verdict = PROBLEME if problemes else BON
        sortie_bloc = sortie
        if problemes:
            sortie_bloc = (sortie + "\n" if sortie else "") + (
                "[Problème : " + "; ".join(problemes) + ".]"
            )
        self._sur_interface(
            self._ajouter_bloc, commande_bloc, sortie_bloc, code, duree, False,
            f"{libelle}, {etape.titre}, {verdict}",
        )
        if not question:
            self.journal.ecrire(f"Code de retour : {code}, durée {duree:.1f} s")
            self._journaliser_sortie(etape, sortie)
        else:
            # La question est déjà au journal : seule la réponse manque.
            self.journal.ecrire(sortie.splitlines()[-1])
        self.journal.ecrire(
            f"Verdict : {verdict}" + (" : " + "; ".join(problemes) if problemes else "")
        )
        self.bilan.append(f"{libelle}, {etape.titre} : {verdict}")

        if self.reglages.sons:
            self.bip(not problemes)
        if not problemes:
            self.nb_bonnes += 1
            self._precedente = f"{libelle}, {etape.titre} : {BON}."
            if not self.pas_a_pas:
                # Sans interrompre : la lecture de la commande, si elle
                # n'est pas finie, se termine avant le verdict.
                self._dire(f"{BON}.", braille=f"{BON}, {libelle}, {etape.titre}")
            return BON

        raisons = "; ".join(problemes)
        # Pas d'annonce vocale ici : la boîte qui s'ouvre aussitôt la
        # couperait. NVDA lit son titre et son message lui-même.
        texte_rapport = rapport(
            self.scenario, libelle, etape, commande_bloc, sortie, code, problemes,
        )
        choix = self._boite(
            self._demander_suite, etape, libelle, raisons, sortie, texte_rapport,
        )
        if choix is None:
            choix = ARRETER
        self.journal.evenement(f"Choix : {self._nom_choix(choix)}")
        self._precedente = f"{libelle}, {etape.titre} : {PROBLEME}, choix {self._nom_choix(choix)}."
        return choix

    def _journaliser_sortie(self, etape, sortie: str) -> None:
        if not etape.sortie_au_journal:
            self.journal.ecrire("Sortie : non journalisée (sortie_au_journal = false)")
        elif sortie:
            self.journal.ecrire("Sortie :")
            self.journal.ecrire(sortie.rstrip("\n"))
        else:
            self.journal.ecrire("Sortie : aucune")

    def _nom_choix(self, choix) -> str:
        if isinstance(choix, tuple):
            return f"étape alternative {choix[1]}"
        return {
            REESSAYER: "réessayer", CONTINUER: "continuer quand même",
            ARRETER: "arrêter",
        }.get(choix, str(choix))

    # -- actions d'interface (thread principal) ------------------------------

    def _signaler_lenteur(self) -> None:
        if self.panneau:
            self.panneau._signaler_lenteur()

    def _ajouter_bloc(
        self, commande: str, sortie: str, code: int, duree: float,
        interrompue: bool, etiquette: str,
    ) -> None:
        p = self.panneau
        # Lus par ajouter_bloc au moment de créer le bloc, comme dans
        # PanneauSession._commande_terminee.
        p._duree = duree
        p._interrompue = interrompue
        p.ajouter_bloc(commande, sortie, code, etiquette=etiquette, annoncer=False)

    def _confirmer(self, libelle: str, titre: str, commande: str) -> bool:
        reponse = wx.MessageBox(
            f"Étape destructive : {titre}.\n\nCommande :\n{commande}\n\nL'exécuter ?",
            f"Confirmation, {libelle}",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_WARNING,
            self.panneau.GetTopLevelParent(),
        )
        return reponse == wx.YES

    def _poser_question(self, libelle: str, titre: str, question: str) -> bool:
        reponse = wx.MessageBox(
            question, f"{libelle}, {titre}", wx.YES_NO | wx.ICON_QUESTION,
            self.panneau.GetTopLevelParent(),
        )
        return reponse == wx.YES

    def _avant_etape(self, libelle: str, etape, commande: str, question: str):
        """Pas à pas : présente l'étape qui va être jouée. Renvoie BON pour
        la lancer, ARRETER sinon."""
        morceaux = []
        if self._precedente:
            morceaux.append(f"Précédente : {self._precedente}")
        else:
            mode = "Simulation" if self.simulation else "Scénario"
            morceaux.append(f"{mode} {self.scenario.nom}, {len(self.sequence)} étapes.")
        suivante = f"Suivante : {libelle}, {etape.titre}."
        if etape.destructive:
            suivante += " Étape destructive."
        morceaux.append(suivante)
        if commande:
            contenu = commande
            etiquette = (
                "&Commande, non exécutée en simulation :" if self.simulation
                else "&Commande à exécuter :"
            )
        else:
            contenu, etiquette = question, "&Question :"
        boite = DialogueVerdict(
            self.panneau.GetTopLevelParent(),
            f"{libelle}, {etape.titre}",
            "\n".join(morceaux),
            contenu,
            [(BON, "&Continuer"), (ARRETER, "&Arrêter le scénario")],
            None, self.copier, self.voix, etiquette_sortie=etiquette,
        )
        try:
            boite.ShowModal()
            return boite.choix
        finally:
            boite.Destroy()

    def _demander_suite(self, etape, libelle, raisons, sortie, texte_rapport):
        choix = []
        for valeur in etape.si_echec:
            if valeur == REESSAYER:
                choix.append((REESSAYER, "&Réessayer"))
            elif valeur == CONTINUER:
                choix.append((CONTINUER, "&Continuer quand même"))
            elif isinstance(valeur, tuple) and valeur[0] == ALTERNATIVE:
                cible = self.scenario.etapes[self.scenario.index(valeur[1])]
                choix.append((valeur, f"É&tape alternative : {cible.titre}"))
        choix.append((ARRETER, "&Arrêter le scénario"))
        boite = DialogueVerdict(
            self.panneau.GetTopLevelParent(),
            f"{PROBLEME}, {libelle}, {etape.titre}",
            f"{PROBLEME} : {raisons}.",
            sortie, choix, texte_rapport, self.copier, self.voix,
        )
        try:
            boite.ShowModal()
            return boite.choix
        finally:
            boite.Destroy()

    def _terminer(self, conclusion: str) -> bool:
        p = self.panneau
        nom_journal = self.journal.chemin.name
        self.journal.fermer(conclusion)
        logging.info("Scénario %s : %s", self.scenario.nom, conclusion)
        p.scenario = None
        p.en_cours = False
        p.saisie.SetEditable(True)
        p.rafraichir_statut()

        lignes = list(self.bilan) or ["Aucune étape jouée."]
        lignes.append(f"Journal : {self.journal.chemin}")
        p._duree, p._interrompue = 0.0, False
        bloc = p.ajouter_bloc(
            f"Scénario {self.scenario.chemin.name}", "\n".join(lignes), 0,
            etiquette=f"Fin du scénario {self.scenario.nom}, {conclusion}",
            annoncer=False,
        )
        # Une boîte plutôt qu'une annonce vocale, qui se faisait couper
        # (seul le braille restait) : NVDA la lit lui-même, et elle reste
        # là tant qu'on n'a pas validé.
        mode = "Simulation" if self.simulation else "Scénario"
        morceaux = []
        if self._precedente:
            morceaux.append(f"Dernière étape jouée : {self._precedente}")
        morceaux.append(f"{mode} {self.scenario.nom} : {conclusion}.")
        if not self.simulation:
            bonnes = self.nb_bonnes
            morceaux.append(
                "Aucune étape bonne." if bonnes == 0
                else "1 étape bonne." if bonnes == 1
                else f"{bonnes} étapes bonnes."
            )
        morceaux.append(f"Journal : {nom_journal}")
        wx.MessageBox(
            "\n".join(morceaux), f"Fin du scénario, {conclusion}",
            wx.OK | wx.ICON_INFORMATION, p.GetTopLevelParent(),
        )
        if p.reglages.suivre_sortie:
            p.sortie.SetFocus()
            p.sortie.SetInsertionPoint(bloc.debut + 1)
            p.sortie.ShowPosition(bloc.debut + 1)
        else:
            p.saisie.SetFocus()
        return True


def lancer_scenario(fenetre, panneau, base: Path, copier, bip) -> None:
    """Point d'entrée depuis le menu Scénarios (Ctrl+Maj+Q)."""
    if panneau.en_cours:
        panneau.voix.dire(
            "Une commande est déjà en cours dans cette session.", interrompre=True,
        )
        return
    if panneau.mode_navigation:
        panneau.voix.dire(
            "Repassez en mode terminal, Ctrl+Maj+F, pour lancer un scénario.",
            interrompre=True,
        )
        return
    dossier = dossier_scenarios(base)
    boite = DialogueLancementScenario(fenetre, dossier)
    try:
        if boite.ShowModal() != wx.ID_OK or boite.scenario is None:
            panneau.saisie.SetFocus()
            return
        scenario = boite.scenario
        simulation = boite.case_simulation.GetValue()
        lire_commandes = boite.case_commandes.GetValue()
        pas_a_pas = boite.case_pas_a_pas.GetValue()
    finally:
        boite.Destroy()

    # En simulation rien ne part vers le serveur : pas besoin d'être sur
    # le bon profil, on peut relire une procédure depuis n'importe où.
    if scenario.profil and not simulation:
        actuel = panneau.profil.nom if panneau.profil is not None else ""
        if actuel != scenario.profil:
            ici = f"le profil « {actuel} »" if actuel else "une session locale"
            wx.MessageBox(
                f"Ce scénario s'exécute sur le profil SSH « {scenario.profil} ». "
                f"La session actuelle, « {panneau.nom} », est {ici}.\n\n"
                "Ouvrez une session sur le bon profil (Ctrl+Maj+O), ou lancez "
                "le scénario en mode simulation.",
                "Mauvaise session", wx.OK | wx.ICON_WARNING, fenetre,
            )
            panneau.saisie.SetFocus()
            return

    ExecutionScenario(
        panneau, scenario, simulation, lire_commandes,
        dossier / "journaux", copier, bip, pas_a_pas=pas_a_pas,
    ).demarrer()
