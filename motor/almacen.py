"""Dónde se guardan los instrumentos: en el repo de GitHub (producción) o en disco (pruebas).

Si están definidas las variables de entorno GITHUB_TOKEN y GITHUB_REPO, todo se lee y se
escribe en ese repo mediante la API de GitHub (cada alta o cambio es un commit). Si no,
se usa la carpeta local ``data/``.
"""

from __future__ import annotations

import base64
import json
import os
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

RAIZ = Path(__file__).resolve().parents[1]
INDICE = "data/instrumentos.json"


class AlmacenLocal:
    modo = "local"

    def __init__(self, base: Path = RAIZ) -> None:
        self.base = Path(base)

    def version(self) -> str:
        p = self.base / INDICE
        return str(p.stat().st_mtime_ns) if p.exists() else "0"

    def leer_texto(self, ruta: str, version: str | None = None) -> str | None:
        p = self.base / ruta
        return p.read_text(encoding="utf-8") if p.exists() else None

    def guardar(self, archivos: dict[str, str], borrar: list[str], mensaje: str) -> None:
        for ruta, contenido in archivos.items():
            p = self.base / ruta
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(contenido, encoding="utf-8")
        for ruta in borrar:
            p = self.base / ruta
            if p.exists():
                p.unlink()


class AlmacenGitHub:
    """Lee y escribe en un repo de GitHub. Varios archivos se guardan en un solo commit."""

    modo = "github"

    def __init__(self, token: str, repo: str, rama: str = "main") -> None:
        self.repo = repo
        self.rama = rama
        self.api = f"https://api.github.com/repos/{repo}"
        self.s = requests.Session()
        self.s.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )

    def _ok(self, r: requests.Response) -> requests.Response:
        if r.status_code >= 400:
            raise RuntimeError(f"GitHub respondió {r.status_code}: {r.text[:300]}")
        return r

    def version(self) -> str:
        """SHA del último commit de la rama: cambia cada vez que se guarda algo."""
        r = self._ok(self.s.get(f"{self.api}/git/ref/heads/{self.rama}", timeout=20))
        return r.json()["object"]["sha"]

    def leer_texto(self, ruta: str, version: str | None = None) -> str | None:
        r = self.s.get(
            f"{self.api}/contents/{ruta}",
            params={"ref": version or self.rama},
            headers={"Accept": "application/vnd.github.raw+json"},
            timeout=30,
        )
        if r.status_code == 404:
            return None
        return self._ok(r).text

    def guardar(self, archivos: dict[str, str], borrar: list[str], mensaje: str) -> None:
        base_sha = self.version()
        base_tree = self._ok(self.s.get(f"{self.api}/git/commits/{base_sha}", timeout=20)).json()[
            "tree"
        ]["sha"]
        arbol = []
        for ruta, contenido in archivos.items():
            blob = self._ok(
                self.s.post(
                    f"{self.api}/git/blobs",
                    json={
                        "content": base64.b64encode(contenido.encode("utf-8")).decode(),
                        "encoding": "base64",
                    },
                    timeout=60,
                )
            ).json()["sha"]
            arbol.append({"path": ruta, "mode": "100644", "type": "blob", "sha": blob})
        for ruta in borrar:
            arbol.append({"path": ruta, "mode": "100644", "type": "blob", "sha": None})
        tree = self._ok(
            self.s.post(
                f"{self.api}/git/trees", json={"base_tree": base_tree, "tree": arbol}, timeout=60
            )
        ).json()["sha"]
        commit = self._ok(
            self.s.post(
                f"{self.api}/git/commits",
                json={"message": mensaje, "tree": tree, "parents": [base_sha]},
                timeout=30,
            )
        ).json()["sha"]
        self._ok(
            self.s.patch(
                f"{self.api}/git/refs/heads/{self.rama}", json={"sha": commit}, timeout=30
            )
        )


def crear_almacen():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPO", "").strip()
    if token and repo:
        return AlmacenGitHub(token, repo, os.environ.get("GITHUB_BRANCH", "main").strip() or "main")
    return AlmacenLocal(Path(os.environ.get("DATA_BASE", RAIZ)))


# --- formato de los archivos -------------------------------------------------------------


def leer_indice(texto: str | None) -> list[dict]:
    if not texto:
        return []
    return json.loads(texto)


def indice_a_texto(instrumentos: list[dict]) -> str:
    return json.dumps(instrumentos, ensure_ascii=False, indent=2) + "\n"


def serie_a_csv(s: pd.Series, columna: str) -> str:
    df = s.rename(columna).to_frame()
    df.index = pd.to_datetime(df.index).strftime("%Y-%m-%d")
    df.index.name = "fecha"
    return df.to_csv(float_format="%.10g")


def csv_a_serie(texto: str | None) -> pd.Series:
    if not texto:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    df = pd.read_csv(StringIO(texto), parse_dates=["fecha"]).set_index("fecha")
    return df.iloc[:, 0].astype(float).sort_index()
