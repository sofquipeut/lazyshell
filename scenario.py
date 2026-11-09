# -*- coding: utf-8 -*-
"""
Scénarios : procédures guidées pas à pas, décrites dans un fichier TOML
lisible et modifiable à la main (voir scenarios/exemple.toml).

Ce module ne connaît pas wx : chargement et validation du fichier,
contrôle du résultat d'une étape, journal de l'exécution, rapport à
copier. L'enchaînement des étapes et les boîtes de dialogue vivent dans
scenario_ui.py.

TOML plutôt que JSON ou YAML : le lecteur est inclus dans Python
(tomllib, 3.11 et plus), on peut y écrire des commentaires, et une
commande se recopie telle quelle entre trois apostrophes, sans échapper
ni guillemets ni barres obliques inverses.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Choix proposés quand une étape pose problème (si_echec). « arreter »
# est toujours proposé, même absent du fichier : on doit toujours pouvoir
# sortir.
REESSAYER = "reessayer"
CONTINUER = "continuer"
ARRETER = "arreter"
ALTERNATIVE = "alternative"
CHOIX_SIMPLES = (REESSAYER, CONTINUER, ARRETER)
SI_ECHEC_DEFAUT = [REESSAYER, ARRETER]

# si_bon : étape suivante dans l'ordre du fichier, fin du scénario, ou
# l'identifiant d'une autre étape.
SUIVANTE = "suivante"
FIN = "fin"

# {{nom}} dans une commande ou un résultat attendu. Un identifiant seul,
# en minuscules : les gabarits de docker (« {{.Id}} », « {{index
# .RepoDigests 0}} ») ne ressemblent jamais à ça et passent intacts.
MOTIF_VARIABLE = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
MOTIF_IDENTIFIANT = re.compile(r"^[a-z0-9_-]+$")

# Variables toujours disponibles. La date est figée au lancement du
# scénario : un $(date) côté serveur changerait si l'opération passait
# minuit, et le nom d'une sauvegarde ne correspondrait plus.
VARIABLES_FOURNIES = ("date",)

CLES_SCENARIO = {"nom", "description", "profil", "variables", "etape"}
CLES_ETAPE = {
    "id", "titre", "commande", "question", "destructive", "attendu",
    "si_bon", "si_echec", "hors_sequence", "sortie_au_journal", "memoriser",
    "relire",
}
CLES_ATTENDU = {
    "code", "contient", "interdit", "motif", "nombre_min", "nombre_max",
    "respecter_casse", "apres",
}

# Horodatage en tête de ligne d'un journal (« 2026-11-03 01:13:23,841 »
# ou « 2026-11-03T01:13:23 ») : sert au critère apres.
MOTIF_HORODATAGE = re.compile(r"^(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)")

# Masquage dans le journal : une valeur qui suit un mot comme password ou
# token. Les scénarios ne transportent aucun secret (les mots de passe
# SSH restent dans le Gestionnaire d'identifiants Windows), c'est un
# filet pour une sortie de commande qui en afficherait un.
MOTIF_SECRET = re.compile(
    r"(?i)\b(password|passwd|mot de passe|passphrase|token|secret|api[_-]?key)"
    r"(\s*[=:]\s*)(\S+)"
)
# Seize caractères au moins : un vrai jeton est long, alors que « bearer
# token » dans une phrase (une description de plugin) ne doit pas être
# masqué.
MOTIF_BEARER = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]{16,})")
MASQUE = "(masqué)"


class ErreurScenario(Exception):
    """Fichier de scénario illisible ou incohérent. Le message est fait
    pour être lu tel quel à l'utilisateur."""


@dataclass
class Attendu:
    code: list[int] = field(default_factory=lambda: [0])
    contient: list[str] = field(default_factory=list)
    interdit: list[str] = field(default_factory=list)
    motif: str = ""
    nombre_min: int | None = None
    nombre_max: int | None = None
    respecter_casse: bool = False
    # Heure « AAAA-MM-JJ HH:MM:SS » (souvent une valeur mémorisée) : seules
    # les lignes horodatées à partir de cette heure sont examinées, avec
    # leurs lignes de suite sans horodatage. Pour lire un journal sans
    # se fier à un ancien démarrage.
    apres: str = ""


@dataclass
class Etape:
    id: str
    titre: str
    commande: str = ""
    # Étape sans commande : une question posée à l'utilisateur (un
    # contrôle à faire à la main), Oui vaut OK, Non vaut Problème.
    question: str = ""
    destructive: bool = False
    attendu: Attendu = field(default_factory=Attendu)
    si_bon: str = SUIVANTE
    # Chaînes de CHOIX_SIMPLES, ou ("alternative", identifiant).
    si_echec: list = field(default_factory=lambda: list(SI_ECHEC_DEFAUT))
    # Jamais atteinte dans l'ordre normal, seulement comme alternative.
    hors_sequence: bool = False
    sortie_au_journal: bool = True
    # Sortie à lire par l'utilisateur avant de continuer, même si l'étape
    # est OK (un audit, une liste d'images annoncées par un pull...).
    relire: bool = False
    # Valeurs lues dans la sortie, réutilisables plus loin en {{nom}} :
    # nom -> expression régulière. Le premier groupe entre parenthèses est
    # gardé s'il y en a un, sinon tout le texte trouvé.
    memoriser: dict[str, str] = field(default_factory=dict)


@dataclass
class Scenario:
    chemin: Path
    nom: str
    description: str = ""
    # Nom du profil SSH sur lequel ce scénario doit tourner : garde-fou
    # contre une procédure lancée sur le mauvais serveur. Vide : n'importe
    # quelle session, locale comprise.
    profil: str = ""
    variables: dict[str, str] = field(default_factory=dict)
    etapes: list[Etape] = field(default_factory=list)

    def index(self, identifiant: str) -> int:
        for i, etape in enumerate(self.etapes):
            if etape.id == identifiant:
                return i
        raise KeyError(identifiant)

    def etapes_en_sequence(self) -> list[Etape]:
        return [e for e in self.etapes if not e.hors_sequence]


# --------------------------------------------------------------------------
# Chargement
# --------------------------------------------------------------------------

def _texte_liste(valeur, ou: str) -> list[str]:
    """Accepte une chaîne seule ou une liste de chaînes."""
    if isinstance(valeur, str):
        return [valeur]
    if isinstance(valeur, list) and all(isinstance(v, str) for v in valeur):
        return list(valeur)
    raise ErreurScenario(f"{ou} : une chaîne ou une liste de chaînes est attendue.")


def _verifier_cles(donnees: dict, permises: set[str], ou: str) -> None:
    inconnues = sorted(set(donnees) - permises)
    if inconnues:
        raise ErreurScenario(
            f"{ou} : champ inconnu « {inconnues[0]} ». "
            f"Champs possibles : {', '.join(sorted(permises))}."
        )


def _lire_attendu(donnees, ou: str) -> Attendu:
    if not isinstance(donnees, dict):
        raise ErreurScenario(f"{ou} : attendu doit être une table.")
    _verifier_cles(donnees, CLES_ATTENDU, ou)
    attendu = Attendu()
    if "code" in donnees:
        code = donnees["code"]
        if isinstance(code, int) and not isinstance(code, bool):
            attendu.code = [code]
        elif isinstance(code, list) and code and all(
            isinstance(c, int) and not isinstance(c, bool) for c in code
        ):
            attendu.code = list(code)
        else:
            raise ErreurScenario(f"{ou} : code doit être un nombre ou une liste de nombres.")
    if "contient" in donnees:
        attendu.contient = _texte_liste(donnees["contient"], f"{ou}, contient")
    if "interdit" in donnees:
        attendu.interdit = _texte_liste(donnees["interdit"], f"{ou}, interdit")
    if "motif" in donnees:
        if not isinstance(donnees["motif"], str):
            raise ErreurScenario(f"{ou} : motif doit être une chaîne.")
        attendu.motif = donnees["motif"]
    for cle in ("nombre_min", "nombre_max"):
        if cle in donnees:
            valeur = donnees[cle]
            if not isinstance(valeur, int) or isinstance(valeur, bool) or valeur < 0:
                raise ErreurScenario(f"{ou} : {cle} doit être un nombre positif.")
            if not attendu.motif:
                raise ErreurScenario(f"{ou} : {cle} n'a de sens qu'avec un motif.")
            setattr(attendu, cle, valeur)
    if "respecter_casse" in donnees:
        if not isinstance(donnees["respecter_casse"], bool):
            raise ErreurScenario(f"{ou} : respecter_casse doit valoir true ou false.")
        attendu.respecter_casse = donnees["respecter_casse"]
    if "apres" in donnees:
        if not isinstance(donnees["apres"], str) or not donnees["apres"].strip():
            raise ErreurScenario(f"{ou} : apres doit être une heure, ou une valeur mémorisée.")
        attendu.apres = donnees["apres"].strip()
    return attendu


def lignes_apres(sortie: str, seuil: str) -> str:
    """Garde les lignes horodatées à partir de seuil, et les lignes sans
    horodatage qui les suivent (suite d'un message sur plusieurs lignes).
    Comparaison de texte : le format AAAA-MM-JJ HH:MM:SS s'ordonne de
    lui-même."""
    trouve = MOTIF_HORODATAGE.match(seuil.strip())
    if trouve is None:
        return ""
    seuil_normal = f"{trouve.group(1)} {trouve.group(2)}"
    gardees, garder = [], False
    for ligne in sortie.splitlines():
        horodatage = MOTIF_HORODATAGE.match(ligne)
        if horodatage:
            garder = f"{horodatage.group(1)} {horodatage.group(2)}" >= seuil_normal
        if garder:
            gardees.append(ligne)
    return "\n".join(gardees)


def _lire_si_echec(valeur, ou: str) -> list:
    if not isinstance(valeur, list):
        raise ErreurScenario(f"{ou} : si_echec doit être une liste.")
    choix = []
    for element in valeur:
        if isinstance(element, str) and element in CHOIX_SIMPLES:
            choix.append(element)
        elif (
            isinstance(element, dict) and set(element) == {ALTERNATIVE}
            and isinstance(element[ALTERNATIVE], str)
        ):
            choix.append((ALTERNATIVE, element[ALTERNATIVE]))
        else:
            raise ErreurScenario(
                f"{ou} : choix inconnu dans si_echec : {element!r}. "
                "Possibles : \"reessayer\", \"continuer\", \"arreter\", "
                "{ alternative = \"identifiant\" }."
            )
    return choix


def _lire_etape(donnees, numero: int) -> Etape:
    ou = f"Étape {numero}"
    if not isinstance(donnees, dict):
        raise ErreurScenario(f"{ou} : table [[etape]] attendue.")
    identifiant = donnees.get("id")
    if isinstance(identifiant, str):
        ou = f"Étape {numero} ({identifiant})"
    _verifier_cles(donnees, CLES_ETAPE, ou)
    if not isinstance(identifiant, str) or not MOTIF_IDENTIFIANT.match(identifiant):
        raise ErreurScenario(
            f"{ou} : id manquant ou invalide (minuscules, chiffres, tiret, souligné)."
        )
    titre = donnees.get("titre")
    if not isinstance(titre, str) or not titre.strip():
        raise ErreurScenario(f"{ou} : titre manquant.")
    commande = donnees.get("commande", "")
    question = donnees.get("question", "")
    if not isinstance(commande, str) or not isinstance(question, str):
        raise ErreurScenario(f"{ou} : commande et question doivent être des chaînes.")
    commande, question = commande.strip(), question.strip()
    if bool(commande) == bool(question):
        raise ErreurScenario(f"{ou} : il faut soit une commande, soit une question, pas les deux.")

    etape = Etape(id=identifiant, titre=titre.strip(), commande=commande, question=question)
    for cle in ("destructive", "hors_sequence", "sortie_au_journal", "relire"):
        if cle in donnees:
            if not isinstance(donnees[cle], bool):
                raise ErreurScenario(f"{ou} : {cle} doit valoir true ou false.")
            setattr(etape, cle, donnees[cle])
    if etape.relire and question:
        raise ErreurScenario(f"{ou} : une question n'a pas de sortie à relire.")
    if "attendu" in donnees:
        if question:
            raise ErreurScenario(f"{ou} : une question n'a pas de résultat attendu.")
        etape.attendu = _lire_attendu(donnees["attendu"], ou)
    if "si_bon" in donnees:
        if not isinstance(donnees["si_bon"], str):
            raise ErreurScenario(f"{ou} : si_bon doit être une chaîne.")
        etape.si_bon = donnees["si_bon"]
    if "si_echec" in donnees:
        etape.si_echec = _lire_si_echec(donnees["si_echec"], ou)
    if "memoriser" in donnees:
        if question:
            raise ErreurScenario(f"{ou} : une question ne peut rien mémoriser.")
        table = donnees["memoriser"]
        if not isinstance(table, dict) or not table:
            raise ErreurScenario(f"{ou} : memoriser doit être une table nom = 'motif'.")
        for nom, motif in table.items():
            if not MOTIF_IDENTIFIANT.match(nom) or "-" in nom:
                raise ErreurScenario(
                    f"{ou} : nom à mémoriser invalide « {nom} » (minuscules, chiffres, souligné)."
                )
            if not isinstance(motif, str) or not motif:
                raise ErreurScenario(f"{ou} : memoriser, {nom} : un motif est attendu.")
            try:
                re.compile(motif)
            except re.error as erreur:
                raise ErreurScenario(f"{ou} : memoriser, {nom} : motif invalide ({erreur}).") from erreur
        etape.memoriser = dict(table)
    return etape


def _noms_utilises(texte: str) -> list[str]:
    return MOTIF_VARIABLE.findall(texte)


def charger_scenario(chemin: Path) -> Scenario:
    """Lit et valide un fichier de scénario. Toute incohérence (champ mal
    orthographié, étape introuvable, variable inconnue) est signalée au
    chargement, jamais au milieu d'une procédure déjà entamée."""
    try:
        # utf-8-sig : le Bloc-notes peut ajouter une marque d'ordre
        # d'octets en tête de fichier, que tomllib refuse.
        texte = chemin.read_text(encoding="utf-8-sig")
    except OSError as erreur:
        raise ErreurScenario(f"Fichier illisible : {erreur}") from erreur
    except UnicodeDecodeError as erreur:
        raise ErreurScenario(
            "Le fichier n'est pas enregistré en UTF-8. Dans le Bloc-notes : "
            "Fichier, Enregistrer sous, Encodage UTF-8."
        ) from erreur
    try:
        donnees = tomllib.loads(texte)
    except tomllib.TOMLDecodeError as erreur:
        detail = str(erreur)
        position = re.search(r"line (\d+), column (\d+)", detail)
        ou = (
            f"ligne {position.group(1)}, colonne {position.group(2)}"
            if position else "position inconnue"
        )
        raise ErreurScenario(
            f"Fichier mal formé, {ou}. Détail technique : {detail}"
        ) from erreur

    _verifier_cles(donnees, CLES_SCENARIO, "Scénario")
    nom = donnees.get("nom")
    if not isinstance(nom, str) or not nom.strip():
        raise ErreurScenario("Scénario : nom manquant.")
    scenario = Scenario(chemin=chemin, nom=nom.strip())
    for cle in ("description", "profil"):
        if cle in donnees:
            if not isinstance(donnees[cle], str):
                raise ErreurScenario(f"Scénario : {cle} doit être une chaîne.")
            setattr(scenario, cle, donnees[cle].strip())

    variables = donnees.get("variables", {})
    if not isinstance(variables, dict):
        raise ErreurScenario("Scénario : [variables] doit être une table.")
    for cle, valeur in variables.items():
        if not MOTIF_IDENTIFIANT.match(cle) or "-" in cle:
            raise ErreurScenario(
                f"Variable « {cle} » : nom invalide (minuscules, chiffres, souligné)."
            )
        if cle in VARIABLES_FOURNIES:
            raise ErreurScenario(f"Variable « {cle} » : nom réservé.")
        if not isinstance(valeur, str):
            raise ErreurScenario(f"Variable « {cle} » : une chaîne est attendue.")
        scenario.variables[cle] = valeur

    etapes = donnees.get("etape", [])
    if not isinstance(etapes, list) or not etapes:
        raise ErreurScenario("Scénario : aucune étape ([[etape]]).")
    scenario.etapes = [_lire_etape(e, i) for i, e in enumerate(etapes, start=1)]
    if not scenario.etapes_en_sequence():
        raise ErreurScenario("Scénario : toutes les étapes sont hors séquence.")

    identifiants = [e.id for e in scenario.etapes]
    for etape in scenario.etapes:
        if identifiants.count(etape.id) > 1:
            raise ErreurScenario(f"Identifiant d'étape en double : « {etape.id} ».")

    # Chaque variable ne peut utiliser que celles définies avant elle.
    connus = set(VARIABLES_FOURNIES)
    for cle, valeur in scenario.variables.items():
        for utilise in _noms_utilises(valeur):
            if utilise not in connus:
                raise ErreurScenario(
                    f"Variable « {cle} » : utilise « {utilise} », inconnue ou définie plus bas."
                )
        connus.add(cle)

    # Les valeurs mémorisées sont connues partout dans les étapes (une
    # alternative peut sauter n'importe où) ; celles qui ne le sont pas
    # encore au moment de jouer une étape sont signalées à ce moment-là.
    for etape in scenario.etapes:
        for nom in etape.memoriser:
            if nom in VARIABLES_FOURNIES or nom in scenario.variables:
                raise ErreurScenario(
                    f"Étape {etape.id} : « {nom} » est déjà une variable, "
                    "choisissez un autre nom à mémoriser."
                )
            connus.add(nom)

    for numero, etape in enumerate(scenario.etapes, start=1):
        ou = f"Étape {numero} ({etape.id})"
        a = etape.attendu
        textes = [etape.commande, etape.question, a.motif, a.apres, *a.contient, *a.interdit]
        for texte in textes:
            for utilise in _noms_utilises(texte):
                if utilise not in connus:
                    raise ErreurScenario(f"{ou} : variable inconnue « {utilise} ».")
        if a.motif and not MOTIF_VARIABLE.search(a.motif):
            try:
                re.compile(a.motif)
            except re.error as erreur:
                raise ErreurScenario(f"{ou} : motif invalide ({erreur}).") from erreur
        if a.nombre_min is not None and a.nombre_max is not None and a.nombre_min > a.nombre_max:
            raise ErreurScenario(f"{ou} : nombre_min dépasse nombre_max.")
        if etape.si_bon not in (SUIVANTE, FIN) and etape.si_bon not in identifiants:
            raise ErreurScenario(f"{ou} : si_bon vise une étape inconnue « {etape.si_bon} ».")
        for choix in etape.si_echec:
            if isinstance(choix, tuple):
                if choix[1] not in identifiants:
                    raise ErreurScenario(
                        f"{ou} : alternative vers une étape inconnue « {choix[1]} »."
                    )
                if choix[1] == etape.id:
                    raise ErreurScenario(f"{ou} : une étape ne peut pas être sa propre alternative.")
    return scenario


def lister_scenarios(dossier: Path) -> list[Path]:
    if not dossier.is_dir():
        return []
    return sorted(dossier.glob("*.toml"), key=lambda p: p.name.lower())


# --------------------------------------------------------------------------
# Variables et contrôle du résultat
# --------------------------------------------------------------------------

def variables_initiales(
    scenario: Scenario, maintenant: datetime, date: str | None = None,
    memorisees: dict[str, str] | None = None,
) -> dict[str, str]:
    """date et memorisees servent à la reprise : on retrouve la date du
    premier lancement (le nom de la sauvegarde en dépend) et les valeurs
    déjà lues avant l'interruption."""
    valeurs = {"date": date or maintenant.strftime("%Y%m%d")}
    for cle, valeur in scenario.variables.items():
        valeurs[cle] = substituer(valeur, valeurs)
    valeurs.update(memorisees or {})
    return valeurs


def noms_memorises(scenario: Scenario) -> set[str]:
    return {nom for etape in scenario.etapes for nom in etape.memoriser}


def variables_manquantes(texte: str, valeurs: dict[str, str]) -> list[str]:
    """Noms encore sans valeur : une valeur à mémoriser par une étape qui
    n'a pas encore été jouée."""
    return [nom for nom in MOTIF_VARIABLE.findall(texte) if nom not in valeurs]


def extraire(motif: str, sortie: str) -> str | None:
    """Valeur à mémoriser : le premier groupe s'il y en a un, sinon tout
    le texte trouvé. None si le motif est introuvable."""
    trouve = re.search(motif, sortie, re.MULTILINE)
    if trouve is None:
        return None
    valeur = trouve.group(1) if trouve.groups() else trouve.group(0)
    return (valeur or "").strip() or None


def substituer(texte: str, valeurs: dict[str, str]) -> str:
    # Une variable inconnue a déjà été refusée au chargement : celles qui
    # restent ici sont laissées telles quelles plutôt que de lever.
    return MOTIF_VARIABLE.sub(lambda m: valeurs.get(m.group(1), m.group(0)), texte)


def verifier(attendu: Attendu, sortie: str, code: int, valeurs: dict[str, str]) -> list[str]:
    """Liste des problèmes constatés, en phrases à lire telles quelles.
    Vide : l'étape est bonne."""
    problemes = []
    if code not in attendu.code:
        attendus = " ou ".join(str(c) for c in attendu.code)
        problemes.append(f"code de retour {code} au lieu de {attendus}")

    if attendu.apres:
        seuil = substituer(attendu.apres, valeurs)
        if MOTIF_HORODATAGE.match(seuil.strip()) is None:
            problemes.append(f"heure de référence inconnue ou invalide : {seuil}")
            return problemes
        sortie = lignes_apres(sortie, seuil)

    def normaliser(t: str) -> str:
        return t if attendu.respecter_casse else t.casefold()

    texte = normaliser(sortie)
    for morceau in attendu.contient:
        morceau = substituer(morceau, valeurs)
        if normaliser(morceau) not in texte:
            problemes.append(f"texte attendu absent : {morceau}")
    for morceau in attendu.interdit:
        morceau = substituer(morceau, valeurs)
        if normaliser(morceau) in texte:
            problemes.append(f"texte interdit présent : {morceau}")

    if attendu.motif:
        motif = substituer(attendu.motif, valeurs)
        drapeaux = re.MULTILINE | (0 if attendu.respecter_casse else re.IGNORECASE)
        try:
            nombre = len(re.findall(motif, sortie, drapeaux))
        except re.error as erreur:
            problemes.append(f"motif invalide : {erreur}")
        else:
            minimum = attendu.nombre_min if attendu.nombre_min is not None else 1
            if nombre < minimum:
                if nombre == 0:
                    problemes.append(f"motif introuvable : {motif}")
                else:
                    problemes.append(
                        f"motif trouvé {nombre} fois au lieu d'au moins {minimum} : {motif}"
                    )
            if attendu.nombre_max is not None and nombre > attendu.nombre_max:
                problemes.append(
                    f"motif trouvé {nombre} fois au lieu d'au plus {attendu.nombre_max} : {motif}"
                )
    return problemes


# --------------------------------------------------------------------------
# État de reprise
# --------------------------------------------------------------------------
#
# Un fichier JSON par scénario, réécrit avant chaque étape : si LazyShell
# s'arrête (onglet fermé, connexion perdue, plantage), on sait quelle étape
# rejouer et avec quelles valeurs. Effacé quand le scénario se termine.
# Jamais en simulation : rien n'y a été fait pour de vrai.

def chemin_etat(dossier: Path, scenario: Scenario) -> Path:
    return dossier / f"{scenario.chemin.stem}.json"


def enregistrer_etat(
    chemin: Path, prochaine: str, valeurs: dict[str, str],
    memorises: set[str], retour: str | None, lance_le: datetime,
) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    donnees = {
        "prochaine": prochaine,
        "date": valeurs.get("date", ""),
        "memorise": {k: v for k, v in valeurs.items() if k in memorises},
        "retour": retour,
        "lance_le": lance_le.isoformat(timespec="seconds"),
        "interrompu_le": datetime.now().isoformat(timespec="seconds"),
    }
    # Écriture dans un fichier à part puis remplacement : un arrêt en plein
    # milieu ne laisse jamais un état à moitié écrit.
    provisoire = chemin.with_suffix(".tmp")
    provisoire.write_text(json.dumps(donnees, ensure_ascii=False, indent=2), encoding="utf-8")
    provisoire.replace(chemin)


def lire_etat(chemin: Path, scenario: Scenario) -> dict | None:
    """État enregistré, ou None s'il n'y en a pas, s'il est illisible, ou
    s'il désigne une étape qui n'existe plus (fichier modifié depuis)."""
    try:
        donnees = json.loads(chemin.read_text(encoding="utf-8"))
        scenario.index(donnees["prochaine"])
        if donnees.get("retour"):
            scenario.index(donnees["retour"])
        if not isinstance(donnees.get("memorise", {}), dict):
            return None
        return donnees
    except (OSError, ValueError, KeyError, TypeError):
        return None


def effacer_etat(chemin: Path) -> None:
    try:
        chemin.unlink()
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------
# Journal et rapport
# --------------------------------------------------------------------------

def masquer_secrets(texte: str) -> str:
    texte = MOTIF_SECRET.sub(lambda m: m.group(1) + m.group(2) + MASQUE, texte)
    return MOTIF_BEARER.sub(lambda m: m.group(1) + MASQUE, texte)


def rapport(
    scenario: Scenario, libelle: str, etape: Etape, commande: str,
    sortie: str, code: int, problemes: list[str],
) -> str:
    """Texte à coller dans une conversation avec Claude : assez de
    contexte pour comprendre l'échec sans rien avoir à décrire. libelle
    situe l'étape (« Étape 3 sur 12 », « Étape alternative »)."""
    lignes = [
        f"Scénario : {scenario.nom} ({scenario.chemin.name})",
        f"{libelle} : {etape.titre} (id {etape.id})",
        f"Commande : {commande}",
        f"Code de retour : {code}",
        "Problèmes : " + "; ".join(problemes),
        "Sortie :",
        sortie if sortie else "(aucune sortie)",
    ]
    return masquer_secrets("\n".join(lignes)) + "\n"


class JournalScenario:
    """Fichier texte daté, un par exécution, écrit au fil de l'eau pour
    qu'un plantage laisse quand même la trace de ce qui a été fait."""

    def __init__(self, dossier: Path, scenario: Scenario, simulation: bool, session: str):
        dossier.mkdir(parents=True, exist_ok=True)
        maintenant = datetime.now()
        suffixe = "_simulation" if simulation else ""
        self.chemin = dossier / (
            f"{maintenant:%Y-%m-%d_%H%M%S}_{scenario.chemin.stem}{suffixe}.txt"
        )
        self._fichier = self.chemin.open("w", encoding="utf-8")
        self.ecrire(f"Scénario : {scenario.nom} ({scenario.chemin.name})")
        self.ecrire(f"Début : {maintenant:%d/%m/%Y %H:%M:%S}")
        self.ecrire("Mode : simulation, rien n'est exécuté" if simulation else "Mode : réel")
        self.ecrire(f"Session : {session}")

    def ecrire(self, texte: str = "") -> None:
        if self._fichier is None:
            return
        self._fichier.write(masquer_secrets(texte) + "\n")
        self._fichier.flush()

    def evenement(self, texte: str) -> None:
        self.ecrire(f"[{datetime.now():%H:%M:%S}] {texte}")

    def fermer(self, conclusion: str) -> None:
        self.ecrire()
        self.ecrire(f"Fin : {datetime.now():%d/%m/%Y %H:%M:%S}, {conclusion}")
        if self._fichier is not None:
            self._fichier.close()
            self._fichier = None
