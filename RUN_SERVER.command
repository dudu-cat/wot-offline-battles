#!/bin/sh
cd "$(dirname "$0")" || exit 1

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 was not found."
    echo "Download it from https://www.python.org/downloads/"
    printf "Press Return to close..."
    read answer
    exit 1
fi

echo "Starting the World of Tanks 0.8.2 LAN server on TCP port 28782..."
python3 lan_battle_server.py --host 0.0.0.0 --port 28782
status=$?
echo
echo "The server has stopped with status $status."
printf "Press Return to close..."
read answer
exit "$status"
