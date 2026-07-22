"""DriveMonitor — Flujo A (Teoría).

Vigila una carpeta local (P18, 2026-07-22: la integración real con la API de
Google Drive se difiere) y entrega al siguiente paso de la cadena (Parser)
solo los ficheros nuevos o modificados desde la última ejecución.

Idempotencia por hash MD5 del contenido de cada fichero, persistido en un
`processed_files.json` (ver `src/theory/SPEC.md` §Idempotencia y versionado).

Nunca modifica, mueve ni borra el fichero original vigilado — ese material es
sagrado (ver CLAUDE.md, `docs/specs/00-overview.md` §1).
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


class DriveMonitor:
    """Vigila `folder` y detecta ficheros nuevos/modificados por hash MD5.

    `folder` y `state_path` se inyectan (nunca hardcodeados) para que sea
    testable con `tmp_path`; el punto de entrada de producción (futuro
    `pipeline.py`) decide qué carpeta real vigilar
    (`data/raw/books/`, `data/raw/notes/`).
    """

    def __init__(self, folder: Path, state_path: Path):
        self.folder = Path(folder)
        self.state_path = Path(state_path)

    def _cargar_estado(self) -> dict:
        if not self.state_path.exists():
            return {}
        return json.loads(self.state_path.read_text())

    def _guardar_estado(self, estado: dict) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(estado, indent=2, ensure_ascii=False))

    def scan(self) -> list[Path]:
        """Escanea `folder` y devuelve la lista de ficheros nuevos o
        modificados (hash MD5 distinto al registrado). Actualiza
        `processed_files.json` con el hash de cada fichero entregado.

        No toca los ficheros originales en `folder` en ningún caso.
        """
        estado = self._cargar_estado()
        pendientes = []

        for path in sorted(self.folder.iterdir()):
            if not path.is_file():
                continue
            md5_actual = _md5_de_fichero(path)
            registro_previo = estado.get(path.name)
            if registro_previo is not None and registro_previo.get("md5") == md5_actual:
                continue  # ya procesado, mismo hash: idempotencia
            pendientes.append(path)
            estado[path.name] = {"md5": md5_actual}

        self._guardar_estado(estado)
        return pendientes
