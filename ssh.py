# -*- coding: utf-8 -*-
"""
Exécution des commandes à distance, par SSH (palier 2).

Même contrat que execution.ExecuteurLocal.executer() : c'est ce qui
permet à PanneauSession de piloter indifféremment une session locale ou
distante sans rien savoir de la différence. Ce module ne connaît rien de
wxPython, pour les mêmes raisons qu'execution.py : il reste testable sans
interface, et les callbacks (sur_ligne, sur_lenteur, sur_invite,
sur_verification_hote) sont à charge de l'appelant de les réacheminer
vers le thread principal.

SSH par Paramiko, jamais par plink ni par le ssh.exe de Windows : OpenSSH
lit le mot de passe sur le terminal et non sur stdin, ce qui rend
l'authentification impossible sans console.
"""

from __future__ import annotations

import base64
import codecs
import dataclasses
import hashlib
import json
import logging
import queue
import re
import shlex
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import keyring
import paramiko

from execution import (
    MAX_LIGNES,
    SEUIL_COMMANDE_LONGUE,
    SEUIL_INVITE,
    Resultat,
    nettoyer_ansi,
)

# Nom de service utilisé dans le Gestionnaire d'identifiants Windows.
# Aucun mot de passe ni passphrase n'est jamais écrit en clair dans un
# fichier : seuls les champs non secrets d'un profil (hôte, port,
# utilisateur, mode d'authentification, chemin de clé) sont dans
# ssh_profiles.json.
SERVICE_KEYRING = "LazyShell-SSH"

# Intervalle, en secondes, entre deux paquets de maintien de connexion.
INTERVALLE_KEEPALIVE = 30


def _dossier_base() -> Path:
    """Dossier de travail : à côté de l'exe si compilé, du script sinon.

    Recopié depuis lazyshell.dossier_base() : ce module ne doit
    rien importer de wxPython ni de l'application pour rester testable
    seul, et cette fonction ne fait que quatre lignes.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _chemin_profils() -> Path:
    return _dossier_base() / "ssh_profiles.json"


def _chemin_cles_connues() -> Path:
    return _dossier_base() / "known_hosts"


# --------------------------------------------------------------------------
# Profils de connexion
# --------------------------------------------------------------------------

@dataclass
class ProfilConnexion:
    nom: str
    hote: str
    port: int = 22
    utilisateur: str = ""
    mode_auth: str = "mot_de_passe"   # ou "cle"
    chemin_cle: str = ""              # utilisé seulement si mode_auth == "cle"

    def cle_keyring(self) -> str:
        """Identifiant du secret associé, dans le Gestionnaire d'identifiants."""
        return f"profil:{self.nom}"


def charger_profils() -> list[ProfilConnexion]:
    chemin = _chemin_profils()
    if not chemin.exists():
        return []
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
        return [ProfilConnexion(**d) for d in donnees]
    except (OSError, json.JSONDecodeError, TypeError):
        logging.exception("Profils SSH illisibles, ignorés : %s", chemin)
        return []


def enregistrer_profils(profils: list[ProfilConnexion]) -> None:
    chemin = _chemin_profils()
    donnees = [dataclasses.asdict(p) for p in profils]
    chemin.write_text(
        json.dumps(donnees, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def lire_secret(profil: ProfilConnexion) -> str | None:
    return keyring.get_password(SERVICE_KEYRING, profil.cle_keyring())


def enregistrer_secret(profil: ProfilConnexion, secret: str) -> None:
    keyring.set_password(SERVICE_KEYRING, profil.cle_keyring(), secret)


def supprimer_secret(profil: ProfilConnexion) -> None:
    try:
        keyring.delete_password(SERVICE_KEYRING, profil.cle_keyring())
    except keyring.errors.PasswordDeleteError:
        pass


# --------------------------------------------------------------------------
# Vérification de la clé d'hôte (mémorisation à la première connexion)
# --------------------------------------------------------------------------

def _empreinte_sha256(cle: paramiko.PKey) -> str:
    empreinte = hashlib.sha256(cle.asbytes()).digest()
    return "SHA256:" + base64.b64encode(empreinte).decode().rstrip("=")


class _PolitiqueMemorisation(paramiko.MissingHostKeyPolicy):
    """Mémorise la clé d'un hôte inconnu après confirmation explicite.

    Équivalent du known_hosts d'OpenSSH, mais dans un fichier propre à
    l'application plutôt que dans celui de l'utilisateur (~/.ssh/known_hosts
    n'est jamais touché). Le cas « clé connue mais différente » n'est PAS
    géré ici : Paramiko le détecte lui-même et lève BadHostKeyException
    avant même d'appeler cette politique — voir connecter().
    """

    def __init__(self, chemin_fichier: Path, sur_verification: Callable[[str], bool]):
        self._chemin_fichier = chemin_fichier
        self._sur_verification = sur_verification

    def missing_host_key(self, client, hostname, key):
        message = (
            f"Nouvelle clé d'hôte pour {hostname}.\n"
            f"Type : {key.get_name()}\n"
            f"Empreinte SHA256 : {_empreinte_sha256(key)}\n\n"
            "Si possible, vérifiez cette empreinte auprès de l'administrateur "
            "du serveur avant d'accepter.\n\n"
            "Accepter et mémoriser cette clé ?"
        )
        if not self._sur_verification(message):
            raise paramiko.SSHException(f"Clé d'hôte refusée pour {hostname}.")
        client.get_host_keys().add(hostname, key.get_name(), key)
        client.save_host_keys(str(self._chemin_fichier))
        logging.info("Nouvelle clé d'hôte mémorisée pour %s", hostname)


# --------------------------------------------------------------------------
# Listing lisible (équivalent distant du reecrire_listing d'execution.py)
# --------------------------------------------------------------------------

# ls, au tout début de la ligne uniquement — POSIX, donc sensible à la
# casse, contrairement au dir/ls/gci de PowerShell côté local.
MOTIF_LISTING = re.compile(r"^\s*ls(?=\s|$)(?P<reste>.*)$")


def reecrire_listing(commande: str) -> str | None:
    """Transforme un « ls » nu en listing nom-en-tête, même principe que
    execution.reecrire_listing côté local : le nom du fichier est
    l'information utile, il ne doit pas attendre la fin d'une ligne de
    droits/propriétaire/taille pour être lu.

    Renvoie None si la commande ne s'y prête pas : tube, redirection,
    point-virgule, plusieurs lignes, ou des arguments après ls (« ls -la »)
    — ceux-ci n'ont pas d'équivalent direct en argument de find, les
    réécrire produirait une erreur au lieu du listing attendu. Dans ces
    cas l'utilisateur compose déjà quelque chose de précis.
    """
    if any(c in commande for c in ("|", ">", ";", "\n")):
        return None
    correspondance = MOTIF_LISTING.match(commande)
    if correspondance is None:
        return None
    if correspondance.group("reste").strip():
        return None
    return (
        "{ echo 'Nom\tType\tTaille\tModifié'; "
        "find . -maxdepth 1 -mindepth 1 "
        "-printf '%f\\t%y\\t%s\\t%TY-%Tm-%Td %TH:%TM\\n' 2>/dev/null "
        "| awk -F'\\t' 'BEGIN{OFS=\"\\t\"} "
        "{if ($2==\"d\") $2=\"dossier\"; else if ($2==\"f\") $2=\"fichier\"; "
        "else if ($2==\"l\") $2=\"lien\"; else $2=\"autre\"; print}' "
        "| sort; }"
    )


# --------------------------------------------------------------------------
# Exécuteur SSH
# --------------------------------------------------------------------------

class ExecuteurSSH:
    """Exécute des commandes à distance par SSH, une connexion persistante
    par session — contrairement à l'exécution locale, qui relance un
    processus à chaque commande, la connexion SSH est coûteuse à établir
    et reste ouverte tant que la session est active."""

    def __init__(self):
        self._client: paramiko.SSHClient | None = None
        self._canal = None
        self._verrou = threading.Lock()

    @property
    def connecte(self) -> bool:
        with self._verrou:
            return self._client is not None

    # -- connexion -----------------------------------------------------

    def connecter(
        self,
        profil: ProfilConnexion,
        secret: str | None,
        sur_verification_hote: Callable[[str], bool],
    ) -> None:
        """Établit la connexion. Bloque : à appeler hors thread principal.

        sur_verification_hote(message) est appelée, et son résultat
        attendu, pour toute clé d'hôte nouvelle ou changée. Les exceptions
        Paramiko (échec d'authentification, hôte injoignable, clé refusée)
        remontent telles quelles : c'est à l'appelant de les traduire pour
        l'utilisateur.
        """
        chemin_cles = _chemin_cles_connues()
        client = paramiko.SSHClient()
        if chemin_cles.exists():
            try:
                client.load_host_keys(str(chemin_cles))
            except OSError:
                logging.exception("Fichier des hôtes SSH connus illisible.")
        client.set_missing_host_key_policy(
            _PolitiqueMemorisation(chemin_cles, sur_verification_hote)
        )

        parametres = dict(
            hostname=profil.hote,
            port=profil.port,
            username=profil.utilisateur or None,
            timeout=15,
            allow_agent=False,
            look_for_keys=False,
        )
        if profil.mode_auth == "cle":
            parametres["key_filename"] = profil.chemin_cle
            if secret:
                parametres["passphrase"] = secret
        else:
            parametres["password"] = secret

        try:
            client.connect(**parametres)
        except paramiko.BadHostKeyException as erreur:
            message = (
                f"ATTENTION : la clé d'hôte de {profil.hote} a CHANGÉ.\n"
                f"Nouvelle empreinte SHA256 : {_empreinte_sha256(erreur.key)}\n\n"
                "Cela peut signifier une interception de la connexion, ou "
                "simplement un serveur réinstallé. Accepter quand même la "
                "nouvelle clé et mémoriser ?"
            )
            if not sur_verification_hote(message):
                raise
            client.get_host_keys().add(
                profil.hote, erreur.key.get_name(), erreur.key
            )
            client.save_host_keys(str(chemin_cles))
            logging.warning("Clé d'hôte changée et acceptée pour %s", profil.hote)
            client.connect(**parametres)

        # Sans ça, une session inactive un moment peut être coupée en
        # silence par le serveur ou un pare-feu/NAT intermédiaire : la
        # commande suivante échouerait alors avec une erreur qui ne dit
        # pas pourquoi. Un paquet toutes les INTERVALLE_KEEPALIVE secondes
        # suffit à garder la connexion vivante côté deux extrémités.
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(INTERVALLE_KEEPALIVE)

        with self._verrou:
            self._client = client
        logging.info(
            "Connecté en SSH à %s@%s:%s", profil.utilisateur, profil.hote, profil.port
        )

    def fermer(self) -> None:
        with self._verrou:
            client, self._client = self._client, None
        if client is not None:
            client.close()
            logging.info("Connexion SSH fermée.")

    # -- transfert de fichiers (SFTP) --------------------------------------

    def envoyer_fichier(
        self,
        chemin_local: str,
        chemin_distant: str,
        sur_progression: Callable[[int, int], None] | None = None,
    ) -> None:
        """Copie un fichier local vers le serveur. Bloque : à appeler hors
        thread principal. Les exceptions (fichier local introuvable,
        chemin distant invalide, droits refusés...) remontent telles
        quelles, à charge de l'appelant de les traduire."""
        with self._verrou:
            client = self._client
        if client is None:
            raise RuntimeError("Aucune connexion SSH active.")
        sftp = client.open_sftp()
        try:
            sftp.put(chemin_local, chemin_distant, callback=sur_progression)
            logging.info("Fichier envoyé : %s -> %s", chemin_local, chemin_distant)
        finally:
            sftp.close()

    def recuperer_fichier(
        self,
        chemin_distant: str,
        chemin_local: str,
        sur_progression: Callable[[int, int], None] | None = None,
    ) -> None:
        """Copie un fichier distant vers cette machine. Mêmes règles que
        envoyer_fichier."""
        with self._verrou:
            client = self._client
        if client is None:
            raise RuntimeError("Aucune connexion SSH active.")
        sftp = client.open_sftp()
        try:
            sftp.get(chemin_distant, chemin_local, callback=sur_progression)
            logging.info("Fichier récupéré : %s -> %s", chemin_distant, chemin_local)
        finally:
            sftp.close()

    # -- exécution -------------------------------------------------------

    def executer(
        self,
        commande: str,
        repertoire: str | None = None,
        sur_ligne: Callable[[str, bool], None] | None = None,
        sur_lenteur: Callable[[], None] | None = None,
        sur_invite: Callable[[str], str | None] | None = None,
        listing_lisible: bool = True,
    ) -> Resultat:
        """Lance la commande sur la connexion déjà établie et attend sa fin.

        listing_lisible, si vrai, réécrit un « ls » nu en listing nom-en-
        tête (voir reecrire_listing) — même principe que dir/ls/gci en
        PowerShell côté local, adapté à un shell distant supposé POSIX
        avec find en coreutils GNU (find -printf).

        Limite connue : sans pseudo-terminal (get_pty=False, choisi pour
        garder stdout et stderr séparés), une invite construite avec le
        « read -p » de bash n'écrit RIEN du tout — bash ne produit ce
        texte que si le shell est interactif, ce qu'il n'est jamais ici.
        sur_invite ne peut détecter que ce qui est effectivement écrit ;
        rien à faire côté détection, l'utilisateur doit interrompre. Même
        limite que Read-Host en PowerShell local (-NonInteractive).
        """
        with self._verrou:
            client = self._client
        if client is None:
            return Resultat(
                sortie="Aucune connexion SSH active.", code_retour=-1, duree=0.0,
            )

        debut = time.monotonic()
        commande_a_executer = commande
        if listing_lisible:
            reecrite = reecrire_listing(commande)
            if reecrite is not None:
                logging.info("Listing réécrit (SSH) : %s", reecrite)
                commande_a_executer = reecrite

        commande_finale = commande_a_executer
        if repertoire:
            # || exit 1 : si le repertoire n'existe plus, on s'arrête net
            # plutôt que d'exécuter silencieusement au mauvais endroit.
            commande_finale = (
                f"cd {shlex.quote(repertoire)} || exit 1\n{commande_a_executer}"
            )
        logging.info("Exécution SSH : %s", commande)

        try:
            # stdin doit rester référencé jusqu'à la fin de la commande :
            # sinon Paramiko le ramasse et referme l'écriture (EOF envoyé
            # au distant) dès que la variable est jetée, avant même qu'une
            # éventuelle réponse à une invite ait pu être envoyée.
            stdin, stdout, _ = client.exec_command(commande_finale, get_pty=False)
        except (paramiko.SSHException, OSError) as erreur:
            logging.exception("Lancement SSH impossible")
            return Resultat(
                sortie=f"Impossible d'exécuter la commande : {erreur}",
                code_retour=-1,
                duree=time.monotonic() - debut,
            )

        canal = stdout.channel
        with self._verrou:
            self._canal = canal

        lignes: list[str] = []
        tronquee = False
        fil: queue.Queue = queue.Queue()

        # Même principe que dans execution.py : un fragment de ligne
        # encore incomplet, par flux, pour repérer une invite de saisie
        # qui n'envoie jamais de retour à la ligne.
        verrou_fragments = threading.Lock()
        fragments = {
            False: {"tampon": [], "temps": 0.0, "signale": False},
            True: {"tampon": [], "temps": 0.0, "signale": False},
        }

        def lire(recevoir, est_erreur: bool):
            info = fragments[est_erreur]
            decodeur = codecs.getincrementaldecoder("utf-8")(errors="replace")
            try:
                while True:
                    morceau = recevoir(4096)
                    if not morceau:
                        break
                    texte = decodeur.decode(morceau)
                    if not texte:
                        continue
                    *completes, reste = texte.split("\n")
                    with verrou_fragments:
                        for ligne in completes:
                            if info["tampon"]:
                                ligne = "".join(info["tampon"]) + ligne
                                info["tampon"].clear()
                            fil.put((nettoyer_ansi(ligne.rstrip("\r")), est_erreur))
                        if reste:
                            info["tampon"].append(reste)
                            info["temps"] = time.monotonic()
                        info["signale"] = False
            except Exception:
                logging.exception("Lecture du canal SSH interrompue")
            finally:
                with verrou_fragments:
                    texte_restant = "".join(info["tampon"])
                    info["tampon"].clear()
                if texte_restant:
                    fil.put((nettoyer_ansi(texte_restant.rstrip("\r")), est_erreur))
                fil.put(None)

        lecteurs = [
            threading.Thread(target=lire, args=(canal.recv, False), daemon=True),
            threading.Thread(
                target=lire, args=(canal.recv_stderr, True), daemon=True
            ),
        ]
        for lecteur in lecteurs:
            lecteur.start()

        termines = 0
        prevenu = False

        while termines < 2:
            try:
                element = fil.get(timeout=0.25)
            except queue.Empty:
                if (
                    not prevenu
                    and sur_lenteur is not None
                    and time.monotonic() - debut > SEUIL_COMMANDE_LONGUE
                ):
                    prevenu = True
                    sur_lenteur()

                if sur_invite is not None and not canal.exit_status_ready():
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
                        if reponse is not None:
                            try:
                                stdin.write((reponse + "\n").encode("utf-8"))
                                stdin.flush()
                            except Exception:
                                logging.exception(
                                    "Écriture sur le canal SSH impossible"
                                )
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
                lignes.append(f"[Sortie tronquée : plus de {MAX_LIGNES} lignes.]")

        interrompue = getattr(canal, "_interrompue", False)
        code = canal.recv_exit_status() if not interrompue else -1

        with self._verrou:
            self._canal = None

        duree = time.monotonic() - debut
        logging.info(
            "SSH terminé, code %s, %.1f s, %d lignes", code, duree, len(lignes)
        )

        return Resultat(
            sortie="\n".join(lignes),
            code_retour=code,
            duree=duree,
            tronquee=tronquee,
            interrompue=interrompue,
        )

    # -- interruption ------------------------------------------------------

    def interrompre(self) -> bool:
        """Ferme le canal de la commande en cours.

        Contrairement à l'exécution locale (taskkill sur l'arborescence
        du processus), rien ne garantit qu'un processus distant sans
        pseudo-terminal meure immédiatement : fermer le canal est la
        seule action possible côté client. L'interface locale cesse dans
        tous les cas d'attendre, ce qui est ce qui compte pour
        l'utilisateur.
        """
        with self._verrou:
            canal = self._canal
        if canal is None:
            return False
        canal._interrompue = True
        logging.info("Interruption demandée (fermeture du canal SSH).")
        canal.close()
        return True
