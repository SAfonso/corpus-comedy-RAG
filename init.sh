#!/bin/bash
# init.sh — verificación del entorno del Comedy Corpus Pipeline
set -e

echo "== Verificando entorno =="

command -v python3 >/dev/null || { echo "ERROR: python3 no encontrado"; exit 1; }
echo "python3: $(python3 --version)"

[ -f requirements.txt ] || { echo "ERROR: falta requirements.txt"; exit 1; }
python3 -m pip install -q -r requirements.txt
echo "dependencias: ok"

if [ ! -f .env ]; then
    echo "AVISO: no existe .env — copia .env.example y rellena las credenciales (Supabase, Telegram, Drive)"
fi

[ -d data/raw ] || echo "AVISO: no existe data/raw/ — el Flujo A no tendrá material de entrada"
[ -d tests/fixtures ] || echo "AVISO: no existe tests/fixtures/ — los tests requieren fixtures reales"

python3 -m pytest tests/unit/ -q || echo "AVISO: hay tests unitarios fallando"

echo "== Entorno verificado — modo EJECUTOR listo =="
