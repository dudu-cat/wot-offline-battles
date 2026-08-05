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
import socket
import socketserver
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from server_bot_ai import BotPlanner


PROTOCOL_VERSION = 5
TICK_HZ = 30.0
RESULT_RESET_SECONDS = 3.0
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
MAP_POOL_0922 = (
    "01_karelia",
    "02_malinovka",
    "04_himmelsdorf",
    "05_prohorovka",
    "06_ensk",
    "07_lakeville",
    "08_ruinberg",
    "10_hills",
    "11_murovanka",
    "13_erlenberg",
    "14_siegfried_line",
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
    "44_north_america",
    "45_north_america",
    "47_canada_a",
    "59_asia_great_wall",
    "63_tundra",
    "73_asia_korea",
    "83_kharkiv",
    "84_winter",
    "86_himmelsdorf_winter",
    "92_stalingrad",
    "95_lost_city",
    "100_thepit",
    "101_dday",
    "103_ruinberg_winter",
    "112_eiffel_tower_ctf",
    "114_czech",
    "217_er_alaska",
)
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
    "bot_observation", "bot_hit_report", "bot_human_hit", "rules_state",
    "battle_result", "leave_battle",
))


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
    fire_seq: int = 0
    shell_index: int = 0
    reported_hits: set = field(default_factory=set, repr=False)
    health: int = 1000
    max_health: int = 1000
    alive: bool = True
    client_position: bool = False
    connected: bool = True
    participating: bool = True
    bot_order_revision_sent: int = -1
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
        self.rules_state = {"bases": {"1": {"points": 0, "stopped": False},
                                      "2": {"points": 0, "stopped": False}}}
        self.battle_result = None
        self.result_reset_tick = None
        self.roster_finalized = False
        self.pending_events = []

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
            (self.phase != "battle" or p.participating))
        old = self.bot_authority_id
        self.bot_authority_id = connected[0] if connected else None
        if old != self.bot_authority_id and self.phase == "battle":
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
            reset = False
            if self.players and self.phase == "battle":
                self._maybe_finish_battle()
                self._finish_abandoned_battle()
            if not self.players and self.phase == "battle":
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
            player.participating = True
            player.forward = 0.0
            player.turn = 0.0
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
        self.next_id = max([player.player_id for player in self.players.values()] or [0]) + 1
        self.bot_roster = self._new_bot_roster()
        self.bot_authority_id = None
        self.bot_manifest_authority_id = None
        self.bot_manifest = []
        self.bot_states = {}
        self.bot_planner.reset()
        self.bot_orders = {"revision": 0, "orders": []}
        self.bot_reported_hits = set()
        self.rules_state = {"bases": {"1": {"points": 0, "stopped": False},
                                       "2": {"points": 0, "stopped": False}}}
        self.battle_result = None
        self.result_reset_tick = None
        self.roster_finalized = False
        self.pending_events = []
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
            self.phase = "battle"
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
                "delay": 0.75,
                "players": [self._public_player(p) for p in connected],
                "bots": list(self.bot_roster),
                "bot_authority_id": self.bot_authority_id,
                "bot_manifest": list(self.bot_manifest),
                "bot_order_revision": self.bot_orders["revision"],
                "bot_orders": list(self.bot_orders["orders"]),
                "rules": self.rules_state,
                "battle_result": self.battle_result,
            }, None

    def leave_battle(self, player_id, message):
        """Retire a client from one round while keeping its lobby socket."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    self.phase != "battle"):
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
            self.pending_events.append({
                "kind": "health",
                "target": player.player_id,
                "damage": previous_health,
                "health": 0,
                "dead": True,
                "source": "player_left",
            })
            if player_id == self.bot_authority_id:
                self._elect_bot_authority()
            self._maybe_finish_battle()
            self._finish_abandoned_battle()
            return True

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
            }

    def update_bot_manifest(self, player_id, message):
        """Accept the canonical bot lineup from the elected simulation client."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    self.phase != "battle" or self.battle_result is not None or
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
                entry = {
                    "id": bot_id,
                    "team": identity["team"],
                    "slot": identity["slot"],
                    "name": identity["name"],
                    "vehicle": _safe_vehicle(raw.get("vehicle"), "ussr:R11_MS-1"),
                    "max_health": max_health,
                    "health": health,
                    "profile": self._sanitize_bot_profile(raw.get("profile")),
                    "route": self._sanitize_bot_route(raw.get("route")),
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
        for point in waypoints[:16]:
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
                    self.phase != "battle" or player_id != self.bot_authority_id or
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
        return {
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
            "fire_seq": fire_seq,
            "shell_index": max(0, min(int(_finite_float(raw.get("shell_index"), 0)), 9)),
            "health": reported_health,
            "max_health": max_health,
            "alive": bool(raw.get("alive", reported_health > 0)) and reported_health > 0,
        }

    def update_bot_states(self, player_id, message):
        with self.lock:
            if (not self._message_round_matches(message) or
                    self.phase != "battle" or self.battle_result is not None or
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
                current = self._sanitize_bot_state(raw, identity, previous)
                previous_fire = int((previous or {}).get("fire_seq", 0))
                if current["fire_seq"] > previous_fire + 1:
                    return False
                next_states[bot_id] = current
                if (current["alive"] and
                        (previous is None or previous.get("alive")) and
                        current["fire_seq"] > previous_fire):
                    shot_events.append({
                        "kind": "bot_shot", "attacker_bot": bot_id,
                        "shot_seq": current["fire_seq"],
                        "shell_index": current["shell_index"],
                    })
            if seen != set(identities):
                return False
            self.bot_states = next_states
            self.pending_events.extend(shot_events)
            self._maybe_finish_battle()
            return True

    def report_bot_hit(self, player_id, message):
        """Apply a human or authority-owned bot shot to a bot HP record."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    self.phase != "battle" or self.battle_result is not None):
                return False
            if not all(key in message for key in
                       ("target", "shot_seq", "damage")):
                return False
            if (not _has_finite_fields(
                    message, ("target", "shot_seq", "damage")) or
                    _finite_float(message.get("damage"), -1.0) < 0.0):
                return False
            try:
                shot_seq = int(message.get("shot_seq", 0))
                bot_id = int(message.get("target", 0))
            except (TypeError, ValueError):
                return False
            state = self.bot_states.get(bot_id)
            if state is None or not state.get("alive"):
                return False
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
                hit_key = ("bot_shot", attacker_bot_id, shot_seq)
                if (attacker_bot is None or not attacker_bot.get("alive") or
                        attacker_bot_id == bot_id or
                        attacker_bot.get("team") == state.get("team") or
                        shot_seq <= 0 or
                        shot_seq > int(attacker_bot.get("fire_seq", 0)) or
                        hit_key in self.bot_reported_hits or
                        math.hypot(state["x"] - attacker_bot["x"],
                                   state["z"] - attacker_bot["z"]) > 2200.0):
                    return False
                self.bot_reported_hits.add(hit_key)
                attacker_id = attacker_bot_id
                shell_index = attacker_bot.get("shell_index", 0)
                event_kind = "bot_bot_hit"
            else:
                attacker = self.players.get(player_id)
                hit_key = ("shot", shot_seq)
                if (attacker is None or not attacker.alive or
                        state.get("team") == attacker.team or shot_seq <= 0 or
                        shot_seq > attacker.fire_seq or
                        hit_key in attacker.reported_hits):
                    return False
                attacker.reported_hits.add(hit_key)
                attacker_id = player_id
                shell_index = attacker.shell_index
                event_kind = "bot_hit"
            damage = max(0, min(int(_finite_float(message.get("damage"), 0)), 5000))
            applied = min(damage, int(state.get("health", 0)))
            state["health"] -= applied
            state["alive"] = state["health"] > 0
            self.pending_events.append({
                "kind": event_kind,
                "attacker_bot" if event_kind == "bot_bot_hit" else "attacker": attacker_id,
                "target_bot": bot_id,
                "shot_seq": shot_seq, "shell_index": shell_index,
                "shot_result": max(0, min(int(_finite_float(message.get("shot_result"), 2)), 2)),
                "damage": applied, "health": state["health"], "dead": not state["alive"],
                "world_pose": True,
                "x": round(_clamp(_finite_float(message.get("x"), state["x"]), -2000.0, 2000.0), 4),
                "y": round(_clamp(_finite_float(message.get("y"), state["y"] + 1.0), -1000.0, 1000.0), 4),
                "z": round(_clamp(_finite_float(message.get("z"), state["z"]), -2000.0, 2000.0), 4),
            })
            self._maybe_finish_battle()
            return True

    def report_bot_human_hit(self, player_id, message):
        """Apply an authority-resolved bot shot against shared human HP."""
        with self.lock:
            if (not self._message_round_matches(message) or
                    self.phase != "battle" or self.battle_result is not None or
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
                bot_id = int(message.get("attacker_bot", 0))
                target_id = int(message.get("target", 0))
                shot_seq = int(message.get("shot_seq", 0))
            except (TypeError, ValueError):
                return False
            bot = self.bot_states.get(bot_id)
            target = self.players.get(target_id)
            if bot is None or not bot.get("alive") or target is None or not target.alive:
                return False
            if bot.get("team") == target.team:
                return False
            try:
                bot_fire_seq = int(bot.get("fire_seq", 0))
            except (TypeError, ValueError):
                bot_fire_seq = 0
            hit_key = ("bot_shot", bot_id, shot_seq)
            if (shot_seq <= 0 or shot_seq > bot_fire_seq or
                    hit_key in self.bot_reported_hits):
                return False
            self.bot_reported_hits.add(hit_key)
            damage = max(0, min(int(_finite_float(message.get("damage"), 0)), 5000))
            applied = min(damage, target.health)
            target.health -= applied
            target.alive = target.health > 0
            self.pending_events.append({
                "kind": "bot_human_hit", "attacker_bot": bot_id, "target": target_id,
                "shot_seq": shot_seq, "shot_result": max(0, min(int(_finite_float(message.get("shot_result"), 2)), 2)),
                "damage": applied, "health": target.health, "dead": not target.alive,
                "world_pose": True,
                "x": round(_clamp(_finite_float(message.get("x"), target.x), -2000.0, 2000.0), 4),
                "y": round(_clamp(_finite_float(message.get("y"), target.y + 1.0), -1000.0, 1000.0), 4),
                "z": round(_clamp(_finite_float(message.get("z"), target.z), -2000.0, 2000.0), 4),
            })
            self._maybe_finish_battle()
            return True

    def update_rules(self, player_id, message):
        with self.lock:
            if (not self._message_round_matches(message) or
                    self.phase != "battle" or
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
                    "stopped": bool(raw.get("stopped", False)),
                }
            self.rules_state = {"bases": bases}
            return True

    def report_battle_result(self, player_id, message):
        with self.lock:
            if (not self._message_round_matches(message) or
                    self.phase != "battle" or
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
        if (self.phase != "battle" or self.battle_result is not None or
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
            if self.phase != "battle" or self.battle_result is not None:
                player.forward = 0.0
                player.turn = 0.0
                return
            if player.alive:
                if "forward" in message:
                    player.forward = _clamp(_finite_float(message.get("forward")), -1.0, 1.0)
                if "turn" in message:
                    player.turn = _clamp(_finite_float(message.get("turn")), -1.0, 1.0)
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
                    self.phase == "battle" and player.alive):
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
            if "reported_health" in message and self.phase == "battle":
                self._apply_reported_health(player, message.get("reported_health"))
            if not player.alive:
                # Late packets from the dead client's still-running input loop
                # must not drag its marker away from the server-owned wreck.
                player.forward = 0.0
                player.turn = 0.0

    def _apply_reported_health(self, player, reported_health):
        """Relay damage caused by local bots, fire, drowning or collisions.

        Human-versus-human damage uses report_hit() below.  The victim client
        remains authoritative for local simulation damage that the standalone
        server cannot reproduce because it has no proprietary map or bot data.
        Health reports may only move downward during a round.
        """
        health = max(0, min(int(_finite_float(reported_health, player.health)), player.max_health))
        if health >= player.health:
            return
        damage = player.health - health
        player.health = health
        if health == 0:
            player.alive = False
        self.pending_events.append({
            "kind": "health",
            "target": player.player_id,
            "damage": damage,
            "health": player.health,
            "dead": not player.alive,
            "source": "client_simulation",
        })
        self._maybe_finish_battle()

    def report_hit(self, player_id, message):
        """Apply a map/armor hit resolved by the firing 0.8.2 client.

        The server validates identity, team, range and one report per shot, then
        owns the shared HP result.  This reuses the existing client armor and
        shell collision logic instead of the old fixed 100-HP cone test.
        """
        with self.lock:
            attacker = self.players.get(player_id)
            if (not self._message_round_matches(message) or
                    self.phase != "battle" or self.battle_result is not None or
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
                shot_seq = int(message.get("shot_seq", 0))
                target_id = int(message.get("target", 0))
            except (TypeError, ValueError):
                return False
            hit_key = ("shot", shot_seq)
            if shot_seq <= 0 or shot_seq > attacker.fire_seq or hit_key in attacker.reported_hits:
                return False
            target = self.players.get(target_id)
            if target is None or not target.connected or not target.alive:
                return False
            if target.player_id == attacker.player_id or target.team == attacker.team:
                return False
            distance = math.hypot(target.x - attacker.x, target.z - attacker.z)
            if distance > 2200.0:
                return False
            attacker.reported_hits.add(hit_key)
            damage = max(0, min(int(_finite_float(message.get("damage"), 0)), 5000))
            applied_damage = min(damage, target.health)
            target.health -= applied_damage
            if target.health == 0:
                target.alive = False
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
                "world_pose": True,
                "x": round(_clamp(_finite_float(message.get("x"), target.x), -2000.0, 2000.0), 4),
                "y": round(_clamp(_finite_float(message.get("y"), target.y + 1.0), -1000.0, 1000.0), 4),
                "z": round(_clamp(_finite_float(message.get("z"), target.z), -2000.0, 2000.0), 4),
            }
            self.pending_events.append(event)
            self._maybe_finish_battle()
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

    def tick_once(self, dt):
        reset_message = None
        with self.lock:
            if (self.phase == "battle" and self.battle_result is not None and
                    self.result_reset_tick is not None and
                    self.tick + 1 >= self.result_reset_tick):
                self._reset_round()
                reset_message = self.lobby_message()
        if reset_message is not None:
            self.broadcast(reset_message)
            return
        with self.lock:
            if self.phase != "battle":
                return
            self.tick += 1
            for player in list(self.players.values()):
                self._apply_movement(player, dt)
            if self.battle_result is None:
                self.bot_orders = self.bot_planner.build_orders(
                    self.bot_manifest, list(self.bot_states.values()),
                    [self._public_player(p) for p in self.players.values() if p.connected],
                    time.monotonic())
            events = self.pending_events
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
                "bot_order_revision": self.bot_orders["revision"],
                "rules": self.rules_state,
                "battle_result": self.battle_result,
            }
            recipients = list(self.players.values())
        for player in recipients:
            outgoing = snapshot
            if player.bot_order_revision_sent != self.bot_orders["revision"]:
                outgoing = dict(snapshot)
                outgoing["bot_orders"] = list(self.bot_orders["orders"])
            if not player.send(outgoing):
                self.remove_player(player.player_id)
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
            self.broadcast({"type": "events", "protocol": PROTOCOL_VERSION,
                            "round_id": self.round_id,
                            "server_tick": self.tick, "events": events})

    @staticmethod
    def _public_player(player):
        return {
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
            "fire_seq": player.fire_seq,
            "shell_index": player.shell_index,
            "health": player.health,
            "max_health": player.max_health,
            "alive": player.alive,
        }

    def broadcast(self, message):
        with self.lock:
            players = list(self.players.values())
        for player in players:
            if not player.send(message):
                self.remove_player(player.player_id)


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
                    elif message_type == "rules_state":
                        server.state.update_rules(player.player_id, message)
                    elif message_type == "battle_result":
                        if not server.state.report_battle_result(player.player_id, message):
                            _server_log("BATTLE RESULT rejected sender=%d" % player.player_id)
                    elif message_type == "leave_battle":
                        if server.state.leave_battle(player.player_id, message):
                            _server_log("BATTLE LEAVE id=%d round=%d" % (
                                player.player_id, server.state.round_id))
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
                            _server_log("BATTLE START round=%d map=%s players=%d requested_by=%s" % (
                                start_message["round_id"],
                                start_message["map"],
                                len(start_message["players"]),
                                player.name,
                            ))
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
