#!/usr/bin/env bash
# Arrête l'application lancée par start.sh
cd "$(dirname "$0")"

PORT="${PORT:-8001}"
PID_FILE=airtel_mock.pid

if [ -f "$PID_FILE" ]; then
    PID="$(cat "$PID_FILE")"
    if kill "$PID" 2>/dev/null; then
        # Laisse jusqu'à 10 s pour terminer les requêtes en cours
        for _ in $(seq 10); do
            kill -0 "$PID" 2>/dev/null || break
            sleep 1
        done
        kill -9 "$PID" 2>/dev/null || true
        echo "Arrêté (PID $PID)."
    else
        echo "Le processus $PID ne tournait plus."
    fi
    rm -f "$PID_FILE"
elif pkill -f "uvicorn app.main:app --host 0.0.0.0 --port $PORT"; then
    # Instance lancée à la main avec nohup, sans start.sh
    echo "Arrêté."
else
    echo "L'application ne tourne pas."
fi
