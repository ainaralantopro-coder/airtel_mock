#!/usr/bin/env bash
# Lance le bank-mock en arrière-plan. Port modifiable : PORT=9001 ./start_bank.sh
set -e
cd "$(dirname "$0")"

PORT="${PORT:-9000}"
PID_FILE=bank_mock.pid
LOG_FILE=bank_mock.log

if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Déjà lancé (PID $(cat "$PID_FILE")). Utilisez ./stop_bank.sh d'abord."
    exit 1
fi

nohup .venv/bin/uvicorn bank_mock.main:app --host 0.0.0.0 --port "$PORT" >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

sleep 2
if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "Bank-mock lancé sur le port $PORT (PID $(cat "$PID_FILE")). Logs : tail -f $LOG_FILE"
else
    rm -f "$PID_FILE"
    echo "Échec du démarrage. Dernières lignes de $LOG_FILE :"
    tail -n 20 "$LOG_FILE"
    exit 1
fi
