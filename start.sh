#!/usr/bin/env bash
# Lance l'application en arrière-plan. Port modifiable : PORT=8002 ./start.sh
set -e
cd "$(dirname "$0")"

PORT="${PORT:-8001}"
PID_FILE=airtel_mock.pid
LOG_FILE=airtel_mock.log

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Déjà lancé (PID $(cat "$PID_FILE")). Utilisez ./stop.sh d'abord."
    exit 1
fi

nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "$PORT" >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

sleep 2
if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Lancé sur le port $PORT (PID $(cat "$PID_FILE")). Logs : tail -f $LOG_FILE"
else
    rm -f "$PID_FILE"
    echo "Échec du démarrage. Dernières lignes de $LOG_FILE :"
    tail -n 20 "$LOG_FILE"
    exit 1
fi
