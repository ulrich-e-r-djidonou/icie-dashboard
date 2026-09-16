#!/usr/bin/env python3
"""
Non-regression de l'echappement HTML du tableau de bord ICIE.

Les series, les libelles de pics et les notes evenementielles sont
regeneres toutes les deux semaines par le pipeline gtrends et injectes
dans le DOM via innerHTML. Si une interpolation perd son echappement, une
valeur du pipeline peut ecrire du HTML sur djidonou.com, meme origine que
tout le reste du site.

table() laisse volontairement passer du HTML : un appelant y insere un
bouton. L'echappement se fait donc au point d'appel, ce que ce test
verifie. Il lit index.html sans navigateur ni dependance.

    python scripts/test_echappement_html.py
"""

import io
import re
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

INDEX = Path(__file__).resolve().parent.parent / "index.html"

# Fonctions qui composent du HTML a partir des donnees du pipeline.
FONCTIONS = [
    "renderBars",
    "renderPeaks",
    "renderPeakDetail",
    "renderCorrelationTable",
    "table",
]

# Expressions dont la valeur ne peut pas porter de HTML, avec leur raison.
# Toute nouveaute doit etre justifiee ici plutot qu'ajoutee sans reflexion.
SURES = {
    # valueText passe par Number() puis Intl.NumberFormat : la sortie est
    # toujours un nombre formate, jamais une chaine du pipeline.
    "valueText(row.value)",
    "valueText(row.correlation, 3)",
    # largeur de barre calculee, bornee, arrondie
    "Math.max(3, (Number(row.value) / maxValue) * 100).toFixed(1)",
    # fragments de HTML deja composes par une fonction de cette liste
    "head",
    "body",
    "cell",
    # passthrough assume de table() : c'est sa raison d'etre
    'row.map((cell, index) => `<td class="${numericCols.has(index) ? "num" : ""}">${cell}</td>`).join("")',
    'numericCols.has(index) ? "num" : ""',
    # coercition numerique explicite
    "Number(row.rank)",
}

def interpolations(segment: str):
    """Rend les expressions ${...} de premier niveau, accolades equilibrees.

    Une interpolation peut en contenir d'autres (un .map qui compose du HTML).
    Une expression reguliere en `[^}]*` s'arrete au premier } et tronque
    l'expression, ce qui fait echouer le test sur du code pourtant correct.
    """
    i = 0
    while True:
        i = segment.find("${", i)
        if i < 0:
            return
        profondeur = 0
        for j in range(i + 1, len(segment)):
            if segment[j] == "{":
                profondeur += 1
            elif segment[j] == "}":
                profondeur -= 1
                if profondeur == 0:
                    yield segment[i + 2:j]
                    i = j + 1
                    break
        else:
            return


SUR = re.compile(r"^(escapeHtml|safeUrl|Number)\(")


def cellules_de_table(source: str):
    """Rend les expressions de cellule passees a table(), par site d'appel.

    table() n'echappe pas ses cellules, volontairement : un appelant y
    insere un bouton. L'echappement se fait donc a l'appel, et une cellule
    est un argument, pas une interpolation ${...} : sans ce controle, une
    valeur du pipeline passee brute en cellule n'est vue par personne.
    """
    i = 0
    while True:
        i = source.find("table(", i)
        if i < 0:
            return
        if i > 0 and (source[i - 1].isalnum() or source[i - 1] in "_."):
            i += 6
            continue
        # le tableau de cellules est le corps du .map( ... => [ ... ])
        debut = source.find("=> [", i)
        if debut < 0:
            return
        debut += 3
        profondeur = 0
        for j in range(debut, len(source)):
            if source[j] == "[":
                profondeur += 1
            elif source[j] == "]":
                profondeur -= 1
                if profondeur == 0:
                    yield source[debut + 1:j]
                    i = j
                    break
        else:
            return


def cellules(bloc_tableau: str):
    """Decoupe un corps de tableau en expressions, virgules de premier niveau."""
    profondeur = 0
    courant = ""
    litteral = None
    for ch in bloc_tableau:
        if litteral:
            courant += ch
            if ch == litteral:
                litteral = None
            continue
        if ch in "`\"'":
            litteral = ch
            courant += ch
            continue
        if ch in "([{":
            profondeur += 1
        elif ch in ")]}":
            profondeur -= 1
        if ch == "," and profondeur == 0:
            if courant.strip():
                yield courant.strip()
            courant = ""
            continue
        courant += ch
    if courant.strip():
        yield courant.strip()


def sure(expr: str):
    """Rend None si l'expression est sure, sinon la sous-expression fautive.

    Une expression composite (un .map qui compose du HTML) est sure quand
    toutes ses interpolations internes le sont : le texte litteral autour
    d'elles est du HTML ecrit par l'auteur, pas une donnee du pipeline.
    """
    e = expr.strip()
    if SUR.match(e) or e in SURES:
        return None
    internes = list(interpolations(e))
    if internes:
        for i in internes:
            faute = sure(i)
            if faute is not None:
                return faute
        return None
    return e


def corps(source: str, nom: str) -> str:
    debut = source.find("function " + nom)
    if debut < 0:
        raise SystemExit(f"ECHEC : fonction {nom} introuvable dans index.html")
    i = source.find("{", debut)
    profondeur = 0
    for j in range(i, len(source)):
        if source[j] == "{":
            profondeur += 1
        elif source[j] == "}":
            profondeur -= 1
            if profondeur == 0:
                return source[debut:j + 1]
    raise SystemExit(f"ECHEC : accolades desequilibrees dans {nom}")


def main() -> int:
    source = INDEX.read_text(encoding="utf-8")

    for helper in ("function escapeHtml", "function safeUrl"):
        if helper not in source:
            print(f"ECHEC : {helper.split()[1]}() a disparu de index.html")
            return 1

    if "^https?:" not in source:
        print("ECHEC : safeUrl n'impose plus le schema http(s)")
        return 1

    fautes = []
    total = 0
    for nom in FONCTIONS:
        seg = corps(source, nom)
        for expr in sorted(set(interpolations(seg))):
            total += 1
            faute = sure(expr)
            if faute is not None:
                fautes.append((nom, faute))

    nb_cellules = 0
    for bloc_tableau in cellules_de_table(source):
        for cellule in cellules(bloc_tableau):
            nb_cellules += 1
            faute = sure(cellule)
            if faute is not None:
                fautes.append(("cellule de table()", faute))

    print(f"{total} interpolations et {nb_cellules} cellules de tableau "
          f"examinees dans {len(FONCTIONS)} fonctions.")

    if fautes:
        print(f"\nECHEC : {len(fautes)} interpolation(s) sans echappement.")
        print("Une valeur du pipeline rendue brute permet d'ecrire du HTML sur")
        print("le site. Enveloppez-la dans escapeHtml(), dans safeUrl() s'il")
        print("s'agit d'une URL, ou dans Number() si elle doit etre numerique.")
        print("Si la valeur ne peut pas porter de HTML, ajoutez-la a SURES en")
        print("expliquant pourquoi.\n")
        for nom, e in fautes:
            print(f"  {nom} : ${{{e}}}")
        return 1

    print("Echappement complet : aucune donnee du pipeline n'entre brute dans le DOM.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
