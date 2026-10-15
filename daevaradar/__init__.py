"""Daeva Radar: a radar for AION2 - Hidden Cubes, monsters, gathering nodes and alerts around you.

Passively copies the game's incoming TCP traffic with WinDivert (SNIFF | RECV_ONLY:
packets are never delayed, modified or injected), decodes the game's message
framing and LZ4 blocks, and shows what the server sent to your client (cubes,
monsters, gathering nodes, your pet), relative to your character.

Only objects your client receives are visible - this is not a whole-map scan.
Requires Windows 10/11 x64, administrator rights and WinDivert 2.2
(WinDivert.dll + WinDivert64.sys)."""
