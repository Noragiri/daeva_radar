# Linux end-to-end test (fake aion2.exe + fake server). From the project folder:
#   docker run --rm -v "$PWD:/app:ro" -v "$PWD/dev/linux_test:/t:ro" python:3.12-slim sh /t/run_root.sh
set -e
cp "$(readlink -f "$(which python3)")" /tmp/aion2.exe
python3 /t/fake_server.py & python3 -c 'import time; time.sleep(0.3)'
/tmp/aion2.exe /t/fake_game.py &
timeout 14 python3 -u /app/daeva_radar.py --console --no-sound || true
