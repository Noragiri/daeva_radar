"""Cube Finder: standalone Altgard Hidden Cube radar for AION2.

Passively copies the game's incoming TCP traffic with WinDivert (SNIFF | RECV_ONLY:
packets are never delayed, modified or injected), decodes the game's message
framing and LZ4 blocks, and shows Hidden Cube objects the server sent to your
client, relative to your character.

Only objects your client receives are visible - this is not a whole-map scan.
Requires Windows 10/11 x64, administrator rights and WinDivert 2.2
(WinDivert.dll + WinDivert64.sys)."""
