# Linux end-to-end test (fake aion2.exe + fake server). From the project folder:
#   docker run --rm -v "$PWD:/app:ro" -v "$PWD/dev/linux_test:/t:ro" python:3.12-slim sh /t/run_user.sh
set -e
apt-get -qq update >/dev/null && apt-get -qq install -y sudo >/dev/null 2>&1
useradd -m player && echo 'player ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/player
cp "$(readlink -f "$(which python3)")" /tmp/aion2.exe && chmod 755 /tmp/aion2.exe
su player -c 'python3 /t/fake_server.py' & sleep 0.3
su player -c '/tmp/aion2.exe /t/fake_game.py' &
su player -c 'timeout 14 python3 -u /app/daeva_radar.py --console --no-sound' || true
sleep 3; echo "helper processes left: $(grep -l -a "capture-helpe[r]" /proc/[0-9]*/cmdline 2>/dev/null | grep -v self | wc -l)"
