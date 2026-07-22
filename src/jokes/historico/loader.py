"""Loader — Flujo C (Histórico).

Lee ficheros .md ya marcados con [REMATE]…[/REMATE] y [CHISTOIDE]…[/CHISTOIDE]
de una carpeta, con idempotencia por hash MD5 del documento.

`folder` y `state_path` se inyectan (nunca hardcodeados) para que sea testable
con `tmp_path`; el punto de entrada de producción (futuro `pipeline.py`) decide
qué carpeta real vigilar (`data/raw/historico/`, etc.).
"""
import hashlib
import json
from pathlib import Path


def _md5_de_fichero(path: Path) -> str:
    """Hash MD5 del contenido de un fichero, en streaming (no carga todo a RAM)."""
    hasher = hashlib.md5()
    with path.open("rb") as f:
        for bloque in iter(lambda: f.read(65536), b""):
            hasher.update(bloque)
    return hasher.hexdigest()


class Loader:
    """Lee ficheros .md marcados, con idempotencia por hash MD5 del documento.

    Procesa ficheros de una carpeta (`folder`), detectando nuevos y modificados
    por comparación de hash MD5 con un registro persistido en JSON (`state_path`).
    No modifica los ficheros originales en la carpeta.
    """

    def __init__(self, folder: Path, state_path: Path):
        self.folder = Path(folder)
        self.state_path = Path(state_path)

    def _cargar_estado(self) -> dict:
        """Carga el estado persistido del JSON."""
        if not self.state_path.exists():
            return {}
        return json.loads(self.state_path.read_text())

    def _guardar_estado(self, estado: dict) -> None:
        """Guarda el estado en el JSON."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(estado, indent=2, ensure_ascii=False))

    def load(self) -> list[dict]:
        """Lee los ficheros .md de `folder` y devuelve lista de documentos
        nuevos/modificados. Cada documento es un dict con `name` y `content`.
        Actualiza `state_path` con el hash MD5 de cada fichero procesado.

        No toca los ficheros originales en `folder` en ningún caso.
        """
        estado = self._cargar_estado()
        pendientes = []

        # Iterar sobre ficheros .md en la carpeta (ordenados para determinismo)
        for path in sorted(self.folder.iterdir()):
            if not path.is_file() or path.suffix.lower() != ".md":
                continue

            md5_actual = _md5_de_fichero(path)
            registro_previo = estado.get(path.name)

            # Idempotencia: si el hash es el mismo, se salta
            if registro_previo is not None and registro_previo.get("md5") == md5_actual:
                continue

            # Documento nuevo/modificado
            contenido = path.read_text(encoding="utf-8")
            pendientes.append({"name": path.name, "content": contenido})
            estado[path.name] = {"md5": md5_actual}

        self._guardar_estado(estado)
        return pendientes
