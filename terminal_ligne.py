# -*- coding: utf-8 -*-
"""
Interprétation minimale d'un flux de pseudo-terminal, ligne par ligne.

Utilisé par le mode interactif (ExecuteurSSH.executer_interactif) : un
programme qui croit parler à un vrai terminal envoie des retours chariot
pour réécrire sa ligne (barres de progression), des retours arrière, des
effacements de ligne, des couleurs, des changements de titre de fenêtre.
Supprimer bêtement ces séquences, comme nettoyer_ansi le fait pour le
mode normal, laisserait des doublons (« 10% 20% 30% » sur une seule
ligne) que NVDA lirait tous. On les interprète donc pour ne garder que
ce qui resterait réellement affiché sur la ligne.

Pas un émulateur de terminal complet, volontairement : aucune notion
d'écran ni de déplacement vertical. Un programme plein écran (vim, htop,
nano) demande l'écran alternatif, ce qui est simplement signalé par
plein_ecran pour que l'appelant puisse refuser proprement.

Ce module ne connaît rien de wxPython ni de Paramiko : testable seul.
"""

from __future__ import annotations

import re

ECHAP = "\x1b"

# Paramètres des séquences CSI qui basculent sur l'écran alternatif,
# utilisé par tous les programmes plein écran courants.
ECRANS_ALTERNATIFS = ("?1049", "?1047", "?47")

# Fin d'une séquence CSI : un octet final entre @ et ~.
MOTIF_CSI = re.compile(r"\[([0-?]*)([ -/]*)([@-~])")


class NettoyeurTerminal:
    """Reçoit le flux décodé morceau par morceau (alimenter) et rend les
    lignes terminées, nettoyées. La ligne encore incomplète (une invite
    comme « >>> » ou « Mot de passe : ») reste lisible par fragment.

    Les morceaux peuvent être coupés n'importe où, y compris au milieu
    d'une séquence d'échappement : le reste non interprétable est gardé
    pour le morceau suivant.
    """

    def __init__(self):
        self._ligne: list[str] = []
        self._colonne = 0
        self._en_suspens = ""
        self.plein_ecran = False

    @property
    def fragment(self) -> str:
        return "".join(self._ligne).rstrip()

    def alimenter(self, texte: str) -> list[str]:
        texte = self._en_suspens + texte
        self._en_suspens = ""
        terminees: list[str] = []
        i = 0
        n = len(texte)
        while i < n:
            c = texte[i]
            if c == ECHAP:
                consomme = self._sequence(texte, i)
                if consomme is None:
                    # Séquence coupée en fin de morceau : on attend la suite.
                    self._en_suspens = texte[i:]
                    break
                i += consomme
                continue
            if c == "\n":
                terminees.append("".join(self._ligne).rstrip())
                self._ligne = []
                self._colonne = 0
            elif c == "\r":
                self._colonne = 0
            elif c == "\b":
                self._colonne = max(0, self._colonne - 1)
            elif c == "\t":
                self._ecrire(" " * (8 - self._colonne % 8))
            elif ord(c) < 32 or c == "\x7f":
                pass                # cloche et autres caractères de contrôle
            else:
                self._ecrire(c)
            i += 1
        return terminees

    def vider(self) -> str:
        """Fin du flux : rend la ligne incomplète restante et repart à zéro."""
        reste = self.fragment
        self._ligne = []
        self._colonne = 0
        self._en_suspens = ""
        return reste

    # -- interne -------------------------------------------------------------

    def _ecrire(self, texte: str) -> None:
        for c in texte:
            if self._colonne < len(self._ligne):
                self._ligne[self._colonne] = c
            else:
                self._ligne.extend(" " * (self._colonne - len(self._ligne)))
                self._ligne.append(c)
            self._colonne += 1

    def _sequence(self, texte: str, i: int) -> int | None:
        """Interprète la séquence qui commence en texte[i] (ESC).
        Renvoie le nombre de caractères consommés, ou None si elle est
        incomplète."""
        if i + 1 >= len(texte):
            return None
        suivant = texte[i + 1]

        if suivant == "[":
            correspondance = MOTIF_CSI.match(texte, i + 1)
            if correspondance is None:
                # Incomplète si on n'a vu que des caractères de paramètre.
                reste = texte[i + 2:]
                if all("\x20" <= c <= "\x3f" for c in reste):
                    return None
                return 2
            parametres, _, final = correspondance.groups()
            self._csi(parametres, final)
            return 1 + correspondance.end() - (i + 1)

        if suivant == "]":
            # OSC (titre de fenêtre...) : jusqu'à BEL ou ESC \.
            fin_bel = texte.find("\x07", i + 2)
            fin_st = texte.find(ECHAP + "\\", i + 2)
            fins = [f for f in (fin_bel, fin_st) if f != -1]
            if not fins:
                return None
            fin = min(fins)
            return fin - i + (1 if fin == fin_bel else 2)

        if suivant in "()":
            # Choix du jeu de caractères : un caractère de plus.
            return 3 if i + 2 < len(texte) else None

        # Séquence à deux caractères (ESC =, ESC >, ESC 7...) : ignorée.
        return 2

    def _csi(self, parametres: str, final: str) -> None:
        if final in "hl" and parametres in ECRANS_ALTERNATIFS:
            if final == "h":
                self.plein_ecran = True
            return
        valeurs = [int(v) if v.isdigit() else 0 for v in parametres.split(";")]
        nombre = max(1, valeurs[0]) if valeurs else 1
        if final == "K":
            mode = valeurs[0] if valeurs else 0
            if mode == 0:
                del self._ligne[self._colonne:]
            elif mode == 1:
                for k in range(min(self._colonne + 1, len(self._ligne))):
                    self._ligne[k] = " "
            else:
                self._ligne = []
        elif final == "D":
            self._colonne = max(0, self._colonne - nombre)
        elif final == "C":
            self._colonne += nombre
        elif final == "G":
            self._colonne = nombre - 1
        elif final == "P":
            # Suppression de caractères sous le curseur (édition readline).
            del self._ligne[self._colonne:self._colonne + nombre]
        elif final == "@":
            # Insertion d'espaces sous le curseur.
            self._ligne[self._colonne:self._colonne] = [" "] * nombre
        # Tout le reste (couleurs m, déplacements verticaux, effacement
        # d'écran J...) est ignoré : sans notion d'écran, il n'a rien à
        # modifier sur la ligne courante.
