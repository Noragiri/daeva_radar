"""Following you between position packets with your summoned pet. The server doesn't send your position for
plain walking (your client reports it, encrypted): only teleports ('#6'), dashes ('*8') and aura ticks ('+8').
It does send where your pet is, and the pet follows you."""
import math
import time


class PositionFusion:
    """Your position for every feature: the last position packet, or, once you've moved on from it, where your
    summoned pet says you are. The pet follows you about 1-1.5 s behind, so its position projected PET_LEAD s
    ahead along its velocity is where you are, within ~4-6 m (live 2026-10-15; worst ~9-14 m right after
    dashes, when you jump ahead of it)."""
    PET_LEAD = 1.25  # s: how far ahead of the pet you are
    PET_MOVED_CM = 400  # the pet says you're this far from your last packet: you've moved, use it

    def __init__(self):
        self.updates = None  # tracker.position_updates last seen
        self.last = None  # last estimate
        self.source = None
        self.fix_error = None  # metres between the pet estimate and a packet when it arrived

    def apply(self, snap):
        """Replace snap['position'] with the best estimate; the packet's own is kept as snap['position_fix']."""
        packet = snap["position"]
        if snap.get("position_updates") != self.updates:  # a new packet: how far off was the pet estimate?
            self.updates = snap.get("position_updates")
            teleport = (snap.get("position_source") or "").startswith("#6")
            if packet and self.last is not None and self.source == "pet" and not teleport:
                self.fix_error = math.dist(self.last[:2], packet[:2]) / 100
        pos, source = packet, "packet" if packet else None
        guess = self.pet_guess(snap)
        if guess and packet and (snap["pet"]["time"] - (snap.get("position_time") or 0) > 0.5
                                 and math.dist(guess[:2], packet[:2]) > self.PET_MOVED_CM):
            pos, source = guess, "pet"
        self.last, self.source = (list(pos) if pos else None), source
        snap["position_fix"] = packet
        snap["position"] = self.last
        snap["follow"] = dict(source=source, fix_error=self.fix_error, pet=guess)
        return snap

    def pet_guess(self, snap):
        """Where your pet says you are: its last position, moved on by its velocity to now plus PET_LEAD
        (capped, as a pet that stopped sends nothing more). None without a pet."""
        pet = snap.get("pet")
        if not pet:
            return None
        vx, vy, _ = pet["velocity"]
        if vx == 0 and vy == 0:
            return list(pet["xyz"])
        ahead = min(max(0.0, time.time() - pet["time"]), 1.5) + self.PET_LEAD
        return [pet["xyz"][0] + vx * ahead, pet["xyz"][1] + vy * ahead, pet["xyz"][2]]
