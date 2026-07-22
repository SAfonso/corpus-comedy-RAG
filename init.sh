#!/bin/bash
# init.sh — verificación del entorno del Comedy Corpus Pipeline
set -e

echo "== Verificando entorno =="

command -v python3 >/dev/null || { echo "ERROR: python3 no encontrado"; exit 1; }
echo "python3: $(python3 --version)"

[ -f requirements.txt ] || { echo "ERROR: falta requirements.txt"; exit 1; }

# venv obligatorio: el sistema puede ser "externally-managed" (PEP 668) y
# rechazar pip install directo contra el python3 global.
if [ ! -d .venv ]; then
    echo "Creando entorno virtual en .venv/ ..."
    python3 -m venv .venv
fi
VENV_PY=.venv/bin/python3
VENV_PIP=.venv/bin/pip

"$VENV_PIP" install -q --upgrade pip
"$VENV_PIP" install -q -r requirements.txt
echo "dependencias: ok (.venv/)"

if [ ! -f .env ]; then
    echo "AVISO: no existe .env — copia .env.example y rellena las credenciales (Supabase, Telegram, Drive)"
fi

[ -d data/raw ] || echo "AVISO: no existe data/raw/ — el Flujo A no tendrá material de entrada"
[ -d tests/fixtures ] || echo "AVISO: no existe tests/fixtures/ — los tests requieren fixtures reales"
command -v tesseract >/dev/null || echo "AVISO: tesseract no instalado en el sistema (apt install tesseract-ocr) — necesario como fallback OCR de markitdown (P17) para PDFs escaneados"

"$VENV_PY" -m pytest tests/unit/ -q || echo "AVISO: hay tests unitarios fallando"

echo "Comprobando git/gh (necesarios para integrator/NOTARIO y watchman/CENTINELA)..."
command -v git >/dev/null || echo "AVISO: git no está instalado"
command -v gh >/dev/null || echo "AVISO: gh no está instalado — instálalo antes de la primera tarea"
gh auth status >/dev/null 2>&1 || echo "AVISO: gh no está autenticado — ejecuta 'gh auth login' antes de la primera tarea"
git remote get-url origin >/dev/null 2>&1 || echo "AVISO: no hay remoto 'origin' configurado — configúralo antes de la primera tarea"

echo "== Entorno verificado — modo EJECUTOR listo =="
echo "Activa el venv con: source .venv/bin/activate"
