#!/usr/bin/env bash
# Arrête le bank-mock lancé par start_bank.sh
cd "$(dirname "$0")"

PORT="${PORT:-9000}"
PID_FILE=bank_mock.pid

if [ -f "$PID_FILE" ]; then
    PID="$(cat "$PID_FILE")"
    if kill "$PID" 2>/dev/null; then
        # Laisse jusqu'à 10 s pour terminer les requêtes en cours
        for _ in $(seq 10); do
            kill -0 "$PID" 2>/dev/null || break
            sleep 1
        done
        kill -9 "$PID" 2>/dev/null || true
        echo "Bank-mock arrêté (PID $PID)."
    else
        echo "Le processus $PID ne tournait plus."
    fi
    rm -f "$PID_FILE"
elif pkill -f "uvicorn bank_mock.main:app --host 0.0.0.0 --port $PORT"; then
    # Instance lancée à la main avec nohup, sans start_bank.sh
    echo "Bank-mock arrêté."
else
    echo "Le bank-mock ne tourne pas."
fi
