#!/usr/bin/env python3
"""Small LAN battle server for the supported offline LAN clients.

This is deliberately a separate, dependency-free process.  It is the first
network slice, not an implementation of the original BigWorld server
protocol.  Clients connect with the companion network mod and exchange
newline-delimited JSON messages.

The server owns the shared player state, relayed positions, team assignment
and validated hit/health events.  The existing client remains responsible for
rendering the original map/tank assets, resolving map/armor collisions and the
local garage.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import os
import sys
import socket
import socketserver
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_PORT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_CLIENT_SCRIPT_ROOT = os.path.join(
    _PORT_ROOT, 'src', 'res', 'scripts', 'client')
if _CLIENT_SCRIPT_ROOT not in sys.path:
    sys.path.insert(0, _CLIENT_SCRIPT_ROOT)

from server_bot_ai import BotPlanner
from gui.mods.offline_lan_0922.ai.maps import get_tactical_map
from gui.mods.offline_lan_0922.ai.maps_0922_extra import (
    TACTICAL_MAPS_0922_EXTRA as _MAPS_0922_DATA,
)
from gui.mods.offline_lan_0922.navigation_graph_schema import (
    SUPPORTED_MAPS as _SUPPORTED_MAPS_0922,
)


PROTOCOL_VERSION = 5
TICK_HZ = 30.0
RESULT_RESET_SECONDS = 5.0
PREBATTLE_SECONDS = 15.0
BATTLE_DURATION_SECONDS = 900.0
RAM_COOLDOWN_SECONDS = 0.75
BOT_FIRE_DURATION_SECONDS = 10.0
BOT_FIRE_TICK_SECONDS = 1.0
MAX_LINE_BYTES = 256 * 1024
DEFAULT_MAP = "server_random"
CLIENT_BUILD_082 = "wot-0.8.2"
CLIENT_BUILD_0922 = "wot-0.9.22.0.1-cn-1513"
MAP_POOL_082 = (
    "01_karelia",
    "02_malinovka",
    "03_campania",
    "04_himmelsdorf",
    "05_prohorovka",
    "06_ensk",
    "07_lakeville",
    "08_ruinberg",
    "10_hills",
    "11_murovanka",
    "13_erlenberg",
    "14_siegfried_line",
    "15_komarin",
    "17_munchen",
    "18_cliff",
    "19_monastery",
    "22_slough",
    "23_westfeld",
    "28_desert",
    "29_el_hallouf",
    "31_airfield",
    "33_fjord",
    "34_redshire",
    "35_steppes",
    "36_fishing_bay",
    "37_caucasus",
    "38_mannerheim_line",
    "39_crimea",
    "42_north_america",
    "44_north_america",
    "45_north_america",
    "47_canada_a",
    "51_asia",
)
MAP_POOL_0922 = tuple(_SUPPORTED_MAPS_0922)
MAP_POOL = MAP_POOL_0922
CLIENT_MAP_POOLS = {
    CLIENT_BUILD_082: MAP_POOL_082,
    CLIENT_BUILD_0922: MAP_POOL_0922,
}
CLIENT_DEFAULT_VEHICLES = {
    CLIENT_BUILD_082: "ussr:MS-1",
    CLIENT_BUILD_0922: "ussr:R11_MS-1",
}
ALL_MAP_POOL = tuple(sorted(set(MAP_POOL_082 + MAP_POOL_0922)))
BOT_CALLSIGNS = (
    "Atlas", "Badger", "Bison", "Cedar", "Comet", "Condor", "Coyote", "Dagger",
    "Echo", "Falcon", "Frost", "Golem", "Harbor", "Hawk", "Ibis", "Jade",
    "Kestrel", "Lancer", "Lynx", "Mantis", "Maple", "Meteor", "Nomad", "Onyx",
    "Orion", "Otter", "Panda", "Quartz", "Raven", "Rook", "Saber", "Scout",
    "Shark", "Sparrow", "Talon", "Tiger", "Viper", "Wolf", "Yak", "Zephyr",
)
ROUND_SCOPED_MESSAGE_TYPES = frozenset((
    "start_battle", "input", "hit_report", "bot_manifest", "bot_state",
    "bot_observation", "bot_hit_report", "bot_human_hit", "bot_ram_report",
    "rules_state", "destructible",
    "battle_result", "leave_battle", "battle_ready",
))
DESTRUCTIBLE_KINDS = frozenset(("tree", "column", "fragile", "module"))
COMBAT_EVENT_KINDS = frozenset((
    "health", "hit", "bot_hit", "bot_human_hit", "bot_bot_hit",
))
COMBAT_SOURCE_KINDS = {
    "shot": frozenset((
        "hit", "bot_hit", "bot_human_hit", "bot_bot_hit")),
    "fire": frozenset(("bot_hit", "bot_human_hit", "bot_bot_hit")),
    "ram": frozenset(("bot_hit", "bot_human_hit", "bot_bot_hit")),
    "client_simulation": frozenset(("health",)),
    "player_left": frozenset(("health",)),
}
CRITICAL_DEVICE_NAMES = frozenset((
    "engineHealth", "ammoBayHealth", "fuelTankHealth", "radioHealth",
    "leftTrackHealth", "rightTrackHealth", "gunHealth",
    "turretRotatorHealth", "surveyingDeviceHealth",
))
CRITICAL_CREW_NAMES = frozenset((
    "commander", "driver", "gunner1", "gunner2", "loader1",
    "loader2", "radioman1", "radioman2",
))
CRITICAL_STATES = frozenset(("normal", "critical", "destroyed"))
CRITICAL_CAUSES = frozenset((
    "shot", "explosion", "repair", "fire", "drowning"))


def _server_log(message):
    stamp = time.strftime("%H:%M:%S")
    print("[%s] %s" % (stamp, message), flush=True)


def _finite_float(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(value):
        return float(default)
    return value


def _has_finite_fields(value, names):
    if not isinstance(value, dict):
        return False
    for name in names:
        if name not in value:
            return False
        try:
            if not math.isfinite(float(value[name])):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _critical_proposal_admission(message, expected_base_revision,
                                 expected_ack_seq):
    """Validate one modern firing-client critical compare-and-swap token."""
    if (isinstance(expected_base_revision, bool) or
            not isinstance(expected_base_revision, int) or
            expected_base_revision < 0 or
            isinstance(expected_ack_seq, bool) or
            not isinstance(expected_ack_seq, int) or
            expected_ack_seq < 0):
        raise ValueError("invalid canonical critical target token")
    values = []
    for name in ("critical_target_base_revision",
                 "critical_target_ack_seq", "hull_damage"):
        value = message.get(name)
        if (isinstance(value, bool) or not isinstance(value, int) or
                value < 0):
            raise ValueError("invalid modern critical proposal")
        values.append(value)
    base_revision, ack_seq, hull_damage = values
    if hull_damage > 5000:
        raise ValueError("invalid modern critical hull damage")
    accepted = (base_revision == expected_base_revision and
                ack_seq == expected_ack_seq)
    return hull_damage, accepted


def _clamp(value, low, high):
    return max(low, min(high, value))


def _safe_name(value, fallback):
    value = str(value or fallback).strip()
    value = "".join(ch for ch in value if ch.isalnum() or ch in " _-")
    return value[:24] or fallback


def _safe_vehicle(value, fallback):
    value = str(value or fallback).strip()
    value = "".join(ch for ch in value if ch.isalnum() or ch in ":_-")
    return value[:64] or fallback


def _critical_payload(value):
    """Validate one client-resolved 0.8.2 critical-state transition.

    The firing authority owns collision/material rolls; the server only
    bounds and relays their resulting state.  This mirrors the existing
    server-owned hull-HP boundary without inventing a second module law.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("critical payload must be an object")
    devices = []
    seen = set()
    for record in list(value.get("devices") or ())[:16]:
        if not isinstance(record, dict):
            raise ValueError("critical device must be an object")
        name = str(record.get("name", ""))
        state = str(record.get("state", ""))
        if (name not in CRITICAL_DEVICE_NAMES or name in seen or
                state not in CRITICAL_STATES or
                not _has_finite_fields(record, ("hp", "max_hp"))):
            raise ValueError("invalid critical device")
        hp = _clamp(_finite_float(record.get("hp")), 0.0, 10000.0)
        maximum = _clamp(
            _finite_float(record.get("max_hp")), 1.0, 10000.0)
        devices.append({"name": name, "hp": round(min(hp, maximum), 3),
                        "max_hp": round(maximum, 3), "state": state})
        seen.add(name)
    destroyed = sorted(set(
        str(name) for name in value.get("destroyed") or ()
        if str(name) in CRITICAL_DEVICE_NAMES))
    crew_ko = sorted(set(
        str(name) for name in value.get("crew_ko") or ()
        if str(name) in CRITICAL_CREW_NAMES))
    events = []
    for raw in list(value.get("events") or ())[:24]:
        if not isinstance(raw, dict):
            raise ValueError("critical event must be an object")
        kind = str(raw.get("kind", ""))
        state = raw.get("state")
        cause = str(raw.get("cause", "shot"))
        if kind == "device":
            name = str(raw.get("name", ""))
            if name not in CRITICAL_DEVICE_NAMES or state not in CRITICAL_STATES:
                raise ValueError("invalid critical device event")
            event = {"kind": kind, "name": name, "state": state}
            old_state = raw.get("old_state")
            if old_state in CRITICAL_STATES:
                event["old_state"] = old_state
        elif kind == "crew":
            name = str(raw.get("name", ""))
            if name not in CRITICAL_CREW_NAMES or state not in (
                    "normal", "destroyed"):
                raise ValueError("invalid critical crew event")
            event = {"kind": kind, "name": name, "state": state}
        elif kind == "fire":
            event = {"kind": kind, "state": bool(state)}
        elif kind == "ammo_rack" and state == "destroyed":
            event = {"kind": kind, "state": state}
        else:
            raise ValueError("invalid critical event kind")
        event["cause"] = cause if cause in CRITICAL_CAUSES else "shot"
        events.append(event)
    return {
        "devices": devices,
        "destroyed": destroyed,
        "crew_ko": crew_ko,
        "fire": bool(value.get("fire", False)),
        "ammo_rack_death": bool(value.get("ammo_rack_death", False)),
        "events": events,
    }


def _critical_state(value):
    """Store durable critical state without replaying transition events."""
    if value is None:
        return None
    result = dict(value)
    result["events"] = []
    return result


def _critical_discrete_state(value):
    """Compare module/crew phases without per-frame repair HP progress."""
    if not isinstance(value, dict):
        return None
    devices = tuple(sorted(
        (str(record.get("name", "")), str(record.get("state", "")))
        for record in value.get("devices") or ()
        if isinstance(record, dict)))
    return (
        devices,
        tuple(sorted(str(name) for name in value.get("destroyed") or ())),
        tuple(sorted(str(name) for name in value.get("crew_ko") or ())),
        bool(value.get("fire", False)),
        bool(value.get("ammo_rack_death", False)),
    )


@dataclass
class Player:
    player_id: int
    conn: socket.socket
    address: Tuple[str, int]
    name: str = "Player"
    vehicle: str = "ussr:R11_MS-1"
    team: int = 1
    slot: int = 0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    aim_yaw: float = 0.0
    gun_pitch: float = 0.0
    forward: float = 0.0
    turn: float = 0.0
    speed: float = 0.0
    fire_seq: int = 0
    shell_index: int = 0
    reported_hits: set = field(default_factory=set, repr=False)
    health: int = 1000
    max_health: int = 1000
    alive: bool = True
    critical: dict = field(default_factory=dict)
    critical_revision: int = 0
    critical_report_base_revision: int = 0
    critical_ack_seq: int = 0
    death_reason: int = 0
    display_health: Optional[int] = None
    frags: int = 0
    team_killer: bool = False
    death_attacker_kind: str = ""
    death_attacker_id: int = 0
    client_position: bool = False
    connected: bool = True
    participating: bool = True
    bot_order_revision_sent: int = -1
    destructible_revision_sent: int = -1
    battle_ready_round: int = 0
    send_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def send(self, message):
        if not self.connected:
            return False
        payload = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        if len(payload) > MAX_LINE_BYTES:
            return False
        try:
            with self.send_lock:
                self.conn.sendall(payload)
            if "bot_orders" in message:
                try:
                    self.bot_order_revision_sent = int(message.get("bot_order_revision", -1))
                except (TypeError, ValueError):
                    pass
            if "destructibles" in message:
                try:
                    self.destructible_revision_sent = int(
                        message.get("destructible_revision", -1))
                except (TypeError, ValueError):
                    pass
            return True
        except (BrokenPipeError, ConnectionError, OSError):
            self.connected = False
            return False


class BattleState:
    def __init__(self, map_name=DEFAULT_MAP, max_players=30):
        self.map_option = map_name
        self.map_name = self._choose_map()
        self.client_build = None
        self.max_players = max(1, min(int(max_players), 30))
        self.players: Dict[int, Player] = {}
        self.next_id = 1
        self.tick = 0
        self.lock = threading.RLock()
        self.running = True
        self.phase = "waiting"
        self.round_id = 1
        self.state_revision = 0
        self.host_player_id = None
        self.bot_roster = self._new_bot_roster()
        self.bot_authority_id = None
        self.bot_manifest_authority_id = None
        self.bot_manifest = []
        self.bot_states = {}
        self.bot_planner = BotPlanner()
        self.bot_orders = {"revision": 0, "orders": []}
        self.bot_reported_hits = set()
        self.bot_reported_rams = set()
        self.bot_ram_cooldowns = {}
        self.rules_state = {"bases": {
            "1": {"points": 0, "time_left": 0.0,
                  "invaders": 0, "stopped": False},
            "2": {"points": 0, "time_left": 0.0,
                  "invaders": 0, "stopped": False}}}
        self.battle_result = None
        self.result_reset_tick = None
        self.roster_finalized = False
        self.pending_events = []
        self.pending_live_message = None
        self.capture_bases = {}
        self.destructibles = {}
        self.destructible_revision = 0

    def _choose_map(self, map_pool=None):
        map_pool = MAP_POOL if map_pool is None else tuple(map_pool)
        if self.map_option in (None, "", "random", DEFAULT_MAP):
            return random.choice(map_pool)
        selected = str(self.map_option)
        if selected not in ALL_MAP_POOL:
            raise ValueError("unsupported standard map: %s" % selected)
        return selected

    def _active_map_pool(self):
        return CLIENT_MAP_POOLS.get(self.client_build, MAP_POOL)

    def _message_round_matches(self, message):
        """Fence round-aware clients while accepting 0.8.2 payloads."""
        if not isinstance(message, dict):
            return False
        if "round_id" not in message:
            return True
        try:
            raw_round = message.get("round_id")
            parsed_round = int(raw_round)
            return (not isinstance(raw_round, bool) and
                    float(raw_round) == parsed_round and
                    parsed_round == self.round_id)
        except (TypeError, ValueError, OverflowError):
            return False

    @staticmethod
    def _validate_combat_event_for_wire(event):
        """Reject incomplete cause metadata before any combat event ships."""
        kind = event.get("kind")
        if kind not in COMBAT_EVENT_KINDS:
            return True
        if "source" not in event:
            raise RuntimeError("combat event has no source")
        source = event["source"]
        if source not in COMBAT_SOURCE_KINDS:
            raise RuntimeError("combat event has invalid source: %s" % source)
        if kind not in COMBAT_SOURCE_KINDS[source]:
            raise RuntimeError(
                "combat source %s does not allow kind %s" % (source, kind))
        if "death_reason" not in event:
            raise RuntimeError("combat event has no death_reason")
        death_reason = event["death_reason"]
        if (isinstance(death_reason, bool) or
                not isinstance(death_reason, int) or death_reason < 0):
            raise RuntimeError("combat event has invalid death_reason")
        if not event.get("dead", False) and death_reason != 0:
            raise RuntimeError(
                "nonfatal combat event has nonzero death_reason")
        has_attacker = "attacker" in event or "attacker_bot" in event
        if "attacker" in event and "attacker_bot" in event:
            raise RuntimeError("combat event has ambiguous attacker")
        if source == "player_left":
            if (event.get("attack_reason", object()) is not None or
                    has_attacker):
                raise RuntimeError(
                    "player_left event must be an explicit non-attack cause")
            return True
        if "attack_reason" not in event:
            raise RuntimeError("combat event has no attack_reason")
        attack_reason = event["attack_reason"]
        if (isinstance(attack_reason, bool) or
                not isinstance(attack_reason, int) or attack_reason < 0):
            raise RuntimeError("combat event has invalid attack_reason")
        if source in ("shot", "fire", "ram"):
            expected = {"shot": 0, "fire": 1, "ram": 2}[source]
            if not has_attacker or attack_reason != expected:
                raise RuntimeError(
                    "combat event attacker/cause does not match source %s" %
                    source)
        elif has_attacker:
            raise RuntimeError(
                "client_simulation event must not have an attacker")
        return True

    @staticmethod
    def _new_bot_roster(occupied_slots=None):
        occupied_slots = set(occupied_slots or ())
        roster = []
        used = set()
        for team in (1, 2):
            for slot in range(15):
                if (team, slot) in occupied_slots:
                    continue
                while True:
                    name = "%s-%02d" % (random.choice(BOT_CALLSIGNS), random.randint(10, 99))
                    if name.lower() not in used:
                        used.add(name.lower())
                        break
                # Preserve the canonical id for a team slot even when humans
                # occupy other slots.  This keeps bot identity deterministic
                # across different waiting-room sizes without slot collisions.
                bot_id = slot + 1 if team == 1 else slot + 16
                roster.append({"id": bot_id, "team": team, "slot": slot, "name": name})
        return roster

    def _elect_bot_authority(self):
        connected = sorted(
            p.player_id for p in self.players.values()
            if p.connected and
            (self.phase not in ("loading", "battle") or p.participating))
        old = self.bot_authority_id
        self.bot_authority_id = connected[0] if connected else None
        if (old != self.bot_authority_id and
                self.phase in ("loading", "battle")):
            self.bot_manifest_authority_id = None
            self.bot_planner.clear_observations()
            self.pending_events.append({
                "kind": "authority",
                "player_id": self.bot_authority_id,
                "round_id": self.round_id,
            })
        return old, self.bot_authority_id

    def _elect_room_host(self):
        connected = sorted(
            p.player_id for p in self.players.values() if p.connected)
        self.host_player_id = connected[0] if connected else None
        return self.host_player_id

    def _spawn_for(self, slot, team):
        # Coordinates are intentionally simple and are also sent to clients.
        # The client maps these onto the same local battle space.
        # Keep the synthetic arena small; clients map it onto the loaded map.
        return self._spawn_x_for(slot), self._spawn_z_for(team), (0.0 if team == 1 else math.pi)

    @staticmethod
    def _spawn_x_for(slot):
        return float(int(slot) * 12.0)

    @staticmethod
    def _spawn_z_for(team):
        return -35.0 if team == 1 else 35.0

    def _unique_name(self, requested, address, player_id):
        fallback = "Player%d" % player_id
        base = _safe_name(requested, fallback)
        if base.lower() in ("defaultplayer", "player", "offline_player"):
            address_tail = str(address[0]).rsplit(".", 1)[-1]
            if not address_tail.isdigit():
                address_tail = str(player_id)
            base = "Player-%s" % address_tail
        existing = set(p.name.lower() for p in self.players.values() if p.connected)
        candidate = base
        suffix = 2
        while candidate.lower() in existing:
            suffix_text = "-%d" % suffix
            candidate = base[:max(1, 24 - len(suffix_text))] + suffix_text
            suffix += 1
        return candidate

    def add_player(self, conn, address, hello):
        with self.lock:
            if self.phase != "waiting":
                return None, "battle_in_progress"
            client_build = hello.get("client_build", CLIENT_BUILD_082)
            if (not isinstance(client_build, str) or
                    client_build not in CLIENT_MAP_POOLS):
                return None, "unsupported_client_build"
            if (self.client_build is not None and
                    client_build != self.client_build):
                return None, "incompatible_client_build"
            if len(self.players) >= self.max_players:
                return None, "full"
            if self.client_build is None:
                map_pool = CLIENT_MAP_POOLS[client_build]
                if (self.map_option not in (None, "", "random", DEFAULT_MAP) and
                        str(self.map_option) not in map_pool):
                    return None, "map_not_available_for_client"
                self.client_build = client_build
                self.map_name = self._choose_map(map_pool)
            occupied = {
                team: {player.slot for player in self.players.values()
                       if player.connected and player.team == team}
                for team in (1, 2)}
            available = {
                team: [slot for slot in range(15)
                       if slot not in occupied[team]]
                for team in (1, 2)}
            candidates = [team for team in (1, 2) if available[team]]
            if not candidates:
                return None, "full"
            team = min(candidates, key=lambda value: (
                len(occupied[value]), value))
            slot = available[team][0]
            player_id = self.next_id
            self.next_id += 1
            x, z, yaw = self._spawn_for(slot, team)
            player = Player(
                player_id=player_id,
                conn=conn,
                address=address,
                name=self._unique_name(hello.get("name"), address, player_id),
                vehicle=_safe_vehicle(
                    hello.get("vehicle"), CLIENT_DEFAULT_VEHICLES[client_build]),
                team=team,
                slot=slot,
                x=x,
                z=z,
                yaw=yaw,
                aim_yaw=yaw,
                health=max(1, min(int(_finite_float(hello.get("max_health"), 1000)), 100000)),
                max_health=max(1, min(int(_finite_float(hello.get("max_health"), 1000)), 100000)),
            )
            self.players[player_id] = player
            if self.host_player_id is None:
                self.host_player_id = player_id
            self.state_revision += 1
            return player, None

    def remove_player(self, player_id):
        with self.lock:
            player = self.players.pop(player_id, None)
            if player is not None:
                player.connected = False
                self.state_revision += 1
            if player_id == self.host_player_id:
                self._elect_room_host()
            if player_id == self.bot_authority_id:
                self._elect_bot_authority()
            if self.phase == "loading":
                self._activate_battle_if_ready()
            reset = False
            if self.players and self.phase in ("loading", "battle"):
                self._maybe_finish_battle()
                self._finish_abandoned_battle()
            if not self.players and self.phase in ("loading", "battle"):
                self._reset_round()
                reset = True
            if not self.players:
                self.client_build = None
                self.host_player_id = None
            return player, reset

    def _finish_abandoned_battle(self):
        """End a round that has no connected client left to simulate it."""
        if (self.phase != "battle" or self.battle_result is not None or
                any(player.connected and player.participating
                    for player in self.players.values())):
            return False
        return self._finish_battle(0, "all_players_left", 0)

    def _reset_round(self):
        """Return connected players to a clean waiting-room round."""
        self.phase = "waiting"
        self.round_id += 1
        self.tick = 0
        self.map_name = self._choose_map(self._active_map_pool())
        for player in self.players.values():
            player.health = player.max_health
            player.alive = True
            player.critical = {}
            player.critical_revision = 0
            player.critical_report_base_revision = 0
            player.critical_ack_seq = 0
            player.death_reason = 0
            player.display_health = None
            player.frags = 0
            player.team_killer = False
            player.death_attacker_kind = ""
            player.death_attacker_id = 0
            player.participating = True
            player.forward = 0.0
            player.turn = 0.0
            player.speed = 0.0
            player.fire_seq = 0
            player.shell_index = 0
            player.reported_hits.clear()
            player.client_position = False
            player.x, player.z, player.yaw = self._spawn_for(
                player.slot, player.team)
            player.y = 0.0
            player.aim_yaw = player.yaw
            player.gun_pitch = 0.0
            player.bot_order_revision_sent = -1
            player.destructible_revision_sent = -1
            player.battle_ready_round = 0
        self.next_id = max([player.player_id for player in self.players.values()] or [0]) + 1
        self.bot_roster = self._new_bot_roster()
        self.bot_authority_id = None
        self.bot_manifest_authority_id = None
        self.bot_manifest = []
        self.bot_states = {}
        self.bot_planner.reset()
        self.bot_orders = {"revision": 0, "orders": []}
        self.bot_reported_hits = set()
        self.bot_reported_rams = set()
        self.bot_ram_cooldowns = {}
        self.rules_state = {"bases": {
            "1": {"points": 0, "time_left": 0.0,
                  "invaders": 0, "stopped": False},
            "2": {"points": 0, "time_left": 0.0,
                  "invaders": 0, "stopped": False}}}
        self.battle_result = None
        self.result_reset_tick = None
        self.roster_finalized = False
        self.pending_events = []
        self.pending_live_message = None
        self.capture_bases = {}
        self.destructibles = {}
        self.destructible_revision = 0
        self._elect_room_host()
        self.state_revision += 1

    def lobby_message(self):
        with self.lock:
            return {
                "type": "roster",
                "protocol": PROTOCOL_VERSION,
                "client_build": self.client_build,
                "phase": self.phase,
                "round_id": self.round_id,
                "state_revision": self.state_revision,
                "map": self.map_name,
                "map_pool": list(self._active_map_pool()),
                "host_player_id": self.host_player_id,
                "bot_authority_id": self.bot_authority_id,
                "players": [self._public_player(p) for p in self.players.values() if p.connected],
            }

    def request_start(self, player_id, requested_map=None):
        with self.lock:
            player = self.players.get(player_id)
            if player is None or not player.connected:
                return None, "player_not_found"
            if self.phase != "waiting":
                return None, "already_started"
            if (self.client_build == CLIENT_BUILD_0922 and
                    player_id != self.host_player_id):
                return None, "host_only"
            if requested_map not in (None, ""):
                requested_map = str(requested_map)
                if requested_map not in self._active_map_pool():
                    return None, "invalid_map"
                self.map_name = requested_map
            connected = [p for p in self.players.values() if p.connected]
            for participant in connected:
                participant.participating = True
            occupied_slots = {(p.team, p.slot) for p in connected}
            self.bot_roster = self._new_bot_roster(occupied_slots)
            self.roster_finalized = True
            self.phase = ("loading" if self.client_build == CLIENT_BUILD_0922
                          else "battle")
            self._elect_bot_authority()
            self.state_revision += 1
            return {
                "type": "battle_start",
                "protocol": PROTOCOL_VERSION,
                "client_build": self.client_build,
                "round_id": self.round_id,
                "state_revision": self.state_revision,
                "map": self.map_name,
                "requested_by": player_id,
                "host_player_id": self.host_player_id,
                "phase": self.phase,
                "delay": 0.75,
                "players": [self._public_player(p) for p in connected],
                "bots": list(self.bot_roster),
                "bot_authority_id": self.bot_authority_id,
                "bot_manifest": list(self.bot_manifest),
                "bot_order_revision": self.bot_orders["revision"],
                "bot_orders": list(self.bot_orders["orders"]),
                "rules": self.rules_state,
                "battle_result": self.battle_result,
                "destructible_revision": self.destructible_revision,
                "destructibles": list(self.destructibles.values()),
            }, None

    def _activate_battle_if_ready(self):
        if self.phase != "loading":
            return None
        participants = [
            player for player in self.players.values()
            if player.connected and player.participating]
        if (not participants or
                (self.bot_roster and
                 self.bot_manifest_authority_id != self.bot_authority_id) or
                any(
                player.battle_ready_round != self.round_id
                for player in participants)):
            return None
        self.phase = "battle"
        self.tick = 0
        self.state_revision += 1
        live_message = {
            "type": "battle_live",
            "protocol": PROTOCOL_VERSION,
            "client_build": self.client_build,
            "round_id": self.round_id,
            "server_tick": self.tick,
            "state_revision": self.state_revision,
            "countdown_seconds": PREBATTLE_SECONDS,
            "battle_duration_seconds": BATTLE_DURATION_SECONDS,
            "timing": self._timing_payload(),
        }
        # The tick thread is the only publisher of this barrier.  It sends the
        # barrier before advancing tick zero or publishing the first snapshot,
        # so every TCP stream observes one ordered transition into PREBATTLE.
        self.pending_live_message = {
            "round_id": self.round_id,
            "recipients": tuple(participants),
            "message": live_message,
        }
        return live_message

    def _timing_payload(self):
        """Return server-authoritative phase time as relative milliseconds."""
        prebattle_ticks = int(round(PREBATTLE_SECONDS * TICK_HZ))
        battle_ticks = int(round(BATTLE_DURATION_SECONDS * TICK_HZ))
        total_ticks = prebattle_ticks + battle_ticks
        tick = max(0, int(self.tick))
        if self.phase == "loading":
            phase = "loading"
        elif self.battle_result is not None or tick >= total_ticks:
            phase = "finished"
        elif tick < prebattle_ticks:
            phase = "prebattle"
        else:
            phase = "battle"
        return {
            "phase": phase,
            "start_in_ms": max(
                0, int(round(1000.0 * (prebattle_ticks - tick) /
                             TICK_HZ))),
            "remaining_ms": max(
                0, int(round(1000.0 *
                             (total_ticks - max(tick, prebattle_ticks)) /
                             TICK_HZ))),
            "duration_ms": int(round(
                BATTLE_DURATION_SECONDS * 1000.0)),
        }

    def mark_battle_ready(self, player_id, message):
        """Open one shared countdown after every #1513 client loaded."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    self.phase != "loading"):
                return None
            player = self.players.get(player_id)
            if (player is None or not player.connected or
                    not player.participating):
                return None
            if player_id == self.bot_authority_id:
                bases = self._sanitize_capture_bases(message.get("bases"))
                if bases:
                    self.capture_bases = bases
            player.battle_ready_round = self.round_id
            return self._activate_battle_if_ready()

    def activate_battle_if_ready(self):
        """Re-evaluate the barrier when its final prerequisite arrives."""
        with self.lock:
            return self._activate_battle_if_ready()

    @staticmethod
    def _sanitize_capture_bases(raw):
        if not isinstance(raw, dict):
            return {}
        result = {}
        for team in (1, 2):
            values = raw.get(str(team), raw.get(team))
            if not isinstance(values, (list, tuple)):
                continue
            points = []
            for value in values[:4]:
                try:
                    if isinstance(value, dict):
                        x, z = value.get("x"), value.get("z")
                    else:
                        x, z = value[0], value[1]
                    x = _finite_float(x, float("nan"))
                    z = _finite_float(z, float("nan"))
                    if not math.isfinite(x) or not math.isfinite(z):
                        continue
                    points.append((round(_clamp(x, -2000.0, 2000.0), 3),
                                   round(_clamp(z, -2000.0, 2000.0), 3)))
                except (KeyError, TypeError, ValueError, IndexError):
                    continue
            if points:
                result[team] = points
        return result if 1 in result and 2 in result else {}

    def loading_snapshot(self):
        """Publish the accepted canonical bot lineup before the load barrier."""
        with self.lock:
            if (self.phase != "loading" or
                    self.bot_manifest_authority_id != self.bot_authority_id):
                return None
            return {
                "type": "snapshot",
                "protocol": PROTOCOL_VERSION,
                "client_build": self.client_build,
                "server_tick": 0,
                "round_id": self.round_id,
                "map": self.map_name,
                "bot_authority_id": self.bot_authority_id,
                "players": [self._public_player(p) for p in self.players.values()
                            if p.connected and p.participating],
                "bots": [self.bot_states[key] for key in sorted(self.bot_states)],
                "bot_manifest": list(self.bot_manifest),
                "bot_order_revision": self.bot_orders["revision"],
                "rules": self.rules_state,
                "battle_result": self.battle_result,
                "destructible_revision": self.destructible_revision,
                "destructibles": list(self.destructibles.values()),
            }

    @staticmethod
    def _sanitize_destructible(message):
        if not isinstance(message, dict):
            return None
        kind = str(message.get("destructible_kind", ""))
        if kind not in DESTRUCTIBLE_KINDS:
            return None
        try:
            chunk_id = int(message.get("chunk_id"))
            item_index = int(message.get("item_index"))
            if (isinstance(message.get("chunk_id"), bool) or
                    isinstance(message.get("item_index"), bool) or
                    float(message.get("chunk_id")) != chunk_id or
                    float(message.get("item_index")) != item_index or
                    not -2147483648 <= chunk_id <= 4294967295 or
                    not 0 <= item_index <= 1048575):
                return None
        except (TypeError, ValueError, OverflowError):
            return None
        mat_kind = message.get("mat_kind")
        if mat_kind is not None:
            try:
                parsed = int(mat_kind)
                if (isinstance(mat_kind, bool) or float(mat_kind) != parsed or
                        not 0 <= parsed <= 65535):
                    return None
                mat_kind = parsed
            except (TypeError, ValueError, OverflowError):
                return None
        if kind == "module" and mat_kind is None:
            return None
        is_shot = message.get("is_shot")
        if not isinstance(is_shot, bool):
            return None
        if not _has_finite_fields(
                message, ("x", "y", "z", "fall_yaw", "speed")):
            return None
        event = {
            "kind": "destructible",
            "destructible_kind": kind,
            "chunk_id": chunk_id,
            "item_index": item_index,
            "x": round(_clamp(_finite_float(message.get("x")),
                              -5000.0, 5000.0), 3),
            "y": round(_clamp(_finite_float(message.get("y")),
                              -1000.0, 3000.0), 3),
            "z": round(_clamp(_finite_float(message.get("z")),
                              -5000.0, 5000.0), 3),
            "fall_yaw": round(_clamp(
                _finite_float(message.get("fall_yaw")),
                -math.pi * 4.0, math.pi * 4.0), 6),
            "speed": round(_clamp(_finite_float(message.get("speed")),
                                  -200.0, 200.0), 3),
            "is_shot": is_shot,
        }
        if mat_kind is not None:
            event["mat_kind"] = mat_kind
        return event

    def report_destructible(self, player_id, message):
        """Admit one client-resolved map destruction into shared LAN state."""
        with self.lock:
            player = self.players.get(player_id)
            if (not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    self.battle_result is not None or player is None or
                    not player.connected or not player.participating or
                    not player.alive):
                return False
            event = self._sanitize_destructible(message)
            if event is None:
                return False
            key = (event["destructible_kind"], event["chunk_id"],
                   event["item_index"], event.get("mat_kind"))
            if key in self.destructibles:
                return True
            self.destructible_revision += 1
            event["revision"] = self.destructible_revision
            event["reported_by"] = player_id
            self.destructibles[key] = event
            self.pending_events.append(dict(event))
            return True

    def leave_battle(self, player_id, message):
        """Retire a client from one round while keeping its lobby socket."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    self.phase not in ("loading", "battle")):
                return False
            player = self.players.get(player_id)
            if player is None or not player.connected:
                return False
            if not player.participating:
                return True
            player.participating = False
            previous_health = player.health
            player.health = 0
            player.alive = False
            player.forward = 0.0
            player.turn = 0.0
            self.state_revision += 1
            self.pending_events.append({
                "kind": "health",
                "target": player.player_id,
                "damage": previous_health,
                "health": 0,
                "dead": True,
                "attack_reason": None,
                "death_reason": 0,
                "source": "player_left",
            })
            if player_id == self.bot_authority_id:
                self._elect_bot_authority()
            if self.phase == "loading":
                participants = [
                    value for value in self.players.values()
                    if value.connected and value.participating]
                if not participants:
                    # A graceful leave keeps every TCP connection alive.  If
                    # nobody remains in this load, return those same sockets
                    # to a fresh waiting-room round instead of leaving an
                    # impossible ready barrier behind forever.
                    self._reset_round()
                    return True
                # A not-yet-ready participant may be the only remaining
                # blocker.  Re-evaluate under the same state lock that retired
                # it so tick zero cannot observe a half-updated recipient set.
                self._activate_battle_if_ready()
                return True
            self._maybe_finish_battle()
            self._finish_abandoned_battle()
            return True

    def leave_battle_and_publish(self, player_id, message):
        """Apply a graceful loading leave and publish its membership atomically."""
        with self.lock:
            was_loading = self.phase == "loading"
            accepted = self.leave_battle(player_id, message)
            if accepted and was_loading:
                self._broadcast_current_roster_locked()
            return accepted

    def current_battle_message(self):
        with self.lock:
            if self.phase != "battle":
                return None
            connected = [p for p in self.players.values() if p.connected]
            return {
                "type": "battle_start",
                "protocol": PROTOCOL_VERSION,
                "round_id": self.round_id,
                "state_revision": self.state_revision,
                "map": self.map_name,
                "requested_by": 0,
                "host_player_id": self.host_player_id,
                "delay": 0.75,
                "late_join": True,
                "players": [self._public_player(p) for p in connected],
                "bots": list(self.bot_roster),
                "bot_authority_id": self.bot_authority_id,
                "bot_manifest": list(self.bot_manifest),
                "bot_order_revision": self.bot_orders["revision"],
                "bot_orders": list(self.bot_orders["orders"]),
                "rules": self.rules_state,
                "battle_result": self.battle_result,
                "destructible_revision": self.destructible_revision,
                "destructibles": list(self.destructibles.values()),
            }

    def update_bot_manifest(self, player_id, message):
        """Accept the canonical bot lineup from the elected simulation client."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    self.phase not in ("loading", "battle") or
                    self.battle_result is not None or
                    player_id != self.bot_authority_id):
                return False
            incoming = message.get("bots") or []
            if not isinstance(incoming, (list, tuple)):
                return False
            roster = {entry["id"]: entry for entry in self.bot_roster}
            if not roster:
                if incoming:
                    return False
                self.bot_manifest_authority_id = player_id
                return True
            if len(incoming) != len(roster):
                return False
            manifest = []
            states = {}
            seen = set()
            required = ("id", "team", "slot", "vehicle", "health",
                        "max_health", "x", "y", "z", "yaw")
            for raw in incoming:
                if (not isinstance(raw, dict) or
                        not all(key in raw for key in required) or
                        not _has_finite_fields(
                            raw, ("id", "team", "slot", "health",
                                  "max_health", "x", "y", "z", "yaw"))):
                    return False
                try:
                    bot_id = int(raw.get("id"))
                    raw_team = int(raw.get("team", roster.get(bot_id, {}).get("team", 0)))
                    raw_slot = int(raw.get("slot", roster.get(bot_id, {}).get("slot", -1)))
                except (TypeError, ValueError):
                    return False
                identity = roster.get(bot_id)
                if identity is None or bot_id in seen:
                    return False
                if raw_team != identity["team"] or raw_slot != identity["slot"]:
                    return False
                seen.add(bot_id)
                max_health = max(1, min(int(_finite_float(raw.get("max_health"), 1000)), 100000))
                health = max(0, min(int(_finite_float(raw.get("health"), max_health)), max_health))
                try:
                    route = self._sanitize_bot_route(raw.get("route"))
                except (TypeError, ValueError):
                    return False
                entry = {
                    "id": bot_id,
                    "team": identity["team"],
                    "slot": identity["slot"],
                    "name": identity["name"],
                    "vehicle": _safe_vehicle(raw.get("vehicle"), "ussr:R11_MS-1"),
                    "max_health": max_health,
                    "health": health,
                    "profile": self._sanitize_bot_profile(raw.get("profile")),
                    "route": route,
                }
                manifest.append(entry)
                states[bot_id] = self._sanitize_bot_state(raw, entry, None)
            if seen != set(roster):
                return False
            manifest.sort(key=lambda value: value["id"])
            self.bot_manifest = manifest
            self.bot_manifest_authority_id = player_id
            if not self.bot_states:
                self.bot_states = states
            self.pending_events.append({"kind": "bot_manifest", "bots": list(manifest)})
            return True

    @staticmethod
    def _sanitize_bot_profile(raw):
        raw = raw if isinstance(raw, dict) else {}
        profile = {}
        for key in ("class_tag", "dominant_role"):
            profile[key] = _safe_name(raw.get(key), "unknown")
        roles = raw.get("roles")
        profile["roles"] = {}
        if isinstance(roles, dict):
            for key, value in list(roles.items())[:8]:
                role = _safe_name(key, "unknown")
                profile["roles"][role] = round(
                    _clamp(_finite_float(value), 0.0, 1.0), 3)
        for key, default, maximum in (("desired_range", 180.0, 2000.0),
                                      ("fire_range", 500.0, 2500.0),
                                      ("speed", 0.0, 200.0),
                                      ("armor", 0.0, 10000.0)):
            profile[key] = round(_clamp(_finite_float(raw.get(key), default), 0.0, maximum), 3)
        profile["shells"] = []
        shells = raw.get("shells") or []
        if not isinstance(shells, (list, tuple)):
            shells = []
        for shell in shells[:5]:
            if not isinstance(shell, dict):
                continue
            profile["shells"].append({
                "index": max(0, min(int(_finite_float(shell.get("index"), 0)), 9)),
                "kind": _safe_name(shell.get("kind"), "unknown"),
                "penetration": round(_clamp(_finite_float(shell.get("penetration")), 0.0, 10000.0), 3),
                "damage": round(_clamp(_finite_float(shell.get("damage")), 0.0, 10000.0), 3),
                "speed": round(_clamp(_finite_float(shell.get("speed")), 0.0, 10000.0), 3),
            })
        return profile

    @staticmethod
    def _sanitize_bot_route(raw):
        raw = raw if isinstance(raw, dict) else {}
        route = {"id": _safe_name(raw.get("id"), "server_route"),
                 "waypoints": []}
        waypoints = raw.get("waypoints") or []
        if not isinstance(waypoints, (list, tuple)):
            waypoints = []
        if len(waypoints) > 16:
            raise ValueError("bot route exceeds the 16-waypoint protocol limit")
        for point in waypoints:
            if not isinstance(point, dict):
                continue
            route["waypoints"].append({
                "x": round(_clamp(_finite_float(point.get("x")), -2000.0, 2000.0), 3),
                "y": round(_clamp(_finite_float(point.get("y")), -1000.0, 1000.0), 3),
                "z": round(_clamp(_finite_float(point.get("z")), -2000.0, 2000.0), 3),
                "hold": bool(point.get("hold", False)),
            })
        return route

    def update_bot_observation(self, player_id, message):
        """Accept authority observations; never derive contacts from snapshots."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    player_id != self.bot_authority_id or
                    player_id != self.bot_manifest_authority_id):
                return False
            if (not isinstance(message.get("contacts"), (list, tuple)) or
                    ("affordances" in message and
                     not isinstance(message.get("affordances"), (list, tuple)))):
                return False
            players = [self._public_player(p) for p in self.players.values() if p.connected]
            known_targets = self.bot_planner.known_targets(list(self.bot_states.values()), players)
            now = time.monotonic()
            accepted_contacts = self.bot_planner.report_contacts(
                message.get("contacts"), known_targets, now)
            known_bots = self.bot_planner.known_bots(
                self.bot_manifest, list(self.bot_states.values()))
            accepted_affordances = self.bot_planner.report_affordances(
                message.get("affordances"), known_bots, known_targets, now)
            return accepted_contacts > 0 or accepted_affordances > 0

    @staticmethod
    def _sanitize_bot_state(raw, identity, previous):
        max_health = int(identity.get("max_health", 1000))
        reported_health = max(0, min(int(_finite_float(raw.get("health"), max_health)), max_health))
        if previous is not None:
            reported_health = min(reported_health, int(previous.get("health", max_health)))
        try:
            fire_seq = max(0, int(raw.get("fire_seq", 0)))
        except (TypeError, ValueError):
            fire_seq = 0
        if previous is not None:
            fire_seq = max(fire_seq, int(previous.get("fire_seq", 0)))
        yaw = _finite_float(raw.get("yaw"), 0.0)
        movement = _finite_float(raw.get("movement_dir"), 0.0)
        rotation = _finite_float(raw.get("rotation_dir"), 0.0)
        result = {
            "id": int(identity["id"]),
            "team": int(identity["team"]),
            "slot": int(identity["slot"]),
            "name": identity["name"],
            "vehicle": identity.get("vehicle", "ussr:R11_MS-1"),
            "world_pose": True,
            "x": round(_clamp(_finite_float(raw.get("x")), -2000.0, 2000.0), 4),
            "y": round(_clamp(_finite_float(raw.get("y")), -1000.0, 1000.0), 4),
            "z": round(_clamp(_finite_float(raw.get("z")), -2000.0, 2000.0), 4),
            "yaw": round(yaw, 5),
            "aim_yaw": round(_finite_float(raw.get("aim_yaw"), yaw), 5),
            "gun_pitch": round(_clamp(_finite_float(raw.get("gun_pitch")), -1.2, 1.2), 5),
            "movement_dir": (1 if movement > 0.01 else
                             (-1 if movement < -0.01 else 0)),
            "rotation_dir": (1 if rotation > 0.01 else
                             (-1 if rotation < -0.01 else 0)),
            "fire_seq": fire_seq,
            "shell_index": max(0, min(int(_finite_float(raw.get("shell_index"), 0)), 9)),
            "health": reported_health,
            "max_health": max_health,
            "alive": bool(raw.get("alive", reported_health > 0)) and reported_health > 0,
            "frags": int((previous or {}).get("frags", 0)),
            "team_killer": bool((previous or {}).get(
                "team_killer", False)),
            "death_attacker_kind": str((previous or {}).get(
                "death_attacker_kind", "")),
            "death_attacker_id": int((previous or {}).get(
                "death_attacker_id", 0)),
            "combat_revision": int((previous or {}).get(
                "combat_revision", 0)),
            "combat_base_revision": int((previous or {}).get(
                "combat_base_revision", 0)),
            "combat_ack_seq": int((previous or {}).get(
                "combat_ack_seq", 0)),
            "combat_fire_elapsed": round(_finite_float(
                raw.get("combat_fire_elapsed"), (previous or {}).get(
                    "combat_fire_elapsed", 0.0)), 6),
            "combat_fire_timer": round(_finite_float(
                raw.get("combat_fire_timer"), (previous or {}).get(
                    "combat_fire_timer", 0.0)), 6),
            "fire_attacker_kind": str((previous or {}).get(
                "fire_attacker_kind", "")),
            "fire_attacker_id": int((previous or {}).get(
                "fire_attacker_id", 0)),
        }
        critical = (previous or {}).get("critical")
        if "critical" in raw:
            critical = _critical_state(_critical_payload(raw.get("critical")))
        # Canonical snapshots always carry the complete modern combat
        # baseline.  Legacy 0.8.2 senders need not provide it, but an empty
        # state must not disappear before the #1513 loading snapshot.
        result["critical"] = critical or {}
        has_shot_yaw = "shot_yaw" in raw
        has_shot_pitch = "shot_pitch" in raw
        if has_shot_yaw != has_shot_pitch:
            raise ValueError("bot shot angles must be an atomic pair")
        raw_shot_yaw = (raw.get("shot_yaw") if has_shot_yaw else
                        (previous or {}).get("shot_yaw"))
        raw_shot_pitch = (raw.get("shot_pitch") if has_shot_pitch else
                          (previous or {}).get("shot_pitch"))
        if raw_shot_yaw is not None and raw_shot_pitch is not None:
            shot_yaw = _finite_float(raw_shot_yaw, float("nan"))
            shot_pitch = _finite_float(raw_shot_pitch, float("nan"))
            if not math.isfinite(shot_yaw) or not math.isfinite(shot_pitch):
                raise ValueError("bot shot angles must be finite")
            result["shot_yaw"] = round(
                ((shot_yaw + math.pi) % (2.0 * math.pi)) - math.pi, 5)
            result["shot_pitch"] = round(
                _clamp(shot_pitch, -1.2, 1.2), 5)
        result["death_reason"] = max(0, min(int(_finite_float(
            raw.get("death_reason"), (previous or {}).get(
                "death_reason", 0))), 255))
        result["display_health"] = max(0, min(int(_finite_float(
            raw.get("display_health"), reported_health)), max_health))
        return result

    @staticmethod
    def _bot_combat_signature(state):
        critical = state.get("critical")
        if critical:
            critical = _critical_state(_critical_payload(critical))
            if _critical_discrete_state(critical) == (
                    (), (), (), False, False):
                critical = None
        else:
            critical = None
        return (int(state.get("health", 0)), bool(state.get("alive")),
                critical,
                round(float(state.get("combat_fire_elapsed", 0.0)), 6),
                round(float(state.get("combat_fire_timer", 0.0)), 6))

    @staticmethod
    def _copy_bot_combat(target, source):
        for key in ("health", "alive", "critical", "death_reason",
                    "display_health", "death_attacker_kind",
                    "death_attacker_id", "combat_revision",
                    "combat_base_revision", "combat_ack_seq",
                    "combat_fire_elapsed", "combat_fire_timer",
                    "fire_attacker_kind", "fire_attacker_id"):
            if key in source:
                value = source[key]
                target[key] = dict(value) if isinstance(value, dict) else value
            else:
                target.pop(key, None)

    @staticmethod
    def _commit_external_bot_combat(bot, before):
        """Open a new lineage for one server-admitted bot combat change."""
        before_fire = bool(before[2] and before[2].get("fire", False))
        after_critical = bot.get("critical") or {}
        after_fire = bool(after_critical.get("fire", False))
        if not (before_fire and after_fire):
            bot["combat_fire_elapsed"] = 0.0
            bot["combat_fire_timer"] = 0.0
        if not after_fire:
            bot["fire_attacker_kind"] = ""
            bot["fire_attacker_id"] = 0
        after = BattleState._bot_combat_signature(bot)
        if after == before:
            return False
        revision = int(bot.get("combat_revision", 0)) + 1
        bot["combat_revision"] = revision
        bot["combat_base_revision"] = revision
        # Publication sequence numbers are global within one round.  Keeping
        # the accepted prefix identifies whether an in-flight authority state
        # was incorporated before this external hit.
        bot["combat_ack_seq"] = int(bot.get("combat_ack_seq", 0))
        return True

    def _reconcile_modern_bot_combat(self, raw, previous, current):
        """Apply the strict #1513 bot publication/base/ack contract."""
        required = ("critical", "combat_base_revision", "combat_seq",
                    "combat_fire_elapsed", "combat_fire_timer")
        if not all(key in raw for key in required):
            raise ValueError("modern bot combat publication is incomplete")
        if not isinstance(raw["critical"], dict):
            raise ValueError("modern bot critical state is invalid")
        try:
            raw_base = int(raw["combat_base_revision"])
            raw_seq = int(raw["combat_seq"])
            fire_elapsed = float(raw["combat_fire_elapsed"])
            fire_timer = float(raw["combat_fire_timer"])
        except (TypeError, ValueError, OverflowError):
            raise ValueError("modern bot combat revision is invalid")
        if (isinstance(raw["combat_base_revision"], bool) or
                isinstance(raw["combat_seq"], bool) or
                isinstance(raw["combat_fire_elapsed"], bool) or
                isinstance(raw["combat_fire_timer"], bool) or
                not math.isfinite(fire_elapsed) or
                not math.isfinite(fire_timer) or raw_base < 0 or
                raw_seq < 0 or
                float(raw["combat_base_revision"]) != raw_base or
                float(raw["combat_seq"]) != raw_seq or
                fire_elapsed < 0.0 or
                fire_elapsed > BOT_FIRE_DURATION_SECONDS or
                fire_timer < 0.0 or
                fire_timer >= BOT_FIRE_TICK_SECONDS):
            raise ValueError("modern bot combat revision is invalid")
        current_fire = bool((current.get("critical") or {}).get(
            "fire", False))
        if (not current_fire and
                (fire_elapsed != 0.0 or fire_timer != 0.0)):
            raise ValueError("inactive bot fire has a non-zero clock")
        current["combat_fire_elapsed"] = round(fire_elapsed, 6)
        current["combat_fire_timer"] = round(fire_timer, 6)

        server_base = int(previous.get("combat_base_revision", 0))
        server_ack = int(previous.get("combat_ack_seq", 0))
        if raw_base > server_base:
            raise ValueError("bot combat publication is ahead of its base")
        if raw_base < server_base:
            # An external hit won the server ordering race.  Pose and shot
            # edges remain current, while the stale combat proposal is fenced.
            self._copy_bot_combat(current, previous)
            return
        if raw_seq < server_ack or raw_seq > server_ack + 1:
            raise ValueError("bot combat publication sequence is not contiguous")
        if raw_seq == server_ack:
            if self._bot_combat_signature(current) != \
                    self._bot_combat_signature(previous):
                raise ValueError("repeated bot combat publication changed state")
            self._copy_bot_combat(current, previous)
            return

        current["combat_revision"] = int(
            previous.get("combat_revision", 0)) + 1
        current["combat_base_revision"] = server_base
        current["combat_ack_seq"] = raw_seq

    def update_bot_states(self, player_id, message):
        with self.lock:
            if (not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    self.battle_result is not None or
                    player_id != self.bot_authority_id or
                    player_id != self.bot_manifest_authority_id or
                    not self.bot_manifest):
                return False
            identities = {entry["id"]: entry for entry in self.bot_manifest}
            incoming = message.get("bots") or []
            if (not isinstance(incoming, (list, tuple)) or
                    len(incoming) != len(identities)):
                return False
            next_states = {}
            shot_events = []
            fire_deaths = []
            seen = set()
            required = ("id", "x", "y", "z", "yaw", "health",
                        "alive", "fire_seq")
            for raw in incoming:
                if (not isinstance(raw, dict) or
                        not all(key in raw for key in required) or
                        not _has_finite_fields(
                            raw, ("id", "x", "y", "z", "yaw",
                                  "health", "fire_seq")) or
                        not isinstance(raw.get("alive"), bool)):
                    return False
                try:
                    bot_id = int(raw.get("id"))
                except (TypeError, ValueError):
                    return False
                identity = identities.get(bot_id)
                if identity is None or bot_id in seen:
                    return False
                seen.add(bot_id)
                try:
                    fire_seq = int(raw.get("fire_seq"))
                except (TypeError, ValueError):
                    return False
                if (fire_seq < 0 or float(raw.get("fire_seq")) != fire_seq or
                        bool(raw.get("alive")) !=
                        (int(float(raw.get("health"))) > 0)):
                    return False
                previous = self.bot_states.get(bot_id)
                try:
                    current = self._sanitize_bot_state(
                        raw, identity, previous)
                    if self.client_build == CLIENT_BUILD_0922:
                        self._reconcile_modern_bot_combat(
                            raw, previous, current)
                except ValueError:
                    return False
                previous_fire = int((previous or {}).get("fire_seq", 0))
                if current["fire_seq"] > previous_fire + 1:
                    return False
                next_states[bot_id] = current
                previous_fire_active = bool(
                    (previous or {}).get("critical") and
                    previous["critical"].get("fire", False))
                fire_tick_damage = max(1, int(
                    int(current.get("max_health", 0)) * 0.05))
                previous_health = int((previous or {}).get("health", 0))
                current_health = int(current.get("health", 0))
                if (previous is not None and previous.get("alive") and
                        not current.get("alive") and
                        previous_fire_active and
                        current_health == max(
                            0, previous_health - fire_tick_damage) and
                        current.get("fire_attacker_kind") in (
                            "player", "bot") and
                        int(current.get("fire_attacker_id", 0)) > 0):
                    current["death_reason"] = 1
                    current["death_attacker_kind"] = current[
                        "fire_attacker_kind"]
                    current["death_attacker_id"] = int(current[
                        "fire_attacker_id"])
                    fire_deaths.append((
                        current["fire_attacker_kind"],
                        current["fire_attacker_id"], current,
                        previous_health - current_health))
                if (not current.get("alive") or
                        not bool((current.get("critical") or {}).get(
                            "fire", False))):
                    current["fire_attacker_kind"] = ""
                    current["fire_attacker_id"] = 0
                if (current["alive"] and
                        (previous is None or previous.get("alive")) and
                        current["fire_seq"] > previous_fire):
                    shot_event = {
                        "kind": "bot_shot", "attacker_bot": bot_id,
                        "shot_seq": current["fire_seq"],
                        "shell_index": current["shell_index"],
                    }
                    if ("shot_yaw" in current and
                            "shot_pitch" in current):
                        shot_event["shot_yaw"] = current["shot_yaw"]
                        shot_event["shot_pitch"] = current["shot_pitch"]
                    shot_events.append(shot_event)
            if seen != set(identities):
                return False
            self.bot_states = next_states
            for attacker_kind, attacker_id, victim, damage in fire_deaths:
                self._record_frag(
                    attacker_kind, attacker_id, victim["team"],
                    "bot", victim["id"])
                event = {
                    "kind": ("bot_bot_hit" if attacker_kind == "bot"
                             else "bot_hit"),
                    "target_bot": victim["id"],
                    "damage": damage, "health": 0, "dead": True,
                    "attack_reason": 1, "death_reason": 1,
                    "source": "fire",
                }
                event["attacker_bot" if attacker_kind == "bot"
                      else "attacker"] = int(attacker_id)
                self.pending_events.append(event)
            self.pending_events.extend(shot_events)
            self._maybe_finish_battle()
            return True

    def report_bot_hit(self, player_id, message):
        """Apply a human or authority-owned bot shot to a bot HP record."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    self.battle_result is not None):
                return False
            if not all(key in message for key in
                       ("target", "shot_seq", "damage")):
                return False
            if (not _has_finite_fields(
                    message, ("target", "shot_seq", "damage")) or
                    _finite_float(message.get("damage"), -1.0) < 0.0):
                return False
            try:
                critical = _critical_payload(message.get("critical"))
            except ValueError:
                return False
            try:
                shot_seq = int(message.get("shot_seq", 0))
                bot_id = int(message.get("target", 0))
            except (TypeError, ValueError):
                return False
            state = self.bot_states.get(bot_id)
            if state is None or not state.get("alive"):
                return False
            combat_before = self._bot_combat_signature(state)
            attacker_bot_value = message.get("attacker_bot")
            if attacker_bot_value is not None:
                if player_id != self.bot_authority_id:
                    return False
                if player_id != self.bot_manifest_authority_id:
                    return False
                try:
                    attacker_bot_id = int(attacker_bot_value)
                except (TypeError, ValueError):
                    return False
                attacker_bot = self.bot_states.get(attacker_bot_id)
                splash = bool(message.get("splash", False))
                hit_key = (("bot_shot", attacker_bot_id, shot_seq,
                            "bot", bot_id) if splash else
                           ("bot_shot", attacker_bot_id, shot_seq))
                if (attacker_bot is None or not attacker_bot.get("alive") or
                        (attacker_bot_id == bot_id and not splash) or
                        shot_seq <= 0 or
                        shot_seq > int(attacker_bot.get("fire_seq", 0)) or
                        hit_key in self.bot_reported_hits or
                        math.hypot(state["x"] - attacker_bot["x"],
                                   state["z"] - attacker_bot["z"]) > 5000.0):
                    return False
                reported_hits = self.bot_reported_hits
                attacker_id = attacker_bot_id
                shell_index = attacker_bot.get("shell_index", 0)
                event_kind = "bot_bot_hit"
            else:
                attacker = self.players.get(player_id)
                splash = bool(message.get("splash", False))
                hit_key = (("shot", shot_seq, "bot", bot_id)
                           if splash else ("shot", shot_seq))
                if (attacker is None or not attacker.alive or shot_seq <= 0 or
                        shot_seq > attacker.fire_seq or
                        hit_key in attacker.reported_hits):
                    return False
                reported_hits = attacker.reported_hits
                attacker_id = player_id
                shell_index = attacker.shell_index
                event_kind = "bot_hit"
            modern_proposal = (
                self.client_build == CLIENT_BUILD_0922 and
                critical is not None)
            critical_accepted = True
            hull_damage = None
            if modern_proposal:
                try:
                    hull_damage, critical_accepted = (
                        _critical_proposal_admission(
                            message, state.get("combat_base_revision"),
                            state.get("combat_ack_seq")))
                except ValueError:
                    return False
            reported_hits.add(hit_key)
            damage = max(0, min(int(_finite_float(message.get("damage"), 0)), 5000))
            if modern_proposal and not critical_accepted:
                damage = hull_damage
            applied = min(damage, int(state.get("health", 0)))
            state["health"] -= applied
            state["alive"] = state["health"] > 0
            state["display_health"] = state["health"]
            admitted_critical = (
                critical if not modern_proposal or critical_accepted else None)
            if admitted_critical is not None:
                state["critical"] = _critical_state(admitted_critical)
                before_fire = bool(
                    combat_before[2] and combat_before[2].get("fire", False))
                after_fire = bool(state["critical"].get("fire", False))
                if not before_fire and after_fire:
                    state["fire_attacker_kind"] = (
                        "bot" if event_kind == "bot_bot_hit" else "player")
                    state["fire_attacker_id"] = int(attacker_id)
            self._commit_external_bot_combat(state, combat_before)
            event = {
                "kind": event_kind,
                "attacker_bot" if event_kind == "bot_bot_hit" else "attacker": attacker_id,
                "target_bot": bot_id,
                "shot_seq": shot_seq, "shell_index": shell_index,
                "shot_result": max(0, min(int(_finite_float(message.get("shot_result"), 2)), 2)),
                "damage": applied, "health": state["health"], "dead": not state["alive"],
                "attack_reason": 0, "death_reason": 0,
                "source": "shot",
                "splash": splash,
                "world_pose": True,
                "x": round(_clamp(_finite_float(message.get("x"), state["x"]), -2000.0, 2000.0), 4),
                "y": round(_clamp(_finite_float(message.get("y"), state["y"] + 1.0), -1000.0, 1000.0), 4),
                "z": round(_clamp(_finite_float(message.get("z"), state["z"]), -2000.0, 2000.0), 4),
            }
            if critical is not None:
                if modern_proposal:
                    event["critical_accepted"] = critical_accepted
                if admitted_critical is not None:
                    event["critical"] = admitted_critical
                elif modern_proposal:
                    event["critical_reject_reason"] = (
                        "stale_target_state")
                if modern_proposal:
                    event.update({
                        "combat_revision": state["combat_revision"],
                        "combat_base_revision":
                            state["combat_base_revision"],
                        "combat_ack_seq": state["combat_ack_seq"],
                    })
            self.pending_events.append(event)
            if not state["alive"]:
                state["death_attacker_kind"] = (
                    "bot" if event_kind == "bot_bot_hit" else "player")
                state["death_attacker_id"] = int(attacker_id)
                self._record_frag(
                    "bot" if event_kind == "bot_bot_hit" else "player",
                    attacker_id, int(state.get("team", 0)),
                    "bot", bot_id)
            self._maybe_finish_battle()
            return True

    def report_bot_human_hit(self, player_id, message):
        """Apply an authority-resolved bot shot against shared human HP."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    self.battle_result is not None or
                    player_id != self.bot_authority_id or
                    player_id != self.bot_manifest_authority_id):
                return False
            if not all(key in message for key in
                       ("attacker_bot", "target", "shot_seq", "damage")):
                return False
            if (not _has_finite_fields(
                    message, ("attacker_bot", "target", "shot_seq", "damage")) or
                    _finite_float(message.get("damage"), -1.0) < 0.0):
                return False
            try:
                critical = _critical_payload(message.get("critical"))
            except ValueError:
                return False
            try:
                bot_id = int(message.get("attacker_bot", 0))
                target_id = int(message.get("target", 0))
                shot_seq = int(message.get("shot_seq", 0))
            except (TypeError, ValueError):
                return False
            bot = self.bot_states.get(bot_id)
            target = self.players.get(target_id)
            if bot is None or not bot.get("alive") or target is None or not target.alive:
                return False
            try:
                bot_fire_seq = int(bot.get("fire_seq", 0))
            except (TypeError, ValueError):
                bot_fire_seq = 0
            splash = bool(message.get("splash", False))
            hit_key = (("bot_shot", bot_id, shot_seq,
                        "player", target_id) if splash else
                       ("bot_shot", bot_id, shot_seq))
            if (shot_seq <= 0 or shot_seq > bot_fire_seq or
                    hit_key in self.bot_reported_hits):
                return False
            modern_proposal = (
                self.client_build == CLIENT_BUILD_0922 and
                critical is not None)
            critical_accepted = True
            hull_damage = None
            if modern_proposal:
                try:
                    hull_damage, critical_accepted = (
                        _critical_proposal_admission(
                            message,
                            target.critical_report_base_revision,
                            target.critical_ack_seq))
                except ValueError:
                    return False
            self.bot_reported_hits.add(hit_key)
            damage = max(0, min(int(_finite_float(message.get("damage"), 0)), 5000))
            if modern_proposal and not critical_accepted:
                damage = hull_damage
            applied = min(damage, target.health)
            target.health -= applied
            target.alive = target.health > 0
            target.display_health = target.health
            admitted_critical = (
                critical if not modern_proposal or critical_accepted else None)
            critical_commit = self._commit_external_player_critical(
                target, admitted_critical)
            event = {
                "kind": "bot_human_hit", "attacker_bot": bot_id, "target": target_id,
                "shot_seq": shot_seq,
                "shell_index": max(0, min(int(_finite_float(
                    bot.get("shell_index"), 0)), 9)),
                "shot_result": max(0, min(int(_finite_float(message.get("shot_result"), 2)), 2)),
                "damage": applied, "health": target.health, "dead": not target.alive,
                "attack_reason": 0, "death_reason": 0,
                "source": "shot",
                "splash": splash,
                "world_pose": True,
                "x": round(_clamp(_finite_float(message.get("x"), target.x), -2000.0, 2000.0), 4),
                "y": round(_clamp(_finite_float(message.get("y"), target.y + 1.0), -1000.0, 1000.0), 4),
                "z": round(_clamp(_finite_float(message.get("z"), target.z), -2000.0, 2000.0), 4),
            }
            if critical is not None:
                if modern_proposal:
                    event["critical_accepted"] = critical_accepted
                if admitted_critical is not None:
                    event["critical"] = admitted_critical
                    event.update(critical_commit)
                elif modern_proposal:
                    event["critical_reject_reason"] = (
                        "stale_target_state")
                    event.update({
                        "critical_revision": target.critical_revision,
                        "critical_base_revision":
                            target.critical_report_base_revision,
                        "critical_ack_seq": target.critical_ack_seq,
                    })
            self.pending_events.append(event)
            if not target.alive:
                target.death_attacker_kind = "bot"
                target.death_attacker_id = int(bot_id)
                self._record_frag(
                    "bot", bot_id, target.team,
                    "player", target.player_id)
            self._maybe_finish_battle()
            return True

    def report_bot_ram(self, player_id, message):
        """Apply one cooldown-gated authority tank collision atomically."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    self.battle_result is not None or
                    player_id != self.bot_authority_id or
                    player_id != self.bot_manifest_authority_id):
                return False
            required = ("bot_id", "target_kind", "target_id", "ram_seq",
                        "damage_to_bot", "damage_to_target")
            if (not all(key in message for key in required) or
                    not _has_finite_fields(message, (
                        "bot_id", "target_id", "ram_seq",
                        "damage_to_bot", "damage_to_target"))):
                return False
            try:
                bot_id = int(message["bot_id"])
                target_id = int(message["target_id"])
                ram_seq = int(message["ram_seq"])
                target_kind = str(message["target_kind"])
            except (TypeError, ValueError):
                return False
            if target_kind not in ("bot", "human") or ram_seq <= 0:
                return False
            bot = self.bot_states.get(bot_id)
            target = (self.bot_states.get(target_id) if target_kind == "bot"
                      else self.players.get(target_id))
            if (bot is None or not bot.get("alive") or target is None or
                    not (target.get("alive") if target_kind == "bot"
                         else target.alive) or
                    (target_kind == "bot" and target_id == bot_id)):
                return False
            target_x = target.get("x") if target_kind == "bot" else target.x
            target_z = target.get("z") if target_kind == "bot" else target.z
            if math.hypot(float(bot["x"]) - float(target_x),
                          float(bot["z"]) - float(target_z)) > 12.5:
                return False
            key = (player_id, bot_id, target_kind, target_id, ram_seq)
            if key in self.bot_reported_rams:
                return False
            if target_kind == "bot":
                first, second = sorted((bot_id, target_id))
                pair = ("bot", first, "bot", second)
            else:
                pair = ("bot", bot_id, "human", target_id)
            now = time.monotonic()
            if now - self.bot_ram_cooldowns.get(pair, -1000.0) <= \
                    RAM_COOLDOWN_SECONDS:
                return False
            damage_to_bot = max(0, min(int(_finite_float(
                message["damage_to_bot"])), 500))
            damage_to_target = max(0, min(int(_finite_float(
                message["damage_to_target"])), 500))
            if damage_to_bot <= 0 and damage_to_target <= 0:
                return False
            self.bot_reported_rams.add(key)
            self.bot_ram_cooldowns[pair] = now
            reason = 2

            bot_combat_before = self._bot_combat_signature(bot)
            applied_bot = min(damage_to_bot, int(bot.get("health", 0)))
            bot["health"] -= applied_bot
            bot["alive"] = bot["health"] > 0
            bot["display_health"] = bot["health"]
            bot["death_reason"] = reason if not bot["alive"] else 0
            self._commit_external_bot_combat(bot, bot_combat_before)
            bot_event = {
                "kind": ("bot_bot_hit" if target_kind == "bot"
                         else "bot_hit"),
                "target_bot": bot_id, "damage": applied_bot,
                "health": bot["health"], "dead": not bot["alive"],
                "attack_reason": reason,
                "death_reason": bot["death_reason"], "source": "ram",
            }
            if target_kind == "bot":
                bot_event["attacker_bot"] = target_id
            else:
                bot_event["attacker"] = target_id
            self.pending_events.append(bot_event)

            if target_kind == "bot":
                target_combat_before = self._bot_combat_signature(target)
                applied_target = min(
                    damage_to_target, int(target.get("health", 0)))
                target["health"] -= applied_target
                target["alive"] = target["health"] > 0
                target["display_health"] = target["health"]
                target["death_reason"] = reason if not target["alive"] else 0
                self._commit_external_bot_combat(
                    target, target_combat_before)
                target_event = {
                    "kind": "bot_bot_hit", "attacker_bot": bot_id,
                    "target_bot": target_id, "damage": applied_target,
                    "health": target["health"],
                    "dead": not target["alive"],
                    "attack_reason": reason,
                    "death_reason": target["death_reason"], "source": "ram",
                }
                target_team = int(target.get("team", 0))
            else:
                applied_target = min(damage_to_target, target.health)
                target.health -= applied_target
                target.alive = target.health > 0
                target.display_health = target.health
                target.death_reason = reason if not target.alive else 0
                target_event = {
                    "kind": "bot_human_hit", "attacker_bot": bot_id,
                    "target": target_id, "damage": applied_target,
                    "health": target.health, "dead": not target.alive,
                    "attack_reason": reason,
                    "death_reason": target.death_reason, "source": "ram",
                }
                target_team = target.team
            self.pending_events.append(target_event)

            if not bot["alive"]:
                bot["death_attacker_kind"] = (
                    "bot" if target_kind == "bot" else "player")
                bot["death_attacker_id"] = target_id
                self._record_frag(
                    bot["death_attacker_kind"], target_id,
                    int(bot.get("team", 0)), "bot", bot_id)
            if not (target.get("alive") if target_kind == "bot"
                    else target.alive):
                if target_kind == "bot":
                    target["death_attacker_kind"] = "bot"
                    target["death_attacker_id"] = bot_id
                else:
                    target.death_attacker_kind = "bot"
                    target.death_attacker_id = bot_id
                self._record_frag(
                    "bot", bot_id, target_team,
                    "bot" if target_kind == "bot" else "player",
                    target_id)
            self._maybe_finish_battle()
            return True

    def update_rules(self, player_id, message):
        with self.lock:
            if (not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    player_id != self.bot_authority_id or
                    self.battle_result is not None):
                return False
            bases = {}
            rules = message.get("rules") or {}
            if not isinstance(rules, dict):
                return False
            incoming = rules.get("bases") or {}
            if not isinstance(incoming, dict):
                return False
            for team in (1, 2):
                raw = incoming.get(str(team), incoming.get(team, {})) or {}
                if not isinstance(raw, dict):
                    raw = {}
                bases[str(team)] = {
                    "points": max(0, min(int(_finite_float(raw.get("points"), 0)), 100)),
                    "time_left": max(
                        0.0, _finite_float(raw.get("time_left"), 0.0)),
                    "invaders": max(
                        0, min(int(_finite_float(raw.get("invaders"), 0)), 30)),
                    "stopped": bool(raw.get("stopped", False)),
                }
            self.rules_state = {"bases": bases}
            return True

    def report_battle_result(self, player_id, message):
        with self.lock:
            if (not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    player_id != self.bot_authority_id or
                    self.battle_result is not None):
                return False
            if "winner" not in message or "reason" not in message:
                return False
            try:
                winner = int(message.get("winner"))
                base_team = int(message.get("base_team", 0))
            except (TypeError, ValueError):
                return False
            reason = _safe_name(message.get("reason"), "")
            if winner not in (0, 1, 2) or base_team not in (0, 1, 2) or not reason:
                return False
            return self._finish_battle(
                winner, reason, base_team)

    def _finish_battle(self, winner, reason, base_team=0):
        """Store and announce a terminal result exactly once."""
        if self.battle_result is not None:
            return False
        self.battle_result = {
            "winner": max(0, min(int(winner), 2)),
            "reason": _safe_name(reason, "battle finished"),
            "base_team": max(0, min(int(base_team), 2)),
        }
        self.result_reset_tick = self.tick + max(
            1, int(round(RESULT_RESET_SECONDS * TICK_HZ)))
        for player in self.players.values():
            player.forward = 0.0
            player.turn = 0.0
        self.pending_events.append(dict(self.battle_result, kind="battle_result"))
        return True

    def _maybe_finish_battle(self):
        """Finish a standard battle once one of the two teams is eliminated.

        Wait for the authority manifest before evaluating.  The stock roster is
        intentionally not counted as alive: it has no health state yet, while
        treating it as dead would end the round during the startup handshake.
        """
        if (not self._combat_accepting() or self.battle_result is not None or
                (not self.roster_finalized and not self.bot_manifest)):
            return False
        if self.bot_roster and not self.bot_manifest:
            return False
        if self.bot_roster:
            roster_ids = {int(entry.get("id", 0))
                          for entry in self.bot_roster}
            manifest_ids = {int(entry.get("id", 0))
                            for entry in self.bot_manifest}
            if not roster_ids.issubset(manifest_ids):
                return False
        participant_teams = {
            int(entry.get("team", 0)) for entry in self.bot_roster}
        participant_teams.update(
            player.team for player in self.players.values()
            if player.connected)
        if not {1, 2}.issubset(participant_teams):
            return False
        alive_teams = set()
        for player in self.players.values():
            if player.connected and player.alive and player.team in (1, 2):
                alive_teams.add(player.team)
        for state in self.bot_states.values():
            if state.get("alive") and state.get("team") in (1, 2):
                alive_teams.add(int(state["team"]))
        if alive_teams == {1, 2}:
            return False
        winner = next(iter(alive_teams)) if len(alive_teams) == 1 else 0
        return self._finish_battle(winner, "team_eliminated", 0)

    def update_input(self, player_id, message):
        with self.lock:
            if not self._message_round_matches(message):
                return False
            player = self.players.get(player_id)
            if player is None or not player.connected:
                return
            if not self._combat_accepting() or self.battle_result is not None:
                player.forward = 0.0
                player.turn = 0.0
                return
            if player.alive:
                if "forward" in message:
                    player.forward = _clamp(_finite_float(message.get("forward")), -1.0, 1.0)
                if "turn" in message:
                    player.turn = _clamp(_finite_float(message.get("turn")), -1.0, 1.0)
                if "speed" in message:
                    player.speed = _clamp(
                        _finite_float(message.get("speed")), -200.0, 200.0)
                if "aim_yaw" in message:
                    player.aim_yaw = _finite_float(message.get("aim_yaw"), player.aim_yaw)
                if "gun_pitch" in message:
                    player.gun_pitch = _clamp(_finite_float(message.get("gun_pitch")), -1.2, 1.2)
                if "x" in message and "z" in message:
                    player.x = _clamp(_finite_float(message.get("x"), player.x), -2000.0, 2000.0)
                    player.y = _clamp(_finite_float(message.get("y"), player.y), -1000.0, 1000.0)
                    player.z = _clamp(_finite_float(message.get("z"), player.z), -2000.0, 2000.0)
                    player.yaw = _finite_float(message.get("yaw"), player.yaw)
                    player.client_position = True
            try:
                fire_seq = int(message.get("fire_seq", player.fire_seq))
            except (TypeError, ValueError):
                fire_seq = player.fire_seq
            try:
                player.shell_index = max(0, min(int(message.get("shell_index", player.shell_index)), 9))
            except (TypeError, ValueError):
                pass
            if (fire_seq == player.fire_seq + 1 and
                    self._combat_accepting() and player.alive):
                player.fire_seq = fire_seq
                self.pending_events.append({
                    "kind": "shot",
                    "attacker": player.player_id,
                    "shot_seq": player.fire_seq,
                    "shell_index": player.shell_index,
                    "world_pose": player.client_position,
                    "x": round(player.x, 4),
                    "y": round(player.y, 4),
                    "z": round(player.z, 4),
                    "yaw": round(player.yaw, 5),
                    "aim_yaw": round(player.aim_yaw, 5),
                    "gun_pitch": round(player.gun_pitch, 5),
                })
            if (("reported_health" in message or
                 "reported_critical" in message) and
                    self._combat_accepting()):
                self._apply_reported_health(player, message)
            if not player.alive:
                # Late packets from the dead client's still-running input loop
                # must not drag its marker away from the server-owned wreck.
                player.forward = 0.0
                player.turn = 0.0

    @staticmethod
    def _commit_external_player_critical(player, critical):
        """Commit damage and open a new owner-report lineage.

        A later repair checkpoint may advance within this lineage, but a
        checkpoint computed before this damage cannot overwrite it.
        """
        if critical is None:
            return None
        player.critical = _critical_state(critical)
        player.critical_revision += 1
        player.critical_report_base_revision = player.critical_revision
        player.critical_ack_seq = 0
        return {
            "critical_revision": player.critical_revision,
            "critical_base_revision":
                player.critical_report_base_revision,
            "critical_ack_seq": player.critical_ack_seq,
        }

    @staticmethod
    def _commit_reported_player_critical(player, critical, message):
        """Accept one monotonic checkpoint from the owning client.

        Socket delivery is not an acknowledgement.  The accepted sequence is
        returned in events and snapshots; only that canonical echo permits the
        client to retire its pending report.
        """
        if critical is None:
            return None, False
        try:
            base_revision = int(message.get(
                "reported_critical_base_revision"))
            report_seq = int(message.get("reported_critical_seq"))
        except (TypeError, ValueError):
            return None, False
        if base_revision != player.critical_report_base_revision:
            return None, False
        if report_seq <= player.critical_ack_seq:
            return {
                "critical_revision": player.critical_revision,
                "critical_base_revision":
                    player.critical_report_base_revision,
                "critical_ack_seq": player.critical_ack_seq,
            }, True
        player.critical = _critical_state(critical)
        player.critical_revision += 1
        player.critical_ack_seq = report_seq
        return {
            "critical_revision": player.critical_revision,
            "critical_base_revision":
                player.critical_report_base_revision,
            "critical_ack_seq": player.critical_ack_seq,
        }, True

    def _apply_reported_health(self, player, message):
        """Relay damage caused by local bots, fire, drowning or collisions.

        Human-versus-human damage uses report_hit() below.  The victim client
        remains authoritative for local simulation damage that the standalone
        server cannot reproduce because it has no proprietary map or bot data.
        Health reports may only move downward during a round.
        """
        try:
            critical = _critical_payload(message.get("reported_critical"))
        except ValueError:
            return False
        health = max(0, min(int(_finite_float(
            message.get("reported_health"), player.health)),
            player.max_health))
        health = min(health, player.health)
        stored_critical = _critical_state(critical)
        old_discrete = _critical_discrete_state(player.critical)
        new_discrete = _critical_discrete_state(stored_critical)
        critical_event_changed = (
            stored_critical is not None and
            (new_discrete != old_discrete or bool(critical.get("events"))))
        critical_commit = None
        if stored_critical is not None:
            if self.client_build == CLIENT_BUILD_0922:
                critical_commit, accepted = (
                    self._commit_reported_player_critical(
                        player, critical, message))
                if not accepted:
                    return False
                if (int(critical_commit["critical_ack_seq"]) !=
                        int(message.get("reported_critical_seq"))):
                    return True
            else:
                # The completed 0.8.2 package predates revisioned repair
                # reports.  Rooms are build-homogeneous, so preserving its
                # protocol does not weaken the strict #1513 path.
                player.critical = stored_critical
        if health == player.health and not critical_event_changed:
            return stored_critical is not None
        was_alive = player.alive
        damage = player.health - health
        player.health = health
        try:
            reason = max(0, min(int(message.get("reported_reason", 0)), 255))
        except (TypeError, ValueError):
            reason = 0
        if health == 0:
            player.alive = False
            player.death_reason = reason
            display_health = max(0, min(int(_finite_float(
                message.get("reported_display_health"), health)),
                player.max_health))
            player.display_health = display_health
        event = {
            "kind": "health",
            "target": player.player_id,
            "damage": damage,
            "health": player.health,
            "dead": not player.alive,
            "source": "client_simulation",
            "attack_reason": reason,
            "death_reason": player.death_reason if not player.alive else 0,
            "display_health": (player.display_health
                               if not player.alive else player.health),
        }
        if critical is not None:
            event["critical"] = critical
            if critical_commit is not None:
                event.update(critical_commit)
        try:
            attacker_id = int(message.get("reported_attacker", 0))
        except (TypeError, ValueError):
            attacker_id = 0
        try:
            attacker_bot = int(message.get("reported_attacker_bot", 0))
        except (TypeError, ValueError):
            attacker_bot = 0
        if attacker_id in self.players:
            event["attacker"] = attacker_id
        elif attacker_bot in self.bot_states:
            event["attacker_bot"] = attacker_bot
        self.pending_events.append(event)
        if was_alive and not player.alive:
            if "attacker" in event:
                player.death_attacker_kind = "player"
                player.death_attacker_id = int(event["attacker"])
                self._record_frag(
                    "player", event["attacker"], player.team,
                    "player", player.player_id)
            elif "attacker_bot" in event:
                player.death_attacker_kind = "bot"
                player.death_attacker_id = int(event["attacker_bot"])
                self._record_frag(
                    "bot", event["attacker_bot"], player.team,
                    "player", player.player_id)
        self._maybe_finish_battle()
        return True

    def report_hit(self, player_id, message):
        """Apply a map/armor hit resolved by the firing 0.8.2 client.

        The server validates identity, team, range and one report per target
        per shot, then
        owns the shared HP result.  This reuses the existing client armor and
        shell collision logic instead of the old fixed 100-HP cone test.
        """
        with self.lock:
            attacker = self.players.get(player_id)
            if (not self._message_round_matches(message) or
                    not self._combat_accepting() or
                    self.battle_result is not None or
                    attacker is None or not attacker.connected or not attacker.alive):
                return False
            if not all(key in message for key in
                       ("target", "shot_seq", "damage")):
                return False
            if (not _has_finite_fields(
                    message, ("target", "shot_seq", "damage")) or
                    _finite_float(message.get("damage"), -1.0) < 0.0):
                return False
            try:
                critical = _critical_payload(message.get("critical"))
            except ValueError:
                return False
            try:
                shot_seq = int(message.get("shot_seq", 0))
                target_id = int(message.get("target", 0))
            except (TypeError, ValueError):
                return False
            splash = bool(message.get("splash", False))
            hit_key = (("shot", shot_seq, "player", target_id)
                       if splash else ("shot", shot_seq))
            if shot_seq <= 0 or shot_seq > attacker.fire_seq or hit_key in attacker.reported_hits:
                return False
            target = self.players.get(target_id)
            if target is None or not target.connected or not target.alive:
                return False
            if target.player_id == attacker.player_id and not splash:
                return False
            distance = math.hypot(target.x - attacker.x, target.z - attacker.z)
            if distance > 5000.0:
                return False
            modern_proposal = (
                self.client_build == CLIENT_BUILD_0922 and
                critical is not None)
            critical_accepted = True
            hull_damage = None
            if modern_proposal:
                try:
                    hull_damage, critical_accepted = (
                        _critical_proposal_admission(
                            message,
                            target.critical_report_base_revision,
                            target.critical_ack_seq))
                except ValueError:
                    return False
            attacker.reported_hits.add(hit_key)
            damage = max(0, min(int(_finite_float(message.get("damage"), 0)), 5000))
            if modern_proposal and not critical_accepted:
                damage = hull_damage
            applied_damage = min(damage, target.health)
            target.health -= applied_damage
            if target.health == 0:
                target.alive = False
            target.display_health = target.health
            admitted_critical = (
                critical if not modern_proposal or critical_accepted else None)
            critical_commit = self._commit_external_player_critical(
                target, admitted_critical)
            try:
                shot_result = max(0, min(int(message.get("shot_result", 2)), 2))
            except (TypeError, ValueError):
                shot_result = 2
            event = {
                "kind": "hit",
                "attacker": attacker.player_id,
                "target": target.player_id,
                "shot_seq": shot_seq,
                "shell_index": attacker.shell_index,
                "shot_result": shot_result,
                "damage": applied_damage,
                "health": target.health,
                "dead": not target.alive,
                "attack_reason": 0,
                "death_reason": 0,
                "source": "shot",
                "splash": splash,
                "world_pose": True,
                "x": round(_clamp(_finite_float(message.get("x"), target.x), -2000.0, 2000.0), 4),
                "y": round(_clamp(_finite_float(message.get("y"), target.y + 1.0), -1000.0, 1000.0), 4),
                "z": round(_clamp(_finite_float(message.get("z"), target.z), -2000.0, 2000.0), 4),
            }
            if critical is not None:
                if modern_proposal:
                    event["critical_accepted"] = critical_accepted
                if admitted_critical is not None:
                    event["critical"] = admitted_critical
                    event.update(critical_commit)
                elif modern_proposal:
                    event["critical_reject_reason"] = (
                        "stale_target_state")
                    event.update({
                        "critical_revision": target.critical_revision,
                        "critical_base_revision":
                            target.critical_report_base_revision,
                        "critical_ack_seq": target.critical_ack_seq,
                    })
            self.pending_events.append(event)
            if not target.alive:
                target.death_attacker_kind = "player"
                target.death_attacker_id = int(attacker.player_id)
                self._record_frag(
                    "player", attacker.player_id, target.team,
                    "player", target.player_id)
            self._maybe_finish_battle()
            return True

    def _record_frag(self, attacker_kind, attacker_id, victim_team,
                     victim_kind, victim_id):
        """Copy 0.8.2 +1 enemy / -1 ally frag and team-killer law."""
        if (attacker_kind == victim_kind and
                int(attacker_id) == int(victim_id)):
            return False
        if attacker_kind == "player":
            actor = self.players.get(int(attacker_id))
            if actor is None:
                return False
            actor_team = actor.team
            delta = -1 if actor_team == int(victim_team) else 1
            actor.frags += delta
            if delta < 0:
                actor.team_killer = True
            frags = actor.frags
            team_killer = actor.team_killer
        elif attacker_kind == "bot":
            actor = self.bot_states.get(int(attacker_id))
            if actor is None:
                return False
            actor_team = int(actor.get("team", 0))
            delta = -1 if actor_team == int(victim_team) else 1
            actor["frags"] = int(actor.get("frags", 0)) + delta
            frags = actor["frags"]
            # The copied 0.8.2 bot path adjusts frags but only the human
            # player path publishes the blue/team-killer state.
            team_killer = False
        else:
            return False
        self.pending_events.append({
            "kind": "vehicle_statistics",
            "actor_kind": attacker_kind,
            "actor_id": int(attacker_id),
            "frags": int(frags),
            "team_killer": bool(team_killer),
        })
        return True

    def _apply_movement(self, player, dt):
        if not player.alive or self.battle_result is not None:
            return
        if not player.client_position:
            player.yaw += player.turn * 0.85 * dt
            speed = 14.0 * player.forward
            player.x += math.sin(player.yaw) * speed * dt
            player.z += math.cos(player.yaw) * speed * dt
            player.x = _clamp(player.x, -220.0, 220.0)
            player.z = _clamp(player.z, -220.0, 220.0)

    def _combat_accepting(self):
        """Fence #1513 combat until the shared countdown becomes live.

        The 0.8.2 client already owns its proven local PREBATTLE guard and the
        original v5 server accepted its packets as soon as the room entered
        ``battle``.  Keep that wire behavior unchanged; only #1513 uses the
        server-owned load barrier and countdown clock added by this port.
        """
        return (self.phase == "battle" and
                (self.client_build != CLIENT_BUILD_0922 or
                 self.tick >= int(round(PREBATTLE_SECONDS * TICK_HZ))))

    def _map_rule_data(self):
        return (get_tactical_map(self.map_name) or
                _MAPS_0922_DATA.get(self.map_name) or {})

    def _update_capture(self):
        """Copy the 0.8.2 standard-mode 50 m, 1 Hz capture law."""
        if (not self._combat_accepting() or
                self.tick % max(1, int(round(TICK_HZ))) != 0 or
                self.battle_result is not None):
            return False
        bases = self.capture_bases or (self._map_rule_data().get('bases') or {})
        if not bases:
            return False
        vehicles = {1: [], 2: []}
        for player in self.players.values():
            if (player.connected and player.participating and player.alive and
                    player.team in vehicles):
                vehicles[player.team].append((player.x, player.z))
        for state in self.bot_states.values():
            team = int(state.get('team', 0))
            if state.get('alive') and team in vehicles:
                vehicles[team].append((state['x'], state['z']))
        changed = False
        for base_team in (1, 2):
            raw_base = bases.get(base_team, bases.get(str(base_team)))
            if raw_base is None:
                continue
            if isinstance(raw_base, dict):
                base_positions = [(raw_base.get('x'), raw_base.get('z'))]
            elif (isinstance(raw_base, (list, tuple)) and len(raw_base) >= 2 and
                  not isinstance(raw_base[0], (list, tuple, dict))):
                base_positions = [(raw_base[0], raw_base[1])]
            else:
                base_positions = list(raw_base or ())
            normalized = []
            for point in base_positions:
                try:
                    if isinstance(point, dict):
                        normalized.append((float(point['x']), float(point['z'])))
                    else:
                        normalized.append((float(point[0]), float(point[1])))
                except (KeyError, TypeError, ValueError, IndexError):
                    continue
            if not normalized:
                continue
            invading_team = 3 - base_team
            invaders = sum(1 for x, z in vehicles[invading_team]
                           if any((x - bx) ** 2 + (z - bz) ** 2 <= 2500.0
                                  for bx, bz in normalized))
            defenders = sum(1 for x, z in vehicles[base_team]
                            if any((x - bx) ** 2 + (z - bz) ** 2 <= 2500.0
                                   for bx, bz in normalized))
            state = self.rules_state['bases'][str(base_team)]
            previous = dict(state)
            if invaders > 0 and defenders == 0:
                state['points'] = min(
                    100, int(state.get('points', 0)) + min(invaders, 3))
            elif invaders == 0:
                state['points'] = 0
            state['invaders'] = invaders
            rate = min(invaders, 3)
            state['time_left'] = (
                float(max(0, 100 - state['points'])) / float(rate)
                if rate > 0 else 0.0)
            state['stopped'] = defenders > 0
            changed = changed or state != previous
            if state['points'] >= 100:
                self._finish_battle(
                    invading_team, 'base captured', base_team)
                break
        return changed

    def tick_once(self, dt):
        reset_message = None
        had_pending_live = False
        failed_live_recipients = []
        with self.lock:
            if self.pending_live_message is not None:
                pending = self.pending_live_message
                self.pending_live_message = None
                had_pending_live = True
                if not isinstance(pending, dict):
                    raise RuntimeError("pending battle_live barrier is invalid")
                barrier_round = pending.get("round_id")
                barrier_message = pending.get("message")
                barrier_recipients = pending.get("recipients")
                if (not isinstance(barrier_message, dict) or
                        barrier_message.get("type") != "battle_live" or
                        barrier_message.get("round_id") != barrier_round or
                        not isinstance(barrier_recipients, tuple)):
                    raise RuntimeError(
                        "pending battle_live barrier contract is invalid")
                if (barrier_round == self.round_id and
                        self.phase == "battle"):
                    message = dict(barrier_message)
                    message["state_revision"] = self.state_revision
                    recipients = tuple(
                        player for player in barrier_recipients
                        if (self.players.get(player.player_id) is player and
                            player.connected and player.participating))
                    # Keep the round/recipient check and each send in one
                    # state-lock critical section.  A result reset preserves
                    # Player objects, so merely binding object references is
                    # insufficient: without this lock, the same connection
                    # could enter the next round between validation and send.
                    for player in recipients:
                        if not player.send(message):
                            failed_live_recipients.append(player.player_id)
            if (self.phase == "battle" and self.battle_result is not None and
                    self.result_reset_tick is not None and
                    self.tick + 1 >= self.result_reset_tick):
                self._reset_round()
                reset_message = self.lobby_message()
        if reset_message is not None:
            self.broadcast(reset_message)
            return
        if had_pending_live:
            for player_id in failed_live_recipients:
                self.remove_player(player_id)
            # A round reset can invalidate a barrier after it was queued.  It
            # still consumes this tick: a newly queued round must publish its
            # own tick-zero barrier before any snapshot can advance it.  A
            # valid barrier likewise remains its own ordered wire transition.
            return
        with self.lock:
            if self.phase != "battle":
                return
            self.tick += 1
            if (self.battle_result is None and
                    self.tick >= int(round(
                        (PREBATTLE_SECONDS + BATTLE_DURATION_SECONDS) *
                        TICK_HZ))):
                self._finish_battle(0, "battle_timeout", 0)
            self._update_capture()
            for player in list(self.players.values()):
                self._apply_movement(player, dt)
            if self.battle_result is None:
                self.bot_orders = self.bot_planner.build_orders(
                    self.bot_manifest, list(self.bot_states.values()),
                    [self._public_player(p) for p in self.players.values() if p.connected],
                    time.monotonic())
            events = []
            for ordinal, pending in enumerate(self.pending_events):
                self._validate_combat_event_for_wire(pending)
                event = dict(pending)
                event["event_id"] = "%d:%d:%d" % (
                    self.round_id, self.tick, ordinal)
                events.append(event)
            self.pending_events = []
            snapshot = {
                "type": "snapshot",
                "protocol": PROTOCOL_VERSION,
                "server_tick": self.tick,
                "round_id": self.round_id,
                "map": self.map_name,
                "bot_authority_id": self.bot_authority_id,
                "players": [self._public_player(p) for p in self.players.values() if p.connected],
                "bots": [self.bot_states[key] for key in sorted(self.bot_states)],
                "bot_manifest": list(self.bot_manifest),
                "bot_order_revision": self.bot_orders["revision"],
                "rules": self.rules_state,
                "battle_result": self.battle_result,
                "destructible_revision": self.destructible_revision,
                "timing": self._timing_payload(),
            }
            recipients = list(self.players.values())
        if events:
            for event in events:
                if event.get("kind") == "shot":
                    _server_log("SHOT attacker=%s seq=%s shell=%s" % (
                        event.get("attacker"), event.get("shot_seq"), event.get("shell_index")))
                elif event.get("kind") == "hit":
                    _server_log("HIT attacker=%s target=%s damage=%s health=%s dead=%s" % (
                        event.get("attacker"), event.get("target"), event.get("damage"),
                        event.get("health"), event.get("dead")))
                elif event.get("kind") == "health":
                    _server_log("HEALTH target=%s damage=%s health=%s dead=%s source=%s" % (
                        event.get("target"), event.get("damage"), event.get("health"),
                        event.get("dead"), event.get("source")))
                elif event.get("kind") in ("bot_hit", "bot_human_hit", "bot_bot_hit"):
                    _server_log("BOT COMBAT kind=%s attacker=%s target=%s damage=%s health=%s dead=%s" % (
                        event.get("kind"), event.get("attacker", event.get("attacker_bot")),
                        event.get("target", event.get("target_bot")), event.get("damage"),
                        event.get("health"), event.get("dead")))
                elif event.get("kind") == "authority":
                    _server_log("BOT AUTHORITY player_id=%s" % event.get("player_id"))
                elif event.get("kind") == "battle_result":
                    _server_log("BATTLE RESULT winner=%s reason=%s base_team=%s" % (
                        event.get("winner"), event.get("reason"), event.get("base_team")))
                elif event.get("kind") == "destructible":
                    _server_log(
                        "DESTRUCTIBLE kind=%s chunk=%s item=%s by=%s" % (
                            event.get("destructible_kind"),
                            event.get("chunk_id"), event.get("item_index"),
                            event.get("reported_by")))
            self.broadcast({"type": "events", "protocol": PROTOCOL_VERSION,
                            "round_id": self.round_id,
                            "server_tick": self.tick, "events": events})
        # Ordered combat causes must reach the client before the durable state
        # they produced.  Otherwise #1513 observes the new HP/death first and
        # suppresses hit direction, attacker attribution and the fatal shot.
        for player in recipients:
            outgoing = snapshot
            needs_orders = (
                player.bot_order_revision_sent !=
                self.bot_orders["revision"])
            needs_destructibles = (
                player.destructible_revision_sent !=
                self.destructible_revision)
            if needs_orders or needs_destructibles:
                outgoing = dict(snapshot)
            if needs_orders:
                outgoing["bot_orders"] = list(self.bot_orders["orders"])
            if needs_destructibles:
                outgoing["destructibles"] = list(
                    self.destructibles.values())
            if not player.send(outgoing):
                self.remove_player(player.player_id)

    @staticmethod
    def _public_player(player):
        result = {
            "id": player.player_id,
            "name": player.name,
            "vehicle": player.vehicle,
            "team": player.team,
            "slot": player.slot,
            "world_pose": player.client_position,
            "spawn_x": BattleState._spawn_x_for(player.slot),
            "spawn_z": BattleState._spawn_z_for(player.team),
            "x": round(player.x, 4),
            "y": round(player.y, 4),
            "z": round(player.z, 4),
            "yaw": round(player.yaw, 5),
            "aim_yaw": round(player.aim_yaw, 5),
            "gun_pitch": round(player.gun_pitch, 5),
            "forward": round(player.forward, 4),
            "turn": round(player.turn, 4),
            "speed": round(player.speed, 4),
            "fire_seq": player.fire_seq,
            "shell_index": player.shell_index,
            "health": player.health,
            "max_health": player.max_health,
            "alive": player.alive,
            "death_reason": player.death_reason,
            "display_health": (player.health if player.display_health is None
                               else player.display_health),
            "frags": player.frags,
            "team_killer": player.team_killer,
            "death_attacker_kind": player.death_attacker_kind,
            "death_attacker_id": player.death_attacker_id,
            "critical_revision": player.critical_revision,
            "critical_base_revision":
                player.critical_report_base_revision,
            "critical_ack_seq": player.critical_ack_seq,
        }
        if player.critical:
            result["critical"] = player.critical
        return result

    def broadcast(self, message):
        with self.lock:
            players = list(self.players.values())
        for player in players:
            if not player.send(message):
                self.remove_player(player.player_id)

    def _broadcast_current_roster_locked(self):
        """Send a roster that remains current after every observed send failure."""
        while True:
            message = self.lobby_message()
            recipients = tuple(
                player for player in self.players.values()
                if player.connected)
            failed = []
            for player in recipients:
                if not player.send(message):
                    failed.append(player.player_id)
            if not failed:
                return message
            # Player.send() only marks the failed connection.  Remove every
            # failed member before rebuilding the next revision so the last
            # roster received by every surviving socket is authoritative.
            for player_id in failed:
                self.remove_player(player_id)

    def broadcast_current_roster(self):
        with self.lock:
            return self._broadcast_current_roster_locked()

    def broadcast_loading_transition(self, message):
        """Publish one #1513 loading transition with strict membership repair."""
        with self.lock:
            if self.client_build != CLIENT_BUILD_0922:
                raise RuntimeError(
                    "loading transition is only valid for the #1513 client")
            if not isinstance(message, dict):
                raise RuntimeError("loading transition must be an object")
            kind = message.get("type")
            if kind not in ("battle_start", "snapshot"):
                raise RuntimeError("unsupported loading transition: %s" % kind)

            # A different sender can discover a dead connection between the
            # state mutation and this publisher acquiring the lock.  Retire it
            # before rebuilding the transition rather than knowingly putting
            # stale authority or membership on another socket.
            disconnected = [
                player.player_id for player in self.players.values()
                if not player.connected]
            for player_id in disconnected:
                self.remove_player(player_id)

            if (self.phase != "loading" or
                    message.get("round_id") != self.round_id):
                self._broadcast_current_roster_locked()
                return False

            if kind == "battle_start":
                outgoing = dict(message)
                connected = [
                    player for player in self.players.values()
                    if player.connected and player.participating]
                outgoing.update({
                    "client_build": self.client_build,
                    "round_id": self.round_id,
                    "state_revision": self.state_revision,
                    "map": self.map_name,
                    "host_player_id": self.host_player_id,
                    "phase": self.phase,
                    "players": [self._public_player(player)
                                for player in connected],
                    "bots": list(self.bot_roster),
                    "bot_authority_id": self.bot_authority_id,
                    "bot_manifest": list(self.bot_manifest),
                    "bot_order_revision": self.bot_orders["revision"],
                    "bot_orders": list(self.bot_orders["orders"]),
                    "rules": self.rules_state,
                    "battle_result": self.battle_result,
                    "destructible_revision": self.destructible_revision,
                    "destructibles": list(self.destructibles.values()),
                })
            else:
                outgoing = self.loading_snapshot()
                if outgoing is None:
                    # Authority changed before publication.  Its old manifest
                    # is no longer canonical; publish the new authority and
                    # wait for that client to submit a fresh manifest.
                    self._broadcast_current_roster_locked()
                    return False

            recipients = tuple(
                player for player in self.players.values()
                if player.connected and player.participating)
            failed = []
            # Defer removals until every surviving recipient has observed the
            # transition.  If any send fails, a revisioned roster repair below
            # becomes the final membership message on every surviving stream.
            for player in recipients:
                if not player.send(outgoing):
                    failed.append(player.player_id)
            for player_id in failed:
                self.remove_player(player_id)
            if failed:
                self._broadcast_current_roster_locked()
            return True


class ClientHandler(socketserver.BaseRequestHandler):
    def handle(self):
        server = self.server.game_server
        conn = self.request
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(10.0)
        player = None
        buffer = b""
        _server_log("TCP connection from %s:%d" % self.client_address)
        try:
            while b"\n" not in buffer and len(buffer) < MAX_LINE_BYTES:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buffer += chunk
            line, _, buffer = buffer.partition(b"\n")
            hello = json.loads(line.decode("utf-8"))
            try:
                hello_protocol = (int(hello.get("protocol", -1))
                                  if isinstance(hello, dict) else -1)
            except (TypeError, ValueError):
                hello_protocol = -1
            if (not isinstance(hello, dict) or hello.get("type") != "hello" or
                    hello_protocol != PROTOCOL_VERSION):
                self._send_raw(conn, {"type": "error", "code": "protocol", "message": "protocol mismatch"})
                _server_log("Rejected %s:%d: protocol mismatch" % self.client_address)
                return
            # Publish membership and this connection's welcome atomically.
            # Otherwise an existing handler can start a battle after add_player
            # releases the state lock but before this handler sends welcome,
            # making battle_start the new client's first state message.
            welcomed = False
            with server.state.lock:
                player, join_error = server.state.add_player(
                    conn, self.client_address, hello)
                if player is not None:
                    welcomed = player.send({
                        "type": "welcome",
                        "protocol": PROTOCOL_VERSION,
                        "client_build": server.state.client_build,
                        "player_id": player.player_id,
                        "name": player.name,
                        "vehicle": player.vehicle,
                        "team": player.team,
                        "slot": player.slot,
                        "max_health": player.max_health,
                        "map": server.state.map_name,
                        "map_pool": list(server.state._active_map_pool()),
                        "host_player_id": server.state.host_player_id,
                        "phase": server.state.phase,
                        "round_id": server.state.round_id,
                        "state_revision": server.state.state_revision,
                        "spawn": {"x": player.x, "y": player.y, "z": player.z, "yaw": player.yaw},
                        "bot_authority_id": server.state.bot_authority_id,
                    })
            if player is None:
                messages = {
                    "battle_in_progress": "battle already in progress",
                    "full": "server is full",
                    "unsupported_client_build": "unsupported or missing client build",
                    "incompatible_client_build": "this room is using a different client build",
                    "map_not_available_for_client": "the fixed server map is unavailable in this client build",
                }
                message = messages.get(join_error, "join rejected")
                self._send_raw(conn, {"type": "error", "code": join_error, "message": message})
                _server_log("Rejected %s:%d: %s" % (self.client_address[0], self.client_address[1], message))
                return
            _server_log("JOIN id=%d name=%s build=%s vehicle=%s max_hp=%d team=%d address=%s:%d phase=%s players=%d" % (
                player.player_id,
                player.name,
                server.state.client_build,
                player.vehicle,
                player.max_health,
                player.team,
                self.client_address[0],
                self.client_address[1],
                server.state.phase,
                len(server.state.players),
            ))
            if not welcomed:
                return
            server.state.broadcast(server.state.lobby_message())
            current_battle = server.state.current_battle_message()
            if current_battle is not None:
                player.send(current_battle)
                _server_log("LATE JOIN id=%d round=%d map=%s" % (
                    player.player_id,
                    current_battle["round_id"],
                    current_battle["map"],
                ))
            conn.settimeout(0.5)
            while True:
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > MAX_LINE_BYTES * 4:
                    break
                while b"\n" in buffer:
                    line, _, buffer = buffer.partition(b"\n")
                    if not line:
                        continue
                    if len(line) > MAX_LINE_BYTES:
                        return
                    message = json.loads(line.decode("utf-8"))
                    if not isinstance(message, dict):
                        continue
                    message_type = message.get("type")
                    if (message_type in ROUND_SCOPED_MESSAGE_TYPES and
                            not server.state._message_round_matches(message)):
                        continue
                    if message_type == "input":
                        server.state.update_input(player.player_id, message)
                    elif message_type == "hit_report":
                        if not server.state.report_hit(player.player_id, message):
                            _server_log("HIT REPORT rejected attacker=%d target=%s seq=%s" % (
                                player.player_id, message.get("target"), message.get("shot_seq")))
                    elif message_type == "bot_manifest":
                        if server.state.update_bot_manifest(player.player_id, message):
                            _server_log("BOT MANIFEST authority=%d bots=%d" % (
                                player.player_id, len(server.state.bot_manifest)))
                            loading_snapshot = server.state.loading_snapshot()
                            if loading_snapshot is not None:
                                server.state.broadcast_loading_transition(
                                    loading_snapshot)
                            live = server.state.activate_battle_if_ready()
                            if live is not None:
                                _server_log("BATTLE LIVE round=%d countdown=%.1fs players=%d" % (
                                    live["round_id"], live["countdown_seconds"],
                                    len(server.state.players)))
                        else:
                            _server_log("BOT MANIFEST rejected sender=%d" % player.player_id)
                    elif message_type == "bot_state":
                        server.state.update_bot_states(player.player_id, message)
                    elif message_type == "bot_observation":
                        server.state.update_bot_observation(player.player_id, message)
                    elif message_type == "bot_hit_report":
                        if not server.state.report_bot_hit(player.player_id, message):
                            _server_log("BOT HIT rejected attacker=%d target=%s seq=%s" % (
                                player.player_id, message.get("target"), message.get("shot_seq")))
                    elif message_type == "bot_human_hit":
                        if not server.state.report_bot_human_hit(player.player_id, message):
                            _server_log("BOT HUMAN HIT rejected authority=%d target=%s" % (
                                player.player_id, message.get("target")))
                    elif message_type == "bot_ram_report":
                        if not server.state.report_bot_ram(
                                player.player_id, message):
                            _server_log(
                                "BOT RAM rejected authority=%d target=%s:%s" % (
                                    player.player_id,
                                    message.get("target_kind"),
                                    message.get("target_id")))
                    elif message_type == "rules_state":
                        server.state.update_rules(player.player_id, message)
                    elif message_type == "destructible":
                        if not server.state.report_destructible(
                                player.player_id, message):
                            _server_log(
                                "DESTRUCTIBLE rejected sender=%d chunk=%s item=%s" % (
                                    player.player_id,
                                    message.get("chunk_id"),
                                    message.get("item_index")))
                    elif message_type == "battle_result":
                        if not server.state.report_battle_result(player.player_id, message):
                            _server_log("BATTLE RESULT rejected sender=%d" % player.player_id)
                    elif message_type == "leave_battle":
                        if server.state.leave_battle_and_publish(
                                player.player_id, message):
                            _server_log("BATTLE LEAVE id=%d round=%d" % (
                                player.player_id, server.state.round_id))
                    elif message_type == "battle_ready":
                        live_message = server.state.mark_battle_ready(
                            player.player_id, message)
                        if live_message is not None:
                            _server_log(
                                "BATTLE LIVE round=%d countdown=%ss players=%d" % (
                                    live_message["round_id"],
                                    live_message["countdown_seconds"],
                                    len(server.state.players)))
                    elif message_type == "start_battle":
                        start_message, start_error = server.state.request_start(
                            player.player_id, message.get("map"))
                        if start_message is None:
                            player.send({
                                "type": "start_denied",
                                "protocol": PROTOCOL_VERSION,
                                "round_id": server.state.round_id,
                                "state_revision": server.state.state_revision,
                                "code": start_error,
                                "players": len(server.state.players),
                            })
                            _server_log("START denied for id=%d: %s" % (player.player_id, start_error))
                        else:
                            _server_log("BATTLE LOADING round=%d map=%s players=%d requested_by=%s" % (
                                start_message["round_id"],
                                start_message["map"],
                                len(start_message["players"]),
                                player.name,
                            ))
                            if start_message.get("phase") == "loading":
                                server.state.broadcast_loading_transition(
                                    start_message)
                            else:
                                # Preserve the mature 0.8.2 immediate-battle
                                # publisher exactly; only #1513 has a loading
                                # membership barrier to repair.
                                server.state.broadcast(start_message)
                    elif message_type == "ping":
                        player.send({
                            "type": "pong",
                            "seq": message.get("seq"),
                            "client_time": message.get("client_time"),
                            "server_time": time.time(),
                        })
                    elif message_type == "leave":
                        return
        except (TypeError, ValueError, UnicodeError, json.JSONDecodeError) as error:
            _server_log("Invalid message from %s:%d: %s" % (self.client_address[0], self.client_address[1], error))
        except (ConnectionError, OSError) as error:
            _server_log("Connection error from %s:%d: %s" % (self.client_address[0], self.client_address[1], error))
        finally:
            if player is not None:
                removed, reset = server.state.remove_player(player.player_id)
                if removed is not None:
                    _server_log("LEAVE id=%d name=%s remaining=%d" % (
                        removed.player_id, removed.name, len(server.state.players)))
                if reset:
                    _server_log("ROOM RESET round=%d map=%s" % (server.state.round_id, server.state.map_name))
                server.state.broadcast(server.state.lobby_message())
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _send_raw(conn, message):
        payload = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        conn.sendall(payload)


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_server(host, port, map_name, max_players):
    state = BattleState(map_name=map_name, max_players=max_players)
    tcp_server = ThreadedTCPServer((host, port), ClientHandler)
    tcp_server.game_server = type("GameServer", (), {"state": state})()

    def tick_loop():
        interval = 1.0 / TICK_HZ
        next_tick = time.monotonic()
        while state.running:
            next_tick += interval
            state.tick_once(min(interval, 0.1))
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.monotonic()

    thread = threading.Thread(target=tick_loop, name="battle-tick", daemon=True)
    thread.start()
    _server_log("LAN battle server listening on %s:%d (map=%s, max_players=%d)" % (
        host, port, state.map_name, state.max_players))
    _server_log("Ready: clients click Battle! to join, choose a map, then click START BATTLE")
    try:
        tcp_server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping server", flush=True)
    finally:
        state.running = False
        tcp_server.shutdown()
        tcp_server.server_close()


def main():
    parser = argparse.ArgumentParser(description="LAN server for the offhangar network MVP")
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default: all interfaces)")
    parser.add_argument("--port", type=int, default=28782, help="TCP port (default: 28782)")
    parser.add_argument(
        "--map", dest="map_name", default=DEFAULT_MAP,
        choices=(DEFAULT_MAP,) + ALL_MAP_POOL,
        help="standard map name, or server_random")
    parser.add_argument("--max-players", type=int, default=30, help="maximum connected clients")
    args = parser.parse_args()
    run_server(args.host, args.port, args.map_name, args.max_players)


if __name__ == "__main__":
    main()
